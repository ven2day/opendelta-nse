"""Repository and migration tests against a real PostgreSQL (``TEST_DATABASE_URL``); skipped otherwise."""

from __future__ import annotations

import os
import unittest
from datetime import date, datetime, timezone

from backend.backtest.result_writer import DatabaseResultWriter
from backend.data.database import Database
from backend.data.repositories import BacktestRunRepository, BacktestTradeRepository, LiveSignalRepository, StrategyConfigRepository, StrategyDeploymentRepository

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL", "").strip()


@unittest.skipUnless(TEST_DATABASE_URL, "TEST_DATABASE_URL is not set")
class PlatformDatabaseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.database = Database(TEST_DATABASE_URL, max_pool_size=2)
        cls.database.open()
        cls.database.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
        cls.first_migration = cls.database.migrate()
        cls.runs = BacktestRunRepository(cls.database)
        cls.trades = BacktestTradeRepository(cls.database)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.database.close()

    def _run(self, **overrides):
        values = dict(
            market="NSE",
            strategy_id="ema_vwap_strong_buy",
            strategy_version="1.0.0",
            configuration_snapshot={"target_pct": 1.0},
            execution_settings={"batchSize": 500},
            timeframe="5m",
            symbols=["RELIANCE", "TCS"],
            start_date=date(2026, 8, 1),
            end_date=date(2026, 8, 31),
        )
        values.update(overrides)
        return self.runs.create(**values)

    def test_migrations_are_versioned_and_idempotent(self) -> None:
        self.assertEqual(
            self.first_migration,
            [
                "001_platform",
                "001_timescale_market_data",
                "002_timescale_candle_reader",
                "003_backtest_trade_last_price",
                "004_paper_pending_entries",
                "005_dhan_fifo_cost_basis",
                "006_live_signal_strategy_identity",
                "007_strategy_deployments",
            ],
        )
        self.assertEqual(self.database.migrate(), [])
        tables = {
            row["table_name"]
            for row in self.database.fetch_all(
                "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'"
            )
        }
        for expected in (
            "screener_runs",
            "screener_results",
            "saved_universes",
            "strategy_configs",
            "strategy_deployments",
            "backtest_runs",
            "backtest_trades",
            "live_signals",
            "paper_accounts",
            "paper_orders",
            "paper_pending_entries",
            "paper_lots",
            "paper_trades",
            "engine_status",
            "schema_migrations",
        ):
            self.assertIn(expected, tables)

    def test_different_strategy_ids_do_not_collide_at_same_candle(self) -> None:
        signals = LiveSignalRepository(self.database)
        stamp = datetime(2026, 9, 1, 10, 0, tzinfo=timezone.utc)
        common = dict(
            market="NSE",
            strategy_version="1.0.0",
            symbol="TCS",
            timeframe="5m",
            candle_timestamp=stamp,
            signal_type="BUY",
            signal_price=100.0,
            target_price=101.0,
            stop_price=None,
            expires_at=None,
            reasons=["TEST"],
            indicators={},
            configuration_snapshot={"target_pct": 1.0},
        )
        first = signals.insert_new(strategy_id="ema_vwap_strong_buy", **common)
        second = signals.insert_new(strategy_id="rsi_dip_ladder_v1", **common)
        duplicate = signals.insert_new(strategy_id="ema_vwap_strong_buy", **common)
        self.assertIsNotNone(first)
        self.assertIsNotNone(second)
        self.assertIsNone(duplicate)

    def test_strategy_deployment_pins_the_active_configuration(self) -> None:
        active = StrategyConfigRepository(self.database).save(market="CRYPTO", strategy_id="ema_vwap_strong_buy", strategy_version="1.0.0", name="deployment-test", configuration={"target_pct": 1.0}, risk_settings={"priceModel": "NEXT_OPEN"}, activate=True)
        deployments = StrategyDeploymentRepository(self.database)
        paper = deployments.save(market="CRYPTO", strategy_id="ema_vwap_strong_buy", strategy_version="1.0.0", config_id=active["configId"], timeframe="5m", mode="PAPER")
        self.assertEqual((paper["mode"], paper["configId"]), ("PAPER", active["configId"]))
        stopped = deployments.save(market="CRYPTO", strategy_id="ema_vwap_strong_buy", strategy_version="1.0.0", config_id=active["configId"], timeframe="5m", mode="OFF")
        self.assertEqual(stopped["deploymentId"], paper["deploymentId"])
        self.assertEqual(deployments.get("CRYPTO", "ema_vwap_strong_buy")["mode"], "OFF")

    def test_run_lifecycle_and_progress(self) -> None:
        record = self._run()
        self.assertEqual(record["status"], "QUEUED")
        self.assertEqual(record["configurationSnapshot"], {"target_pct": 1.0})
        self.assertEqual(record["symbolsTotal"], 2)
        self.runs.mark_started(record["runId"])
        self.runs.update_progress(
            record["runId"],
            symbols_completed=1,
            current_symbol="TCS",
            failed_symbols=[{"symbol": "RELIANCE", "message": "no data"}],
        )
        progressed = self.runs.get(record["runId"])
        self.assertEqual(
            (progressed["status"], progressed["symbolsCompleted"], progressed["currentSymbol"]), ("RUNNING", 1, "TCS")
        )
        self.assertEqual(progressed["failedSymbols"][0]["symbol"], "RELIANCE")
        finished = self.runs.finish(record["runId"], status="COMPLETE", metrics={"completedTrades": 3})
        self.assertEqual(finished["status"], "COMPLETE")
        self.assertEqual(finished["metrics"], {"completedTrades": 3})
        self.assertIsNone(finished["currentSymbol"])
        self.assertIn(record["runId"], [item["runId"] for item in self.runs.list("NSE")])
        with self.assertRaises(ValueError):
            self.runs.finish(record["runId"], status="RUNNING", metrics=None)

    def test_cancel_request_is_durable_and_stale_runs_are_interrupted_on_recovery(self) -> None:
        record = self._run()
        self.assertFalse(self.runs.cancel_requested(record["runId"]))
        self.runs.request_cancel(record["runId"])
        self.assertTrue(self.runs.cancel_requested(record["runId"]))
        self.runs.mark_started(record["runId"])
        stale = self._run()
        self.runs.mark_started(stale["runId"])
        interrupted = self.runs.interrupt_stale()
        self.assertGreaterEqual(interrupted, 2)
        self.assertEqual(self.runs.get(stale["runId"])["status"], "INTERRUPTED")
        self.assertEqual(self.runs.interrupt_stale(), 0)

    def test_trades_are_written_in_batches_with_duplicate_protection(self) -> None:
        record = self._run()
        writer = DatabaseResultWriter(self.runs, self.trades)
        writer.started(record["runId"])
        stamp = datetime(2026, 8, 4, 9, 30, tzinfo=timezone.utc)
        row = {
            "run_id": record["runId"],
            "market": "NSE",
            "strategy_id": "ema_vwap_strong_buy",
            "strategy_version": "1.0.0",
            "symbol": "TCS",
            "timeframe": "5m",
            "lot_id": "TCS-Cycle1-Lot1",
            "cycle_id": "TCS-Cycle1",
            "lot_number": 1,
            "signal_timestamp": stamp,
            "signal_price": 100.0,
            "entry_timestamp": stamp,
            "entry_price": 100.05,
            "cost_basis_price": 100.05,
            "quantity": 100,
            "target_price": 101.05,
            "stop_price": None,
            "expires_at": None,
            "exit_timestamp": stamp,
            "exit_price": 101.0,
            "status": "TARGET_HIT",
            "gross_pnl": 95.0,
            "fees": 45.0,
            "slippage": 10.0,
            "net_pnl": 50.0,
            "unrealized_pnl": 0.0,
            "mae_pct": -0.2,
            "mfe_pct": 1.1,
            "holding_bars": 12,
            "holding_minutes": 60.0,
        }
        writer.write_trades(
            record["runId"],
            [
                row,
                {
                    **row,
                    "lot_id": "TCS-Cycle1-Lot2",
                    "lot_number": 2,
                    "status": "OPEN",
                    "exit_timestamp": None,
                    "exit_price": None,
                },
            ],
        )
        writer.write_trades(record["runId"], [row])  # duplicate lot is ignored, not duplicated
        self.assertEqual(self.trades.count(record["runId"]), 2)
        listed = self.trades.list(record["runId"], symbol="TCS")
        self.assertEqual([item["lotId"] for item in listed], ["TCS-Cycle1-Lot1", "TCS-Cycle1-Lot2"])
        self.assertEqual(listed[0]["netPnl"], 50.0)
        self.assertEqual(listed[1]["status"], "OPEN")
        open_trades = self.trades.list(record["runId"], symbol="CS", status="OPEN", sort_by="netPnl", direction="desc")
        self.assertEqual([item["lotId"] for item in open_trades], ["TCS-Cycle1-Lot2"])
        self.assertEqual(self.trades.count(record["runId"], symbol="CS", status="OPEN"), 1)
        with self.assertRaises(ValueError):
            self.trades.list(record["runId"], sort_by="not_a_column")
        writer.finished(record["runId"], status="COMPLETE", metrics={"completedTrades": 1})
        self.assertEqual(self.runs.get(record["runId"])["status"], "COMPLETE")
