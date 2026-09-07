"""Secret-free HTTP action auditing for the unified platform."""

from __future__ import annotations

import re
import uuid
from collections.abc import Awaitable, Callable

from fastapi import Request, Response

from backend.monitoring.repository import MonitoringRepository

_ACTIONS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("STRATEGY_VALIDATED", re.compile(r"^/v2/strategy-studio/validate$")),
    ("STRATEGY_DRAFT_SAVED", re.compile(r"^/v2/strategy-studio/sources$")),
    ("INDICATOR_VALIDATED", re.compile(r"^/v2/indicator-studio/validate$")),
    ("INDICATOR_DRAFT_SAVED", re.compile(r"^/v2/indicator-studio/sources$")),
    ("BACKTEST_CANCELLED", re.compile(r"^/v2/backtests/[^/]+$")),
    ("BACKTEST_CREATED", re.compile(r"^/v2/backtests$")),
    ("EXPERIMENT_CANCELLED", re.compile(r"^/v2/research/experiments/[^/]+$")),
    ("EXPERIMENT_CREATED", re.compile(r"^/v2/research/experiments(?:/from-preview)?$")),
    ("WALK_FORWARD_CANCELLED", re.compile(r"^/v2/research/walk-forward/[^/]+$")),
    ("WALK_FORWARD_CREATED", re.compile(r"^/v2/research/walk-forward(?:/from-preview)?$")),
    ("CREDENTIAL_CHANGED", re.compile(r"^/v2/connections(?:/[^/]+/(?:replace|disable|rotate|delete))?$")),
    ("CREDENTIAL_TESTED", re.compile(r"^/v2/connections/[^/]+/test$")),
    ("LIVE_DEPLOYMENT_CHANGED", re.compile(r"^/v2/live-execution/deployments")),
    ("EMERGENCY_STOP_CHANGED", re.compile(r"^/v2/live-execution/emergency-stops")),
    ("ORDER_INTENT_CHANGED", re.compile(r"^/v2/live-execution/orders")),
    ("AI_COPILOT_ACTION", re.compile(r"^/v2/ai/copilot")),
    ("AGENT_TOKEN_CHANGED", re.compile(r"^/v2/agent/tokens(?:/[^/]+)?$")),
    ("ALERT_CHANGED", re.compile(r"^/v2/operations/alerts/")),
    ("AGENT_ACTION", re.compile(r"^/mcp(?:/|$)")),
)


def install_audit_middleware(app: object, repository: Callable[[], MonitoringRepository]) -> None:
    @app.middleware("http")
    async def audit_actions(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        action = _action(request.method, request.url.path)
        if action is None:
            return await call_next(request)
        request_id = request.headers.get("x-request-id") or str(uuid.uuid4())
        actor_id = request.headers.get("x-opendelta-actor", "platform-user")[:200]
        try:
            response = await call_next(request)
        except Exception:
            _record(repository, request_id, action, actor_id, request.url.path, False, 500)
            raise
        _record(repository, request_id, action, actor_id, request.url.path, response.status_code < 400, response.status_code)
        return response


def _action(method: str, path: str) -> str | None:
    if method not in {"POST", "PUT", "PATCH", "DELETE"}:
        return None
    return next((action for action, pattern in _ACTIONS if pattern.search(path)), None)


def _record(
    repository: Callable[[], MonitoringRepository],
    request_id: str,
    action: str,
    actor: str,
    path: str,
    success: bool,
    status_code: int,
) -> None:
    try:
        identifier = path.rstrip("/").rsplit("/", 1)[-1]
        repository().append_audit(
            request_id=request_id,
            action=action,
            actor_type="USER",
            actor_id=actor,
            success=success,
            subject_type="HTTP_RESOURCE",
            subject_id=identifier[:200],
            details={"pathTemplate": _redacted_path(path), "statusCode": status_code},
        )
    except Exception:  # noqa: BLE001 - auditing must never break the governed action
        return


def _redacted_path(path: str) -> str:
    return re.sub(r"/[0-9a-fA-F-]{32,36}(?=/|$)", "/{id}", path)[:300]
