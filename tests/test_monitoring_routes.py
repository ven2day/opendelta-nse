from __future__ import annotations

from backend.api.monitoring_routes import create_monitoring_router
from fastapi import FastAPI
from fastapi.testclient import TestClient


class Repository:
    def leases(self, *, limit):
        return [{"leaseId": "lease", "status": "ACTIVE"}][:limit]

    def alerts(self, *, status=None, limit=100):
        return [{"alertId": "alert", "status": status or "OPEN"}][:limit]

    def acknowledge_alert(self, alert_id, *, actor):
        return {"alertId": alert_id, "status": "ACKNOWLEDGED", "acknowledgedBy": actor}

    def resolve_alert(self, alert_id, *, actor, resolution):
        if not resolution:
            raise ValueError("Resolution is required")
        return {"alertId": alert_id, "status": "RESOLVED", "resolvedBy": actor, "resolution": resolution}

    def audit_events(self, **_):
        return [{"auditId": "audit", "details": {}}]


class Service:
    repository = Repository()

    def health(self):
        return {"overall": "HEALTHY", "activeAlerts": [], "workerLeases": []}


def client() -> TestClient:
    app = FastAPI()
    service = Service()
    app.router.routes.extend(create_monitoring_router(lambda: service).routes)
    return TestClient(app)


def test_monitoring_routes_are_bounded_and_health_is_read_only() -> None:
    api = client()
    assert api.get("/v2/operations/health").json()["overall"] == "HEALTHY"
    assert api.get("/v2/operations/leases?limit=201").status_code == 422
    assert api.get("/v2/operations/alerts?status=INVALID").status_code == 422
    assert api.get("/v2/operations/audit").json()["items"][0]["details"] == {}


def test_alert_acknowledgement_and_resolution_are_explicit() -> None:
    api = client()
    acknowledged = api.post("/v2/operations/alerts/alert/acknowledge", json={"actor": "operator"})
    assert acknowledged.json()["status"] == "ACKNOWLEDGED"
    missing = api.post("/v2/operations/alerts/alert/resolve", json={"actor": "operator"})
    assert missing.status_code == 422
    resolved = api.post(
        "/v2/operations/alerts/alert/resolve",
        json={"actor": "operator", "resolution": "Provider recovered"},
    )
    assert resolved.json()["resolution"] == "Provider recovered"
