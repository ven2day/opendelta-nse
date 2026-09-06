-- Make the live signal origin explicit and allow TradingView alerts to be
-- replay-safe. Existing deployments and signals remain OpenDelta-managed.

ALTER TABLE strategy_deployments
    ADD COLUMN IF NOT EXISTS signal_source text NOT NULL DEFAULT 'OPENDELTA';

ALTER TABLE strategy_deployments
    DROP CONSTRAINT IF EXISTS strategy_deployments_signal_source_check;

ALTER TABLE strategy_deployments
    ADD CONSTRAINT strategy_deployments_signal_source_check
    CHECK (signal_source IN ('OPENDELTA', 'TRADINGVIEW'));

ALTER TABLE live_signals
    ADD COLUMN IF NOT EXISTS source text NOT NULL DEFAULT 'OPENDELTA',
    ADD COLUMN IF NOT EXISTS external_event_id text,
    ADD COLUMN IF NOT EXISTS received_at timestamptz;

ALTER TABLE live_signals
    DROP CONSTRAINT IF EXISTS live_signals_source_check;

ALTER TABLE live_signals
    ADD CONSTRAINT live_signals_source_check
    CHECK (source IN ('OPENDELTA', 'TRADINGVIEW'));

CREATE UNIQUE INDEX IF NOT EXISTS live_signals_external_event
    ON live_signals (source, external_event_id)
    WHERE external_event_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS live_signals_source_recent
    ON live_signals (market, source, received_at DESC);
