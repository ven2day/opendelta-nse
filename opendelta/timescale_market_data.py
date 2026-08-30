from __future__ import annotations

import hashlib
import json
import math
import os
import threading
import uuid
from collections.abc import Callable, Iterable, Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Any, Literal, Mapping, Protocol
from zoneinfo import ZoneInfo

from psycopg import connect
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool


HealthStatus = Literal[
    "HEALTHY",
    "DELAYED",
    "GAPS_DETECTED",
    "REPAIRING",
    "PROVIDER_UNAVAILABLE",
    "INVALID_DATA",
    "UNSUPPORTED",
]

TIMEFRAME_SECONDS = {"1m": 60, "5m": 300, "15m": 900, "30m": 1800, "1h": 3600, "1d": 86400}
UTC_ZONE = ZoneInfo("UTC")
NSE_ZONE = ZoneInfo("Asia/Kolkata")


def utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("Timestamps must be timezone-aware")
    return value.astimezone(UTC_ZONE)


@dataclass(frozen=True)
class CanonicalCandle:
    market: str
    provider: str
    instrument_id: str
    symbol: str
    timeframe: str
    open_time: datetime
    close_time: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float
    quote_volume: float | None = None
    complete: bool = True

    def __post_init__(self) -> None:
        if self.timeframe not in TIMEFRAME_SECONDS:
            raise ValueError(f"Unsupported timeframe: {self.timeframe}")
        opened, closed = utc(self.open_time), utc(self.close_time)
        if closed <= opened:
            raise ValueError("Candle close_time must be after open_time")
        values = (self.open, self.high, self.low, self.close, self.volume)
        if any(not math.isfinite(value) for value in values):
            raise ValueError("OHLCV values must be finite")
        if self.volume < 0 or self.low > min(self.open, self.close) or self.high < max(self.open, self.close):
            raise ValueError("Invalid OHLCV candle")
        object.__setattr__(self, "market", self.market.strip().upper())
        object.__setattr__(self, "provider", self.provider.strip().upper())
        object.__setattr__(self, "symbol", self.symbol.strip().upper())
        object.__setattr__(self, "open_time", opened)
        object.__setattr__(self, "close_time", closed)


@dataclass(frozen=True)
class MissingRange:
    start: datetime
    end: datetime
    missing_candles: int


@dataclass(frozen=True)
class RepairResult:
    market: str
    provider: str
    instrument_id: str
    timeframe: str
    status: HealthStatus
    missing_before: int
    candles_received: int
    candles_written: int
    missing_after: int
    error: str | None = None

    def public(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ReconciliationResult:
    status: Literal["MATCHED", "MISMATCH"]
    expected_count: int
    canonical_count: int
    expected_version: str
    canonical_version: str

    def public(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class DualWriteResult:
    status: Literal["DISABLED", "WRITTEN", "FAILED", "MISMATCH"]
    received: int
    written: int
    error_type: str | None = None
    reconciled: bool | None = None

    def public(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class BackfillJob:
    job_id: uuid.UUID
    market: str
    provider: str
    instrument_id: str
    symbol: str
    timeframe: str
    range_start: datetime
    range_end: datetime
    next_start: datetime
    chunk_days: int
    status: str
    attempts: int
    max_attempts: int

    @classmethod
    def from_row(cls, row: Mapping[str, Any]) -> "BackfillJob":
        return cls(
            job_id=uuid.UUID(str(row["job_id"])),
            market=str(row["market"]),
            provider=str(row["provider"]),
            instrument_id=str(row["instrument_id"]),
            symbol=str(row["symbol"]),
            timeframe=str(row["timeframe"]),
            range_start=utc(row["range_start"]),
            range_end=utc(row["range_end"]),
            next_start=utc(row["next_start"]),
            chunk_days=int(row["chunk_days"]),
            status=str(row["status"]),
            attempts=int(row["attempts"]),
            max_attempts=int(row["max_attempts"]),
        )


class CandleProvider(Protocol):
    def candles(
        self, instrument_id: str, timeframe: str, start: datetime, end: datetime
    ) -> Sequence[CanonicalCandle]: ...


class CanonicalCandleWriter(Protocol):
    def write(self, candles: Sequence[CanonicalCandle]) -> DualWriteResult: ...

    def status(self) -> dict[str, Any]: ...

    def close(self) -> None: ...


def canonical_candles_from_dhan_frame(
    frame: Any,
    *,
    instrument_id: str,
    symbol: str,
    timeframe: str,
    completed_before: datetime,
) -> list[CanonicalCandle]:
    """Convert Dhan's provider-native, open-timestamped frame to canonical candles."""

    if timeframe not in TIMEFRAME_SECONDS:
        raise ValueError(f"Unsupported Dhan canonical timeframe: {timeframe}")
    if frame is None or getattr(frame, "empty", True):
        return []
    step = timedelta(seconds=TIMEFRAME_SECONDS[timeframe])
    completed = utc(completed_before)
    rows: list[CanonicalCandle] = []
    for opened, row in frame.iterrows():
        try:
            opened_at = opened.to_pydatetime() if hasattr(opened, "to_pydatetime") else opened
            opened_at = utc(opened_at)
            closed_at = opened_at + step
            if closed_at > completed:
                continue
            rows.append(
                CanonicalCandle(
                    market="NSE",
                    provider="DHAN",
                    instrument_id=str(instrument_id),
                    symbol=symbol,
                    timeframe=timeframe,
                    open_time=opened_at,
                    close_time=closed_at,
                    open=float(row["Open"]),
                    high=float(row["High"]),
                    low=float(row["Low"]),
                    close=float(row["Close"]),
                    volume=float(row.get("Volume", 0.0)),
                    complete=True,
                )
            )
        except (KeyError, TypeError, ValueError):
            continue
    return rows


def contiguous_missing_ranges(
    expected: Iterable[datetime], actual: Iterable[datetime], timeframe: str
) -> list[MissingRange]:
    step = timedelta(seconds=TIMEFRAME_SECONDS[timeframe])
    missing = sorted({utc(item) for item in expected}.difference(utc(item) for item in actual))
    if not missing:
        return []
    result: list[MissingRange] = []
    start = previous = missing[0]
    count = 1
    for item in missing[1:]:
        if item == previous + step:
            previous, count = item, count + 1
            continue
        result.append(MissingRange(start, previous + step, count))
        start = previous = item
        count = 1
    result.append(MissingRange(start, previous + step, count))
    return result


def crypto_expected_open_times(start: datetime, end: datetime, timeframe: str) -> list[datetime]:
    step = timedelta(seconds=TIMEFRAME_SECONDS[timeframe])
    current, boundary = utc(start), utc(end)
    result: list[datetime] = []
    while current + step <= boundary:
        result.append(current)
        current += step
    return result


def nse_expected_open_times(
    sessions: Iterable[date], start: datetime, end: datetime, timeframe: str
) -> list[datetime]:
    """Build expected candles only from explicit exchange-session dates.

    Callers must load authoritative holiday-aware session dates into market_sessions;
    weekdays are intentionally not guessed here.
    """
    step = timedelta(seconds=TIMEFRAME_SECONDS[timeframe])
    lower, upper = utc(start), utc(end)
    result: list[datetime] = []
    for session_date in sorted(set(sessions)):
        opened = datetime.combine(session_date, time(9, 15), NSE_ZONE).astimezone(UTC_ZONE)
        closed = datetime.combine(session_date, time(15, 30), NSE_ZONE).astimezone(UTC_ZONE)
        current = opened
        while current + step <= closed:
            if current >= lower and current + step <= upper:
                result.append(current)
            current += step
    return result


class TimescaleMarketDataStore:
    """Authoritative candle and data-quality store backed by TimescaleDB."""

    def __init__(
        self,
        database_url: str,
        *,
        min_pool_size: int = 1,
        max_pool_size: int = 8,
        pool_timeout_seconds: float = 5.0,
    ) -> None:
        if not database_url.startswith(("postgresql://", "postgres://")):
            raise ValueError("MARKET_DATA_DATABASE_URL must be a PostgreSQL URL")
        self.pool = ConnectionPool(
            database_url,
            min_size=min_pool_size,
            max_size=max_pool_size,
            kwargs={"row_factory": dict_row, "autocommit": False, "connect_timeout": 3},
            timeout=pool_timeout_seconds,
            open=False,
        )

    def open(self) -> None:
        self.pool.open(wait=True)

    def close(self) -> None:
        self.pool.close()

    def migrate(self) -> None:
        from importlib.resources import files

        sql = files("opendelta.sql").joinpath("001_timescale_market_data.sql").read_text()
        with self.pool.connection() as connection, connection.cursor() as cursor:
            cursor.execute(sql)
            connection.commit()

    def upsert_candles(self, candles: Sequence[CanonicalCandle]) -> int:
        if not candles:
            return 0
        values = [
            (
                item.market, item.provider, item.instrument_id, item.symbol, item.timeframe,
                item.open_time, item.close_time, item.open, item.high, item.low, item.close,
                item.volume, item.quote_volume, item.complete,
            )
            for item in candles
        ]
        statement = """
            INSERT INTO market_candles (
                market, provider, instrument_id, symbol, timeframe, open_time, close_time,
                open, high, low, close, volume, quote_volume, complete
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (market, provider, instrument_id, timeframe, open_time)
            DO UPDATE SET symbol=EXCLUDED.symbol, close_time=EXCLUDED.close_time,
                open=EXCLUDED.open, high=EXCLUDED.high, low=EXCLUDED.low,
                close=EXCLUDED.close, volume=EXCLUDED.volume,
                quote_volume=EXCLUDED.quote_volume, complete=EXCLUDED.complete,
                ingested_at=now()
        """
        with self.pool.connection() as connection, connection.cursor() as cursor:
            cursor.executemany(statement, values)
            connection.commit()
        return len(values)

    def candle_times(
        self, market: str, provider: str, instrument_id: str, timeframe: str,
        start: datetime, end: datetime,
    ) -> list[datetime]:
        with self.pool.connection() as connection, connection.cursor() as cursor:
            cursor.execute(
                """SELECT open_time FROM market_candles
                   WHERE market=%s AND provider=%s AND instrument_id=%s AND timeframe=%s
                     AND open_time >= %s AND open_time < %s AND complete
                   ORDER BY open_time""",
                (market.upper(), provider.upper(), instrument_id, timeframe, utc(start), utc(end)),
            )
            return [row["open_time"] for row in cursor.fetchall()]

    def candles(
        self, market: str, provider: str, instrument_id: str, timeframe: str,
        start: datetime, end: datetime,
    ) -> list[CanonicalCandle]:
        with self.pool.connection() as connection, connection.cursor() as cursor:
            cursor.execute(
                """SELECT market, provider, instrument_id, symbol, timeframe,
                          open_time, close_time, open, high, low, close, volume,
                          quote_volume, complete
                   FROM market_candles
                   WHERE market=%s AND provider=%s AND instrument_id=%s AND timeframe=%s
                     AND open_time >= %s AND open_time < %s AND complete
                   ORDER BY open_time""",
                (market.upper(), provider.upper(), instrument_id, timeframe, utc(start), utc(end)),
            )
            return [CanonicalCandle(**dict(row)) for row in cursor.fetchall()]

    def session_dates(self, market: str, start: datetime, end: datetime) -> list[date]:
        with self.pool.connection() as connection, connection.cursor() as cursor:
            cursor.execute(
                """SELECT session_date FROM market_sessions
                   WHERE market=%s AND is_trading_day AND session_date BETWEEN %s AND %s
                   ORDER BY session_date""",
                (market.upper(), utc(start).date(), utc(end).date()),
            )
            return [row["session_date"] for row in cursor.fetchall()]

    def record_health(
        self, *, market: str, provider: str, instrument_id: str, timeframe: str,
        status: HealthStatus, expected_last_candle: datetime | None,
        actual_last_candle: datetime | None, missing_candles: int, error: str | None = None,
    ) -> None:
        with self.pool.connection() as connection, connection.cursor() as cursor:
            cursor.execute(
                """INSERT INTO market_data_health (
                       market, provider, instrument_id, timeframe, status,
                       expected_last_candle, actual_last_candle, missing_candles, last_error, checked_at
                   ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,now())
                   ON CONFLICT (market, provider, instrument_id, timeframe) DO UPDATE SET
                       status=EXCLUDED.status, expected_last_candle=EXCLUDED.expected_last_candle,
                       actual_last_candle=EXCLUDED.actual_last_candle,
                       missing_candles=EXCLUDED.missing_candles,
                       last_error=EXCLUDED.last_error, checked_at=now()""",
                (market.upper(), provider.upper(), instrument_id, timeframe, status,
                 expected_last_candle, actual_last_candle, missing_candles, error),
            )
            connection.commit()

    def health(self, *, limit: int = 1000) -> list[dict[str, Any]]:
        with self.pool.connection() as connection, connection.cursor() as cursor:
            cursor.execute(
                """SELECT market, provider, instrument_id, timeframe, status,
                          expected_last_candle, actual_last_candle, missing_candles,
                          last_error, checked_at
                   FROM market_data_health ORDER BY status DESC, checked_at DESC LIMIT %s""",
                (limit,),
            )
            return list(cursor.fetchall())

    def reconcile(
        self, expected: Sequence[CanonicalCandle], *, market: str, provider: str,
        instrument_id: str, timeframe: str, start: datetime, end: datetime,
    ) -> ReconciliationResult:
        canonical = self.candles(market, provider, instrument_id, timeframe, start, end)
        expected_version = candle_data_version(expected)
        canonical_version = candle_data_version(canonical)
        matched = len(expected) == len(canonical) and expected_version == canonical_version
        return ReconciliationResult(
            "MATCHED" if matched else "MISMATCH",
            len(expected),
            len(canonical),
            expected_version,
            canonical_version,
        )

    def enqueue_backfill(
        self, *, market: str, provider: str, instrument_id: str, symbol: str,
        timeframe: str, start: datetime, end: datetime, chunk_days: int = 30,
        max_attempts: int = 5,
    ) -> uuid.UUID:
        lower, upper = utc(start), utc(end)
        if lower >= upper:
            raise ValueError("Backfill start must be earlier than end")
        if timeframe not in TIMEFRAME_SECONDS:
            raise ValueError(f"Unsupported timeframe: {timeframe}")
        if not 1 <= chunk_days <= 90:
            raise ValueError("chunk_days must be between 1 and 90")
        if not 1 <= max_attempts <= 20:
            raise ValueError("max_attempts must be between 1 and 20")
        identity = "|".join(
            (market.upper(), provider.upper(), instrument_id, timeframe, lower.isoformat(), upper.isoformat())
        )
        job_id = uuid.uuid5(uuid.NAMESPACE_URL, "opendelta-market-data:" + identity)
        with self.pool.connection() as connection, connection.cursor() as cursor:
            cursor.execute(
                """INSERT INTO market_data_repair_jobs (
                       job_id, market, provider, instrument_id, symbol, timeframe,
                       range_start, range_end, next_start, chunk_days, status, max_attempts
                   ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'PENDING',%s)
                   ON CONFLICT (job_id) DO UPDATE SET
                       symbol=EXCLUDED.symbol, chunk_days=EXCLUDED.chunk_days,
                       max_attempts=EXCLUDED.max_attempts,
                       updated_at=now()
                   WHERE market_data_repair_jobs.status NOT IN ('RUNNING','COMPLETE')""",
                (
                    job_id, market.upper(), provider.upper(), instrument_id, symbol.upper(),
                    timeframe, lower, upper, lower, chunk_days, max_attempts,
                ),
            )
            connection.commit()
        return job_id

    def claim_backfill(self, worker_id: str, *, lease_seconds: int = 300) -> BackfillJob | None:
        if not worker_id.strip():
            raise ValueError("worker_id is required")
        with self.pool.connection() as connection, connection.cursor() as cursor:
            cursor.execute(
                """WITH candidate AS (
                       SELECT job_id FROM market_data_repair_jobs
                       WHERE (
                           status IN ('PENDING','RETRY')
                           OR (status='RUNNING' AND (lease_expires_at IS NULL OR lease_expires_at < now()))
                       ) AND (next_attempt_at IS NULL OR next_attempt_at <= now())
                       ORDER BY created_at, job_id
                       FOR UPDATE SKIP LOCKED
                       LIMIT 1
                   )
                   UPDATE market_data_repair_jobs AS job
                   SET status='RUNNING', lease_owner=%s,
                       lease_expires_at=now() + (%s * interval '1 second'), updated_at=now()
                   FROM candidate WHERE job.job_id=candidate.job_id
                   RETURNING job.*""",
                (worker_id, max(30, lease_seconds)),
            )
            row = cursor.fetchone()
            connection.commit()
        return BackfillJob.from_row(row) if row else None

    def advance_backfill(
        self, job_id: uuid.UUID, *, next_start: datetime, received: int, written: int,
        reconciliation: ReconciliationResult,
    ) -> bool:
        if reconciliation.status != "MATCHED":
            raise ValueError("A backfill chunk cannot advance before reconciliation matches")
        with self.pool.connection() as connection, connection.cursor() as cursor:
            cursor.execute(
                """UPDATE market_data_repair_jobs SET
                       next_start=%s, status=CASE WHEN %s >= range_end THEN 'COMPLETE' ELSE 'PENDING' END,
                       candles_received=candles_received+%s,
                       candles_written=candles_written+%s,
                       missing_before=0, missing_after=0, last_error=NULL,
                       lease_owner=NULL, lease_expires_at=NULL, next_attempt_at=NULL,
                       completed_at=CASE WHEN %s >= range_end THEN now() ELSE NULL END,
                       updated_at=now()
                   WHERE job_id=%s
                   RETURNING status""",
                (utc(next_start), utc(next_start), received, written, utc(next_start), job_id),
            )
            row = cursor.fetchone()
            connection.commit()
        return bool(row and row["status"] == "COMPLETE")

    def fail_backfill(self, job_id: uuid.UUID, error: Exception) -> str:
        message = f"{type(error).__name__}: {error}"[:500]
        with self.pool.connection() as connection, connection.cursor() as cursor:
            cursor.execute(
                """UPDATE market_data_repair_jobs SET
                       attempts=attempts+1,
                       status=CASE WHEN attempts+1 >= max_attempts THEN 'FAILED' ELSE 'RETRY' END,
                       last_error=%s, lease_owner=NULL, lease_expires_at=NULL,
                       next_attempt_at=CASE WHEN attempts+1 >= max_attempts THEN NULL
                           ELSE now() + (LEAST(3600, power(2, attempts+1)::integer * 30) * interval '1 second') END,
                       updated_at=now()
                   WHERE job_id=%s RETURNING status""",
                (message, job_id),
            )
            row = cursor.fetchone()
            connection.commit()
        return str(row["status"]) if row else "MISSING"

    def repair_job_health(self) -> dict[str, Any]:
        with self.pool.connection() as connection, connection.cursor() as cursor:
            cursor.execute(
                """SELECT status, count(*) AS count
                   FROM market_data_repair_jobs GROUP BY status ORDER BY status"""
            )
            statuses = {str(row["status"]): int(row["count"]) for row in cursor.fetchall()}
            cursor.execute(
                """SELECT count(*) AS failed, max(updated_at) AS last_update
                   FROM market_data_repair_jobs WHERE status='FAILED'"""
            )
            failures = cursor.fetchone()
            cursor.execute(
                """SELECT coalesce(sum(candles_received), 0) AS received,
                          coalesce(sum(candles_written), 0) AS written
                   FROM market_data_repair_jobs"""
            )
            totals = cursor.fetchone()
        return {
            "statuses": statuses,
            "failed": int(failures["failed"]),
            "lastFailureUpdate": failures["last_update"].isoformat()
            if failures["last_update"] else None,
            "candlesReceived": int(totals["received"]),
            "candlesWritten": int(totals["written"]),
        }


class TimescaleDualWriter:
    """Best-effort transition writer: legacy persistence stays available on failure."""

    def __init__(
        self,
        database_url: str | None,
        *,
        store_factory: Callable[[str], TimescaleMarketDataStore] = TimescaleMarketDataStore,
        verify: bool = True,
    ) -> None:
        self.database_url = database_url.strip() if database_url else None
        self.store_factory = store_factory
        self.verify = verify
        self._store: TimescaleMarketDataStore | None = None
        self._lock = threading.Lock()
        self._received = 0
        self._written = 0
        self._failed_batches = 0
        self._mismatch_batches = 0
        self._last_success: str | None = None
        self._last_error_type: str | None = None

    def _resolved_store(self) -> TimescaleMarketDataStore:
        if not self.database_url:
            raise RuntimeError("MARKET_DATA_DATABASE_URL is not configured")
        if self._store is None:
            store = self.store_factory(self.database_url)
            store.open()
            self._store = store
        return self._store

    def write(self, candles: Sequence[CanonicalCandle]) -> DualWriteResult:
        complete = [item for item in candles if item.complete]
        if not complete:
            return DualWriteResult("DISABLED" if not self.database_url else "WRITTEN", 0, 0)
        with self._lock:
            self._received += len(complete)
            if not self.database_url:
                return DualWriteResult("DISABLED", len(complete), 0)
            try:
                store = self._resolved_store()
                written = store.upsert_candles(complete)
                self._written += written
                if self.verify:
                    grouped: dict[tuple[str, str, str, str], list[CanonicalCandle]] = {}
                    for item in complete:
                        grouped.setdefault(
                            (item.market, item.provider, item.instrument_id, item.timeframe), []
                        ).append(item)
                    for (market, provider, instrument_id, timeframe), rows in grouped.items():
                        start = min(item.open_time for item in rows)
                        end = max(item.close_time for item in rows)
                        reconciliation = store.reconcile(
                            rows,
                            market=market,
                            provider=provider,
                            instrument_id=instrument_id,
                            timeframe=timeframe,
                            start=start,
                            end=end,
                        )
                        if reconciliation.status != "MATCHED":
                            self._mismatch_batches += 1
                            self._last_error_type = "ReconciliationMismatch"
                            return DualWriteResult(
                                "MISMATCH", len(complete), written,
                                "ReconciliationMismatch", False,
                            )
            except Exception as error:
                self._failed_batches += 1
                self._last_error_type = type(error).__name__
                return DualWriteResult("FAILED", len(complete), 0, type(error).__name__, False)
            self._last_success = datetime.now(UTC).isoformat()
            self._last_error_type = None
            return DualWriteResult("WRITTEN", len(complete), written, reconciled=self.verify)

    def status(self) -> dict[str, Any]:
        with self._lock:
            return {
                "enabled": bool(self.database_url),
                "status": (
                    "NOT_CONFIGURED" if not self.database_url
                    else "DEGRADED" if self._last_error_type
                    else "HEALTHY"
                ),
                "received": self._received,
                "written": self._written,
                "failedBatches": self._failed_batches,
                "mismatchBatches": self._mismatch_batches,
                "lastSuccess": self._last_success,
                "lastErrorType": self._last_error_type,
            }

    def close(self) -> None:
        with self._lock:
            if self._store is not None:
                self._store.close()
                self._store = None


def dual_writer_from_environment() -> TimescaleDualWriter:
    return TimescaleDualWriter(os.environ.get("MARKET_DATA_DATABASE_URL", "").strip() or None)


class BackfillWorker:
    def __init__(
        self,
        store: TimescaleMarketDataStore,
        providers: Mapping[str, CandleProvider],
        *,
        worker_id: str | None = None,
    ) -> None:
        self.store = store
        self.providers = {key.upper(): value for key, value in providers.items()}
        self.worker_id = worker_id or "market-data-" + uuid.uuid4().hex[:12]

    def run_once(self) -> dict[str, Any] | None:
        job = self.store.claim_backfill(self.worker_id)
        if job is None:
            return None
        provider = self.providers.get(job.provider.upper())
        if provider is None:
            status = self.store.fail_backfill(
                job.job_id, RuntimeError(f"Provider adapter is not configured: {job.provider}")
            )
            return {"jobId": str(job.job_id), "status": status, "written": 0}
        if job.next_start >= job.range_end:
            try:
                empty = self.store.reconcile(
                    [],
                    market=job.market,
                    provider=job.provider,
                    instrument_id=job.instrument_id,
                    timeframe=job.timeframe,
                    start=job.range_end,
                    end=job.range_end,
                )
                self.store.advance_backfill(
                    job.job_id,
                    next_start=job.range_end,
                    received=0,
                    written=0,
                    reconciliation=empty,
                )
                repair = GapRepairService(self.store, self.providers).repair(
                    market=job.market,
                    provider=job.provider,
                    instrument_id=job.instrument_id,
                    timeframe=job.timeframe,
                    start=job.range_start,
                    end=job.range_end,
                )
                status = "COMPLETE"
                if repair.status != "HEALTHY":
                    status = self.store.fail_backfill(
                        job.job_id,
                        RuntimeError(
                            f"Final gap repair ended with {repair.status} and "
                            f"{repair.missing_after} missing candles"
                        ),
                    )
                return {
                    "jobId": str(job.job_id),
                    "status": status,
                    "written": repair.candles_written,
                    "repair": repair.public(),
                }
            except Exception as error:
                status = self.store.fail_backfill(job.job_id, error)
                return {
                    "jobId": str(job.job_id),
                    "status": status,
                    "written": 0,
                    "errorType": type(error).__name__,
                }
        chunk_end = min(job.range_end, job.next_start + timedelta(days=job.chunk_days))
        try:
            candles = list(
                provider.candles(job.instrument_id, job.timeframe, job.next_start, chunk_end)
            )
            for candle in candles:
                if (
                    candle.market != job.market
                    or candle.provider != job.provider
                    or candle.instrument_id != job.instrument_id
                    or candle.timeframe != job.timeframe
                    or not job.next_start <= candle.open_time < chunk_end
                    or not candle.complete
                ):
                    raise ValueError("Provider returned a candle outside the claimed backfill chunk")
            written = self.store.upsert_candles(candles)
            reconciliation = self.store.reconcile(
                candles,
                market=job.market,
                provider=job.provider,
                instrument_id=job.instrument_id,
                timeframe=job.timeframe,
                start=job.next_start,
                end=chunk_end,
            )
            if reconciliation.status != "MATCHED":
                raise RuntimeError("Canonical count/checksum reconciliation failed")
            complete = self.store.advance_backfill(
                job.job_id,
                next_start=chunk_end,
                received=len(candles),
                written=written,
                reconciliation=reconciliation,
            )
            result: dict[str, Any] = {
                "jobId": str(job.job_id),
                "status": "COMPLETE" if complete else "PENDING",
                "chunkStart": job.next_start.isoformat(),
                "chunkEnd": chunk_end.isoformat(),
                "received": len(candles),
                "written": written,
                "reconciliation": reconciliation.public(),
            }
            if complete:
                repair = GapRepairService(self.store, self.providers).repair(
                    market=job.market,
                    provider=job.provider,
                    instrument_id=job.instrument_id,
                    timeframe=job.timeframe,
                    start=job.range_start,
                    end=job.range_end,
                )
                result["repair"] = repair.public()
                if repair.status != "HEALTHY":
                    result["status"] = self.store.fail_backfill(
                        job.job_id,
                        RuntimeError(
                            f"Final gap repair ended with {repair.status} and "
                            f"{repair.missing_after} missing candles"
                        ),
                    )
            return result
        except Exception as error:
            status = self.store.fail_backfill(job.job_id, error)
            return {
                "jobId": str(job.job_id),
                "status": status,
                "written": 0,
                "errorType": type(error).__name__,
            }

    def run_pending(self, *, maximum_chunks: int = 100) -> list[dict[str, Any]]:
        if not 1 <= maximum_chunks <= 10_000:
            raise ValueError("maximum_chunks must be between 1 and 10000")
        results: list[dict[str, Any]] = []
        for _ in range(maximum_chunks):
            result = self.run_once()
            if result is None:
                break
            results.append(result)
        return results


class GapRepairService:
    def __init__(self, store: TimescaleMarketDataStore, providers: dict[str, CandleProvider]) -> None:
        self.store = store
        self.providers = {key.upper(): value for key, value in providers.items()}

    def expected_times(
        self, market: str, timeframe: str, start: datetime, end: datetime
    ) -> list[datetime]:
        if market.upper() == "CRYPTO":
            return crypto_expected_open_times(start, end, timeframe)
        if market.upper() == "NSE":
            sessions = self.store.session_dates("NSE", start, end)
            return nse_expected_open_times(sessions, start, end, timeframe)
        raise ValueError(f"Unsupported market: {market}")

    def repair(
        self, *, market: str, provider: str, instrument_id: str, timeframe: str,
        start: datetime, end: datetime,
    ) -> RepairResult:
        provider_key = provider.upper()
        adapter = self.providers.get(provider_key)
        if adapter is None:
            return RepairResult(market, provider, instrument_id, timeframe, "UNSUPPORTED", 0, 0, 0, 0,
                                f"Provider adapter is not configured: {provider_key}")
        expected = self.expected_times(market, timeframe, start, end)
        actual = self.store.candle_times(market, provider, instrument_id, timeframe, start, end)
        ranges = contiguous_missing_ranges(expected, actual, timeframe)
        before = sum(item.missing_candles for item in ranges)
        if not ranges:
            self._record(market, provider, instrument_id, timeframe, "HEALTHY", expected, actual, 0)
            return RepairResult(market, provider, instrument_id, timeframe, "HEALTHY", 0, 0, 0, 0)
        self._record(market, provider, instrument_id, timeframe, "REPAIRING", expected, actual, before)
        received = written = 0
        try:
            for gap in ranges:
                candles = list(adapter.candles(instrument_id, timeframe, gap.start, gap.end))
                received += len(candles)
                written += self.store.upsert_candles(candles)
            after_times = self.store.candle_times(market, provider, instrument_id, timeframe, start, end)
            after = sum(item.missing_candles for item in contiguous_missing_ranges(expected, after_times, timeframe))
            status: HealthStatus = "HEALTHY" if after == 0 else "GAPS_DETECTED"
            self._record(market, provider, instrument_id, timeframe, status, expected, after_times, after)
            return RepairResult(market, provider, instrument_id, timeframe, status, before, received, written, after)
        except Exception as error:
            message = f"{type(error).__name__}: {error}"
            self._record(market, provider, instrument_id, timeframe, "PROVIDER_UNAVAILABLE", expected, actual, before, message)
            return RepairResult(market, provider, instrument_id, timeframe, "PROVIDER_UNAVAILABLE",
                                before, received, written, before, message)

    def _record(
        self, market: str, provider: str, instrument_id: str, timeframe: str,
        status: HealthStatus, expected: Sequence[datetime], actual: Sequence[datetime],
        missing: int, error: str | None = None,
    ) -> None:
        self.store.record_health(
            market=market, provider=provider, instrument_id=instrument_id, timeframe=timeframe,
            status=status, expected_last_candle=expected[-1] if expected else None,
            actual_last_candle=actual[-1] if actual else None, missing_candles=missing, error=error,
        )


def candle_data_version(candles: Sequence[CanonicalCandle]) -> str:
    digest = hashlib.sha256()
    for item in sorted(candles, key=lambda row: (row.instrument_id, row.timeframe, row.open_time)):
        digest.update(json.dumps({
            "instrument": item.instrument_id, "timeframe": item.timeframe,
            "open_time": item.open_time.isoformat(), "ohlcv": [item.open, item.high, item.low, item.close, item.volume],
        }, separators=(",", ":"), sort_keys=True).encode())
    return "sha256:" + digest.hexdigest()


def timescale_health(database_url: str | None) -> dict[str, Any]:
    if not database_url:
        return {"status": "NOT_CONFIGURED", "sourceOfTruth": False}
    try:
        with connect(database_url, connect_timeout=3, row_factory=dict_row) as connection:
            row = connection.execute(
                """SELECT count(*) AS candles, max(open_time) AS latest_candle,
                          count(DISTINCT instrument_id) AS instruments
                   FROM market_candles WHERE complete"""
            ).fetchone()
            extension = connection.execute(
                "SELECT extversion FROM pg_extension WHERE extname='timescaledb'"
            ).fetchone()
            health_rows = connection.execute(
                """SELECT status, count(*) AS count, coalesce(sum(missing_candles), 0) AS missing
                   FROM market_data_health GROUP BY status ORDER BY status"""
            ).fetchall()
            job_rows = connection.execute(
                """SELECT status, count(*) AS count
                   FROM market_data_repair_jobs GROUP BY status ORDER BY status"""
            ).fetchall()
        data_health = {str(item["status"]): int(item["count"]) for item in health_rows}
        repair_jobs = {str(item["status"]): int(item["count"]) for item in job_rows}
        missing = sum(int(item["missing"]) for item in health_rows)
        unhealthy_rows = sum(
            count for status, count in data_health.items() if status != "HEALTHY"
        )
        unfinished_jobs = sum(
            count for status, count in repair_jobs.items() if status != "COMPLETE"
        )
        degraded = int(row["candles"]) == 0 or missing > 0 or unhealthy_rows > 0 or unfinished_jobs > 0
        return {
            "status": "DEGRADED" if degraded else "HEALTHY", "sourceOfTruth": True,
            "timescaleVersion": extension["extversion"] if extension else None,
            "candles": int(row["candles"]), "instruments": int(row["instruments"]),
            "latestCandle": row["latest_candle"].isoformat() if row["latest_candle"] else None,
            "dataHealth": data_health,
            "missingCandles": missing,
            "repairJobs": repair_jobs,
        }
    except Exception as error:
        return {"status": "UNAVAILABLE", "sourceOfTruth": False, "errorType": type(error).__name__}
