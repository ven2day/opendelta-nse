"""The backtest replayer.

Processes one symbol at a time: loads that symbol's completed candles, asks the
strategy for its causal decisions, replays them with next-candle-open entries,
independent lots, fees and slippage, optional stop loss and holding limits,
writes the resulting trade rows in batches, persists progress, and releases the
symbol's data before moving on. Nothing about a symbol survives past its turn
except the trade rows already handed to the writer and a handful of metric
scalars.
"""

from __future__ import annotations

import math
import threading
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any, Callable, Mapping, Sequence

import numpy as np
import pandas as pd

from backend.backtest.metrics import MetricsAccumulator
from backend.backtest.result_writer import ResultWriter
from backend.core.models import MarketContext
from backend.markets.base import CandleSource, MarketSpec
from backend.strategies.base import Strategy, decision_frame
from backend.strategies.lot_policy import PriceBandLadder

CANCEL_CHECK_BARS = 500


class BacktestCancelled(RuntimeError):
    pass


@dataclass(frozen=True)
class ExecutionSettings:
    """Engine-side execution and sizing rules; the strategy never sees these."""

    target_pct: float | None = None  # None -> use the strategy's own target
    stop_loss_pct: float | None = None
    maximum_holding_bars: int | None = None
    initial_quantity: int = 100
    allow_additional_buys: bool = True
    additional_quantity_pct: float = 50.0
    additional_sizing_mode: str = "REDUCE_EVERY_NEW_LOT"
    minimum_quantity: int = 1
    maximum_entries_per_cycle: int = 10
    batch_size: int = 500

    def validate(self) -> "ExecutionSettings":
        if self.target_pct is not None and self.target_pct <= 0:
            raise ValueError("target_pct must be greater than zero")
        if self.stop_loss_pct is not None and not 0 < self.stop_loss_pct < 100:
            raise ValueError("stop_loss_pct must be between 0 and 100")
        if self.maximum_holding_bars is not None and self.maximum_holding_bars < 1:
            raise ValueError("maximum_holding_bars must be at least 1")
        if self.initial_quantity < 1 or self.minimum_quantity < 1:
            raise ValueError("Lot quantities must be positive")
        if not 0 < self.additional_quantity_pct <= 100:
            raise ValueError("additional_quantity_pct must be in (0, 100]")
        if self.additional_sizing_mode not in {"REDUCE_EVERY_NEW_LOT", "FIXED_PERCENTAGE_OF_FIRST_LOT"}:
            raise ValueError("Unsupported additional sizing mode")
        if not 1 <= self.maximum_entries_per_cycle <= 100:
            raise ValueError("maximum_entries_per_cycle must be between 1 and 100")
        if self.batch_size < 1:
            raise ValueError("batch_size must be positive")
        return self

    def lot_quantity(self, entry_number: int) -> int:
        ratio = self.additional_quantity_pct / 100.0
        if entry_number == 0:
            raw: float = self.initial_quantity
        elif self.additional_sizing_mode == "REDUCE_EVERY_NEW_LOT":
            raw = self.initial_quantity * math.pow(ratio, entry_number)
        else:
            raw = self.initial_quantity * ratio
        return max(self.minimum_quantity, math.floor(raw))

    def public(self) -> dict[str, Any]:
        return {
            "targetPct": self.target_pct,
            "stopLossPct": self.stop_loss_pct,
            "maximumHoldingBars": self.maximum_holding_bars,
            "initialQuantity": self.initial_quantity,
            "allowAdditionalBuys": self.allow_additional_buys,
            "additionalQuantityPct": self.additional_quantity_pct,
            "additionalSizingMode": self.additional_sizing_mode,
            "minimumQuantity": self.minimum_quantity,
            "maximumEntriesPerCycle": self.maximum_entries_per_cycle,
            "batchSize": self.batch_size,
        }

    @classmethod
    def from_mapping(cls, values: Mapping[str, Any] | None) -> "ExecutionSettings":
        aliases = {
            "targetPct": "target_pct", "stopLossPct": "stop_loss_pct", "maximumHoldingBars": "maximum_holding_bars",
            "initialQuantity": "initial_quantity", "allowAdditionalBuys": "allow_additional_buys",
            "additionalQuantityPct": "additional_quantity_pct", "additionalSizingMode": "additional_sizing_mode",
            "minimumQuantity": "minimum_quantity", "maximumEntriesPerCycle": "maximum_entries_per_cycle", "batchSize": "batch_size",
        }
        kwargs: dict[str, Any] = {}
        for key, value in (values or {}).items():
            name = aliases.get(key, key)
            if name not in cls.__dataclass_fields__:
                raise ValueError(f"Unknown execution setting {key!r}")
            kwargs[name] = value
        return cls(**kwargs).validate()


@dataclass(frozen=True)
class BacktestRequest:
    run_id: str
    market: str
    strategy_id: str
    symbols: Sequence[str]
    timeframe: str
    start_date: date
    end_date: date
    configuration: Mapping[str, Any] = field(default_factory=dict)
    execution: ExecutionSettings = field(default_factory=ExecutionSettings)

    def validate(self) -> "BacktestRequest":
        if not self.symbols:
            raise ValueError("A backtest needs at least one symbol")
        if self.end_date < self.start_date:
            raise ValueError("end_date must not be before start_date")
        self.execution.validate()
        return self


@dataclass
class _Lot:
    lot_id: str
    cycle_id: str
    lot_number: int
    signal_timestamp: datetime
    signal_price: float
    entry_bar: int
    entry_timestamp: datetime
    entry_price: float
    quantity: int
    target_price: float
    stop_price: float | None
    expires_bar: int | None
    fees: float
    slippage: float
    lowest: float
    highest: float


class BacktestEngine:
    def __init__(
        self,
        *,
        strategy: Strategy,
        market: MarketSpec,
        source: CandleSource,
        writer: ResultWriter,
        cancel_event: threading.Event | None = None,
        progress: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        self.strategy = strategy
        self.market = market
        self.source = source
        self.writer = writer
        self.cancel_event = cancel_event or threading.Event()
        self.progress = progress or (lambda values: None)

    # ---- run -------------------------------------------------------------------

    def run(self, request: BacktestRequest) -> dict[str, Any]:
        request.validate()
        config = self.strategy.resolve(request.configuration) if hasattr(self.strategy, "resolve") else dict(request.configuration)
        self.strategy.validate_config(config)
        metrics = MetricsAccumulator()
        failed: list[dict[str, str]] = []
        run_id = request.run_id
        self.writer.started(run_id)
        try:
            for index, symbol in enumerate(request.symbols):
                self._check_cancel(run_id)
                self.writer.progress(run_id, symbols_completed=index, current_symbol=symbol, failed_symbols=failed)
                self.progress({"symbolsCompleted": index, "symbolsTotal": len(request.symbols), "currentSymbol": symbol})
                try:
                    self._run_symbol(request, config, symbol, metrics)
                    metrics.symbols_processed += 1
                except BacktestCancelled:
                    raise
                except Exception as error:  # a bad symbol must not sink the run
                    failed.append({"symbol": symbol, "message": str(error)})
                    metrics.symbols_failed += 1
            self.writer.progress(run_id, symbols_completed=len(request.symbols), current_symbol=None, failed_symbols=failed)
            summary = metrics.public()
            self.writer.finished(run_id, status="COMPLETE", metrics=summary)
            return {"status": "COMPLETE", "metrics": summary, "failedSymbols": failed}
        except BacktestCancelled:
            summary = metrics.public()
            self.writer.finished(run_id, status="CANCELLED", metrics=summary)
            return {"status": "CANCELLED", "metrics": summary, "failedSymbols": failed}
        except Exception as error:
            self.writer.finished(run_id, status="FAILED", metrics=metrics.public(), error=str(error))
            raise

    def _check_cancel(self, run_id: str) -> None:
        if self.cancel_event.is_set() or self.writer.cancel_requested(run_id):
            self.cancel_event.set()
            raise BacktestCancelled(run_id)

    # ---- per symbol ------------------------------------------------------------

    def _run_symbol(self, request: BacktestRequest, config: Mapping[str, Any], symbol: str, metrics: MetricsAccumulator) -> None:
        timezone = self.market.timezone
        execution = request.execution
        ladder = PriceBandLadder.from_config(config)
        bar_minutes = self.market.minutes(request.timeframe)
        warmup = self.strategy.required_history(config)
        start = datetime.combine(request.start_date, datetime.min.time()).replace(tzinfo=_zone(timezone))
        end = datetime.combine(request.end_date, datetime.max.time()).replace(tzinfo=_zone(timezone))
        candles = self.source.candles(symbol, request.timeframe, start, end, warmup_bars=warmup)
        context = MarketContext(market=self.market.market, symbol=symbol, timeframe=request.timeframe, timezone=timezone)
        frame = decision_frame(self.strategy, candles, context, config)
        del candles
        if frame.empty:
            return
        frame = frame[frame.index <= end]
        timestamps = frame.index
        opens = frame["Open"].to_numpy(dtype=float)
        highs = frame["High"].to_numpy(dtype=float)
        lows = frame["Low"].to_numpy(dtype=float)
        closes = frame["Close"].to_numpy(dtype=float)
        decisions = (frame["Decision"] == "BUY").to_numpy()
        signal_targets = frame["TargetPrice"].to_numpy(dtype=float)
        in_window = np.asarray(timestamps >= start, dtype=bool)
        del frame

        batch: list[dict[str, Any]] = []
        open_lots: list[_Lot] = []
        pending: tuple[int, int, str] | None = None  # (signal bar, entry number, cycle id)
        cycle = 0
        entries = 0
        cycle_first_entry_price: float | None = None
        last_entry_price: float | None = None
        bars = len(timestamps)
        for bar in range(bars):
            if bar % CANCEL_CHECK_BARS == 0:
                self._check_cancel(request.run_id)
            stamp = timestamps[bar].to_pydatetime()
            if pending is not None:
                signal_bar, entry_number, cycle_id = pending
                pending = None
                entered = self._enter(
                    request, execution, config, symbol, signal_bar, bar, entry_number, cycle_id,
                    timestamps, opens, closes, signal_targets, cycle_first_entry_price, open_lots,
                )
                if entered is not None:
                    open_lots.append(entered)
                    cycle_first_entry_price = cycle_first_entry_price or entered.entry_price
                    last_entry_price = entered.entry_price
                else:
                    entries -= 1
            still_open: list[_Lot] = []
            for lot in open_lots:
                if bar > lot.entry_bar:
                    lot.lowest = min(lot.lowest, float(lows[bar]))
                    lot.highest = max(lot.highest, float(highs[bar]))
                closed = self._maybe_exit(lot, bar, stamp, highs, lows, closes)
                if closed is None:
                    still_open.append(lot)
                else:
                    row = self._trade_row(request, symbol, lot, bar_minutes, closed)
                    metrics.add_trade(row)
                    batch.append(row)
                    if len(batch) >= execution.batch_size:
                        self.writer.write_trades(request.run_id, batch)
                        batch = []
            open_lots = still_open
            if not open_lots and pending is None:
                entries = 0
                cycle_first_entry_price = None
                last_entry_price = None
            if not decisions[bar] or not in_window[bar]:
                continue
            metrics.add_signal()
            if not open_lots and pending is None:
                cycle += 1
                entries = 0
            maximum_entries = min(execution.maximum_entries_per_cycle, ladder.maximum_entries) if ladder else execution.maximum_entries_per_cycle
            can_order = (not open_lots or execution.allow_additional_buys) and entries < maximum_entries and bar + 1 < bars
            if can_order and ladder is not None and entries > 0:
                can_order = last_entry_price is not None and ladder.additional_entry_allowed(float(closes[bar]), last_entry_price)
            if can_order:
                pending = (bar, entries, f"{symbol}-Cycle{cycle}")
                entries += 1
        last_close = float(closes[-1]) if bars else 0.0
        for lot in open_lots:
            row = self._trade_row(request, symbol, lot, bar_minutes, None, last_close=last_close, last_bar=bars - 1)
            metrics.add_trade(row)
            batch.append(row)
        if batch:
            self.writer.write_trades(request.run_id, batch)

    def _enter(self, request, execution, config, symbol, signal_bar, bar, entry_number, cycle_id, timestamps, opens, closes, signal_targets, cycle_first_entry_price, open_lots) -> _Lot | None:
        reference_price = float(opens[bar])
        ladder = PriceBandLadder.from_config(config)
        indicative_fill_price = self.market.fees.buy(reference_price, 1).price
        first_price = cycle_first_entry_price or indicative_fill_price
        quantity = ladder.quantity(entry_number, first_price) if ladder else execution.lot_quantity(entry_number)
        fill = self.market.fees.buy(reference_price, quantity)
        if ladder is not None:
            current_open_capital = sum(lot.entry_price * lot.quantity for lot in open_lots)
            if not ladder.within_capital(current_open_capital, fill.price, quantity):
                return None
        entry_price = round(fill.price, 4)
        if execution.target_pct is not None:
            target = entry_price * (1 + execution.target_pct / 100)
        else:
            strategy_target = float(signal_targets[signal_bar])
            signal_close = float(closes[signal_bar])
            # Preserve the strategy's target distance relative to the actual fill.
            target = entry_price * (strategy_target / signal_close) if signal_close > 0 and not math.isnan(strategy_target) else entry_price * 1.01
        stop = entry_price * (1 - execution.stop_loss_pct / 100) if execution.stop_loss_pct is not None else None
        expires_bar = bar + execution.maximum_holding_bars if execution.maximum_holding_bars is not None else None
        return _Lot(
            lot_id=f"{cycle_id}-Lot{entry_number + 1}",
            cycle_id=cycle_id,
            lot_number=entry_number + 1,
            signal_timestamp=timestamps[signal_bar].to_pydatetime(),
            signal_price=float(closes[signal_bar]),
            entry_bar=bar,
            entry_timestamp=timestamps[bar].to_pydatetime(),
            entry_price=entry_price,
            quantity=quantity,
            target_price=round(target, 4),
            stop_price=round(stop, 4) if stop is not None else None,
            expires_bar=expires_bar,
            fees=fill.fees,
            slippage=fill.slippage,
            lowest=fill.price,
            highest=fill.price,
        )

    def _maybe_exit(self, lot: _Lot, bar: int, stamp: datetime, highs, lows, closes) -> tuple[str, float, datetime, int] | None:
        if bar <= lot.entry_bar:
            return None
        if lot.stop_price is not None and float(lows[bar]) <= lot.stop_price:
            return ("STOPPED", lot.stop_price, stamp, bar)
        if float(highs[bar]) >= lot.target_price:
            return ("TARGET_HIT", lot.target_price, stamp, bar)
        if lot.expires_bar is not None and bar >= lot.expires_bar:
            return ("EXPIRED", float(closes[bar]), stamp, bar)
        return None

    def _trade_row(self, request, symbol, lot: _Lot, bar_minutes: int, closed, *, last_close: float | None = None, last_bar: int | None = None) -> dict[str, Any]:
        base = {
            "run_id": request.run_id,
            "market": self.market.market,
            "strategy_id": self.strategy.strategy_id,
            "strategy_version": self.strategy.version,
            "symbol": symbol,
            "timeframe": request.timeframe,
            "lot_id": lot.lot_id,
            "cycle_id": lot.cycle_id,
            "lot_number": lot.lot_number,
            "signal_timestamp": lot.signal_timestamp,
            "signal_price": round(lot.signal_price, 4),
            "entry_timestamp": lot.entry_timestamp,
            "entry_price": lot.entry_price,
            "quantity": lot.quantity,
            "target_price": lot.target_price,
            "stop_price": lot.stop_price,
            "expires_at": lot.entry_timestamp + timedelta(minutes=bar_minutes * (lot.expires_bar - lot.entry_bar)) if lot.expires_bar is not None else None,
            "mae_pct": round((lot.lowest / lot.entry_price - 1) * 100, 4),
            "mfe_pct": round((lot.highest / lot.entry_price - 1) * 100, 4),
        }
        if closed is None:
            price = float(last_close or lot.entry_price)
            holding_bars = int((last_bar or lot.entry_bar) - lot.entry_bar)
            return {
                **base,
                "exit_timestamp": None,
                "exit_price": None,
                "status": "OPEN",
                "gross_pnl": 0.0,
                "fees": round(lot.fees, 4),
                "slippage": round(lot.slippage, 4),
                "net_pnl": 0.0,
                "unrealized_pnl": round((price - lot.entry_price) * lot.quantity - lot.fees, 2),
                "holding_bars": holding_bars,
                "holding_minutes": float(holding_bars * bar_minutes),
            }
        status, raw_exit, exit_stamp, exit_bar = closed
        fill = self.market.fees.sell(raw_exit, lot.quantity)
        exit_price = round(fill.price, 4)
        # Stored fields reconcile exactly: net == gross - fees on the rounded values.
        gross = round((exit_price - lot.entry_price) * lot.quantity, 2)
        fees = round(lot.fees + fill.fees, 4)
        slippage = round(lot.slippage + fill.slippage, 4)
        holding_bars = exit_bar - lot.entry_bar
        return {
            **base,
            "exit_timestamp": exit_stamp,
            "exit_price": exit_price,
            "status": status,
            "gross_pnl": gross,
            "fees": fees,
            "slippage": slippage,
            "net_pnl": round(gross - fees, 2),
            "unrealized_pnl": 0.0,
            "holding_bars": holding_bars,
            "holding_minutes": float(holding_bars * bar_minutes),
        }


def _zone(timezone: str):
    from zoneinfo import ZoneInfo

    return ZoneInfo(timezone)


def new_run_id() -> str:
    return str(uuid.uuid4())
