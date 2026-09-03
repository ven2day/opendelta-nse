CREATE INDEX IF NOT EXISTS market_candles_symbol_read
    ON market_candles (market, symbol, timeframe, provider, open_time DESC)
    WHERE complete;
