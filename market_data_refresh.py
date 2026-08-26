from __future__ import annotations

import concurrent.futures
import threading
from collections.abc import Callable
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd

from main import ConfigurationError, DhanAPIError, DhanConfig, IST, run_screener


RefreshRunner = Callable[[DhanConfig], pd.DataFrame]


class MarketDataRefreshService:
    """Run the existing all-symbol Dhan collector without blocking API requests."""

    def __init__(
        self,
        output_file: Path,
        *,
        runner: RefreshRunner = run_screener,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.output_file = output_file.expanduser()
        if not self.output_file.is_absolute():
            raise ValueError("Market-data output path must be absolute")
        self.runner = runner
        self.clock = clock or (lambda: datetime.now(IST))
        self._lock = threading.Lock()
        self._executor = concurrent.futures.ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix="market-data-refresh",
        )
        self._future: concurrent.futures.Future[None] | None = None
        self._state = "IDLE"
        self._started_at: datetime | None = None
        self._completed_at: datetime | None = None
        self._rows_published: int | None = None
        self._error: str | None = None

    def _file_timestamp(self) -> str | None:
        try:
            return datetime.fromtimestamp(self.output_file.stat().st_mtime, tz=IST).isoformat()
        except OSError:
            return None

    def _status_unlocked(self) -> dict[str, Any]:
        running = self._future is not None and not self._future.done()
        return {
            "state": "RUNNING" if running else self._state,
            "running": running,
            "startedAt": self._started_at.isoformat() if self._started_at else None,
            "completedAt": self._completed_at.isoformat() if self._completed_at else None,
            "lastRefreshTimestamp": self._file_timestamp(),
            "rowsPublished": self._rows_published,
            "error": self._error,
        }

    def status(self) -> dict[str, Any]:
        with self._lock:
            return self._status_unlocked()

    def start(self) -> dict[str, Any]:
        with self._lock:
            if self._future is not None and not self._future.done():
                return {"accepted": False, **self._status_unlocked()}
            self._state = "RUNNING"
            self._started_at = self.clock()
            self._completed_at = None
            self._rows_published = None
            self._error = None
            self._future = self._executor.submit(self._run)
            return {"accepted": True, **self._status_unlocked()}

    def _run(self) -> None:
        try:
            config = replace(DhanConfig.from_environment(), output_file=self.output_file)
            output = self.runner(config)
        except (ConfigurationError, DhanAPIError, OSError, ValueError) as error:
            message = " ".join(str(error).split())[:300] or "Market-data refresh failed"
            with self._lock:
                self._state = "FAILED"
                self._completed_at = self.clock()
                self._error = message
        except Exception:
            with self._lock:
                self._state = "FAILED"
                self._completed_at = self.clock()
                self._error = "Unexpected market-data refresh failure"
        else:
            with self._lock:
                self._state = "SUCCEEDED"
                self._completed_at = self.clock()
                self._rows_published = len(output)
                self._error = None

    def wait(self, timeout: float | None = None) -> dict[str, Any]:
        future = self._future
        if future is not None:
            future.result(timeout=timeout)
        return self.status()

    def shutdown(self) -> None:
        self._executor.shutdown(wait=False, cancel_futures=False)
