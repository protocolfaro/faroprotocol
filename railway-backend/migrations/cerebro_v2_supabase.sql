-- Faro Cerebro v2.0 — Tablas Supabase
-- Ejecutar en: Supabase Dashboard → SQL Editor → New Query
-- ─────────────────────────────────────────────────────────────────────────────

-- Cooldowns persistentes (reemplaza SQLite local que se destruye en Railway)
CREATE TABLE IF NOT EXISTS faro_cerebro_cooldowns (
    id          BIGSERIAL PRIMARY KEY,
    fingerprint TEXT        NOT NULL,
    sent_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_cooldowns_fp_sent
    ON faro_cerebro_cooldowns (fingerprint, sent_at DESC);

-- Limpiar cooldowns viejos automáticamente (>24h)
-- Ejecutar como cron en Supabase o pg_cron si está disponible:
-- SELECT cron.schedule('cleanup-cooldowns', '0 * * * *',
--   'DELETE FROM faro_cerebro_cooldowns WHERE sent_at < NOW() - INTERVAL ''24 hours''');


-- Skills aprendidas por el cerebro (persisten entre redeployos)
CREATE TABLE IF NOT EXISTS faro_cerebro_skills (
    id          BIGSERIAL PRIMARY KEY,
    name        TEXT        NOT NULL,
    description TEXT,
    code        TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Log de acciones del cerebro (ya existente — asegurar columnas nuevas)
ALTER TABLE faro_cerebro_log
    ADD COLUMN IF NOT EXISTS escalado  BOOLEAN     DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS timestamp TIMESTAMPTZ DEFAULT NOW();

-- RLS: solo el service role puede escribir (si está habilitado)
-- ALTER TABLE faro_cerebro_cooldowns ENABLE ROW LEVEL SECURITY;
-- ALTER TABLE faro_cerebro_skills    ENABLE ROW LEVEL SECURITY;
