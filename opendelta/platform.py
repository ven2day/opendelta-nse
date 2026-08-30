from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Callable, Literal

from fastapi import APIRouter, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import JSONResponse

from .core import (
    PLATFORM_VERSION,
    MetricsRegistry,
    PlatformSettings,
    StructuredLogger,
    request_id,
    utc_now_iso,
)
from .factors import FactorEngine
from .instruments import InstrumentRepository, InstrumentService
from .jobs import JobRepository, JobService
from .market_data import FeatureCache, PROVIDER_CAPABILITIES, freshness
from .timescale_market_data import timescale_health
from .market_context import MarketContextService
from .research import ResearchRequest, ResearchService
from .risk import RiskService
from .strategies import StrategyRegistry


CandleLoader = Callable[[ResearchRequest], Any]
CryptoInstrumentLoader = Callable[[], list[dict[str, Any]]]
ProviderStatusLoader = Callable[[], dict[str, Any]]
UniverseResolver = Callable[[str], list[str]]
LEGACY_INVALID_RESEARCH_MODEL = "LEGACY_INVALID_RESEARCH_MODEL"
LEGACY_RESEARCH_EXPLANATION = (
    "This result used one-bar next-open-to-next-close observations, not a strategy "
    "backtest with a complete position lifecycle. It must not be interpreted as "
    "strategy profitability."
)


def research_engine_status(settings: PlatformSettings) -> dict[str, Any]:
    enabled = settings.research_engine_v2_enabled
    return {
        "version": "2",
        "enabled": enabled,
        "status": "ENABLED" if enabled else "DISABLED_FAIL_CLOSED",
        "legacyResultStatus": LEGACY_INVALID_RESEARCH_MODEL,
        "message": (
            "Research V2 is enabled after server-side acceptance gates."
            if enabled
            else "New Research experiments are disabled while Research V2 correctness is validated."
        ),
    }


def mark_legacy_research_job(job: dict[str, Any]) -> dict[str, Any]:
    """Annotate old research jobs at read time without mutating retained history."""

    if job.get("jobType") != "RESEARCH_EXPERIMENT":
        return job
    result = job.get("result")
    if isinstance(result, dict) and result.get("researchVersion") == "2":
        return job
    marked = {
        **job,
        "researchValidity": LEGACY_INVALID_RESEARCH_MODEL,
        "researchWarning": LEGACY_RESEARCH_EXPLANATION,
    }
    if isinstance(result, dict):
        marked["result"] = {
            **result,
            "researchValidity": LEGACY_INVALID_RESEARCH_MODEL,
            "researchWarning": LEGACY_RESEARCH_EXPLANATION,
        }
    return marked


def provider_rows(runtime: "PlatformRuntime") -> list[dict[str, Any]]:
    market_data = freshness(
        runtime.settings.market_data_file, runtime.settings.data_stale_seconds
    )
    engine = runtime.provider_health()
    engine_providers = set(engine.get("providers") or [])
    rows: list[dict[str, Any]] = []
    for capability in PROVIDER_CAPABILITIES.values():
        if capability.provider == "DHAN":
            status = market_data.get("status", "NOT_PROBED")
        elif engine.get("status") == "DEGRADED":
            status = "DEGRADED"
        elif capability.provider not in engine_providers:
            status = "UNAVAILABLE"
        else:
            status = str(engine.get("engineStatus") or "NOT_PROBED")
        rows.append(
            {
                **capability.__dict__,
                "status": status,
                "privateTradingEndpoints": False,
            }
        )
    return rows


@dataclass
class PlatformRuntime:
    settings: PlatformSettings
    instruments: InstrumentService
    factors: FactorEngine
    strategies: StrategyRegistry
    research: ResearchService
    risk: RiskService
    jobs: JobService
    job_repository: JobRepository
    feature_cache: FeatureCache
    market_context: MarketContextService
    metrics: MetricsRegistry
    logger: StructuredLogger
    provider_status_loader: ProviderStatusLoader | None = None

    @classmethod
    def build(
        cls,
        settings: PlatformSettings,
        candle_loader: CandleLoader,
        crypto_instrument_loader: CryptoInstrumentLoader | None = None,
        provider_status_loader: ProviderStatusLoader | None = None,
        universe_resolver: UniverseResolver | None = None,
    ) -> "PlatformRuntime":
        job_repository = JobRepository(settings.database_path)
        factor_engine = FactorEngine()
        feature_cache = FeatureCache(settings.database_path)
        return cls(
            settings=settings,
            instruments=InstrumentService(
                InstrumentRepository(settings.symbols_file, crypto_instrument_loader)
            ),
            factors=factor_engine,
            strategies=StrategyRegistry(),
            research=ResearchService(
                candle_loader,
                factor_engine,
                feature_cache,
                universe_resolver,
            ),
            risk=RiskService(),
            jobs=JobService(
                job_repository,
                maximum_workers=settings.maximum_workers,
                maximum_pending=settings.maximum_pending_jobs,
                retry_limit=settings.job_retry_limit,
            ),
            job_repository=job_repository,
            feature_cache=feature_cache,
            market_context=MarketContextService(
                settings.market_data_file, settings.data_stale_seconds
            ),
            metrics=MetricsRegistry(),
            logger=StructuredLogger(),
            provider_status_loader=provider_status_loader,
        )

    def provider_health(self) -> dict[str, Any]:
        if self.provider_status_loader is None:
            return {"status": "NOT_PROBED", "reason": "PROVIDER_STATUS_ADAPTER_NOT_CONFIGURED"}
        try:
            status = self.provider_status_loader()
        except Exception as error:
            return {"status": "DEGRADED", "errorType": type(error).__name__}
        return {
            "status": "DEGRADED" if status.get("lastError") else "HEALTHY",
            **status,
        }

    def health(self) -> dict[str, Any]:
        data = freshness(self.settings.market_data_file, self.settings.data_stale_seconds)
        checks = {
            "database": {
                "status": "HEALTHY",
                "migrations": self.job_repository.migrations(),
            },
            "worker": self.jobs.health(),
            "featureCache": self.feature_cache.health(),
            "marketData": data,
            "instrumentMaster": {
                "status": "HEALTHY" if self.settings.symbols_file.exists() else "DEGRADED",
                "symbolsFileAvailable": self.settings.symbols_file.exists(),
            },
            "providers": self.provider_health(),
        }
        unavailable = any(
            value.get("status") in {"FAILED", "UNAVAILABLE", "INVALID", "STALE", "DEGRADED"}
            for value in checks.values()
        )
        return {
            "status": "DEGRADED" if unavailable else "HEALTHY",
            "version": PLATFORM_VERSION,
            "environment": self.settings.environment,
            "checkedAt": utc_now_iso(),
            "checks": checks,
            "paperOnly": True,
            "liveOrdersEnabled": False,
        }

    def shutdown(self) -> None:
        self.jobs.shutdown()


def create_platform_router(runtime_factory: Callable[[], PlatformRuntime]) -> APIRouter:
    router = APIRouter(prefix="/platform", tags=["quant-platform"])

    @router.get("/health/live")
    def liveness() -> dict[str, Any]:
        return {
            "status": "HEALTHY",
            "version": PLATFORM_VERSION,
            "checkedAt": utc_now_iso(),
        }

    @router.get("/health/ready")
    def readiness() -> dict[str, Any]:
        result = runtime_factory().health()
        if result["status"] == "FAILED":
            raise HTTPException(status_code=503, detail="Platform is not ready")
        return result

    @router.get("/overview")
    def overview() -> dict[str, Any]:
        runtime = runtime_factory()
        factors = runtime.factors.registry.list()
        strategies = runtime.strategies.list()
        return {
            "platform": "OpenDelta",
            "version": PLATFORM_VERSION,
            "environment": runtime.settings.environment,
            "markets": ["NSE", "CRYPTO"],
            "modules": [
                "INSTRUMENT_MASTER",
                "MARKET_DATA",
                "MARKET_CONTEXT",
                "FACTOR_ENGINE",
                "STRATEGY_ENGINE",
                "BACKTEST_ENGINE",
                "RESEARCH_LAB",
                "SIGNAL_ENGINE",
                "PORTFOLIO_RISK",
                "JOBS",
                "ANALYTICS",
                "AUDIT_OBSERVABILITY",
            ],
            "factorCount": len(factors),
            "factorFamilies": sorted({factor.family for factor in factors}),
            "strategyCount": len(strategies),
            "jobStatus": runtime.jobs.health(),
            "dataFreshness": freshness(
                runtime.settings.market_data_file, runtime.settings.data_stale_seconds
            ),
            "researchEngine": research_engine_status(runtime.settings),
            "paperOnly": True,
            "liveOrdersEnabled": False,
        }

    @router.get("/instruments")
    def instruments(
        market: Literal["NSE", "CRYPTO"] | None = None,
        offset: int = Query(default=0, ge=0),
        limit: int = Query(default=100, ge=1, le=500),
    ) -> dict[str, Any]:
        try:
            return runtime_factory().instruments.list(market, offset, limit)
        except (OSError, ValueError) as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

    @router.get("/market-context")
    def market_context(
        market: Literal["NSE", "CRYPTO"] = "NSE",
    ) -> dict[str, Any]:
        try:
            return runtime_factory().market_context.snapshot(market)
        except (OSError, ValueError) as error:
            raise HTTPException(status_code=503, detail="Market context is unavailable") from error

    @router.get("/factors")
    def factors(family: str | None = Query(default=None, max_length=80)) -> dict[str, Any]:
        rows = [item.public() for item in runtime_factory().factors.registry.list(family)]
        return {"rows": rows, "count": len(rows), "family": family}

    @router.get("/strategies")
    def strategies(
        market: Literal["NSE", "CRYPTO"] | None = None,
    ) -> dict[str, Any]:
        rows = [item.public() for item in runtime_factory().strategies.list(market)]
        return {
            "rows": rows,
            "count": len(rows),
            "paperOnly": True,
            "liveOrdersEnabled": False,
        }

    @router.get("/risk")
    def risk() -> dict[str, Any]:
        return runtime_factory().risk.status()

    @router.get("/data-health")
    def data_health() -> dict[str, Any]:
        runtime = runtime_factory()
        return {
            "marketData": freshness(
                runtime.settings.market_data_file, runtime.settings.data_stale_seconds
            ),
            "featureCache": runtime.feature_cache.health(),
            "providers": provider_rows(runtime),
            "providerEngine": runtime.provider_health(),
            "canonicalStore": timescale_health(runtime.settings.market_data_database_url),
            "warnings": [
                "Provider availability is evaluated independently per instrument and timeframe",
                "Missing spread, sector, order-book, OI, or benchmark data is never manufactured",
            ],
        }

    @router.get("/jobs")
    def jobs(limit: int = Query(default=100, ge=1, le=500)) -> dict[str, Any]:
        runtime = runtime_factory()
        rows = [mark_legacy_research_job(row) for row in runtime.job_repository.list(limit)]
        return {"rows": rows, "count": len(rows), "worker": runtime.jobs.health()}

    @router.get("/jobs/{job_id}")
    def job(job_id: str) -> dict[str, Any]:
        try:
            return mark_legacy_research_job(runtime_factory().job_repository.get(job_id))
        except KeyError as error:
            raise HTTPException(status_code=404, detail="Job was not found") from error

    @router.delete("/jobs/{job_id}")
    def cancel_job(job_id: str) -> dict[str, Any]:
        try:
            return runtime_factory().jobs.cancel(job_id)
        except KeyError as error:
            raise HTTPException(status_code=404, detail="Job was not found") from error

    @router.post("/research/estimate")
    def estimate_research(request: ResearchRequest) -> dict[str, Any]:
        try:
            return runtime_factory().research.estimate(request)
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

    @router.post("/research/experiments", status_code=202)
    def start_research(
        request: ResearchRequest,
        idempotency_key: str | None = Header(default=None, alias="X-Idempotency-Key"),
    ) -> dict[str, Any]:
        runtime = runtime_factory()
        if not runtime.settings.research_engine_v2_enabled:
            raise HTTPException(
                status_code=503,
                detail=(
                    "RESEARCH_ENGINE_V2_DISABLED: new experiments are blocked by the "
                    "server-side fail-closed safety gate"
                ),
            )
        try:
            runtime.research.estimate(request)
            result = runtime.jobs.submit(
                "RESEARCH_EXPERIMENT",
                request.snapshot(),
                runtime.research.run,
                idempotency_key=idempotency_key,
            )
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        except RuntimeError as error:
            raise HTTPException(status_code=429, detail=str(error)) from error
        runtime.metrics.increment("research_jobs_submitted")
        return result

    @router.get("/metrics")
    def metrics() -> dict[str, Any]:
        runtime = runtime_factory()
        return {
            **runtime.metrics.snapshot(),
            "jobs": runtime.jobs.health(),
            "generatedAt": utc_now_iso(),
        }

    return router


def install_platform_observability(
    app: FastAPI, runtime_factory: Callable[[], PlatformRuntime]
) -> None:
    @app.middleware("http")
    async def correlated_requests(request: Request, call_next):  # type: ignore[no-untyped-def]
        identifier = request_id(request.headers.get("x-request-id"))
        started = time.monotonic()
        try:
            response = await call_next(request)
        except Exception as error:
            runtime = runtime_factory()
            runtime.metrics.increment("http_unhandled_errors")
            runtime.logger.event(
                "http_request_failed",
                requestId=identifier,
                path=request.url.path,
                errorType=type(error).__name__,
            )
            return JSONResponse(
                status_code=500,
                content={"detail": "Internal server error", "requestId": identifier},
            )
        elapsed = time.monotonic() - started
        runtime = runtime_factory()
        runtime.metrics.increment(f"http_status_{response.status_code}")
        runtime.metrics.observe("http_request_seconds", elapsed)
        response.headers["X-Request-ID"] = identifier
        return response
