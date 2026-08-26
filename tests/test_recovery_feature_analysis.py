from __future__ import annotations

import inspect
import tempfile
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

import recovery_feature_analysis as analysis
from main import IST
from recovery_backtest import RecoveryConfig, calculate_recovery_indicators


def fixture_candles() -> pd.DataFrame:
    first = pd.date_range("2026-08-21 09:20", periods=40, freq="5min", tz=IST)
    second = pd.date_range("2026-08-24 09:20", periods=40, freq="5min", tz=IST)
    index = first.append(second)
    path = np.concatenate(
        [
            np.linspace(100.0, 103.0, 40),
            np.linspace(101.5, 99.0, 22),
            np.linspace(99.0, 103.0, 18),
        ]
    )
    open_values = path - np.where(np.arange(len(path)) % 2 == 0, 0.12, -0.06)
    high = np.maximum(open_values, path) + 0.32
    low = np.minimum(open_values, path) - 0.28
    volume = 1000.0 + (np.arange(len(path)) % 11) * 73.0
    return pd.DataFrame(
        {"Open": open_values, "High": high, "Low": low, "Close": path, "Volume": volume},
        index=index,
    )


def fixture_trade(candles: pd.DataFrame, *, duration: float = 30, status: str = "TARGET_HIT") -> dict:
    arm_index = 67
    signal_index = 72
    entry = float(candles.iloc[signal_index]["Close"])
    return {
        "tradeId": "fixture:TEST:1",
        "runId": "fixture",
        "symbol": "TEST",
        "timeframe": "5m",
        "signalTimestamp": candles.index[signal_index].isoformat(),
        "entryTimestamp": candles.index[signal_index].isoformat(),
        "executionModel": "SIGNAL_CLOSE",
        "rsiArmTimestamp": candles.index[arm_index].isoformat(),
        "rsiArmValue": 36.0,
        "confirmationScore": 3,
        "emaConfirmation": True,
        "vwapConfirmation": True,
        "volumeConfirmation": True,
        "status": status,
        "targetHitTimestamp": candles.index[min(signal_index + 6, len(candles) - 1)].isoformat()
        if status == "TARGET_HIT"
        else None,
        "durationMinutes": duration,
        "durationHours": duration / 60.0,
        "durationDays": duration / 1440.0,
        "maxAdversePct": -0.8,
        "maxFavorablePct": 0.7,
        "barsHeld": max(1, int(duration / 5)),
        "tradingSessionsHeld": 1,
        "currentPnlPct": -4.0 if status == "OPEN" else 0.5,
        "entryPrice": entry,
        "targetPrice": entry * 1.005,
    }


class OutcomeClassificationTests(unittest.TestCase):
    def test_exact_speed_boundaries(self) -> None:
        cases = [
            (30, True, "FAST_30M", "GOOD"),
            (31, True, "FAST_2H", "GOOD"),
            (120, True, "FAST_2H", "GOOD"),
            (121, True, "SAME_DAY", "NEUTRAL"),
            (1440, True, "SAME_DAY", "NEUTRAL"),
            (1441, True, "SLOW", "BAD"),
            (100, False, "TRAPPED", "BAD"),
        ]
        for minutes, hit, speed, binary in cases:
            with self.subTest(minutes=minutes, hit=hit):
                self.assertEqual(analysis.classify_outcome(minutes, hit), speed)
                self.assertEqual(analysis.classify_binary_quality(minutes, hit), binary)


class CausalFeatureSnapshotTests(unittest.TestCase):
    def setUp(self) -> None:
        self.candles = fixture_candles()
        self.config = RecoveryConfig()
        self.trade = fixture_trade(self.candles)

    def snapshot(self, candles: pd.DataFrame | None = None) -> pd.Series:
        frame = analysis.build_signal_feature_snapshots(
            symbol="TEST",
            timeframe="5m",
            candles=self.candles if candles is None else candles,
            trades=[self.trade],
            config=self.config,
            nifty_candles=self.candles,
        )
        return frame.iloc[0]

    def test_future_candles_cannot_change_entry_features(self) -> None:
        original = self.snapshot()
        changed = self.candles.copy()
        signal = changed.index.get_loc(pd.Timestamp(self.trade["signalTimestamp"]))
        changed.iloc[signal + 1 :, changed.columns.get_loc("Close")] *= 4
        changed.iloc[signal + 1 :, changed.columns.get_loc("High")] *= 4
        changed.iloc[signal + 1 :, changed.columns.get_loc("Low")] *= 3
        changed.iloc[signal + 1 :, changed.columns.get_loc("Volume")] *= 20
        modified = self.snapshot(changed)
        pd.testing.assert_series_equal(
            original[list(analysis.ENTRY_FEATURE_COLUMNS)],
            modified[list(analysis.ENTRY_FEATURE_COLUMNS)],
            check_names=False,
        )

    def test_outcomes_are_excluded_from_input_features(self) -> None:
        input_columns = set(analysis.input_feature_columns())
        self.assertFalse(input_columns.intersection(analysis.OUTCOME_COLUMNS))
        self.assertNotIn("outcome_mae_pct", input_columns)
        self.assertNotIn("outcome_duration_minutes", input_columns)
        self.assertNotIn("outcome_target_hit", input_columns)

    def test_rolling_implementation_has_no_future_shift(self) -> None:
        source = inspect.getsource(analysis.calculate_entry_feature_frame).replace(" ", "")
        self.assertNotIn("shift(-", source)
        self.assertNotIn("center=True", source)

    def test_rsi_path_and_arm_duration(self) -> None:
        snapshot = self.snapshot()
        indicators = calculate_recovery_indicators(self.candles, self.config)
        arm = self.candles.index.get_loc(pd.Timestamp(self.trade["rsiArmTimestamp"]))
        signal = self.candles.index.get_loc(pd.Timestamp(self.trade["signalTimestamp"]))
        path = indicators["RecoveryRSI"].iloc[arm : signal + 1]
        self.assertAlmostEqual(snapshot["feature_rsi_min_since_arm"], float(path.min()))
        self.assertEqual(snapshot["feature_bars_arm_to_recovery"], signal - arm)
        self.assertEqual(snapshot["feature_minutes_arm_to_recovery"], 25)
        self.assertAlmostEqual(
            snapshot["feature_rsi_recovery_strength"],
            snapshot["feature_rsi_at_entry"] - self.config.rsi_recovery,
        )

    def test_entry_math_is_documented_and_causal(self) -> None:
        snapshot = self.snapshot()
        stamp = pd.Timestamp(self.trade["signalTimestamp"])
        position = self.candles.index.get_loc(stamp)
        row = self.candles.iloc[position]
        expected_return = (row.Close - row.Open) / row.Open * 100
        expected_body = abs(row.Close - row.Open) / row.Open * 100
        expected_clv = (row.Close - row.Low) / (row.High - row.Low)
        self.assertAlmostEqual(snapshot["feature_candle_return_pct"], expected_return)
        self.assertAlmostEqual(snapshot["feature_body_pct"], expected_body)
        self.assertAlmostEqual(snapshot["feature_close_location_value"], expected_clv)
        self.assertAlmostEqual(
            snapshot["feature_return_15m"],
            (row.Close / self.candles.iloc[position - 3].Close - 1) * 100,
        )
        self.assertAlmostEqual(
            snapshot["feature_return_30m"],
            (row.Close / self.candles.iloc[position - 6].Close - 1) * 100,
        )
        self.assertGreater(snapshot["feature_atr_pct"], 0)
        self.assertGreaterEqual(snapshot["feature_position_in_20bar_range"], 0)
        self.assertLessEqual(snapshot["feature_position_in_20bar_range"], 1)

    def test_ema_vwap_volume_and_time_features(self) -> None:
        snapshot = self.snapshot()
        self.assertAlmostEqual(
            snapshot["feature_ema_spread_pct"],
            (snapshot["feature_ema_fast"] - snapshot["feature_ema_slow"])
            / snapshot["feature_close"]
            * 100,
        )
        self.assertAlmostEqual(
            snapshot["feature_close_vs_vwap_pct"],
            (snapshot["feature_close"] - snapshot["feature_vwap_at_entry"])
            / snapshot["feature_vwap_at_entry"]
            * 100,
        )
        self.assertAlmostEqual(
            snapshot["feature_volume_ratio"],
            snapshot["feature_volume"] / snapshot["feature_volume_ema"],
        )
        self.assertEqual(snapshot["feature_time_of_day_bucket"], "MIDDAY")
        self.assertEqual(snapshot["feature_minutes_since_market_open"], 165)
        self.assertTrue(np.isfinite(snapshot["feature_opening_gap_pct"]))


class FeatureReportTests(unittest.TestCase):
    def test_quantile_labels_are_value_ordered(self) -> None:
        labels = analysis._quantile_bins(pd.Series(np.arange(100, dtype=float)))
        intervals = sorted(pd.unique(labels.dropna()), key=lambda interval: interval.left)
        self.assertEqual(len(intervals), 5)
        self.assertLess(intervals[0].right, intervals[-1].left)

    def test_reports_are_written_and_analysis_uses_all_outcomes(self) -> None:
        candles = fixture_candles()
        config = RecoveryConfig()
        trades = [
            fixture_trade(candles, duration=20, status="TARGET_HIT"),
            {**fixture_trade(candles, duration=600, status="TARGET_HIT"), "tradeId": "fixture:TEST:2"},
            {**fixture_trade(candles, duration=2000, status="OPEN"), "tradeId": "fixture:TEST:3"},
        ]
        snapshots = analysis.build_signal_feature_snapshots(
            symbol="TEST", timeframe="5m", candles=candles, trades=trades, config=config
        )
        with tempfile.TemporaryDirectory() as temporary:
            payload = analysis.write_feature_reports(snapshots, temporary, {"strategyVersion": "test"})
            self.assertEqual(payload["summary"]["observations"], 3)
            self.assertEqual(payload["summary"]["goodCount"], 1)
            self.assertEqual(payload["summary"]["neutralCount"], 1)
            self.assertEqual(payload["summary"]["badCount"], 1)
            for filename in analysis.REPORT_FILENAMES:
                self.assertTrue((Path(temporary) / filename).is_file(), filename)

    def test_cliffs_delta_direction(self) -> None:
        self.assertEqual(analysis.cliffs_delta([10, 11, 12], [1, 2, 3]), 1.0)
        self.assertEqual(analysis.cliffs_delta([1, 2, 3], [10, 11, 12]), -1.0)


if __name__ == "__main__":
    unittest.main()
