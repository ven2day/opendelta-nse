"""OpenDelta composition root.

This module is deliberately thin: it builds the FastAPI application, wires the
application lifespan, and mounts the route groups. All business logic lives in
the dedicated modules below; service singletons live in ``backend.runtime``.

Route surface (legacy research endpoints were retired with their engines):

* ``/health``, ``/platform/*``            chrome/health (``api.platform_routes``)
* ``/market-data/status|csv|refresh|symbols`` snapshot admin (``api.market_snapshot_routes``)
* ``/application-settings``, ``/market-data/symbols`` (GET) settings (``api.application_settings_routes``)
* ``/nifty-oi/history/status``            OI coverage status (``api.oi_routes``)
* ``/market-symbols/*`` and crypto routes (``markets.crypto.api``)
* ``/v2/*``                               unified NSE+Crypto platform (``platform_runtime.install_platform``)

The service is paper-only: it has no broker client and cannot place real orders.
"""

from __future__ import annotations

import os
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI

from backend.api.application_settings_routes import create_application_settings_router
from backend.api.market_snapshot_routes import create_market_snapshot_router
from backend.api.oi_routes import create_oi_router
from backend.api.platform_routes import create_platform_router, platform_overview_payload, set_boot_time
from backend.markets.crypto.api import create_crypto_router
from backend.observability import configure_logging, get_logger, install_observability
from backend.platform_runtime import install_platform
from backend.runtime import get_crypto_market_service, get_platform_runtime, shutdown_runtime


def _truthy(value: str | None) -> bool:
    return str(value or "").strip().casefold() in {"1", "true", "yes", "on"}


@asynccontextmanager
async def application_lifespan(_: FastAPI):
    configure_logging()
    app_logger = get_logger("opendelta.app")
    app_logger.info("startup_begin")
    get_platform_runtime().start()
    if _truthy(os.environ.get("CRYPTO_SIGNAL_ENGINE_ENABLED")):
        get_crypto_market_service().start()
    try:
        yield
    finally:
        app_logger.info("shutdown_begin")
        get_platform_runtime().stop()
        shutdown_runtime()
        app_logger.info("shutdown_complete")


app = FastAPI(
    title="OpenDelta Trading Research API",
    docs_url=None,
    redoc_url=None,
    lifespan=application_lifespan,
)
install_observability(app)
set_boot_time(time.monotonic())

# Operational (non-v2) routes: chrome/health, snapshot admin, settings, OI status.
app.router.routes.extend(create_platform_router().routes)
app.router.routes.extend(create_market_snapshot_router().routes)
app.router.routes.extend(create_application_settings_router().routes)
app.router.routes.extend(create_oi_router().routes)

# Crypto market-data routes (public exchange adapters only, paper-only service).
app.router.routes.extend(create_crypto_router(get_crypto_market_service).routes)

# Unified v2 platform: strategies, screener, backtests, live signals, paper trading.
install_platform(app, get_platform_runtime(), overview=platform_overview_payload)