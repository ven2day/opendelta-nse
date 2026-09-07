"""Reusable lease guard with a bounded database heartbeat thread."""

from __future__ import annotations

import os
import socket
import threading
import uuid
from collections.abc import Mapping
from types import TracebackType
from typing import Any, Self

from backend.monitoring.repository import MonitoringRepository


class WorkerLeaseGuard:
    def __init__(
        self,
        repository: MonitoringRepository,
        *,
        worker_type: str,
        task_key: str,
        worker_identity: str,
        current_task: Mapping[str, Any] | None = None,
        ttl_seconds: int = 90,
    ) -> None:
        self.repository = repository
        self.worker_type = worker_type
        self.task_key = task_key
        self.worker_identity = worker_identity
        self.current_task = dict(current_task or {})
        self.ttl_seconds = ttl_seconds
        self.owner_token = str(uuid.uuid4())
        self.acquired = False
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._error: str | None = None

    def __enter__(self) -> Self:
        row = self.repository.acquire_lease(
            worker_type=self.worker_type,
            task_key=self.task_key,
            worker_identity=self.worker_identity,
            host_identity=socket.gethostname(),
            process_identity=str(os.getpid()),
            owner_token=self.owner_token,
            ttl_seconds=self.ttl_seconds,
            current_task=self.current_task,
        )
        self.acquired = row is not None
        if self.acquired:
            self._thread = threading.Thread(target=self._heartbeat, name="worker-lease-heartbeat", daemon=True)
            self._thread.start()
        return self

    def _heartbeat(self) -> None:
        interval = max(2.0, self.ttl_seconds / 3)
        while not self._stop.wait(interval):
            try:
                row = self.repository.heartbeat(
                    self.worker_type,
                    self.task_key,
                    self.owner_token,
                    ttl_seconds=self.ttl_seconds,
                    current_task=self.current_task,
                )
                if row is None:
                    self.acquired = False
                    return
            except Exception as error:  # noqa: BLE001 - loss is exposed and the task must stop at its next check
                self._error = str(error)[:500]
                self.acquired = False
                return

    def __exit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        del exception_type, traceback
        self._stop.set()
        if self._thread and self._thread is not threading.current_thread():
            self._thread.join(timeout=2)
        if self.acquired:
            self.repository.release_lease(
                self.worker_type,
                self.task_key,
                self.owner_token,
                error=str(exception)[:500] if exception else self._error,
            )


class WorkerLeaseGroup:
    def __init__(self, guards: list[WorkerLeaseGuard]) -> None:
        self.guards = guards
        self.acquired = False

    def __enter__(self) -> Self:
        entered: list[WorkerLeaseGuard] = []
        for guard in self.guards:
            entered.append(guard.__enter__())
            if not guard.acquired:
                for owned in reversed(entered):
                    owned.__exit__(None, None, None)
                return self
        self.acquired = True
        return self

    def __exit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        for guard in reversed(self.guards):
            guard.__exit__(exception_type, exception, traceback)
        self.acquired = False
