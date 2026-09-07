"""Administration and Streamable HTTP MCP entry point for scoped research agents."""

from __future__ import annotations

import hmac
import json
import os
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator

from backend.agent.mcp import MAX_MCP_OUTPUT_BYTES, AgentToolError, MCPGateway
from backend.agent.repository import (
    AGENT_SCOPES,
    AgentAccessDenied,
    AgentRateLimit,
    AgentTokenRepository,
)
from backend.data.database import DatabaseUnavailable
from backend.monitoring.repository import MonitoringRepository

MAX_MCP_REQUEST_BYTES = 65_536
SUPPORTED_PROTOCOL_VERSIONS = frozenset({"2025-03-26", "2025-06-18", "2025-11-25"})
LATEST_PROTOCOL_VERSION = "2025-11-25"


class AgentTokenCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=120)
    scopes: list[str] = Field(min_length=1, max_length=len(AGENT_SCOPES))
    expiresAt: datetime
    rateLimitPerMinute: int = Field(default=60, ge=1, le=300)

    @field_validator("expiresAt")
    @classmethod
    def timezone_required(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Agent token expiry must include a timezone")
        return value


def create_agent_router(
    tokens: Callable[[], AgentTokenRepository],
    audit: Callable[[], MonitoringRepository],
) -> APIRouter:
    router = APIRouter(tags=["agent-access"])

    def repository() -> AgentTokenRepository:
        try:
            return tokens()
        except DatabaseUnavailable as error:
            raise HTTPException(status_code=503, detail=str(error)) from error

    def require_platform_admin(request: Request) -> None:
        expected = os.environ.get("BACKTEST_PROXY_TOKEN", "").strip()
        supplied = request.headers.get("x-opendelta-proxy-token", "").strip()
        if not expected:
            raise HTTPException(status_code=503, detail="Agent token administration is not configured")
        if not supplied or not hmac.compare_digest(supplied, expected):
            raise HTTPException(status_code=401, detail="Platform administrator authentication required")

    @router.get("/v2/agent/tokens")
    def list_tokens(request: Request, limit: int = 100) -> dict[str, Any]:
        require_platform_admin(request)
        if not 1 <= limit <= 200:
            raise HTTPException(status_code=422, detail="limit must be between 1 and 200")
        return {"tokens": repository().list(limit=limit), "availableScopes": sorted(AGENT_SCOPES)}

    @router.post("/v2/agent/tokens", status_code=201)
    def create_token(request: Request, values: AgentTokenCreateRequest) -> dict[str, Any]:
        require_platform_admin(request)
        try:
            record, raw_token = repository().create(
                name=values.name,
                scopes=values.scopes,
                expires_at=values.expiresAt.astimezone(UTC),
                created_by=request.headers.get("x-opendelta-actor", "platform-user"),
                rate_limit_per_minute=values.rateLimitPerMinute,
            )
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        return {
            **record,
            "token": raw_token,
            "tokenDisplay": "This token is shown once. Store it in a secret manager.",
        }

    @router.delete("/v2/agent/tokens/{token_id}")
    def revoke_token(token_id: str, request: Request) -> dict[str, Any]:
        require_platform_admin(request)
        try:
            return repository().revoke(token_id)
        except (KeyError, ValueError) as error:
            raise HTTPException(status_code=404, detail="Agent token was not found") from error

    @router.post("/mcp")
    async def mcp(request: Request) -> Response:
        origin = request.headers.get("origin", "").strip()
        allowed_origins = {
            item.strip()
            for item in os.environ.get("MCP_ALLOWED_ORIGINS", "").split(",")
            if item.strip()
        }
        if origin and origin not in allowed_origins:
            return _http_error(403, "ORIGIN_DENIED", "MCP request origin is not allowed")
        content_type = request.headers.get("content-type", "").split(";", 1)[0].strip().casefold()
        if content_type != "application/json":
            return _http_error(415, "CONTENT_TYPE_REQUIRED", "MCP requests must use application/json")
        try:
            content_length = int(request.headers.get("content-length", "0") or "0")
        except ValueError:
            return _http_error(400, "INVALID_CONTENT_LENGTH", "Invalid Content-Length header")
        if content_length > MAX_MCP_REQUEST_BYTES:
            return _http_error(413, "PAYLOAD_TOO_LARGE", "MCP request exceeded the 65,536-byte limit")
        authorization = request.headers.get("authorization", "")
        scheme, _, raw_token = authorization.partition(" ")
        if scheme.casefold() != "bearer" or not raw_token.strip():
            return _http_error(401, "AUTHENTICATION_REQUIRED", "A scoped Bearer token is required")
        try:
            token = repository().authenticate(raw_token.strip())
        except AgentAccessDenied:
            return _http_error(401, "INVALID_TOKEN", "The agent token is invalid, expired, or revoked")
        except AgentRateLimit as error:
            return _http_error(429, "RATE_LIMITED", str(error))
        body = await request.body()
        if not body or len(body) > MAX_MCP_REQUEST_BYTES:
            return _http_error(413, "PAYLOAD_TOO_LARGE", "MCP request is empty or too large")
        try:
            payload = json.loads(body)
        except json.JSONDecodeError:
            return JSONResponse(_rpc_error(None, -32700, "Parse error", "INVALID_JSON"), status_code=400)
        if not isinstance(payload, dict) or payload.get("jsonrpc") != "2.0":
            return JSONResponse(_rpc_error(payload.get("id") if isinstance(payload, dict) else None, -32600, "Invalid Request", "INVALID_REQUEST"), status_code=400)
        rpc_id = payload.get("id")
        method = payload.get("method")
        params = payload.get("params") or {}
        if not isinstance(method, str) or not isinstance(params, dict):
            return JSONResponse(_rpc_error(rpc_id, -32600, "Invalid Request", "INVALID_REQUEST"), status_code=400)
        gateway = MCPGateway(repository(), audit)
        try:
            if method == "initialize":
                requested_version = params.get("protocolVersion")
                protocol_version = (
                    requested_version
                    if isinstance(requested_version, str) and requested_version in SUPPORTED_PROTOCOL_VERSIONS
                    else LATEST_PROTOCOL_VERSION
                )
                result = {
                    "protocolVersion": protocol_version,
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": {"name": "opendelta-research", "version": "2.0"},
                    "instructions": "Research-only. No approval, deployment, credential, emergency-stop release, or live-order tools exist.",
                }
            elif method == "ping":
                result = {}
            elif method == "notifications/initialized":
                return Response(status_code=202)
            elif method == "tools/list":
                cursor = params.get("cursor")
                if cursor is not None and not isinstance(cursor, str):
                    raise AgentToolError("INVALID_CURSOR", "Tool cursor must be a string")
                result = gateway.tools_for(token, cursor)
            elif method == "tools/call":
                name = params.get("name")
                arguments = params.get("arguments", {})
                if not isinstance(name, str) or not isinstance(arguments, dict):
                    raise AgentToolError("INVALID_ARGUMENTS", "tools/call requires a name and object arguments")
                request_id = request.headers.get("x-request-id") or str(uuid.uuid4())
                output = await gateway.call(
                    app=request.app,
                    token=token,
                    name=name,
                    arguments=arguments,
                    request_id=request_id,
                )
                encoded = json.dumps(output, separators=(",", ":"), ensure_ascii=False, default=str)
                result = {
                    "content": [{"type": "text", "text": encoded}],
                    "structuredContent": output,
                    "isError": False,
                }
            else:
                return JSONResponse(_rpc_error(rpc_id, -32601, "Method not found", "METHOD_NOT_FOUND"), status_code=404)
        except AgentToolError as error:
            result = {
                "content": [{"type": "text", "text": json.dumps({"code": error.code, "message": str(error)})}],
                "isError": True,
            }
        response = {"jsonrpc": "2.0", "id": rpc_id, "result": result}
        if len(json.dumps(response, default=str).encode()) > MAX_MCP_OUTPUT_BYTES * 2:
            return JSONResponse(_rpc_error(rpc_id, -32001, "Output too large", "OUTPUT_TOO_LARGE"), status_code=413)
        return JSONResponse(response, headers={"cache-control": "private, no-store"})

    @router.get("/mcp")
    def mcp_sse_not_enabled() -> Response:
        return Response(status_code=405, headers={"allow": "POST", "cache-control": "private, no-store"})

    return router


def _rpc_error(rpc_id: Any, code: int, message: str, stable_code: str) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": rpc_id,
        "error": {"code": code, "message": message, "data": {"code": stable_code}},
    }


def _http_error(status: int, code: str, message: str) -> JSONResponse:
    headers = {"cache-control": "private, no-store"}
    if status == 401:
        headers["www-authenticate"] = "Bearer"
    return JSONResponse({"detail": message, "code": code}, status_code=status, headers=headers)
