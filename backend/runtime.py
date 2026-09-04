"""Module-level service singletons shared by the routes and the platform runtime.

Centralising the lazily-created singletons here keeps ``backend/app.py`` as a
thin composition root while preserving the exact previous behaviour: the first
caller constructs a service, every later caller reuses it, and callers that do
not need a service never trigger its construction.
"""

from __future__ import annotations

import os
import threading
from pathlib import Path

from backend.collector import DhanConfig
from backend.config.application_settings import ApplicationSettingsRepository
from backend.data.candle_repository import CanonicalCandleRepository
from backend.data.database import Database
from backend.data.store import HistoricalDataStore
from backend.data.timescale import TimescaleDualWriter, dual_writer_from_environment
from backend.markets.base import CandleSource
from backend.markets.crypto.engine import (
    CryptoMarketService,
    service_from_environment as crypto_service_from_environment,
)
from backend.markets.crypto.exchange_adapter import CryptoCandleSource
from backend.markets.nse.dhan_adapter import DhanCandleSource
from backend.markets.nse.oi_regime import OiRegimeRepository
from backend.markets.timescale_source import READ_MODES, TimescaleCandleSource, select_candle_source
from backend.platform_runtime import PlatformRuntime
from backend.data.refresh import MarketDataRefreshService

_store: HistoricalDataStore | None = None
_canonical_market_data_writer: TimescaleDualWriter | None = None
_application_settings_repository: ApplicationSettingsRepository | None = None
_crypto_market_service: CryptoMarketService | None = None
_oi_repository: OiRegimeRepository | None = None
_market_data_refresh_service: MarketDataRefreshService | None = None
_platform_runtime_instance: PlatformRuntime | None = None
_platform_runtime_lock = threading.Lock()


def get_canonical_market_data_writer() -> TimescaleDualWriter:
    global _canonical_market_data_writer
    if _canonical_market_data_writer is None:
        _canonical_market_data_writer = dual_writer_from_environment()
    return _canonical_market_data_writer


def create_store() -> HistoricalDataStore:
    config = DhanConfig.from_environment()
    cache_directory = Path(os.environ.get("BACKTEST_CACHE_DIR", "/var/lib/vento-nse/backtest")).expanduser()
    if not cache_directory.is_absolute():
        raise RuntimeError("BACKTEST_CACHE_DIR must be an absolute path")
    return HistoricalDataStore(config, cache_directory, get_canonical_market_data_writer())


def get_store() -> HistoricalDataStore:
    global _store
    if _store is None:
        _store = create_store()
    return _store


def get_application_settings_repository() -> ApplicationSettingsRepository:
    global _application_settings_repository
    if _application_settings_repository is None:
        default_root = Path(
            os.environ.get("BACKTEST_CACHE_DIR", "/var/lib/vento-nse/backtest")
        ).expanduser() / "application-settings"
        root = Path(os.environ.get("APPLICATION_SETTINGS_DIR", str(default_root))).expanduser()
        if not root.is_absolute():
            raise RuntimeError("APPLICATION_SETTINGS_DIR must be an absolute path")
        _application_settings_repository = ApplicationSettingsRepository(root)
    return _application_settings_repository


def get_crypto_market_service() -> CryptoMarketService:
    global _crypto_market_service
    if _crypto_market_service is None:
        _crypto_market_service = crypto_service_from_environment(
            get_canonical_market_data_writer()
        )
    return _crypto_market_service


def get_oi_repository() -> OiRegimeRepository:
    global _oi_repository
    if _oi_repository is None:
        default_root = Path(
            os.environ.get("BACKTEST_CACHE_DIR", "/var/lib/vento-nse/backtest")
        ).expanduser() / "nifty-oi"
        root = Path(os.environ.get("NIFTY_OI_DIR", str(default_root))).expanduser()
        if not root.is_absolute():
            raise RuntimeError("NIFTY_OI_DIR must be an absolute path")
        _oi_repository = OiRegimeRepository(root)
    return _oi_repository


def get_market_data_refresh_service() -> MarketDataRefreshService:
    global _market_data_refresh_service
    if _market_data_refresh_service is None:
        output_file = Path(
            os.environ.get(
                "LIVE_MARKET_DATA_FILE",
                "/var/lib/vento-nse/data/nse_symbols_rsi_volume.csv",
            )
        ).expanduser()
        _market_data_refresh_service = MarketDataRefreshService(output_file)
    return _market_data_refresh_service


def _nse_fallback_universe() -> list[str]:
    """The raw managed NSE symbol list, used until a screener universe is saved."""
    return get_store().universe()


def _nse_symbol_catalogue() -> list[str]:
    return get_store().universe()


def _crypto_signal_enabled_symbols() -> list[str]:
    return [
        item.display_symbol
        for item in get_crypto_market_service().list_instruments()
        if item.signals_enabled
    ]


def _crypto_symbol_catalogue() -> list[str]:
    return [item.display_symbol for item in get_crypto_market_service().list_instruments()]


def get_platform_runtime() -> PlatformRuntime:
    """The unified NSE+Crypto platform (v2 routes), sharing this process and database."""
    global _platform_runtime_instance
    with _platform_runtime_lock:
        if _platform_runtime_instance is None:
            database = Database.from_environment()
            read_mode = os.environ.get("PLATFORM_CANDLE_READ_MODE", "legacy").strip().lower()
            if read_mode not in READ_MODES:
                raise RuntimeError(
                    "PLATFORM_CANDLE_READ_MODE must be legacy, timescale, or timescale-fallback"
                )
            canonical_repository = CanonicalCandleRepository(database) if database is not None else None

            def candle_source(market: str) -> CandleSource:
                if read_mode == "legacy":
                    return (
                        DhanCandleSource(get_store())
                        if market == "NSE"
                        else CryptoCandleSource(get_crypto_market_service())
                    )
                if canonical_repository is None:
                    raise RuntimeError("Timescale candle reads require MARKET_DATA_DATABASE_URL")
                timescale = TimescaleCandleSource(
                    canonical_repository,
                    market=market,
                    provider="DHAN" if market == "NSE" else None,
                )
                if read_mode == "timescale":
                    return timescale
                legacy = (
                    DhanCandleSource(get_store())
                    if market == "NSE"
                    else CryptoCandleSource(get_crypto_market_service())
                )
                return select_candle_source(read_mode, timescale=timescale, legacy=legacy)

            _platform_runtime_instance = PlatformRuntime(
                database=database,
                candle_sources={
                    "NSE": lambda: candle_source("NSE"),
                    "CRYPTO": lambda: candle_source("CRYPTO"),
                },
                candle_read_mode=read_mode,
                fallback_universes={
                    # Until a screener universe is saved, fall back to the managed symbol list.
                    "NSE": _nse_fallback_universe,
                    "CRYPTO": _crypto_signal_enabled_symbols,
                },
                symbol_catalogues={
                    # The full configured universe the screener starts from.
                    "NSE": _nse_symbol_catalogue,
                    "CRYPTO": _crypto_symbol_catalogue,
                },
            )
        return _platform_runtime_instance


def shutdown_runtime() -> None:
    """Release long-lived services on interpreter shutdown."""
    global _canonical_market_data_writer, _crypto_market_service
    if _crypto_market_service is not None:
        _crypto_market_service.stop()
        _crypto_market_service = None
    if _market_data_refresh_service is not None:
        _market_data_refresh_service.shutdown()
    if _canonical_market_data_writer is not None:
        _canonical_market_data_writer.close()
        _canonical_market_data_writer = None