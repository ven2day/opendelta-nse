"""Backtest engine guarantees: incremental writes, bounded memory, cost model, exits, cancellation, causality."""

from __future__ import annotations

import threading
import tracemalloc
import unittest
import zlib
from datetime import date, datetime, timedelta

import numpy as np
import pandas as pd

from backend.backtest import BacktestEngine, BacktestRequest, ExecutionSettings, MemoryResultWriter
from backend.backtest.metrics import MetricsAccumulator
from backend.core.models import MarketContext
from backend.markets.base import market_spec
from backend.markets.crypto.fees import CryptoFeeModel
from backend.markets.nse.fees import NseFeeModel
from backend.strategies import STRATEGIES
from backend.strategies.base import decision_frame
from test_strategy_engine import synthetic_nse_candles  # tests/ is on sys.path under pytest

IST = "Asia/Kolkata"


def fifo_target_return_pct(trade: dict) -> float:
    entry_fees = sum(float(item["fees"]) for item in trade["fifo_allocations"])
    acquisition_cost = float(trade["cost_basis_price"]) * float(trade["quantity"]) + entry_fees
    fill = NseFeeModel().sell(float(trade["target_price"]), float(trade["quantity"]))
    return (fill.price * float(trade["quantity"]) - fill.fees - acquisition_cost) / acquisition_cost * 100


class SyntheticSource:
    """Generates a distinct deterministic history per symbol on demand."""

    def __init__(self, days: int = 12, broken: set[str] | None = None) -> None:
        self.days = days
        self.broken = broken or set()
        self.calls: list[str] = []

    def candles(self, symbol: str, timeframe: str, start: datetime, end: datetime, *, warmup_bars: int) -> pd.DataFrame:
        self.calls.append(symbol)
        if symbol in self.broken:
            raise RuntimeError(f"{symbol} has no candle data")
        return synthetic_nse_candles(days=self.days, seed=zlib.crc32(symbol.encode()) % 10_000)


def request(symbols: list[str], *, execution: ExecutionSettings | None = None, start: date = date(2026, 8, 3), end: date = date(2026, 8, 31)) -> BacktestRequest:
    return BacktestRequest(
        run_id="run-1",
        market="NSE",
        strategy_id="ema_vwap_strong_buy",
        symbols=symbols,
        timeframe="5m",
        start_date=start,
        end_date=end,
        configuration={},
        execution=execution or ExecutionSettings(),
    )


def engine(writer: MemoryResultWriter, source: SyntheticSource, *, cancel_event: threading.Event | None = None) -> BacktestEngine:
    return BacktestEngine(strategy=STRATEGIES.get("ema_vwap_strong_buy"), market=market_spec("NSE"), source=source, writer=writer, cancel_event=cancel_event)


class FeeModelTests(unittest.TestCase):
    def test_nse_fees_and_slippage_are_applied_on_both_sides(self) -> None:
        fees = NseFeeModel()
        buy = fees.buy(100.0, 10)
        self.assertAlmostEqual(buy.price, 100.05)
        self.assertAlmostEqual(buy.slippage, 0.5)
        self.assertAlmostEqual(buy.fees, 100.05 * 10 * 0.00111 + 20.0)
        sell = fees.sell(100.0, 10)
        self.assertAlmostEqual(sell.price, 99.95)
        self.assertAlmostEqual(sell.slippage, 0.5)
        self.assertAlmostEqual(sell.fees, 99.95 * 10 * 0.00111 + 20.0)

    def test_crypto_fees_are_basis_points_without_a_fixed_charge(self) -> None:
        fees = CryptoFeeModel()
        buy = fees.buy(50_000.0, 0.1)
        self.assertAlmostEqual(buy.price, 50_000.0 * 1.0002)
        self.assertAlmostEqual(buy.fees, buy.price * 0.1 * 0.0008)
        self.assertAlmostEqual(buy.slippage, 50_000.0 * 0.0002 * 0.1)

    def test_market_specs_carry_currency_timezone_and_session_rules(self) -> None:
        nse, crypto = market_spec("NSE"), market_spec("CRYPTO")
        self.assertEqual((nse.currency, nse.timezone), ("INR", IST))
        self.assertEqual((crypto.currency, crypto.timezone), ("USDT", "UTC"))
        self.assertTrue(crypto.session_is_open(datetime(2026, 9, 6, 3, 0)))
        self.assertFalse(nse.session_is_open(datetime(2026, 9, 6, 10, 0)))  # Sunday


class EngineBehaviourTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.writer = MemoryResultWriter()
        cls.source = SyntheticSource()
        cls.result = engine(cls.writer, cls.source).run(request(["AAA", "BBB"]))
        cls.trades = cls.writer.trades

    def test_run_completes_with_trades_and_metrics(self) -> None:
        self.assertEqual(self.result["status"], "COMPLETE")
        self.assertEqual(self.writer.status, "COMPLETE")
        self.assertGreater(len(self.trades), 0)
        metrics = self.writer.metrics
        self.assertEqual(metrics["completedTrades"] + metrics["openTrades"], len(self.trades))
        self.assertEqual(metrics["targetHits"] + metrics["stoppedTrades"] + metrics["expiredTrades"], metrics["completedTrades"])
        self.assertGreater(metrics["totalSignals"], 0)
        self.assertGreater(metrics["fees"], 0)
        self.assertIsNotNone(metrics["maximumDrawdown"])
        self.assertEqual(metrics["symbolsProcessed"], 2)

    def test_every_lot_enters_at_the_next_candle_open_with_costs(self) -> None:
        strategy = STRATEGIES.get("ema_vwap_strong_buy")
        fees = NseFeeModel()
        for symbol in ("AAA", "BBB"):
            frame = decision_frame(strategy, self.source.candles(symbol, "5m", datetime(2026, 8, 3), datetime(2026, 8, 31), warmup_bars=0), MarketContext(market="NSE", symbol=symbol, timeframe="5m", timezone=IST), {})
            for trade in [item for item in self.trades if item["symbol"] == symbol]:
                signal_bar = frame.index.get_loc(pd.Timestamp(trade["signal_timestamp"]))
                self.assertEqual(frame["Decision"].iloc[signal_bar], "BUY")
                self.assertEqual(pd.Timestamp(trade["entry_timestamp"]), frame.index[signal_bar + 1])
                self.assertAlmostEqual(trade["entry_price"], round(fees.buy(float(frame["Open"].iloc[signal_bar + 1]), trade["quantity"]).price, 4))

    def test_target_hits_exit_at_target_and_net_pnl_deducts_both_sides_costs(self) -> None:
        fees = NseFeeModel()
        hits = [item for item in self.trades if item["status"] == "TARGET_HIT"]
        self.assertGreater(len(hits), 0)
        for trade in hits:
            sell = fees.sell(trade["target_price"], trade["quantity"])
            self.assertAlmostEqual(trade["exit_price"], round(sell.price, 4))
            expected_gross = (trade["exit_price"] - trade["cost_basis_price"]) * trade["quantity"]
            self.assertAlmostEqual(trade["gross_pnl"], round(expected_gross, 2), places=2)
            self.assertGreater(trade["fees"], sell.fees)  # entry fees + exit fees
            self.assertAlmostEqual(trade["net_pnl"], round(trade["gross_pnl"] - trade["fees"], 2), places=2)
            self.assertGreaterEqual(trade["mfe_pct"], 0.0)
            self.assertLessEqual(trade["mae_pct"], 0.0)
            self.assertGreaterEqual(fifo_target_return_pct(trade), 1.0)

    def test_lot_ids_are_unique_and_statuses_are_well_formed(self) -> None:
        lot_ids = [trade["lot_id"] for trade in self.trades]
        self.assertEqual(len(lot_ids), len(set(lot_ids)))
        self.assertTrue({"TARGET_HIT", "OPEN"} & {trade["status"] for trade in self.trades})

    def test_open_lots_publish_mark_to_market_pnl_and_holding_duration(self) -> None:
        open_trades = [trade for trade in self.trades if trade["status"] == "OPEN"]
        self.assertGreater(len(open_trades), 0)
        for trade in open_trades:
            self.assertIsInstance(trade["unrealized_pnl"], float)
            self.assertIsInstance(trade["last_price"], float)
            expected = round((trade["last_price"] - trade["cost_basis_price"]) * trade["quantity"] - trade["fees"], 2)
            self.assertEqual(trade["unrealized_pnl"], expected)
            self.assertEqual(trade["holding_minutes"], float(trade["holding_bars"] * 5))

    def test_each_strong_buy_lot_is_tracked_and_closed_independently(self) -> None:
        """A far target keeps lots open, so later signals in the same cycle add further independent lots."""
        writer = MemoryResultWriter()
        engine(writer, SyntheticSource()).run(request(["AAA", "BBB"], execution=ExecutionSettings(target_pct=8.0)))
        by_cycle: dict[str, list[dict]] = {}
        for trade in writer.trades:
            by_cycle.setdefault(trade["cycle_id"], []).append(trade)
        multi = [lots for lots in by_cycle.values() if len(lots) > 1]
        self.assertTrue(multi, "expected at least one cycle holding several lots")
        for lots in multi:
            lots.sort(key=lambda item: item["lot_number"])
            self.assertEqual([lot["lot_number"] for lot in lots], list(range(1, len(lots) + 1)))
            self.assertEqual([lot["quantity"] for lot in lots][:3], [100, 50, 25][: len(lots)])
            entries = [lot["entry_timestamp"] for lot in lots]
            self.assertEqual(entries, sorted(entries))
            self.assertEqual(len({lot["entry_price"] for lot in lots}), len(lots))
            for lot in lots:
                self.assertGreaterEqual(fifo_target_return_pct(lot), 8.0)
            self.assertEqual(len({lot["lot_id"] for lot in lots}), len(lots))
        limited = MemoryResultWriter()
        engine(limited, SyntheticSource()).run(request(["AAA", "BBB"], execution=ExecutionSettings(target_pct=8.0, allow_additional_buys=False)))
        for trade in limited.trades:
            self.assertEqual(trade["lot_number"], 1)

    def test_progress_is_persisted_after_every_symbol(self) -> None:
        completed = [event["symbolsCompleted"] for event in self.writer.progress_events]
        self.assertEqual(completed, [0, 1, 2])
        self.assertEqual(self.writer.progress_events[-1]["currentSymbol"], None)


class ExitRuleTests(unittest.TestCase):
    def test_stop_loss_and_holding_limit_close_lots(self) -> None:
        writer = MemoryResultWriter()
        engine(writer, SyntheticSource()).run(request(["AAA", "BBB"], execution=ExecutionSettings(stop_loss_pct=0.4, maximum_holding_bars=40)))
        statuses = {trade["status"] for trade in writer.trades}
        self.assertIn("STOPPED", statuses)
        self.assertIn("EXPIRED", statuses)
        for trade in writer.trades:
            self.assertLessEqual(trade["holding_bars"], 40)
            if trade["status"] == "STOPPED":
                self.assertAlmostEqual(trade["stop_price"], round(trade["entry_price"] * (1 - 0.4 / 100), 4))
                self.assertLess(trade["net_pnl"], 0)
            if trade["status"] == "EXPIRED":
                self.assertEqual(trade["holding_bars"], 40)
                self.assertEqual(trade["holding_minutes"], 200.0)
        self.assertEqual(writer.metrics["openTrades"], 0)

    def test_engine_target_override_replaces_the_strategy_target(self) -> None:
        writer = MemoryResultWriter()
        engine(writer, SyntheticSource()).run(request(["AAA"], execution=ExecutionSettings(target_pct=0.3)))
        for trade in writer.trades:
            self.assertGreaterEqual(fifo_target_return_pct(trade), 0.3)

    def test_invalid_execution_settings_are_rejected(self) -> None:
        for bad in ({"stop_loss_pct": 0}, {"maximum_holding_bars": 0}, {"initial_quantity": 0}, {"additional_sizing_mode": "NOPE"}, {"whole_units": False}, {"unknownSetting": 1}):
            with self.assertRaises(ValueError):
                ExecutionSettings.from_mapping(bad)
        self.assertEqual(ExecutionSettings.from_mapping({"stopLossPct": 1.5, "maximumHoldingBars": 30}).stop_loss_pct, 1.5)

    def test_crypto_execution_accepts_fractional_quantities(self) -> None:
        execution = ExecutionSettings.from_mapping(
            {"initialQuantity": 0.01, "minimumQuantity": 1e-8},
            whole_units=False,
        )

        self.assertFalse(execution.whole_units)
        self.assertEqual(execution.minimum_quantity, 1e-8)
        self.assertEqual(execution.lot_quantity(0), 0.01)
        self.assertEqual(execution.lot_quantity(1), 0.005)
        with self.assertRaisesRegex(ValueError, "whole numbers"):
            ExecutionSettings.from_mapping({"minimumQuantity": 1e-8})


class ResilienceTests(unittest.TestCase):
    def test_a_failed_symbol_is_reported_without_failing_the_run(self) -> None:
        writer = MemoryResultWriter()
        result = engine(writer, SyntheticSource(broken={"BAD"})).run(request(["AAA", "BAD", "BBB"]))
        self.assertEqual(result["status"], "COMPLETE")
        self.assertEqual([item["symbol"] for item in result["failedSymbols"]], ["BAD"])
        self.assertEqual(writer.metrics["symbolsFailed"], 1)
        self.assertEqual(writer.metrics["symbolsProcessed"], 2)
        self.assertEqual({trade["symbol"] for trade in writer.trades}, {"AAA", "BBB"})

    def test_cancellation_stops_between_symbols_and_records_partial_metrics(self) -> None:
        writer = MemoryResultWriter()
        event = threading.Event()
        source = SyntheticSource()
        original = source.candles

        def cancel_after_first(symbol, *args, **kwargs):
            frame = original(symbol, *args, **kwargs)
            if symbol == "AAA":
                event.set()
            return frame

        source.candles = cancel_after_first  # type: ignore[method-assign]
        result = engine(writer, source, cancel_event=event).run(request(["AAA", "BBB", "CCC"]))
        self.assertEqual(result["status"], "CANCELLED")
        self.assertEqual(writer.status, "CANCELLED")
        self.assertLess(len(source.calls), 3)
        self.assertIsNotNone(writer.metrics)

    def test_cancellation_requested_through_the_writer_is_honoured(self) -> None:
        writer = MemoryResultWriter()
        writer.cancel_flags.add("run-1")
        result = engine(writer, SyntheticSource()).run(request(["AAA", "BBB"]))
        self.assertEqual(result["status"], "CANCELLED")
        self.assertEqual(writer.trade_count, 0)


class CausalityTests(unittest.TestCase):
    def test_changing_future_candles_does_not_change_earlier_trades(self) -> None:
        base = synthetic_nse_candles(days=12, seed=11)
        cut = len(base) - 150

        class Source:
            def __init__(self, frame: pd.DataFrame) -> None:
                self.frame = frame

            def candles(self, symbol, timeframe, start, end, *, warmup_bars):
                return self.frame

        tampered = base.copy()
        tampered.iloc[cut:, :4] = tampered.iloc[cut:, :4].to_numpy() * 1.5  # a different future
        first, second = MemoryResultWriter(), MemoryResultWriter()
        engine(first, Source(base)).run(request(["AAA"]))
        engine(second, Source(tampered)).run(request(["AAA"]))
        boundary = base.index[cut]
        settled = lambda trades: sorted(  # noqa: E731
            (t["lot_id"], t["entry_price"], t["exit_price"], t["status"]) for t in trades if t["exit_timestamp"] is not None and pd.Timestamp(t["exit_timestamp"]) < boundary
        )
        self.assertGreater(len(settled(first.trades)), 0)
        self.assertEqual(settled(first.trades), settled(second.trades))
        signals_before = lambda trades: sorted(t["signal_timestamp"] for t in trades if pd.Timestamp(t["signal_timestamp"]) < boundary)  # noqa: E731
        self.assertEqual(signals_before(first.trades), signals_before(second.trades))


class BoundedMemoryTests(unittest.TestCase):
    def _peak(self, symbols: int, batch_size: int) -> tuple[int, MemoryResultWriter]:
        writer = MemoryResultWriter(keep_trades=False)
        source = SyntheticSource(days=8)
        run = request([f"SYM{index:03d}" for index in range(symbols)], execution=ExecutionSettings(batch_size=batch_size))
        tracemalloc.start()
        engine(writer, source).run(run)
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        return peak, writer

    def test_full_universe_memory_does_not_grow_with_symbol_count_and_writes_incrementally(self) -> None:
        small_peak, _ = self._peak(6, batch_size=25)
        large_peak, writer = self._peak(36, batch_size=25)
        self.assertGreater(writer.trade_count, 0)
        self.assertGreater(len(writer.batches), 1)
        self.assertTrue(all(size <= 25 or size == writer.batches[-1] for size in writer.batches[:-1]))
        self.assertLess(large_peak, small_peak * 1.6, f"peak grew from {small_peak} to {large_peak} bytes across 6x more symbols")
        self.assertLess(large_peak, 64 * 1024 * 1024)


class MetricsTests(unittest.TestCase):
    def test_drawdown_and_rates_from_streamed_trades(self) -> None:
        metrics = MetricsAccumulator()
        for index, (status, net) in enumerate([("TARGET_HIT", 100.0), ("STOPPED", -150.0), ("STOPPED", -80.0), ("TARGET_HIT", 300.0)]):
            metrics.add_trade({"status": status, "net_pnl": net, "fees": 5.0, "slippage": 1.0, "mae_pct": -0.5, "mfe_pct": 1.0, "holding_minutes": 10.0 * (index + 1), "exit_timestamp": f"2026-08-0{index + 1}T10:00:00+05:30"})
        metrics.add_trade({"status": "OPEN", "unrealized_pnl": 12.5, "fees": 5.0, "slippage": 1.0, "mae_pct": -0.1, "mfe_pct": 0.2})
        public = metrics.public()
        self.assertEqual(public["completedTrades"], 4)
        self.assertEqual(public["openTrades"], 1)
        self.assertEqual(public["winRate"], 50.0)
        self.assertEqual(public["realizedPnl"], 170.0)
        self.assertEqual(public["unrealizedPnl"], 12.5)
        self.assertEqual(public["fees"], 25.0)
        self.assertEqual(public["maximumDrawdown"], 230.0)
        self.assertEqual(public["medianHoldingMinutes"], 25.0)
        self.assertEqual(public["averageHoldingMinutes"], 25.0)
        self.assertAlmostEqual(public["averageMaePct"], -0.42)
