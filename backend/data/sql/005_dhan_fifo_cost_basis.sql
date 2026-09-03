ALTER TABLE backtest_trades
    ADD COLUMN IF NOT EXISTS cost_basis_price double precision;

ALTER TABLE backtest_trades
    ADD COLUMN IF NOT EXISTS fifo_allocations jsonb NOT NULL DEFAULT '[]'::jsonb;

UPDATE backtest_trades
SET cost_basis_price = entry_price
WHERE cost_basis_price IS NULL;

ALTER TABLE backtest_trades
    ALTER COLUMN cost_basis_price SET NOT NULL;

ALTER TABLE paper_lots
    ADD COLUMN IF NOT EXISTS cost_basis_price double precision;

ALTER TABLE paper_lots
    ADD COLUMN IF NOT EXISTS fifo_allocations jsonb NOT NULL DEFAULT '[]'::jsonb;

UPDATE paper_lots
SET cost_basis_price = entry_price
WHERE cost_basis_price IS NULL;

ALTER TABLE paper_lots
    ALTER COLUMN cost_basis_price SET NOT NULL;
