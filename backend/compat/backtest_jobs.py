from __future__ import annotations

import copy
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import Any, Callable


TERMINAL_STATUSES = {"CANCELLED", "COMPLETE", "FAILED"}


class BacktestJobService:
    """Runs bounded long backtests with observable, cancellable state."""

    def __init__(self, maximum_jobs: int = 10) -> None:
        self.maximum_jobs = maximum_jobs
        self._lock = threading.Lock()
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="backtest-job")
        self._jobs: dict[str, dict[str, Any]] = {}

    def start(
        self,
        *,
        symbols_total: int,
        runner: Callable[[Callable[[dict[str, Any]], None], threading.Event], dict[str, Any]],
    ) -> dict[str, Any]:
        job_id = str(uuid.uuid4())
        now = datetime.now().astimezone().isoformat()
        record: dict[str, Any] = {
            "jobId": job_id,
            "status": "QUEUED",
            "createdAt": now,
            "startedAt": None,
            "completedAt": None,
            "elapsedSeconds": 0.0,
            "estimatedRemainingSeconds": None,
            "currentStage": "QUEUED",
            "symbolsCompleted": 0,
            "symbolsTotal": symbols_total,
            "supportSymbolsCompleted": 0,
            "supportSymbolsTotal": 0,
            "candlesProcessed": 0,
            "candidatesFound": 0,
            "acceptedSignals": 0,
            "workersActive": 0,
            "result": None,
            "error": None,
            "cancelEvent": threading.Event(),
            "startedClock": None,
        }
        with self._lock:
            self._prune_locked()
            self._jobs[job_id] = record
        self._executor.submit(self._run, job_id, runner)
        return self.get(job_id)

    def _run(
        self,
        job_id: str,
        runner: Callable[[Callable[[dict[str, Any]], None], threading.Event], dict[str, Any]],
    ) -> None:
        with self._lock:
            record = self._jobs[job_id]
            if record["cancelEvent"].is_set():
                record.update(status="CANCELLED", currentStage="CANCELLED", completedAt=datetime.now().astimezone().isoformat())
                return
            record.update(
                status="RUNNING",
                currentStage="STARTING",
                startedAt=datetime.now().astimezone().isoformat(),
                startedClock=time.perf_counter(),
            )

        def progress(values: dict[str, Any]) -> None:
            with self._lock:
                current = self._jobs.get(job_id)
                if current is None or current["status"] in TERMINAL_STATUSES:
                    return
                for key, value in values.items():
                    if key in current and key not in {"status", "result", "error", "cancelEvent"}:
                        current[key] = value
                self._update_elapsed_locked(current)

        try:
            result = runner(progress, record["cancelEvent"])
            with self._lock:
                current = self._jobs[job_id]
                cancelled = current["cancelEvent"].is_set()
                current.update(
                    status="CANCELLED" if cancelled else "COMPLETE",
                    currentStage="CANCELLED" if cancelled else "COMPLETE",
                    completedAt=datetime.now().astimezone().isoformat(),
                    result=None if cancelled else result,
                    workersActive=0,
                )
                self._update_elapsed_locked(current)
        except Exception as error:  # surfaced through the job status endpoint
            with self._lock:
                current = self._jobs[job_id]
                cancelled = current["cancelEvent"].is_set() or error.__class__.__name__ == "BacktestCancelledError"
                current.update(
                    status="CANCELLED" if cancelled else "FAILED",
                    currentStage="CANCELLED" if cancelled else "FAILED",
                    completedAt=datetime.now().astimezone().isoformat(),
                    error=None if cancelled else str(error),
                    result=None,
                    workersActive=0,
                )
                self._update_elapsed_locked(current)

    def cancel(self, job_id: str) -> dict[str, Any]:
        with self._lock:
            record = self._jobs.get(job_id)
            if record is None:
                raise KeyError(job_id)
            if record["status"] not in TERMINAL_STATUSES:
                record["cancelEvent"].set()
                record["status"] = "CANCELLING"
                record["currentStage"] = "CANCELLING"
            return self._public_locked(record)

    def get(self, job_id: str) -> dict[str, Any]:
        with self._lock:
            record = self._jobs.get(job_id)
            if record is None:
                raise KeyError(job_id)
            self._update_elapsed_locked(record)
            return self._public_locked(record)

    def shutdown(self) -> None:
        self._executor.shutdown(wait=False, cancel_futures=True)

    def _update_elapsed_locked(self, record: dict[str, Any]) -> None:
        started = record.get("startedClock")
        if started is None:
            return
        if record["status"] not in TERMINAL_STATUSES:
            record["elapsedSeconds"] = round(time.perf_counter() - float(started), 2)
        completed = int(record.get("symbolsCompleted", 0))
        total = int(record.get("symbolsTotal", 0))
        if completed > 0 and completed < total:
            rate = float(record["elapsedSeconds"]) / completed
            record["estimatedRemainingSeconds"] = round(rate * (total - completed), 1)
        elif completed >= total and total > 0:
            record["estimatedRemainingSeconds"] = 0.0

    def _public_locked(self, record: dict[str, Any]) -> dict[str, Any]:
        return copy.deepcopy({key: value for key, value in record.items() if key not in {"cancelEvent", "startedClock"}})

    def _prune_locked(self) -> None:
        terminal = [
            item for item in self._jobs.values() if item["status"] in TERMINAL_STATUSES
        ]
        terminal.sort(key=lambda item: str(item.get("completedAt") or item["createdAt"]))
        while len(self._jobs) >= self.maximum_jobs and terminal:
            oldest = terminal.pop(0)
            self._jobs.pop(str(oldest["jobId"]), None)
