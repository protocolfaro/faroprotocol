-- ── Faro Protocol — Agentes Claude API ──────────────────────────────────────
-- Ejecutar en Supabase SQL Editor antes de activar Hermes y Paperclip.

-- hermes_flags: certificados bloqueados por anomalía físico-estacional
CREATE TABLE IF NOT EXISTS hermes_flags (
    id          BIGSERIAL    PRIMARY KEY,
    area_name   TEXT         NOT NULL,
    sha256      TEXT         NOT NULL,
    mes         INT,
    anomalias   JSONB,
    motivo      TEXT,
    confianza   FLOAT,
    created_at  TIMESTAMPTZ  DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS hermes_flags_area_idx  ON hermes_flags (area_name);
CREATE INDEX IF NOT EXISTS hermes_flags_sha_idx   ON hermes_flags (sha256);
CREATE INDEX IF NOT EXISTS hermes_flags_ts_idx    ON hermes_flags (created_at DESC);

-- paperclip_decisions: todas las decisiones del monitor inteligente (append-only)
CREATE TABLE IF NOT EXISTS paperclip_decisions (
    id           BIGSERIAL    PRIMARY KEY,
    venue_id     TEXT         NOT NULL,
    alertar      BOOLEAN      NOT NULL,
    nivel        TEXT         NOT NULL,   -- none / low / medium / high
    motivo       TEXT,
    confianza    FLOAT,
    recomendacion TEXT,
    contexto     JSONB,                   -- datos de entrada usados
    created_at   TIMESTAMPTZ  DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS paperclip_venue_idx   ON paperclip_decisions (venue_id);
CREATE INDEX IF NOT EXISTS paperclip_alertar_idx ON paperclip_decisions (alertar);
CREATE INDEX IF NOT EXISTS paperclip_ts_idx      ON paperclip_decisions (created_at DESC);

-- Vista rápida de flags recientes
CREATE OR REPLACE VIEW hermes_flags_recientes AS
SELECT * FROM hermes_flags
ORDER BY created_at DESC
LIMIT 50;

-- Vista de alertas activas de Paperclip (últimas 48h)
CREATE OR REPLACE VIEW paperclip_alertas_activas AS
SELECT * FROM paperclip_decisions
WHERE alertar = TRUE
  AND created_at >= NOW() - INTERVAL '48 hours'
ORDER BY created_at DESC;
