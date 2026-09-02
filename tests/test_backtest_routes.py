"""v2 backtest and settings routes, exercised directly with in-memory fakes (no HTTP client needed)."""

from __future__ import annotations

import unittest
import uuid
from datetime import date
from typing import Any, Callable

from fastapi import HTTPException

from backend.api.backtest_routes import BacktestCreateRequest, BacktestServices, create_backtest_router
from backend.api.settings_routes import create_settings_router
from backend.backtest.engine import BacktestRequest
from backend.data.database import DatabaseUnavailable
from backend.strategies import STRATEGIES


class FakeRuns:
    def __init__(self) -> None:
        self.records: dict[str, dict[str, Any]] = {}

    def create(self, **values: Any) -> dict[str, Any]:
        run_id = str(uuid.uuid4())
        record = {"runId": run_id, "status": "QUEUED", "cancelRequested": False, **{_camel(key): value for key, value in values.items()}}
        record["symbolsTotal"] = len(values["symbols"])
        self.records[run_id] = record
        return dict(record)

    def get(self, run_id: str) -> dict[str, Any]:
        uuid.UUID(str(run_id))
        return dict(self.records[run_id])

    def list(self, market: str | None = None, *, limit: int = 50) -> list[dict[str, Any]]:
        return [dict(item) for item in self.records.values() if market is None or item["market"] == market][:limit]

    def request_cancel(self, run_id: str) -> dict[str, Any]:
        self.records[run_id]["cancelRequested"] = True
        return dict(self.records[run_id])


class FakeTrades:
    def __init__(self) -> None:
        self.rows: dict[str, list[dict[str, Any]]] = {}

    def list(self, run_id: str, *, symbol: str | None = None, limit: int = 500, offset: int = 0) -> list[dict[str, Any]]:
        rows = [row for row in self.rows.get(run_id, []) if symbol is None or row["symbol"] == symbol]
        return rows[offset : offset + limit]

    def count(self, run_id: str) -> int:
        return len(self.rows.get(run_id, []))


class FakeRunner:
    def __init__(self, runs: FakeRuns) -> None:
        self.runs = runs
        self.submitted: list[BacktestRequest] = []

    def submit(self, request: BacktestRequest) -> None:
        self.submitted.append(request)

    def cancel(self, run_id: str) -> dict[str, Any]:
        return self.runs.request_cancel(run_id)


def _camel(name: str) -> str:
    head, *rest = name.split("_")
    return head + "".join(part.title() for part in rest)


def endpoints(router) -> dict[str, Callable[..., Any]]:
    found: dict[str, Callable[..., Any]] = {}
    for route in router.routes:
        for method in route.methods:
            found[f"{method} {route.path}"] = route.endpoint
    return found


class BacktestRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.runs = FakeRuns()
        self.trades = FakeTrades()
        self.runner = FakeRunner(self.runs)
        services = BacktestServices(registry=STRATEGIES, runs=lambda: self.runs, trades=lambda: self.trades, runner=lambda: self.runner)
        self.api = endpoints(create_backtest_router(services))

    def _create(self, **overrides: Any) -> dict[str, Any]:
        payload = {"market": "NSE", "strategyId": "ema_vwap_strong_buy", "symbols": ["reliance", "TCS", " tcs "], "timeframe": "5m", "startDate": date(2026, 8, 1), "endDate": date(2026, 8, 31), "configuration": {"target_pct": 1.5}, "execution": {"stopLossPct": 1.0}}
        payload.update(overrides)
        return self.api["POST /v2/backtests"](BacktestCreateRequest(**payload))

    def test_create_validates_through_the_registry_and_queues_a_background_job(self) -> None:
        record = self._create()
        self.assertEqual(record["status"], "QUEUED")
        self.assertEqual(record["symbols"], ["RELIANCE", "TCS"])
        self.assertEqual(record["strategyVersion"], STRATEGIES.get("ema_vwap_strong_buy").version)
        self.assertEqual(record["configurationSnapshot"]["target_pct"], 1.5)
        self.assertEqual(record["configurationSnapshot"]["ema_fast"], 9)
        self.assertEqual(record["executionSettings"]["stopLossPct"], 1.0)
        self.assertEqual(len(self.runner.submitted), 1)
        submitted = self.runner.submitted[0]
        self.assertEqual(submitted.run_id, record["runId"])
        self.assertEqual(submitted.execution.stop_loss_pct, 1.0)
        self.assertEqual(submitted.configuration, record["configurationSnapshot"])

    def test_create_rejects_unknown_strategy_market_timeframe_config_and_dates(self) -> None:
        for overrides in (
            {"strategyId": "nope"},
            {"timeframe": "1h"},
            {"configuration": {"ema_fast": 50, "ema_slow": 20}},
            {"configuration": {"mystery": 1}},
            {"execution": {"stopLossPct": 500}},
            {"startDate": date(2026, 9, 1), "endDate": date(2026, 8, 1)},
            {"symbols": ["   "]},
        ):
            with self.assertRaises(HTTPException, msg=str(overrides)) as caught:
                self._create(**overrides)
            self.assertEqual(caught.exception.status_code, 422)
        self.assertEqual(self.runner.submitted, [])

    def test_list_get_cancel_and_trades(self) -> None:
        record = self._create()
        run_id = record["runId"]
        self.assertEqual([item["runId"] for item in self.api["GET /v2/backtests"](market="nse", limit=10)["runs"]], [run_id])
        self.assertEqual(self.api["GET /v2/backtests"](market="CRYPTO", limit=10)["runs"], [])
        self.assertEqual(self.api["GET /v2/backtests/{run_id}"](run_id)["runId"], run_id)
        with self.assertRaises(HTTPException) as missing:
            self.api["GET /v2/backtests/{run_id}"](str(uuid.uuid4()))
        self.assertEqual(missing.exception.status_code, 404)
        with self.assertRaises(HTTPException) as malformed:
            self.api["GET /v2/backtests/{run_id}"]("not-a-uuid")
        self.assertEqual(malformed.exception.status_code, 404)
        self.assertTrue(self.api["DELETE /v2/backtests/{run_id}"](run_id)["cancelRequested"])
        self.trades.rows[run_id] = [{"symbol": "TCS", "lotId": f"TCS-Cycle1-Lot{index}"} for index in range(1, 4)] + [{"symbol": "RELIANCE", "lotId": "RELIANCE-Cycle1-Lot1"}]
        page = self.api["GET /v2/backtests/{run_id}/trades"](run_id, symbol="tcs", limit=2, offset=1)
        self.assertEqual([row["lotId"] for row in page["trades"]], ["TCS-Cycle1-Lot2", "TCS-Cycle1-Lot3"])
        self.assertEqual(page["total"], 4)

    def test_routes_fail_closed_without_a_database(self) -> None:
        def unavailable() -> Any:
            raise DatabaseUnavailable("The platform database is not configured")

        services = BacktestServices(registry=STRATEGIES, runs=unavailable, trades=unavailable, runner=unavailable)
        api = endpoints(create_backtest_router(services))
        with self.assertRaises(HTTPException) as caught:
            api["GET /v2/backtests"](market=None, limit=10)
        self.assertEqual(caught.exception.status_code, 503)
        with self.assertRaises(HTTPException) as created:
            api["POST /v2/backtests"](BacktestCreateRequest(market="NSE", strategyId="ema_vwap_strong_buy", symbols=["TCS"], startDate=date(2026, 8, 1), endDate=date(2026, 8, 2)))
        self.assertEqual(created.exception.status_code, 503)


class SettingsRouteTests(unittest.TestCase):
    def test_strategy_catalogue_drives_dropdowns_and_dynamic_settings(self) -> None:
        api = endpoints(create_settings_router(STRATEGIES))
        payload = api["GET /v2/strategies"](market="crypto")
        self.assertEqual(payload["markets"], ["NSE", "CRYPTO"])
        self.assertEqual([item["strategyId"] for item in payload["strategies"]], ["ema_vwap_strong_buy"])
        schema = payload["strategies"][0]["configSchema"]
        self.assertEqual(schema["ema_fast"], {"type": "integer", "default": 9, "minimum": 1, "maximum": 499, "label": "Fast EMA length"})
        self.assertEqual(payload["strategies"][0]["defaults"]["target_pct"], 1.0)
        with self.assertRaises(HTTPException):
            api["GET /v2/strategies"](market="FOREX")
