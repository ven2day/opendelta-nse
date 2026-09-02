from __future__ import annotations

import math
import uuid
from dataclasses import dataclass
from typing import Any, Literal

import pandas as pd

from backend.strategies.strong_buy_v1 import StrongBuyV1

STRATEGY_KEY = "ema_vwap_strong_buy"
STRATEGY_NAME = "EMA 9/21 + VWAP Strong Buy"
STRATEGY_VERSION = "ema-vwap-strong-buy-1.0.0"
STRATEGY_DESCRIPTION = (
    "EMA crossover above session VWAP, promoted to STRONG BUY when at least two "
    "of ADX/DMI, relative volume, and confirmed 15-minute EMA alignment pass."
)


@dataclass(frozen=True)
class StrongBuyConfig:
    ema_fast: int = 9
    ema_slow: int = 21
    adx_length: int = 14
    adx_smoothing: int = 14
    minimum_adx: float = 20.0
    rvol_length: int = 20
    minimum_rvol: float = 1.2
    higher_timeframe_minutes: Literal[15] = 15
    minimum_confirmations: Literal[2] = 2
    target_pct: float = 1.0
    initial_quantity: int = 100
    allow_additional_buys: bool = True
    additional_quantity_pct: float = 50.0
    additional_sizing_mode: Literal["REDUCE_EVERY_NEW_LOT", "FIXED_PERCENTAGE_OF_FIRST_LOT"] = "REDUCE_EVERY_NEW_LOT"
    minimum_quantity: int = 1
    maximum_entries_per_cycle: int = 10
    execution_model: Literal["NEXT_BAR_OPEN"] = "NEXT_BAR_OPEN"

    def validate(self) -> "StrongBuyConfig":
        if not 1 <= self.ema_fast < self.ema_slow <= 500:
            raise ValueError("EMA lengths must satisfy 1 <= fast < slow <= 500")
        if min(self.adx_length, self.adx_smoothing, self.rvol_length) < 1:
            raise ValueError("Indicator lengths must be positive")
        if self.minimum_adx < 0 or self.minimum_rvol < 0:
            raise ValueError("ADX and RVOL thresholds cannot be negative")
        if self.higher_timeframe_minutes != 15 or self.minimum_confirmations != 2:
            raise ValueError("STRONG BUY uses confirmed 15-minute alignment and exactly two of three confirmations")
        if self.target_pct <= 0:
            raise ValueError("Profit target must be greater than zero")
        if self.initial_quantity < 1 or self.minimum_quantity < 1:
            raise ValueError("Lot quantities must be positive whole shares")
        if not 0 < self.additional_quantity_pct <= 100:
            raise ValueError("Additional quantity percentage must be greater than zero and at most 100")
        if self.additional_sizing_mode not in {"REDUCE_EVERY_NEW_LOT", "FIXED_PERCENTAGE_OF_FIRST_LOT"}:
            raise ValueError("Unsupported additional sizing mode")
        if not 1 <= self.maximum_entries_per_cycle <= 100:
            raise ValueError("Maximum entries per cycle must be between 1 and 100")
        return self

    def quantity(self, entry_number: int) -> int:
        if entry_number < 0:
            raise ValueError("Entry number cannot be negative")
        ratio = self.additional_quantity_pct / 100.0
        if entry_number == 0:
            raw = self.initial_quantity
        elif self.additional_sizing_mode == "REDUCE_EVERY_NEW_LOT":
            raw = self.initial_quantity * math.pow(ratio, entry_number)
        else:
            raw = self.initial_quantity * ratio
        return max(self.minimum_quantity, math.floor(raw))

    def public(self) -> dict[str, Any]:
        return {
            "emaFast": self.ema_fast, "emaSlow": self.ema_slow,
            "adxLength": self.adx_length, "adxSmoothing": self.adx_smoothing,
            "minimumAdx": self.minimum_adx, "rvolLength": self.rvol_length,
            "minimumRvol": self.minimum_rvol, "higherTimeframe": "15m",
            "minimumConfirmations": 2, "targetPct": self.target_pct,
            "initialQuantity": self.initial_quantity,
            "allowAdditionalBuys": self.allow_additional_buys,
            "additionalQuantityPct": self.additional_quantity_pct,
            "additionalSizingMode": self.additional_sizing_mode,
            "minimumQuantity": self.minimum_quantity,
            "maximumEntriesPerCycle": self.maximum_entries_per_cycle,
            "executionModel": self.execution_model,
        }


_STRATEGY = StrongBuyV1()
_PLUGIN_FIELDS = (
    "ema_fast", "ema_slow", "adx_length", "adx_smoothing", "minimum_adx", "rvol_length", "minimum_rvol",
    "higher_timeframe_minutes", "minimum_confirmations", "target_pct", "initial_quantity", "allow_additional_buys",
    "additional_quantity_pct", "additional_sizing_mode", "minimum_quantity", "maximum_entries_per_cycle",
)


def plugin_config(config: StrongBuyConfig) -> dict[str, Any]:
    """The validated plugin configuration snapshot equivalent to ``config``."""
    return _STRATEGY.resolve({name: getattr(config, name) for name in _PLUGIN_FIELDS})


def calculate_strong_buy_indicators(candles: pd.DataFrame, config: StrongBuyConfig | None = None) -> pd.DataFrame:
    """Strong Buy indicator table, computed by the single shared STRONG_BUY_V1 evaluator."""
    cfg = (config or StrongBuyConfig()).validate()
    return _STRATEGY.compute_indicators(candles, plugin_config(cfg), "Asia/Kolkata")


def evaluate_latest_strong_buy(candles: pd.DataFrame, config: StrongBuyConfig | None = None) -> dict[str, Any] | None:
    data = calculate_strong_buy_indicators(candles, config)
    if data.empty or not bool(data.iloc[-1]["StrongBuy"]):
        return None
    row = data.iloc[-1]
    return {
        "signalTimestamp": data.index[-1], "signalClose": float(row["Close"]),
        "emaFast": float(row["EmaFast"]), "emaSlow": float(row["EmaSlow"]),
        "vwap": float(row["SessionVwap"]), "adx": float(row["Adx"]),
        "plusDi": float(row["PlusDi"]), "minusDi": float(row["MinusDi"]),
        "relativeVolume": float(row["RelativeVolume"]),
        "confirmationScore": int(row["ConfirmationScore"]),
        "adxConfirmation": bool(row["AdxConfirmation"]),
        "rvolConfirmation": bool(row["RvolConfirmation"]),
        "htfConfirmation": bool(row["HtfAlignment"]), "indicators": data,
    }


def simulate_strong_buy_symbol(symbol: str, candles: pd.DataFrame, *, timeframe: str = "5m", config: StrongBuyConfig | None = None, run_id: str | None = None, analysis_start: pd.Timestamp | None = None) -> dict[str, Any]:
    cfg = (config or StrongBuyConfig()).validate()
    if timeframe != "5m":
        raise ValueError("EMA/VWAP Strong Buy requires completed 5-minute candles")
    data = calculate_strong_buy_indicators(candles, cfg)
    start = pd.Timestamp(analysis_start) if analysis_start is not None else (data.index[0] if len(data) else None)
    if start is not None:
        start = start.tz_localize("Asia/Kolkata") if start.tzinfo is None else start.tz_convert("Asia/Kolkata")
    run = run_id or str(uuid.uuid4())
    signals, lots, open_lots = [], [], []
    pending = None
    cycle = entries = max_open = 0
    # DataFrame.iterrows() builds a full pandas Series per row (boxing every value,
    # and forcing object dtype since the row mixes bool/float/int columns) - the
    # dominant cost of this loop at scale. Reading each column as a plain numpy
    # array once upfront and indexing by bar_index is the same data, same values,
    # same per-bar control flow below - just a faster way to read it.
    timestamps = data.index
    open_values = data["Open"].to_numpy(dtype=float, copy=False)
    high_values = data["High"].to_numpy(dtype=float, copy=False)
    close_values = data["Close"].to_numpy(dtype=float, copy=False)
    strong_buy_values = data["StrongBuy"].to_numpy(dtype=bool, copy=False)
    confirmation_score_values = data["ConfirmationScore"].to_numpy(dtype=int, copy=False)
    adx_values = data["Adx"].to_numpy(dtype=float, copy=False)
    relative_volume_values = data["RelativeVolume"].to_numpy(dtype=float, copy=False)
    for bar_index, stamp in enumerate(timestamps):
        if pending:
            price = float(open_values[bar_index])
            quantity = cfg.quantity(pending["entryNumber"])
            lot = {**pending, "lotNumber": pending["entryNumber"] + 1, "entryTimestamp": stamp.isoformat(), "entryBarIndex": bar_index, "entryPrice": round(price, 4), "quantity": quantity, "targetPct": cfg.target_pct, "targetPrice": round(price * (1 + cfg.target_pct / 100), 4), "status": "HOLDING", "exitTimestamp": None, "exitPrice": None, "realizedPnl": None, "unrealizedPnl": None}
            lot.pop("entryNumber")
            lots.append(lot); open_lots.append(lot); pending = None; max_open = max(max_open, len(open_lots))
        remaining = []
        for lot in open_lots:
            if bar_index > lot["entryBarIndex"] and float(high_values[bar_index]) >= lot["targetPrice"]:
                lot.update(status="TAKE_PROFIT_SOLD", exitTimestamp=stamp.isoformat(), exitPrice=lot["targetPrice"], realizedPnl=round((lot["targetPrice"] - lot["entryPrice"]) * lot["quantity"], 2), unrealizedPnl=0.0)
            else:
                remaining.append(lot)
        open_lots = remaining
        if not open_lots and pending is None:
            entries = 0
        if not bool(strong_buy_values[bar_index]) or (start is not None and stamp < start):
            continue
        if not open_lots and pending is None:
            cycle += 1; entries = 0
        can_order = (not open_lots or cfg.allow_additional_buys) and entries < cfg.maximum_entries_per_cycle and bar_index + 1 < len(data)
        cycle_id = f"{symbol}-Cycle{cycle}"
        lot_id = f"{cycle_id}-Lot{entries + 1}"
        signal = {"signalId": f"{run}-{symbol}-{len(signals)+1}", "symbol": symbol, "signalTimestamp": stamp.isoformat(), "signalClose": round(float(close_values[bar_index]), 4), "signalType": "STRONG_BUY", "confirmationScore": int(confirmation_score_values[bar_index]), "adx": round(float(adx_values[bar_index]), 4), "relativeVolume": round(float(relative_volume_values[bar_index]), 4), "cycleId": cycle_id, "lotId": lot_id if can_order else None, "lotNumber": entries + 1 if can_order else None, "status": "PENDING_NEXT_OPEN" if can_order else "NO_ORDER", "quantity": cfg.quantity(entries) if can_order else 0}
        signals.append(signal)
        if can_order:
            pending = {"signalTimestamp": stamp.isoformat(), "confirmationScore": int(confirmation_score_values[bar_index]), "entryNumber": entries, "cycleId": cycle_id, "lotId": lot_id}
            entries += 1
    last_close = float(close_values[-1]) if len(data) else 0
    for lot in open_lots:
        lot["unrealizedPnl"] = round((last_close - lot["entryPrice"]) * lot["quantity"], 2)
    sold = [lot for lot in lots if lot["status"] == "TAKE_PROFIT_SOLD"]
    holding = [lot for lot in lots if lot["status"] == "HOLDING"]
    return {"symbol": symbol, "firstCandle": data.index[0].isoformat() if len(data) else None, "lastCandle": data.index[-1].isoformat() if len(data) else None, "bars": len(data), "strongBuySignals": len(signals), "executedLots": len(lots), "targetHits": len(sold), "openLots": len(holding), "targetHitRate": round(len(sold)/len(lots)*100, 2) if lots else 0.0, "realizedPnl": round(sum(x["realizedPnl"] or 0 for x in sold), 2), "unrealizedPnl": round(sum(x["unrealizedPnl"] or 0 for x in holding), 2), "maximumConcurrentLots": max_open, "signals": signals, "lots": lots}


def aggregate_strong_buy_results(results: list[dict[str, Any]]) -> dict[str, Any]:
    lots = [lot for result in results for lot in result["lots"]]
    sold = [lot for lot in lots if lot["status"] == "TAKE_PROFIT_SOLD"]
    holding = [lot for lot in lots if lot["status"] == "HOLDING"]
    return {"strongBuySignals": sum(r["strongBuySignals"] for r in results), "executedLots": len(lots), "takeProfitSold": len(sold), "holdingLots": len(holding), "targetHitRate": round(len(sold)/len(lots)*100, 2) if lots else 0.0, "realizedPnl": round(sum(x["realizedPnl"] or 0 for x in sold), 2), "unrealizedPnl": round(sum(x["unrealizedPnl"] or 0 for x in holding), 2)}
