"""Explicit, confirmation-gated live-execution foundation API."""

from __future__ import annotations

from collections.abc import Callable
from decimal import Decimal
from typing import Annotated, Any, Literal

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field, field_validator

from backend.data.database import DatabaseUnavailable
from backend.live.adapters import OrderRequest
from backend.live.service import GateFailure, LiveExecutionService


class RiskPolicyRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    maxOrderValue: Annotated[Decimal, Field(gt=0)]
    maxPositionValue: Annotated[Decimal, Field(gt=0)]
    maxTotalExposure: Annotated[Decimal, Field(gt=0)]
    maxOpenPositions: int = Field(ge=1, le=1000)
    maxDailyTrades: int = Field(ge=1, le=10000)
    maxDailyLoss: Annotated[Decimal, Field(gt=0)]
    maxPriceDeviationPct: Decimal = Field(ge=0, le=100)
    maxSignalAgeSeconds: int = Field(ge=1, le=86400)
    maxCandleAgeSeconds: int = Field(ge=1, le=86400)
    symbolAllowlist: list[str] = Field(min_length=1, max_length=1000)
    marketAllowlist: list[Literal["NSE", "CRYPTO"]] = Field(min_length=1, max_length=2)
    strategyAllowlist: list[str] = Field(min_length=1, max_length=100)
    timeframeAllowlist: list[str] = Field(min_length=1, max_length=20)

    @field_validator("symbolAllowlist", "strategyAllowlist", "timeframeAllowlist")
    @classmethod
    def unique_nonempty(cls, value: list[str]) -> list[str]:
        cleaned = [item.strip() for item in value]
        if any(not item for item in cleaned) or len(set(cleaned)) != len(cleaned):
            raise ValueError("All allowlist values must be non-empty and unique")
        return cleaned


class DeploymentRequest(BaseModel):
    approvalId: str
    riskPolicyId: str
    provider: Literal["DHAN", "OKX", "VALR"]
    connectionId: str | None = None


class ConfirmationRequest(BaseModel):
    confirmation: str = Field(min_length=1, max_length=200)


class OrderIntentRequest(BaseModel):
    liveDeploymentId: str
    signalId: str
    symbol: str = Field(min_length=1, max_length=80)
    side: Literal["BUY", "SELL"]
    orderType: Literal["MARKET", "LIMIT"]
    quantity: Annotated[Decimal, Field(gt=0)]
    price: Annotated[Decimal, Field(gt=0)] | None = None
    timeInForce: Literal["GTC", "IOC", "FOK", "DAY"] = "GTC"
    providerFields: dict[str, Any] = Field(default_factory=dict)

    @field_validator("providerFields")
    @classmethod
    def bounded_provider_fields(cls, value: dict[str, Any]) -> dict[str, Any]:
        if len(value) > 20 or len(str(value)) > 4000:
            raise ValueError("Provider-specific order fields exceed the safe limit")
        forbidden = {"apiKey", "apiSecret", "passphrase", "accessToken", "credentials"}
        if forbidden.intersection(value):
            raise ValueError("Credentials are not valid order fields")
        return value


class EmergencyStopRequest(BaseModel):
    scopeType: Literal["GLOBAL", "PROVIDER", "MARKET", "STRATEGY"]
    scopeKey: str = Field(min_length=1, max_length=160)
    active: bool
    reason: str = Field(min_length=1, max_length=500)
    confirmation: str = Field(min_length=1, max_length=240)


def create_live_execution_router(service_provider: Callable[[], LiveExecutionService]) -> APIRouter:
    router = APIRouter(prefix="/v2/live-execution", tags=["live-execution"])

    def service() -> LiveExecutionService:
        try:
            return service_provider()
        except DatabaseUnavailable as error:
            raise HTTPException(status_code=503, detail=str(error)) from error

    def safe(operation: Callable[[], Any]) -> Any:
        try:
            return operation()
        except KeyError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except GateFailure as error:
            raise HTTPException(
                status_code=409, detail={"message": "Live execution gate failed", "reasons": error.reasons}
            ) from error
        except (ValueError, RuntimeError) as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

    @router.get("/status")
    def status() -> dict[str, Any]:
        return service().status()

    @router.post("/risk-policies", status_code=201)
    def create_risk_policy(request: RiskPolicyRequest) -> dict[str, Any]:
        return safe(lambda: service().create_risk_policy(request.model_dump()))

    @router.post("/deployments", status_code=201)
    def create_deployment(request: DeploymentRequest) -> dict[str, Any]:
        return safe(
            lambda: service().create_deployment(
                approval_id=request.approvalId,
                risk_policy_id=request.riskPolicyId,
                provider=request.provider,
                connection_id=request.connectionId,
            )
        )

    @router.post("/deployments/{deployment_id}/activate")
    def activate_deployment(deployment_id: str, request: ConfirmationRequest) -> dict[str, Any]:
        return safe(lambda: service().activate_deployment(deployment_id, confirmation=request.confirmation))

    @router.post("/deployments/{deployment_id}/disable")
    def disable_deployment(deployment_id: str, request: ConfirmationRequest) -> dict[str, Any]:
        if request.confirmation != "DISABLE LIVE":
            raise HTTPException(status_code=422, detail="Type DISABLE LIVE to confirm")
        return safe(lambda: service().disable_deployment(deployment_id))

    @router.post("/orders", status_code=201)
    def submit_order(request: OrderIntentRequest) -> dict[str, Any]:
        order = OrderRequest(
            symbol=request.symbol,
            side=request.side,
            order_type=request.orderType,
            quantity=request.quantity,
            price=request.price,
            time_in_force=request.timeInForce,
            provider_fields=request.providerFields,
        )
        return safe(lambda: service().submit_order(request.liveDeploymentId, request.signalId, order))

    @router.get("/orders")
    def list_orders(limit: int = Query(default=100, ge=1, le=500)) -> dict[str, Any]:
        return {"items": service().repository.list_intents(limit=limit)}

    @router.get("/orders/{intent_id}")
    def get_order(intent_id: str) -> dict[str, Any]:
        return safe(lambda: service().repository.get_intent(intent_id))

    @router.post("/orders/{intent_id}/cancel")
    def cancel_order(intent_id: str, request: ConfirmationRequest) -> dict[str, Any]:
        return safe(lambda: service().cancel_order(intent_id, confirmation=request.confirmation))

    @router.post("/emergency-stops")
    def set_emergency_stop(request: EmergencyStopRequest) -> dict[str, Any]:
        return safe(
            lambda: service().set_emergency_stop(
                scope_type=request.scopeType,
                scope_key=request.scopeKey,
                active=request.active,
                reason=request.reason,
                confirmation=request.confirmation,
            )
        )

    @router.post("/reconciliation/run")
    def run_reconciliation(limit: int = Query(default=100, ge=1, le=500)) -> dict[str, int]:
        if not service().config.live_trading_enabled:
            raise HTTPException(status_code=409, detail="Live trading is disabled; reconciliation worker remains idle")
        return service().reconcile(limit=limit)

    return router
