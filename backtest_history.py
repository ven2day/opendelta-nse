from __future__ import annotations

import gzip
import hashlib
import json
import os
import re
import sqlite3
import tempfile
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Mapping


HISTORY_LIMIT = 10
MAX_PAYLOAD_BYTES = 100 * 1024 * 1024
_OWNER_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_RUN_ID_PATTERN = re.compile(r"^[A-Za-z0-9._-]{1,120}$")
_STRATEGIES = {"rsi_range", "rsi_recovery", "market_aligned_rsi_scalper"}


class BacktestHistoryRepository:
    """Per-user result metadata in SQLite with compressed, atomic JSON payloads."""

    def __init__(self, root: Path, *, limit: int = HISTORY_LIMIT) -> None:
        self.root = root.expanduser()
        if not self.root.is_absolute():
            raise ValueError("Backtest-history persistence path must be absolute")
        if not isinstance(limit, int) or limit < 1:
            raise ValueError("Backtest-history limit must be a positive integer")
        self.limit = limit
        self.database_path = self.root / "history.sqlite3"
        self.payload_root = self.root / "payloads"
        self._lock = threading.RLock()
        self.root.mkdir(parents=True, exist_ok=True)
        self.payload_root.mkdir(parents=True, exist_ok=True)
        os.chmod(self.root, 0o700)
        os.chmod(self.payload_root, 0o700)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=FULL")
        return connection

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        connection = self._connect()
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self._lock, self._connection() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS backtest_history (
                    owner_key TEXT NOT NULL,
                    run_id TEXT NOT NULL,
                    completed_at TEXT NOT NULL,
                    completed_epoch REAL NOT NULL,
                    strategy_mode TEXT NOT NULL,
                    strategy_name TEXT NOT NULL,
                    timeframe TEXT NOT NULL,
                    duration_years INTEGER NOT NULL,
                    symbol_count INTEGER NOT NULL,
                    payload_path TEXT NOT NULL,
                    saved_at TEXT NOT NULL,
                    PRIMARY KEY (owner_key, run_id)
                )
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS backtest_history_owner_completed
                ON backtest_history(owner_key, completed_epoch DESC, run_id DESC)
                """
            )

    @staticmethod
    def validate_owner(owner_key: str) -> str:
        normalized = owner_key.strip().lower()
        if not _OWNER_PATTERN.fullmatch(normalized):
            raise ValueError("Backtest-history owner is invalid")
        return normalized

    @staticmethod
    def _validate_record(record: Mapping[str, Any]) -> tuple[dict[str, Any], float]:
        normalized = dict(record)
        run_id = normalized.get("id")
        if not isinstance(run_id, str) or not _RUN_ID_PATTERN.fullmatch(run_id):
            raise ValueError("Backtest-history run ID is invalid")

        completed_at = normalized.get("completedAt")
        if not isinstance(completed_at, str):
            raise ValueError("Backtest-history completion timestamp is invalid")
        try:
            completed = datetime.fromisoformat(completed_at.replace("Z", "+00:00"))
        except ValueError as error:
            raise ValueError("Backtest-history completion timestamp is invalid") from error
        if completed.tzinfo is None:
            raise ValueError("Backtest-history completion timestamp must include a timezone")

        strategy_mode = normalized.get("strategyMode")
        if strategy_mode not in _STRATEGIES:
            raise ValueError("Backtest-history strategy is invalid")
        if not isinstance(normalized.get("strategyName"), str) or not normalized["strategyName"].strip():
            raise ValueError("Backtest-history strategy name is invalid")
        if not isinstance(normalized.get("timeframe"), str) or not normalized["timeframe"].strip():
            raise ValueError("Backtest-history timeframe is invalid")
        duration_years = normalized.get("durationYears")
        if isinstance(duration_years, bool) or not isinstance(duration_years, int) or not 1 <= duration_years <= 10:
            raise ValueError("Backtest-history duration is invalid")
        symbol_count = normalized.get("symbolCount")
        if isinstance(symbol_count, bool) or not isinstance(symbol_count, int) or not 1 <= symbol_count <= 100_000:
            raise ValueError("Backtest-history symbol count is invalid")

        response = normalized.get("response")
        if not isinstance(response, dict) or not isinstance(response.get("metadata"), dict) or not isinstance(response.get("results"), list):
            raise ValueError("Backtest-history response is invalid")
        response_run_id = response["metadata"].get("runId")
        if response_run_id is not None and response_run_id != run_id:
            raise ValueError("Backtest-history run ID does not match the response")
        return normalized, completed.timestamp()

    def _write_payload(self, owner_key: str, run_id: str, raw_json: bytes) -> tuple[str, bool]:
        owner_directory = self.payload_root / owner_key
        owner_directory.mkdir(parents=True, exist_ok=True)
        os.chmod(owner_directory, 0o700)
        run_hash = hashlib.sha256(run_id.encode("utf-8")).hexdigest()[:32]
        payload_hash = hashlib.sha256(raw_json).hexdigest()[:16]
        destination = owner_directory / f"{run_hash}-{payload_hash}.json.gz"
        if destination.is_file():
            return destination.relative_to(self.root).as_posix(), False

        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="wb", dir=owner_directory, prefix=".history-", suffix=".tmp", delete=False
            ) as temporary:
                temporary_path = Path(temporary.name)
                with gzip.GzipFile(fileobj=temporary, mode="wb", compresslevel=6, mtime=0) as compressed:
                    compressed.write(raw_json)
                temporary.flush()
                os.fsync(temporary.fileno())
            os.chmod(temporary_path, 0o600)
            os.replace(temporary_path, destination)
            return destination.relative_to(self.root).as_posix(), True
        finally:
            if temporary_path is not None and temporary_path.exists():
                temporary_path.unlink(missing_ok=True)

    def _payload_file(self, relative_path: str) -> Path:
        path = (self.root / relative_path).resolve()
        payload_root = self.payload_root.resolve()
        if path != payload_root and payload_root not in path.parents:
            raise ValueError("Backtest-history payload path is invalid")
        return path

    def _unlink_payload(self, relative_path: str) -> None:
        try:
            self._payload_file(relative_path).unlink(missing_ok=True)
        except (OSError, ValueError):
            return

    @staticmethod
    def _summary(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["run_id"],
            "completedAt": row["completed_at"],
            "strategyMode": row["strategy_mode"],
            "strategyName": row["strategy_name"],
            "timeframe": row["timeframe"],
            "durationYears": row["duration_years"],
            "symbolCount": row["symbol_count"],
        }

    def save(self, owner_key: str, record: Mapping[str, Any]) -> dict[str, Any]:
        owner = self.validate_owner(owner_key)
        normalized, completed_epoch = self._validate_record(record)
        raw_json = json.dumps(normalized, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")
        if len(raw_json) > MAX_PAYLOAD_BYTES:
            raise ValueError("Backtest-history result exceeds the 100 MB storage limit")

        with self._lock:
            relative_path, payload_created = self._write_payload(owner, normalized["id"], raw_json)
            obsolete_paths: set[str] = set()
            try:
                with self._connection() as connection:
                    old = connection.execute(
                        "SELECT payload_path FROM backtest_history WHERE owner_key = ? AND run_id = ?",
                        (owner, normalized["id"]),
                    ).fetchone()
                    connection.execute(
                        """
                        INSERT INTO backtest_history (
                            owner_key, run_id, completed_at, completed_epoch, strategy_mode,
                            strategy_name, timeframe, duration_years, symbol_count, payload_path, saved_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(owner_key, run_id) DO UPDATE SET
                            completed_at = excluded.completed_at,
                            completed_epoch = excluded.completed_epoch,
                            strategy_mode = excluded.strategy_mode,
                            strategy_name = excluded.strategy_name,
                            timeframe = excluded.timeframe,
                            duration_years = excluded.duration_years,
                            symbol_count = excluded.symbol_count,
                            payload_path = excluded.payload_path,
                            saved_at = excluded.saved_at
                        """,
                        (
                            owner,
                            normalized["id"],
                            normalized["completedAt"],
                            completed_epoch,
                            normalized["strategyMode"],
                            normalized["strategyName"].strip(),
                            normalized["timeframe"].strip(),
                            normalized["durationYears"],
                            normalized["symbolCount"],
                            relative_path,
                            datetime.now(timezone.utc).isoformat(),
                        ),
                    )
                    expired = connection.execute(
                        """
                        SELECT run_id, payload_path FROM backtest_history
                        WHERE owner_key = ?
                        ORDER BY completed_epoch DESC, run_id DESC
                        LIMIT -1 OFFSET ?
                        """,
                        (owner, self.limit),
                    ).fetchall()
                    if expired:
                        connection.executemany(
                            "DELETE FROM backtest_history WHERE owner_key = ? AND run_id = ?",
                            [(owner, row["run_id"]) for row in expired],
                        )
                    obsolete_paths.update(row["payload_path"] for row in expired)
                    if old is not None and old["payload_path"] != relative_path:
                        obsolete_paths.add(old["payload_path"])
            except Exception:
                if payload_created:
                    self._unlink_payload(relative_path)
                raise

            obsolete_paths.discard(relative_path)
            for path in obsolete_paths:
                self._unlink_payload(path)
        return {
            "id": normalized["id"],
            "completedAt": normalized["completedAt"],
            "strategyMode": normalized["strategyMode"],
            "strategyName": normalized["strategyName"].strip(),
            "timeframe": normalized["timeframe"].strip(),
            "durationYears": normalized["durationYears"],
            "symbolCount": normalized["symbolCount"],
        }

    def list(self, owner_key: str) -> list[dict[str, Any]]:
        owner = self.validate_owner(owner_key)
        with self._lock, self._connection() as connection:
            rows = connection.execute(
                """
                SELECT * FROM backtest_history WHERE owner_key = ?
                ORDER BY completed_epoch DESC, run_id DESC LIMIT ?
                """,
                (owner, self.limit),
            ).fetchall()
        return [self._summary(row) for row in rows]

    def get_summary(self, owner_key: str, run_id: str) -> dict[str, Any]:
        owner = self.validate_owner(owner_key)
        if not _RUN_ID_PATTERN.fullmatch(run_id):
            raise ValueError("Backtest-history run ID is invalid")
        with self._lock, self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM backtest_history WHERE owner_key = ? AND run_id = ?",
                (owner, run_id),
            ).fetchone()
        if row is None:
            raise KeyError(run_id)
        return self._summary(row)

    def get(self, owner_key: str, run_id: str) -> dict[str, Any]:
        owner = self.validate_owner(owner_key)
        if not _RUN_ID_PATTERN.fullmatch(run_id):
            raise ValueError("Backtest-history run ID is invalid")
        with self._lock:
            with self._connection() as connection:
                row = connection.execute(
                    "SELECT payload_path FROM backtest_history WHERE owner_key = ? AND run_id = ?",
                    (owner, run_id),
                ).fetchone()
            if row is None:
                raise KeyError(run_id)
            try:
                with gzip.open(self._payload_file(row["payload_path"]), "rt", encoding="utf-8") as source:
                    payload = json.load(source)
            except (OSError, json.JSONDecodeError) as error:
                raise ValueError("Stored backtest-history payload is unavailable") from error
        if not isinstance(payload, dict):
            raise ValueError("Stored backtest-history payload is invalid")
        return payload

    def delete(self, owner_key: str, run_id: str) -> bool:
        owner = self.validate_owner(owner_key)
        if not _RUN_ID_PATTERN.fullmatch(run_id):
            raise ValueError("Backtest-history run ID is invalid")
        with self._lock, self._connection() as connection:
            row = connection.execute(
                "SELECT payload_path FROM backtest_history WHERE owner_key = ? AND run_id = ?",
                (owner, run_id),
            ).fetchone()
            if row is None:
                return False
            connection.execute(
                "DELETE FROM backtest_history WHERE owner_key = ? AND run_id = ?",
                (owner, run_id),
            )
        self._unlink_payload(row["payload_path"])
        return True
