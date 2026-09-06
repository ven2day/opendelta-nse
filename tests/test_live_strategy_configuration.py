"""Live Signals can run several strategy/timeframe bindings per market."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from backend.platform_runtime import PlatformRuntime
from backend.signals.configuration import LiveStrategyBinding, live_strategy_bindings


class LiveStrategyConfigurationTests(unittest.TestCase):
    def test_nse_defaults_to_the_daily_rsi_swing_strategy(self) -> None:
        self.assertEqual(
            live_strategy_bindings("nse", {}),
            (LiveStrategyBinding("rsi_dip_ladder_v1", "1d"),),
        )

    def test_plural_json_runs_independent_strategy_timeframes(self) -> None:
        environment = {
            "NSE_LIVE_STRATEGIES": """
                [
                  {"strategyId":"rsi_dip_ladder_v1","timeframe":"1d"},
                  {"strategyId":"ema_vwap_strong_buy","timeframe":"5m"},
                  {"strategyId":"disabled","timeframe":"15m","enabled":false}
                ]
            """,
        }
        self.assertEqual(
            live_strategy_bindings("NSE", environment),
            (
                LiveStrategyBinding("rsi_dip_ladder_v1", "1d"),
                LiveStrategyBinding("ema_vwap_strong_buy", "5m"),
            ),
        )

    def test_legacy_single_strategy_variables_remain_supported(self) -> None:
        bindings = live_strategy_bindings(
            "NSE",
            {"NSE_LIVE_STRATEGY": "ema_vwap_strong_buy", "NSE_LIVE_TIMEFRAME": "15m"},
        )
        self.assertEqual(bindings, (LiveStrategyBinding("ema_vwap_strong_buy", "15m"),))

    def test_invalid_and_duplicate_bindings_fail_closed(self) -> None:
        for raw in ("not json", "[]", '[{"strategyId":"x"}]'):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                live_strategy_bindings("NSE", {"NSE_LIVE_STRATEGIES": raw})
        duplicate = '[{"strategyId":"x","timeframe":"1d"},{"strategyId":"x","timeframe":"1d"}]'
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            live_strategy_bindings("NSE", {"NSE_LIVE_STRATEGIES": duplicate})

    def test_runtime_validates_registry_market_and_timeframe_compatibility(self) -> None:
        runtime = PlatformRuntime(database=None, candle_sources={})
        with patch.dict(
            "os.environ",
            {"NSE_LIVE_STRATEGIES": '[{"strategyId":"rsi_dip_ladder_v1","timeframe":"1d"}]'},
            clear=False,
        ):
            self.assertEqual(runtime.live_bindings("NSE")[0].public(), {"strategyId": "rsi_dip_ladder_v1", "timeframe": "1d"})
        with patch.dict(
            "os.environ",
            {"NSE_LIVE_STRATEGIES": '[{"strategyId":"rsi_dip_ladder_v1","timeframe":"1m"}]'},
            clear=False,
        ):
            with self.assertRaisesRegex(ValueError, "does not support"):
                runtime.live_bindings("NSE")

        with patch.dict(
            "os.environ",
            {"NSE_LIVE_STRATEGIES": '[{"strategyId":"rsi_dip_ladder_v1","timeframe":"4h"}]'},
            clear=False,
        ):
            with self.assertRaisesRegex(ValueError, "backtest-only"):
                runtime.live_bindings("NSE")

    def test_runtime_starts_every_configured_binding_independently(self) -> None:
        runtime = PlatformRuntime(database=None, candle_sources={})
        started: list[tuple[str, str]] = []

        def fake_worker(_market: str, *, binding: LiveStrategyBinding, **_kwargs):
            return SimpleNamespace(
                engine=SimpleNamespace(
                    tracked_symbols=lambda: [],
                    track_market_candle=lambda *_args: None,
                ),
                configure_market_tracking=lambda **_kwargs: None,
                start=lambda: started.append((binding.strategy_id, binding.timeframe)),
                stop=lambda: None,
                status=lambda: {**binding.public(), "status": "READY"},
            )

        environment = {
            "NSE_SIGNAL_ENGINE_V2_ENABLED": "true",
            "NSE_PAPER_TRADING_V2_ENABLED": "false",
            "CRYPTO_SIGNAL_ENGINE_V2_ENABLED": "false",
            "NSE_LIVE_STRATEGIES": '[{"strategyId":"rsi_dip_ladder_v1","timeframe":"1d"},{"strategyId":"ema_vwap_strong_buy","timeframe":"5m"}]',
        }
        with patch.dict("os.environ", environment, clear=False), patch.object(runtime, "build_signal_worker", side_effect=fake_worker):
            runtime._start_signal_workers()

        self.assertEqual(started, [("rsi_dip_ladder_v1", "1d"), ("ema_vwap_strong_buy", "5m")])
        self.assertEqual(
            [(row["strategyId"], row["timeframe"]) for row in runtime.worker_statuses("NSE")],
            started,
        )

    def test_runtime_resolves_latest_paper_outcome_for_each_worker_cycle(self) -> None:
        runtime = PlatformRuntime(database=None, candle_sources={})
        deployments = [
            {"strategyId": "rsi_dip_ladder_v1", "timeframe": "5m", "mode": "PAPER", "signalSource": "OPENDELTA"},
            {"strategyId": "ema_vwap_strong_buy", "timeframe": "5m", "mode": "PAPER", "signalSource": "OPENDELTA"},
        ]
        workers = [
            {
                "strategyId": "rsi_dip_ladder_v1",
                "strategyVersion": "1.0.0",
                "timeframe": "5m",
                "status": "READY",
                "connectionStatus": "CONNECTED",
                "lifecycle": {"status": "COMPLETE", "lastSignalId": "filled-signal"},
                "lastSignal": {"signalId": "filled-signal", "symbol": "TCS"},
            },
            {
                "strategyId": "ema_vwap_strong_buy",
                "strategyVersion": "1.0.0",
                "timeframe": "5m",
                "status": "READY",
                "connectionStatus": "CONNECTED",
                "lifecycle": {"status": "COMPLETE", "lastSignalId": "queued-signal"},
                "lastSignal": {"signalId": "queued-signal", "symbol": "INFY"},
            },
        ]
        broker = SimpleNamespace(
            account={"accountId": "paper-account"},
            repositories=SimpleNamespace(
                pending=SimpleNamespace(
                    list=lambda _account: [
                        {"signalId": "queued-signal", "symbol": "INFY", "createdAt": "2026-09-07T09:35:00+05:30"}
                    ]
                ),
                orders=SimpleNamespace(
                    for_signal=lambda _account, signal_id: [
                        {
                            "orderId": "order-1",
                            "symbol": "TCS",
                            "status": "FILLED",
                            "createdAt": "2026-09-07T09:40:00+05:30",
                        }
                    ]
                    if signal_id == "filled-signal"
                    else []
                ),
            ),
        )

        with (
            patch.object(runtime, "configured_deployments", return_value=deployments),
            patch.object(runtime, "worker_statuses", return_value=workers),
            patch.object(runtime, "paper_broker", return_value=broker),
        ):
            lifecycle = runtime.strategy_lifecycles("NSE")

        self.assertEqual([item["paper"]["status"] for item in lifecycle], ["PLACED", "QUEUED"])
        self.assertEqual(lifecycle[0]["paper"]["orderId"], "order-1")
        self.assertEqual(lifecycle[1]["paper"]["symbol"], "INFY")


if __name__ == "__main__":
    unittest.main()
