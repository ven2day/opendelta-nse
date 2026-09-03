from __future__ import annotations

import inspect
import unittest
from unittest.mock import patch

import pandas as pd

import backend.compat.recovery_position_backtest as recovery_position_backtest
from backend.collector import IST
from backend.compat.recovery_backtest import RecoveryConfig
from backend.compat.recovery_position_backtest import (
    PositionProtectionConfig,
    aggregate_protected_results,
    simulate_protected_recovery_symbol,
)


def candles_for_sessions(
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
    highs = highs or [
        max(open_price, close_price) + 0.2
        for open_price, close_price in zip(opens, closes, strict=True)
    ]
    lows = lows or [
        min(open_price, close_price) - 0.2
        for open_price, close_price in zip(opens, closes, strict=True)
    ]
    occurrences: dict[str, int] = {}
    timestamps = []
    for session in sessions:
        offset = occurrences.get(session, 0)
        occurrences[session] = offset + 1
        timestamps.append(
            pd.Timestamp(f"{session} 09:20", tz=IST) + pd.Timedelta(minutes=5 * offset)
        )
    return pd.DataFrame(
        {
            "Open": opens,
            "High": highs,
            "Low": lows,
            "Close": closes,
            "Volume": [1_000.0] * count,
        },
        index=pd.DatetimeIndex(timestamps),
    )


def candidate(
    frame: pd.DataFrame,
    sequence: int,
    entry_index: int,
    *,
    execution_model: str = "SIGNAL_CLOSE",
    entry_price: float | None = None,
    target_pct: float = 0.51,
) -> dict:
    signal_index = entry_index if execution_model == "SIGNAL_CLOSE" else entry_index - 1
    price = float(
        frame.iloc[entry_index][
            "Close" if execution_model == "SIGNAL_CLOSE" else "Open"
        ]
    )
    price = price if entry_price is None else entry_price
    return {
        "tradeId": f"run:TEST:{sequence}",
        "sequenceNumber": sequence,
        "signalTimestamp": frame.index[signal_index].isoformat(),
        "entryTimestamp": frame.index[entry_index].isoformat(),
        "entryBarIndex": entry_index,
        "entryPrice": price,
        "targetPrice": price * (1 + target_pct / 100),
        "confirmationScore": 3,
        "requiredConfirmations": 2,
        "rsiAtEntry": 41.0,
        "executionModel": execution_model,
    }


def observation(frame: pd.DataFrame, candidates: list[dict]) -> dict:
    return {
        "symbol": "TEST",
        "firstCandle": frame.index[0].isoformat(),
        "lastCandle": frame.index[-1].isoformat(),
        "bars": len(frame),
        "trades": candidates,
        "events": [],
        "chart": [],
    }


def simulate(
    frame: pd.DataFrame,
    candidates: list[dict],
    *,
    recovery: RecoveryConfig | None = None,
    protection: PositionProtectionConfig | None = None,
) -> dict:
    with patch(
        "backend.compat.recovery_position_backtest.simulate_recovery_symbol",
        return_value=observation(frame, candidates),
    ):
        return simulate_protected_recovery_symbol(
            "TEST",
            frame,
            timeframe="5m",
            recovery_config=recovery or RecoveryConfig(target_pct=0.51),
            protection_config=protection or PositionProtectionConfig(enabled=True),
            run_id="run",
        )


class PositionProtectionLifecycleTests(unittest.TestCase):
    def test_exit_protection_off_returns_existing_observation_unchanged(self) -> None:
        frame = candles_for_sessions(["2026-01-02", "2026-01-05"])
        existing = observation(frame, [candidate(frame, 1, 0)])
        with patch(
            "backend.compat.recovery_position_backtest.simulate_recovery_symbol", return_value=existing
        ):
            result = simulate_protected_recovery_symbol(
                "TEST",
                frame,
                timeframe="5m",
                recovery_config=RecoveryConfig(target_pct=0.51),
                protection_config=PositionProtectionConfig(enabled=False),
                run_id="run",
            )
        self.assertIs(result, existing)

    def test_maximum_one_open_lot_skips_additional_valid_signal(self) -> None:
        frame = candles_for_sessions(["2026-01-02", "2026-01-02", "2026-01-05"])
        result = simulate(frame, [candidate(frame, 1, 0), candidate(frame, 2, 1)])
        self.assertEqual(result["totalValidBuySignals"], 2)
        self.assertEqual(result["executedTrades"], 1)
        self.assertEqual(result["skippedMaxOpenLots"], 1)
        self.assertEqual(result["skippedSignals"][0]["status"], "SKIPPED_MAX_OPEN_LOTS")

    def test_five_sessions_skip_weekends_and_missing_market_dates_then_exit_next_open(
        self,
    ) -> None:
        sessions = [
            "2026-01-02",
            "2026-01-05",
            "2026-01-06",
            "2026-01-08",
            "2026-01-09",
            "2026-01-12",
        ]
        frame = candles_for_sessions(sessions, opens=[100, 99, 98, 97, 96, 95])
        result = simulate(frame, [candidate(frame, 1, 0)])
        trade = result["positions"][0]
        self.assertEqual(trade["status"], "TIME_EXIT")
        self.assertEqual(trade["holdingSessions"], 5)
        self.assertEqual(trade["exitTimestamp"], frame.index[5].isoformat())
        self.assertEqual(trade["exitPrice"], 95.0)

    def test_target_before_expiry_is_target_exit_and_gap_uses_open(self) -> None:
        sessions = ["2026-01-02", "2026-01-05", "2026-01-06"]
        frame = candles_for_sessions(sessions, opens=[100, 101, 100])
        result = simulate(frame, [candidate(frame, 1, 0)])
        trade = result["positions"][0]
        self.assertEqual(trade["status"], "TARGET_EXIT")
        self.assertEqual(trade["exitFill"], "GAP_OPEN")
        self.assertEqual(trade["exitPrice"], 101.0)

    def test_target_high_fills_at_target_not_candle_high(self) -> None:
        sessions = ["2026-01-02", "2026-01-05"]
        frame = candles_for_sessions(sessions, highs=[100.2, 110.0])
        result = simulate(frame, [candidate(frame, 1, 0)])
        trade = result["positions"][0]
        self.assertEqual(trade["status"], "TARGET_EXIT")
        self.assertEqual(trade["exitFill"], "TARGET_PRICE")
        self.assertAlmostEqual(trade["exitPrice"], 100.51)

    def test_missing_next_session_leaves_position_open_and_marks_final_close(
        self,
    ) -> None:
        sessions = [
            "2026-01-02",
            "2026-01-05",
            "2026-01-06",
            "2026-01-07",
            "2026-01-08",
        ]
        frame = candles_for_sessions(sessions, closes=[100, 98, 96, 94, 90])
        result = simulate(frame, [candidate(frame, 1, 0)])
        trade = result["positions"][0]
        self.assertEqual(trade["status"], "OPEN")
        self.assertEqual(trade["holdingSessions"], 5)
        self.assertEqual(trade["unrealizedPnl"], -500.0)
        self.assertEqual(result["unrealizedPnl"], -500.0)

    def test_quantity_changes_rupee_pnl_without_fractional_shares(self) -> None:
        sessions = [
            "2026-01-02",
            "2026-01-05",
            "2026-01-06",
            "2026-01-07",
            "2026-01-08",
        ]
        frame = candles_for_sessions(sessions, closes=[100, 99, 98, 97, 90])
        ten = simulate(
            frame,
            [candidate(frame, 1, 0)],
            protection=PositionProtectionConfig(enabled=True, quantity_per_trade=10),
        )
        fifty = simulate(
            frame,
            [candidate(frame, 1, 0)],
            protection=PositionProtectionConfig(enabled=True, quantity_per_trade=50),
        )
        self.assertEqual(ten["positions"][0]["quantity"], 10)
        self.assertEqual(fifty["positions"][0]["quantity"], 50)
        self.assertEqual(fifty["unrealizedPnl"], ten["unrealizedPnl"] * 5)

    def test_buy_sell_costs_and_slippage_reduce_realized_pnl(self) -> None:
        sessions = [
            "2026-01-02",
            "2026-01-05",
            "2026-01-06",
            "2026-01-07",
            "2026-01-08",
            "2026-01-09",
        ]
        frame = candles_for_sessions(sessions, opens=[100, 99, 98, 97, 96, 90])
        result = simulate(
            frame,
            [candidate(frame, 1, 0)],
            recovery=RecoveryConfig(
                target_pct=0.51, buy_cost_bps=10, sell_cost_bps=20, slippage_bps=5
            ),
        )
        trade = result["positions"][0]
        self.assertEqual(trade["status"], "TIME_EXIT")
        self.assertAlmostEqual(trade["grossPnl"], -500.0)
        self.assertAlmostEqual(trade["buyCost"], 5.0)
        self.assertAlmostEqual(trade["sellCost"], 9.0)
        self.assertAlmostEqual(trade["slippageCost"], 4.75)
        self.assertAlmostEqual(trade["realizedPnl"], -518.75)

    def test_entry_candle_cannot_hit_target(self) -> None:
        frame = candles_for_sessions(["2026-01-02"], highs=[150.0])
        result = simulate(frame, [candidate(frame, 1, 0)])
        self.assertEqual(result["positions"][0]["status"], "OPEN")

    def test_signal_close_and_next_bar_open_remain_distinct(self) -> None:
        frame = candles_for_sessions(
            ["2026-01-02", "2026-01-02"], opens=[100, 102], closes=[100, 103]
        )
        signal_close = simulate(
            frame, [candidate(frame, 1, 0)], recovery=RecoveryConfig(target_pct=0.51)
        )
        next_open_candidate = candidate(frame, 1, 1, execution_model="NEXT_BAR_OPEN")
        next_open = simulate(
            frame,
            [next_open_candidate],
            recovery=RecoveryConfig(target_pct=0.51, execution_model="NEXT_BAR_OPEN"),
        )
        self.assertEqual(signal_close["positions"][0]["entryPrice"], 100.0)
        self.assertEqual(next_open["positions"][0]["entryPrice"], 102.0)
        self.assertNotEqual(
            signal_close["positions"][0]["entryTimestamp"],
            next_open["positions"][0]["entryTimestamp"],
        )

    def test_future_changes_do_not_change_signal_or_entry(self) -> None:
        sessions = ["2026-01-02", "2026-01-05", "2026-01-06"]
        original = candles_for_sessions(sessions, closes=[100, 99, 98])
        changed = candles_for_sessions(
            sessions, closes=[100, 60, 150], highs=[100.2, 60.2, 151]
        )
        first = simulate(original, [candidate(original, 1, 0)])["positions"][0]
        second = simulate(changed, [candidate(changed, 1, 0)])["positions"][0]
        self.assertEqual(first["signalTimestamp"], second["signalTimestamp"])
        self.assertEqual(first["entryTimestamp"], second["entryTimestamp"])
        self.assertEqual(first["entryPrice"], second["entryPrice"])

    def test_source_has_no_future_shift_or_centered_rolling_window(self) -> None:
        source = inspect.getsource(
            recovery_position_backtest.simulate_protected_recovery_symbol
        )
        self.assertNotIn("shift(-", source)
        self.assertNotIn("center=True", source)

    def test_aggregate_counts_skips_separately_from_trade_profitability(self) -> None:
        frame = candles_for_sessions(["2026-01-02", "2026-01-02", "2026-01-05"])
        result = simulate(frame, [candidate(frame, 1, 0), candidate(frame, 2, 1)])
        summary = aggregate_protected_results([result])
        self.assertEqual(summary["totalValidBuySignals"], 2)
        self.assertEqual(summary["executedTrades"], 1)
        self.assertEqual(summary["skippedMaxOpenLots"], 1)
        self.assertEqual(summary["openPositions"], 1)


class PositionProtectionRequestTests(unittest.TestCase):
    def test_protection_defaults_are_safe_and_target_defaults_to_point_five_one(
        self,
    ) -> None:
        from backend.app import BacktestRequest

        request = BacktestRequest(
            symbols=["KPIGREEN"],
            strategyMode="rsi_recovery",
            exitProtectionEnabled=True,
        )
        self.assertEqual(request.targetPct, 0.51)
        self.assertEqual(request.quantityPerTrade, 50)
        self.assertEqual(request.maxOpenLotsPerSymbol, 1)
        self.assertEqual(request.maxHoldingTradingDays, 5)

    def test_existing_request_contract_remains_signal_observation_by_default(
        self,
    ) -> None:
        from backend.app import BacktestRequest

        request = BacktestRequest(symbols=["KPIGREEN"], strategyMode="rsi_recovery")
        self.assertFalse(request.exitProtectionEnabled)
        self.assertEqual(request.targetPct, 0.5)


if __name__ == "__main__":
    unittest.main()
