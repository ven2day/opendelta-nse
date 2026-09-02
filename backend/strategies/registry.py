"""Strategy discovery.

Every component that needs a strategy (screener, backtest, live signals, paper
trading, the API, the settings UI) asks the registry; none of them may switch
on a strategy name themselves.
"""

from __future__ import annotations

from typing import Any, Iterator, Mapping

from backend.core.models import Market
from backend.strategies.base import Strategy, default_config


class StrategyRegistry:
    def __init__(self) -> None:
        self._strategies: dict[str, Strategy] = {}

    def register(self, strategy: Strategy) -> Strategy:
        if not isinstance(strategy, Strategy):
            raise TypeError(f"{type(strategy).__name__} does not implement the Strategy interface")
        existing = self._strategies.get(strategy.strategy_id)
        if existing is not None and existing is not strategy:
            raise ValueError(f"A strategy with id {strategy.strategy_id!r} is already registered")
        strategy.validate_config(default_config(strategy.config_schema))
        self._strategies[strategy.strategy_id] = strategy
        return strategy

    def get(self, strategy_id: str) -> Strategy:
        try:
            return self._strategies[strategy_id]
        except KeyError as error:
            raise KeyError(f"Unknown strategy {strategy_id!r}") from error

    def list(self, market: Market | None = None) -> list[Strategy]:
        strategies = list(self._strategies.values())
        if market is not None:
            strategies = [item for item in strategies if market in item.supported_markets]
        return sorted(strategies, key=lambda item: item.strategy_id)

    def ids(self) -> list[str]:
        return sorted(self._strategies)

    def __contains__(self, strategy_id: object) -> bool:
        return strategy_id in self._strategies

    def __iter__(self) -> Iterator[Strategy]:
        return iter(self.list())

    def __len__(self) -> int:
        return len(self._strategies)

    def describe(self, market: Market | None = None) -> list[dict[str, Any]]:
        """Serializable catalogue for the API and the frontend strategy dropdown."""
        return [describe_strategy(item) for item in self.list(market)]


def describe_strategy(strategy: Strategy) -> dict[str, Any]:
    return {
        "strategyId": strategy.strategy_id,
        "name": strategy.name,
        "version": strategy.version,
        "supportedMarkets": list(strategy.supported_markets),
        "supportedTimeframes": list(strategy.supported_timeframes),
        "configSchema": {name: dict(definition) for name, definition in strategy.config_schema.items()},
        "defaults": default_config(strategy.config_schema),
    }


STRATEGIES = StrategyRegistry()
