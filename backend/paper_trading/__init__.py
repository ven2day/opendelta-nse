"""Paper trading: simulated execution and money tracking on top of stored live signals.

There is no broker or exchange order client anywhere in this package; every
"fill" is arithmetic against the candle stream.
"""

from backend.paper_trading.accounting import Accounting
from backend.paper_trading.broker import NO_REAL_ORDERS, PaperBroker, PaperRepositories
from backend.paper_trading.execution import ExecutionPolicy
from backend.paper_trading.portfolio import Portfolio

__all__ = ["Accounting", "ExecutionPolicy", "NO_REAL_ORDERS", "PaperBroker", "PaperRepositories", "Portfolio"]
