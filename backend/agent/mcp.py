"""Narrow MCP tool catalogue backed only by existing OpenDelta V2 HTTP workflows."""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Mapping
from contextlib import suppress
from dataclasses import dataclass
from typing import Any, ClassVar
from urllib.parse import urlencode
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from backend.agent.repository import (
    AgentRequestConflict,
    AgentTokenRepository,
    canonical_request_hash,
)
from backend.api.ai_copilot_routes import CopilotRequest
from backend.api.backtest_routes import BacktestCreateRequest
from backend.api.research_routes import ResearchPreviewRequest, ResearchSubmissionRequest
from backend.api.walk_forward_routes import WalkForwardPreviewRequest, WalkForwardSubmissionRequest
from backend.monitoring.repository import MonitoringRepository

MAX_MCP_OUTPUT_BYTES = 524_288
TOOL_PAGE_SIZE = 10


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class EmptyArgs(StrictModel):
    pass


class PageArgs(StrictModel):
    market: str | None = Field(default=None, pattern="^(NSE|CRYPTO)$")
    limit: int = Field(default=50, ge=1, le=100)
    cursor: int = Field(default=0, ge=0, le=10_000)


class IndicatorListArgs(StrictModel):
    status: str | None = Field(default=None, pattern="^(VALIDATED|ARCHIVED)$")
    limit: int = Field(default=50, ge=1, le=100)
    cursor: int = Field(default=0, ge=0, le=10_000)


class StrategyMetadataArgs(StrictModel):
    strategyId: str | None = Field(default=None, pattern=r"^[A-Za-z][A-Za-z0-9_-]{0,79}$")
    strategySourceId: UUID | None = None
    market: str | None = Field(default=None, pattern="^(NSE|CRYPTO)$")

    @model_validator(mode="after")
    def exact_identity(self) -> StrategyMetadataArgs:
        if bool(self.strategyId) == bool(self.strategySourceId):
            raise ValueError("Provide exactly one strategyId or strategySourceId")
        return self


class SourceMetadataArgs(StrictModel):
    sourceId: UUID


class AgentBacktestPreviewRequest(BacktestCreateRequest):
    model_config = ConfigDict(extra="forbid")


class AgentBacktestSubmissionRequest(BacktestCreateRequest):
    model_config = ConfigDict(extra="forbid")
    idempotencyKey: str = Field(min_length=8, max_length=128, pattern=r"^[A-Za-z0-9_.:-]+$")


class RunArgs(StrictModel):
    runId: UUID


class TradeArgs(RunArgs):
    symbol: str | None = Field(default=None, max_length=80)
    status: str | None = Field(default=None, max_length=40)
    sort: str = Field(default="entryTimestamp", max_length=40)
    direction: str = Field(default="asc", pattern="^(asc|desc)$")
    limit: int = Field(default=100, ge=1, le=200)
    cursor: int = Field(default=0, ge=0, le=100_000)


class ExperimentArgs(StrictModel):
    experimentId: UUID


class ValidationArgs(StrictModel):
    validationId: UUID


class StrictResearchPreviewRequest(ResearchPreviewRequest):
    model_config = ConfigDict(extra="forbid")


class StrictResearchSubmissionRequest(ResearchSubmissionRequest):
    model_config = ConfigDict(extra="forbid")


class StrictWalkForwardPreviewRequest(WalkForwardPreviewRequest):
    model_config = ConfigDict(extra="forbid")


class StrictWalkForwardSubmissionRequest(WalkForwardSubmissionRequest):
    model_config = ConfigDict(extra="forbid")


class StrictCopilotRequest(CopilotRequest):
    model_config = ConfigDict(extra="forbid")


class AgentToolError(RuntimeError):
    def __init__(self, code: str, message: str, *, status: int = 422) -> None:
        super().__init__(message)
        self.code = code
        self.status = status


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    scope: str
    arguments: type[BaseModel]
    idempotent_submission: bool = False
    read_only: bool = True

    def public(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "inputSchema": self.arguments.model_json_schema(),
            "annotations": {
                "readOnlyHint": self.read_only,
                "destructiveHint": False,
                "idempotentHint": self.idempotent_submission,
                "openWorldHint": self.name == "opendelta_explain_research",
            },
        }


TOOLS: tuple[ToolSpec, ...] = (
    ToolSpec("opendelta_list_strategies", "List built-in and immutable strategy metadata.", "research:read", PageArgs),
    ToolSpec("opendelta_read_strategy", "Read one strategy's immutable metadata without source code.", "research:read", StrategyMetadataArgs),
    ToolSpec("opendelta_list_indicators", "List immutable indicator metadata.", "research:read", IndicatorListArgs),
    ToolSpec("opendelta_read_indicator", "Read one indicator's immutable metadata without source code.", "research:read", SourceMetadataArgs),
    ToolSpec("opendelta_list_watchlists", "List bounded saved watchlist snapshots.", "research:read", PageArgs),
    ToolSpec("opendelta_preview_backtest", "Validate a research backtest without creating a run.", "research:read", AgentBacktestPreviewRequest),
    ToolSpec("opendelta_submit_backtest", "Submit one research backtest to the bounded worker pool.", "backtests:submit", AgentBacktestSubmissionRequest, idempotent_submission=True, read_only=False),
    ToolSpec("opendelta_poll_backtest", "Poll one immutable backtest run.", "research:read", RunArgs),
    ToolSpec("opendelta_read_backtest_metrics", "Read status, metrics, and pinned configuration for one run.", "research:read", RunArgs),
    ToolSpec("opendelta_read_backtest_trades", "Read a bounded page of recorded trades.", "research:read", TradeArgs),
    ToolSpec("opendelta_preview_experiment", "Preview a controlled parameter experiment without side effects.", "experiments:submit", StrictResearchPreviewRequest),
    ToolSpec("opendelta_submit_experiment", "Submit a previously previewed parameter experiment.", "experiments:submit", StrictResearchSubmissionRequest, idempotent_submission=True, read_only=False),
    ToolSpec("opendelta_poll_experiment", "Poll an experiment and exact child runs.", "research:read", ExperimentArgs),
    ToolSpec("opendelta_read_comparison", "Read status-aware experiment comparison inputs.", "research:read", ExperimentArgs),
    ToolSpec("opendelta_preview_walk_forward", "Preview walk-forward folds and workload without side effects.", "walk-forward:submit", StrictWalkForwardPreviewRequest),
    ToolSpec("opendelta_submit_walk_forward", "Submit a previously previewed walk-forward validation.", "walk-forward:submit", StrictWalkForwardSubmissionRequest, idempotent_submission=True, read_only=False),
    ToolSpec("opendelta_poll_walk_forward", "Poll training and unseen walk-forward results.", "research:read", ValidationArgs),
    ToolSpec("opendelta_explain_research", "Request a research-only AI explanation when configured.", "ai:use", StrictCopilotRequest, read_only=False),
    ToolSpec("opendelta_read_monitoring", "Read the current secret-free Operations health snapshot.", "monitoring:read", EmptyArgs),
)
TOOL_BY_NAME = {tool.name: tool for tool in TOOLS}


class MCPGateway:
    forbidden_names: ClassVar[frozenset[str]] = frozenset(
        {
            "enable_live_trading",
            "disable_emergency_stop",
            "approve_signals",
            "approve_paper",
            "deploy_strategy",
            "place_order",
            "cancel_live_order",
            "read_credentials",
            "execute_python",
            "execute_shell",
        }
    )

    def __init__(
        self,
        tokens: AgentTokenRepository,
        audit: Callable[[], MonitoringRepository],
    ) -> None:
        self.tokens = tokens
        self.audit = audit

    def tools_for(self, token: Mapping[str, Any], cursor: str | None) -> dict[str, Any]:
        try:
            offset = int(cursor or "0")
        except ValueError as error:
            raise AgentToolError("INVALID_CURSOR", "Tool cursor is invalid") from error
        if offset < 0:
            raise AgentToolError("INVALID_CURSOR", "Tool cursor is invalid")
        scopes = set(token["scopes"])
        allowed = [tool for tool in TOOLS if tool.scope in scopes]
        page = allowed[offset : offset + TOOL_PAGE_SIZE]
        next_offset = offset + len(page)
        return {
            "tools": [tool.public() for tool in page],
            **({"nextCursor": str(next_offset)} if next_offset < len(allowed) else {}),
        }

    async def call(
        self,
        *,
        app: Any,
        token: Mapping[str, Any],
        name: str,
        arguments: Mapping[str, Any],
        request_id: str,
    ) -> dict[str, Any]:
        tool = TOOL_BY_NAME.get(name)
        if tool is None:
            raise AgentToolError("UNKNOWN_TOOL", "Unknown OpenDelta tool", status=404)
        if tool.scope not in set(token["scopes"]):
            self._audit(token, request_id, name, False, "SCOPE_DENIED")
            raise AgentToolError("SCOPE_DENIED", f"Token requires the {tool.scope} scope", status=403)
        try:
            parsed = tool.arguments.model_validate(dict(arguments))
        except ValidationError as error:
            self._audit(token, request_id, name, False, "INVALID_ARGUMENTS")
            raise AgentToolError("INVALID_ARGUMENTS", _validation_message(error)) from error
        values = parsed.model_dump(mode="json", exclude_none=True)
        request_record: dict[str, Any] | None = None
        if tool.idempotent_submission:
            idempotency_key = str(values.get("idempotencyKey", ""))
            try:
                request_record, created = self.tokens.begin_tool_request(
                    token_id=str(token["tokenId"]),
                    tool_name=name,
                    idempotency_key=idempotency_key,
                    request_hash=canonical_request_hash(values),
                )
            except AgentRequestConflict as error:
                self._audit(token, request_id, name, False, "IDEMPOTENCY_CONFLICT")
                raise AgentToolError("IDEMPOTENCY_CONFLICT", str(error), status=409) from error
            if not created:
                if request_record["status"] == "COMPLETE":
                    self._audit(token, request_id, name, True, None)
                    return dict(request_record["response"])
                if request_record["status"] == "FAILED":
                    saved = request_record["error"] or {}
                    self._audit(token, request_id, name, False, str(saved.get("code", "PREVIOUS_REQUEST_FAILED")))
                    raise AgentToolError(
                        str(saved.get("code", "PREVIOUS_REQUEST_FAILED")),
                        str(saved.get("message", "The previous request failed")),
                        status=int(saved.get("status", 409)),
                    )
                raise AgentToolError(
                    "REQUEST_IN_PROGRESS",
                    "This idempotent request is already in progress or requires reconciliation",
                    status=409,
                )
        try:
            result = await self._invoke(app, tool.name, values, request_id, str(token["tokenId"]))
            safe = _safe_output(result)
            _bounded_output(safe)
            if request_record is not None:
                self.tokens.complete_tool_request(request_record["requestId"], safe)
            self._audit(token, request_id, name, True, None)
            return safe
        except AgentToolError as error:
            if request_record is not None:
                self.tokens.fail_tool_request(
                    request_record["requestId"],
                    {"code": error.code, "message": str(error), "status": error.status},
                )
            self._audit(token, request_id, name, False, error.code)
            raise
        except Exception as error:
            failure = AgentToolError("INTERNAL_ERROR", "The OpenDelta tool failed safely", status=500)
            if request_record is not None:
                with suppress(Exception):
                    self.tokens.fail_tool_request(
                        request_record["requestId"],
                        {"code": failure.code, "message": str(failure), "status": failure.status},
                    )
            self._audit(token, request_id, name, False, failure.code)
            raise failure from error

    async def _invoke(
        self,
        app: Any,
        name: str,
        values: dict[str, Any],
        request_id: str,
        token_id: str,
    ) -> dict[str, Any]:
        actor = f"agent:{token_id}"
        if name == "opendelta_list_strategies":
            built_in = await _dispatch(app, "GET", "/v2/strategies", query=_query(values, "market"), request_id=request_id, actor=actor)
            sources = await _dispatch(app, "GET", "/v2/strategy-studio/sources", query=_query(values, "market"), request_id=request_id, actor=actor)
            return _page({"builtIn": built_in.get("strategies", []), "immutable": sources.get("sources", [])}, values)
        if name == "opendelta_read_strategy":
            if values.get("strategySourceId"):
                return await _dispatch(app, "GET", f"/v2/strategy-studio/sources/{values['strategySourceId']}", request_id=request_id, actor=actor)
            listing = await _dispatch(app, "GET", "/v2/strategies", query=_query(values, "market"), request_id=request_id, actor=actor)
            match = next((item for item in listing.get("strategies", []) if item.get("strategyId") == values["strategyId"]), None)
            if match is None:
                raise AgentToolError("NOT_FOUND", "Strategy was not found", status=404)
            return dict(match)
        if name == "opendelta_list_indicators":
            result = await _dispatch(app, "GET", "/v2/indicator-studio/sources", query=_query(values, "status"), request_id=request_id, actor=actor)
            return _page({"items": result.get("sources", [])}, values)
        if name == "opendelta_read_indicator":
            return await _dispatch(app, "GET", f"/v2/indicator-studio/sources/{values['sourceId']}", request_id=request_id, actor=actor)
        if name == "opendelta_list_watchlists":
            result = await _dispatch(app, "GET", "/v2/screener/universes", query={**_query(values, "market"), "limit": "100"}, request_id=request_id, actor=actor)
            return _page({"items": result.get("universes", []), "active": result.get("active", {})}, values)
        if name == "opendelta_preview_backtest":
            version = await self._strategy_version(app, values, request_id, actor)
            body = {
                "name": "Agent backtest preview",
                "mode": "MANUAL",
                "market": values["market"],
                "strategyId": values["strategyId"],
                "strategyVersion": version,
                "strategySourceId": values.get("strategySourceId"),
                "universePresetId": values.get("universePresetId"),
                "symbols": values.get("symbols", []),
                "timeframe": values["timeframe"],
                "startDate": values["startDate"],
                "endDate": values["endDate"],
                "configuration": {},
                "execution": {},
                "variants": [{"name": "Backtest", "configuration": values.get("configuration", {}), "execution": values.get("execution", {})}],
                "parameters": [],
            }
            return await _dispatch(app, "POST", "/v2/research/experiments/preview", body=body, request_id=request_id, actor=actor)
        if name == "opendelta_submit_backtest":
            body = {key: value for key, value in values.items() if key != "idempotencyKey"}
            return await _dispatch(app, "POST", "/v2/backtests", body=body, request_id=request_id, actor=actor)
        if name == "opendelta_poll_backtest":
            return await _dispatch(app, "GET", f"/v2/backtests/{values['runId']}", request_id=request_id, actor=actor)
        if name == "opendelta_read_backtest_metrics":
            run = await _dispatch(app, "GET", f"/v2/backtests/{values['runId']}", request_id=request_id, actor=actor)
            return {key: run.get(key) for key in ("runId", "status", "market", "strategyId", "strategyVersion", "strategySourceId", "timeframe", "symbols", "configurationSnapshot", "executionSettings", "metrics", "failedSymbols", "createdAt", "completedAt")}
        if name == "opendelta_read_backtest_trades":
            query = {key: value for key, value in values.items() if key not in {"runId", "cursor"}}
            query["offset"] = values.get("cursor", 0)
            return await _dispatch(app, "GET", f"/v2/backtests/{values['runId']}/trades", query=query, request_id=request_id, actor=actor)
        if name == "opendelta_preview_experiment":
            return await _dispatch(app, "POST", "/v2/research/experiments/preview", body=values, request_id=request_id, actor=actor)
        if name == "opendelta_submit_experiment":
            return await _dispatch(app, "POST", "/v2/research/experiments/from-preview", body=values, request_id=request_id, actor=actor)
        if name in {"opendelta_poll_experiment", "opendelta_read_comparison"}:
            return await _dispatch(app, "GET", f"/v2/research/experiments/{values['experimentId']}", request_id=request_id, actor=actor)
        if name == "opendelta_preview_walk_forward":
            return await _dispatch(app, "POST", "/v2/research/walk-forward/preview", body=values, request_id=request_id, actor=actor)
        if name == "opendelta_submit_walk_forward":
            return await _dispatch(app, "POST", "/v2/research/walk-forward/from-preview", body=values, request_id=request_id, actor=actor)
        if name == "opendelta_poll_walk_forward":
            return await _dispatch(app, "GET", f"/v2/research/walk-forward/{values['validationId']}", request_id=request_id, actor=actor)
        if name == "opendelta_explain_research":
            return await _dispatch(app, "POST", "/v2/ai/copilot/requests", body=values, request_id=request_id, actor=actor)
        if name == "opendelta_read_monitoring":
            return await _dispatch(app, "GET", "/v2/operations/health", request_id=request_id, actor=actor)
        raise AgentToolError("UNKNOWN_TOOL", "Unknown OpenDelta tool", status=404)

    async def _strategy_version(self, app: Any, values: Mapping[str, Any], request_id: str, actor: str) -> str:
        if values.get("strategySourceId"):
            source = await _dispatch(app, "GET", f"/v2/strategy-studio/sources/{values['strategySourceId']}", request_id=request_id, actor=actor)
            return str(source["strategyVersion"])
        listing = await _dispatch(app, "GET", "/v2/strategies", query={"market": values["market"]}, request_id=request_id, actor=actor)
        match = next((item for item in listing.get("strategies", []) if item.get("strategyId") == values["strategyId"]), None)
        if match is None:
            raise AgentToolError("NOT_FOUND", "Strategy was not found", status=404)
        return str(match["version"])

    def _audit(
        self,
        token: Mapping[str, Any],
        request_id: str,
        tool_name: str,
        success: bool,
        error_code: str | None,
    ) -> None:
        try:
            self.audit().append_audit(
                request_id=request_id,
                action="MCP_AGENT_ACTION",
                actor_type="AGENT",
                actor_id=str(token["tokenId"]),
                success=success,
                subject_type="MCP_TOOL",
                subject_id=tool_name,
                details={"scope": TOOL_BY_NAME.get(tool_name).scope if tool_name in TOOL_BY_NAME else None, "errorCode": error_code},
            )
        except Exception:  # noqa: BLE001 - audit outage must not broaden or mutate tool access
            return


async def _dispatch(
    app: Any,
    method: str,
    path: str,
    *,
    body: Mapping[str, Any] | None = None,
    query: Mapping[str, Any] | None = None,
    request_id: str,
    actor: str,
) -> dict[str, Any]:
    encoded = json.dumps(body, separators=(",", ":"), default=str).encode() if body is not None else b""
    query_string = urlencode({key: value for key, value in (query or {}).items() if value is not None}).encode()
    messages: list[dict[str, Any]] = []
    received = False

    async def receive() -> dict[str, Any]:
        nonlocal received
        if received:
            return {"type": "http.disconnect"}
        received = True
        return {"type": "http.request", "body": encoded, "more_body": False}

    async def send(message: dict[str, Any]) -> None:
        messages.append(message)

    headers = [(b"x-request-id", request_id.encode()), (b"x-opendelta-actor", actor.encode())]
    if body is not None:
        headers.append((b"content-type", b"application/json"))
    scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": method,
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": query_string,
        "headers": headers,
        "client": ("127.0.0.1", 0),
        "server": ("opendelta-internal", 80),
        "root_path": "",
    }
    await app(scope, receive, send)
    start = next((item for item in messages if item["type"] == "http.response.start"), None)
    if start is None:
        raise AgentToolError("INTERNAL_DISPATCH_FAILED", "OpenDelta workflow did not return a response", status=503)
    status = int(start["status"])
    raw = b"".join(item.get("body", b"") for item in messages if item["type"] == "http.response.body")
    try:
        payload = json.loads(raw or b"{}")
    except json.JSONDecodeError as error:
        raise AgentToolError("INVALID_UPSTREAM_RESPONSE", "OpenDelta workflow returned invalid JSON", status=502) from error
    if status >= 400:
        detail = payload.get("detail", "OpenDelta workflow rejected the request") if isinstance(payload, dict) else str(payload)
        raise AgentToolError(_status_code(status), _detail_text(detail), status=status)
    if not isinstance(payload, dict):
        raise AgentToolError("INVALID_UPSTREAM_RESPONSE", "OpenDelta workflow returned an unexpected response", status=502)
    return payload


def _safe_output(value: Any) -> Any:
    forbidden = {"sourcecode", "token", "tokenhash", "secret", "password", "passphrase", "credential", "authorization"}
    if isinstance(value, Mapping):
        return {
            str(key): _safe_output(item)
            for key, item in value.items()
            if re.sub(r"[^a-z]", "", str(key).casefold()) not in forbidden
            and not any(part in str(key).casefold() for part in ("api_key", "apikey"))
        }
    if isinstance(value, list):
        return [_safe_output(item) for item in value]
    return value


def _bounded_output(value: Mapping[str, Any]) -> None:
    if len(json.dumps(value, separators=(",", ":"), default=str).encode()) > MAX_MCP_OUTPUT_BYTES:
        raise AgentToolError("OUTPUT_TOO_LARGE", "Tool output exceeded the 524,288-byte limit", status=413)


def _query(values: Mapping[str, Any], *keys: str) -> dict[str, Any]:
    return {key: values[key] for key in keys if key in values}


def _page(result: dict[str, Any], values: Mapping[str, Any]) -> dict[str, Any]:
    key = "items" if "items" in result else "builtIn"
    immutable = list(result.get("immutable", []))
    combined = list(result.get(key, [])) + immutable
    offset = int(values.get("cursor", 0))
    limit = int(values.get("limit", 50))
    page = combined[offset : offset + limit]
    response = {"items": page, "cursor": offset, "limit": limit, "total": len(combined)}
    if offset + len(page) < len(combined):
        response["nextCursor"] = offset + len(page)
    if "active" in result:
        response["active"] = result["active"]
    return response


def _validation_message(error: ValidationError) -> str:
    first = error.errors(include_url=False)[0]
    location = ".".join(str(item) for item in first.get("loc", ()))
    return f"{location}: {first['msg']}" if location else str(first["msg"])


def _status_code(status: int) -> str:
    return {404: "NOT_FOUND", 409: "CONFLICT", 413: "PAYLOAD_TOO_LARGE", 429: "RATE_LIMITED", 503: "UNAVAILABLE"}.get(status, "INVALID_REQUEST")


def _detail_text(detail: Any) -> str:
    if isinstance(detail, str):
        return detail[:2_000]
    return json.dumps(detail, separators=(",", ":"), default=str)[:2_000]
