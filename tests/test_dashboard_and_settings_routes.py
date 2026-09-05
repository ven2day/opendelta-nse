"""Dashboard aggregation and strategy-configuration routes, with fakes."""

from __future__ import annotations

import unittest
import uuid
from typing import Any

from backend.api.dashboard_routes import create_dashboard_router
from backend.api.settings_routes import StrategyConfigRequest, StrategyDeploymentRequest, create_settings_router
from backend.api.signal_routes import create_signal_router
from backend.data.database import DatabaseUnavailable
from backend.strategies import STRATEGIES
from fastapi import HTTPException

from test_backtest_routes import endpoints


class FakeConfigs:
    def __init__(self) -> None:
        self.rows: list[dict[str, Any]] = []

    def active(self, market: str, strategy_id: str):
        return next(
            (dict(r) for r in self.rows if r["market"] == market and r["strategyId"] == strategy_id and r["active"]),
            None,
        )

    def save(self, *, market, strategy_id, strategy_version, name, configuration, risk_settings, activate):
        if activate:
            for row in self.rows:
                if row["market"] == market and row["strategyId"] == strategy_id:
                    row["active"] = False
        row = {
            "configId": str(uuid.uuid4()),
            "market": market,
            "strategyId": strategy_id,
            "strategyVersion": strategy_version,
            "name": name,
            "configuration": dict(configuration),
            "riskSettings": dict(risk_settings),
            "active": activate,
        }
        self.rows.append(row)
        return dict(row)

    def list(self, market=None):
        return [dict(r) for r in self.rows if market is None or r["market"] == market]


class FakeDeployments:
    def __init__(self) -> None:
        self.rows: dict[tuple[str, str], dict[str, Any]] = {}

    def get(self, market, strategy_id):
        row = self.rows.get((market, strategy_id))
        return dict(row) if row else None

    def save(self, *, market, strategy_id, strategy_version, config_id, timeframe, mode):
        row = {"deploymentId": str(uuid.uuid4()), "market": market, "strategyId": strategy_id, "strategyVersion": strategy_version, "configId": config_id, "timeframe": timeframe, "mode": mode, "source": "DATABASE"}
        self.rows[(market, strategy_id)] = row
        return dict(row)


class SettingsRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.configs = FakeConfigs()
        self.deployments = FakeDeployments()
        self.changed: list[str] = []
        self.api = endpoints(create_settings_router(STRATEGIES, configs=lambda: self.configs, deployments=lambda: self.deployments, deployment_changed=self.changed.append))

    def test_catalogue_includes_risk_defaults_for_dynamic_forms(self) -> None:
        payload = self.api["GET /v2/strategies"](market=None)
        self.assertEqual(payload["riskDefaults"]["sizingMode"], "FIXED_QUANTITY")
        self.assertEqual(payload["riskDefaults"]["maximumDailyTrades"], 5)
        self.assertEqual(payload["riskDefaults"]["maximumDailyLossPct"], 2.0)
        self.assertEqual(payload["riskSchema"]["maximumOpenPositions"]["minimum"], 1)
        self.assertEqual(payload["riskSchema"]["maximumTotalExposurePct"]["maximum"], 100.0)
        self.assertEqual(payload["strategies"][0]["configSchema"]["target_pct"]["default"], 1.0)

    def test_save_validates_through_the_strategy_and_activates_per_market(self) -> None:
        saved = self.api["POST /v2/strategies/{strategy_id}/config"](
            "ema_vwap_strong_buy",
            StrategyConfigRequest(
                market="NSE",
                name="tight",
                configuration={"target_pct": 0.5},
                riskSettings={"stopLossPct": 1.0, "initialQuantity": 50},
            ),
        )
        self.assertTrue(saved["active"])
        self.assertEqual(saved["configuration"]["target_pct"], 0.5)
        self.assertEqual(saved["configuration"]["ema_fast"], 9)  # full snapshot, not just the override
        self.assertEqual(saved["riskSettings"]["stopLossPct"], 1.0)
        self.assertEqual(saved["strategyVersion"], STRATEGIES.get("ema_vwap_strong_buy").version)
        again = self.api["POST /v2/strategies/{strategy_id}/config"](
            "ema_vwap_strong_buy", StrategyConfigRequest(market="NSE", name="loose", configuration={"target_pct": 2.0})
        )
        self.assertTrue(again["active"])
        self.assertEqual([row["active"] for row in self.configs.rows], [False, True])
        effective = self.api["GET /v2/strategies/{strategy_id}/config"]("ema_vwap_strong_buy", market="nse")
        self.assertEqual(effective["effectiveConfiguration"]["target_pct"], 2.0)
        self.assertEqual(effective["active"]["name"], "loose")
        self.assertEqual(len(effective["all"]), 2)
        crypto = self.api["GET /v2/strategies/{strategy_id}/config"]("ema_vwap_strong_buy", market="CRYPTO")
        self.assertIsNone(crypto["active"])
        self.assertFalse(crypto["effectiveRiskSettings"]["wholeUnits"])

    def test_invalid_configuration_unknown_strategy_and_missing_storage(self) -> None:
        with self.assertRaises(HTTPException) as bad:
            self.api["POST /v2/strategies/{strategy_id}/config"](
                "ema_vwap_strong_buy",
                StrategyConfigRequest(market="NSE", configuration={"ema_fast": 50, "ema_slow": 10}),
            )
        self.assertEqual(bad.exception.status_code, 422)
        with self.assertRaises(HTTPException) as risk:
            self.api["POST /v2/strategies/{strategy_id}/config"](
                "ema_vwap_strong_buy", StrategyConfigRequest(market="NSE", riskSettings={"stopLossPct": 500})
            )
        self.assertEqual(risk.exception.status_code, 422)
        with self.assertRaises(HTTPException) as unknown:
            self.api["GET /v2/strategies/{strategy_id}/config"]("nope", market="NSE")
        self.assertEqual(unknown.exception.status_code, 404)

        def unavailable():
            raise DatabaseUnavailable("no database")

        api = endpoints(create_settings_router(STRATEGIES, configs=unavailable))
        with self.assertRaises(HTTPException) as missing:
            api["GET /v2/strategies/{strategy_id}/config"]("ema_vwap_strong_buy", market="NSE")
        self.assertEqual(missing.exception.status_code, 503)
        self.assertEqual(
            len(api["GET /v2/strategies"](market="CRYPTO")["strategies"]), 1
        )  # catalogue never needs the database

    def test_strategy_mode_requires_an_active_config_and_reconciles_immediately(self) -> None:
        current = self.api["GET /v2/strategies/{strategy_id}/deployment"]("ema_vwap_strong_buy", market="CRYPTO")
        self.assertEqual((current["mode"], current["timeframe"]), ("OFF", "5m"))
        with self.assertRaises(HTTPException) as inactive:
            self.api["POST /v2/strategies/{strategy_id}/deployment"]("ema_vwap_strong_buy", StrategyDeploymentRequest(market="CRYPTO", timeframe="5m", mode="PAPER"))
        self.assertEqual(inactive.exception.status_code, 409)
        self.api["POST /v2/strategies/{strategy_id}/config"]("ema_vwap_strong_buy", StrategyConfigRequest(market="CRYPTO", name="paper-default"))
        paper = self.api["POST /v2/strategies/{strategy_id}/deployment"]("ema_vwap_strong_buy", StrategyDeploymentRequest(market="CRYPTO", timeframe="5m", mode="PAPER"))
        self.assertEqual(paper["mode"], "PAPER")
        self.assertIsNotNone(paper["configId"])
        self.assertEqual(self.changed, ["CRYPTO", "CRYPTO"])
        with self.assertRaises(HTTPException) as timeframe:
            self.api["POST /v2/strategies/{strategy_id}/deployment"]("ema_vwap_strong_buy", StrategyDeploymentRequest(market="CRYPTO", timeframe="1d", mode="SIGNALS"))
        self.assertEqual(timeframe.exception.status_code, 422)


class DashboardRouteTests(unittest.TestCase):
    def test_dashboard_aggregates_every_section_and_degrades_per_section(self) -> None:
        overview_markets: list[str] = []

        def overview(market: str):
            overview_markets.append(market)
            return {"dataFreshness": {"status": "FRESH"}}

        def broken(market: str):
            raise DatabaseUnavailable("no database")

        api = endpoints(
            create_dashboard_router(
                overview=overview,
                screener_runs=lambda market: [{"runId": "r1", "market": market, "status": "COMPLETE"}],
                backtest_runs=lambda market: [],
                engine_health=lambda market: {
                    "stored": [],
                    "workers": [{"status": "READY", "strategyId": "rsi_dip_ladder_v1", "timeframe": "1d"}],
                },
                paper_summary=broken,
                paper_positions=lambda market: [],
                active_universe=lambda market: {"name": "Liquid", "symbols": ["TCS"]},
            )
        )
        payload = api["GET /v2/dashboard"](market="crypto")
        self.assertEqual(payload["market"], "CRYPTO")
        self.assertEqual(overview_markets, ["CRYPTO"])
        self.assertEqual(payload["marketData"]["data"]["dataFreshness"]["status"], "FRESH")
        self.assertEqual(payload["screener"]["data"]["latestRun"]["runId"], "r1")
        self.assertEqual(payload["screener"]["data"]["activeUniverse"]["name"], "Liquid")
        self.assertEqual(payload["signalEngine"]["data"]["workers"][0]["status"], "READY")
        self.assertFalse(payload["paper"]["available"])
        self.assertIn("no database", payload["paper"]["error"])
        self.assertTrue(payload["paperOnly"] and not payload["liveOrdersEnabled"])
        with self.assertRaises(HTTPException):
            api["GET /v2/dashboard"](market="FOREX")


class SignalRouteTests(unittest.TestCase):
    def test_strategy_timeframe_filters_and_multiple_worker_health_are_exposed(self) -> None:
        class Signals:
            arguments: dict[str, Any] = {}

            def list(self, market, **arguments):
                self.arguments = {"market": market, **arguments}
                return [{"status": "STRONG_BUY", "strategyId": "rsi_dip_ladder_v1", "timeframe": "1d"}]

        class Statuses:
            def list(self):
                return [{"market": "NSE", "engine": "live-signals-v2:rsi_dip_ladder_v1:1d"}]

        signals = Signals()
        workers = {
            "NSE": [
                {"strategyId": "rsi_dip_ladder_v1", "timeframe": "1d", "status": "READY"},
                {"strategyId": "scalping_v1", "timeframe": "5m", "status": "READY"},
            ],
            "CRYPTO": [],
        }
        api = endpoints(
            create_signal_router(
                signals=lambda: signals,
                engine_status=lambda: Statuses(),
                worker_statuses=lambda market: workers[market],
            )
        )
        payload = api["GET /v2/signals"](
            market="nse",
            status="strong_buy",
            symbol=" m&m ",
            strategy="rsi_dip_ladder_v1",
            timeframe="1d",
            limit=50,
        )
        self.assertEqual(payload["signals"][0]["colour"], "blue")
        self.assertEqual(
            signals.arguments,
            {
                "market": "NSE",
                "status": "STRONG_BUY",
                "symbol": "M&M",
                "strategy_id": "rsi_dip_ladder_v1",
                "timeframe": "1d",
                "limit": 50,
            },
        )
        health = api["GET /v2/signals/health"](market="NSE")
        self.assertEqual(
            [item["strategyId"] for item in health["workers"]["NSE"]], ["rsi_dip_ladder_v1", "scalping_v1"]
        )
