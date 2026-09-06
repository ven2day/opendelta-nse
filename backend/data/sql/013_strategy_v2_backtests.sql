-- Pin Strategy V2 backtests to the exact immutable source snapshot.

ALTER TABLE backtest_runs
    ADD COLUMN IF NOT EXISTS strategy_source_id uuid REFERENCES strategy_sources (source_id) ON DELETE RESTRICT;

CREATE INDEX IF NOT EXISTS backtest_runs_strategy_source
    ON backtest_runs (strategy_source_id, created_at DESC)
    WHERE strategy_source_id IS NOT NULL;
