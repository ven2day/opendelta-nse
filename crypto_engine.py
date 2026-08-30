from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
import uuid
from collections.abc import Callable, Mapping
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from crypto_providers import MarketProviderError, OkxPublicProvider, ProviderFactory, ValrPublicProvider
from crypto_strategy import (
    STRATEGY_KEY,
    STRATEGY_NAME,
    STRATEGY_VERSION,
    CryptoPullbackConfig,
    candle_frame,
    generate_signals,
    run_pullback_backtest,
    signal_payload,
)
from market_core import (
    TIMEFRAME_SECONDS,
    MarketCandle,
    MarketInstrument,
    iso_utc,
    normalize_provider,
    normalize_provider_symbol,
    utc_datetime,
)
from opendelta.timescale_market_data import CanonicalCandle, CanonicalCandleWriter


class CryptoMarketRepository:
    """Durable instrument, candle, signal, and backtest metadata store."""

    def __init__(self, database_path: Path) -> None:
        if not database_path.is_absolute():
            raise ValueError("Crypto market database path must be absolute")
        self.database_path = database_path
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=FULL")
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    def _initialize(self) -> None:
        with self._lock, self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS market_instruments (
                    instrument_id TEXT PRIMARY KEY,
                    provider TEXT NOT NULL,
                    provider_symbol TEXT NOT NULL,
                    display_symbol TEXT NOT NULL,
                    market TEXT NOT NULL,
                    instrument_type TEXT NOT NULL,
                    base_currency TEXT NOT NULL,
                    quote_currency TEXT NOT NULL,
                    tick_size TEXT NOT NULL,
                    quantity_step TEXT NOT NULL,
                    minimum_quantity TEXT NOT NULL,
                    minimum_notional TEXT NOT NULL,
                    contract_multiplier TEXT NOT NULL,
                    active INTEGER NOT NULL,
                    backtest_enabled INTEGER NOT NULL,
                    signals_enabled INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(provider, provider_symbol)
                );
                CREATE TABLE IF NOT EXISTS market_candles (
                    provider TEXT NOT NULL,
                    provider_symbol TEXT NOT NULL,
                    timeframe TEXT NOT NULL,
                    open_time TEXT NOT NULL,
                    close_time TEXT NOT NULL,
                    open REAL NOT NULL,
                    high REAL NOT NULL,
                    low REAL NOT NULL,
                    close REAL NOT NULL,
                    base_volume REAL NOT NULL,
                    quote_volume REAL,
                    complete INTEGER NOT NULL,
                    ingested_at TEXT NOT NULL,
                    PRIMARY KEY(provider, provider_symbol, timeframe, open_time)
                );
                CREATE INDEX IF NOT EXISTS market_candles_lookup
                    ON market_candles(provider, provider_symbol, timeframe, open_time DESC);
                CREATE TABLE IF NOT EXISTS crypto_signals (
                    signal_id TEXT PRIMARY KEY,
                    instrument_id TEXT NOT NULL,
                    signal_time TEXT NOT NULL,
                    side TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(instrument_id) REFERENCES market_instruments(instrument_id)
                );
                CREATE INDEX IF NOT EXISTS crypto_signals_recent
                    ON crypto_signals(signal_time DESC, signal_id DESC);
                CREATE TABLE IF NOT EXISTS crypto_backtest_runs (
                    run_id TEXT PRIMARY KEY,
                    instrument_id TEXT NOT NULL,
                    provider TEXT NOT NULL,
                    strategy_key TEXT NOT NULL,
                    timeframe TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    completed_at TEXT NOT NULL,
                    configuration_json TEXT NOT NULL,
                    summary_json TEXT NOT NULL,
                    FOREIGN KEY(instrument_id) REFERENCES market_instruments(instrument_id)
                );
                """
            )

    def save_instrument(self, instrument: MarketInstrument) -> MarketInstrument:
        now = iso_utc(datetime.now(timezone.utc))
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                INSERT INTO market_instruments (
                    instrument_id, provider, provider_symbol, display_symbol, market,
                    instrument_type, base_currency, quote_currency, tick_size,
                    quantity_step, minimum_quantity, minimum_notional,
                    contract_multiplier, active, backtest_enabled, signals_enabled,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(provider, provider_symbol) DO UPDATE SET
                    display_symbol=excluded.display_symbol,
                    market=excluded.market,
                    instrument_type=excluded.instrument_type,
                    base_currency=excluded.base_currency,
                    quote_currency=excluded.quote_currency,
                    tick_size=excluded.tick_size,
                    quantity_step=excluded.quantity_step,
                    minimum_quantity=excluded.minimum_quantity,
                    minimum_notional=excluded.minimum_notional,
                    contract_multiplier=excluded.contract_multiplier,
                    active=excluded.active,
                    backtest_enabled=excluded.backtest_enabled,
                    signals_enabled=excluded.signals_enabled,
                    updated_at=excluded.updated_at
                """,
                (
                    instrument.instrument_id,
                    instrument.provider,
                    instrument.provider_symbol,
                    instrument.display_symbol,
                    instrument.market,
                    instrument.instrument_type,
                    instrument.base_currency,
                    instrument.quote_currency,
                    instrument.tick_size,
                    instrument.quantity_step,
                    instrument.minimum_quantity,
                    instrument.minimum_notional,
                    instrument.contract_multiplier,
                    int(instrument.active),
                    int(instrument.backtest_enabled),
                    int(instrument.signals_enabled),
                    now,
                    now,
                ),
            )
        return instrument

    def instruments(self) -> list[MarketInstrument]:
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM market_instruments ORDER BY market, display_symbol, provider"
            ).fetchall()
        return [MarketInstrument.from_row(row) for row in rows]

    def instrument(self, instrument_id: str) -> MarketInstrument:
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM market_instruments WHERE instrument_id = ?", (instrument_id,)
            ).fetchone()
        if row is None:
            raise KeyError(instrument_id)
        return MarketInstrument.from_row(row)

    def delete_instrument(self, instrument_id: str) -> bool:
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT 1 FROM market_instruments WHERE instrument_id = ?", (instrument_id,)
            ).fetchone()
            if row is None:
                return False
            signal_count = connection.execute(
                "SELECT COUNT(*) FROM crypto_signals WHERE instrument_id = ?", (instrument_id,)
            ).fetchone()[0]
            run_count = connection.execute(
                "SELECT COUNT(*) FROM crypto_backtest_runs WHERE instrument_id = ?", (instrument_id,)
            ).fetchone()[0]
            if signal_count or run_count:
                connection.execute(
                    "UPDATE market_instruments SET active=0, backtest_enabled=0, signals_enabled=0, updated_at=? WHERE instrument_id=?",
                    (iso_utc(datetime.now(timezone.utc)), instrument_id),
                )
            else:
                connection.execute("DELETE FROM market_instruments WHERE instrument_id = ?", (instrument_id,))
        return True

    def save_candles(self, candles: list[MarketCandle]) -> int:
        if not candles:
            return 0
        now = iso_utc(datetime.now(timezone.utc))
        values = [
            (
                item.provider,
                item.provider_symbol,
                item.timeframe,
                iso_utc(item.open_time),
                iso_utc(item.close_time),
                item.open,
                item.high,
                item.low,
                item.close,
                item.base_volume,
                item.quote_volume,
                int(item.complete),
                now,
            )
            for item in candles
        ]
        with self._lock, self._connect() as connection:
            connection.executemany(
                """
                INSERT INTO market_candles (
                    provider, provider_symbol, timeframe, open_time, close_time,
                    open, high, low, close, base_volume, quote_volume, complete, ingested_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(provider, provider_symbol, timeframe, open_time) DO UPDATE SET
                    close_time=excluded.close_time, open=excluded.open, high=excluded.high,
                    low=excluded.low, close=excluded.close, base_volume=excluded.base_volume,
                    quote_volume=excluded.quote_volume, complete=excluded.complete,
                    ingested_at=excluded.ingested_at
                """,
                values,
            )
        return len(values)

    def candles(
        self,
        instrument: MarketInstrument,
        timeframe: str,
        start: datetime,
        end: datetime,
    ) -> list[MarketCandle]:
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM market_candles
                WHERE provider=? AND provider_symbol=? AND timeframe=?
                  AND open_time>=? AND open_time<? AND complete=1
                ORDER BY open_time
                """,
                (
                    instrument.provider,
                    instrument.provider_symbol,
                    timeframe,
                    iso_utc(start),
                    iso_utc(end),
                ),
            ).fetchall()
        return [
            MarketCandle.build(
                provider=row["provider"],
                provider_symbol=row["provider_symbol"],
                timeframe=row["timeframe"],
                open_time=row["open_time"],
                open=row["open"],
                high=row["high"],
                low=row["low"],
                close=row["close"],
                base_volume=row["base_volume"],
                quote_volume=row["quote_volume"],
                complete=bool(row["complete"]),
            )
            for row in rows
        ]

    def save_signal(self, payload: Mapping[str, Any]) -> bool:
        signal_id = str(payload["signalId"])
        now = iso_utc(datetime.now(timezone.utc))
        with self._lock, self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT OR IGNORE INTO crypto_signals (
                    signal_id, instrument_id, signal_time, side, payload_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    signal_id,
                    str(payload["instrumentId"]),
                    str(payload["signalTimestamp"]),
                    str(payload["side"]),
                    json.dumps(dict(payload), ensure_ascii=False, separators=(",", ":")),
                    now,
                ),
            )
            return cursor.rowcount == 1

    def signals(self, limit: int = 200) -> list[dict[str, Any]]:
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                "SELECT payload_json FROM crypto_signals ORDER BY signal_time DESC, signal_id DESC LIMIT ?",
                (max(1, min(limit, 2_000)),),
            ).fetchall()
        result: list[dict[str, Any]] = []
        for row in rows:
            try:
                payload = json.loads(row["payload_json"])
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict):
                result.append(payload)
        return result

    def save_backtest(self, response: Mapping[str, Any], started_at: datetime) -> str:
        run_id = str(response["metadata"]["runId"])
        completed = str(response["metadata"]["completedAt"])
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                INSERT INTO crypto_backtest_runs (
                    run_id, instrument_id, provider, strategy_key, timeframe,
                    started_at, completed_at, configuration_json, summary_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    str(response["metadata"]["instrumentId"]),
                    str(response["metadata"]["provider"]),
                    STRATEGY_KEY,
                    str(response["metadata"]["timeframe"]),
                    iso_utc(started_at),
                    completed,
                    json.dumps(response["metadata"]["configuration"], separators=(",", ":")),
                    json.dumps(response["summary"], separators=(",", ":")),
                ),
            )
        return run_id


class CryptoMarketService:
    def __init__(
        self,
        repository: CryptoMarketRepository,
        providers: ProviderFactory | None = None,
        *,
        clock: Callable[[], datetime] | None = None,
        polling_seconds: int = 60,
        canonical_writer: CanonicalCandleWriter | None = None,
    ) -> None:
        self.repository = repository
        self.providers = providers or ProviderFactory()
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.polling_seconds = max(15, polling_seconds)
        self.canonical_writer = canonical_writer
        self._catalog_cache: dict[str, tuple[float, list[MarketInstrument]]] = {}
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._state = "STOPPED"
        self._message = "Crypto signal engine is not running"
        self._last_scan: str | None = None
        self._last_error: str | None = None

    def provider_names(self) -> list[str]:
        return self.providers.names()

    def _catalog(self, provider_name: str, force: bool = False) -> list[MarketInstrument]:
        provider = normalize_provider(provider_name)
        now = time.monotonic()
        cached = self._catalog_cache.get(provider)
        if not force and cached and now - cached[0] < 300:
            return cached[1]
        instruments = self.providers.get(provider).instruments()
        self._catalog_cache[provider] = (now, instruments)
        return instruments

    def search_catalog(
        self,
        provider: str,
        query: str = "",
        instrument_type: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        needle = query.strip().upper().replace("/", "").replace(" ", "")
        kind = instrument_type.strip().upper() if instrument_type else None
        rows = []
        for item in self._catalog(provider):
            if needle and needle not in item.provider_symbol.replace("-", "") and needle not in item.display_symbol:
                continue
            if kind and item.instrument_type != kind:
                continue
            rows.append(item.public())
            if len(rows) >= max(1, min(limit, 500)):
                break
        return rows

    def add_instrument(self, provider: str, provider_symbol: str) -> MarketInstrument:
        provider_name = normalize_provider(provider)
        symbol = normalize_provider_symbol(provider_symbol)
        found = next(
            (item for item in self._catalog(provider_name, force=True) if item.provider_symbol == symbol),
            None,
        )
        if found is None:
            raise ValueError(f"{symbol} is not an active {provider_name} instrument")
        return self.repository.save_instrument(found)

    def list_instruments(self) -> list[MarketInstrument]:
        return self.repository.instruments()

    def remove_instrument(self, instrument_id: str) -> bool:
        return self.repository.delete_instrument(instrument_id)

    def sync_candles(
        self,
        instrument: MarketInstrument,
        timeframe: str,
        start: datetime,
        end: datetime,
    ) -> list[MarketCandle]:
        if timeframe not in TIMEFRAME_SECONDS:
            raise ValueError("Unsupported timeframe")
        expected_bars = max(0, int((end - start).total_seconds() / TIMEFRAME_SECONDS[timeframe]))
        if expected_bars > 20_000:
            raise ValueError(
                f"Requested window contains about {expected_bars:,} bars; the interactive limit is 20,000"
            )
        cached = self.repository.candles(instrument, timeframe, start, end)
        fetch_start = start
        bar = timedelta(seconds=TIMEFRAME_SECONDS[timeframe])
        if cached and cached[0].open_time <= start + bar:
            if cached[-1].close_time >= end - bar:
                return cached
            fetch_start = max(start, cached[-1].open_time - bar)
        provider = self.providers.get(instrument.provider)
        candles = provider.candles(instrument, timeframe, fetch_start, end)
        self.repository.save_candles(candles)
        if self.canonical_writer is not None:
            self.canonical_writer.write(
                [
                    CanonicalCandle(
                        market="CRYPTO",
                        provider=item.provider,
                        instrument_id=instrument.instrument_id,
                        symbol=instrument.display_symbol,
                        timeframe=item.timeframe,
                        open_time=item.open_time,
                        close_time=item.close_time,
                        open=item.open,
                        high=item.high,
                        low=item.low,
                        close=item.close,
                        volume=item.base_volume,
                        quote_volume=item.quote_volume,
                        complete=item.complete,
                    )
                    for item in candles
                    if item.complete
                ]
            )
        return self.repository.candles(instrument, timeframe, start, end)

    def backtest(
        self,
        instrument_id: str,
        timeframe: str,
        duration_days: int,
        config: CryptoPullbackConfig,
    ) -> dict[str, Any]:
        if not 1 <= duration_days <= 730:
            raise ValueError("Duration must be between 1 and 730 days")
        instrument = self.repository.instrument(instrument_id)
        if not instrument.active or not instrument.backtest_enabled:
            raise ValueError("Instrument is not enabled for backtesting")
        completed_at = self.clock().astimezone(timezone.utc)
        start = completed_at - timedelta(days=duration_days)
        started_at = self.clock().astimezone(timezone.utc)
        candles = self.sync_candles(instrument, timeframe, start, completed_at)
        response = run_pullback_backtest(instrument, timeframe, candles, config.validate())
        response["metadata"].update(
            {
                "runId": "CRUN-" + uuid.uuid4().hex[:20].upper(),
                "durationDays": duration_days,
                "startedAt": iso_utc(started_at),
                "completedAt": iso_utc(completed_at),
                "dataStart": iso_utc(start),
                "dataEnd": iso_utc(completed_at),
                "dataSource": f"{instrument.provider} public REST candles",
            }
        )
        self.repository.save_backtest(response, started_at)
        return response

    def scan(self, timeframe: str = "5m") -> dict[str, Any]:
        if timeframe not in TIMEFRAME_SECONDS:
            raise ValueError("Unsupported timeframe")
        now = self.clock().astimezone(timezone.utc)
        lookback = max(5, round(TIMEFRAME_SECONDS[timeframe] * 600 / 86_400) + 1)
        created: list[dict[str, Any]] = []
        errors: list[dict[str, str]] = []
        instruments = [item for item in self.repository.instruments() if item.active and item.signals_enabled]
        for instrument in instruments:
            try:
                candles = self.sync_candles(instrument, timeframe, now - timedelta(days=lookback), now)
                frame = candle_frame(candles, CryptoPullbackConfig())
                candidates = generate_signals(frame, CryptoPullbackConfig())
                if candidates:
                    latest = candidates[-1]
                    maximum_age = timedelta(seconds=TIMEFRAME_SECONDS[timeframe] * 2)
                    if now - latest.signal_time <= maximum_age:
                        payload = signal_payload(instrument, timeframe, latest)
                        payload["createdAt"] = iso_utc(now)
                        if self.repository.save_signal(payload):
                            created.append(payload)
            except (MarketProviderError, OSError, ValueError) as error:
                errors.append({"instrumentId": instrument.instrument_id, "error": str(error)[:240]})
        with self._lock:
            self._last_scan = iso_utc(now)
            self._last_error = errors[0]["error"] if errors else None
            self._state = "DEGRADED" if errors else "READY"
            self._message = f"Scanned {len(instruments)} instruments; created {len(created)} new signals"
        return {
            "scannedAt": iso_utc(now),
            "timeframe": timeframe,
            "instrumentsScanned": len(instruments),
            "signalsCreated": len(created),
            "signals": created,
            "errors": errors,
            "paperOnly": True,
            "liveOrdersEnabled": False,
        }

    def signals(self, limit: int = 200) -> list[dict[str, Any]]:
        return self.repository.signals(limit)

    def status(self) -> dict[str, Any]:
        with self._lock:
            running = self._thread is not None and self._thread.is_alive()
            return {
                "engineStatus": self._state if running else "STOPPED",
                "message": self._message,
                "providers": self.provider_names(),
                "configuredInstruments": len([item for item in self.repository.instruments() if item.active]),
                "lastScan": self._last_scan,
                "lastError": self._last_error,
                "pollingSeconds": self.polling_seconds,
                "strategyKey": STRATEGY_KEY,
                "strategyName": STRATEGY_NAME,
                "strategyVersion": STRATEGY_VERSION,
                "paperOnly": True,
                "liveOrdersEnabled": False,
            }

    def start(self) -> None:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop.clear()
            self._state = "STARTING"
            self._message = "Crypto signal engine is starting"
            self._thread = threading.Thread(target=self._run, name="opendelta-crypto-signals", daemon=True)
            self._thread.start()

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self.scan("5m")
            except Exception as error:  # keep the paper scanner alive; expose sanitized state
                with self._lock:
                    self._state = "ERROR"
                    self._last_error = " ".join(str(error).split())[:240]
                    self._message = "Crypto signal scan failed; the next scheduled scan will retry"
            self._stop.wait(self.polling_seconds)
        with self._lock:
            self._state = "STOPPED"
            self._message = "Crypto signal engine is stopped"

    def stop(self, timeout: float = 10.0) -> None:
        self._stop.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout=timeout)
        with self._lock:
            self._state = "STOPPED"


def service_from_environment(
    canonical_writer: CanonicalCandleWriter | None = None,
) -> CryptoMarketService:
    default_root = Path(os.environ.get("BACKTEST_CACHE_DIR", "/var/lib/vento-nse/backtest")).expanduser() / "crypto-market"
    root = Path(os.environ.get("CRYPTO_MARKET_DIR", str(default_root))).expanduser()
    if not root.is_absolute():
        raise RuntimeError("CRYPTO_MARKET_DIR must be an absolute path")
    database = root / "market.sqlite3"
    polling = int(os.environ.get("CRYPTO_SIGNAL_POLL_SECONDS", "60"))
    providers = ProviderFactory(
        {
            "OKX": OkxPublicProvider(base_url=os.environ.get("OKX_PUBLIC_API_URL", "https://www.okx.com")),
            "VALR": ValrPublicProvider(base_url=os.environ.get("VALR_PUBLIC_API_URL", "https://api.valr.com")),
        }
    )
    return CryptoMarketService(
        CryptoMarketRepository(database),
        providers,
        polling_seconds=polling,
        canonical_writer=canonical_writer,
    )
