-- Faro Protocol · Infra Migration 004
-- Health snapshots: periodic time-series for dashboard charting
-- Captured every 15 min by APScheduler via faro_infra.capture_health_snapshot()

CREATE TABLE IF NOT EXISTS health_snapshots (
    id                  BIGSERIAL   PRIMARY KEY,
    tenant_id           TEXT        NOT NULL DEFAULT 'velez',
    capturado_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    ndvi_median         NUMERIC(5,3),
    et0_latest          NUMERIC(6,2),
    sm_theta_median     NUMERIC(6,4),
    pipeline_ok         BOOLEAN,
    canchas_ok          SMALLINT,
    canchas_total       SMALLINT,
    sar_age_hours       SMALLINT,
    ndvi_age_hours      SMALLINT,
    climate_age_hours   SMALLINT,
    audit_errors_1h     SMALLINT    DEFAULT 0,
    active_alerts       SMALLINT    DEFAULT 0,
    score_global        SMALLINT,
    extra               JSONB
);

CREATE INDEX IF NOT EXISTS hs_tenant_time
    ON health_snapshots(tenant_id, capturado_at DESC);

-- Retention view: last 90 days (use pg_cron or Supabase scheduled function to purge)
COMMENT ON TABLE health_snapshots IS
    'System health time series — 15-min resolution, 90-day retention target.';
