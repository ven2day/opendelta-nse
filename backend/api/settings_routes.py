"""Settings API: strategy catalogue (drives dropdowns and dynamic forms) and per-market strategy/risk configuration."""

from __future__ import annotations

from typing import Any, Callable

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from backend.core.models import MARKETS
from backend.data.database import DatabaseUnavailable
from backend.data.repositories import StrategyConfigRepository
from backend.paper_trading.execution import ExecutionPolicy
from backend.strategies.registry import StrategyRegistry


RISK_SCHEMA: dict[str, dict[str, Any]] = {
    "sizingMode": {"type": "string", "default": "FIXED_QUANTITY", "enum": ["FIXED_QUANTITY", "FIXED_CAPITAL"], "label": "Position sizing"},
    "initialQuantity": {"type": "number", "default": 100, "minimum": 0.00000001, "label": "First lot quantity"},
    "capitalPerLot": {"type": "number", "default": 50_000.0, "minimum": 0.01, "label": "Capital per lot (fixed-capital sizing)"},
    "allowAdditionalBuys": {"type": "boolean", "default": True, "label": "Allow additional lots while holding"},
    "additionalQuantityPct": {"type": "number", "default": 50.0, "minimum": 0.01, "maximum": 100.0, "label": "Additional lot size %"},
    "additionalSizingMode": {"type": "string", "default": "REDUCE_EVERY_NEW_LOT", "enum": ["REDUCE_EVERY_NEW_LOT", "FIXED_PERCENTAGE_OF_FIRST_LOT"], "label": "Additional lot sizing"},
    "maximumEntriesPerCycle": {"type": "integer", "default": 10, "minimum": 1, "maximum": 100, "label": "Maximum lots per cycle"},
    "priceModel": {"type": "string", "default": "NEXT_OPEN", "enum": ["SIGNAL_CLOSE", "NEXT_OPEN"], "label": "Paper entry price"},
    "stopLossPct": {"type": "number", "default": None, "minimum": 0.01, "maximum": 99.99, "label": "Stop loss % (optional)"},
    "maximumHoldingBars": {"type": "integer", "default": None, "minimum": 1, "label": "Maximum holding bars (optional)"},
}


class StrategyConfigRequest(BaseModel):
    market: str = Field(pattern="^(NSE|CRYPTO)$")
    name: str = Field(default="default", min_length=1, max_length=120)
    configuration: dict[str, Any] = Field(default_factory=dict)
    riskSettings: dict[str, Any] = Field(default_factory=dict)
    activate: bool = True


def create_settings_router(registry: StrategyRegistry, *, configs: Callable[[], StrategyConfigRepository] | None = None) -> APIRouter:
    router = APIRouter(prefix="/v2", tags=["settings"])

    def _market(value: str | None) -> str | None:
        key = value.strip().upper() if value else None
        if key and key not in MARKETS:
            raise HTTPException(status_code=422, detail="market must be NSE or CRYPTO")
        return key

    def _repository() -> StrategyConfigRepository:
        if configs is None:
            raise HTTPException(status_code=503, detail="Strategy configuration storage is not configured")
        try:
            return configs()
        except DatabaseUnavailable as error:
            raise HTTPException(status_code=503, detail=str(error)) from error

    def _strategy(strategy_id: str):
        try:
            return registry.get(strategy_id)
        except KeyError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error

    @router.get("/strategies")
    def list_strategies(market: str | None = Query(default=None)) -> dict[str, Any]:
        market_key = _market(market)
        return {"strategies": registry.describe(market_key), "markets": list(MARKETS), "riskDefaults": ExecutionPolicy().public(), "riskSchema": RISK_SCHEMA}

    @router.get("/strategies/{strategy_id}/config")
    def get_config(strategy_id: str, market: str = Query(...)) -> dict[str, Any]:
        strategy = _strategy(strategy_id)
        key = _market(market)
        active = _repository().active(key, strategy.strategy_id)
        return {
            "strategyId": strategy.strategy_id,
            "market": key,
            "active": active,
            "effectiveConfiguration": strategy.resolve((active or {}).get("configuration")) if hasattr(strategy, "resolve") else (active or {}).get("configuration", {}),
            "effectiveRiskSettings": ExecutionPolicy.from_mapping((active or {}).get("riskSettings"), whole_units=(key == "NSE")).public(),
            "all": _repository().list(key),
        }

    @router.post("/strategies/{strategy_id}/config", status_code=201)
    def save_config(strategy_id: str, request: StrategyConfigRequest) -> dict[str, Any]:
        strategy = _strategy(strategy_id)
        if request.market not in strategy.supported_markets:
            raise HTTPException(status_code=422, detail=f"{strategy.strategy_id} does not support {request.market}")
        try:
            snapshot = strategy.resolve(request.configuration) if hasattr(strategy, "resolve") else dict(request.configuration)
            strategy.validate_config(snapshot)
            risk = ExecutionPolicy.from_mapping(request.riskSettings, whole_units=(request.market == "NSE")).public()
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        return _repository().save(market=request.market, strategy_id=strategy.strategy_id, strategy_version=strategy.version, name=request.name, configuration=snapshot, risk_settings=risk, activate=request.activate)

    return router
