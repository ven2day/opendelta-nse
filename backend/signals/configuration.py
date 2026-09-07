"""Pinned strategy/timeframe identity used by durable signal deployments."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class LiveStrategyBinding:
    strategy_id: str
    timeframe: str

    @property
    def worker_key(self) -> str:
        return f"{self.strategy_id}:{self.timeframe}"

    def public(self) -> dict[str, str]:
        return {"strategyId": self.strategy_id, "timeframe": self.timeframe}
