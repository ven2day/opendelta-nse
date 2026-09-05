"""PlatformRuntime wiring: explicit migrations, fail-closed startup, and an end-to-end v2 backtest run."""

from __future__ import annotations

import os
import time
import unittest
from datetime import UTC, date, datetime, timedelta
from unittest.mock import patch

from backend.api.backtest_routes import BacktestCreateRequest, BacktestServices, create_backtest_router
from backend.data.candle_repository import CanonicalCandleRepository
from backend.data.database import Database, DatabaseUnavailable
from backend.data.migrate import main as migrate_main
from backend.markets.timescale_source import TimescaleCandleSource
from backend.platform_runtime import PlatformRuntime
from backend.strategies import STRATEGIES
from test_backtest_engine import SyntheticSource
from test_backtest_routes import endpoints

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL", "").strip()


class _StubDatabase:
    def __init__(self, pending: list[str]) -> None:
        self._pending = pending
        self.opened = False
        self.closed = False
        self.migrated = False

    def open(self) -> None:
        self.opened = True

    def close(self) -> None:
        self.closed = True

    def pending_versions(self) -> list[str]:
        return list(self._pending)

    def migrate(self) -> list[str]:
        self.migrated = True
        self._pending = []
        return ["001"]

    def execute(self, _query: str, _parameters=None) -> int:
        return 0


class StartupPolicyTests(unittest.TestCase):
    def test_pending_migrations_fail_closed_unless_auto_migrate_is_explicit(self) -> None:
        stub = _StubDatabase(pending=["001"])
        runtime = PlatformRuntime(database=stub, candle_sources={})  # type: ignore[arg-type]
        with patch.dict(os.environ, {"PLATFORM_AUTO_MIGRATE": ""}):
            runtime.start()
        self.assertIsNone(runtime.database)
        self.assertFalse(stub.migrated)
        self.assertTrue(stub.closed)
        self.assertIn("backend.data.migrate", runtime.disabled_reason or "")
        with self.assertRaises(DatabaseUnavailable):
            runtime.require_database()
        self.assertFalse(runtime.status()["databaseConfigured"])

    def test_auto_migrate_applies_pending_migrations_when_opted_in(self) -> None:
        stub = _StubDatabase(pending=["001"])
        runtime = PlatformRuntime(database=stub, candle_sources={})  # type: ignore[arg-type]
        with patch.dict(os.environ, {"PLATFORM_AUTO_MIGRATE": "true"}), patch.object(PlatformRuntime, "runner") as runner, patch.object(PlatformRuntime, "_start_signal_workers"):
            runner.return_value.recover.return_value = 0
            runtime.start()
        self.assertTrue(stub.migrated)
        self.assertEqual(runtime.migrated_versions, ["001"])
        self.assertIs(runtime.database, stub)

    def test_without_a_database_url_the_runtime_is_simply_disabled(self) -> None:
        runtime = PlatformRuntime(database=None, candle_sources={})
        runtime.start()
        with self.assertRaises(DatabaseUnavailable):
            runtime.runs()


@unittest.skipUnless(TEST_DATABASE_URL, "TEST_DATABASE_URL is not set")
class EndToEndBacktestTests(unittest.TestCase):
    """A v2 backtest created through the API runs in the background and lands in PostgreSQL."""

    @classmethod
    def setUpClass(cls) -> None:
        bootstrap = Database(TEST_DATABASE_URL, max_pool_size=1)
        bootstrap.open()
        bootstrap.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
        bootstrap.close()
        assert migrate_main(["--check", "--database-url", TEST_DATABASE_URL]) == 1, "fresh schema should report pending migrations"
        assert migrate_main(["--database-url", TEST_DATABASE_URL]) == 0
        assert migrate_main(["--check", "--database-url", TEST_DATABASE_URL]) == 0, "schema should be current after migrating"
        cls.runtime = PlatformRuntime(database=Database(TEST_DATABASE_URL, max_pool_size=4), candle_sources={"NSE": SyntheticSource, "CRYPTO": SyntheticSource})
        with patch.dict(os.environ, {"PLATFORM_AUTO_MIGRATE": ""}):
            cls.runtime.start()
        assert cls.runtime.database is not None, cls.runtime.disabled_reason
        services = BacktestServices(registry=STRATEGIES, runs=cls.runtime.runs, trades=cls.runtime.trades, runner=cls.runtime.runner)
        cls.api = endpoints(create_backtest_router(services))

    @classmethod
    def tearDownClass(cls) -> None:
        cls.runtime.stop()

    def _wait(self, run_id: str, timeout: float = 60.0) -> dict:
        deadline = time.time() + timeout
        while time.time() < deadline:
            record = self.api["GET /v2/backtests/{run_id}"](run_id)
            if record["status"] not in {"QUEUED", "RUNNING"}:
                return record
            time.sleep(0.2)
        raise AssertionError("backtest did not finish in time")

    def test_background_run_completes_and_persists_trades_progress_and_metrics(self) -> None:
        created = self.api["POST /v2/backtests"](BacktestCreateRequest(market="NSE", strategyId="ema_vwap_strong_buy", symbols=["AAA", "BBB"], startDate=date(2026, 8, 3), endDate=date(2026, 8, 31), execution={"batchSize": 2}))
        self.assertEqual(created["status"], "QUEUED")
        record = self._wait(created["runId"])
        self.assertEqual(record["status"], "COMPLETE", record.get("error"))
        self.assertEqual(record["symbolsCompleted"], 2)
        self.assertIsNone(record["currentSymbol"])
        self.assertGreater(record["metrics"]["completedTrades"] + record["metrics"]["openTrades"], 0)
        page = self.api["GET /v2/backtests/{run_id}/trades"](created["runId"], symbol=None, limit=100, offset=0)
        self.assertEqual(page["total"], record["metrics"]["completedTrades"] + record["metrics"]["openTrades"])
        strategy = STRATEGIES.get("ema_vwap_strong_buy")
        for row in page["trades"]:
            self.assertEqual(row["runId"], created["runId"])
            self.assertEqual((row["market"], row["strategyId"], row["strategyVersion"], row["timeframe"]), ("NSE", strategy.strategy_id, strategy.version, "5m"))
            self.assertIn(row["status"], {"OPEN", "TARGET_HIT", "STOPPED", "EXPIRED"})
            if row["status"] == "OPEN":
                self.assertEqual((row["netPnl"], row["grossPnl"]), (0.0, 0.0))  # nothing realised yet; fees already paid
            else:
                self.assertAlmostEqual(row["netPnl"], round(row["grossPnl"] - row["fees"], 2), places=2)
        self.assertEqual(record["configurationSnapshot"]["ema_fast"], 9)
        self.assertEqual(record["strategyVersion"], strategy.version)

    def test_timescale_source_reads_warmup_for_nse_and_crypto_without_mixing_markets(self) -> None:
        assert self.runtime.database is not None
        start = datetime(2026, 8, 28, 10, tzinfo=UTC)
        statement = """
            INSERT INTO market_candles (
                market, provider, instrument_id, symbol, timeframe,
                open_time, close_time, open, high, low, close, volume, complete
            ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,true)
        """
        for opened in (start - timedelta(minutes=5), start, start + timedelta(minutes=5)):
            self.runtime.database.execute(
                statement,
                ("NSE", "DHAN", "reader-nse", "READERTEST", "5m", opened,
                 opened + timedelta(minutes=5), 100, 102, 99, 101, 1_000),
            )
        self.runtime.database.execute(
            statement,
            ("CRYPTO", "OKX", "reader-crypto", "READERTEST", "5m", start,
             start + timedelta(minutes=5), 200, 203, 198, 202, 2_000),
        )
        repository = CanonicalCandleRepository(self.runtime.database)
        nse = TimescaleCandleSource(repository, market="NSE", provider="DHAN").candles(
            "READERTEST", "5m", start, start + timedelta(minutes=10), warmup_bars=1
        )
        crypto = TimescaleCandleSource(repository, market="CRYPTO", provider="OKX").candles(
            "READERTEST", "5m", start, start + timedelta(minutes=10), warmup_bars=0
        )
        nse_batch = TimescaleCandleSource(repository, market="NSE", provider="DHAN").candles_many(
            ["READERTEST", "NOTSTORED"], "5m", start, start + timedelta(minutes=10), warmup_bars=0
        )

        self.assertEqual(list(nse.index), [start - timedelta(minutes=5), start, start + timedelta(minutes=5)])
        self.assertEqual(list(crypto.index), [start])
        self.assertEqual(list(nse["Close"]), [101.0, 101.0, 101.0])
        self.assertEqual(list(crypto["Close"]), [202.0])
        self.assertEqual(list(nse_batch.frames["READERTEST"]["Close"]), [101.0, 101.0])
        self.assertTrue(nse_batch.frames["NOTSTORED"].empty)
        self.assertEqual(nse_batch.errors, {})

    def test_cancel_is_durable_and_a_cancelled_run_finishes_as_cancelled(self) -> None:
        created = self.api["POST /v2/backtests"](BacktestCreateRequest(market="NSE", strategyId="ema_vwap_strong_buy", symbols=[f"SYM{index:02d}" for index in range(12)], startDate=date(2026, 8, 3), endDate=date(2026, 8, 31)))
        cancelled = self.api["DELETE /v2/backtests/{run_id}"](created["runId"])
        self.assertTrue(cancelled["cancelRequested"])
        record = self._wait(created["runId"])
        self.assertEqual(record["status"], "CANCELLED")
        self.assertLess(record["symbolsCompleted"], 12)

    def test_restart_marks_unfinished_runs_interrupted(self) -> None:
        runs = self.runtime.runs()
        stale = runs.create(market="NSE", strategy_id="ema_vwap_strong_buy", strategy_version="1.0.0", configuration_snapshot={}, execution_settings={}, timeframe="5m", symbols=["X"], start_date=date(2026, 8, 1), end_date=date(2026, 8, 2))
        runs.mark_started(stale["runId"])
        second = PlatformRuntime(database=Database(TEST_DATABASE_URL, max_pool_size=1), candle_sources={"NSE": SyntheticSource, "CRYPTO": SyntheticSource})
        with patch.dict(os.environ, {"PLATFORM_AUTO_MIGRATE": ""}):
            second.start()
        try:
            self.assertEqual(runs.get(stale["runId"])["status"], "INTERRUPTED")
        finally:
            second.stop()
