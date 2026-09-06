"""Background execution of backtest runs; state lives in the database, not in this process."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from contextlib import AbstractContextManager
from types import TracebackType
from typing import Self

from backend.backtest.engine import BacktestEngine, BacktestRequest
from backend.data.repositories import BacktestRunRepository

logger = logging.getLogger("opendelta.backtest.jobs")


class BacktestQueueFull(RuntimeError):
    """Raised before persistence when the bounded background queue has no capacity."""


class BacktestQueueReservation(AbstractContextManager["BacktestQueueReservation"]):
    def __init__(self, runner: BacktestJobRunner, count: int) -> None:
        self.runner = runner
        self.count = count
        self.submitted = False
        self.released = False

    def __enter__(self) -> Self:
        return self

    def submit(self, requests: list[BacktestRequest]) -> None:
        if self.submitted or self.released:
            raise RuntimeError("Backtest queue reservation is no longer active")
        if len(requests) != self.count:
            raise ValueError("Reserved backtest count does not match submitted requests")
        self.runner._submit_reserved(requests)
        self.submitted = True

    def release(self) -> None:
        if not self.submitted and not self.released:
            self.runner._release_reservation(self.count)
            self.released = True

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.release()


class BacktestJobRunner:
    def __init__(
        self,
        runs: BacktestRunRepository,
        engine_factory: Callable[[BacktestRequest, threading.Event], BacktestEngine],
        *,
        max_workers: int = 1,
        max_pending: int = 200,
    ) -> None:
        if max_workers < 1:
            raise ValueError("max_workers must be positive")
        if max_pending < max_workers:
            raise ValueError("max_pending must be at least max_workers")
        self.runs = runs
        self.engine_factory = engine_factory
        self.max_pending = max_pending
        self._executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="backtest-run")
        self._cancel_events: dict[str, threading.Event] = {}
        self._futures: dict[str, Future] = {}
        self._reserved = 0
        self._lock = threading.Lock()

    def recover(self) -> int:
        """Called once at startup: nothing from a previous process can still be running."""
        return self.runs.interrupt_stale()

    def submit(self, request: BacktestRequest) -> None:
        with self.reserve(1) as reservation:
            reservation.submit([request])

    def submit_many(self, requests: list[BacktestRequest]) -> None:
        with self.reserve(len(requests)) as reservation:
            reservation.submit(requests)

    def reserve(self, count: int) -> BacktestQueueReservation:
        if count < 1:
            raise ValueError("At least one backtest queue slot must be reserved")
        with self._lock:
            pending = len(self._futures) + self._reserved
            if pending + count > self.max_pending:
                raise BacktestQueueFull(
                    f"Backtest queue capacity exceeded ({pending} pending, {count} requested, "
                    f"limit {self.max_pending})"
                )
            self._reserved += count
        return BacktestQueueReservation(self, count)

    def _submit_reserved(self, requests: list[BacktestRequest]) -> None:
        with self._lock:
            if self._reserved < len(requests):
                raise RuntimeError("Backtest queue reservation was lost")
            self._reserved -= len(requests)
            for request in requests:
                event = threading.Event()
                self._cancel_events[request.run_id] = event
                self._futures[request.run_id] = self._executor.submit(self._execute, request, event)

    def _release_reservation(self, count: int) -> None:
        with self._lock:
            self._reserved = max(0, self._reserved - count)

    def pending_count(self) -> int:
        with self._lock:
            return len(self._futures) + self._reserved

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
