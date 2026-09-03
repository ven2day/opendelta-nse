from __future__ import annotations

import inspect
import unittest
from unittest.mock import patch

import pandas as pd

import backend.compat.recovery_dynamic_exit as recovery_dynamic_exit
from backend.collector import IST
from backend.compat.recovery_backtest import RecoveryConfig
from backend.compat.recovery_dynamic_exit import (
    DynamicExitConfig,
    aggregate_dynamic_exit_results,
    calculate_wilder_atr,
    simulate_dynamic_exit_symbol,
)


def frame(
    sessions: list[str],
    *,
    opens: list[float] | None = None,
    highs: list[float] | None = None,
    lows: list[float] | None = None,
    closes: list[float] | None = None,
) -> pd.DataFrame:
    count = len(sessions)
    opens = opens or [100.0] * count
    closes = closes or opens.copy()
    highs = highs or [max(left, right) + 0.5 for left, right in zip(opens, closes, strict=True)]
    lows = lows or [min(left, right) - 0.5 for left, right in zip(opens, closes, strict=True)]
    occurrences: dict[str, int] = {}
    stamps = []
    for session in sessions:
        offset = occurrences.get(session, 0)
        occurrences[session] = offset + 1
        stamps.append(pd.Timestamp(f"{session} 09:20", tz=IST) + pd.Timedelta(minutes=offset * 5))
    return pd.DataFrame(
        {"Open": opens, "High": highs, "Low": lows, "Close": closes, "Volume": [1_000.0] * count},
        index=pd.DatetimeIndex(stamps),
    )


def candidate(candles: pd.DataFrame, sequence: int, entry_index: int, execution_model: str = "SIGNAL_CLOSE") -> dict:
    signal_index = entry_index if execution_model == "SIGNAL_CLOSE" else entry_index - 1
    entry_price = float(candles.iloc[entry_index]["Close" if execution_model == "SIGNAL_CLOSE" else "Open"])
    return {
        "tradeId": f"run:TEST:{sequence}",
        "sequenceNumber": sequence,
        "signalTimestamp": candles.index[signal_index].isoformat(),
        "entryTimestamp": candles.index[entry_index].isoformat(),
        "entryBarIndex": entry_index,
        "entryPrice": entry_price,
        "targetPrice": entry_price * 1.0051,
        "confirmationScore": 3,
        "requiredConfirmations": 2,
        "rsiAtEntry": 41.0,
        "executionModel": execution_model,
    }


def observation(candles: pd.DataFrame, candidates: list[dict], symbol: str = "TEST") -> dict:
    return {
        "symbol": symbol,
        "firstCandle": candles.index[0].isoformat(),
        "lastCandle": candles.index[-1].isoformat(),
        "bars": len(candles),
        "trades": candidates,
        "events": [],
        "chart": [],
    }


def simulate(
    candles: pd.DataFrame,
    candidates: list[dict],
    *,
    exits: DynamicExitConfig | None = None,
    recovery: RecoveryConfig | None = None,
    symbol: str = "TEST",
) -> dict:
    recovery = recovery or RecoveryConfig(target_pct=0.51)
    with patch("backend.compat.recovery_dynamic_exit.simulate_recovery_symbol", return_value=observation(candles, candidates, symbol)):
        return simulate_dynamic_exit_symbol(
            symbol,
            candles,
            timeframe="5m",
            recovery_config=recovery,
            exit_config=exits or DynamicExitConfig(atr_length=1),
            run_id="run",
        )


class WilderAtrTests(unittest.TestCase):
    def test_known_wilder_atr_series(self) -> None:
        candles = frame(
            ["2026-01-02"] * 5,
            opens=[9, 10, 11, 12, 12],
            highs=[10, 12, 13, 15, 14],
            lows=[8, 9, 10, 11, 10],
            closes=[9, 11, 12, 12, 13],
        )
        result = calculate_wilder_atr(candles, 3)
        self.assertTrue(result.iloc[:2].isna().all())
        self.assertAlmostEqual(result.iloc[2], 8 / 3, places=8)
        self.assertAlmostEqual(result.iloc[3], 28 / 9, places=8)
        self.assertAlmostEqual(result.iloc[4], 92 / 27, places=8)

    def test_atr_is_causal(self) -> None:
        original = frame(["2026-01-02"] * 4, highs=[101, 102, 103, 104], lows=[99, 99, 100, 101])
        changed = original.copy()
        changed.iloc[3, changed.columns.get_loc("High")] = 500
        pd.testing.assert_series_equal(
            calculate_wilder_atr(original, 2).iloc[:3],
            calculate_wilder_atr(changed, 2).iloc[:3],
        )


class DynamicExitLifecycleTests(unittest.TestCase):
    def test_signal_close_uses_close_and_signal_candle_atr(self) -> None:
        candles = frame(["2026-01-02", "2026-01-05"], closes=[100, 100])
        trade = simulate(candles, [candidate(candles, 1, 0)])["positions"][0]
        self.assertEqual(trade["entryPrice"], 100)
        self.assertEqual(trade["atrAtSignal"], 1)

    def test_next_bar_open_uses_signal_atr_not_entry_candle(self) -> None:
        candles = frame(
            ["2026-01-02", "2026-01-02", "2026-01-02"],
            opens=[100, 110, 110],
            closes=[100, 110, 110],
            highs=[100.5, 120, 110.5],
            lows=[99.5, 100, 109.5],
        )
        recovery = RecoveryConfig(target_pct=0.51, execution_model="NEXT_BAR_OPEN")
        trade = simulate(candles, [candidate(candles, 1, 1, "NEXT_BAR_OPEN")], recovery=recovery)["positions"][0]
        self.assertEqual(trade["entryPrice"], 110)
        self.assertEqual(trade["atrAtSignal"], 1)
        self.assertAlmostEqual(trade["atrPctAtEntry"], 1 / 110 * 100, places=6)

    def test_minimum_and_maximum_stop_clamps(self) -> None:
        low_atr = frame(["2026-01-02"], highs=[100.1], lows=[99.9], closes=[100])
        low_trade = simulate(low_atr, [candidate(low_atr, 1, 0)])["positions"][0]
        self.assertEqual(low_trade["dynamicStopPct"], 0.75)
        high_atr = frame(["2026-01-02"], highs=[105], lows=[95], closes=[100])
        high_trade = simulate(high_atr, [candidate(high_atr, 1, 0)])["positions"][0]
        self.assertEqual(high_trade["dynamicStopPct"], 3.0)

    def test_reward_risk_sets_target(self) -> None:
        candles = frame(["2026-01-02"])
        trade = simulate(candles, [candidate(candles, 1, 0)])["positions"][0]
        self.assertAlmostEqual(trade["dynamicTargetPct"], trade["dynamicStopPct"] * 1.5)
        self.assertAlmostEqual(trade["targetPrice"], 100 * (1 + trade["dynamicTargetPct"] / 100), places=4)

    def test_target_stop_and_gap_execution_order(self) -> None:
        fixed = DynamicExitConfig(exit_model="FIXED_TP_SL", atr_length=1, fixed_take_profit_pct=1, fixed_stop_loss_pct=1)
        cases = [
            ({"opens": [100, 100], "highs": [100.5, 102], "lows": [99.5, 99.5]}, "TARGET_EXIT", 101),
            ({"opens": [100, 100], "highs": [100.5, 100.5], "lows": [99.5, 98]}, "STOP_EXIT", 99),
            ({"opens": [100, 102], "highs": [100.5, 102.5], "lows": [99.5, 101.5]}, "TARGET_GAP", 102),
            ({"opens": [100, 98], "highs": [100.5, 98.5], "lows": [99.5, 97.5]}, "STOP_GAP", 98),
            ({"opens": [100, 100], "highs": [100.5, 102], "lows": [99.5, 98]}, "STOP_EXIT", 99),
        ]
        for values, expected_status, expected_price in cases:
            with self.subTest(expected_status=expected_status):
                candles = frame(["2026-01-02", "2026-01-05"], closes=[100, values["opens"][1]], **values)
                trade = simulate(candles, [candidate(candles, 1, 0)], exits=fixed)["positions"][0]
                self.assertEqual(trade["status"], expected_status)
                self.assertEqual(trade["exitPrice"], expected_price)

    def test_entry_candle_never_exits(self) -> None:
        candles = frame(["2026-01-02"], highs=[150], lows=[50], closes=[100])
        fixed = DynamicExitConfig(exit_model="FIXED_TP_SL", atr_length=1, fixed_take_profit_pct=1, fixed_stop_loss_pct=1)
        self.assertEqual(simulate(candles, [candidate(candles, 1, 0)], exits=fixed)["positions"][0]["status"], "OPEN")

    def test_time_exit_uses_next_available_session_and_gap_checks_first(self) -> None:
        sessions = ["2026-01-02", "2026-01-05", "2026-01-06", "2026-01-08", "2026-01-09", "2026-01-12"]
        candles = frame(sessions, opens=[100, 100, 100, 100, 100, 100], closes=[100] * 6)
        fixed = DynamicExitConfig(exit_model="FIXED_TP_SL", atr_length=1, fixed_take_profit_pct=10, fixed_stop_loss_pct=10)
        trade = simulate(candles, [candidate(candles, 1, 0)], exits=fixed)["positions"][0]
        self.assertEqual(trade["status"], "TIME_EXIT")
        self.assertEqual(trade["exitTimestamp"], candles.index[5].isoformat())
        self.assertEqual(trade["holdingSessions"], 5)

    def test_missing_next_session_remains_open(self) -> None:
        sessions = ["2026-01-02", "2026-01-05", "2026-01-06", "2026-01-07", "2026-01-08"]
        candles = frame(sessions, closes=[100, 99, 98, 97, 96])
        fixed = DynamicExitConfig(exit_model="FIXED_TP_SL", atr_length=1, fixed_take_profit_pct=20, fixed_stop_loss_pct=20)
        trade = simulate(candles, [candidate(candles, 1, 0)], exits=fixed)["positions"][0]
        self.assertEqual(trade["status"], "OPEN")
        self.assertEqual(trade["unrealizedPnl"], -200)

    def test_max_open_lots_and_multi_symbol_aggregation(self) -> None:
        candles = frame(["2026-01-02", "2026-01-02", "2026-01-05"])
        fixed = DynamicExitConfig(exit_model="FIXED_TP_SL", atr_length=1, fixed_take_profit_pct=20, fixed_stop_loss_pct=20)
        first = simulate(candles, [candidate(candles, 1, 0), candidate(candles, 2, 1)], exits=fixed, symbol="AAA")
        second = simulate(candles, [candidate(candles, 1, 0)], exits=fixed, symbol="BBB")
        summary = aggregate_dynamic_exit_results([first, second])
        self.assertEqual(first["skippedMaxOpenLots"], 1)
        self.assertEqual(summary["executedTrades"], 2)
        self.assertEqual(summary["skippedMaxOpenLots"], 1)

    def test_fixed_quantity_and_risk_budget_sizing(self) -> None:
        candles = frame(["2026-01-02"])
        fixed_quantity = DynamicExitConfig(exit_model="FIXED_TP_SL", atr_length=1, fixed_stop_loss_pct=1, quantity_per_trade=50)
        risk_budget = DynamicExitConfig(
            exit_model="FIXED_TP_SL", atr_length=1, fixed_stop_loss_pct=1,
            position_sizing="RISK_BUDGET", rupee_risk_budget=250,
            maximum_quantity=80, maximum_capital_per_position=5_000,
        )
        self.assertEqual(simulate(candles, [candidate(candles, 1, 0)], exits=fixed_quantity)["positions"][0]["quantity"], 50)
        risk_trade = simulate(candles, [candidate(candles, 1, 0)], exits=risk_budget)["positions"][0]
        self.assertEqual(risk_trade["quantity"], 50)
        self.assertEqual(risk_trade["rupeeRiskAtEntry"], 50)

    def test_costs_and_slippage_reduce_net_pnl(self) -> None:
        candles = frame(["2026-01-02", "2026-01-05"], highs=[100.5, 102], lows=[99.5, 99.5])
        fixed = DynamicExitConfig(exit_model="FIXED_TP_SL", atr_length=1, fixed_take_profit_pct=1, fixed_stop_loss_pct=1)
        recovery = RecoveryConfig(target_pct=1, buy_cost_bps=10, sell_cost_bps=20, slippage_bps=5)
        trade = simulate(candles, [candidate(candles, 1, 0)], exits=fixed, recovery=recovery)["positions"][0]
        self.assertEqual(trade["grossPnl"], 50)
        self.assertAlmostEqual(trade["tradingCosts"], 20.12)
        self.assertAlmostEqual(trade["netPnl"], 29.88)

    def test_source_contains_no_future_shift_or_centered_window(self) -> None:
        source = inspect.getsource(recovery_dynamic_exit)
        self.assertNotIn("shift(-", source)
        self.assertNotIn("center=True", source)


class DynamicExitRequestTests(unittest.TestCase):
    def test_new_defaults_and_legacy_compatibility(self) -> None:
        from backend.app import BacktestRequest

        legacy = BacktestRequest(symbols=["SBIN"], strategyMode="rsi_recovery")
        self.assertEqual(legacy.resolved_exit_model(), "LEGACY_FIXED_TARGET")
        old_protected = BacktestRequest(symbols=["SBIN"], strategyMode="rsi_recovery", exitProtectionEnabled=True)
        self.assertEqual(old_protected.resolved_exit_model(), "LEGACY_PROTECTED_TARGET")
        dynamic = BacktestRequest(symbols=["SBIN"], strategyMode="rsi_recovery", exitModel="ATR_DYNAMIC_TP_SL")
        config = dynamic.dynamic_exit_config()
        self.assertEqual(config.atr_length, 14)
        self.assertEqual(config.stop_atr_multiplier, 1.25)
        self.assertEqual(config.reward_risk_ratio, 1.5)


if __name__ == "__main__":
    unittest.main()
