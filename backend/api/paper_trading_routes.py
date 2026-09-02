"""Paper trading API: accounts, balances, positions, orders and trades per market."""

from __future__ import annotations

from typing import Any, Callable

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from backend.core.models import MARKETS
from backend.data.database import DatabaseUnavailable
from backend.paper_trading.broker import PaperBroker


class PaperAccountRequest(BaseModel):
    market: str = Field(pattern="^(NSE|CRYPTO)$")
    startingBalance: float | None = Field(default=None, gt=0)


class ManualCloseRequest(BaseModel):
    price: float = Field(gt=0)


def create_paper_trading_router(broker_for: Callable[[str], PaperBroker]) -> APIRouter:
    router = APIRouter(prefix="/v2/paper", tags=["paper-trading"])

    def _broker(market: str) -> PaperBroker:
        key = market.strip().upper()
        if key not in MARKETS:
            raise HTTPException(status_code=422, detail="market must be NSE or CRYPTO")
        try:
            return broker_for(key)
        except DatabaseUnavailable as error:
            raise HTTPException(status_code=503, detail=str(error)) from error

    @router.get("/accounts")
    def list_accounts() -> dict[str, Any]:
        return {"accounts": [_broker(market).summary() for market in MARKETS], "paperOnly": True, "liveOrdersEnabled": False}

    @router.post("/accounts", status_code=201)
    def create_account(request: PaperAccountRequest) -> dict[str, Any]:
        broker = _broker(request.market)
        if request.startingBalance is not None and float(broker.account["startingBalance"]) != request.startingBalance and broker.portfolio.open_count() == 0:
            broker.reset(starting_balance=request.startingBalance)
        return broker.summary()

    @router.post("/accounts/{market}/reset")
    def reset_account(market: str, request: PaperAccountRequest | None = None) -> dict[str, Any]:
        broker = _broker(market)
        broker.reset(starting_balance=request.startingBalance if request else None)
        return broker.summary()

    @router.get("/accounts/{market}")
    def account_summary(market: str) -> dict[str, Any]:
        return _broker(market).summary()

    @router.get("/positions")
    def positions(market: str = Query(...)) -> dict[str, Any]:
        broker = _broker(market)
        return {"market": broker.market.market, "positions": broker.positions(), "paperOnly": True}

    @router.get("/orders")
    def orders(market: str = Query(...), limit: int = Query(default=200, ge=1, le=2_000)) -> dict[str, Any]:
        broker = _broker(market)
        return {"market": broker.market.market, "orders": broker.repositories.orders.list(broker.account["accountId"], limit=limit)}

    @router.get("/trades")
    def trades(market: str = Query(...), limit: int = Query(default=500, ge=1, le=5_000)) -> dict[str, Any]:
        broker = _broker(market)
        return {"market": broker.market.market, "trades": broker.repositories.trades.list(broker.account["accountId"], limit=limit)}

    @router.get("/lots")
    def lots(market: str = Query(...), status: str | None = Query(default=None), limit: int = Query(default=500, ge=1, le=5_000)) -> dict[str, Any]:
        broker = _broker(market)
        return {"market": broker.market.market, "lots": broker.repositories.lots.list(broker.account["accountId"], status=status.strip().upper() if status else None, limit=limit)}

    @router.post("/lots/{lot_id}/close")
    def close_lot(lot_id: str, request: ManualCloseRequest, market: str = Query(...)) -> dict[str, Any]:
        broker = _broker(market)
        try:
            return broker.close_lot_manually(lot_id, price=request.price)
        except KeyError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error

    return router
