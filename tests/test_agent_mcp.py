from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from backend.agent.mcp import TOOLS, MCPGateway
from backend.agent.repository import AgentAccessDenied, AgentRateLimit
from backend.api.agent_routes import create_agent_router
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient


class FakeTokens:
    def __init__(self, scopes: list[str] | None = None) -> None:
        self.scopes = scopes or ["research:read"]
        self.rate_limited = False
        self.requests: dict[tuple[str, str], dict[str, Any]] = {}

    def authenticate(self, raw: str) -> dict[str, Any]:
        if raw == "bad":
            raise AgentAccessDenied("bad")
        if self.rate_limited:
            raise AgentRateLimit("Agent request rate limit exceeded")
        return {"tokenId": "00000000-0000-0000-0000-000000000001", "scopes": self.scopes}

    def create(self, **_: Any) -> tuple[dict[str, Any], str]:
        return self._public(), "odt_once-only-secret"

    def list(self, *, limit: int) -> list[dict[str, Any]]:
        return [self._public()][:limit]

    def revoke(self, token_id: str) -> dict[str, Any]:
        return {**self._public(), "tokenId": token_id, "revokedAt": datetime.now(UTC).isoformat()}

    def begin_tool_request(self, *, tool_name: str, idempotency_key: str, request_hash: str, **_: Any):
        key = (tool_name, idempotency_key)
        if key in self.requests:
            row = self.requests[key]
            if row["requestHash"] != request_hash:
                from backend.agent.repository import AgentRequestConflict

                raise AgentRequestConflict("different request")
            return row, False
        row = {
            "requestId": str(uuid.uuid4()),
            "requestHash": request_hash,
            "status": "PENDING",
            "response": None,
            "error": None,
        }
        self.requests[key] = row
        return row, True

    def complete_tool_request(self, request_id: str, response: dict[str, Any]) -> None:
        row = next(item for item in self.requests.values() if item["requestId"] == request_id)
        row.update(status="COMPLETE", response=response)

    def fail_tool_request(self, request_id: str, error: dict[str, Any]) -> None:
        row = next(item for item in self.requests.values() if item["requestId"] == request_id)
        row.update(status="FAILED", error=error)

    @staticmethod
    def _public() -> dict[str, Any]:
        stamp = datetime.now(UTC).isoformat()
        return {
            "tokenId": "00000000-0000-0000-0000-000000000001",
            "name": "Codex research",
            "tokenPrefix": "odt_example",
            "scopes": ["research:read"],
            "rateLimitPerMinute": 60,
            "expiresAt": (datetime.now(UTC) + timedelta(days=1)).isoformat(),
            "revokedAt": None,
            "lastUsedAt": None,
            "createdBy": "owner",
            "createdAt": stamp,
        }


class FakeAudit:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def append_audit(self, **values: Any) -> dict[str, Any]:
        self.events.append(values)
        return values


def setup(scopes: list[str] | None = None) -> tuple[TestClient, FakeTokens, FakeAudit, dict[str, int]]:
    app = FastAPI()
    tokens = FakeTokens(scopes)
    audit = FakeAudit()
    calls = {"backtests": 0}

    @app.get("/v2/strategies")
    def strategies() -> dict[str, Any]:
        return {"strategies": [{"strategyId": "rsi_dip_ladder", "version": "1.0.0"}]}

    @app.get("/v2/strategy-studio/sources")
    def strategy_sources() -> dict[str, Any]:
        return {"sources": [{"sourceId": "00000000-0000-0000-0000-000000000002", "strategyId": "custom"}]}

    @app.get("/v2/strategy-studio/sources/{source_id}")
    def strategy_source(source_id: str) -> dict[str, Any]:
        return {"sourceId": source_id, "strategyVersion": "2.0.0", "sourceCode": "never return me", "apiKey": "never"}

    @app.post("/v2/backtests")
    async def backtests(request: Request) -> dict[str, Any]:
        calls["backtests"] += 1
        body = await request.json()
        return {"runId": "00000000-0000-0000-0000-000000000003", "status": "QUEUED", "symbols": body["symbols"]}

    app.router.routes.extend(create_agent_router(lambda: tokens, lambda: audit).routes)
    return TestClient(app), tokens, audit, calls


def rpc(client: TestClient, method: str, params: dict[str, Any] | None = None, *, token: str = "odt_test"):
    return client.post(
        "/mcp",
        headers={"authorization": f"Bearer {token}", "content-type": "application/json"},
        json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}},
    )


def test_token_admin_is_proxy_protected_and_shows_secret_once(monkeypatch) -> None:
    client, _, _, _ = setup()
    monkeypatch.setenv("BACKTEST_PROXY_TOKEN", "internal-proxy-secret")
    body = {
        "name": "Codex research",
        "scopes": ["research:read"],
        "expiresAt": (datetime.now(UTC) + timedelta(days=1)).isoformat(),
    }
    assert client.post("/v2/agent/tokens", json=body).status_code == 401
    created = client.post(
        "/v2/agent/tokens",
        headers={"x-opendelta-proxy-token": "internal-proxy-secret"},
        json=body,
    )
    assert created.status_code == 201
    assert created.json()["token"] == "odt_once-only-secret"
    listed = client.get("/v2/agent/tokens", headers={"x-opendelta-proxy-token": "internal-proxy-secret"})
    assert listed.status_code == 200
    assert all("token" not in item for item in listed.json()["tokens"])
    assert "odt_once-only-secret" not in json.dumps(listed.json())


def test_mcp_authentication_rate_limit_and_tool_pagination() -> None:
    client, tokens, _, _ = setup(["research:read", "monitoring:read"])
    assert rpc(client, "initialize", token="bad").status_code == 401
    page = rpc(client, "tools/list")
    assert page.status_code == 200
    names = {item["name"] for item in page.json()["result"]["tools"]}
    assert "opendelta_poll_backtest" in names
    assert not names & {"place_order", "approve_signals", "read_credentials", "execute_shell"}
    assert "nextCursor" in page.json()["result"]
    tokens.rate_limited = True
    assert rpc(client, "ping").status_code == 429


def test_every_tool_has_a_strict_schema_and_forbidden_authority_is_absent() -> None:
    assert all(tool.arguments.model_json_schema().get("additionalProperties") is False for tool in TOOLS)
    assert not {tool.name for tool in TOOLS} & MCPGateway.forbidden_names
    assert all(tool.public()["description"] and tool.public()["annotations"] for tool in TOOLS)


def test_mcp_strict_validation_scope_enforcement_and_metadata_redaction() -> None:
    client, _, audit, calls = setup(["research:read"])
    source = rpc(
        client,
        "tools/call",
        {"name": "opendelta_read_strategy", "arguments": {"strategySourceId": "00000000-0000-0000-0000-000000000002"}},
    ).json()["result"]
    assert source["isError"] is False
    assert "never return me" not in json.dumps(source)
    invalid = rpc(
        client,
        "tools/call",
        {"name": "opendelta_poll_backtest", "arguments": {"runId": "00000000-0000-0000-0000-000000000003", "extra": True}},
    ).json()["result"]
    assert invalid["isError"] is True
    assert "INVALID_ARGUMENTS" in invalid["content"][0]["text"]
    denied = rpc(
        client,
        "tools/call",
        {
            "name": "opendelta_submit_backtest",
            "arguments": {
                "market": "NSE",
                "strategyId": "rsi_dip_ladder",
                "symbols": ["TCS"],
                "timeframe": "5m",
                "startDate": "2026-01-01",
                "endDate": "2026-01-02",
                "idempotencyKey": "agent-test-1",
            },
        },
    ).json()["result"]
    assert denied["isError"] is True
    assert "SCOPE_DENIED" in denied["content"][0]["text"]
    assert calls["backtests"] == 0
    assert {item["actor_type"] for item in audit.events} == {"AGENT"}


def test_agent_submission_is_at_most_once_under_retry() -> None:
    client, _, audit, calls = setup(["backtests:submit"])
    arguments = {
        "market": "NSE",
        "strategyId": "rsi_dip_ladder",
        "symbols": ["TCS"],
        "timeframe": "5m",
        "startDate": "2026-01-01",
        "endDate": "2026-01-02",
        "idempotencyKey": "agent-test-2",
    }
    payload = {"name": "opendelta_submit_backtest", "arguments": arguments}
    first = rpc(client, "tools/call", payload).json()["result"]
    second = rpc(client, "tools/call", payload).json()["result"]
    assert first["structuredContent"] == second["structuredContent"]
    assert calls["backtests"] == 1
    assert len([item for item in audit.events if item["success"]]) == 2


def test_mcp_rejects_non_json_oversize_and_unknown_methods() -> None:
    client, _, _, _ = setup()
    assert client.post("/mcp", content="x", headers={"authorization": "Bearer odt_test"}).status_code == 415
    oversized = client.post(
        "/mcp",
        content=b"x" * 65_537,
        headers={"authorization": "Bearer odt_test", "content-type": "application/json"},
    )
    assert oversized.status_code == 413
    unknown = rpc(client, "live/place-order")
    assert unknown.status_code == 404
    assert unknown.json()["error"]["data"]["code"] == "METHOD_NOT_FOUND"
    denied_origin = client.post(
        "/mcp",
        headers={
            "authorization": "Bearer odt_test",
            "content-type": "application/json",
            "origin": "https://attacker.example",
        },
        json={"jsonrpc": "2.0", "id": 1, "method": "ping"},
    )
    assert denied_origin.status_code == 403
    assert client.get("/mcp").status_code == 405
