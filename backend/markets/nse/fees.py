"""NSE equity cost model, unchanged from the existing backtest engines."""

from __future__ import annotations

from dataclasses import dataclass

from backend.markets.base import Fill

VARIABLE_FEE_RATE = 0.00111  # brokerage + STT + exchange + GST, per side, on turnover
FIXED_FEE_PER_ORDER = 20.0  # INR
SLIPPAGE_RATE = 0.0005  # 0.05% adverse per side


@dataclass(frozen=True)
class NseFeeModel:
    variable_fee_rate: float = VARIABLE_FEE_RATE
    fixed_fee_per_order: float = FIXED_FEE_PER_ORDER
    slippage_rate: float = SLIPPAGE_RATE

    def __post_init__(self) -> None:
        if min(self.variable_fee_rate, self.fixed_fee_per_order, self.slippage_rate) < 0:
            raise ValueError("NSE fees and slippage cannot be negative")

    def buy(self, price: float, quantity: float) -> Fill:
        executed = price * (1 + self.slippage_rate)
        turnover = executed * quantity
        return Fill(price=executed, fees=turnover * self.variable_fee_rate + self.fixed_fee_per_order, slippage=(executed - price) * quantity)

    def sell(self, price: float, quantity: float) -> Fill:
        executed = price * (1 - self.slippage_rate)
        turnover = executed * quantity
        return Fill(price=executed, fees=turnover * self.variable_fee_rate + self.fixed_fee_per_order, slippage=(price - executed) * quantity)

    def public(self) -> dict[str, float]:
        return {"variableFeeRate": self.variable_fee_rate, "fixedFeePerOrder": self.fixed_fee_per_order, "slippageRate": self.slippage_rate}
