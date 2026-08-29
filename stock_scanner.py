from __future__ import annotations

import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, time as datetime_time, timedelta
from pathlib import Path
from typing import Any, Mapping, Protocol, Sequence

import pandas as pd

from daily_scalping_watchlist import (
    FEATURE_CODE_VERSION as RANKING_FEATURE_VERSION,
    WATCHLIST_RULE_VERSION,
    DailyWatchlistConfig,
    build_watchlist_history,
    calculate_watchlist_features,
    file_stat_fingerprint,
    rescan_times_for_session,
    score_rescan_rows,
    stable_fingerprint,
)
from main import IST

SCANNER_VERSION = "stock-scanner-1.1.0"
SCANNER_FEATURE_CACHE_VERSION = "stock-scanner-features-1.0.0"
SCANNER_TIMEFRAME = "5m"
SCANNER_CONFIG = DailyWatchlistConfig(
    mode="ROLLING",
    selection_time="09:30",
    rescan_interval_minutes=15,
    rescan_end_time="14:30",
    selected_symbols=5,
    primary_symbols=2,
    minimum_residence_minutes=30,
    required_promotion_advantage=10.0,
    maximum_replacements_per_rescan=2,
)


class CachedCandleStore(Protocol):
    cache_directory: Path

    def cached_candles(
        self,
        symbol: str,
        timeframe: str,
        duration_years: int,
        analysis_start: datetime,
        now_ist: datetime,
        *,
        benchmark: bool = False,
        warmup_bars: int = 0,
    ) -> pd.DataFrame: ...

    def cached_candle_path(
        self,
        symbol: str,
        timeframe: str,
        duration_years: int,
        *,
        benchmark: bool = False,
    ) -> Path: ...


def _as_ist(value: datetime) -> datetime:
    return value.replace(tzinfo=IST) if value.tzinfo is None else value.astimezone(IST)


def _iso(value: object) -> str:
    return pd.Timestamp(value).isoformat()


def _completed_row(frame: pd.DataFrame | None, timestamp: pd.Timestamp) -> pd.Series | None:
    if frame is None or frame.empty:
        return None
    position = int(frame.index.searchsorted(timestamp, side="right")) - 1
    if position < 0:
        return None
    source = pd.Timestamp(frame.index[position])
    if source.date() != timestamp.date() or source > timestamp:
        return None
    return frame.iloc[position]


def _company_name(symbol: str, names: Mapping[str, str]) -> str:
    value = str(names.get(symbol, "")).strip()
    return value or symbol


def _entry_with_name(entry: Mapping[str, Any], names: Mapping[str, str]) -> dict[str, Any]:
    symbol = str(entry.get("symbol", ""))
    return {**entry, "companyName": _company_name(symbol, names)}


def _empty_snapshot(
    *,
    now_ist: datetime,
    symbols_requested: int,
    symbols_loaded: int,
    errors: Sequence[Mapping[str, str]],
    minimum_price: float,
    maximum_price: float,
    status: str,
) -> dict[str, Any]:
    return {
        "metadata": {
            "scannerVersion": SCANNER_VERSION,
            "rankingFeatureVersion": RANKING_FEATURE_VERSION,
            "watchlistRuleVersion": WATCHLIST_RULE_VERSION,
            "status": status,
            "generatedAt": now_ist.isoformat(),
            "timeframe": SCANNER_TIMEFRAME,
            "rescanIntervalMinutes": 15,
            "rescanWindow": ["09:30", "14:30"],
            "symbolsRequested": symbols_requested,
            "symbolsLoaded": symbols_loaded,
            "symbolsFailed": len(errors),
            "globalPriceRange": {
                "minimumPrice": minimum_price,
                "maximumPrice": maximum_price,
            },
            "completedCandlesOnly": True,
            "paperOnly": True,
            "liveOrdersEnabled": False,
            "signalUniversePolicy": "FROZEN_AT_09_30",
        },
        "watchlist": {
            "topFive": [],
            "primary": [],
            "reserve": [],
            "promoted": [],
            "removed": [],
            "history": [],
        },
        "opportunities": [],
        "eligibility": {"eligible": 0, "rejected": 0, "rejectionCounts": {}},
        "errors": list(errors),
        "warnings": [
            "Research and paper-signal only. The scanner has no broker-order path.",
            "RSI Recovery signals continue to use their separately frozen signal universe.",
        ],
    }


def build_stock_scanner_snapshot(
    feature_frames: Mapping[str, pd.DataFrame],
    *,
    nifty_frame: pd.DataFrame | None,
    sector_by_symbol: Mapping[str, str],
    company_names: Mapping[str, str],
    now_ist: datetime,
    minimum_price: float,
    maximum_price: float,
    symbols_requested: int | None = None,
    errors: Sequence[Mapping[str, str]] = (),
) -> dict[str, Any]:
    """Build one causal scanner snapshot from already-completed feature frames."""
    now = _as_ist(now_ist)
    requested = symbols_requested if symbols_requested is not None else len(feature_frames)
    if not feature_frames:
        return _empty_snapshot(
            now_ist=now,
            symbols_requested=requested,
            symbols_loaded=0,
            errors=errors,
            minimum_price=minimum_price,
            maximum_price=maximum_price,
            status="CACHED_CANDLE_DATA_UNAVAILABLE",
        )

    latest_source = max(
        pd.Timestamp(frame.index.max())
        for frame in feature_frames.values()
        if not frame.empty
    )
    session_day = latest_source.date()
    session_frames = {
        symbol: frame[pd.DatetimeIndex(frame.index).date == session_day].copy()
        for symbol, frame in feature_frames.items()
    }
    session_frames = {symbol: frame for symbol, frame in session_frames.items() if not frame.empty}
    session_nifty = None
    if nifty_frame is not None and not nifty_frame.empty:
        session_nifty = nifty_frame[pd.DatetimeIndex(nifty_frame.index).date == session_day].copy()

    base_config = replace(
        SCANNER_CONFIG,
        minimum_price=float(minimum_price),
        maximum_price=float(maximum_price),
    ).validate()
    available_through = latest_source
    if session_day == now.date():
        available_through = min(available_through, pd.Timestamp(now))
    scheduled = [
        timestamp
        for timestamp in rescan_times_for_session(session_day, base_config)
        if timestamp <= available_through
    ]
    if not scheduled:
        return _empty_snapshot(
            now_ist=now,
            symbols_requested=requested,
            symbols_loaded=len(session_frames),
            errors=errors,
            minimum_price=minimum_price,
            maximum_price=maximum_price,
            status="WAITING_FOR_FIRST_RESCAN",
        )

    final_rescan = scheduled[-1]
    effective_config = replace(base_config, rescan_end_time=final_rescan.strftime("%H:%M")).validate()
    history = build_watchlist_history(
        session_frames,
        context_frames=session_frames,
        nifty_frame=session_nifty,
        sector_by_symbol=sector_by_symbol,
        config=effective_config,
        minimum_average_traded_value=effective_config.minimum_average_traded_value,
        maximum_candle_range_atr=effective_config.maximum_candle_range_atr,
        maximum_spread_pct=effective_config.live_maximum_spread_pct,
        selection_method="SCORE",
    )
    if not history:
        return _empty_snapshot(
            now_ist=now,
            symbols_requested=requested,
            symbols_loaded=len(session_frames),
            errors=errors,
            minimum_price=minimum_price,
            maximum_price=maximum_price,
            status="NO_SCANNABLE_SESSION",
        )

    stock_rows = {
        symbol: row
        for symbol, frame in session_frames.items()
        if (row := _completed_row(frame, final_rescan)) is not None
    }
    nifty_row = _completed_row(session_nifty, final_rescan)
    ranked = score_rescan_rows(
        stock_rows,
        nifty_row=nifty_row,
        sector_by_symbol=sector_by_symbol,
        minimum_average_traded_value=effective_config.minimum_average_traded_value,
        minimum_price=effective_config.minimum_price,
        maximum_price=effective_config.maximum_price,
        minimum_median_daily_traded_value=effective_config.minimum_median_daily_traded_value,
        minimum_opening_traded_value=effective_config.minimum_opening_traded_value,
        minimum_daily_atr_pct=effective_config.minimum_daily_atr_pct,
        maximum_daily_atr_pct=effective_config.maximum_daily_atr_pct,
        maximum_opening_gap_pct=effective_config.maximum_opening_gap_pct,
        maximum_candle_range_atr=effective_config.maximum_candle_range_atr,
        maximum_spread_pct=effective_config.live_maximum_spread_pct,
    )
    eligible = [row for row in ranked if bool(row.get("eligible"))]
    opportunities = [
        _entry_with_name({**row, "rank": rank}, company_names)
        for rank, row in enumerate(eligible[:20], start=1)
    ]

    final_watchlist = history[-1]
    top_five = [_entry_with_name(entry, company_names) for entry in final_watchlist.get("entries", [])]
    history_payload = [
        {
            **snapshot,
            "entries": [_entry_with_name(entry, company_names) for entry in snapshot.get("entries", [])],
            "promoted": [_entry_with_name(entry, company_names) for entry in snapshot.get("promoted", [])],
            "removed": [_entry_with_name(entry, company_names) for entry in snapshot.get("removed", [])],
        }
        for snapshot in history
    ]
    rejection_counts: dict[str, int] = {}
    for row in ranked:
        if bool(row.get("eligible")):
            continue
        reason = str(row.get("primaryEligibilityReason") or "INELIGIBLE")
        rejection_counts[reason] = rejection_counts.get(reason, 0) + 1

    current_session = session_day == now.date()
    next_rescan = None
    if current_session:
        remaining = [
            timestamp
            for timestamp in rescan_times_for_session(session_day, base_config)
            if timestamp > final_rescan
        ]
        if remaining:
            next_rescan = remaining[0].isoformat()
    freshness = max(0.0, (pd.Timestamp(now) - latest_source).total_seconds() / 60.0)
    warnings = [
        "Research and paper-signal only. The scanner has no broker-order path.",
        "RSI Recovery signals continue to use their separately frozen 09:30 signal universe.",
        "Historical bid/ask spread is advisory when unavailable and is never fabricated.",
    ]
    if nifty_row is None:
        warnings.append("NIFTY context was unavailable at the selected rescan; its score contribution is neutral.")

    return {
        "metadata": {
            "scannerVersion": SCANNER_VERSION,
            "rankingFeatureVersion": RANKING_FEATURE_VERSION,
            "watchlistRuleVersion": WATCHLIST_RULE_VERSION,
            "status": "CURRENT_SESSION" if current_session else "LAST_COMPLETED_SESSION",
            "generatedAt": now.isoformat(),
            "sessionDate": session_day.isoformat(),
            "timeframe": SCANNER_TIMEFRAME,
            "rescanIntervalMinutes": effective_config.rescan_interval_minutes,
            "rescanWindow": [effective_config.selection_time, SCANNER_CONFIG.rescan_end_time],
            "lastRescanTimestamp": final_rescan.isoformat(),
            "nextRescanTimestamp": next_rescan,
            "latestSourceTimestamp": latest_source.isoformat(),
            "dataFreshnessMinutes": round(freshness, 2),
            "symbolsRequested": requested,
            "symbolsLoaded": len(session_frames),
            "symbolsScored": len(ranked),
            "symbolsFailed": len(errors),
            "globalPriceRange": {
                "minimumPrice": minimum_price,
                "maximumPrice": maximum_price,
            },
            "completedCandlesOnly": True,
            "paperOnly": True,
            "liveOrdersEnabled": False,
            "signalUniversePolicy": "FROZEN_AT_09_30",
        },
        "watchlist": {
            "topFive": top_five,
            "primary": [entry for entry in top_five if entry.get("tier") == "PRIMARY"],
            "reserve": [entry for entry in top_five if entry.get("tier") == "RESERVE"],
            "promoted": [_entry_with_name(entry, company_names) for entry in final_watchlist.get("promoted", [])],
            "removed": [_entry_with_name(entry, company_names) for entry in final_watchlist.get("removed", [])],
            "history": history_payload,
        },
        "opportunities": opportunities,
        "eligibility": {
            "eligible": len(eligible),
            "rejected": len(ranked) - len(eligible),
            "rejectionCounts": dict(sorted(rejection_counts.items(), key=lambda pair: (-pair[1], pair[0]))),
        },
        "errors": list(errors),
        "warnings": warnings,
    }


class StockScannerService:
    """Short-lived cache around local-only scanner calculation."""

    def __init__(self, store: CachedCandleStore, cache_seconds: int = 60) -> None:
        self.store = store
        self.cache_seconds = max(1, int(cache_seconds))
        self._lock = threading.Lock()
        self._cache_key: tuple[Any, ...] | None = None
        self._cache_expires = 0.0
        self._cache_value: dict[str, Any] | None = None

    def _feature_cache_path(
        self,
        symbol: str,
        *,
        now: datetime,
        benchmark: bool = False,
    ) -> Path | None:
        path_getter = getattr(self.store, "cached_candle_path", None)
        cache_directory = getattr(self.store, "cache_directory", None)
        if not callable(path_getter) or cache_directory is None:
            return None
        source = path_getter(
            symbol,
            SCANNER_TIMEFRAME,
            1,
            benchmark=benchmark,
        )
        if not source.is_file():
            return None
        feature_key = stable_fingerprint({
            "version": SCANNER_FEATURE_CACHE_VERSION,
            "rankingFeatureVersion": RANKING_FEATURE_VERSION,
            "symbol": symbol,
            "source": file_stat_fingerprint(source),
            "asOfSession": now.date().isoformat(),
            "timeframe": SCANNER_TIMEFRAME,
            "featureParameters": {
                "rsiLength": SCANNER_CONFIG.rsi_length,
                "emaFast": SCANNER_CONFIG.ema_fast,
                "emaSlow": SCANNER_CONFIG.ema_slow,
                "atrLength": SCANNER_CONFIG.atr_length,
                "rvolPeriod": SCANNER_CONFIG.rvol_period,
                "historicalSessions": SCANNER_CONFIG.historical_sessions,
                "rollingWindowMinutes": SCANNER_CONFIG.rolling_window_minutes,
            },
        })
        safe_symbol = "".join(
            character for character in symbol if character.isalnum() or character in "-&"
        )
        return Path(cache_directory) / "stock-scanner-features-v1" / safe_symbol / f"{feature_key}.parquet"

    def _load_features(
        self,
        symbol: str,
        *,
        analysis_start: datetime,
        now: datetime,
        benchmark: bool = False,
    ) -> tuple[pd.DataFrame | None, bool]:
        feature_path = self._feature_cache_path(symbol, now=now, benchmark=benchmark)
        if feature_path is not None and feature_path.is_file():
            frame = pd.read_parquet(feature_path)
            frame.index = pd.DatetimeIndex(frame.index)
            frame.index = (
                frame.index.tz_localize(IST)
                if frame.index.tz is None
                else frame.index.tz_convert(IST)
            )
            return frame, True

        candles = self.store.cached_candles(
            symbol,
            SCANNER_TIMEFRAME,
            1,
            analysis_start,
            now,
            benchmark=benchmark,
        )
        if candles.empty:
            return None, False
        features = calculate_watchlist_features(candles, SCANNER_CONFIG)
        latest_day = pd.Timestamp(features.index.max()).date()
        latest_session = features[pd.DatetimeIndex(features.index).date == latest_day].copy()
        if feature_path is not None:
            feature_path.parent.mkdir(parents=True, exist_ok=True)
            temporary = feature_path.with_suffix(f".{os.getpid()}.{threading.get_ident()}.tmp.parquet")
            latest_session.to_parquet(temporary, index=True)
            os.replace(temporary, feature_path)
        return latest_session, False

    def snapshot(
        self,
        symbols: Sequence[str],
        *,
        minimum_price: float,
        maximum_price: float,
        sector_by_symbol: Mapping[str, str],
        company_names: Mapping[str, str],
        now_ist: datetime | None = None,
        force: bool = False,
    ) -> dict[str, Any]:
        now = _as_ist(now_ist or datetime.now(IST))
        normalized_symbols = tuple(sorted({str(symbol).strip().upper() for symbol in symbols if str(symbol).strip()}))
        rescan_bucket = now.replace(minute=(now.minute // 15) * 15, second=0, microsecond=0)
        cache_key = (normalized_symbols, float(minimum_price), float(maximum_price), rescan_bucket.isoformat())
        with self._lock:
            monotonic_now = time.monotonic()
            if (
                not force
                and self._cache_key == cache_key
                and self._cache_value is not None
                and monotonic_now < self._cache_expires
            ):
                cached = dict(self._cache_value)
                cached["metadata"] = {**cached["metadata"], "resultSource": "SCANNER_CACHE"}
                return cached

            calculation_started = time.perf_counter()
            analysis_start = now - timedelta(days=120)
            workers = min(max((os.cpu_count() or 2) - 1, 1), 8)

            def load(symbol: str) -> tuple[str, pd.DataFrame | None, str | None, bool]:
                try:
                    features, cache_hit = self._load_features(
                        symbol, analysis_start=analysis_start, now=now
                    )
                    if features is None or features.empty:
                        return symbol, None, "CACHED_CANDLE_DATA_UNAVAILABLE", cache_hit
                    return symbol, features, None, cache_hit
                except (OSError, RuntimeError, ValueError) as error:
                    return symbol, None, f"{type(error).__name__}: {error}", False

            frames: dict[str, pd.DataFrame] = {}
            errors: list[dict[str, str]] = []
            feature_cache_hits = 0
            feature_cache_misses = 0
            with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="stock-scanner") as executor:
                for symbol, frame, error, cache_hit in executor.map(load, normalized_symbols):
                    feature_cache_hits += int(cache_hit)
                    feature_cache_misses += int(not cache_hit)
                    if frame is not None:
                        frames[symbol] = frame
                    else:
                        errors.append({"symbol": symbol, "reason": error or "CACHED_CANDLE_DATA_UNAVAILABLE"})

            nifty_features, nifty_cache_hit = self._load_features(
                "NIFTY50", analysis_start=analysis_start, now=now, benchmark=True
            )
            feature_cache_hits += int(nifty_cache_hit)
            feature_cache_misses += int(not nifty_cache_hit)
            result = build_stock_scanner_snapshot(
                frames,
                nifty_frame=nifty_features,
                sector_by_symbol=sector_by_symbol,
                company_names=company_names,
                now_ist=now,
                minimum_price=minimum_price,
                maximum_price=maximum_price,
                symbols_requested=len(normalized_symbols),
                errors=errors,
            )
            result["metadata"] = {
                **result["metadata"],
                "workerCount": workers,
                "featureCacheHits": feature_cache_hits,
                "featureCacheMisses": feature_cache_misses,
                "runtimeSeconds": round(time.perf_counter() - calculation_started, 3),
                "resultSource": "FRESH_CALCULATION",
            }
            self._cache_key = cache_key
            self._cache_expires = time.monotonic() + self.cache_seconds
            self._cache_value = result
            return result
