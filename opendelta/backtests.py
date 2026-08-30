from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any, Literal

import pandas as pd

from .core import stable_id


@dataclass(frozen=True)
class ExecutionPolicy:
    entry: Literal["NEXT_BAR_OPEN"] = "NEXT_BAR_OPEN"
    collision: Literal["STOP_FIRST"] = "STOP_FIRST"
    buy_cost_bps: float = 0.0
    sell_cost_bps: float = 0.0
    slippage_bps_per_side: float = 0.0
    maximum_open_positions: int = 2
    maximum_trades_per_day: int = 5
    maximum_daily_loss: float = 0.0
    stop_after_first_loss: bool = False
    maximum_holding_bars: int = 50

    def validate(self) -> None:
        if self.entry != "NEXT_BAR_OPEN":
            raise ValueError("Platform V1 execution must use next-bar open")
        if self.collision != "STOP_FIRST":
            raise ValueError("Intrabar collision policy must be STOP_FIRST")
        if min(self.buy_cost_bps, self.sell_cost_bps, self.slippage_bps_per_side) < 0:
            raise ValueError("Costs and slippage cannot be negative")
        if not 1 <= self.maximum_open_positions <= 100:
            raise ValueError("maximum_open_positions is invalid")
        if not 1 <= self.maximum_trades_per_day <= 1_000:
            raise ValueError("maximum_trades_per_day is invalid")
        if not 1 <= self.maximum_holding_bars <= 100_000:
            raise ValueError("maximum_holding_bars is invalid")


@dataclass(frozen=True)
class TradeIntent:
    signal_timestamp: datetime
    side: Literal["LONG", "SHORT"]
    stop_price: float
    target_price: float
    quantity: float
    signal_id: str


def next_bar_entry(frame: pd.DataFrame, signal_index: int) -> tuple[int, float]:
    entry_index = signal_index + 1
    if signal_index < 0 or entry_index >= len(frame):
        raise ValueError("A completed next bar is required for entry")
    return entry_index, float(frame.iloc[entry_index]["open"])


def resolve_long_exit(candle: pd.Series, stop_price: float, target_price: float) -> tuple[str, float] | None:
    open_price = float(candle["open"])
    if open_price <= stop_price:
        return "STOP_GAP", open_price
    if open_price >= target_price:
        return "TARGET_GAP", open_price
    if float(candle["low"]) <= stop_price:
        return "STOP_EXIT", stop_price
    if float(candle["high"]) >= target_price:
        return "TARGET_EXIT", target_price
    return None


def costed_return(entry: float, exit_price: float, side: str, policy: ExecutionPolicy) -> dict[str, float]:
    direction = 1 if side == "LONG" else -1
    gross = direction * (exit_price - entry) / entry
    costs = (policy.buy_cost_bps + policy.sell_cost_bps + 2 * policy.slippage_bps_per_side) / 10_000
    return {"grossReturn": gross, "costRate": costs, "netReturn": gross - costs}


def configuration_snapshot(strategy_key: str, strategy_version: str, data_version: str, policy: ExecutionPolicy, parameters: dict[str, Any]) -> dict[str, Any]:
    snapshot = {
        "strategyKey": strategy_key,
        "strategyVersion": strategy_version,
        "dataVersion": data_version,
        "executionPolicy": asdict(policy),
        "parameters": parameters,
    }
    return {**snapshot, "configurationId": stable_id("config", snapshot)}
