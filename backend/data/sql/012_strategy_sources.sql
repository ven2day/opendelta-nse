-- Immutable, validated source snapshots for Strategy Studio V2.

CREATE TABLE IF NOT EXISTS strategy_sources (
    source_id uuid PRIMARY KEY,
    strategy_id text NOT NULL,
    strategy_version text NOT NULL,
    name text NOT NULL,
    description text NOT NULL DEFAULT '',
    source_code text NOT NULL,
    code_hash text NOT NULL,
    manifest jsonb NOT NULL,
    validation jsonb NOT NULL,
    status text NOT NULL DEFAULT 'VALIDATED' CHECK (status IN ('VALIDATED', 'ARCHIVED')),
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT strategy_sources_version UNIQUE (strategy_id, strategy_version),
    CONSTRAINT strategy_sources_hash UNIQUE (strategy_id, code_hash)
);

CREATE INDEX IF NOT EXISTS strategy_sources_recent
    ON strategy_sources (strategy_id, created_at DESC);
