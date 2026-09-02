"""Backtest API: create runs as background jobs, poll them, cancel them, page their trades."""

from __future__ import annotations

from datetime import date
from typing import Any, Callable

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from backend.backtest.engine import BacktestRequest, ExecutionSettings
from backend.backtest.jobs import BacktestJobRunner
from backend.core.models import MARKETS
from backend.data.database import DatabaseUnavailable
from backend.data.repositories import BacktestRunRepository, BacktestTradeRepository
from backend.strategies.registry import StrategyRegistry

MAX_SYMBOLS = 2_000


class BacktestCreateRequest(BaseModel):
    market: str = Field(pattern="^(NSE|CRYPTO)$")
    strategyId: str = Field(min_length=1, max_length=80)
    symbols: list[str] = Field(min_length=1, max_length=MAX_SYMBOLS)
    timeframe: str = Field(default="5m", min_length=1, max_length=8)
    startDate: date
    endDate: date
    configuration: dict[str, Any] = Field(default_factory=dict)
    execution: dict[str, Any] = Field(default_factory=dict)


class BacktestServices:
    """Everything the router needs; resolved lazily so a missing database fails closed per request."""

    def __init__(
        self,
        *,
        registry: StrategyRegistry,
        runs: Callable[[], BacktestRunRepository],
        trades: Callable[[], BacktestTradeRepository],
        runner: Callable[[], BacktestJobRunner],
    ) -> None:
        self.registry = registry
        self._runs = runs
        self._trades = trades
        self._runner = runner

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
        symbols = sorted({symbol.strip().upper() for symbol in request.symbols if symbol.strip()})
        if not symbols:
            raise HTTPException(status_code=422, detail="At least one symbol is required")
        try:
            snapshot = strategy.resolve(request.configuration) if hasattr(strategy, "resolve") else dict(request.configuration)
            strategy.validate_config(snapshot)
            execution = ExecutionSettings.from_mapping(request.execution)
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        runs = _guard(services.runs)
        record = runs.create(
            market=request.market,
            strategy_id=strategy.strategy_id,
            strategy_version=strategy.version,
            configuration_snapshot=snapshot,
            execution_settings=execution.public(),
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
        limit: int = Query(default=500, ge=1, le=5_000),
        offset: int = Query(default=0, ge=0),
    ) -> dict[str, Any]:
        trades = _guard(services.trades)
        try:
            _guard(services.runs).get(run_id)
        except (KeyError, ValueError) as error:
            raise HTTPException(status_code=404, detail="Backtest run was not found") from error
        return {"runId": run_id, "trades": trades.list(run_id, symbol=symbol.strip().upper() if symbol else None, limit=limit, offset=offset), "total": trades.count(run_id), "limit": limit, "offset": offset}

    return router
