-- Preserve the final completed-candle close used to mark open backtest lots.
ALTER TABLE backtest_trades
    ADD COLUMN IF NOT EXISTS last_price double precision;
