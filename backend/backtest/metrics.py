"""Run-level metrics accumulated trade by trade, never from a full in-memory trade list."""

from __future__ import annotations

import statistics
from typing import Any, Mapping


def _round(value: float | None, digits: int = 4) -> float | None:
    return None if value is None else round(float(value), digits)


class MetricsAccumulator:
    """Holds only scalars and per-trade scalars (holding time, MAE, MFE, net P/L)."""

    def __init__(self) -> None:
        self.total_signals = 0
        self.completed_trades = 0
        self.target_hits = 0
        self.stopped_trades = 0
        self.expired_trades = 0
        self.open_trades = 0
        self.realized_pnl = 0.0
        self.unrealized_pnl = 0.0
        self.fees = 0.0
        self.slippage = 0.0
        self.winners = 0
        self._holding_minutes: list[float] = []
        self._mae: list[float] = []
        self._mfe: list[float] = []
        self._closed: list[tuple[str, float]] = []  # (exit timestamp, net pnl) for drawdown
        self.symbols_processed = 0
        self.symbols_failed = 0

    def add_signal(self, count: int = 1) -> None:
        self.total_signals += count

    def add_trade(self, trade: Mapping[str, Any]) -> None:
        status = trade["status"]
        self.fees += float(trade.get("fees") or 0.0)
        self.slippage += float(trade.get("slippage") or 0.0)
        if trade.get("mae_pct") is not None:
            self._mae.append(float(trade["mae_pct"]))
        if trade.get("mfe_pct") is not None:
            self._mfe.append(float(trade["mfe_pct"]))
        if status == "OPEN":
            self.open_trades += 1
            self.unrealized_pnl += float(trade.get("unrealized_pnl") or 0.0)
            return
        self.completed_trades += 1
        net = float(trade.get("net_pnl") or 0.0)
        self.realized_pnl += net
        if net > 0:
            self.winners += 1
        if status == "TARGET_HIT":
            self.target_hits += 1
        elif status == "STOPPED":
            self.stopped_trades += 1
        elif status == "EXPIRED":
            self.expired_trades += 1
        if trade.get("holding_minutes") is not None:
            self._holding_minutes.append(float(trade["holding_minutes"]))
        self._closed.append((str(trade.get("exit_timestamp")), net))

    def maximum_drawdown(self) -> float:
        peak = 0.0
        equity = 0.0
        worst = 0.0
        for _, net in sorted(self._closed, key=lambda item: item[0]):
            equity += net
            peak = max(peak, equity)
            worst = min(worst, equity - peak)
        return abs(worst)

    def public(self) -> dict[str, Any]:
        return {
            "totalSignals": self.total_signals,
            "completedTrades": self.completed_trades,
            "targetHits": self.target_hits,
            "stoppedTrades": self.stopped_trades,
            "expiredTrades": self.expired_trades,
            "openTrades": self.open_trades,
            "realizedPnl": _round(self.realized_pnl, 2),
            "unrealizedPnl": _round(self.unrealized_pnl, 2),
            "fees": _round(self.fees, 2),
            "slippage": _round(self.slippage, 2),
            "winRate": _round(self.winners / self.completed_trades * 100.0, 2) if self.completed_trades else None,
            "averageMaePct": _round(statistics.fmean(self._mae)) if self._mae else None,
            "averageMfePct": _round(statistics.fmean(self._mfe)) if self._mfe else None,
            "averageHoldingMinutes": _round(statistics.fmean(self._holding_minutes), 2) if self._holding_minutes else None,
            "medianHoldingMinutes": _round(statistics.median(self._holding_minutes), 2) if self._holding_minutes else None,
            "maximumDrawdown": _round(self.maximum_drawdown(), 2),
            "symbolsProcessed": self.symbols_processed,
            "symbolsFailed": self.symbols_failed,
        }
