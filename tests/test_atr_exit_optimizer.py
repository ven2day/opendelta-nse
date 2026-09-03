from __future__ import annotations

import unittest
from unittest.mock import patch

import pandas as pd

from backend.compat.atr_exit_optimizer import (
    AtrOptimizationGrid,
    build_walk_forward_folds,
    evaluate_atr_exit_grid,
)
from backend.collector import IST
from backend.compat.recovery_backtest import RecoveryConfig
from backend.compat.recovery_dynamic_exit import DynamicExitConfig


def daily_candles(start: str, periods: int) -> pd.DataFrame:
    index = pd.date_range(start, periods=periods, freq="D", tz=IST)
    return pd.DataFrame(
        {
            "Open": [100.0] * periods,
            "High": [101.0] * periods,
            "Low": [99.0] * periods,
            "Close": [100.0] * periods,
            "Volume": [1_000.0] * periods,
        },
        index=index,
    )


class WalkForwardFoldTests(unittest.TestCase):
    def test_one_year_is_nine_month_training_then_validation(self) -> None:
        folds = build_walk_forward_folds(
            pd.Timestamp("2025-01-01", tz=IST),
            pd.Timestamp("2026-01-01", tz=IST),
            1,
        )
        self.assertEqual(len(folds), 1)
        self.assertEqual(folds[0].validation_start, pd.Timestamp("2025-10-01", tz=IST))
        self.assertEqual(folds[0].validation_end, pd.Timestamp("2026-01-01", tz=IST))

    def test_three_year_folds_are_chronological_and_roll_three_months(self) -> None:
        folds = build_walk_forward_folds(
            pd.Timestamp("2023-01-01", tz=IST),
            pd.Timestamp("2026-01-01", tz=IST),
            3,
        )
        self.assertGreaterEqual(len(folds), 2)
        for fold in folds:
            self.assertLess(fold.training_start, fold.training_end)
            self.assertEqual(fold.training_end, fold.validation_start)
            self.assertLess(fold.validation_start, fold.validation_end)
        self.assertEqual(
            folds[1].training_start,
            folds[0].training_start + pd.DateOffset(months=3),
        )


class OptimizerTests(unittest.TestCase):
    def test_optimizer_request_defaults_to_dynamic_exit_model(self) -> None:
        from backend.app import AtrOptimizationRequest

        request = AtrOptimizationRequest(symbols=["SBIN"])
        self.assertEqual(request.resolved_exit_model(), "ATR_DYNAMIC_TP_SL")
        self.assertEqual(request.dynamic_exit_config().atr_length, 14)

    def test_grid_order_and_results_are_deterministic(self) -> None:
        grid = AtrOptimizationGrid(
            atr_lengths=(14,),
            stop_atr_multipliers=(1.25, 0.75),
            reward_risk_ratios=(1.5, 1.0),
            max_holding_sessions=(5,),
            minimum_stop_pcts=(0.75,),
            maximum_stop_pcts=(3.0,),
        )
        self.assertEqual(grid.combinations(), grid.combinations())
        self.assertEqual(grid.combinations()[0]["stopAtrMultiplier"], 0.75)

    def test_training_never_receives_validation_or_future_candles(self) -> None:
        candles = daily_candles("2025-01-01", 366)
        calls: list[tuple[pd.Timestamp, pd.Timestamp]] = []

        def fake_observations(symbol, period_candles, *, timeframe, config, run_id, analysis_start):
            calls.append((pd.Timestamp(analysis_start), period_candles.index.max()))
            return {
                "symbol": symbol,
                "firstCandle": period_candles.index[0].isoformat(),
                "lastCandle": period_candles.index[-1].isoformat(),
                "bars": len(period_candles),
                "trades": [],
                "events": [],
                "chart": [],
            }

        grid = AtrOptimizationGrid(
            atr_lengths=(1,),
            stop_atr_multipliers=(1.0,),
            reward_risk_ratios=(1.0,),
            max_holding_sessions=(1,),
            minimum_stop_pcts=(0.5,),
            maximum_stop_pcts=(2.0,),
        )
        with patch("backend.compat.atr_exit_optimizer.simulate_recovery_symbol", side_effect=fake_observations):
            first = evaluate_atr_exit_grid(
                {"AAA": candles},
                timeframe="5m",
                recovery_config=RecoveryConfig(),
                base_exit_config=DynamicExitConfig(atr_length=1),
                grid=grid,
                analysis_start=pd.Timestamp("2025-01-01", tz=IST),
                analysis_end=pd.Timestamp("2026-01-01", tz=IST),
                duration_years=1,
                run_id="deterministic",
                minimum_validation_trades=1,
            )
        training_start, training_last = calls[0]
        validation_start, validation_last = calls[1]
        self.assertEqual(training_start, pd.Timestamp("2025-01-01", tz=IST))
        self.assertLess(training_last, pd.Timestamp("2025-10-01", tz=IST))
        self.assertEqual(validation_start, pd.Timestamp("2025-10-01", tz=IST))
        self.assertLessEqual(validation_last, pd.Timestamp("2026-01-01", tz=IST))
        self.assertTrue(first["metadata"]["chronological"])
        self.assertFalse(first["metadata"]["shuffled"])


if __name__ == "__main__":
    unittest.main()
