"""Secret-safe exchange connection management endpoints."""

from __future__ import annotations

import os
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Annotated, Any, Literal

from fastapi import APIRouter, FastAPI, HTTPException, Request
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, SecretStr, model_validator

from backend.connections.crypto import CredentialKeyError, EnvelopeCipher
from backend.connections.providers import ConnectionTester, ConnectionTestError
from backend.connections.repository import ExchangeConnectionRepository
from backend.data.database import DatabaseUnavailable

Actor = "authenticated-web-user"


class CredentialInput(BaseModel):
    provider: Literal["OKX", "VALR"]
    label: str = Field(min_length=1, max_length=120)
    environment: Literal["LIVE", "DEMO"] = "LIVE"
    apiKey: Annotated[SecretStr, Field(min_length=8, max_length=512)]
    apiSecret: Annotated[SecretStr, Field(min_length=8, max_length=512)]
    passphrase: Annotated[SecretStr, Field(min_length=1, max_length=512)] | None = None

    @model_validator(mode="after")
    def provider_fields(self) -> CredentialInput:
        if self.provider == "OKX" and self.passphrase is None:
            raise ValueError("OKX requires a passphrase")
        if self.provider == "VALR" and self.passphrase is not None:
            raise ValueError("VALR does not use a passphrase")
        if self.provider == "VALR" and self.environment != "LIVE":
            raise ValueError("VALR supports LIVE connections only")
        return self

    def private(self) -> dict[str, str]:
        values = {
            "apiKey": self.apiKey.get_secret_value(),
            "apiSecret": self.apiSecret.get_secret_value(),
            "environment": self.environment,
        }
        if self.passphrase is not None:
            values["passphrase"] = self.passphrase.get_secret_value()
        return values


class ReplaceCredentialInput(CredentialInput):
    confirmation: str


class ConfirmationInput(BaseModel):
    confirmation: str


class DisableInput(BaseModel):
    disabled: bool


@dataclass(frozen=True)
class ExchangeConnectionServices:
    repository: Callable[[], ExchangeConnectionRepository]
    cipher: Callable[[], EnvelopeCipher | None]
    testers: Callable[[], Mapping[str, ConnectionTester]]
    dhan_status: Callable[[], dict[str, Any]]


def install_exchange_connection_validation_handler(app: FastAPI) -> None:
    """Prevent FastAPI's validation payload from echoing submitted secrets."""

    @app.exception_handler(RequestValidationError)
    async def secret_safe_validation(request: Request, error: RequestValidationError):
        if request.url.path == "/v2/connections" or request.url.path.startswith("/v2/connections/"):
            return JSONResponse(status_code=422, content={"detail": "Invalid exchange connection request"})
        return await request_validation_exception_handler(request, error)


def create_exchange_connection_router(services: ExchangeConnectionServices) -> APIRouter:
    router = APIRouter(prefix="/v2/connections", tags=["exchange-connections"])

    def repository() -> ExchangeConnectionRepository:
        try:
            return services.repository()
        except DatabaseUnavailable as error:
            raise HTTPException(status_code=503, detail=str(error)) from error

    def cipher() -> EnvelopeCipher:
        try:
            configured = services.cipher()
        except CredentialKeyError as error:
            raise HTTPException(status_code=503, detail=str(error)) from error
        if configured is None:
            raise HTTPException(status_code=503, detail="Exchange credential encryption is not configured")
        return configured

    def existing(operation: Callable[[], Any]) -> Any:
        try:
            return operation()
        except (KeyError, ValueError) as error:
            raise HTTPException(status_code=404, detail="Exchange connection was not found") from error

    @router.get("")
    def list_connections() -> dict[str, Any]:
        try:
            encryption_configured = services.cipher() is not None
        except CredentialKeyError:
            encryption_configured = False
        return {
            "encryptionConfigured": encryption_configured,
            "connections": repository().list(),
            "platformConnections": [services.dhan_status()],
            "publicMarketData": {
                "OKX": {"available": True, "requiresPrivateConnection": False},
                "VALR": {"available": True, "requiresPrivateConnection": False},
                "DHAN": {"available": services.dhan_status()["configured"], "requiresPrivateConnection": True},
            },
        }

    @router.post("", status_code=201)
    def create_connection(request: CredentialInput) -> dict[str, Any]:
        connection_id = uuid.uuid4()
        private = request.private()
        encrypted = cipher().encrypt(str(connection_id), private)
        return repository().create(
            connection_id=connection_id,
            provider=request.provider,
            label=request.label.strip(),
            environment=request.environment,
            masked_key_identifier=_masked(private["apiKey"]),
            encrypted=encrypted,
            actor=Actor,
        )

    @router.post("/{connection_id}/replace")
    def replace_connection(connection_id: str, request: ReplaceCredentialInput) -> dict[str, Any]:
        current = existing(lambda: repository().get(connection_id))
        if request.provider != current["provider"]:
            raise HTTPException(status_code=422, detail="Replacement provider must match the connection")
        if request.confirmation != f"REPLACE {current['provider']}":
            raise HTTPException(status_code=422, detail=f"Type REPLACE {current['provider']} to confirm")
        private = request.private()
        encrypted = cipher().encrypt(connection_id, private)
        return repository().replace(
            connection_id,
            label=request.label.strip(),
            environment=request.environment,
            masked_key_identifier=_masked(private["apiKey"]),
            encrypted=encrypted,
            actor=Actor,
        )

    @router.post("/{connection_id}/test")
    def test_connection(connection_id: str) -> dict[str, Any]:
        record, encrypted = existing(lambda: repository().encrypted(connection_id))
        try:
            private = cipher().decrypt(connection_id, encrypted)
        except CredentialKeyError as error:
            raise HTTPException(status_code=503, detail=str(error)) from error
        try:
            tester = services.testers().get(record["provider"])
        except ValueError as error:
            raise HTTPException(status_code=503, detail="Private connection tester configuration is invalid") from error
        if tester is None:
            raise HTTPException(status_code=503, detail="Private connection tester is unavailable")
        try:
            report = tester.test(private)
        except ConnectionTestError:
            return repository().record_test(
                connection_id, report=None, actor=Actor, failure_message="Exchange authentication failed"
            )
        return repository().record_test(connection_id, report=report, actor=Actor)

    @router.get("/{connection_id}/permissions")
    def permissions(connection_id: str) -> dict[str, Any]:
        record = existing(lambda: repository().get(connection_id))
        return {
            "connectionId": record["connectionId"],
            "provider": record["provider"],
            "status": record["status"],
            "permissions": record["permissions"],
            "lastTestedAt": record["lastTestedAt"],
        }

    @router.post("/{connection_id}/disable")
    def disable_connection(connection_id: str, request: DisableInput) -> dict[str, Any]:
        try:
            return repository().set_disabled(connection_id, disabled=request.disabled, actor=Actor)
        except KeyError as error:
            raise HTTPException(status_code=404, detail="Exchange connection was not found") from error
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

    @router.post("/{connection_id}/rotate")
    def rotate_connection(connection_id: str, request: ConfirmationInput) -> dict[str, Any]:
        if request.confirmation != "ROTATE":
            raise HTTPException(status_code=422, detail="Type ROTATE to confirm encrypted-material rotation")
        _record, encrypted = existing(lambda: repository().encrypted(connection_id))
        configured = cipher()
        try:
            rotated = configured.rotate(connection_id, encrypted)
        except CredentialKeyError as error:
            raise HTTPException(status_code=503, detail=str(error)) from error
        return repository().update_encryption(connection_id, encrypted=rotated, actor=Actor)

    @router.post("/{connection_id}/delete")
    def delete_connection(connection_id: str, request: ConfirmationInput) -> dict[str, bool]:
        current = existing(lambda: repository().get(connection_id))
        if request.confirmation != f"DELETE {current['provider']}":
            raise HTTPException(status_code=422, detail=f"Type DELETE {current['provider']} to confirm")
        try:
            repository().delete(connection_id, actor=Actor)
        except KeyError as error:
            raise HTTPException(status_code=404, detail="Exchange connection was not found") from error
        return {"deleted": True}

    return router


def dhan_connection_status(environ: Mapping[str, str] | None = None) -> dict[str, Any]:
    values = os.environ if environ is None else environ
    configured = all(values.get(name, "").strip() for name in ("DHAN_CLIENT_ID", "DHAN_PIN", "DHAN_TOTP_SECRET"))
    return {
        "provider": "DHAN",
        "managedBy": "deployment",
        "configured": configured,
        "status": "CONFIGURED" if configured else "NOT_CONFIGURED",
        "message": (
            "Dhan authentication remains managed by the existing collector integration"
            if configured else "Dhan deployment credentials are not configured"
        ),
    }


def _masked(value: str) -> str:
    return f"••••{value[-4:]}"
