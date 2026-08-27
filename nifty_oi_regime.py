from __future__ import annotations

import copy
from bisect import bisect_right
import json
import math
import os
import threading
from dataclasses import asdict, dataclass
from datetime import date, datetime, time as clock_time, timedelta
from pathlib import Path
from statistics import median
from typing import Any, Iterable, Literal, Mapping, Sequence
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd


IST = ZoneInfo("Asia/Kolkata")
OI_SCHEMA_VERSION = "nifty-oi-1.0.0"
OI_FILTER_VERSION = "nifty-oi-regime-filter-1.0.0"

OiFilterMode = Literal["OFF", "ADVISORY", "ENFORCED"]
RegimeName = Literal[
    "STRONGLY_BEARISH",
    "BEARISH",
    "NEUTRAL",
    "BULLISH",
    "STRONGLY_BULLISH",
    "VOLATILITY_EXPANSION",
    "INSUFFICIENT_OI_DATA",
]
BuildUp = Literal[
    "LONG_BUILDUP",
    "SHORT_BUILDUP",
    "SHORT_COVERING",
    "LONG_UNWINDING",
    "NEUTRAL",
]


def _as_ist(value: datetime | pd.Timestamp | str) -> datetime:
    stamp = pd.Timestamp(value)
    if stamp.tzinfo is None:
        stamp = stamp.tz_localize(IST)
    else:
        stamp = stamp.tz_convert(IST)
    return stamp.to_pydatetime()


def _finite(value: Any, digits: int = 6) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return round(number, digits)


def _change_pct(current: float, previous: float) -> float | None:
    if not math.isfinite(current) or not math.isfinite(previous) or previous == 0:
        return None
    return (current - previous) / abs(previous) * 100.0


def _clamp(value: float, lower: float, upper: float) -> float:
    return min(upper, max(lower, value))


@dataclass(frozen=True)
class NiftyOiConfig:
    lookback_bars: int = 3
    strikes_each_side: int = 5
    minimum_price_change_pct: float = 0.05
    minimum_oi_change_pct: float = 0.50
    maximum_spread_pct: float = 20.0
    stale_data_seconds: int = 360
    completed_bar_seconds: int = 300
    expiry_rollover_hour: int = 15
    expiry_rollover_minute: int = 20
    minimum_valid_contract_fraction: float = 0.50
    minimum_futures_volume: float = 1.0
    minimum_component_coverage: float = 0.65
    options_weight: float = 0.35
    futures_weight: float = 0.35
    spot_weight: float = 0.30
    strongly_bearish_threshold: float = -60.0
    bearish_threshold: float = -20.0
    bullish_threshold: float = 20.0
    strongly_bullish_threshold: float = 60.0
    elevated_quality_threshold: float = 95.0
    volatility_price_rise_pct: float = 0.25
    volatility_iv_rise: float = 0.50
    fail_policy: Literal["SKIP", "ALLOW"] = "SKIP"

    def validate(self) -> NiftyOiConfig:
        if self.lookback_bars < 1:
            raise ValueError("OI lookback must be at least one completed bar")
        if not 0 <= self.strikes_each_side <= 20:
            raise ValueError("ATM strike wings must be between 0 and 20")
        if self.minimum_price_change_pct < 0 or self.minimum_oi_change_pct < 0:
            raise ValueError("OI classification thresholds cannot be negative")
        if self.maximum_spread_pct <= 0:
            raise ValueError("Maximum option spread must be positive")
        if self.stale_data_seconds < 1:
            raise ValueError("OI stale-data tolerance must be positive")
        if self.completed_bar_seconds < 60:
            raise ValueError("OI completed-bar duration must be at least 60 seconds")
        if not 0 <= self.expiry_rollover_hour <= 23 or not 0 <= self.expiry_rollover_minute <= 59:
            raise ValueError("OI expiry rollover time is invalid")
        if not 0 < self.minimum_valid_contract_fraction <= 1:
            raise ValueError("Minimum valid option-contract fraction must be in (0, 1]")
        if self.minimum_futures_volume < 0:
            raise ValueError("Minimum futures volume cannot be negative")
        if not 0 < self.minimum_component_coverage <= 1:
            raise ValueError("Minimum OI component coverage must be in (0, 1]")
        weights = (self.options_weight, self.futures_weight, self.spot_weight)
        if any(weight < 0 for weight in weights) or sum(weights) <= 0:
            raise ValueError("OI component weights must be non-negative and sum above zero")
        if not (
            -100 <= self.strongly_bearish_threshold < self.bearish_threshold
            < self.bullish_threshold < self.strongly_bullish_threshold <= 100
        ):
            raise ValueError("OI regime thresholds must be strictly ordered between -100 and 100")
        if not 0 <= self.elevated_quality_threshold <= 100:
            raise ValueError("Elevated stock-quality threshold must be between 0 and 100")
        if self.volatility_price_rise_pct < 0 or self.volatility_iv_rise < 0:
            raise ValueError("Volatility-expansion thresholds cannot be negative")
        if self.fail_policy not in {"SKIP", "ALLOW"}:
            raise ValueError("OI fail policy must be SKIP or ALLOW")
        return self

    def public(self) -> dict[str, Any]:
        return {
            "schemaVersion": OI_SCHEMA_VERSION,
            "lookbackBars": self.lookback_bars,
            "strikesEachSide": self.strikes_each_side,
            "minimumPriceChangePct": self.minimum_price_change_pct,
            "minimumOiChangePct": self.minimum_oi_change_pct,
            "maximumSpreadPct": self.maximum_spread_pct,
            "staleDataSeconds": self.stale_data_seconds,
            "completedBarSeconds": self.completed_bar_seconds,
            "expiryRolloverTime": f"{self.expiry_rollover_hour:02d}:{self.expiry_rollover_minute:02d}",
            "minimumValidContractFraction": self.minimum_valid_contract_fraction,
            "minimumFuturesVolume": self.minimum_futures_volume,
            "minimumComponentCoverage": self.minimum_component_coverage,
            "weights": {
                "options": self.options_weight,
                "futures": self.futures_weight,
                "spot": self.spot_weight,
            },
            "thresholds": {
                "stronglyBearish": self.strongly_bearish_threshold,
                "bearish": self.bearish_threshold,
                "bullish": self.bullish_threshold,
                "stronglyBullish": self.strongly_bullish_threshold,
            },
            "elevatedQualityThreshold": self.elevated_quality_threshold,
            "volatilityPriceRisePct": self.volatility_price_rise_pct,
            "volatilityIvRise": self.volatility_iv_rise,
            "failPolicy": self.fail_policy,
        }


@dataclass(frozen=True)
class OptionOiObservation:
    timestamp: datetime
    underlying: str
    expiry: date
    strike: float
    option_type: Literal["CALL", "PUT"]
    security_id: str
    ltp: float
    previous_ltp: float | None
    open_interest: float
    previous_open_interest: float | None
    oi_change: float | None
    oi_change_pct: float | None
    volume: float
    implied_volatility: float | None
    bid: float
    ask: float
    spot_price: float
    distance_from_atm: int
    data_source: str
    ingestion_timestamp: datetime
    delta: float | None = None
    gamma: float | None = None
    theta: float | None = None
    vega: float | None = None
    source_timestamp: datetime | None = None
    open_price: float | None = None
    high_price: float | None = None
    low_price: float | None = None
    close_price: float | None = None

    @property
    def key(self) -> tuple[date, float, str]:
        return (self.expiry, float(self.strike), self.option_type)

    def public(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["timestamp"] = _as_ist(self.timestamp).isoformat()
        payload["expiry"] = self.expiry.isoformat()
        payload["ingestion_timestamp"] = _as_ist(self.ingestion_timestamp).isoformat()
        payload["source_timestamp"] = _as_ist(self.source_timestamp or self.timestamp).isoformat()
        payload["schema_version"] = OI_SCHEMA_VERSION
        return payload

    @classmethod
    def from_public(cls, payload: Mapping[str, Any]) -> OptionOiObservation:
        values = dict(payload)
        values.pop("schema_version", None)
        values["timestamp"] = _as_ist(values["timestamp"])
        values["expiry"] = date.fromisoformat(str(values["expiry"]))
        values["ingestion_timestamp"] = _as_ist(values["ingestion_timestamp"])
        if values.get("source_timestamp"):
            values["source_timestamp"] = _as_ist(values["source_timestamp"])
        return cls(**values)


@dataclass(frozen=True)
class FuturesOiObservation:
    timestamp: datetime
    expiry: date
    security_id: str
    futures_price: float
    previous_price: float | None
    open_interest: float
    previous_open_interest: float | None
    price_change_pct: float | None
    oi_change_pct: float | None
    volume: float
    spot_price: float
    basis: float
    data_source: str
    ingestion_timestamp: datetime
    source_timestamp: datetime | None = None

    def public(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["timestamp"] = _as_ist(self.timestamp).isoformat()
        payload["expiry"] = self.expiry.isoformat()
        payload["ingestion_timestamp"] = _as_ist(self.ingestion_timestamp).isoformat()
        payload["source_timestamp"] = _as_ist(self.source_timestamp or self.timestamp).isoformat()
        payload["schema_version"] = OI_SCHEMA_VERSION
        return payload

    @classmethod
    def from_public(cls, payload: Mapping[str, Any]) -> FuturesOiObservation:
        values = dict(payload)
        values.pop("schema_version", None)
        values["timestamp"] = _as_ist(values["timestamp"])
        values["expiry"] = date.fromisoformat(str(values["expiry"]))
        values["ingestion_timestamp"] = _as_ist(values["ingestion_timestamp"])
        if values.get("source_timestamp"):
            values["source_timestamp"] = _as_ist(values["source_timestamp"])
        return cls(**values)


def _observation_source_time(observation: OptionOiObservation | FuturesOiObservation) -> datetime:
    return _as_ist(observation.source_timestamp or observation.timestamp)


def classify_buildup(
    price_change_pct: float | None,
    oi_change_pct: float | None,
    *,
    minimum_price_change_pct: float = 0.05,
    minimum_oi_change_pct: float = 0.50,
) -> BuildUp:
    if price_change_pct is None or oi_change_pct is None:
        return "NEUTRAL"
    if not math.isfinite(price_change_pct) or not math.isfinite(oi_change_pct):
        return "NEUTRAL"
    if abs(price_change_pct) < minimum_price_change_pct or abs(oi_change_pct) < minimum_oi_change_pct:
        return "NEUTRAL"
    if price_change_pct > 0 and oi_change_pct > 0:
        return "LONG_BUILDUP"
    if price_change_pct < 0 and oi_change_pct > 0:
        return "SHORT_BUILDUP"
    if price_change_pct > 0 and oi_change_pct < 0:
        return "SHORT_COVERING"
    if price_change_pct < 0 and oi_change_pct < 0:
        return "LONG_UNWINDING"
    return "NEUTRAL"


OPTION_DIRECTION: dict[str, dict[BuildUp, int]] = {
    "CALL": {
        "LONG_BUILDUP": 1,
        "SHORT_COVERING": 1,
        "SHORT_BUILDUP": -1,
        "LONG_UNWINDING": -1,
        "NEUTRAL": 0,
    },
    "PUT": {
        "LONG_BUILDUP": -1,
        "SHORT_COVERING": -1,
        "SHORT_BUILDUP": 1,
        "LONG_UNWINDING": 1,
        "NEUTRAL": 0,
    },
}


def option_direction(option_type: str, buildup: BuildUp) -> int:
    normalized = option_type.upper()
    if normalized not in OPTION_DIRECTION:
        raise ValueError("Option type must be CALL or PUT")
    return OPTION_DIRECTION[normalized][buildup]


def select_expiry(
    expiries: Iterable[date | str],
    evaluation_timestamp: datetime,
    *,
    rollover_time: clock_time | None = None,
) -> date | None:
    evaluation = _as_ist(evaluation_timestamp)
    session_date = evaluation.date()
    if rollover_time is not None and evaluation.timetz().replace(tzinfo=None) >= rollover_time:
        session_date += timedelta(days=1)
    parsed = sorted({value if isinstance(value, date) else date.fromisoformat(str(value)) for value in expiries})
    return next((expiry for expiry in parsed if expiry >= session_date), None)


def select_atm_strikes(strikes: Iterable[float], spot_price: float, strikes_each_side: int = 5) -> list[float]:
    ordered = sorted({float(value) for value in strikes if math.isfinite(float(value))})
    if not ordered or not math.isfinite(spot_price):
        return []
    atm_index = min(range(len(ordered)), key=lambda index: (abs(ordered[index] - spot_price), ordered[index]))
    start = max(0, atm_index - strikes_each_side)
    stop = min(len(ordered), atm_index + strikes_each_side + 1)
    return ordered[start:stop]


def _spread_pct(observation: OptionOiObservation) -> float:
    midpoint = (observation.bid + observation.ask) / 2.0
    if midpoint <= 0:
        return math.inf
    return (observation.ask - observation.bid) / midpoint * 100.0


def option_contract_is_eligible(
    observation: OptionOiObservation,
    evaluation_timestamp: datetime,
    config: NiftyOiConfig,
) -> bool:
    age = (_as_ist(evaluation_timestamp) - _observation_source_time(observation)).total_seconds()
    return bool(
        observation.open_interest >= 0
        and observation.volume > 0
        and observation.ltp > 0
        and observation.bid > 0
        and observation.ask >= observation.bid
        and _spread_pct(observation) <= config.maximum_spread_pct
        and 0 <= age <= config.stale_data_seconds
    )


def score_options(
    current: Sequence[OptionOiObservation],
    lookback: Sequence[OptionOiObservation],
    evaluation_timestamp: datetime,
    config: NiftyOiConfig,
) -> dict[str, Any]:
    config.validate()
    if not current or not lookback:
        return {"available": False, "reason": "Option OI lookback is unavailable"}
    evaluation = _as_ist(evaluation_timestamp)
    rollover = clock_time(config.expiry_rollover_hour, config.expiry_rollover_minute)
    first_expiry = select_expiry((item.expiry for item in current), evaluation, rollover_time=rollover)
    if first_expiry is None:
        return {"available": False, "reason": "No non-expired NIFTY option expiry is available"}
    candidate_expiries = sorted({item.expiry for item in current if item.expiry >= first_expiry})
    expiry: date | None = None
    spot = 0.0
    selected_strikes: list[float] = []
    matched_pairs: list[tuple[OptionOiObservation, OptionOiObservation]] = []
    expected = 0
    maximum_gap = config.completed_bar_seconds * (config.lookback_bars + 1) + config.stale_data_seconds
    for candidate_expiry in candidate_expiries:
        expiry_rows = [item for item in current if item.expiry == candidate_expiry]
        spot_values = [item.spot_price for item in expiry_rows if item.spot_price > 0]
        if not spot_values:
            continue
        candidate_spot = median(spot_values)
        candidate_strikes = select_atm_strikes(
            (item.strike for item in expiry_rows), candidate_spot, config.strikes_each_side
        )
        if not candidate_strikes:
            continue
        previous_by_key = {item.key: item for item in lookback if item.expiry == candidate_expiry}
        candidate_pairs: list[tuple[OptionOiObservation, OptionOiObservation]] = []
        for item in expiry_rows:
            if item.strike not in candidate_strikes or not option_contract_is_eligible(item, evaluation, config):
                continue
            previous = previous_by_key.get(item.key)
            if previous is None:
                continue
            gap = (_as_ist(item.timestamp) - _as_ist(previous.timestamp)).total_seconds()
            if gap <= 0 or gap > maximum_gap:
                continue
            candidate_pairs.append((item, previous))
        candidate_expected = max(1, len(candidate_strikes) * 2)
        if len(candidate_pairs) / candidate_expected < config.minimum_valid_contract_fraction:
            continue
        expiry = candidate_expiry
        spot = candidate_spot
        selected_strikes = candidate_strikes
        matched_pairs = candidate_pairs
        expected = candidate_expected
        break
    if expiry is None or not matched_pairs:
        return {"available": False, "reason": "No single liquid NIFTY expiry passed the option quality and lookback checks"}

    atm_index = min(
        range(len(selected_strikes)),
        key=lambda index: (abs(selected_strikes[index] - spot), selected_strikes[index]),
    )
    distance_by_strike = {strike: index - atm_index for index, strike in enumerate(selected_strikes)}
    weighted_score = 0.0
    total_weight = 0.0
    iv_changes: list[float] = []
    near_call_rises: list[float] = []
    near_put_rises: list[float] = []
    classifications: list[dict[str, Any]] = []
    strengths = {
        "putLongBuildupStrength": 0.0,
        "callLongBuildupStrength": 0.0,
        "putShortBuildupStrength": 0.0,
        "callShortBuildupStrength": 0.0,
    }
    current_call_oi = current_put_oi = previous_call_oi = previous_put_oi = 0.0
    source_timestamps: list[datetime] = []
    lookback_timestamps: list[datetime] = []
    for item, previous in matched_pairs:
        price_change = _change_pct(item.ltp, previous.ltp)
        oi_change = _change_pct(item.open_interest, previous.open_interest)
        buildup = classify_buildup(
            price_change,
            oi_change,
            minimum_price_change_pct=config.minimum_price_change_pct,
            minimum_oi_change_pct=config.minimum_oi_change_pct,
        )
        absolute_oi_change = abs(item.open_interest - previous.open_interest)
        distance = distance_by_strike[item.strike]
        distance_weight = 1.0 / (1.0 + abs(distance))
        spread_quality = _clamp(1.0 - _spread_pct(item) / config.maximum_spread_pct, 0.05, 1.0)
        liquidity_weight = max(1.0, math.sqrt(math.log1p(max(item.volume, 0.0))))
        oi_weight = max(1.0, math.log1p(absolute_oi_change))
        weight = distance_weight * spread_quality * liquidity_weight * oi_weight
        direction = option_direction(item.option_type, buildup)
        weighted_score += direction * weight
        total_weight += weight
        if item.implied_volatility is not None and previous.implied_volatility is not None:
            iv_changes.append(item.implied_volatility - previous.implied_volatility)
        if abs(distance) <= 1 and price_change is not None:
            (near_call_rises if item.option_type == "CALL" else near_put_rises).append(price_change)
        if item.option_type == "CALL":
            current_call_oi += item.open_interest
            previous_call_oi += previous.open_interest
            if buildup == "LONG_BUILDUP":
                strengths["callLongBuildupStrength"] += weight
            elif buildup == "SHORT_BUILDUP":
                strengths["callShortBuildupStrength"] += weight
        else:
            current_put_oi += item.open_interest
            previous_put_oi += previous.open_interest
            if buildup == "LONG_BUILDUP":
                strengths["putLongBuildupStrength"] += weight
            elif buildup == "SHORT_BUILDUP":
                strengths["putShortBuildupStrength"] += weight
        classifications.append({
            "securityId": item.security_id,
            "strike": item.strike,
            "optionType": item.option_type,
            "distanceFromAtm": distance,
            "priceChangePct": _finite(price_change),
            "oiChangePct": _finite(oi_change),
            "classification": buildup,
            "direction": direction,
            "weight": _finite(weight),
            "sourceTimestamp": _observation_source_time(item).isoformat(),
            "lookbackTimestamp": _observation_source_time(previous).isoformat(),
        })
        source_timestamps.append(_observation_source_time(item))
        lookback_timestamps.append(_observation_source_time(previous))
    matched = len(classifications)
    if total_weight <= 0 or matched == 0:
        return {"available": False, "reason": "No liquid matched option contracts passed OI quality checks"}
    score = _clamp(weighted_score / total_weight * 100.0, -100.0, 100.0)
    for key in strengths:
        strengths[key] = round(strengths[key] / total_weight * 100.0, 4)
    iv_change = median(iv_changes) if iv_changes else None
    iv_coverage = len(iv_changes) / matched if matched else 0.0
    volatility_expansion = bool(
        near_call_rises and near_put_rises and iv_change is not None
        and iv_coverage >= config.minimum_valid_contract_fraction
        and median(near_call_rises) >= config.volatility_price_rise_pct
        and median(near_put_rises) >= config.volatility_price_rise_pct
        and iv_change >= config.volatility_iv_rise
    )
    coverage = min(1.0, matched / expected)
    confidence = "LOW" if volatility_expansion or coverage < 0.60 else "HIGH" if coverage >= 0.85 else "MEDIUM"
    pcr_oi = current_put_oi / current_call_oi if current_call_oi > 0 else None
    previous_pcr = previous_put_oi / previous_call_oi if previous_call_oi > 0 else None
    pcr_change = (pcr_oi - previous_pcr) if pcr_oi is not None and previous_pcr is not None else None
    return {
        "available": True,
        "score": _finite(score),
        "confidence": confidence,
        "classification": "VOLATILITY_EXPANSION" if volatility_expansion else "DIRECTIONAL",
        "volatilityExpansion": volatility_expansion,
        "selectedExpiry": expiry.isoformat(),
        "selectedStrikes": selected_strikes,
        "spotPrice": _finite(spot, 4),
        "sourceTimestamp": max(source_timestamps).isoformat(),
        "oldestSourceTimestamp": min(source_timestamps + lookback_timestamps).isoformat(),
        "dataAgeSeconds": _finite(max(0.0, (evaluation - min(source_timestamps)).total_seconds()), 2),
        "coverage": _finite(coverage),
        "validContracts": matched,
        "expectedContracts": expected,
        "ivChange": _finite(iv_change),
        "ivCoverage": _finite(iv_coverage),
        "pcrOi": _finite(pcr_oi),
        "pcrOiChange": _finite(pcr_change),
        **strengths,
        "contracts": classifications,
    }


def score_futures(
    current: FuturesOiObservation | None,
    previous: FuturesOiObservation | None,
    evaluation_timestamp: datetime,
    config: NiftyOiConfig,
) -> dict[str, Any]:
    if current is None or previous is None:
        return {"available": False, "reason": "Futures OI lookback is unavailable"}
    if current.security_id != previous.security_id or current.expiry != previous.expiry:
        return {"available": False, "reason": "Futures contract rollover prevents cross-contract comparison"}
    evaluation = _as_ist(evaluation_timestamp)
    current_timestamp = _as_ist(current.timestamp)
    previous_timestamp = _as_ist(previous.timestamp)
    current_source_timestamp = _observation_source_time(current)
    age = (evaluation - current_source_timestamp).total_seconds()
    if age < 0 or age > config.stale_data_seconds:
        return {"available": False, "reason": "Futures OI snapshot is stale"}
    gap = (current_timestamp - previous_timestamp).total_seconds()
    maximum_gap = config.completed_bar_seconds * (config.lookback_bars + 1) + config.stale_data_seconds
    if gap <= 0 or gap > maximum_gap:
        return {"available": False, "reason": "Futures OI lookback has a gap too large for a completed-bar comparison"}
    if current.futures_price <= 0 or current.open_interest < 0 or current.volume < config.minimum_futures_volume:
        return {"available": False, "reason": "Futures quote failed price, OI, or volume quality checks"}
    price_change = _change_pct(current.futures_price, previous.futures_price)
    oi_change = _change_pct(current.open_interest, previous.open_interest)
    buildup = classify_buildup(
        price_change,
        oi_change,
        minimum_price_change_pct=config.minimum_price_change_pct,
        minimum_oi_change_pct=config.minimum_oi_change_pct,
    )
    direction = 1 if buildup in {"LONG_BUILDUP", "SHORT_COVERING"} else -1 if buildup in {"SHORT_BUILDUP", "LONG_UNWINDING"} else 0
    strength = min(100.0, (abs(price_change or 0.0) / max(config.minimum_price_change_pct, 0.01) + abs(oi_change or 0.0) / max(config.minimum_oi_change_pct, 0.01)) * 12.5)
    return {
        "available": True,
        "score": _finite(direction * strength),
        "regime": buildup,
        "confidence": "HIGH" if strength >= 50 else "MEDIUM" if strength >= 20 else "LOW",
        "priceChangePct": _finite(price_change),
        "oiChangePct": _finite(oi_change),
        "expiry": current.expiry.isoformat(),
        "securityId": current.security_id,
        "basis": _finite(current.basis),
        "sourceTimestamp": current_source_timestamp.isoformat(),
        "lookbackTimestamp": _observation_source_time(previous).isoformat(),
        "dataAgeSeconds": _finite(age, 2),
    }


def score_spot_trend(
    candles: pd.DataFrame,
    evaluation_timestamp: datetime,
    config: NiftyOiConfig | None = None,
) -> dict[str, Any]:
    if candles.empty or not isinstance(candles.index, pd.DatetimeIndex):
        return {"available": False, "reason": "Completed NIFTY spot candles are unavailable"}
    data = candles.copy().sort_index()
    data.index = data.index.tz_localize(IST) if data.index.tz is None else data.index.tz_convert(IST)
    cutoff = pd.Timestamp(_as_ist(evaluation_timestamp))
    data = data.loc[data.index <= cutoff]
    if len(data) < 20:
        return {"available": False, "reason": "At least 20 completed NIFTY candles are required"}
    source_timestamp = _as_ist(data.index[-1])
    age = (_as_ist(evaluation_timestamp) - source_timestamp).total_seconds()
    if age < 0 or (config is not None and age > config.stale_data_seconds):
        return {"available": False, "reason": "Completed NIFTY spot candle is stale"}
    close = pd.to_numeric(data["Close"], errors="coerce")
    high = pd.to_numeric(data["High"], errors="coerce")
    low = pd.to_numeric(data["Low"], errors="coerce")
    volume = pd.to_numeric(data["Volume"], errors="coerce").fillna(0.0)
    ema9 = close.ewm(span=9, adjust=False, min_periods=9).mean()
    ema20 = close.ewm(span=20, adjust=False, min_periods=20).mean()
    session = pd.Series(data.index.date, index=data.index)
    typical = (high + low + close) / 3.0
    cumulative_value = (typical * volume).groupby(session).cumsum()
    cumulative_volume = volume.groupby(session).cumsum()
    vwap = cumulative_value / cumulative_volume.replace(0, np.nan)
    if not math.isfinite(float(vwap.iloc[-1])):
        return {"available": False, "reason": "NIFTY session VWAP is unavailable"}
    last = float(close.iloc[-1])
    score = 0.0
    score += 25.0 if last > float(vwap.iloc[-1]) else -25.0
    return5 = _change_pct(last, float(close.iloc[-2])) or 0.0
    return15 = _change_pct(last, float(close.iloc[-4])) or 0.0
    score += _clamp(return5 * 20.0, -20.0, 20.0)
    score += _clamp(return15 * 10.0, -20.0, 20.0)
    score += 20.0 if float(ema9.iloc[-1]) > float(ema20.iloc[-1]) else -20.0
    slope = _change_pct(float(ema9.iloc[-1]), float(ema9.iloc[-4])) or 0.0
    score += _clamp(slope * 15.0, -15.0, 15.0)
    return {
        "available": True,
        "score": _finite(_clamp(score, -100.0, 100.0)),
        "confidence": "HIGH",
        "aboveVwap": last > float(vwap.iloc[-1]),
        "return5mPct": _finite(return5),
        "return15mPct": _finite(return15),
        "ema9AboveEma20": float(ema9.iloc[-1]) > float(ema20.iloc[-1]),
        "emaSlopePct": _finite(slope),
        "sourceTimestamp": source_timestamp.isoformat(),
        "dataAgeSeconds": _finite(age, 2),
    }


def regime_from_score(score: float, config: NiftyOiConfig) -> RegimeName:
    if score <= config.strongly_bearish_threshold:
        return "STRONGLY_BEARISH"
    if score < config.bearish_threshold:
        return "BEARISH"
    if score < config.bullish_threshold:
        return "NEUTRAL"
    if score < config.strongly_bullish_threshold:
        return "BULLISH"
    return "STRONGLY_BULLISH"


def combine_regime_components(
    options: Mapping[str, Any],
    futures: Mapping[str, Any],
    spot: Mapping[str, Any],
    evaluation_timestamp: datetime,
    config: NiftyOiConfig,
) -> dict[str, Any]:
    config.validate()
    evaluation = _as_ist(evaluation_timestamp)
    components = {"options": dict(options), "futures": dict(futures), "spot": dict(spot)}
    for value in components.values():
        source = value.get("sourceTimestamp")
        if value.get("available") and source and _as_ist(source) > evaluation:
            value.update({"available": False, "reason": "Component source timestamp is after the evaluation timestamp"})
    weights = {"options": config.options_weight, "futures": config.futures_weight, "spot": config.spot_weight}
    total_configured = sum(weights.values())
    available_weight = sum(weights[name] for name, value in components.items() if value.get("available"))
    coverage = available_weight / total_configured if total_configured else 0.0
    source_timestamps = [
        _as_ist(value["sourceTimestamp"])
        for value in components.values()
        if value.get("available") and value.get("sourceTimestamp")
    ]
    if coverage < config.minimum_component_coverage or not source_timestamps:
        return insufficient_regime(
            evaluation_timestamp,
            reason=f"OI component coverage {coverage:.1%} is below required {config.minimum_component_coverage:.1%}",
            components=components,
            coverage=coverage,
        )
    denominator = available_weight
    score = sum(float(components[name]["score"]) * weights[name] for name in components if components[name].get("available")) / denominator
    volatility_expansion = bool(options.get("available") and options.get("volatilityExpansion"))
    confidence_values = [str(value.get("confidence", "LOW")) for value in components.values() if value.get("available")]
    confidence = "LOW" if volatility_expansion or "LOW" in confidence_values else "HIGH" if all(value == "HIGH" for value in confidence_values) else "MEDIUM"
    source_timestamp = max(source_timestamps)
    oldest_source_timestamp = min(source_timestamps)
    data_age = max(0.0, (evaluation - oldest_source_timestamp).total_seconds())
    return {
        "schemaVersion": OI_SCHEMA_VERSION,
        "filterVersion": OI_FILTER_VERSION,
        "timestamp": evaluation.isoformat(),
        "underlying": "NIFTY",
        "regime": "VOLATILITY_EXPANSION" if volatility_expansion else regime_from_score(score, config),
        "combinedScore": _finite(score),
        "confidence": confidence,
        "coverage": _finite(coverage),
        "sourceTimestamp": source_timestamp.isoformat(),
        "oldestSourceTimestamp": oldest_source_timestamp.isoformat(),
        "dataAgeSeconds": _finite(data_age, 2),
        "effectiveWeights": {
            name: _finite(weights[name] / available_weight) if value.get("available") else 0.0
            for name, value in components.items()
        },
        "options": dict(options),
        "futures": dict(futures),
        "spot": dict(spot),
        "reason": "Near-ATM calls and puts rose with broad IV expansion" if volatility_expansion else "Weighted completed-bar OI and NIFTY spot regime",
    }


def insufficient_regime(
    evaluation_timestamp: datetime,
    *,
    reason: str,
    components: Mapping[str, Any] | None = None,
    coverage: float = 0.0,
) -> dict[str, Any]:
    return {
        "schemaVersion": OI_SCHEMA_VERSION,
        "filterVersion": OI_FILTER_VERSION,
        "timestamp": _as_ist(evaluation_timestamp).isoformat(),
        "underlying": "NIFTY",
        "regime": "INSUFFICIENT_OI_DATA",
        "combinedScore": None,
        "confidence": "LOW",
        "coverage": _finite(coverage),
        "sourceTimestamp": None,
        "dataAgeSeconds": None,
        "options": dict((components or {}).get("options", {"available": False})),
        "futures": dict((components or {}).get("futures", {"available": False})),
        "spot": dict((components or {}).get("spot", {"available": False})),
        "reason": reason,
    }


def decide_long_trade(
    mode: OiFilterMode,
    regime: Mapping[str, Any],
    *,
    stock_quality_score: float | None,
    open_portfolio_positions: int = 0,
    config: NiftyOiConfig,
) -> dict[str, Any]:
    if mode == "OFF":
        return {"allowed": True, "decision": "OI_OFF", "reason": "OI filter is disabled"}
    state = str(regime.get("regime") or "INSUFFICIENT_OI_DATA")
    if mode == "ADVISORY":
        return {"allowed": True, "decision": "ADVISORY_ONLY", "reason": f"{state} recorded without blocking"}
    if state == "INSUFFICIENT_OI_DATA":
        allowed = config.fail_policy == "ALLOW"
        return {
            "allowed": allowed,
            "decision": "ALLOW_MISSING_OI" if allowed else "SKIPPED_INSUFFICIENT_OI_DATA",
            "reason": regime.get("reason") or "Required OI data is unavailable",
        }
    if state == "STRONGLY_BEARISH":
        return {"allowed": False, "decision": "SKIPPED_STRONGLY_BEARISH_OI", "reason": "Strongly bearish NIFTY OI regime blocks long cash-equity entries"}
    if state in {"BEARISH", "VOLATILITY_EXPANSION"}:
        quality_ok = stock_quality_score is not None and stock_quality_score >= config.elevated_quality_threshold
        capacity_ok = open_portfolio_positions < 1
        allowed = quality_ok and capacity_ok
        suffix = "BEARISH_OI" if state == "BEARISH" else "VOLATILITY_EXPANSION"
        return {
            "allowed": allowed,
            "decision": "ALLOWED_ELEVATED_QUALITY" if allowed else f"SKIPPED_{suffix}",
            "reason": (
                f"Requires quality >= {config.elevated_quality_threshold:g} and at most one open portfolio long; "
                f"quality={stock_quality_score if stock_quality_score is not None else 'unavailable'}, open={open_portfolio_positions}"
            ),
        }
    return {"allowed": True, "decision": f"ALLOWED_{state}", "reason": "OI regime does not add a long-entry restriction"}


class OiRegimeRepository:
    """Append-only JSONL storage following the existing file-backed persistence model."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._json_cache: dict[Path, tuple[tuple[int, int], list[dict[str, Any]]]] = {}
        self.option_file = root / "option-observations.jsonl"
        self.futures_file = root / "futures-observations.jsonl"
        self.regime_file = root / "regime-snapshots.jsonl"

    @staticmethod
    def _append(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
        if not rows:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(path, os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o600)
        try:
            content = "".join(json.dumps(dict(row), separators=(",", ":"), allow_nan=False) + "\n" for row in rows)
            os.write(descriptor, content.encode("utf-8"))
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    @staticmethod
    def _read(path: Path) -> list[dict[str, Any]]:
        if not path.exists():
            return []
        rows: list[dict[str, Any]] = []
        try:
            with path.open("r", encoding="utf-8") as handle:
                for line in handle:
                    if line.strip():
                        value = json.loads(line)
                        if isinstance(value, dict):
                            rows.append(value)
        except (OSError, json.JSONDecodeError):
            return []
        return rows

    def _read_cached(self, path: Path) -> list[dict[str, Any]]:
        try:
            stat = path.stat()
            signature = (stat.st_mtime_ns, stat.st_size)
        except OSError:
            return []
        cached = self._json_cache.get(path)
        if cached is None or cached[0] != signature:
            cached = (signature, self._read(path))
            self._json_cache[path] = cached
        return [dict(row) for row in cached[1]]

    def _append_cached(self, path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
        self._append(path, rows)
        self._json_cache.pop(path, None)

    def append_options(self, observations: Sequence[OptionOiObservation]) -> None:
        with self._lock:
            self._append_cached(self.option_file, [item.public() for item in observations])

    def append_futures(self, observation: FuturesOiObservation) -> None:
        with self._lock:
            self._append_cached(self.futures_file, [observation.public()])

    def append_regime(self, snapshot: Mapping[str, Any]) -> None:
        timestamp = str(snapshot.get("timestamp") or "")
        if not timestamp:
            raise ValueError("Regime snapshot requires a timestamp")
        with self._lock:
            existing = self._read_cached(self.regime_file)
            if existing and str(existing[-1].get("timestamp")) == timestamp:
                return
            self._append_cached(self.regime_file, [snapshot])

    def option_history(self) -> list[OptionOiObservation]:
        with self._lock:
            return [OptionOiObservation.from_public(row) for row in self._read_cached(self.option_file)]

    def futures_history(self) -> list[FuturesOiObservation]:
        with self._lock:
            return [FuturesOiObservation.from_public(row) for row in self._read_cached(self.futures_file)]

    def regimes(self) -> list[dict[str, Any]]:
        with self._lock:
            return self._read_cached(self.regime_file)

    def regime_at_or_before(
        self,
        timestamp: datetime | str,
        *,
        stale_seconds: int,
    ) -> dict[str, Any] | None:
        target = _as_ist(timestamp)
        rows = sorted(self.regimes(), key=lambda row: _as_ist(row["timestamp"]))
        timestamps = [_as_ist(row["timestamp"]) for row in rows]
        index = bisect_right(timestamps, target) - 1
        while index >= 0 and rows[index].get("sourceTimestamp") and _as_ist(rows[index]["sourceTimestamp"]) > target:
            index -= 1
        if index < 0:
            return None
        latest = rows[index]
        freshness_timestamp = (
            latest.get("oldestSourceTimestamp")
            or latest.get("sourceTimestamp")
            or latest["timestamp"]
        )
        age = (target - _as_ist(freshness_timestamp)).total_seconds()
        if age < 0 or age > stale_seconds:
            return None
        return latest

    def latest_regime(self) -> dict[str, Any] | None:
        rows = self.regimes()
        return rows[-1] if rows else None

    def observations_at(self, timestamp: datetime, lookback_bars: int) -> tuple[list[OptionOiObservation], list[OptionOiObservation], FuturesOiObservation | None, FuturesOiObservation | None]:
        target = _as_ist(timestamp)
        option_rows = [item for item in self.option_history() if _as_ist(item.timestamp) <= target]
        option_times = sorted({_as_ist(item.timestamp) for item in option_rows})
        current_time = option_times[-1] if option_times else None
        previous_time = option_times[-(lookback_bars + 1)] if len(option_times) > lookback_bars else None
        current_options = [item for item in option_rows if current_time is not None and _as_ist(item.timestamp) == current_time]
        previous_options = [item for item in option_rows if previous_time is not None and _as_ist(item.timestamp) == previous_time]
        future_rows = [item for item in self.futures_history() if _as_ist(item.timestamp) <= target]
        future_rows.sort(key=lambda item: _as_ist(item.timestamp))
        current_future = future_rows[-1] if future_rows else None
        previous_future = future_rows[-(lookback_bars + 1)] if len(future_rows) > lookback_bars else None
        return current_options, previous_options, current_future, previous_future


def attach_oi_filter(
    observations: Mapping[str, Any],
    *,
    repository: OiRegimeRepository,
    mode: OiFilterMode,
    config: NiftyOiConfig,
    stock_quality_score: float | None,
) -> dict[str, Any]:
    if mode == "OFF":
        return dict(observations)
    symbol = str(observations.get("symbol") or "")
    return apply_oi_filter_chronologically(
        [observations],
        repository=repository,
        mode=mode,
        config=config,
        quality_by_symbol={symbol: stock_quality_score} if symbol else {},
    )[0]


def _trade_end_timestamp(trade: Mapping[str, Any]) -> datetime | None:
    status = str(trade.get("status") or "").upper()
    if status in {"OPEN", "STILL_OPEN"}:
        return None
    for key in ("exitTimestamp", "targetHitTimestamp", "lastTimestamp"):
        if trade.get(key):
            return _as_ist(trade[key])
    return None


def apply_oi_filter_chronologically(
    results: Sequence[Mapping[str, Any]],
    *,
    repository: OiRegimeRepository,
    mode: OiFilterMode,
    config: NiftyOiConfig,
    quality_by_symbol: Mapping[str, float | None] | None = None,
) -> list[dict[str, Any]]:
    """Gate existing BUY candidates in one deterministic, cross-symbol time stream."""
    if mode == "OFF":
        return [copy.deepcopy(dict(result)) for result in results]
    output = [copy.deepcopy(dict(result)) for result in results]
    candidates: list[tuple[datetime, str, str, int, dict[str, Any]]] = []
    for result_index, result in enumerate(output):
        symbol = str(result.get("symbol") or "")
        for trade in result.get("trades", []):
            signal_value = trade.get("signalTimestamp") or trade.get("entryTimestamp")
            if not signal_value:
                continue
            candidates.append((
                _as_ist(signal_value),
                symbol,
                str(trade.get("tradeId") or ""),
                result_index,
                dict(trade),
            ))
        result["trades"] = []
        result["oiSkippedSignals"] = []
        result["oiFilterMode"] = mode
    candidates.sort(key=lambda value: (value[0], value[1], value[2]))
    regime_rows = sorted(repository.regimes(), key=lambda row: _as_ist(row["timestamp"]))
    regime_times = [_as_ist(row["timestamp"]) for row in regime_rows]
    active_until: list[datetime | None] = []
    qualities = quality_by_symbol or {}
    for signal_timestamp, symbol, _, result_index, trade in candidates:
        active_until = [end for end in active_until if end is None or end > signal_timestamp]
        regime_index = bisect_right(regime_times, signal_timestamp) - 1
        while (
            regime_index >= 0
            and regime_rows[regime_index].get("sourceTimestamp")
            and _as_ist(regime_rows[regime_index]["sourceTimestamp"]) > signal_timestamp
        ):
            regime_index -= 1
        regime = dict(regime_rows[regime_index]) if regime_index >= 0 else None
        if regime is not None:
            freshness = regime.get("oldestSourceTimestamp") or regime.get("sourceTimestamp") or regime["timestamp"]
            age = (signal_timestamp - _as_ist(freshness)).total_seconds()
            if age < 0 or age > config.stale_data_seconds:
                regime = None
        if regime is None:
            regime = insufficient_regime(
                signal_timestamp,
                reason="No causal OI regime snapshot exists at or before this signal",
            )
        decision = decide_long_trade(
            mode,
            regime,
            stock_quality_score=qualities.get(symbol),
            open_portfolio_positions=len(active_until),
            config=config,
        )
        enriched = {
            **trade,
            "oiRegimeAtSignal": regime.get("regime"),
            "oiScoreAtSignal": regime.get("combinedScore"),
            "oiConfidence": regime.get("confidence"),
            "oiDecision": decision["decision"],
            "oiSourceTimestamp": regime.get("sourceTimestamp"),
        }
        target = output[result_index]
        if decision["allowed"]:
            target["trades"].append(enriched)
            active_until.append(_trade_end_timestamp(enriched))
        else:
            target["oiSkippedSignals"].append({
                "tradeId": trade.get("tradeId"),
                "symbol": symbol,
                "signalTimestamp": signal_timestamp.isoformat(),
                "reason": decision["reason"],
                "status": decision["decision"],
                "oiRegime": regime,
                "hypotheticalOutcome": enriched,
            })
    return output


def chronological_walk_forward_folds(
    timestamps: Sequence[datetime | str],
    duration_years: int,
) -> list[dict[str, str]]:
    if not timestamps:
        return []
    ordered = sorted(_as_ist(value) for value in timestamps)
    start = ordered[0]
    end = ordered[-1]
    folds: list[dict[str, str]] = []
    if duration_years <= 1:
        validation_start = min(_as_ist(pd.Timestamp(start) + pd.DateOffset(months=9)), end)
        return [{
            "trainingFrom": start.isoformat(),
            "trainingTo": (validation_start - timedelta(microseconds=1)).isoformat(),
            "validationFrom": validation_start.isoformat(),
            "validationTo": end.isoformat(),
        }]
    cursor = start
    while True:
        training_end = _as_ist(pd.Timestamp(cursor) + pd.DateOffset(months=12))
        validation_end = _as_ist(pd.Timestamp(training_end) + pd.DateOffset(months=3))
        if validation_end > end:
            break
        folds.append({
            "trainingFrom": cursor.isoformat(),
            "trainingTo": (training_end - timedelta(microseconds=1)).isoformat(),
            "validationFrom": training_end.isoformat(),
            "validationTo": validation_end.isoformat(),
        })
        cursor = _as_ist(pd.Timestamp(cursor) + pd.DateOffset(months=3))
    return folds


def _trade_net_pnl(trade: Mapping[str, Any]) -> float | None:
    for key in ("netPnl", "realizedNetPnl", "realizedPnl", "pnl"):
        value = _finite(trade.get(key))
        if value is not None:
            return value
    return None


def _period_pnl(trades: Sequence[Mapping[str, Any]], period: Literal["day", "month"]) -> list[dict[str, Any]]:
    values: dict[str, float] = {}
    counts: dict[str, int] = {}
    for trade in trades:
        timestamp = trade.get("exitTimestamp") or trade.get("targetHitTimestamp") or trade.get("lastTimestamp")
        pnl = _trade_net_pnl(trade)
        if not timestamp or pnl is None:
            continue
        stamp = pd.Timestamp(_as_ist(timestamp))
        key = stamp.strftime("%Y-%m-%d" if period == "day" else "%Y-%m")
        values[key] = values.get(key, 0.0) + pnl
        counts[key] = counts.get(key, 0) + 1
    return [{"period": key, "netPnl": _finite(values[key], 2), "trades": counts[key]} for key in sorted(values)]


def compare_oi_modes(
    off: Mapping[str, Any],
    advisory: Mapping[str, Any],
    enforced: Mapping[str, Any],
) -> dict[str, Any]:
    rejected = [item for result in enforced.get("results", []) for item in result.get("oiSkippedSignals", [])]
    off_trades = [trade for result in off.get("results", []) for trade in result.get("trades", [])]
    off_outcomes = {
        str(trade.get("tradeId")): trade
        for trade in off_trades
        if trade.get("tradeId")
    }
    rejected_winners = 0
    rejected_losers = 0
    avoided_stops = 0
    reasons: dict[str, int] = {}
    for item in rejected:
        reason = str(item.get("status") or "UNKNOWN")
        reasons[reason] = reasons.get(reason, 0) + 1
        outcome = off_outcomes.get(str(item.get("tradeId") or "")) or item.get("hypotheticalOutcome") or {}
        status = str(outcome.get("status") or "")
        pnl = outcome.get("netPnl", outcome.get("realizedPnl"))
        if status in {"TARGET_HIT", "TARGET_EXIT", "TARGET_GAP", "RSI_PROFIT_EXIT", "RSI_OVERBOUGHT_PROFIT_EXIT"} or (pnl is not None and float(pnl) > 0):
            rejected_winners += 1
        elif status in {"STOP_EXIT", "STOP_GAP"} or (pnl is not None and float(pnl) <= 0):
            rejected_losers += 1
        if status in {"STOP_EXIT", "STOP_GAP"}:
            avoided_stops += 1
    off_summary = dict(off.get("summary") or {})
    enforced_summary = dict(enforced.get("summary") or {})
    def metric_change(*names: str) -> float | None:
        for name in names:
            if name in off_summary or name in enforced_summary:
                return _finite(float(enforced_summary.get(name, 0) or 0) - float(off_summary.get(name, 0) or 0))
        return None
    total_rejected = len(rejected)
    advisory_trades = [trade for result in advisory.get("results", []) for trade in result.get("trades", [])]
    enforced_trades = [trade for result in enforced.get("results", []) for trade in result.get("trades", [])]
    off_ids = [str(item.get("tradeId") or "") for item in off_trades]
    advisory_ids = [str(item.get("tradeId") or "") for item in advisory_trades]
    regime_performance: dict[str, dict[str, Any]] = {}
    for trade in enforced_trades:
        regime = str(trade.get("oiRegimeAtSignal") or "UNKNOWN")
        row = regime_performance.setdefault(regime, {"accepted": 0, "rejected": 0, "netPnl": 0.0, "resolvedPnlTrades": 0})
        row["accepted"] += 1
        pnl = _trade_net_pnl(trade)
        if pnl is not None:
            row["netPnl"] += pnl
            row["resolvedPnlTrades"] += 1
    for item in rejected:
        regime = str((item.get("oiRegime") or {}).get("regime") or "UNKNOWN")
        row = regime_performance.setdefault(regime, {"accepted": 0, "rejected": 0, "netPnl": 0.0, "resolvedPnlTrades": 0})
        row["rejected"] += 1
    for row in regime_performance.values():
        row["netPnl"] = _finite(row["netPnl"], 2)
    return {
        "researchLabel": "Research candidate — paper trading required",
        "tradesAccepted": len(enforced_trades),
        "tradesRejected": total_rejected,
        "tradesRejectedByStrongBearishOi": reasons.get("SKIPPED_STRONGLY_BEARISH_OI", 0),
        "tradesRejectedByBearishOi": reasons.get("SKIPPED_BEARISH_OI", 0),
        "tradesRejectedByVolatilityExpansion": reasons.get("SKIPPED_VOLATILITY_EXPANSION", 0),
        "tradesSkippedForMissingData": reasons.get("SKIPPED_INSUFFICIENT_OI_DATA", 0),
        "rejectionReasons": reasons,
        "rejectedTradesThatWouldHaveWon": rejected_winners,
        "rejectedTradesThatWouldHaveLost": rejected_losers,
        "falseRejectionRate": _finite(rejected_winners / total_rejected * 100.0) if total_rejected else 0.0,
        "stopExitsAvoided": avoided_stops,
        "netPnlChange": metric_change("combinedPnl", "netReturnPct"),
        "profitFactorChange": metric_change("profitFactor"),
        "expectancyChange": metric_change("expectancyPerTrade"),
        "drawdownChange": metric_change("maximumDrawdown", "worstCompletedMae"),
        "winRateChange": metric_change("winRate", "targetHitRate"),
        "tradeCountChange": metric_change("executedTrades", "totalBuySignals"),
        "advisoryExecutionIdentical": off_ids == advisory_ids,
        "dailyPnl": _period_pnl(enforced_trades, "day"),
        "monthlyPnl": _period_pnl(enforced_trades, "month"),
        "regimeLevelPerformance": regime_performance,
    }
