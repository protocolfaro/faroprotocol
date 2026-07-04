CREATE TABLE IF NOT EXISTS intervention_log (
    id                  BIGSERIAL       PRIMARY KEY,
    venue_id            TEXT            NOT NULL DEFAULT 'velez',
    cancha_id           TEXT            NOT NULL,
    sector_id           TEXT,
    tipo                TEXT            NOT NULL,
    subtipo             TEXT,
    delta_ndvi          NUMERIC(6,4),
    delta_sar_vv_db     NUMERIC(6,2),
    delta_sar_vh_db     NUMERIC(6,2),
    delta_theta_soil    NUMERIC(6,4),
    delta_et0_mm        NUMERIC(6,2),
    delta_score         SMALLINT,
    estado_antes        JSONB           NOT NULL DEFAULT '{}',
    estado_despues      JSONB           NOT NULL DEFAULT '{}',
    operador            TEXT            NOT NULL DEFAULT 'system',
    notas               TEXT,
    confirmado_por      TEXT,
    confirmado_at       TIMESTAMPTZ,
    created_at          TIMESTAMPTZ     NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS il_venue_cancha_time ON intervention_log(venue_id, cancha_id, created_at DESC);
CREATE INDEX IF NOT EXISTS il_tipo              ON intervention_log(tipo, created_at DESC);
CREATE INDEX IF NOT EXISTS il_unconfirmed       ON intervention_log(venue_id, created_at DESC)
    WHERE confirmado_por IS NULL AND tipo LIKE 'calibracion%';
