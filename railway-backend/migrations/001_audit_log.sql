CREATE TABLE IF NOT EXISTS audit_log (
    id              BIGSERIAL       PRIMARY KEY,
    venue_id        TEXT            NOT NULL DEFAULT 'velez',
    action_type     TEXT            NOT NULL,
    source          TEXT            NOT NULL,
    method          TEXT,
    path            TEXT,
    status_code     SMALLINT,
    duration_ms     INTEGER,
    confidence_pct  SMALLINT        CHECK (confidence_pct BETWEEN 0 AND 100),
    metadata        JSONB           NOT NULL DEFAULT '{}',
    error_msg       TEXT,
    ip_addr         TEXT,
    created_at      TIMESTAMPTZ     NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS al_venue_time    ON audit_log(venue_id, created_at DESC);
CREATE INDEX IF NOT EXISTS al_action        ON audit_log(action_type, created_at DESC);
CREATE INDEX IF NOT EXISTS al_source        ON audit_log(source, created_at DESC);
CREATE INDEX IF NOT EXISTS al_errors        ON audit_log(created_at DESC) WHERE status_code >= 400;
CREATE INDEX IF NOT EXISTS al_metadata_gin  ON audit_log USING GIN(metadata);
