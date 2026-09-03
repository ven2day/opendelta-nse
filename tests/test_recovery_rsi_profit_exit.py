from __future__ import annotations

import inspect
import unittest
from dataclasses import replace
from unittest.mock import patch

import pandas as pd

import backend.compat.recovery_rsi_profit_exit as recovery_rsi_profit_exit
from backend.collector import IST
from backend.compat.recovery_backtest import RecoveryConfig, simulate_recovery_symbol
from backend.compat.recovery_rsi_profit_exit import (
    RsiProfitExitConfig,
    aggregate_rsi_profit_exit_results,
    simulate_rsi_profit_exit_symbol,
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
    highs = highs or [max(left, right) + 0.2 for left, right in zip(opens, closes, strict=True)]
    lows = lows or [min(left, right) - 0.2 for left, right in zip(opens, closes, strict=True)]
    occurrences: dict[str, int] = {}
    timestamps = []
    for session in sessions:
        offset = occurrences.get(session, 0)
        occurrences[session] = offset + 1
        timestamps.append(pd.Timestamp(f"{session} 09:20", tz=IST) + pd.Timedelta(minutes=5 * offset))
    return pd.DataFrame(
        {"Open": opens, "High": highs, "Low": lows, "Close": closes, "Volume": [1_000.0] * count},
        index=pd.DatetimeIndex(timestamps),
    )


def candidate(
    candles: pd.DataFrame,
    sequence: int,
    entry_index: int,
    execution_model: str = "SIGNAL_CLOSE",
    symbol: str = "TEST",
) -> dict:
    signal_index = entry_index if execution_model == "SIGNAL_CLOSE" else entry_index - 1
    entry_price = float(candles.iloc[entry_index]["Close" if execution_model == "SIGNAL_CLOSE" else "Open"])
    return {
        "tradeId": f"run:{symbol}:{sequence}",
        "sequenceNumber": sequence,
        "runId": "run",
        "strategyMode": "rsi_recovery",
        "symbol": symbol,
        "timeframe": "5m",
        "signalTimestamp": candles.index[signal_index].isoformat(),
        "entryTimestamp": candles.index[entry_index].isoformat(),
        "entryBarIndex": entry_index,
        "entryPrice": entry_price,
        "executionModel": execution_model,
        "rsiArmTimestamp": candles.index[max(signal_index - 1, 0)].isoformat(),
        "rsiArmValue": 30.0,
        "rsiAtEntry": 41.0,
        "confirmationScore": 3,
        "requiredConfirmations": 2,
        "emaConfirmation": True,
        "vwapConfirmation": True,
        "volumeConfirmation": True,
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
    rsi: list[float],
    *,
    exits: RsiProfitExitConfig | None = None,
    recovery: RecoveryConfig | None = None,
    symbol: str = "TEST",
) -> dict:
    indicators = candles.copy()
    indicators["RecoveryRSI"] = rsi
    recovery = recovery or RecoveryConfig(target_pct=0.51)
    with (
        patch("backend.compat.recovery_rsi_profit_exit.simulate_recovery_symbol", return_value=observation(candles, candidates, symbol)),
        patch("backend.compat.recovery_rsi_profit_exit.calculate_recovery_indicators", return_value=indicators),
    ):
        return simulate_rsi_profit_exit_symbol(
            symbol,
            candles,
            timeframe="5m",
            recovery_config=recovery,
            exit_config=exits or RsiProfitExitConfig(),
            run_id="run",
        )


def indicator_frame(rsi: list[float]) -> pd.DataFrame:
    candles = frame(["2026-01-02"] * len(rsi), closes=[100.0] * len(rsi))
    candles["RecoveryRSI"] = rsi
    candles["EMAFast"] = 101.0
    candles["EMASlow"] = 100.0
    candles["SessionVWAP"] = 99.0
    candles["VolumeEMA"] = 900.0
    return candles


def recovery_signals(rsi: list[float], config: RecoveryConfig) -> dict:
    config = replace(config, rsi_length=1, ema_fast=1, ema_slow=1, volume_ema=1)
    data = indicator_frame(rsi)
    with (
        patch("backend.compat.recovery_backtest.validate_candles", return_value=[]),
        patch("backend.compat.recovery_backtest.calculate_recovery_indicators", return_value=data),
    ):
        return simulate_recovery_symbol(
            "TEST", data[["Open", "High", "Low", "Close", "Volume"]],
            timeframe="5m", config=config, run_id="run",
        )


class ConfigurableEntryTests(unittest.TestCase):
    def test_arm_zone_is_configurable_and_oversold_alone_does_not_buy(self) -> None:
        position_config = RecoveryConfig(rsi_arm_low=20, rsi_arm_high=35, rsi_recovery=40)
        self.assertEqual(recovery_signals([50, 34, 38, 41, 42], position_config)["buySignals"], 1)
        self.assertEqual(recovery_signals([50, 25, 24, 23, 22], position_config)["buySignals"], 0)
        incompatible_zone = RecoveryConfig(rsi_arm_low=30, rsi_arm_high=33, rsi_recovery=40)
        self.assertEqual(recovery_signals([50, 25, 38, 41, 42], incompatible_zone)["buySignals"], 0)

    def test_buy_requires_recovery_crossover_and_confirmations(self) -> None:
        config = RecoveryConfig(rsi_arm_low=20, rsi_arm_high=35, rsi_recovery=40, minimum_confirmations=2)
        result = recovery_signals([50, 30, 39, 40, 41, 42], config)
        self.assertEqual(result["buySignals"], 1)
        self.assertEqual(result["trades"][0]["signalTimestamp"], indicator_frame([50, 30, 39, 40, 41, 42]).index[4].isoformat())
        rejected = indicator_frame([50, 30, 39, 41, 42])
        rejected["EMAFast"] = 99.0
        rejected["SessionVWAP"] = 101.0
        compact_config = replace(config, rsi_length=1, ema_fast=1, ema_slow=1, volume_ema=1)
        with (
            patch("backend.compat.recovery_backtest.validate_candles", return_value=[]),
            patch("backend.compat.recovery_backtest.calculate_recovery_indicators", return_value=rejected),
        ):
            result = simulate_recovery_symbol(
                "TEST", rejected[["Open", "High", "Low", "Close", "Volume"]],
                timeframe="5m", config=compact_config, run_id="run",
            )
        self.assertEqual(result["buySignals"], 0)

    def test_arm_expiry_and_future_changes(self) -> None:
        config = RecoveryConfig(
            rsi_arm_low=20, rsi_arm_high=35, rsi_recovery=40,
            setup_expiry_bars=2,
        )
        self.assertEqual(recovery_signals([50, 30, 35, 35, 41], config)["buySignals"], 0)
        causal_config = RecoveryConfig(rsi_arm_low=20, rsi_arm_high=35, rsi_recovery=40)
        original = recovery_signals([50, 30, 39, 41, 45, 45], causal_config)
        changed = recovery_signals([50, 30, 39, 41, 1, 99], causal_config)
        self.assertEqual(original["trades"][0]["signalTimestamp"], changed["trades"][0]["signalTimestamp"])


class RsiProfitExitLifecycleTests(unittest.TestCase):
    def test_entry_execution_models_remain_distinct(self) -> None:
        candles = frame(
            ["2026-01-02"] * 3,
            opens=[100, 97, 97],
            highs=[101, 98, 98],
            lows=[99, 96, 96],
            closes=[100, 97.5, 97.5],
        )
        close_trade = simulate(
            candles,
            [candidate(candles, 1, 0, execution_model="SIGNAL_CLOSE")],
            [41, 41, 41],
            recovery=RecoveryConfig(execution_model="SIGNAL_CLOSE"),
        )["positions"][0]
        next_open_trade = simulate(
            candles,
            [candidate(candles, 1, 1, execution_model="NEXT_BAR_OPEN")],
            [41, 41, 41],
            recovery=RecoveryConfig(execution_model="NEXT_BAR_OPEN"),
        )["positions"][0]
        self.assertEqual(close_trade["entryPrice"], 100)
        self.assertEqual(close_trade["entryTimestamp"], candles.index[0].isoformat())
        self.assertEqual(next_open_trade["entryPrice"], 97)
        self.assertEqual(next_open_trade["entryTimestamp"], candles.index[1].isoformat())

    def test_rsi_below_threshold_or_insufficient_profit_does_not_exit(self) -> None:
        candles = frame(["2026-01-02"] * 3, closes=[100, 101, 101])
        first = simulate(candles, [candidate(candles, 1, 0)], [41, 49, 49])["positions"][0]
        second = simulate(
            frame(["2026-01-02"] * 3, closes=[100, 100.4, 100.4]),
            [candidate(frame(["2026-01-02"] * 3, closes=[100, 100.4, 100.4]), 1, 0)],
            [41, 55, 55],
        )["positions"][0]
        self.assertEqual(first["status"], "OPEN")
        self.assertEqual(second["status"], "OPEN")

    def test_profit_and_overbought_exit_reasons(self) -> None:
        candles = frame(["2026-01-02"] * 3, closes=[100, 100.6, 101])
        profit = simulate(candles, [candidate(candles, 1, 0)], [41, 55, 55])["positions"][0]
        overbought = simulate(candles, [candidate(candles, 1, 0)], [41, 70, 70])["positions"][0]
        self.assertEqual(profit["status"], "RSI_PROFIT_EXIT")
        self.assertEqual(profit["exitPrice"], 100.6)
        self.assertEqual(overbought["status"], "RSI_OVERBOUGHT_PROFIT_EXIT")

    def test_next_open_rechecks_minimum_profit(self) -> None:
        candles = frame(
            ["2026-01-02"] * 4,
            opens=[100, 100, 100.2, 100.2],
            closes=[100, 100.8, 100.2, 100.2],
            highs=[100.2, 101, 100.4, 100.4],
            lows=[99.8, 99.8, 100, 100],
        )
        exits = RsiProfitExitConfig(exit_execution_model="NEXT_BAR_OPEN")
        trade = simulate(candles, [candidate(candles, 1, 0)], [41, 55, 55, 55], exits=exits)["positions"][0]
        self.assertEqual(trade["status"], "OPEN")

    def test_next_open_executes_when_profit_survives(self) -> None:
        candles = frame(
            ["2026-01-02"] * 3,
            opens=[100, 100, 100.7],
            closes=[100, 100.8, 100.7],
            highs=[100.2, 101, 100.9],
            lows=[99.8, 99.8, 100.6],
        )
        exits = RsiProfitExitConfig(exit_execution_model="NEXT_BAR_OPEN")
        trade = simulate(candles, [candidate(candles, 1, 0)], [41, 55, 55], exits=exits)["positions"][0]
        self.assertEqual(trade["status"], "RSI_PROFIT_EXIT")
        self.assertEqual(trade["exitPrice"], 100.7)

    def test_stop_and_gap_stop_have_priority(self) -> None:
        stop_candles = frame(
            ["2026-01-02", "2026-01-05"],
            opens=[100, 100], highs=[150, 101], lows=[50, 98], closes=[100, 100.8],
        )
        stopped = simulate(stop_candles, [candidate(stop_candles, 1, 0)], [41, 75])["positions"][0]
        self.assertEqual(stopped["status"], "STOP_EXIT")
        self.assertEqual(stopped["exitPrice"], 98.5)
        gap_candles = frame(
            ["2026-01-02", "2026-01-05"],
            opens=[100, 97], highs=[100.2, 101], lows=[99.8, 96], closes=[100, 100.8],
        )
        gap = simulate(gap_candles, [candidate(gap_candles, 1, 0)], [41, 75])["positions"][0]
        self.assertEqual(gap["status"], "STOP_GAP")
        self.assertEqual(gap["exitPrice"], 97)

    def test_entry_candle_cannot_stop_or_exit(self) -> None:
        candles = frame(["2026-01-02"], opens=[100], highs=[150], lows=[50], closes=[101])
        trade = simulate(candles, [candidate(candles, 1, 0)], [75])["positions"][0]
        self.assertEqual(trade["status"], "OPEN")
        self.assertEqual(trade["maxAdversePct"], 0)
        self.assertEqual(trade["maxFavorablePct"], 0)

    def test_time_exit_uses_next_available_session_and_missing_next_remains_open(self) -> None:
        sessions = ["2026-01-02", "2026-01-05", "2026-01-06", "2026-01-08", "2026-01-09", "2026-01-12"]
        candles = frame(sessions, closes=[100] * 6)
        trade = simulate(candles, [candidate(candles, 1, 0)], [41] * 6)["positions"][0]
        self.assertEqual(trade["status"], "TIME_EXIT")
        self.assertEqual(trade["exitTimestamp"], candles.index[5].isoformat())
        self.assertEqual(trade["holdingSessions"], 5)
        shorter = candles.iloc[:5]
        open_trade = simulate(shorter, [candidate(shorter, 1, 0)], [41] * 5)["positions"][0]
        self.assertEqual(open_trade["status"], "OPEN")

    def test_max_open_lots_are_per_symbol_and_skips_do_not_change_profitability(self) -> None:
        candles = frame(["2026-01-02"] * 3)
        first = simulate(
            candles,
            [candidate(candles, 1, 0, symbol="AAA"), candidate(candles, 2, 1, symbol="AAA")],
            [41, 41, 41], symbol="AAA",
        )
        second = simulate(
            candles,
            [candidate(candles, 1, 0, symbol="BBB")],
            [41, 41, 41], symbol="BBB",
        )
        summary = aggregate_rsi_profit_exit_results([first, second])
        self.assertEqual(first["skippedMaxOpenLots"], 1)
        self.assertEqual(summary["executedTrades"], 2)
        self.assertEqual(summary["skippedMaxOpenLots"], 1)
        self.assertEqual(summary["winningTrades"], 0)

    def test_quantity_costs_mae_and_mfe_are_isolated(self) -> None:
        candles = frame(
            ["2026-01-02"] * 3,
            opens=[100, 100, 100], highs=[100.2, 101, 101.5], lows=[99.8, 99, 99.5], closes=[100, 100.6, 101],
        )
        exits = RsiProfitExitConfig(quantity_per_trade=25)
        recovery = RecoveryConfig(buy_cost_bps=10, sell_cost_bps=20, slippage_bps=5)
        trade = simulate(candles, [candidate(candles, 1, 0)], [41, 55, 55], exits=exits, recovery=recovery)["positions"][0]
        self.assertEqual(trade["quantity"], 25)
        self.assertEqual(trade["grossPnl"], 15)
        self.assertAlmostEqual(trade["tradingCosts"], 10.04, places=2)
        self.assertEqual(trade["maxAdversePct"], -1)
        self.assertEqual(trade["maxFavorablePct"], 1)

    def test_source_contains_no_future_shift_or_centered_window(self) -> None:
        source = inspect.getsource(recovery_rsi_profit_exit)
        self.assertNotIn("shift(-", source)
        self.assertNotIn("center=True", source)


class RequestContractTests(unittest.TestCase):
    def test_new_mode_defaults_are_separate_from_legacy(self) -> None:
        from backend.app import BacktestRequest

        legacy = BacktestRequest(symbols=["SBIN"], strategyMode="rsi_recovery")
        self.assertEqual((legacy.rsiArmLow, legacy.rsiArmHigh), (30, 40))
        self.assertEqual(legacy.resolved_exit_model(), "LEGACY_FIXED_TARGET")
        position = BacktestRequest(
            symbols=["SBIN"], strategyMode="rsi_recovery",
            exitModel="RSI_PROFIT_RISK_CONTROL",
        )
        self.assertEqual((position.rsiArmLow, position.rsiArmHigh), (20, 35))
        self.assertEqual(position.rsi_profit_exit_config(), RsiProfitExitConfig())


if __name__ == "__main__":
    unittest.main()
