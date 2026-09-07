"""Research Lab walk-forward preview, submission, polling, and cancellation APIs."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, model_validator

from backend.api.backtest_routes import MAX_SYMBOLS
from backend.api.research_routes import (
    ResearchServices,
    _strategy_for_request,
    _symbols_for_request,
    _validate_identity_and_range,
)
from backend.backtest.engine import BacktestRequest, ExecutionSettings
from backend.backtest.jobs import BacktestJobRunner, BacktestQueueFull
from backend.data.database import DatabaseUnavailable
from backend.data.repositories import ResearchExperimentRepository, WalkForwardValidationRepository
from backend.research.walk_forward import (
    LARGE_WALK_FORWARD_WORKLOAD,
    MAX_CANDIDATES_PER_FOLD,
    MAX_WALK_FORWARD_FOLDS,
    FoldDefinition,
    canonical_hash,
    generate_folds,
    workload_estimate,
)
from backend.research.walk_forward_jobs import WalkForwardJobRunner
from backend.strategies.registry import StrategyRegistry


class WalkForwardPreviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=120)
    mode: Literal["ANCHORED", "ROLLING"]
    market: str = Field(pattern="^(NSE|CRYPTO)$")
    strategyId: str = Field(min_length=1, max_length=80)
    strategyVersion: str = Field(min_length=1, max_length=40)
    strategySourceId: str | None = None
    universeId: str | None = None
    universePresetId: str | None = Field(default=None, min_length=1, max_length=80)
    symbols: list[str] = Field(default_factory=list, max_length=MAX_SYMBOLS)
    timeframe: str = Field(min_length=1, max_length=8)
    startDate: date
    endDate: date
    trainingWindow: int = Field(ge=1, le=5_000)
    testingWindow: int = Field(ge=1, le=1_000)
    step: int = Field(ge=1, le=1_000)
    maximumFolds: int = Field(default=MAX_WALK_FORWARD_FOLDS, ge=1, le=MAX_WALK_FORWARD_FOLDS)
    candidateExperimentId: str = Field(min_length=36, max_length=36)
    rankingObjective: Literal["NET_PNL", "RETURN_DRAWDOWN", "LOWEST_DRAWDOWN", "HIGHEST_WIN_RATE"]
    minimumRequiredTrades: int = Field(default=1, ge=0, le=1_000_000)
    transactionCostBps: float = Field(default=0, ge=0, le=10_000, allow_inf_nan=False)
    slippageBps: float = Field(default=0, ge=0, le=10_000, allow_inf_nan=False)

    @model_validator(mode="after")
    def validate_universe(self) -> WalkForwardPreviewRequest:
        choices = bool(self.universeId) + bool(self.universePresetId) + bool(self.symbols)
        if choices != 1:
            raise ValueError("Select exactly one saved watchlist, supported universe, or symbol list")
        return self


class WalkForwardSubmissionRequest(WalkForwardPreviewRequest):
    previewHash: str = Field(min_length=71, max_length=71, pattern=r"^sha256:[0-9a-f]{64}$")
    idempotencyKey: str = Field(min_length=8, max_length=128, pattern=r"^[A-Za-z0-9_.:-]+$")


class WalkForwardServices:
    def __init__(
        self,
        *,
        registry: StrategyRegistry,
        validations: Callable[[], WalkForwardValidationRepository],
        experiments: Callable[[], ResearchExperimentRepository],
        backtests: Callable[[], BacktestJobRunner],
        coordinator: Callable[[], WalkForwardJobRunner],
        research: ResearchServices,
    ) -> None:
        self.registry = registry
        self.validations = validations
        self.experiments = experiments
        self.backtests = backtests
        self.coordinator = coordinator
        self.research = research


@dataclass(frozen=True)
class PreparedWalkForward:
    public: dict[str, Any]
    folds: list[FoldDefinition]
    candidates: list[dict[str, Any]]
    symbols: list[str]
    universe_id: str | None
    universe_name: str


def create_walk_forward_router(services: WalkForwardServices) -> APIRouter:
    router = APIRouter(prefix="/v2/research/walk-forward", tags=["research"])

    def guarded(callable_: Callable[[], Any]) -> Any:
        try:
            return callable_()
        except DatabaseUnavailable as error:
            raise HTTPException(status_code=503, detail=str(error)) from error

    def prepare(request: WalkForwardPreviewRequest) -> PreparedWalkForward:
        try:
            strategy = _strategy_for_request(request, services.research, guarded)  # type: ignore[arg-type]
            symbols, universe_id, universe_name = _symbols_for_request(  # type: ignore[arg-type]
                request, services.research, guarded
            )
            _validate_identity_and_range(request, strategy)  # type: ignore[arg-type]
            experiment = guarded(lambda: services.experiments().get(request.candidateExperimentId))
            _validate_candidate_experiment(request, experiment)
            candidates = list(experiment["variants"])
            if len(candidates) > MAX_CANDIDATES_PER_FOLD:
                raise ValueError(f"Candidate experiments may contain at most {MAX_CANDIDATES_PER_FOLD} variants")
            for candidate in candidates:
                strategy.validate_config(candidate["configuration"])
                ExecutionSettings.from_mapping(
                    {
                        **candidate["execution"],
                        "transactionCostBps": request.transactionCostBps,
                        "slippageBps": request.slippageBps,
                    },
                    whole_units=request.market == "NSE",
                )
            repository = guarded(services.validations)
            nse_sessions = repository.nse_sessions(request.startDate, request.endDate) if request.market == "NSE" else None
            if request.market == "NSE" and not nse_sessions:
                raise ValueError("The NSE market-session calendar is unavailable for the requested range")
            folds = generate_folds(
                market=request.market, start=request.startDate, end=request.endDate,
                training_window=request.trainingWindow, testing_window=request.testingWindow,
                step=request.step, mode=request.mode, maximum_folds=request.maximumFolds,
                nse_sessions=nse_sessions,
            )
            workload = workload_estimate(
                market=request.market, timeframe=request.timeframe, folds=folds,
                candidate_count=len(candidates), symbol_count=len(symbols),
            )
        except HTTPException:
            raise
        except (KeyError, TypeError, ValueError) as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

        candidate_snapshots = [
            {
                "variantId": candidate["variantId"], "position": candidate["position"],
                "name": candidate["name"], "configuration": candidate["configuration"],
                "execution": {
                    **candidate["execution"],
                    "transactionCostBps": request.transactionCostBps,
                    "slippageBps": request.slippageBps,
                },
            }
            for candidate in candidates
        ]
        basis = {
            "name": request.name.strip(), "mode": request.mode, "market": request.market,
            "strategyId": strategy.strategy_id, "strategyVersion": strategy.version,
            "strategySourceId": request.strategySourceId, "timeframe": request.timeframe,
            "universeId": universe_id, "universeName": universe_name, "symbols": symbols,
            "overallStartDate": request.startDate.isoformat(), "overallEndDate": request.endDate.isoformat(),
            "trainingWindow": request.trainingWindow, "testingWindow": request.testingWindow,
            "step": request.step, "maximumFolds": request.maximumFolds,
            "candidateExperimentId": request.candidateExperimentId,
            "rankingObjective": request.rankingObjective,
            "minimumRequiredTrades": request.minimumRequiredTrades,
            "transactionCostBps": request.transactionCostBps, "slippageBps": request.slippageBps,
            **workload, "folds": [fold.public() for fold in folds], "candidates": candidate_snapshots,
        }
        warnings = []
        if workload["estimatedCandleWorkload"] >= LARGE_WALK_FORWARD_WORKLOAD:
            warnings.append("Large workload: child runs remain bounded and may stay queued for an extended period.")
        if request.minimumRequiredTrades == 0:
            warnings.append("Candidates with no completed trades are eligible for training selection.")
        return PreparedWalkForward(
            public={"previewHash": canonical_hash(basis), **basis, "warnings": warnings},
            folds=folds, candidates=candidate_snapshots, symbols=symbols,
            universe_id=universe_id, universe_name=universe_name,
        )

    @router.post("/preview")
    def preview_walk_forward(request: WalkForwardPreviewRequest) -> dict[str, Any]:
        """Validate the complete schedule and workload without writing rows or starting runs."""
        return prepare(request).public

    @router.post("/from-preview", status_code=202)
    def submit_walk_forward(request: WalkForwardSubmissionRequest) -> dict[str, Any]:
        prepared = prepare(request)
        if prepared.public["previewHash"] != request.previewHash:
            raise HTTPException(status_code=409, detail="The preview is stale; preview the validation again")
        repository = guarded(services.validations)
        existing = repository.get_by_idempotency_key(request.idempotencyKey)
        if existing is not None:
            if existing["previewHash"] != request.previewHash:
                raise HTTPException(status_code=409, detail="The idempotency key belongs to a different preview")
            return existing

        training_count = len(prepared.folds) * len(prepared.candidates)
        backtests = guarded(services.backtests)
        coordinator = guarded(services.coordinator)
        try:
            coordinator_reservation = coordinator.reserve()
            try:
                backtest_reservation = backtests.reserve(training_count)
            except Exception:
                coordinator_reservation.release()
                raise
        except BacktestQueueFull as error:
            raise HTTPException(status_code=429, detail=str(error)) from error
        with backtest_reservation, coordinator_reservation:
            validation, created = guarded(lambda: repository.create_generated(
                name=request.name.strip(), mode=request.mode, market=request.market,
                strategy_id=request.strategyId, strategy_version=request.strategyVersion,
                strategy_source_id=request.strategySourceId, timeframe=request.timeframe,
                symbols=prepared.symbols, universe_id=prepared.universe_id, universe_name=prepared.universe_name,
                overall_start_date=request.startDate, overall_end_date=request.endDate,
                training_window=request.trainingWindow, testing_window=request.testingWindow,
                step_length=request.step, maximum_folds=request.maximumFolds,
                candidate_experiment_id=request.candidateExperimentId,
                ranking_objective=request.rankingObjective, minimum_required_trades=request.minimumRequiredTrades,
                transaction_cost_bps=request.transactionCostBps, slippage_bps=request.slippageBps,
                preview_hash=request.previewHash, idempotency_key=request.idempotencyKey,
                workload={key: prepared.public[key] for key in (
                    "foldCount", "candidateCount", "childRunCount", "symbolCount",
                    "estimatedSymbolRuns", "estimatedCandleWorkload",
                )},
                folds=[fold.public() for fold in prepared.folds], candidates=prepared.candidates,
            ))
            if not created:
                if validation["previewHash"] != request.previewHash:
                    raise HTTPException(status_code=409, detail="The idempotency key belongs to a different preview")
                return validation
            requests = [
                _backtest_request(candidate["run"])
                for fold in validation["folds"]
                for candidate in fold["trainingCandidates"]
            ]
            backtest_reservation.submit(requests)
            coordinator_reservation.submit(validation["validationId"])
        return validation

    @router.get("")
    def list_walk_forward(
        market: str | None = Query(default=None, pattern="^(NSE|CRYPTO)$"),
        limit: int = Query(default=50, ge=1, le=200),
    ) -> dict[str, Any]:
        return {"validations": guarded(lambda: services.validations().list(market, limit=limit))}

    @router.get("/{validation_id}")
    def get_walk_forward(validation_id: str) -> dict[str, Any]:
        try:
            return guarded(lambda: services.validations().get(validation_id))
        except (KeyError, ValueError) as error:
            raise HTTPException(status_code=404, detail="Walk-forward validation was not found") from error

    @router.delete("/{validation_id}")
    def cancel_walk_forward(validation_id: str) -> dict[str, Any]:
        try:
            return guarded(lambda: services.coordinator().cancel(validation_id))
        except (KeyError, ValueError) as error:
            raise HTTPException(status_code=404, detail="Walk-forward validation was not found") from error

    return router


def _validate_candidate_experiment(request: WalkForwardPreviewRequest, experiment: dict[str, Any]) -> None:
    fields = (
        ("market", request.market), ("strategyId", request.strategyId),
        ("strategyVersion", request.strategyVersion), ("strategySourceId", request.strategySourceId),
        ("timeframe", request.timeframe),
    )
    for field, expected in fields:
        if experiment.get(field) != expected:
            raise ValueError(f"Candidate experiment {field} does not match the requested immutable strategy context")
    if not experiment.get("variants"):
        raise ValueError("The candidate experiment has no variants")


def _backtest_request(run: dict[str, Any]) -> BacktestRequest:
    return BacktestRequest(
        run_id=run["runId"], market=run["market"], strategy_id=run["strategyId"],
        strategy_source_id=run["strategySourceId"], symbols=run["symbols"], timeframe=run["timeframe"],
        start_date=date.fromisoformat(run["startDate"]), end_date=date.fromisoformat(run["endDate"]),
        configuration=run["configurationSnapshot"],
        execution=ExecutionSettings.from_mapping(run["executionSettings"], whole_units=run["market"] == "NSE"),
    )
