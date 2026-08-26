from __future__ import annotations

import hashlib
import json
import math
import os
import tempfile
from dataclasses import dataclass
from datetime import datetime, time
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import numpy as np
import pandas as pd

from main import IST
from recovery_backtest import (
    STRATEGY_VERSION,
    RecoveryConfig,
    calculate_ema,
    calculate_recovery_indicators,
    calculate_session_vwap,
    calculate_wilder_rma,
    calculate_wilder_rsi,
)

FEATURE_SCHEMA_VERSION = "recovery-signal-features-1.0.0"
FEATURE_DEFINITIONS_VERSION = "2026-08-25.1"

IDENTITY_COLUMNS = (
    "trade_id",
    "run_id",
    "strategy_version",
    "feature_schema_version",
    "feature_definitions_version",
    "symbol",
    "timeframe",
    "signal_timestamp",
    "entry_timestamp",
    "snapshot_timestamp",
    "execution_model",
)

RSI_FEATURES = (
    "feature_rsi_at_entry",
    "feature_rsi_previous",
    "feature_rsi_arm_value",
    "feature_rsi_min_since_arm",
    "feature_rsi_max_since_arm",
    "feature_bars_arm_to_recovery",
    "feature_minutes_arm_to_recovery",
    "feature_rsi_recovery_strength",
    "feature_rsi_change_1bar",
    "feature_rsi_change_2bar",
    "feature_rsi_change_3bar",
    "feature_rsi_below_30_since_arm",
    "feature_rsi_min_distance_below_30",
    "feature_bars_below_30_since_arm",
)

EMA_FEATURES = (
    "feature_ema_fast",
    "feature_ema_slow",
    "feature_ema_spread_pct",
    "feature_ema_fast_slope_1",
    "feature_ema_fast_slope_3",
    "feature_ema_fast_slope_5",
    "feature_ema_slow_slope_1",
    "feature_ema_slow_slope_3",
    "feature_ema_slow_slope_5",
    "feature_close_above_ema_fast_pct",
    "feature_close_above_ema_slow_pct",
    "feature_ema_fast_above_slow",
)

VWAP_FEATURES = (
    "feature_vwap_at_entry",
    "feature_close_vs_vwap_pct",
    "feature_vwap_slope_1",
    "feature_vwap_slope_3",
    "feature_vwap_slope_5",
    "feature_close_above_vwap",
)

VOLUME_FEATURES = (
    "feature_volume",
    "feature_volume_ema",
    "feature_volume_ratio",
    "feature_volume_vs_previous_bar",
    "feature_volume_vs_5bar_average",
    "feature_volume_vs_20bar_average",
    "feature_relative_volume_5",
    "feature_relative_volume_20",
)

CANDLE_FEATURES = (
    "feature_open",
    "feature_high",
    "feature_low",
    "feature_close",
    "feature_candle_return_pct",
    "feature_body_pct",
    "feature_range_pct",
    "feature_upper_wick_pct",
    "feature_lower_wick_pct",
    "feature_bullish_candle",
    "feature_close_location_value",
)

VOLATILITY_FEATURES = (
    "feature_atr14",
    "feature_atr_pct",
    "feature_true_range",
    "feature_rolling_realized_volatility_5",
    "feature_rolling_realized_volatility_20",
)

MOMENTUM_FEATURES = (
    "feature_return_1bar",
    "feature_return_2bar",
    "feature_return_3bar",
    "feature_return_5bar",
    "feature_return_10bar",
    "feature_return_15m",
    "feature_return_30m",
    "feature_return_60m",
)

LOCATION_FEATURES = (
    "feature_distance_from_5bar_low_pct",
    "feature_distance_from_10bar_low_pct",
    "feature_distance_from_20bar_low_pct",
    "feature_distance_from_5bar_high_pct",
    "feature_distance_from_10bar_high_pct",
    "feature_distance_from_20bar_high_pct",
    "feature_position_in_20bar_range",
)

TREND_FEATURES = (
    "feature_higher_high_3bar",
    "feature_higher_low_3bar",
    "feature_higher_high_5bar",
    "feature_higher_low_5bar",
    "feature_close_above_previous_high",
    "feature_close_above_previous_3bar_high",
)

TIME_FEATURES = (
    "feature_entry_hour",
    "feature_entry_minute",
    "feature_minutes_since_market_open",
    "feature_minutes_until_market_close",
    "feature_time_of_day_bucket",
    "feature_day_of_week",
    "feature_month",
    "feature_trading_day_of_month",
)

GAP_FEATURES = (
    "feature_opening_gap_pct",
    "feature_gap_up",
    "feature_gap_down",
)

NIFTY_FEATURES = (
    "feature_nifty_return_5m",
    "feature_nifty_return_15m",
    "feature_nifty_return_30m",
    "feature_nifty_above_ema20",
    "feature_nifty_ema9_vs_ema20_pct",
    "feature_nifty_rsi14",
    "feature_nifty_vwap_distance_pct",
)

CONFIRMATION_FEATURES = (
    "feature_confirmation_score",
    "feature_ema_confirmation",
    "feature_vwap_confirmation",
    "feature_volume_confirmation",
    "feature_confirmation_combination",
)

ENTRY_FEATURE_COLUMNS = (
    *RSI_FEATURES,
    *EMA_FEATURES,
    *VWAP_FEATURES,
    *VOLUME_FEATURES,
    *CANDLE_FEATURES,
    *VOLATILITY_FEATURES,
    *MOMENTUM_FEATURES,
    *LOCATION_FEATURES,
    *TREND_FEATURES,
    *TIME_FEATURES,
    *GAP_FEATURES,
    *NIFTY_FEATURES,
    *CONFIRMATION_FEATURES,
)

OUTCOME_COLUMNS = (
    "outcome_target_hit",
    "outcome_target_hit_timestamp",
    "outcome_duration_minutes",
    "outcome_duration_hours",
    "outcome_duration_days",
    "outcome_speed_bucket",
    "outcome_binary_quality_label",
    "outcome_mae_pct",
    "outcome_mfe_pct",
    "outcome_bars_held",
    "outcome_sessions_held",
    "outcome_open_at_dataset_end",
    "outcome_open_pnl_pct",
)

BOOLEAN_FEATURES = (
    "feature_rsi_below_30_since_arm",
    "feature_ema_fast_above_slow",
    "feature_close_above_vwap",
    "feature_bullish_candle",
    "feature_higher_high_3bar",
    "feature_higher_low_3bar",
    "feature_higher_high_5bar",
    "feature_higher_low_5bar",
    "feature_close_above_previous_high",
    "feature_close_above_previous_3bar_high",
    "feature_gap_up",
    "feature_gap_down",
    "feature_nifty_above_ema20",
    "feature_ema_confirmation",
    "feature_vwap_confirmation",
    "feature_volume_confirmation",
)

CATEGORICAL_FEATURES = (
    *BOOLEAN_FEATURES,
    "feature_time_of_day_bucket",
    "feature_day_of_week",
    "feature_confirmation_combination",
)

NUMERIC_FEATURES = tuple(
    column
    for column in ENTRY_FEATURE_COLUMNS
    if column not in set(CATEGORICAL_FEATURES)
)

BIN_FEATURES = (
    "feature_rsi_at_entry",
    "feature_rsi_min_since_arm",
    "feature_bars_arm_to_recovery",
    "feature_ema_spread_pct",
    "feature_close_vs_vwap_pct",
    "feature_volume_ratio",
    "feature_atr_pct",
    "feature_return_15m",
    "feature_return_30m",
    "feature_distance_from_20bar_low_pct",
)

TIMEFRAME_MINUTES: dict[str, int | None] = {
    "5m": 5,
    "15m": 15,
    "30m": 30,
    "1h": 60,
    "2h": 120,
    "4h": 240,
    "1d": None,
}

REPORT_FILENAMES = {
    "recovery_signal_features.parquet",
    "recovery_signal_features.csv",
    "recovery_outcome_summary.csv",
    "recovery_feature_descriptive.csv",
    "recovery_categorical_analysis.csv",
    "recovery_feature_separation.csv",
    "recovery_feature_bins.csv",
    "recovery_two_dimensional_analysis.csv",
    "recovery_confirmation_analysis.csv",
    "recovery_time_of_day_analysis.csv",
    "recovery_symbol_feature_summary.csv",
    "recovery_trapped_signals.csv",
    "recovery_worst_mae.csv",
    "recovery_oldest_open.csv",
    "recovery_worst_open_pnl.csv",
    "recovery_slowest_targets.csv",
    "recovery_feature_analysis.json",
    "recovery_candidate_filter_diagnostics.csv",
}


FEATURE_DEFINITIONS: dict[str, str] = {
    "feature_ema_fast_slope_N": "(EMA[t] - EMA[t-N]) / close[t] * 100; causal total change over N bars.",
    "feature_vwap_slope_N": "(session VWAP[t] - session VWAP[t-N]) / close[t] * 100; null across session boundaries.",
    "feature_volume_vs_Nbar_average": "(current volume / mean of the preceding N completed bars - 1) * 100.",
    "feature_relative_volume_N": "current volume / mean of the preceding N completed bars.",
    "feature_candle_return_pct": "(close - open) / open * 100.",
    "feature_body_pct": "absolute(close - open) / open * 100.",
    "feature_range_pct": "(high - low) / open * 100.",
    "feature_upper_wick_pct": "(high - max(open, close)) / open * 100.",
    "feature_lower_wick_pct": "(min(open, close) - low) / open * 100.",
    "feature_close_location_value": "(close - low) / (high - low); null for zero-range candles.",
    "feature_atr14": "Wilder RMA(14) of causal true range.",
    "feature_rolling_realized_volatility_N": "population standard deviation of causal close-to-close percentage returns over N bars.",
    "feature_return_Nbar": "(close[t] / close[t-N] - 1) * 100.",
    "feature_distance_from_Nbar_low_pct": "(close / rolling low including current bar - 1) * 100.",
    "feature_distance_from_Nbar_high_pct": "(close / rolling high including current bar - 1) * 100.",
    "feature_higher_high_Nbar": "true when consecutive highs are strictly increasing across N bars, including the signal bar.",
    "feature_higher_low_Nbar": "true when consecutive lows are strictly increasing across N bars, including the signal bar.",
    "feature_opening_gap_pct": "(current session first open / previous session final close - 1) * 100.",
}


def _safe_ratio(numerator: pd.Series, denominator: pd.Series) -> pd.Series:
    numeric_denominator = pd.to_numeric(denominator, errors="coerce").replace(0, np.nan)
    return pd.to_numeric(numerator, errors="coerce") / numeric_denominator


def _percentage_change(current: pd.Series, reference: pd.Series) -> pd.Series:
    return (_safe_ratio(current, reference) - 1.0) * 100.0


def _normalized_change(current: pd.Series, previous: pd.Series, close: pd.Series) -> pd.Series:
    return _safe_ratio(current - previous, close) * 100.0


def _strictly_rising(values: pd.Series, bars: int) -> pd.Series:
    result = pd.Series(True, index=values.index, dtype="boolean")
    valid = pd.Series(True, index=values.index, dtype=bool)
    for offset in range(bars - 1):
        current = values.shift(offset)
        previous = values.shift(offset + 1)
        result &= current > previous
        valid &= current.notna() & previous.notna()
    return result.where(valid, pd.NA)


def _time_bucket(stamp: pd.Timestamp) -> str | None:
    clock = stamp.timetz().replace(tzinfo=None)
    if time(9, 15) <= clock < time(10, 0):
        return "OPENING"
    if time(10, 0) <= clock < time(12, 0):
        return "MORNING"
    if time(12, 0) <= clock < time(13, 30):
        return "MIDDAY"
    if time(13, 30) <= clock < time(14, 45):
        return "AFTERNOON"
    if time(14, 45) <= clock <= time(15, 30):
        return "LATE"
    return None


def classify_outcome(duration_minutes: float | None, target_hit: bool) -> str:
    if not target_hit:
        return "TRAPPED"
    if duration_minutes is None or not math.isfinite(float(duration_minutes)):
        raise ValueError("Completed observations require a finite duration")
    if duration_minutes <= 30:
        return "FAST_30M"
    if duration_minutes <= 120:
        return "FAST_2H"
    if duration_minutes <= 1_440:
        return "SAME_DAY"
    return "SLOW"


def classify_binary_quality(duration_minutes: float | None, target_hit: bool) -> str:
    speed = classify_outcome(duration_minutes, target_hit)
    if speed in {"FAST_30M", "FAST_2H"}:
        return "GOOD"
    if speed in {"SLOW", "TRAPPED"}:
        return "BAD"
    return "NEUTRAL"


def confirmation_combination(trade: Mapping[str, Any]) -> str:
    ema = bool(trade.get("emaConfirmation"))
    vwap = bool(trade.get("vwapConfirmation"))
    volume = bool(trade.get("volumeConfirmation"))
    if ema and vwap and volume:
        return "EMA_VWAP_VOLUME"
    if ema and vwap:
        return "EMA_VWAP"
    if ema and volume:
        return "EMA_VOLUME"
    if vwap and volume:
        return "VWAP_VOLUME"
    return "OTHER"


def build_outcome_labels(trade: Mapping[str, Any]) -> dict[str, Any]:
    target_hit = trade.get("status") == "TARGET_HIT"
    duration_minutes = float(trade["durationMinutes"])
    return {
        "outcome_target_hit": target_hit,
        "outcome_target_hit_timestamp": trade.get("targetHitTimestamp"),
        "outcome_duration_minutes": duration_minutes,
        "outcome_duration_hours": float(trade["durationHours"]),
        "outcome_duration_days": float(trade["durationDays"]),
        "outcome_speed_bucket": classify_outcome(duration_minutes, target_hit),
        "outcome_binary_quality_label": classify_binary_quality(duration_minutes, target_hit),
        "outcome_mae_pct": float(trade["maxAdversePct"]),
        "outcome_mfe_pct": float(trade["maxFavorablePct"]),
        "outcome_bars_held": int(trade["barsHeld"]),
        "outcome_sessions_held": int(trade["tradingSessionsHeld"]),
        "outcome_open_at_dataset_end": not target_hit,
        "outcome_open_pnl_pct": float(trade["currentPnlPct"]) if not target_hit else None,
    }


def _trading_day_numbers(index: pd.DatetimeIndex) -> pd.Series:
    sessions = pd.Series(index.date, index=index)
    unique_sessions = pd.DataFrame({"session": pd.unique(sessions)})
    session_stamps = pd.to_datetime(unique_sessions["session"])
    unique_sessions["year"] = session_stamps.dt.year
    unique_sessions["month"] = session_stamps.dt.month
    unique_sessions["number"] = unique_sessions.groupby(["year", "month"], sort=False).cumcount() + 1
    mapping = dict(zip(unique_sessions["session"], unique_sessions["number"], strict=False))
    return sessions.map(mapping).astype("Int64")


def _opening_gap(index: pd.DatetimeIndex, open_values: pd.Series, close_values: pd.Series) -> pd.Series:
    sessions = pd.Series(index.date, index=index)
    session_open = open_values.groupby(sessions, sort=False).first()
    session_close = close_values.groupby(sessions, sort=False).last()
    gaps = (session_open / session_close.shift(1).replace(0, np.nan) - 1.0) * 100.0
    return sessions.map(gaps)


def _intraday_return(close: pd.Series, timeframe: str, minutes: int) -> pd.Series:
    timeframe_minutes = TIMEFRAME_MINUTES.get(timeframe)
    if timeframe_minutes is None or minutes < timeframe_minutes or minutes % timeframe_minutes:
        return pd.Series(np.nan, index=close.index, dtype=float)
    return close.pct_change(minutes // timeframe_minutes, fill_method=None) * 100.0


def _nifty_feature_frame(candles: pd.DataFrame, timeframe: str) -> pd.DataFrame:
    if candles.empty:
        return pd.DataFrame(index=candles.index)
    close = candles["Close"].astype(float)
    ema9 = calculate_ema(close, 9)
    ema20 = calculate_ema(close, 20)
    rsi14 = calculate_wilder_rsi(close, 14)
    vwap = calculate_session_vwap(candles)
    return pd.DataFrame(
        {
            "feature_nifty_return_5m": _intraday_return(close, timeframe, 5),
            "feature_nifty_return_15m": _intraday_return(close, timeframe, 15),
            "feature_nifty_return_30m": _intraday_return(close, timeframe, 30),
            "feature_nifty_above_ema20": (close > ema20).astype("boolean").where(ema20.notna()),
            "feature_nifty_ema9_vs_ema20_pct": _safe_ratio(ema9 - ema20, close) * 100.0,
            "feature_nifty_rsi14": rsi14,
            "feature_nifty_vwap_distance_pct": _percentage_change(close, vwap),
        },
        index=candles.index,
    )


def calculate_entry_feature_frame(
    candles: pd.DataFrame,
    config: RecoveryConfig,
    *,
    timeframe: str,
    nifty_candles: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """Calculate causal candidate features; every row depends only on rows at or before it."""
    data = calculate_recovery_indicators(candles, config)
    index = pd.DatetimeIndex(data.index)
    if index.tz is None:
        index = index.tz_localize(IST)
    else:
        index = index.tz_convert(IST)
    data.index = index
    feature = pd.DataFrame(index=index)
    open_values = data["Open"].astype(float)
    high = data["High"].astype(float)
    low = data["Low"].astype(float)
    close = data["Close"].astype(float)
    volume = data["Volume"].astype(float)
    rsi = data["RecoveryRSI"].astype(float)
    ema_fast = data["EMAFast"].astype(float)
    ema_slow = data["EMASlow"].astype(float)
    vwap = data["SessionVWAP"].astype(float)
    volume_ema = data["VolumeEMA"].astype(float)

    feature["feature_rsi_at_entry"] = rsi
    feature["feature_rsi_previous"] = rsi.shift(1)
    for bars in (1, 2, 3):
        feature[f"feature_rsi_change_{bars}bar"] = rsi - rsi.shift(bars)

    feature["feature_ema_fast"] = ema_fast
    feature["feature_ema_slow"] = ema_slow
    feature["feature_ema_spread_pct"] = _safe_ratio(ema_fast - ema_slow, close) * 100.0
    for bars in (1, 3, 5):
        feature[f"feature_ema_fast_slope_{bars}"] = _normalized_change(ema_fast, ema_fast.shift(bars), close)
        feature[f"feature_ema_slow_slope_{bars}"] = _normalized_change(ema_slow, ema_slow.shift(bars), close)
    feature["feature_close_above_ema_fast_pct"] = _percentage_change(close, ema_fast)
    feature["feature_close_above_ema_slow_pct"] = _percentage_change(close, ema_slow)
    feature["feature_ema_fast_above_slow"] = (ema_fast > ema_slow).astype("boolean").where(ema_fast.notna() & ema_slow.notna())

    feature["feature_vwap_at_entry"] = vwap
    feature["feature_close_vs_vwap_pct"] = _percentage_change(close, vwap)
    session = pd.Series(index.date, index=index)
    for bars in (1, 3, 5):
        same_session = session.eq(session.shift(bars))
        feature[f"feature_vwap_slope_{bars}"] = _normalized_change(vwap, vwap.shift(bars), close).where(same_session)
    feature["feature_close_above_vwap"] = (close > vwap).astype("boolean").where(vwap.notna())

    prior_volume = volume.shift(1)
    average_volume_5 = prior_volume.rolling(5, min_periods=5).mean()
    average_volume_20 = prior_volume.rolling(20, min_periods=20).mean()
    feature["feature_volume"] = volume
    feature["feature_volume_ema"] = volume_ema
    feature["feature_volume_ratio"] = _safe_ratio(volume, volume_ema)
    feature["feature_volume_vs_previous_bar"] = _percentage_change(volume, prior_volume)
    feature["feature_volume_vs_5bar_average"] = _percentage_change(volume, average_volume_5)
    feature["feature_volume_vs_20bar_average"] = _percentage_change(volume, average_volume_20)
    feature["feature_relative_volume_5"] = _safe_ratio(volume, average_volume_5)
    feature["feature_relative_volume_20"] = _safe_ratio(volume, average_volume_20)

    feature["feature_open"] = open_values
    feature["feature_high"] = high
    feature["feature_low"] = low
    feature["feature_close"] = close
    feature["feature_candle_return_pct"] = _percentage_change(close, open_values)
    feature["feature_body_pct"] = _safe_ratio((close - open_values).abs(), open_values) * 100.0
    feature["feature_range_pct"] = _safe_ratio(high - low, open_values) * 100.0
    feature["feature_upper_wick_pct"] = _safe_ratio(high - pd.concat([open_values, close], axis=1).max(axis=1), open_values) * 100.0
    feature["feature_lower_wick_pct"] = _safe_ratio(pd.concat([open_values, close], axis=1).min(axis=1) - low, open_values) * 100.0
    feature["feature_bullish_candle"] = (close > open_values).astype("boolean")
    feature["feature_close_location_value"] = _safe_ratio(close - low, high - low)

    previous_close = close.shift(1)
    true_range = pd.concat(
        [high - low, (high - previous_close).abs(), (low - previous_close).abs()], axis=1
    ).max(axis=1)
    atr14 = calculate_wilder_rma(true_range, 14)
    returns = close.pct_change(fill_method=None) * 100.0
    feature["feature_true_range"] = true_range
    feature["feature_atr14"] = atr14
    feature["feature_atr_pct"] = _safe_ratio(atr14, close) * 100.0
    feature["feature_rolling_realized_volatility_5"] = returns.rolling(5, min_periods=5).std(ddof=0)
    feature["feature_rolling_realized_volatility_20"] = returns.rolling(20, min_periods=20).std(ddof=0)

    for bars in (1, 2, 3, 5, 10):
        feature[f"feature_return_{bars}bar"] = close.pct_change(bars, fill_method=None) * 100.0
    for minutes in (15, 30, 60):
        feature[f"feature_return_{minutes}m"] = _intraday_return(close, timeframe, minutes)

    rolling_low: dict[int, pd.Series] = {}
    rolling_high: dict[int, pd.Series] = {}
    for bars in (5, 10, 20):
        rolling_low[bars] = low.rolling(bars, min_periods=bars).min()
        rolling_high[bars] = high.rolling(bars, min_periods=bars).max()
        feature[f"feature_distance_from_{bars}bar_low_pct"] = _percentage_change(close, rolling_low[bars])
        feature[f"feature_distance_from_{bars}bar_high_pct"] = _percentage_change(close, rolling_high[bars])
    feature["feature_position_in_20bar_range"] = _safe_ratio(
        close - rolling_low[20], rolling_high[20] - rolling_low[20]
    )

    feature["feature_higher_high_3bar"] = _strictly_rising(high, 3)
    feature["feature_higher_low_3bar"] = _strictly_rising(low, 3)
    feature["feature_higher_high_5bar"] = _strictly_rising(high, 5)
    feature["feature_higher_low_5bar"] = _strictly_rising(low, 5)
    feature["feature_close_above_previous_high"] = (close > high.shift(1)).astype("boolean").where(high.shift(1).notna())
    previous_3bar_high = high.shift(1).rolling(3, min_periods=3).max()
    feature["feature_close_above_previous_3bar_high"] = (close > previous_3bar_high).astype("boolean").where(previous_3bar_high.notna())

    minutes = pd.Series(index.hour * 60 + index.minute, index=index)
    inside_session = minutes.between(9 * 60 + 15, 15 * 60 + 30)
    feature["feature_entry_hour"] = index.hour
    feature["feature_entry_minute"] = index.minute
    feature["feature_minutes_since_market_open"] = (minutes - (9 * 60 + 15)).where(inside_session)
    feature["feature_minutes_until_market_close"] = ((15 * 60 + 30) - minutes).where(inside_session)
    feature["feature_time_of_day_bucket"] = [_time_bucket(stamp) for stamp in index]
    feature["feature_day_of_week"] = index.day_name()
    feature["feature_month"] = index.month
    feature["feature_trading_day_of_month"] = _trading_day_numbers(index)

    opening_gap = _opening_gap(index, open_values, close)
    feature["feature_opening_gap_pct"] = opening_gap
    feature["feature_gap_up"] = opening_gap.gt(0).astype("boolean").where(opening_gap.notna())
    feature["feature_gap_down"] = opening_gap.lt(0).astype("boolean").where(opening_gap.notna())

    for column in NIFTY_FEATURES:
        feature[column] = pd.NA if column == "feature_nifty_above_ema20" else np.nan
    if nifty_candles is not None and not nifty_candles.empty:
        nifty = _nifty_feature_frame(nifty_candles, timeframe)
        source_time = pd.Series(nifty.index, index=nifty.index).reindex(index, method="ffill")
        aligned = nifty.reindex(index, method="ffill")
        same_session = pd.Series(
            [source.date() == target.date() if pd.notna(source) else False for source, target in zip(source_time, index, strict=False)],
            index=index,
        )
        aligned = aligned.where(same_session, np.nan)
        for column in NIFTY_FEATURES:
            feature[column] = aligned[column]

    return feature


def _as_ist(value: Any) -> pd.Timestamp:
    stamp = pd.Timestamp(value)
    if stamp.tzinfo is None:
        return stamp.tz_localize(IST)
    return stamp.tz_convert(IST)


def _python_value(value: Any) -> Any:
    if value is pd.NA or value is None or (not isinstance(value, (str, bool)) and pd.isna(value)):
        return None
    if isinstance(value, (np.bool_, bool)):
        return bool(value)
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating, float)):
        return float(value) if math.isfinite(float(value)) else None
    if isinstance(value, pd.Timestamp):
        return _as_ist(value).isoformat()
    return value


def build_entry_snapshot(
    trade: Mapping[str, Any],
    feature_frame: pd.DataFrame,
    indicator_frame: pd.DataFrame,
    config: RecoveryConfig,
) -> dict[str, Any]:
    """Freeze one entry snapshot without reading any post-signal candle or outcome field."""
    signal_stamp = _as_ist(trade["signalTimestamp"])
    arm_stamp = _as_ist(trade["rsiArmTimestamp"])
    signal_position = int(feature_frame.index.get_indexer([signal_stamp])[0])
    arm_position = int(feature_frame.index.get_indexer([arm_stamp])[0])
    if signal_position < 0 or arm_position < 0 or arm_position > signal_position:
        raise ValueError(f"Trade timestamps are unavailable in the causal candle frame: {trade['tradeId']}")

    row = feature_frame.iloc[signal_position]
    rsi_path = indicator_frame["RecoveryRSI"].iloc[arm_position : signal_position + 1].dropna()
    if rsi_path.empty:
        raise ValueError(f"RSI arm path is unavailable for trade: {trade['tradeId']}")
    minimum_rsi = float(rsi_path.min())
    below_30 = rsi_path < 30.0
    snapshot = {column: _python_value(row.get(column)) for column in ENTRY_FEATURE_COLUMNS}
    snapshot.update(
        {
            "feature_rsi_arm_value": float(trade["rsiArmValue"]),
            "feature_rsi_min_since_arm": minimum_rsi,
            "feature_rsi_max_since_arm": float(rsi_path.max()),
            "feature_bars_arm_to_recovery": signal_position - arm_position,
            "feature_minutes_arm_to_recovery": (signal_stamp - arm_stamp).total_seconds() / 60.0,
            "feature_rsi_recovery_strength": float(row["feature_rsi_at_entry"]) - config.rsi_recovery,
            "feature_rsi_below_30_since_arm": bool(below_30.any()),
            "feature_rsi_min_distance_below_30": max(0.0, 30.0 - minimum_rsi),
            "feature_bars_below_30_since_arm": int(below_30.sum()),
            "feature_confirmation_score": int(trade["confirmationScore"]),
            "feature_ema_confirmation": bool(trade["emaConfirmation"]),
            "feature_vwap_confirmation": bool(trade["vwapConfirmation"]),
            "feature_volume_confirmation": bool(trade["volumeConfirmation"]),
            "feature_confirmation_combination": confirmation_combination(trade),
        }
    )
    return snapshot


def build_signal_feature_snapshots(
    symbol: str,
    candles: pd.DataFrame,
    trades: Sequence[Mapping[str, Any]],
    *,
    timeframe: str,
    config: RecoveryConfig,
    nifty_candles: pd.DataFrame | None = None,
    strategy_version: str = STRATEGY_VERSION,
) -> pd.DataFrame:
    """Create one feature/outcome row per existing engine observation."""
    columns = [*IDENTITY_COLUMNS, *ENTRY_FEATURE_COLUMNS, *OUTCOME_COLUMNS]
    if not trades:
        return pd.DataFrame(columns=columns)
    indicator_frame = calculate_recovery_indicators(candles, config)
    feature_frame = calculate_entry_feature_frame(
        candles,
        config,
        timeframe=timeframe,
        nifty_candles=nifty_candles,
    )
    rows: list[dict[str, Any]] = []
    for trade in trades:
        signal_stamp = _as_ist(trade["signalTimestamp"])
        identity = {
            "trade_id": trade["tradeId"],
            "run_id": trade["runId"],
            "strategy_version": strategy_version,
            "feature_schema_version": FEATURE_SCHEMA_VERSION,
            "feature_definitions_version": FEATURE_DEFINITIONS_VERSION,
            "symbol": symbol,
            "timeframe": timeframe,
            "signal_timestamp": signal_stamp.isoformat(),
            "entry_timestamp": _as_ist(trade["entryTimestamp"]).isoformat(),
            "snapshot_timestamp": signal_stamp.isoformat(),
            "execution_model": trade["executionModel"],
        }
        entry_snapshot = build_entry_snapshot(trade, feature_frame, indicator_frame, config)
        outcome_labels = build_outcome_labels(trade)
        rows.append({**identity, **entry_snapshot, **outcome_labels})
    return pd.DataFrame(rows, columns=columns)


def input_feature_columns(frame: pd.DataFrame | None = None) -> list[str]:
    available = set(frame.columns) if frame is not None else set(ENTRY_FEATURE_COLUMNS)
    return [column for column in ENTRY_FEATURE_COLUMNS if column in available]


def configuration_hash(parameters: Mapping[str, Any]) -> str:
    canonical = json.dumps(dict(parameters), sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def feature_cache_key(
    *,
    run_id: str,
    strategy_version: str,
    config_hash: str,
    data_from: str,
    data_to: str,
) -> str:
    raw = "|".join((run_id, strategy_version, FEATURE_SCHEMA_VERSION, config_hash, data_from, data_to))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _numeric(series: pd.Series) -> pd.Series:
    return pd.to_numeric(series, errors="coerce").replace([np.inf, -np.inf], np.nan).dropna()


def _pct(count: int, total: int) -> float:
    return round(count / total * 100.0, 4) if total else 0.0


def _median(series: pd.Series) -> float | None:
    values = _numeric(series)
    return float(values.median()) if not values.empty else None


def outcome_summary(frame: pd.DataFrame) -> pd.DataFrame:
    order = ("FAST_30M", "FAST_2H", "SAME_DAY", "SLOW", "TRAPPED")
    total = len(frame)
    rows = []
    for label in order:
        count = int(frame["outcome_speed_bucket"].eq(label).sum())
        rows.append({"outcome_class": label, "count": count, "percentage": _pct(count, total)})
    for label in ("GOOD", "BAD", "NEUTRAL"):
        count = int(frame["outcome_binary_quality_label"].eq(label).sum())
        rows.append({"outcome_class": label, "count": count, "percentage": _pct(count, total)})
    return pd.DataFrame(rows)


def descriptive_numeric_analysis(frame: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for outcome in ("FAST_30M", "FAST_2H", "SAME_DAY", "SLOW", "TRAPPED"):
        subset = frame[frame["outcome_speed_bucket"] == outcome]
        for feature in NUMERIC_FEATURES:
            values = _numeric(subset[feature])
            rows.append(
                {
                    "outcome_class": outcome,
                    "feature_name": feature,
                    "count": len(values),
                    "mean": float(values.mean()) if len(values) else None,
                    "median": float(values.median()) if len(values) else None,
                    "std": float(values.std(ddof=1)) if len(values) > 1 else None,
                    "p10": float(values.quantile(0.10)) if len(values) else None,
                    "p25": float(values.quantile(0.25)) if len(values) else None,
                    "p50": float(values.quantile(0.50)) if len(values) else None,
                    "p75": float(values.quantile(0.75)) if len(values) else None,
                    "p90": float(values.quantile(0.90)) if len(values) else None,
                }
            )
    return pd.DataFrame(rows)


def categorical_analysis(frame: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for outcome in ("FAST_30M", "FAST_2H", "SAME_DAY", "SLOW", "TRAPPED"):
        subset = frame[frame["outcome_speed_bucket"] == outcome]
        for feature in CATEGORICAL_FEATURES:
            present = subset[feature].dropna()
            for value, count in present.value_counts(dropna=False).items():
                rows.append(
                    {
                        "outcome_class": outcome,
                        "feature_name": feature,
                        "value": str(value),
                        "count": int(count),
                        "percentage": _pct(int(count), len(present)),
                    }
                )
    return pd.DataFrame(rows)


def cliffs_delta(good: Iterable[float], bad: Iterable[float]) -> float | None:
    good_values = np.asarray(list(good), dtype=float)
    bad_values = np.sort(np.asarray(list(bad), dtype=float))
    good_values = good_values[np.isfinite(good_values)]
    bad_values = bad_values[np.isfinite(bad_values)]
    if not len(good_values) or not len(bad_values):
        return None
    less = np.searchsorted(bad_values, good_values, side="left").sum(dtype=np.int64)
    greater = (len(bad_values) - np.searchsorted(bad_values, good_values, side="right")).sum(dtype=np.int64)
    return float((less - greater) / (len(good_values) * len(bad_values)))


def _separation_analysis(
    frame: pd.DataFrame,
    *,
    good_mask: pd.Series,
    bad_mask: pd.Series,
) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for feature in NUMERIC_FEATURES:
        good = _numeric(frame.loc[good_mask, feature])
        bad = _numeric(frame.loc[bad_mask, feature])
        good_median = float(good.median()) if len(good) else None
        bad_median = float(bad.median()) if len(bad) else None
        difference = good_median - bad_median if good_median is not None and bad_median is not None else None
        relative = difference / abs(bad_median) * 100.0 if difference is not None and bad_median not in {None, 0.0} else None
        effect = cliffs_delta(good, bad)
        direction = "mixed"
        if effect is not None and abs(effect) >= 0.05:
            direction = "higher_is_better" if effect > 0 else "lower_is_better"
        missing = int(frame[feature].isna().sum())
        rows.append(
            {
                "feature_name": feature,
                "good_median": good_median,
                "bad_median": bad_median,
                "absolute_difference": difference,
                "relative_difference_pct": relative,
                "effect_size": effect,
                "absolute_effect_size": abs(effect) if effect is not None else None,
                "direction": direction,
                "good_count": len(good),
                "bad_count": len(bad),
                "sample_count": len(good) + len(bad),
                "missing_pct": _pct(missing, len(frame)),
            }
        )
    return pd.DataFrame(rows).sort_values(
        ["absolute_effect_size", "sample_count"], ascending=[False, False], na_position="last"
    ).reset_index(drop=True).assign(feature_rank=lambda data: np.arange(1, len(data) + 1))[
        [
            "feature_rank",
            "feature_name",
            "good_median",
            "bad_median",
            "absolute_difference",
            "relative_difference_pct",
            "effect_size",
            "absolute_effect_size",
            "direction",
            "good_count",
            "bad_count",
            "sample_count",
            "missing_pct",
        ]
    ]


def feature_separation_analysis(frame: pd.DataFrame) -> pd.DataFrame:
    labels = frame["outcome_binary_quality_label"]
    return _separation_analysis(frame, good_mask=labels.eq("GOOD"), bad_mask=labels.eq("BAD"))


def _group_metrics(subset: pd.DataFrame) -> dict[str, Any]:
    total = len(subset)
    completed = subset[subset["outcome_target_hit"].eq(True)]
    return {
        "observations": total,
        "target_hit_rate_pct": _pct(int(subset["outcome_target_hit"].eq(True).sum()), total),
        "fast_30m_pct": _pct(int(subset["outcome_speed_bucket"].eq("FAST_30M").sum()), total),
        "good_le2h_pct": _pct(int(subset["outcome_binary_quality_label"].eq("GOOD").sum()), total),
        "target_le24h_pct": _pct(
            int(subset["outcome_speed_bucket"].isin(["FAST_30M", "FAST_2H", "SAME_DAY"]).sum()), total
        ),
        "slow_gt24h_pct": _pct(int(subset["outcome_speed_bucket"].eq("SLOW").sum()), total),
        "bad_pct": _pct(int(subset["outcome_binary_quality_label"].eq("BAD").sum()), total),
        "neutral_pct": _pct(int(subset["outcome_binary_quality_label"].eq("NEUTRAL").sum()), total),
        "open_pct": _pct(int(subset["outcome_open_at_dataset_end"].eq(True).sum()), total),
        "median_target_minutes": _median(completed["outcome_duration_minutes"]),
        "median_mae_pct": _median(subset["outcome_mae_pct"]),
        "worst_mae_pct": float(_numeric(subset["outcome_mae_pct"]).min())
        if not _numeric(subset["outcome_mae_pct"]).empty
        else None,
    }


def _quantile_bins(series: pd.Series, bins: int = 5) -> pd.Series:
    numeric = pd.to_numeric(series, errors="coerce")
    valid = numeric.dropna()
    output = pd.Series(pd.NA, index=series.index, dtype="object")
    if valid.nunique() < 2:
        return output
    try:
        categorized = pd.qcut(valid, bins, duplicates="drop")
    except ValueError:
        return output
    output.loc[valid.index] = categorized.astype(object)
    return output


def feature_bin_analysis(frame: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for feature in BIN_FEATURES:
        labels = _quantile_bins(frame[feature])
        categories = sorted(
            pd.unique(labels.dropna()).tolist(),
            key=lambda interval: float(interval.left),
        )
        for number, label in enumerate(categories, start=1):
            subset = frame[labels.eq(label)]
            values = _numeric(subset[feature])
            rows.append(
                {
                    "feature_name": feature,
                    "bin_number": number,
                    "bin_label": str(label),
                    "minimum": float(values.min()) if len(values) else None,
                    "maximum": float(values.max()) if len(values) else None,
                    **_group_metrics(subset),
                }
            )
    return pd.DataFrame(rows)


def two_dimensional_analysis(frame: pd.DataFrame) -> pd.DataFrame:
    pairs = (
        ("feature_volume_ratio", "feature_ema_spread_pct"),
        ("feature_atr_pct", "feature_return_30m"),
    )
    rows: list[dict[str, Any]] = []
    for first, second in pairs:
        first_bins = _quantile_bins(frame[first])
        second_bins = _quantile_bins(frame[second])
        first_order = list(dict.fromkeys(first_bins.dropna().tolist()))
        second_order = list(dict.fromkeys(second_bins.dropna().tolist()))
        for first_number, first_label in enumerate(first_order, start=1):
            for second_number, second_label in enumerate(second_order, start=1):
                subset = frame[first_bins.eq(first_label) & second_bins.eq(second_label)]
                if subset.empty:
                    continue
                rows.append(
                    {
                        "matrix": f"{first}_x_{second}",
                        "row_feature": first,
                        "row_bin": first_number,
                        "row_label": first_label,
                        "column_feature": second,
                        "column_bin": second_number,
                        "column_label": second_label,
                        **_group_metrics(subset),
                    }
                )
    return pd.DataFrame(rows)


def confirmation_analysis(frame: pd.DataFrame) -> pd.DataFrame:
    order = ("EMA_VWAP", "EMA_VOLUME", "VWAP_VOLUME", "EMA_VWAP_VOLUME", "OTHER")
    rows = []
    for combination in order:
        subset = frame[frame["feature_confirmation_combination"].eq(combination)]
        if subset.empty:
            continue
        rows.append({"confirmation_combination": combination, **_group_metrics(subset)})
    return pd.DataFrame(rows)


def time_of_day_analysis(frame: pd.DataFrame) -> pd.DataFrame:
    order = ("OPENING", "MORNING", "MIDDAY", "AFTERNOON", "LATE")
    rows = []
    for bucket in order:
        subset = frame[frame["feature_time_of_day_bucket"].eq(bucket)]
        rows.append({"time_of_day_bucket": bucket, **_group_metrics(subset)})
    return pd.DataFrame(rows)


def symbol_feature_summary(frame: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for symbol, subset in frame.groupby("symbol", sort=True):
        metrics = _group_metrics(subset)
        rows.append(
            {
                "symbol": symbol,
                "buy_observations": len(subset),
                "good_pct": metrics["good_le2h_pct"],
                "bad_pct": metrics["bad_pct"],
                "neutral_pct": metrics["neutral_pct"],
                "open_pct": metrics["open_pct"],
                "median_target_minutes": metrics["median_target_minutes"],
                "median_mae_pct": metrics["median_mae_pct"],
                "median_entry_volume_ratio": _median(subset["feature_volume_ratio"]),
                "median_entry_atr_pct": _median(subset["feature_atr_pct"]),
                "median_entry_ema_spread_pct": _median(subset["feature_ema_spread_pct"]),
                "median_entry_vwap_distance_pct": _median(subset["feature_close_vs_vwap_pct"]),
            }
        )
    return pd.DataFrame(rows)


def trapped_comparison(frame: pd.DataFrame) -> pd.DataFrame:
    selected = (
        "feature_rsi_at_entry",
        "feature_ema_spread_pct",
        "feature_close_vs_vwap_pct",
        "feature_volume_ratio",
        "feature_atr_pct",
        "feature_return_15m",
        "feature_return_30m",
    )
    good_mask = frame["outcome_binary_quality_label"].eq("GOOD")
    trapped_mask = frame["outcome_speed_bucket"].eq("TRAPPED")
    comparison = _separation_analysis(frame, good_mask=good_mask, bad_mask=trapped_mask)
    return comparison[comparison["feature_name"].isin(selected)].reset_index(drop=True)


def candidate_filter_diagnostics(frame: pd.DataFrame, separation: pd.DataFrame) -> pd.DataFrame:
    """Evaluate only coarse 40% tails for later research; never alters signal generation."""

    baseline_total = len(frame)
    baseline_good = _pct(int(frame["outcome_binary_quality_label"].eq("GOOD").sum()), baseline_total)
    baseline_bad = _pct(int(frame["outcome_binary_quality_label"].eq("BAD").sum()), baseline_total)
    baseline_open = _pct(int(frame["outcome_open_at_dataset_end"].eq(True).sum()), baseline_total)
    effect_by_feature = separation.set_index("feature_name")["effect_size"].to_dict()
    rows: list[dict[str, Any]] = []
    for feature in BIN_FEATURES:
        values = pd.to_numeric(frame[feature], errors="coerce")
        valid = values.dropna()
        effect = effect_by_feature.get(feature)
        if valid.nunique() < 5 or effect is None or not math.isfinite(float(effect)):
            continue
        higher = float(effect) >= 0
        quantile = 0.60 if higher else 0.40
        threshold = float(valid.quantile(quantile))
        selected_mask = values.ge(threshold) if higher else values.le(threshold)
        selected = frame[selected_mask.fillna(False)]
        if selected.empty:
            continue
        retained = len(selected)
        rows.append(
            {
                "feature_name": feature,
                "coarse_filter": f"{feature} {'>=' if higher else '<='} {threshold:.6g}",
                "tail": "upper_40_pct" if higher else "lower_40_pct",
                "threshold": threshold,
                "effect_size": float(effect),
                "observations_retained": retained,
                "retained_pct": _pct(retained, baseline_total),
                "good_rate_before_pct": baseline_good,
                "good_rate_after_pct": _pct(
                    int(selected["outcome_binary_quality_label"].eq("GOOD").sum()), retained
                ),
                "bad_rate_before_pct": baseline_bad,
                "bad_rate_after_pct": _pct(
                    int(selected["outcome_binary_quality_label"].eq("BAD").sum()), retained
                ),
                "open_rate_before_pct": baseline_open,
                "open_rate_after_pct": _pct(
                    int(selected["outcome_open_at_dataset_end"].eq(True).sum()), retained
                ),
            }
        )
    columns = (
        "feature_name",
        "coarse_filter",
        "tail",
        "threshold",
        "effect_size",
        "observations_retained",
        "retained_pct",
        "good_rate_before_pct",
        "good_rate_after_pct",
        "bad_rate_before_pct",
        "bad_rate_after_pct",
        "open_rate_before_pct",
        "open_rate_after_pct",
    )
    if not rows:
        return pd.DataFrame(columns=columns)
    return pd.DataFrame(rows, columns=columns).sort_values(
        "effect_size", key=lambda values: values.abs(), ascending=False
    )


def _json_records(frame: pd.DataFrame) -> list[dict[str, Any]]:
    if frame.empty:
        return []
    return json.loads(frame.to_json(orient="records", date_format="iso"))


@dataclass
class FeatureAnalysisBundle:
    payload: dict[str, Any]
    tables: dict[str, pd.DataFrame]


def build_feature_analysis(
    frame: pd.DataFrame,
    metadata: Mapping[str, Any] | None = None,
) -> FeatureAnalysisBundle:
    required = set((*IDENTITY_COLUMNS, *ENTRY_FEATURE_COLUMNS, *OUTCOME_COLUMNS))
    missing = sorted(required.difference(frame.columns))
    if missing:
        raise ValueError("Feature snapshot is missing required columns: " + ", ".join(missing))
    summary = outcome_summary(frame)
    descriptive = descriptive_numeric_analysis(frame)
    categorical = categorical_analysis(frame)
    separation = feature_separation_analysis(frame)
    bins = feature_bin_analysis(frame)
    matrices = two_dimensional_analysis(frame)
    confirmations = confirmation_analysis(frame)
    time_analysis = time_of_day_analysis(frame)
    symbols = symbol_feature_summary(frame)
    trapped = frame[frame["outcome_speed_bucket"].eq("TRAPPED")].copy()
    trapped_profile = trapped_comparison(frame)
    candidate_diagnostics = candidate_filter_diagnostics(frame, separation)
    worst_mae = frame.sort_values("outcome_mae_pct", ascending=True).head(100).copy()
    oldest_open = trapped.sort_values("outcome_duration_minutes", ascending=False).head(100).copy()
    worst_open_pnl = trapped.sort_values("outcome_open_pnl_pct", ascending=True).head(100).copy()
    slowest = frame[frame["outcome_target_hit"].eq(True)].sort_values(
        "outcome_duration_minutes", ascending=False
    ).head(100).copy()
    counts = {row["outcome_class"]: int(row["count"]) for row in summary.to_dict("records")}
    total = len(frame)
    payload = {
        "metadata": {
            **dict(metadata or {}),
            "featureSchemaVersion": FEATURE_SCHEMA_VERSION,
            "featureDefinitionsVersion": FEATURE_DEFINITIONS_VERSION,
            "strategyVersion": (metadata or {}).get("strategyVersion", STRATEGY_VERSION),
            "observationsAnalyzed": total,
            "inputFeatureCount": len(ENTRY_FEATURE_COLUMNS),
            "outcomeFieldCount": len(OUTCOME_COLUMNS),
            "niftyContextAvailable": bool(frame["feature_nifty_rsi14"].notna().any()),
            "sectorContextAvailable": False,
            "sectorContextStatus": "sector context not implemented: no reliable sector mapping exists in the project",
            "analysisType": "causal entry-feature descriptive analysis; no classifier and no strategy filter",
        },
        "summary": {
            "observations": total,
            "goodCount": counts.get("GOOD", 0),
            "goodPct": _pct(counts.get("GOOD", 0), total),
            "badCount": counts.get("BAD", 0),
            "badPct": _pct(counts.get("BAD", 0), total),
            "neutralCount": counts.get("NEUTRAL", 0),
            "neutralPct": _pct(counts.get("NEUTRAL", 0), total),
            "fast30mCount": counts.get("FAST_30M", 0),
            "fast2hCount": counts.get("FAST_2H", 0),
            "sameDayCount": counts.get("SAME_DAY", 0),
            "slowCount": counts.get("SLOW", 0),
            "trappedCount": counts.get("TRAPPED", 0),
        },
        "topSeparatingFeatures": _json_records(separation.head(50)),
        "featureBins": _json_records(bins),
        "twoDimensionalMatrices": _json_records(matrices),
        "confirmationAnalysis": _json_records(confirmations),
        "timeOfDayAnalysis": _json_records(time_analysis),
        "symbolAnalysis": _json_records(symbols),
        "trappedComparison": _json_records(trapped_profile),
        "exploratoryCandidateFilters": _json_records(candidate_diagnostics),
        "availableFilters": {
            "symbols": sorted(frame["symbol"].dropna().astype(str).unique().tolist()),
            "timeframes": sorted(frame["timeframe"].dropna().astype(str).unique().tolist()),
            "confirmationCombinations": sorted(
                frame["feature_confirmation_combination"].dropna().astype(str).unique().tolist()
            ),
            "timeOfDayBuckets": ["OPENING", "MORNING", "MIDDAY", "AFTERNOON", "LATE"],
            "targetOutcomes": ["FAST_30M", "FAST_2H", "SAME_DAY", "SLOW", "TRAPPED"],
        },
        "featureDefinitions": FEATURE_DEFINITIONS,
        "reportFiles": sorted(REPORT_FILENAMES),
    }
    tables = {
        "recovery_outcome_summary.csv": summary,
        "recovery_feature_descriptive.csv": descriptive,
        "recovery_categorical_analysis.csv": categorical,
        "recovery_feature_separation.csv": separation,
        "recovery_feature_bins.csv": bins,
        "recovery_two_dimensional_analysis.csv": matrices,
        "recovery_confirmation_analysis.csv": confirmations,
        "recovery_time_of_day_analysis.csv": time_analysis,
        "recovery_symbol_feature_summary.csv": symbols,
        "recovery_trapped_signals.csv": trapped,
        "recovery_worst_mae.csv": worst_mae,
        "recovery_oldest_open.csv": oldest_open,
        "recovery_worst_open_pnl.csv": worst_open_pnl,
        "recovery_slowest_targets.csv": slowest,
        "recovery_candidate_filter_diagnostics.csv": candidate_diagnostics,
    }
    return FeatureAnalysisBundle(payload=payload, tables=tables)


def filter_feature_snapshots(
    frame: pd.DataFrame,
    *,
    symbol: str | None = None,
    timeframe: str | None = None,
    date_from: str | datetime | None = None,
    date_to: str | datetime | None = None,
    confirmation_combination: str | None = None,
    time_of_day_bucket: str | None = None,
    target_outcome: str | None = None,
) -> pd.DataFrame:
    """Filter persisted snapshots without recalculating indicators in the browser/API."""

    filtered = frame
    if symbol:
        filtered = filtered[filtered["symbol"].astype(str).str.upper().eq(symbol.upper())]
    if timeframe:
        filtered = filtered[filtered["timeframe"].astype(str).eq(timeframe)]

    timestamps = pd.to_datetime(filtered["entry_timestamp"], utc=True, errors="coerce")
    if date_from:
        lower = pd.Timestamp(date_from)
        lower = lower.tz_localize(IST) if lower.tzinfo is None else lower.tz_convert(IST)
        filtered = filtered[timestamps >= lower.tz_convert("UTC")]
        timestamps = timestamps.loc[filtered.index]
    if date_to:
        upper = pd.Timestamp(date_to)
        upper = upper.tz_localize(IST) if upper.tzinfo is None else upper.tz_convert(IST)
        if isinstance(date_to, str) and len(date_to.strip()) == 10:
            upper = upper + pd.Timedelta(days=1) - pd.Timedelta(microseconds=1)
        filtered = filtered[timestamps <= upper.tz_convert("UTC")]

    if confirmation_combination:
        filtered = filtered[
            filtered["feature_confirmation_combination"].eq(confirmation_combination)
        ]
    if time_of_day_bucket:
        filtered = filtered[filtered["feature_time_of_day_bucket"].eq(time_of_day_bucket)]
    if target_outcome:
        normalized = target_outcome.upper()
        if normalized in {"GOOD", "BAD", "NEUTRAL"}:
            filtered = filtered[filtered["outcome_binary_quality_label"].eq(normalized)]
        else:
            filtered = filtered[filtered["outcome_speed_bucket"].eq(normalized)]
    return filtered.copy()


def _atomic_path(destination: Path) -> tuple[Path, Path]:
    destination.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.", suffix=".tmp", dir=destination.parent
    )
    os.close(handle)
    return Path(temporary_name), destination


def _atomic_csv(frame: pd.DataFrame, destination: Path) -> None:
    temporary, final = _atomic_path(destination)
    try:
        frame.to_csv(temporary, index=False)
        os.replace(temporary, final)
    finally:
        temporary.unlink(missing_ok=True)


def _atomic_parquet(frame: pd.DataFrame, destination: Path) -> None:
    temporary, final = _atomic_path(destination)
    try:
        frame.to_parquet(temporary, index=False, engine="pyarrow", compression="zstd")
        os.replace(temporary, final)
    finally:
        temporary.unlink(missing_ok=True)


def _atomic_json(payload: Mapping[str, Any], destination: Path) -> None:
    temporary, final = _atomic_path(destination)
    try:
        temporary.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False, allow_nan=False), encoding="utf-8"
        )
        os.replace(temporary, final)
    finally:
        temporary.unlink(missing_ok=True)


def write_feature_reports(
    frame: pd.DataFrame,
    output_directory: str | Path,
    metadata: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Write the durable snapshot and all descriptive reports atomically."""

    destination = Path(output_directory)
    destination.mkdir(parents=True, exist_ok=True)
    ordered = frame.loc[:, [*IDENTITY_COLUMNS, *ENTRY_FEATURE_COLUMNS, *OUTCOME_COLUMNS]].copy()
    bundle = build_feature_analysis(ordered, metadata=metadata)
    _atomic_parquet(ordered, destination / "recovery_signal_features.parquet")
    _atomic_csv(ordered, destination / "recovery_signal_features.csv")
    for filename, table in bundle.tables.items():
        _atomic_csv(table, destination / filename)
    _atomic_json(bundle.payload, destination / "recovery_feature_analysis.json")
    return bundle.payload


def load_feature_snapshots(report_directory: str | Path) -> pd.DataFrame:
    path = Path(report_directory) / "recovery_signal_features.parquet"
    if not path.is_file():
        raise FileNotFoundError(f"Recovery feature snapshot is not available: {path}")
    return pd.read_parquet(path, engine="pyarrow")


def load_feature_analysis(report_directory: str | Path) -> dict[str, Any]:
    path = Path(report_directory) / "recovery_feature_analysis.json"
    if not path.is_file():
        raise FileNotFoundError(f"Recovery feature analysis is not available: {path}")
    return json.loads(path.read_text(encoding="utf-8"))
