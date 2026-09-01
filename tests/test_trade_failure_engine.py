from __future__ import annotations

import unittest
from unittest.mock import patch

import pandas as pd

from ema_vwap_strong_buy import StrongBuyConfig
from trade_failure_engine import (
    EmpiricalFailureModel,
    TradeFailureResearchConfig,
    _failure_engine_trade,
    extract_failure_research_lots,
    run_trade_failure_research,
)


def observation(*, success: bool, decision: str, next_stamp: str, next_open: float = 98.0) -> dict:
    features = {
        "below_vwap": True,
        "ema_bearish": True,
        "ema_slope_down": True,
        "support_break": True,
        "bearish_direction": True,
        "bearish_volume": False,
        "stalled_progress": True,
    }
    return {
        "decisionTimestamp": decision,
        "nextTimestamp": next_stamp,
        "nextOpen": next_open,
        "barsHeld": 8,
        "currentClose": 98.5,
        "remainingTargetPct": 2.538,
        "mfePct": 0.05,
        "features": features,
        "stateKey": "1111101",
        "failedGroups": ["STRUCTURE", "MOMENTUM_PARTICIPATION", "PROGRESS"],
        "success": success,
        "futureAdverseLossPct": 3.0,
    }


def research_lot(number: int, *, success: bool, state_observations: int = 2) -> dict:
    day = 1 + number // 20
    minute = (number % 20) * 5
    entry = pd.Timestamp("2026-01-01 09:15", tz="Asia/Kolkata") + pd.Timedelta(days=day, minutes=minute)
    observations = [
        observation(
            success=success,
            decision=(entry + pd.Timedelta(minutes=5 * index)).isoformat(),
            next_stamp=(entry + pd.Timedelta(minutes=5 * (index + 1))).isoformat(),
        )
        for index in range(state_observations)
    ]
    return {
        "lotId": f"TEST-{number}",
        "symbol": "TEST",
        "quantity": 100,
        "entryTimestamp": entry.isoformat(),
        "entryPrice": 100.0,
        "targetPrice": 101.0,
        "targetPct": 1.0,
        "resolutionTimestamp": (entry + pd.Timedelta(hours=2)).isoformat(),
        "resolutionPrice": 101.0 if success else 95.0,
        "resolutionStatus": "TAKE_PROFIT" if success else "TIME_HORIZON_FAILURE",
        "success": success,
        "barsToResolution": 24,
        "observations": observations,
    }


class TradeFailureConfigurationTests(unittest.TestCase):
    def test_live_mode_does_not_exist(self) -> None:
        with self.assertRaisesRegex(ValueError, "OFF or RESEARCH_COMPARE"):
            TradeFailureResearchConfig(mode="LIVE").validate()

    def test_cost_bps_are_converted_to_percentage_points(self) -> None:
        self.assertEqual(TradeFailureResearchConfig(round_trip_cost_bps=14).cost_pct, 0.14)


class EmpiricalFailureModelTests(unittest.TestCase):
    def test_test_outcomes_never_change_the_fitted_probability(self) -> None:
        training = [research_lot(index, success=index % 4 == 0) for index in range(40)]
        model = EmpiricalFailureModel(20).fit(training)
        probe = observation(
            success=True,
            decision="2026-03-01T10:00:00+05:30",
            next_stamp="2026-03-01T10:05:00+05:30",
        )
        before = model.predict(probe)
        probe["success"] = False
        probe["futureAdverseLossPct"] = 99.0
        self.assertEqual(before, model.predict(probe))

    def test_exit_requires_persistence_and_executes_at_next_open(self) -> None:
        training = [research_lot(index, success=False) for index in range(40)]
        model = EmpiricalFailureModel(20).fit(training)
        test_lot = research_lot(100, success=False, state_observations=2)
        trade = _failure_engine_trade(
            test_lot,
            model,
            TradeFailureResearchConfig(decision_persistence_bars=2),
        )
        self.assertEqual(trade["status"], "THESIS_FAILED_EXIT")
        self.assertEqual(trade["exitTimestamp"], test_lot["observations"][1]["nextTimestamp"])
        self.assertEqual(trade["exitPrice"], 98.0)
        self.assertEqual(trade["decision"]["persistenceBars"], 2)


class CausalExtractionTests(unittest.TestCase):
    def indicator_frame(self) -> pd.DataFrame:
        index = pd.date_range("2026-01-05 09:15", periods=10, freq="5min", tz="Asia/Kolkata")
        close = [100.0, 100.2, 100.1, 99.8, 99.5, 99.4, 99.3, 99.2, 99.1, 99.0]
        data = pd.DataFrame(
            {
                "Open": close,
                "High": [value + 0.2 for value in close],
                "Low": [value - 0.2 for value in close],
                "Close": close,
                "Volume": [1_000.0] * len(close),
                "EmaFast": [100.0, 100.1, 100.05, 99.9, 99.7, 99.5, 99.4, 99.3, 99.2, 99.1],
                "EmaSlow": [99.9, 100.0, 100.0, 100.0, 99.95, 99.9, 99.8, 99.7, 99.6, 99.5],
                "SessionVwap": [99.8, 99.9, 100.0, 100.0, 99.9, 99.8, 99.7, 99.6, 99.5, 99.4],
                "PlusDi": [30.0, 30.0, 25.0, 20.0, 15.0, 14.0, 13.0, 12.0, 11.0, 10.0],
                "MinusDi": [10.0, 10.0, 15.0, 25.0, 30.0, 31.0, 32.0, 33.0, 34.0, 35.0],
                "RelativeVolume": [1.5] * len(close),
            },
            index=index,
        )
        return data

    def test_future_price_change_does_not_change_earlier_feature_state(self) -> None:
        data = self.indicator_frame()
        lot = {
            "lotId": "TEST-Cycle1-Lot1",
            "entryTimestamp": data.index[1].isoformat(),
            "entryPrice": 100.2,
            "targetPrice": 101.202,
            "targetPct": 1.0,
            "quantity": 100,
        }
        failure_config = TradeFailureResearchConfig(
            maximum_holding_bars=8,
            support_lookback_bars=2,
            ema_slope_lookback_bars=1,
            progress_lookback_bars=2,
        )
        with patch("trade_failure_engine.calculate_strong_buy_indicators", return_value=data):
            original = extract_failure_research_lots(
                "TEST", data, {"lots": [lot]},
                entry_config=StrongBuyConfig(), failure_config=failure_config,
            )
        changed = data.copy()
        changed.iloc[-1, changed.columns.get_loc("Low")] = 1.0
        changed.iloc[-1, changed.columns.get_loc("Close")] = 500.0
        with patch("trade_failure_engine.calculate_strong_buy_indicators", return_value=changed):
            mutated = extract_failure_research_lots(
                "TEST", changed, {"lots": [lot]},
                entry_config=StrongBuyConfig(), failure_config=failure_config,
            )
        self.assertEqual(original[0]["observations"][0]["features"], mutated[0]["observations"][0]["features"])


class WalkForwardResearchTests(unittest.TestCase):
    def test_matched_comparison_is_walk_forward_and_never_live(self) -> None:
        lots = [research_lot(index, success=False) for index in range(80)]
        result = run_trade_failure_research(
            lots,
            TradeFailureResearchConfig(
                mode="RESEARCH_COMPARE",
                minimum_training_lots=20,
                minimum_test_lots=10,
                walk_forward_folds=2,
            ),
        )
        self.assertEqual(result["foldsCompleted"], 2)
        self.assertFalse(result["liveAutoExitEnabled"])
        self.assertEqual(result["methodology"]["split"], "EXPANDING_WINDOW_WALK_FORWARD_BY_LOT_ENTRY_TIME_WITH_RESOLUTION_EMBARGO")
        self.assertGreater(result["matchedTestComparison"]["failureEngine"]["thesisFailedExits"], 0)

    def test_training_excludes_lots_unresolved_when_test_starts(self) -> None:
        lots = [research_lot(index, success=False) for index in range(80)]
        lots[39]["resolutionTimestamp"] = "2027-01-01T09:15:00+05:30"
        result = run_trade_failure_research(
            lots,
            TradeFailureResearchConfig(
                mode="RESEARCH_COMPARE",
                minimum_training_lots=20,
                minimum_test_lots=10,
                walk_forward_folds=2,
            ),
        )
        self.assertEqual(result["folds"][0]["trainingLots"], 39)

    def test_round_trip_cost_is_applied_to_both_matched_arms(self) -> None:
        lots = [research_lot(index, success=True) for index in range(80)]
        result = run_trade_failure_research(
            lots,
            TradeFailureResearchConfig(
                mode="RESEARCH_COMPARE",
                round_trip_cost_bps=14,
                minimum_training_lots=20,
                minimum_test_lots=10,
                walk_forward_folds=2,
            ),
        )
        metrics = result["matchedTestComparison"]
        self.assertAlmostEqual(metrics["baseline"]["averageReturnPct"], 0.86)
        self.assertEqual(metrics["baseline"]["netPnl"], metrics["failureEngine"]["netPnl"])

    def test_skipped_fold_can_never_be_promoted_as_a_candidate(self) -> None:
        result = run_trade_failure_research(
            [research_lot(index, success=False) for index in range(40)],
            TradeFailureResearchConfig(
                mode="RESEARCH_COMPARE",
                minimum_training_lots=30,
                minimum_test_lots=15,
                walk_forward_folds=2,
            ),
        )
        self.assertEqual(result["status"], "INSUFFICIENT_DATA")


if __name__ == "__main__":
    unittest.main()
