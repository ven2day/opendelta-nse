-- Phase 7: parameter-sweep provenance and deterministic variant ordering.
ALTER TABLE research_experiments
    ADD COLUMN generation_mode text NOT NULL DEFAULT 'MANUAL',
    ADD COLUMN sweep_definitions jsonb NOT NULL DEFAULT '[]'::jsonb,
    ADD COLUMN preview_hash text NOT NULL DEFAULT 'legacy',
    ADD COLUMN variant_count integer NOT NULL DEFAULT 1,
    ADD COLUMN symbol_count integer NOT NULL DEFAULT 1,
    ADD COLUMN estimated_symbol_runs integer NOT NULL DEFAULT 1,
    ADD COLUMN idempotency_key text NULL,
    ADD COLUMN universe_id uuid NULL REFERENCES saved_universes (universe_id) ON DELETE RESTRICT,
    ADD COLUMN universe_name text NULL;

ALTER TABLE research_variants
    ADD COLUMN position integer;

WITH ranked AS (
    SELECT variant_id,
           row_number() OVER (PARTITION BY experiment_id ORDER BY created_at, variant_id) AS position
    FROM research_variants
)
UPDATE research_variants AS variant
SET position = ranked.position
FROM ranked
WHERE ranked.variant_id = variant.variant_id;

UPDATE research_experiments AS experiment
SET variant_count = counts.variant_count,
    symbol_count = jsonb_array_length(experiment.symbols),
    estimated_symbol_runs = counts.variant_count * jsonb_array_length(experiment.symbols)
FROM (
    SELECT experiment_id, count(*)::integer AS variant_count
    FROM research_variants
    GROUP BY experiment_id
) AS counts
WHERE counts.experiment_id = experiment.experiment_id;

ALTER TABLE research_variants
    ALTER COLUMN position SET NOT NULL,
    ADD CONSTRAINT research_variants_position_positive CHECK (position BETWEEN 1 AND 100),
    ADD CONSTRAINT research_variants_experiment_position UNIQUE (experiment_id, position);

ALTER TABLE research_experiments
    ADD CONSTRAINT research_experiments_generation_mode CHECK (generation_mode IN ('MANUAL', 'GRID')),
    ADD CONSTRAINT research_experiments_sweep_array CHECK (jsonb_typeof(sweep_definitions) = 'array'),
    ADD CONSTRAINT research_experiments_sweep_count CHECK (
        generation_mode = 'MANUAL'
        OR jsonb_array_length(sweep_definitions) BETWEEN 1 AND 8
    ),
    ADD CONSTRAINT research_experiments_preview_hash CHECK (
        preview_hash = 'legacy' OR preview_hash ~ '^sha256:[0-9a-f]{64}$'
    ),
    ADD CONSTRAINT research_experiments_idempotency_key CHECK (
        idempotency_key IS NULL OR char_length(idempotency_key) BETWEEN 8 AND 128
    ),
    ADD CONSTRAINT research_experiments_symbols_array CHECK (
        jsonb_typeof(symbols) = 'array' AND jsonb_array_length(symbols) = symbol_count
    ),
    ADD CONSTRAINT research_experiments_date_range CHECK (end_date >= start_date),
    ADD CONSTRAINT research_experiments_variant_count CHECK (variant_count BETWEEN 1 AND 100),
    ADD CONSTRAINT research_experiments_symbol_count CHECK (symbol_count BETWEEN 1 AND 2000),
    ADD CONSTRAINT research_experiments_workload CHECK (
        estimated_symbol_runs = variant_count * symbol_count
        AND estimated_symbol_runs BETWEEN 1 AND 20000
    );

CREATE UNIQUE INDEX research_experiments_idempotency
    ON research_experiments (idempotency_key)
    WHERE idempotency_key IS NOT NULL;
CREATE INDEX research_experiments_preview_hash
    ON research_experiments (preview_hash);
