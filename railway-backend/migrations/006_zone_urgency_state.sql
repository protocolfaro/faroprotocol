-- Migration 006: zone_urgency_state
-- Tabla referenciada en velez_supabase.get_zone_urgency_bulk() pero nunca creada formalmente.
-- Ejecutar en: Supabase Dashboard → SQL Editor → New Query

CREATE TABLE IF NOT EXISTS zone_urgency_state (
    cancha_id   TEXT        NOT NULL,
    zone_id     TEXT        NOT NULL,
    urgency     TEXT        NOT NULL DEFAULT 'OK',
    consecutive_count INTEGER     DEFAULT 1,
    last_updated TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (cancha_id, zone_id)
);

CREATE INDEX IF NOT EXISTS idx_zone_urgency_cancha
    ON zone_urgency_state (cancha_id);

-- Comentario: urgency values = 'OK' | 'WATCH' | 'ALERT' | 'CRITICAL'
-- Poblado por faro_spatial_alerts.py y consultado por faro_assembler.py via get_zone_urgency_bulk()
