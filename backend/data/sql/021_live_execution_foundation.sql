-- Fail-closed live execution foundation. This migration creates no active deployment
-- and grants no broker/exchange authority by itself.

CREATE TABLE IF NOT EXISTS live_risk_policies (
    risk_policy_id uuid PRIMARY KEY,
    name varchar(120) NOT NULL CHECK (btrim(name) <> ''),
    max_order_value numeric(24, 8) NOT NULL CHECK (max_order_value > 0),
    max_position_value numeric(24, 8) NOT NULL CHECK (max_position_value > 0),
    max_total_exposure numeric(24, 8) NOT NULL CHECK (max_total_exposure > 0),
    max_open_positions integer NOT NULL CHECK (max_open_positions BETWEEN 1 AND 1000),
    max_daily_trades integer NOT NULL CHECK (max_daily_trades BETWEEN 1 AND 10000),
    max_daily_loss numeric(24, 8) NOT NULL CHECK (max_daily_loss > 0),
    max_price_deviation_pct numeric(10, 6) NOT NULL CHECK (max_price_deviation_pct BETWEEN 0 AND 100),
    max_signal_age_seconds integer NOT NULL CHECK (max_signal_age_seconds BETWEEN 1 AND 86400),
    max_candle_age_seconds integer NOT NULL CHECK (max_candle_age_seconds BETWEEN 1 AND 86400),
    symbol_allowlist jsonb NOT NULL CHECK (jsonb_typeof(symbol_allowlist) = 'array'),
    market_allowlist jsonb NOT NULL CHECK (jsonb_typeof(market_allowlist) = 'array'),
    strategy_allowlist jsonb NOT NULL CHECK (jsonb_typeof(strategy_allowlist) = 'array'),
    timeframe_allowlist jsonb NOT NULL CHECK (jsonb_typeof(timeframe_allowlist) = 'array'),
    created_by varchar(120) NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS live_deployments (
    live_deployment_id uuid PRIMARY KEY,
    provider varchar(12) NOT NULL CHECK (provider IN ('DHAN', 'OKX', 'VALR')),
    connection_id uuid REFERENCES exchange_connections(connection_id) ON DELETE RESTRICT,
    approval_id uuid NOT NULL REFERENCES strategy_approvals(approval_id) ON DELETE RESTRICT,
    risk_policy_id uuid NOT NULL REFERENCES live_risk_policies(risk_policy_id) ON DELETE RESTRICT,
    market varchar(12) NOT NULL CHECK (market IN ('NSE', 'CRYPTO')),
    strategy_id text NOT NULL,
    strategy_version text NOT NULL,
    strategy_source_id uuid REFERENCES strategy_sources(source_id) ON DELETE RESTRICT,
    config_id uuid NOT NULL REFERENCES strategy_configs(config_id) ON DELETE RESTRICT,
    universe_id uuid NOT NULL REFERENCES saved_universes(universe_id) ON DELETE RESTRICT,
    timeframe text NOT NULL,
    configuration_snapshot jsonb NOT NULL CHECK (jsonb_typeof(configuration_snapshot) = 'object'),
    execution_settings jsonb NOT NULL CHECK (jsonb_typeof(execution_settings) = 'object'),
    status varchar(20) NOT NULL DEFAULT 'DRAFT' CHECK (status IN ('DRAFT', 'ACTIVE', 'DISABLED')),
    activated_by varchar(120),
    activated_at timestamptz,
    disabled_by varchar(120),
    disabled_at timestamptz,
    created_by varchar(120) NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CHECK ((provider = 'DHAN' AND market = 'NSE' AND connection_id IS NULL)
        OR (provider IN ('OKX', 'VALR') AND market = 'CRYPTO' AND connection_id IS NOT NULL)),
    CHECK ((status = 'ACTIVE' AND activated_at IS NOT NULL AND activated_by IS NOT NULL)
        OR status <> 'ACTIVE')
);

CREATE UNIQUE INDEX IF NOT EXISTS live_deployments_active_strategy_idx
    ON live_deployments (market, strategy_id) WHERE status = 'ACTIVE';
CREATE INDEX IF NOT EXISTS live_deployments_provider_status_idx
    ON live_deployments (provider, status, updated_at DESC);

CREATE TABLE IF NOT EXISTS emergency_stops (
    emergency_stop_id uuid PRIMARY KEY,
    scope_type varchar(16) NOT NULL CHECK (scope_type IN ('GLOBAL', 'PROVIDER', 'MARKET', 'STRATEGY')),
    scope_key varchar(160) NOT NULL CHECK (btrim(scope_key) <> ''),
    active boolean NOT NULL DEFAULT true,
    reason varchar(500) NOT NULL CHECK (btrim(reason) <> ''),
    changed_by varchar(120) NOT NULL,
    activated_at timestamptz NOT NULL DEFAULT now(),
    cleared_at timestamptz,
    updated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT emergency_stops_scope UNIQUE (scope_type, scope_key),
    CHECK ((active AND cleared_at IS NULL) OR (NOT active AND cleared_at IS NOT NULL))
);

CREATE TABLE IF NOT EXISTS emergency_stop_events (
    event_id bigserial PRIMARY KEY,
    emergency_stop_id uuid NOT NULL REFERENCES emergency_stops(emergency_stop_id) ON DELETE RESTRICT,
    scope_type varchar(16) NOT NULL,
    scope_key varchar(160) NOT NULL,
    active boolean NOT NULL,
    actor varchar(120) NOT NULL,
    reason varchar(500) NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS live_order_intents (
    intent_id uuid PRIMARY KEY,
    idempotency_key char(64) NOT NULL UNIQUE CHECK (idempotency_key ~ '^[0-9a-f]{64}$'),
    live_deployment_id uuid NOT NULL REFERENCES live_deployments(live_deployment_id) ON DELETE RESTRICT,
    connection_id uuid REFERENCES exchange_connections(connection_id) ON DELETE RESTRICT,
    signal_id uuid NOT NULL REFERENCES live_signals(signal_id) ON DELETE RESTRICT,
    provider varchar(12) NOT NULL CHECK (provider IN ('DHAN', 'OKX', 'VALR')),
    market varchar(12) NOT NULL CHECK (market IN ('NSE', 'CRYPTO')),
    strategy_id text NOT NULL,
    strategy_version text NOT NULL,
    strategy_source_id uuid REFERENCES strategy_sources(source_id) ON DELETE RESTRICT,
    config_id uuid NOT NULL REFERENCES strategy_configs(config_id) ON DELETE RESTRICT,
    universe_id uuid NOT NULL REFERENCES saved_universes(universe_id) ON DELETE RESTRICT,
    timeframe text NOT NULL,
    symbol text NOT NULL,
    client_order_id varchar(64) NOT NULL UNIQUE,
    provider_order_id varchar(160),
    requested_order jsonb NOT NULL CHECK (jsonb_typeof(requested_order) = 'object'),
    configuration_snapshot jsonb NOT NULL CHECK (jsonb_typeof(configuration_snapshot) = 'object'),
    execution_settings jsonb NOT NULL CHECK (jsonb_typeof(execution_settings) = 'object'),
    provider_response jsonb CHECK (provider_response IS NULL OR jsonb_typeof(provider_response) = 'object'),
    provider_metadata jsonb NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(provider_metadata) = 'object'),
    state varchar(32) NOT NULL CHECK (state IN (
        'CREATED', 'BLOCKED', 'SUBMITTED', 'ACKNOWLEDGED', 'PARTIALLY_FILLED', 'FILLED',
        'CANCEL_REQUESTED', 'CANCELLED', 'REJECTED', 'UNKNOWN'
    )),
    reconciliation_status varchar(32) NOT NULL DEFAULT 'NOT_REQUIRED' CHECK (reconciliation_status IN (
        'NOT_REQUIRED', 'PENDING', 'MATCHED', 'MISMATCH', 'REQUIRED'
    )),
    blocked_reasons jsonb NOT NULL DEFAULT '[]'::jsonb CHECK (jsonb_typeof(blocked_reasons) = 'array'),
    last_error varchar(500),
    retry_count integer NOT NULL DEFAULT 0 CHECK (retry_count >= 0),
    submitted_at timestamptz,
    terminal_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT live_order_intents_deployment_signal UNIQUE (live_deployment_id, signal_id)
);

CREATE INDEX IF NOT EXISTS live_order_intents_reconcile_idx
    ON live_order_intents (reconciliation_status, updated_at)
    WHERE reconciliation_status IN ('PENDING', 'MISMATCH', 'REQUIRED');
CREATE INDEX IF NOT EXISTS live_order_intents_deployment_state_idx
    ON live_order_intents (live_deployment_id, state, created_at DESC);

CREATE TABLE IF NOT EXISTS live_order_state_events (
    event_id bigserial PRIMARY KEY,
    intent_id uuid NOT NULL REFERENCES live_order_intents(intent_id) ON DELETE RESTRICT,
    from_state varchar(32),
    to_state varchar(32) NOT NULL,
    actor varchar(120) NOT NULL,
    reason varchar(500),
    provider_payload jsonb CHECK (provider_payload IS NULL OR jsonb_typeof(provider_payload) = 'object'),
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS live_order_state_events_intent_idx
    ON live_order_state_events (intent_id, created_at);

CREATE TABLE IF NOT EXISTS live_order_fills (
    fill_id uuid PRIMARY KEY,
    intent_id uuid NOT NULL REFERENCES live_order_intents(intent_id) ON DELETE RESTRICT,
    provider_fill_id varchar(160) NOT NULL,
    quantity numeric(24, 10) NOT NULL CHECK (quantity > 0),
    price numeric(24, 10) NOT NULL CHECK (price > 0),
    fee numeric(24, 10) NOT NULL DEFAULT 0 CHECK (fee >= 0),
    fee_currency varchar(24),
    filled_at timestamptz NOT NULL,
    provider_metadata jsonb NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(provider_metadata) = 'object'),
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT live_order_fills_provider UNIQUE (intent_id, provider_fill_id)
);

CREATE TABLE IF NOT EXISTS live_reconciliation_findings (
    finding_id uuid PRIMARY KEY,
    intent_id uuid REFERENCES live_order_intents(intent_id) ON DELETE RESTRICT,
    provider varchar(12) NOT NULL CHECK (provider IN ('DHAN', 'OKX', 'VALR')),
    finding_type varchar(40) NOT NULL CHECK (finding_type IN (
        'MISSING_ACKNOWLEDGEMENT', 'UNKNOWN_ORDER', 'DUPLICATE_PROVIDER_ORDER', 'PARTIAL_FILL',
        'CANCEL_FAILURE', 'POSITION_MISMATCH', 'BALANCE_MISMATCH', 'STALE_CONNECTION', 'PROVIDER_OUTAGE'
    )),
    status varchar(16) NOT NULL DEFAULT 'OPEN' CHECK (status IN ('OPEN', 'ACKNOWLEDGED', 'RESOLVED')),
    details jsonb NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(details) = 'object'),
    detected_at timestamptz NOT NULL DEFAULT now(),
    resolved_at timestamptz
);

CREATE INDEX IF NOT EXISTS live_reconciliation_findings_open_idx
    ON live_reconciliation_findings (provider, detected_at DESC) WHERE status <> 'RESOLVED';
