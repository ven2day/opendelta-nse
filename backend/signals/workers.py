"""One independent worker thread per market.

NSE: follows the trading session and polls settled Dhan candles while open.
Crypto: runs 24/7 against the configured public exchange. Both reconnect with
backoff after failures, persist their health to ``engine_status``, and never
place orders.
"""

from __future__ import annotations

import logging
import threading
from datetime import datetime, timedelta
from typing import Any, Callable, Mapping, Sequence
from zoneinfo import ZoneInfo

from backend.data.repositories import EngineStatusRepository
from backend.markets.base import CandleSource, MarketSpec
from backend.signals.candle_processor import CandleProcessor
from backend.signals.engine import SignalEngine
from backend.signals.recovery import rebuild_histories

logger = logging.getLogger("opendelta.signals.worker")

ENGINE_NAME = "live-signals-v2"


class MarketSignalWorker:
    def __init__(
        self,
        *,
        market: MarketSpec,
        engine: SignalEngine,
        source: CandleSource,
        universe: Callable[[], Sequence[str]],
        status_repository: EngineStatusRepository | None,
        clock: Callable[[], datetime],
        poll_seconds: float = 60.0,
        closed_poll_seconds: float = 30.0,
        maximum_backoff_seconds: float = 300.0,
        lookback_days: int = 2,
        engine_name: str | None = None,
    ) -> None:
        self.market = market
        self.engine = engine
        self.source = source
        self.universe = universe
        self.status_repository = status_repository
        self.clock = clock
        self.poll_seconds = poll_seconds
        self.closed_poll_seconds = closed_poll_seconds
        self.maximum_backoff_seconds = maximum_backoff_seconds
        self.lookback_days = lookback_days
        self.engine_name = engine_name or f"{ENGINE_NAME}:{engine.strategy.strategy_id}:{engine.timeframe}"
        daily_session_close = market.daily_session_close if engine.timeframe == "1d" else None
        self.processor = CandleProcessor(
            bar_minutes=market.minutes(engine.timeframe),
            timezone=market.timezone,
            clock=clock,
            daily_session_close=daily_session_close,
        )
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._symbols: list[str] = []
        self._last_daily_poll_date = None
        self._state: dict[str, Any] = {"status": "STOPPED", "connectionStatus": "DISCONNECTED", "message": "Not started", "consecutiveFailures": 0, "polls": 0}
        self.candle_listeners: list[Callable[[str, Any, datetime], None]] = []

    def add_candle_listener(self, listener: Callable[[str, Any, datetime], None]) -> None:
        """Called with ``(symbol, candle_row, timestamp)`` for every completed candle the engine accepts."""
        self.candle_listeners.append(listener)

    # ---- lifecycle ---------------------------------------------------------------

    def start(self) -> None:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop.clear()
            self._thread = threading.Thread(target=self.run, name=f"signals-{self.market.market.lower()}", daemon=True)
            self._thread.start()

    def stop(self, timeout: float = 10.0) -> None:
        self._stop.set()
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=timeout)
        self._set_state(status="STOPPED", connection="DISCONNECTED", message="Worker stopped")

    def run(self) -> None:
        try:
            self.recover()
        except Exception as error:  # noqa: BLE001 - recovery problems are reported, then polling continues
            self._set_state(status="ERROR", connection="DISCONNECTED", message=f"Recovery failed: {error}"[:240])
        backoff = self.poll_seconds
        while not self._stop.is_set():
            now = self.clock()
            if not self._poll_is_due(now):
                self._set_state(status="MARKET_CLOSED", connection="DISCONNECTED", message=f"{self.market.market} session is closed")
                self._stop.wait(self.closed_poll_seconds)
                continue
            try:
                created = self.poll_once()
                if self.engine.timeframe == "1d" and self.market.daily_session_close is not None:
                    self._last_daily_poll_date = self._local_moment(now).date()
                backoff = self.poll_seconds
                self._set_state(status="READY", connection="CONNECTED", message=f"{created} new signal(s) on the last poll" if created else "Completed candles evaluated", reset_failures=True)
                self._stop.wait(self.poll_seconds)
            except Exception as error:  # noqa: BLE001 - keep polling; expose the failure
                backoff = min(backoff * 2, self.maximum_backoff_seconds)
                self._set_state(status="ERROR", connection="DISCONNECTED", message=f"Poll failed, retrying in {int(backoff)}s: {error}"[:240], failure=True)
                self._stop.wait(backoff)

    def _local_moment(self, moment: datetime) -> datetime:
        zone = ZoneInfo(self.market.timezone)
        return moment.replace(tzinfo=zone) if moment.tzinfo is None else moment.astimezone(zone)

    def _poll_is_due(self, moment: datetime) -> bool:
        """Intraday workers follow the session; NSE daily workers run once after close."""
        if self.engine.timeframe != "1d" or self.market.daily_session_close is None:
            return self.market.session_is_open(moment)
        local = self._local_moment(moment)
        if local.weekday() >= 5 or local.time() < self.market.daily_session_close:
            return False
        return self._last_daily_poll_date != local.date()

    # ---- work ----------------------------------------------------------------------

    def recover(self) -> dict[str, Any]:
        symbols = list(dict.fromkeys(str(item).strip().upper() for item in self.universe() if str(item).strip()))
        with self._lock:
            self._symbols = symbols
        summary = rebuild_histories(self.engine, self.source, self.processor, symbols, now=self.clock(), lookback_days=self.lookback_days)
        self._set_state(status="RECOVERED", connection="DISCONNECTED", message=f"Recovered {summary['symbolsSeeded']} symbols, {summary['openSignals']} open signal(s)", details=summary)
        return summary

    def poll_once(self) -> int:
        now = self.clock()
        with self._lock:
            symbols = list(self._symbols)
        created = 0
        failures: list[str] = []
        for symbol in symbols:
            if self._stop.is_set():
                break
            try:
                frame = self.source.candles(symbol, self.engine.timeframe, now - timedelta(days=self.lookback_days), now, warmup_bars=self.engine.history.maximum_bars)
                completed = self.processor.completed(frame, now)
                before = self.engine.history.latest_timestamp(symbol)
                if self.engine.process_completed_candle(symbol, completed) is not None:
                    created += 1
                if self.candle_listeners:
                    fresh = completed[completed.index > before] if before is not None else completed
                    for stamp, row in fresh.iterrows():
                        for listener in self.candle_listeners:
                            try:
                                listener(symbol, row, stamp.to_pydatetime())
                            except Exception:  # noqa: BLE001 - a listener must never break the feed
                                logger.exception("Candle listener failed for %s", symbol)
            except Exception as error:  # noqa: BLE001 - one symbol's provider error must not skip the rest
                failures.append(f"{symbol}: {error}"[:120])
        with self._lock:
            self._state["polls"] += 1
        if failures and len(failures) == len(symbols) and symbols:
            raise RuntimeError("every symbol failed; " + failures[0])
        return created

    def refresh_universe(self) -> list[str]:
        symbols = list(dict.fromkeys(str(item).strip().upper() for item in self.universe() if str(item).strip()))
        with self._lock:
            self._symbols = symbols
        return symbols

    # ---- status --------------------------------------------------------------------

    def _set_state(self, *, status: str, connection: str, message: str, details: Mapping[str, Any] | None = None, failure: bool = False, reset_failures: bool = False) -> None:
        with self._lock:
            self._state.update({"status": status, "connectionStatus": connection, "message": message})
            if failure:
                self._state["consecutiveFailures"] += 1
            if reset_failures:
                self._state["consecutiveFailures"] = 0
            if details:
                self._state["recovery"] = dict(details)
        if self.status_repository is not None:
            try:
                snapshot = self.engine.snapshot()
                last = self.engine.last_completed
                self.status_repository.upsert(
                    engine=self.engine_name,
                    market=self.market.market,
                    status=status,
                    connection_status=connection,
                    data_age_seconds=snapshot["dataAgeSeconds"],
                    last_completed_candle=last.to_pydatetime() if last is not None else None,
                    message=message,
                    details={**snapshot, **self._state},
                )
            except Exception:  # noqa: BLE001 - status persistence must never take the worker down
                logger.exception("Could not persist %s engine status", self.market.market)

    def status(self) -> dict[str, Any]:
        with self._lock:
            state = dict(self._state)
            state["symbols"] = list(self._symbols)
        return {**self.engine.snapshot(), **state, "engine": self.engine_name}
