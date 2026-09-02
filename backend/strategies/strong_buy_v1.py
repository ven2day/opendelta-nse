"""STRONG_BUY_V1 — EMA 9/21 crossover above session VWAP with two-of-three confirmation.

The rules are the existing EMA/VWAP Strong Buy rules, unchanged:

1. Base buy: the fast EMA crosses above the slow EMA on the completed candle and
   that candle closes above the session VWAP.
2. Promotion to STRONG BUY needs at least two of:
   - ADX >= minimum_adx with +DI > -DI,
   - relative volume >= minimum_rvol,
   - the last *completed* higher-timeframe (15-minute) fast EMA above slow EMA.
3. The signal is confirmed at that candle's close; the engine that consumes it
   enters at the next candle's open. Every lot has its own target of
   ``target_pct`` above its entry.
"""

from __future__ import annotations

import math
from typing import Any, Mapping

import pandas as pd

from backend.core import indicators
from backend.core.models import MarketContext, SignalDecision, normalize_candles
from backend.strategies.base import ConfigSchema, assert_supported, resolve_config

STRATEGY_ID = "ema_vwap_strong_buy"
STRATEGY_NAME = "Strong Buy"
STRATEGY_VERSION = "1.0.0"

CONFIG_SCHEMA: ConfigSchema = {
    "ema_fast": {"type": "integer", "default": 9, "minimum": 1, "maximum": 499, "label": "Fast EMA length"},
    "ema_slow": {"type": "integer", "default": 21, "minimum": 2, "maximum": 500, "label": "Slow EMA length"},
    "adx_length": {"type": "integer", "default": 14, "minimum": 1, "maximum": 500, "label": "ADX/DMI length"},
    "adx_smoothing": {"type": "integer", "default": 14, "minimum": 1, "maximum": 500, "label": "ADX smoothing"},
    "minimum_adx": {"type": "number", "default": 20.0, "minimum": 0.0, "maximum": 100.0, "label": "Minimum ADX"},
    "rvol_length": {"type": "integer", "default": 20, "minimum": 1, "maximum": 500, "label": "Relative volume length"},
    "minimum_rvol": {"type": "number", "default": 1.2, "minimum": 0.0, "maximum": 100.0, "label": "Minimum relative volume"},
    "higher_timeframe_minutes": {"type": "integer", "default": 15, "enum": [15], "label": "Confirmation timeframe (minutes)"},
    "minimum_confirmations": {"type": "integer", "default": 2, "enum": [2], "label": "Confirmations required"},
    "target_pct": {"type": "number", "default": 1.0, "minimum": 0.01, "maximum": 100.0, "label": "Profit target %"},
    "initial_quantity": {"type": "integer", "default": 100, "minimum": 1, "maximum": 1_000_000, "label": "First lot quantity"},
    "allow_additional_buys": {"type": "boolean", "default": True, "label": "Allow additional lots while holding"},
    "additional_quantity_pct": {"type": "number", "default": 50.0, "minimum": 0.01, "maximum": 100.0, "label": "Additional lot size %"},
    "additional_sizing_mode": {"type": "string", "default": "REDUCE_EVERY_NEW_LOT", "enum": ["REDUCE_EVERY_NEW_LOT", "FIXED_PERCENTAGE_OF_FIRST_LOT"], "label": "Additional lot sizing"},
    "minimum_quantity": {"type": "integer", "default": 1, "minimum": 1, "maximum": 1_000_000, "label": "Minimum lot quantity"},
    "maximum_entries_per_cycle": {"type": "integer", "default": 10, "minimum": 1, "maximum": 100, "label": "Maximum lots per cycle"},
}


class StrongBuyV1:
    strategy_id = STRATEGY_ID
    name = STRATEGY_NAME
    version = STRATEGY_VERSION
    supported_markets = ("NSE", "CRYPTO")
    supported_timeframes = ("5m",)
    config_schema = CONFIG_SCHEMA

    # ---- configuration ---------------------------------------------------

    def resolve(self, config: Mapping[str, Any] | None) -> dict[str, Any]:
        """Defaults merged with ``config`` and validated; this is the snapshot engines store."""
        resolved = resolve_config(self.config_schema, config)
        self._check(resolved)
        return resolved

    def validate_config(self, config: Mapping[str, Any]) -> None:
        self.resolve(config)

    @staticmethod
    def _check(cfg: Mapping[str, Any]) -> None:
        if not 1 <= cfg["ema_fast"] < cfg["ema_slow"] <= 500:
            raise ValueError("EMA lengths must satisfy 1 <= fast < slow <= 500")
        if min(cfg["adx_length"], cfg["adx_smoothing"], cfg["rvol_length"]) < 1:
            raise ValueError("Indicator lengths must be positive")
        if cfg["minimum_adx"] < 0 or cfg["minimum_rvol"] < 0:
            raise ValueError("ADX and RVOL thresholds cannot be negative")
        if cfg["higher_timeframe_minutes"] != 15 or cfg["minimum_confirmations"] != 2:
            raise ValueError("STRONG BUY uses confirmed 15-minute alignment and exactly two of three confirmations")
        if cfg["target_pct"] <= 0:
            raise ValueError("Profit target must be greater than zero")
        if cfg["initial_quantity"] < 1 or cfg["minimum_quantity"] < 1:
            raise ValueError("Lot quantities must be positive whole shares")
        if not 0 < cfg["additional_quantity_pct"] <= 100:
            raise ValueError("Additional quantity percentage must be greater than zero and at most 100")
        if not 1 <= cfg["maximum_entries_per_cycle"] <= 100:
            raise ValueError("Maximum entries per cycle must be between 1 and 100")

    def required_history(self, config: Mapping[str, Any]) -> int:
        cfg = self.resolve(config)
        return max(cfg["ema_slow"] * 3, cfg["adx_length"] + cfg["adx_smoothing"], cfg["rvol_length"]) + 10

    @staticmethod
    def lot_quantity(cfg: Mapping[str, Any], entry_number: int) -> int:
        """Quantity for the ``entry_number``-th lot of a cycle (0 = first lot)."""
        if entry_number < 0:
            raise ValueError("Entry number cannot be negative")
        ratio = cfg["additional_quantity_pct"] / 100.0
        if entry_number == 0:
            raw = cfg["initial_quantity"]
        elif cfg["additional_sizing_mode"] == "REDUCE_EVERY_NEW_LOT":
            raw = cfg["initial_quantity"] * math.pow(ratio, entry_number)
        else:
            raw = cfg["initial_quantity"] * ratio
        return max(cfg["minimum_quantity"], math.floor(raw))

    # ---- evaluation --------------------------------------------------------

    def compute_indicators(self, candles: pd.DataFrame, cfg: Mapping[str, Any], timezone: str) -> pd.DataFrame:
        """The full causal indicator table; the backtest and live paths both use this."""
        data = normalize_candles(candles, timezone)
        data["EmaFast"] = indicators.ema(data["Close"], cfg["ema_fast"])
        data["EmaSlow"] = indicators.ema(data["Close"], cfg["ema_slow"])
        data["SessionVwap"] = indicators.session_vwap(data, timezone)
        dmi = indicators.directional_movement(data, cfg["adx_length"], cfg["adx_smoothing"])
        data["PlusDi"], data["MinusDi"], data["Adx"] = dmi["PlusDi"], dmi["MinusDi"], dmi["Adx"]
        data["RelativeVolume"] = indicators.relative_volume(data["Volume"], cfg["rvol_length"])
        data["HtfAlignment"] = indicators.higher_timeframe_ema_alignment(
            data["Close"], cfg["higher_timeframe_minutes"], cfg["ema_fast"], cfg["ema_slow"]
        )
        data["AdxConfirmation"] = (data["Adx"] >= cfg["minimum_adx"]) & (data["PlusDi"] > data["MinusDi"])
        data["RvolConfirmation"] = data["RelativeVolume"] >= cfg["minimum_rvol"]
        data["ConfirmationScore"] = data[["AdxConfirmation", "RvolConfirmation", "HtfAlignment"]].astype(int).sum(axis=1)
        data["BullishCross"] = (data["EmaFast"] > data["EmaSlow"]) & (data["EmaFast"].shift() <= data["EmaSlow"].shift())
        data["BaseBuy"] = data["BullishCross"] & (data["Close"] > data["SessionVwap"])
        data["StrongBuy"] = data["BaseBuy"] & (data["ConfirmationScore"] >= cfg["minimum_confirmations"])
        return data

    def evaluate(self, candles: pd.DataFrame, market_context: MarketContext, config: Mapping[str, Any]) -> SignalDecision:
        assert_supported(self, market_context)
        cfg = self.resolve(config)
        data = self.compute_indicators(candles, cfg, market_context.timezone)
        common = {
            "strategy_id": self.strategy_id,
            "strategy_version": self.version,
            "market": market_context.market,
            "symbol": market_context.symbol,
            "timeframe": market_context.timeframe,
            "configuration_snapshot": cfg,
        }
        if data.empty:
            stamp = market_context.as_of or pd.Timestamp.now(tz=market_context.timezone)
            return SignalDecision(decision="NONE", candle_timestamp=pd.Timestamp(stamp).to_pydatetime(), signal_price=None, target_price=None, reasons=("NO_COMPLETED_CANDLES",), **common)
        row = data.iloc[-1]
        stamp = data.index[-1].to_pydatetime()
        close = float(row["Close"])
        snapshot = {
            "close": close,
            "emaFast": _finite(row["EmaFast"]),
            "emaSlow": _finite(row["EmaSlow"]),
            "sessionVwap": _finite(row["SessionVwap"]),
            "adx": _finite(row["Adx"]),
            "plusDi": _finite(row["PlusDi"]),
            "minusDi": _finite(row["MinusDi"]),
            "relativeVolume": _finite(row["RelativeVolume"]),
            "confirmationScore": int(row["ConfirmationScore"]),
            "bullishCross": bool(row["BullishCross"]),
            "aboveSessionVwap": bool(row["Close"] > row["SessionVwap"]) if pd.notna(row["SessionVwap"]) else False,
            "adxConfirmation": bool(row["AdxConfirmation"]),
            "rvolConfirmation": bool(row["RvolConfirmation"]),
            "htfConfirmation": bool(row["HtfAlignment"]),
        }
        if len(data) < self.required_history(cfg):
            return SignalDecision(decision="NONE", candle_timestamp=stamp, signal_price=close, target_price=None, reasons=("INSUFFICIENT_HISTORY",), indicators=snapshot, **common)
        if not bool(row["StrongBuy"]):
            reasons = []
            if not snapshot["bullishCross"]:
                reasons.append("NO_EMA_BULLISH_CROSS")
            if not snapshot["aboveSessionVwap"]:
                reasons.append("CLOSE_NOT_ABOVE_SESSION_VWAP")
            if snapshot["confirmationScore"] < cfg["minimum_confirmations"]:
                reasons.append("INSUFFICIENT_CONFIRMATIONS")
            return SignalDecision(decision="NONE", candle_timestamp=stamp, signal_price=close, target_price=None, reasons=tuple(reasons), indicators=snapshot, **common)
        reasons = ["EMA_BULLISH_CROSS", "CLOSE_ABOVE_SESSION_VWAP"]
        if snapshot["adxConfirmation"]:
            reasons.append("ADX_DMI_CONFIRMED")
        if snapshot["rvolConfirmation"]:
            reasons.append("RELATIVE_VOLUME_CONFIRMED")
        if snapshot["htfConfirmation"]:
            reasons.append("HIGHER_TIMEFRAME_EMA_ALIGNED")
        return SignalDecision(
            decision="BUY",
            candle_timestamp=stamp,
            signal_price=close,
            target_price=round(close * (1 + cfg["target_pct"] / 100), 4),
            reasons=tuple(reasons),
            indicators=snapshot,
            **common,
        )


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None
