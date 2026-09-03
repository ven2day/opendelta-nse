from __future__ import annotations

import inspect
import json
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import pandas as pd
from pydantic import ValidationError

import backend.compat.recovery_backtest as recovery_backtest
from backend.app import BacktestRequest, run_recovery_backtest
from backend.collector import IST
from backend.compat.recovery_backtest import (
    RecoveryConfig,
    _session_speed_bucket,
    _target_speed_bucket,
    aggregate_recovery_results,
    calculate_ema,
    calculate_recovery_indicators,
    calculate_session_vwap,
    calculate_wilder_rsi,
    rsi_recovery_crossovers,
    simulate_recovery_symbol,
    validate_candles,
)


def state_frame(
    rsi: list[float],
    *,
    highs: list[float] | None = None,
    lows: list[float] | None = None,
    closes: list[float] | None = None,
    opens: list[float] | None = None,
    index: pd.DatetimeIndex | None = None,
    ema_pass: list[bool] | None = None,
    vwap_pass: list[bool] | None = None,
    volume_pass: list[bool] | None = None,
) -> pd.DataFrame:
    count = len(rsi)
    closes = closes or [100.0] * count
    opens = opens or closes.copy()
    highs = highs or [max(open_price, close_price) + 0.2 for open_price, close_price in zip(opens, closes, strict=True)]
    lows = lows or [min(open_price, close_price) - 0.2 for open_price, close_price in zip(opens, closes, strict=True)]
    return pd.DataFrame(
        {
            "Open": opens,
            "High": highs,
            "Low": lows,
            "Close": closes,
            "Volume": [1_000.0] * count,
            "TestRSI": rsi,
            "TestEmaPass": ema_pass or [True] * count,
            "TestVwapPass": vwap_pass or [True] * count,
            "TestVolumePass": volume_pass or [True] * count,
        },
        index=index if index is not None else pd.date_range("2025-01-02 09:20", periods=count, freq="5min", tz=IST),
    )


def injected_indicators(candles: pd.DataFrame, _config: RecoveryConfig) -> pd.DataFrame:
    data = candles.copy()
    data["RecoveryRSI"] = data["TestRSI"]
    data["EMAFast"] = data["TestEmaPass"].map({True: 2.0, False: 0.0})
    data["EMASlow"] = 1.0
    data["SessionVWAP"] = data["Close"] + data["TestVwapPass"].map({True: -1.0, False: 1.0})
    data["VolumeEMA"] = data["Volume"] + data["TestVolumePass"].map({True: -1.0, False: 1.0})
    return data


def baseline_rsi(count: int = 30) -> list[float]:
    values = [50.0] * count
    values[5:9] = [35.0, 29.0, 39.0, 41.0]
    return values


def simulate(frame: pd.DataFrame, config: RecoveryConfig | None = None) -> dict:
    with patch("backend.compat.recovery_backtest.calculate_recovery_indicators", side_effect=injected_indicators):
        return simulate_recovery_symbol(
            "TEST",
            frame,
            timeframe="5m",
            config=config or RecoveryConfig(),
            run_id="deterministic-run",
        )


class IndicatorTests(unittest.TestCase):
    def test_ema_calculation_uses_causal_pine_recurrence(self) -> None:
        result = calculate_ema(pd.Series([1.0, 2.0, 3.0, 4.0]), 3)
        self.assertTrue(pd.isna(result.iloc[0]))
        self.assertTrue(pd.isna(result.iloc[1]))
        self.assertAlmostEqual(result.iloc[2], 2.25, places=10)
        self.assertAlmostEqual(result.iloc[3], 3.125, places=10)

    def test_wilder_rsi_matches_frozen_reference_value(self) -> None:
        close = pd.Series([100 + index * 0.4 for index in range(20)] + [107.6 - index * 1.2 for index in range(12)] + [94.4 + index for index in range(20)])
        result = calculate_wilder_rsi(close, 14)
        self.assertAlmostEqual(result.iloc[36], 44.482500299706004, places=10)

    def test_volume_ema_uses_same_ema_semantics(self) -> None:
        volume = pd.Series([100.0, 200.0, 300.0, 400.0])
        self.assertTrue(calculate_ema(volume, 3).equals(calculate_ema(volume.astype(float), 3)))
        self.assertAlmostEqual(calculate_ema(volume, 3).iloc[-1], 312.5, places=10)

    def test_session_vwap_resets_each_ist_trading_day(self) -> None:
        index = pd.DatetimeIndex([
            pd.Timestamp("2025-01-02 09:20", tz=IST),
            pd.Timestamp("2025-01-02 09:25", tz=IST),
            pd.Timestamp("2025-01-03 09:20", tz=IST),
        ])
        candles = pd.DataFrame({
            "High": [101.0, 103.0, 201.0],
            "Low": [99.0, 101.0, 199.0],
            "Close": [100.0, 102.0, 200.0],
            "Volume": [100.0, 300.0, 50.0],
        }, index=index)
        vwap = calculate_session_vwap(candles)
        self.assertAlmostEqual(vwap.iloc[0], 100.0)
        self.assertAlmostEqual(vwap.iloc[1], 101.5)
        self.assertAlmostEqual(vwap.iloc[2], 200.0)

    def test_rsi_crossover_is_strictly_current_above_previous_at_or_below(self) -> None:
        result = rsi_recovery_crossovers(pd.Series([39.0, 40.0, 40.01, 41.0, 39.0, 42.0]), 40.0)
        self.assertEqual(result.tolist(), [False, False, True, False, False, True])


class MandatoryRsiStateTests(unittest.TestCase):
    def test_setup_arms_when_rsi_enters_configured_zone(self) -> None:
        result = simulate(state_frame(baseline_rsi()))
        self.assertEqual(result["trades"][0]["rsiArmValue"], 35.0)
        self.assertEqual(result["trades"][0]["rsiArmTimestamp"], result["events"][0]["timestamp"])

    def test_ema_vwap_volume_cannot_buy_without_rsi_recovery(self) -> None:
        rsi = [50.0] * 30
        rsi[5:] = [35.0] * 25
        result = simulate(state_frame(rsi))
        self.assertEqual(result["buySignals"], 0)

    def test_rsi_recovery_without_prior_arm_cannot_buy(self) -> None:
        rsi = [50.0] * 30
        rsi[7:9] = [29.0, 41.0]
        result = simulate(state_frame(rsi))
        self.assertEqual(result["buySignals"], 0)

    def test_prior_arm_recovery_and_two_confirmations_can_buy(self) -> None:
        volume_pass = [False] * 30
        result = simulate(state_frame(baseline_rsi(), volume_pass=volume_pass))
        self.assertEqual(result["buySignals"], 1)
        self.assertEqual(result["trades"][0]["confirmationScore"], 2)

    def test_prior_arm_survives_rsi_falling_below_thirty(self) -> None:
        rsi = [50.0] * 30
        rsi[5:10] = [35.0, 25.0, 27.0, 39.0, 41.0]
        result = simulate(state_frame(rsi))
        self.assertEqual(result["buySignals"], 1)
        self.assertEqual(result["trades"][0]["rsiArmValue"], 35.0)

    def test_setup_expiry_cancels_stale_arm(self) -> None:
        rsi = baseline_rsi()
        result = simulate(state_frame(rsi), RecoveryConfig(setup_expiry_bars=2))
        self.assertEqual(result["buySignals"], 0)
        self.assertTrue(any(event["type"] == "SETUP_EXPIRED" for event in result["events"]))

    def test_zero_setup_expiry_never_expires(self) -> None:
        rsi = [50.0] * 35
        rsi[5] = 35.0
        rsi[6:20] = [25.0] * 14
        rsi[20:22] = [39.0, 41.0]
        result = simulate(state_frame(rsi), RecoveryConfig(setup_expiry_bars=0))
        self.assertEqual(result["buySignals"], 1)

    def test_recovery_with_insufficient_confirmations_does_not_buy(self) -> None:
        failed = [False] * 30
        result = simulate(state_frame(baseline_rsi(), ema_pass=failed, vwap_pass=failed, volume_pass=failed))
        self.assertEqual(result["buySignals"], 0)
        self.assertTrue(any(event["type"] == "RECOVERY_REJECTED" for event in result["events"]))


class NoLookaheadAndTargetTests(unittest.TestCase):
    def test_changing_future_candles_does_not_modify_historical_buy(self) -> None:
        frame = state_frame(baseline_rsi())
        changed = frame.copy()
        changed.loc[changed.index[12]:, ["Open", "High", "Low", "Close"]] *= 1.4
        first = simulate(frame)["trades"][0]
        second = simulate(changed)["trades"][0]
        self.assertEqual(first["signalTimestamp"], second["signalTimestamp"])
        self.assertEqual(first["entryTimestamp"], second["entryTimestamp"])
        self.assertEqual(first["entryPrice"], second["entryPrice"])

    def test_buy_candle_high_cannot_hit_target(self) -> None:
        highs = [100.2] * 30
        highs[8] = 150.0
        result = simulate(state_frame(baseline_rsi(), highs=highs))
        self.assertEqual(result["trades"][0]["status"], "OPEN")

    def test_following_candle_can_hit_target(self) -> None:
        highs = [100.2] * 30
        highs[9] = 100.6
        result = simulate(state_frame(baseline_rsi(), highs=highs))
        trade = result["trades"][0]
        self.assertEqual(trade["status"], "TARGET_HIT")
        self.assertEqual(trade["targetHitTimestamp"], frame_time(result, 9))

    def test_signal_close_uses_only_completed_signal_candle(self) -> None:
        result = simulate(state_frame(baseline_rsi()))
        trade = result["trades"][0]
        self.assertEqual(trade["signalTimestamp"], trade["entryTimestamp"])
        self.assertEqual(trade["entryPrice"], 100.0)

    def test_next_bar_open_records_separate_execution_model(self) -> None:
        opens = [100.0] * 30
        opens[9] = 101.0
        highs = [100.2] * 30
        highs[9] = 200.0
        result = simulate(
            state_frame(baseline_rsi(), opens=opens, highs=highs),
            RecoveryConfig(execution_model="NEXT_BAR_OPEN"),
        )
        trade = result["trades"][0]
        self.assertEqual(trade["executionModel"], "NEXT_BAR_OPEN")
        self.assertEqual(trade["entryPrice"], 101.0)
        self.assertEqual(trade["status"], "OPEN")

    def test_signal_generation_contains_no_negative_shift_or_centered_window(self) -> None:
        source = inspect.getsource(recovery_backtest.simulate_recovery_symbol) + inspect.getsource(recovery_backtest.rsi_recovery_crossovers)
        self.assertNotIn("shift(-", source)
        self.assertNotIn("center=True", source)


def frame_time(result: dict, relative_index: int) -> str:
    first = pd.Timestamp(result["firstCandle"])
    return (first + pd.Timedelta(minutes=5 * relative_index)).isoformat()


class PositionLifecycleTests(unittest.TestCase):
    @staticmethod
    def overlapping_frame(*, hit_later_signals: bool = True) -> pd.DataFrame:
        rsi = baseline_rsi(30)
        rsi[10:13] = [35.0, 39.0, 41.0]
        rsi[15:18] = [35.0, 39.0, 41.0]
        closes = [400.0] * 9 + [380.0] * 6 + [370.0] * 15
        highs = [value + 0.2 for value in closes]
        if hit_later_signals:
            highs[13] = 382.0
            highs[18] = 372.0
        lows = [value - 0.2 for value in closes]
        lows[9] = 350.0
        lows[13] = 378.0
        lows[18] = 368.0
        return state_frame(rsi, closes=closes, highs=highs, lows=lows)

    def test_fresh_cycles_create_overlapping_buys_and_unique_trade_ids(self) -> None:
        result = simulate(self.overlapping_frame())
        self.assertEqual(result["buySignals"], 3)
        self.assertEqual([trade["sequenceNumber"] for trade in result["trades"]], [1, 2, 3])
        self.assertEqual([trade["entryBarIndex"] for trade in result["trades"]], [8, 12, 17])
        self.assertEqual(len({trade["tradeId"] for trade in result["trades"]}), 3)
        self.assertEqual([trade["status"] for trade in result["trades"]], ["OPEN", "TARGET_HIT", "TARGET_HIT"])

    def test_second_target_completion_does_not_close_first_observation(self) -> None:
        result = simulate(self.overlapping_frame())
        first, second, third = result["trades"]
        self.assertEqual(first["status"], "OPEN")
        self.assertEqual(second["status"], "TARGET_HIT")
        self.assertEqual(third["status"], "TARGET_HIT")
        self.assertEqual(second["targetHitTimestamp"], frame_time(result, 13))
        self.assertEqual(third["targetHitTimestamp"], frame_time(result, 18))

    def test_every_buy_requires_a_fresh_arm_and_one_recovery_creates_one_buy(self) -> None:
        rsi = baseline_rsi(30)
        rsi[9:15] = [42.0, 43.0, 39.0, 41.0, 42.0, 43.0]
        result = simulate(state_frame(rsi))
        self.assertEqual(result["buySignals"], 2)
        self.assertEqual([trade["signalTimestamp"] for trade in result["trades"]], [frame_time(result, 8), frame_time(result, 12)])

    def test_overlapping_mae_and_mfe_are_isolated(self) -> None:
        result = simulate(self.overlapping_frame())
        first, second, third = result["trades"]
        self.assertAlmostEqual(first["maxAdversePct"], -12.5)
        self.assertAlmostEqual(second["maxAdversePct"], (378.0 / 380.0 - 1.0) * 100.0, places=5)
        self.assertAlmostEqual(second["maxFavorablePct"], (382.0 / 380.0 - 1.0) * 100.0, places=5)
        self.assertAlmostEqual(third["maxAdversePct"], (368.0 / 370.0 - 1.0) * 100.0, places=5)

    def test_multiple_open_observations_survive_dataset_end(self) -> None:
        result = simulate(self.overlapping_frame(hit_later_signals=False))
        self.assertEqual(result["buySignals"], 3)
        self.assertEqual(result["openSignals"], 3)
        self.assertEqual([trade["status"] for trade in result["trades"]], ["OPEN", "OPEN", "OPEN"])
        self.assertEqual(result["maximumConcurrentOpenSignals"], 3)

    def test_entry_candle_cannot_hit_its_own_overlapping_observation(self) -> None:
        rsi = baseline_rsi(30)
        rsi[10:13] = [35.0, 39.0, 41.0]
        highs = [100.2] * 30
        highs[12] = 100.6
        result = simulate(state_frame(rsi, highs=highs))
        first, second = result["trades"]
        self.assertEqual(first["status"], "TARGET_HIT")
        self.assertEqual(second["status"], "OPEN")

    def test_next_bar_open_supports_overlapping_observations(self) -> None:
        rsi = baseline_rsi(30)
        rsi[10:13] = [35.0, 39.0, 41.0]
        closes = [400.0] * 9 + [390.0] * 4 + [380.0] * 17
        opens = closes.copy()
        opens[9] = 400.0
        opens[13] = 380.0
        highs = [max(open_price, close_price) + 0.2 for open_price, close_price in zip(opens, closes, strict=True)]
        highs[14] = 382.0
        result = simulate(
            state_frame(rsi, closes=closes, opens=opens, highs=highs),
            RecoveryConfig(execution_model="NEXT_BAR_OPEN"),
        )
        self.assertEqual(result["buySignals"], 2)
        self.assertEqual(result["trades"][0]["status"], "OPEN")
        self.assertEqual(result["trades"][1]["status"], "TARGET_HIT")

    def test_no_end_of_day_close_and_overnight_position_remains_active(self) -> None:
        day_one = pd.date_range("2025-01-02 14:15", periods=15, freq="5min", tz=IST)
        day_two = pd.date_range("2025-01-03 09:20", periods=15, freq="5min", tz=IST)
        index = day_one.append(day_two)
        highs = [100.2] * 30
        highs[20] = 100.6
        result = simulate(state_frame(baseline_rsi(), highs=highs, index=index))
        trade = result["trades"][0]
        self.assertEqual(trade["status"], "TARGET_HIT")
        self.assertEqual(trade["sessionDistance"], 1)
        self.assertGreater(trade["durationHours"], 17)

    def test_no_stop_loss_closes_deeply_underwater_position(self) -> None:
        highs = [100.2] * 30
        lows = [99.8] * 30
        lows[9] = 50.0
        highs[10] = 100.6
        result = simulate(state_frame(baseline_rsi(), highs=highs, lows=lows))
        trade = result["trades"][0]
        self.assertEqual(trade["status"], "TARGET_HIT")
        self.assertAlmostEqual(trade["maxAdversePct"], -50.0)

    def test_unresolved_position_remains_open_at_dataset_end(self) -> None:
        result = simulate(state_frame(baseline_rsi()))
        trade = result["trades"][0]
        self.assertEqual(trade["status"], "OPEN")
        self.assertIsNone(trade["targetHitTimestamp"])
        self.assertIsNotNone(trade["currentPnlPct"])


class ExcursionAndMetricsTests(unittest.TestCase):
    def test_entry_candle_low_is_excluded_from_signal_close_mae(self) -> None:
        lows = [99.8] * 30
        lows[8] = 1.0
        result = simulate(state_frame(baseline_rsi(), lows=lows))
        self.assertGreater(result["trades"][0]["maxAdversePct"], -1.0)

    def test_next_candle_low_is_included_and_worst_mae_is_preserved(self) -> None:
        lows = [99.8] * 30
        lows[9] = 98.0
        lows[10] = 95.0
        lows[11] = 97.0
        result = simulate(state_frame(baseline_rsi(), lows=lows))
        self.assertAlmostEqual(result["trades"][0]["maxAdversePct"], -5.0)

    def test_mfe_preserves_highest_future_high(self) -> None:
        highs = [100.2] * 30
        highs[9:12] = [100.3, 100.4, 100.45]
        result = simulate(state_frame(baseline_rsi(), highs=highs))
        self.assertAlmostEqual(result["trades"][0]["maxFavorablePct"], 0.45)

    def test_open_position_mae_is_in_symbol_and_universe_metrics(self) -> None:
        lows = [99.8] * 30
        lows[9] = 92.0
        result = simulate(state_frame(baseline_rsi(), lows=lows))
        summary = aggregate_recovery_results([result])
        self.assertAlmostEqual(summary["worstOpenMaePct"], -8.0)
        self.assertEqual(summary["stillOpen"], 1)

    def test_symbol_and_universe_metrics_count_all_overlapping_observations(self) -> None:
        result = simulate(PositionLifecycleTests.overlapping_frame())
        self.assertEqual(result["totalBuySignals"], 3)
        self.assertEqual(result["targetsHit"], 2)
        self.assertEqual(result["openSignals"], 1)
        self.assertAlmostEqual(result["targetHitRate"], 66.67)
        self.assertEqual(result["maximumConcurrentOpenSignals"], 2)
        self.assertGreater(result["averageConcurrentOpenSignals"], 1.0)
        self.assertEqual(result["maximumSignalsOpenSameDay"], 3)

        summary = aggregate_recovery_results([result])
        self.assertEqual(summary["totalBuySignals"], 3)
        self.assertEqual(summary["totalTargetsHit"], 2)
        self.assertEqual(summary["totalOpenSignals"], 1)
        self.assertAlmostEqual(summary["targetHitRate"], 66.67)
        self.assertEqual(summary["maximumConcurrentSignalsUniverse"], 2)
        self.assertEqual(summary["maximumConcurrentSignalsSameSymbol"], 2)
        self.assertEqual(summary["symbolsWithOpenSignals"], 1)
        self.assertEqual(summary["symbolsWith2PlusOpenSignals"], 0)

    def test_target_speed_buckets_are_exclusive_at_boundaries(self) -> None:
        self.assertEqual(_target_speed_bucket(30), "LE_30_MIN")
        self.assertEqual(_target_speed_bucket(30.01), "GT_30_MIN_LE_2_HOURS")
        self.assertEqual(_target_speed_bucket(120), "GT_30_MIN_LE_2_HOURS")
        self.assertEqual(_target_speed_bucket(120.01), "GT_2_HOURS_LE_24_HOURS")
        self.assertEqual(_target_speed_bucket(1_440), "GT_2_HOURS_LE_24_HOURS")
        self.assertEqual(_target_speed_bucket(1_440.01), "GT_24_HOURS")

    def test_session_speed_buckets_are_exclusive(self) -> None:
        self.assertEqual(_session_speed_bucket(0), "SAME_SESSION")
        self.assertEqual(_session_speed_bucket(1), "NEXT_SESSION")
        self.assertEqual(_session_speed_bucket(2), "TWO_TO_FIVE_TRADING_DAYS")
        self.assertEqual(_session_speed_bucket(5), "TWO_TO_FIVE_TRADING_DAYS")
        self.assertEqual(_session_speed_bucket(6), "GT_FIVE_TRADING_DAYS")

    def test_configurable_cost_model_keeps_gross_and_net_separate(self) -> None:
        result = simulate(
            state_frame(baseline_rsi()),
            RecoveryConfig(buy_cost_bps=10, sell_cost_bps=10, slippage_bps=5),
        )
        trade = result["trades"][0]
        self.assertAlmostEqual(trade["estimatedCostPct"], 0.3)
        self.assertAlmostEqual(trade["netReturnPct"], trade["grossReturnPct"] - 0.3)


class ValidationAndParityTests(unittest.TestCase):
    def test_invalid_confirmation_count_is_rejected_by_request_model(self) -> None:
        with self.assertRaisesRegex(ValidationError, "Minimum confirmations"):
            BacktestRequest(
                symbols=["LUPIN"],
                strategyMode="rsi_recovery",
                emaEnabled=False,
                vwapEnabled=False,
                volumeEnabled=True,
                minimumConfirmations=2,
            )

    def test_invalid_ohlcv_is_reported_not_silently_processed(self) -> None:
        frame = state_frame(baseline_rsi())
        frame.iloc[3, frame.columns.get_loc("High")] = 1.0
        self.assertIn("high is below", "; ".join(validate_candles(frame)))
        with self.assertRaisesRegex(ValueError, "Data quality validation failed"):
            simulate(frame)

    def test_pine_compatible_fixture_indicator_signal_and_trade_parity(self) -> None:
        expected = json.loads((Path(__file__).parent / "fixtures" / "pine_recovery_expected.json").read_text())
        closes = (
            [100 + index * 0.4 for index in range(20)]
            + [107.6 - index * 1.2 for index in range(12)]
            + [94.4, 95.4, 96.4, 97.4, 98.4]
            + [97.2 - index * 1.2 for index in range(12)]
            + [84.0 + index for index in range(7)]
        )
        index = pd.date_range("2025-01-02 09:20", periods=32, freq="5min", tz=IST).append(
            pd.date_range("2025-01-03 09:20", periods=17, freq="5min", tz=IST)
        ).append(pd.date_range("2025-01-06 09:20", periods=7, freq="5min", tz=IST))
        volume = [1_000 + (position % 5) * 100 for position in range(len(closes))]
        volume[36] = 3_000
        volume[54] = 3_000
        candles = pd.DataFrame({
            "Open": closes,
            "High": [value + 0.2 for value in closes],
            "Low": [value - 0.8 for value in closes],
            "Close": closes,
            "Volume": volume,
        }, index=index)
        indicators = calculate_recovery_indicators(candles, RecoveryConfig())
        for checkpoint in expected["indicator_checkpoints"]:
            entry = indicators.iloc[checkpoint["index"]]
            self.assertAlmostEqual(entry["EMAFast"], checkpoint["ema_fast"], places=6)
            self.assertAlmostEqual(entry["EMASlow"], checkpoint["ema_slow"], places=6)
            self.assertAlmostEqual(entry["RecoveryRSI"], checkpoint["rsi"], places=6)
            self.assertAlmostEqual(entry["SessionVWAP"], checkpoint["session_vwap"], places=6)
            self.assertAlmostEqual(entry["VolumeEMA"], checkpoint["volume_ema"], places=6)
        result = simulate_recovery_symbol("PINE", candles, timeframe="5m", config=RecoveryConfig(), run_id="pine-fixture")
        self.assertEqual(len(result["trades"]), 2)
        for trade, expected_trade in zip(result["trades"], expected["trades"], strict=True):
            self.assertEqual(trade["sequenceNumber"], expected_trade["sequence_number"])
            self.assertEqual(trade["rsiArmTimestamp"], expected_trade["rsi_arm_timestamp"])
            self.assertAlmostEqual(trade["rsiArmValue"], expected_trade["rsi_arm_value"], places=6)
            self.assertEqual(trade["signalTimestamp"], expected_trade["recovery_timestamp"])
            self.assertEqual(trade["entryTimestamp"], expected_trade["buy_timestamp"])
            self.assertAlmostEqual(trade["entryPrice"], expected_trade["entry_price"], places=6)
            self.assertAlmostEqual(trade["targetPrice"], expected_trade["target_price"], places=6)
            self.assertEqual(trade["targetHitTimestamp"], expected_trade["target_hit_timestamp"])
            self.assertEqual(trade["status"], expected_trade["status"])
            self.assertAlmostEqual(trade["maxAdversePct"], expected_trade["mae_pct"], places=6)
            self.assertAlmostEqual(trade["maxFavorablePct"], expected_trade["mfe_pct"], places=6)

    def test_run_metadata_reports_mode_counts_runtime_and_adjustment_warning(self) -> None:
        class Store:
            @staticmethod
            def universe() -> list[str]:
                return ["TEST"]

            @staticmethod
            def candles(*_args, **_kwargs) -> pd.DataFrame:
                return state_frame(baseline_rsi())

        with patch("backend.compat.recovery_backtest.calculate_recovery_indicators", side_effect=injected_indicators):
            response = run_recovery_backtest(
                BacktestRequest(symbols=["TEST"], strategyMode="rsi_recovery", timeframe="5m", runId="metadata-run"),
                Store(),
                datetime(2025, 2, 1, 15, 30, tzinfo=IST),
            )
        self.assertEqual(response["metadata"]["strategyMode"], "rsi_recovery")
        self.assertEqual(response["metadata"]["symbolsProcessed"], 1)
        self.assertEqual(response["metadata"]["symbolsFailed"], 0)
        self.assertEqual(response["metadata"]["corporateActionAdjustment"], "UNVERIFIED_SOURCE_AS_RECEIVED")
        self.assertGreaterEqual(response["metadata"]["runtimeSeconds"], 0)


if __name__ == "__main__":
    unittest.main()
