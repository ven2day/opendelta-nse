-- Phase 8: durable, immutable walk-forward validation provenance and child-run graph.
CREATE TABLE walk_forward_validations (
    validation_id uuid PRIMARY KEY,
    name text NOT NULL CHECK (char_length(name) BETWEEN 1 AND 120),
    mode text NOT NULL CHECK (mode IN ('ANCHORED', 'ROLLING')),
    market text NOT NULL CHECK (market IN ('NSE', 'CRYPTO')),
    strategy_id text NOT NULL,
    strategy_version text NOT NULL,
    strategy_source_id uuid NULL REFERENCES strategy_sources (source_id) ON DELETE RESTRICT,
    timeframe text NOT NULL,
    symbols jsonb NOT NULL CHECK (jsonb_typeof(symbols) = 'array' AND jsonb_array_length(symbols) BETWEEN 1 AND 2000),
    universe_id uuid NULL REFERENCES saved_universes (universe_id) ON DELETE RESTRICT,
    universe_name text NOT NULL,
    overall_start_date date NOT NULL,
    overall_end_date date NOT NULL,
    training_window integer NOT NULL CHECK (training_window > 0),
    testing_window integer NOT NULL CHECK (testing_window > 0),
    step_length integer NOT NULL CHECK (step_length > 0),
    maximum_folds integer NOT NULL CHECK (maximum_folds BETWEEN 1 AND 12),
    candidate_experiment_id uuid NOT NULL REFERENCES research_experiments (experiment_id) ON DELETE RESTRICT,
    ranking_objective text NOT NULL CHECK (ranking_objective IN ('NET_PNL', 'RETURN_DRAWDOWN', 'LOWEST_DRAWDOWN', 'HIGHEST_WIN_RATE')),
    minimum_required_trades integer NOT NULL CHECK (minimum_required_trades >= 0),
    transaction_cost_bps double precision NOT NULL CHECK (
        transaction_cost_bps BETWEEN 0 AND 10000 AND transaction_cost_bps <> 'NaN'::double precision
    ),
    slippage_bps double precision NOT NULL CHECK (
        slippage_bps BETWEEN 0 AND 10000 AND slippage_bps <> 'NaN'::double precision
    ),
    preview_hash text NOT NULL CHECK (preview_hash ~ '^sha256:[0-9a-f]{64}$'),
    idempotency_key text NOT NULL UNIQUE CHECK (char_length(idempotency_key) BETWEEN 8 AND 128),
    fold_count integer NOT NULL CHECK (fold_count BETWEEN 1 AND 12),
    candidate_count integer NOT NULL CHECK (candidate_count BETWEEN 1 AND 20),
    child_run_count integer NOT NULL CHECK (child_run_count BETWEEN 2 AND 120),
    symbol_count integer NOT NULL CHECK (symbol_count BETWEEN 1 AND 2000),
    estimated_symbol_runs integer NOT NULL CHECK (estimated_symbol_runs BETWEEN 1 AND 20000),
    estimated_candle_workload bigint NOT NULL CHECK (estimated_candle_workload BETWEEN 1 AND 25000000),
    cancel_requested boolean NOT NULL DEFAULT false,
    aggregate_unseen_metrics jsonb NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    completed_at timestamptz NULL,
    CONSTRAINT walk_forward_validations_date_range CHECK (overall_end_date >= overall_start_date),
    CONSTRAINT walk_forward_validations_workload CHECK (estimated_symbol_runs = child_run_count * symbol_count)
);

CREATE TABLE walk_forward_folds (
    fold_id uuid PRIMARY KEY,
    validation_id uuid NOT NULL REFERENCES walk_forward_validations (validation_id) ON DELETE CASCADE,
    position integer NOT NULL CHECK (position BETWEEN 1 AND 12),
    training_start_date date NOT NULL,
    training_end_date date NOT NULL,
    testing_start_date date NOT NULL,
    testing_end_date date NOT NULL,
    training_sessions integer NOT NULL CHECK (training_sessions > 0),
    testing_sessions integer NOT NULL CHECK (testing_sessions > 0),
    selected_variant_id uuid NULL REFERENCES research_variants (variant_id) ON DELETE RESTRICT,
    selected_candidate_name text NULL,
    selected_configuration jsonb NULL,
    selected_execution_settings jsonb NULL,
    training_rank integer NULL CHECK (training_rank IS NULL OR training_rank > 0),
    test_run_id uuid NULL UNIQUE REFERENCES backtest_runs (run_id) ON DELETE RESTRICT,
    status text NOT NULL DEFAULT 'QUEUED' CHECK (status IN ('QUEUED', 'TRAINING', 'TESTING', 'COMPLETE', 'FAILED', 'CANCELLED')),
    error text NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    completed_at timestamptz NULL,
    UNIQUE (validation_id, position),
    CHECK (training_end_date >= training_start_date),
    CHECK (testing_start_date > training_end_date),
    CHECK (testing_end_date >= testing_start_date)
);

CREATE TABLE walk_forward_training_runs (
    fold_id uuid NOT NULL REFERENCES walk_forward_folds (fold_id) ON DELETE CASCADE,
    candidate_variant_id uuid NOT NULL REFERENCES research_variants (variant_id) ON DELETE RESTRICT,
    candidate_name text NOT NULL,
    position integer NOT NULL CHECK (position BETWEEN 1 AND 20),
    run_id uuid NOT NULL UNIQUE REFERENCES backtest_runs (run_id) ON DELETE RESTRICT,
    PRIMARY KEY (fold_id, candidate_variant_id),
    UNIQUE (fold_id, position)
);

CREATE INDEX walk_forward_validations_recent ON walk_forward_validations (market, created_at DESC);
CREATE INDEX walk_forward_validations_preview ON walk_forward_validations (preview_hash);
CREATE INDEX walk_forward_folds_validation ON walk_forward_folds (validation_id, position);
CREATE INDEX walk_forward_training_fold ON walk_forward_training_runs (fold_id, position);
