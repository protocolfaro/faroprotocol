-- Faro Protocol · Infra Migration 003
-- Alert configuration: Slack webhooks + email dispatch rules

CREATE TABLE IF NOT EXISTS alert_config (
    id                BIGSERIAL PRIMARY KEY,
    tenant_id         TEXT        NOT NULL DEFAULT 'velez',
    nombre            TEXT        NOT NULL,
    -- Metric keys: ndvi_below | ndvi_above | et0_above | sm_below | sm_above
    --              smith_kerns_above | ndvi_stale_hours | sar_stale_hours
    --              pipeline_failed | audit_errors_above
    metrica           TEXT        NOT NULL,
    umbral            NUMERIC,
    operador          TEXT        NOT NULL DEFAULT '<',
    cancha_id         TEXT,
    -- canal: email | slack | both
    canal             TEXT        NOT NULL DEFAULT 'email',
    -- Store env var name (not the webhook URL itself) for security
    slack_webhook_env TEXT        NOT NULL DEFAULT 'SLACK_WEBHOOK_URL',
    email_to          TEXT[]      NOT NULL DEFAULT '{}',
    activo            BOOLEAN     NOT NULL DEFAULT TRUE,
    cooldown_minutes  INTEGER     NOT NULL DEFAULT 60,
    ultimo_disparo    TIMESTAMPTZ,
    disparos_total    INTEGER     NOT NULL DEFAULT 0,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS alert_config_tenant_nombre
    ON alert_config(tenant_id, nombre);

CREATE INDEX IF NOT EXISTS alert_config_active
    ON alert_config(tenant_id, metrica)
    WHERE activo = TRUE;

COMMENT ON COLUMN alert_config.slack_webhook_env IS
    'Name of the Railway env var containing the Slack webhook URL — never store the URL directly.';
