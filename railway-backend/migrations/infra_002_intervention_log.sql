-- Faro Protocol · Infra Migration 002
-- Intervention & calibration log (Roger calibration schema)
-- Covers both field interventions and system calibration events

CREATE TABLE IF NOT EXISTS intervention_log (
    id              BIGSERIAL PRIMARY KEY,
    tenant_id       TEXT        NOT NULL DEFAULT 'velez',
    cancha_id       TEXT        NOT NULL,
    sector_id       TEXT,
    -- Field ops: riego | corte | fertilizante | fungicida | resiembra | aireacion | escarificado
    -- System:    calibracion_sar | ajuste_ndvi | reset_kalman | cambio_bbox | cambio_fuente
    -- Ops:       partido | entrenamiento | mantenimiento_mayor | cierre_cancha
    tipo            TEXT        NOT NULL,
    subtipo         TEXT,
    valor_antes     JSONB,
    valor_despues   JSONB,
    delta_json      JSONB,
    operador        TEXT        NOT NULL DEFAULT 'system',
    notas           TEXT,
    confirmado_por  TEXT,
    confirmado_at   TIMESTAMPTZ,
    archivo_url     TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS il_tenant_cancha_time
    ON intervention_log(tenant_id, cancha_id, created_at DESC);

CREATE INDEX IF NOT EXISTS il_tipo_time
    ON intervention_log(tipo, created_at DESC);

CREATE INDEX IF NOT EXISTS il_unconfirmed
    ON intervention_log(tenant_id, created_at DESC)
    WHERE confirmado_por IS NULL AND tipo LIKE 'calibracion%';

COMMENT ON TABLE intervention_log IS
    'Field interventions + Roger calibration events. delta_json = valor_despues - valor_antes computed by app layer.';
