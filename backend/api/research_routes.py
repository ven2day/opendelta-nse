"""Research Lab experiment grouping over the existing immutable backtest engine."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field, model_validator

from backend.api.backtest_routes import BacktestCreateRequest
from backend.data.database import DatabaseUnavailable
from backend.data.repositories import ResearchExperimentRepository


class ResearchVariantRequest(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    configuration: dict[str, Any] = Field(default_factory=dict)
    execution: dict[str, Any] = Field(default_factory=dict)


class ResearchExperimentRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    market: str = Field(pattern="^(NSE|CRYPTO)$")
    strategyId: str = Field(min_length=1, max_length=80)
    strategySourceId: str | None = None
    symbols: list[str] = Field(default_factory=list, max_length=2_000)
    universePresetId: str | None = Field(default=None, min_length=1, max_length=80)
    timeframe: str = Field(default="5m", min_length=1, max_length=8)
    startDate: str
    endDate: str
    variants: list[ResearchVariantRequest] = Field(min_length=1, max_length=12)

    @model_validator(mode="after")
    def unique_variant_names(self) -> ResearchExperimentRequest:
        names = [variant.name.strip().casefold() for variant in self.variants]
        if len(names) != len(set(names)):
            raise ValueError("Variant names must be unique within an experiment")
        return self


class ResearchServices:
    def __init__(
        self,
        *,
        experiments: Callable[[], ResearchExperimentRepository],
        submit_backtest: Callable[[BacktestCreateRequest], dict[str, Any]],
    ) -> None:
        self.experiments = experiments
        self.submit_backtest = submit_backtest


def create_research_router(services: ResearchServices) -> APIRouter:
    router = APIRouter(prefix="/v2/research/experiments", tags=["research"])

    def guarded(callable_: Callable[[], Any]) -> Any:
        try:
            return callable_()
        except DatabaseUnavailable as error:
            raise HTTPException(status_code=503, detail=str(error)) from error

    @router.post("", status_code=202)
    def create_experiment(request: ResearchExperimentRequest) -> dict[str, Any]:
        submitted: list[dict[str, Any]] = []
        for variant in request.variants:
            run = services.submit_backtest(BacktestCreateRequest(
                market=request.market,
                strategyId=request.strategyId,
                strategySourceId=request.strategySourceId,
                symbols=request.symbols,
                universePresetId=request.universePresetId,
                timeframe=request.timeframe,
                startDate=request.startDate,
                endDate=request.endDate,
                configuration=variant.configuration,
                execution=variant.execution,
            ))
            submitted.append({"name": variant.name.strip(), "run": run})
        return guarded(lambda: services.experiments().create(name=request.name.strip(), runs=submitted))

    @router.get("")
    def list_experiments(
        market: str | None = Query(default=None, pattern="^(NSE|CRYPTO)$"),
        limit: int = Query(default=50, ge=1, le=200),
    ) -> dict[str, Any]:
        return {"experiments": guarded(lambda: services.experiments().list(market, limit=limit))}

    @router.get("/{experiment_id}")
    def get_experiment(experiment_id: str) -> dict[str, Any]:
        try:
            return guarded(lambda: services.experiments().get(experiment_id))
        except (KeyError, ValueError) as error:
            raise HTTPException(status_code=404, detail="Research experiment was not found") from error

    return router
