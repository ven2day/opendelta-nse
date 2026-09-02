from __future__ import annotations

import unittest
from datetime import datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import pandas as pd

from backtest_api import (
    BacktestHistorySaveRequest,
    BacktestRequest,
    IST,
    LiveSignalDecisionRequest,
    LiveSignalSettingsRequest,
    LiveUniverseRequest,
    LiveUniverseSaveRequest,
    MarketSymbolRequest,
    GlobalPriceSettingsRequest,
    HistoricalDataStore,
    MAX_SYMBOLS_PER_RUN,
    PaperBuyRequest,
    PaperCloseRequest,
    StrongBuyConfigurationRequest,
    app,
    _entry_signal,
    list_market_symbols,
    application_settings,
    update_application_settings,
    prepare_candles,
    run_backtest,
    run_rsi_range_backtest,
    simulate_symbol,
    start_backtest_job,
)
from fastapi import HTTPException
from main import DhanAPIError, DhanConfig


class SignalTests(unittest.TestCase):
    def test_strong_buy_failure_engine_configuration_maps_to_research_model(self) -> None:
        request = StrongBuyConfigurationRequest(
            failureEngineMode="RESEARCH_COMPARE",
            failureMaximumHoldingBars=250,
            failureDecisionPersistenceBars=3,
            failureRoundTripCostBps=18,
            failureMinimumStateLots=12,
            failureMinimumCandidateExits=25,
            failureMinimumCandidateExitsPerFold=6,
            failureMaximumAuditRows=250,
        )
        config = request.failure_config()
        self.assertEqual(config.mode, "RESEARCH_COMPARE")
        self.assertEqual(config.maximum_holding_bars, 250)
        self.assertEqual(config.decision_persistence_bars, 3)
        self.assertEqual(config.round_trip_cost_bps, 18)
        self.assertEqual(config.minimum_state_lots, 12)
        self.assertEqual(config.minimum_candidate_exits, 25)
        self.assertEqual(config.minimum_candidate_exits_per_fold, 6)
        self.assertEqual(config.maximum_audit_rows, 250)

    def test_entry_signal_fires_only_when_rsi_enters_range(self) -> None:
        values = pd.Series([35.0, 29.0, 25.0, 31.0, 30.0, 29.0])
        self.assertEqual(
            _entry_signal(values, 20, 30).tolist(),
            [False, True, False, False, True, False],
        )

    def test_trade_executes_on_next_candle_open(self) -> None:
        index = pd.date_range("2025-01-01 15:30", periods=20, freq="1D", tz=IST)
        opens = [100.0] * 20
        closes = [100.0] * 20
        rsi = [40.0] * 20
        rsi[3] = 25.0
        opens[4] = 90.0
        closes[4:8] = [92.0, 96.0, 101.0, 106.0]
        rsi[7] = 55.0
        opens[8] = 110.0
        candles = pd.DataFrame(
            {
                "Open": opens,
                "High": [max(open_price, close_price) + 1 for open_price, close_price in zip(opens, closes, strict=True)],
                "Low": [min(open_price, close_price) - 1 for open_price, close_price in zip(opens, closes, strict=True)],
                "Close": closes,
                "Volume": [1_000] * 20,
                "RSI": rsi,
            },
            index=index,
        )

        result = simulate_symbol(
            "TEST",
            candles,
            timeframe="1d",
            entry_low=20,
            entry_high=30,
            exit_low=50,
            exit_high=70,
            nifty_return_pct=5.0,
        )

        self.assertEqual(result["closedTrades"], 1)
        trade = result["trades"][0]
        self.assertEqual(trade["entryTime"], index[4].isoformat())
        self.assertEqual(trade["exitTime"], index[8].isoformat())
        self.assertAlmostEqual(trade["entryPrice"], 90.05, places=2)
        self.assertAlmostEqual(trade["exitPrice"], 109.95, places=2)
        self.assertGreaterEqual(trade["returnPct"], 1.0)
        self.assertGreater(result["strategyReturnPct"], 0)
        self.assertEqual(result["verdict"], "profitable")

    def test_high_rsi_exit_is_held_until_net_profit_reaches_one_percent(self) -> None:
        index = pd.date_range("2025-01-01 15:30", periods=20, freq="1D", tz=IST)
        opens = [100.0] * 20
        closes = [100.0] * 20
        rsi = [40.0] * 20
        rsi[3] = 25.0
        opens[4] = 100.0
        rsi[7] = 55.0
        opens[8] = 99.0
        rsi[11] = 58.0
        opens[12] = 103.0
        candles = pd.DataFrame(
            {
                "Open": opens,
                "High": [max(open_price, close_price) + 1 for open_price, close_price in zip(opens, closes, strict=True)],
                "Low": [min(open_price, close_price) - 1 for open_price, close_price in zip(opens, closes, strict=True)],
                "Close": closes,
                "Volume": [1_000] * 20,
                "RSI": rsi,
            },
            index=index,
        )

        result = simulate_symbol(
            "TEST",
            candles,
            timeframe="1d",
            entry_low=20,
            entry_high=30,
            exit_low=50,
            exit_high=70,
            nifty_return_pct=None,
        )

        self.assertEqual(result["heldExitSignals"], 1)
        self.assertEqual(result["closedTrades"], 1)
        self.assertEqual(result["trades"][0]["exitTime"], index[12].isoformat())
        self.assertGreaterEqual(result["trades"][0]["returnPct"], 1.0)
        hold = next(point for point in result["chart"] if point["action"] == "hold")
        self.assertLess(hold["netReturnPct"], hold["requiredNetProfitPct"])
        self.assertIn("Position kept open", hold["reason"])

    def test_one_percent_raw_gain_is_not_enough_after_costs(self) -> None:
        index = pd.date_range("2025-01-01 15:30", periods=20, freq="1D", tz=IST)
        opens = [100.0] * 20
        closes = [100.0] * 20
        rsi = [40.0] * 20
        rsi[3] = 25.0
        rsi[7] = 55.0
        opens[8] = 101.0
        candles = pd.DataFrame(
            {
                "Open": opens,
                "High": [max(open_price, close_price) + 1 for open_price, close_price in zip(opens, closes, strict=True)],
                "Low": [min(open_price, close_price) - 1 for open_price, close_price in zip(opens, closes, strict=True)],
                "Close": closes,
                "Volume": [1_000] * 20,
                "RSI": rsi,
            },
            index=index,
        )

        result = simulate_symbol(
            "TEST",
            candles,
            timeframe="1d",
            entry_low=20,
            entry_high=30,
            exit_low=50,
            exit_high=70,
            nifty_return_pct=None,
        )

        self.assertEqual(result["closedTrades"], 0)
        self.assertEqual(result["heldExitSignals"], 1)
        self.assertIsNotNone(result["openPosition"])
        hold = next(point for point in result["chart"] if point["action"] == "hold")
        self.assertLess(hold["netReturnPct"], 1.0)


class CandlePreparationTests(unittest.TestCase):
    def test_dhan_cache_fetch_dual_writes_provider_native_completed_candles(self) -> None:
        class Writer:
            def __init__(self) -> None:
                self.rows = []

            def write(self, candles):
                self.rows.extend(candles)

        with TemporaryDirectory() as directory:
            writer = Writer()
            store = object.__new__(HistoricalDataStore)
            store.cache_directory = Path(directory)
            store.canonical_writer = writer
            store._security_map = {"TEST": "123"}
            store._nifty_security_id = "13"
            store._read_cache = lambda path: None
            store._fetch_raw = lambda *args, **kwargs: pd.DataFrame(
                {
                    "Open": [100.0, 101.0],
                    "High": [101.0, 102.0],
                    "Low": [99.0, 100.0],
                    "Close": [100.5, 101.5],
                    "Volume": [1_000.0, 1_100.0],
                },
                index=pd.to_datetime(["2026-08-28T09:15+05:30", "2026-08-28T09:20+05:30"]),
            )
            now = datetime(2026, 8, 28, 9, 30, tzinfo=IST)

            result = store.candles(
                "TEST", "5m", 1, datetime(2026, 8, 28, 9, 0, tzinfo=IST), now
            )

            self.assertEqual(len(result), 2)
            self.assertEqual(len(writer.rows), 2)
            self.assertEqual({item.instrument_id for item in writer.rows}, {"123"})
            self.assertEqual({item.timeframe for item in writer.rows}, {"5m"})

    def test_fresh_second_resolution_candles_accept_microsecond_boundary(self) -> None:
        index = pd.date_range("2025-01-01", periods=3, freq="1D", tz=IST).as_unit("s")
        frame = pd.DataFrame(
            {
                "Open": [100, 101, 102],
                "High": [101, 102, 103],
                "Low": [99, 100, 101],
                "Close": [100.5, 101.5, 102.5],
                "Volume": [1_000, 1_100, 1_200],
            },
            index=index,
        )

        prepared = prepare_candles(
            frame,
            "1d",
            datetime(2025, 1, 2, 0, 0, 0, 123456, tzinfo=IST),
            datetime(2025, 1, 10, 16, 0, tzinfo=IST),
        )

        self.assertEqual(prepared.index.dtype, pd.DatetimeTZDtype(unit="us", tz=IST))
        self.assertEqual(prepared.index.tolist(), [pd.Timestamp("2025-01-03", tz=IST)])

    def test_two_hour_candles_are_anchored_to_nse_open_in_ist(self) -> None:
        session_start = pd.Timestamp("2025-01-02 09:15", tz=IST)
        index = pd.date_range(session_start, periods=7, freq="60min")
        frame = pd.DataFrame(
            {
                "Open": range(100, 107),
                "High": range(101, 108),
                "Low": range(99, 106),
                "Close": range(100, 107),
                "Volume": [100] * 7,
            },
            index=index,
        )
        prepared = prepare_candles(
            frame,
            "2h",
            datetime(2025, 1, 1, tzinfo=IST),
            datetime(2025, 1, 3, tzinfo=IST),
        )

        self.assertEqual(prepared.index[0], pd.Timestamp("2025-01-02 11:15", tz=IST))
        self.assertEqual(prepared.index[-1], pd.Timestamp("2025-01-02 15:30", tz=IST))
        self.assertEqual(len(prepared), 4)


class RequestTests(unittest.TestCase):
    def test_symbols_are_normalized_and_deduplicated(self) -> None:
        request = BacktestRequest(symbols=[" lupin.ns ", "LUPIN", "INFY"])
        self.assertEqual(request.symbols, ["LUPIN", "INFY"])

    def test_managed_universe_can_grow_past_the_original_bundle(self) -> None:
        self.assertGreater(MAX_SYMBOLS_PER_RUN, 750)
        symbols = [f"SYMBOL{index}" for index in range(751)]
        self.assertEqual(len(BacktestRequest(symbols=symbols).symbols), 751)

    def test_exit_model_contract_preserves_legacy_clients(self) -> None:
        legacy = BacktestRequest(symbols=["LUPIN"], strategyMode="rsi_recovery")
        self.assertEqual(legacy.resolved_exit_model(), "LEGACY_FIXED_TARGET")

        old_protected_client = BacktestRequest(
            symbols=["LUPIN"],
            strategyMode="rsi_recovery",
            exitProtectionEnabled=True,
        )
        self.assertEqual(old_protected_client.resolved_exit_model(), "LEGACY_PROTECTED_TARGET")

        dynamic = BacktestRequest(
            symbols=["LUPIN"],
            strategyMode="rsi_recovery",
            exitModel="ATR_DYNAMIC_TP_SL",
        )
        self.assertEqual(dynamic.resolved_exit_model(), "ATR_DYNAMIC_TP_SL")
        self.assertTrue(dynamic.exitProtectionEnabled)

    def test_atr_optimizer_api_surface_is_registered(self) -> None:
        paths = {route.path for route in app.routes}
        self.assertIn("/backtest/optimize-atr", paths)

    def test_rsi_exit_comparison_api_surface_is_registered(self) -> None:
        paths = {route.path for route in app.routes}
        self.assertIn("/backtest/compare-rsi-exits", paths)

    def test_live_universe_defaults_and_overrides_are_validated(self) -> None:
        request = LiveUniverseRequest()
        self.assertEqual(request.topN, 300)
        self.assertEqual(request.minimumPrice, 500)
        self.assertEqual(request.maximumPrice, 2000)
        self.assertEqual(request.minimumBuyObservations, 50)
        normalized = LiveUniverseRequest(manualPins=[" reliance.ns ", "RELIANCE"])
        self.assertEqual(normalized.manualPins, ["RELIANCE"])
        with self.assertRaisesRegex(ValueError, "greater than minimum"):
            LiveUniverseRequest(minimumPrice=500, maximumPrice=500)
        with self.assertRaisesRegex(ValueError, "both pinned and excluded"):
            LiveUniverseRequest(manualPins=["SBIN"], manualExclusions=["SBIN"])

    def test_live_universe_save_requires_preview_hash(self) -> None:
        with self.assertRaises(ValueError):
            LiveUniverseSaveRequest(configurationHash="not-a-hash")

    def test_live_universe_api_surface_is_registered(self) -> None:
        paths = {route.path for route in app.routes}
        self.assertTrue(
            {
                "/live-universe/config",
                "/live-universe/preview",
                "/live-universe/save",
                "/live-universe/active",
                "/live-universe/history",
                "/live-universe/rebuild",
                "/live-universe/symbols",
                "/live-universe/export",
            }.issubset(paths)
        )

    def test_live_signal_defaults_and_manual_models_are_validated(self) -> None:
        settings = LiveSignalSettingsRequest()
        self.assertEqual(settings.entryRangeMethod, "FIXED_PERCENT")
        self.assertEqual(settings.fixedLowerPct, 0.15)
        self.assertEqual(settings.fixedUpperPct, 0.10)
        self.assertEqual(settings.paperAllocation, 25_000)
        self.assertEqual(LiveSignalDecisionRequest(action="WATCH").action, "WATCH")
        self.assertEqual(PaperBuyRequest(actualEntryPrice=100, actualQuantity=25).actualQuantity, 25)
        self.assertEqual(PaperCloseRequest(actualExitPrice=100.5).actualExitPrice, 100.5)
        with self.assertRaises(ValueError):
            PaperBuyRequest(actualEntryPrice=100, actualQuantity=0)

    def test_live_signal_paper_only_api_surface_is_registered(self) -> None:
        paths = {route.path for route in app.routes}
        self.assertTrue(
            {
                "/live-signals/status",
                "/live-signals/settings",
                "/live-signals",
                "/live-signals/{signal_id}/decision",
                "/live-signals/{signal_id}/paper-buy",
                "/paper-trades",
                "/paper-trades/{paper_trade_id}/close",
                "/live-signals/study",
            }.issubset(paths)
        )

    def test_market_data_refresh_api_surface_is_registered(self) -> None:
        paths = {route.path for route in app.routes}
        self.assertTrue(
            {
                "/market-data/status",
                "/market-data/csv",
                "/market-data/refresh",
                "/market-data/symbols",
            }.issubset(paths)
        )
        symbol_methods = {
            method
            for route in app.routes
            if route.path == "/market-data/symbols"
            for method in (route.methods or set())
        }
        self.assertTrue({"GET", "POST"}.issubset(symbol_methods))

    def test_global_settings_api_surface_is_registered(self) -> None:
        methods = {
            method
            for route in app.routes
            if route.path == "/application-settings"
            for method in (route.methods or set())
        }
        self.assertTrue({"GET", "PUT"}.issubset(methods))

    def test_account_backtest_history_api_surface_is_registered(self) -> None:
        methods = {
            (route.path, method)
            for route in app.routes
            if route.path.startswith("/backtest-history")
            for method in (route.methods or set())
        }
        self.assertTrue(
            {
                ("/backtest-history", "GET"),
                ("/backtest-history", "POST"),
                ("/backtest-history/{run_id}", "GET"),
                ("/backtest-history/{run_id}", "DELETE"),
            }.issubset(methods)
        )

    def test_backtest_history_request_preserves_result_metadata(self) -> None:
        request = BacktestHistorySaveRequest(
            id="run-1",
            completedAt="2026-08-28T10:00:00+00:00",
            strategyMode="rsi_recovery",
            strategyName="RSI Recovery Scalping",
            timeframe="5m",
            durationYears=1,
            symbolCount=1,
            response={"metadata": {"runId": "run-1"}, "results": []},
        )
        self.assertEqual(request.persisted()["response"]["metadata"]["runId"], "run-1")

    def test_market_symbol_list_reads_the_runtime_registry(self) -> None:
        with TemporaryDirectory() as directory:
            symbols_file = Path(directory) / "symbols.csv"
            symbols_file.write_text("symbol\nALPHA\nBETA\nGAMMA\n", encoding="utf-8")
            with patch.dict("os.environ", {"SYMBOLS_FILE": str(symbols_file), "APPLICATION_SETTINGS_DIR": directory}):
                with patch("backtest_api._application_settings_repository", None):
                    response = list_market_symbols()
        self.assertEqual(response["symbols"], ["ALPHA", "BETA", "GAMMA"])
        self.assertEqual(response["symbolCount"], 3)
        self.assertEqual(response["totalSymbolCount"], 3)
        self.assertFalse(response["priceFilterApplied"])

    def test_market_symbol_list_applies_saved_global_price_range(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            symbols_file = root / "symbols.csv"
            market_file = root / "market.csv"
            symbols_file.write_text("symbol\nLOW\nMIN\nMAX\nHIGH\nMISSING\n", encoding="utf-8")
            market_file.write_text("symbol,entry_price\nLOW,109\nMIN,110\nMAX,3000\nHIGH,3001\n", encoding="utf-8")
            environment = {
                "SYMBOLS_FILE": str(symbols_file),
                "LIVE_MARKET_DATA_FILE": str(market_file),
                "APPLICATION_SETTINGS_DIR": str(root / "settings"),
            }
            with patch.dict("os.environ", environment):
                with patch("backtest_api._application_settings_repository", None):
                    update_application_settings(GlobalPriceSettingsRequest(minimumPrice=110, maximumPrice=3000))
                    response = list_market_symbols()
                    current = application_settings()
        self.assertEqual(response["symbols"], ["MIN", "MAX"])
        self.assertEqual(response["missingPriceCount"], 1)
        self.assertTrue(response["priceFilterApplied"])
        self.assertEqual(current["priceRange"], {"minimumPrice": 110.0, "maximumPrice": 3000.0})

    def test_market_symbol_request_normalizes_nse_suffix(self) -> None:
        self.assertEqual(MarketSymbolRequest(symbol=" alpha.ns ").symbol, "ALPHA")

    def test_unavailable_batch_returns_symbol_errors_instead_of_failing_request(self) -> None:
        class UnavailableStore:
            @staticmethod
            def universe() -> list[str]:
                return ["A", "B"]

            @staticmethod
            def candles(*args, **kwargs):
                raise DhanAPIError("Historical data is temporarily unavailable")

        result = run_rsi_range_backtest(
            BacktestRequest(symbols=["A", "B"], timeframe="4h"),
            UnavailableStore(),
            datetime(2026, 8, 23, 15, 30, tzinfo=IST),
        )

        self.assertEqual(result["results"], [])
        self.assertEqual([item["symbol"] for item in result["errors"]], ["A", "B"])
        self.assertTrue(any("universe run continued" in item for item in result["warnings"]))


class StrategyLaunchRestrictionTests(unittest.TestCase):
    class RefusingStore:
        @staticmethod
        def universe() -> list[str]:
            raise AssertionError("A retired strategy must never reach a data store")

        @staticmethod
        def candles(*args, **kwargs):
            raise AssertionError("A retired strategy must never reach a data store")

    def test_only_strong_buy_can_start_a_new_backtest(self) -> None:
        for strategy_mode, extra in (
            ("rsi_range", {"timeframe": "1d"}),
            ("rsi_recovery", {"timeframe": "5m"}),
            ("top_5_opening_range_breakout", {"timeframe": "5m"}),
        ):
            with self.subTest(strategyMode=strategy_mode):
                request = BacktestRequest(symbols=["LUPIN"], strategyMode=strategy_mode, **extra)
                with self.assertRaises(ValueError) as caught:
                    run_backtest(request, self.RefusingStore())
                self.assertIn("EMA/VWAP Strong Buy", str(caught.exception))

    def test_strong_buy_is_still_dispatched_to_its_engine(self) -> None:
        request = BacktestRequest(
            symbols=["LUPIN"], strategyMode="ema_vwap_strong_buy", timeframe="5m",
        )
        captured: dict[str, object] = {}

        def engine(passed_request, store, now_ist=None):
            captured["request"] = passed_request
            return {"metadata": {"strategyMode": "ema_vwap_strong_buy"}}

        with patch("backtest_api.run_strong_buy_backtest", engine):
            result = run_backtest(request, self.RefusingStore())

        self.assertIs(captured["request"], request)
        self.assertEqual(result["metadata"]["strategyMode"], "ema_vwap_strong_buy")

    def test_asynchronous_job_endpoint_refuses_every_strategy(self) -> None:
        for strategy_mode, extra in (
            ("top_5_opening_range_breakout", {"timeframe": "5m"}),
            ("ema_vwap_strong_buy", {"timeframe": "5m"}),
        ):
            with self.subTest(strategyMode=strategy_mode):
                request = BacktestRequest(symbols=["LUPIN"], strategyMode=strategy_mode, **extra)
                with self.assertRaises(HTTPException) as caught:
                    start_backtest_job(request)
                self.assertEqual(caught.exception.status_code, 422)

    def test_asynchronous_job_endpoint_accepts_strong_buy_failure_research(self) -> None:
        request = BacktestRequest(
            symbols=["LUPIN"],
            strategyMode="ema_vwap_strong_buy",
            timeframe="5m",
            strongBuyConfiguration={"failureEngineMode": "RESEARCH_COMPARE"},
        )
        with (
            patch("backtest_api.get_store", return_value=self.RefusingStore()),
            patch("backtest_api._backtest_job_service.start", return_value={"jobId": "research-job"}) as start,
        ):
            result = start_backtest_job(request)

        self.assertEqual(result, {"jobId": "research-job"})
        self.assertEqual(start.call_args.kwargs["symbols_total"], 1)


class CacheFreshnessTests(unittest.TestCase):
    """A cached candle file's freshness must depend on whether NSE could have
    produced newer data since it was written, not just wall-clock minutes -
    otherwise every full-universe backtest run outside market hours forces a
    live re-fetch of every symbol even though nothing new could exist."""

    def setUp(self) -> None:
        self.temp = TemporaryDirectory()
        config = DhanConfig(
            client_id="1", pin="000000", totp_secret="A", auth_base_url="https://auth",
            base_url="https://api.dhan.co/v2", exchange_segment="NSE_EQ", instrument="EQUITY",
            instrument_master_url="https://master",
            token_cache_file=Path(self.temp.name) / "token",
            symbols_file=Path(self.temp.name) / "symbols",
            output_file=Path(self.temp.name) / "out", history_days=1,
            requests_per_second=1, request_retries=2, session_retry_passes=1, minimum_coverage=0.9,
        )
        self.store = HistoricalDataStore(config, Path(self.temp.name))
        self.path = Path(self.temp.name) / "cache.csv.gz"
        self.path.write_text("placeholder")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _touch(self, when: datetime) -> None:
        import os
        os.utime(self.path, (when.timestamp(), when.timestamp()))

    def test_missing_file_is_never_fresh(self) -> None:
        missing = Path(self.temp.name) / "does-not-exist.csv.gz"
        self.assertFalse(self.store._cache_is_fresh(missing, now=datetime(2026, 9, 1, 12, 0, tzinfo=IST)))

    def test_during_market_hours_uses_the_short_ttl(self) -> None:
        now = datetime(2026, 9, 1, 12, 0, tzinfo=IST)  # Tuesday, market open
        self._touch(now - timedelta(minutes=30))
        self.assertTrue(self.store._cache_is_fresh(self.path, now=now))
        self._touch(now - timedelta(minutes=90))
        self.assertFalse(self.store._cache_is_fresh(self.path, now=now))

    def test_after_close_requires_todays_close_or_later(self) -> None:
        now = datetime(2026, 9, 1, 20, 0, tzinfo=IST)  # Tuesday evening, hours after close
        self._touch(datetime(2026, 9, 1, 15, 30, tzinfo=IST))  # written right at today's close
        self.assertTrue(self.store._cache_is_fresh(self.path, now=now))
        self._touch(datetime(2026, 8, 31, 12, 0, tzinfo=IST))  # only from yesterday
        self.assertFalse(self.store._cache_is_fresh(self.path, now=now))

    def test_early_morning_before_open_accepts_yesterdays_close(self) -> None:
        now = datetime(2026, 9, 2, 5, 30, tzinfo=IST)  # Wednesday, before market open
        self._touch(datetime(2026, 9, 1, 15, 30, tzinfo=IST))  # Tuesday's close
        self.assertTrue(self.store._cache_is_fresh(self.path, now=now))
        self._touch(datetime(2026, 9, 1, 10, 0, tzinfo=IST))  # mid-Tuesday, before its close
        self.assertFalse(self.store._cache_is_fresh(self.path, now=now))

    def test_weekend_walks_back_to_fridays_close(self) -> None:
        now = datetime(2026, 9, 6, 10, 0, tzinfo=IST)  # Sunday
        self._touch(datetime(2026, 9, 4, 15, 30, tzinfo=IST))  # Friday's close
        self.assertTrue(self.store._cache_is_fresh(self.path, now=now))
        self._touch(datetime(2026, 9, 4, 10, 0, tzinfo=IST))  # mid-Friday, before its close
        self.assertFalse(self.store._cache_is_fresh(self.path, now=now))


if __name__ == "__main__":
    unittest.main()
