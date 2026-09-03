"""FIFO inventory accounting shared by backtests and paper trading.

Strategies may keep independent target tranches, but a broker sell order names
only a symbol and quantity.  This ledger therefore keeps acquisition inventory
separate from the tranche that requested a sale.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from backend.markets.base import Fill


class SellFeeModel(Protocol):
    def sell(self, price: float, quantity: float) -> Fill: ...


@dataclass
class InventorySlice:
    acquisition_id: str
    acquired_at: datetime
    price: float
    quantity: float
    fees: float


@dataclass(frozen=True)
class FifoAllocation:
    acquisition_id: str
    quantity: float
    price: float
    fees: float


@dataclass(frozen=True)
class FifoMatch:
    quantity: float
    cost_basis_price: float
    entry_fees: float
    allocations: tuple[FifoAllocation, ...]

    @property
    def acquisition_cost(self) -> float:
        return self.cost_basis_price * self.quantity + self.entry_fees


def net_profit_target_price(match: FifoMatch, fee_model: SellFeeModel, target_pct: float) -> float:
    """Raw market price needed to retain ``target_pct`` after all execution costs."""
    if target_pct <= 0:
        raise ValueError("FIFO profit target must be positive")
    desired_proceeds = match.acquisition_cost * (1 + float(target_pct) / 100)

    def proceeds(raw_price: float) -> float:
        fill = fee_model.sell(raw_price, match.quantity)
        return fill.price * match.quantity - fill.fees

    low = 0.0
    high = max(match.cost_basis_price * (1 + float(target_pct) / 100), 1.0)
    while proceeds(high) < desired_proceeds:
        high *= 2
        if not math.isfinite(high):
            raise ValueError("FIFO profit target cannot be represented")
    for _ in range(64):
        middle = (low + high) / 2
        if proceeds(middle) >= desired_proceeds:
            high = middle
        else:
            low = middle
    # NSE targets are placed in paise. Never round down below the configured
    # net return after fees and adverse sell slippage.
    return math.ceil(high * 100) / 100


class FifoInventory:
    """Mutable FIFO queue with proportional entry-fee allocation."""

    _EPSILON = 1e-9

    def __init__(self) -> None:
        self._slices: list[InventorySlice] = []

    def add(self, acquisition_id: str, acquired_at: datetime, price: float, quantity: float, fees: float = 0.0) -> None:
        if quantity <= 0 or price <= 0 or fees < 0:
            raise ValueError("FIFO acquisitions need positive price/quantity and non-negative fees")
        self._slices.append(InventorySlice(str(acquisition_id), acquired_at, float(price), float(quantity), float(fees)))

    def consume(self, quantity: float) -> FifoMatch:
        requested = float(quantity)
        if requested <= 0:
            raise ValueError("FIFO sale quantity must be positive")
        if requested > self.quantity + self._EPSILON:
            raise ValueError(f"Cannot sell {requested:g}; FIFO inventory contains only {self.quantity:g}")
        remaining = requested
        allocations: list[FifoAllocation] = []
        while remaining > self._EPSILON:
            item = self._slices[0]
            taken = min(remaining, item.quantity)
            fee = item.fees * (taken / item.quantity)
            allocations.append(FifoAllocation(item.acquisition_id, taken, item.price, fee))
            item.quantity -= taken
            item.fees -= fee
            remaining -= taken
            if item.quantity <= self._EPSILON:
                self._slices.pop(0)
        cost = sum(item.price * item.quantity for item in allocations)
        matched_quantity = sum(item.quantity for item in allocations)
        return FifoMatch(
            quantity=matched_quantity,
            cost_basis_price=cost / matched_quantity,
            entry_fees=sum(item.fees for item in allocations),
            allocations=tuple(allocations),
        )

    def preview_allocations(self, quantities: list[float]) -> list[FifoMatch]:
        clone = FifoInventory()
        clone._slices = [InventorySlice(item.acquisition_id, item.acquired_at, item.price, item.quantity, item.fees) for item in self._slices]
        return [clone.consume(quantity) for quantity in quantities]

    @property
    def quantity(self) -> float:
        return sum(item.quantity for item in self._slices)

    @property
    def cost(self) -> float:
        return sum(item.price * item.quantity for item in self._slices)
