from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class RiskPolicy:
    capital: float = 100_000.0
    maximum_open_positions: int = 2
    maximum_symbol_exposure_pct: float = 25.0
    maximum_sector_exposure_pct: float = 40.0
    maximum_daily_loss_pct: float = 2.0
    maximum_consecutive_losses: int = 3
    paper_only: bool = True
    live_orders_enabled: bool = False

    def validate(self) -> None:
        if self.capital <= 0:
            raise ValueError("Capital must be positive")
        if not 1 <= self.maximum_open_positions <= 100:
            raise ValueError("Maximum open positions is invalid")
        for value in (self.maximum_symbol_exposure_pct, self.maximum_sector_exposure_pct, self.maximum_daily_loss_pct):
            if not 0 < value <= 100:
                raise ValueError("Risk percentages must be in (0, 100]")
        if not 1 <= self.maximum_consecutive_losses <= 100:
            raise ValueError("Consecutive-loss limit is invalid")
        if not self.paper_only or self.live_orders_enabled:
            raise ValueError("Platform V1 must remain paper-only")


class RiskService:
    def __init__(self, policy: RiskPolicy | None = None) -> None:
        self.policy = policy or RiskPolicy()
        self.policy.validate()

    def status(self) -> dict[str, Any]:
        return {
            "policy": asdict(self.policy),
            "state": "READY_FOR_PAPER_RESEARCH",
            "warnings": ["No broker execution adapter is installed", "Unrealized P&L requires paper positions"],
            "paperOnly": True,
            "liveOrdersEnabled": False,
        }

    def evaluate(
        self,
        *,
        open_positions: int,
        proposed_notional: float,
        symbol_exposure: float,
        sector_exposure: float | None,
        daily_pnl: float,
        consecutive_losses: int,
    ) -> dict[str, Any]:
        reasons = []
        if open_positions >= self.policy.maximum_open_positions:
            reasons.append("MAXIMUM_OPEN_POSITIONS")
        if proposed_notional + symbol_exposure > self.policy.capital * self.policy.maximum_symbol_exposure_pct / 100:
            reasons.append("SYMBOL_CONCENTRATION")
        if sector_exposure is None:
            reasons.append("SECTOR_DATA_UNAVAILABLE")
        elif proposed_notional + sector_exposure > self.policy.capital * self.policy.maximum_sector_exposure_pct / 100:
            reasons.append("SECTOR_CONCENTRATION")
        if daily_pnl <= -self.policy.capital * self.policy.maximum_daily_loss_pct / 100:
            reasons.append("MAXIMUM_DAILY_LOSS")
        if consecutive_losses >= self.policy.maximum_consecutive_losses:
            reasons.append("CONSECUTIVE_LOSS_LIMIT")
        blocking = [reason for reason in reasons if reason != "SECTOR_DATA_UNAVAILABLE"]
        return {"accepted": not blocking, "reasons": reasons, "paperOnly": True, "liveOrdersEnabled": False}
