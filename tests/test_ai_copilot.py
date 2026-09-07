from __future__ import annotations

import json
import uuid
from typing import Any

import pytest
from backend.ai.copilot import MAX_AI_OUTPUT_BYTES, CopilotProviderError, CopilotResponse, HTTPChatProvider
from backend.ai.repository import AICopilotRateLimit
from backend.api.ai_copilot_routes import (
    CopilotContextRequest,
    CopilotRequest,
    CopilotServices,
    SaveDraftRequest,
    configured_ai_provider,
    create_ai_copilot_router,
)
from fastapi import HTTPException
from pydantic import ValidationError


def endpoints(router):
    return {f"{next(iter(route.methods - {'HEAD', 'OPTIONS'}))} {route.path}": route.endpoint for route in router.routes}


class FakeProvider:
    name = "fake"
    model = "research-model"

    def __init__(self, content: str = "bounded research explanation") -> None:
        self.content = content
        self.prompts: list[tuple[str, str]] = []

    def complete(self, *, system_prompt: str, user_prompt: str) -> CopilotResponse:
        self.prompts.append((system_prompt, user_prompt))
        return CopilotResponse(self.content, {"total_tokens": 12})


class FakeAudit:
    def __init__(self) -> None:
        self.started: list[dict[str, Any]] = []
        self.finished: list[tuple[uuid.UUID | str, dict[str, Any]]] = []
        self.drafts: list[dict[str, Any]] = []

    def start_request(self, **values: Any) -> None:
        self.started.append(values)

    def finish_request(self, request_id: uuid.UUID | str, **values: Any) -> None:
        self.finished.append((request_id, values))

    def create_draft(self, *, request_id: str, draft_type: str, content: str) -> dict[str, Any]:
        draft = {"draftId": str(uuid.uuid4()), "requestId": request_id, "draftType": draft_type, "content": content, "status": "DRAFT"}
        self.drafts.append(draft)
        return draft

    def get_draft(self, draft_id: str) -> dict[str, Any]:
        return next(item for item in self.drafts if item["draftId"] == draft_id)


class Rows:
    def __init__(self, value: Any) -> None:
        self.value = value

    def get(self, _key: str) -> Any:
        return self.value


class Trades:
    def list(self, _run_id: str, *, limit: int) -> list[dict[str, Any]]:
        assert limit == 5_000
        return [{"lotId": "lot-1", "symbol": "TCS", "status": "TARGET_HIT", "netPnl": 10}]


def services(provider: FakeProvider | None = None, audit: FakeAudit | None = None) -> CopilotServices:
    run = {
        "runId": "run-1", "market": "NSE", "strategyId": "safe", "strategyVersion": "1.0.0",
        "strategySourceId": None, "configurationSnapshot": {"period": 20}, "executionSettings": {},
        "timeframe": "5m", "symbols": ["TCS"], "startDate": "2026-01-01", "endDate": "2026-02-01",
        "status": "COMPLETE", "metrics": {"realizedPnl": 10}, "failedSymbols": [],
        "exchangeCredential": "must-never-be-sent",
    }
    return CopilotServices(
        audit=lambda: audit or FakeAudit(),
        strategy_sources=lambda: Rows({
            "sourceId": "source-1", "status": "VALIDATED", "manifest": {"id": "safe"},
            "sourceCode": "def generate(): pass", "validation": {"valid": True},
        }),
        indicator_sources=lambda: Rows({
            "sourceId": "indicator-1", "status": "VALIDATED", "manifest": {"id": "rsi"},
            "sourceCode": "def calculate(): pass", "validation": {"valid": True},
        }),
        runs=lambda: Rows(run), trades=lambda: Trades(),
        experiments=lambda: Rows({"experimentId": "experiment-1", "variants": []}),
        walk_forward=lambda: Rows({"validationId": "walk-1", "folds": []}),
        provider=lambda: provider,
    )


def test_provider_not_configured_fails_closed_and_status_exposes_no_key() -> None:
    api = endpoints(create_ai_copilot_router(services()))
    assert api["GET /v2/ai/copilot/status"]() == {
        "configured": False, "message": "AI provider not configured", "provider": None, "model": None,
        "safetyMode": "RESEARCH_DRAFT_ONLY",
    }
    with pytest.raises(HTTPException) as error:
        api["POST /v2/ai/copilot/requests"](CopilotRequest(action="DRAFT_STRATEGY", instruction="Draft one"))
    assert error.value.status_code == 503


def test_only_explicit_server_resolved_context_reaches_provider_and_is_audited() -> None:
    provider = FakeProvider()
    audit = FakeAudit()
    api = endpoints(create_ai_copilot_router(services(provider, audit)))
    response = api["POST /v2/ai/copilot/requests"](CopilotRequest(
        action="EXPLAIN_BACKTEST", instruction="Explain drawdown",
        context=CopilotContextRequest(backtestRunId="run-1", selectedTradeLotIds=["lot-1"]),
    ))

    assert response["label"].startswith("AI-generated research draft")
    assert response["contextCategories"] == ["BACKTEST_SUMMARY", "SELECTED_TRADES"]
    prompt = provider.prompts[0][1]
    assert "must-never-be-sent" not in prompt
    assert "exchangeCredential" not in prompt
    assert "lot-1" in prompt
    assert audit.started[0]["categories"] == ["BACKTEST_SUMMARY", "SELECTED_TRADES"]
    assert audit.finished[0][1]["status"] == "SUCCEEDED"
    assert audit.drafts == []


def test_draft_is_created_only_after_explicit_save() -> None:
    provider = FakeProvider("STRATEGY = research draft")
    audit = FakeAudit()
    api = endpoints(create_ai_copilot_router(services(provider, audit)))
    response = api["POST /v2/ai/copilot/requests"](CopilotRequest(
        action="DRAFT_STRATEGY", instruction="Draft a strategy",
    ))
    assert audit.drafts == []
    draft = api["POST /v2/ai/copilot/drafts"](SaveDraftRequest(
        requestId=response["requestId"], draftType="STRATEGY", content=response["content"],
    ))
    assert draft["status"] == "DRAFT"
    assert "sourceId" not in draft


def test_unknown_live_or_approval_action_is_not_in_schema() -> None:
    with pytest.raises(ValidationError):
        CopilotRequest.model_validate({"action": "APPROVE_PAPER", "instruction": "do it"})
    with pytest.raises(ValidationError):
        CopilotRequest.model_validate({"action": "PLACE_ORDER", "instruction": "do it"})


def test_selected_trade_must_belong_to_selected_run() -> None:
    api = endpoints(create_ai_copilot_router(services(FakeProvider(), FakeAudit())))
    with pytest.raises(HTTPException) as error:
        api["POST /v2/ai/copilot/requests"](CopilotRequest(
            action="EXPLAIN_BACKTEST", instruction="Explain",
            context=CopilotContextRequest(backtestRunId="run-1", selectedTradeLotIds=["other-lot"]),
        ))
    assert error.value.status_code == 422


def test_rate_limit_is_returned_without_calling_provider() -> None:
    provider = FakeProvider()
    audit = FakeAudit()

    def reject(**_values: Any) -> None:
        raise AICopilotRateLimit("AI Copilot rate limit exceeded")

    audit.start_request = reject  # type: ignore[method-assign]
    api = endpoints(create_ai_copilot_router(services(provider, audit)))
    with pytest.raises(HTTPException) as error:
        api["POST /v2/ai/copilot/requests"](CopilotRequest(action="EXPLAIN_STRATEGY", instruction="Explain"))
    assert error.value.status_code == 429
    assert provider.prompts == []


def test_provider_failure_is_audited_without_persisting_response_content() -> None:
    class FailingProvider(FakeProvider):
        def complete(self, *, system_prompt: str, user_prompt: str) -> CopilotResponse:
            self.prompts.append((system_prompt, user_prompt))
            raise CopilotProviderError("AI provider request failed")

    audit = FakeAudit()
    api = endpoints(create_ai_copilot_router(services(FailingProvider(), audit)))
    with pytest.raises(HTTPException) as error:
        api["POST /v2/ai/copilot/requests"](
            CopilotRequest(action="EXPLAIN_STRATEGY", instruction="Explain")
        )

    assert error.value.status_code == 502
    assert audit.finished[0][1] == {
        "status": "FAILED",
        "duration_ms": audit.finished[0][1]["duration_ms"],
        "output_bytes": None,
        "error_code": "PROVIDER_FAILURE",
    }


def test_http_adapter_bounds_output_and_never_puts_key_in_payload() -> None:
    captured: dict[str, Any] = {}

    def transport(request, timeout: float) -> bytes:
        captured["request"] = request
        captured["timeout"] = timeout
        return json.dumps({"choices": [{"message": {"content": "ok"}}], "usage": {"total_tokens": 2}}).encode()

    provider = HTTPChatProvider(
        endpoint="https://provider.invalid/v1/chat/completions", api_key="test-provider-key",
        model="model", timeout_seconds=90, transport=transport,
    )
    assert provider.complete(system_prompt="system", user_prompt="user").text == "ok"
    assert captured["timeout"] == 60
    assert b"test-provider-key" not in captured["request"].data

    oversized = HTTPChatProvider(
        endpoint="https://provider.invalid", api_key="key", model="model",
        transport=lambda _request, _timeout: json.dumps({"choices": [{"message": {"content": "x" * (MAX_AI_OUTPUT_BYTES + 1)}}]}).encode(),
    )
    with pytest.raises(CopilotProviderError, match="exceeded"):
        oversized.complete(system_prompt="system", user_prompt="user")


def test_environment_configuration_requires_complete_operator_settings() -> None:
    assert configured_ai_provider({}) is None
    assert configured_ai_provider({"AI_PROVIDER": "openai-compatible"}) is None
    provider = configured_ai_provider({
        "AI_PROVIDER": "openai-compatible", "AI_PROVIDER_ENDPOINT": "https://provider.invalid",
        "AI_PROVIDER_API_KEY": "key", "AI_MODEL": "model",
    })
    assert provider is not None
    assert configured_ai_provider({
        "AI_PROVIDER": "openai-compatible", "AI_PROVIDER_ENDPOINT": "http://provider.invalid",
        "AI_PROVIDER_API_KEY": "key", "AI_MODEL": "model",
    }) is None
