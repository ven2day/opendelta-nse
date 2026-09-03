-- Different strategies commonly share semantic versions such as 1.0.0.
-- Strategy identity must therefore participate in live-signal deduplication.

ALTER TABLE live_signals
    DROP CONSTRAINT IF EXISTS live_signals_unique_candle;

ALTER TABLE live_signals
    ADD CONSTRAINT live_signals_unique_candle UNIQUE (
        market,
        strategy_id,
        strategy_version,
        symbol,
        timeframe,
        candle_timestamp,
        signal_type
    );

CREATE INDEX IF NOT EXISTS live_signals_strategy_recent
    ON live_signals (market, strategy_id, timeframe, candle_timestamp DESC);
