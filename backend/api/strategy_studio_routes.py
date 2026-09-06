"""Strategy Studio V2 authoring endpoints.

These routes validate and version source only. They never execute submitted
Python in the API process, and saved sources do not automatically enter the
live registry. Backtests execute a selected immutable version through the
separate Strategy V2 runner.
"""

from __future__ import annotations

from collections.abc import Callable

from fastapi import APIRouter, HTTPException, Query
from psycopg.errors import UniqueViolation
from pydantic import BaseModel, Field

from backend.data.database import DatabaseUnavailable
from backend.data.repositories import StrategySourceRepository
from backend.strategies.source_v2 import starter_source, validate_source


class StrategySourceRequest(BaseModel):
    sourceCode: str = Field(min_length=1, max_length=128_000)


def create_strategy_studio_router(sources: Callable[[], StrategySourceRepository] | None = None) -> APIRouter:
    router = APIRouter(prefix="/v2/strategy-studio", tags=["strategy-studio"])

    def repository() -> StrategySourceRepository:
        if sources is None:
            raise HTTPException(status_code=503, detail="Strategy source storage is not configured")
        try:
            return sources()
        except DatabaseUnavailable as error:
            raise HTTPException(status_code=503, detail=str(error)) from error

    @router.get("/template")
    def template() -> dict[str, str]:
        return {"sourceCode": starter_source()}

    @router.post("/validate")
    def validate(request: StrategySourceRequest) -> dict[str, object]:
        return validate_source(request.sourceCode).public()

    @router.get("/sources")
    def list_sources(market: str | None = Query(default=None, pattern="^(NSE|CRYPTO)$")) -> dict[str, object]:
        return {"sources": repository().list(market)}

    @router.get("/sources/{source_id}")
    def get_source(source_id: str) -> dict[str, object]:
        try:
            return repository().get(source_id)
        except (KeyError, ValueError) as error:
            raise HTTPException(status_code=404, detail=str(error)) from error

    @router.post("/sources", status_code=201)
    def save_source(request: StrategySourceRequest) -> dict[str, object]:
        result = validate_source(request.sourceCode)
        if not result.valid or result.manifest is None:
            raise HTTPException(status_code=422, detail=result.public())
        try:
            return repository().create(
                source_code=request.sourceCode,
                code_hash=result.code_hash,
                manifest=result.manifest,
                validation=result.public(),
            )
        except UniqueViolation as error:
            raise HTTPException(
                status_code=409,
                detail="That strategy version or identical source already exists. Change STRATEGY.version before saving.",
            ) from error

    return router
