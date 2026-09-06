-- Immutable, validated Indicator Studio V2 source snapshots.

CREATE TABLE IF NOT EXISTS indicator_sources (
    source_id uuid PRIMARY KEY,
    indicator_id text NOT NULL,
    indicator_version text NOT NULL,
    name text NOT NULL,
    description text NOT NULL DEFAULT '',
    source_code text NOT NULL,
    code_hash text NOT NULL,
    manifest jsonb NOT NULL,
    validation jsonb NOT NULL,
    status text NOT NULL DEFAULT 'VALIDATED' CHECK (status IN ('VALIDATED', 'ARCHIVED')),
    created_at timestamptz NOT NULL DEFAULT now(),
    archived_at timestamptz,
    CONSTRAINT indicator_sources_version UNIQUE (indicator_id, indicator_version),
    CONSTRAINT indicator_sources_hash UNIQUE (indicator_id, code_hash)
);

CREATE INDEX IF NOT EXISTS indicator_sources_recent
    ON indicator_sources (indicator_id, created_at DESC);
