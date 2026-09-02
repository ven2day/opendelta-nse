"""Settings API: strategy catalogue for the dropdown and dynamic settings forms."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from backend.core.models import MARKETS
from backend.strategies.registry import StrategyRegistry


def create_settings_router(registry: StrategyRegistry) -> APIRouter:
    router = APIRouter(prefix="/v2", tags=["settings"])

    @router.get("/strategies")
    def list_strategies(market: str | None = Query(default=None)) -> dict[str, Any]:
        market_key = market.strip().upper() if market else None
        if market_key and market_key not in MARKETS:
            raise HTTPException(status_code=422, detail="market must be NSE or CRYPTO")
        return {"strategies": registry.describe(market_key), "markets": list(MARKETS)}

    return router
