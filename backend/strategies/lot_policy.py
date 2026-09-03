"""Optional per-strategy lot policy shared by backtests and paper execution.

Strategies may publish these keys in their immutable configuration snapshot. The
engines use this module instead of duplicating price-band, dip, and capital rules.
Strategies without the keys continue to use the ordinary execution settings.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence


@dataclass(frozen=True)
class PriceBandLadder:
    price_threshold: float
    high_price_quantities: tuple[int, ...]
    low_price_quantities: tuple[int, ...]
    dip_step_pct: float
    maximum_position_capital: float

    @classmethod
    def from_config(cls, config: Mapping[str, Any] | None) -> "PriceBandLadder | None":
        values = config or {}
        if values.get("lot_sizing_mode") != "PRICE_BAND_LADDER":
            return None
        high = _positive_quantities(values.get("high_price_quantities"), "high_price_quantities")
        low = _positive_quantities(values.get("low_price_quantities"), "low_price_quantities")
        if len(high) != len(low):
            raise ValueError("High- and low-price quantity ladders must have the same number of lots")
        threshold = float(values["price_band_threshold"])
        dip = float(values["dip_step_pct"])
        capital = float(values["maximum_position_capital"])
        if threshold <= 0:
            raise ValueError("price_band_threshold must be greater than zero")
        if not 0 < dip < 100:
            raise ValueError("dip_step_pct must be between 0 and 100")
        if capital <= 0:
            raise ValueError("maximum_position_capital must be greater than zero")
        return cls(threshold, high, low, dip, capital)

    @property
    def maximum_entries(self) -> int:
        return len(self.high_price_quantities)

    def quantity(self, entry_number: int, first_entry_price: float) -> int:
        if entry_number < 0 or entry_number >= self.maximum_entries:
            raise ValueError("Entry number is outside the configured quantity ladder")
        ladder = self.high_price_quantities if first_entry_price >= self.price_threshold else self.low_price_quantities
        return ladder[entry_number]

    def dip_price(self, last_entry_price: float) -> float:
        return last_entry_price * (1 - self.dip_step_pct / 100)

    def additional_entry_allowed(self, signal_price: float, last_entry_price: float) -> bool:
        return signal_price <= self.dip_price(last_entry_price)

    def within_capital(self, current_open_capital: float, entry_price: float, quantity: float) -> bool:
        return current_open_capital + entry_price * quantity <= self.maximum_position_capital


def _positive_quantities(value: Any, name: str) -> tuple[int, ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)) or not value:
        raise ValueError(f"{name} must be a non-empty integer array")
    quantities: list[int] = []
    for item in value:
        if isinstance(item, bool) or not isinstance(item, (int, float)) or not float(item).is_integer() or int(item) <= 0:
            raise ValueError(f"{name} must contain positive whole quantities")
        quantities.append(int(item))
    return tuple(quantities)
