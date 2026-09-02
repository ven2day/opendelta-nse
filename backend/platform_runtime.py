"""Wires the unified platform into the existing FastAPI process.

Kept separate from backtest_api.py so the legacy module only needs to call
``install_platform(app, ...)``. The database is optional: without
``MARKET_DATA_DATABASE_URL`` the v2 routes answer 503 instead of crashing the
service that also hosts the legacy endpoints.
"""

from __future__ import annotations

import logging
import os
import threading
from typing import Any, Callable

from fastapi import FastAPI

from backend.api.backtest_routes import BacktestServices, create_backtest_router
from backend.api.settings_routes import create_settings_router
from backend.backtest.engine import BacktestEngine, BacktestRequest
from backend.backtest.jobs import BacktestJobRunner
from backend.backtest.result_writer import DatabaseResultWriter
from backend.data.database import Database, DatabaseUnavailable
from backend.data.repositories import BacktestRunRepository, BacktestTradeRepository
from backend.markets.base import CandleSource, market_spec
from backend.strategies import STRATEGIES

logger = logging.getLogger("opendelta.platform")


class PlatformRuntime:
    def __init__(self, *, database: Database | None, candle_sources: dict[str, Callable[[], CandleSource]]) -> None:
        self.database = database
        self.candle_sources = candle_sources
        self._runner: BacktestJobRunner | None = None
        self._lock = threading.Lock()
        self.migrated_versions: list[str] = []
        self.disabled_reason: str | None = None

    def start(self) -> None:
        """Open the database and recover state.

        Migrations are never applied implicitly: the schema must already be
        present (``python -m backend.data.migrate``) unless
        ``PLATFORM_AUTO_MIGRATE=true`` is set explicitly. A database that is
        configured but not migrated leaves the v2 routes failing closed rather
        than mutating a production database as a side effect of a deploy.
        """
        if self.database is None:
            logger.warning("MARKET_DATA_DATABASE_URL is not set; unified platform routes are disabled")
            return
        try:
            self.database.open()
            if _truthy(os.environ.get("PLATFORM_AUTO_MIGRATE")):
                self.migrated_versions = self.database.migrate()
            else:
                pending = self.database.pending_versions()
                if pending:
                    raise RuntimeError(
                        "Platform schema migrations pending: " + ", ".join(pending)
                        + ". Run `python -m backend.data.migrate` or set PLATFORM_AUTO_MIGRATE=true."
                    )
            interrupted = self.runner().recover()
            if interrupted:
                logger.warning("Marked %s stale backtest run(s) INTERRUPTED after restart", interrupted)
        except Exception as error:
            logger.error("Unified platform database is unavailable; v2 routes will fail closed: %s", error)
            self.disabled_reason = str(error)
            try:
                self.database.close()
            finally:
                self.database = None

    def stop(self) -> None:
        with self._lock:
            runner, self._runner = self._runner, None
        if runner is not None:
            runner.shutdown()
        if self.database is not None:
            self.database.close()

    def require_database(self) -> Database:
        if self.database is None:
            raise DatabaseUnavailable(self.disabled_reason or "The platform database is not configured")
        return self.database

    def runs(self) -> BacktestRunRepository:
        return BacktestRunRepository(self.require_database())

    def trades(self) -> BacktestTradeRepository:
        return BacktestTradeRepository(self.require_database())

    def runner(self) -> BacktestJobRunner:
        with self._lock:
            if self._runner is None:
                self._runner = BacktestJobRunner(self.runs(), self._engine)
            return self._runner

    def _engine(self, request: BacktestRequest, cancel_event: threading.Event) -> BacktestEngine:
        spec = market_spec(request.market)
        source = self.candle_sources[request.market]()
        return BacktestEngine(
            strategy=STRATEGIES.get(request.strategy_id),
            market=spec,
            source=source,
            writer=DatabaseResultWriter(self.runs(), self.trades()),
            cancel_event=cancel_event,
        )

    def status(self) -> dict[str, Any]:
        return {
            "databaseConfigured": self.database is not None,
            "disabledReason": self.disabled_reason,
            "migratedVersions": self.migrated_versions,
            "activeBacktests": self._runner.active_run_ids() if self._runner else [],
            "strategies": STRATEGIES.ids(),
        }


def _truthy(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def install_platform(app: FastAPI, runtime: PlatformRuntime) -> None:
    services = BacktestServices(registry=STRATEGIES, runs=runtime.runs, trades=runtime.trades, runner=runtime.runner)
    app.router.routes.extend(create_backtest_router(services).routes)
    app.router.routes.extend(create_settings_router(STRATEGIES).routes)
