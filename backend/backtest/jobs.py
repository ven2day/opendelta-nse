"""Background execution of backtest runs; state lives in the database, not in this process."""

from __future__ import annotations

import logging
import threading
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Callable

from backend.backtest.engine import BacktestEngine, BacktestRequest
from backend.data.repositories import BacktestRunRepository

logger = logging.getLogger("opendelta.backtest.jobs")


class BacktestJobRunner:
    def __init__(self, runs: BacktestRunRepository, engine_factory: Callable[[BacktestRequest, threading.Event], BacktestEngine], *, max_workers: int = 1) -> None:
        self.runs = runs
        self.engine_factory = engine_factory
        self._executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="backtest-run")
        self._cancel_events: dict[str, threading.Event] = {}
        self._futures: dict[str, Future] = {}
        self._lock = threading.Lock()

    def recover(self) -> int:
        """Called once at startup: nothing from a previous process can still be running."""
        return self.runs.interrupt_stale()

    def submit(self, request: BacktestRequest) -> None:
        event = threading.Event()
        with self._lock:
            self._cancel_events[request.run_id] = event
            self._futures[request.run_id] = self._executor.submit(self._execute, request, event)

    def _execute(self, request: BacktestRequest, event: threading.Event) -> None:
        try:
            if self.runs.cancel_requested(request.run_id):
                self.runs.finish(request.run_id, status="CANCELLED", metrics=None)
                return
            engine = self.engine_factory(request, event)
            engine.run(request)
        except Exception:  # the engine already recorded FAILED; keep the worker alive
            logger.exception("Backtest run %s failed", request.run_id)
        finally:
            with self._lock:
                self._cancel_events.pop(request.run_id, None)
                self._futures.pop(request.run_id, None)

    def cancel(self, run_id: str) -> dict:
        record = self.runs.request_cancel(run_id)
        with self._lock:
            event = self._cancel_events.get(run_id)
        if event is not None:
            event.set()
        return record

    def active_run_ids(self) -> list[str]:
        with self._lock:
            return sorted(self._futures)

    def shutdown(self) -> None:
        with self._lock:
            for event in self._cancel_events.values():
                event.set()
        self._executor.shutdown(wait=False, cancel_futures=True)
