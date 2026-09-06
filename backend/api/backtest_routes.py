"""Backtest API: create runs as background jobs, poll them, cancel them, page their trades."""

from __future__ import annotations

from collections.abc import Callable
from datetime import date
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from backend.backtest.engine import BacktestRequest, ExecutionSettings
from backend.backtest.jobs import BacktestJobRunner
from backend.core.models import MARKETS
from backend.data.database import DatabaseUnavailable
from backend.data.repositories import (
    BacktestRunRepository,
    BacktestTradeRepository,
    SavedUniverseRepository,
    StrategyApprovalRepository,
    StrategyConfigRepository,
    StrategyDeploymentRepository,
)
from backend.data.universe_presets import get_universe_preset
from backend.markets.common import MAX_INTERACTIVE_CANDLE_BARS, TIMEFRAME_SECONDS
from backend.paper_trading.execution import ExecutionPolicy
from backend.strategies.registry import StrategyRegistry

MAX_SYMBOLS = 2_000


class BacktestCreateRequest(BaseModel):
    market: str = Field(pattern="^(NSE|CRYPTO)$")
    strategyId: str = Field(min_length=1, max_length=80)
    symbols: list[str] = Field(default_factory=list, max_length=MAX_SYMBOLS)
    universePresetId: str | None = Field(default=None, min_length=1, max_length=80)
    timeframe: str = Field(default="5m", min_length=1, max_length=8)
    startDate: date
    endDate: date
    configuration: dict[str, Any] = Field(default_factory=dict)
    execution: dict[str, Any] = Field(default_factory=dict)


class BacktestApprovalRequest(BaseModel):
    mode: str = Field(pattern="^(SIGNALS|PAPER)$")
    universeId: str
    signalSource: str = Field(default="OPENDELTA", pattern="^(OPENDELTA|TRADINGVIEW)$")


class BacktestServices:
    """Everything the router needs; resolved lazily so a missing database fails closed per request."""

    def __init__(
        self,
        *,
        registry: StrategyRegistry,
        runs: Callable[[], BacktestRunRepository],
        trades: Callable[[], BacktestTradeRepository],
        runner: Callable[[], BacktestJobRunner],
        configs: Callable[[], StrategyConfigRepository] | None = None,
        deployments: Callable[[], StrategyDeploymentRepository] | None = None,
        universes: Callable[[], SavedUniverseRepository] | None = None,
        approvals: Callable[[], StrategyApprovalRepository] | None = None,
        deployment_changed: Callable[[str], None] | None = None,
    ) -> None:
        self.registry = registry
        self._runs = runs
        self._trades = trades
        self._runner = runner
        self.configs = configs
        self.deployments = deployments
        self.universes = universes
        self.approvals = approvals
        self.deployment_changed = deployment_changed

    def runs(self) -> BacktestRunRepository:
        return self._runs()

    def trades(self) -> BacktestTradeRepository:
        return self._trades()

    def runner(self) -> BacktestJobRunner:
        return self._runner()


def create_backtest_router(services: BacktestServices) -> APIRouter:
    router = APIRouter(prefix="/v2/backtests", tags=["backtests"])

    def _guard(callable_: Callable[[], Any]) -> Any:
        try:
            return callable_()
        except DatabaseUnavailable as error:
            raise HTTPException(status_code=503, detail=str(error)) from error

    @router.post("", status_code=202)
    def create_backtest(request: BacktestCreateRequest) -> dict[str, Any]:
        if request.market not in MARKETS:
            raise HTTPException(status_code=422, detail="market must be NSE or CRYPTO")
        try:
            strategy = services.registry.get(request.strategyId)
        except KeyError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        if request.market not in strategy.supported_markets:
            raise HTTPException(status_code=422, detail=f"{strategy.strategy_id} does not support {request.market}")
        if request.timeframe not in strategy.supported_timeframes:
            raise HTTPException(status_code=422, detail=f"{strategy.strategy_id} does not support the {request.timeframe} timeframe")
        if request.endDate < request.startDate:
            raise HTTPException(status_code=422, detail="endDate must not be before startDate")
        if request.market == "CRYPTO":
            requested_seconds = (request.endDate - request.startDate).days * 86_400
            requested_bars = requested_seconds // TIMEFRAME_SECONDS[request.timeframe]
            if requested_bars > MAX_INTERACTIVE_CANDLE_BARS:
                raise HTTPException(
                    status_code=422,
                    detail=(
                        f"The selected {request.timeframe} Crypto range contains about {requested_bars:,} bars; "
                        f"choose a shorter range (maximum {MAX_INTERACTIVE_CANDLE_BARS:,} bars)"
                    ),
                )
        if request.universePresetId is not None and request.symbols:
            raise HTTPException(status_code=422, detail="Use either universePresetId or symbols, not both")
        if request.universePresetId is not None:
            try:
                requested_symbols = get_universe_preset(request.universePresetId, request.market).symbols
            except KeyError as error:
                raise HTTPException(status_code=422, detail=str(error)) from error
        else:
            requested_symbols = request.symbols
        symbols = sorted({symbol.strip().upper() for symbol in requested_symbols if symbol.strip()})
        if not symbols:
            raise HTTPException(status_code=422, detail="At least one symbol is required")
        try:
            snapshot = strategy.resolve(request.configuration) if hasattr(strategy, "resolve") else dict(request.configuration)
            strategy.validate_config(snapshot)
            execution = ExecutionSettings.from_mapping(
                request.execution,
                whole_units=request.market == "NSE",
            )
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        runs = _guard(services.runs)
        execution_snapshot = execution.public()
        execution_snapshot["executionTimeframe"] = "5m" if request.market == "NSE" and request.timeframe == "1d" else request.timeframe
        record = runs.create(
            market=request.market,
            strategy_id=strategy.strategy_id,
            strategy_version=strategy.version,
            configuration_snapshot=snapshot,
            execution_settings=execution_snapshot,
            timeframe=request.timeframe,
            symbols=symbols,
            start_date=request.startDate,
            end_date=request.endDate,
        )
        _guard(services.runner).submit(
            BacktestRequest(
                run_id=record["runId"],
                market=request.market,
                strategy_id=strategy.strategy_id,
                symbols=symbols,
                timeframe=request.timeframe,
                start_date=request.startDate,
                end_date=request.endDate,
                configuration=snapshot,
                execution=execution,
            )
        )
        return record

    @router.get("")
    def list_backtests(market: str | None = Query(default=None), limit: int = Query(default=50, ge=1, le=500)) -> dict[str, Any]:
        market_key = market.strip().upper() if market else None
        if market_key and market_key not in MARKETS:
            raise HTTPException(status_code=422, detail="market must be NSE or CRYPTO")
        return {"runs": _guard(services.runs).list(market_key, limit=limit)}

    @router.get("/{run_id}")
    def get_backtest(run_id: str) -> dict[str, Any]:
        try:
            return _guard(services.runs).get(run_id)
        except (KeyError, ValueError) as error:
            raise HTTPException(status_code=404, detail="Backtest run was not found") from error

    @router.post("/{run_id}/approve")
    def approve_backtest(run_id: str, request: BacktestApprovalRequest) -> dict[str, Any]:
        if not all((services.configs, services.deployments, services.universes, services.approvals)):
            raise HTTPException(status_code=503, detail="Backtest approval storage is not configured")
        try:
            run = _guard(services.runs).get(run_id)
            universe = _guard(services.universes).get(request.universeId)  # type: ignore[union-attr]
        except (KeyError, ValueError) as error:
            raise HTTPException(status_code=404, detail="Backtest run or watchlist was not found") from error
        if run["status"] != "COMPLETE":
            raise HTTPException(status_code=409, detail="Only a completed backtest can be approved")
        try:
            strategy = services.registry.get(run["strategyId"])
        except KeyError as error:
            raise HTTPException(status_code=409, detail="This backtest strategy is no longer registered") from error
        if run["strategyVersion"] != strategy.version:
            raise HTTPException(
                status_code=409,
                detail="This backtest uses an older strategy version; run a new backtest before approval",
            )
        if universe["market"] != run["market"]:
            raise HTTPException(status_code=422, detail="The watchlist belongs to a different market")
        approved_symbols = _guard(services.universes).symbols(request.universeId, market=run["market"])  # type: ignore[union-attr]
        if set(approved_symbols) != set(run["symbols"]):
            raise HTTPException(status_code=409, detail="The watchlist must contain exactly the symbols used by this backtest")
        if request.mode == "PAPER" and _guard(services.approvals).get(run_id, "SIGNALS") is None:  # type: ignore[union-attr]
            raise HTTPException(status_code=409, detail="Approve this backtest for Signals before approving Paper")
        if _guard(services.approvals).get(run_id, request.mode) is not None:  # type: ignore[union-attr]
            raise HTTPException(status_code=409, detail=f"This backtest is already approved for {request.mode.title()}")

        configuration = dict(run["configurationSnapshot"])
        execution = dict(run["executionSettings"])
        execution.pop("executionTimeframe", None)
        execution.pop("batchSize", None)  # Backtest persistence tuning, not a paper-execution rule.
        target_override = execution.pop("targetPct", None)
        if target_override is not None:
            if "target_pct" not in strategy.config_schema:
                raise HTTPException(
                    status_code=409,
                    detail="This backtest target override cannot be represented by the live strategy configuration",
                )
            configuration["target_pct"] = target_override
        try:
            strategy.validate_config(configuration)
            risk = ExecutionPolicy.from_mapping(execution).public()
        except ValueError as error:
            raise HTTPException(status_code=409, detail=f"This backtest cannot be promoted: {error}") from error
        config = _guard(services.configs).save(  # type: ignore[union-attr]
            market=run["market"], strategy_id=run["strategyId"], strategy_version=run["strategyVersion"],
            name=f"Approved {run['strategyId']} {run_id[:8]}", configuration=configuration,
            risk_settings=risk, activate=True,
        )
        deployment = _guard(services.deployments).save(  # type: ignore[union-attr]
            market=run["market"], strategy_id=run["strategyId"], strategy_version=run["strategyVersion"],
            config_id=config["configId"], universe_id=request.universeId, timeframe=run["timeframe"],
            mode=request.mode, signal_source=request.signalSource,
        )
        approval = _guard(services.approvals).save(  # type: ignore[union-attr]
            run=run, config_id=config["configId"], universe_id=request.universeId,
            mode=request.mode, signal_source=request.signalSource,
        )
        if services.deployment_changed:
            services.deployment_changed(run["market"])
        return {"approval": approval, "configuration": config, "deployment": deployment}

    @router.get("/{run_id}/approvals")
    def list_backtest_approvals(run_id: str) -> dict[str, Any]:
        if not services.approvals:
            return {"approvals": []}
        try:
            _guard(services.runs).get(run_id)
        except (KeyError, ValueError) as error:
            raise HTTPException(status_code=404, detail="Backtest run was not found") from error
        return {"approvals": _guard(services.approvals).list_run(run_id)}

    @router.delete("/{run_id}")
    def cancel_backtest(run_id: str) -> dict[str, Any]:
        try:
            return _guard(services.runner).cancel(run_id)
        except (KeyError, ValueError) as error:
            raise HTTPException(status_code=404, detail="Backtest run was not found") from error

    @router.get("/{run_id}/trades")
    def list_backtest_trades(
        run_id: str,
        symbol: str | None = Query(default=None),
        status: str | None = None,
        sort: str = "entryTimestamp",
        direction: str = "asc",
        limit: int = Query(default=500, ge=1, le=5_000),
        offset: int = Query(default=0, ge=0),
    ) -> dict[str, Any]:
        trades = _guard(services.trades)
        try:
            _guard(services.runs).get(run_id)
        except (KeyError, ValueError) as error:
            raise HTTPException(status_code=404, detail="Backtest run was not found") from error
        symbol_filter = symbol.strip().upper() if symbol else None
        status_filter = status.strip().upper() if status else None
        try:
            page = trades.list(
                run_id,
                symbol=symbol_filter,
                status=status_filter,
                sort_by=sort,
                direction=direction,
                limit=limit,
                offset=offset,
            )
            total = trades.count(run_id, symbol=symbol_filter, status=status_filter)
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        return {"runId": run_id, "trades": page, "total": total, "limit": limit, "offset": offset}

    return router
