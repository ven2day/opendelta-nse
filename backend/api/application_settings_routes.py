"""Application settings and managed-symbol-list routes.

The retired legacy ``/live-signals/settings`` endpoints are intentionally not
carried over; strategy configuration now lives entirely in the v2 platform
strategy settings under /v2/*.
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException

from backend.api.schemas import GlobalPriceSettingsRequest
from backend.collector import DEFAULT_SYMBOLS_FILE, load_symbols
from backend.config.application_settings import (
    DEFAULT_MAXIMUM_PRICE as GLOBAL_DEFAULT_MAXIMUM_PRICE,
    filter_symbols_by_price,
    prices_by_symbol,
)
from backend.runtime import get_application_settings_repository


def create_application_settings_router() -> APIRouter:
    router = APIRouter()

    @router.get("/application-settings")
    def application_settings() -> dict[str, object]:
        try:
            return get_application_settings_repository().get().public()
        except (OSError, RuntimeError, sqlite3.Error, ValueError) as error:
            raise HTTPException(status_code=503, detail="Application settings are temporarily unavailable") from error

    @router.put("/application-settings")
    def update_application_settings(request: GlobalPriceSettingsRequest) -> dict[str, object]:
        try:
            return get_application_settings_repository().update(
                request.minimumPrice,
                request.maximumPrice,
            ).public()
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        except (OSError, RuntimeError, sqlite3.Error) as error:
            raise HTTPException(status_code=503, detail="Application settings are temporarily unavailable") from error

    @router.get("/market-data/symbols")
    def list_market_symbols() -> dict[str, Any]:
        """Return the same managed symbol registry used by refreshes and backtests."""
        try:
            symbols_file = Path(os.environ.get("SYMBOLS_FILE", DEFAULT_SYMBOLS_FILE)).expanduser()
            symbols = load_symbols(symbols_file)
            settings = get_application_settings_repository().get()
            filter_applied = not (
                settings.minimum_price == 0
                and settings.maximum_price == GLOBAL_DEFAULT_MAXIMUM_PRICE
            )
            missing_price_count = 0
            filtered = list(symbols)
            if filter_applied:
                market_data = Path(
                    os.environ.get(
                        "LIVE_MARKET_DATA_FILE",
                        "/var/lib/vento-nse/data/nse_symbols_rsi_volume.csv",
                    )
                ).expanduser()
                filtered, missing_price_count = filter_symbols_by_price(
                    symbols,
                    prices_by_symbol(market_data),
                    settings,
                )
            return {
                "symbols": filtered,
                "symbolCount": len(filtered),
                "totalSymbolCount": len(symbols),
                "priceRange": settings.public()["priceRange"],
                "priceFilterApplied": filter_applied,
                "missingPriceCount": missing_price_count,
            }
        except (OSError, RuntimeError, sqlite3.Error, ValueError) as error:
            raise HTTPException(status_code=503, detail=str(error)) from error

    return router