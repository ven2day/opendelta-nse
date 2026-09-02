"""Incremental, memory-bounded backtesting on top of the shared strategy engine."""

from backend.backtest.engine import BacktestCancelled, BacktestEngine, BacktestRequest, ExecutionSettings
from backend.backtest.metrics import MetricsAccumulator
from backend.backtest.result_writer import DatabaseResultWriter, MemoryResultWriter, ResultWriter

__all__ = [
    "BacktestCancelled",
    "BacktestEngine",
    "BacktestRequest",
    "DatabaseResultWriter",
    "ExecutionSettings",
    "MemoryResultWriter",
    "MetricsAccumulator",
    "ResultWriter",
]
