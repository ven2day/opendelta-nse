from __future__ import annotations

from backend.monitoring.audit import install_audit_middleware
from backend.monitoring.notifications import NotificationDispatcher, WebhookNotificationAdapter
from fastapi import FastAPI
from fastapi.testclient import TestClient


class AuditRepository:
    def __init__(self) -> None:
        self.audits = []
        self.deliveries = []

    def append_audit(self, **values):
        self.audits.append(values)

    def record_delivery(self, alert_id, provider, *, status, error=None):
        self.deliveries.append((alert_id, provider, status, error))


class WorkingAdapter:
    provider = "UI"

    def send(self, alert):
        assert "secret" not in alert


class FailingAdapter:
    provider = "WEBHOOK"

    def send(self, _alert):
        raise RuntimeError("bounded delivery failure")


def test_mutations_are_audited_without_request_body_or_secrets() -> None:
    repository = AuditRepository()
    app = FastAPI()
    install_audit_middleware(app, lambda: repository)

    @app.post("/v2/backtests")
    def create_backtest() -> dict:
        return {"runId": "run"}

    client = TestClient(app)
    response = client.post(
        "/v2/backtests",
        headers={"x-request-id": "request-1", "x-opendelta-actor": "researcher"},
        json={"apiSecret": "never-audited"},
    )
    assert response.status_code == 200
    event = repository.audits[0]
    assert event["action"] == "BACKTEST_CREATED"
    assert event["actor_id"] == "researcher"
    assert event["details"] == {"pathTemplate": "/v2/backtests", "statusCode": 200}
    assert "never-audited" not in str(event)


def test_notifications_are_provider_neutral_and_failure_is_durable() -> None:
    repository = AuditRepository()
    dispatcher = NotificationDispatcher(repository, [WorkingAdapter(), FailingAdapter()])
    dispatcher.dispatch({"alertId": "alert-1", "notificationDue": True})
    assert repository.deliveries[0][1:3] == ("UI", "SENT")
    assert repository.deliveries[1][1:3] == ("WEBHOOK", "FAILED")


def test_webhook_configuration_requires_https() -> None:
    try:
        WebhookNotificationAdapter("http://example.com/alerts")
    except ValueError as error:
        assert "HTTPS" in str(error)
    else:
        raise AssertionError("HTTP notification URLs must fail closed")
