"""Bounded Operations/Monitoring API."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from backend.data.database import DatabaseUnavailable
from backend.monitoring.service import MonitoringService


class AlertActionRequest(BaseModel):
    actor: Annotated[str, Field(min_length=1, max_length=200)] = "platform-user"
    resolution: Annotated[str | None, Field(max_length=1000)] = None


def create_monitoring_router(service: Callable[[], MonitoringService]) -> APIRouter:
    router = APIRouter(prefix="/v2/operations", tags=["operations"])

    def current() -> MonitoringService:
        try:
            return service()
        except DatabaseUnavailable as error:
            raise HTTPException(status_code=503, detail=str(error)) from error

    @router.get("/health")
    def health() -> dict:
        return current().health()

    @router.get("/leases")
    def leases(limit: Annotated[int, Query(ge=1, le=200)] = 100) -> dict:
        return {"items": current().repository.leases(limit=limit)}

    @router.get("/alerts")
    def alerts(
        status: Annotated[str | None, Query(pattern="^(OPEN|ACKNOWLEDGED|RESOLVED)$")] = None,
        limit: Annotated[int, Query(ge=1, le=200)] = 100,
    ) -> dict:
        return {"items": current().repository.alerts(status=status, limit=limit)}

    @router.post("/alerts/{alert_id}/acknowledge")
    def acknowledge(alert_id: str, request: AlertActionRequest) -> dict:
        try:
            return current().repository.acknowledge_alert(alert_id, actor=request.actor)
        except (KeyError, ValueError) as error:
            raise HTTPException(status_code=404 if isinstance(error, KeyError) else 422, detail=str(error)) from error

    @router.post("/alerts/{alert_id}/resolve")
    def resolve(alert_id: str, request: AlertActionRequest) -> dict:
        try:
            return current().repository.resolve_alert(
                alert_id, actor=request.actor, resolution=request.resolution or ""
            )
        except KeyError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

    @router.get("/audit")
    def audit(
        limit: Annotated[int, Query(ge=1, le=200)] = 100,
        before: datetime | None = None,
    ) -> dict:
        return {"items": current().repository.audit_events(limit=limit, before=before)}

    return router
