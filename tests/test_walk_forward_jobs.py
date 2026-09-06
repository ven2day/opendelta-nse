from __future__ import annotations

import time
from copy import deepcopy
from typing import Any

from backend.research.walk_forward_jobs import WalkForwardJobRunner


class FakeRuns:
    def finish(self, _run_id: str, *, status: str, metrics: Any) -> None:
        assert status == "CANCELLED"
        assert metrics is None


class FakeBacktests:
    def __init__(self, repository: FakeRepository) -> None:
        self.repository = repository
        self.runs = FakeRuns()
        self.submitted = []
        self.cancelled: list[str] = []

    def submit(self, request) -> None:
        self.submitted.append(request)
        self.repository.test_run["status"] = "COMPLETE"
        self.repository.test_run["metrics"] = {
            "realizedPnl": 4, "maximumDrawdown": 2, "completedTrades": 1, "winRate": 100,
        }

    def cancel(self, run_id: str) -> None:
        if run_id not in self.cancelled:
            self.cancelled.append(run_id)


class FakeRepository:
    def __init__(self, *, training_status: str = "COMPLETE") -> None:
        self.test_run: dict[str, Any] | None = None
        self.finished = None
        self.cancel_requested = False
        self.fold_status = "QUEUED"
        self.fold_error = None
        self.training_status = training_status
        self.winner = None

    def get(self, _validation_id: str) -> dict[str, Any]:
        candidates = [
            self._candidate("winner", 1, pnl=10, drawdown=2),
            self._candidate("overfit-loser", 2, pnl=5, drawdown=1),
        ]
        return {
            "validationId": "validation-1", "foldCount": 1,
            "cancelRequested": self.cancel_requested, "rankingObjective": "NET_PNL",
            "minimumRequiredTrades": 1,
            "folds": [{
                "foldId": "fold-1", "status": self.fold_status, "error": self.fold_error,
                "trainingCandidates": candidates, "testRun": deepcopy(self.test_run),
            }],
        }

    def _candidate(self, name: str, position: int, *, pnl: float, drawdown: float) -> dict[str, Any]:
        return {
            "variantId": f"variant-{position}", "name": name, "position": position,
            "configuration": {"choice": position},
            "execution": {"wholeUnits": False, "transactionCostBps": 2, "slippageBps": 1},
            "run": {
                "runId": f"train-{position}", "status": self.training_status,
                "metrics": {
                    "realizedPnl": pnl, "maximumDrawdown": drawdown,
                    "completedTrades": 2, "winRate": 50,
                },
            },
        }

    def update_fold(self, _fold_id: str, *, status: str, error: str | None = None) -> None:
        self.fold_status = status
        self.fold_error = error

    def create_test_run(self, *, fold_id: str, candidate: dict[str, Any]) -> dict[str, Any]:
        assert fold_id == "fold-1"
        self.winner = candidate["name"]
        self.test_run = {
            "runId": "test-1", "market": "CRYPTO", "strategyId": "strategy",
            "strategyVersion": "1", "strategySourceId": None, "symbols": ["BTC-USDT"],
            "timeframe": "5m", "startDate": "2026-01-04", "endDate": "2026-01-05",
            "configurationSnapshot": candidate["configuration"],
            "executionSettings": candidate["execution"], "status": "QUEUED", "metrics": None,
        }
        return deepcopy(self.test_run)

    def finish(self, _validation_id: str, aggregate: dict[str, Any]) -> None:
        self.finished = aggregate

    def request_cancel(self, _validation_id: str) -> dict[str, Any]:
        self.cancel_requested = True
        return self.get("validation-1")


def test_coordinator_selects_training_winner_then_runs_exact_unseen_configuration() -> None:
    repository = FakeRepository()
    backtests = FakeBacktests(repository)
    runner = WalkForwardJobRunner(repository, backtests, poll_seconds=0.001)
    try:
        runner.submit("validation-1")
        _wait(runner)
    finally:
        runner.shutdown()

    assert repository.winner == "winner"
    assert len(backtests.submitted) == 1
    assert backtests.submitted[0].configuration == {"choice": 1}
    assert repository.fold_status == "COMPLETE"
    assert repository.finished["netPnl"] == 4


def test_cancellation_is_idempotent_and_never_deletes_completed_children() -> None:
    repository = FakeRepository(training_status="QUEUED")
    backtests = FakeBacktests(repository)
    runner = WalkForwardJobRunner(repository, backtests, poll_seconds=0.001)
    try:
        first = runner.cancel("validation-1")
        second = runner.cancel("validation-1")
    finally:
        runner.shutdown()
    assert first["cancelRequested"] is True
    assert second["cancelRequested"] is True
    assert sorted(backtests.cancelled) == ["train-1", "train-2"]
    assert repository.fold_status == "CANCELLED"


def test_cancellation_preserves_completed_fold_results() -> None:
    repository = FakeRepository()
    repository.fold_status = "COMPLETE"
    backtests = FakeBacktests(repository)
    runner = WalkForwardJobRunner(repository, backtests, poll_seconds=0.001)
    try:
        runner.cancel("validation-1")
    finally:
        runner.shutdown()
    assert backtests.cancelled == []
    assert repository.fold_status == "COMPLETE"


def test_coordinator_queue_reservation_releases_when_unused() -> None:
    repository = FakeRepository()
    runner = WalkForwardJobRunner(repository, FakeBacktests(repository), max_pending=1)
    try:
        with runner.reserve():
            pass
        with runner.reserve():
            pass
    finally:
        runner.shutdown()


def _wait(runner: WalkForwardJobRunner) -> None:
    deadline = time.monotonic() + 2
    while runner.active_validation_ids() and time.monotonic() < deadline:
        time.sleep(0.005)
    assert runner.active_validation_ids() == []
