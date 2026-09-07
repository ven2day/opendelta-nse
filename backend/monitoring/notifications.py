"""Provider-neutral operational notification adapters."""

from __future__ import annotations

import json
import logging
import os
import urllib.request
from collections.abc import Iterable, Mapping
from typing import Any, Protocol
from urllib.parse import urlparse

from backend.monitoring.repository import MonitoringRepository

logger = logging.getLogger("opendelta.monitoring.notifications")


class NotificationAdapter(Protocol):
    provider: str

    def send(self, alert: Mapping[str, Any]) -> None: ...


class UiNotificationAdapter:
    """Durable alert persistence is the UI notification channel."""

    provider = "UI"

    def send(self, alert: Mapping[str, Any]) -> None:
        del alert


class LogNotificationAdapter:
    provider = "LOG"

    def send(self, alert: Mapping[str, Any]) -> None:
        logger.warning(
            "operational_alert",
            extra={
                "alert_id": alert["alertId"],
                "alert_type": alert["alertType"],
                "severity": alert["severity"],
                "source": alert["source"],
            },
        )


class WebhookNotificationAdapter:
    provider = "WEBHOOK"

    def __init__(self, url: str, *, timeout_seconds: float = 5.0) -> None:
        parsed = urlparse(url)
        if parsed.scheme != "https" or not parsed.hostname:
            raise ValueError("Monitoring webhook URL must use HTTPS")
        if timeout_seconds <= 0 or timeout_seconds > 15:
            raise ValueError("Monitoring webhook timeout must be between 0 and 15 seconds")
        self.url = url
        self.timeout_seconds = timeout_seconds

    def send(self, alert: Mapping[str, Any]) -> None:
        safe = {
            key: alert.get(key)
            for key in ("alertId", "alertType", "severity", "source", "title", "message", "lastSeenAt")
        }
        body = json.dumps(safe, separators=(",", ":"), ensure_ascii=True).encode()
        if len(body) > 16_384:
            raise ValueError("Monitoring webhook payload is too large")
        request = urllib.request.Request(
            self.url,
            data=body,
            headers={"Content-Type": "application/json", "User-Agent": "OpenDelta-Monitoring/2"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:  # noqa: S310 - HTTPS validated
            if not 200 <= response.status < 300:
                raise RuntimeError("Monitoring webhook rejected the notification")


class NotificationDispatcher:
    def __init__(self, repository: MonitoringRepository, adapters: Iterable[NotificationAdapter]) -> None:
        self.repository = repository
        self.adapters = tuple(adapters)

    @classmethod
    def from_environment(cls, repository: MonitoringRepository) -> NotificationDispatcher:
        adapters: list[NotificationAdapter] = [UiNotificationAdapter(), LogNotificationAdapter()]
        url = os.environ.get("MONITORING_WEBHOOK_URL", "").strip()
        if url:
            adapters.append(WebhookNotificationAdapter(url))
        return cls(repository, adapters)

    def dispatch(self, alert: Mapping[str, Any]) -> None:
        if not alert.get("notificationDue"):
            return
        for adapter in self.adapters:
            try:
                adapter.send(alert)
                self.repository.record_delivery(str(alert["alertId"]), adapter.provider, status="SENT")
            except Exception as error:  # noqa: BLE001 - one notification channel never blocks another
                logger.exception("notification_delivery_failed", extra={"provider": adapter.provider})
                self.repository.record_delivery(
                    str(alert["alertId"]), adapter.provider, status="FAILED", error=str(error)
                )
