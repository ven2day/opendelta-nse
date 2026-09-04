"""Platform chrome routes: health and the /platform/* endpoints.

These endpoints are read by the shared navigation chrome (data-freshness pills,
environment) and the instrument master. They are deliberately research-free and
derive only from the unified platform runtime and the managed symbol sources,
so the retired legacy live-signal engine is not required.
"""

from __future__ import annotations

import os
import time
from datetime import datetime
from datetime import time as datetime_time
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query

from backend.collector import IST, DEFAULT_SYMBOLS_FILE, load_symbols
from backend.observability import get_logger
from backend.runtime import (
    get_crypto_market_service,
    get_platform_runtime,
    get_store,
)

MARKET_OPEN = datetime_time(9, 15)
MARKET_CLOSE = datetime_time(15, 30)
UNSUPPORTED_DATA_REQUIREMENT = "UNSUPPORTED_DATA_REQUIREMENT"
FRESH_DATA_AGE_SECONDS = 15 * 60

_booted_at: float | None = None


def set_boot_time(value: float) -> None:
    global _booted_at
    _booted_at = value


def _nse_session_is_open(now: datetime | None = None) -> bool:
    current = (now or datetime.now(IST)).astimezone(IST)
    return current.weekday() < 5 and MARKET_OPEN <= current.time() <= MARKET_CLOSE


def _legacy_engine_status_view() -> dict[str, Any]:
    """Shape the v2 NSE engine-status row like the previous engine status payload."""
    runtime = get_platform_runtime()
    if runtime.database is None:
        return {}
    row = next(
        (item for item in runtime.engine_status().list() if item["market"] == "NSE"),
        None,
    )
    if row is None:
        return {}
    return {
        "engineStatus": row.get("status") or "UNAVAILABLE",
        "connectionStatus": row.get("connectionStatus"),
        "dataAgeSeconds": row.get("dataAgeSeconds"),
        "lastCompletedCandle": row.get("lastCompletedCandle"),
        "marketSession": "OPEN" if _nse_session_is_open() else "CLOSED",
    }


def platform_overview_payload() -> dict[str, Any]:
    """Chrome overview payload; also consumed by the v2 dashboard router."""
    try:
        engine = _legacy_engine_status_view()
    except Exception:  # noqa: BLE001 - the chrome must degrade, never fail, on engine errors
        engine = {}
    age = engine.get("dataAgeSeconds")
    if engine.get("marketSession") == "OPEN":
        if age is None:
            freshness = {"status": "UNAVAILABLE", "ageSeconds": None, "reason": "NO_MARKET_DATA"}
        elif float(age) <= FRESH_DATA_AGE_SECONDS:
            freshness = {"status": "FRESH", "ageSeconds": age, "reason": "MARKET_OPEN"}
        else:
            freshness = {"status": "STALE", "ageSeconds": age, "reason": "MARKET_OPEN_DATA_LAGGING"}
    elif engine.get("lastCompletedCandle"):
        freshness = {"status": "FRESH", "ageSeconds": age, "reason": "MARKET_CLOSED_LAST_SESSION_CURRENT"}
    else:
        freshness = {"status": "UNAVAILABLE", "ageSeconds": age, "reason": "NO_COMPLETED_CANDLE"}
    engine_status = str(engine.get("engineStatus") or "UNAVAILABLE")
    worker = "RUNNING" if engine_status in {"READY", "RECOVERING"} else "STOPPED" if engine else "UNAVAILABLE"
    return {
        "environment": os.environ.get("OPENDELTA_ENVIRONMENT", "production"),
        "dataFreshness": freshness,
        "jobStatus": {"status": worker, "engineStatus": engine_status, "connectionStatus": engine.get("connectionStatus")},
        "paperOnly": True,
        "liveOrdersEnabled": False,
    }


def create_platform_router() -> APIRouter:
    router = APIRouter()

    @router.get("/health")
    def health() -> dict[str, Any]:
        try:
            symbols = len(load_symbols(Path(os.environ.get("SYMBOLS_FILE", DEFAULT_SYMBOLS_FILE))))
        except (OSError, ValueError) as error:
            raise HTTPException(status_code=503, detail=str(error)) from error
        payload: dict[str, Any] = {"status": "ok", "symbols": symbols}
        if _booted_at is not None:
            payload["uptimeSeconds"] = int(time.monotonic() - _booted_at)
        runtime = get_platform_runtime()
        payload["databaseConfigured"] = runtime.database is not None
        payload["candleReadMode"] = runtime.candle_read_mode
        if runtime.database is None:
            payload["database"] = None
        else:
            try:
                runtime.database.fetch_one("SELECT 1")
                payload["database"] = "ok"
            except Exception as error:  # noqa: BLE001 - health must still answer when the DB is down
                get_logger("opendelta.health").error("database_unreachable", reason=str(error))
                payload["database"] = "unavailable"
        return payload

    @router.get("/platform/overview")
    def platform_overview() -> dict[str, Any]:
        return platform_overview_payload()

    @router.get("/platform/instruments")
    def platform_instruments(
        market: str = Query(default="NSE"),
        offset: int = Query(default=0, ge=0),
        limit: int = Query(default=100, ge=1, le=1000),
    ) -> dict[str, Any]:
        market_key = market.strip().upper()
        if market_key == "NSE":
            rows = [
                {
                    "instrument_id": f"NSE:{symbol}",
                    "symbol": symbol,
                    "provider": "DHAN",
                    "provider_symbol": symbol,
                    "market_type": "EQUITY",
                    "trading_status": "ACTIVE",
                    "company_name": None,
                    "sector": None,
                }
                for symbol in get_store().universe()
            ]
        elif market_key == "CRYPTO":
            rows = [
                {
                    "instrument_id": instrument.instrument_id,
                    "symbol": instrument.display_symbol,
                    "provider": instrument.provider,
                    "provider_symbol": instrument.provider_symbol,
                    "market_type": instrument.instrument_type,
                    "trading_status": "ACTIVE" if instrument.active else "INACTIVE",
                    "company_name": None,
                    "sector": None,
                }
                for instrument in get_crypto_market_service().list_instruments()
            ]
        else:
            raise HTTPException(status_code=422, detail="market must be NSE or CRYPTO")
        return {"rows": rows[offset : offset + limit], "count": len(rows), "offset": offset, "limit": limit}

    @router.get("/platform/market-context")
    def platform_market_context(market: str = Query(default="NSE")) -> dict[str, Any]:
        market_key = market.strip().upper()
        if market_key == "NSE":
            session = {"status": "OPEN" if _nse_session_is_open() else "CLOSED", "timezone": "Asia/Kolkata"}
        elif market_key == "CRYPTO":
            session = {"status": "OPEN_24_7", "timezone": "UTC"}
        else:
            raise HTTPException(status_code=422, detail="market must be NSE or CRYPTO")
        unsupported = {"status": UNSUPPORTED_DATA_REQUIREMENT}
        return {
            "market": market_key,
            "session": session,
            "breadth": unsupported,
            "benchmarkDirection": unsupported,
            "sectorDirection": unsupported,
        }

    return router