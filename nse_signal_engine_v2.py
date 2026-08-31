from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from datetime import time as datetime_time
from typing import Any, Mapping, Sequence

import pandas as pd

from main import IST


ENGINE_VERSION = "nse-signal-engine-2.0.0"
TREND_PULLBACK_KEY = "nse_trend_pullback_continuation_v2"
BREAKOUT_RETEST_KEY = "nse_breakout_retest_v2"


@dataclass(frozen=True)
class NseSignalEngineV2Config:
    entry_start_time: str = "09:30"
    last_entry_time: str = "14:45"
    square_off_time: str = "15:15"
    minimum_breadth_pct: float = 45.0
    minimum_rvol: float = 1.20
    breakout_rvol: float = 1.50
    minimum_relative_nifty_pct: float = 0.0
    pullback_lookback_bars: int = 6
    breakout_lookback_bars: int = 20
    breakout_retest_bars: int = 3
    opening_range_bars: int = 3
    entry_buffer_atr: float = 0.10
    stop_buffer_atr: float = 0.05
    minimum_stop_pct: float = 0.35
    maximum_stop_pct: float = 1.00
    reward_risk: float = 1.50
    maximum_holding_bars: int = 12
    stagnation_bars: int = 6
    quantity_per_trade: int = 50
    maximum_trades_per_day: int = 5
    maximum_concurrent: int = 2
    evidence_minimum_trades: int = 200
    evidence_minimum_symbols: int = 50
    evidence_minimum_profit_factor: float = 1.20

    def validate(self) -> NseSignalEngineV2Config:
        start = datetime_time.fromisoformat(self.entry_start_time)
        last = datetime_time.fromisoformat(self.last_entry_time)
        square_off = datetime_time.fromisoformat(self.square_off_time)
        if not start < last < square_off:
            raise ValueError("Entry and square-off times must be ordered")
        if not 0 <= self.minimum_breadth_pct <= 100:
            raise ValueError("minimum_breadth_pct must be within 0-100")
        if self.minimum_rvol <= 0 or self.breakout_rvol < self.minimum_rvol:
            raise ValueError("RVOL thresholds are invalid")
        if not 0 < self.minimum_stop_pct < self.maximum_stop_pct:
            raise ValueError("Stop percentage bounds are invalid")
        if self.reward_risk <= 0 or self.quantity_per_trade < 1:
            raise ValueError("Reward:risk and quantity must be positive")
        if self.maximum_trades_per_day < 1 or self.maximum_concurrent < 1:
            raise ValueError("Paper risk controls must be positive")
        return self

    def public(self) -> dict[str, Any]:
        payload = asdict(self)
        return {
            "entryStartTime": payload["entry_start_time"],
            "lastEntryTime": payload["last_entry_time"],
            "squareOffTime": payload["square_off_time"],
            "minimumBreadthPct": payload["minimum_breadth_pct"],
            "minimumRvol": payload["minimum_rvol"],
            "breakoutRvol": payload["breakout_rvol"],
            "minimumRelativeNiftyPct": payload["minimum_relative_nifty_pct"],
            "rewardRisk": payload["reward_risk"],
            "quantityPerTrade": payload["quantity_per_trade"],
            "maximumTradesPerDay": payload["maximum_trades_per_day"],
            "maximumConcurrent": payload["maximum_concurrent"],
            "maximumHoldingBars": payload["maximum_holding_bars"],
            "stagnationBars": payload["stagnation_bars"],
            "evidenceMinimumTrades": payload["evidence_minimum_trades"],
            "evidenceMinimumSymbols": payload["evidence_minimum_symbols"],
            "evidenceMinimumProfitFactor": payload["evidence_minimum_profit_factor"],
            "executionModel": "STOP_ENTRY_AFTER_COMPLETED_CANDLE",
        }


@dataclass(frozen=True)
class HistoricalEvidence:
    status: str = "UNVALIDATED"
    sample_size: int = 0
    distinct_symbols: int = 0
    target_hit_probability: float | None = None
    stop_hit_probability: float | None = None
    timeout_probability: float | None = None
    expected_net_return_pct: float | None = None
    expected_net_r: float | None = None
    profit_factor: float | None = None
    confidence_lower_net_r: float | None = None
    confidence_upper_net_r: float | None = None
    stress_expected_net_r: float | None = None
    maximum_drawdown_r: float | None = None
    tested_from: str | None = None
    tested_to: str | None = None
    evidence_version: str | None = None

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any] | None) -> HistoricalEvidence:
        if not payload:
            return cls()
        allowed = {field.name for field in cls.__dataclass_fields__.values()}
        normalized = {
            key: value
            for key, value in payload.items()
            if key in allowed
        }
        return cls(**normalized)

    def passes(self, config: NseSignalEngineV2Config) -> bool:
        return bool(
            self.status == "WALK_FORWARD_VALIDATED"
            and self.sample_size >= config.evidence_minimum_trades
            and self.distinct_symbols >= config.evidence_minimum_symbols
            and self.expected_net_r is not None
            and self.expected_net_r > 0
            and self.profit_factor is not None
            and self.profit_factor >= config.evidence_minimum_profit_factor
            and self.confidence_lower_net_r is not None
            and self.confidence_lower_net_r > 0
            and self.stress_expected_net_r is not None
            and self.stress_expected_net_r > 0
        )

    def public(self, config: NseSignalEngineV2Config) -> dict[str, Any]:
        return {
            "status": self.status,
            "passesQualificationGate": self.passes(config),
            "sampleSize": self.sample_size,
            "distinctSymbols": self.distinct_symbols,
            "targetHitProbability": self.target_hit_probability,
            "stopHitProbability": self.stop_hit_probability,
            "timeoutProbability": self.timeout_probability,
            "expectedNetReturnPct": self.expected_net_return_pct,
            "expectedNetR": self.expected_net_r,
            "profitFactor": self.profit_factor,
            "confidenceLowerNetR": self.confidence_lower_net_r,
            "confidenceUpperNetR": self.confidence_upper_net_r,
            "stressExpectedNetR": self.stress_expected_net_r,
            "maximumDrawdownR": self.maximum_drawdown_r,
            "testedFrom": self.tested_from,
            "testedTo": self.tested_to,
            "evidenceVersion": self.evidence_version,
            "message": (
                "Walk-forward evidence gate passed."
                if self.passes(config)
                else "No qualifying V2 walk-forward evidence is available; probability and expectancy are not claimed."
            ),
        }


def _finite(value: Any, digits: int = 6) -> float | None:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    return round(numeric, digits) if math.isfinite(numeric) else None


def _as_ist(value: object) -> pd.Timestamp:
    stamp = pd.Timestamp(value)
    return stamp.tz_localize(IST) if stamp.tzinfo is None else stamp.tz_convert(IST)


def _slice(frame: pd.DataFrame | None, as_of: pd.Timestamp) -> pd.DataFrame:
    if frame is None or frame.empty:
        return pd.DataFrame()
    data = frame.copy()
    index = pd.DatetimeIndex(data.index)
    data.index = index.tz_localize(IST) if index.tz is None else index.tz_convert(IST)
    return data.loc[data.index <= as_of].sort_index()


def _rule(
    code: str,
    label: str,
    passed: bool,
    *,
    actual: Any = None,
    threshold: Any = None,
    required: bool = True,
    unavailable: bool = False,
) -> dict[str, Any]:
    return {
        "code": code,
        "label": label,
        "passed": bool(passed),
        "required": required,
        "unavailable": unavailable,
        "actual": actual,
        "threshold": threshold,
    }


def _required_failures(rules: Sequence[Mapping[str, Any]]) -> list[str]:
    return [str(rule["code"]) for rule in rules if bool(rule.get("required")) and not bool(rule.get("passed"))]


def _breadth(
    feature_frames: Mapping[str, pd.DataFrame], as_of: pd.Timestamp
) -> tuple[float | None, int]:
    bullish = 0
    observed = 0
    for frame in feature_frames.values():
        data = _slice(frame, as_of)
        if data.empty:
            continue
        row = data.iloc[-1]
        close = _finite(row.get("Close"))
        fast = _finite(row.get("EMAFast"))
        slow = _finite(row.get("EMASlow"))
        vwap = _finite(row.get("SessionVWAP"))
        if None in {close, fast, slow, vwap}:
            continue
        observed += 1
        bullish += int(close >= vwap and fast > slow)
    return (round(bullish * 100.0 / observed, 4), observed) if observed else (None, 0)


def build_market_context(
    feature_frames: Mapping[str, pd.DataFrame],
    nifty_frame: pd.DataFrame | None,
    as_of: pd.Timestamp,
) -> dict[str, Any]:
    breadth_pct, breadth_symbols = _breadth(feature_frames, as_of)
    nifty = _slice(nifty_frame, as_of)
    nifty_available = not nifty.empty
    nifty_fresh = False
    nifty_bullish = False
    nifty_payload: dict[str, Any] = {}
    if nifty_available:
        nifty_timestamp = _as_ist(nifty.index[-1])
        nifty_fresh = nifty_timestamp == as_of
        row = nifty.iloc[-1]
        close = _finite(row.get("Close"))
        fast = _finite(row.get("EMAFast"))
        slow = _finite(row.get("EMASlow"))
        vwap = _finite(row.get("SessionVWAP"))
        nifty_available = None not in {close, fast, slow, vwap}
        if nifty_available:
            nifty_bullish = bool(close >= vwap and fast >= slow)
            nifty_payload = {
                "close": close,
                "emaFast": fast,
                "emaSlow": slow,
                "sessionVwap": vwap,
                "timestamp": nifty_timestamp.isoformat(),
            }
    return {
        "niftyAvailable": nifty_available,
        "niftyFresh": nifty_fresh,
        "niftyNotBearish": nifty_bullish,
        "nifty": nifty_payload,
        "breadthPct": breadth_pct,
        "breadthSymbols": breadth_symbols,
    }


def _common_rules(
    data: pd.DataFrame,
    activity: Mapping[str, Any],
    context: Mapping[str, Any],
    as_of: pd.Timestamp,
    config: NseSignalEngineV2Config,
) -> list[dict[str, Any]]:
    clock = as_of.time().replace(tzinfo=None)
    start = datetime_time.fromisoformat(config.entry_start_time)
    end = datetime_time.fromisoformat(config.last_entry_time)
    relative_nifty = _finite(activity.get("relativeToNiftyPct"))
    breadth = _finite(context.get("breadthPct"))
    symbol_timestamp = _as_ist(data.index[-1]) if not data.empty else None
    return [
        _rule(
            "SYMBOL_CANDLE_STALE",
            "The symbol has a completed candle at the evaluation timestamp",
            symbol_timestamp == as_of,
            actual=symbol_timestamp.isoformat() if symbol_timestamp is not None else None,
            threshold=as_of.isoformat(),
        ),
        _rule(
            "ENTRY_WINDOW_FAILED",
            "Signal candle is inside the permitted entry window",
            start <= clock <= end,
            actual=clock.strftime("%H:%M"),
            threshold=f"{config.entry_start_time}-{config.last_entry_time}",
        ),
        _rule(
            "MARKET_CONTEXT_UNAVAILABLE",
            "Completed NIFTY context is available",
            bool(context.get("niftyAvailable")),
            actual=context.get("niftyAvailable"),
            threshold=True,
        ),
        _rule(
            "NIFTY_BEARISH",
            "NIFTY is not below both VWAP and its fast/slow trend",
            bool(context.get("niftyNotBearish")),
            actual=context.get("nifty"),
            threshold="close >= VWAP and EMA fast >= EMA slow",
        ),
        _rule(
            "MARKET_CONTEXT_STALE",
            "NIFTY context is synchronized to the signal candle",
            bool(context.get("niftyFresh")),
            actual=(context.get("nifty") or {}).get("timestamp"),
            threshold=as_of.isoformat(),
        ),
        _rule(
            "BREADTH_FAILED",
            "Eligible-universe breadth is supportive",
            breadth is not None and breadth >= config.minimum_breadth_pct,
            actual=breadth,
            threshold=f">= {config.minimum_breadth_pct}%",
        ),
        _rule(
            "RELATIVE_NIFTY_FAILED",
            "Stock is outperforming NIFTY over the scanner horizon",
            relative_nifty is not None and relative_nifty > config.minimum_relative_nifty_pct,
            actual=relative_nifty,
            threshold=f"> {config.minimum_relative_nifty_pct}%",
        ),
        _rule(
            "SECTOR_CONTEXT_UNAVAILABLE" if _finite(activity.get("relativeToSectorPct")) is None else "RELATIVE_SECTOR_FAILED",
            "Audited sector-relative strength is positive when available",
            _finite(activity.get("relativeToSectorPct")) is None or float(activity["relativeToSectorPct"]) > 0,
            actual=_finite(activity.get("relativeToSectorPct")),
            threshold="> 0% when supported",
            required=False,
            unavailable=_finite(activity.get("relativeToSectorPct")) is None,
        ),
    ]


def _trigger_shape(row: pd.Series, previous: pd.Series, atr: float) -> tuple[bool, dict[str, float | None]]:
    open_price = float(row["Open"])
    high = float(row["High"])
    low = float(row["Low"])
    close = float(row["Close"])
    candle_range = max(high - low, 0.0)
    body_fraction = (close - open_price) / candle_range if candle_range > 0 else 0.0
    close_location = (close - low) / candle_range if candle_range > 0 else 0.0
    passed = bool(
        close > open_price
        and close > float(previous["High"])
        and body_fraction >= 0.50
        and close_location >= 0.70
        and candle_range <= atr * 1.50
    )
    return passed, {
        "bodyFraction": _finite(body_fraction),
        "closeLocation": _finite(close_location),
        "rangeAtr": _finite(candle_range / atr if atr > 0 else None),
    }


class TrendPullbackContinuationDetector:
    key = TREND_PULLBACK_KEY
    name = "Trend Pullback Continuation V2"
    version = "2.0.0"

    def evaluate(
        self,
        frame: pd.DataFrame,
        activity: Mapping[str, Any],
        context: Mapping[str, Any],
        as_of: pd.Timestamp,
        config: NseSignalEngineV2Config,
    ) -> dict[str, Any]:
        data = _slice(frame, as_of)
        required = {"Open", "High", "Low", "Close", "Volume", "RSI", "EMAFast", "EMASlow", "ATR", "SessionVWAP", "RVOL", "AverageTradedValue"}
        if len(data) < 24 or not required.issubset(data.columns):
            return {
                "ready": False,
                "reasons": ["INSUFFICIENT_WARMUP"],
                "rules": [_rule("INSUFFICIENT_WARMUP", "Required completed candles and features are available", False, actual=len(data), threshold=24)],
            }
        row = data.iloc[-1]
        previous = data.iloc[-2]
        atr = float(row["ATR"])
        fast = float(row["EMAFast"])
        slow = float(row["EMASlow"])
        close = float(row["Close"])
        vwap = float(row["SessionVWAP"])
        rsi = float(row["RSI"])
        rvol = float(row["RVOL"])
        slope_bars = min(3, len(data) - 1)
        trend_passed = bool(
            fast > slow
            and fast > float(data["EMAFast"].iloc[-1 - slope_bars])
            and slow >= float(data["EMASlow"].iloc[-1 - slope_bars])
            and close >= fast
            and close >= vwap
        )
        armed_rows = data.iloc[-1 - config.pullback_lookback_bars:-1]
        pullback_reference = None
        arm_row: pd.Series | None = None
        arm_timestamp: pd.Timestamp | None = None
        for timestamp, candidate in armed_rows.iloc[::-1].iterrows():
            candidate_atr = float(candidate["ATR"])
            references = {
                "VWAP": float(candidate["SessionVWAP"]),
                "EMA9": float(candidate["EMAFast"]),
                "EMA20": float(candidate["EMASlow"]),
            }
            nearest_name, nearest_value = min(
                references.items(), key=lambda item: abs(float(candidate["Low"]) - item[1])
            )
            near_reference = abs(float(candidate["Low"]) - nearest_value) <= candidate_atr * 0.25
            controlled = 38.0 <= float(candidate["RSI"]) <= 52.0 and float(candidate["Close"]) >= float(candidate["EMASlow"]) - candidate_atr * 0.25
            if near_reference and controlled:
                pullback_reference = nearest_name
                arm_row = candidate
                arm_timestamp = _as_ist(timestamp)
                break
        trigger_passed, trigger_details = _trigger_shape(row, previous, atr)
        rules = [
            *_common_rules(data, activity, context, as_of, config),
            _rule("UPTREND_FAILED", "EMA/VWAP trend is aligned and rising", trend_passed, actual={"close": _finite(close), "emaFast": _finite(fast), "emaSlow": _finite(slow), "vwap": _finite(vwap)}, threshold="close >= VWAP and EMA9 > rising EMA20"),
            _rule("PULLBACK_NOT_ARMED", "A controlled pullback touched VWAP, EMA9 or EMA20", arm_row is not None, actual={"reference": pullback_reference, "timestamp": arm_timestamp.isoformat() if arm_timestamp is not None else None}, threshold=f"within 0.25 ATR during previous {config.pullback_lookback_bars} bars"),
            _rule("TRIGGER_CANDLE_FAILED", "Bullish confirmation closed above the previous high", trigger_passed, actual=trigger_details, threshold="body >= 50%, close location >= 70%, range <= 1.5 ATR"),
            _rule("MOMENTUM_CONFIRMATION_FAILED", "RSI confirms recovery without being extended", 45.0 <= rsi <= 65.0, actual=_finite(rsi), threshold="45-65"),
            _rule("RVOL_FAILED", "Trigger participation meets the minimum RVOL", rvol >= config.minimum_rvol, actual=_finite(rvol), threshold=f">= {config.minimum_rvol}"),
        ]
        reasons = _required_failures(rules)
        structural_low = min(float(arm_row["Low"]) if arm_row is not None else float(row["Low"]), float(row["Low"]))
        return {
            "ready": not reasons,
            "reasons": reasons,
            "rules": rules,
            "whyBuy": [
                "The stock is outperforming NIFTY while the market context is not bearish.",
                f"The uptrend pulled back toward {pullback_reference or 'trend support'} without breaking structure.",
                "A completed bullish candle confirmed continuation with sufficient participation.",
            ],
            "trigger": {
                "signalTimestamp": as_of.isoformat(),
                "triggerHigh": _finite(row["High"]),
                "triggerLow": _finite(row["Low"]),
                "structuralLow": _finite(structural_low),
                "atr": _finite(atr),
                "rsi": _finite(rsi),
                "rvol": _finite(rvol),
                "pullbackReference": pullback_reference,
                "armTimestamp": arm_timestamp.isoformat() if arm_timestamp is not None else None,
            },
        }


class BreakoutRetestDetector:
    key = BREAKOUT_RETEST_KEY
    name = "Breakout and Retest V2"
    version = "2.0.0"

    def evaluate(
        self,
        frame: pd.DataFrame,
        activity: Mapping[str, Any],
        context: Mapping[str, Any],
        as_of: pd.Timestamp,
        config: NseSignalEngineV2Config,
    ) -> dict[str, Any]:
        data = _slice(frame, as_of)
        required = {"Open", "High", "Low", "Close", "Volume", "RSI", "EMAFast", "EMASlow", "ATR", "SessionVWAP", "RVOL", "AverageTradedValue"}
        if len(data) < config.breakout_lookback_bars + config.breakout_retest_bars + 2 or not required.issubset(data.columns):
            return {
                "ready": False,
                "reasons": ["INSUFFICIENT_WARMUP"],
                "rules": [_rule("INSUFFICIENT_WARMUP", "Required completed candles and features are available", False, actual=len(data), threshold=config.breakout_lookback_bars + config.breakout_retest_bars + 2)],
            }
        today = data[pd.DatetimeIndex(data.index).date == as_of.date()]
        opening = today.iloc[:config.opening_range_bars]
        opening_ready = len(opening) == config.opening_range_bars
        opening_high = float(opening["High"].max()) if opening_ready else math.nan
        current = data.iloc[-1]
        atr = float(current["ATR"])
        breakout_row: pd.Series | None = None
        breakout_timestamp: pd.Timestamp | None = None
        breakout_level: float | None = None
        breakout_rvol: float | None = None
        search_start = max(1, len(data) - config.breakout_retest_bars - 1)
        for position in range(len(data) - 2, search_start - 1, -1):
            prior = data.iloc[max(0, position - config.breakout_lookback_bars):position]
            if prior.empty or not opening_ready:
                continue
            swing_high = float(prior["High"].max())
            level = max(opening_high, swing_high)
            candidate = data.iloc[position]
            if float(candidate["Close"]) > level and float(candidate["RVOL"]) >= config.breakout_rvol:
                breakout_row = candidate
                breakout_timestamp = _as_ist(data.index[position])
                breakout_level = level
                breakout_rvol = float(candidate["RVOL"])
                break
        retest_passed = False
        retest_details: dict[str, Any] = {}
        if breakout_row is not None and breakout_level is not None:
            low = float(current["Low"])
            close = float(current["Close"])
            candle_range = max(float(current["High"]) - low, 0.0)
            close_location = (close - low) / candle_range if candle_range > 0 else 0.0
            retest_passed = bool(
                low <= breakout_level + atr * 0.15
                and close >= breakout_level - atr * 0.05
                and close > float(current["Open"])
                and close_location >= 0.60
                and close - breakout_level <= atr * 0.50
            )
            retest_details = {
                "level": _finite(breakout_level),
                "low": _finite(low),
                "close": _finite(close),
                "closeLocation": _finite(close_location),
                "distanceAtr": _finite((close - breakout_level) / atr if atr > 0 else None),
            }
        trend_support = bool(
            float(current["Close"]) >= float(current["SessionVWAP"])
            and float(current["EMAFast"]) >= float(current["EMASlow"])
        )
        rules = [
            *_common_rules(data, activity, context, as_of, config),
            _rule("OPENING_RANGE_INCOMPLETE", "The opening range is complete", opening_ready, actual=len(opening), threshold=config.opening_range_bars),
            _rule("BREAKOUT_NOT_CONFIRMED", "A completed candle broke prior structure with high RVOL", breakout_row is not None, actual={"timestamp": breakout_timestamp.isoformat() if breakout_timestamp is not None else None, "rvol": _finite(breakout_rvol)}, threshold=f"close > opening/prior high and RVOL >= {config.breakout_rvol}"),
            _rule("RETEST_FAILED", "The broken level was retested and held without chasing", retest_passed, actual=retest_details, threshold="touch within 0.15 ATR, hold within 0.05 ATR, close <= 0.50 ATR above"),
            _rule("TREND_SUPPORT_FAILED", "Price remains above VWAP with EMA9 not below EMA20", trend_support, actual={"close": _finite(current["Close"]), "vwap": _finite(current["SessionVWAP"]), "emaFast": _finite(current["EMAFast"]), "emaSlow": _finite(current["EMASlow"])}, threshold="close >= VWAP and EMA9 >= EMA20"),
        ]
        reasons = _required_failures(rules)
        return {
            "ready": not reasons,
            "reasons": reasons,
            "rules": rules,
            "whyBuy": [
                "Price broke a completed opening/prior range with unusually strong participation.",
                "The system waited for the broken level to be retested instead of chasing the breakout.",
                "The retest held above VWAP while the stock continued to outperform NIFTY.",
            ],
            "trigger": {
                "signalTimestamp": as_of.isoformat(),
                "triggerHigh": _finite(current["High"]),
                "triggerLow": _finite(current["Low"]),
                "structuralLow": _finite(min(float(current["Low"]), breakout_level if breakout_level is not None else float(current["Low"]))),
                "breakoutLevel": _finite(breakout_level),
                "breakoutTimestamp": breakout_timestamp.isoformat() if breakout_timestamp is not None else None,
                "atr": _finite(atr),
                "rsi": _finite(current["RSI"]),
                "rvol": _finite(current["RVOL"]),
                "breakoutRvol": _finite(breakout_rvol),
            },
        }


DETECTORS = (TrendPullbackContinuationDetector(), BreakoutRetestDetector())


def build_trade_plan(
    detector: Any,
    evaluation: Mapping[str, Any],
    frame: pd.DataFrame,
    as_of: pd.Timestamp,
    config: NseSignalEngineV2Config,
) -> dict[str, Any]:
    trigger = dict(evaluation.get("trigger") or {})
    data = _slice(frame, as_of)
    row = data.iloc[-1]
    atr = float(trigger["atr"])
    entry_min = float(trigger["triggerHigh"])
    entry_max = entry_min + atr * config.entry_buffer_atr
    structural_stop = float(trigger["structuralLow"]) - atr * config.stop_buffer_atr
    risk = entry_min - structural_stop
    risk_pct = risk * 100.0 / entry_min if entry_min > 0 else math.inf
    target = entry_min + risk * config.reward_risk
    prior = data.iloc[max(0, len(data) - 51):-1]
    prior_high = float(prior["High"].max()) if not prior.empty else entry_min
    known_resistance = prior_high if prior_high > entry_min else None
    room_r = (known_resistance - entry_min) / risk if known_resistance is not None and risk > 0 else None
    plan_rules = [
        _rule("INVALID_RISK", "Structural stop creates positive risk", risk > 0, actual=_finite(risk), threshold="> 0"),
        _rule("STOP_TOO_NARROW", "Stop is outside normal five-minute noise", risk_pct >= config.minimum_stop_pct, actual=_finite(risk_pct), threshold=f">= {config.minimum_stop_pct}%"),
        _rule("STOP_TOO_WIDE", "Stop remains within the maximum permitted loss distance", risk_pct <= config.maximum_stop_pct, actual=_finite(risk_pct), threshold=f"<= {config.maximum_stop_pct}%"),
        _rule("INSUFFICIENT_ROOM", "Known resistance leaves at least the required reward", room_r is None or room_r >= config.reward_risk, actual=_finite(room_r), threshold=f">= {config.reward_risk}R or no known overhead resistance"),
    ]
    sell_conditions = [
        {"type": "TARGET", "price": _finite(target), "condition": f"Price reaches {config.reward_risk}R"},
        {"type": "STOP", "price": _finite(structural_stop), "condition": "Price reaches the frozen structural stop"},
        {"type": "SETUP_INVALIDATION", "price": None, "condition": "Trend setup: completed close below both VWAP and EMA20; breakout setup: completed close back inside the broken range"},
        {"type": "STAGNATION_TIMEOUT", "price": None, "condition": f"No meaningful progress after {config.stagnation_bars} completed five-minute candles"},
        {"type": "MAXIMUM_TIMEOUT", "price": None, "condition": f"Exit after {config.maximum_holding_bars} candles if still open"},
        {"type": "SESSION_EXIT", "price": None, "condition": f"Exit remaining paper position by {config.square_off_time} IST"},
    ]
    return {
        "planReady": not _required_failures(plan_rules),
        "planRejectionReasons": _required_failures(plan_rules),
        "planRules": plan_rules,
        "signalClose": _finite(row["Close"]),
        "entryRange": {"minimum": _finite(entry_min), "maximum": _finite(entry_max)},
        "estimatedEntry": _finite(entry_min),
        "estimatedStop": _finite(structural_stop),
        "estimatedTarget": _finite(target),
        "riskPerShare": _finite(risk),
        "riskPct": _finite(risk_pct),
        "rewardRisk": config.reward_risk,
        "knownResistance": _finite(known_resistance),
        "roomToResistanceR": _finite(room_r),
        "entryCondition": "After the completed signal candle, buy only if price trades through the trigger high inside the entry range; cancel after two candles or on a gap above the range.",
        "sellConditions": sell_conditions,
    }
