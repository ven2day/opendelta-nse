"""RSI Dip Ladder strategy, sizing, dip gates, and independent exits."""

from __future__ import annotations

import threading
import unittest
from datetime import date

import pandas as pd

from backend.backtest import BacktestEngine, BacktestRequest, ExecutionSettings, MemoryResultWriter
from backend.core.models import MarketContext
from backend.markets.base import market_spec
from backend.markets.nse.fees import NseFeeModel
from backend.strategies import STRATEGIES
from backend.strategies.base import decision_frame
from backend.strategies.lot_policy import PriceBandLadder

IST = "Asia/Kolkata"


def fifo_target_return_pct(trade: dict) -> float:
    entry_fees = sum(float(item["fees"]) for item in trade["fifo_allocations"])
    acquisition_cost = float(trade["cost_basis_price"]) * float(trade["quantity"]) + entry_fees
    fill = NseFeeModel().sell(float(trade["target_price"]), float(trade["quantity"]))
    return (fill.price * float(trade["quantity"]) - fill.fees - acquisition_cost) / acquisition_cost * 100


def candles(closes: list[float]) -> pd.DataFrame:
    index = pd.date_range("2026-08-03 09:15", periods=len(closes), freq="5min", tz=IST)
    values = pd.Series(closes, index=index, dtype=float)
    return pd.DataFrame({"Open": values, "High": values * 1.002, "Low": values * 0.998, "Close": values, "Volume": 100_000.0}, index=index)


class Source:
    def __init__(self, frame: pd.DataFrame) -> None:
        self.frame = frame

    def candles(self, symbol, timeframe, start, end, *, warmup_bars):
        return self.frame


class RsiDipLadderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.strategy = STRATEGIES.get("rsi_dip_ladder_v1")
        self.config = self.strategy.resolve({"rsi_length": 2, "rsi_low": 30, "rsi_recovery": 35})

    def test_defaults_are_the_requested_finite_ladders(self) -> None:
        policy = PriceBandLadder.from_config(self.config)
        assert policy is not None
        self.assertEqual(policy.price_threshold, 1000)
        self.assertEqual([policy.quantity(i, 3000) for i in range(4)], [5, 10, 25, 50])
        self.assertEqual([policy.quantity(i, 999.99) for i in range(4)], [10, 20, 50, 100])
        self.assertEqual(policy.maximum_entries, 4)
        self.assertEqual(self.config["target_pct"], 5)

    def test_four_hour_nse_backtests_are_exposed(self) -> None:
        self.assertIn("4h", self.strategy.supported_timeframes)
        self.assertEqual(market_spec("NSE").minutes("4h"), 240)

    def test_invalid_ladders_and_rsi_thresholds_are_rejected(self) -> None:
        for override in (
            {"rsi_low": 35, "rsi_recovery": 35},
            {"high_price_quantities": [5, 0, 25, 50]},
            {"high_price_quantities": [5, 10], "low_price_quantities": [10]},
            {"high_price_quantities": "5,10,25,50"},
        ):
            with self.assertRaises(ValueError):
                self.strategy.resolve(override)

    def test_only_completed_rsi_recoveries_create_signals(self) -> None:
        frame = candles([100, 98, 96, 94, 96, 98, 97])
        decision = decision_frame(self.strategy, frame, MarketContext(market="NSE", symbol="TEST", timeframe="5m", timezone=IST), self.config)
        buys = decision.index[decision["Decision"] == "BUY"]
        self.assertEqual(len(buys), 1)
        incomplete = frame.loc[: buys[0]].copy()
        incomplete["Complete"] = True
        incomplete.loc[incomplete.index[-1], "Complete"] = False
        self.assertEqual(self.strategy.evaluate(incomplete, MarketContext(market="NSE", symbol="TEST", timeframe="5m", timezone=IST), self.config).decision, "NONE")

    def test_vectorized_backtest_and_live_prefix_evaluation_agree_bar_for_bar(self) -> None:
        frame = candles([100, 98, 96, 94, 96, 98, 97, 92, 90, 92, 94])
        context = MarketContext(market="NSE", symbol="TEST", timeframe="5m", timezone=IST)
        vectorized = decision_frame(self.strategy, frame, context, self.config)
        live = [self.strategy.evaluate(frame.iloc[: position + 1], context, self.config).decision for position in range(len(frame))]
        self.assertEqual(vectorized["Decision"].tolist(), live)

    def test_backtest_initial_rsi_signal_then_price_only_dips_fill_each_next_open(self) -> None:
        frame = candles([1100, 1080, 1060, 1040, 1060, 1080, 1070, 1020, 1000, 970, 940, 910, 880, 850, 830])
        decisions = decision_frame(self.strategy, frame, MarketContext(market="NSE", symbol="TEST", timeframe="5m", timezone=IST), self.config)
        self.assertEqual((decisions["Decision"] == "BUY").sum(), 1)
        writer = MemoryResultWriter()
        engine = BacktestEngine(strategy=self.strategy, market=market_spec("NSE"), source=Source(frame), writer=writer, cancel_event=threading.Event())
        result = engine.run(BacktestRequest(run_id="ladder", market="NSE", strategy_id=self.strategy.strategy_id, symbols=["TEST"], timeframe="5m", start_date=date(2026, 8, 3), end_date=date(2026, 8, 3), configuration={"rsi_length": 2, "rsi_low": 30, "rsi_recovery": 35}, execution=ExecutionSettings(target_pct=50)))
        self.assertEqual(result["status"], "COMPLETE")
        lots = sorted(writer.trades, key=lambda row: row["lot_number"])
        self.assertEqual([lot["lot_number"] for lot in lots], [1, 2, 3, 4])
        self.assertEqual([lot["quantity"] for lot in lots], [5, 10, 25, 50])
        self.assertTrue(all(fifo_target_return_pct(lot) >= 50 for lot in lots))
        for lot in lots:
            signal_position = frame.index.get_loc(pd.Timestamp(lot["signal_timestamp"]))
            self.assertEqual(pd.Timestamp(lot["entry_timestamp"]), frame.index[signal_position + 1])

    def test_five_percent_targets_use_each_candidate_fifo_quantity(self) -> None:
        frame = candles([900, 880, 860, 840, 860, 880, 820, 800, 820, 840, 860, 880, 920])
        writer = MemoryResultWriter()
        BacktestEngine(strategy=self.strategy, market=market_spec("NSE"), source=Source(frame), writer=writer).run(BacktestRequest(run_id="targets", market="NSE", strategy_id=self.strategy.strategy_id, symbols=["TEST"], timeframe="5m", start_date=date(2026, 8, 3), end_date=date(2026, 8, 3), configuration={"rsi_length": 2, "rsi_low": 30, "rsi_recovery": 35}))
        self.assertTrue(writer.trades)
        for lot in writer.trades:
            self.assertGreaterEqual(fifo_target_return_pct(lot), 5)
        self.assertEqual([lot["quantity"] for lot in sorted(writer.trades, key=lambda row: row["lot_number"])], [10, 20])


if __name__ == "__main__":
    unittest.main()
