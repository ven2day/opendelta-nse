"""Research-only AI Copilot routes with explicit, server-resolved context."""

from __future__ import annotations

import hashlib
import json
import os
import time
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field, model_validator

from backend.ai.copilot import AIProvider, CopilotProviderError, HTTPChatProvider
from backend.ai.repository import AICopilotRateLimit, AICopilotRepository
from backend.data.database import DatabaseUnavailable
from backend.data.repositories import (
    BacktestRunRepository,
    BacktestTradeRepository,
    IndicatorSourceRepository,
    ResearchExperimentRepository,
    StrategySourceRepository,
    WalkForwardValidationRepository,
)

MAX_AI_CONTEXT_BYTES = 262_144
MAX_AI_INSTRUCTION_CHARS = 4_000
MAX_SELECTED_TRADES = 20
AI_DRAFT_LABEL = "AI-generated research draft — review and validate"

CopilotAction = Literal[
    "EXPLAIN_STRATEGY", "EXPLAIN_INDICATOR", "EXPLAIN_BACKTEST", "SUGGEST_IMPROVEMENTS",
    "SUGGEST_EXPERIMENT", "EXPLAIN_COMPARISON", "EXPLAIN_WALK_FORWARD",
    "DRAFT_STRATEGY", "DRAFT_INDICATOR", "DRAFT_CONFIGURATION",
]


class CopilotContextRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    strategySourceId: str | None = None
    indicatorSourceId: str | None = None
    backtestRunId: str | None = None
    selectedTradeLotIds: list[str] = Field(default_factory=list, max_length=MAX_SELECTED_TRADES)
    experimentId: str | None = None
    walkForwardValidationId: str | None = None

    @model_validator(mode="after")
    def trades_require_run(self) -> CopilotContextRequest:
        if self.selectedTradeLotIds and not self.backtestRunId:
            raise ValueError("Selected trades require backtestRunId")
        if len(set(self.selectedTradeLotIds)) != len(self.selectedTradeLotIds):
            raise ValueError("Selected trade IDs must be unique")
        return self


class CopilotRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: CopilotAction
    instruction: str = Field(min_length=1, max_length=MAX_AI_INSTRUCTION_CHARS)
    context: CopilotContextRequest = Field(default_factory=CopilotContextRequest)


class SaveDraftRequest(BaseModel):
    requestId: str
    draftType: Literal["STRATEGY", "INDICATOR", "CONFIGURATION", "NOTE"]
    content: str = Field(min_length=1, max_length=131_072)


@dataclass(frozen=True)
class CopilotServices:
    audit: Callable[[], AICopilotRepository]
    strategy_sources: Callable[[], StrategySourceRepository]
    indicator_sources: Callable[[], IndicatorSourceRepository]
    runs: Callable[[], BacktestRunRepository]
    trades: Callable[[], BacktestTradeRepository]
    experiments: Callable[[], ResearchExperimentRepository]
    walk_forward: Callable[[], WalkForwardValidationRepository]
    provider: Callable[[], AIProvider | None]


def configured_ai_provider(environ: Mapping[str, str] | None = None) -> AIProvider | None:
    values = os.environ if environ is None else environ
    provider = values.get("AI_PROVIDER", "").strip().lower()
    if not provider:
        return None
    if provider != "openai-compatible":
        return None
    endpoint = values.get("AI_PROVIDER_ENDPOINT", "").strip()
    api_key = values.get("AI_PROVIDER_API_KEY", "").strip()
    model = values.get("AI_MODEL", "").strip()
    if not endpoint or not api_key or not model:
        return None
    try:
        timeout = float(values.get("AI_PROVIDER_TIMEOUT_SECONDS", "30"))
        return HTTPChatProvider(endpoint=endpoint, api_key=api_key, model=model, timeout_seconds=timeout)
    except ValueError:
        return None


def create_ai_copilot_router(services: CopilotServices) -> APIRouter:
    router = APIRouter(prefix="/v2/ai/copilot", tags=["ai-copilot"])

    def guard(factory: Callable[[], Any]) -> Any:
        try:
            return factory()
        except DatabaseUnavailable as error:
            raise HTTPException(status_code=503, detail=str(error)) from error

    @router.get("/status")
    def status() -> dict[str, Any]:
        provider = services.provider()
        return {
            "configured": provider is not None,
            "message": "AI research provider configured" if provider else "AI provider not configured",
            "provider": provider.name if provider else None,
            "model": provider.model if provider else None,
            "safetyMode": "RESEARCH_DRAFT_ONLY",
        }

    @router.post("/requests")
    def complete(request: CopilotRequest) -> dict[str, Any]:
        provider = services.provider()
        if provider is None:
            raise HTTPException(status_code=503, detail="AI provider not configured")
        try:
            categories, context = _resolve_context(request.context, services)
        except (KeyError, ValueError) as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        user_prompt = json.dumps(
            {"action": request.action, "instruction": request.instruction, "selectedContext": context},
            sort_keys=True, separators=(",", ":"), ensure_ascii=False,
        )
        input_bytes = len(user_prompt.encode())
        if input_bytes > MAX_AI_CONTEXT_BYTES:
            raise HTTPException(status_code=413, detail="Selected AI context exceeds the 262,144-byte limit")
        request_id = uuid.uuid4()
        audit = guard(services.audit)
        try:
            configured_rate_limit = int(os.environ.get("AI_COPILOT_REQUESTS_PER_MINUTE", "10"))
        except ValueError as error:
            raise HTTPException(
                status_code=503,
                detail="AI Copilot rate limit is invalid",
            ) from error
        rate_limit = min(max(configured_rate_limit, 1), 60)
        try:
            audit.start_request(
                request_id=request_id, actor="authenticated-web-user", action=request.action,
                categories=categories, provider=provider.name, model=provider.model,
                input_bytes=input_bytes, rate_limit=rate_limit,
            )
        except AICopilotRateLimit as error:
            raise HTTPException(status_code=429, detail=str(error)) from error
        started = time.perf_counter()
        try:
            response = provider.complete(system_prompt=_system_prompt(request.action), user_prompt=user_prompt)
        except CopilotProviderError as error:
            audit.finish_request(
                request_id, status="FAILED", duration_ms=_duration_ms(started), output_bytes=None,
                error_code="PROVIDER_FAILURE",
            )
            raise HTTPException(status_code=502, detail=str(error)) from error
        output_bytes = len(response.text.encode())
        output_hash = hashlib.sha256(response.text.encode()).hexdigest()
        audit.finish_request(
            request_id, status="SUCCEEDED", duration_ms=_duration_ms(started), output_bytes=output_bytes,
            output_sha256=output_hash, usage=response.usage,
        )
        return {
            "requestId": str(request_id), "label": AI_DRAFT_LABEL, "action": request.action,
            "content": response.text, "provider": provider.name, "model": provider.model,
            "usage": response.usage, "contextCategories": categories,
            "suggestedDraftType": _draft_type(request.action),
        }

    @router.post("/drafts", status_code=201)
    def save_draft(request: SaveDraftRequest) -> dict[str, Any]:
        try:
            return guard(services.audit).create_draft(
                request_id=request.requestId, draft_type=request.draftType, content=request.content,
            )
        except (KeyError, ValueError) as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

    @router.get("/drafts/{draft_id}")
    def get_draft(draft_id: str) -> dict[str, Any]:
        try:
            return guard(services.audit).get_draft(draft_id)
        except (KeyError, ValueError) as error:
            raise HTTPException(status_code=404, detail=str(error)) from error

    return router


def _duration_ms(started: float) -> int:
    return max(0, round((time.perf_counter() - started) * 1_000))


def _system_prompt(action: str) -> str:
    return (
        "You are the OpenDelta research copilot. Use only the explicitly selected JSON context. "
        "Never claim a strategy is approved, safe, production-ready, deployed, or live-enabled. "
        "Never request or infer credentials. Never issue broker/exchange actions. "
        "Any code or configuration is an unvalidated research draft that must follow normal V2 validation. "
        f"Perform the bounded research action {action}."
    )


def _draft_type(action: str) -> str:
    if action == "DRAFT_STRATEGY":
        return "STRATEGY"
    if action == "DRAFT_INDICATOR":
        return "INDICATOR"
    if action in {"DRAFT_CONFIGURATION", "SUGGEST_EXPERIMENT"}:
        return "CONFIGURATION"
    return "NOTE"


def _resolve_context(
    selected: CopilotContextRequest,
    services: CopilotServices,
) -> tuple[list[str], dict[str, Any]]:
    categories: list[str] = []
    context: dict[str, Any] = {}
    if selected.strategySourceId:
        source = services.strategy_sources().get(selected.strategySourceId)
        categories.append("STRATEGY_SOURCE")
        context["strategySource"] = {
            "sourceId": source["sourceId"], "status": source["status"], "manifest": source["manifest"],
            "sourceCode": source["sourceCode"], "validation": source["validation"],
        }
    if selected.indicatorSourceId:
        source = services.indicator_sources().get(selected.indicatorSourceId)
        categories.append("INDICATOR_SOURCE")
        context["indicatorSource"] = {
            "sourceId": source["sourceId"], "status": source["status"], "manifest": source["manifest"],
            "sourceCode": source["sourceCode"], "validation": source["validation"],
        }
    if selected.backtestRunId:
        run = services.runs().get(selected.backtestRunId)
        categories.append("BACKTEST_SUMMARY")
        context["backtestSummary"] = {
            "runId": run["runId"], "market": run["market"], "strategyId": run["strategyId"],
            "strategyVersion": run["strategyVersion"], "strategySourceId": run.get("strategySourceId"),
            "configuration": run["configurationSnapshot"], "execution": run["executionSettings"],
            "timeframe": run["timeframe"], "symbols": run["symbols"], "startDate": run["startDate"],
            "endDate": run["endDate"], "status": run["status"], "metrics": run["metrics"],
            "failedSymbols": run.get("failedSymbols") or [],
        }
        if selected.selectedTradeLotIds:
            requested = set(selected.selectedTradeLotIds)
            trades = services.trades().list(selected.backtestRunId, limit=5_000)
            chosen = [trade for trade in trades if trade["lotId"] in requested]
            if {trade["lotId"] for trade in chosen} != requested:
                raise ValueError("One or more selected trades do not belong to the selected backtest")
            categories.append("SELECTED_TRADES")
            context["selectedTrades"] = chosen
    if selected.experimentId:
        experiment = services.experiments().get(selected.experimentId)
        categories.append("EXPERIMENT_COMPARISON")
        context["experimentComparison"] = experiment
    if selected.walkForwardValidationId:
        validation = services.walk_forward().get(selected.walkForwardValidationId)
        categories.append("WALK_FORWARD_RESULT")
        context["walkForwardResult"] = validation
    return categories, context
