from __future__ import annotations

import unittest
import uuid
from typing import Any

from backend.api.backtest_routes import BacktestCreateRequest
from backend.api.research_routes import ResearchExperimentRequest, ResearchServices, create_research_router


class FakeExperiments:
    def __init__(self) -> None:
        self.rows: list[dict[str, Any]] = []

    def create(self, *, name: str, runs: list[dict[str, Any]]) -> dict[str, Any]:
        row = {"experimentId": str(uuid.uuid4()), "name": name, "market": runs[0]["run"]["market"], "variants": runs}
        self.rows.append(row)
        return row

    def list(self, market: str | None = None, *, limit: int = 50) -> list[dict[str, Any]]:
        return [row for row in self.rows if market is None or row["market"] == market][:limit]

    def get(self, experiment_id: str) -> dict[str, Any]:
        return next(row for row in self.rows if row["experimentId"] == experiment_id)


def endpoints(router):
    return {f"{method} {route.path}": route.endpoint for route in router.routes for method in route.methods}


class ResearchRouteTests(unittest.TestCase):
    def test_experiment_submits_each_variant_through_backtest_contract(self) -> None:
        repository = FakeExperiments()
        submitted: list[BacktestCreateRequest] = []

        def submit(request: BacktestCreateRequest) -> dict[str, Any]:
            submitted.append(request)
            return {
                "runId": str(uuid.uuid4()), "market": request.market, "strategyId": request.strategyId,
                "strategyVersion": "2.0.0", "strategySourceId": request.strategySourceId,
                "configurationSnapshot": request.configuration, "executionSettings": request.execution,
                "timeframe": request.timeframe, "symbols": request.symbols, "startDate": request.startDate,
                "endDate": request.endDate, "status": "QUEUED",
            }

        api = endpoints(create_research_router(ResearchServices(experiments=lambda: repository, submit_backtest=submit)))
        result = api["POST /v2/research/experiments"](ResearchExperimentRequest(
            name="RSI sweep", market="NSE", strategyId="rsi_dip_ladder", symbols=["TCS"],
            timeframe="5m", startDate="2026-08-01", endDate="2026-08-31",
            variants=[
                {"name": "Conservative", "configuration": {"rsi_low": 25}, "execution": {"targetPct": 0.5}},
                {"name": "Baseline", "configuration": {"rsi_low": 30}, "execution": {"targetPct": 0.75}},
            ],
        ))
        self.assertEqual(len(submitted), 2)
        self.assertEqual(submitted[0].configuration, {"rsi_low": 25})
        self.assertEqual(result["name"], "RSI sweep")
        self.assertEqual(len(result["variants"]), 2)

    def test_duplicate_variant_names_are_rejected(self) -> None:
        with self.assertRaises(ValueError):
            ResearchExperimentRequest(
                name="Duplicate", market="NSE", strategyId="x", symbols=["TCS"], timeframe="5m",
                startDate="2026-08-01", endDate="2026-08-31",
                variants=[{"name": "Same"}, {"name": "same"}],
            )


if __name__ == "__main__":
    unittest.main()
