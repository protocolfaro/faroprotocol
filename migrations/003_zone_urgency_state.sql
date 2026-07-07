-- zone_urgency_state: debounce table for Faro prescription urgency hysteresis
-- Escalate immediately; downgrade only after 3 consecutive lower cycles
-- Ejecutar en Supabase SQL Editor

CREATE TABLE IF NOT EXISTS zone_urgency_state (
    cancha_id         TEXT NOT NULL,
    zone_id           TEXT NOT NULL,
    urgency           TEXT NOT NULL DEFAULT 'OK',
    consecutive_count INTEGER      DEFAULT 1,
    last_updated      TIMESTAMPTZ  DEFAULT NOW(),
    PRIMARY KEY (cancha_id, zone_id)
);

ALTER TABLE zone_urgency_state DISABLE ROW LEVEL SECURITY;
GRANT ALL ON public.zone_urgency_state TO anon;

COMMENT ON TABLE zone_urgency_state IS
  'Debounce state for per-zone urgency: prevents flickering on 5-min poll cycles.';
