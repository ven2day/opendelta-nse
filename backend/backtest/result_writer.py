"""Where backtest output goes. The engine only ever holds one batch of trade rows at a time."""

from __future__ import annotations

from typing import Any, Mapping, Protocol, Sequence

from backend.data.repositories import BacktestRunRepository, BacktestTradeRepository


class ResultWriter(Protocol):
    def started(self, run_id: str) -> None: ...

    def write_trades(self, run_id: str, rows: Sequence[Mapping[str, Any]]) -> None: ...

    def progress(self, run_id: str, *, symbols_completed: int, current_symbol: str | None, failed_symbols: Sequence[Mapping[str, str]]) -> None: ...

    def cancel_requested(self, run_id: str) -> bool: ...

    def finished(self, run_id: str, *, status: str, metrics: Mapping[str, Any] | None, error: str | None = None) -> None: ...


class DatabaseResultWriter:
    def __init__(self, runs: BacktestRunRepository, trades: BacktestTradeRepository) -> None:
        self.runs = runs
        self.trades = trades

    def started(self, run_id: str) -> None:
        self.runs.mark_started(run_id)

    def write_trades(self, run_id: str, rows: Sequence[Mapping[str, Any]]) -> None:
        self.trades.insert_many(rows)

    def progress(self, run_id: str, *, symbols_completed: int, current_symbol: str | None, failed_symbols: Sequence[Mapping[str, str]]) -> None:
        self.runs.update_progress(run_id, symbols_completed=symbols_completed, current_symbol=current_symbol, failed_symbols=failed_symbols)

    def cancel_requested(self, run_id: str) -> bool:
        return self.runs.cancel_requested(run_id)

    def finished(self, run_id: str, *, status: str, metrics: Mapping[str, Any] | None, error: str | None = None) -> None:
        self.runs.finish(run_id, status=status, metrics=metrics, error=error)


class MemoryResultWriter:
    """Test double that records what a database writer would have received."""

    def __init__(self, *, keep_trades: bool = True) -> None:
        self.keep_trades = keep_trades
        self.trades: list[dict[str, Any]] = []
        self.trade_count = 0
        self.batches: list[int] = []
        self.progress_events: list[dict[str, Any]] = []
        self.status: str | None = None
        self.metrics: dict[str, Any] | None = None
        self.error: str | None = None
        self.started_runs: list[str] = []
        self.cancel_flags: set[str] = set()

    def started(self, run_id: str) -> None:
        self.started_runs.append(run_id)

    def write_trades(self, run_id: str, rows: Sequence[Mapping[str, Any]]) -> None:
        self.batches.append(len(rows))
        self.trade_count += len(rows)
        if self.keep_trades:
            self.trades.extend(dict(row) for row in rows)

    def progress(self, run_id: str, *, symbols_completed: int, current_symbol: str | None, failed_symbols: Sequence[Mapping[str, str]]) -> None:
        self.progress_events.append({"symbolsCompleted": symbols_completed, "currentSymbol": current_symbol, "failedSymbols": [dict(item) for item in failed_symbols]})

    def cancel_requested(self, run_id: str) -> bool:
        return run_id in self.cancel_flags

    def finished(self, run_id: str, *, status: str, metrics: Mapping[str, Any] | None, error: str | None = None) -> None:
        self.status = status
        self.metrics = dict(metrics) if metrics is not None else None
        self.error = error
