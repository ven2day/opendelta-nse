from __future__ import annotations

import unittest
from unittest.mock import patch

import pandas as pd

from main import IST
from recovery_backtest import RecoveryConfig
from recovery_rsi_profit_exit import RsiProfitExitConfig
from rsi_exit_optimizer import RsiExitOptimizationGrid, evaluate_rsi_exit_grid


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


class RsiExitOptimizerTests(unittest.TestCase):
    def test_default_grid_and_request_are_deterministic(self) -> None:
        from backtest_api import RsiExitComparisonRequest

        request = RsiExitComparisonRequest(symbols=["SBIN"])
        self.assertEqual(request.resolved_exit_model(), "RSI_PROFIT_RISK_CONTROL")
        self.assertEqual(len(request.comparison_grid().combinations()), 648)
        grid = RsiExitOptimizationGrid(
            arm_zones=((30, 40), (20, 35)),
            recovery_thresholds=(40, 35),
            profit_exit_rsi_levels=(60, 50),
            minimum_profit_pcts=(1.0, 0.5),
            stop_loss_pcts=(1.5, 1.0),
            max_holding_sessions=(5,),
        )
        self.assertEqual(grid.combinations(), grid.combinations())
        self.assertEqual(grid.combinations()[0]["rsiArmLow"], 20)

    def test_chronological_split_never_puts_validation_in_training(self) -> None:
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

        grid = RsiExitOptimizationGrid(
            arm_zones=((20, 35),),
            recovery_thresholds=(40,),
            profit_exit_rsi_levels=(50,),
            minimum_profit_pcts=(0.5,),
            stop_loss_pcts=(1.5,),
            max_holding_sessions=(5,),
        )
        with patch("rsi_exit_optimizer.simulate_recovery_symbol", side_effect=fake_observations):
            first = evaluate_rsi_exit_grid(
                {"AAA": candles},
                timeframe="5m",
                base_recovery_config=RecoveryConfig(),
                base_exit_config=RsiProfitExitConfig(),
                grid=grid,
                analysis_start=pd.Timestamp("2025-01-01", tz=IST),
                analysis_end=pd.Timestamp("2026-01-01", tz=IST),
                duration_years=1,
                run_id="deterministic",
                minimum_validation_trades=1,
            )
            second = evaluate_rsi_exit_grid(
                {"AAA": candles},
                timeframe="5m",
                base_recovery_config=RecoveryConfig(),
                base_exit_config=RsiProfitExitConfig(),
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
        comparable_first = {**first, "metadata": {**first["metadata"], "runtimeSeconds": 0}}
        comparable_second = {**second, "metadata": {**second["metadata"], "runtimeSeconds": 0}}
        self.assertEqual(comparable_first, comparable_second)


if __name__ == "__main__":
    unittest.main()
