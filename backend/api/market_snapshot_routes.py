"""Live market-snapshot routes: status, CSV download, refresh, symbol addition.

These keep the shared RSI/volume snapshot CSV (consumed by price filters and
the screener universe) up to date, and add Dhan-validated symbols to the
managed registry.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from backend.api.schemas import MarketSymbolRequest
from backend.collector import (
    ConfigurationError,
    DEFAULT_SYMBOLS_FILE,
    DhanAPIError,
    DhanConfig,
    download_instrument_master,
)
from backend.data.symbol_registry import (
    MarketSymbolRegistry,
    SymbolAlreadyExistsError,
    SymbolNotFoundError,
)
from backend.runtime import get_market_data_refresh_service


def create_market_snapshot_router() -> APIRouter:
    router = APIRouter()

    @router.get("/market-data/status")
    def market_data_status() -> dict[str, Any]:
        try:
            return get_market_data_refresh_service().status()
        except (RuntimeError, ValueError) as error:
            raise HTTPException(status_code=503, detail=str(error)) from error

    @router.get("/market-data/csv")
    def market_data_csv() -> FileResponse:
        path = Path(
            os.environ.get(
                "LIVE_MARKET_DATA_FILE",
                "/var/lib/opendelta/data/nse_symbols_rsi_volume.csv",
            )
        ).expanduser()
        if not path.is_file():
            raise HTTPException(status_code=404, detail="Live market data is not available")
        return FileResponse(path, media_type="text/csv", filename="nse_symbols_rsi_volume.csv")

    @router.post("/market-data/refresh")
    def refresh_market_data() -> dict[str, Any]:
        try:
            return get_market_data_refresh_service().start()
        except (RuntimeError, ValueError) as error:
            raise HTTPException(status_code=503, detail=str(error)) from error

    @router.post("/market-data/symbols")
    async def add_market_symbol(request: MarketSymbolRequest) -> dict[str, Any]:
        """Add a Dhan-validated NSE equity and refresh the shared market snapshot."""
        service = get_market_data_refresh_service()
        if service.status()["running"]:
            raise HTTPException(
                status_code=409,
                detail="Wait for the current market-data refresh to finish before adding a symbol",
            )

        try:
            config = DhanConfig.from_environment()
            instruments = await asyncio.to_thread(
                download_instrument_master,
                config.instrument_master_url,
            )
            registry = MarketSymbolRegistry(config.symbols_file, DEFAULT_SYMBOLS_FILE)
            addition = await asyncio.to_thread(registry.add, request.symbol, instruments)
            refresh = service.start()
            return {
                "symbol": addition.symbol,
                "companyName": addition.company_name,
                "symbolCount": addition.symbol_count,
                "refresh": refresh,
            }
        except SymbolAlreadyExistsError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        except SymbolNotFoundError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        except (ConfigurationError, DhanAPIError, OSError, ValueError) as error:
            raise HTTPException(status_code=503, detail=str(error)) from error

    return router