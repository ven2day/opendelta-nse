"""Screener guarantees: configurable filters with recorded reasons, ranking without a hard-coded cap, manual selection, saved universes."""

from __future__ import annotations

import os
import threading
import time
import unittest
from datetime import datetime, timedelta

import pandas as pd
from fastapi import HTTPException

from backend.api.screener_routes import SaveUniverseRequest, ScreenerRunRequest, ScreenerServices, create_screener_router
from backend.data.database import Database
from backend.data.repositories import SavedUniverseRepository, ScreenerResultRepository, ScreenerRunRepository
from backend.markets.base import CandleBatch, market_spec
from backend.screener import ScreenerEngine, ScreenerFilters, rank_symbols
from backend.screener.engine import apply_manual_selection, symbol_metrics
from test_backtest_routes import endpoints
from test_strategy_engine import synthetic_nse_candles

IST = "Asia/Kolkata"
TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL", "").strip()


class CatalogueSource:
    """Serves synthetic histories whose price/volume scale by symbol so filters have something to bite on."""

    def __init__(self) -> None:
        self.profiles = {
            "CHEAP": dict(price=4.0, volume=1.0, days=12),
            "THIN": dict(price=1.0, volume=0.01, days=12),
            "BLUE": dict(price=1.0, volume=3.0, days=12),
            "MID": dict(price=1.0, volume=1.0, days=12),
            "SPARSE": dict(price=1.0, volume=1.0, days=2),
        }

    def candles(self, symbol: str, timeframe: str, start: datetime, end: datetime, *, warmup_bars: int) -> pd.DataFrame:
        if symbol == "MISSING":
            raise RuntimeError("no candle data for MISSING")
        profile = self.profiles[symbol]
        frame = synthetic_nse_candles(days=profile["days"], seed=len(symbol))
        frame[["Open", "High", "Low", "Close"]] *= profile["price"]
        frame["Volume"] *= profile["volume"]
        return frame


class BatchCatalogueSource(CatalogueSource):
    def __init__(self) -> None:
        super().__init__()
        self.batches: list[list[str]] = []

    def candles_many(self, symbols: list[str], timeframe: str, start: datetime, end: datetime, *, warmup_bars: int) -> CandleBatch:
        self.batches.append(list(symbols))
        frames: dict[str, pd.DataFrame] = {}
        errors: dict[str, Exception] = {}
        for symbol in symbols:
            try:
                frames[symbol] = super().candles(symbol, timeframe, start, end, warmup_bars=warmup_bars)
            except Exception as error:
                errors[symbol] = error
        return CandleBatch(frames=frames, errors=errors)


def engine() -> ScreenerEngine:
    return ScreenerEngine(market=market_spec("NSE"), source=CatalogueSource(), clock=lambda: datetime(2026, 9, 1, 12, 0, tzinfo=pd.Timestamp.now(tz=IST).tzinfo))


SYMBOLS = ["CHEAP", "THIN", "BLUE", "MID", "SPARSE", "MISSING"]


class FilterAndRankingTests(unittest.TestCase):
    def test_metrics_measure_price_liquidity_volume_volatility_and_coverage(self) -> None:
        metrics = symbol_metrics(synthetic_nse_candles(days=6, seed=3), timezone=IST, timeframe="5m", market="NSE")
        self.assertEqual(metrics["sessions"], 6)
        self.assertEqual(metrics["candleCoverage"], 1.0)
        self.assertGreater(metrics["averageTradedValue"], 0)
        self.assertGreater(metrics["averageVolume"], 0)
        self.assertGreater(metrics["volatilityPct"], 0)
        self.assertIsNotNone(metrics["lastPrice"])
        half = synthetic_nse_candles(days=6, seed=3).iloc[::2]
        self.assertAlmostEqual(symbol_metrics(half, timezone=IST, timeframe="5m", market="NSE")["candleCoverage"], 0.5, places=3)

    def test_filters_validate_and_reject_with_explicit_reasons(self) -> None:
        with self.assertRaises(ValueError):
            ScreenerFilters(minimum_price=10, maximum_price=5).validate()
        with self.assertRaises(ValueError):
            ScreenerFilters.from_mapping({"rankBy": "magic"})
        with self.assertRaises(ValueError):
            ScreenerFilters.from_mapping({"unknown": 1})
        filters = ScreenerFilters.from_mapping({"minimumPrice": 100, "maximumPrice": 1_000, "minimumAverageVolume": 5_000, "minimumVolatilityPct": 0.1, "maximumVolatilityPct": 5})
        self.assertEqual(filters.evaluate({"sessions": 1, "candleCoverage": 1.0, "lastPrice": 500}), "INSUFFICIENT_SESSIONS")
        self.assertEqual(filters.evaluate({"sessions": 9, "candleCoverage": 0.2, "lastPrice": 500}), "INSUFFICIENT_CANDLE_COVERAGE")
        self.assertEqual(filters.evaluate({"sessions": 9, "candleCoverage": 1.0, "lastPrice": 50}), "PRICE_BELOW_MINIMUM")
        self.assertEqual(filters.evaluate({"sessions": 9, "candleCoverage": 1.0, "lastPrice": 5_000}), "PRICE_ABOVE_MAXIMUM")
        self.assertEqual(filters.evaluate({"sessions": 9, "candleCoverage": 1.0, "lastPrice": 500, "averageVolume": 10}), "VOLUME_BELOW_MINIMUM")
        self.assertEqual(filters.evaluate({"sessions": 9, "candleCoverage": 1.0, "lastPrice": 500, "averageVolume": 9_000, "volatilityPct": 9}), "VOLATILITY_ABOVE_MAXIMUM")
        self.assertIsNone(filters.evaluate({"sessions": 9, "candleCoverage": 1.0, "lastPrice": 500, "averageVolume": 9_000, "volatilityPct": 1}))

    def test_ranking_keeps_every_passing_symbol_unless_the_user_caps_it(self) -> None:
        rows = [{"symbol": name, "passed": True, "metrics": {"averageTradedValue": value}} for name, value in (("A", 10), ("B", 30), ("C", 20))]
        rows.append({"symbol": "D", "passed": False, "rejection_reason": "PRICE_BELOW_MINIMUM", "metrics": {"averageTradedValue": 99}})
        everything = rank_symbols(rows, "liquidity", None)
        self.assertEqual([(row["symbol"], row["rank"]) for row in everything], [("B", 1), ("C", 2), ("A", 3)])
        capped = rank_symbols(rows, "liquidity", 2)
        self.assertEqual([row["symbol"] for row in capped if row["passed"]], ["B", "C"])
        self.assertEqual(next(row for row in capped if row["symbol"] == "A")["rejection_reason"], "RANKED_OUT_BY_MAXIMUM_SYMBOLS")

    def test_manual_include_and_exclude(self) -> None:
        self.assertEqual(apply_manual_selection(["A", "B", "C"], includes=["d", "B"], excludes=["c"]), ["A", "B", "D"])


class EngineTests(unittest.TestCase):
    def test_engine_records_every_symbol_with_a_pass_or_a_reason(self) -> None:
        filters = ScreenerFilters(minimum_price=100, minimum_average_volume=15_000, minimum_sessions=5)
        outcome = engine().run("run-1", SYMBOLS + ["blue"], filters)
        by_symbol = {row["symbol"]: row for row in outcome.rows}
        self.assertEqual(set(by_symbol), set(SYMBOLS))  # duplicates and case normalised
        self.assertEqual(by_symbol["MISSING"]["rejection_reason"], "CANDLE_DATA_UNAVAILABLE")
        self.assertEqual(by_symbol["SPARSE"]["rejection_reason"], "INSUFFICIENT_SESSIONS")
        self.assertEqual(by_symbol["THIN"]["rejection_reason"], "VOLUME_BELOW_MINIMUM")
        self.assertTrue(by_symbol["BLUE"]["passed"] and by_symbol["MID"]["passed"] and by_symbol["CHEAP"]["passed"])
        self.assertEqual(outcome.passing_symbols()[0], "CHEAP")  # highest traded value ranks first
        self.assertEqual([item["symbol"] for item in outcome.failed], ["MISSING"])
        self.assertEqual(len(outcome.passed) + len(outcome.rejected), 6)

    def test_maximum_symbols_and_rank_key_are_user_choices(self) -> None:
        outcome = engine().run("run-2", SYMBOLS, ScreenerFilters(minimum_sessions=5, rank_by="volume", maximum_symbols=2))
        self.assertEqual(outcome.passing_symbols(), ["BLUE", "MID"])  # BLUE has 3x volume, then MID/CHEAP tie broken by name
        unlimited = engine().run("run-3", SYMBOLS, ScreenerFilters(minimum_sessions=5, rank_by="volume"))
        self.assertEqual(len(unlimited.passed), 4)

    def test_cancellation_stops_between_symbols(self) -> None:
        event = threading.Event()
        event.set()
        outcome = engine().run("run-4", SYMBOLS, ScreenerFilters(), cancel_event=event)
        self.assertEqual(outcome.rows, [])

    def test_batch_source_is_bounded_and_preserves_per_symbol_results(self) -> None:
        source = BatchCatalogueSource()
        batch_engine = ScreenerEngine(
            market=market_spec("NSE"),
            source=source,
            batch_size=2,
            clock=lambda: datetime(2026, 9, 1, 12, 0, tzinfo=pd.Timestamp.now(tz=IST).tzinfo),
        )
        outcome = batch_engine.run("batch-run", SYMBOLS, ScreenerFilters(minimum_sessions=5))

        self.assertEqual(source.batches, [["CHEAP", "THIN"], ["BLUE", "MID"], ["SPARSE", "MISSING"]])
        self.assertEqual(len(outcome.rows), len(SYMBOLS))
        self.assertEqual([item["symbol"] for item in outcome.failed], ["MISSING"])
        self.assertEqual(next(row for row in outcome.rows if row["symbol"] == "MISSING")["rejection_reason"], "CANDLE_DATA_UNAVAILABLE")

    def test_batch_size_is_bounded(self) -> None:
        with self.assertRaises(ValueError):
            ScreenerEngine(market=market_spec("NSE"), source=CatalogueSource(), batch_size=0)
        with self.assertRaises(ValueError):
            ScreenerEngine(market=market_spec("NSE"), source=CatalogueSource(), batch_size=251)


class FakeRuns:
    def __init__(self) -> None:
        self.records = {}

    def create(self, *, market, filters, symbols_total):
        import uuid

        run_id = str(uuid.uuid4())
        self.records[run_id] = {"runId": run_id, "market": market, "status": "RUNNING", "filters": filters, "symbolsTotal": symbols_total, "symbolsPassed": 0, "error": None}
        return dict(self.records[run_id])

    def get(self, run_id):
        import uuid

        uuid.UUID(str(run_id))
        return dict(self.records[run_id])

    def list(self, market=None, *, limit=50):
        return [dict(r) for r in self.records.values() if market is None or r["market"] == market][:limit]

    def finish(self, run_id, *, status, symbols_passed, error=None):
        self.records[run_id].update(status=status, symbolsPassed=symbols_passed, error=error)
        return dict(self.records[run_id])


class FakeResults:
    def __init__(self) -> None:
        self.rows = {}

    def insert_many(self, run_id, rows):
        self.rows[run_id] = [dict(r) for r in rows]
        return len(rows)

    def list(self, run_id, *, passed=None, limit=5000):
        rows = [r for r in self.rows.get(run_id, []) if passed is None or r["passed"] == passed]
        rows.sort(key=lambda r: (r.get("rank") is None, r.get("rank") or 0, r["symbol"]))
        return [{"runId": run_id, "symbol": r["symbol"], "passed": r["passed"], "rank": r.get("rank"), "score": r.get("score"), "rejectionReason": r.get("rejection_reason"), "metrics": r["metrics"]} for r in rows][:limit]


class FakeUniverses:
    def __init__(self) -> None:
        self.saved = []

    def save(self, **values):
        import uuid

        record = {"universeId": str(uuid.uuid4()), **values, "manualIncludes": list(values["manual_includes"]), "manualExcludes": list(values["manual_excludes"]), "active": values["activate"]}
        for item in self.saved:
            if item["market"] == values["market"] and values["activate"]:
                item["active"] = False
        self.saved.append(record)
        return dict(record)

    def list(self, market=None, *, limit=50):
        return [dict(r) for r in self.saved if market is None or r["market"] == market][:limit]

    def active(self, market):
        return next((dict(r) for r in self.saved if r["market"] == market and r["active"]), None)

    def activate(self, universe_id):
        target = next(r for r in self.saved if r["universeId"] == universe_id)
        for item in self.saved:
            if item["market"] == target["market"]:
                item["active"] = item is target
        return dict(target)


class RouteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.runs, self.results, self.universes = FakeRuns(), FakeResults(), FakeUniverses()
        self.services = ScreenerServices(runs=lambda: self.runs, results=lambda: self.results, universes=lambda: self.universes, engine_for=lambda market: engine(), catalogue_for=lambda market: SYMBOLS)
        self.api = endpoints(create_screener_router(self.services))

    def tearDown(self) -> None:
        self.services.shutdown()

    def _wait(self, run_id: str) -> dict:
        deadline = time.time() + 20
        while time.time() < deadline:
            record = self.api["GET /v2/screener/runs/{run_id}"](run_id)
            if record["status"] != "RUNNING":
                return record
            time.sleep(0.05)
        raise AssertionError("screener run did not finish")

    def test_run_from_catalogue_then_save_and_activate_a_universe(self) -> None:
        started = self.api["POST /v2/screener/runs"](ScreenerRunRequest(market="NSE", filters={"minimumSessions": 5, "rankBy": "liquidity"}))
        self.assertEqual(started["status"], "RUNNING")
        record = self._wait(started["runId"])
        self.assertEqual((record["status"], record["symbolsTotal"], record["symbolsPassed"]), ("COMPLETE", 6, 4))
        results = self.api["GET /v2/screener/runs/{run_id}/results"](started["runId"], passed=False, limit=100)["results"]
        self.assertEqual({row["symbol"]: row["rejectionReason"] for row in results}, {"MISSING": "CANDLE_DATA_UNAVAILABLE", "SPARSE": "INSUFFICIENT_SESSIONS"})
        saved = self.api["POST /v2/screener/universes"](SaveUniverseRequest(runId=started["runId"], name="Liquid", maximumSymbols=3, manualIncludes=["sparse"], manualExcludes=["MID"]))
        self.assertEqual(saved["symbols"], ["CHEAP", "BLUE", "SPARSE"])
        self.assertTrue(saved["active"])
        listed = self.api["GET /v2/screener/universes"](market="NSE", limit=10)
        self.assertEqual(listed["active"]["NSE"]["universeId"], saved["universeId"])
        second = self.api["POST /v2/screener/universes"](SaveUniverseRequest(runId=started["runId"], name="All", activate=False))
        self.assertEqual(len(second["symbols"]), 4)
        self.assertFalse(second["active"])
        activated = self.api["POST /v2/screener/universes/{universe_id}/activate"](second["universeId"])
        self.assertTrue(activated["active"])
        self.assertFalse(self.universes.saved[0]["active"])

    def test_validation_and_not_found(self) -> None:
        with self.assertRaises(HTTPException) as bad_filter:
            self.api["POST /v2/screener/runs"](ScreenerRunRequest(market="NSE", filters={"rankBy": "magic"}))
        self.assertEqual(bad_filter.exception.status_code, 422)
        with self.assertRaises(HTTPException) as empty:
            self.api["POST /v2/screener/runs"](ScreenerRunRequest(market="NSE", symbols=["  "]))
        self.assertEqual(empty.exception.status_code, 422)
        with self.assertRaises(HTTPException) as missing:
            self.api["GET /v2/screener/runs/{run_id}"]("00000000-0000-0000-0000-000000000000")
        self.assertEqual(missing.exception.status_code, 404)
        self.assertIn("liquidity", self.api["GET /v2/screener/filters"]()["rankBy"])

    def test_official_presets_are_listed_and_resolved_by_the_backend(self) -> None:
        presets = self.api["GET /v2/screener/presets"](market="NSE")["presets"]
        self.assertEqual([(item["presetId"], len(item["symbols"])) for item in presets], [("nifty_50", 50), ("nifty_top_20", 20)])
        self.assertEqual(self.api["GET /v2/screener/presets"](market="CRYPTO")["presets"], [])

        started = self.api["POST /v2/screener/runs"](ScreenerRunRequest(market="NSE", presetId="nifty_top_20"))
        self.assertEqual(started["symbolsTotal"], 20)
        self._wait(started["runId"])

        for request in (
            ScreenerRunRequest(market="CRYPTO", presetId="nifty_50"),
            ScreenerRunRequest(market="NSE", presetId="nifty_50", symbols=["TCS"]),
        ):
            with self.assertRaises(HTTPException) as caught:
                self.api["POST /v2/screener/runs"](request)
            self.assertEqual(caught.exception.status_code, 422)


@unittest.skipUnless(TEST_DATABASE_URL, "TEST_DATABASE_URL is not set")
class ScreenerDatabaseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.database = Database(TEST_DATABASE_URL, max_pool_size=2)
        cls.database.open()
        cls.database.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
        cls.database.migrate()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.database.close()

    def test_runs_results_and_universes_round_trip(self) -> None:
        runs, results, universes = ScreenerRunRepository(self.database), ScreenerResultRepository(self.database), SavedUniverseRepository(self.database)
        outcome = engine().run("ignored", SYMBOLS, ScreenerFilters(minimum_sessions=5, maximum_symbols=2))
        record = runs.create(market="NSE", filters=outcome.filters.public(), symbols_total=len(SYMBOLS))
        results.insert_many(record["runId"], outcome.rows)
        finished = runs.finish(record["runId"], status="COMPLETE", symbols_passed=len(outcome.passed))
        self.assertEqual((finished["status"], finished["symbolsPassed"]), ("COMPLETE", 2))
        stored = results.list(record["runId"])
        self.assertEqual(len(stored), 6)
        self.assertEqual([row["symbol"] for row in results.list(record["runId"], passed=True)], outcome.passing_symbols())
        self.assertIn("RANKED_OUT_BY_MAXIMUM_SYMBOLS", {row["rejectionReason"] for row in stored})
        universe = universes.save(market="NSE", name="Top 2", symbols=outcome.passing_symbols(), source_run_id=record["runId"], manual_includes=["EXTRA"], manual_excludes=[], activate=True)
        self.assertEqual(universes.active_symbols("NSE"), [*outcome.passing_symbols(), "EXTRA"])
        other = universes.save(market="NSE", name="Other", symbols=["ONLY"], activate=True)
        self.assertEqual(universes.active("NSE")["universeId"], other["universeId"])  # one active per market
        universes.activate(universe["universeId"])
        self.assertEqual(universes.active("NSE")["universeId"], universe["universeId"])
        self.assertIsNone(universes.active("CRYPTO"))
        self.assertEqual(runs.list("NSE")[0]["runId"], record["runId"])

    def test_restart_recovery_marks_running_screener_runs_failed(self) -> None:
        runs = ScreenerRunRepository(self.database)
        stale = runs.create(market="NSE", filters={}, symbols_total=751)
        self.assertEqual(runs.recover_interrupted(), 1)
        recovered = runs.get(stale["runId"])
        self.assertEqual(recovered["status"], "FAILED")
        self.assertEqual(recovered["error"], "Interrupted by a service restart")
        self.assertIsNotNone(recovered["completedAt"])
        self.assertEqual(runs.recover_interrupted(), 0)
