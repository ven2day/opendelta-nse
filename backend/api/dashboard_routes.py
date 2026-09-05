"""Dashboard API: one call per market with everything the landing page shows."""

from __future__ import annotations

from typing import Any, Callable

from fastapi import APIRouter, HTTPException, Query

from backend.core.models import MARKETS
from backend.data.database import DatabaseUnavailable


def create_dashboard_router(
    *,
    overview: Callable[[str], dict[str, Any]],
    screener_runs: Callable[[str], list[dict[str, Any]]],
    backtest_runs: Callable[[str], list[dict[str, Any]]],
    engine_health: Callable[[str], dict[str, Any]],
    paper_summary: Callable[[str], dict[str, Any]],
    paper_positions: Callable[[str], list[dict[str, Any]]],
    active_universe: Callable[[str], dict[str, Any] | None],
) -> APIRouter:
    router = APIRouter(prefix="/v2/dashboard", tags=["dashboard"])

    def _section(loader: Callable[[], Any]) -> dict[str, Any]:
        try:
            return {"available": True, "data": loader()}
        except DatabaseUnavailable as error:
            return {"available": False, "error": str(error), "data": None}
        except Exception as error:  # noqa: BLE001 - one broken section must not blank the dashboard
            return {"available": False, "error": str(error)[:240], "data": None}

    @router.get("")
    def dashboard(market: str = Query(default="NSE")) -> dict[str, Any]:
        key = market.strip().upper()
        if key not in MARKETS:
            raise HTTPException(status_code=422, detail="market must be NSE or CRYPTO")
        return {
            "market": key,
            "marketData": _section(lambda: overview(key)),
            "screener": _section(lambda: {"latestRun": (screener_runs(key) or [None])[0], "activeUniverse": active_universe(key)}),
            "backtests": _section(lambda: {"recent": backtest_runs(key)}),
            "signalEngine": _section(lambda: engine_health(key)),
            "paper": _section(lambda: {"account": paper_summary(key), "openPositions": paper_positions(key)}),
            "paperOnly": True,
            "liveOrdersEnabled": False,
        }

    return router
