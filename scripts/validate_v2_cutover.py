"""Run the environment-free OpenDelta V2 release contract.

This is deliberately a structural and policy validation. Provider credentials,
production data and live order mutations are never required or attempted.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from backend.agent.mcp import TOOLS, MCPGateway
from backend.app import app
from backend.connections.providers import connection_testers
from backend.core.models import MARKET_TIMEZONES, MARKETS
from backend.live.adapters import DhanOrderAdapter, OkxOrderAdapter, ValrOrderAdapter
from backend.live.service import LiveExecutionConfig
from backend.markets.base import market_spec
from backend.runtime import DEFAULT_CANDLE_READ_MODE

ROOT = Path(__file__).resolve().parents[1]
REQUIRED_MIGRATIONS = {
    "012_strategy_sources.sql",
    "013_strategy_v2_backtests.sql",
    "014_strategy_v2_live.sql",
    "015_indicator_sources.sql",
    "016_research_experiments.sql",
    "017_parameter_experiments.sql",
    "018_walk_forward_validations.sql",
    "019_ai_research_copilot.sql",
    "020_secure_exchange_connections.sql",
    "021_live_execution_foundation.sql",
    "022_production_monitoring.sql",
    "023_agent_mcp_access.sql",
}
REQUIRED_ROUTES = {
    "/v2/strategy-studio/sources",
    "/v2/indicator-studio/sources",
    "/v2/backtests",
    "/v2/backtests/{run_id}/chart",
    "/v2/backtests/{run_id}/approve",
    "/v2/research/experiments/preview",
    "/v2/research/experiments/from-preview",
    "/v2/research/walk-forward/preview",
    "/v2/research/walk-forward/from-preview",
    "/v2/signals",
    "/v2/paper/orders",
    "/v2/connections",
    "/v2/live-execution/status",
    "/v2/operations/health",
    "/v2/ai/copilot/requests",
    "/v2/agent/tokens",
    "/mcp",
}
REQUIRED_AGENT_SCOPES = {
    "research:read",
    "backtests:submit",
    "experiments:submit",
    "walk-forward:submit",
    "ai:use",
    "monitoring:read",
}


@dataclass(frozen=True)
class ValidationCheck:
    name: str
    passed: bool
    detail: str


def _evaluate(name: str, check: Callable[[], str]) -> ValidationCheck:
    try:
        return ValidationCheck(name, True, check())
    except Exception as error:  # noqa: BLE001 - every failed contract is reported together
        return ValidationCheck(name, False, f"{type(error).__name__}: {error}")


def _markets() -> str:
    assert MARKETS == ("NSE", "CRYPTO")
    assert MARKET_TIMEZONES == {"NSE": "Asia/Kolkata", "CRYPTO": "UTC"}
    assert market_spec("NSE").currency == "INR"
    assert market_spec("CRYPTO").currency == "USDT"
    try:
        market_spec("FOREX")
    except ValueError:
        pass
    else:
        raise AssertionError("unsupported markets must fail closed")
    return "NSE/Asia-Kolkata and Crypto/UTC only"


def _providers() -> str:
    assert set(connection_testers({})) == {"OKX", "VALR"}
    assert {item.__name__ for item in (DhanOrderAdapter, OkxOrderAdapter, ValrOrderAdapter)} == {
        "DhanOrderAdapter",
        "OkxOrderAdapter",
        "ValrOrderAdapter",
    }
    return "Dhan, OKX and VALR adapters; OKX/VALR private tests"


def _migrations() -> str:
    actual = {path.name for path in (ROOT / "backend" / "data" / "sql").glob("*.sql")}
    missing = sorted(REQUIRED_MIGRATIONS - actual)
    assert not missing, f"missing migrations: {', '.join(missing)}"
    assert max(actual) == "023_agent_mcp_access.sql"
    return "immutable V2 schema sequence 012-023 present"


def _routes() -> str:
    actual = {getattr(route, "path", "") for route in app.routes}
    missing = sorted(REQUIRED_ROUTES - actual)
    assert not missing, f"missing routes: {', '.join(missing)}"
    retired = {
        "/backtest/run",
        "/research/run",
        "/live-signals/settings",
        "/api/backtest",
    }
    assert actual.isdisjoint(retired), "a retired V1 route was mounted"
    return f"{len(REQUIRED_ROUTES)} lifecycle routes present; retired entry points absent"


def _v2_defaults() -> str:
    assert DEFAULT_CANDLE_READ_MODE == "timescale"
    return "TimescaleDB is canonical and durable deployments are the signal/paper source of truth"


def _live_default() -> str:
    config = LiveExecutionConfig.from_environment({})
    assert config.live_trading_enabled is False
    assert config.deployment_permission is False
    assert config.environment_allowed is False
    assert config.public()["defaultState"] == "Live trading disabled"
    return "all independent live activation flags fail closed when unset"


def _agent_boundary() -> str:
    names = {tool.name for tool in TOOLS}
    assert names.isdisjoint(MCPGateway.forbidden_names)
    assert {tool.scope for tool in TOOLS} == REQUIRED_AGENT_SCOPES
    assert all(tool.arguments.model_json_schema().get("additionalProperties") is False for tool in TOOLS)
    forbidden_fragments = ("credential", "approve", "deploy", "place_order", "cancel_live", "emergency_stop")
    assert not any(fragment in name for name in names for fragment in forbidden_fragments)
    return f"{len(TOOLS)} strict research tools; no live/governance/credential capability"


def _isolated_runner() -> str:
    unit = (ROOT / "web" / "deploy" / "opendelta-strategy-runner.service").read_text(encoding="utf-8")
    source = (ROOT / "backend" / "strategies" / "source_v2.py").read_text(encoding="utf-8")
    assert "--network none" in unit
    for forbidden in ("socket", "subprocess", "requests", "pickle"):
        assert f'"{forbidden}"' in source
    return "runner service has no network; validator blocks host/network modules"


def collect_checks() -> tuple[ValidationCheck, ...]:
    return tuple(
        _evaluate(name, check)
        for name, check in (
            ("supported-markets", _markets),
            ("supported-providers", _providers),
            ("migration-chain", _migrations),
            ("v2-route-surface", _routes),
            ("v2-platform-defaults", _v2_defaults),
            ("live-disabled-default", _live_default),
            ("agent-safety-boundary", _agent_boundary),
            ("strategy-runner-isolation", _isolated_runner),
        )
    )


def validation_payload() -> dict[str, Any]:
    checks = collect_checks()
    return {
        "status": "PASS" if all(item.passed for item in checks) else "FAIL",
        "checks": [asdict(item) for item in checks],
        "productionMutationsAttempted": False,
        "externalCredentialsRequired": False,
    }


def main() -> int:
    payload = validation_payload()
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0 if payload["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
