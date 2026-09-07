"""PostgreSQL persistence for production monitoring."""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Any

from backend.data.database import Database, jsonb

WORKER_TYPES = {
    "MARKET_DATA",
    "SIGNAL",
    "BACKTEST",
    "RESEARCH_EXPERIMENT",
    "WALK_FORWARD",
    "PAPER_EXECUTION",
    "MONITORING",
}


class MonitoringRepository:
    def __init__(self, database: Database) -> None:
        self.database = database

    def acquire_lease(
        self,
        *,
        worker_type: str,
        task_key: str,
        worker_identity: str,
        host_identity: str,
        process_identity: str,
        owner_token: str,
        ttl_seconds: int,
        current_task: Mapping[str, Any] | None = None,
        now: datetime | None = None,
    ) -> dict[str, Any] | None:
        worker_type = worker_type.strip().upper()
        if worker_type not in WORKER_TYPES:
            raise ValueError("Unsupported worker type")
        if not 5 <= ttl_seconds <= 3600:
            raise ValueError("Lease TTL must be between 5 and 3600 seconds")
        moment = now or datetime.now(UTC)
        token = uuid.UUID(owner_token)
        row = self.database.fetch_one(
            """
            INSERT INTO worker_leases (
                lease_id, worker_type, task_key, worker_identity, host_identity,
                process_identity, owner_token, acquired_at, heartbeat_at,
                expires_at, current_task, status
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'ACTIVE')
            ON CONFLICT (worker_type, task_key) DO UPDATE SET
                worker_identity = EXCLUDED.worker_identity,
                host_identity = EXCLUDED.host_identity,
                process_identity = EXCLUDED.process_identity,
                owner_token = EXCLUDED.owner_token,
                acquired_at = CASE
                    WHEN worker_leases.owner_token = EXCLUDED.owner_token THEN worker_leases.acquired_at
                    ELSE EXCLUDED.acquired_at END,
                heartbeat_at = EXCLUDED.heartbeat_at,
                expires_at = EXCLUDED.expires_at,
                current_task = EXCLUDED.current_task,
                status = 'ACTIVE', last_error = NULL, updated_at = EXCLUDED.heartbeat_at
            WHERE worker_leases.owner_token = EXCLUDED.owner_token
               OR worker_leases.expires_at <= EXCLUDED.heartbeat_at
               OR worker_leases.status <> 'ACTIVE'
            RETURNING *
            """,
            (
                uuid.uuid4(),
                worker_type,
                _bounded(task_key, 200, "task key"),
                _bounded(worker_identity, 200, "worker identity"),
                _bounded(host_identity, 200, "host identity"),
                _bounded(process_identity, 200, "process identity"),
                token,
                moment,
                moment,
                moment + timedelta(seconds=ttl_seconds),
                jsonb(dict(current_task or {})),
            ),
        )
        return _lease(row) if row else None

    def heartbeat(
        self,
        worker_type: str,
        task_key: str,
        owner_token: str,
        *,
        ttl_seconds: int,
        current_task: Mapping[str, Any] | None = None,
        now: datetime | None = None,
    ) -> dict[str, Any] | None:
        if not 5 <= ttl_seconds <= 3600:
            raise ValueError("Lease TTL must be between 5 and 3600 seconds")
        moment = now or datetime.now(UTC)
        row = self.database.fetch_one(
            """
            UPDATE worker_leases SET heartbeat_at = %s, expires_at = %s,
                current_task = COALESCE(%s, current_task), updated_at = %s
            WHERE worker_type = %s AND task_key = %s AND owner_token = %s
              AND status = 'ACTIVE' AND expires_at > %s
            RETURNING *
            """,
            (
                moment,
                moment + timedelta(seconds=ttl_seconds),
                jsonb(dict(current_task)) if current_task is not None else None,
                moment,
                worker_type,
                task_key,
                uuid.UUID(owner_token),
                moment,
            ),
        )
        return _lease(row) if row else None

    def release_lease(
        self, worker_type: str, task_key: str, owner_token: str, *, error: str | None = None
    ) -> bool:
        return bool(
            self.database.execute(
                """
                UPDATE worker_leases SET status = %s, last_error = %s, updated_at = now()
                WHERE worker_type = %s AND task_key = %s AND owner_token = %s AND status = 'ACTIVE'
                """,
                (
                    "FAILED" if error else "RELEASED",
                    _safe_error(error),
                    worker_type,
                    task_key,
                    uuid.UUID(owner_token),
                ),
            )
        )

    def expire_stale_leases(self, *, now: datetime | None = None) -> int:
        return self.database.execute(
            """
            UPDATE worker_leases SET status = 'EXPIRED', updated_at = %s
            WHERE status = 'ACTIVE' AND expires_at <= %s
            """,
            (now or datetime.now(UTC), now or datetime.now(UTC)),
        )

    def leases(self, *, limit: int = 200) -> list[dict[str, Any]]:
        limit = _limit(limit)
        return [
            _lease(row)
            for row in self.database.fetch_all(
                "SELECT * FROM worker_leases ORDER BY updated_at DESC LIMIT %s", (limit,)
            )
        ]

    def append_audit(
        self,
        *,
        request_id: str,
        action: str,
        actor_type: str,
        actor_id: str,
        success: bool,
        subject_type: str | None = None,
        subject_id: str | None = None,
        details: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        safe_details = _safe_details(details or {})
        row = self.database.fetch_one(
            """
            INSERT INTO operational_audit_events (
                audit_id, request_id, action, actor_type, actor_id, subject_type,
                subject_id, success, details
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING *
            """,
            (
                uuid.uuid4(),
                _bounded(request_id, 128, "request ID"),
                _bounded(action, 80, "action"),
                actor_type,
                _bounded(actor_id, 200, "actor ID"),
                subject_type,
                subject_id,
                success,
                jsonb(safe_details),
            ),
        )
        assert row is not None
        return _audit(row)

    def audit_events(self, *, limit: int = 100, before: datetime | None = None) -> list[dict[str, Any]]:
        limit = _limit(limit)
        rows = self.database.fetch_all(
            """
            SELECT * FROM operational_audit_events
            WHERE (%s::timestamptz IS NULL OR created_at < %s)
            ORDER BY created_at DESC LIMIT %s
            """,
            (before, before, limit),
        )
        return [_audit(row) for row in rows]

    def raise_alert(
        self,
        *,
        alert_type: str,
        severity: str,
        source: str,
        title: str,
        message: str,
        context: Mapping[str, Any] | None = None,
        cooldown_seconds: int = 900,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        if not 30 <= cooldown_seconds <= 86400:
            raise ValueError("Alert cooldown must be between 30 seconds and 24 hours")
        moment = now or datetime.now(UTC)
        safe_context = _safe_details(context or {})
        fingerprint = _fingerprint(alert_type, source, safe_context)
        with self.database.transaction() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT * FROM operational_alerts
                WHERE fingerprint = %s AND status <> 'RESOLVED' FOR UPDATE
                """,
                (fingerprint,),
            )
            existing = cursor.fetchone()
            if existing:
                notification_due = existing["cooldown_until"] <= moment
                cooldown_until = (
                    moment + timedelta(seconds=cooldown_seconds)
                    if notification_due
                    else existing["cooldown_until"]
                )
                cursor.execute(
                    """
                    UPDATE operational_alerts SET severity = %s, title = %s, message = %s,
                        context = %s, occurrence_count = occurrence_count + 1,
                        last_seen_at = %s, cooldown_until = %s, updated_at = %s
                    WHERE alert_id = %s RETURNING *
                    """,
                    (
                        severity,
                        title[:200],
                        message[:2000],
                        jsonb(safe_context),
                        moment,
                        cooldown_until,
                        moment,
                        existing["alert_id"],
                    ),
                )
            else:
                notification_due = True
                cursor.execute(
                    """
                    INSERT INTO operational_alerts (
                        alert_id, fingerprint, alert_type, severity, source, title,
                        message, context, status, first_seen_at, last_seen_at, cooldown_until
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'OPEN', %s, %s, %s)
                    RETURNING *
                    """,
                    (
                        uuid.uuid4(),
                        fingerprint,
                        alert_type,
                        severity,
                        source[:100],
                        title[:200],
                        message[:2000],
                        jsonb(safe_context),
                        moment,
                        moment,
                        moment + timedelta(seconds=cooldown_seconds),
                    ),
                )
            row = cursor.fetchone()
            assert row is not None
            cursor.execute(
                """
                INSERT INTO operational_alert_occurrences (occurrence_id, alert_id, context, occurred_at)
                VALUES (%s, %s, %s, %s)
                """,
                (uuid.uuid4(), row["alert_id"], jsonb(safe_context), moment),
            )
        return {**_alert(dict(row)), "notificationDue": notification_due}

    def alerts(self, *, status: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        if status is not None and status not in {"OPEN", "ACKNOWLEDGED", "RESOLVED"}:
            raise ValueError("Invalid alert status")
        rows = self.database.fetch_all(
            """
            SELECT * FROM operational_alerts WHERE (%s IS NULL OR status = %s)
            ORDER BY CASE severity WHEN 'CRITICAL' THEN 0 WHEN 'WARNING' THEN 1 ELSE 2 END,
                     last_seen_at DESC LIMIT %s
            """,
            (status, status, _limit(limit)),
        )
        return [_alert(row) for row in rows]

    def acknowledge_alert(self, alert_id: str, *, actor: str, now: datetime | None = None) -> dict[str, Any]:
        moment = now or datetime.now(UTC)
        row = self.database.fetch_one(
            """
            UPDATE operational_alerts SET status = 'ACKNOWLEDGED', acknowledged_at = %s,
                acknowledged_by = %s, updated_at = %s
            WHERE alert_id = %s AND status = 'OPEN' RETURNING *
            """,
            (moment, actor[:200], moment, uuid.UUID(alert_id)),
        )
        if row is None:
            row = self.database.fetch_one("SELECT * FROM operational_alerts WHERE alert_id = %s", (uuid.UUID(alert_id),))
        if row is None:
            raise KeyError("Alert was not found")
        return _alert(row)

    def resolve_alert(
        self, alert_id: str, *, actor: str, resolution: str, now: datetime | None = None
    ) -> dict[str, Any]:
        if not resolution.strip():
            raise ValueError("Resolution is required")
        moment = now or datetime.now(UTC)
        row = self.database.fetch_one(
            """
            UPDATE operational_alerts SET status = 'RESOLVED', resolved_at = %s,
                resolved_by = %s, resolution = %s, updated_at = %s
            WHERE alert_id = %s AND status <> 'RESOLVED' RETURNING *
            """,
            (moment, actor[:200], resolution.strip()[:1000], moment, uuid.UUID(alert_id)),
        )
        if row is None:
            row = self.database.fetch_one("SELECT * FROM operational_alerts WHERE alert_id = %s", (uuid.UUID(alert_id),))
        if row is None:
            raise KeyError("Alert was not found")
        return _alert(row)

    def record_delivery(
        self, alert_id: str, provider: str, *, status: str, error: str | None = None
    ) -> None:
        now = datetime.now(UTC)
        self.database.execute(
            """
            INSERT INTO notification_deliveries (
                delivery_id, alert_id, provider, status, attempt_count, last_error,
                attempted_at, delivered_at
            ) VALUES (%s, %s, %s, %s, 1, %s, %s, %s)
            ON CONFLICT (alert_id, provider) DO UPDATE SET status = EXCLUDED.status,
                attempt_count = notification_deliveries.attempt_count + 1,
                last_error = EXCLUDED.last_error, attempted_at = EXCLUDED.attempted_at,
                delivered_at = EXCLUDED.delivered_at, updated_at = EXCLUDED.attempted_at
            """,
            (
                uuid.uuid4(),
                uuid.UUID(alert_id),
                provider,
                status,
                _safe_error(error),
                now,
                now if status == "SENT" else None,
            ),
        )

    def queue_counts(self) -> dict[str, Any]:
        row = self.database.fetch_one(
            """
            SELECT
                count(*) FILTER (WHERE b.status = 'QUEUED') AS backtest_queued,
                count(*) FILTER (WHERE b.status = 'RUNNING') AS backtest_running,
                count(*) FILTER (WHERE b.status = 'QUEUED' AND rv.run_id IS NOT NULL) AS research_queued,
                count(*) FILTER (WHERE b.status = 'RUNNING' AND rv.run_id IS NOT NULL) AS research_running
            FROM backtest_runs b LEFT JOIN research_variants rv ON rv.run_id = b.run_id
            """
        ) or {}
        walk = self.database.fetch_one(
            """
            SELECT count(*) FILTER (WHERE completed_at IS NULL AND NOT cancel_requested) AS active
            FROM walk_forward_validations
            """
        ) or {}
        return {
            "backtests": {"queued": int(row.get("backtest_queued") or 0), "running": int(row.get("backtest_running") or 0)},
            "research": {"queued": int(row.get("research_queued") or 0), "running": int(row.get("research_running") or 0)},
            "walkForward": {"active": int(walk.get("active") or 0)},
        }

    def research_experiment_for_run(self, run_id: str) -> str | None:
        row = self.database.fetch_one(
            "SELECT experiment_id FROM research_variants WHERE run_id = %s", (uuid.UUID(run_id),)
        )
        return str(row["experiment_id"]) if row else None


def _fingerprint(alert_type: str, source: str, context: Mapping[str, Any]) -> str:
    identity = {key: context[key] for key in sorted(context) if key in {"market", "provider", "workerType", "taskKey"}}
    value = json.dumps([alert_type, source, identity], separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(value.encode()).hexdigest()


def _safe_details(values: Mapping[str, Any]) -> dict[str, Any]:
    forbidden = {"secret", "password", "passphrase", "credential", "token", "api_key", "apikey", "authorization"}
    safe: dict[str, Any] = {}
    for key, value in values.items():
        if any(word in key.casefold() for word in forbidden):
            continue
        safe[key[:100]] = value
    encoded = json.dumps(safe, default=str)
    if len(encoded.encode()) > 16_384:
        return {"truncated": True}
    return safe


def _bounded(value: str, maximum: int, label: str) -> str:
    value = str(value).strip()
    if not value or len(value) > maximum:
        raise ValueError(f"{label} must contain between 1 and {maximum} characters")
    return value


def _limit(value: int) -> int:
    if value < 1 or value > 200:
        raise ValueError("limit must be between 1 and 200")
    return value


def _safe_error(error: str | None) -> str | None:
    return str(error)[:500] if error else None


def _lease(row: Mapping[str, Any]) -> dict[str, Any]:
    token = str(row["owner_token"])
    return {
        "leaseId": str(row["lease_id"]),
        "workerType": row["worker_type"],
        "taskKey": row["task_key"],
        "workerIdentity": row["worker_identity"],
        "hostIdentity": row["host_identity"],
        "processIdentity": row["process_identity"],
        "ownerFingerprint": token[:8],
        "acquiredAt": row["acquired_at"].isoformat(),
        "heartbeatAt": row["heartbeat_at"].isoformat(),
        "expiresAt": row["expires_at"].isoformat(),
        "currentTask": row["current_task"],
        "status": row["status"],
        "lastError": row.get("last_error"),
        "updatedAt": row["updated_at"].isoformat(),
    }


def _audit(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "auditId": str(row["audit_id"]),
        "requestId": row["request_id"],
        "action": row["action"],
        "actorType": row["actor_type"],
        "actorId": row["actor_id"],
        "subjectType": row.get("subject_type"),
        "subjectId": row.get("subject_id"),
        "success": row["success"],
        "details": row["details"],
        "createdAt": row["created_at"].isoformat(),
    }


def _alert(row: Mapping[str, Any]) -> dict[str, Any]:
    def stamp(key: str) -> str | None:
        value = row.get(key)
        return value.isoformat() if value else None

    return {
        "alertId": str(row["alert_id"]),
        "alertType": row["alert_type"],
        "severity": row["severity"],
        "source": row["source"],
        "title": row["title"],
        "message": row["message"],
        "context": row["context"],
        "status": row["status"],
        "occurrenceCount": row["occurrence_count"],
        "firstSeenAt": stamp("first_seen_at"),
        "lastSeenAt": stamp("last_seen_at"),
        "cooldownUntil": stamp("cooldown_until"),
        "acknowledgedAt": stamp("acknowledged_at"),
        "acknowledgedBy": row.get("acknowledged_by"),
        "resolvedAt": stamp("resolved_at"),
        "resolvedBy": row.get("resolved_by"),
        "resolution": row.get("resolution"),
    }
