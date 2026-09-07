-- Phase 14: hashed, scoped agent access and idempotent MCP requests.

CREATE TABLE agent_access_tokens (
    token_id uuid PRIMARY KEY,
    name varchar(120) NOT NULL CHECK (btrim(name) <> ''),
    token_prefix varchar(16) NOT NULL,
    token_hash char(64) NOT NULL UNIQUE CHECK (token_hash ~ '^[0-9a-f]{64}$'),
    scopes jsonb NOT NULL CHECK (
        jsonb_typeof(scopes) = 'array'
        AND jsonb_array_length(scopes) BETWEEN 1 AND 6
        AND scopes <@ '["research:read","backtests:submit","experiments:submit","walk-forward:submit","ai:use","monitoring:read"]'::jsonb
    ),
    rate_limit_per_minute integer NOT NULL DEFAULT 60 CHECK (rate_limit_per_minute BETWEEN 1 AND 300),
    expires_at timestamptz NOT NULL,
    revoked_at timestamptz,
    last_used_at timestamptz,
    created_by varchar(200) NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    CHECK (expires_at > created_at),
    CHECK (revoked_at IS NULL OR revoked_at >= created_at)
);

CREATE INDEX agent_access_tokens_active_idx
    ON agent_access_tokens (expires_at, last_used_at DESC)
    WHERE revoked_at IS NULL;

CREATE TABLE agent_rate_limit_windows (
    token_id uuid NOT NULL REFERENCES agent_access_tokens(token_id) ON DELETE CASCADE,
    window_started_at timestamptz NOT NULL,
    request_count integer NOT NULL CHECK (request_count BETWEEN 1 AND 300),
    PRIMARY KEY (token_id, window_started_at)
);

CREATE INDEX agent_rate_limit_windows_started_idx
    ON agent_rate_limit_windows (window_started_at);

CREATE TABLE agent_tool_requests (
    request_id uuid PRIMARY KEY,
    token_id uuid NOT NULL REFERENCES agent_access_tokens(token_id),
    tool_name varchar(100) NOT NULL,
    idempotency_key varchar(128) NOT NULL,
    request_hash char(64) NOT NULL CHECK (request_hash ~ '^[0-9a-f]{64}$'),
    status varchar(12) NOT NULL DEFAULT 'PENDING' CHECK (status IN ('PENDING', 'COMPLETE', 'FAILED')),
    response jsonb,
    error jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    completed_at timestamptz,
    UNIQUE (token_id, tool_name, idempotency_key),
    CHECK ((status = 'PENDING') = (completed_at IS NULL)),
    CHECK ((status = 'COMPLETE') = (response IS NOT NULL)),
    CHECK ((status = 'FAILED') = (error IS NOT NULL))
);

CREATE INDEX agent_tool_requests_token_created_idx
    ON agent_tool_requests (token_id, created_at DESC);
