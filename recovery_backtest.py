from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

import numpy as np
import pandas as pd

from main import IST

STRATEGY_VERSION = "rsi-recovery-1.1.0"
MAX_CHART_POINTS = 360
MAX_EVENTS = 300

QUALITY_WEIGHTS = {
    "targetHitRate": 0.40,
    "targetSpeed": 0.30,
    "maeQuality": 0.20,
    "openPosition": 0.10,
}
SPEED_SCORES = {
    "LE_30_MIN": 100.0,
    "GT_30_MIN_LE_2_HOURS": 75.0,
    "GT_2_HOURS_LE_24_HOURS": 40.0,
    "GT_24_HOURS": 10.0,
}


@dataclass(frozen=True)
class RecoveryConfig:
    rsi_length: int = 14
    rsi_arm_low: float = 30.0
    rsi_arm_high: float = 40.0
    rsi_recovery: float = 40.0
    ema_enabled: bool = True
    ema_fast: int = 9
    ema_slow: int = 20
    vwap_enabled: bool = True
    volume_enabled: bool = True
    volume_ema: int = 20
    minimum_confirmations: int = 2
    target_pct: float = 0.5
    setup_expiry_bars: int = 50
    execution_model: Literal["SIGNAL_CLOSE", "NEXT_BAR_OPEN"] = "SIGNAL_CLOSE"
    buy_cost_bps: float = 0.0
    sell_cost_bps: float = 0.0
    slippage_bps: float = 0.0

    @property
    def enabled_confirmations(self) -> int:
        return sum((self.ema_enabled, self.vwap_enabled, self.volume_enabled))

    @property
    def estimated_round_trip_cost_pct(self) -> float:
        return (
            self.buy_cost_bps
            + self.sell_cost_bps
            + 2.0 * self.slippage_bps
        ) / 100.0

    def public_parameters(self) -> dict[str, Any]:
        return {
            "rsiLength": self.rsi_length,
            "rsiArmLow": self.rsi_arm_low,
            "rsiArmHigh": self.rsi_arm_high,
            "rsiRecovery": self.rsi_recovery,
            "emaEnabled": self.ema_enabled,
            "emaFast": self.ema_fast,
            "emaSlow": self.ema_slow,
            "vwapEnabled": self.vwap_enabled,
            "volumeEnabled": self.volume_enabled,
            "volumeEma": self.volume_ema,
            "minimumConfirmations": self.minimum_confirmations,
            "targetPct": self.target_pct,
            "setupExpiryBars": self.setup_expiry_bars,
            "executionModel": self.execution_model,
        }


def _finite(value: Any, digits: int = 6) -> float | None:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(numeric):
        return None
    return round(numeric, digits)


def _iso_ist(value: pd.Timestamp | datetime | None) -> str | None:
    if value is None or pd.isna(value):
        return None
    stamp = pd.Timestamp(value)
    if stamp.tzinfo is None:
        stamp = stamp.tz_localize(IST)
    else:
        stamp = stamp.tz_convert(IST)
    return stamp.isoformat()


def calculate_ema(values: pd.Series, length: int) -> pd.Series:
    """Causal EMA using the ta.ema recurrence (alpha = 2 / (length + 1))."""
    if length <= 0:
        raise ValueError("EMA length must be greater than 0")
    numeric = pd.to_numeric(values, errors="coerce").astype(float)
    return numeric.ewm(span=length, adjust=False, min_periods=length).mean()


def calculate_wilder_rma(values: pd.Series, length: int) -> pd.Series:
    """Wilder RMA seeded with the first length-value SMA, matching Pine ta.rma."""
    if length <= 0:
        raise ValueError("RMA length must be greater than 0")
    numeric = pd.to_numeric(values, errors="coerce").astype(float)
    result = pd.Series(np.nan, index=numeric.index, dtype=float)
    valid_positions = np.flatnonzero(numeric.notna().to_numpy())
    if len(valid_positions) < length:
        return result

    seed_position = int(valid_positions[length - 1])
    seed = float(numeric.iloc[valid_positions[:length]].mean())
    seeded = pd.Series(np.nan, index=numeric.index, dtype=float)
    seeded.iloc[seed_position] = seed
    if seed_position + 1 < len(numeric):
        seeded.iloc[seed_position + 1 :] = numeric.iloc[seed_position + 1 :]
    return seeded.ewm(alpha=1.0 / length, adjust=False, ignore_na=True).mean()


def calculate_wilder_rsi(close: pd.Series, length: int = 14) -> pd.Series:
    """Pine-compatible ta.rsi: close changes smoothed with Wilder RMA."""
    if length <= 0:
        raise ValueError("RSI length must be greater than 0")
    numeric = pd.to_numeric(close, errors="coerce").astype(float)
    change = numeric.diff()
    gains = change.clip(lower=0)
    losses = -change.clip(upper=0)
    average_gain = calculate_wilder_rma(gains, length)
    average_loss = calculate_wilder_rma(losses, length)
    relative_strength = average_gain / average_loss.replace(0, np.nan)
    rsi = 100.0 - (100.0 / (1.0 + relative_strength))
    rsi = rsi.mask(average_loss.eq(0), 100.0)
    rsi = rsi.mask(average_gain.eq(0) & average_loss.ne(0), 0.0)
    return rsi


def calculate_session_vwap(candles: pd.DataFrame) -> pd.Series:
    """NSE session VWAP, reset independently for every Asia/Kolkata date."""
    if candles.empty:
        return pd.Series(index=candles.index, dtype=float)
    if not isinstance(candles.index, pd.DatetimeIndex):
        raise ValueError("VWAP requires a DatetimeIndex")
    index = candles.index
    if index.tz is None:
        index = index.tz_localize(IST)
    else:
        index = index.tz_convert(IST)
    session = pd.Series(index.date, index=candles.index)
    typical_price = (candles["High"] + candles["Low"] + candles["Close"]) / 3.0
    weighted = typical_price * candles["Volume"]
    cumulative_weighted = weighted.groupby(session, sort=False).cumsum()
    cumulative_volume = candles["Volume"].groupby(session, sort=False).cumsum()
    return cumulative_weighted / cumulative_volume.replace(0, np.nan)


def calculate_recovery_indicators(
    candles: pd.DataFrame,
    config: RecoveryConfig,
) -> pd.DataFrame:
    data = candles.copy()
    data["RecoveryRSI"] = calculate_wilder_rsi(data["Close"], config.rsi_length)
    data["EMAFast"] = calculate_ema(data["Close"], config.ema_fast)
    data["EMASlow"] = calculate_ema(data["Close"], config.ema_slow)
    data["SessionVWAP"] = calculate_session_vwap(data)
    data["VolumeEMA"] = calculate_ema(data["Volume"], config.volume_ema)
    return data


def validate_candles(candles: pd.DataFrame) -> list[str]:
    required = ["Open", "High", "Low", "Close", "Volume"]
    issues: list[str] = []
    missing = [column for column in required if column not in candles.columns]
    if missing:
        return ["missing columns: " + ", ".join(missing)]
    if not isinstance(candles.index, pd.DatetimeIndex):
        issues.append("timestamps are not a DatetimeIndex")
    else:
        if not candles.index.is_monotonic_increasing:
            issues.append("timestamps are not ascending")
        if candles.index.has_duplicates:
            issues.append("duplicate candle timestamps")
        if candles.index.tz is None:
            issues.append("timestamps are not timezone-aware")

    numeric = candles[required].apply(pd.to_numeric, errors="coerce")
    if numeric[["Open", "High", "Low", "Close"]].isna().any().any():
        issues.append("OHLC contains missing or non-numeric values")
    if numeric["Volume"].isna().any():
        issues.append("volume contains missing or non-numeric values")
    if numeric["Volume"].lt(0).any():
        issues.append("volume contains negative values")
    if (
        numeric["High"].lt(numeric["Open"])
        | numeric["High"].lt(numeric["Close"])
        | numeric["High"].lt(numeric["Low"])
    ).any():
        issues.append("high is below open, close, or low")
    if (
        numeric["Low"].gt(numeric["Open"])
        | numeric["Low"].gt(numeric["Close"])
        | numeric["Low"].gt(numeric["High"])
    ).any():
        issues.append("low is above open, close, or high")
    return issues


def rsi_recovery_crossovers(rsi: pd.Series, recovery_level: float) -> pd.Series:
    numeric = pd.to_numeric(rsi, errors="coerce")
    return (numeric.gt(recovery_level) & numeric.shift(1).le(recovery_level)).fillna(False)


def _target_speed_bucket(minutes: float) -> str:
    if minutes <= 30:
        return "LE_30_MIN"
    if minutes <= 120:
        return "GT_30_MIN_LE_2_HOURS"
    if minutes <= 1_440:
        return "GT_2_HOURS_LE_24_HOURS"
    return "GT_24_HOURS"


def _session_speed_bucket(session_distance: int) -> str:
    if session_distance <= 0:
        return "SAME_SESSION"
    if session_distance == 1:
        return "NEXT_SESSION"
    if session_distance <= 5:
        return "TWO_TO_FIVE_TRADING_DAYS"
    return "GT_FIVE_TRADING_DAYS"


def _holding_fields(
    candles: pd.DataFrame,
    entry_index: int,
    end_index: int,
) -> dict[str, Any]:
    entry_time = pd.Timestamp(candles.index[entry_index])
    end_time = pd.Timestamp(candles.index[end_index])
    duration_minutes = max((end_time - entry_time).total_seconds() / 60.0, 0.0)
    session_dates = pd.Index(candles.index[entry_index : end_index + 1].date).unique()
    sessions_held = len(session_dates)
    session_distance = max(sessions_held - 1, 0)
    return {
        "barsHeld": max(end_index - entry_index, 0),
        "tradingSessionsHeld": sessions_held,
        "sessionDistance": session_distance,
        "durationMinutes": _finite(duration_minutes, 2),
        "durationHours": _finite(duration_minutes / 60.0, 4),
        "durationDays": _finite(duration_minutes / 1_440.0, 6),
    }


def _confirmation_state(row: pd.Series, config: RecoveryConfig) -> dict[str, Any]:
    ema_confirmation = bool(
        config.ema_enabled
        and pd.notna(row["EMAFast"])
        and pd.notna(row["EMASlow"])
        and float(row["EMAFast"]) > float(row["EMASlow"])
    )
    vwap_confirmation = bool(
        config.vwap_enabled
        and pd.notna(row["SessionVWAP"])
        and float(row["Close"]) > float(row["SessionVWAP"])
    )
    volume_confirmation = bool(
        config.volume_enabled
        and pd.notna(row["VolumeEMA"])
        and float(row["Volume"]) > float(row["VolumeEMA"])
    )
    score = sum((ema_confirmation, vwap_confirmation, volume_confirmation))
    return {
        "confirmationScore": score,
        "emaConfirmation": ema_confirmation,
        "vwapConfirmation": vwap_confirmation,
        "volumeConfirmation": volume_confirmation,
    }


def _sample_indices(total: int, event_indices: set[int]) -> list[int]:
    if total <= MAX_CHART_POINTS:
        return list(range(total))
    regular = np.linspace(0, total - 1, MAX_CHART_POINTS, dtype=int)
    return sorted(set(regular.tolist()) | event_indices)


def _complete_trade(
    *,
    run_id: str,
    symbol: str,
    timeframe: str,
    candles: pd.DataFrame,
    position: dict[str, Any],
    end_index: int,
    config: RecoveryConfig,
    status: Literal["TARGET_HIT", "OPEN"],
) -> dict[str, Any]:
    entry_price = float(position["entryPrice"])
    target_price = float(position["targetPrice"])
    lowest = float(position["lowestPrice"] if position["lowestPrice"] is not None else entry_price)
    highest = float(position["highestPrice"] if position["highestPrice"] is not None else entry_price)
    last_close = float(candles.iloc[end_index]["Close"])
    holding = _holding_fields(candles, position["entryIndex"], end_index)
    gross_return = config.target_pct if status == "TARGET_HIT" else (last_close / entry_price - 1.0) * 100.0
    estimated_cost = config.estimated_round_trip_cost_pct
    target_timestamp = _iso_ist(candles.index[end_index]) if status == "TARGET_HIT" else None
    exit_price = target_price if status == "TARGET_HIT" else None
    target_bucket = _target_speed_bucket(float(holding["durationMinutes"])) if status == "TARGET_HIT" else None
    session_bucket = _session_speed_bucket(int(holding["sessionDistance"])) if status == "TARGET_HIT" else None
    signal = position["signal"]
    return {
        "tradeId": position["tradeId"],
        "sequenceNumber": position["sequenceNumber"],
        "runId": run_id,
        "strategyMode": "rsi_recovery",
        "symbol": symbol,
        "timeframe": timeframe,
        "signalTimestamp": _iso_ist(signal["signalTimestamp"]),
        "entryTimestamp": _iso_ist(candles.index[position["entryIndex"]]),
        "entryBarIndex": int(position["entryIndex"]),
        "executionModel": config.execution_model,
        "entryPrice": _finite(entry_price, 4),
        "targetPct": config.target_pct,
        "targetPrice": _finite(target_price, 4),
        "status": status,
        "targetHitTimestamp": target_timestamp,
        "exitPrice": _finite(exit_price, 4),
        **holding,
        "targetSpeedBucket": target_bucket,
        "sessionSpeedBucket": session_bucket,
        "rsiArmTimestamp": _iso_ist(signal["rsiArmTimestamp"]),
        "rsiArmValue": _finite(signal["rsiArmValue"], 6),
        "rsiAtEntry": _finite(signal["rsiAtEntry"], 6),
        "confirmationScore": signal["confirmationScore"],
        "requiredConfirmations": config.minimum_confirmations,
        "emaEnabled": config.ema_enabled,
        "vwapEnabled": config.vwap_enabled,
        "volumeEnabled": config.volume_enabled,
        "emaConfirmation": signal["emaConfirmation"],
        "vwapConfirmation": signal["vwapConfirmation"],
        "volumeConfirmation": signal["volumeConfirmation"],
        "emaFastAtEntry": _finite(signal["emaFastAtEntry"], 6),
        "emaSlowAtEntry": _finite(signal["emaSlowAtEntry"], 6),
        "vwapAtEntry": _finite(signal["vwapAtEntry"], 6),
        "volumeAtEntry": _finite(signal["volumeAtEntry"], 2),
        "volumeEmaAtEntry": _finite(signal["volumeEmaAtEntry"], 6),
        "lowestPriceAfterEntry": _finite(lowest, 4),
        "maxAdversePct": _finite((lowest / entry_price - 1.0) * 100.0, 6),
        "highestPriceAfterEntry": _finite(highest, 4),
        "maxFavorablePct": _finite((highest / entry_price - 1.0) * 100.0, 6),
        "lastTimestamp": _iso_ist(candles.index[end_index]),
        "lastClose": _finite(last_close, 4),
        "currentPnlPct": _finite(gross_return, 6) if status == "OPEN" else None,
        "grossReturnPct": _finite(gross_return, 6),
        "estimatedCostPct": _finite(estimated_cost, 6),
        "netReturnPct": _finite(gross_return - estimated_cost, 6),
    }


def _concurrency_metrics(trades: list[dict[str, Any]]) -> dict[str, Any]:
    """Summarize independent observation overlap without scanning completed trades per candle."""
    if not trades:
        return {
            "maximumConcurrentOpenSignals": 0,
            "averageConcurrentOpenSignals": 0.0,
            "maximumSignalsOpenSameDay": 0,
        }

    events: list[tuple[pd.Timestamp, int]] = []
    day_events: list[tuple[pd.Timestamp, int]] = []
    for trade in trades:
        start = pd.Timestamp(trade["entryTimestamp"])
        end = pd.Timestamp(trade["targetHitTimestamp"] or trade["lastTimestamp"])
        events.extend(((start, 1), (end, -1)))
        start_day = start.tz_convert(IST).normalize()
        end_day_exclusive = end.tz_convert(IST).normalize() + pd.Timedelta(days=1)
        day_events.extend(((start_day, 1), (end_day_exclusive, -1)))

    ordered = sorted(events, key=lambda item: (item[0], -item[1]))
    active = 0
    maximum = 0
    weighted_seconds = 0.0
    previous = ordered[0][0]
    position = 0
    while position < len(ordered):
        timestamp = ordered[position][0]
        weighted_seconds += active * max((timestamp - previous).total_seconds(), 0.0)
        while position < len(ordered) and ordered[position][0] == timestamp:
            active += ordered[position][1]
            maximum = max(maximum, active)
            position += 1
        previous = timestamp
    span_seconds = max((ordered[-1][0] - ordered[0][0]).total_seconds(), 0.0)
    average = weighted_seconds / span_seconds if span_seconds > 0 else float(maximum)

    active_by_day = 0
    maximum_same_day = 0
    for _, delta in sorted(day_events, key=lambda item: (item[0], -item[1])):
        active_by_day += delta
        maximum_same_day = max(maximum_same_day, active_by_day)

    return {
        "maximumConcurrentOpenSignals": maximum,
        "averageConcurrentOpenSignals": _finite(average, 4),
        "maximumSignalsOpenSameDay": maximum_same_day,
    }


def _mean(values: list[float]) -> float | None:
    return _finite(np.mean(values), 4) if values else None


def _median(values: list[float]) -> float | None:
    return _finite(np.median(values), 4) if values else None


def summarize_recovery_trades(symbol: str, trades: list[dict[str, Any]]) -> dict[str, Any]:
    completed = [trade for trade in trades if trade["status"] == "TARGET_HIT"]
    open_trades = [trade for trade in trades if trade["status"] == "OPEN"]
    total = len(trades)
    hit_rate = len(completed) / total * 100.0 if total else 0.0
    speed_counts = {bucket: 0 for bucket in SPEED_SCORES}
    for trade in completed:
        speed_counts[str(trade["targetSpeedBucket"])] += 1
    completed_count = len(completed)
    speed_percentages = {
        bucket: (count / completed_count * 100.0 if completed_count else 0.0)
        for bucket, count in speed_counts.items()
    }
    speed_score = (
        sum(speed_counts[bucket] * SPEED_SCORES[bucket] for bucket in SPEED_SCORES) / completed_count
        if completed_count
        else 0.0
    )
    completed_mae = [float(trade["maxAdversePct"]) for trade in completed]
    completed_mfe = [float(trade["maxFavorablePct"]) for trade in completed]
    median_mae = float(np.median(completed_mae)) if completed_mae else -10.0
    mae_score = min(max(100.0 + median_mae * 10.0, 0.0), 100.0)
    open_rate = len(open_trades) / total * 100.0 if total else 100.0
    quality_score = (
        QUALITY_WEIGHTS["targetHitRate"] * hit_rate
        + QUALITY_WEIGHTS["targetSpeed"] * speed_score
        + QUALITY_WEIGHTS["maeQuality"] * mae_score
        + QUALITY_WEIGHTS["openPosition"] * (100.0 - open_rate)
    )
    durations = [float(trade["durationMinutes"]) for trade in completed]
    bars = [float(trade["barsHeld"]) for trade in completed]
    open_ages = [float(trade["durationMinutes"]) for trade in open_trades]
    open_pnl = [float(trade["currentPnlPct"]) for trade in open_trades]
    open_mae = [float(trade["maxAdversePct"]) for trade in open_trades]
    le_30 = speed_counts["LE_30_MIN"]
    le_2h = le_30 + speed_counts["GT_30_MIN_LE_2_HOURS"]
    le_24h = le_2h + speed_counts["GT_2_HOURS_LE_24_HOURS"]
    concurrency = _concurrency_metrics(trades)
    return {
        "symbol": symbol,
        "totalBuySignals": total,
        "buySignals": total,
        "targetsHit": completed_count,
        "openSignals": len(open_trades),
        "targetHitRate": _finite(hit_rate, 2),
        **concurrency,
        "le30mPct": _finite(le_30 / completed_count * 100.0, 2) if completed_count else 0.0,
        "le2hPct": _finite(le_2h / completed_count * 100.0, 2) if completed_count else 0.0,
        "le24hPct": _finite(le_24h / completed_count * 100.0, 2) if completed_count else 0.0,
        "averageTargetMinutes": _mean(durations),
        "medianTargetMinutes": _median(durations),
        "averageBarsToTarget": _mean(bars),
        "medianBarsToTarget": _median(bars),
        "averageMaePct": _mean(completed_mae),
        "medianMaePct": _median(completed_mae),
        "worstMaePct": _finite(min(completed_mae), 4) if completed_mae else None,
        "averageMfePct": _mean(completed_mfe),
        "medianMfePct": _median(completed_mfe),
        "openPositions": len(open_trades),
        "openPct": _finite(open_rate, 2),
        "averageOpenAgeMinutes": _mean(open_ages),
        "medianOpenAgeMinutes": _median(open_ages),
        "oldestOpenMinutes": _finite(max(open_ages), 2) if open_ages else None,
        "averageOpenPnlPct": _mean(open_pnl),
        "worstOpenPnlPct": _finite(min(open_pnl), 4) if open_pnl else None,
        "averageOpenMaePct": _mean(open_mae),
        "worstOpenMaePct": _finite(min(open_mae), 4) if open_mae else None,
        "speedBuckets": {
            bucket: {"count": speed_counts[bucket], "pct": _finite(speed_percentages[bucket], 2)}
            for bucket in SPEED_SCORES
        },
        "hitRateScore": _finite(hit_rate, 2),
        "speedScore": _finite(speed_score, 2),
        "maeScore": _finite(mae_score, 2),
        "openPenalty": _finite(open_rate, 2),
        "qualityScore": _finite(quality_score, 2),
    }


def simulate_recovery_symbol(
    symbol: str,
    candles: pd.DataFrame,
    *,
    timeframe: str,
    config: RecoveryConfig,
    run_id: str,
    analysis_start: datetime | None = None,
    indicator_frame: pd.DataFrame | None = None,
) -> dict[str, Any]:
    issues = validate_candles(candles)
    if issues:
        raise ValueError("Data quality validation failed: " + "; ".join(issues))
    warmup = max(config.rsi_length + 1, config.ema_fast, config.ema_slow, config.volume_ema)
    if len(candles) < warmup + 2:
        raise ValueError("Not enough candles to calculate recovery indicators")

    # Market-Aligned workers calculate this deterministic frame once and reuse it
    # for candidate detection and candidate-only feature extraction.  Legacy
    # callers keep the original path and therefore retain byte-for-byte results.
    data = indicator_frame if indicator_frame is not None else calculate_recovery_indicators(candles, config)
    if not data.index.equals(candles.index):
        raise ValueError("Precomputed recovery indicators must match candle timestamps")
    start_position = 0
    if analysis_start is not None:
        start_stamp = pd.Timestamp(analysis_start)
        if start_stamp.tzinfo is None:
            start_stamp = start_stamp.tz_localize(IST)
        else:
            start_stamp = start_stamp.tz_convert(IST)
        start_position = int(data.index.searchsorted(start_stamp, side="left"))
    if start_position >= len(data):
        raise ValueError("No candles are available inside the requested analysis window")

    recovery_crossovers = rsi_recovery_crossovers(data["RecoveryRSI"], config.rsi_recovery)
    timestamps = data.index
    open_values = data["Open"].to_numpy(dtype=float, copy=False)
    high_values = data["High"].to_numpy(dtype=float, copy=False)
    low_values = data["Low"].to_numpy(dtype=float, copy=False)
    close_values = data["Close"].to_numpy(dtype=float, copy=False)
    volume_values = data["Volume"].to_numpy(dtype=float, copy=False)
    rsi_values = data["RecoveryRSI"].to_numpy(dtype=float, copy=False)
    ema_fast_values = data["EMAFast"].to_numpy(dtype=float, copy=False)
    ema_slow_values = data["EMASlow"].to_numpy(dtype=float, copy=False)
    vwap_values = data["SessionVWAP"].to_numpy(dtype=float, copy=False)
    volume_ema_values = data["VolumeEMA"].to_numpy(dtype=float, copy=False)
    recovery_values = recovery_crossovers.to_numpy(dtype=bool, copy=False)
    armed: dict[str, Any] | None = None
    active_trades: list[dict[str, Any]] = []
    pending: dict[str, Any] | None = None
    trades: list[dict[str, Any]] = []
    events: list[dict[str, Any]] = []
    event_indices: set[int] = set()
    action_by_index: dict[int, str] = {}
    decision_by_index: dict[int, dict[str, Any]] = {}
    next_sequence = 1

    def create_active_trade(signal: dict[str, Any], entry_index: int, entry_price: float) -> dict[str, Any]:
        nonlocal next_sequence
        sequence = next_sequence
        next_sequence += 1
        return {
            "tradeId": f"{run_id}:{symbol}:{sequence}",
            "sequenceNumber": sequence,
            "signal": signal,
            "entryIndex": entry_index,
            "entryPrice": entry_price,
            "targetPrice": entry_price * (1.0 + config.target_pct / 100.0),
            "monitorFrom": entry_index + 1,
            "lowestPrice": None,
            "highestPrice": None,
        }

    for index in range(start_position, len(data)):
        current_rsi = rsi_values[index]
        cycle_completed_this_bar = False

        if pending is not None and pending["entryIndex"] == index:
            entry_price = open_values[index]
            opened = create_active_trade(pending["signal"], index, entry_price)
            active_trades.append(opened)
            action_by_index[index] = "buy"
            decision_by_index[index] = {
                "entryPrice": _finite(entry_price, 4),
                "targetPrice": _finite(opened["targetPrice"], 4),
                "confirmationScore": pending["signal"]["confirmationScore"],
                "reason": "Closed-candle recovery signal executed at the next candle open.",
            }
            event_indices.add(index)
            pending = None

        if active_trades:
            still_active: list[dict[str, Any]] = []
            for active in active_trades:
                if index < active["monitorFrom"]:
                    still_active.append(active)
                    continue
                low = low_values[index]
                high = high_values[index]
                active["lowestPrice"] = low if active["lowestPrice"] is None else min(active["lowestPrice"], low)
                active["highestPrice"] = high if active["highestPrice"] is None else max(active["highestPrice"], high)
                if high < active["targetPrice"]:
                    still_active.append(active)
                    continue
                trade = _complete_trade(
                    run_id=run_id,
                    symbol=symbol,
                    timeframe=timeframe,
                    candles=data,
                    position=active,
                    end_index=index,
                    config=config,
                    status="TARGET_HIT",
                )
                trades.append(trade)
                action_by_index[index] = "sell"
                decision_by_index[index] = {
                    "entryPrice": trade["entryPrice"],
                    "targetPrice": trade["targetPrice"],
                    "netReturnPct": trade["netReturnPct"],
                    "confirmationScore": trade["confirmationScore"],
                    "reason": "Target reached after entry; timestamp uses this candle close as a conservative approximation.",
                }
                events.append({
                    "type": "TARGET_HIT",
                    "timestamp": trade["targetHitTimestamp"],
                    "price": trade["targetPrice"],
                    "rsi": _finite(current_rsi, 4),
                    "tradeId": trade["tradeId"],
                })
                event_indices.add(index)
            active_trades = still_active

        if armed is not None and config.setup_expiry_bars > 0 and index - armed["index"] > config.setup_expiry_bars:
            events.append({
                "type": "SETUP_EXPIRED",
                "timestamp": _iso_ist(timestamps[index]),
                "price": _finite(close_values[index], 4),
                "rsi": _finite(current_rsi, 4),
            })
            event_indices.add(index)
            armed = None

        recovery = recovery_values[index]
        if armed is not None and armed["index"] < index and recovery:
            ema_confirmation = bool(
                config.ema_enabled
                and np.isfinite(ema_fast_values[index])
                and np.isfinite(ema_slow_values[index])
                and ema_fast_values[index] > ema_slow_values[index]
            )
            vwap_confirmation = bool(
                config.vwap_enabled
                and np.isfinite(vwap_values[index])
                and close_values[index] > vwap_values[index]
            )
            volume_confirmation = bool(
                config.volume_enabled
                and np.isfinite(volume_ema_values[index])
                and volume_values[index] > volume_ema_values[index]
            )
            confirmation = {
                "confirmationScore": sum((ema_confirmation, vwap_confirmation, volume_confirmation)),
                "emaConfirmation": ema_confirmation,
                "vwapConfirmation": vwap_confirmation,
                "volumeConfirmation": volume_confirmation,
            }
            signal = {
                "signalIndex": index,
                "signalTimestamp": timestamps[index],
                "rsiArmTimestamp": armed["timestamp"],
                "rsiArmValue": armed["rsi"],
                "rsiAtEntry": current_rsi,
                **confirmation,
                "emaFastAtEntry": ema_fast_values[index],
                "emaSlowAtEntry": ema_slow_values[index],
                "vwapAtEntry": vwap_values[index],
                "volumeAtEntry": volume_values[index],
                "volumeEmaAtEntry": volume_ema_values[index],
            }
            if confirmation["confirmationScore"] >= config.minimum_confirmations:
                opened: dict[str, Any] | None = None
                if config.execution_model == "SIGNAL_CLOSE":
                    entry_price = close_values[index]
                    opened = create_active_trade(signal, index, entry_price)
                    active_trades.append(opened)
                    cycle_completed_this_bar = True
                    action_by_index[index] = "buy"
                    reason = "Mandatory RSI recovery and enough enabled confirmations; reference entry is the signal close."
                elif index + 1 < len(data):
                    pending = {"entryIndex": index + 1, "signal": signal}
                    cycle_completed_this_bar = True
                    event_indices.add(index + 1)
                    reason = "Mandatory RSI recovery and enough enabled confirmations; execution waits for the next open."
                else:
                    reason = "Valid recovery signal, but no following candle exists for NEXT_BAR_OPEN execution."
                decision_by_index[index] = {
                    "entryPrice": _finite(close_values[index], 4) if config.execution_model == "SIGNAL_CLOSE" else None,
                    "targetPrice": _finite(opened["targetPrice"], 4) if opened is not None else None,
                    "confirmationScore": confirmation["confirmationScore"],
                    "reason": reason,
                }
                events.append({
                    "type": "BUY" if opened is not None or pending is not None else "RECOVERY_NO_NEXT_BAR",
                    "timestamp": _iso_ist(timestamps[index]),
                    "price": _finite(close_values[index], 4),
                    "rsi": _finite(current_rsi, 4),
                    "tradeId": opened["tradeId"] if opened is not None else None,
                    **confirmation,
                })
                event_indices.add(index)
                if opened is not None or pending is not None:
                    armed = None
            else:
                events.append({
                    "type": "RECOVERY_REJECTED",
                    "timestamp": _iso_ist(timestamps[index]),
                    "price": _finite(close_values[index], 4),
                    "rsi": _finite(current_rsi, 4),
                    **confirmation,
                })
                event_indices.add(index)

        if (
            not cycle_completed_this_bar
            and armed is None
            and np.isfinite(current_rsi)
            and config.rsi_arm_low <= current_rsi <= config.rsi_arm_high
        ):
            armed = {
                "index": index,
                "timestamp": timestamps[index],
                "rsi": current_rsi,
            }
            events.append({
                "type": "ARMED",
                "timestamp": _iso_ist(timestamps[index]),
                "price": _finite(close_values[index], 4),
                "rsi": _finite(current_rsi, 4),
            })
            event_indices.add(index)

    for active in active_trades:
        trades.append(
            _complete_trade(
                run_id=run_id,
                symbol=symbol,
                timeframe=timeframe,
                candles=data,
                position=active,
                end_index=len(data) - 1,
                config=config,
                status="OPEN",
            )
        )
    trades.sort(key=lambda trade: int(trade["sequenceNumber"]))

    analysis_data = data.iloc[start_position:]
    sampled = _sample_indices(len(analysis_data), {index - start_position for index in event_indices if index >= start_position})
    chart = []
    for relative_index in sampled:
        absolute_index = start_position + relative_index
        row = data.iloc[absolute_index]
        chart.append({
            "time": _iso_ist(data.index[absolute_index]),
            "close": _finite(row["Close"], 4),
            "rsi": _finite(row["RecoveryRSI"], 4),
            "equity": None,
            "action": action_by_index.get(absolute_index),
            **decision_by_index.get(absolute_index, {}),
        })

    summary = summarize_recovery_trades(symbol, trades)
    return {
        "symbol": symbol,
        "firstCandle": _iso_ist(analysis_data.index[0]),
        "lastCandle": _iso_ist(analysis_data.index[-1]),
        "bars": len(analysis_data),
        **summary,
        "trades": trades,
        "events": events[-MAX_EVENTS:],
        "chart": chart,
    }


def aggregate_recovery_results(results: list[dict[str, Any]]) -> dict[str, Any]:
    trades = [trade for result in results for trade in result.get("trades", [])]
    completed = [trade for trade in trades if trade["status"] == "TARGET_HIT"]
    open_trades = [trade for trade in trades if trade["status"] == "OPEN"]
    speed_counts = {bucket: 0 for bucket in SPEED_SCORES}
    session_counts = {
        "SAME_SESSION": 0,
        "NEXT_SESSION": 0,
        "TWO_TO_FIVE_TRADING_DAYS": 0,
        "GT_FIVE_TRADING_DAYS": 0,
    }
    for trade in completed:
        speed_counts[str(trade["targetSpeedBucket"])] += 1
        session_counts[str(trade["sessionSpeedBucket"])] += 1
    completed_count = len(completed)

    def bucket_payload(counts: dict[str, int]) -> dict[str, dict[str, Any]]:
        return {
            bucket: {
                "count": count,
                "pct": _finite(count / completed_count * 100.0, 2) if completed_count else 0.0,
            }
            for bucket, count in counts.items()
        }

    durations = [float(trade["durationMinutes"]) for trade in completed]
    bars = [float(trade["barsHeld"]) for trade in completed]
    completed_mae = [float(trade["maxAdversePct"]) for trade in completed]
    completed_mfe = [float(trade["maxFavorablePct"]) for trade in completed]
    open_ages = [float(trade["durationMinutes"]) for trade in open_trades]
    open_pnl = [float(trade["currentPnlPct"]) for trade in open_trades]
    open_mae = [float(trade["maxAdversePct"]) for trade in open_trades]

    universe_concurrency = _concurrency_metrics(trades)
    open_counts = [int(result.get("openSignals", result.get("openPositions", 0))) for result in results]
    max_same_symbol = max(
        (int(result.get("maximumConcurrentOpenSignals", 0)) for result in results),
        default=0,
    )
    oldest = max(open_trades, key=lambda trade: float(trade["durationMinutes"]), default=None)
    return {
        "totalBuySignals": len(trades),
        "buySignals": len(trades),
        "totalTargetsHit": completed_count,
        "targetsHit": completed_count,
        "targetHitRate": _finite(completed_count / len(trades) * 100.0, 2) if trades else 0.0,
        "totalOpenSignals": len(open_trades),
        "stillOpen": len(open_trades),
        "maximumConcurrentSignalsUniverse": universe_concurrency["maximumConcurrentOpenSignals"],
        "maximumConcurrentSignalsSameSymbol": max_same_symbol,
        "symbolsWithOpenSignals": sum(count > 0 for count in open_counts),
        "averageOpenSignalsPerSymbol": _finite(len(open_trades) / len(results), 4) if results else 0.0,
        "symbolsWith2PlusOpenSignals": sum(count >= 2 for count in open_counts),
        "symbolsWith5PlusOpenSignals": sum(count >= 5 for count in open_counts),
        "maxConcurrentPositions": universe_concurrency["maximumConcurrentOpenSignals"],
        "targetSpeedBuckets": bucket_payload(speed_counts),
        "sessionSpeedBuckets": bucket_payload(session_counts),
        "averageTargetMinutes": _mean(durations),
        "medianTargetMinutes": _median(durations),
        "averageBarsToTarget": _mean(bars),
        "medianBarsToTarget": _median(bars),
        "averageCompletedMaePct": _mean(completed_mae),
        "medianCompletedMaePct": _median(completed_mae),
        "worstCompletedMaePct": _finite(min(completed_mae), 4) if completed_mae else None,
        "averageCompletedMfePct": _mean(completed_mfe),
        "medianCompletedMfePct": _median(completed_mfe),
        "averageOpenAgeMinutes": _mean(open_ages),
        "medianOpenAgeMinutes": _median(open_ages),
        "oldestOpenMinutes": _finite(max(open_ages), 2) if open_ages else None,
        "oldestOpenSymbol": oldest["symbol"] if oldest else None,
        "averageOpenPnlPct": _mean(open_pnl),
        "worstOpenPnlPct": _finite(min(open_pnl), 4) if open_pnl else None,
        "averageOpenMaePct": _mean(open_mae),
        "worstOpenMaePct": _finite(min(open_mae), 4) if open_mae else None,
        "candleRowsProcessed": sum(int(result.get("bars", 0)) for result in results),
    }
