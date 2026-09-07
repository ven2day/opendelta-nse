"""Persistence for encrypted exchange connections; public mappings omit all key material."""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from backend.connections.crypto import EncryptedCredentials
from backend.connections.providers import ConnectionPermissionReport
from backend.data.database import Database, jsonb


class ExchangeConnectionRepository:
    def __init__(self, database: Database) -> None:
        self.database = database

    def list(self) -> list[dict[str, Any]]:
        rows = self.database.fetch_all("SELECT * FROM exchange_connections ORDER BY provider, created_at")
        return [_public_connection(row) for row in rows]

    def get(self, connection_id: uuid.UUID | str) -> dict[str, Any]:
        row = self._row(connection_id)
        return _public_connection(row)

    def encrypted(self, connection_id: uuid.UUID | str) -> tuple[dict[str, Any], EncryptedCredentials]:
        row = self._row(connection_id)
        return row, EncryptedCredentials(
            ciphertext=bytes(row["credentials_ciphertext"]),
            data_nonce=bytes(row["credentials_nonce"]),
            encrypted_dek=bytes(row["encrypted_data_key"]),
            dek_nonce=bytes(row["data_key_nonce"]),
            key_version=row["master_key_version"],
        )

    def create(
        self,
        *,
        connection_id: uuid.UUID,
        provider: str,
        label: str,
        environment: str,
        masked_key_identifier: str,
        encrypted: EncryptedCredentials,
        actor: str,
    ) -> dict[str, Any]:
        with self.database.transaction() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO exchange_connections (
                    connection_id, provider, label, account_environment, masked_key_identifier,
                    credentials_ciphertext, credentials_nonce, encrypted_data_key, data_key_nonce,
                    master_key_version
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING *
                """,
                (
                    connection_id, provider, label, environment, masked_key_identifier,
                    encrypted.ciphertext, encrypted.data_nonce, encrypted.encrypted_dek,
                    encrypted.dek_nonce, encrypted.key_version,
                ),
            )
            row = cursor.fetchone()
            _event(cursor, connection_id, provider, actor, "ADDED", {"environment": environment})
        return _public_connection(row)

    def replace(
        self,
        connection_id: uuid.UUID | str,
        *,
        label: str,
        environment: str,
        masked_key_identifier: str,
        encrypted: EncryptedCredentials,
        actor: str,
    ) -> dict[str, Any]:
        key = uuid.UUID(str(connection_id))
        with self.database.transaction() as connection, connection.cursor() as cursor:
            cursor.execute("SELECT provider FROM exchange_connections WHERE connection_id = %s FOR UPDATE", (key,))
            current = cursor.fetchone()
            if current is None:
                raise KeyError(f"Exchange connection {connection_id} was not found")
            cursor.execute(
                """
                UPDATE exchange_connections SET label = %s, account_environment = %s,
                    masked_key_identifier = %s, credentials_ciphertext = %s, credentials_nonce = %s,
                    encrypted_data_key = %s, data_key_nonce = %s, master_key_version = %s,
                    disabled = true, status = 'NOT_TESTED', permissions = '{}'::jsonb,
                    last_test_success = NULL, last_test_message = NULL, last_tested_at = NULL, updated_at = %s
                WHERE connection_id = %s RETURNING *
                """,
                (
                    label, environment, masked_key_identifier, encrypted.ciphertext, encrypted.data_nonce,
                    encrypted.encrypted_dek, encrypted.dek_nonce, encrypted.key_version, datetime.now(UTC), key,
                ),
            )
            row = cursor.fetchone()
            _event(cursor, key, current["provider"], actor, "REPLACED", {"environment": environment})
        return _public_connection(row)

    def update_encryption(
        self,
        connection_id: uuid.UUID | str,
        *,
        encrypted: EncryptedCredentials,
        actor: str,
    ) -> dict[str, Any]:
        key = uuid.UUID(str(connection_id))
        with self.database.transaction() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE exchange_connections SET credentials_ciphertext = %s, credentials_nonce = %s,
                    encrypted_data_key = %s, data_key_nonce = %s, master_key_version = %s, updated_at = %s
                WHERE connection_id = %s RETURNING *
                """,
                (
                    encrypted.ciphertext, encrypted.data_nonce, encrypted.encrypted_dek,
                    encrypted.dek_nonce, encrypted.key_version, datetime.now(UTC), key,
                ),
            )
            row = cursor.fetchone()
            if row is None:
                raise KeyError(f"Exchange connection {connection_id} was not found")
            _event(cursor, key, row["provider"], actor, "ROTATED", {"keyVersion": encrypted.key_version})
        return _public_connection(row)

    def record_test(
        self,
        connection_id: uuid.UUID | str,
        *,
        report: ConnectionPermissionReport | None,
        actor: str,
        failure_message: str | None = None,
    ) -> dict[str, Any]:
        key = uuid.UUID(str(connection_id))
        now = datetime.now(UTC)
        withdrawal = report is not None and report.withdrawal is True
        failed = report is None or not report.authenticated or not report.read
        status = "WITHDRAWAL_PERMISSION" if withdrawal else "FAILED" if failed else "CONNECTED"
        disabled = withdrawal
        permissions = report.public() if report else {}
        message = report.message if report else (failure_message or "Exchange connection test failed")
        with self.database.transaction() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE exchange_connections SET status = %s, disabled = disabled OR %s, permissions = %s,
                    last_test_success = %s, last_test_message = %s, last_tested_at = %s, updated_at = %s
                WHERE connection_id = %s RETURNING *
                """,
                (status, disabled, jsonb(permissions), not failed and not withdrawal, message, now, now, key),
            )
            row = cursor.fetchone()
            if row is None:
                raise KeyError(f"Exchange connection {connection_id} was not found")
            action = "WITHDRAWAL_PERMISSION" if withdrawal else "TEST_FAILED" if failed else "TEST_SUCCEEDED"
            _event(cursor, key, row["provider"], actor, action, {"status": status})
        return _public_connection(row)

    def set_disabled(
        self, connection_id: uuid.UUID | str, *, disabled: bool, actor: str
    ) -> dict[str, Any]:
        key = uuid.UUID(str(connection_id))
        now = datetime.now(UTC)
        with self.database.transaction() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE exchange_connections SET disabled = %s,
                    status = CASE WHEN %s THEN 'DISABLED' WHEN status = 'DISABLED' THEN 'NOT_TESTED' ELSE status END,
                    updated_at = %s WHERE connection_id = %s RETURNING *
                """,
                (disabled, disabled, now, key),
            )
            row = cursor.fetchone()
            if row is None:
                raise KeyError(f"Exchange connection {connection_id} was not found")
            if not disabled and row["permissions"].get("withdrawal") is True:
                raise ValueError("A connection with withdrawal permission cannot be enabled")
            _event(cursor, key, row["provider"], actor, "DISABLED" if disabled else "ENABLED", {})
        return _public_connection(row)

    def delete(self, connection_id: uuid.UUID | str, *, actor: str) -> None:
        key = uuid.UUID(str(connection_id))
        with self.database.transaction() as connection, connection.cursor() as cursor:
            cursor.execute("DELETE FROM exchange_connections WHERE connection_id = %s RETURNING provider", (key,))
            row = cursor.fetchone()
            if row is None:
                raise KeyError(f"Exchange connection {connection_id} was not found")
            _event(cursor, key, row["provider"], actor, "DELETED", {})

    def _row(self, connection_id: uuid.UUID | str) -> dict[str, Any]:
        row = self.database.fetch_one(
            "SELECT * FROM exchange_connections WHERE connection_id = %s",
            (uuid.UUID(str(connection_id)),),
        )
        if row is None:
            raise KeyError(f"Exchange connection {connection_id} was not found")
        return row


def _event(cursor: Any, connection_id: uuid.UUID, provider: str, actor: str, action: str, details: Mapping[str, Any]) -> None:
    cursor.execute(
        """
        INSERT INTO exchange_connection_events (connection_id, provider, actor, action, details)
        VALUES (%s, %s, %s, %s, %s)
        """,
        (connection_id, provider, actor, action, jsonb(dict(details))),
    )


def _public_connection(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "connectionId": str(row["connection_id"]),
        "provider": row["provider"],
        "label": row["label"],
        "environment": row["account_environment"],
        "configured": True,
        "maskedKeyIdentifier": row["masked_key_identifier"],
        "disabled": row["disabled"],
        "status": row["status"],
        "permissions": row["permissions"],
        "lastTestSuccess": row["last_test_success"],
        "lastTestMessage": row["last_test_message"],
        "lastTestedAt": row["last_tested_at"].isoformat() if row["last_tested_at"] else None,
        "createdAt": row["created_at"].isoformat(),
        "updatedAt": row["updated_at"].isoformat(),
    }
