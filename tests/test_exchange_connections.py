from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import uuid
from datetime import UTC, datetime
from typing import Any

import pytest
from backend.api.exchange_connection_routes import (
    ConfirmationInput,
    CredentialInput,
    DisableInput,
    ExchangeConnectionServices,
    ReplaceCredentialInput,
    create_exchange_connection_router,
    dhan_connection_status,
    install_exchange_connection_validation_handler,
)
from backend.connections.crypto import (
    CredentialKeyError,
    EncryptedCredentials,
    EnvelopeCipher,
    MasterKeyring,
)
from backend.connections.providers import (
    ConnectionPermissionReport,
    ConnectionTestError,
    OkxConnectionTester,
    ValrConnectionTester,
)
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from pydantic import ValidationError


def endpoints(router):
    return {
        f"{next(iter(route.methods - {'HEAD', 'OPTIONS'}))} {route.path}": route.endpoint
        for route in router.routes
    }


def keyring(version: str = "v1", *, previous: dict[str, bytes] | None = None) -> MasterKeyring:
    keys = dict(previous or {})
    keys[version] = secrets.token_bytes(32)
    return MasterKeyring(version, keys)


def private_values(provider: str = "OKX", environment: str = "LIVE") -> dict[str, str]:
    values = {
        "apiKey": secrets.token_hex(32),
        "apiSecret": secrets.token_hex(32),
        "environment": environment,
    }
    if provider == "OKX":
        values["passphrase"] = secrets.token_urlsafe(16)
    return values


def test_envelope_encryption_uses_fresh_data_keys_nonces_and_authenticated_context() -> None:
    cipher = EnvelopeCipher(keyring())
    connection_id = str(uuid.uuid4())
    private = private_values()
    first = cipher.encrypt(connection_id, private)
    second = cipher.encrypt(connection_id, private)

    assert cipher.decrypt(connection_id, first) == private
    assert first.ciphertext != second.ciphertext
    assert first.data_nonce != second.data_nonce
    assert first.dek_nonce != second.dek_nonce
    assert private["apiSecret"].encode() not in first.ciphertext
    with pytest.raises(CredentialKeyError):
        cipher.decrypt(str(uuid.uuid4()), first)


def test_encrypted_material_rotation_reads_old_key_and_rewraps_with_current_key() -> None:
    old = keyring("old")
    connection_id = str(uuid.uuid4())
    private = private_values()
    original = EnvelopeCipher(old).encrypt(connection_id, private)
    current = MasterKeyring("current", {"old": old.keys["old"], "current": secrets.token_bytes(32)})
    rotated = EnvelopeCipher(current).rotate(connection_id, original)

    assert rotated.key_version == "current"
    assert rotated.ciphertext != original.ciphertext
    assert EnvelopeCipher(current).decrypt(connection_id, rotated) == private
    with pytest.raises(CredentialKeyError):
        EnvelopeCipher(MasterKeyring("current", {"current": current.keys["current"]})).decrypt(
            connection_id, original
        )


def test_master_key_environment_is_strict_and_rotation_ready() -> None:
    current, old = secrets.token_bytes(32), secrets.token_bytes(32)
    configured = MasterKeyring.from_environment({
        "EXCHANGE_CREDENTIAL_MASTER_KEY": base64.b64encode(current).decode(),
        "EXCHANGE_CREDENTIAL_MASTER_KEY_VERSION": "v2",
        "EXCHANGE_CREDENTIAL_PREVIOUS_KEYS": '{"v1":"' + base64.b64encode(old).decode() + '"}',
    })
    assert configured is not None
    assert configured.keys == {"v2": current, "v1": old}
    assert MasterKeyring.from_environment({}) is None
    with pytest.raises(CredentialKeyError):
        MasterKeyring.from_environment({"EXCHANGE_CREDENTIAL_MASTER_KEY": "not-base64", "EXCHANGE_CREDENTIAL_MASTER_KEY_VERSION": "v1"})


def test_okx_read_only_connection_contract_and_demo_header() -> None:
    captured: dict[str, Any] = {}
    private = private_values(environment="DEMO")

    def transport(request, timeout: float):
        captured.update(request=request, timeout=timeout)
        return {"code": "0", "data": [{"perm": "read_only,trade", "ip": "restricted"}]}

    def clock() -> datetime:
        return datetime(2026, 1, 2, 3, 4, 5, 678000, tzinfo=UTC)
    report = OkxConnectionTester(transport=transport, clock=clock).test(private)
    request = captured["request"]
    expected = base64.b64encode(hmac.new(
        private["apiSecret"].encode(),
        b"2026-01-02T03:04:05.678ZGET/api/v5/account/config",
        hashlib.sha256,
    ).digest()).decode()
    assert request.get_header("Ok-access-sign") == expected
    assert request.get_header("X-simulated-trading") == "1"
    assert report.authenticated and report.read and report.trade
    assert report.withdrawal is False and report.ip_allowlisted is True


def test_valr_connection_contract_detects_withdrawal_permission() -> None:
    captured: dict[str, Any] = {}
    private = private_values("VALR")

    def transport(request, timeout: float):
        captured.update(request=request, timeout=timeout)
        return {"permissions": ["VIEW", "TRADE", "WITHDRAW_CRYPTO"], "allowedIps": []}

    report = ValrConnectionTester(transport=transport, clock_ms=lambda: 1558014486185).test(private)
    request = captured["request"]
    expected = hmac.new(
        private["apiSecret"].encode(),
        b"1558014486185GET/v1/account/api-keys/current",
        hashlib.sha512,
    ).hexdigest()
    assert request.get_header("X-valr-signature") == expected
    assert report.withdrawal is True
    assert report.ip_allowlisted is False


class FakeRepository:
    def __init__(self) -> None:
        self.rows: dict[str, dict[str, Any]] = {}
        self.encrypted_values: dict[str, EncryptedCredentials] = {}

    def list(self):
        return list(self.rows.values())

    def get(self, connection_id: str):
        if connection_id not in self.rows:
            raise KeyError(connection_id)
        return self.rows[connection_id]

    def encrypted(self, connection_id: str):
        row = self.get(connection_id)
        return {"provider": row["provider"]}, self.encrypted_values[connection_id]

    def create(self, *, connection_id, provider, label, environment, masked_key_identifier, encrypted, actor):
        assert actor
        row = self._row(str(connection_id), provider, label, environment, masked_key_identifier)
        self.rows[str(connection_id)] = row
        self.encrypted_values[str(connection_id)] = encrypted
        return row

    def replace(self, connection_id, *, label, environment, masked_key_identifier, encrypted, actor):
        assert actor
        current = self.get(connection_id)
        row = self._row(connection_id, current["provider"], label, environment, masked_key_identifier)
        self.rows[connection_id] = row
        self.encrypted_values[connection_id] = encrypted
        return row

    def record_test(self, connection_id, *, report, actor, failure_message=None):
        assert actor
        row = self.get(connection_id)
        withdrawal = report is not None and report.withdrawal is True
        failed = report is None or not report.authenticated or not report.read
        row.update(
            status="WITHDRAWAL_PERMISSION" if withdrawal else "FAILED" if failed else "CONNECTED",
            disabled=row["disabled"] or withdrawal,
            permissions=report.public() if report else {},
            lastTestSuccess=not failed and not withdrawal,
            lastTestMessage=report.message if report else failure_message,
        )
        return row

    def set_disabled(self, connection_id, *, disabled, actor):
        assert actor
        row = self.get(connection_id)
        if not disabled and row["permissions"].get("withdrawal") is True:
            raise ValueError("A connection with withdrawal permission cannot be enabled")
        row["disabled"] = disabled
        row["status"] = "DISABLED" if disabled else "NOT_TESTED"
        return row

    def update_encryption(self, connection_id, *, encrypted, actor):
        assert actor
        self.encrypted_values[connection_id] = encrypted
        return self.get(connection_id)

    def delete(self, connection_id, *, actor):
        assert actor
        self.get(connection_id)
        del self.rows[connection_id]
        del self.encrypted_values[connection_id]

    @staticmethod
    def _row(connection_id, provider, label, environment, masked):
        return {
            "connectionId": connection_id, "provider": provider, "label": label,
            "environment": environment, "configured": True, "maskedKeyIdentifier": masked,
            "disabled": True, "status": "NOT_TESTED", "permissions": {},
            "lastTestSuccess": None, "lastTestMessage": None, "lastTestedAt": None,
            "createdAt": "2026-01-01T00:00:00+00:00", "updatedAt": "2026-01-01T00:00:00+00:00",
        }


class WithdrawalTester:
    provider = "OKX"

    def test(self, credentials):
        assert credentials
        return ConnectionPermissionReport(True, True, True, True, False, "LIVE", "AVAILABLE", "Withdrawal detected")


class SafeTester:
    provider = "OKX"

    def test(self, credentials):
        assert credentials
        return ConnectionPermissionReport(True, True, True, False, True, "LIVE", "AVAILABLE", "Connected")


class MissingReadTester:
    provider = "OKX"

    def test(self, credentials):
        assert credentials
        return ConnectionPermissionReport(True, False, True, False, None, "LIVE", "AVAILABLE", "Read missing")


def connection_api(repository: FakeRepository, cipher: EnvelopeCipher | None, tester: Any = None):
    return endpoints(create_exchange_connection_router(ExchangeConnectionServices(
        repository=lambda: repository, cipher=lambda: cipher,
        testers=lambda: {"OKX": tester or WithdrawalTester()},
        dhan_status=lambda: dhan_connection_status({}),
    )))


def request(provider: str = "OKX") -> CredentialInput:
    private = private_values(provider)
    return CredentialInput(
        provider=provider, label="Research account", environment="LIVE",
        apiKey=private["apiKey"], apiSecret=private["apiSecret"], passphrase=private.get("passphrase"),
    )


def test_connection_api_never_returns_secrets_and_fails_closed_without_master_key() -> None:
    repository = FakeRepository()
    api = connection_api(repository, None)
    with pytest.raises(HTTPException) as error:
        api["POST /v2/connections"](request())
    assert error.value.status_code == 503

    private_cipher = EnvelopeCipher(keyring())
    api = connection_api(repository, private_cipher)
    secret_request = request()
    created = api["POST /v2/connections"](secret_request)
    serialized = str(created)
    assert secret_request.apiKey.get_secret_value() not in serialized
    assert secret_request.apiSecret.get_secret_value() not in serialized
    assert "ciphertext" not in serialized and "encrypted" not in serialized
    assert created["maskedKeyIdentifier"].startswith("••••")
    assert created["disabled"] is True


def test_connection_confirmation_test_disable_rotation_and_deletion() -> None:
    repository = FakeRepository()
    cipher = EnvelopeCipher(keyring())
    api = connection_api(repository, cipher)
    created = api["POST /v2/connections"](request())
    connection_id = created["connectionId"]

    tested = api["POST /v2/connections/{connection_id}/test"](connection_id)
    assert tested["status"] == "WITHDRAWAL_PERMISSION"
    assert tested["disabled"] is True
    with pytest.raises(HTTPException):
        api["POST /v2/connections/{connection_id}/disable"](connection_id, DisableInput(disabled=False))
    with pytest.raises(HTTPException):
        api["POST /v2/connections/{connection_id}/replace"](
            connection_id, ReplaceCredentialInput(**request().model_dump(), confirmation="wrong")
        )
    before = repository.encrypted_values[connection_id]
    rotated = api["POST /v2/connections/{connection_id}/rotate"](
        connection_id, ConfirmationInput(confirmation="ROTATE")
    )
    assert rotated["connectionId"] == connection_id
    assert repository.encrypted_values[connection_id].ciphertext != before.ciphertext
    api["POST /v2/connections/{connection_id}/delete"](
        connection_id, ConfirmationInput(confirmation="DELETE OKX")
    )
    assert repository.rows == {}


def test_connection_stays_disabled_after_safe_test_and_missing_read_fails() -> None:
    repository = FakeRepository()
    cipher = EnvelopeCipher(keyring())
    safe_api = connection_api(repository, cipher, SafeTester())
    safe = safe_api["POST /v2/connections"](request())
    tested = safe_api["POST /v2/connections/{connection_id}/test"](safe["connectionId"])
    assert tested["status"] == "CONNECTED"
    assert tested["disabled"] is True
    enabled = safe_api["POST /v2/connections/{connection_id}/disable"](
        safe["connectionId"], DisableInput(disabled=False)
    )
    assert enabled["disabled"] is False

    missing_api = connection_api(repository, cipher, MissingReadTester())
    missing = missing_api["POST /v2/connections"](request())
    missing = missing_api["POST /v2/connections/{connection_id}/test"](missing["connectionId"])
    assert missing["status"] == "FAILED"
    assert missing["lastTestSuccess"] is False


def test_provider_validation_and_dhan_status_expose_no_credential_values() -> None:
    private = private_values("VALR")
    with pytest.raises(ValidationError) as error:
        CredentialInput(
            provider="VALR", label="bad", environment="DEMO", apiKey=private["apiKey"],
            apiSecret=private["apiSecret"], passphrase=secrets.token_urlsafe(8),
        )
    assert private["apiKey"] not in str(error.value)
    assert private["apiSecret"] not in str(error.value)
    dhan = dhan_connection_status({
        "DHAN_CLIENT_ID": secrets.token_hex(8), "DHAN_PIN": secrets.token_hex(8),
        "DHAN_TOTP_SECRET": secrets.token_hex(8),
    })
    assert dhan["configured"] is True
    assert set(dhan) == {"provider", "managedBy", "configured", "status", "message"}


def test_http_validation_never_echoes_submitted_credentials() -> None:
    repository = FakeRepository()
    app = FastAPI()
    install_exchange_connection_validation_handler(app)
    app.include_router(create_exchange_connection_router(ExchangeConnectionServices(
        repository=lambda: repository, cipher=lambda: None, testers=lambda: {},
        dhan_status=lambda: dhan_connection_status({}),
    )))
    private = private_values("VALR")
    response = TestClient(app).post("/v2/connections", json={
        "provider": "VALR", "label": "Invalid", "environment": "DEMO",
        "apiKey": private["apiKey"], "apiSecret": private["apiSecret"],
        "passphrase": secrets.token_urlsafe(8),
    })
    assert response.status_code == 422
    assert response.json() == {"detail": "Invalid exchange connection request"}
    assert private["apiKey"] not in response.text
    assert private["apiSecret"] not in response.text


def test_provider_failure_is_generic() -> None:
    tester = OkxConnectionTester(transport=lambda _request, _timeout: {"code": "501", "msg": secrets.token_hex(20)})
    with pytest.raises(ConnectionTestError, match="authentication failed"):
        tester.test(private_values())
