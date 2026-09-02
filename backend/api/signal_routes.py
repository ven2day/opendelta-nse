"""Signals API: list stored signals and report live-engine health per market."""

from __future__ import annotations

from typing import Any, Callable

from fastapi import APIRouter, HTTPException, Query

from backend.core.models import MARKETS
from backend.data.database import DatabaseUnavailable
from backend.data.repositories import SIGNAL_STATUSES, EngineStatusRepository, LiveSignalRepository

SIGNAL_COLOURS = {"STRONG_BUY": "blue", "HOLDING": "orange", "TARGET_HIT": "green", "EXITED": "red", "EXPIRED": "red"}


def create_signal_router(
    *,
    signals: Callable[[], LiveSignalRepository],
    engine_status: Callable[[], EngineStatusRepository],
    worker_status: Callable[[str], dict[str, Any] | None],
) -> APIRouter:
    router = APIRouter(prefix="/v2/signals", tags=["signals"])

    def _market(value: str | None) -> str | None:
        key = value.strip().upper() if value else None
        if key and key not in MARKETS:
            raise HTTPException(status_code=422, detail="market must be NSE or CRYPTO")
        return key

    def _guard(callable_: Callable[[], Any]) -> Any:
        try:
            return callable_()
        except DatabaseUnavailable as error:
            raise HTTPException(status_code=503, detail=str(error)) from error

    @router.get("")
    def list_signals(
        market: str | None = Query(default=None),
        status: str | None = Query(default=None),
        symbol: str | None = Query(default=None),
        limit: int = Query(default=200, ge=1, le=2_000),
    ) -> dict[str, Any]:
        market_key = _market(market)
        status_key = status.strip().upper() if status else None
        if status_key and status_key not in SIGNAL_STATUSES:
            raise HTTPException(status_code=422, detail="status must be one of " + ", ".join(SIGNAL_STATUSES))
        rows = _guard(signals).list(market_key, status=status_key, symbol=symbol.strip().upper() if symbol else None, limit=limit)
        return {"signals": [{**row, "colour": SIGNAL_COLOURS[row["status"]]} for row in rows], "colours": SIGNAL_COLOURS, "paperOnly": True, "liveOrdersEnabled": False}

    @router.get("/health")
    def signal_health(market: str | None = Query(default=None)) -> dict[str, Any]:
        market_key = _market(market)
        stored = _guard(engine_status).list()
        if market_key:
            stored = [row for row in stored if row["market"] == market_key]
        live = {key: worker_status(key) for key in (MARKETS if market_key is None else (market_key,))}
        return {"engines": stored, "workers": {key: value for key, value in live.items() if value is not None}, "paperOnly": True, "liveOrdersEnabled": False}

    return router
