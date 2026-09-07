from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from backend.monitoring.leases import WorkerLeaseGuard
from backend.monitoring.service import MonitoringService

NOW = datetime(2026, 9, 7, 12, 0, tzinfo=UTC)


class FakeRepository:
    def __init__(self) -> None:
        self.raised: list[dict[str, Any]] = []
        self.released: list[tuple[str, str, str | None]] = []
        self.active_lease = False

    def expire_stale_leases(self, **_):
        return 0

    def leases(self, **_):
        return [{
            "workerType": "BACKTEST", "taskKey": "old-run", "status": "EXPIRED",
            "lastError": None, "leaseId": "lease", "workerIdentity": "worker", "hostIdentity": "host",
            "processIdentity": "1", "ownerFingerprint": "12345678", "acquiredAt": NOW.isoformat(),
            "heartbeatAt": NOW.isoformat(), "expiresAt": NOW.isoformat(), "currentTask": {}, "updatedAt": NOW.isoformat(),
        }]

    def alerts(self, **_):
        return self.raised

    def audit_events(self, **_):
        return []

    def queue_counts(self):
        return {"backtests": {"queued": 0, "running": 0}, "research": {"queued": 0, "running": 0}, "walkForward": {"active": 0}}

    def raise_alert(self, **values):
        alert = {**values, "alertId": str(len(self.raised) + 1), "notificationDue": True, "status": "OPEN"}
        self.raised.append(alert)
        return alert

    def record_delivery(self, *_args, **_kwargs):
        return None

    def acquire_lease(self, **_):
        if self.active_lease:
            return None
        self.active_lease = True
        return {"leaseId": "lease"}

    def heartbeat(self, *_args, **_kwargs):
        return {"leaseId": "lease"} if self.active_lease else None

    def release_lease(self, worker_type, task_key, _owner, *, error=None):
        self.released.append((worker_type, task_key, error))
        self.active_lease = False
        return True


class Notifications:
    def __init__(self) -> None:
        self.items = []

    def dispatch(self, alert):
        self.items.append(alert)


def service(repository: FakeRepository, notifications: Notifications) -> MonitoringService:
    return MonitoringService(
        repository,
        platform_status=lambda: {"databaseConfigured": True},
        market_overview=lambda market: {
            "dataFreshness": {
                "status": "STALE" if market == "CRYPTO" else "FRESH",
                "reason": "MARKET_24_7_DATA_LAGGING" if market == "CRYPTO" else "MARKET_OPEN",
            }
        },
        connection_status=lambda: {
            "connections": [{"provider": "OKX", "connectionId": "okx", "status": "ERROR", "lastTestSuccess": False}],
            "dhan": {"configured": True, "status": "CONNECTED"},
        },
        live_status=lambda: {
            "intents": [{"intentId": "intent", "provider": "OKX", "state": "UNKNOWN"}],
            "emergencyStops": [{"stopId": "stop", "scopeType": "GLOBAL", "active": True, "reason": "Operator"}],
        },
        strategy_runner_status=lambda: {"available": False, "networkless": True},
        queue_capacity=lambda: {
            "backtests": {"pending": 9, "limit": 10}, "research": {"pending": 0, "limit": 10}
        },
        notifications=notifications,
        clock=lambda: NOW,
    )


def test_collection_emits_required_conditions_without_credentials() -> None:
    repository, notifications = FakeRepository(), Notifications()
    raised = service(repository, notifications).collect()
    kinds = {item["alert_type"] for item in raised}
    assert {
        "STALE_CRYPTO_DATA", "OKX_DISCONNECTION", "STRATEGY_RUNNER_UNAVAILABLE",
        "BACKTEST_QUEUE_SATURATION", "UNKNOWN_LIVE_ORDER_STATE", "EMERGENCY_STOP_ACTIVATED",
        "FAILED_WORKER_CYCLE",
    } <= kinds
    assert notifications.items == raised
    assert "credential" not in str(raised).lower()


def test_health_is_status_aware_and_includes_durable_surfaces() -> None:
    repository, notifications = FakeRepository(), Notifications()
    payload = service(repository, notifications).health()
    assert payload["overall"] == "HEALTHY"
    assert payload["strategyRunner"]["available"] is False
    assert payload["workerLeases"][0]["status"] == "EXPIRED"
    assert "queues" in payload and "exchangeConnections" in payload


def test_worker_lease_guard_is_single_owner_and_releases() -> None:
    repository = FakeRepository()
    with WorkerLeaseGuard(
        repository, worker_type="SIGNAL", task_key="NSE:strategy", worker_identity="signal-worker", ttl_seconds=30
    ) as first:
        assert first.acquired
        with WorkerLeaseGuard(
            repository, worker_type="SIGNAL", task_key="NSE:strategy", worker_identity="other", ttl_seconds=30
        ) as second:
            assert not second.acquired
    assert repository.released[0][:2] == ("SIGNAL", "NSE:strategy")
