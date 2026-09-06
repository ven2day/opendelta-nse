-- Phase 6A: durable Research Lab experiment groups over immutable backtest runs.
CREATE TABLE IF NOT EXISTS research_experiments (
    experiment_id uuid PRIMARY KEY,
    name text NOT NULL CHECK (char_length(name) BETWEEN 1 AND 120),
    market text NOT NULL CHECK (market IN ('NSE', 'CRYPTO')),
    strategy_id text NOT NULL,
    strategy_version text NOT NULL,
    strategy_source_id uuid NULL REFERENCES strategy_sources (source_id) ON DELETE RESTRICT,
    timeframe text NOT NULL,
    symbols jsonb NOT NULL,
    start_date date NOT NULL,
    end_date date NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS research_variants (
    variant_id uuid PRIMARY KEY,
    experiment_id uuid NOT NULL REFERENCES research_experiments (experiment_id) ON DELETE CASCADE,
    name text NOT NULL CHECK (char_length(name) BETWEEN 1 AND 80),
    configuration_snapshot jsonb NOT NULL,
    execution_settings jsonb NOT NULL,
    run_id uuid NOT NULL UNIQUE REFERENCES backtest_runs (run_id) ON DELETE RESTRICT,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (experiment_id, name)
);

CREATE INDEX IF NOT EXISTS research_experiments_recent
    ON research_experiments (market, created_at DESC);
CREATE INDEX IF NOT EXISTS research_variants_experiment
    ON research_variants (experiment_id, created_at);
