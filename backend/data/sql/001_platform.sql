-- Unified trading platform schema. Idempotent; applied by backend.data.database.Database.migrate().
-- Candles live in the TimescaleDB hypertable owned by backend/data/sql; everything here is plain PostgreSQL.

CREATE TABLE IF NOT EXISTS schema_migrations (
    version text PRIMARY KEY,
    applied_at timestamptz NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------- screener

CREATE TABLE IF NOT EXISTS screener_runs (
    run_id uuid PRIMARY KEY,
    market text NOT NULL CHECK (market IN ('NSE', 'CRYPTO')),
    status text NOT NULL CHECK (status IN ('RUNNING', 'COMPLETE', 'FAILED')),
    filters jsonb NOT NULL DEFAULT '{}'::jsonb,
    symbols_total integer NOT NULL DEFAULT 0,
    symbols_passed integer NOT NULL DEFAULT 0,
    error text,
    requested_at timestamptz NOT NULL DEFAULT now(),
    completed_at timestamptz
);

CREATE INDEX IF NOT EXISTS screener_runs_recent ON screener_runs (market, requested_at DESC);

CREATE TABLE IF NOT EXISTS screener_results (
    run_id uuid NOT NULL REFERENCES screener_runs (run_id) ON DELETE CASCADE,
    symbol text NOT NULL,
    passed boolean NOT NULL,
    rank integer,
    score double precision,
    rejection_reason text,
    metrics jsonb NOT NULL DEFAULT '{}'::jsonb,
    PRIMARY KEY (run_id, symbol)
);

CREATE TABLE IF NOT EXISTS saved_universes (
    universe_id uuid PRIMARY KEY,
    market text NOT NULL CHECK (market IN ('NSE', 'CRYPTO')),
    name text NOT NULL,
    source_run_id uuid REFERENCES screener_runs (run_id) ON DELETE SET NULL,
    symbols jsonb NOT NULL DEFAULT '[]'::jsonb,
    manual_includes jsonb NOT NULL DEFAULT '[]'::jsonb,
    manual_excludes jsonb NOT NULL DEFAULT '[]'::jsonb,
    active boolean NOT NULL DEFAULT false,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX IF NOT EXISTS saved_universes_one_active_per_market
    ON saved_universes (market) WHERE active;

-- ---------------------------------------------------------------- strategy configuration

CREATE TABLE IF NOT EXISTS strategy_configs (
    config_id uuid PRIMARY KEY,
    market text NOT NULL CHECK (market IN ('NSE', 'CRYPTO')),
    strategy_id text NOT NULL,
    strategy_version text NOT NULL,
    name text NOT NULL,
    configuration jsonb NOT NULL,
    risk_settings jsonb NOT NULL DEFAULT '{}'::jsonb,
    active boolean NOT NULL DEFAULT false,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT strategy_configs_name UNIQUE (market, strategy_id, name)
);

CREATE UNIQUE INDEX IF NOT EXISTS strategy_configs_one_active
    ON strategy_configs (market, strategy_id) WHERE active;

-- ---------------------------------------------------------------- backtests

CREATE TABLE IF NOT EXISTS backtest_runs (
    run_id uuid PRIMARY KEY,
    market text NOT NULL CHECK (market IN ('NSE', 'CRYPTO')),
    strategy_id text NOT NULL,
    strategy_version text NOT NULL,
    configuration_snapshot jsonb NOT NULL,
    execution_settings jsonb NOT NULL DEFAULT '{}'::jsonb,
    timeframe text NOT NULL,
    symbols jsonb NOT NULL,
    start_date date NOT NULL,
    end_date date NOT NULL,
    status text NOT NULL CHECK (status IN ('QUEUED', 'RUNNING', 'COMPLETE', 'FAILED', 'CANCELLED', 'INTERRUPTED')),
    cancel_requested boolean NOT NULL DEFAULT false,
    symbols_total integer NOT NULL DEFAULT 0,
    symbols_completed integer NOT NULL DEFAULT 0,
    current_symbol text,
    failed_symbols jsonb NOT NULL DEFAULT '[]'::jsonb,
    metrics jsonb,
    error text,
    created_at timestamptz NOT NULL DEFAULT now(),
    started_at timestamptz,
    completed_at timestamptz,
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS backtest_runs_recent ON backtest_runs (market, created_at DESC);
CREATE INDEX IF NOT EXISTS backtest_runs_active ON backtest_runs (status) WHERE status IN ('QUEUED', 'RUNNING');

CREATE TABLE IF NOT EXISTS backtest_trades (
    trade_id bigserial PRIMARY KEY,
    run_id uuid NOT NULL REFERENCES backtest_runs (run_id) ON DELETE CASCADE,
    market text NOT NULL,
    strategy_id text NOT NULL,
    strategy_version text NOT NULL,
    symbol text NOT NULL,
    timeframe text NOT NULL,
    lot_id text NOT NULL,
    cycle_id text NOT NULL,
    lot_number integer NOT NULL,
    signal_timestamp timestamptz NOT NULL,
    signal_price double precision NOT NULL,
    entry_timestamp timestamptz NOT NULL,
    entry_price double precision NOT NULL,
    quantity double precision NOT NULL,
    target_price double precision NOT NULL,
    stop_price double precision,
    expires_at timestamptz,
    exit_timestamp timestamptz,
    exit_price double precision,
    status text NOT NULL CHECK (status IN ('OPEN', 'TARGET_HIT', 'STOPPED', 'EXPIRED')),
    gross_pnl double precision NOT NULL DEFAULT 0,
    fees double precision NOT NULL DEFAULT 0,
    slippage double precision NOT NULL DEFAULT 0,
    net_pnl double precision NOT NULL DEFAULT 0,
    unrealized_pnl double precision NOT NULL DEFAULT 0,
    last_price double precision,
    mae_pct double precision,
    mfe_pct double precision,
    holding_bars integer NOT NULL DEFAULT 0,
    holding_minutes double precision,
    CONSTRAINT backtest_trades_lot UNIQUE (run_id, lot_id)
);

CREATE INDEX IF NOT EXISTS backtest_trades_run_symbol ON backtest_trades (run_id, symbol, entry_timestamp);

-- ---------------------------------------------------------------- live signals

CREATE TABLE IF NOT EXISTS live_signals (
    signal_id uuid PRIMARY KEY,
    market text NOT NULL CHECK (market IN ('NSE', 'CRYPTO')),
    strategy_id text NOT NULL,
    strategy_version text NOT NULL,
    symbol text NOT NULL,
    timeframe text NOT NULL,
    candle_timestamp timestamptz NOT NULL,
    signal_type text NOT NULL,
    status text NOT NULL CHECK (status IN ('STRONG_BUY', 'HOLDING', 'TARGET_HIT', 'EXITED', 'EXPIRED')),
    signal_price double precision NOT NULL,
    target_price double precision,
    stop_price double precision,
    expires_at timestamptz,
    reasons jsonb NOT NULL DEFAULT '[]'::jsonb,
    indicators jsonb NOT NULL DEFAULT '{}'::jsonb,
    configuration_snapshot jsonb NOT NULL,
    last_price double precision,
    exit_timestamp timestamptz,
    exit_price double precision,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT live_signals_unique_candle UNIQUE (market, strategy_version, symbol, timeframe, candle_timestamp, signal_type)
);

CREATE INDEX IF NOT EXISTS live_signals_recent ON live_signals (market, candle_timestamp DESC);
CREATE INDEX IF NOT EXISTS live_signals_open ON live_signals (market, symbol) WHERE status IN ('STRONG_BUY', 'HOLDING');

-- ---------------------------------------------------------------- paper trading

CREATE TABLE IF NOT EXISTS paper_accounts (
    account_id uuid PRIMARY KEY,
    market text NOT NULL UNIQUE CHECK (market IN ('NSE', 'CRYPTO')),
    currency text NOT NULL,
    starting_balance double precision NOT NULL,
    cash_balance double precision NOT NULL,
    risk_settings jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    reset_at timestamptz
);

CREATE TABLE IF NOT EXISTS paper_orders (
    order_id uuid PRIMARY KEY,
    account_id uuid NOT NULL REFERENCES paper_accounts (account_id) ON DELETE CASCADE,
    market text NOT NULL,
    signal_id uuid REFERENCES live_signals (signal_id) ON DELETE SET NULL,
    strategy_id text NOT NULL,
    strategy_version text NOT NULL,
    symbol text NOT NULL,
    side text NOT NULL CHECK (side IN ('BUY', 'SELL')),
    quantity double precision NOT NULL,
    requested_price double precision NOT NULL,
    executed_price double precision,
    fees double precision NOT NULL DEFAULT 0,
    slippage double precision NOT NULL DEFAULT 0,
    status text NOT NULL CHECK (status IN ('FILLED', 'REJECTED')),
    reason text,
    created_at timestamptz NOT NULL DEFAULT now()
);

-- The same signal can open at most one paper order per account.
CREATE UNIQUE INDEX IF NOT EXISTS paper_orders_one_per_signal
    ON paper_orders (account_id, signal_id) WHERE signal_id IS NOT NULL AND side = 'BUY' AND status = 'FILLED';

CREATE TABLE IF NOT EXISTS paper_lots (
    lot_id uuid PRIMARY KEY,
    account_id uuid NOT NULL REFERENCES paper_accounts (account_id) ON DELETE CASCADE,
    order_id uuid NOT NULL REFERENCES paper_orders (order_id) ON DELETE CASCADE,
    signal_id uuid REFERENCES live_signals (signal_id) ON DELETE SET NULL,
    market text NOT NULL,
    strategy_id text NOT NULL,
    strategy_version text NOT NULL,
    symbol text NOT NULL,
    timeframe text NOT NULL,
    cycle_id text NOT NULL,
    lot_number integer NOT NULL,
    entry_timestamp timestamptz NOT NULL,
    entry_price double precision NOT NULL,
    quantity double precision NOT NULL,
    target_price double precision NOT NULL,
    stop_price double precision,
    expires_at timestamptz,
    status text NOT NULL CHECK (status IN ('OPEN', 'TARGET_HIT', 'STOPPED', 'EXPIRED', 'CLOSED')),
    exit_timestamp timestamptz,
    exit_price double precision,
    realized_pnl double precision,
    unrealized_pnl double precision NOT NULL DEFAULT 0,
    fees double precision NOT NULL DEFAULT 0,
    last_price double precision,
    mae_pct double precision,
    mfe_pct double precision,
    configuration_snapshot jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS paper_lots_open ON paper_lots (account_id, symbol) WHERE status = 'OPEN';

CREATE TABLE IF NOT EXISTS paper_trades (
    trade_id bigserial PRIMARY KEY,
    account_id uuid NOT NULL REFERENCES paper_accounts (account_id) ON DELETE CASCADE,
    lot_id uuid NOT NULL REFERENCES paper_lots (lot_id) ON DELETE CASCADE,
    market text NOT NULL,
    symbol text NOT NULL,
    side text NOT NULL CHECK (side IN ('BUY', 'SELL')),
    quantity double precision NOT NULL,
    price double precision NOT NULL,
    fees double precision NOT NULL DEFAULT 0,
    slippage double precision NOT NULL DEFAULT 0,
    reason text NOT NULL,
    executed_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS paper_trades_recent ON paper_trades (account_id, executed_at DESC);

-- ---------------------------------------------------------------- engine health

CREATE TABLE IF NOT EXISTS engine_status (
    engine text NOT NULL,
    market text NOT NULL,
    status text NOT NULL,
    connection_status text,
    data_age_seconds double precision,
    last_completed_candle timestamptz,
    message text,
    details jsonb NOT NULL DEFAULT '{}'::jsonb,
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (engine, market)
);
