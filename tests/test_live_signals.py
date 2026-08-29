from __future__ import annotations

import inspect
import struct
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import pandas as pd

import live_signals
from live_signals import (
    DhanQuoteTick,
    FiveMinuteCandleBuilder,
    LiveSignalEngine,
    LiveSignalRepository,
    LiveSignalSettings,
    buy_range_status,
    calculate_buy_range,
    calculate_support_resistance,
    deterministic_signal_id,
    evaluate_latest_recovery,
    parse_dhan_quote_packets,
    quantity_suggestion,
)
from main import IST
from recovery_backtest import RecoveryConfig


def fixed_clock(value: str = "2026-08-26 10:05:10"):
    stamp = datetime.fromisoformat(value).replace(tzinfo=IST)
    return lambda: stamp


def state_frame(rsi: list[float], *, confirmations: tuple[bool, bool, bool] = (True, True, True)) -> pd.DataFrame:
    count = len(rsi)
    index = pd.date_range("2026-08-26 09:20", periods=count, freq="5min", tz=IST)
    return pd.DataFrame(
        {
            "Open": [100.0] * count,
            "High": [100.2] * count,
            "Low": [99.8] * count,
            "Close": [100.0] * count,
            "Volume": [1_000.0] * count,
            "TestRSI": rsi,
            "TestEma": [confirmations[0]] * count,
            "TestVwap": [confirmations[1]] * count,
            "TestVolume": [confirmations[2]] * count,
        },
        index=index,
    )


def injected_indicators(candles: pd.DataFrame, _config: RecoveryConfig) -> pd.DataFrame:
    data = candles.copy()
    data["RecoveryRSI"] = data["TestRSI"]
    data["EMAFast"] = data["TestEma"].map({True: 2.0, False: 0.0})
    data["EMASlow"] = 1.0
    data["SessionVWAP"] = data["Close"] + data["TestVwap"].map({True: -1.0, False: 1.0})
    data["VolumeEMA"] = data["Volume"] + data["TestVolume"].map({True: -1.0, False: 1.0})
    return data


def signal_record(signal_id: str = "SIG-TEST", symbol: str = "TEST") -> dict:
    return {
        "signalId": signal_id,
        "symbol": symbol,
        "signalTimestamp": "2026-08-26T10:05:00+05:30",
        "signalClose": 100.0,
        "systemTargetPrice": 100.5,
        "buyRange": {"low": 99.85, "midpoint": 99.975, "high": 100.1},
        "manualAction": "NO_ACTION",
        "hypotheticalOutcome": {
            "status": "OPEN",
            "targetHitTimestamp": None,
            "durationMinutes": None,
            "lowestPrice": None,
            "highestPrice": None,
            "maePct": None,
            "mfePct": None,
            "barsHeld": 0,
            "lastTimestamp": "2026-08-26T10:05:00+05:30",
            "lastClose": 100.0,
        },
    }


class RangeAndQuantityTests(unittest.TestCase):
    def test_fixed_percent_range(self) -> None:
        result = calculate_buy_range(1_000, 4.0, LiveSignalSettings())
        self.assertEqual(result["low"], 998.5)
        self.assertEqual(result["high"], 1_001.0)
        self.assertEqual(result["methodLabel"], "Fixed % heuristic")

    def test_atr_range(self) -> None:
        settings = LiveSignalSettings(entry_range_method="ATR_BASED")
        result = calculate_buy_range(1_000, 4.0, settings)
        self.assertEqual(result["low"], 999.0)
        self.assertEqual(result["high"], 1_000.6)

    def test_configurable_tolerances(self) -> None:
        result = calculate_buy_range(1_000, None, LiveSignalSettings(fixed_lower_pct=0.3, fixed_upper_pct=0.2))
        self.assertEqual((result["low"], result["high"]), (997.0, 1_002.0))

    def test_range_statuses(self) -> None:
        self.assertEqual(buy_range_status(100, 99, 101), "IN_RANGE")
        self.assertEqual(buy_range_status(101.01, 99, 101), "ABOVE_RANGE")
        self.assertEqual(buy_range_status(98.99, 99, 101), "BELOW_RANGE")

    def test_safe_quantity_is_integer_and_never_exceeds_allocation(self) -> None:
        result = quantity_suggestion(25_000, 998.5, 999.75, 1_001.0)
        self.assertEqual(result["recommendedQuantity"], 24)
        self.assertIsInstance(result["recommendedQuantity"], int)
        self.assertLessEqual(result["recommendedQuantity"] * 1_001.0, 25_000)
        self.assertTrue(result["noLeverage"])

    def test_allocation_change_recalculates_quantity(self) -> None:
        low = quantity_suggestion(10_000, 999, 1_000, 1_001)["recommendedQuantity"]
        high = quantity_suggestion(50_000, 999, 1_000, 1_001)["recommendedQuantity"]
        self.assertGreater(high, low)


class CandleAndPacketTests(unittest.TestCase):
    def test_quote_packet_parser(self) -> None:
        packet = bytearray(50)
        struct.pack_into("<BHBIfHIfIIIffff", packet, 0, 4, 50, 1, 1333, 123.5, 10, 1_777_000_000, 123.4, 45_000, 0, 0, 120.0, 0.0, 125.0, 119.0)
        ticks = parse_dhan_quote_packets(bytes(packet))
        self.assertEqual(len(ticks), 1)
        self.assertEqual(ticks[0].security_id, "1333")
        self.assertAlmostEqual(ticks[0].price, 123.5)
        self.assertEqual(ticks[0].cumulative_volume, 45_000)

    def test_incomplete_candle_is_marked_unconfirmed(self) -> None:
        completed: list[dict] = []
        builder = FiveMinuteCandleBuilder(lambda _symbol, candle: completed.append(candle))
        builder.connection_started(datetime(2026, 8, 26, 10, 2, tzinfo=IST))
        builder.add_tick("TEST", DhanQuoteTick(1, "1", 100, 1_000, datetime(2026, 8, 26, 10, 2, tzinfo=IST)))
        builder.flush_due(datetime(2026, 8, 26, 10, 5, 1, tzinfo=IST))
        self.assertFalse(completed[0]["complete"])

    def test_full_completed_candle_is_confirmed_once(self) -> None:
        completed: list[dict] = []
        builder = FiveMinuteCandleBuilder(lambda _symbol, candle: completed.append(candle))
        builder.connection_started(datetime(2026, 8, 26, 9, 59, tzinfo=IST))
        builder.add_tick("TEST", DhanQuoteTick(1, "1", 100, 1_000, datetime(2026, 8, 26, 10, 0, tzinfo=IST)))
        builder.add_tick("TEST", DhanQuoteTick(1, "1", 101, 1_050, datetime(2026, 8, 26, 10, 4, tzinfo=IST)))
        builder.flush_due(datetime(2026, 8, 26, 10, 5, 1, tzinfo=IST))
        builder.flush_due(datetime(2026, 8, 26, 10, 5, 2, tzinfo=IST))
        self.assertEqual(len(completed), 1)
        self.assertTrue(completed[0]["complete"])
        self.assertEqual((completed[0]["Open"], completed[0]["High"], completed[0]["Close"]), (100, 101, 101))


class RecoveryStateTests(unittest.TestCase):
    def evaluate(self, rsi: list[float], confirmations=(True, True, True), config: RecoveryConfig | None = None):
        with patch("live_signals.calculate_recovery_indicators", side_effect=injected_indicators):
            return evaluate_latest_recovery(state_frame(rsi, confirmations=confirmations), config or RecoveryConfig())

    def test_rsi_must_arm_before_recovery(self) -> None:
        rsi = [50.0] * 30
        rsi[-2:] = [29.0, 41.0]
        self.assertIsNone(self.evaluate(rsi))

    def test_recovery_requires_strict_crossover(self) -> None:
        rsi = [50.0] * 30
        rsi[-4:] = [35.0, 39.0, 40.0, 40.0]
        self.assertIsNone(self.evaluate(rsi))

    def test_two_of_three_confirmations_pass(self) -> None:
        rsi = [50.0] * 30
        rsi[-4:] = [35.0, 29.0, 39.0, 41.0]
        result = self.evaluate(rsi, (True, True, False))
        self.assertIsNotNone(result)
        self.assertEqual(result["confirmationScore"], 2)
        self.assertEqual(result["rsiMinimumSinceArm"], 29.0)

    def test_one_of_three_confirmations_fails(self) -> None:
        rsi = [50.0] * 30
        rsi[-3:] = [35.0, 39.0, 41.0]
        self.assertIsNone(self.evaluate(rsi, (True, False, False)))

    def test_one_recovery_creates_only_one_latest_event(self) -> None:
        rsi = [50.0] * 30
        rsi[-5:] = [35.0, 39.0, 41.0, 42.0, 43.0]
        self.assertIsNone(self.evaluate(rsi))
        self.assertIsNotNone(self.evaluate(rsi[:-2]))

    def test_new_arm_recovery_cycle_can_create_later_signal(self) -> None:
        rsi = [50.0] * 30
        rsi[20:23] = [35.0, 39.0, 41.0]
        rsi[-3:] = [34.0, 39.0, 41.0]
        result = self.evaluate(rsi)
        self.assertIsNotNone(result)
        self.assertEqual(result["rsiArmValue"], 34.0)


class SupportResistanceTests(unittest.TestCase):
    def candles(self) -> pd.DataFrame:
        index = pd.date_range("2026-08-25 14:30", periods=12, freq="5min", tz=IST).append(
            pd.date_range("2026-08-26 09:20", periods=20, freq="5min", tz=IST)
        )
        close = [98.0] * 12 + [100.0] * 20
        return pd.DataFrame({"Open": close, "High": [value + 1 for value in close], "Low": [value - 1 for value in close], "Close": close, "Volume": [1000] * 32}, index=index)

    def test_levels_are_causal_and_previous_session_is_correct(self) -> None:
        frame = self.candles()
        result = calculate_support_resistance(frame, 100.0, 5, 10)
        self.assertEqual(result["previousSessionHigh"], 99.0)
        self.assertEqual(result["previousSessionLow"], 97.0)
        changed_future = pd.concat([frame, pd.DataFrame({"Open": [500], "High": [600], "Low": [1], "Close": [500], "Volume": [1]}, index=pd.DatetimeIndex([pd.Timestamp("2026-08-26 12:00", tz=IST)]))])
        self.assertEqual(result, calculate_support_resistance(changed_future.iloc[:-1], 100.0, 5, 10))

    def test_resistance_before_target_flag(self) -> None:
        frame = self.candles()
        frame.loc[frame.index[-5]:, "High"] = 100.3
        result = calculate_support_resistance(frame, 100.0, 5, 10)
        self.assertTrue(result["resistanceBeforeTarget"])
        self.assertEqual(result["targetRoom"], "TIGHT")


class RepositoryAndPaperTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.repo = LiveSignalRepository(self.root, clock=fixed_clock())
        self.repo.add_signal(signal_record())

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_restart_deduplicates_signal(self) -> None:
        reloaded = LiveSignalRepository(self.root, clock=fixed_clock())
        _record, created = reloaded.add_signal(signal_record())
        self.assertFalse(created)
        self.assertEqual(len(reloaded.signals()), 1)

    def test_watch_can_become_paper_buy(self) -> None:
        self.repo.decide("SIG-TEST", "WATCH")
        trade = self.repo.create_paper_trade("SIG-TEST", 99.9, 25)
        self.assertEqual(trade["status"], "OPEN")
        self.assertEqual(self.repo.signal("SIG-TEST")["manualAction"], "PAPER_BUY")

    def test_actual_entry_determines_paper_target_and_no_broker_execution(self) -> None:
        trade = self.repo.create_paper_trade("SIG-TEST", 99.9, 25)
        self.assertAlmostEqual(trade["targetPrice"], 100.3995)
        self.assertFalse(trade["brokerExecution"])
        self.assertNotIn("place_order", inspect.getsource(LiveSignalRepository.create_paper_trade))

    def test_legacy_oi_fields_never_block_rsi_recovery_paper_trade(self) -> None:
        isolated = signal_record("SIG-OI-ISOLATED")
        isolated.update({
            "oiFilterMode": "ENFORCED",
            "executionEligible": False,
            "oiDecision": "SKIPPED_STRONGLY_BEARISH_OI",
        })
        self.repo.add_signal(isolated)
        trade = self.repo.create_paper_trade("SIG-OI-ISOLATED", 100, 10)
        self.assertEqual(trade["status"], "OPEN")

    def test_paper_target_hit_and_manual_close(self) -> None:
        first = self.repo.create_paper_trade("SIG-TEST", 100, 10)
        self.repo.process_completed_candle("TEST", {"timestamp": "2026-08-26T10:10:00+05:30", "Low": 99, "High": 100.6, "Close": 100.5})
        hit = next(item for item in self.repo.paper_trades() if item["paperTradeId"] == first["paperTradeId"])
        self.assertEqual(hit["status"], "TARGET_HIT")
        self.repo.add_signal(signal_record("SIG-TWO"))
        second = self.repo.create_paper_trade("SIG-TWO", 100, 10)
        closed = self.repo.close_paper_trade(second["paperTradeId"], 99)
        self.assertEqual(closed["status"], "MANUALLY_CLOSED")

    def test_ignore_keeps_and_tracks_hypothetical_signal(self) -> None:
        self.repo.decide("SIG-TEST", "IGNORE", reason="Near resistance")
        self.repo.process_completed_candle("TEST", {"timestamp": "2026-08-26T10:10:00+05:30", "Low": 99, "High": 100.6, "Close": 100.5})
        signal = self.repo.signal("SIG-TEST")
        self.assertEqual(signal["manualAction"], "IGNORE")
        self.assertEqual(signal["hypotheticalOutcome"]["status"], "TARGET_HIT")

    def test_manual_decisions_survive_restart(self) -> None:
        self.repo.decide("SIG-TEST", "WATCH")
        reloaded = LiveSignalRepository(self.root, clock=fixed_clock())
        self.assertEqual(reloaded.signal("SIG-TEST")["manualAction"], "WATCH")


class EngineSafetyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.repo = LiveSignalRepository(Path(self.temp.name).resolve(), clock=fixed_clock())

        class Store:
            config = object()
            client = object()
            cache_directory = Path(self.temp.name)

            @staticmethod
            def security_id(symbol: str) -> str:
                return str(abs(hash(symbol)) % 1_000_000)

            @staticmethod
            def _cache_path(_symbol: str, _interval: str, _years: int) -> Path:
                return Path("missing")

            @staticmethod
            def candles(*_args, **_kwargs) -> pd.DataFrame:
                return pd.DataFrame(columns=["Open", "High", "Low", "Close", "Volume"])

        selected = [{"symbol": f"SYM{index}", "rank": index + 1, "qualityScore": 80} for index in range(300)]

        class Universe:
            @staticmethod
            def get_active_live_universe():
                return [row["symbol"] for row in selected], {"universeVersion": "LIVE-20260826-001", "frozen": True, "selected": selected}

        self.engine = LiveSignalEngine(self.repo, Store(), Universe(), clock=fixed_clock())

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_active_frozen_universe_is_loaded_without_mutation(self) -> None:
        self.engine._initialize()
        self.assertEqual(len(self.engine._symbols), 300)
        self.assertEqual(self.engine.status()["universeVersion"], "LIVE-20260826-001")
        self.assertTrue(self.engine.status()["universeFrozen"])

    def test_incomplete_candle_cannot_create_signal(self) -> None:
        self.assertIsNone(self.engine.process_completed_candle("SYM0", {"complete": False, "timestamp": "2026-08-26T10:05:00+05:30"}))

    def test_stale_completed_candle_is_suppressed(self) -> None:
        candle = {"complete": True, "timestamp": "2026-08-26T09:20:00+05:30", "Open": 100, "High": 101, "Low": 99, "Close": 100, "Volume": 1000}
        self.assertIsNone(self.engine.process_completed_candle("SYM0", candle))
        self.assertEqual(self.engine.status()["engineStatus"], "STALE_DATA")

    def test_disconnect_reconnect_restores_subscriptions_without_changing_universe(self) -> None:
        self.engine._initialize()
        self.engine.on_disconnected(reconnecting=True)
        self.assertEqual(self.engine.status()["connectionStatus"], "RECONNECTING")
        self.engine.on_connected()
        status = self.engine.status()
        self.assertEqual(status["connectionStatus"], "CONNECTED")
        self.assertEqual(status["universeVersion"], "LIVE-20260826-001")
        self.assertEqual(status["monitoredSymbols"], 300)

    def test_deterministic_identity_changes_only_with_cycle_candle(self) -> None:
        first = deterministic_signal_id("RELIANCE", "2026-08-26T10:05:00+05:30")
        self.assertEqual(first, deterministic_signal_id("RELIANCE", "2026-08-26T10:05:00+05:30"))
        self.assertNotEqual(first, deterministic_signal_id("RELIANCE", "2026-08-26T10:10:00+05:30"))

    def test_no_live_order_api_exists(self) -> None:
        source = inspect.getsource(live_signals)
        self.assertNotIn("place_order(", source)
        self.assertTrue(live_signals.NO_ORDER_EXECUTION)


if __name__ == "__main__":
    unittest.main()
