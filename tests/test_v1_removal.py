"""Guards the narrow, evidence-backed V1 cleanup boundary."""

from __future__ import annotations

import inspect
from pathlib import Path

import backend.signals.configuration as signal_configuration
from backend.platform_runtime import PlatformRuntime

ROOT = Path(__file__).resolve().parents[1]
RETIRED_ENVIRONMENT_SETTINGS = {
    "CRYPTO_SIGNAL_ENGINE_ENABLED",
    "LIVE_SIGNAL_ENGINE_ENABLED",
    "RESEARCH_ENGINE_V2_ENABLED",
    "NSE_SIGNAL_ENGINE_V2_ENABLED",
    "NSE_PAPER_TRADING_V2_ENABLED",
    "NSE_LIVE_STRATEGIES",
    "NSE_LIVE_STRATEGY",
    "NSE_LIVE_TIMEFRAME",
    "CRYPTO_SIGNAL_ENGINE_V2_ENABLED",
    "CRYPTO_PAPER_TRADING_V2_ENABLED",
    "CRYPTO_LIVE_STRATEGIES",
    "CRYPTO_LIVE_STRATEGY",
    "CRYPTO_LIVE_TIMEFRAME",
}


def test_environment_deployment_parser_is_removed() -> None:
    assert not hasattr(signal_configuration, "live_strategy_bindings")
    assert not hasattr(PlatformRuntime, "live_bindings")
    parameters = inspect.signature(PlatformRuntime.build_signal_worker).parameters
    assert "binding" in parameters
    assert parameters["binding"].default is inspect.Parameter.empty
    assert "strategy_id" not in parameters
    assert "timeframe" not in parameters


def test_deployment_examples_and_service_do_not_set_retired_flags() -> None:
    paths = (
        ROOT / "web" / "deploy" / "opendelta-backtest.service",
        ROOT / "web" / "deploy" / "opendelta-dhan.env.example",
    )
    content = "\n".join(path.read_text(encoding="utf-8") for path in paths)
    assert not {name for name in RETIRED_ENVIRONMENT_SETTINGS if name in content}


def test_old_crypto_scanner_is_not_started_by_the_application_lifespan() -> None:
    source = (ROOT / "backend" / "app.py").read_text(encoding="utf-8")
    assert "get_crypto_market_service().start()" not in source
    assert "get_crypto_market_service" in source  # Public data routes remain installed.
