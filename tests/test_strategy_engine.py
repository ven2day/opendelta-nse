"""Phase 2 guarantees for the shared strategy engine.

Proves, on deterministic synthetic candles (and on real cached candles when
they are available on this machine):

1. Backtest and live evaluation agree bar-for-bar for identical candles.
2. An incomplete candle can never produce a signal.
3. Entries happen at the next candle's open.
4. Evaluation is causal: a prefix of history yields the same decision as the
   full history does for that bar, so future candles cannot leak in.
5. Strategies are discovered through the registry and carry a versioned,
   validated configuration snapshot; no code switches on a strategy name.
"""

from __future__ import annotations

import gzip
import re
import unittest
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
from pandas.testing import assert_frame_equal

from backend.core import indicators
from backend.core.models import MarketContext, SignalDecision, normalize_candles
from backend.strategies import STRATEGIES, StrongBuyV1
from backend.strategies.base import Strategy, resolve_config
from backend.strategies.registry import StrategyRegistry
from ema_vwap_strong_buy import StrongBuyConfig, calculate_strong_buy_indicators, simulate_strong_buy_symbol

IST = "Asia/Kolkata"
CANDLE_CACHE = Path("/var/lib/vento-nse/backtest")


def synthetic_nse_candles(days: int = 12, seed: int = 7) -> pd.DataFrame:
    """Deterministic 5-minute NSE session candles with trend + cycle + noise."""
    rng = np.random.default_rng(seed)
    stamps: list[pd.Timestamp] = []
    day = datetime(2026, 8, 3)  # a Monday
    while len(stamps) < days * 75:
        if day.weekday() < 5:
            open_at = day.replace(hour=9, minute=15)
            stamps.extend(pd.Timestamp(open_at + timedelta(minutes=5 * i), tz=IST) for i in range(75))
        day += timedelta(days=1)
    n = len(stamps)
    drift = np.linspace(0, 6, n)
    cycle = 4 * np.sin(np.arange(n) / 23) + 2 * np.sin(np.arange(n) / 7)
    noise = rng.normal(0, 0.35, n).cumsum()
    close = 500 + drift + cycle + noise
    open_ = np.concatenate([[close[0]], close[:-1]]) + rng.normal(0, 0.15, n)
    spread = np.abs(rng.normal(0.4, 0.15, n))
    high = np.maximum(open_, close) + spread
    low = np.minimum(open_, close) - spread
    volume = rng.integers(8_000, 40_000, n).astype(float) * (1 + 0.8 * (np.abs(cycle) > 3))
    return pd.DataFrame({"Open": open_, "High": high, "Low": low, "Close": close, "Volume": volume}, index=pd.DatetimeIndex(stamps))


def context(symbol: str = "SYN") -> MarketContext:
    return MarketContext(market="NSE", symbol=symbol, timeframe="5m", timezone=IST)


class StrongBuyEquivalenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.strategy = STRATEGIES.get("ema_vwap_strong_buy")
        cls.candles = synthetic_nse_candles()
        cls.config = cls.strategy.resolve({})
        cls.table = cls.strategy.compute_indicators(cls.candles, cls.config, IST)
        cls.backtest = simulate_strong_buy_symbol("SYN", cls.candles, timeframe="5m", config=StrongBuyConfig(), run_id="fixed")
        cls.warmup = cls.strategy.required_history(cls.config)

    def test_synthetic_history_actually_produces_signals(self) -> None:
        self.assertGreaterEqual(int(self.table["StrongBuy"].sum()), 3)
        self.assertGreaterEqual(len(self.backtest["signals"]), 3)

    def test_shared_indicator_table_matches_the_legacy_strong_buy_table_exactly(self) -> None:
        legacy = calculate_strong_buy_indicators(self.candles, StrongBuyConfig())
        assert_frame_equal(legacy, self.table, check_exact=True)

    def test_backtest_and_live_evaluation_agree_for_identical_candles(self) -> None:
        """Every backtest signal bar is a live BUY and every live BUY is a backtest signal."""
        backtest_signal_bars = {
            self.table.index.get_loc(pd.Timestamp(signal["signalTimestamp"]))
            for signal in self.backtest["signals"]
        }
        strong_bars = set(np.flatnonzero(self.table["StrongBuy"].to_numpy()))
        self.assertEqual(backtest_signal_bars, strong_bars)
        rng = np.random.default_rng(1)
        sample_bars = sorted(strong_bars | set(rng.choice(np.arange(self.warmup, len(self.table)), 40, replace=False).tolist()))
        for bar in sample_bars:
            live = self.strategy.evaluate(self.candles.iloc[: bar + 1], context(), {})
            self.assertEqual(live.candle_timestamp, self.table.index[bar].to_pydatetime())
            self.assertEqual(live.decision == "BUY", bar in backtest_signal_bars, f"bar {bar}")
            if live.decision == "BUY":
                self.assertAlmostEqual(live.signal_price, float(self.table["Close"].iloc[bar]))
                self.assertAlmostEqual(live.target_price, round(live.signal_price * 1.01, 4))
                self.assertEqual(live.indicators["confirmationScore"], int(self.table["ConfirmationScore"].iloc[bar]))

    def test_evaluation_is_causal_so_future_candles_cannot_leak_in(self) -> None:
        """Indicator values for bar t computed on candles[:t+1] equal those computed on all candles."""
        rng = np.random.default_rng(2)
        columns = ["EmaFast", "EmaSlow", "SessionVwap", "Adx", "PlusDi", "MinusDi", "RelativeVolume", "HtfAlignment", "ConfirmationScore", "StrongBuy"]
        for bar in sorted(rng.choice(np.arange(self.warmup, len(self.table)), 30, replace=False).tolist()):
            prefix = self.strategy.compute_indicators(self.candles.iloc[: bar + 1], self.config, IST)
            assert_frame_equal(prefix[columns].iloc[[-1]], self.table[columns].iloc[[bar]], check_exact=True)

    def test_an_incomplete_candle_cannot_generate_a_signal(self) -> None:
        signal_bar = int(np.flatnonzero(self.table["StrongBuy"].to_numpy())[0])
        prefix = self.candles.iloc[: signal_bar + 1].copy()
        self.assertEqual(self.strategy.evaluate(prefix, context(), {}).decision, "BUY")
        flagged = prefix.copy()
        flagged["Complete"] = True
        flagged.iloc[-1, flagged.columns.get_loc("Complete")] = False
        decision = self.strategy.evaluate(flagged, context(), {})
        self.assertEqual(decision.candle_timestamp, self.table.index[signal_bar - 1].to_pydatetime())
        self.assertNotEqual(decision.candle_timestamp, self.table.index[signal_bar].to_pydatetime())
        self.assertEqual(normalize_candles(flagged, IST).index[-1], self.table.index[signal_bar - 1])

    def test_entry_uses_the_next_candle_open(self) -> None:
        signals_by_lot = {signal["lotId"]: signal for signal in self.backtest["signals"] if signal["lotId"]}
        self.assertGreaterEqual(len(self.backtest["lots"]), 1)
        for lot in self.backtest["lots"]:
            signal_bar = self.table.index.get_loc(pd.Timestamp(signals_by_lot[lot["lotId"]]["signalTimestamp"]))
            self.assertEqual(lot["entryBarIndex"], signal_bar + 1)
            self.assertEqual(lot["entryTimestamp"], self.table.index[signal_bar + 1].isoformat())
            self.assertAlmostEqual(lot["entryPrice"], round(float(self.table["Open"].iloc[signal_bar + 1]), 4))

    def test_insufficient_history_never_signals(self) -> None:
        decision = self.strategy.evaluate(self.candles.iloc[: self.warmup - 1], context(), {})
        self.assertEqual(decision.decision, "NONE")
        self.assertIn("INSUFFICIENT_HISTORY", decision.reasons)
        empty = self.strategy.evaluate(self.candles.iloc[:0], context(), {})
        self.assertEqual(empty.decision, "NONE")

    def test_decision_carries_versioned_configuration_snapshot(self) -> None:
        decision = self.strategy.evaluate(self.candles, context(), {"target_pct": 2.5})
        self.assertEqual(decision.strategy_id, "ema_vwap_strong_buy")
        self.assertEqual(decision.strategy_version, StrongBuyV1.version)
        self.assertEqual(decision.configuration_snapshot["target_pct"], 2.5)
        self.assertEqual(decision.configuration_snapshot["ema_fast"], 9)
        public = decision.public()
        self.assertEqual(public["configurationSnapshot"], dict(decision.configuration_snapshot))
        self.assertEqual(public["candleTimestamp"], decision.candle_timestamp.isoformat())


@unittest.skipUnless((CANDLE_CACHE / "RELIANCE-5-1y.csv.gz").exists(), "real candle cache not present")
class RealCandleEquivalenceTests(unittest.TestCase):
    def test_shared_pipeline_is_identical_on_real_nse_candles(self) -> None:
        strategy = STRATEGIES.get("ema_vwap_strong_buy")
        for symbol in ("RELIANCE", "HDFCBANK"):
            with gzip.open(CANDLE_CACHE / f"{symbol}-5-1y.csv.gz") as fh:
                candles = pd.read_csv(fh, parse_dates=["Timestamp"]).set_index("Timestamp")
            assert_frame_equal(
                calculate_strong_buy_indicators(candles, StrongBuyConfig()),
                strategy.compute_indicators(candles, strategy.resolve({}), IST),
                check_exact=True,
            )


class IndicatorLibraryTests(unittest.TestCase):
    def test_wilder_rma_and_rsi_match_recovery_backtest_exactly(self) -> None:
        from recovery_backtest import calculate_wilder_rma, calculate_wilder_rsi

        close = synthetic_nse_candles(days=3)["Close"]
        pd.testing.assert_series_equal(indicators.wilder_rma(close, 14), calculate_wilder_rma(close, 14), check_exact=True)
        pd.testing.assert_series_equal(indicators.wilder_rsi(close, 14), calculate_wilder_rsi(close, 14), check_exact=True)

    def test_session_vwap_resets_per_session_in_the_market_timezone(self) -> None:
        candles = synthetic_nse_candles(days=2)
        vwap = indicators.session_vwap(candles, IST)
        first_bar_of_day_two = 75
        typical = (candles["High"] + candles["Low"] + candles["Close"]) / 3
        self.assertAlmostEqual(vwap.iloc[first_bar_of_day_two], typical.iloc[first_bar_of_day_two])
        self.assertAlmostEqual(vwap.iloc[0], typical.iloc[0])
        self.assertNotAlmostEqual(vwap.iloc[74], typical.iloc[74])

    def test_lengths_must_be_positive(self) -> None:
        series = pd.Series([1.0, 2.0, 3.0])
        for function in (indicators.ema, indicators.rma, indicators.wilder_rma, indicators.wilder_rsi, indicators.relative_volume):
            with self.assertRaises(ValueError):
                function(series, 0)


class RegistryAndConfigTests(unittest.TestCase):
    def test_strategy_is_discovered_through_the_registry_with_a_settings_schema(self) -> None:
        self.assertIn("ema_vwap_strong_buy", STRATEGIES)
        self.assertEqual([item.strategy_id for item in STRATEGIES.list("NSE")], ["ema_vwap_strong_buy"])
        self.assertEqual([item.strategy_id for item in STRATEGIES.list("CRYPTO")], ["ema_vwap_strong_buy"])
        described = STRATEGIES.describe()[0]
        self.assertEqual(described["name"], "Strong Buy")
        self.assertEqual(described["defaults"]["ema_fast"], 9)
        self.assertEqual(described["configSchema"]["target_pct"]["type"], "number")
        self.assertIsInstance(STRATEGIES.get("ema_vwap_strong_buy"), Strategy)
        with self.assertRaises(KeyError):
            STRATEGIES.get("does_not_exist")

    def test_registering_requires_the_interface_and_rejects_duplicate_ids(self) -> None:
        registry = StrategyRegistry()
        registry.register(StrongBuyV1())
        with self.assertRaises(ValueError):
            registry.register(StrongBuyV1())
        with self.assertRaises(TypeError):
            registry.register(object())  # type: ignore[arg-type]

    def test_configuration_is_validated_against_the_schema_and_the_strategy_rules(self) -> None:
        strategy = STRATEGIES.get("ema_vwap_strong_buy")
        with self.assertRaises(ValueError):
            strategy.validate_config({"unknown_setting": 1})
        with self.assertRaises(ValueError):
            strategy.validate_config({"ema_fast": 30, "ema_slow": 20})
        with self.assertRaises(ValueError):
            strategy.validate_config({"ema_fast": 9.5})
        with self.assertRaises(ValueError):
            strategy.validate_config({"additional_sizing_mode": "SOMETHING_ELSE"})
        with self.assertRaises(ValueError):
            strategy.validate_config({"target_pct": 0})
        resolved = resolve_config(strategy.config_schema, {"target_pct": 2})
        self.assertIsInstance(resolved["target_pct"], float)

    def test_lot_sizing_matches_the_legacy_strong_buy_configuration(self) -> None:
        strategy = STRATEGIES.get("ema_vwap_strong_buy")
        for overrides in ({}, {"additional_quantity_pct": 25.0}, {"additional_sizing_mode": "FIXED_PERCENTAGE_OF_FIRST_LOT"}):
            cfg = strategy.resolve(overrides)
            legacy = StrongBuyConfig(**overrides)
            for entry in range(6):
                self.assertEqual(StrongBuyV1.lot_quantity(cfg, entry), legacy.quantity(entry), overrides)

    def test_market_and_timeframe_compatibility_is_enforced(self) -> None:
        strategy = STRATEGIES.get("ema_vwap_strong_buy")
        candles = synthetic_nse_candles(days=1)
        with self.assertRaises(ValueError):
            strategy.evaluate(candles, MarketContext(market="NSE", symbol="X", timeframe="1h", timezone=IST), {})
        with self.assertRaises(ValueError):
            MarketContext(market="FOREX", symbol="X", timeframe="5m", timezone="UTC")  # type: ignore[arg-type]

    def test_no_strategy_name_branching_outside_the_strategy_files(self) -> None:
        """Engines and APIs must go through the registry, never `if strategy_id == ...`."""
        backend = Path(__file__).resolve().parent.parent / "backend"
        offenders = []
        for path in backend.rglob("*.py"):
            if path.parent.name == "strategies" and path.name not in {"registry.py", "base.py", "__init__.py"}:
                continue
            text = path.read_text()
            if re.search(r"""(if|elif)\s+[\w.\[\]"']*strategy(_id|Id|Key|_key)?\s*==\s*["']""", text) or re.search(r"""(if|elif)\s+["']ema_vwap_strong_buy["']""", text):
                offenders.append(str(path))
        self.assertEqual(offenders, [])

    def test_signal_decision_contract(self) -> None:
        with self.assertRaises(ValueError):
            SignalDecision(decision="BUY", strategy_id="x", strategy_version="1", market="NSE", symbol="S", timeframe="5m", candle_timestamp=datetime(2026, 1, 1), signal_price=None, target_price=None)
        with self.assertRaises(ValueError):
            SignalDecision(decision="MAYBE", strategy_id="x", strategy_version="1", market="NSE", symbol="S", timeframe="5m", candle_timestamp=datetime(2026, 1, 1), signal_price=1.0, target_price=None)  # type: ignore[arg-type]
