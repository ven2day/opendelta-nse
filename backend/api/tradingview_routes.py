"""HTTP boundary for TradingView alerts."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from backend.core.models import MARKETS
from backend.data.database import DatabaseUnavailable
from backend.integrations.tradingview import TradingViewAlert, TradingViewIngestionService, TradingViewRejected


def create_tradingview_router(service: TradingViewIngestionService) -> APIRouter:
    router = APIRouter(prefix="/v2/integrations/tradingview", tags=["integrations"])

    @router.get("/status")
    def status(market: str = Query(...), strategy: str = Query(...)) -> dict:
        key = market.strip().upper()
        if key not in MARKETS:
            raise HTTPException(status_code=422, detail="market must be NSE or CRYPTO")
        try:
            return service.status(key, strategy.strip())
        except DatabaseUnavailable as error:
            raise HTTPException(status_code=503, detail=str(error)) from error

    @router.post("/webhook", status_code=202)
    def webhook(alert: TradingViewAlert) -> dict:
        try:
            return service.ingest(alert)
        except TradingViewRejected as error:
            raise HTTPException(status_code=error.status_code, detail=error.detail) from error
        except DatabaseUnavailable as error:
            raise HTTPException(status_code=503, detail=str(error)) from error

    return router
