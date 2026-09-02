# ADR 0003: Versioned SQLite operational metadata

Status: accepted

The application already uses filesystem/SQLite persistence on a bind-mounted VPS volume. V1 stores jobs, audit, feature cache, and migrations in one SQLite file there. WAL, idempotent migrations, indexes, bounded payloads, and restart recovery fit one backend process and avoid another database product.

Repository interfaces allow later migration if measured write concurrency exceeds this design.
