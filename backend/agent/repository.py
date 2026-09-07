"""Hashed agent credentials, durable rate limiting, and at-most-once tool requests."""

from __future__ import annotations

import hashlib
import secrets
import uuid
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

from backend.data.database import Database, jsonb

AGENT_SCOPES = frozenset(
    {
        "research:read",
        "backtests:submit",
        "experiments:submit",
        "walk-forward:submit",
        "ai:use",
        "monitoring:read",
    }
)
TOKEN_PREFIX = "odt_"


class AgentAccessDenied(RuntimeError):
    """The supplied agent credential is invalid, expired, revoked, or under-scoped."""


class AgentRateLimit(RuntimeError):
    """The token exhausted its durable per-minute request allowance."""


class AgentRequestConflict(RuntimeError):
    """An idempotency key was reused for a different or unresolved request."""


class AgentTokenRepository:
    def __init__(self, database: Database, *, clock: Any | None = None) -> None:
        self.database = database
        self.clock = clock or (lambda: datetime.now(UTC))

    def create(
        self,
        *,
        name: str,
        scopes: Sequence[str],
        expires_at: datetime,
        created_by: str,
        rate_limit_per_minute: int = 60,
    ) -> tuple[dict[str, Any], str]:
        now = self.clock()
        label = name.strip()
        if not label:
            raise ValueError("Agent token name is required")
        selected = sorted(set(scopes))
        if not selected or not set(selected).issubset(AGENT_SCOPES):
            raise ValueError("One or more agent scopes are unsupported")
        if expires_at.tzinfo is None:
            raise ValueError("Agent token expiry must include a timezone")
        if expires_at <= now or expires_at > now + timedelta(days=365):
            raise ValueError("Agent token expiry must be within the next 365 days")
        if not 1 <= rate_limit_per_minute <= 300:
            raise ValueError("Agent rate limit must be between 1 and 300 requests per minute")
        raw_token = TOKEN_PREFIX + secrets.token_urlsafe(32)
        token_id = uuid.uuid4()
        row = self.database.fetch_one(
            """
            INSERT INTO agent_access_tokens (
                token_id, name, token_prefix, token_hash, scopes,
                rate_limit_per_minute, expires_at, created_by, created_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING *
            """,
            (
                token_id,
                label,
                raw_token[:12],
                _token_hash(raw_token),
                jsonb(selected),
                rate_limit_per_minute,
                expires_at,
                created_by[:200],
                now,
            ),
        )
        assert row is not None
        return _public_token(row), raw_token

    def list(self, *, limit: int = 100) -> list[dict[str, Any]]:
        rows = self.database.fetch_all(
            "SELECT * FROM agent_access_tokens ORDER BY created_at DESC LIMIT %s",
            (min(max(limit, 1), 200),),
        )
        return [_public_token(row) for row in rows]

    def revoke(self, token_id: str) -> dict[str, Any]:
        row = self.database.fetch_one(
            """
            UPDATE agent_access_tokens
            SET revoked_at = COALESCE(revoked_at, %s)
            WHERE token_id = %s RETURNING *
            """,
            (self.clock(), uuid.UUID(token_id)),
        )
        if row is None:
            raise KeyError("Agent token was not found")
        return _public_token(row)

    def authenticate(self, raw_token: str) -> dict[str, Any]:
        if not raw_token.startswith(TOKEN_PREFIX) or len(raw_token) > 128:
            raise AgentAccessDenied("Invalid agent token")
        now = self.clock()
        window = now.replace(second=0, microsecond=0)
        with self.database.transaction() as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT * FROM agent_access_tokens WHERE token_hash = %s FOR UPDATE",
                (_token_hash(raw_token),),
            )
            row = cursor.fetchone()
            if row is None or row["revoked_at"] is not None or row["expires_at"] <= now:
                raise AgentAccessDenied("Invalid agent token")
            cursor.execute(
                """
                INSERT INTO agent_rate_limit_windows (token_id, window_started_at, request_count)
                VALUES (%s, %s, 1)
                ON CONFLICT (token_id, window_started_at) DO UPDATE
                SET request_count = agent_rate_limit_windows.request_count + 1
                WHERE agent_rate_limit_windows.request_count < %s
                RETURNING request_count
                """,
                (row["token_id"], window, row["rate_limit_per_minute"]),
            )
            if cursor.fetchone() is None:
                raise AgentRateLimit("Agent request rate limit exceeded")
            cursor.execute(
                "UPDATE agent_access_tokens SET last_used_at = %s WHERE token_id = %s RETURNING *",
                (now, row["token_id"]),
            )
            updated = cursor.fetchone()
        assert updated is not None
        return _public_token(updated)

    def begin_tool_request(
        self,
        *,
        token_id: str,
        tool_name: str,
        idempotency_key: str,
        request_hash: str,
    ) -> tuple[dict[str, Any], bool]:
        row = self.database.fetch_one(
            """
            INSERT INTO agent_tool_requests (
                request_id, token_id, tool_name, idempotency_key, request_hash
            ) VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (token_id, tool_name, idempotency_key) DO NOTHING
            RETURNING *
            """,
            (uuid.uuid4(), uuid.UUID(token_id), tool_name, idempotency_key, request_hash),
        )
        created = row is not None
        if row is None:
            row = self.database.fetch_one(
                """
                SELECT * FROM agent_tool_requests
                WHERE token_id = %s AND tool_name = %s AND idempotency_key = %s
                """,
                (uuid.UUID(token_id), tool_name, idempotency_key),
            )
        assert row is not None
        if row["request_hash"] != request_hash:
            raise AgentRequestConflict("The idempotency key belongs to a different request")
        return _public_request(row), created

    def complete_tool_request(self, request_id: str, response: Mapping[str, Any]) -> None:
        self.database.execute(
            """
            UPDATE agent_tool_requests
            SET status = 'COMPLETE', response = %s, completed_at = %s
            WHERE request_id = %s AND status = 'PENDING'
            """,
            (jsonb(dict(response)), self.clock(), uuid.UUID(request_id)),
        )

    def fail_tool_request(self, request_id: str, error: Mapping[str, Any]) -> None:
        self.database.execute(
            """
            UPDATE agent_tool_requests
            SET status = 'FAILED', error = %s, completed_at = %s
            WHERE request_id = %s AND status = 'PENDING'
            """,
            (jsonb(dict(error)), self.clock(), uuid.UUID(request_id)),
        )


def canonical_request_hash(value: Mapping[str, Any]) -> str:
    import json

    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str).encode()
    return hashlib.sha256(encoded).hexdigest()


def _token_hash(raw_token: str) -> str:
    return hashlib.sha256(raw_token.encode()).hexdigest()


def _public_token(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "tokenId": str(row["token_id"]),
        "name": row["name"],
        "tokenPrefix": row["token_prefix"],
        "scopes": list(row["scopes"]),
        "rateLimitPerMinute": int(row["rate_limit_per_minute"]),
        "expiresAt": row["expires_at"].isoformat(),
        "revokedAt": row["revoked_at"].isoformat() if row.get("revoked_at") else None,
        "lastUsedAt": row["last_used_at"].isoformat() if row.get("last_used_at") else None,
        "createdBy": row["created_by"],
        "createdAt": row["created_at"].isoformat(),
    }


def _public_request(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "requestId": str(row["request_id"]),
        "requestHash": row["request_hash"],
        "status": row["status"],
        "response": row.get("response"),
        "error": row.get("error"),
    }
