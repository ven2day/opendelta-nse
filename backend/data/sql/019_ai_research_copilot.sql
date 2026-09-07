-- Provider-neutral AI Research Copilot audit metadata and explicit user-saved drafts.
-- Provider prompts/responses and secrets are intentionally not persisted here.

CREATE TABLE IF NOT EXISTS ai_copilot_requests (
    request_id uuid PRIMARY KEY,
    actor varchar(120) NOT NULL,
    action varchar(40) NOT NULL CHECK (action IN (
        'EXPLAIN_STRATEGY', 'EXPLAIN_INDICATOR', 'EXPLAIN_BACKTEST',
        'SUGGEST_IMPROVEMENTS', 'SUGGEST_EXPERIMENT', 'EXPLAIN_COMPARISON',
        'EXPLAIN_WALK_FORWARD', 'DRAFT_STRATEGY', 'DRAFT_INDICATOR', 'DRAFT_CONFIGURATION'
    )),
    context_categories jsonb NOT NULL DEFAULT '[]'::jsonb CHECK (jsonb_typeof(context_categories) = 'array'),
    provider varchar(80) NOT NULL,
    model varchar(160) NOT NULL,
    status varchar(16) NOT NULL CHECK (status IN ('STARTED', 'SUCCEEDED', 'FAILED')),
    input_bytes integer NOT NULL CHECK (input_bytes BETWEEN 0 AND 262144),
    output_bytes integer CHECK (output_bytes BETWEEN 0 AND 65536),
    output_sha256 char(64),
    duration_ms integer CHECK (duration_ms >= 0),
    usage jsonb NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(usage) = 'object'),
    error_code varchar(80),
    created_at timestamptz NOT NULL DEFAULT now(),
    completed_at timestamptz,
    CHECK (output_sha256 IS NULL OR output_sha256 ~ '^[0-9a-f]{64}$'),
    CONSTRAINT ai_copilot_requests_lifecycle CHECK (
        (status = 'STARTED' AND completed_at IS NULL AND duration_ms IS NULL AND output_bytes IS NULL AND output_sha256 IS NULL)
        OR (status = 'SUCCEEDED' AND completed_at IS NOT NULL AND duration_ms IS NOT NULL AND output_bytes IS NOT NULL AND output_sha256 IS NOT NULL AND error_code IS NULL)
        OR (status = 'FAILED' AND completed_at IS NOT NULL AND duration_ms IS NOT NULL AND output_bytes IS NULL AND output_sha256 IS NULL AND error_code IS NOT NULL)
    )
);

CREATE INDEX IF NOT EXISTS ai_copilot_requests_actor_created_idx
    ON ai_copilot_requests (actor, created_at DESC);
CREATE INDEX IF NOT EXISTS ai_copilot_requests_status_created_idx
    ON ai_copilot_requests (status, created_at DESC);

CREATE TABLE IF NOT EXISTS ai_research_drafts (
    draft_id uuid PRIMARY KEY,
    request_id uuid NOT NULL UNIQUE REFERENCES ai_copilot_requests(request_id) ON DELETE RESTRICT,
    draft_type varchar(20) NOT NULL CHECK (draft_type IN ('STRATEGY', 'INDICATOR', 'CONFIGURATION', 'NOTE')),
    content text NOT NULL CHECK (octet_length(content) BETWEEN 1 AND 131072),
    status varchar(16) NOT NULL DEFAULT 'DRAFT' CHECK (status = 'DRAFT'),
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ai_research_drafts_request_idx
    ON ai_research_drafts (request_id, created_at);
