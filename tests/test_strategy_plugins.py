"""Adding a strategy means one file implementing the interface plus a registration — nothing else changes."""

from __future__ import annotations

import threading
import unittest
from datetime import date, datetime
from typing import Any, Mapping

import pandas as pd

from backend.api.backtest_routes import BacktestCreateRequest, BacktestServices, create_backtest_router
from backend.api.settings_routes import create_settings_router
from backend.backtest import BacktestEngine, BacktestRequest, ExecutionSettings, MemoryResultWriter
from backend.core.models import MarketContext, SignalDecision, normalize_candles
from backend.markets.base import market_spec
from backend.signals.engine import RiskSettings, SignalEngine
from backend.strategies.base import Strategy, assert_supported, resolve_config
from backend.strategies.registry import StrategyRegistry
from test_backtest_routes import FakeRunner, FakeRuns, FakeTrades, endpoints
from test_signal_engine import FakeSignalRepository
from test_strategy_engine import synthetic_nse_candles

IST = "Asia/Kolkata"


class BreakoutV1:
    """A deliberately tiny second strategy: BUY when the close breaks the prior N-bar high."""

    strategy_id = "breakout_v1"
    name = "Breakout"
    version = "1.0.0"
    supported_markets = ("NSE", "CRYPTO")
    supported_timeframes = ("5m",)
    config_schema = {
        "lookback": {"type": "integer", "default": 20, "minimum": 2, "maximum": 500, "label": "Breakout lookback"},
        "target_pct": {"type": "number", "default": 0.8, "minimum": 0.01, "maximum": 50.0, "label": "Target %"},
    }

    def resolve(self, config: Mapping[str, Any] | None) -> dict[str, Any]:
        return resolve_config(self.config_schema, config)

    def validate_config(self, config: Mapping[str, Any]) -> None:
        self.resolve(config)

    def required_history(self, config: Mapping[str, Any]) -> int:
        return self.resolve(config)["lookback"] + 1

    def evaluate(self, candles: pd.DataFrame, market_context: MarketContext, config: Mapping[str, Any]) -> SignalDecision:
        assert_supported(self, market_context)
        cfg = self.resolve(config)
        data = normalize_candles(candles, market_context.timezone)
        common = dict(strategy_id=self.strategy_id, strategy_version=self.version, market=market_context.market, symbol=market_context.symbol, timeframe=market_context.timeframe, configuration_snapshot=cfg)
        if len(data) < self.required_history(cfg):
            stamp = data.index[-1].to_pydatetime() if len(data) else datetime.now()
            return SignalDecision(decision="NONE", candle_timestamp=stamp, signal_price=None, target_price=None, reasons=("INSUFFICIENT_HISTORY",), **common)
        close = float(data["Close"].iloc[-1])
        prior_high = float(data["High"].iloc[-cfg["lookback"] - 1 : -1].max())
        stamp = data.index[-1].to_pydatetime()
        if close > prior_high:
            return SignalDecision(decision="BUY", candle_timestamp=stamp, signal_price=close, target_price=round(close * (1 + cfg["target_pct"] / 100), 4), reasons=("PRIOR_HIGH_BREAKOUT",), indicators={"priorHigh": prior_high}, **common)
        return SignalDecision(decision="NONE", candle_timestamp=stamp, signal_price=close, target_price=None, reasons=("NO_BREAKOUT",), **common)


class PluginTests(unittest.TestCase):
    def setUp(self) -> None:
        self.registry = StrategyRegistry()
        self.registry.register(BreakoutV1())
        self.candles = synthetic_nse_candles(days=3, seed=5)

    def test_registration_is_one_line_and_the_catalogue_discovers_it(self) -> None:
        self.assertIsInstance(self.registry.get("breakout_v1"), Strategy)
        api = endpoints(create_settings_router(self.registry))
        catalogue = api["GET /v2/strategies"](market="NSE")["strategies"]
        self.assertEqual([item["strategyId"] for item in catalogue], ["breakout_v1"])
        self.assertEqual(catalogue[0]["configSchema"]["lookback"]["default"], 20)

    def test_backtest_api_and_engine_run_the_new_strategy_without_changes(self) -> None:
        runs = FakeRuns()
        runner = FakeRunner(runs)
        api = endpoints(create_backtest_router(BacktestServices(registry=self.registry, runs=lambda: runs, trades=lambda: FakeTrades(), runner=lambda: runner)))
        record = api["POST /v2/backtests"](BacktestCreateRequest(market="NSE", strategyId="breakout_v1", symbols=["SYN"], startDate=date(2026, 8, 3), endDate=date(2026, 8, 31), configuration={"lookback": 10}))
        self.assertEqual(record["strategyVersion"], "1.0.0")
        self.assertEqual(record["configurationSnapshot"], {"lookback": 10, "target_pct": 0.8})

        class Source:
            def candles(inner, symbol, timeframe, start, end, *, warmup_bars):
                return self.candles

        writer = MemoryResultWriter()
        engine = BacktestEngine(strategy=self.registry.get("breakout_v1"), market=market_spec("NSE"), source=Source(), writer=writer, cancel_event=threading.Event())
        result = engine.run(BacktestRequest(run_id="plugin-run", market="NSE", strategy_id="breakout_v1", symbols=["SYN"], timeframe="5m", start_date=date(2026, 8, 3), end_date=date(2026, 8, 31), configuration={"lookback": 10}, execution=ExecutionSettings()))
        self.assertEqual(result["status"], "COMPLETE")
        self.assertGreater(writer.trade_count, 0)
        self.assertTrue(all(trade["strategy_id"] == "breakout_v1" for trade in writer.trades))
        self.assertTrue(all(trade["target_price"] > trade["entry_price"] for trade in writer.trades))

    def test_signal_engine_runs_the_new_strategy_and_stamps_its_id_and_version(self) -> None:
        repository = FakeSignalRepository()
        engine = SignalEngine(market=market_spec("NSE"), strategy=self.registry.get("breakout_v1"), configuration={"lookback": 5}, risk=RiskSettings(), timeframe="5m", repository=repository, clock=lambda: datetime(2026, 9, 1, tzinfo=self.candles.index.tz))
        engine.history.seed("SYN", self.candles.iloc[:30])
        for bar in range(30, len(self.candles)):
            engine.process_completed_candle("SYN", self.candles.iloc[[bar]])
        self.assertGreater(len(repository.rows), 0)
        row = next(iter(repository.rows.values()))
        self.assertEqual((row["strategyId"], row["strategyVersion"]), ("breakout_v1", "1.0.0"))
        self.assertEqual(row["configurationSnapshot"], {"lookback": 5, "target_pct": 0.8})
        self.assertEqual(row["reasons"], ["PRIOR_HIGH_BREAKOUT"])
