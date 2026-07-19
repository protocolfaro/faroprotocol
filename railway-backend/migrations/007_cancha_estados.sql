-- Migration 007: cancha_estados
-- Tabla referenciada en faro_temporal_eye._save_estado_supabase() pero nunca creada formalmente.
-- Ejecutar en: Supabase Dashboard → SQL Editor → New Query

CREATE TABLE IF NOT EXISTS cancha_estados (
    venue_id               TEXT        NOT NULL,
    cancha_id              TEXT        NOT NULL,
    fecha                  DATE        NOT NULL,
    estado_detectado       TEXT,
    prescripcion_operativa TEXT,
    created_at             TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (venue_id, cancha_id, fecha)
);

CREATE INDEX IF NOT EXISTS idx_cancha_estados_venue
    ON cancha_estados (venue_id, fecha DESC);

-- Comentario: estado_detectado values = 'OK' | 'STRESS_HIDRICO' | 'STRESS_MECANICO' | 'RECUPERACION' | 'CRITICO'
-- Poblado por faro_temporal_eye._save_estado_supabase()
-- Consultado por faro_assembler.py para prescripciones operativas del día
