-- Pin an immutable Strategy V2 source through approval and live deployment.

ALTER TABLE strategy_deployments
    ADD COLUMN IF NOT EXISTS strategy_source_id uuid REFERENCES strategy_sources (source_id) ON DELETE RESTRICT;

ALTER TABLE strategy_approvals
    ADD COLUMN IF NOT EXISTS strategy_source_id uuid REFERENCES strategy_sources (source_id) ON DELETE RESTRICT;

CREATE INDEX IF NOT EXISTS strategy_deployments_source
    ON strategy_deployments (strategy_source_id)
    WHERE strategy_source_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS strategy_approvals_source
    ON strategy_approvals (strategy_source_id, approved_at DESC)
    WHERE strategy_source_id IS NOT NULL;
