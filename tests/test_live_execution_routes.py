from __future__ import annotations

from backend.api.live_execution_routes import create_live_execution_router
from backend.live.service import GateFailure
from fastapi import FastAPI
from fastapi.testclient import TestClient


class StubRepository:
    def list_intents(self, *, limit):
        return [{"intentId": "intent-1", "state": "BLOCKED"}][:limit]

    def get_intent(self, intent_id):
        if intent_id != "intent-1":
            raise KeyError("Live order intent was not found")
        return {"intentId": intent_id, "state": "BLOCKED"}


class StubService:
    def __init__(self) -> None:
        self.repository = StubRepository()
        self.config = type("Config", (), {"live_trading_enabled": False})()
        self.orders = 0

    def status(self):
        return {
            "liveTradingEnabled": False,
            "deploymentPermission": False,
            "environmentAllowed": False,
            "defaultState": "Live trading disabled",
            "deployments": [],
            "riskPolicies": [],
            "eligiblePaperApprovals": [],
            "emergencyStops": [],
            "intents": [],
            "cancelAllSupported": False,
            "cancelAllMessage": "No implicit cancel-all",
        }

    def submit_order(self, *_args):
        self.orders += 1
        return {"intentId": "intent-1", "state": "BLOCKED", "blockedReasons": ["Live trading disabled"]}

    def activate_deployment(self, *_args, **_kwargs):
        raise GateFailure(["Global LIVE_TRADING_ENABLED flag is false"])

    def set_emergency_stop(self, **values):
        return {"active": values["active"], "scopeType": values["scope_type"], "scopeKey": values["scope_key"]}


def client_for(service: StubService) -> TestClient:
    app = FastAPI()
    app.router.routes.extend(create_live_execution_router(lambda: service).routes)
    return TestClient(app)


def test_status_is_explicitly_disabled_and_exposes_no_enable_endpoint() -> None:
    service = StubService()
    client = client_for(service)
    response = client.get("/v2/live-execution/status")
    assert response.status_code == 200
    assert response.json()["defaultState"] == "Live trading disabled"
    paths = {route.path for route in client.app.routes}
    assert "/v2/live-execution/enable" not in paths
    assert not any("approval" in path for path in paths)


def test_order_schema_rejects_credentials_and_invalid_quantity_before_service() -> None:
    service = StubService()
    response = client_for(service).post(
        "/v2/live-execution/orders",
        json={
            "liveDeploymentId": "deployment",
            "signalId": "signal",
            "symbol": "BTC-USDT",
            "side": "BUY",
            "orderType": "LIMIT",
            "quantity": 0,
            "price": 100,
            "providerFields": {"apiSecret": "must-never-be-accepted"},
        },
    )
    assert response.status_code == 422
    assert service.orders == 0


def test_activation_gate_returns_stable_conflict_and_reconciliation_is_idle() -> None:
    client = client_for(StubService())
    response = client.post("/v2/live-execution/deployments/deployment/activate", json={"confirmation": "ENABLE LIVE x"})
    assert response.status_code == 409
    assert response.json()["detail"]["message"] == "Live execution gate failed"
    reconciliation = client.post("/v2/live-execution/reconciliation/run")
    assert reconciliation.status_code == 409


def test_emergency_stop_requires_bounded_explicit_input() -> None:
    client = client_for(StubService())
    response = client.post(
        "/v2/live-execution/emergency-stops",
        json={
            "scopeType": "GLOBAL",
            "scopeKey": "*",
            "active": True,
            "reason": "Operator stop",
            "confirmation": "ACTIVATE EMERGENCY STOP",
        },
    )
    assert response.status_code == 200
    assert response.json() == {"active": True, "scopeType": "GLOBAL", "scopeKey": "*"}
