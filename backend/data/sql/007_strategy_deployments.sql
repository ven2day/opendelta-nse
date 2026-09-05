CREATE TABLE IF NOT EXISTS strategy_deployments (
    deployment_id uuid PRIMARY KEY,
    market text NOT NULL CHECK (market IN ('NSE', 'CRYPTO')),
    strategy_id text NOT NULL,
    strategy_version text NOT NULL,
    config_id uuid REFERENCES strategy_configs(config_id),
    timeframe text NOT NULL,
    mode text NOT NULL CHECK (mode IN ('OFF', 'SIGNALS', 'PAPER')),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT strategy_deployments_market_strategy UNIQUE (market, strategy_id)
);

CREATE INDEX IF NOT EXISTS strategy_deployments_market_mode
    ON strategy_deployments (market, mode);
