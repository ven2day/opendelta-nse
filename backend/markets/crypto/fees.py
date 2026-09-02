"""Crypto spot cost model, unchanged from the existing crypto engine defaults."""

from __future__ import annotations

from dataclasses import dataclass

from backend.markets.base import Fill

MAKER_TAKER_COST_BPS = 8.0
SLIPPAGE_BPS = 2.0


@dataclass(frozen=True)
class CryptoFeeModel:
    maker_taker_cost_bps: float = MAKER_TAKER_COST_BPS
    slippage_bps: float = SLIPPAGE_BPS

    def __post_init__(self) -> None:
        if min(self.maker_taker_cost_bps, self.slippage_bps) < 0:
            raise ValueError("Crypto costs and slippage cannot be negative")

    @property
    def _cost_rate(self) -> float:
        return self.maker_taker_cost_bps / 10_000.0

    @property
    def _slip_rate(self) -> float:
        return self.slippage_bps / 10_000.0

    def buy(self, price: float, quantity: float) -> Fill:
        executed = price * (1 + self._slip_rate)
        return Fill(price=executed, fees=executed * quantity * self._cost_rate, slippage=(executed - price) * quantity)

    def sell(self, price: float, quantity: float) -> Fill:
        executed = price * (1 - self._slip_rate)
        return Fill(price=executed, fees=executed * quantity * self._cost_rate, slippage=(price - executed) * quantity)

    def public(self) -> dict[str, float]:
        return {"makerTakerCostBps": self.maker_taker_cost_bps, "slippageBps": self.slippage_bps}
