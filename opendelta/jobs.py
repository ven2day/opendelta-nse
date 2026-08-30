from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from .core import canonical_json, stable_id, utc_now_iso


TERMINAL_STATUSES = {"COMPLETE", "FAILED", "CANCELLED"}


class JobCancelled(RuntimeError):
    pass


class JobRepository:
    SCHEMA_VERSION = 1

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.migrate()

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        return connection

    def migrate(self) -> None:
        with self.connect() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute(
                "CREATE TABLE IF NOT EXISTS schema_migrations(version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)"
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS platform_jobs (
                    job_id TEXT PRIMARY KEY,
                    job_type TEXT NOT NULL,
                    idempotency_key TEXT NOT NULL UNIQUE,
                    status TEXT NOT NULL,
                    progress REAL NOT NULL,
                    attempt INTEGER NOT NULL,
                    maximum_attempts INTEGER NOT NULL,
                    payload_json TEXT NOT NULL,
                    result_json TEXT,
                    error_code TEXT,
                    error_message TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    started_at TEXT,
                    completed_at TEXT
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_platform_jobs_status_updated ON platform_jobs(status, updated_at DESC)"
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS audit_events (
                    event_id TEXT PRIMARY KEY,
                    event_type TEXT NOT NULL,
                    actor TEXT NOT NULL,
                    entity_id TEXT,
                    metadata_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                "INSERT OR IGNORE INTO schema_migrations(version, applied_at) VALUES (?, ?)",
                (self.SCHEMA_VERSION, utc_now_iso()),
            )
            connection.execute(
                "UPDATE platform_jobs SET status='FAILED', error_code='WORKER_RESTARTED', error_message='Worker restarted before completion', updated_at=?, completed_at=? WHERE status IN ('QUEUED','RUNNING','RETRYING')",
                (utc_now_iso(), utc_now_iso()),
            )
            connection.execute("PRAGMA optimize")

    def create(self, job_type: str, payload: dict[str, Any], idempotency_key: str, maximum_attempts: int) -> tuple[dict[str, Any], bool]:
        now = utc_now_iso()
        job_id = f"job-{uuid.uuid4()}"
        with self.connect() as connection:
            existing = connection.execute(
                "SELECT * FROM platform_jobs WHERE idempotency_key = ?", (idempotency_key,)
            ).fetchone()
            if existing:
                return self._public(existing), False
            connection.execute(
                """
                INSERT INTO platform_jobs(job_id, job_type, idempotency_key, status, progress, attempt, maximum_attempts, payload_json, created_at, updated_at)
                VALUES (?, ?, ?, 'QUEUED', 0, 0, ?, ?, ?, ?)
                """,
                (job_id, job_type, idempotency_key, maximum_attempts, canonical_json(payload), now, now),
            )
            row = connection.execute("SELECT * FROM platform_jobs WHERE job_id = ?", (job_id,)).fetchone()
        assert row is not None
        self.audit("JOB_CREATED", "platform", job_id, {"jobType": job_type})
        return self._public(row), True

    def update(self, job_id: str, **changes: Any) -> dict[str, Any]:
        allowed = {
            "status", "progress", "attempt", "result_json", "error_code", "error_message",
            "started_at", "completed_at",
        }
        supplied = {key: value for key, value in changes.items() if key in allowed}
        if not supplied:
            return self.get(job_id)
        supplied["updated_at"] = utc_now_iso()
        assignments = ", ".join(f"{key} = ?" for key in supplied)
        with self.connect() as connection:
            cursor = connection.execute(
                f"UPDATE platform_jobs SET {assignments} WHERE job_id = ?", (*supplied.values(), job_id)
            )
            if cursor.rowcount != 1:
                raise KeyError(job_id)
            row = connection.execute("SELECT * FROM platform_jobs WHERE job_id = ?", (job_id,)).fetchone()
        assert row is not None
        return self._public(row)

    def get(self, job_id: str) -> dict[str, Any]:
        with self.connect() as connection:
            row = connection.execute("SELECT * FROM platform_jobs WHERE job_id = ?", (job_id,)).fetchone()
        if not row:
            raise KeyError(job_id)
        return self._public(row)

    def list(self, limit: int = 100) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM platform_jobs ORDER BY created_at DESC LIMIT ?", (max(1, min(limit, 500)),)
            ).fetchall()
        return [self._public(row) for row in rows]

    def counts(self) -> dict[str, int]:
        with self.connect() as connection:
            rows = connection.execute("SELECT status, COUNT(*) AS count FROM platform_jobs GROUP BY status").fetchall()
        return {str(row["status"]): int(row["count"]) for row in rows}

    def audit(self, event_type: str, actor: str, entity_id: str | None, metadata: dict[str, Any]) -> None:
        with self.connect() as connection:
            connection.execute(
                "INSERT INTO audit_events(event_id, event_type, actor, entity_id, metadata_json, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (f"audit-{uuid.uuid4()}", event_type, actor, entity_id, canonical_json(metadata), utc_now_iso()),
            )

    def migrations(self) -> list[int]:
        with self.connect() as connection:
            return [int(row[0]) for row in connection.execute("SELECT version FROM schema_migrations ORDER BY version")]

    @staticmethod
    def _public(row: sqlite3.Row) -> dict[str, Any]:
        result = json.loads(row["result_json"]) if row["result_json"] else None
        return {
            "jobId": row["job_id"],
            "jobType": row["job_type"],
            "status": row["status"],
            "progress": row["progress"],
            "attempt": row["attempt"],
            "maximumAttempts": row["maximum_attempts"],
            "result": result,
            "error": ({"code": row["error_code"], "message": row["error_message"]} if row["error_code"] else None),
            "createdAt": row["created_at"],
            "updatedAt": row["updated_at"],
            "startedAt": row["started_at"],
            "completedAt": row["completed_at"],
        }


ProgressCallback = Callable[[float], None]
CancellationCheck = Callable[[], None]
JobHandler = Callable[[dict[str, Any], ProgressCallback, CancellationCheck], dict[str, Any]]


class JobService:
    def __init__(self, repository: JobRepository, *, maximum_workers: int = 2, maximum_pending: int = 20, retry_limit: int = 2) -> None:
        self.repository = repository
        self.maximum_workers = maximum_workers
        self.maximum_pending = maximum_pending
        self.retry_limit = retry_limit
        self.executor = ThreadPoolExecutor(max_workers=maximum_workers, thread_name_prefix="opendelta-job")
        self._lock = threading.Lock()
        self._cancellations: dict[str, threading.Event] = {}
        self._futures: dict[str, Future[Any]] = {}
        self._started_at = time.monotonic()

    def submit(self, job_type: str, payload: dict[str, Any], handler: JobHandler, idempotency_key: str | None = None) -> dict[str, Any]:
        with self._lock:
            active = sum(1 for future in self._futures.values() if not future.done())
            if active >= self.maximum_pending:
                raise RuntimeError("Job queue is at capacity")
        key = (idempotency_key or "").strip() or stable_id("idem", {"type": job_type, "payload": payload})
        job, created = self.repository.create(job_type, payload, key, self.retry_limit + 1)
        if not created:
            return {**job, "idempotentReplay": True}
        cancellation = threading.Event()
        with self._lock:
            self._cancellations[job["jobId"]] = cancellation
            self._futures[job["jobId"]] = self.executor.submit(
                self._execute, job["jobId"], payload, handler, cancellation
            )
        return {**job, "idempotentReplay": False}

    def _execute(self, job_id: str, payload: dict[str, Any], handler: JobHandler, cancellation: threading.Event) -> None:
        def cancel_check() -> None:
            if cancellation.is_set():
                raise JobCancelled("Job cancelled")

        def progress(value: float) -> None:
            cancel_check()
            self.repository.update(job_id, progress=max(0.0, min(100.0, float(value))))

        for attempt in range(1, self.retry_limit + 2):
            try:
                cancel_check()
                self.repository.update(job_id, status="RUNNING" if attempt == 1 else "RETRYING", attempt=attempt, started_at=utc_now_iso())
                result = handler(payload, progress, cancel_check)
                cancel_check()
                self.repository.update(job_id, status="COMPLETE", progress=100.0, result_json=canonical_json(result), completed_at=utc_now_iso(), error_code=None, error_message=None)
                self.repository.audit("JOB_COMPLETED", "worker", job_id, {"attempt": attempt})
                return
            except JobCancelled:
                self.repository.update(job_id, status="CANCELLED", error_code="CANCELLED", error_message="Cancelled by user", completed_at=utc_now_iso())
                self.repository.audit("JOB_CANCELLED", "user", job_id, {})
                return
            except Exception as error:  # sanitized at the repository boundary
                if attempt > self.retry_limit:
                    self.repository.update(job_id, status="FAILED", error_code="JOB_FAILED", error_message=type(error).__name__, completed_at=utc_now_iso())
                    self.repository.audit("JOB_FAILED", "worker", job_id, {"errorType": type(error).__name__})
                    return
                time.sleep(min(2 ** (attempt - 1), 4))

    def cancel(self, job_id: str) -> dict[str, Any]:
        job = self.repository.get(job_id)
        if job["status"] in TERMINAL_STATUSES:
            return job
        with self._lock:
            event = self._cancellations.get(job_id)
        if event is None:
            return self.repository.update(job_id, status="CANCELLED", error_code="CANCELLED", error_message="Cancelled before execution", completed_at=utc_now_iso())
        event.set()
        return {**job, "cancellationRequested": True}

    def health(self) -> dict[str, Any]:
        counts = self.repository.counts()
        return {
            "status": "HEALTHY",
            "maximumWorkers": self.maximum_workers,
            "maximumPending": self.maximum_pending,
            "queueDepth": counts.get("QUEUED", 0) + counts.get("RETRYING", 0),
            "running": counts.get("RUNNING", 0),
            "uptimeSeconds": round(time.monotonic() - self._started_at, 3),
        }

    def shutdown(self) -> None:
        with self._lock:
            for event in self._cancellations.values():
                event.set()
        self.executor.shutdown(wait=False, cancel_futures=True)
