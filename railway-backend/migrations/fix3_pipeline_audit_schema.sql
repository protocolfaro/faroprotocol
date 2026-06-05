-- ── Faro Protocol — Fix 3: pipeline_runs + audit_log ─────────────────────────
-- Ejecutar en Supabase SQL Editor.
-- Idempotente: usa IF NOT EXISTS y DO $$ para ALTER seguro.

-- pipeline_runs: historial de ciclos satelitales Vélez
CREATE TABLE IF NOT EXISTS pipeline_runs (
    id                  BIGSERIAL    PRIMARY KEY,
    timestamp_utc       TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    fecha_imagen        TEXT,
    ndvi_median         REAL,
    accepted            BOOLEAN      NOT NULL,
    canchas_procesadas  INTEGER,
    skipped_reason      TEXT,
    error               TEXT
);

CREATE INDEX IF NOT EXISTS pipeline_runs_ts_idx
    ON pipeline_runs (timestamp_utc DESC);

-- Agrega created_at como alias de timestamp_utc si la tabla ya existía sin él
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'pipeline_runs' AND column_name = 'created_at'
    ) THEN
        ALTER TABLE pipeline_runs
            ADD COLUMN created_at TIMESTAMPTZ DEFAULT NOW();
    END IF;
END $$;

-- audit_log: trazabilidad inmutable de cada run del pipeline Dale Play
CREATE TABLE IF NOT EXISTS audit_log (
    id                 BIGSERIAL    PRIMARY KEY,
    show_id            TEXT         NOT NULL,
    timestamp          TIMESTAMPTZ  DEFAULT NOW(),
    modulos_ejecutados JSONB,
    modulos_fallidos   JSONB,
    fuentes_usadas     JSONB,
    confianza_general  TEXT,
    smoke_tests        JSONB
);

CREATE INDEX IF NOT EXISTS audit_log_show_idx ON audit_log (show_id);
CREATE INDEX IF NOT EXISTS audit_log_ts_idx   ON audit_log (timestamp DESC);
