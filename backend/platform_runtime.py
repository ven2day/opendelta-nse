"""Wires the unified platform into the existing FastAPI process.

Kept separate from backend/app.py so the compatibility layer only needs to call
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

from datetime import datetime, timezone as _timezone

from backend.api.backtest_routes import BacktestServices, create_backtest_router
from backend.api.dashboard_routes import create_dashboard_router
from backend.api.settings_routes import create_settings_router
from backend.api.paper_trading_routes import create_paper_trading_router
from backend.api.screener_routes import ScreenerServices, create_screener_router
from backend.api.signal_routes import create_signal_router
from backend.screener.engine import ScreenerEngine
from backend.paper_trading.broker import PaperBroker, PaperRepositories
from backend.paper_trading.execution import ExecutionPolicy
from backend.backtest.engine import BacktestEngine, BacktestRequest
from backend.backtest.jobs import BacktestJobRunner
from backend.backtest.result_writer import DatabaseResultWriter
from backend.data.database import Database, DatabaseUnavailable
from backend.data.repositories import (
    BacktestRunRepository,
    BacktestTradeRepository,
    EngineStatusRepository,
    LiveSignalRepository,
    PaperAccountRepository,
    PaperLotRepository,
    PaperOrderRepository,
    PaperTradeRepository,
    SavedUniverseRepository,
    ScreenerResultRepository,
    ScreenerRunRepository,
    StrategyConfigRepository,
)
from backend.markets.base import CandleSource, market_spec
from backend.signals.engine import RiskSettings, SignalEngine
from backend.signals.workers import MarketSignalWorker
from backend.strategies import STRATEGIES

DEFAULT_LIVE_STRATEGY = "ema_vwap_strong_buy"
LIVE_TIMEFRAME = "5m"

logger = logging.getLogger("opendelta.platform")


class PlatformRuntime:
    def __init__(
        self,
        *,
        database: Database | None,
        candle_sources: dict[str, Callable[[], CandleSource]],
        fallback_universes: dict[str, Callable[[], list[str]]] | None = None,
        symbol_catalogues: dict[str, Callable[[], list[str]]] | None = None,
        candle_read_mode: str = "custom",
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.database = database
        self.candle_sources = candle_sources
        self.fallback_universes = fallback_universes or {}
        self.symbol_catalogues = symbol_catalogues or {}
        self.candle_read_mode = candle_read_mode
        self._screener: ScreenerServices | None = None
        self.clock = clock or (lambda: datetime.now(_timezone.utc))
        self._runner: BacktestJobRunner | None = None
        self._workers: dict[str, MarketSignalWorker] = {}
        self._brokers: dict[str, PaperBroker] = {}
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
            stale_screens = ScreenerRunRepository(self.require_database()).recover_interrupted()
            if stale_screens:
                logger.warning("Marked %s stale screener run(s) FAILED after restart", stale_screens)
            self._start_signal_workers()
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
            workers, self._workers = dict(self._workers), {}
        for worker in workers.values():
            worker.stop()
        if runner is not None:
            runner.shutdown()
        if self._screener is not None:
            self._screener.shutdown()
        if self.database is not None:
            self.database.close()

    # ---- live signals ------------------------------------------------------------

    def _start_signal_workers(self) -> None:
        for market in ("NSE", "CRYPTO"):
            if not _truthy(os.environ.get(f"{market}_SIGNAL_ENGINE_V2_ENABLED")):
                continue
            worker = self.build_signal_worker(market)
            if not _truthy(os.environ.get(f"{market}_PAPER_TRADING_V2_ENABLED", "true")):
                logger.info("%s paper trading v2 is disabled", market)
            else:
                broker = self.paper_broker(market)
                worker.engine.publish = broker.on_signal
                worker.add_candle_listener(lambda symbol, row, stamp, _broker=broker: _broker.on_completed_candle(symbol, row, stamp))
            with self._lock:
                self._workers[market] = worker
            worker.start()
            logger.info("Started %s live-signal worker", market)

    # ---- paper trading -----------------------------------------------------------

    def paper_repositories(self) -> PaperRepositories:
        database = self.require_database()
        return PaperRepositories(PaperAccountRepository(database), PaperOrderRepository(database), PaperLotRepository(database), PaperTradeRepository(database))

    def paper_broker(self, market: str) -> PaperBroker:
        key = market.strip().upper()
        with self._lock:
            broker = self._brokers.get(key)
        if broker is not None:
            return broker
        spec = market_spec(key)
        strategy = STRATEGIES.get(os.environ.get(f"{key}_LIVE_STRATEGY", DEFAULT_LIVE_STRATEGY))
        active = self.strategy_configs().active(spec.market, strategy.strategy_id)
        policy = ExecutionPolicy.from_mapping((active or {}).get("riskSettings"), whole_units=(key == "NSE"))
        broker = PaperBroker(market=spec, repositories=self.paper_repositories(), policy=policy, timeframe=LIVE_TIMEFRAME, clock=self.clock)
        with self._lock:
            self._brokers.setdefault(key, broker)
            return self._brokers[key]

    def build_signal_worker(self, market: str, *, strategy_id: str | None = None) -> MarketSignalWorker:
        spec = market_spec(market)
        strategy_key = strategy_id or os.environ.get(f"{market}_LIVE_STRATEGY", DEFAULT_LIVE_STRATEGY)
        strategy = STRATEGIES.get(strategy_key)
        active = self.strategy_configs().active(spec.market, strategy.strategy_id)
        configuration = active["configuration"] if active else {}
        risk = RiskSettings.from_mapping(active["riskSettings"] if active else None)
        engine = SignalEngine(
            market=spec,
            strategy=strategy,
            configuration=configuration,
            risk=risk,
            timeframe=LIVE_TIMEFRAME,
            repository=self.signals(),
            clock=self.clock,
        )
        universes = self.universes()

        def universe() -> list[str]:
            symbols = universes.active_symbols(spec.market)
            if symbols:
                return symbols
            fallback = self.fallback_universes.get(spec.market)
            return list(fallback()) if fallback else []

        return MarketSignalWorker(
            market=spec,
            engine=engine,
            source=self.candle_sources[spec.market](),
            universe=universe,
            status_repository=self.engine_status(),
            clock=self.clock,
            poll_seconds=float(os.environ.get(f"{market}_SIGNAL_POLL_SECONDS", "120" if market == "NSE" else "60")),
        )

    def worker_status(self, market: str) -> dict[str, Any] | None:
        with self._lock:
            worker = self._workers.get(market)
        return worker.status() if worker else None

    def signals(self) -> LiveSignalRepository:
        return LiveSignalRepository(self.require_database())

    def engine_status(self) -> EngineStatusRepository:
        return EngineStatusRepository(self.require_database())

    def strategy_configs(self) -> StrategyConfigRepository:
        return StrategyConfigRepository(self.require_database())

    def universes(self) -> SavedUniverseRepository:
        return SavedUniverseRepository(self.require_database())

    # ---- screener ----------------------------------------------------------------

    def screener_engine(self, market: str) -> ScreenerEngine:
        spec = market_spec(market)
        return ScreenerEngine(
            market=spec,
            source=self.candle_sources[spec.market](),
            timeframe=LIVE_TIMEFRAME,
            batch_size=int(os.environ.get("SCREENER_CANDLE_BATCH_SIZE", "50")),
            clock=self.clock,
        )

    def symbol_catalogue(self, market: str) -> list[str]:
        provider = self.symbol_catalogues.get(market.strip().upper())
        return list(provider()) if provider else []

    def screener(self) -> ScreenerServices:
        with self._lock:
            if self._screener is None:
                self._screener = ScreenerServices(
                    runs=lambda: ScreenerRunRepository(self.require_database()),
                    results=lambda: ScreenerResultRepository(self.require_database()),
                    universes=self.universes,
                    engine_for=self.screener_engine,
                    catalogue_for=self.symbol_catalogue,
                )
            return self._screener

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
        with self._lock:
            workers = list(self._workers)
        return {
            "databaseConfigured": self.database is not None,
            "candleReadMode": self.candle_read_mode,
            "disabledReason": self.disabled_reason,
            "migratedVersions": self.migrated_versions,
            "activeBacktests": self._runner.active_run_ids() if self._runner else [],
            "signalWorkers": workers,
            "strategies": STRATEGIES.ids(),
        }


def _truthy(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def install_platform(app: FastAPI, runtime: PlatformRuntime, *, overview: Callable[[], dict[str, Any]] | None = None) -> None:
    services = BacktestServices(registry=STRATEGIES, runs=runtime.runs, trades=runtime.trades, runner=runtime.runner)
    app.router.routes.extend(create_backtest_router(services).routes)
    app.router.routes.extend(create_settings_router(STRATEGIES, configs=runtime.strategy_configs).routes)
    app.router.routes.extend(
        create_dashboard_router(
            overview=overview or (lambda: {}),
            screener_runs=lambda market: ScreenerRunRepository(runtime.require_database()).list(market, limit=1),
            backtest_runs=lambda market: runtime.runs().list(market, limit=5),
            engine_health=lambda market: {"stored": runtime.engine_status().get("live-signals-v2", market), "worker": runtime.worker_status(market)},
            paper_summary=lambda market: runtime.paper_broker(market).summary(),
            paper_positions=lambda market: runtime.paper_broker(market).positions(),
            active_universe=lambda market: runtime.universes().active(market),
        ).routes
    )
    app.router.routes.extend(create_signal_router(signals=runtime.signals, engine_status=runtime.engine_status, worker_status=runtime.worker_status).routes)
    app.router.routes.extend(create_paper_trading_router(runtime.paper_broker).routes)
    app.router.routes.extend(create_screener_router(runtime.screener()).routes)
