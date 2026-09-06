-- Auditable TradingView delivery and immutable backtest promotion records.

CREATE TABLE IF NOT EXISTS tradingview_webhook_events (
    webhook_event_id uuid PRIMARY KEY,
    external_event_id text,
    market text CHECK (market IN ('NSE', 'CRYPTO')),
    strategy_id text,
    strategy_version text,
    symbol text,
    timeframe text,
    action text,
    accepted boolean NOT NULL,
    duplicate boolean NOT NULL DEFAULT false,
    mode text,
    signal_id uuid REFERENCES live_signals (signal_id) ON DELETE SET NULL,
    status_code integer NOT NULL,
    reason text,
    duration_ms double precision NOT NULL,
    received_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS tradingview_webhook_events_recent
    ON tradingview_webhook_events (market, received_at DESC);

CREATE TABLE IF NOT EXISTS strategy_approvals (
    approval_id uuid PRIMARY KEY,
    run_id uuid NOT NULL REFERENCES backtest_runs (run_id) ON DELETE RESTRICT,
    market text NOT NULL CHECK (market IN ('NSE', 'CRYPTO')),
    strategy_id text NOT NULL,
    strategy_version text NOT NULL,
    config_id uuid NOT NULL REFERENCES strategy_configs (config_id) ON DELETE RESTRICT,
    universe_id uuid NOT NULL REFERENCES saved_universes (universe_id) ON DELETE RESTRICT,
    timeframe text NOT NULL,
    mode text NOT NULL CHECK (mode IN ('SIGNALS', 'PAPER')),
    signal_source text NOT NULL CHECK (signal_source IN ('OPENDELTA', 'TRADINGVIEW')),
    configuration_snapshot jsonb NOT NULL,
    execution_settings jsonb NOT NULL,
    symbols jsonb NOT NULL,
    approved_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT strategy_approvals_run_mode UNIQUE (run_id, mode)
);

CREATE INDEX IF NOT EXISTS strategy_approvals_recent
    ON strategy_approvals (market, approved_at DESC);
