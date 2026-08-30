CREATE EXTENSION IF NOT EXISTS timescaledb;

CREATE TABLE IF NOT EXISTS market_candles (
    market text NOT NULL,
    provider text NOT NULL,
    instrument_id text NOT NULL,
    symbol text NOT NULL,
    timeframe text NOT NULL,
    open_time timestamptz NOT NULL,
    close_time timestamptz NOT NULL,
    open double precision NOT NULL,
    high double precision NOT NULL,
    low double precision NOT NULL,
    close double precision NOT NULL,
    volume double precision NOT NULL,
    quote_volume double precision,
    complete boolean NOT NULL DEFAULT true,
    ingested_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT market_candles_identity UNIQUE (market, provider, instrument_id, timeframe, open_time),
    CONSTRAINT market_candles_time CHECK (close_time > open_time),
    CONSTRAINT market_candles_ohlc CHECK (
        volume >= 0 AND low <= open AND low <= close AND high >= open AND high >= close AND low <= high
    )
);

SELECT create_hypertable('market_candles', by_range('open_time'), if_not_exists => TRUE);

CREATE INDEX IF NOT EXISTS market_candles_read
    ON market_candles (market, provider, instrument_id, timeframe, open_time DESC);

CREATE TABLE IF NOT EXISTS market_sessions (
    market text NOT NULL,
    session_date date NOT NULL,
    is_trading_day boolean NOT NULL,
    open_time timestamptz,
    close_time timestamptz,
    calendar_version text NOT NULL,
    PRIMARY KEY (market, session_date)
);

CREATE TABLE IF NOT EXISTS market_data_health (
    market text NOT NULL,
    provider text NOT NULL,
    instrument_id text NOT NULL,
    timeframe text NOT NULL,
    status text NOT NULL,
    expected_last_candle timestamptz,
    actual_last_candle timestamptz,
    missing_candles integer NOT NULL DEFAULT 0,
    last_error text,
    checked_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (market, provider, instrument_id, timeframe),
    CONSTRAINT market_data_health_status CHECK (status IN (
        'HEALTHY','DELAYED','GAPS_DETECTED','REPAIRING','PROVIDER_UNAVAILABLE','INVALID_DATA','UNSUPPORTED'
    ))
);

CREATE TABLE IF NOT EXISTS market_data_repair_jobs (
    job_id uuid PRIMARY KEY,
    market text NOT NULL,
    provider text NOT NULL,
    instrument_id text NOT NULL,
    timeframe text NOT NULL,
    range_start timestamptz NOT NULL,
    range_end timestamptz NOT NULL,
    status text NOT NULL,
    attempts integer NOT NULL DEFAULT 0,
    missing_before integer NOT NULL DEFAULT 0,
    missing_after integer,
    last_error text,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT market_data_repair_range CHECK (range_end > range_start)
);
