-- Durable production monitoring: compare-and-set worker leases, append-only audit
-- history, deduplicated alert incidents, occurrence history, and notification delivery.

CREATE TABLE worker_leases (
    lease_id uuid PRIMARY KEY,
    worker_type varchar(40) NOT NULL CHECK (worker_type IN (
        'MARKET_DATA', 'SIGNAL', 'BACKTEST', 'RESEARCH_EXPERIMENT',
        'WALK_FORWARD', 'PAPER_EXECUTION', 'LIVE_RECONCILIATION', 'MONITORING'
    )),
    task_key text NOT NULL,
    worker_identity text NOT NULL,
    host_identity text NOT NULL,
    process_identity text NOT NULL,
    owner_token uuid NOT NULL,
    acquired_at timestamptz NOT NULL,
    heartbeat_at timestamptz NOT NULL,
    expires_at timestamptz NOT NULL,
    current_task jsonb NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(current_task) = 'object'),
    status varchar(16) NOT NULL CHECK (status IN ('ACTIVE', 'RELEASED', 'EXPIRED', 'FAILED')),
    last_error text,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (worker_type, task_key),
    CHECK (heartbeat_at >= acquired_at),
    CHECK (expires_at > heartbeat_at),
    CHECK (length(task_key) BETWEEN 1 AND 200),
    CHECK (length(worker_identity) BETWEEN 1 AND 200)
);

CREATE INDEX worker_leases_expiry_idx ON worker_leases (status, expires_at);
CREATE INDEX worker_leases_worker_idx ON worker_leases (worker_type, heartbeat_at DESC);

CREATE TABLE operational_audit_events (
    audit_id uuid PRIMARY KEY,
    request_id varchar(128) NOT NULL,
    action varchar(80) NOT NULL,
    actor_type varchar(20) NOT NULL CHECK (actor_type IN ('USER', 'SYSTEM', 'WORKER', 'AGENT')),
    actor_id varchar(200) NOT NULL,
    subject_type varchar(80),
    subject_id varchar(200),
    success boolean NOT NULL,
    details jsonb NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(details) = 'object'),
    created_at timestamptz NOT NULL DEFAULT now(),
    CHECK (length(request_id) BETWEEN 1 AND 128),
    CHECK (length(action) BETWEEN 1 AND 80)
);

CREATE INDEX operational_audit_created_idx ON operational_audit_events (created_at DESC);
CREATE INDEX operational_audit_action_idx ON operational_audit_events (action, created_at DESC);
CREATE INDEX operational_audit_subject_idx
    ON operational_audit_events (subject_type, subject_id, created_at DESC);

CREATE TABLE operational_alerts (
    alert_id uuid PRIMARY KEY,
    fingerprint char(64) NOT NULL,
    alert_type varchar(48) NOT NULL CHECK (alert_type IN (
        'FAILED_WORKER_CYCLE', 'REPEATED_CYCLE_FAILURE', 'STALE_NSE_DATA',
        'STALE_CRYPTO_DATA', 'MISSING_CANDLES', 'DHAN_DISCONNECTION',
        'OKX_DISCONNECTION', 'VALR_DISCONNECTION', 'STRATEGY_RUNNER_UNAVAILABLE',
        'BACKTEST_QUEUE_SATURATION', 'RESEARCH_QUEUE_SATURATION',
        'DATABASE_UNAVAILABLE', 'RECONCILIATION_MISMATCH',
        'UNKNOWN_LIVE_ORDER_STATE', 'EMERGENCY_STOP_ACTIVATED'
    )),
    severity varchar(12) NOT NULL CHECK (severity IN ('INFO', 'WARNING', 'CRITICAL')),
    source varchar(100) NOT NULL,
    title varchar(200) NOT NULL,
    message text NOT NULL,
    context jsonb NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(context) = 'object'),
    status varchar(16) NOT NULL CHECK (status IN ('OPEN', 'ACKNOWLEDGED', 'RESOLVED')),
    occurrence_count integer NOT NULL DEFAULT 1 CHECK (occurrence_count > 0),
    first_seen_at timestamptz NOT NULL,
    last_seen_at timestamptz NOT NULL,
    cooldown_until timestamptz NOT NULL,
    acknowledged_at timestamptz,
    acknowledged_by varchar(200),
    resolved_at timestamptz,
    resolved_by varchar(200),
    resolution text,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CHECK (last_seen_at >= first_seen_at),
    CHECK ((status = 'ACKNOWLEDGED') = (acknowledged_at IS NOT NULL AND acknowledged_by IS NOT NULL)
        OR status = 'RESOLVED'),
    CHECK ((status = 'RESOLVED') = (resolved_at IS NOT NULL AND resolved_by IS NOT NULL AND resolution IS NOT NULL)
        OR status <> 'RESOLVED')
);

CREATE UNIQUE INDEX operational_alerts_active_fingerprint_uq
    ON operational_alerts (fingerprint) WHERE status <> 'RESOLVED';
CREATE INDEX operational_alerts_status_idx ON operational_alerts (status, severity, last_seen_at DESC);

CREATE TABLE operational_alert_occurrences (
    occurrence_id uuid PRIMARY KEY,
    alert_id uuid NOT NULL REFERENCES operational_alerts(alert_id) ON DELETE RESTRICT,
    context jsonb NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(context) = 'object'),
    occurred_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX operational_alert_occurrences_idx
    ON operational_alert_occurrences (alert_id, occurred_at DESC);

CREATE TABLE notification_deliveries (
    delivery_id uuid PRIMARY KEY,
    alert_id uuid NOT NULL REFERENCES operational_alerts(alert_id) ON DELETE RESTRICT,
    provider varchar(20) NOT NULL CHECK (provider IN ('UI', 'LOG', 'WEBHOOK', 'EMAIL')),
    status varchar(16) NOT NULL CHECK (status IN ('PENDING', 'SENT', 'FAILED', 'SKIPPED')),
    attempt_count integer NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
    last_error text,
    attempted_at timestamptz,
    delivered_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (alert_id, provider)
);

CREATE INDEX notification_deliveries_pending_idx
    ON notification_deliveries (status, created_at) WHERE status IN ('PENDING', 'FAILED');

CREATE FUNCTION prevent_operational_history_mutation() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'operational history is append-only';
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER operational_audit_append_only
    BEFORE UPDATE OR DELETE ON operational_audit_events
    FOR EACH ROW EXECUTE FUNCTION prevent_operational_history_mutation();

CREATE TRIGGER operational_alert_occurrences_append_only
    BEFORE UPDATE OR DELETE ON operational_alert_occurrences
    FOR EACH ROW EXECUTE FUNCTION prevent_operational_history_mutation();
