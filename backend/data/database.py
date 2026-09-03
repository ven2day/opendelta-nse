"""Connection pooling and migrations for the platform tables.

Uses the same PostgreSQL/TimescaleDB instance as the canonical candle store
(``MARKET_DATA_DATABASE_URL``); the platform tables are plain PostgreSQL.
"""

from __future__ import annotations

import json
import os
import re
from contextlib import contextmanager
from datetime import date, datetime
from importlib.resources import files
from typing import Any, Iterator, Mapping, Sequence

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

DATABASE_URL_VARIABLE = "MARKET_DATA_DATABASE_URL"
_MIGRATION_NAME = re.compile(r"^(\d{3}_[a-z0-9_]+)\.sql$")


class DatabaseUnavailable(RuntimeError):
    """Raised when a feature needs PostgreSQL but no database is configured."""


def jsonb(value: Any) -> Jsonb:
    """Adapt Python values (including dates/datetimes) for a jsonb column."""
    return Jsonb(value, dumps=lambda item: json.dumps(item, default=_json_default))


def _json_default(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if hasattr(value, "item"):
        return value.item()  # numpy scalars
    raise TypeError(f"{type(value).__name__} is not JSON serialisable")


class Database:
    def __init__(self, database_url: str, *, min_pool_size: int = 1, max_pool_size: int = 8, pool_timeout_seconds: float = 5.0) -> None:
        if not database_url.startswith(("postgresql://", "postgres://")):
            raise ValueError(f"{DATABASE_URL_VARIABLE} must be a PostgreSQL URL")
        self.pool = ConnectionPool(
            database_url,
            min_size=min_pool_size,
            max_size=max_pool_size,
            kwargs={"row_factory": dict_row, "autocommit": False, "connect_timeout": 3},
            timeout=pool_timeout_seconds,
            open=False,
        )

    @classmethod
    def from_environment(cls, variable: str = DATABASE_URL_VARIABLE) -> "Database | None":
        url = os.environ.get(variable, "").strip()
        return cls(url) if url else None

    def open(self) -> None:
        self.pool.open(wait=True)

    def close(self) -> None:
        self.pool.close()

    @contextmanager
    def connection(self) -> Iterator[Any]:
        with self.pool.connection() as connection:
            yield connection

    @contextmanager
    def transaction(self) -> Iterator[Any]:
        with self.pool.connection() as connection:
            try:
                yield connection
                connection.commit()
            except Exception:
                connection.rollback()
                raise

    def fetch_all(self, query: str, parameters: Sequence[Any] | Mapping[str, Any] | None = None) -> list[dict[str, Any]]:
        with self.pool.connection() as connection, connection.cursor() as cursor:
            cursor.execute(query, parameters)
            return list(cursor.fetchall())

    def fetch_one(self, query: str, parameters: Sequence[Any] | Mapping[str, Any] | None = None) -> dict[str, Any] | None:
        with self.pool.connection() as connection, connection.cursor() as cursor:
            cursor.execute(query, parameters)
            row = cursor.fetchone()
            return dict(row) if row is not None else None

    def execute(self, query: str, parameters: Sequence[Any] | Mapping[str, Any] | None = None) -> int:
        with self.transaction() as connection, connection.cursor() as cursor:
            cursor.execute(query, parameters)
            return cursor.rowcount

    # ---- migrations ---------------------------------------------------------

    @staticmethod
    def migration_files() -> list[tuple[str, str]]:
        """Return migrations keyed by their full unique filename stem."""
        found: list[tuple[str, str]] = []
        for entry in files("backend.data.sql").iterdir():
            match = _MIGRATION_NAME.match(entry.name)
            if match:
                found.append((match.group(1), entry.read_text()))
        return sorted(found)

    def applied_versions(self) -> set[str]:
        with self.pool.connection() as connection, connection.cursor() as cursor:
            cursor.execute("CREATE TABLE IF NOT EXISTS schema_migrations (version text PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now())")
            connection.commit()
            cursor.execute("SELECT version FROM schema_migrations")
            return {str(row["version"]) for row in cursor.fetchall()}

    def pending_versions(self) -> list[str]:
        applied = self.applied_versions()
        return [version for version, _ in self.migration_files() if version not in applied]

    def migrate(self) -> list[str]:
        """Apply every migration not yet recorded; each file runs in one transaction."""
        applied = self.applied_versions()
        newly_applied: list[str] = []
        for version, sql in self.migration_files():
            if version in applied:
                continue
            with self.transaction() as connection, connection.cursor() as cursor:
                cursor.execute(sql)
                cursor.execute("INSERT INTO schema_migrations (version) VALUES (%s) ON CONFLICT DO NOTHING", (version,))
            newly_applied.append(version)
        return newly_applied
