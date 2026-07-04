-- Faro Protocol · Infra Migration 001
-- Audit log: full API lineage tracking per tenant
-- Idempotent — safe to run multiple times

CREATE TABLE IF NOT EXISTS audit_log (
    id            BIGSERIAL PRIMARY KEY,
    tenant_id     TEXT        NOT NULL DEFAULT 'velez',
    method        TEXT        NOT NULL,
    path          TEXT        NOT NULL,
    status_code   SMALLINT,
    duration_ms   INTEGER,
    request_hash  TEXT,
    ip_addr       TEXT,
    user_agent    TEXT,
    pin_used      BOOLEAN     DEFAULT FALSE,
    error_msg     TEXT,
    extra         JSONB,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS audit_log_tenant_time
    ON audit_log(tenant_id, created_at DESC);

CREATE INDEX IF NOT EXISTS audit_log_path_status
    ON audit_log(path, status_code);

CREATE INDEX IF NOT EXISTS audit_log_errors
    ON audit_log(created_at DESC)
    WHERE status_code >= 400;

COMMENT ON TABLE audit_log IS
    'API request lineage — 90-day rolling window. Populated by faro_infra middleware.';
