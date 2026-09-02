"""Operator CLI: ``python -m backend.data.migrate [--check]``.

Applies the platform schema migrations to ``MARKET_DATA_DATABASE_URL``. The
service itself never migrates implicitly (see PlatformRuntime.start), so this
is the deliberate, auditable step that changes a database.
"""

from __future__ import annotations

import argparse
import sys

from backend.data.database import DATABASE_URL_VARIABLE, Database


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Apply unified platform schema migrations")
    parser.add_argument("--check", action="store_true", help="only report pending migrations; exit 1 if any")
    parser.add_argument("--database-url", default=None, help=f"override {DATABASE_URL_VARIABLE}")
    arguments = parser.parse_args(argv)
    database = Database(arguments.database_url) if arguments.database_url else Database.from_environment()
    if database is None:
        print(f"{DATABASE_URL_VARIABLE} is not set", file=sys.stderr)
        return 2
    database.open()
    try:
        pending = database.pending_versions()
        if arguments.check:
            print("pending migrations: " + (", ".join(pending) if pending else "none"))
            return 1 if pending else 0
        applied = database.migrate()
        print("applied migrations: " + (", ".join(applied) if applied else "none (schema already current)"))
        return 0
    finally:
        database.close()


if __name__ == "__main__":
    raise SystemExit(main())
