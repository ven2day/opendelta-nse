"""Health aggregation and leased production-monitoring workers."""

from __future__ import annotations

import os
import socket
import threading
import uuid
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from typing import Any

from backend.monitoring.notifications import NotificationDispatcher
from backend.monitoring.repository import MonitoringRepository


class MonitoringService:
    def __init__(
        self,
        repository: MonitoringRepository,
        *,
        platform_status: Callable[[], Mapping[str, Any]],
        market_overview: Callable[[str], Mapping[str, Any]],
        connection_status: Callable[[], Mapping[str, Any]],
        strategy_runner_status: Callable[[], Mapping[str, Any]],
        queue_capacity: Callable[[], Mapping[str, Any]],
        notifications: NotificationDispatcher | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.repository = repository
        self.platform_status = platform_status
        self.market_overview = market_overview
        self.connection_status = connection_status
        self.strategy_runner_status = strategy_runner_status
        self.queue_capacity = queue_capacity
        self.notifications = notifications or NotificationDispatcher.from_environment(repository)
        self.clock = clock or (lambda: datetime.now(UTC))

    def health(self) -> dict[str, Any]:
        self.repository.expire_stale_leases(now=self.clock())
        nse = _safe_call(lambda: self.market_overview("NSE"))
        crypto = _safe_call(lambda: self.market_overview("CRYPTO"))
        platform = _safe_call(self.platform_status)
        connections = _safe_call(self.connection_status)
        strategy_runner = _safe_call(self.strategy_runner_status)
        queues = {**self.repository.queue_counts(), **dict(_safe_call(self.queue_capacity))}
        leases = self.repository.leases(limit=200)
        alerts = self.repository.alerts(limit=200)
        active_alerts = [item for item in alerts if item["status"] != "RESOLVED"]
        unhealthy = any(item["severity"] == "CRITICAL" for item in active_alerts)
        degraded = unhealthy or bool(active_alerts)
        return {
            "overall": "UNHEALTHY" if unhealthy else "DEGRADED" if degraded else "HEALTHY",
            "generatedAt": self.clock().isoformat(),
            "marketData": {"NSE": nse, "CRYPTO": crypto},
            "platform": platform,
            "workerLeases": leases,
            "queues": queues,
            "strategyRunner": strategy_runner,
            "exchangeConnections": connections,
            "activeAlerts": active_alerts,
        }

    def collect(self) -> list[dict[str, Any]]:
        now = self.clock()
        self.repository.expire_stale_leases(now=now)
        raised: list[dict[str, Any]] = []
        platform = _safe_call(self.platform_status)
        if platform.get("databaseConfigured") is False:
            raised.append(
                self._alert(
                    alert_type="DATABASE_UNAVAILABLE",
                    severity="CRITICAL",
                    source="database",
                    title="Platform database is unavailable",
                    message="The unified platform database is not configured or failed its startup check.",
                    context={},
                )
            )
        market_data = {
            "NSE": _safe_call(lambda: self.market_overview("NSE")),
            "CRYPTO": _safe_call(lambda: self.market_overview("CRYPTO")),
        }
        for market, overview in market_data.items():
            freshness = overview.get("dataFreshness", {}) if isinstance(overview, Mapping) else {}
            status = freshness.get("status") if isinstance(freshness, Mapping) else "UNAVAILABLE"
            reason = freshness.get("reason") if isinstance(freshness, Mapping) else "STATUS_UNAVAILABLE"
            if status == "STALE":
                raised.append(
                    self._alert(
                        alert_type=f"STALE_{market}_DATA",
                        severity="CRITICAL" if market == "CRYPTO" else "WARNING",
                        source="market-data",
                        title=f"{market} market data is stale",
                        message=f"Market-data freshness check reported {reason}.",
                        context={"market": market, "reason": reason},
                    )
                )
            if reason in {"NO_MARKET_DATA", "NO_COMPLETED_CANDLE"}:
                raised.append(
                    self._alert(
                        alert_type="MISSING_CANDLES",
                        severity="WARNING",
                        source="market-data",
                        title=f"{market} candles are unavailable",
                        message=f"Canonical candle coverage reported {reason}.",
                        context={"market": market, "reason": reason},
                    )
                )

        connections = _safe_call(self.connection_status)
        for connection in connections.get("connections", []) if isinstance(connections, Mapping) else []:
            if connection.get("status") not in {"CONNECTED", "CONFIGURED"} or connection.get("lastTestSuccess") is False:
                provider = str(connection.get("provider", "")).upper()
                if provider in {"OKX", "VALR"}:
                    raised.append(
                        self._alert(
                            alert_type=f"{provider}_DISCONNECTION",
                            severity="WARNING",
                            source="exchange-connections",
                            title=f"{provider} private connection is unavailable",
                            message="The last private connection check did not succeed.",
                            context={"provider": provider, "connectionId": connection.get("connectionId")},
                        )
                    )
        dhan = connections.get("dhan", {}) if isinstance(connections, Mapping) else {}
        if isinstance(dhan, Mapping) and dhan.get("configured") and dhan.get("status") not in {"CONNECTED", "CONFIGURED"}:
            raised.append(
                self._alert(
                    alert_type="DHAN_DISCONNECTION",
                    severity="WARNING",
                    source="exchange-connections",
                    title="Dhan connection is unavailable",
                    message="The deployment-managed Dhan connection is not healthy.",
                    context={"provider": "DHAN"},
                )
            )

        runner = _safe_call(self.strategy_runner_status)
        if not runner.get("available", False):
            raised.append(
                self._alert(
                    alert_type="STRATEGY_RUNNER_UNAVAILABLE",
                    severity="CRITICAL",
                    source="strategy-runner",
                    title="Strategy runner is unavailable",
                    message="The isolated strategy runner socket is unavailable.",
                    context={},
                )
            )

        queues = self.queue_capacity()
        for name, alert_type in (
            ("backtests", "BACKTEST_QUEUE_SATURATION"),
            ("research", "RESEARCH_QUEUE_SATURATION"),
        ):
            queue = queues.get(name, {})
            pending, limit = int(queue.get("pending", 0)), int(queue.get("limit", 0))
            if limit and pending / limit >= 0.8:
                raised.append(
                    self._alert(
                        alert_type=alert_type,
                        severity="WARNING",
                        source="job-queues",
                        title=f"{name.title()} queue is near capacity",
                        message=f"{pending} of {limit} bounded queue slots are occupied.",
                        context={"taskKey": name, "pending": pending, "limit": limit},
                    )
                )

        for lease in self.repository.leases(limit=200):
            if lease["status"] in {"EXPIRED", "FAILED"}:
                raised.append(
                    self._alert(
                        alert_type="FAILED_WORKER_CYCLE",
                        severity="WARNING",
                        source="worker-leases",
                        title=f"{lease['workerType']} worker lease {lease['status'].lower()}",
                        message=lease.get("lastError") or "The worker stopped heartbeating before its lease expired.",
                        context={"workerType": lease["workerType"], "taskKey": lease["taskKey"]},
                    )
                )
                if lease["status"] == "FAILED":
                    raised.append(
                        self._alert(
                            alert_type="REPEATED_CYCLE_FAILURE",
                            severity="CRITICAL",
                            source="worker-leases",
                            title=f"{lease['workerType']} worker cycle is repeatedly failing",
                            message=lease.get("lastError") or "The worker recorded a failed lease.",
                            context={"workerType": lease["workerType"], "taskKey": lease["taskKey"]},
                        )
                    )
        return raised

    def _alert(self, **values: Any) -> dict[str, Any]:
        alert = self.repository.raise_alert(**values, now=self.clock())
        self.notifications.dispatch(alert)
        return alert


class MonitoringWorker:
    def __init__(
        self,
        service: MonitoringService,
        *,
        interval_seconds: float = 30.0,
        lease_ttl_seconds: int = 90,
    ) -> None:
        self.service = service
        self.interval_seconds = max(5.0, interval_seconds)
        self.lease_ttl_seconds = max(15, lease_ttl_seconds)
        self.owner_token = str(uuid.uuid4())
        self.worker_identity = os.environ.get("MONITORING_WORKER_ID", "platform-monitor")
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self.run, name="production-monitoring", daemon=True)
        self._thread.start()

    def run(self) -> None:
        host = socket.gethostname()
        process = str(os.getpid())
        lease = self.service.repository.acquire_lease(
            worker_type="MONITORING",
            task_key="health-collection",
            worker_identity=self.worker_identity,
            host_identity=host,
            process_identity=process,
            owner_token=self.owner_token,
            ttl_seconds=self.lease_ttl_seconds,
            current_task={"cycle": "health-collection"},
        )
        if lease is None:
            return
        error: str | None = None
        try:
            while not self._stop.is_set():
                try:
                    self.service.collect()
                    error = None
                    current = self.service.repository.heartbeat(
                        "MONITORING",
                        "health-collection",
                        self.owner_token,
                        ttl_seconds=self.lease_ttl_seconds,
                        current_task={"cycle": "health-collection"},
                    )
                    if current is None:
                        break
                except Exception as failure:  # noqa: BLE001 - persist and retry the monitoring cycle
                    error = str(failure)[:500]
                    alert = self.service.repository.raise_alert(
                        alert_type="FAILED_WORKER_CYCLE",
                        severity="WARNING",
                        source="monitoring",
                        title="Monitoring cycle failed",
                        message="The monitoring cycle failed and will retry.",
                        context={"workerType": "MONITORING", "taskKey": "health-collection"},
                    )
                    self.service.notifications.dispatch(alert)
                self._stop.wait(self.interval_seconds)
        finally:
            self.service.repository.release_lease(
                "MONITORING", "health-collection", self.owner_token, error=error
            )

    def stop(self, timeout: float = 10.0) -> None:
        self._stop.set()
        if self._thread and self._thread is not threading.current_thread():
            self._thread.join(timeout=timeout)


def strategy_runner_health() -> dict[str, Any]:
    path = os.environ.get("STRATEGY_V2_RUNNER_SOCKET", "/run/opendelta-strategy/runner.sock")
    return {"available": os.path.exists(path), "transport": "unix-socket", "networkless": True}


def _safe_call(call: Callable[[], Mapping[str, Any]]) -> dict[str, Any]:
    try:
        return dict(call())
    except Exception as error:  # noqa: BLE001 - health surfaces degrade without exposing internals
        return {"status": "UNAVAILABLE", "reason": type(error).__name__}
