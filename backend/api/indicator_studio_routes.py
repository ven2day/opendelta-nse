"""Indicator Studio authoring and isolated preview endpoints."""

from __future__ import annotations

import os
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from psycopg.errors import UniqueViolation
from pydantic import BaseModel, Field

from backend.data.database import DatabaseUnavailable
from backend.data.repositories import IndicatorSourceRepository
from backend.indicators.source_v2 import starter_source, validate_source
from backend.markets.base import CandleSource
from backend.strategies.adapter_v2 import StrategyRunnerClient


class IndicatorSourceRequest(BaseModel):
    sourceCode: str = Field(min_length=1, max_length=128_000)


class CandleColumns(BaseModel):
    timestamp: list[str]
    open: list[float]
    high: list[float]
    low: list[float]
    close: list[float]
    volume: list[float]


class IndicatorPreviewRequest(BaseModel):
    market: str = Field(pattern="^(NSE|CRYPTO)$")
    symbol: str = Field(min_length=1, max_length=80)
    timeframe: str = Field(pattern="^(1m|3m|5m|15m|30m|1h|4h|1d)$")
    params: dict[str, Any] = Field(default_factory=dict)
    candles: CandleColumns | None = None


def create_indicator_studio_router(
    sources: Callable[[], IndicatorSourceRepository] | None = None,
    runner: Callable[[], StrategyRunnerClient] | None = None,
    candle_source: Callable[[str], CandleSource] | None = None,
    clock: Callable[[], datetime] | None = None,
) -> APIRouter:
    router = APIRouter(prefix="/v2/indicator-studio", tags=["indicator-studio"])

    def repository() -> IndicatorSourceRepository:
        if sources is None:
            raise HTTPException(status_code=503, detail="Indicator source storage is not configured")
        try:
            return sources()
        except DatabaseUnavailable as error:
            raise HTTPException(status_code=503, detail=str(error)) from error

    def execution_client() -> StrategyRunnerClient:
        if runner is not None:
            return runner()
        socket_path = os.environ.get("STRATEGY_V2_RUNNER_SOCKET", "/run/opendelta-strategy/runner.sock")
        return StrategyRunnerClient(socket_path, timeout_seconds=20, execution_timeout_seconds=15)

    @router.get("/template")
    def template() -> dict[str, str]:
        return {"sourceCode": starter_source()}

    @router.post("/validate")
    def validate(request: IndicatorSourceRequest) -> dict[str, object]:
        return validate_source(request.sourceCode).public()

    @router.get("/sources")
    def list_sources(status: str | None = Query(default=None, pattern="^(VALIDATED|ARCHIVED)$")) -> dict[str, object]:
        return {"sources": repository().list(status=status)}

    @router.get("/sources/{source_id}")
    def get_source(source_id: str) -> dict[str, object]:
        try:
            return repository().get(source_id)
        except (KeyError, ValueError) as error:
            raise HTTPException(status_code=404, detail=str(error)) from error

    @router.post("/sources", status_code=201)
    def save_source(request: IndicatorSourceRequest) -> dict[str, object]:
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
                detail="That indicator version or identical source already exists. Change INDICATOR.version before saving.",
            ) from error

    @router.post("/sources/{source_id}/archive")
    def archive_source(source_id: str) -> dict[str, object]:
        try:
            return repository().archive(source_id)
        except (KeyError, ValueError) as error:
            raise HTTPException(status_code=404, detail=str(error)) from error

    @router.post("/sources/{source_id}/preview")
    def preview(source_id: str, request: IndicatorPreviewRequest) -> dict[str, object]:
        try:
            source = repository().get(source_id)
        except (KeyError, ValueError) as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        if source["status"] != "VALIDATED":
            raise HTTPException(status_code=409, detail="Archived indicators cannot be previewed")
        candles = request.candles
        if candles is None:
            if candle_source is None:
                raise HTTPException(status_code=503, detail="Stored candle preview is not configured")
            now = (clock or (lambda: datetime.now(UTC)))()
            try:
                frame = candle_source(request.market).candles(
                    request.symbol, request.timeframe, now - timedelta(days=45), now,
                    warmup_bars=int(source["manifest"].get("requiredHistory", 1)),
                ).tail(500)
            except Exception as error:  # noqa: BLE001 - provider errors become a safe API response
                raise HTTPException(status_code=422, detail=f"Stored candles could not be loaded: {error}") from error
            if frame.empty:
                raise HTTPException(status_code=422, detail="No stored completed candles were found for this preview")
            candles = CandleColumns(
                timestamp=[stamp.isoformat() for stamp in frame.index],
                open=frame["Open"].astype(float).tolist(), high=frame["High"].astype(float).tolist(),
                low=frame["Low"].astype(float).tolist(), close=frame["Close"].astype(float).tolist(),
                volume=frame["Volume"].astype(float).tolist(),
            )
        lengths = {len(getattr(candles, name)) for name in CandleColumns.model_fields}
        if len(lengths) != 1 or not lengths or next(iter(lengths)) == 0:
            raise HTTPException(status_code=422, detail="Candle columns must be non-empty and have equal lengths")
        try:
            result = execution_client().evaluate(
                {
                    "payloadType": "indicator",
                    "sourceCode": source["sourceCode"],
                    "market": request.market,
                    "symbol": request.symbol,
                    "timeframe": request.timeframe,
                    "params": request.params,
                    "candles": candles.model_dump(),
                }
            )
            return {**result, "candles": candles.model_dump()}
        except (OSError, RuntimeError, TimeoutError, ValueError) as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

    return router
