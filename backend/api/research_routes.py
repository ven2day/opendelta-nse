"""Research Lab parameter experiments over the existing immutable backtest engine."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, model_validator

from backend.api.backtest_routes import MAX_SYMBOLS
from backend.backtest.engine import BacktestRequest
from backend.backtest.jobs import BacktestJobRunner, BacktestQueueFull
from backend.core.models import MARKETS
from backend.data.database import DatabaseUnavailable
from backend.data.repositories import ResearchExperimentRepository, SavedUniverseRepository, StrategySourceRepository
from backend.data.universe_presets import get_universe_preset
from backend.markets.common import MAX_INTERACTIVE_CANDLE_BARS, TIMEFRAME_SECONDS
from backend.research.parameter_experiments import (
    LARGE_WORKLOAD_WARNING,
    MAX_ESTIMATED_SYMBOL_RUNS,
    MAX_GENERATED_VARIANTS,
    MAX_SWEEP_PARAMETERS,
    MAX_VALUES_PER_PARAMETER,
    GeneratedVariant,
    generate_variants,
    preview_hash,
)
from backend.strategies.adapter_v2 import StrategyRunnerClient, StrategyV2BacktestAdapter
from backend.strategies.registry import StrategyRegistry


class ResearchVariantRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=80)
    configuration: dict[str, Any] = Field(default_factory=dict)
    execution: dict[str, Any] = Field(default_factory=dict)


class ResearchParameterRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    section: Literal["strategy", "execution"]
    parameter: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_]{0,79}$")
    type: Literal["number", "integer", "boolean", "enum"]
    method: Literal["EXPLICIT_VALUES", "NUMERIC_RANGE", "FIXED"]
    values: list[Any] | None = Field(default=None, max_length=MAX_VALUES_PER_PARAMETER)
    minimum: Any | None = None
    maximum: Any | None = None
    step: Any | None = None
    value: Any | None = None

    @model_validator(mode="after")
    def validate_method_fields(self) -> ResearchParameterRequest:
        range_fields = (self.minimum, self.maximum, self.step)
        if self.method == "EXPLICIT_VALUES":
            if self.values is None or self.value is not None or any(item is not None for item in range_fields):
                raise ValueError("Explicit values require only the values list")
        elif self.method == "NUMERIC_RANGE":
            if self.type not in {"number", "integer"}:
                raise ValueError("Numeric ranges support only number or integer parameters")
            if self.values is not None or self.value is not None or any(item is None for item in range_fields):
                raise ValueError("Numeric ranges require only minimum, maximum and step")
        elif self.value is None or self.values is not None or any(item is not None for item in range_fields):
            raise ValueError("Fixed parameters require only one value")
        return self


class ResearchPreviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=120)
    mode: Literal["MANUAL", "GRID"] = "MANUAL"
    market: str = Field(pattern="^(NSE|CRYPTO)$")
    strategyId: str = Field(min_length=1, max_length=80)
    strategyVersion: str = Field(min_length=1, max_length=40)
    strategySourceId: str | None = None
    universeId: str | None = None
    universePresetId: str | None = Field(default=None, min_length=1, max_length=80)
    symbols: list[str] = Field(default_factory=list, max_length=MAX_SYMBOLS)
    timeframe: str = Field(default="5m", min_length=1, max_length=8)
    startDate: date
    endDate: date
    configuration: dict[str, Any] = Field(default_factory=dict)
    execution: dict[str, Any] = Field(default_factory=dict)
    variants: list[ResearchVariantRequest] = Field(default_factory=list, max_length=MAX_GENERATED_VARIANTS)
    parameters: list[ResearchParameterRequest] = Field(default_factory=list, max_length=MAX_SWEEP_PARAMETERS)

    @model_validator(mode="after")
    def validate_mode_and_universe(self) -> ResearchPreviewRequest:
        choices = bool(self.universeId) + bool(self.universePresetId) + bool(self.symbols)
        if choices != 1:
            raise ValueError("Select exactly one saved watchlist, supported universe, or symbol list")
        if self.mode == "MANUAL" and (not self.variants or self.parameters):
            raise ValueError("Manual mode requires variants and does not accept sweep parameters")
        if self.mode == "GRID" and (not self.parameters or self.variants):
            raise ValueError("Grid mode requires parameters and does not accept manual variants")
        return self


class ResearchSubmissionRequest(ResearchPreviewRequest):
    previewHash: str = Field(min_length=71, max_length=71, pattern=r"^sha256:[0-9a-f]{64}$")
    idempotencyKey: str = Field(min_length=8, max_length=128, pattern=r"^[A-Za-z0-9_.:-]+$")


# Compatibility name retained for API imports; Phase 7 submissions require a preview hash.
ResearchExperimentRequest = ResearchSubmissionRequest


class ResearchServices:
    def __init__(
        self,
        *,
        registry: StrategyRegistry,
        experiments: Callable[[], ResearchExperimentRepository],
        runner: Callable[[], BacktestJobRunner],
        universes: Callable[[], SavedUniverseRepository] | None = None,
        sources: Callable[[], StrategySourceRepository] | None = None,
    ) -> None:
        self.registry = registry
        self.experiments = experiments
        self.runner = runner
        self.universes = universes
        self.sources = sources


@dataclass(frozen=True)
class PreparedExperiment:
    public: dict[str, Any]
    variants: list[GeneratedVariant]
    universe_id: str | None
    universe_name: str


def create_research_router(services: ResearchServices) -> APIRouter:
    router = APIRouter(prefix="/v2/research/experiments", tags=["research"])

    def guarded(callable_: Callable[[], Any]) -> Any:
        try:
            return callable_()
        except DatabaseUnavailable as error:
            raise HTTPException(status_code=503, detail=str(error)) from error

    def prepare(request: ResearchPreviewRequest) -> PreparedExperiment:
        try:
            strategy = _strategy_for_request(request, services, guarded)
            symbols, universe_id, universe_name = _symbols_for_request(request, services, guarded)
            _validate_identity_and_range(request, strategy)
            variants = generate_variants(
                strategy=strategy,
                market=request.market,
                timeframe=request.timeframe,
                mode=request.mode,
                base_configuration=request.configuration,
                base_execution=request.execution,
                manual_variants=[item.model_dump() for item in request.variants],
                parameters=[item.model_dump(exclude_none=True) for item in request.parameters],
            )
            estimated_symbol_runs = len(variants) * len(symbols)
            if estimated_symbol_runs > MAX_ESTIMATED_SYMBOL_RUNS:
                raise ValueError(
                    f"The experiment requires {estimated_symbol_runs:,} symbol-runs; "
                    f"the limit is {MAX_ESTIMATED_SYMBOL_RUNS:,}"
                )
        except HTTPException:
            raise
        except (KeyError, TypeError, ValueError) as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

        definitions = [item.model_dump(exclude_none=True) for item in request.parameters]
        basis = {
            "name": request.name.strip(),
            "mode": request.mode,
            "strategyId": strategy.strategy_id,
            "strategyVersion": strategy.version,
            "strategySourceId": request.strategySourceId,
            "market": request.market,
            "timeframe": request.timeframe,
            "universeId": universe_id,
            "universePresetId": request.universePresetId,
            "universeName": universe_name,
            "symbols": symbols,
            "startDate": request.startDate.isoformat(),
            "endDate": request.endDate.isoformat(),
            "sweepDefinitions": definitions,
            "parameterCount": len(definitions),
            "variantCount": len(variants),
            "symbolCount": len(symbols),
            "estimatedSymbolRuns": estimated_symbol_runs,
            "variants": [item.public() for item in variants],
        }
        warnings = []
        if estimated_symbol_runs >= LARGE_WORKLOAD_WARNING:
            warnings.append(
                f"Large workload: {estimated_symbol_runs:,} symbol-runs will use the bounded backtest queue."
            )
        return PreparedExperiment(
            public={"previewHash": preview_hash(basis), **basis, "warnings": warnings},
            variants=variants,
            universe_id=universe_id,
            universe_name=universe_name,
        )

    @router.post("/preview")
    def preview_experiment(request: ResearchPreviewRequest) -> dict[str, Any]:
        """Validate and generate the complete experiment without writing or starting runs."""
        return prepare(request).public

    def submit_from_preview(request: ResearchSubmissionRequest) -> dict[str, Any]:
        prepared = prepare(request)
        if prepared.public["previewHash"] != request.previewHash:
            raise HTTPException(status_code=409, detail="The preview is stale; preview the experiment again")
        repository = guarded(services.experiments)
        existing = repository.get_by_idempotency_key(request.idempotencyKey)
        if existing is not None:
            if existing["previewHash"] != request.previewHash:
                raise HTTPException(status_code=409, detail="The idempotency key belongs to a different preview")
            return existing

        runner = guarded(services.runner)
        try:
            reservation = runner.reserve(len(prepared.variants))
        except BacktestQueueFull as error:
            raise HTTPException(status_code=429, detail=str(error)) from error

        with reservation:
            experiment, created = guarded(lambda: repository.create_generated(
                name=request.name.strip(),
                generation_mode=request.mode,
                sweep_definitions=prepared.public["sweepDefinitions"],
                preview_hash=request.previewHash,
                idempotency_key=request.idempotencyKey,
                market=request.market,
                strategy_id=request.strategyId,
                strategy_version=request.strategyVersion,
                strategy_source_id=request.strategySourceId,
                timeframe=request.timeframe,
                symbols=prepared.public["symbols"],
                start_date=request.startDate,
                end_date=request.endDate,
                universe_id=prepared.universe_id,
                universe_name=prepared.universe_name,
                variants=[item.public() for item in prepared.variants],
            ))
            if not created:
                if experiment["previewHash"] != request.previewHash:
                    raise HTTPException(status_code=409, detail="The idempotency key belongs to a different preview")
                return experiment
            run_requests = [
                BacktestRequest(
                    run_id=persisted["run"]["runId"],
                    market=request.market,
                    strategy_id=request.strategyId,
                    strategy_source_id=request.strategySourceId,
                    symbols=prepared.public["symbols"],
                    timeframe=request.timeframe,
                    start_date=request.startDate,
                    end_date=request.endDate,
                    configuration=generated.configuration,
                    execution=generated.execution_settings,
                )
                for persisted, generated in zip(experiment["variants"], prepared.variants, strict=True)
            ]
            reservation.submit(run_requests)
        return experiment

    @router.post("/from-preview", status_code=202)
    def create_from_preview(request: ResearchSubmissionRequest) -> dict[str, Any]:
        return submit_from_preview(request)

    @router.post("", status_code=202)
    def create_experiment(request: ResearchSubmissionRequest) -> dict[str, Any]:
        """Phase 6-compatible path with the same mandatory Phase 7 preview contract."""
        return submit_from_preview(request)

    @router.get("")
    def list_experiments(
        market: str | None = Query(default=None, pattern="^(NSE|CRYPTO)$"),
        limit: int = Query(default=50, ge=1, le=200),
    ) -> dict[str, Any]:
        return {"experiments": guarded(lambda: services.experiments().list(market, limit=limit))}

    @router.delete("/{experiment_id}")
    def cancel_experiment(experiment_id: str) -> dict[str, Any]:
        try:
            experiment = guarded(lambda: services.experiments().get(experiment_id))
            runner = guarded(services.runner)
            for variant in experiment["variants"]:
                if variant["run"]["status"] in {"QUEUED", "RUNNING"}:
                    runner.cancel(variant["run"]["runId"])
            return guarded(lambda: services.experiments().get(experiment_id))
        except (KeyError, ValueError) as error:
            raise HTTPException(status_code=404, detail="Research experiment was not found") from error

    @router.get("/{experiment_id}")
    def get_experiment(experiment_id: str) -> dict[str, Any]:
        try:
            return guarded(lambda: services.experiments().get(experiment_id))
        except (KeyError, ValueError) as error:
            raise HTTPException(status_code=404, detail="Research experiment was not found") from error

    return router


def _strategy_for_request(
    request: ResearchPreviewRequest,
    services: ResearchServices,
    guarded: Callable[[Callable[[], Any]], Any],
) -> Any:
    if request.strategySourceId:
        if services.sources is None:
            raise HTTPException(status_code=503, detail="Strategy V2 source storage is not configured")
        try:
            source = guarded(services.sources).get(request.strategySourceId)
        except (KeyError, ValueError) as error:
            raise HTTPException(status_code=422, detail="Strategy V2 source was not found") from error
        if source["status"] != "VALIDATED":
            raise HTTPException(status_code=409, detail="Archived Strategy V2 sources cannot be used in research")
        manifest = source["manifest"]
        if source["strategyId"] != manifest["strategyId"] or source["strategyVersion"] != manifest["version"]:
            raise ValueError("Stored Strategy V2 identity does not match its immutable manifest")
        strategy = StrategyV2BacktestAdapter(source, StrategyRunnerClient("/not-used-during-validation"))
    else:
        try:
            strategy = services.registry.get(request.strategyId)
        except KeyError as error:
            raise ValueError(str(error)) from error
    if strategy.strategy_id != request.strategyId:
        raise ValueError("strategyId does not match the selected immutable strategy source")
    if strategy.version != request.strategyVersion:
        raise ValueError("strategyVersion does not match the selected immutable strategy version")
    return strategy


def _symbols_for_request(
    request: ResearchPreviewRequest,
    services: ResearchServices,
    guarded: Callable[[Callable[[], Any]], Any],
) -> tuple[list[str], str | None, str]:
    universe_id: str | None = None
    if request.universeId:
        if services.universes is None:
            raise HTTPException(status_code=503, detail="Watchlist storage is not configured")
        repository = guarded(services.universes)
        universe = repository.get(request.universeId)
        if universe["market"] != request.market:
            raise ValueError("The selected watchlist belongs to a different market")
        if not universe["active"]:
            raise ValueError("The selected saved watchlist is not active")
        requested_symbols = repository.symbols(request.universeId, market=request.market)
        universe_id = request.universeId
        universe_name = str(universe["name"])
    elif request.universePresetId:
        preset = get_universe_preset(request.universePresetId, request.market)
        requested_symbols = list(preset.symbols)
        universe_name = preset.name
    else:
        requested_symbols = request.symbols
        universe_name = "Explicit symbol snapshot"
    symbols = sorted({str(symbol).strip().upper() for symbol in requested_symbols if str(symbol).strip()})
    if not symbols:
        raise ValueError("The selected watchlist or universe is empty")
    if len(symbols) > MAX_SYMBOLS:
        raise ValueError(f"A research experiment may contain at most {MAX_SYMBOLS} symbols")
    return symbols, universe_id, universe_name


def _validate_identity_and_range(request: ResearchPreviewRequest, strategy: Any) -> None:
    if request.market not in MARKETS or request.market not in strategy.supported_markets:
        raise ValueError(f"{strategy.strategy_id} does not support {request.market}")
    if request.timeframe not in strategy.supported_timeframes:
        raise ValueError(f"{strategy.strategy_id} does not support the {request.timeframe} timeframe")
    if request.endDate < request.startDate:
        raise ValueError("endDate must not be before startDate")
    if request.market == "CRYPTO":
        requested_seconds = ((request.endDate - request.startDate).days + 1) * 86_400
        requested_bars = requested_seconds // TIMEFRAME_SECONDS[request.timeframe]
        if requested_bars > MAX_INTERACTIVE_CANDLE_BARS:
            raise ValueError(
                f"The selected {request.timeframe} Crypto range contains about {requested_bars:,} bars; "
                f"choose a shorter range (maximum {MAX_INTERACTIVE_CANDLE_BARS:,} bars)"
            )
