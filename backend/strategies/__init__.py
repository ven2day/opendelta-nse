"""Strategy plugins.

Registering a strategy is one line here. Everything else — screener,
backtest, live signals, paper trading, the API, and the settings UI — discovers
it through ``STRATEGIES``.
"""

from backend.strategies.base import Strategy
from backend.strategies.registry import STRATEGIES, StrategyRegistry, describe_strategy
from backend.strategies.strong_buy_v1 import StrongBuyV1

STRATEGIES.register(StrongBuyV1())

__all__ = ["STRATEGIES", "Strategy", "StrategyRegistry", "StrongBuyV1", "describe_strategy"]
