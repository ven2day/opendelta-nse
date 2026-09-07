"""Durable Copilot audit metadata and explicit user-saved drafts."""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

from backend.data.database import Database, jsonb


class AICopilotRateLimit(RuntimeError):
    pass


class AICopilotRepository:
    def __init__(self, database: Database) -> None:
        self.database = database

    def start_request(
        self,
        *,
        request_id: uuid.UUID,
        actor: str,
        action: str,
        categories: Sequence[str],
        provider: str,
        model: str,
        input_bytes: int,
        rate_limit: int,
    ) -> None:
        with self.database.transaction() as connection, connection.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (actor,))
            cursor.execute(
                "SELECT count(*) AS total FROM ai_copilot_requests WHERE actor = %s AND created_at >= %s",
                (actor, datetime.now(UTC) - timedelta(seconds=60)),
            )
            if int(cursor.fetchone()["total"]) >= rate_limit:
                raise AICopilotRateLimit("AI Copilot rate limit exceeded")
            cursor.execute(
                """
                INSERT INTO ai_copilot_requests (
                    request_id, actor, action, context_categories, provider, model, status, input_bytes
                ) VALUES (%s, %s, %s, %s, %s, %s, 'STARTED', %s)
                """,
                (request_id, actor, action, jsonb(list(categories)), provider, model, input_bytes),
            )

    def finish_request(
        self,
        request_id: uuid.UUID | str,
        *,
        status: str,
        duration_ms: int,
        output_bytes: int | None,
        output_sha256: str | None = None,
        usage: Mapping[str, int] | None = None,
        error_code: str | None = None,
    ) -> None:
        self.database.execute(
            """
            UPDATE ai_copilot_requests SET status = %s, duration_ms = %s, output_bytes = %s, output_sha256 = %s,
                usage = %s, error_code = %s, completed_at = %s WHERE request_id = %s
            """,
            (
                status, duration_ms, output_bytes, output_sha256, jsonb(dict(usage or {})), error_code,
                datetime.now(UTC), uuid.UUID(str(request_id)),
            ),
        )

    def recent_count(self, actor: str, *, seconds: int = 60) -> int:
        row = self.database.fetch_one(
            "SELECT count(*) AS total FROM ai_copilot_requests WHERE actor = %s AND created_at >= %s",
            (actor, datetime.now(UTC) - timedelta(seconds=seconds)),
        )
        return int(row["total"]) if row else 0

    def get_request(self, request_id: uuid.UUID | str) -> dict[str, Any]:
        key = uuid.UUID(str(request_id))
        row = self.database.fetch_one("SELECT * FROM ai_copilot_requests WHERE request_id = %s", (key,))
        if row is None:
            raise KeyError(f"AI Copilot request {request_id} was not found")
        drafts = self.database.fetch_all(
            "SELECT draft_id FROM ai_research_drafts WHERE request_id = %s ORDER BY created_at", (key,)
        )
        return {
            "requestId": str(row["request_id"]), "actor": row["actor"], "action": row["action"],
            "contextCategories": row["context_categories"], "provider": row["provider"], "model": row["model"],
            "status": row["status"], "durationMs": row["duration_ms"], "usage": row["usage"],
            "errorCode": row["error_code"], "draftIds": [str(item["draft_id"]) for item in drafts],
            "createdAt": row["created_at"].isoformat(),
            "completedAt": row["completed_at"].isoformat() if row["completed_at"] else None,
        }

    def create_draft(self, *, request_id: uuid.UUID | str, draft_type: str, content: str) -> dict[str, Any]:
        request_key = uuid.UUID(str(request_id))
        draft_id = uuid.uuid4()
        with self.database.transaction() as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT status, action, output_sha256 FROM ai_copilot_requests WHERE request_id = %s FOR UPDATE",
                (request_key,),
            )
            request = cursor.fetchone()
            if request is None:
                raise KeyError(f"AI Copilot request {request_id} was not found")
            if request["status"] != "SUCCEEDED":
                raise ValueError("Only a successful AI response can be saved as a draft")
            if request["output_sha256"] != hashlib.sha256(content.encode()).hexdigest():
                raise ValueError("Draft content must exactly match the reviewed AI response")
            expected_type = {
                "DRAFT_STRATEGY": "STRATEGY", "DRAFT_INDICATOR": "INDICATOR",
                "DRAFT_CONFIGURATION": "CONFIGURATION", "SUGGEST_EXPERIMENT": "CONFIGURATION",
            }.get(request["action"], "NOTE")
            if draft_type != expected_type:
                raise ValueError(f"This AI response can only be saved as a {expected_type.lower()} draft")
            cursor.execute(
                """
                INSERT INTO ai_research_drafts (draft_id, request_id, draft_type, content)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (request_id) DO UPDATE SET request_id = EXCLUDED.request_id
                RETURNING *
                """,
                (draft_id, request_key, draft_type, content),
            )
            row = cursor.fetchone()
        return _public_draft(row)

    def get_draft(self, draft_id: uuid.UUID | str) -> dict[str, Any]:
        row = self.database.fetch_one(
            "SELECT * FROM ai_research_drafts WHERE draft_id = %s", (uuid.UUID(str(draft_id)),)
        )
        if row is None:
            raise KeyError(f"AI research draft {draft_id} was not found")
        return _public_draft(row)


def _public_draft(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "draftId": str(row["draft_id"]), "requestId": str(row["request_id"]),
        "draftType": row["draft_type"], "content": row["content"], "status": row["status"],
        "createdAt": row["created_at"].isoformat(),
    }
