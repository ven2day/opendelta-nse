from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Literal


StrategyStatus = Literal["ACTIVE", "RESEARCH_ONLY", "RETIRED"]


@dataclass(frozen=True)
class StrategyDefinition:
    key: str
    version: str
    name: str
    market: str
    status: StrategyStatus
    supports_long: bool
    supports_short: bool
    paper_only: bool
    live_orders_enabled: bool
    execution_model: str
    description: str
    compatibility: tuple[str, ...]

    def public(self) -> dict[str, Any]:
        return asdict(self)


STRATEGIES: tuple[StrategyDefinition, ...] = (
    StrategyDefinition("rsi_range", "rsi-range-1.0.0", "RSI Range Strategy", "NSE", "ACTIVE", True, False, True, False, "NEXT_BAR_OPEN", "Existing RSI range semantics are preserved by the legacy adapter.", ("1m", "5m", "15m", "30m", "1h", "2h", "4h", "1d")),
    StrategyDefinition("rsi_recovery", "rsi-recovery-1.1.0", "RSI Recovery Scalping", "NSE", "ACTIVE", True, False, True, False, "SIGNAL_CLOSE_OR_NEXT_BAR_OPEN", "Existing overlapping-observation signal semantics remain unchanged.", ("1m", "5m", "15m", "30m", "1h", "2h", "4h", "1d")),
    StrategyDefinition("top_5_opening_range_breakout", "top-5-orb-1.0.0", "Top-5 Opening Range Breakout", "NSE", "RESEARCH_ONLY", True, False, True, False, "NEXT_BAR_OPEN", "Causal completed-range research strategy; rejected for live use.", ("5m",)),
    StrategyDefinition("market_aligned_rsi_scalper", "market-aligned-rsi-1.0.0", "Market-Aligned RSI Scalper", "NSE", "RETIRED", True, False, True, False, "HISTORICAL_READ_ONLY", "Historical results remain readable; new dispatch is blocked.", ("5m",)),
    StrategyDefinition("market_aligned_vwap_pullback_scalper", "market-aligned-vwap-1.0.0", "Market-Aligned VWAP Pullback Scalper", "NSE", "RETIRED", True, False, True, False, "HISTORICAL_READ_ONLY", "Retired strategy remains WATCH-only in the scanner.", ("5m",)),
    StrategyDefinition("crypto_trend_pullback_recovery", "1.0.0", "Crypto Trend Pullback Recovery", "CRYPTO", "RESEARCH_ONLY", True, True, True, False, "NEXT_BAR_OPEN", "Provider-neutral completed-candle crypto research strategy.", ("1m", "5m", "15m", "30m", "1h", "6h", "1d")),
)


class StrategyRegistry:
    def __init__(self) -> None:
        self._definitions = {item.key: item for item in STRATEGIES}

    def list(self, market: str | None = None) -> list[StrategyDefinition]:
        rows = list(self._definitions.values())
        if market:
            rows = [row for row in rows if row.market == market]
        return sorted(rows, key=lambda row: (row.market, row.name))

    def get(self, key: str, *, allow_retired_read: bool = True) -> StrategyDefinition:
        try:
            definition = self._definitions[key]
        except KeyError as error:
            raise ValueError("Strategy is not registered") from error
        if definition.status == "RETIRED" and not allow_retired_read:
            raise ValueError("Retired strategy cannot start a new run")
        return definition

    def validate(self, key: str, market: str, timeframe: str, *, for_execution: bool = False) -> StrategyDefinition:
        definition = self.get(key, allow_retired_read=not for_execution)
        if definition.market != market:
            raise ValueError("Strategy is incompatible with the selected market")
        if timeframe not in definition.compatibility:
            raise ValueError("Strategy does not support the selected timeframe")
        if for_execution and definition.live_orders_enabled is False:
            raise ValueError("Live broker execution is disabled")
        return definition
