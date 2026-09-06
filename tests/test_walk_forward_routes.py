from __future__ import annotations

import uuid
from copy import deepcopy
from datetime import date, timedelta
from typing import Any

import pytest
from backend.api.research_routes import ResearchServices
from backend.api.walk_forward_routes import (
    WalkForwardPreviewRequest,
    WalkForwardServices,
    WalkForwardSubmissionRequest,
    create_walk_forward_router,
)
from backend.backtest.engine import ExecutionSettings
from backend.strategies import STRATEGIES
from fastapi import HTTPException


class Reservation:
    def __init__(self, owner: Any, count: int | None = None) -> None:
        self.owner = owner
        self.count = count

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def submit(self, value):
        if isinstance(value, list):
            assert len(value) == self.count
            self.owner.submitted.extend(value)
        else:
            self.owner.submitted.append(value)


class FakeBacktests:
    def __init__(self) -> None:
        self.reservations: list[int] = []
        self.submitted: list[Any] = []

    def reserve(self, count: int) -> Reservation:
        self.reservations.append(count)
        return Reservation(self, count)


class FakeCoordinator:
    def __init__(self) -> None:
        self.reserved = 0
        self.submitted: list[str] = []

    def reserve(self) -> Reservation:
        self.reserved += 1
        return Reservation(self)

    def cancel(self, validation_id: str) -> dict[str, Any]:
        return {"validationId": validation_id, "status": "CANCELLED"}


class FakeExperiments:
    def __init__(self) -> None:
        strategy = STRATEGIES.get("rsi_dip_ladder_v1")
        execution = ExecutionSettings(whole_units=True).public()
        self.row = {
            "experimentId": str(uuid.uuid4()), "market": "NSE",
            "strategyId": strategy.strategy_id, "strategyVersion": strategy.version,
            "strategySourceId": None, "timeframe": "5m",
            "variants": [
                {
                    "variantId": str(uuid.uuid4()), "position": position, "name": f"candidate-{position}",
                    "configuration": strategy.resolve({"rsi_low": rsi}), "execution": execution,
                }
                for position, rsi in enumerate((25, 30), start=1)
            ],
        }

    def get(self, experiment_id: str) -> dict[str, Any]:
        if experiment_id != self.row["experimentId"]:
            raise KeyError(experiment_id)
        return deepcopy(self.row)


class FakeValidations:
    def __init__(self) -> None:
        self.rows: list[dict[str, Any]] = []
        self.by_key: dict[str, dict[str, Any]] = {}

    def nse_sessions(self, start: date, end: date) -> list[date]:
        return [
            start + timedelta(days=offset)
            for offset in range((end - start).days + 1)
            if (start + timedelta(days=offset)).weekday() < 5
        ]

    def get_by_idempotency_key(self, key: str):
        return deepcopy(self.by_key.get(key))

    def create_generated(self, **values: Any):
        validation_id = str(uuid.uuid4())
        folds = []
        for fold in values["folds"]:
            candidates = []
            for candidate in values["candidates"]:
                run_id = str(uuid.uuid4())
                candidates.append({
                    **deepcopy(candidate),
                    "run": {
                        "runId": run_id, "market": values["market"],
                        "strategyId": values["strategy_id"], "strategyVersion": values["strategy_version"],
                        "strategySourceId": values["strategy_source_id"], "symbols": list(values["symbols"]),
                        "timeframe": values["timeframe"], "startDate": fold["trainingStart"],
                        "endDate": fold["trainingEnd"], "configurationSnapshot": candidate["configuration"],
                        "executionSettings": candidate["execution"], "status": "QUEUED", "metrics": None,
                    },
                })
            folds.append({**fold, "foldId": str(uuid.uuid4()), "status": "QUEUED", "trainingCandidates": candidates})
        row = {
            "validationId": validation_id, "previewHash": values["preview_hash"],
            "foldCount": values["workload"]["foldCount"], "folds": folds,
        }
        self.rows.append(row)
        self.by_key[values["idempotency_key"]] = row
        return deepcopy(row), True

    def list(self, _market=None, *, limit=50):
        return deepcopy(self.rows[:limit])

    def get(self, validation_id: str):
        return deepcopy(next(row for row in self.rows if row["validationId"] == validation_id))


def endpoint_map(router):
    return {
        f"{method} {route.path}": route.endpoint
        for route in router.routes
        for method in route.methods
    }


@pytest.fixture
def setup_api():
    experiments = FakeExperiments()
    validations = FakeValidations()
    backtests = FakeBacktests()
    coordinator = FakeCoordinator()
    research = ResearchServices(
        registry=STRATEGIES, experiments=lambda: experiments, runner=lambda: backtests
    )
    services = WalkForwardServices(
        registry=STRATEGIES, validations=lambda: validations, experiments=lambda: experiments,
        backtests=lambda: backtests, coordinator=lambda: coordinator, research=research,
    )
    return endpoint_map(create_walk_forward_router(services)), experiments, validations, backtests, coordinator


def payload(experiments: FakeExperiments, **overrides: Any) -> dict[str, Any]:
    values: dict[str, Any] = {
        "name": "RSI rolling validation", "mode": "ROLLING", "market": "NSE",
        "strategyId": "rsi_dip_ladder_v1", "strategyVersion": "1.0.0",
        "symbols": ["TCS", "INFY"], "timeframe": "5m",
        "startDate": date(2026, 8, 3), "endDate": date(2026, 8, 31),
        "trainingWindow": 5, "testingWindow": 2, "step": 2, "maximumFolds": 3,
        "candidateExperimentId": experiments.row["experimentId"],
        "rankingObjective": "RETURN_DRAWDOWN", "minimumRequiredTrades": 2,
        "transactionCostBps": 10, "slippageBps": 2,
    }
    values.update(overrides)
    return values


def test_preview_is_deterministic_and_has_no_write_or_execution_side_effect(setup_api) -> None:
    api, experiments, validations, backtests, coordinator = setup_api
    request = WalkForwardPreviewRequest(**payload(experiments))
    first = api["POST /v2/research/walk-forward/preview"](request)
    second = api["POST /v2/research/walk-forward/preview"](request)
    assert first["previewHash"] == second["previewHash"]
    assert first["foldCount"] == 3
    assert first["candidateCount"] == 2
    assert first["childRunCount"] == 9
    assert first["estimatedSymbolRuns"] == 18
    assert first["folds"][0]["testingStart"] > first["folds"][0]["trainingEnd"]
    assert validations.rows == []
    assert backtests.reservations == []
    assert coordinator.reserved == 0


def test_submission_regenerates_preview_and_submits_exact_training_runs(setup_api) -> None:
    api, experiments, validations, backtests, coordinator = setup_api
    values = payload(experiments)
    preview = api["POST /v2/research/walk-forward/preview"](WalkForwardPreviewRequest(**values))
    request = WalkForwardSubmissionRequest(
        **values, previewHash=preview["previewHash"], idempotencyKey="walk-forward:stable:1"
    )
    result = api["POST /v2/research/walk-forward/from-preview"](request)
    assert len(validations.rows) == 1
    assert backtests.reservations == [6]
    assert len(backtests.submitted) == 6
    assert coordinator.submitted == [result["validationId"]]
    assert all(item.execution.transaction_cost_bps == 10 for item in backtests.submitted)


def test_submission_rejects_stale_hash_before_creating_anything(setup_api) -> None:
    api, experiments, validations, backtests, coordinator = setup_api
    values = payload(experiments)
    request = WalkForwardSubmissionRequest(
        **values, previewHash="sha256:" + "0" * 64, idempotencyKey="walk-forward:stale:1"
    )
    with pytest.raises(HTTPException) as captured:
        api["POST /v2/research/walk-forward/from-preview"](request)
    assert captured.value.status_code == 409
    assert validations.rows == []
    assert backtests.reservations == []
    assert coordinator.reserved == 0


def test_candidate_identity_mismatch_fails_closed(setup_api) -> None:
    api, experiments, validations, *_ = setup_api
    experiments.row["strategyVersion"] = "9.9.9"
    with pytest.raises(HTTPException) as captured:
        api["POST /v2/research/walk-forward/preview"](
            WalkForwardPreviewRequest(**payload(experiments))
        )
    assert captured.value.status_code == 422
    assert "immutable strategy context" in captured.value.detail
    assert validations.rows == []


def test_nse_preview_fails_closed_without_market_calendar(setup_api) -> None:
    api, experiments, validations, *_ = setup_api
    validations.nse_sessions = lambda _start, _end: []  # type: ignore[method-assign]
    with pytest.raises(HTTPException, match="market-session calendar"):
        api["POST /v2/research/walk-forward/preview"](
            WalkForwardPreviewRequest(**payload(experiments))
        )
