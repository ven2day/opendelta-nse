from __future__ import annotations

import hashlib
import gzip
import json
import math
import os
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime, time as datetime_time
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import numpy as np
import pandas as pd

try:  # resource is unavailable on Windows, where the test suite also runs.
    import resource
except ImportError:  # pragma: no cover - exercised by Windows CI/runtime
    resource = None

from main import DhanConfig, IST
from market_aligned_rsi_scalper import (
    DATA_UNAVAILABLE_REASON_CODES,
    REASON_MESSAGES,
    MarketAlignedConfig,
)
from nifty_oi_regime import NiftyOiConfig, score_spot_trend
from recovery_backtest import (
    RecoveryConfig,
    calculate_recovery_indicators,
    simulate_recovery_symbol,
)


FEATURE_CODE_VERSION = "market-aligned-features-1"
SHARED_CONTEXT_CODE_VERSION = "market-aligned-context-1"
FEATURE_COLUMNS = (
    "Open",
    "High",
    "Low",
    "Close",
    "Volume",
    "RecoveryRSI",
    "EMAFast",
    "EMASlow",
    "SessionVWAP",
    "VolumeEMA",
    "MarketReturnPct",
    "MedianTradedValue",
    "RangeQualityPct",
    "RoomToTargetPct",
    "RVOL",
)


def process_peak_memory_bytes() -> int:
    if resource is None:
        return 0
    peak = int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
    # Linux reports KiB; macOS reports bytes.
    return peak if os.uname().sysname == "Darwin" else peak * 1024


def _finite(value: Any, digits: int = 4) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return round(number, digits) if math.isfinite(number) else None


def _nifty_finite(value: Any) -> float | None:
    return _finite(value, 6)


def _as_ist(value: Any) -> datetime:
    stamp = pd.Timestamp(value)
    if stamp.tzinfo is None:
        stamp = stamp.tz_localize(IST)
    else:
        stamp = stamp.tz_convert(IST)
    return stamp.to_pydatetime()


def file_stat_fingerprint(path: Path | None) -> str:
    if path is None or not path.is_file():
        return "MISSING"
    stat = path.stat()
    return f"{path.name}:{stat.st_size}:{stat.st_mtime_ns}"


def stable_fingerprint(payload: Any) -> str:
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class BacktestResultCache:
    """Atomic completed-result cache; partial runs are never readable."""

    def __init__(self, root: Path) -> None:
        self.root = root

    def path_for(self, fingerprint: str) -> Path:
        return self.root / fingerprint[:2] / f"{fingerprint}.json.gz"

    def load(self, fingerprint: str) -> dict[str, Any] | None:
        path = self.path_for(fingerprint)
        try:
            with gzip.open(path, "rt", encoding="utf-8") as handle:
                response = json.load(handle)
        except (OSError, ValueError, TypeError):
            return None
        if response.get("metadata", {}).get("fingerprint") != fingerprint:
            return None
        metadata = response.setdefault("metadata", {})
        metadata["cachedResult"] = True
        metadata["originalRunTimestamp"] = metadata.get("completedAt")
        return response

    def save(self, fingerprint: str, response: Mapping[str, Any]) -> int:
        path = self.path_for(fingerprint)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                dir=path.parent, prefix=f".{fingerprint}.", suffix=".json.gz", delete=False
            ) as handle:
                temporary = Path(handle.name)
            with gzip.open(temporary, "wt", encoding="utf-8", compresslevel=5) as handle:
                json.dump(response, handle, sort_keys=True, separators=(",", ":"), default=str)
            temporary.chmod(0o600)
            os.replace(temporary, path)
        finally:
            if temporary is not None and temporary.exists():
                temporary.unlink()
        return path.stat().st_size


def _time_bucket(value: datetime, timeframe: str) -> str:
    stamp = pd.Timestamp(value).tz_convert(IST)
    minutes = {
        "5m": 5,
        "15m": 15,
        "30m": 30,
        "1h": 60,
        "2h": 120,
        "4h": 240,
        "1d": 1_440,
    }[timeframe]
    if minutes == 1_440:
        return stamp.normalize().isoformat()
    day_start = stamp.normalize()
    elapsed_minutes = int((stamp - day_start).total_seconds() // 60)
    bucket = elapsed_minutes // minutes * minutes
    return (day_start + pd.Timedelta(minutes=bucket)).isoformat()


def _recovery_feature_parameters(config: RecoveryConfig) -> dict[str, Any]:
    return {
        "rsiLength": config.rsi_length,
        "emaFast": config.ema_fast,
        "emaSlow": config.ema_slow,
        "volumeEma": config.volume_ema,
    }


def _market_feature_parameters(config: MarketAlignedConfig) -> dict[str, Any]:
    return {
        "relativeStrengthLookbackBars": config.relative_strength_lookback_bars,
        "roomLookbackBars": config.room_lookback_bars,
        "rvolPeriod": config.rvol_period,
    }


def calculate_market_feature_frame(
    candles: pd.DataFrame,
    recovery_config: RecoveryConfig,
    market_config: MarketAlignedConfig,
) -> pd.DataFrame:
    """Calculate every deterministic stock feature once with causal windows."""
    data = calculate_recovery_indicators(candles, recovery_config)
    close = pd.to_numeric(data["Close"], errors="coerce").astype(float)
    high = pd.to_numeric(data["High"], errors="coerce").astype(float)
    low = pd.to_numeric(data["Low"], errors="coerce").astype(float)
    volume = pd.to_numeric(data["Volume"], errors="coerce").astype(float)
    previous = close.shift(market_config.relative_strength_lookback_bars)
    data["MarketReturnPct"] = (close / previous - 1.0) * 100.0
    traded_value = close * volume
    data["MedianTradedValue"] = traded_value.rolling(
        market_config.rvol_period,
        min_periods=market_config.rvol_period,
    ).median()
    data["RangeQualityPct"] = (high - low) / close * 100.0
    resistance = high.shift(1).rolling(
        market_config.room_lookback_bars,
        min_periods=market_config.room_lookback_bars,
    ).max()
    data["RoomToTargetPct"] = (resistance / close - 1.0) * 100.0
    market_volume_ema = volume.ewm(
        span=market_config.rvol_period,
        adjust=False,
        min_periods=market_config.rvol_period,
    ).mean()
    data["RVOL"] = volume / market_volume_ema
    return data.loc[:, list(FEATURE_COLUMNS)]


@dataclass(frozen=True)
class FeatureCacheResult:
    frame: pd.DataFrame
    path: Path
    hit: bool
    bytes_read: int
    read_seconds: float
    calculation_seconds: float
    write_seconds: float
    source_fingerprint: str


class MarketFeatureCache:
    def __init__(self, root: Path) -> None:
        self.root = root

    def _key(
        self,
        *,
        symbol: str,
        timeframe: str,
        duration_years: int,
        analysis_start: datetime,
        now: datetime,
        warmup_bars: int,
        source_fingerprint: str,
        recovery_config: RecoveryConfig,
        market_config: MarketAlignedConfig,
    ) -> str:
        return stable_fingerprint({
            "version": FEATURE_CODE_VERSION,
            "symbol": symbol,
            "timeframe": timeframe,
            "durationYears": duration_years,
            "analysisStartBucket": _time_bucket(analysis_start, timeframe),
            "endBucket": _time_bucket(now, timeframe),
            "warmupBars": warmup_bars,
            "source": source_fingerprint,
            "recovery": _recovery_feature_parameters(recovery_config),
            "market": _market_feature_parameters(market_config),
        })

    def path_for(self, symbol: str, key: str) -> Path:
        safe_symbol = "".join(
            character for character in symbol if character.isalnum() or character in "-&"
        )
        return self.root / safe_symbol / f"{key}.parquet"

    def load(
        self,
        *,
        symbol: str,
        timeframe: str,
        duration_years: int,
        analysis_start: datetime,
        now: datetime,
        warmup_bars: int,
        source_path: Path,
        recovery_config: RecoveryConfig,
        market_config: MarketAlignedConfig,
        max_source_age_seconds: int = 3_600,
    ) -> FeatureCacheResult | None:
        source_fingerprint = file_stat_fingerprint(source_path)
        if source_fingerprint == "MISSING":
            return None
        if time.time() - source_path.stat().st_mtime > max_source_age_seconds:
            return None
        key = self._key(
            symbol=symbol,
            timeframe=timeframe,
            duration_years=duration_years,
            analysis_start=analysis_start,
            now=now,
            warmup_bars=warmup_bars,
            source_fingerprint=source_fingerprint,
            recovery_config=recovery_config,
            market_config=market_config,
        )
        path = self.path_for(symbol, key)
        if not path.is_file():
            return None
        started = time.perf_counter()
        try:
            frame = pd.read_parquet(path)
            if not isinstance(frame.index, pd.DatetimeIndex):
                return None
            frame.index = (
                frame.index.tz_localize(IST)
                if frame.index.tz is None
                else frame.index.tz_convert(IST)
            )
            if tuple(frame.columns) != FEATURE_COLUMNS:
                return None
            return FeatureCacheResult(
                frame=frame,
                path=path,
                hit=True,
                bytes_read=path.stat().st_size,
                read_seconds=time.perf_counter() - started,
                calculation_seconds=0.0,
                write_seconds=0.0,
                source_fingerprint=source_fingerprint,
            )
        except (OSError, ValueError, KeyError):
            return None

    def build(
        self,
        *,
        symbol: str,
        timeframe: str,
        duration_years: int,
        analysis_start: datetime,
        now: datetime,
        warmup_bars: int,
        source_path: Path,
        candles: pd.DataFrame,
        recovery_config: RecoveryConfig,
        market_config: MarketAlignedConfig,
        source_bytes_read: int,
        source_read_seconds: float,
    ) -> FeatureCacheResult:
        source_fingerprint = file_stat_fingerprint(source_path)
        key = self._key(
            symbol=symbol,
            timeframe=timeframe,
            duration_years=duration_years,
            analysis_start=analysis_start,
            now=now,
            warmup_bars=warmup_bars,
            source_fingerprint=source_fingerprint,
            recovery_config=recovery_config,
            market_config=market_config,
        )
        path = self.path_for(symbol, key)
        calculation_started = time.perf_counter()
        frame = calculate_market_feature_frame(candles, recovery_config, market_config)
        calculation_seconds = time.perf_counter() - calculation_started
        path.parent.mkdir(parents=True, exist_ok=True)
        write_started = time.perf_counter()
        temporary: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                dir=path.parent,
                prefix=f".{path.stem}.",
                suffix=".parquet",
                delete=False,
            ) as handle:
                temporary = Path(handle.name)
            frame.to_parquet(temporary, index=True, compression="zstd")
            temporary.chmod(0o600)
            os.replace(temporary, path)
        finally:
            if temporary is not None and temporary.exists():
                temporary.unlink()
        return FeatureCacheResult(
            frame=frame,
            path=path,
            hit=False,
            bytes_read=source_bytes_read,
            read_seconds=source_read_seconds,
            calculation_seconds=calculation_seconds,
            write_seconds=time.perf_counter() - write_started,
            source_fingerprint=source_fingerprint,
        )


def _stock_context_at(
    feature_frame: pd.DataFrame,
    timestamp: datetime,
    config: MarketAlignedConfig,
) -> dict[str, Any]:
    stamp = pd.Timestamp(timestamp)
    position = int(feature_frame.index.searchsorted(stamp, side="right")) - 1
    minimum = max(config.ema_slow, config.rvol_period, config.room_lookback_bars + 1)
    if position < minimum - 1:
        return {"available": False, "reasonCode": "MISSING_STOCK_DATA"}
    source = pd.Timestamp(feature_frame.index[position])
    age = (stamp - source).total_seconds()
    if age < 0 or age > config.stale_data_seconds:
        return {
            "available": False,
            "reasonCode": "STALE_STOCK_DATA",
            "sourceTimestamp": _as_ist(source).isoformat(),
        }
    row = feature_frame.iloc[position]
    prior = feature_frame.iloc[position - 1]
    return {
        "available": True,
        "sourceTimestamp": _as_ist(source).isoformat(),
        "previousRsi": _finite(prior["RecoveryRSI"], 6),
        "price": float(row["Close"]),
        "sessionVwap": float(row["SessionVWAP"]),
        "emaFast": float(row["EMAFast"]),
        "emaSlow": float(row["EMASlow"]),
        "emaFastPrevious": float(prior["EMAFast"]),
        "rvol": float(row["RVOL"]),
        "medianTradedValue": float(row["MedianTradedValue"]),
        "rangeQualityPct": float(row["RangeQualityPct"]),
        "roomToTargetPct": float(row["RoomToTargetPct"]),
        "returnPct": float(row["MarketReturnPct"]),
    }


def _raw_cache_path(
    cache_directory: Path,
    symbol: str,
    timeframe: str,
    duration_years: int,
    *,
    benchmark: bool = False,
) -> Path:
    source_interval = {
        "5m": "5",
        "15m": "15",
        "30m": "30",
        "1h": "60",
        "2h": "60",
        "4h": "60",
        "1d": "daily",
    }[timeframe]
    safe_symbol = "NIFTY50" if benchmark else "".join(
        character for character in symbol if character.isalnum() or character in "-&"
    )
    return cache_directory / f"{safe_symbol}-{source_interval}-{duration_years}y.csv.gz"


def _load_worker_candles(
    *,
    source_path: Path,
    symbol: str,
    cache_directory: Path,
    timeframe: str,
    duration_years: int,
    analysis_start: datetime,
    now: datetime,
    warmup_bars: int,
    max_source_age_seconds: int,
) -> tuple[pd.DataFrame, int, float, float]:
    from backtest_api import HistoricalDataStore, prepare_candles

    if (
        source_path.is_file()
        and time.time() - source_path.stat().st_mtime <= max_source_age_seconds
    ):
        read_started = time.perf_counter()
        raw = pd.read_csv(source_path, index_col="Timestamp", parse_dates=["Timestamp"])
        read_seconds = time.perf_counter() - read_started
        raw.index = pd.DatetimeIndex(raw.index)
        raw.index = raw.index.tz_localize(IST) if raw.index.tz is None else raw.index.tz_convert(IST)
        conversion_started = time.perf_counter()
        candles = prepare_candles(
            raw, timeframe, analysis_start, now, warmup_bars=warmup_bars
        )
        return (
            candles,
            source_path.stat().st_size,
            read_seconds,
            time.perf_counter() - conversion_started,
        )
    # Only the stale/missing path needs credentials and may refresh from Dhan.
    started = time.perf_counter()
    store = HistoricalDataStore(DhanConfig.from_environment(), cache_directory)
    candles = store.candles(
        symbol, timeframe, duration_years, analysis_start, now,
        warmup_bars=warmup_bars,
    )
    return (
        candles,
        source_path.stat().st_size if source_path.is_file() else 0,
        time.perf_counter() - started,
        0.0,
    )


def prepare_market_symbol_task(task: Mapping[str, Any]) -> dict[str, Any]:
    """Process one symbol in a bounded worker without copying the universe."""
    # Imported lazily so the worker process starts with only its own symbol data.
    from backtest_api import HistoricalDataStore

    symbol = str(task["symbol"])
    cache_directory = Path(str(task["cacheDirectory"]))
    feature_cache = MarketFeatureCache(Path(str(task["featureCacheDirectory"])))
    recovery_config: RecoveryConfig = task["recoveryConfig"]
    market_config: MarketAlignedConfig = task["marketConfig"]
    analysis_start: datetime = task["analysisStart"]
    now: datetime = task["now"]
    timeframe = str(task["timeframe"])
    duration_years = int(task["durationYears"])
    warmup_bars = int(task["warmupBars"])
    source_path = _raw_cache_path(cache_directory, symbol, timeframe, duration_years)
    cached = feature_cache.load(
        symbol=symbol,
        timeframe=timeframe,
        duration_years=duration_years,
        analysis_start=analysis_start,
        now=now,
        warmup_bars=warmup_bars,
        source_path=source_path,
        recovery_config=recovery_config,
        market_config=market_config,
        max_source_age_seconds=int(task.get("rawCacheTtlSeconds", 3_600)),
    )
    candle_read_seconds = 0.0
    candle_conversion_seconds = 0.0
    source_bytes_read = 0
    if cached is None:
        candles, source_bytes_read, candle_read_seconds, candle_conversion_seconds = _load_worker_candles(
            source_path=source_path, symbol=symbol, cache_directory=cache_directory,
            timeframe=timeframe, duration_years=duration_years,
            analysis_start=analysis_start, now=now, warmup_bars=warmup_bars,
            max_source_age_seconds=int(task.get("rawCacheTtlSeconds", 3_600)),
        )
        cached = feature_cache.build(
            symbol=symbol,
            timeframe=timeframe,
            duration_years=duration_years,
            analysis_start=analysis_start,
            now=now,
            warmup_bars=warmup_bars,
            source_path=source_path,
            candles=candles,
            recovery_config=recovery_config,
            market_config=market_config,
            source_bytes_read=source_bytes_read,
            source_read_seconds=candle_read_seconds,
        )
    simulation_started = time.perf_counter()
    observations = simulate_recovery_symbol(
        symbol,
        cached.frame,
        timeframe=timeframe,
        config=recovery_config,
        run_id=str(task["runId"]),
        analysis_start=analysis_start,
        indicator_frame=cached.frame,
    )
    simulation_seconds = time.perf_counter() - simulation_started
    context_started = time.perf_counter()
    stock_contexts = {
        str(trade.get("tradeId") or ""): _stock_context_at(
            cached.frame,
            _as_ist(trade.get("signalTimestamp") or trade["entryTimestamp"]),
            market_config,
        )
        for trade in observations.get("trades", [])
    }
    return {
        "symbol": symbol,
        "observations": observations,
        "featurePath": str(cached.path),
        "sourceFingerprint": cached.source_fingerprint,
        "featureCacheHit": cached.hit,
        "stockContexts": stock_contexts,
        "candles": len(cached.frame),
        "metrics": {
            "candleReads": 0 if cached.hit else 1,
            "databaseQueries": 0,
            "bytesRead": cached.bytes_read,
            "candleReadSeconds": cached.read_seconds,
            "candleConversionSeconds": candle_conversion_seconds,
            "indicatorSeconds": cached.calculation_seconds,
            "featureCacheWriteSeconds": cached.write_seconds,
            "candidateAndExitSeconds": simulation_seconds,
            "stockContextSeconds": time.perf_counter() - context_started,
            "peakMemoryBytes": process_peak_memory_bytes(),
        },
    }


def prepare_support_symbol_task(task: Mapping[str, Any]) -> dict[str, Any]:
    """Warm one support feature frame; no RSI simulation is performed."""
    from backtest_api import HistoricalDataStore

    symbol = str(task["symbol"])
    cache_directory = Path(str(task["cacheDirectory"]))
    feature_cache = MarketFeatureCache(Path(str(task["featureCacheDirectory"])))
    recovery_config: RecoveryConfig = task["recoveryConfig"]
    market_config: MarketAlignedConfig = task["marketConfig"]
    analysis_start: datetime = task["analysisStart"]
    now: datetime = task["now"]
    timeframe = str(task["timeframe"])
    duration_years = int(task["durationYears"])
    warmup_bars = int(task["warmupBars"])
    source_path = _raw_cache_path(cache_directory, symbol, timeframe, duration_years)
    cached = feature_cache.load(
        symbol=symbol,
        timeframe=timeframe,
        duration_years=duration_years,
        analysis_start=analysis_start,
        now=now,
        warmup_bars=warmup_bars,
        source_path=source_path,
        recovery_config=recovery_config,
        market_config=market_config,
        max_source_age_seconds=int(task.get("rawCacheTtlSeconds", 3_600)),
    )
    if cached is None:
        candles, source_bytes, read_seconds, conversion_seconds = _load_worker_candles(
            source_path=source_path, symbol=symbol, cache_directory=cache_directory,
            timeframe=timeframe, duration_years=duration_years,
            analysis_start=analysis_start, now=now, warmup_bars=warmup_bars,
            max_source_age_seconds=int(task.get("rawCacheTtlSeconds", 3_600)),
        )
        cached = feature_cache.build(
            symbol=symbol,
            timeframe=timeframe,
            duration_years=duration_years,
            analysis_start=analysis_start,
            now=now,
            warmup_bars=warmup_bars,
            source_path=source_path,
            candles=candles,
            recovery_config=recovery_config,
            market_config=market_config,
            source_bytes_read=source_bytes,
            source_read_seconds=read_seconds,
        )
    else:
        conversion_seconds = 0.0
    return {
        "symbol": symbol,
        "featurePath": str(cached.path),
        "sourceFingerprint": cached.source_fingerprint,
        "featureCacheHit": cached.hit,
        "metrics": {
            "candleReads": 0 if cached.hit else 1,
            "databaseQueries": 0,
            "bytesRead": cached.bytes_read,
            "candleReadSeconds": cached.read_seconds,
            "candleConversionSeconds": conversion_seconds,
            "indicatorSeconds": cached.calculation_seconds,
            "featureCacheWriteSeconds": cached.write_seconds,
            "peakMemoryBytes": process_peak_memory_bytes(),
        },
    }


def prepare_market_symbol_batch(tasks: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for task in tasks:
        try:
            output.append({"item": prepare_market_symbol_task(task), "error": None})
        except (OSError, ValueError, KeyError, RuntimeError) as error:
            output.append({
                "item": None,
                "error": {"symbol": str(task.get("symbol") or ""), "message": str(error)},
            })
    return output


def prepare_support_symbol_batch(tasks: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for task in tasks:
        try:
            output.append({"item": prepare_support_symbol_task(task), "error": None})
        except (OSError, ValueError, KeyError, RuntimeError) as error:
            output.append({
                "item": None,
                "error": {"symbol": str(task.get("symbol") or ""), "message": str(error)},
            })
    return output


def _sample_positions(index: pd.DatetimeIndex, timestamps: pd.DatetimeIndex) -> np.ndarray:
    return np.asarray(index.searchsorted(timestamps, side="right"), dtype=np.int64) - 1


def _source_iso(nanoseconds: int) -> str:
    return pd.Timestamp(nanoseconds, unit="ns", tz="UTC").tz_convert(IST).isoformat()


def build_nifty_context(
    nifty_frame: pd.DataFrame,
    candidate_timestamps: pd.DatetimeIndex,
    stale_data_seconds: int,
    relative_strength_lookback_bars: int,
) -> dict[str, dict[str, Any]]:
    if nifty_frame.empty or not isinstance(nifty_frame.index, pd.DatetimeIndex):
        return {}
    data = nifty_frame.copy().sort_index()
    data.index = data.index.tz_localize(IST) if data.index.tz is None else data.index.tz_convert(IST)
    close = pd.to_numeric(data["Close"], errors="coerce").astype(float)
    positions = _sample_positions(data.index, candidate_timestamps)
    output: dict[str, dict[str, Any]] = {}
    for candidate, position in zip(candidate_timestamps, positions, strict=True):
        key = candidate.isoformat()
        context = score_spot_trend(
            data,
            candidate.to_pydatetime(),
            NiftyOiConfig(stale_data_seconds=stale_data_seconds),
        )
        relative_return = None
        relative_position = position - relative_strength_lookback_bars
        if position >= 0 and relative_position >= 0:
            current = float(close.iloc[position])
            previous = float(close.iloc[relative_position])
            if math.isfinite(current) and math.isfinite(previous) and previous != 0:
                relative_return = (current / previous - 1.0) * 100.0
        output[key] = {
            **context,
            "_relativeStrengthReturnPct": relative_return,
        }
    return output


def build_support_context(
    *,
    candidate_timestamps: pd.DatetimeIndex,
    feature_paths_by_symbol: Mapping[str, str],
    breadth_symbols: Sequence[str],
    sector_symbols: Sequence[str],
    sector_by_symbol: Mapping[str, str],
    config: MarketAlignedConfig,
) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]], dict[str, Any]]:
    count = len(candidate_timestamps)
    candidate_ns = candidate_timestamps.as_unit("ns").asi8
    stale_ns = int(config.stale_data_seconds * 1_000_000_000)
    max_ns = np.iinfo(np.int64).max
    breadth_observed = np.zeros(count, dtype=np.int32)
    breadth_bullish = np.zeros(count, dtype=np.int32)
    breadth_source = np.full(count, max_ns, dtype=np.int64)
    requested_sectors = sorted({
        sector_by_symbol.get(symbol)
        for symbol in sector_symbols
        if sector_by_symbol.get(symbol)
    })
    sector_state = {
        sector: {
            "observed": np.zeros(count, dtype=np.int32),
            "bullish": np.zeros(count, dtype=np.int32),
            "sum": np.zeros(count, dtype=np.float64),
            "source": np.full(count, max_ns, dtype=np.int64),
        }
        for sector in requested_sectors
    }
    breadth_set = set(breadth_symbols)
    sector_set = set(sector_symbols)
    bytes_read = 0
    files_read = 0
    for symbol in sorted(set(breadth_symbols) | set(sector_symbols)):
        path_value = feature_paths_by_symbol.get(symbol)
        path = Path(path_value) if path_value else None
        if path is None or not path.is_file():
            continue
        frame = pd.read_parquet(
            path,
            columns=["Close", "EMASlow", "MarketReturnPct"],
        )
        files_read += 1
        bytes_read += path.stat().st_size
        index = frame.index
        index = index.tz_localize(IST) if index.tz is None else index.tz_convert(IST)
        positions = _sample_positions(index, candidate_timestamps)
        valid_position = positions >= 0
        safe_positions = np.maximum(positions, 0)
        source_ns = index.as_unit("ns").asi8[safe_positions]
        fresh = valid_position & ((candidate_ns - source_ns) >= 0) & ((candidate_ns - source_ns) <= stale_ns)
        if symbol in breadth_set:
            close = pd.to_numeric(frame["Close"], errors="coerce").to_numpy(dtype=float, copy=False)[safe_positions]
            ema = pd.to_numeric(frame["EMASlow"], errors="coerce").to_numpy(dtype=float, copy=False)[safe_positions]
            valid = fresh & np.isfinite(close) & np.isfinite(ema)
            breadth_observed += valid.astype(np.int32)
            breadth_bullish += (valid & (close >= ema)).astype(np.int32)
            breadth_source[valid] = np.minimum(breadth_source[valid], source_ns[valid])
        sector = sector_by_symbol.get(symbol)
        if symbol in sector_set and sector in sector_state:
            returns = pd.to_numeric(frame["MarketReturnPct"], errors="coerce").to_numpy(dtype=float, copy=False)[safe_positions]
            valid = fresh & np.isfinite(returns)
            state = sector_state[sector]
            state["observed"] += valid.astype(np.int32)
            state["bullish"] += (valid & (returns > 0)).astype(np.int32)
            state["sum"] += np.where(valid, returns, 0.0)
            state["source"][valid] = np.minimum(state["source"][valid], source_ns[valid])

    breadth: dict[str, dict[str, Any]] = {}
    for position, timestamp in enumerate(candidate_timestamps):
        observed = int(breadth_observed[position])
        if observed < config.minimum_breadth_symbols:
            breadth[timestamp.isoformat()] = {
                "available": False,
                "attemptedSymbols": len(breadth_symbols),
                "observedSymbols": observed,
                "requiredSymbols": config.minimum_breadth_symbols,
                "reason": "Insufficient point-in-time breadth coverage",
            }
            continue
        pct = int(breadth_bullish[position]) / observed * 100.0
        breadth[timestamp.isoformat()] = {
            "available": True,
            "breadthPct": _finite(pct),
            "attemptedSymbols": len(breadth_symbols),
            "observedSymbols": observed,
            "bullishSymbols": int(breadth_bullish[position]),
            "notBearish": pct >= config.minimum_breadth_pct,
            "sourceTimestamp": _source_iso(int(breadth_source[position])),
        }

    sectors: dict[str, dict[str, Any]] = {}
    for sector, state in sector_state.items():
        for position, timestamp in enumerate(candidate_timestamps):
            observed = int(state["observed"][position])
            key = f"{sector}\u0000{timestamp.isoformat()}"
            if observed < config.minimum_sector_members:
                sectors[key] = {
                    "available": False,
                    "mappingFound": True,
                    "sector": sector,
                    "observedMembers": observed,
                    "requiredMembers": config.minimum_sector_members,
                    "reason": "Insufficient point-in-time sector coverage",
                }
                continue
            sector_return = float(state["sum"][position] / observed)
            bullish_pct = int(state["bullish"][position]) / observed * 100.0
            sectors[key] = {
                "available": True,
                "mappingFound": True,
                "sector": sector,
                "returnPct": _finite(sector_return),
                "bullish": sector_return > 0 and bullish_pct >= config.minimum_sector_bullish_pct,
                "bullishPct": _finite(bullish_pct),
                "requiredBullishPct": config.minimum_sector_bullish_pct,
                "observedMembers": observed,
                "sourceTimestamp": _source_iso(int(state["source"][position])),
            }
    return breadth, sectors, {"featureFilesRead": files_read, "featureBytesRead": bytes_read}


def evaluate_precomputed_market_alignment(
    trade: Mapping[str, Any],
    *,
    stock: Mapping[str, Any],
    nifty: Mapping[str, Any],
    breadth: Mapping[str, Any],
    sector: Mapping[str, Any],
    config: MarketAlignedConfig,
) -> dict[str, Any]:
    """Assemble the canonical decision from point-in-time precomputed values."""
    signal_timestamp = _as_ist(trade.get("signalTimestamp") or trade["entryTimestamp"])
    symbol = str(trade.get("symbol") or "")
    source_timestamps = {
        "stock": stock.get("sourceTimestamp"),
        "nifty": nifty.get("sourceTimestamp"),
        "sector": sector.get("sourceTimestamp"),
        "breadth": breadth.get("sourceTimestamp"),
        "oi": None,
    }
    base = {
        "candidateTimestamp": signal_timestamp.isoformat(),
        "symbol": symbol,
        "tradeId": trade.get("tradeId"),
        "rsiArmTimestamp": trade.get("rsiArmTimestamp"),
        "rsiAtArm": _finite(trade.get("rsiArmValue"), 6),
        "previousRsi": stock.get("previousRsi"),
        "signalRsi": _finite(trade.get("rsiAtEntry"), 6),
        "timeWindowPassed": False,
        "niftyDataAvailable": False,
        "niftyTrendScore": None,
        "niftyPass": False,
        "sectorMappingFound": bool(sector.get("mappingFound")),
        "sectorName": sector.get("sector"),
        "sectorDataAvailable": False,
        "sectorMemberCount": int(sector.get("observedMembers", 0)),
        "sectorRequiredMembers": config.minimum_sector_members,
        "sectorBullishPct": _finite(sector.get("bullishPct")),
        "sectorPass": False,
        "breadthDataAvailable": False,
        "breadthSymbolCount": int(breadth.get("observedSymbols", 0)),
        "breadthRequiredSymbols": config.minimum_breadth_symbols,
        "breadthPct": _finite(breadth.get("breadthPct")),
        "breadthPass": False,
        "relativeStrengthValue": None,
        "relativeStrengthPass": False,
        "price": _finite(stock.get("price")),
        "sessionVwap": _finite(stock.get("sessionVwap")),
        "priceAboveVwap": False,
        "vwapPass": False,
        "emaFastValue": _finite(stock.get("emaFast")),
        "emaSlowValue": _finite(stock.get("emaSlow")),
        "ema9AboveEma20": False,
        "ema9AboveEma20Pass": False,
        "ema9Rising": False,
        "ema9RisingPass": False,
        "emaPass": False,
        "rvolValue": _finite(stock.get("rvol")),
        "rvolPass": False,
        "liquidityValue": _finite(stock.get("medianTradedValue"), 2),
        "liquidityPass": False,
        "roomToTargetValue": _finite(stock.get("roomToTargetPct")),
        "roomToTargetPass": False,
        "oiMode": "NOT_SET",
        "oiResult": "NOT_REACHED",
        "alignmentScore": 0.0,
        "requiredScore": config.minimum_alignment_score,
        "scorePass": False,
        "rejectionReasons": [],
        "rejectionReasonDetails": [],
        "finalStatus": "SKIPPED_DATA_UNAVAILABLE",
        "executed": False,
        "sourceTimestamps": source_timestamps,
    }
    if not stock.get("available"):
        code = str(stock.get("reasonCode") or "MISSING_STOCK_DATA")
        base["rejectionReasons"] = [code]
        base["rejectionReasonDetails"] = [{"code": code, "message": REASON_MESSAGES[code]}]
        return {
            "allowed": False,
            "score": 0.0,
            "decision": "SKIPPED_INSUFFICIENT_MARKET_ALIGNMENT_DATA",
            "reason": REASON_MESSAGES[code],
            "sourceTimestamp": stock.get("sourceTimestamp"),
            "gates": {},
            "candidateDiagnostic": base,
        }

    current_close = float(stock["price"])
    vwap = float(stock["sessionVwap"]) if stock.get("sessionVwap") is not None else math.nan
    ema_fast = float(stock["emaFast"]) if stock.get("emaFast") is not None else math.nan
    ema_slow = float(stock["emaSlow"]) if stock.get("emaSlow") is not None else math.nan
    ema_previous = float(stock["emaFastPrevious"]) if stock.get("emaFastPrevious") is not None else math.nan
    rvol = float(stock["rvol"]) if stock.get("rvol") is not None else math.nan
    traded_value = float(stock["medianTradedValue"]) if stock.get("medianTradedValue") is not None else math.nan
    range_pct = float(stock["rangeQualityPct"]) if stock.get("rangeQualityPct") is not None else math.nan
    room_pct = stock.get("roomToTargetPct")
    stock_return = stock.get("returnPct")
    nifty_return = nifty.get("_relativeStrengthReturnPct")
    sector_return = sector.get("returnPct") if sector.get("available") else None
    relative_strength = None
    if stock_return is not None and nifty_return is not None and sector_return is not None:
        relative_strength = float(stock_return) - max(float(nifty_return), float(sector_return))
    entry_start = datetime_time.fromisoformat(config.entry_start_time)
    last_entry = datetime_time.fromisoformat(config.last_entry_time)
    time_window = entry_start <= signal_timestamp.time().replace(tzinfo=None) < last_entry
    ema_above = math.isfinite(ema_fast) and math.isfinite(ema_slow) and ema_fast > ema_slow
    ema_rising = math.isfinite(ema_fast) and math.isfinite(ema_previous) and ema_fast > ema_previous
    vwap_pass = math.isfinite(vwap) and current_close > vwap
    traded_pass = math.isfinite(traded_value) and traded_value >= config.minimum_average_traded_value
    range_pass = math.isfinite(range_pct) and range_pct <= config.maximum_intrabar_range_pct
    rsi_at_entry = _finite(trade.get("rsiAtEntry"))
    gates = {
        "timeWindow": time_window,
        "niftyTrend": bool(nifty.get("available")) and float(nifty.get("score", -100)) >= config.minimum_nifty_trend_score,
        "sectorBullish": bool(sector.get("available")) and bool(sector.get("bullish")),
        "breadthNotBearish": bool(breadth.get("available")) and bool(breadth.get("notBearish")),
        "relativeStrength": relative_strength is not None and relative_strength > 0,
        "rsiRecovery": rsi_at_entry is not None and 40.0 < rsi_at_entry <= config.signal_rsi_maximum,
        "aboveSessionVwap": vwap_pass,
        "emaTrend": ema_above and ema_rising,
        "rvol": math.isfinite(rvol) and rvol >= config.minimum_rvol,
        "roomToTarget": room_pct is not None and float(room_pct) >= config.target_pct,
        "liquidity": traded_pass,
        "spreadQuality": range_pass,
    }
    weights = {
        "niftyTrend": 15.0, "sectorBullish": 10.0, "breadthNotBearish": 10.0,
        "relativeStrength": 15.0, "rsiRecovery": 10.0, "aboveSessionVwap": 10.0,
        "emaTrend": 10.0, "rvol": 10.0, "roomToTarget": 5.0,
        "liquidity": 3.0, "spreadQuality": 2.0,
    }
    score = sum(weight for gate, weight in weights.items() if gates[gate])
    failed = [gate for gate, passed in gates.items() if not passed]
    reasons: list[str] = []
    if not time_window: reasons.append("TIME_WINDOW_FAILED")
    if not nifty.get("available"): reasons.append("MISSING_NIFTY_DATA")
    elif not gates["niftyTrend"]: reasons.append("NIFTY_GATE_FAILED")
    if not sector.get("mappingFound"): reasons.append("MISSING_SECTOR_MAPPING")
    elif not sector.get("available"):
        reasons.append("MISSING_SECTOR_DATA" if int(sector.get("observedMembers", 0)) == 0 else "INSUFFICIENT_SECTOR_MEMBERS")
    elif not gates["sectorBullish"]: reasons.append("SECTOR_GATE_FAILED")
    if not breadth.get("available"):
        reasons.append("MISSING_BREADTH_DATA" if int(breadth.get("observedSymbols", 0)) == 0 else "INSUFFICIENT_BREADTH_SYMBOLS")
    elif not gates["breadthNotBearish"]: reasons.append("BREADTH_GATE_FAILED")
    if nifty.get("available") and sector.get("available") and not gates["relativeStrength"]: reasons.append("RELATIVE_STRENGTH_FAILED")
    if not gates["rsiRecovery"]: reasons.append("RSI_GATE_FAILED")
    if not gates["aboveSessionVwap"]: reasons.append("VWAP_FAILED")
    if not gates["emaTrend"]: reasons.append("EMA_FAILED")
    if not gates["rvol"]: reasons.append("RVOL_FAILED")
    if not gates["liquidity"] or not gates["spreadQuality"]: reasons.append("LIQUIDITY_FAILED")
    if not gates["roomToTarget"]: reasons.append("ROOM_TO_TARGET_FAILED")
    if score < config.minimum_alignment_score: reasons.append("ALIGNMENT_SCORE_FAILED")
    allowed = not reasons
    unavailable = any(code in DATA_UNAVAILABLE_REASON_CODES for code in reasons)
    decision = "MARKET_ALIGNMENT_ACCEPTED" if allowed else (
        "SKIPPED_INSUFFICIENT_MARKET_ALIGNMENT_DATA" if unavailable else "SKIPPED_MARKET_ALIGNMENT"
    )
    source_values = [value for value in source_timestamps.values() if value]
    diagnostic = {
        **base,
        "timeWindowPassed": time_window,
        "niftyDataAvailable": bool(nifty.get("available")),
        "niftyTrendScore": _finite(nifty.get("score")),
        "niftyPass": gates["niftyTrend"],
        "sectorDataAvailable": bool(sector.get("available")),
        "sectorPass": gates["sectorBullish"],
        "breadthDataAvailable": bool(breadth.get("available")),
        "breadthPass": gates["breadthNotBearish"],
        "relativeStrengthValue": _finite(relative_strength),
        "relativeStrengthPass": gates["relativeStrength"],
        "priceAboveVwap": vwap_pass,
        "vwapPass": gates["aboveSessionVwap"],
        "ema9AboveEma20": ema_above,
        "ema9AboveEma20Pass": ema_above,
        "ema9Rising": ema_rising,
        "ema9RisingPass": ema_rising,
        "emaPass": gates["emaTrend"],
        "rvolPass": gates["rvol"],
        "liquidityPass": gates["liquidity"] and gates["spreadQuality"],
        "roomToTargetPass": gates["roomToTarget"],
        "alignmentScore": _finite(score),
        "scorePass": score >= config.minimum_alignment_score,
        "rejectionReasons": reasons if reasons else ["ACCEPTED"],
        "rejectionReasonDetails": [
            {"code": code, "message": REASON_MESSAGES[code]}
            for code in (reasons if reasons else ["ACCEPTED"])
        ],
        "finalStatus": "ACCEPTED" if allowed else "SKIPPED_DATA_UNAVAILABLE" if unavailable else "REJECTED_GATE",
    }
    return {
        "allowed": allowed,
        "score": _finite(score),
        "decision": decision,
        "reason": REASON_MESSAGES["ACCEPTED"] if allowed else "; ".join(REASON_MESSAGES[code] for code in reasons),
        "sourceTimestamp": min(source_values) if source_values else None,
        "gates": gates,
        "niftyTrend": {key: value for key, value in nifty.items() if not key.startswith("_")},
        "sectorTrend": dict(sector),
        "marketBreadth": dict(breadth),
        "stockReturnPct": _finite(stock_return),
        "niftyReturnPct": _finite(nifty_return),
        "sectorReturnPct": _finite(sector_return),
        "rvol": _finite(rvol),
        "roomToTargetPct": _finite(room_pct),
        "medianTradedValue": _finite(traded_value, 2),
        "historicalRangeQualityPct": _finite(range_pct),
        "historicalSpreadNote": "Intraday OHLC has no bid/ask history; candle range is a conservative data-quality proxy.",
        "failedGates": failed,
        "candidateDiagnostic": diagnostic,
    }


def candidate_timestamp_index(observations: Iterable[Mapping[str, Any]]) -> pd.DatetimeIndex:
    timestamps = {
        pd.Timestamp(_as_ist(trade.get("signalTimestamp") or trade["entryTimestamp"]))
        for result in observations
        for trade in result.get("trades", [])
    }
    return pd.DatetimeIndex(sorted(timestamps))


def candidate_sector_key(sector: str, timestamp: Any) -> str:
    return f"{sector}\u0000{pd.Timestamp(_as_ist(timestamp)).isoformat()}"
