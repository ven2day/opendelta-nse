from __future__ import annotations

import unittest
from datetime import datetime, timedelta

import pandas as pd

from backtest_api import (
    BacktestRequest,
    IST,
    LiveSignalDecisionRequest,
    LiveSignalSettingsRequest,
    LiveUniverseRequest,
    LiveUniverseSaveRequest,
    PaperBuyRequest,
    PaperCloseRequest,
    app,
    _entry_signal,
    prepare_candles,
    run_backtest,
    simulate_symbol,
)
from main import DhanAPIError


class SignalTests(unittest.TestCase):
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
            }.issubset(paths)
        )

    def test_unavailable_batch_returns_symbol_errors_instead_of_failing_request(self) -> None:
        class UnavailableStore:
            @staticmethod
            def universe() -> list[str]:
                return ["A", "B"]

            @staticmethod
            def candles(*args, **kwargs):
                raise DhanAPIError("Historical data is temporarily unavailable")

        result = run_backtest(
            BacktestRequest(symbols=["A", "B"], timeframe="4h"),
            UnavailableStore(),
            datetime(2026, 8, 23, 15, 30, tzinfo=IST),
        )

        self.assertEqual(result["results"], [])
        self.assertEqual([item["symbol"] for item in result["errors"]], ["A", "B"])
        self.assertTrue(any("universe run continued" in item for item in result["warnings"]))


if __name__ == "__main__":
    unittest.main()
