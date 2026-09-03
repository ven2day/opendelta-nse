CREATE TABLE IF NOT EXISTS paper_pending_entries (
    pending_entry_id uuid PRIMARY KEY,
    account_id uuid NOT NULL REFERENCES paper_accounts (account_id) ON DELETE CASCADE,
    signal_id uuid REFERENCES live_signals (signal_id) ON DELETE SET NULL,
    market text NOT NULL,
    strategy_id text NOT NULL,
    strategy_version text NOT NULL,
    symbol text NOT NULL,
    timeframe text NOT NULL,
    trigger_timestamp timestamptz NOT NULL,
    signal_price double precision NOT NULL,
    target_price double precision,
    stop_price double precision,
    configuration_snapshot jsonb NOT NULL,
    entry_reason text NOT NULL,
    cycle_id text,
    lot_number integer,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX IF NOT EXISTS paper_pending_entries_one_per_signal
    ON paper_pending_entries (account_id, signal_id)
    WHERE signal_id IS NOT NULL;

CREATE UNIQUE INDEX IF NOT EXISTS paper_pending_entries_one_per_ladder_lot
    ON paper_pending_entries (account_id, cycle_id, lot_number)
    WHERE cycle_id IS NOT NULL AND lot_number IS NOT NULL;

