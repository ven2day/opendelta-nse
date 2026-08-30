from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Callable, Iterable, Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Any, Literal, Protocol
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


class CandleProvider(Protocol):
    def candles(
        self, instrument_id: str, timeframe: str, start: datetime, end: datetime
    ) -> Sequence[CanonicalCandle]: ...


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

    def __init__(self, database_url: str, *, min_pool_size: int = 1, max_pool_size: int = 8) -> None:
        if not database_url.startswith(("postgresql://", "postgres://")):
            raise ValueError("MARKET_DATA_DATABASE_URL must be a PostgreSQL URL")
        self.pool = ConnectionPool(
            database_url,
            min_size=min_pool_size,
            max_size=max_pool_size,
            kwargs={"row_factory": dict_row, "autocommit": False},
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
        return {
            "status": "HEALTHY", "sourceOfTruth": True,
            "timescaleVersion": extension["extversion"] if extension else None,
            "candles": int(row["candles"]), "instruments": int(row["instruments"]),
            "latestCandle": row["latest_candle"].isoformat() if row["latest_candle"] else None,
        }
    except Exception as error:
        return {"status": "UNAVAILABLE", "sourceOfTruth": False, "errorType": type(error).__name__}
