"""Strategy plugins.

Registering a strategy is one line here. Everything else — screener,
backtest, live signals, paper trading, the API, and the settings UI — discovers
it through ``STRATEGIES``.
"""

from backend.strategies.base import Strategy
from backend.strategies.registry import STRATEGIES, StrategyRegistry, describe_strategy
from backend.strategies.rsi_dip_ladder_v1 import RsiDipLadderV1
from backend.strategies.strong_buy_v1 import StrongBuyV1

STRATEGIES.register(StrongBuyV1())
STRATEGIES.register(RsiDipLadderV1())

__all__ = ["RsiDipLadderV1", "STRATEGIES", "Strategy", "StrategyRegistry", "StrongBuyV1", "describe_strategy"]
