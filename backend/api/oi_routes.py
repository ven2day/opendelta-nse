"""NIFTY OI history status route."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from backend.runtime import get_oi_repository


def create_oi_router() -> APIRouter:
    router = APIRouter()

    @router.get("/nifty-oi/history/status")
    def nifty_oi_history_status() -> dict[str, Any]:
        """Expose import coverage without returning credentials or raw contract payloads."""
        try:
            return get_oi_repository().history_status()
        except (OSError, RuntimeError, ValueError) as error:
            raise HTTPException(status_code=503, detail=str(error)) from error

    return router