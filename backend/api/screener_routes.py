"""Screener API: run screens in the background, inspect results and rejections, save and activate universes."""

from __future__ import annotations

import logging
import re
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from backend.core.models import MARKETS
from backend.data.database import DatabaseUnavailable
from backend.data.repositories import (
    SavedUniverseRepository,
    ScreenerResultRepository,
    ScreenerRunRepository,
    WatchlistProfileRepository,
)
from backend.data.universe_presets import get_universe_preset, list_universe_presets
from backend.screener.engine import ScreenerEngine, apply_manual_selection
from backend.screener.filters import ScreenerFilters
from backend.screener.ranking import RANKING_KEYS

logger = logging.getLogger("opendelta.screener.api")


class ScreenerRunRequest(BaseModel):
    market: str = Field(pattern="^(NSE|CRYPTO)$")
    filters: dict[str, Any] = Field(default_factory=dict)
    symbols: list[str] | None = Field(default=None, max_length=5_000)  # None = the market's full catalogue
    presetId: str | None = Field(default=None, min_length=1, max_length=80)
    profileVersionId: str | None = None


class WatchlistProfileVersionRequest(BaseModel):
    filters: dict[str, Any] = Field(default_factory=dict)
    sourceKind: str = Field(pattern="^(MARKET|PRESET|CUSTOM)$")
    presetId: str | None = Field(default=None, min_length=1, max_length=80)
    symbols: list[str] = Field(default_factory=list, max_length=5_000)


class WatchlistProfileRequest(WatchlistProfileVersionRequest):
    market: str = Field(pattern="^(NSE|CRYPTO)$")
    name: str = Field(min_length=1, max_length=120)


class SaveUniverseRequest(BaseModel):
    runId: str
    name: str = Field(min_length=1, max_length=120)
    maximumSymbols: int | None = Field(default=None, ge=1)
    manualIncludes: list[str] = Field(default_factory=list)
    manualExcludes: list[str] = Field(default_factory=list)
    activate: bool = True


class TradingViewImportRequest(BaseModel):
    market: str = Field(pattern="^(NSE|CRYPTO)$")
    name: str = Field(min_length=1, max_length=120)
    symbols: str = Field(min_length=1, max_length=20_000)
    activate: bool = True


class ScreenerServices:
    def __init__(
        self,
        *,
        runs: Callable[[], ScreenerRunRepository],
        results: Callable[[], ScreenerResultRepository],
        universes: Callable[[], SavedUniverseRepository],
        profiles: Callable[[], WatchlistProfileRepository],
        engine_for: Callable[[str], ScreenerEngine],
        catalogue_for: Callable[[str], list[str]],
        max_workers: int = 1,
    ) -> None:
        self.runs = runs
        self.results = results
        self.universes = universes
        self.profiles = profiles
        self.engine_for = engine_for
        self.catalogue_for = catalogue_for
        self._executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="screener")
        self._cancel: dict[str, threading.Event] = {}

    def start(self, market: str, filters: ScreenerFilters, symbols: list[str], *, profile_version_id: str | None = None) -> dict[str, Any]:
        record = self.runs().create(market=market, filters=filters.public(), symbols_total=len(symbols), profile_version_id=profile_version_id)
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

    def _validated_filters(values: dict[str, Any]) -> ScreenerFilters:
        try:
            return ScreenerFilters.from_mapping(values)
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

    def _profile_source(market: str, source_kind: str, preset_id: str | None, symbols: list[str]) -> tuple[str, str | None, list[str]]:
        kind = source_kind.strip().upper()
        normalised = list(dict.fromkeys(item.strip().upper() for item in symbols if item.strip()))
        if kind == "MARKET":
            if preset_id is not None or normalised:
                raise HTTPException(status_code=422, detail="MARKET profiles cannot include presetId or symbols")
            return kind, None, []
        if kind == "PRESET":
            if preset_id is None or normalised:
                raise HTTPException(status_code=422, detail="PRESET profiles require presetId and cannot include symbols")
            try:
                get_universe_preset(preset_id, market)
            except KeyError as error:
                raise HTTPException(status_code=422, detail=str(error)) from error
            return kind, preset_id, []
        if kind == "CUSTOM":
            if preset_id is not None or not normalised:
                raise HTTPException(status_code=422, detail="CUSTOM profiles require symbols and cannot include presetId")
            return kind, None, normalised
        raise HTTPException(status_code=422, detail="sourceKind must be MARKET, PRESET or CUSTOM")

    def _profile_symbols(market: str, source_kind: str, preset_id: str | None, symbols: list[str]) -> list[str]:
        if source_kind == "MARKET":
            return _guard(lambda: services.catalogue_for(market))
        if source_kind == "PRESET":
            assert preset_id is not None
            return get_universe_preset(preset_id, market).symbols
        return symbols

    @router.get("/filters")
    def describe_filters() -> dict[str, Any]:
        return {"defaults": ScreenerFilters().public(), "rankBy": sorted(RANKING_KEYS), "markets": list(MARKETS)}

    @router.get("/presets")
    def list_presets(market: str | None = Query(default=None)) -> dict[str, Any]:
        key = _market(market) if market else None
        return {"presets": [preset.public() for preset in list_universe_presets(key)]}

    @router.get("/profiles")
    def list_profiles(market: str | None = Query(default=None), limit: int = Query(default=100, ge=1, le=500)) -> dict[str, Any]:
        key = _market(market) if market else None
        return {"profiles": _guard(services.profiles).list(key, limit=limit)}

    @router.post("/profiles", status_code=201)
    def create_profile(request: WatchlistProfileRequest) -> dict[str, Any]:
        market = _market(request.market)
        filters = _validated_filters(request.filters)
        source_kind, preset_id, symbols = _profile_source(market, request.sourceKind, request.presetId, request.symbols)
        name = request.name.strip()
        if not name:
            raise HTTPException(status_code=422, detail="Watchlist profile name is required")
        try:
            return _guard(services.profiles).create(
                market=market, name=name, filters=filters.public(), source_kind=source_kind,
                preset_id=preset_id, symbols=symbols,
            )
        except ValueError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error

    @router.post("/profiles/{profile_id}/versions", status_code=201)
    def create_profile_version(profile_id: str, request: WatchlistProfileVersionRequest) -> dict[str, Any]:
        try:
            profile = _guard(services.profiles).get(profile_id)
        except (KeyError, ValueError) as error:
            raise HTTPException(status_code=404, detail="Watchlist profile was not found") from error
        filters = _validated_filters(request.filters)
        source_kind, preset_id, symbols = _profile_source(profile["market"], request.sourceKind, request.presetId, request.symbols)
        return _guard(services.profiles).add_version(
            profile_id, filters=filters.public(), source_kind=source_kind, preset_id=preset_id, symbols=symbols,
        )

    @router.post("/runs", status_code=202)
    def start_run(request: ScreenerRunRequest) -> dict[str, Any]:
        market = _market(request.market)
        profile_version_id: str | None = None
        if request.profileVersionId is not None:
            if request.filters or request.presetId is not None or request.symbols is not None:
                raise HTTPException(status_code=422, detail="A profileVersionId cannot be combined with filters, presetId or symbols")
            try:
                profile_version = _guard(services.profiles).get_version(request.profileVersionId)
            except (KeyError, ValueError) as error:
                raise HTTPException(status_code=404, detail="Watchlist profile version was not found") from error
            if profile_version["market"] != market:
                raise HTTPException(status_code=422, detail=f"Watchlist profile version belongs to {profile_version['market']}, not {market}")
            filters = _validated_filters(profile_version["filters"])
            requested_symbols = _profile_symbols(
                market, profile_version["sourceKind"], profile_version["presetId"], profile_version["symbols"],
            )
            profile_version_id = profile_version["profileVersionId"]
        else:
            if request.presetId is not None and request.symbols is not None:
                raise HTTPException(status_code=422, detail="Use either presetId or symbols, not both")
            filters = _validated_filters(request.filters)
            if request.presetId is not None:
                try:
                    requested_symbols = get_universe_preset(request.presetId, market).symbols
                except KeyError as error:
                    raise HTTPException(status_code=422, detail=str(error)) from error
            else:
                requested_symbols = request.symbols if request.symbols is not None else _guard(lambda: services.catalogue_for(market))
        symbols = list(dict.fromkeys(symbol.strip().upper() for symbol in requested_symbols if symbol.strip()))
        if not symbols:
            raise HTTPException(status_code=422, detail="The symbol universe is empty")
        return _guard(lambda: services.start(market, filters, symbols, profile_version_id=profile_version_id))

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

    @router.post("/universes/import-tradingview", status_code=201)
    def import_tradingview(request: TradingViewImportRequest) -> dict[str, Any]:
        market = _market(request.market)
        catalogue = _guard(lambda: services.catalogue_for(market))
        by_compact = {re.sub(r"[^A-Z0-9]", "", item.upper()): item for item in catalogue}
        requested = list(dict.fromkeys(
            item.strip().upper().split(":", 1)[-1].removesuffix(".P")
            for item in re.split(r"[\s,;]+", request.symbols) if item.strip()
        ))
        accepted: list[str] = []
        rejected: list[str] = []
        for value in requested:
            match = by_compact.get(re.sub(r"[^A-Z0-9]", "", value))
            (accepted if match else rejected).append(match or value)
        accepted = list(dict.fromkeys(accepted))
        if not accepted:
            raise HTTPException(status_code=422, detail="None of the TradingView symbols match configured market instruments")
        universe = _guard(services.universes).save(market=market, name=request.name.strip(), symbols=accepted, activate=request.activate)
        return {"universe": universe, "accepted": accepted, "rejected": rejected}

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
