"""Portable five-minute momentum scalper for NSE and Crypto.

This is deliberately separate from RSI Dip Ladder: it requires short-term trend,
session VWAP, RSI recovery and relative-volume confirmation on the same closed bar.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any

import pandas as pd

from backend.core import indicators
from backend.core.models import MarketContext, SignalDecision, normalize_candles
from backend.strategies.base import ConfigSchema, assert_supported, resolve_config

CONFIG_SCHEMA: ConfigSchema = {
    "ema_fast": {"type": "integer", "default": 9, "minimum": 2, "maximum": 50, "label": "Fast EMA"},
    "ema_slow": {"type": "integer", "default": 20, "minimum": 3, "maximum": 100, "label": "Slow EMA"},
    "rsi_length": {"type": "integer", "default": 14, "minimum": 2, "maximum": 100, "label": "RSI length"},
    "rsi_trigger": {"type": "number", "default": 50.0, "minimum": 20, "maximum": 80, "label": "RSI trigger"},
    "rvol_length": {"type": "integer", "default": 20, "minimum": 2, "maximum": 200, "label": "RVOL length"},
    "minimum_rvol": {"type": "number", "default": 1.5, "minimum": 0.1, "maximum": 20, "label": "Minimum RVOL"},
    "target_pct": {"type": "number", "default": 0.75, "minimum": 0.05, "maximum": 10, "label": "Profit target %"},
}


class MomentumScalperV1:
    strategy_id = "momentum_scalper_v1"
    name = "5m Momentum Scalper"
    version = "1.0.0"
    supported_markets = ("NSE", "CRYPTO")
    supported_timeframes = ("5m",)
    config_schema = CONFIG_SCHEMA

    def resolve(self, config: Mapping[str, Any] | None) -> dict[str, Any]:
        cfg = resolve_config(self.config_schema, config)
        if cfg["ema_fast"] >= cfg["ema_slow"]:
            raise ValueError("ema_fast must be lower than ema_slow")
        return cfg

    def validate_config(self, config: Mapping[str, Any]) -> None:
        self.resolve(config)

    def required_history(self, config: Mapping[str, Any]) -> int:
        cfg = self.resolve(config)
        return max(cfg["ema_slow"], cfg["rsi_length"], cfg["rvol_length"]) + 2

    def compute_indicators(self, candles: pd.DataFrame, cfg: Mapping[str, Any], timezone: str) -> pd.DataFrame:
        data = normalize_candles(candles, timezone)
        data["EmaFast"] = indicators.ema(data["Close"], cfg["ema_fast"])
        data["EmaSlow"] = indicators.ema(data["Close"], cfg["ema_slow"])
        data["Rsi"] = indicators.wilder_rsi(data["Close"], cfg["rsi_length"])
        data["Vwap"] = indicators.session_vwap(data, timezone)
        data["Rvol"] = indicators.relative_volume(data["Volume"], cfg["rvol_length"])
        data["Buy"] = ((data["EmaFast"] > data["EmaSlow"]) & (data["EmaFast"] > data["EmaFast"].shift())
                       & (data["Close"] > data["Vwap"]) & (data["Rsi"] >= cfg["rsi_trigger"])
                       & (data["Rsi"].shift() < cfg["rsi_trigger"]) & (data["Rvol"] >= cfg["minimum_rvol"]))
        return data

    def decision_frame(self, candles: pd.DataFrame, market_context: MarketContext, config: Mapping[str, Any]) -> pd.DataFrame:
        assert_supported(self, market_context)
        cfg = self.resolve(config)
        data = self.compute_indicators(candles, cfg, market_context.timezone)
        eligible = pd.Series(range(len(data)), index=data.index) >= self.required_history(cfg) - 1
        buy = data["Buy"].fillna(False) & eligible
        frame = data[["Open", "High", "Low", "Close", "Volume"]].copy()
        frame["Decision"] = pd.Series("NONE", index=data.index).where(~buy, "BUY")
        frame["SignalPrice"] = data["Close"]
        frame["TargetPrice"] = (data["Close"] * (1 + cfg["target_pct"] / 100)).round(4).where(buy)
        frame["StopPrice"] = pd.Series(float("nan"), index=data.index)
        return frame

    def evaluate(self, candles: pd.DataFrame, market_context: MarketContext, config: Mapping[str, Any]) -> SignalDecision:
        cfg = self.resolve(config)
        data = self.compute_indicators(candles, cfg, market_context.timezone)
        stamp = data.index[-1].to_pydatetime() if len(data) else pd.Timestamp.now(tz=market_context.timezone).to_pydatetime()
        close = float(data["Close"].iloc[-1]) if len(data) else None
        buy = len(data) >= self.required_history(cfg) and bool(data["Buy"].iloc[-1])
        values = data.iloc[-1] if len(data) else {}
        snapshot = {key: _finite(values.get(column)) for key, column in (("emaFast", "EmaFast"), ("emaSlow", "EmaSlow"), ("rsi", "Rsi"), ("vwap", "Vwap"), ("relativeVolume", "Rvol"))}
        return SignalDecision(decision="BUY" if buy else "NONE", strategy_id=self.strategy_id,
                              strategy_version=self.version, market=market_context.market, symbol=market_context.symbol,
                              timeframe=market_context.timeframe, candle_timestamp=stamp, signal_price=close,
                              target_price=round(close * (1 + cfg["target_pct"] / 100), 4) if buy and close else None,
                              reasons=("EMA_TREND", "RSI_RECOVERY", "ABOVE_VWAP", "RELATIVE_VOLUME") if buy else ("NO_SCALP_SETUP",),
                              indicators=snapshot, configuration_snapshot=cfg)


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None
