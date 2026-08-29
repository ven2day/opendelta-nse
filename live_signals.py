from __future__ import annotations

import hashlib
import json
import math
import os
import struct
import tempfile
import threading
import uuid
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from datetime import time as datetime_time
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlencode

import numpy as np
import pandas as pd

from main import IST, DhanClient, DhanConfig, historical_payload_to_frame
from recovery_backtest import (
    STRATEGY_VERSION,
    RecoveryConfig,
    calculate_recovery_indicators,
    rsi_recovery_crossovers,
)
from recovery_feature_analysis import calculate_entry_feature_frame
from nifty_oi_regime import NiftyOiConfig, insufficient_regime

TIMEFRAME = "5m"
TIMEFRAME_MINUTES = 5
MARKET_OPEN = datetime_time(9, 15)
MARKET_CLOSE = datetime_time(15, 30)
DEFAULT_WARMUP_BARS = 250
DEFAULT_TARGET_PCT = 0.5
LIVE_SIGNAL_SCHEMA_VERSION = "live-signals-1"
NO_ORDER_EXECUTION = True


def _finite(value: Any, digits: int = 6) -> float | None:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    return round(numeric, digits) if math.isfinite(numeric) else None


def _as_ist(value: Any) -> pd.Timestamp:
    stamp = pd.Timestamp(value)
    if stamp.tzinfo is None:
        return stamp.tz_localize(IST)
    return stamp.tz_convert(IST)


def _iso_ist(value: Any) -> str:
    return _as_ist(value).isoformat()


def _now_ist() -> datetime:
    return datetime.now(IST)


def _atomic_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            json.dump(payload, handle, ensure_ascii=False, separators=(",", ":"))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def _read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return default


def is_nse_session_timestamp(value: Any) -> bool:
    stamp = _as_ist(value)
    return stamp.weekday() < 5 and MARKET_OPEN <= stamp.time() <= MARKET_CLOSE


def completed_bucket_end(value: Any) -> pd.Timestamp:
    stamp = _as_ist(value)
    session_open = stamp.normalize() + pd.Timedelta(hours=9, minutes=15)
    elapsed = max(0, int((stamp - session_open).total_seconds()))
    bucket = elapsed // (TIMEFRAME_MINUTES * 60)
    return session_open + pd.Timedelta(minutes=(bucket + 1) * TIMEFRAME_MINUTES)


@dataclass(frozen=True)
class LiveSignalSettings:
    timeframe: Literal["5m"] = "5m"
    entry_range_method: Literal["FIXED_PERCENT", "ATR_BASED"] = "FIXED_PERCENT"
    fixed_lower_pct: float = 0.15
    fixed_upper_pct: float = 0.10
    atr_lower_multiplier: float = 0.25
    atr_upper_multiplier: float = 0.15
    paper_allocation: float = 25_000.0
    stale_data_seconds: int = 90
    fresh_minutes: int = 15
    recent_minutes: int = 60
    support_lookback_short: int = 20
    support_lookback_long: int = 50
    oi_filter_mode: Literal["OFF", "ADVISORY", "ENFORCED"] = "OFF"
    oi_lookback_bars: int = 3
    oi_strikes_each_side: int = 5
    oi_minimum_price_change_pct: float = 0.05
    oi_minimum_change_pct: float = 0.50
    oi_maximum_spread_pct: float = 20.0
    oi_stale_data_seconds: int = 360
    oi_minimum_valid_contract_fraction: float = 0.50
    oi_minimum_futures_volume: float = 1.0
    oi_volatility_price_rise_pct: float = 0.25
    oi_volatility_iv_rise: float = 0.50
    oi_minimum_coverage: float = 0.65
    oi_options_weight: float = 0.35
    oi_futures_weight: float = 0.35
    oi_spot_weight: float = 0.30
    oi_strongly_bearish_threshold: float = -60.0
    oi_bearish_threshold: float = -20.0
    oi_bullish_threshold: float = 20.0
    oi_strongly_bullish_threshold: float = 60.0
    oi_elevated_quality_threshold: float = 95.0
    oi_fail_policy: Literal["SKIP", "ALLOW"] = "SKIP"

    def validate(self) -> LiveSignalSettings:
        if self.timeframe != "5m":
            raise ValueError("Phase 1 live signals support only the completed 5-minute timeframe")
        if self.entry_range_method not in {"FIXED_PERCENT", "ATR_BASED"}:
            raise ValueError("Entry range method must be FIXED_PERCENT or ATR_BASED")
        if self.fixed_lower_pct < 0 or self.fixed_upper_pct < 0:
            raise ValueError("Fixed buy-range tolerances cannot be negative")
        if self.atr_lower_multiplier < 0 or self.atr_upper_multiplier < 0:
            raise ValueError("ATR buy-range multipliers cannot be negative")
        if self.paper_allocation <= 0:
            raise ValueError("Paper allocation must be greater than zero")
        if not 10 <= self.stale_data_seconds <= 3_600:
            raise ValueError("Stale-data threshold must be between 10 and 3,600 seconds")
        if not 1 <= self.fresh_minutes < self.recent_minutes:
            raise ValueError("Fresh minutes must be lower than recent minutes")
        if self.support_lookback_short < 2 or self.support_lookback_long < self.support_lookback_short:
            raise ValueError("Support lookbacks must be ordered and at least two bars")
        if self.oi_filter_mode not in {"OFF", "ADVISORY", "ENFORCED"}:
            raise ValueError("NIFTY OI filter mode must be OFF, ADVISORY, or ENFORCED")
        self.oi_config()
        return self

    def oi_config(self) -> NiftyOiConfig:
        return NiftyOiConfig(
            lookback_bars=self.oi_lookback_bars,
            strikes_each_side=self.oi_strikes_each_side,
            minimum_price_change_pct=self.oi_minimum_price_change_pct,
            minimum_oi_change_pct=self.oi_minimum_change_pct,
            maximum_spread_pct=self.oi_maximum_spread_pct,
            stale_data_seconds=self.oi_stale_data_seconds,
            minimum_valid_contract_fraction=self.oi_minimum_valid_contract_fraction,
            minimum_futures_volume=self.oi_minimum_futures_volume,
            minimum_component_coverage=self.oi_minimum_coverage,
            options_weight=self.oi_options_weight,
            futures_weight=self.oi_futures_weight,
            spot_weight=self.oi_spot_weight,
            strongly_bearish_threshold=self.oi_strongly_bearish_threshold,
            bearish_threshold=self.oi_bearish_threshold,
            bullish_threshold=self.oi_bullish_threshold,
            strongly_bullish_threshold=self.oi_strongly_bullish_threshold,
            volatility_price_rise_pct=self.oi_volatility_price_rise_pct,
            volatility_iv_rise=self.oi_volatility_iv_rise,
            elevated_quality_threshold=self.oi_elevated_quality_threshold,
            fail_policy=self.oi_fail_policy,
        ).validate()

    def public(self) -> dict[str, Any]:
        return {
            "timeframe": self.timeframe,
            "entryRangeMethod": self.entry_range_method,
            "fixedLowerPct": self.fixed_lower_pct,
            "fixedUpperPct": self.fixed_upper_pct,
            "atrLowerMultiplier": self.atr_lower_multiplier,
            "atrUpperMultiplier": self.atr_upper_multiplier,
            "paperAllocation": self.paper_allocation,
            "staleDataSeconds": self.stale_data_seconds,
            "freshMinutes": self.fresh_minutes,
            "recentMinutes": self.recent_minutes,
            "supportLookbackShort": self.support_lookback_short,
            "supportLookbackLong": self.support_lookback_long,
            "oiFilterMode": self.oi_filter_mode,
            "oiLookbackBars": self.oi_lookback_bars,
            "oiStrikesEachSide": self.oi_strikes_each_side,
            "oiMinimumPriceChangePct": self.oi_minimum_price_change_pct,
            "oiMinimumChangePct": self.oi_minimum_change_pct,
            "oiMaximumSpreadPct": self.oi_maximum_spread_pct,
            "oiStaleDataSeconds": self.oi_stale_data_seconds,
            "oiMinimumValidContractFraction": self.oi_minimum_valid_contract_fraction,
            "oiMinimumFuturesVolume": self.oi_minimum_futures_volume,
            "oiVolatilityPriceRisePct": self.oi_volatility_price_rise_pct,
            "oiVolatilityIvRise": self.oi_volatility_iv_rise,
            "oiMinimumCoverage": self.oi_minimum_coverage,
            "oiOptionsWeight": self.oi_options_weight,
            "oiFuturesWeight": self.oi_futures_weight,
            "oiSpotWeight": self.oi_spot_weight,
            "oiStronglyBearishThreshold": self.oi_strongly_bearish_threshold,
            "oiBearishThreshold": self.oi_bearish_threshold,
            "oiBullishThreshold": self.oi_bullish_threshold,
            "oiStronglyBullishThreshold": self.oi_strongly_bullish_threshold,
            "oiElevatedQualityThreshold": self.oi_elevated_quality_threshold,
            "oiFailPolicy": self.oi_fail_policy,
            "oiFilterDefault": "OFF",
            "targetPct": DEFAULT_TARGET_PCT,
            "execution": "PAPER_ONLY",
        }


def settings_from_payload(payload: Mapping[str, Any]) -> LiveSignalSettings:
    defaults = LiveSignalSettings()
    return LiveSignalSettings(
        timeframe="5m",
        entry_range_method=str(payload.get("entryRangeMethod", defaults.entry_range_method)).upper(),  # type: ignore[arg-type]
        fixed_lower_pct=float(payload.get("fixedLowerPct", defaults.fixed_lower_pct)),
        fixed_upper_pct=float(payload.get("fixedUpperPct", defaults.fixed_upper_pct)),
        atr_lower_multiplier=float(payload.get("atrLowerMultiplier", defaults.atr_lower_multiplier)),
        atr_upper_multiplier=float(payload.get("atrUpperMultiplier", defaults.atr_upper_multiplier)),
        paper_allocation=float(payload.get("paperAllocation", defaults.paper_allocation)),
        stale_data_seconds=int(payload.get("staleDataSeconds", defaults.stale_data_seconds)),
        fresh_minutes=int(payload.get("freshMinutes", defaults.fresh_minutes)),
        recent_minutes=int(payload.get("recentMinutes", defaults.recent_minutes)),
        support_lookback_short=int(payload.get("supportLookbackShort", defaults.support_lookback_short)),
        support_lookback_long=int(payload.get("supportLookbackLong", defaults.support_lookback_long)),
        oi_filter_mode=str(payload.get("oiFilterMode", defaults.oi_filter_mode)).upper(),  # type: ignore[arg-type]
        oi_lookback_bars=int(payload.get("oiLookbackBars", defaults.oi_lookback_bars)),
        oi_strikes_each_side=int(payload.get("oiStrikesEachSide", defaults.oi_strikes_each_side)),
        oi_minimum_price_change_pct=float(payload.get("oiMinimumPriceChangePct", defaults.oi_minimum_price_change_pct)),
        oi_minimum_change_pct=float(payload.get("oiMinimumChangePct", defaults.oi_minimum_change_pct)),
        oi_maximum_spread_pct=float(payload.get("oiMaximumSpreadPct", defaults.oi_maximum_spread_pct)),
        oi_stale_data_seconds=int(payload.get("oiStaleDataSeconds", defaults.oi_stale_data_seconds)),
        oi_minimum_valid_contract_fraction=float(payload.get("oiMinimumValidContractFraction", defaults.oi_minimum_valid_contract_fraction)),
        oi_minimum_futures_volume=float(payload.get("oiMinimumFuturesVolume", defaults.oi_minimum_futures_volume)),
        oi_volatility_price_rise_pct=float(payload.get("oiVolatilityPriceRisePct", defaults.oi_volatility_price_rise_pct)),
        oi_volatility_iv_rise=float(payload.get("oiVolatilityIvRise", defaults.oi_volatility_iv_rise)),
        oi_minimum_coverage=float(payload.get("oiMinimumCoverage", defaults.oi_minimum_coverage)),
        oi_options_weight=float(payload.get("oiOptionsWeight", defaults.oi_options_weight)),
        oi_futures_weight=float(payload.get("oiFuturesWeight", defaults.oi_futures_weight)),
        oi_spot_weight=float(payload.get("oiSpotWeight", defaults.oi_spot_weight)),
        oi_strongly_bearish_threshold=float(payload.get("oiStronglyBearishThreshold", defaults.oi_strongly_bearish_threshold)),
        oi_bearish_threshold=float(payload.get("oiBearishThreshold", defaults.oi_bearish_threshold)),
        oi_bullish_threshold=float(payload.get("oiBullishThreshold", defaults.oi_bullish_threshold)),
        oi_strongly_bullish_threshold=float(payload.get("oiStronglyBullishThreshold", defaults.oi_strongly_bullish_threshold)),
        oi_elevated_quality_threshold=float(payload.get("oiElevatedQualityThreshold", defaults.oi_elevated_quality_threshold)),
        oi_fail_policy=str(payload.get("oiFailPolicy", defaults.oi_fail_policy)).upper(),  # type: ignore[arg-type]
    ).validate()


def calculate_buy_range(
    signal_close: float,
    atr14: float | None,
    settings: LiveSignalSettings,
) -> dict[str, Any]:
    if signal_close <= 0:
        raise ValueError("Signal close must be greater than zero")
    if settings.entry_range_method == "ATR_BASED":
        if atr14 is None or not math.isfinite(atr14) or atr14 <= 0:
            raise ValueError("ATR-based range requires a positive causal ATR14 value")
        low = signal_close - atr14 * settings.atr_lower_multiplier
        high = signal_close + atr14 * settings.atr_upper_multiplier
        method_label = "ATR heuristic"
        formula = (
            f"close - ATR14*{settings.atr_lower_multiplier:g} to "
            f"close + ATR14*{settings.atr_upper_multiplier:g}"
        )
    else:
        low = signal_close * (1.0 - settings.fixed_lower_pct / 100.0)
        high = signal_close * (1.0 + settings.fixed_upper_pct / 100.0)
        method_label = "Fixed % heuristic"
        formula = (
            f"close*(1-{settings.fixed_lower_pct:g}/100) to "
            f"close*(1+{settings.fixed_upper_pct:g}/100)"
        )
    midpoint = (low + high) / 2.0
    return {
        "method": settings.entry_range_method,
        "methodLabel": method_label,
        "formula": formula,
        "low": _finite(low, 4),
        "midpoint": _finite(midpoint, 4),
        "high": _finite(high, 4),
    }


def buy_range_status(current_price: float | None, low: float, high: float) -> str:
    if current_price is None or not math.isfinite(current_price):
        return "UNAVAILABLE"
    if current_price < low:
        return "BELOW_RANGE"
    if current_price > high:
        return "ABOVE_RANGE"
    return "IN_RANGE"


def quantity_suggestion(allocation: float, low: float, midpoint: float, high: float) -> dict[str, Any]:
    if allocation <= 0 or min(low, midpoint, high) <= 0:
        raise ValueError("Allocation and buy-range prices must be greater than zero")
    return {
        "allocation": _finite(allocation, 2),
        "quantityReference": "UPPER_BUY_RANGE_SAFE_CEILING",
        "recommendedQuantity": math.floor(allocation / high),
        "quantityAtLower": math.floor(allocation / low),
        "quantityAtMidpoint": math.floor(allocation / midpoint),
        "quantityAtUpper": math.floor(allocation / high),
        "referenceEntryMidpoint": _finite(midpoint, 4),
        "noLeverage": True,
        "productAssumption": "CNC_OWN_CAPITAL",
    }


def indicative_targets(low: float, midpoint: float, high: float) -> dict[str, float | None]:
    multiplier = 1.0 + DEFAULT_TARGET_PCT / 100.0
    return {
        "targetPct": DEFAULT_TARGET_PCT,
        "atLower": _finite(low * multiplier, 4),
        "atMidpoint": _finite(midpoint * multiplier, 4),
        "atUpper": _finite(high * multiplier, 4),
    }


def calculate_support_resistance(
    candles: pd.DataFrame,
    reference_entry: float,
    short_lookback: int = 20,
    long_lookback: int = 50,
) -> dict[str, Any]:
    """Causal range/session levels. The current row and prior rows are the only inputs."""
    if candles.empty:
        return {"support": None, "resistance": None, "targetRoom": "UNKNOWN"}
    data = candles.sort_index()
    current_date = _as_ist(data.index[-1]).date()
    prior = data[["High", "Low", "Close"]][pd.Index([_as_ist(value).date() for value in data.index]) < current_date]
    previous_session = prior[pd.Index([_as_ist(value).date() for value in prior.index]) == _as_ist(prior.index[-1]).date()] if not prior.empty else pd.DataFrame()
    levels: dict[str, float | None] = {
        "low20": _finite(data["Low"].tail(short_lookback).min()),
        "high20": _finite(data["High"].tail(short_lookback).max()),
        "low50": _finite(data["Low"].tail(long_lookback).min()),
        "high50": _finite(data["High"].tail(long_lookback).max()),
        "previousSessionLow": _finite(previous_session["Low"].min()) if not previous_session.empty else None,
        "previousSessionHigh": _finite(previous_session["High"].max()) if not previous_session.empty else None,
        "previousSessionClose": _finite(previous_session.iloc[-1]["Close"]) if not previous_session.empty else None,
    }
    support_values = [value for value in levels.values() if value is not None and value <= reference_entry]
    resistance_values = [value for value in levels.values() if value is not None and value >= reference_entry]
    support = max(support_values, default=None)
    resistance = min(resistance_values, default=None)
    target = reference_entry * (1.0 + DEFAULT_TARGET_PCT / 100.0)
    room = ((resistance - reference_entry) / reference_entry * 100.0) if resistance is not None else None
    return {
        **levels,
        "method": "Causal 20/50-bar ranges plus previous-session high, low and close",
        "support": _finite(support),
        "resistance": _finite(resistance),
        "distanceToSupportPct": _finite((support - reference_entry) / reference_entry * 100.0) if support is not None else None,
        "distanceToResistancePct": _finite(room),
        "targetRequiredPct": DEFAULT_TARGET_PCT,
        "resistanceBeforeTarget": bool(resistance is not None and resistance < target),
        "targetRoom": "TIGHT" if resistance is not None and resistance < target else "CLEAR" if resistance is not None else "UNKNOWN",
    }


@dataclass(frozen=True)
class DhanQuoteTick:
    exchange_segment: int
    security_id: str
    price: float
    cumulative_volume: int
    timestamp: datetime


@dataclass(frozen=True)
class DhanFeedPacket:
    response_code: int
    exchange_segment: int
    security_id: str
    timestamp: datetime
    price: float | None = None
    cumulative_volume: int | None = None
    open_interest: int | None = None
    bid: float | None = None
    ask: float | None = None


def parse_dhan_feed_packets(message: bytes, received_at: datetime | None = None) -> list[DhanFeedPacket]:
    """Parse causal quote, OI, and full packets from the Dhan v2 binary feed."""
    received = (received_at or _now_ist()).astimezone(IST)
    packets: list[DhanFeedPacket] = []
    offset = 0
    while offset + 8 <= len(message):
        response_code = message[offset]
        packet_length = struct.unpack_from("<H", message, offset + 1)[0]
        exchange_segment = message[offset + 3]
        security_id = str(struct.unpack_from("<I", message, offset + 4)[0])
        if packet_length < 8 or offset + packet_length > len(message):
            break
        if response_code == 4 and packet_length >= 26:
            price = float(struct.unpack_from("<f", message, offset + 8)[0])
            last_trade_epoch = int(struct.unpack_from("<I", message, offset + 14)[0])
            volume = int(struct.unpack_from("<I", message, offset + 22)[0])
            if price > 0 and last_trade_epoch > 0 and volume >= 0:
                packets.append(DhanFeedPacket(
                    response_code=response_code,
                    exchange_segment=exchange_segment,
                    security_id=security_id,
                    price=price,
                    cumulative_volume=volume,
                    timestamp=datetime.fromtimestamp(last_trade_epoch, tz=IST),
                ))
        elif response_code == 5 and packet_length >= 12:
            oi = int(struct.unpack_from("<I", message, offset + 8)[0])
            if oi >= 0:
                packets.append(DhanFeedPacket(
                    response_code=response_code,
                    exchange_segment=exchange_segment,
                    security_id=security_id,
                    open_interest=oi,
                    timestamp=received,
                ))
        elif response_code == 8 and packet_length >= 82:
            price = float(struct.unpack_from("<f", message, offset + 8)[0])
            last_trade_epoch = int(struct.unpack_from("<I", message, offset + 14)[0])
            volume = int(struct.unpack_from("<I", message, offset + 22)[0])
            oi = int(struct.unpack_from("<I", message, offset + 34)[0])
            bid = float(struct.unpack_from("<f", message, offset + 74)[0])
            ask = float(struct.unpack_from("<f", message, offset + 78)[0])
            # Full packets contain current OI/depth without their own exchange timestamp;
            # reception time is the conservative availability timestamp for causal use.
            timestamp = received
            packets.append(DhanFeedPacket(
                response_code=response_code,
                exchange_segment=exchange_segment,
                security_id=security_id,
                timestamp=timestamp,
                price=price if price > 0 else None,
                cumulative_volume=volume if volume >= 0 else None,
                open_interest=oi if oi >= 0 else None,
                bid=bid if bid > 0 else None,
                ask=ask if ask > 0 else None,
            ))
        offset += packet_length
    return packets


def parse_dhan_quote_packets(message: bytes) -> list[DhanQuoteTick]:
    """Parse Dhan v2 little-endian Quote packets; unrelated feed packets are ignored."""
    return [
        DhanQuoteTick(
            exchange_segment=packet.exchange_segment,
            security_id=packet.security_id,
            price=packet.price,
            cumulative_volume=packet.cumulative_volume,
            timestamp=packet.timestamp,
        )
        for packet in parse_dhan_feed_packets(message)
        if packet.price is not None and packet.cumulative_volume is not None
    ]


class FiveMinuteCandleBuilder:
    def __init__(self, on_complete: Callable[[str, dict[str, Any]], None]) -> None:
        self.on_complete = on_complete
        self._lock = threading.RLock()
        self._buckets: dict[str, dict[str, Any]] = {}
        self._last_volume: dict[str, tuple[date, int]] = {}
        self._continuous_since: datetime | None = None

    def connection_started(self, timestamp: datetime | None = None) -> None:
        with self._lock:
            self._continuous_since = (timestamp or _now_ist()).astimezone(IST)

    def connection_lost(self) -> None:
        with self._lock:
            self._continuous_since = None
            for bucket in self._buckets.values():
                bucket["completeCoverage"] = False

    def add_tick(self, symbol: str, tick: DhanQuoteTick) -> None:
        stamp = tick.timestamp.astimezone(IST)
        if stamp.weekday() >= 5 or not (MARKET_OPEN <= stamp.time() < MARKET_CLOSE):
            return
        bucket_end = completed_bucket_end(stamp)
        bucket_start = bucket_end - pd.Timedelta(minutes=TIMEFRAME_MINUTES)
        with self._lock:
            existing = self._buckets.get(symbol)
            if existing is not None and _as_ist(existing["timestamp"]) < bucket_end:
                self._settle(symbol, existing)
                existing = None

            volume_date, previous_volume = self._last_volume.get(symbol, (stamp.date(), 0))
            if volume_date != stamp.date():
                previous_volume = 0
            elif symbol not in self._last_volume and stamp.time() > datetime_time(9, 17):
                previous_volume = tick.cumulative_volume
            incremental_volume = max(0, tick.cumulative_volume - previous_volume)
            self._last_volume[symbol] = (stamp.date(), tick.cumulative_volume)

            if existing is None:
                continuous = self._continuous_since is not None and self._continuous_since <= bucket_start.to_pydatetime()
                existing = {
                    "symbol": symbol,
                    "timestamp": bucket_end.isoformat(),
                    "Open": tick.price,
                    "High": tick.price,
                    "Low": tick.price,
                    "Close": tick.price,
                    "Volume": incremental_volume,
                    "complete": bool(continuous),
                    "completeCoverage": bool(continuous),
                    "marketDataTimestamp": stamp.isoformat(),
                }
                self._buckets[symbol] = existing
            else:
                existing["High"] = max(float(existing["High"]), tick.price)
                existing["Low"] = min(float(existing["Low"]), tick.price)
                existing["Close"] = tick.price
                existing["Volume"] = int(existing["Volume"]) + incremental_volume
                existing["marketDataTimestamp"] = stamp.isoformat()

    def flush_due(self, now: datetime | None = None) -> None:
        current = (now or _now_ist()).astimezone(IST)
        with self._lock:
            due = [symbol for symbol, candle in self._buckets.items() if _as_ist(candle["timestamp"]) <= current]
            for symbol in due:
                self._settle(symbol, self._buckets[symbol])

    def _settle(self, symbol: str, candle: dict[str, Any]) -> None:
        self._buckets.pop(symbol, None)
        candle["complete"] = bool(candle.pop("completeCoverage", False))
        self.on_complete(symbol, dict(candle))


def _confirmation_state(data: pd.DataFrame, index: int, config: RecoveryConfig) -> dict[str, Any]:
    row = data.iloc[index]
    ema = bool(config.ema_enabled and np.isfinite(row["EMAFast"]) and np.isfinite(row["EMASlow"]) and row["EMAFast"] > row["EMASlow"])
    vwap = bool(config.vwap_enabled and np.isfinite(row["SessionVWAP"]) and row["Close"] > row["SessionVWAP"])
    volume = bool(config.volume_enabled and np.isfinite(row["VolumeEMA"]) and row["Volume"] > row["VolumeEMA"])
    return {
        "confirmationScore": sum((ema, vwap, volume)),
        "emaConfirmation": ema,
        "vwapConfirmation": vwap,
        "volumeConfirmation": volume,
    }


def evaluate_latest_recovery(candles: pd.DataFrame, config: RecoveryConfig) -> dict[str, Any] | None:
    """Exact v1.1.0 arm/recovery gate, evaluated only for the final completed row."""
    warmup = max(config.rsi_length + 1, config.ema_fast, config.ema_slow, config.volume_ema)
    if len(candles) < warmup + 2:
        return None
    data = calculate_recovery_indicators(candles.sort_index(), config)
    crossovers = rsi_recovery_crossovers(data["RecoveryRSI"], config.rsi_recovery).to_numpy(dtype=bool)
    rsi_values = data["RecoveryRSI"].to_numpy(dtype=float)
    armed: dict[str, Any] | None = None
    latest: dict[str, Any] | None = None
    for index in range(len(data)):
        current_rsi = rsi_values[index]
        cycle_completed = False
        if armed is not None:
            armed["minimum"] = min(float(armed["minimum"]), current_rsi) if np.isfinite(current_rsi) else armed["minimum"]
            if np.isfinite(current_rsi) and current_rsi < 30:
                armed["barsBelow30"] += 1
        if armed is not None and config.setup_expiry_bars > 0 and index - int(armed["index"]) > config.setup_expiry_bars:
            armed = None
        if armed is not None and int(armed["index"]) < index and crossovers[index]:
            confirmation = _confirmation_state(data, index, config)
            if confirmation["confirmationScore"] >= config.minimum_confirmations:
                if index == len(data) - 1:
                    latest = {
                        "signalIndex": index,
                        "signalTimestamp": data.index[index],
                        "rsiArmTimestamp": armed["timestamp"],
                        "rsiArmValue": armed["value"],
                        "rsiMinimumSinceArm": armed["minimum"],
                        "barsBelow30SinceArm": armed["barsBelow30"],
                        "barsArmToRecovery": index - int(armed["index"]),
                        "rsi": current_rsi,
                        **confirmation,
                    }
                armed = None
                cycle_completed = True
        if (
            not cycle_completed
            and armed is None
            and np.isfinite(current_rsi)
            and config.rsi_arm_low <= current_rsi <= config.rsi_arm_high
        ):
            armed = {
                "index": index,
                "timestamp": data.index[index],
                "value": current_rsi,
                "minimum": current_rsi,
                "barsBelow30": 0,
            }
    if latest is None:
        return None
    latest["indicators"] = data
    return latest


def deterministic_signal_id(symbol: str, timestamp: Any) -> str:
    identity = f"{STRATEGY_VERSION}|{TIMEFRAME}|{symbol.upper()}|{_iso_ist(timestamp)}"
    return "SIG-" + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:24].upper()


class LiveSignalRepository:
    def __init__(self, root: Path, clock: Callable[[], datetime] = _now_ist) -> None:
        if not root.is_absolute():
            raise ValueError("Live-signal persistence root must be absolute")
        self.root = root
        self.clock = clock
        self._lock = threading.RLock()
        self.settings_path = root / "settings.json"
        self.signals_path = root / "signals.json"
        self.paper_path = root / "paper-trades.json"
        self.study_path = root / "study.json"
        self.candles_path = root / "settled-candles.jsonl"
        root.mkdir(parents=True, exist_ok=True)
        self._signals: list[dict[str, Any]] = list(_read_json(self.signals_path, []))
        self._paper: list[dict[str, Any]] = list(_read_json(self.paper_path, []))
        self._settings = settings_from_payload(_read_json(self.settings_path, {}))

    def settings(self) -> LiveSignalSettings:
        with self._lock:
            return self._settings

    def save_settings(self, settings: LiveSignalSettings) -> dict[str, Any]:
        with self._lock:
            self._settings = settings.validate()
            payload = self._settings.public()
            _atomic_json(self.settings_path, payload)
            return payload

    def ensure_study(self, universe_version: str) -> dict[str, Any]:
        with self._lock:
            current = _read_json(self.study_path, None)
            if isinstance(current, dict) and current.get("universeVersion") == universe_version:
                return current
            started = self.clock().astimezone(IST)
            payload = {
                "studyId": f"PAPER-{started:%Y%m%d}-{universe_version}",
                "studyStart": started.isoformat(),
                "studyEnd": (started + timedelta(days=30)).isoformat(),
                "universeVersion": universe_version,
                "strategyVersion": STRATEGY_VERSION,
                "paperAllocationDefault": self._settings.paper_allocation,
                "buyRangeMethod": self._settings.entry_range_method,
                "schemaVersion": LIVE_SIGNAL_SCHEMA_VERSION,
            }
            _atomic_json(self.study_path, payload)
            return payload

    def study(self) -> dict[str, Any] | None:
        value = _read_json(self.study_path, None)
        return value if isinstance(value, dict) else None

    def signals(self) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(item) for item in self._signals]

    def signal(self, signal_id: str) -> dict[str, Any] | None:
        with self._lock:
            return next((dict(item) for item in self._signals if item.get("signalId") == signal_id), None)

    def add_signal(self, signal: dict[str, Any]) -> tuple[dict[str, Any], bool]:
        with self._lock:
            existing = next((item for item in self._signals if item.get("signalId") == signal["signalId"]), None)
            if existing is not None:
                return dict(existing), False
            self._signals.append(signal)
            _atomic_json(self.signals_path, self._signals)
            return dict(signal), True

    def decide(self, signal_id: str, action: str, *, reason: str | None = None, notes: str | None = None) -> dict[str, Any]:
        if action not in {"WATCH", "IGNORE", "NO_ACTION"}:
            raise ValueError("Decision must be WATCH, IGNORE, or NO_ACTION")
        with self._lock:
            signal = next((item for item in self._signals if item.get("signalId") == signal_id), None)
            if signal is None:
                raise KeyError("Signal was not found")
            signal["manualAction"] = action
            signal["decisionTimestamp"] = self.clock().astimezone(IST).isoformat()
            signal["ignoreReason"] = reason if action == "IGNORE" else None
            signal["notes"] = notes or None
            _atomic_json(self.signals_path, self._signals)
            return dict(signal)

    def create_paper_trade(self, signal_id: str, entry_price: float, quantity: int, notes: str | None = None) -> dict[str, Any]:
        if entry_price <= 0 or quantity <= 0:
            raise ValueError("Actual paper entry and whole-share quantity must be greater than zero")
        with self._lock:
            signal = next((item for item in self._signals if item.get("signalId") == signal_id), None)
            if signal is None:
                raise KeyError("Signal was not found")
            existing = next((item for item in self._paper if item.get("signalId") == signal_id), None)
            if existing is not None:
                raise ValueError("This signal already has a paper-trade observation")
            now = self.clock().astimezone(IST)
            trade = {
                "paperTradeId": "PAPER-" + uuid.uuid4().hex[:20].upper(),
                "signalId": signal_id,
                "symbol": signal["symbol"],
                "decisionTimestamp": now.isoformat(),
                "entryTimestamp": now.isoformat(),
                "entryPrice": _finite(entry_price, 4),
                "quantity": int(quantity),
                "paperAmount": _finite(entry_price * quantity, 2),
                "targetPct": DEFAULT_TARGET_PCT,
                "targetPrice": _finite(entry_price * (1.0 + DEFAULT_TARGET_PCT / 100.0), 4),
                "status": "OPEN",
                "exitTimestamp": None,
                "exitPrice": None,
                "pnl": None,
                "pnlPct": None,
                "lowestPrice": None,
                "highestPrice": None,
                "maePct": None,
                "mfePct": None,
                "targetHitTimestamp": None,
                "notes": notes or None,
                "brokerExecution": False,
            }
            self._paper.append(trade)
            signal["manualAction"] = "PAPER_BUY"
            signal["decisionTimestamp"] = now.isoformat()
            _atomic_json(self.paper_path, self._paper)
            _atomic_json(self.signals_path, self._signals)
            return dict(trade)

    def paper_trades(self) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(item) for item in self._paper]

    def close_paper_trade(self, trade_id: str, exit_price: float, notes: str | None = None) -> dict[str, Any]:
        if exit_price <= 0:
            raise ValueError("Paper exit price must be greater than zero")
        with self._lock:
            trade = next((item for item in self._paper if item.get("paperTradeId") == trade_id), None)
            if trade is None:
                raise KeyError("Paper trade was not found")
            if trade["status"] != "OPEN":
                raise ValueError("Only an open paper trade can be manually closed")
            now = self.clock().astimezone(IST)
            trade["status"] = "MANUALLY_CLOSED"
            trade["exitTimestamp"] = now.isoformat()
            trade["exitPrice"] = _finite(exit_price, 4)
            trade["pnl"] = _finite((exit_price - trade["entryPrice"]) * trade["quantity"], 2)
            trade["pnlPct"] = _finite((exit_price / trade["entryPrice"] - 1.0) * 100.0)
            if notes:
                trade["notes"] = notes
            _atomic_json(self.paper_path, self._paper)
            return dict(trade)

    def process_completed_candle(self, symbol: str, candle: Mapping[str, Any]) -> None:
        stamp = _as_ist(candle["timestamp"])
        low, high, close = (float(candle[key]) for key in ("Low", "High", "Close"))
        changed_signals = False
        changed_paper = False
        with self._lock:
            for signal in self._signals:
                outcome = signal.get("hypotheticalOutcome")
                if signal.get("symbol") != symbol or not isinstance(outcome, dict) or outcome.get("status") != "OPEN":
                    continue
                if stamp <= _as_ist(signal["signalTimestamp"]):
                    continue
                outcome["lowestPrice"] = low if outcome.get("lowestPrice") is None else min(float(outcome["lowestPrice"]), low)
                outcome["highestPrice"] = high if outcome.get("highestPrice") is None else max(float(outcome["highestPrice"]), high)
                outcome["maePct"] = _finite((outcome["lowestPrice"] / signal["signalClose"] - 1.0) * 100.0)
                outcome["mfePct"] = _finite((outcome["highestPrice"] / signal["signalClose"] - 1.0) * 100.0)
                outcome["lastTimestamp"] = stamp.isoformat()
                outcome["lastClose"] = _finite(close, 4)
                outcome["barsHeld"] = int(outcome.get("barsHeld", 0)) + 1
                if high >= signal["systemTargetPrice"]:
                    duration = (stamp - _as_ist(signal["signalTimestamp"])).total_seconds() / 60.0
                    outcome.update({"status": "TARGET_HIT", "targetHitTimestamp": stamp.isoformat(), "durationMinutes": _finite(duration, 2)})
                changed_signals = True

            for trade in self._paper:
                if trade.get("symbol") != symbol or trade.get("status") != "OPEN":
                    continue
                if stamp <= _as_ist(trade["entryTimestamp"]):
                    continue
                trade["lowestPrice"] = low if trade.get("lowestPrice") is None else min(float(trade["lowestPrice"]), low)
                trade["highestPrice"] = high if trade.get("highestPrice") is None else max(float(trade["highestPrice"]), high)
                trade["maePct"] = _finite((trade["lowestPrice"] / trade["entryPrice"] - 1.0) * 100.0)
                trade["mfePct"] = _finite((trade["highestPrice"] / trade["entryPrice"] - 1.0) * 100.0)
                if high >= trade["targetPrice"]:
                    trade["status"] = "TARGET_HIT"
                    trade["targetHitTimestamp"] = stamp.isoformat()
                    trade["exitTimestamp"] = stamp.isoformat()
                    trade["exitPrice"] = trade["targetPrice"]
                    trade["pnl"] = _finite((trade["targetPrice"] - trade["entryPrice"]) * trade["quantity"], 2)
                    trade["pnlPct"] = DEFAULT_TARGET_PCT
                changed_paper = True
            if changed_signals:
                _atomic_json(self.signals_path, self._signals)
            if changed_paper:
                _atomic_json(self.paper_path, self._paper)

    def append_candle(self, symbol: str, candle: Mapping[str, Any]) -> None:
        payload = {"symbol": symbol, **dict(candle)}
        self.candles_path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n"
        with self._lock, self.candles_path.open("a", encoding="utf-8") as handle:
            handle.write(line)
            handle.flush()
            os.fsync(handle.fileno())

    def recent_candles(self, symbols: set[str], per_symbol: int = DEFAULT_WARMUP_BARS) -> dict[str, list[dict[str, Any]]]:
        rows: dict[str, list[dict[str, Any]]] = {symbol: [] for symbol in symbols}
        try:
            with self.candles_path.open("r", encoding="utf-8") as handle:
                for line in handle:
                    try:
                        payload = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    symbol = str(payload.get("symbol", ""))
                    if symbol in rows:
                        rows[symbol].append(payload)
                        if len(rows[symbol]) > per_symbol:
                            rows[symbol] = rows[symbol][-per_symbol:]
        except OSError:
            pass
        return rows


class LiveSignalEngine:
    def __init__(
        self,
        repository: LiveSignalRepository,
        data_store: Any,
        universe_service: Any,
        *,
        clock: Callable[[], datetime] = _now_ist,
        warmup_bars: int = DEFAULT_WARMUP_BARS,
        feed_factory: Callable[..., Any] | None = None,
        oi_service: Any | None = None,
    ) -> None:
        self.repository = repository
        self.data_store = data_store
        self.universe_service = universe_service
        self.clock = clock
        self.warmup_bars = warmup_bars
        self.feed_factory = feed_factory
        self.oi_service = oi_service
        self.strategy_config = RecoveryConfig()
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._histories: dict[str, pd.DataFrame] = {}
        self._latest_prices: dict[str, dict[str, Any]] = {}
        self._historical_context: dict[str, dict[str, Any]] = {}
        self._security_to_symbol: dict[tuple[int, str], str] = {}
        self._symbols: list[str] = []
        self._universe: dict[str, Any] | None = None
        self._last_completed: str | None = None
        self._last_market_data: str | None = None
        self._connection_status = "DISCONNECTED"
        self._engine_status = "STOPPED"
        self._message = "Live signal engine is not running"
        self._last_disconnect: datetime | None = None
        self._recovering = False
        self._recovery_started: datetime | None = None
        self._last_recovery_seconds: float | None = None
        self._latest_oi_regime: dict[str, Any] | None = None
        self._builder = FiveMinuteCandleBuilder(self.process_completed_candle)

    def start(self) -> None:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop.clear()
            self._thread = threading.Thread(target=self._run, name="opendelta-live-signals", daemon=True)
            self._thread.start()

    def stop(self, timeout: float = 10.0) -> None:
        self._stop.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout=timeout)
        with self._lock:
            self._connection_status = "DISCONNECTED"
            self._engine_status = "STOPPED"

    def _run(self) -> None:
        try:
            self._set_state(engine="STARTING", message="Loading frozen universe and cached 5-minute warm-up")
            self._initialize()
            if self.feed_factory is not None:
                feed = self.feed_factory(self)
            else:
                feed = DhanMarketFeed(self, self.data_store.config, self.data_store.client)
            feed.run(self._stop)
        except Exception as error:  # noqa: BLE001 - background boundary reports a durable health state
            self._set_state(connection="DISCONNECTED", engine="ERROR", message=str(error)[:240])

    def _initialize(self) -> None:
        symbols, active = self.universe_service.get_active_live_universe()
        if not active or not active.get("frozen"):
            raise RuntimeError("A frozen active universe is required before live signals can start")
        self._symbols = list(symbols)
        self._universe = active
        self.repository.ensure_study(str(active["universeVersion"]))
        self._historical_context = {str(row["symbol"]): dict(row) for row in active.get("selected", [])}
        persisted = self.repository.recent_candles(set(self._symbols), self.warmup_bars)
        latest_settled: pd.Timestamp | None = None
        for symbol in self._symbols:
            history = self._load_cached_history(symbol)
            live_rows = persisted.get(symbol, [])
            if live_rows:
                live = pd.DataFrame(live_rows)
                live.index = pd.DatetimeIndex([_as_ist(value) for value in live.pop("timestamp")])
                history = pd.concat([history, live[["Open", "High", "Low", "Close", "Volume"]]])
            history = history[~history.index.duplicated(keep="last")].sort_index().tail(self.warmup_bars)
            self._histories[symbol] = history
            if not history.empty:
                symbol_latest = _as_ist(history.index[-1])
                latest_settled = symbol_latest if latest_settled is None else max(latest_settled, symbol_latest)
                self._latest_prices[symbol] = {
                    "price": float(history.iloc[-1]["Close"]),
                    "timestamp": symbol_latest.isoformat(),
                }
            security_id = self.data_store.security_id(symbol)
            self._security_to_symbol[(1, security_id)] = symbol
        self._last_completed = latest_settled.isoformat() if latest_settled is not None else None
        self._set_state(engine="MARKET_CLOSED" if not self._market_is_open() else "CONNECTING", message="Warm-up ready; connecting to Dhan market data")

    def _load_cached_history(self, symbol: str) -> pd.DataFrame:
        path = self.data_store._cache_path(symbol, "5", 1)
        try:
            frame = pd.read_csv(path, index_col="Timestamp", parse_dates=["Timestamp"])
        except (OSError, ValueError, KeyError, pd.errors.ParserError):
            now = self.clock().astimezone(IST)
            try:
                return self.data_store.candles(
                    symbol,
                    TIMEFRAME,
                    1,
                    now - timedelta(days=3),
                    now,
                    warmup_bars=self.warmup_bars,
                )[["Open", "High", "Low", "Close", "Volume"]].tail(self.warmup_bars)
            except Exception:  # noqa: BLE001 - provider/cache fallback must leave the engine available
                return pd.DataFrame(columns=["Open", "High", "Low", "Close", "Volume"])
        frame.index = pd.DatetimeIndex(frame.index)
        frame.index = frame.index.tz_localize(IST) if frame.index.tz is None else frame.index.tz_convert(IST)
        frame.index = frame.index + pd.Timedelta(minutes=TIMEFRAME_MINUTES)
        required = ["Open", "High", "Low", "Close", "Volume"]
        return frame[required].apply(pd.to_numeric, errors="coerce").dropna().tail(self.warmup_bars)

    def _market_is_open(self) -> bool:
        now = self.clock().astimezone(IST)
        return now.weekday() < 5 and MARKET_OPEN <= now.time() <= MARKET_CLOSE

    def _set_state(self, *, connection: str | None = None, engine: str | None = None, message: str | None = None) -> None:
        with self._lock:
            if connection:
                self._connection_status = connection
            if engine:
                self._engine_status = engine
            if message:
                self._message = message

    def on_connected(self) -> None:
        self._builder.connection_started(self.clock())
        now = self.clock().astimezone(IST)
        downtime = (now - self._last_disconnect).total_seconds() if self._last_disconnect else 0
        if downtime > TIMEFRAME_MINUTES * 60:
            self._recovering = True
            self._recovery_started = now
            self._set_state(connection="CONNECTED", engine="RECOVERING", message="Dhan stream restored; recovering missing completed candles")
            threading.Thread(target=self._recover_after_disconnect, name="opendelta-gap-recovery", daemon=True).start()
        else:
            self._set_state(connection="CONNECTED", engine="READY" if self._market_is_open() else "MARKET_CLOSED", message="Dhan quote stream connected")

    def _recover_after_disconnect(self) -> None:
        """Backfill missed settled bars; stale recovered bars rebuild state but never emit late BUYs."""
        try:
            now = self.clock().astimezone(IST)
            latest_complete = _as_ist(now).floor(f"{TIMEFRAME_MINUTES}min")
            for symbol in self._symbols:
                if self._stop.is_set():
                    break
                with self._lock:
                    history = self._histories.get(symbol)
                    last_close = _as_ist(history.index[-1]) if history is not None and not history.empty else latest_complete - pd.Timedelta(days=1)
                if last_close >= latest_complete:
                    continue
                security_id = self.data_store.security_id(symbol)
                payload = self.data_store.client.historical_intraday(
                    security_id,
                    "5",
                    last_close.to_pydatetime() - timedelta(minutes=TIMEFRAME_MINUTES),
                    now,
                )
                raw = historical_payload_to_frame(payload)
                if raw.empty:
                    continue
                raw.index = raw.index + pd.Timedelta(minutes=TIMEFRAME_MINUTES)
                recovered = raw[(raw.index > last_close) & (raw.index <= latest_complete)]
                if recovered.empty:
                    continue
                with self._lock:
                    merged = pd.concat([self._histories[symbol], recovered[["Open", "High", "Low", "Close", "Volume"]]])
                    self._histories[symbol] = merged[~merged.index.duplicated(keep="last")].sort_index().tail(self.warmup_bars)
                for stamp, row in recovered.iterrows():
                    candle = {
                        "timestamp": _iso_ist(stamp),
                        "Open": float(row["Open"]),
                        "High": float(row["High"]),
                        "Low": float(row["Low"]),
                        "Close": float(row["Close"]),
                        "Volume": int(row["Volume"]),
                        "complete": True,
                        "marketDataTimestamp": _iso_ist(stamp),
                        "recovered": True,
                    }
                    self.repository.append_candle(symbol, candle)
                    self.repository.process_completed_candle(symbol, candle)
            if self._recovery_started:
                self._last_recovery_seconds = (self.clock().astimezone(IST) - self._recovery_started).total_seconds()
            self._set_state(engine="READY" if self._market_is_open() else "MARKET_CLOSED", message="Missing-candle recovery completed; realtime evaluation resumed")
        except Exception as error:  # noqa: BLE001 - recovery failure becomes explicit stale-data state
            self._set_state(engine="STALE_DATA", message=f"Gap recovery incomplete: {str(error)[:180]}")
        finally:
            self._recovering = False

    def on_disconnected(self, reconnecting: bool = True, reason: str | None = None) -> None:
        self._builder.connection_lost()
        self._latest_oi_regime = None
        if self.oi_service is not None and hasattr(self.oi_service, "invalidate_live_state"):
            self.oi_service.invalidate_live_state()
        self._last_disconnect = self.clock().astimezone(IST)
        detail = f": {reason[:160]}" if reason else ""
        self._set_state(
            connection="RECONNECTING" if reconnecting else "DISCONNECTED",
            engine="RECONNECTING" if reconnecting else "STOPPED",
            message=f"Dhan stream interrupted; completed-candle generation is paused{detail}",
        )

    def on_tick(self, tick: DhanQuoteTick) -> None:
        symbol = self._security_to_symbol.get((tick.exchange_segment, tick.security_id))
        if symbol is None:
            return
        timestamp = tick.timestamp.astimezone(IST)
        with self._lock:
            self._latest_prices[symbol] = {"price": tick.price, "timestamp": timestamp.isoformat()}
            self._last_market_data = timestamp.isoformat()
        self._builder.add_tick(symbol, tick)

    def on_feed_packet(self, packet: DhanFeedPacket) -> None:
        if self.oi_service is not None and hasattr(self.oi_service, "on_market_feed"):
            self.oi_service.on_market_feed(packet)
        if packet.price is not None and packet.cumulative_volume is not None:
            self.on_tick(DhanQuoteTick(
                exchange_segment=packet.exchange_segment,
                security_id=packet.security_id,
                price=packet.price,
                cumulative_volume=packet.cumulative_volume,
                timestamp=packet.timestamp,
            ))

    def flush_due(self) -> None:
        self._builder.flush_due(self.clock())

    def process_completed_candle(self, symbol: str, candle: Mapping[str, Any]) -> dict[str, Any] | None:
        if not bool(candle.get("complete")):
            return None
        stamp = _as_ist(candle["timestamp"])
        now = self.clock().astimezone(IST)
        if not is_nse_session_timestamp(stamp):
            return None
        data_age = max(0.0, (now - stamp.to_pydatetime()).total_seconds())
        settings = self.repository.settings()
        if data_age > settings.stale_data_seconds:
            self._set_state(engine="STALE_DATA", message=f"Completed candle is {int(data_age)} seconds stale; signal suppressed")
            return None
        row = pd.DataFrame(
            [{key: float(candle[key]) for key in ("Open", "High", "Low", "Close", "Volume")}],
            index=pd.DatetimeIndex([stamp]),
        )
        with self._lock:
            history = self._histories.get(symbol, pd.DataFrame())
            history = pd.concat([history, row])
            history = history[~history.index.duplicated(keep="last")].sort_index().tail(self.warmup_bars)
            self._histories[symbol] = history
            self._last_completed = stamp.isoformat()
        self.repository.append_candle(symbol, candle)
        self.repository.process_completed_candle(symbol, candle)
        if self._recovering:
            self._set_state(engine="RECOVERING", message="Completed candle stored while missing-candle recovery is active; BUY evaluation paused")
            return None
        recovery = evaluate_latest_recovery(history, self.strategy_config)
        if recovery is None:
            self._set_state(engine="READY", message="Latest completed candles evaluated")
            return None
        signal = self._build_signal(symbol, candle, recovery, history, data_age)
        stored, created = self.repository.add_signal(signal)
        self._set_state(engine="READY", message="New RSI Recovery signal recorded" if created else "Duplicate signal safely ignored")
        return stored if created else None

    def _build_signal(
        self,
        symbol: str,
        candle: Mapping[str, Any],
        recovery: Mapping[str, Any],
        history: pd.DataFrame,
        data_age: float,
    ) -> dict[str, Any]:
        settings = self.repository.settings()
        indicators = recovery["indicators"]
        features = calculate_entry_feature_frame(history, self.strategy_config, timeframe=TIMEFRAME)
        indicator = indicators.iloc[-1]
        feature = features.iloc[-1]
        close = float(candle["Close"])
        atr14 = _finite(feature.get("feature_atr14"))
        buy_range = calculate_buy_range(close, atr14, settings)
        low, midpoint, high = (float(buy_range[key]) for key in ("low", "midpoint", "high"))
        support = calculate_support_resistance(
            history,
            midpoint,
            settings.support_lookback_short,
            settings.support_lookback_long,
        )
        historical = self._historical_context.get(symbol, {})
        stamp = _as_ist(candle["timestamp"])
        signal_id = deterministic_signal_id(symbol, stamp)
        oi_regime = insufficient_regime(
            stamp.to_pydatetime(),
            reason="OI context is not integrated with RSI Recovery Scalping",
        )
        return {
            "signalId": signal_id,
            "schemaVersion": LIVE_SIGNAL_SCHEMA_VERSION,
            "universeVersion": self._universe.get("universeVersion") if self._universe else None,
            "strategyVersion": STRATEGY_VERSION,
            "symbol": symbol,
            "timeframe": TIMEFRAME,
            "signalTimestamp": stamp.isoformat(),
            "marketDataTimestamp": str(candle.get("marketDataTimestamp") or stamp.isoformat()),
            "dataAgeSeconds": _finite(data_age, 2),
            "signalCandle": {
                "open": _finite(candle["Open"], 4),
                "high": _finite(candle["High"], 4),
                "low": _finite(candle["Low"], 4),
                "close": _finite(close, 4),
                "volume": int(candle["Volume"]),
                "complete": True,
            },
            "signalClose": _finite(close, 4),
            "systemTargetPct": DEFAULT_TARGET_PCT,
            "systemTargetPrice": _finite(close * (1.0 + DEFAULT_TARGET_PCT / 100.0), 4),
            "rsi": _finite(recovery["rsi"], 4),
            "rsiMinimumSinceArm": _finite(recovery["rsiMinimumSinceArm"], 4),
            "rsiArmTimestamp": _iso_ist(recovery["rsiArmTimestamp"]),
            "rsiArmValue": _finite(recovery["rsiArmValue"], 4),
            "barsArmToRecovery": int(recovery["barsArmToRecovery"]),
            "ema9": _finite(indicator["EMAFast"], 4),
            "ema20": _finite(indicator["EMASlow"], 4),
            "emaSpreadPct": _finite(feature.get("feature_ema_spread_pct"), 6),
            "vwap": _finite(indicator["SessionVWAP"], 4),
            "vwapDistancePct": _finite(feature.get("feature_close_vs_vwap_pct"), 6),
            "volume": int(candle["Volume"]),
            "volumeEma": _finite(indicator["VolumeEMA"], 2),
            "volumeRatio": _finite(feature.get("feature_volume_ratio"), 4),
            "confirmationScore": int(recovery["confirmationScore"]),
            "emaConfirmation": bool(recovery["emaConfirmation"]),
            "vwapConfirmation": bool(recovery["vwapConfirmation"]),
            "volumeConfirmation": bool(recovery["volumeConfirmation"]),
            "atr14": atr14,
            "atrPct": _finite(feature.get("feature_atr_pct"), 6),
            "momentum15m": _finite(feature.get("feature_return_15m"), 6),
            "momentum30m": _finite(feature.get("feature_return_30m"), 6),
            "historicalContext": {
                "rank": historical.get("rank"),
                "qualityScore": historical.get("qualityScore"),
                "goodRate": historical.get("goodRate"),
                "targetHitRate": historical.get("historicalTargetHitRate"),
                "medianTargetMinutes": historical.get("medianTargetMinutes"),
                "medianMaePct": historical.get("medianMaePct"),
                "openRate": historical.get("openRate"),
                "buyObservations": historical.get("buyObservations"),
            },
            "buyRange": buy_range,
            "quantitySuggestion": quantity_suggestion(settings.paper_allocation, low, midpoint, high),
            "indicativeTargets": indicative_targets(low, midpoint, high),
            "supportResistance": support,
            "marketContext": {"available": False, "reason": "Live NIFTY context is optional and not used by BUY generation"},
            "oiFilterMode": "OFF",
            "oiRegime": oi_regime,
            "oiRegimeAtSignal": oi_regime.get("regime"),
            "oiScoreAtSignal": oi_regime.get("combinedScore"),
            "oiConfidence": oi_regime.get("confidence"),
            "oiDecision": "NOT_APPLICABLE_RSI_RECOVERY",
            "oiDecisionReason": "RSI Recovery Scalping preserves its existing no-OI execution behavior",
            "oiSourceTimestamp": oi_regime.get("sourceTimestamp"),
            "executionEligible": True,
            "manualAction": "NO_ACTION",
            "decisionTimestamp": None,
            "ignoreReason": None,
            "notes": None,
            "hypotheticalOutcome": {
                "status": "OPEN",
                "targetHitTimestamp": None,
                "durationMinutes": None,
                "lowestPrice": None,
                "highestPrice": None,
                "maePct": None,
                "mfePct": None,
                "barsHeld": 0,
                "lastTimestamp": stamp.isoformat(),
                "lastClose": _finite(close, 4),
            },
            "brokerExecution": False,
            "createdAt": self.clock().astimezone(IST).isoformat(),
        }

    def status(self) -> dict[str, Any]:
        now = self.clock().astimezone(IST)
        with self._lock:
            last_market = _as_ist(self._last_market_data) if self._last_market_data else None
            data_age = (now - last_market.to_pydatetime()).total_seconds() if last_market is not None else None
            engine_status = self._engine_status
            if not self._market_is_open() and engine_status not in {"ERROR", "STARTING", "STOPPED"}:
                engine_status = "MARKET_CLOSED"
            elif self._market_is_open() and (last_market is None or data_age is not None and data_age > self.repository.settings().stale_data_seconds):
                engine_status = "STALE_DATA"
            return {
                "connectionStatus": self._connection_status,
                "engineStatus": engine_status,
                "message": self._message,
                "universeVersion": self._universe.get("universeVersion") if self._universe else None,
                "universeFrozen": bool(self._universe and self._universe.get("frozen")),
                "monitoredSymbols": len(self._symbols),
                "subscribedSymbols": len(self._security_to_symbol),
                "timeframe": TIMEFRAME,
                "strategyVersion": STRATEGY_VERSION,
                "lastCompletedCandle": self._last_completed,
                "lastMarketDataTimestamp": self._last_market_data,
                "dataAgeSeconds": _finite(data_age, 2),
                "marketSession": "OPEN" if self._market_is_open() else "CLOSED",
                "paperOnly": True,
                "liveOrdersEnabled": False,
                "lastReconnectRecoverySeconds": _finite(self._last_recovery_seconds, 3),
                "oiFilterMode": "OFF",
                "oiRegime": None,
                "oiHistory": (
                    self.oi_service.repository.history_status()
                    if self.oi_service is not None
                    else None
                ),
            }

    def list_signals(self, action: str | None = None) -> list[dict[str, Any]]:
        now = self.clock().astimezone(IST)
        settings = self.repository.settings()
        result: list[dict[str, Any]] = []
        for signal in self.repository.signals():
            if action and signal.get("manualAction") != action:
                continue
            latest = self._latest_prices.get(signal["symbol"])
            current_price = float(latest["price"]) if latest else signal["hypotheticalOutcome"].get("lastClose")
            age_minutes = max(0.0, (now - _as_ist(signal["signalTimestamp"]).to_pydatetime()).total_seconds() / 60.0)
            freshness = "FRESH" if age_minutes <= settings.fresh_minutes else "RECENT" if age_minutes <= settings.recent_minutes else "OLD"
            low, high = float(signal["buyRange"]["low"]), float(signal["buyRange"]["high"])
            result.append(
                {
                    **signal,
                    "currentPrice": _finite(current_price, 4),
                    "currentPriceTimestamp": latest.get("timestamp") if latest else signal["hypotheticalOutcome"].get("lastTimestamp"),
                    "buyRangeStatus": buy_range_status(current_price, low, high),
                    "signalAgeMinutes": _finite(age_minutes, 1),
                    "freshness": freshness,
                }
            )
        return sorted(result, key=lambda item: item["signalTimestamp"], reverse=True)

    def list_paper_trades(self) -> list[dict[str, Any]]:
        now = self.clock().astimezone(IST)
        rows = []
        for trade in self.repository.paper_trades():
            latest = self._latest_prices.get(trade["symbol"])
            current = float(latest["price"]) if latest else trade.get("exitPrice") or trade["entryPrice"]
            rows.append(
                {
                    **trade,
                    "currentPrice": _finite(current, 4),
                    "currentPnl": _finite((current - trade["entryPrice"]) * trade["quantity"], 2),
                    "currentPnlPct": _finite((current / trade["entryPrice"] - 1.0) * 100.0),
                    "targetProgressPct": _finite((current - trade["entryPrice"]) / (trade["targetPrice"] - trade["entryPrice"]) * 100.0),
                    "ageMinutes": _finite((now - _as_ist(trade["entryTimestamp"]).to_pydatetime()).total_seconds() / 60.0, 1),
                }
            )
        return sorted(rows, key=lambda item: item["entryTimestamp"], reverse=True)

    def study_summary(self) -> dict[str, Any]:
        signals = self.repository.signals()
        paper = self.repository.paper_trades()
        action_counts = {action: sum(item.get("manualAction") == action for item in signals) for action in ("PAPER_BUY", "WATCH", "IGNORE", "NO_ACTION")}
        def within_2h(items: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
            rows = list(items)
            completed = [item for item in rows if item.get("hypotheticalOutcome", {}).get("status") == "TARGET_HIT"]
            fast = [item for item in completed if float(item["hypotheticalOutcome"].get("durationMinutes") or math.inf) <= 120]
            return {"eligible": len(rows), "completedWithin2h": len(fast), "rate": _finite(len(fast) / len(rows) * 100.0, 2) if rows else None}
        return {
            "metadata": self.repository.study(),
            "signalsGenerated": len(signals),
            "paperBought": action_counts["PAPER_BUY"],
            "watched": action_counts["WATCH"],
            "ignored": action_counts["IGNORE"],
            "noAction": action_counts["NO_ACTION"],
            "paperTargetsHit": sum(item.get("status") == "TARGET_HIT" for item in paper),
            "paperPositionsOpen": sum(item.get("status") == "OPEN" for item in paper),
            "systemSignal2h": within_2h(signals),
            "paperSelected2h": within_2h(item for item in signals if item.get("manualAction") == "PAPER_BUY"),
            "ignored2h": within_2h(item for item in signals if item.get("manualAction") == "IGNORE"),
            "interpretation": "Signal research and manual paper decisions; not a portfolio backtest or live execution system.",
        }


class DhanMarketFeed:
    def __init__(self, engine: LiveSignalEngine, config: DhanConfig, client: DhanClient) -> None:
        self.engine = engine
        self.config = config
        self.client = client
        self.feed_url = os.environ.get("DHAN_FEED_URL", "wss://api-feed.dhan.co").rstrip("/?")

    def _url(self) -> str:
        return f"{self.feed_url}?{urlencode({'version': '2', 'token': self.client.access_token(), 'clientId': self.config.client_id, 'authType': '2'})}"

    def _subscriptions(self) -> list[dict[str, str]]:
        instruments = [
            {"ExchangeSegment": "NSE_EQ", "SecurityId": security_id}
            for (segment, security_id), _ in self.engine._security_to_symbol.items()
            if segment == 1
        ]
        unique: dict[tuple[str, str], dict[str, str]] = {}
        for item in instruments:
            unique[(item["ExchangeSegment"], item["SecurityId"])] = item
        return list(unique.values())

    @staticmethod
    def _send_subscriptions(connection: Any, instruments: list[dict[str, str]], subscribed: set[tuple[str, str]]) -> None:
        additions = [
            item for item in instruments
            if (item["ExchangeSegment"], item["SecurityId"]) not in subscribed
        ]
        for start in range(0, len(additions), 100):
            batch = additions[start : start + 100]
            connection.send(json.dumps({"RequestCode": 21, "InstrumentCount": len(batch), "InstrumentList": batch}))
            subscribed.update((item["ExchangeSegment"], item["SecurityId"]) for item in batch)

    @staticmethod
    def _remove_stale_oi_subscriptions(
        connection: Any,
        desired: list[dict[str, str]],
        subscribed: set[tuple[str, str]],
    ) -> None:
        desired_keys = {(item["ExchangeSegment"], item["SecurityId"]) for item in desired}
        stale = [
            {"ExchangeSegment": segment, "SecurityId": security_id}
            for segment, security_id in subscribed
            if segment != "NSE_EQ" and (segment, security_id) not in desired_keys
        ]
        for start in range(0, len(stale), 100):
            batch = stale[start : start + 100]
            connection.send(json.dumps({"RequestCode": 22, "InstrumentCount": len(batch), "InstrumentList": batch}))
            subscribed.difference_update((item["ExchangeSegment"], item["SecurityId"]) for item in batch)

    def _refresh_oi_subscriptions(self, connection: Any, subscribed: set[tuple[str, str]]) -> None:
        # The live workspace is RSI Recovery Scalping. Shared OI collection is not
        # subscribed or applied here until Market-Aligned live signals exist.
        self._remove_stale_oi_subscriptions(connection, [], subscribed)

    def run(self, stop: threading.Event) -> None:
        from websockets.sync.client import connect

        backoff = 1.0
        while not stop.is_set():
            if not self.engine._market_is_open():
                self.engine._set_state(
                    connection="DISCONNECTED",
                    engine="MARKET_CLOSED",
                    message="NSE is closed; Dhan live subscriptions resume at the next market session",
                )
                stop.wait(30.0)
                backoff = 1.0
                continue
            connection = None
            try:
                self.engine._set_state(connection="RECONNECTING", engine="CONNECTING", message="Connecting to Dhan v2 quote stream")
                connection = connect(self._url(), open_timeout=30, ping_interval=20, ping_timeout=20, max_size=4 * 1024 * 1024)
                instruments = self._subscriptions()
                subscribed: set[tuple[str, str]] = set()
                self._send_subscriptions(connection, instruments, subscribed)
                self.engine.on_connected()
                backoff = 1.0
                while not stop.is_set():
                    try:
                        message = connection.recv(timeout=1.0)
                    except TimeoutError:
                        self.engine.flush_due()
                        self._refresh_oi_subscriptions(connection, subscribed)
                        continue
                    if isinstance(message, bytes):
                        for packet in parse_dhan_feed_packets(message, received_at=self.engine.clock()):
                            self.engine.on_feed_packet(packet)
                    self.engine.flush_due()
                    self._refresh_oi_subscriptions(connection, subscribed)
            except Exception as error:  # noqa: BLE001 - websocket provider errors share the reconnect policy
                self.engine.on_disconnected(reconnecting=not stop.is_set(), reason=str(error))
                if stop.wait(backoff):
                    break
                backoff = min(backoff * 2.0, 30.0)
            finally:
                if connection is not None:
                    try:
                        connection.close()
                    except Exception:  # noqa: BLE001,S110 - best-effort close after provider failure
                        pass
        self.engine.on_disconnected(reconnecting=False)
