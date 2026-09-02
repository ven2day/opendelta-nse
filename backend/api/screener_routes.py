"""Screener API: run screens in the background, inspect results and rejections, save and activate universes."""

from __future__ import annotations

import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from backend.core.models import MARKETS
from backend.data.database import DatabaseUnavailable
from backend.data.repositories import SavedUniverseRepository, ScreenerResultRepository, ScreenerRunRepository
from backend.screener.engine import ScreenerEngine, apply_manual_selection
from backend.screener.filters import ScreenerFilters
from backend.screener.ranking import RANKING_KEYS

logger = logging.getLogger("opendelta.screener.api")


class ScreenerRunRequest(BaseModel):
    market: str = Field(pattern="^(NSE|CRYPTO)$")
    filters: dict[str, Any] = Field(default_factory=dict)
    symbols: list[str] | None = Field(default=None, max_length=5_000)  # None = the market's full catalogue


class SaveUniverseRequest(BaseModel):
    runId: str
    name: str = Field(min_length=1, max_length=120)
    maximumSymbols: int | None = Field(default=None, ge=1)
    manualIncludes: list[str] = Field(default_factory=list)
    manualExcludes: list[str] = Field(default_factory=list)
    activate: bool = True


class ScreenerServices:
    def __init__(
        self,
        *,
        runs: Callable[[], ScreenerRunRepository],
        results: Callable[[], ScreenerResultRepository],
        universes: Callable[[], SavedUniverseRepository],
        engine_for: Callable[[str], ScreenerEngine],
        catalogue_for: Callable[[str], list[str]],
        max_workers: int = 1,
    ) -> None:
        self.runs = runs
        self.results = results
        self.universes = universes
        self.engine_for = engine_for
        self.catalogue_for = catalogue_for
        self._executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="screener")
        self._cancel: dict[str, threading.Event] = {}

    def start(self, market: str, filters: ScreenerFilters, symbols: list[str]) -> dict[str, Any]:
        record = self.runs().create(market=market, filters=filters.public(), symbols_total=len(symbols))
        event = threading.Event()
        self._cancel[record["runId"]] = event
        self._executor.submit(self._execute, record["runId"], market, filters, symbols, event)
        return record

    def _execute(self, run_id: str, market: str, filters: ScreenerFilters, symbols: list[str], event: threading.Event) -> None:
        try:
            outcome = self.engine_for(market).run(run_id, symbols, filters, cancel_event=event)
            self.results().insert_many(run_id, outcome.rows)
            self.runs().finish(run_id, status="COMPLETE", symbols_passed=len(outcome.passed))
        except Exception as error:  # noqa: BLE001 - record the failure on the run
            logger.exception("Screener run %s failed", run_id)
            try:
                self.runs().finish(run_id, status="FAILED", symbols_passed=0, error=str(error)[:500])
            except Exception:  # noqa: BLE001
                logger.exception("Could not record screener failure for %s", run_id)
        finally:
            self._cancel.pop(run_id, None)

    def shutdown(self) -> None:
        for event in self._cancel.values():
            event.set()
        self._executor.shutdown(wait=False, cancel_futures=True)


def create_screener_router(services: ScreenerServices) -> APIRouter:
    router = APIRouter(prefix="/v2/screener", tags=["screener"])

    def _market(value: str) -> str:
        key = value.strip().upper()
        if key not in MARKETS:
            raise HTTPException(status_code=422, detail="market must be NSE or CRYPTO")
        return key

    def _guard(callable_: Callable[[], Any]) -> Any:
        try:
            return callable_()
        except DatabaseUnavailable as error:
            raise HTTPException(status_code=503, detail=str(error)) from error

    @router.get("/filters")
    def describe_filters() -> dict[str, Any]:
        return {"defaults": ScreenerFilters().public(), "rankBy": sorted(RANKING_KEYS), "markets": list(MARKETS)}

    @router.post("/runs", status_code=202)
    def start_run(request: ScreenerRunRequest) -> dict[str, Any]:
        market = _market(request.market)
        try:
            filters = ScreenerFilters.from_mapping(request.filters)
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        symbols = [symbol.strip().upper() for symbol in (request.symbols if request.symbols is not None else _guard(lambda: services.catalogue_for(market))) if symbol.strip()]
        symbols = list(dict.fromkeys(symbols))
        if not symbols:
            raise HTTPException(status_code=422, detail="The symbol universe is empty")
        return _guard(lambda: services.start(market, filters, symbols))

    @router.get("/runs")
    def list_runs(market: str | None = Query(default=None), limit: int = Query(default=50, ge=1, le=500)) -> dict[str, Any]:
        key = _market(market) if market else None
        return {"runs": _guard(services.runs).list(key, limit=limit)}

    @router.get("/runs/{run_id}")
    def get_run(run_id: str) -> dict[str, Any]:
        try:
            return _guard(services.runs).get(run_id)
        except (KeyError, ValueError) as error:
            raise HTTPException(status_code=404, detail="Screener run was not found") from error

    @router.get("/runs/{run_id}/results")
    def run_results(run_id: str, passed: bool | None = Query(default=None), limit: int = Query(default=5_000, ge=1, le=10_000)) -> dict[str, Any]:
        try:
            record = _guard(services.runs).get(run_id)
        except (KeyError, ValueError) as error:
            raise HTTPException(status_code=404, detail="Screener run was not found") from error
        return {"run": record, "results": _guard(services.results).list(run_id, passed=passed, limit=limit)}

    @router.post("/universes", status_code=201)
    def save_universe(request: SaveUniverseRequest) -> dict[str, Any]:
        try:
            record = _guard(services.runs).get(request.runId)
        except (KeyError, ValueError) as error:
            raise HTTPException(status_code=404, detail="Screener run was not found") from error
        if record["status"] != "COMPLETE":
            raise HTTPException(status_code=409, detail=f"Screener run is {record['status']}")
        passing = [row["symbol"] for row in _guard(services.results).list(request.runId, passed=True)]
        if request.maximumSymbols is not None:
            passing = passing[: request.maximumSymbols]
        symbols = apply_manual_selection(passing, includes=request.manualIncludes, excludes=request.manualExcludes)
        if not symbols:
            raise HTTPException(status_code=422, detail="The resulting universe is empty")
        return _guard(services.universes).save(
            market=record["market"], name=request.name, symbols=symbols, source_run_id=request.runId,
            manual_includes=[item.strip().upper() for item in request.manualIncludes if item.strip()],
            manual_excludes=[item.strip().upper() for item in request.manualExcludes if item.strip()], activate=request.activate,
        )

    @router.get("/universes")
    def list_universes(market: str | None = Query(default=None), limit: int = Query(default=50, ge=1, le=500)) -> dict[str, Any]:
        key = _market(market) if market else None
        universes = _guard(services.universes)
        return {"universes": universes.list(key, limit=limit), "active": {item: universes.active(item) for item in (MARKETS if key is None else (key,))}}

    @router.post("/universes/{universe_id}/activate")
    def activate_universe(universe_id: str) -> dict[str, Any]:
        try:
            return _guard(services.universes).activate(universe_id)
        except (KeyError, ValueError) as error:
            raise HTTPException(status_code=404, detail="Universe was not found") from error

    return router
