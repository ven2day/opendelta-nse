"""Sizing and fill simulation for paper orders."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Mapping

from backend.markets.base import FeeModel, Fill

SIZING_MODES = ("FIXED_QUANTITY", "FIXED_CAPITAL")
PRICE_MODELS = ("SIGNAL_CLOSE", "NEXT_OPEN")


@dataclass(frozen=True)
class ExecutionPolicy:
    sizing_mode: str = "FIXED_QUANTITY"
    initial_quantity: float = 100
    capital_per_lot: float = 50_000.0
    allow_additional_buys: bool = True
    additional_quantity_pct: float = 50.0
    additional_sizing_mode: str = "REDUCE_EVERY_NEW_LOT"
    minimum_quantity: float | None = None  # defaults to 1 whole unit, or 1e-8 for fractional markets
    maximum_entries_per_cycle: int = 10
    # A completed candle close is already historical when a signal exists.
    # NEXT_OPEN is the only executable default for forward paper trading.
    price_model: str = "NEXT_OPEN"
    stop_loss_pct: float | None = None
    maximum_holding_bars: int | None = None
    whole_units: bool = True  # shares are whole; crypto quantities may be fractional

    def validate(self) -> "ExecutionPolicy":
        if self.sizing_mode not in SIZING_MODES:
            raise ValueError("sizing_mode must be FIXED_QUANTITY or FIXED_CAPITAL")
        if self.price_model not in PRICE_MODELS:
            raise ValueError("price_model must be SIGNAL_CLOSE or NEXT_OPEN")
        if self.initial_quantity <= 0 or self.capital_per_lot <= 0 or (self.minimum_quantity is not None and self.minimum_quantity <= 0):
            raise ValueError("Quantities and capital per lot must be positive")
        if not 0 < self.additional_quantity_pct <= 100:
            raise ValueError("additional_quantity_pct must be in (0, 100]")
        if self.additional_sizing_mode not in {"REDUCE_EVERY_NEW_LOT", "FIXED_PERCENTAGE_OF_FIRST_LOT"}:
            raise ValueError("Unsupported additional sizing mode")
        if not 1 <= self.maximum_entries_per_cycle <= 100:
            raise ValueError("maximum_entries_per_cycle must be between 1 and 100")
        if self.stop_loss_pct is not None and not 0 < self.stop_loss_pct < 100:
            raise ValueError("stop_loss_pct must be between 0 and 100")
        if self.maximum_holding_bars is not None and self.maximum_holding_bars < 1:
            raise ValueError("maximum_holding_bars must be at least 1")
        return self

    @classmethod
    def from_mapping(cls, values: Mapping[str, Any] | None, **defaults: Any) -> "ExecutionPolicy":
        aliases = {
            "sizingMode": "sizing_mode", "initialQuantity": "initial_quantity", "capitalPerLot": "capital_per_lot",
            "allowAdditionalBuys": "allow_additional_buys", "additionalQuantityPct": "additional_quantity_pct",
            "additionalSizingMode": "additional_sizing_mode", "minimumQuantity": "minimum_quantity",
            "maximumEntriesPerCycle": "maximum_entries_per_cycle", "priceModel": "price_model",
            "stopLossPct": "stop_loss_pct", "maximumHoldingBars": "maximum_holding_bars", "wholeUnits": "whole_units",
        }
        kwargs: dict[str, Any] = dict(defaults)
        for key, value in (values or {}).items():
            name = aliases.get(key, key)
            if name not in cls.__dataclass_fields__:
                raise ValueError(f"Unknown paper execution setting {key!r}")
            kwargs[name] = value
        return cls(**kwargs).validate()

    def public(self) -> dict[str, Any]:
        return {
            "sizingMode": self.sizing_mode, "initialQuantity": self.initial_quantity, "capitalPerLot": self.capital_per_lot,
            "allowAdditionalBuys": self.allow_additional_buys, "additionalQuantityPct": self.additional_quantity_pct,
            "additionalSizingMode": self.additional_sizing_mode, "minimumQuantity": self.effective_minimum_quantity,
            "maximumEntriesPerCycle": self.maximum_entries_per_cycle, "priceModel": self.price_model,
            "stopLossPct": self.stop_loss_pct, "maximumHoldingBars": self.maximum_holding_bars, "wholeUnits": self.whole_units,
        }

    # ---- sizing ------------------------------------------------------------------

    def lot_quantity(self, entry_number: int, price: float) -> float:
        """Quantity for the ``entry_number``-th lot of a cycle (0 = first lot)."""
        if entry_number < 0:
            raise ValueError("Entry number cannot be negative")
        base = self.initial_quantity if self.sizing_mode == "FIXED_QUANTITY" else self.capital_per_lot / price
        ratio = self.additional_quantity_pct / 100.0
        if entry_number == 0:
            raw = base
        elif self.additional_sizing_mode == "REDUCE_EVERY_NEW_LOT":
            raw = base * math.pow(ratio, entry_number)
        else:
            raw = base * ratio
        quantity = math.floor(raw) if self.whole_units else round(raw, 8)
        return max(self.effective_minimum_quantity, quantity)

    @property
    def effective_minimum_quantity(self) -> float:
        if self.minimum_quantity is not None:
            return self.minimum_quantity
        return 1.0 if self.whole_units else 1e-8

    # ---- fills -------------------------------------------------------------------

    @staticmethod
    def buy(fees: FeeModel, price: float, quantity: float) -> Fill:
        return fees.buy(price, quantity)

    @staticmethod
    def sell(fees: FeeModel, price: float, quantity: float) -> Fill:
        return fees.sell(price, quantity)

    def targets(self, entry_price: float, target_pct: float, entry_time: datetime, bar_minutes: int) -> tuple[float, float | None, datetime | None]:
        target = round(entry_price * (1 + target_pct / 100), 4)
        stop = round(entry_price * (1 - self.stop_loss_pct / 100), 4) if self.stop_loss_pct is not None else None
        expires = entry_time + timedelta(minutes=bar_minutes * self.maximum_holding_bars) if self.maximum_holding_bars else None
        return target, stop, expires
