"""Explicit live-strategy bindings for each market.

One market may run several strategies, and every strategy owns its timeframe.
The plural JSON environment variable is the canonical configuration.  The old
single-strategy variables remain supported for a safe deployment transition.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Mapping


@dataclass(frozen=True)
class LiveStrategyBinding:
    strategy_id: str
    timeframe: str

    @property
    def worker_key(self) -> str:
        return f"{self.strategy_id}:{self.timeframe}"

    def public(self) -> dict[str, str]:
        return {"strategyId": self.strategy_id, "timeframe": self.timeframe}


DEFAULT_BINDINGS: Mapping[str, tuple[LiveStrategyBinding, ...]] = {
    "NSE": (LiveStrategyBinding("rsi_dip_ladder_v1", "1d"),),
    "CRYPTO": (LiveStrategyBinding("ema_vwap_strong_buy", "5m"),),
}


def live_strategy_bindings(market: str, environment: Mapping[str, str] | None = None) -> tuple[LiveStrategyBinding, ...]:
    """Resolve the ordered, enabled live strategies for ``market``.

    Canonical example::

        NSE_LIVE_STRATEGIES='[{"strategyId":"rsi_dip_ladder_v1","timeframe":"1d"}]'

    ``NSE_LIVE_STRATEGY`` plus ``NSE_LIVE_TIMEFRAME`` is accepted only as a
    backwards-compatible single binding when the plural variable is absent.
    """

    key = market.strip().upper()
    if key not in DEFAULT_BINDINGS:
        raise ValueError(f"Unsupported live-signal market {market!r}")
    values = environment if environment is not None else os.environ
    raw = str(values.get(f"{key}_LIVE_STRATEGIES", "")).strip()
    if not raw:
        legacy_strategy = str(values.get(f"{key}_LIVE_STRATEGY", "")).strip()
        if not legacy_strategy:
            return DEFAULT_BINDINGS[key]
        legacy_timeframe = str(values.get(f"{key}_LIVE_TIMEFRAME", "5m")).strip() or "5m"
        return (LiveStrategyBinding(legacy_strategy, legacy_timeframe),)

    try:
        decoded = json.loads(raw)
    except json.JSONDecodeError as error:
        raise ValueError(f"{key}_LIVE_STRATEGIES must be a JSON array: {error.msg}") from error
    if not isinstance(decoded, list) or not decoded:
        raise ValueError(f"{key}_LIVE_STRATEGIES must be a non-empty JSON array")

    bindings: list[LiveStrategyBinding] = []
    seen: set[tuple[str, str]] = set()
    for index, item in enumerate(decoded):
        if not isinstance(item, dict):
            raise ValueError(f"{key}_LIVE_STRATEGIES[{index}] must be an object")
        if item.get("enabled", True) is False:
            continue
        strategy_id = str(item.get("strategyId", "")).strip()
        timeframe = str(item.get("timeframe", "")).strip()
        if not strategy_id or not timeframe:
            raise ValueError(f"{key}_LIVE_STRATEGIES[{index}] requires strategyId and timeframe")
        identity = (strategy_id, timeframe)
        if identity in seen:
            raise ValueError(f"Duplicate live strategy binding {strategy_id!r} at {timeframe!r}")
        seen.add(identity)
        bindings.append(LiveStrategyBinding(strategy_id, timeframe))
    if not bindings:
        raise ValueError(f"{key}_LIVE_STRATEGIES must enable at least one strategy")
    return tuple(bindings)
