"""Single bounded coordinator for walk-forward training selection and unseen tests."""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from contextlib import AbstractContextManager
from types import TracebackType
from typing import Any

from backend.backtest.engine import BacktestRequest, ExecutionSettings
from backend.backtest.jobs import BacktestJobRunner, BacktestQueueFull
from backend.data.repositories import WalkForwardValidationRepository
from backend.research.walk_forward import aggregate_unseen_metrics, rank_completed_candidates

logger = logging.getLogger("opendelta.research.walk_forward")
TERMINAL_RUNS = {"COMPLETE", "FAILED", "CANCELLED", "INTERRUPTED"}


class WalkForwardQueueReservation(AbstractContextManager["WalkForwardQueueReservation"]):
    def __init__(self, runner: WalkForwardJobRunner) -> None:
        self.runner = runner
        self.used = False

    def __enter__(self) -> WalkForwardQueueReservation:
        return self

    def submit(self, validation_id: str) -> None:
        if self.used:
            raise RuntimeError("Walk-forward reservation is no longer active")
        self.runner._submit_reserved(validation_id)
        self.used = True

    def release(self) -> None:
        if not self.used:
            self.runner._release_reservation()
            self.used = True

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        if not self.used:
            self.release()


class WalkForwardJobRunner:
    """Coordinates validations without creating a thread or process for each child run."""

    def __init__(
        self,
        repository: WalkForwardValidationRepository,
        backtests: BacktestJobRunner,
        *,
        poll_seconds: float = 1.0,
        max_pending: int = 20,
        lease_factory: Callable[[str], AbstractContextManager] | None = None,
    ) -> None:
        self.repository = repository
        self.backtests = backtests
        self.poll_seconds = poll_seconds
        self.max_pending = max_pending
        self.lease_factory = lease_factory
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="walk-forward")
        self._futures: dict[str, Future[None]] = {}
        self._reserved = 0
        self._lock = threading.Lock()

    def submit(self, validation_id: str) -> None:
        with self.reserve() as reservation:
            reservation.submit(validation_id)

    def reserve(self) -> WalkForwardQueueReservation:
        with self._lock:
            if len(self._futures) + self._reserved >= self.max_pending:
                raise BacktestQueueFull(f"Walk-forward queue limit {self.max_pending} reached")
            self._reserved += 1
        return WalkForwardQueueReservation(self)

    def _submit_reserved(self, validation_id: str) -> None:
        with self._lock:
            if self._reserved < 1:
                raise RuntimeError("Walk-forward queue reservation was lost")
            self._reserved -= 1
            if validation_id in self._futures:
                return
            self._futures[validation_id] = self._executor.submit(self._execute, validation_id)

    def _release_reservation(self) -> None:
        with self._lock:
            self._reserved = max(0, self._reserved - 1)

    def _execute(self, validation_id: str) -> None:
        lease = self.lease_factory(validation_id) if self.lease_factory else None
        try:
            if lease is not None:
                with lease as ownership:
                    if not getattr(ownership, "acquired", True):
                        return
                    self._execute_owned(validation_id)
                return
            self._execute_owned(validation_id)
        except Exception as error:  # noqa: BLE001 - isolate one validation from the queue
            logger.exception("Walk-forward validation %s failed", validation_id)
            try:
                validation = self.repository.get(validation_id)
                for fold in validation["folds"]:
                    if fold["status"] not in {"COMPLETE", "FAILED", "CANCELLED"}:
                        self.repository.update_fold(fold["foldId"], status="FAILED", error=str(error))
            except Exception:  # noqa: BLE001 - original failure remains authoritative
                logger.exception("Could not persist walk-forward failure for %s", validation_id)
        finally:
            with self._lock:
                self._futures.pop(validation_id, None)

    def _execute_owned(self, validation_id: str) -> None:
        for position in range(1, self.repository.get(validation_id)["foldCount"] + 1):
            validation = self.repository.get(validation_id)
            fold = validation["folds"][position - 1]
            if validation["cancelRequested"]:
                self._cancel_fold(fold)
                continue
            if fold["status"] in {"COMPLETE", "FAILED", "CANCELLED"}:
                continue
            self.repository.update_fold(fold["foldId"], status="TRAINING")
            fold = self._wait_for_training(validation_id, position)
            validation = self.repository.get(validation_id)
            if validation["cancelRequested"]:
                self._cancel_fold(fold)
                continue
            ranked = rank_completed_candidates(
                fold["trainingCandidates"],
                objective=validation["rankingObjective"],
                minimum_trades=validation["minimumRequiredTrades"],
            )
            if not ranked:
                self.repository.update_fold(
                    fold["foldId"],
                    status="FAILED",
                    error="No completed training candidate met the minimum-trades requirement",
                )
                continue
            winner = ranked[0]
            test_run = self.repository.create_test_run(fold_id=fold["foldId"], candidate=winner)
            request = BacktestRequest(
                run_id=test_run["runId"], market=test_run["market"], strategy_id=test_run["strategyId"],
                strategy_source_id=test_run["strategySourceId"], symbols=test_run["symbols"],
                timeframe=test_run["timeframe"], start_date=_date(test_run["startDate"]),
                end_date=_date(test_run["endDate"]), configuration=test_run["configurationSnapshot"],
                execution=ExecutionSettings.from_mapping(
                    test_run["executionSettings"], whole_units=test_run["market"] == "NSE"
                ),
            )
            self._submit_when_available(request, validation_id)
            terminal = self._wait_for_run(validation_id, test_run["runId"])
            if terminal["status"] == "COMPLETE":
                self.repository.update_fold(fold["foldId"], status="COMPLETE")
            elif terminal["status"] == "CANCELLED":
                self.repository.update_fold(fold["foldId"], status="CANCELLED")
            else:
                self.repository.update_fold(
                    fold["foldId"], status="FAILED", error=terminal.get("error") or "Unseen test run failed"
                )
        finished = self.repository.get(validation_id)
        unseen = [
            fold["testRun"]["metrics"]
            for fold in finished["folds"]
            if fold["status"] == "COMPLETE" and fold.get("testRun", {}).get("metrics") is not None
        ]
        self.repository.finish(validation_id, aggregate_unseen_metrics(unseen))

    def _wait_for_training(self, validation_id: str, position: int) -> dict[str, Any]:
        while True:
            validation = self.repository.get(validation_id)
            fold = validation["folds"][position - 1]
            if validation["cancelRequested"]:
                return fold
            if all(candidate["run"]["status"] in TERMINAL_RUNS for candidate in fold["trainingCandidates"]):
                return fold
            time.sleep(self.poll_seconds)

    def _wait_for_run(self, validation_id: str, run_id: str) -> dict[str, Any]:
        while True:
            validation = self.repository.get(validation_id)
            for fold in validation["folds"]:
                run = fold.get("testRun")
                if run and run["runId"] == run_id:
                    if validation["cancelRequested"] and run["status"] not in TERMINAL_RUNS:
                        self.backtests.cancel(run_id)
                    if run["status"] in TERMINAL_RUNS:
                        return run
            time.sleep(self.poll_seconds)

    def _submit_when_available(self, request: BacktestRequest, validation_id: str) -> None:
        while True:
            if self.repository.get(validation_id)["cancelRequested"]:
                self.backtests.cancel(request.run_id)
                self.backtests.runs.finish(request.run_id, status="CANCELLED", metrics=None)
                return
            try:
                self.backtests.submit(request)
                return
            except BacktestQueueFull:
                time.sleep(self.poll_seconds)

    def _cancel_fold(self, fold: dict[str, Any]) -> None:
        if fold["status"] in {"COMPLETE", "FAILED", "CANCELLED"}:
            return
        for candidate in fold["trainingCandidates"]:
            if candidate["run"]["status"] not in TERMINAL_RUNS:
                self.backtests.cancel(candidate["run"]["runId"])
        if fold.get("testRun") and fold["testRun"]["status"] not in TERMINAL_RUNS:
            self.backtests.cancel(fold["testRun"]["runId"])
        self.repository.update_fold(fold["foldId"], status="CANCELLED")

    def cancel(self, validation_id: str) -> dict[str, Any]:
        validation = self.repository.request_cancel(validation_id)
        for fold in validation["folds"]:
            self._cancel_fold(fold)
        return self.repository.get(validation_id)

    def active_validation_ids(self) -> list[str]:
        with self._lock:
            return sorted(self._futures)

    def shutdown(self) -> None:
        self._executor.shutdown(wait=False, cancel_futures=True)


def _date(value: str):
    from datetime import date

    return date.fromisoformat(value)
