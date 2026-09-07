-- Encrypted OKX/VALR credentials and secret-free connection audit events.

CREATE TABLE IF NOT EXISTS exchange_connections (
    connection_id uuid PRIMARY KEY,
    provider varchar(12) NOT NULL CHECK (provider IN ('OKX', 'VALR')),
    label varchar(120) NOT NULL CHECK (btrim(label) <> ''),
    account_environment varchar(12) NOT NULL CHECK (account_environment IN ('LIVE', 'DEMO')),
    masked_key_identifier varchar(24) NOT NULL CHECK (btrim(masked_key_identifier) <> ''),
    credentials_ciphertext bytea NOT NULL CHECK (octet_length(credentials_ciphertext) >= 17),
    credentials_nonce bytea NOT NULL CHECK (octet_length(credentials_nonce) = 12),
    encrypted_data_key bytea NOT NULL CHECK (octet_length(encrypted_data_key) = 48),
    data_key_nonce bytea NOT NULL CHECK (octet_length(data_key_nonce) = 12),
    master_key_version varchar(32) NOT NULL CHECK (master_key_version ~ '^[A-Za-z0-9._-]{1,32}$'),
    disabled boolean NOT NULL DEFAULT true,
    status varchar(32) NOT NULL DEFAULT 'NOT_TESTED' CHECK (status IN (
        'NOT_TESTED', 'CONNECTED', 'FAILED', 'WITHDRAWAL_PERMISSION', 'DISABLED'
    )),
    permissions jsonb NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(permissions) = 'object'),
    last_test_success boolean,
    last_test_message varchar(240),
    last_tested_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CHECK (provider = 'OKX' OR account_environment = 'LIVE'),
    CHECK (last_tested_at IS NOT NULL OR (last_test_success IS NULL AND last_test_message IS NULL))
);

CREATE INDEX IF NOT EXISTS exchange_connections_provider_status_idx
    ON exchange_connections (provider, status, updated_at DESC);

CREATE TABLE IF NOT EXISTS exchange_connection_events (
    event_id bigserial PRIMARY KEY,
    connection_id uuid NOT NULL,
    provider varchar(12) NOT NULL CHECK (provider IN ('OKX', 'VALR')),
    actor varchar(120) NOT NULL,
    action varchar(32) NOT NULL CHECK (action IN (
        'ADDED', 'REPLACED', 'DELETED', 'TEST_SUCCEEDED', 'TEST_FAILED',
        'WITHDRAWAL_PERMISSION', 'DISABLED', 'ENABLED', 'ROTATED'
    )),
    details jsonb NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(details) = 'object'),
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS exchange_connection_events_connection_idx
    ON exchange_connection_events (connection_id, created_at DESC);
