from __future__ import annotations

import json
import sqlite3
import threading
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime, time as datetime_time, timedelta
from pathlib import Path
from typing import Any, Literal
from zoneinfo import ZoneInfo

import pandas as pd

from .core import stable_id, utc_now_iso


@dataclass(frozen=True)
class ProviderCapabilities:
    provider: str
    markets: tuple[str, ...]
    timeframes: tuple[str, ...]
    data_types: tuple[str, ...]
    timezone: str
    public_only: bool


PROVIDER_CAPABILITIES = {
    "DHAN": ProviderCapabilities(
        "DHAN", ("NSE",), ("1m", "5m", "15m", "30m", "1h", "2h", "4h", "1d"),
        ("historical_candles", "live_quotes"), "Asia/Kolkata", False
    ),
    "OKX": ProviderCapabilities(
        "OKX", ("CRYPTO",), ("1m", "5m", "15m", "30m", "1h", "6h", "1d"),
        ("instrument_catalog", "historical_candles"), "UTC", True
    ),
    "VALR": ProviderCapabilities(
        "VALR", ("CRYPTO",), ("1m", "5m", "15m", "30m", "1h", "6h", "1d"),
        ("instrument_catalog", "historical_candles"), "UTC", True
    ),
}


TIMEFRAME_DELTAS = {
    "1m": pd.Timedelta(minutes=1),
    "5m": pd.Timedelta(minutes=5),
    "15m": pd.Timedelta(minutes=15),
    "30m": pd.Timedelta(minutes=30),
    "1h": pd.Timedelta(hours=1),
    "2h": pd.Timedelta(hours=2),
    "4h": pd.Timedelta(hours=4),
    "6h": pd.Timedelta(hours=6),
    "1d": pd.Timedelta(days=1),
}


@dataclass(frozen=True)
class DataQualityReport:
    status: Literal["HEALTHY", "DEGRADED", "INVALID"]
    rows_received: int
    rows_normalized: int
    duplicate_timestamps: int
    incomplete_candles: int
    missing_candles: int
    first_timestamp: str | None
    last_timestamp: str | None
    issues: tuple[str, ...]

    def public(self) -> dict[str, Any]:
        return asdict(self)


def normalize_candles(
    frame: pd.DataFrame,
    *,
    timeframe: str,
    now: datetime | None = None,
    timestamp_column: str = "timestamp",
    timestamp_represents: Literal["START", "CLOSE"] = "START",
    market: Literal["NSE", "CRYPTO"] | None = None,
) -> tuple[pd.DataFrame, DataQualityReport]:
    required = {timestamp_column, "open", "high", "low", "close", "volume"}
    missing = sorted(required.difference(frame.columns))
    if missing:
        raise ValueError(f"Missing candle columns: {', '.join(missing)}")
    received = len(frame)
    normalized = frame.copy()
    normalized[timestamp_column] = pd.to_datetime(normalized[timestamp_column], utc=True, errors="coerce")
    for column in ("open", "high", "low", "close", "volume"):
        normalized[column] = pd.to_numeric(normalized[column], errors="coerce")
    normalized = normalized.dropna(subset=list(required))
    duplicate_count = int(normalized.duplicated(timestamp_column, keep="last").sum())
    normalized = normalized.drop_duplicates(timestamp_column, keep="last").sort_values(timestamp_column)
    invalid_ohlc = (
        (normalized["high"] < normalized[["open", "close", "low"]].max(axis=1))
        | (normalized["low"] > normalized[["open", "close", "high"]].min(axis=1))
        | (normalized["volume"] < 0)
    )
    invalid_count = int(invalid_ohlc.sum())
    normalized = normalized.loc[~invalid_ohlc].copy()
    try:
        interval = TIMEFRAME_DELTAS[timeframe]
    except KeyError as error:
        raise ValueError(f"Unsupported timeframe: {timeframe}") from error
    current = pd.Timestamp(now or datetime.now(UTC))
    if current.tzinfo is None:
        current = current.tz_localize("UTC")
    else:
        current = current.tz_convert("UTC")
    available_at = normalized[timestamp_column]
    if timestamp_represents == "START":
        available_at = available_at + interval
    completed = available_at <= current
    incomplete = int((~completed).sum())
    normalized = normalized.loc[completed].copy()
    missing_candles = 0
    if len(normalized) > 1:
        gaps = normalized[timestamp_column].diff().dropna()
        if market == "NSE":
            local_sessions = normalized[timestamp_column].dt.tz_convert("Asia/Kolkata").dt.date
            gaps = gaps.loc[local_sessions.eq(local_sessions.shift(1)).iloc[1:].to_numpy()]
        missing_candles = int(sum(max(0, round(gap / interval) - 1) for gap in gaps))
    issues = []
    if duplicate_count:
        issues.append("DUPLICATE_TIMESTAMPS_REMOVED")
    if incomplete:
        issues.append("INCOMPLETE_CANDLES_REMOVED")
    if invalid_count:
        issues.append("INVALID_OHLCV_REMOVED")
    if missing_candles:
        issues.append("MISSING_CANDLES_DETECTED")
    status: Literal["HEALTHY", "DEGRADED", "INVALID"] = "HEALTHY"
    if normalized.empty:
        status = "INVALID"
    elif issues:
        status = "DEGRADED"
    first = normalized[timestamp_column].iloc[0].isoformat() if not normalized.empty else None
    last = normalized[timestamp_column].iloc[-1].isoformat() if not normalized.empty else None
    return normalized.reset_index(drop=True), DataQualityReport(
        status=status,
        rows_received=received,
        rows_normalized=len(normalized),
        duplicate_timestamps=duplicate_count,
        incomplete_candles=incomplete,
        missing_candles=missing_candles,
        first_timestamp=first,
        last_timestamp=last,
        issues=tuple(issues),
    )


def align_completed_timeframe(
    lower: pd.DataFrame,
    higher: pd.DataFrame,
    *,
    lower_timestamp: str = "timestamp",
    higher_close_timestamp: str = "timestamp",
    prefix: str = "context_",
    market: Literal["NSE", "CRYPTO"] | None = None,
) -> pd.DataFrame:
    """Backward-as-of join; higher candles become visible only at their close timestamp."""
    left = lower.copy()
    right = higher.copy()
    left[lower_timestamp] = pd.to_datetime(left[lower_timestamp], utc=True)
    right[higher_close_timestamp] = pd.to_datetime(right[higher_close_timestamp], utc=True)
    left = left.sort_values(lower_timestamp)
    right = right.sort_values(higher_close_timestamp)
    renamed = {
        column: f"{prefix}{column}"
        for column in right.columns
        if column != higher_close_timestamp
    }
    right = right.rename(columns=renamed).rename(columns={higher_close_timestamp: f"{prefix}available_at"})
    by = None
    if market == "NSE":
        left["_market_session"] = left[lower_timestamp].dt.tz_convert("Asia/Kolkata").dt.date
        right["_market_session"] = right[f"{prefix}available_at"].dt.tz_convert("Asia/Kolkata").dt.date
        by = "_market_session"
    aligned = pd.merge_asof(
        left,
        right,
        left_on=lower_timestamp,
        right_on=f"{prefix}available_at",
        by=by,
        direction="backward",
        allow_exact_matches=True,
    )
    return aligned.drop(columns=["_market_session"], errors="ignore")


@dataclass(frozen=True)
class FeatureCacheKey:
    market: str
    symbol: str
    provider: str
    data_version: str
    date_range: tuple[str, str]
    timeframe: str
    factor_id: str
    factor_version: str
    parameters: dict[str, Any]
    benchmark_dependency: str | None
    sector_dependency: str | None
    session_calendar_version: str = "UNSPECIFIED"

    @property
    def key(self) -> str:
        return stable_id("feature", asdict(self))


class FeatureCache:
    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._metrics_lock = threading.Lock()
        self._hits = 0
        self._misses = 0
        self._writes = 0
        self._invalidations = 0
        self.migrate()

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=30)
        connection.row_factory = sqlite3.Row
        return connection

    def migrate(self) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS feature_cache (
                    cache_key TEXT PRIMARY KEY,
                    factor_id TEXT NOT NULL,
                    data_version TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_feature_cache_factor_version ON feature_cache(factor_id, data_version)"
            )
            connection.execute("PRAGMA optimize")

    def get(self, key: FeatureCacheKey) -> Any | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT payload_json FROM feature_cache WHERE cache_key = ?", (key.key,)
            ).fetchone()
        with self._metrics_lock:
            if row:
                self._hits += 1
            else:
                self._misses += 1
        return json.loads(row["payload_json"]) if row else None

    def put(self, key: FeatureCacheKey, payload: Any) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO feature_cache(cache_key, factor_id, data_version, payload_json, created_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(cache_key) DO UPDATE SET payload_json=excluded.payload_json, created_at=excluded.created_at
                """,
                (
                    key.key,
                    key.factor_id,
                    key.data_version,
                    json.dumps(payload, allow_nan=False, separators=(",", ":")),
                    utc_now_iso(),
                ),
            )
        with self._metrics_lock:
            self._writes += 1

    def invalidate_data_version(self, data_version: str) -> int:
        with self.connect() as connection:
            cursor = connection.execute("DELETE FROM feature_cache WHERE data_version = ?", (data_version,))
            removed = int(cursor.rowcount)
        with self._metrics_lock:
            self._invalidations += removed
        return removed

    def health(self) -> dict[str, Any]:
        with self.connect() as connection:
            count = int(connection.execute("SELECT COUNT(*) FROM feature_cache").fetchone()[0])
        with self._metrics_lock:
            metrics = {
                "hits": self._hits,
                "misses": self._misses,
                "writes": self._writes,
                "invalidations": self._invalidations,
            }
        return {"status": "ok", "entries": count, **metrics}


def file_data_version(path: Path) -> str:
    stat = path.stat()
    return stable_id("data", {"path": path.name, "size": stat.st_size, "mtimeNs": stat.st_mtime_ns})


NSE_TIMEZONE = ZoneInfo("Asia/Kolkata")
NSE_MARKET_OPEN = datetime_time(9, 15)
NSE_MARKET_CLOSE = datetime_time(15, 30)


def _previous_weekday(value: date) -> date:
    candidate = value - timedelta(days=1)
    while candidate.weekday() >= 5:
        candidate -= timedelta(days=1)
    return candidate


def _nse_session_state(now: datetime) -> tuple[str, date]:
    local_now = now.astimezone(NSE_TIMEZONE)
    local_date = local_now.date()
    local_time = local_now.time().replace(tzinfo=None)
    weekday = local_date.weekday()

    if weekday >= 5:
        return "CLOSED", _previous_weekday(local_date)
    if local_time < NSE_MARKET_OPEN:
        return "CLOSED", _previous_weekday(local_date)
    if local_time >= NSE_MARKET_CLOSE:
        return "CLOSED", local_date
    return "OPEN", local_date


def freshness(
    path: Path,
    stale_seconds: int,
    *,
    market: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    if not path.exists():
        return {"status": "UNAVAILABLE", "reason": "MARKET_DATA_FILE_MISSING"}

    checked_at = now or datetime.now(UTC)
    if checked_at.tzinfo is None:
        raise ValueError("freshness now must be timezone-aware")

    modified = datetime.fromtimestamp(path.stat().st_mtime, UTC)
    age = max(0.0, (checked_at - modified).total_seconds())
    result = {
        "status": "FRESH" if age <= stale_seconds else "STALE",
        "modifiedAt": modified.isoformat(),
        "ageSeconds": round(age, 1),
        "thresholdSeconds": stale_seconds,
        "dataVersion": file_data_version(path),
    }

    if market == "NSE":
        market_status, expected_session = _nse_session_state(checked_at)
        data_session = modified.astimezone(NSE_TIMEZONE).date()
        result.update({
            "marketStatus": market_status,
            "expectedSessionDate": expected_session.isoformat(),
            "dataSessionDate": data_session.isoformat(),
        })
        if market_status == "CLOSED" and data_session >= expected_session:
            result["status"] = "FRESH"
            result["reason"] = "MARKET_CLOSED_LAST_SESSION_CURRENT"

    return result
