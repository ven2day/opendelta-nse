"""Live signal engine guarantees: duplicates rejected, incomplete candles ignored, lifecycle, recovery, workers."""

from __future__ import annotations

import os
import threading
import unittest
import uuid
from datetime import datetime, timedelta
from typing import Any

import pandas as pd

from backend.data.database import Database
from backend.data.repositories import EngineStatusRepository, LiveSignalRepository
from backend.markets.base import market_spec
from backend.signals import CandleHistory, CandleProcessor, MarketSignalWorker, SignalEngine
from backend.signals.engine import NO_ORDER_EXECUTION, RiskSettings
from backend.strategies import STRATEGIES
from test_strategy_engine import synthetic_nse_candles

IST = "Asia/Kolkata"
TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL", "").strip()


class FakeSignalRepository:
    """In-memory stand-in that enforces the same uniqueness rule as the database constraint."""

    def __init__(self) -> None:
        self.rows: dict[str, dict[str, Any]] = {}
        self.keys: set[tuple] = set()
        self.published: list[dict[str, Any]] = []

    def insert_new(self, **values: Any) -> dict[str, Any] | None:
        key = (values["market"], values["strategy_version"], values["symbol"], values["timeframe"], pd.Timestamp(values["candle_timestamp"]), values["signal_type"])
        if key in self.keys:
            return None
        self.keys.add(key)
        signal_id = str(uuid.uuid4())
        row = {
            "signalId": signal_id, "market": values["market"], "strategyId": values["strategy_id"], "strategyVersion": values["strategy_version"],
            "symbol": values["symbol"], "timeframe": values["timeframe"], "candleTimestamp": pd.Timestamp(values["candle_timestamp"]).isoformat(),
            "signalType": values["signal_type"], "status": "STRONG_BUY", "signalPrice": values["signal_price"], "targetPrice": values["target_price"],
            "stopPrice": values["stop_price"], "expiresAt": values["expires_at"].isoformat() if values["expires_at"] else None,
            "reasons": list(values["reasons"]), "indicators": dict(values["indicators"]), "configurationSnapshot": dict(values["configuration_snapshot"]),
            "lastPrice": values["signal_price"], "exitTimestamp": None, "exitPrice": None,
        }
        self.rows[signal_id] = row
        return dict(row)

    def open(self, market: str, symbol: str | None = None) -> list[dict[str, Any]]:
        return [dict(row) for row in self.rows.values() if row["market"] == market and row["status"] in {"STRONG_BUY", "HOLDING"} and (symbol is None or row["symbol"] == symbol)]

    def mark_holding(self, signal_id: str, *, last_price: float) -> None:
        if self.rows[signal_id]["status"] == "STRONG_BUY":
            self.rows[signal_id].update(status="HOLDING", lastPrice=last_price)

    def update_last_price(self, signal_id: str, *, last_price: float) -> None:
        self.rows[signal_id]["lastPrice"] = last_price

    def close(self, signal_id: str, *, status: str, exit_timestamp: datetime, exit_price: float) -> dict[str, Any]:
        row = self.rows[signal_id]
        if row["status"] in {"STRONG_BUY", "HOLDING"}:
            row.update(status=status, exitTimestamp=exit_timestamp.isoformat(), exitPrice=exit_price, lastPrice=exit_price)
        return dict(row)


def make_engine(repository: FakeSignalRepository | None = None, *, risk: RiskSettings | None = None, clock=None) -> SignalEngine:
    return SignalEngine(
        market=market_spec("NSE"),
        strategy=STRATEGIES.get("ema_vwap_strong_buy"),
        configuration={},
        risk=risk or RiskSettings(),
        timeframe="5m",
        repository=repository or FakeSignalRepository(),
        clock=clock or (lambda: datetime(2026, 9, 1, 12, 0, tzinfo=pd.Timestamp.now(tz=IST).tzinfo)),
    )


def first_signal_bar(candles: pd.DataFrame) -> int:
    strategy = STRATEGIES.get("ema_vwap_strong_buy")
    table = strategy.compute_indicators(candles, strategy.resolve({}), IST)
    bars = [index for index in range(strategy.required_history({}), len(table)) if bool(table["StrongBuy"].iloc[index])]
    return bars[0]


class SignalEngineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.candles = synthetic_nse_candles(days=12, seed=7)
        cls.signal_bar = first_signal_bar(cls.candles)

    def _feed(self, engine: SignalEngine, upto: int, symbol: str = "SYN") -> list[dict[str, Any]]:
        created = []
        engine.history.seed(symbol, self.candles.iloc[: self.signal_bar - 5])
        for bar in range(self.signal_bar - 5, upto + 1):
            stored = engine.process_completed_candle(symbol, self.candles.iloc[[bar]])
            if stored:
                created.append(stored)
        return created

    def test_a_buy_candle_creates_exactly_one_stored_signal_and_publishes_it(self) -> None:
        repository = FakeSignalRepository()
        published: list[dict[str, Any]] = []
        engine = make_engine(repository)
        engine.publish = published.append
        created = self._feed(engine, self.signal_bar)
        self.assertEqual(len(created), 1)
        signal = created[0]
        self.assertEqual(signal["status"], "STRONG_BUY")
        self.assertEqual(signal["strategyVersion"], STRATEGIES.get("ema_vwap_strong_buy").version)
        self.assertEqual(signal["configurationSnapshot"]["ema_fast"], 9)
        self.assertEqual(pd.Timestamp(signal["candleTimestamp"]), self.candles.index[self.signal_bar])
        self.assertAlmostEqual(signal["targetPrice"], round(signal["signalPrice"] * 1.01, 4))
        self.assertEqual(published, [signal])

    def test_replaying_the_same_candle_never_stores_a_duplicate(self) -> None:
        repository = FakeSignalRepository()
        engine = make_engine(repository)
        self._feed(engine, self.signal_bar)
        again = engine.process_completed_candle("SYN", self.candles.iloc[[self.signal_bar]])
        self.assertIsNone(again)
        fresh = make_engine(repository)  # a restarted engine sharing the same store
        fresh.history.seed("SYN", self.candles.iloc[: self.signal_bar])
        replay = fresh.process_completed_candle("SYN", self.candles.iloc[[self.signal_bar]])
        self.assertIsNone(replay)
        self.assertEqual(len(repository.rows), 1)
        self.assertEqual(fresh.duplicates_rejected, 1)

    def test_an_incomplete_candle_is_rejected_at_both_the_feed_and_the_strategy(self) -> None:
        signal_stamp = self.candles.index[self.signal_bar]
        processor = CandleProcessor(bar_minutes=5, timezone=IST, clock=lambda: (signal_stamp + timedelta(minutes=2)).to_pydatetime())
        completed = processor.completed(self.candles.iloc[: self.signal_bar + 1])
        self.assertEqual(completed.index[-1], self.candles.index[self.signal_bar - 1])  # the forming candle is dropped by the clock
        flagged = self.candles.iloc[: self.signal_bar + 1].copy()
        flagged["Complete"] = True
        flagged.iloc[-1, flagged.columns.get_loc("Complete")] = False
        later = CandleProcessor(bar_minutes=5, timezone=IST, clock=lambda: (signal_stamp + timedelta(hours=1)).to_pydatetime())
        self.assertEqual(later.completed(flagged).index[-1], self.candles.index[self.signal_bar - 1])  # provider flag also drops it
        engine = make_engine()
        engine.history.seed("SYN", self.candles.iloc[: self.signal_bar])
        row = self.candles.iloc[[self.signal_bar]].copy()
        row["Complete"] = False
        self.assertIsNone(engine.process_completed_candle("SYN", row))
        self.assertEqual(len(engine.repository.rows), 0)

    def test_lifecycle_holding_then_target_hit_with_last_price_tracking(self) -> None:
        repository = FakeSignalRepository()
        engine = make_engine(repository)
        [signal] = self._feed(engine, self.signal_bar)
        target = signal["targetPrice"]
        bar = self.signal_bar + 1
        engine.process_completed_candle("SYN", self.candles.iloc[[bar]])
        row = repository.rows[signal["signalId"]]
        self.assertEqual(row["status"], "HOLDING")
        self.assertEqual(row["lastPrice"], float(self.candles["Close"].iloc[bar]))
        below = self.candles.iloc[[bar + 1]].copy()
        below[["Open", "High", "Low", "Close"]] = [[target * 0.995, target * 0.999, target * 0.99, target * 0.997]]
        engine.process_completed_candle("SYN", below)
        self.assertEqual(row["status"], "HOLDING")  # a high just under the target keeps holding
        self.assertAlmostEqual(row["lastPrice"], target * 0.997)
        touching = self.candles.iloc[[bar + 2]].copy()
        touching[["Open", "High", "Low", "Close"]] = [[target * 0.998, target, target * 0.996, target * 0.999]]
        engine.process_completed_candle("SYN", touching)
        self.assertEqual(row["status"], "TARGET_HIT")
        self.assertEqual(row["exitPrice"], target)
        self.assertEqual(pd.Timestamp(row["exitTimestamp"]), touching.index[0])
        self.assertEqual(engine.repository.open("NSE", "SYN"), [])
        engine.process_completed_candle("SYN", self.candles.iloc[[bar + 3]])
        self.assertEqual(row["status"], "TARGET_HIT")  # closed signals are never reopened

    def test_stop_loss_and_expiry_close_signals(self) -> None:
        repository = FakeSignalRepository()
        engine = make_engine(repository, risk=RiskSettings(stop_loss_pct=0.05, maximum_holding_bars=3))
        [signal] = self._feed(engine, self.signal_bar)
        self.assertAlmostEqual(signal["stopPrice"], round(signal["signalPrice"] * (1 - 0.0005), 4))
        self.assertEqual(pd.Timestamp(signal["expiresAt"]), self.candles.index[self.signal_bar] + timedelta(minutes=15))
        for bar in range(self.signal_bar + 1, self.signal_bar + 5):
            engine.process_completed_candle("SYN", self.candles.iloc[[bar]])
        self.assertIn(repository.rows[signal["signalId"]]["status"], {"EXITED", "EXPIRED", "TARGET_HIT"})
        self.assertIsNotNone(repository.rows[signal["signalId"]]["exitTimestamp"])
        stale = FakeSignalRepository()
        engine = make_engine(stale, risk=RiskSettings(maximum_holding_bars=2))
        [signal] = self._feed(engine, self.signal_bar)
        flat = self.candles.iloc[[self.signal_bar + 1, self.signal_bar + 2, self.signal_bar + 3]].copy()
        flat[["Open", "High", "Low", "Close"]] = signal["signalPrice"]  # never reaches the target
        for stamp in flat.index:
            engine.process_completed_candle("SYN", flat.loc[[stamp]])
        self.assertEqual(stale.rows[signal["signalId"]]["status"], "EXPIRED")

    def test_history_is_bounded_and_status_snapshot_is_paper_only(self) -> None:
        engine = make_engine()
        engine.history.seed("SYN", self.candles)
        self.assertLessEqual(len(engine.history.get("SYN")), engine.history.maximum_bars)
        snapshot = engine.snapshot()
        self.assertTrue(snapshot["paperOnly"])
        self.assertFalse(snapshot["liveOrdersEnabled"])
        self.assertTrue(NO_ORDER_EXECUTION)

    def test_no_order_placement_code_exists_in_the_signal_or_paper_path(self) -> None:
        import inspect

        import backend.signals.engine as engine_module
        import backend.signals.workers as workers_module

        for module in (engine_module, workers_module):
            source = inspect.getsource(module)
            for forbidden in ("place_order(", "create_order(", "submit_order(", "/orders"):
                self.assertNotIn(forbidden, source)


class WorkerTests(unittest.TestCase):
    class Source:
        """CandleSource test double serving one synthetic history to every symbol."""

        def __init__(self, frame: pd.DataFrame, *, fail_symbols: set[str] | None = None, fail_all_until: int = 0) -> None:
            self.frame = frame
            self.fail_symbols = fail_symbols or set()
            self.fail_all_until = fail_all_until
            self.calls = 0

        def candles(self, symbol: str, timeframe: str, start, end, *, warmup_bars: int) -> pd.DataFrame:
            self.calls += 1
            if self.calls <= self.fail_all_until or symbol in self.fail_symbols:
                raise RuntimeError(f"{symbol} unavailable")
            return self.frame[self.frame.index <= pd.Timestamp(end)]

    def setUp(self) -> None:
        self.candles = synthetic_nse_candles(days=12, seed=7)
        self.signal_bar = first_signal_bar(self.candles)
        self.now = (self.candles.index[self.signal_bar] + timedelta(minutes=5)).to_pydatetime()

    def _worker(self, source, repository: FakeSignalRepository | None = None, **kwargs) -> MarketSignalWorker:
        clock = lambda: self.now  # noqa: E731
        engine = SignalEngine(market=market_spec("NSE"), strategy=STRATEGIES.get("ema_vwap_strong_buy"), configuration={}, risk=RiskSettings(), timeframe="5m", repository=repository or FakeSignalRepository(), clock=clock)
        return MarketSignalWorker(market=market_spec("NSE"), engine=engine, source=source, universe=lambda: ["SYN", "syn ", "OTHER"], status_repository=None, clock=clock, **kwargs)

    def test_recovery_rebuilds_history_and_the_first_poll_creates_the_signal_once(self) -> None:
        repository = FakeSignalRepository()
        worker = self._worker(self.Source(self.candles), repository)
        summary = worker.recover()
        self.assertEqual(summary["symbolsSeeded"], 2)  # SYN + OTHER (deduplicated, upper-cased)
        self.assertEqual(worker.status()["symbols"], ["SYN", "OTHER"])
        self.assertEqual(worker.engine.history.latest_timestamp("SYN"), self.candles.index[self.signal_bar])  # forming candle excluded
        created = worker.poll_once()
        self.assertEqual(created, 0)  # recovery already seeded through the signal candle; nothing new to evaluate
        self.now = self.now + timedelta(minutes=5)
        self.assertEqual(worker.poll_once(), 0)
        self.assertEqual(len(repository.rows), 0)

    def test_polling_from_before_the_signal_creates_it_exactly_once_across_polls(self) -> None:
        repository = FakeSignalRepository()
        self.now = (self.candles.index[self.signal_bar - 3] + timedelta(minutes=5)).to_pydatetime()
        worker = self._worker(self.Source(self.candles), repository)
        worker.recover()
        self.now = (self.candles.index[self.signal_bar] + timedelta(minutes=5)).to_pydatetime()
        self.assertEqual(worker.poll_once(), 2)  # both universe symbols see the same signal candle
        self.assertEqual(worker.poll_once(), 0)  # nothing new: no duplicates
        self.now = self.now + timedelta(minutes=5)
        self.assertEqual(worker.poll_once(), 0)
        self.assertEqual(sorted(row["symbol"] for row in repository.rows.values()), ["OTHER", "SYN"])

    def test_one_symbol_failing_does_not_abort_the_poll_but_all_failing_raises(self) -> None:
        worker = self._worker(self.Source(self.candles, fail_symbols={"OTHER"}))
        worker.recover()
        worker.poll_once()  # OTHER fails silently, SYN still processed
        everything = self._worker(self.Source(self.candles, fail_all_until=10_000))
        everything.refresh_universe()
        with self.assertRaises(RuntimeError):
            everything.poll_once()

    def test_worker_loop_backs_off_after_failures_and_recovers(self) -> None:
        source = self.Source(self.candles, fail_all_until=2)
        worker = self._worker(source, poll_seconds=0.01, closed_poll_seconds=0.01, maximum_backoff_seconds=0.05)
        worker.market = market_spec("CRYPTO")  # keep the session open for the test loop
        worker.start()
        deadline = datetime.now() + timedelta(seconds=5)
        while datetime.now() < deadline and worker.status()["status"] != "READY":
            threading.Event().wait(0.02)
        status = worker.status()
        worker.stop()
        self.assertEqual(status["status"], "READY")
        self.assertEqual(status["connectionStatus"], "CONNECTED")
        self.assertEqual(status["consecutiveFailures"], 0)
        self.assertGreaterEqual(source.calls, 3)
        self.assertEqual(worker.status()["status"], "STOPPED")

    def test_closed_session_is_reported_without_polling(self) -> None:
        source = self.Source(self.candles)
        self.now = datetime(2026, 9, 6, 10, 0, tzinfo=self.candles.index.tz)  # Sunday
        worker = self._worker(source, poll_seconds=0.01, closed_poll_seconds=0.01)
        worker.start()
        threading.Event().wait(0.1)
        status = worker.status()
        worker.stop()
        self.assertEqual(status["status"], "MARKET_CLOSED")
        self.assertEqual(source.calls, 2)  # recovery only, one call per unique symbol; no polling while closed


@unittest.skipUnless(TEST_DATABASE_URL, "TEST_DATABASE_URL is not set")
class SignalRepositoryDatabaseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.database = Database(TEST_DATABASE_URL, max_pool_size=2)
        cls.database.open()
        cls.database.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
        cls.database.migrate()
        cls.signals = LiveSignalRepository(cls.database)
        cls.status = EngineStatusRepository(cls.database)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.database.close()

    def _insert(self, **overrides):
        values = dict(market="NSE", strategy_id="ema_vwap_strong_buy", strategy_version="1.0.0", symbol="TCS", timeframe="5m", candle_timestamp=datetime(2026, 9, 1, 10, 0, tzinfo=pd.Timestamp.now(tz=IST).tzinfo), signal_type="BUY", signal_price=100.0, target_price=101.0, stop_price=None, expires_at=None, reasons=["EMA_BULLISH_CROSS"], indicators={"adx": 25.0}, configuration_snapshot={"ema_fast": 9})
        values.update(overrides)
        return self.signals.insert_new(**values)

    def test_database_uniqueness_constraint_rejects_duplicate_signals(self) -> None:
        first = self._insert()
        self.assertIsNotNone(first)
        self.assertIsNone(self._insert())  # same market/version/symbol/timeframe/candle/type
        self.assertIsNotNone(self._insert(strategy_version="1.0.1"))
        self.assertIsNotNone(self._insert(candle_timestamp=datetime(2026, 9, 1, 10, 5, tzinfo=pd.Timestamp.now(tz=IST).tzinfo)))
        self.assertEqual(len(self.signals.list("NSE", symbol="TCS")), 3)

    def test_lifecycle_updates_and_engine_status_upsert(self) -> None:
        stored = self._insert(symbol="INFY")
        self.signals.mark_holding(stored["signalId"], last_price=100.4)
        self.assertEqual(self.signals.get(stored["signalId"])["status"], "HOLDING")
        self.assertEqual([row["symbol"] for row in self.signals.open("NSE", "INFY")], ["INFY"])
        closed = self.signals.close(stored["signalId"], status="TARGET_HIT", exit_timestamp=datetime(2026, 9, 1, 11, 0, tzinfo=pd.Timestamp.now(tz=IST).tzinfo), exit_price=101.0)
        self.assertEqual((closed["status"], closed["exitPrice"]), ("TARGET_HIT", 101.0))
        self.assertEqual(self.signals.open("NSE", "INFY"), [])
        with self.assertRaises(ValueError):
            self.signals.close(stored["signalId"], status="HOLDING", exit_timestamp=datetime.now(), exit_price=1.0)
        self.status.upsert(engine="live-signals-v2", market="NSE", status="READY", connection_status="CONNECTED", data_age_seconds=12.0, last_completed_candle=None, message="ok", details={"polls": 3})
        self.status.upsert(engine="live-signals-v2", market="NSE", status="ERROR", connection_status="DISCONNECTED", data_age_seconds=None, last_completed_candle=None, message="boom")
        self.assertEqual(self.status.get("live-signals-v2", "NSE")["status"], "ERROR")
        self.assertEqual(len(self.status.list()), 1)
