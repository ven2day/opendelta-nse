from __future__ import annotations

import math
import uuid
from dataclasses import dataclass
from typing import Any, Literal

import numpy as np
import pandas as pd

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


def _frame(candles: pd.DataFrame) -> pd.DataFrame:
    required = ["Open", "High", "Low", "Close", "Volume"]
    missing = [name for name in required if name not in candles]
    if missing:
        raise ValueError("Strong Buy candles are missing: " + ", ".join(missing))
    data = candles[required].copy()
    data.index = pd.DatetimeIndex(data.index)
    data.index = data.index.tz_localize("Asia/Kolkata") if data.index.tz is None else data.index.tz_convert("Asia/Kolkata")
    return data.apply(pd.to_numeric, errors="coerce").dropna().sort_index()


def _rma(values: pd.Series, length: int) -> pd.Series:
    return values.ewm(alpha=1.0 / length, adjust=False, min_periods=length).mean()


def calculate_strong_buy_indicators(candles: pd.DataFrame, config: StrongBuyConfig | None = None) -> pd.DataFrame:
    cfg = (config or StrongBuyConfig()).validate()
    data = _frame(candles)
    data["EmaFast"] = data["Close"].ewm(span=cfg.ema_fast, adjust=False, min_periods=cfg.ema_fast).mean()
    data["EmaSlow"] = data["Close"].ewm(span=cfg.ema_slow, adjust=False, min_periods=cfg.ema_slow).mean()
    typical = (data["High"] + data["Low"] + data["Close"]) / 3
    session = pd.Series(data.index.date, index=data.index)
    data["SessionVwap"] = (typical * data["Volume"]).groupby(session).cumsum() / data["Volume"].groupby(session).cumsum().replace(0, np.nan)
    previous_close = data["Close"].shift()
    tr = pd.concat([data["High"] - data["Low"], (data["High"] - previous_close).abs(), (data["Low"] - previous_close).abs()], axis=1).max(axis=1)
    up, down = data["High"].diff(), -data["Low"].diff()
    plus_dm = pd.Series(np.where((up > down) & (up > 0), up, 0.0), index=data.index)
    minus_dm = pd.Series(np.where((down > up) & (down > 0), down, 0.0), index=data.index)
    atr = _rma(tr, cfg.adx_length)
    data["PlusDi"] = 100 * _rma(plus_dm, cfg.adx_length) / atr.replace(0, np.nan)
    data["MinusDi"] = 100 * _rma(minus_dm, cfg.adx_length) / atr.replace(0, np.nan)
    dx = 100 * (data["PlusDi"] - data["MinusDi"]).abs() / (data["PlusDi"] + data["MinusDi"]).replace(0, np.nan)
    data["Adx"] = _rma(dx, cfg.adx_smoothing)
    data["RelativeVolume"] = data["Volume"] / data["Volume"].rolling(cfg.rvol_length, min_periods=cfg.rvol_length).mean().replace(0, np.nan)
    completed = data["Close"].resample("15min", closed="right", label="right", origin="start_day").last().dropna()
    higher = pd.DataFrame({"Fast": completed.ewm(span=cfg.ema_fast, adjust=False, min_periods=cfg.ema_fast).mean(), "Slow": completed.ewm(span=cfg.ema_slow, adjust=False, min_periods=cfg.ema_slow).mean()}).dropna()
    if higher.empty:
        data["HtfAlignment"] = False
    else:
        left = pd.DataFrame({"timestamp": data.index})
        right = higher.reset_index().rename(columns={higher.index.name or "index": "completedAt"})
        aligned = pd.merge_asof(left, right, left_on="timestamp", right_on="completedAt", direction="backward", allow_exact_matches=False)
        data["HtfAlignment"] = (aligned["Fast"] > aligned["Slow"]).fillna(False).to_numpy()
    data["AdxConfirmation"] = (data["Adx"] >= cfg.minimum_adx) & (data["PlusDi"] > data["MinusDi"])
    data["RvolConfirmation"] = data["RelativeVolume"] >= cfg.minimum_rvol
    data["ConfirmationScore"] = data[["AdxConfirmation", "RvolConfirmation", "HtfAlignment"]].astype(int).sum(axis=1)
    data["BullishCross"] = (data["EmaFast"] > data["EmaSlow"]) & (data["EmaFast"].shift() <= data["EmaSlow"].shift())
    data["BaseBuy"] = data["BullishCross"] & (data["Close"] > data["SessionVwap"])
    data["StrongBuy"] = data["BaseBuy"] & (data["ConfirmationScore"] >= 2)
    return data


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
    for bar_index, (stamp, row) in enumerate(data.iterrows()):
        if pending:
            price = float(row["Open"])
            quantity = cfg.quantity(pending["entryNumber"])
            lot = {**pending, "lotNumber": pending["entryNumber"] + 1, "entryTimestamp": stamp.isoformat(), "entryBarIndex": bar_index, "entryPrice": round(price, 4), "quantity": quantity, "targetPct": cfg.target_pct, "targetPrice": round(price * (1 + cfg.target_pct / 100), 4), "status": "HOLDING", "exitTimestamp": None, "exitPrice": None, "realizedPnl": None, "unrealizedPnl": None}
            lot.pop("entryNumber")
            lots.append(lot); open_lots.append(lot); pending = None; max_open = max(max_open, len(open_lots))
        remaining = []
        for lot in open_lots:
            if bar_index > lot["entryBarIndex"] and float(row["High"]) >= lot["targetPrice"]:
                lot.update(status="TAKE_PROFIT_SOLD", exitTimestamp=stamp.isoformat(), exitPrice=lot["targetPrice"], realizedPnl=round((lot["targetPrice"] - lot["entryPrice"]) * lot["quantity"], 2), unrealizedPnl=0.0)
            else:
                remaining.append(lot)
        open_lots = remaining
        if not open_lots and pending is None:
            entries = 0
        if not bool(row["StrongBuy"]) or (start is not None and stamp < start):
            continue
        if not open_lots and pending is None:
            cycle += 1; entries = 0
        can_order = (not open_lots or cfg.allow_additional_buys) and entries < cfg.maximum_entries_per_cycle and bar_index + 1 < len(data)
        cycle_id = f"{symbol}-Cycle{cycle}"
        lot_id = f"{cycle_id}-Lot{entries + 1}"
        signal = {"signalId": f"{run}-{symbol}-{len(signals)+1}", "symbol": symbol, "signalTimestamp": stamp.isoformat(), "signalClose": round(float(row["Close"]), 4), "signalType": "STRONG_BUY", "confirmationScore": int(row["ConfirmationScore"]), "adx": round(float(row["Adx"]), 4), "relativeVolume": round(float(row["RelativeVolume"]), 4), "cycleId": cycle_id, "lotId": lot_id if can_order else None, "lotNumber": entries + 1 if can_order else None, "status": "PENDING_NEXT_OPEN" if can_order else "NO_ORDER", "quantity": cfg.quantity(entries) if can_order else 0}
        signals.append(signal)
        if can_order:
            pending = {"signalTimestamp": stamp.isoformat(), "confirmationScore": int(row["ConfirmationScore"]), "entryNumber": entries, "cycleId": cycle_id, "lotId": lot_id}
            entries += 1
    last_close = float(data.iloc[-1]["Close"]) if len(data) else 0
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
