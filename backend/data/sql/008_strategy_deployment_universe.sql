ALTER TABLE strategy_deployments
    ADD COLUMN IF NOT EXISTS universe_id uuid REFERENCES saved_universes(universe_id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS strategy_deployments_universe
    ON strategy_deployments (universe_id);
