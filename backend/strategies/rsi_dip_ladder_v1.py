"""RSI_DIP_LADDER_V1: confirmed RSI recovery with finite price-band scaling.

The strategy emits the initial BUY only after RSI has visited the low threshold
and crossed the recovery threshold on a completed candle. Once that cycle is
open, the backtest and paper engines use completed-candle dip levels to schedule
later ladder lots at the next candle open without requiring another RSI signal.
Each resulting tranche retains its sell quantity; NSE execution derives its
profit target from the shares that FIFO would consume.
"""

from __future__ import annotations

import math
from typing import Any, Mapping

import pandas as pd

from backend.core import indicators
from backend.core.models import MarketContext, SignalDecision, normalize_candles
from backend.strategies.base import ConfigSchema, assert_supported, resolve_config
from backend.strategies.lot_policy import PriceBandLadder

STRATEGY_ID = "rsi_dip_ladder_v1"
STRATEGY_NAME = "RSI Dip Ladder"
STRATEGY_VERSION = "1.0.0"

CONFIG_SCHEMA: ConfigSchema = {
    "rsi_length": {"type": "integer", "default": 14, "minimum": 2, "maximum": 100, "label": "RSI length"},
    "rsi_low": {"type": "number", "default": 30.0, "minimum": 1.0, "maximum": 90.0, "label": "Low RSI"},
    "rsi_recovery": {"type": "number", "default": 35.0, "minimum": 2.0, "maximum": 99.0, "label": "RSI recovery"},
    "setup_expiry_bars": {"type": "integer", "default": 24, "minimum": 1, "maximum": 500, "label": "RSI setup expiry (bars)"},
    "target_pct": {"type": "number", "default": 5.0, "minimum": 0.01, "maximum": 100.0, "label": "Profit target %"},
    "lot_sizing_mode": {"type": "string", "default": "PRICE_BAND_LADDER", "enum": ["PRICE_BAND_LADDER"], "label": "Lot sizing"},
    "price_band_threshold": {"type": "number", "default": 1000.0, "minimum": 0.01, "label": "High-price threshold"},
    "high_price_quantities": {"type": "integer_array", "default": [5, 10, 25, 50], "minimum": 1, "minItems": 1, "maxItems": 10, "label": "Quantities at or above threshold"},
    "low_price_quantities": {"type": "integer_array", "default": [10, 20, 50, 100], "minimum": 1, "minItems": 1, "maxItems": 10, "label": "Quantities below threshold"},
    "dip_step_pct": {"type": "number", "default": 5.0, "minimum": 0.01, "maximum": 99.0, "label": "Additional-buy dip %"},
    "maximum_position_capital": {"type": "number", "default": 250000.0, "minimum": 1.0, "label": "Maximum open capital per symbol"},
}


class RsiDipLadderV1:
    strategy_id = STRATEGY_ID
    name = STRATEGY_NAME
    version = STRATEGY_VERSION
    supported_markets = ("NSE",)
    supported_timeframes = ("5m", "15m", "30m", "1h", "1d")
    config_schema = CONFIG_SCHEMA

    def resolve(self, config: Mapping[str, Any] | None) -> dict[str, Any]:
        resolved = resolve_config(self.config_schema, config)
        self._check(resolved)
        return resolved

    def validate_config(self, config: Mapping[str, Any]) -> None:
        self.resolve(config)

    @staticmethod
    def _check(cfg: Mapping[str, Any]) -> None:
        if cfg["rsi_low"] >= cfg["rsi_recovery"]:
            raise ValueError("rsi_low must be lower than rsi_recovery")
        PriceBandLadder.from_config(cfg)

    def required_history(self, config: Mapping[str, Any]) -> int:
        return self.resolve(config)["rsi_length"] + 2

    def compute_indicators(self, candles: pd.DataFrame, cfg: Mapping[str, Any], timezone: str) -> pd.DataFrame:
        data = normalize_candles(candles, timezone)
        data["Rsi"] = indicators.wilder_rsi(data["Close"], cfg["rsi_length"])
        armed = False
        armed_bars = 0
        recoveries: list[bool] = []
        previous = float("nan")
        for value in data["Rsi"]:
            current = float(value) if pd.notna(value) else float("nan")
            if math.isfinite(current) and current <= cfg["rsi_low"]:
                armed = True
                armed_bars = 0
            elif armed:
                armed_bars += 1
                if armed_bars > cfg["setup_expiry_bars"]:
                    armed = False
            recovered = bool(armed and math.isfinite(previous) and previous < cfg["rsi_recovery"] <= current)
            recoveries.append(recovered)
            if recovered:
                armed = False
            previous = current
        data["RsiRecovery"] = recoveries
        return data

    def decision_frame(self, candles: pd.DataFrame, market_context: MarketContext, config: Mapping[str, Any]) -> pd.DataFrame:
        assert_supported(self, market_context)
        cfg = self.resolve(config)
        data = self.compute_indicators(candles, cfg, market_context.timezone)
        eligible = pd.Series(range(len(data)), index=data.index) >= self.required_history(cfg) - 1
        buy = data["RsiRecovery"].astype(bool) & eligible
        frame = data[["Open", "High", "Low", "Close", "Volume"]].copy()
        frame["Decision"] = pd.Series("NONE", index=data.index).where(~buy, "BUY")
        frame["SignalPrice"] = data["Close"]
        frame["TargetPrice"] = (data["Close"] * (1 + cfg["target_pct"] / 100)).round(4).where(buy)
        frame["StopPrice"] = pd.Series(float("nan"), index=data.index)
        return frame

    def evaluate(self, candles: pd.DataFrame, market_context: MarketContext, config: Mapping[str, Any]) -> SignalDecision:
        assert_supported(self, market_context)
        cfg = self.resolve(config)
        data = self.compute_indicators(candles, cfg, market_context.timezone)
        stamp = (data.index[-1] if len(data) else pd.Timestamp(market_context.as_of or pd.Timestamp.now(tz=market_context.timezone))).to_pydatetime()
        close = float(data["Close"].iloc[-1]) if len(data) else None
        rsi = _finite(data["Rsi"].iloc[-1]) if len(data) else None
        common = dict(strategy_id=self.strategy_id, strategy_version=self.version, market=market_context.market, symbol=market_context.symbol, timeframe=market_context.timeframe, configuration_snapshot=cfg)
        if len(data) < self.required_history(cfg):
            return SignalDecision(decision="NONE", candle_timestamp=stamp, signal_price=close, target_price=None, reasons=("INSUFFICIENT_HISTORY",), indicators={"rsi": rsi}, **common)
        recovered = bool(data["RsiRecovery"].iloc[-1])
        if not recovered:
            return SignalDecision(decision="NONE", candle_timestamp=stamp, signal_price=close, target_price=None, reasons=("NO_RSI_RECOVERY",), indicators={"rsi": rsi}, **common)
        return SignalDecision(decision="BUY", candle_timestamp=stamp, signal_price=close, target_price=round(close * (1 + cfg["target_pct"] / 100), 4), reasons=("RSI_LOW_ARMED", "RSI_RECOVERED"), indicators={"rsi": rsi}, **common)


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None
