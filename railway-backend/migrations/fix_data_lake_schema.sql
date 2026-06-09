-- fix_data_lake_schema.sql — Faro Protocol
-- Causa: climate_metrics fue creada sin las columnas de payload; soil_metrics con cancha_id NOT NULL.
-- Idempotente: safe to run N veces. Se aplica automáticamente en Railway startup via SUPABASE_DB_URL.
-- También puede ejecutarse manualmente en Supabase SQL Editor.

-- ── climate_metrics: agregar columnas de payload que faltaban en el CREATE original ──
ALTER TABLE climate_metrics ADD COLUMN IF NOT EXISTS et0_mm_dia         NUMERIC;
ALTER TABLE climate_metrics ADD COLUMN IF NOT EXISTS deficit_hidrico_mm NUMERIC;
ALTER TABLE climate_metrics ADD COLUMN IF NOT EXISTS gdd_acumulado_7d   NUMERIC;
ALTER TABLE climate_metrics ADD COLUMN IF NOT EXISTS smith_kerns_pct    NUMERIC;
ALTER TABLE climate_metrics ADD COLUMN IF NOT EXISTS riego_min          SMALLINT;
ALTER TABLE climate_metrics ADD COLUMN IF NOT EXISTS ventana_corte      TEXT;
ALTER TABLE climate_metrics ADD COLUMN IF NOT EXISTS altura_corte_mm    NUMERIC;
ALTER TABLE climate_metrics ADD COLUMN IF NOT EXISTS fuente             TEXT;
ALTER TABLE climate_metrics ADD COLUMN IF NOT EXISTS fecha              DATE;

-- ── soil_metrics: cancha_id debe ser nullable para inserts a nivel venue ──
ALTER TABLE soil_metrics ALTER COLUMN cancha_id DROP NOT NULL;

-- ── vegetation_metrics: cancha_id debe ser nullable para inserts a nivel venue ──
ALTER TABLE vegetation_metrics ALTER COLUMN cancha_id DROP NOT NULL;

-- ── vegetation_metrics: columnas Kalman gap-fill agregadas 2026-06-09 ──
ALTER TABLE vegetation_metrics ADD COLUMN IF NOT EXISTS ndvi_sintetico      REAL;
ALTER TABLE vegetation_metrics ADD COLUMN IF NOT EXISTS margen_error_kalman REAL;
ALTER TABLE vegetation_metrics ADD COLUMN IF NOT EXISTS metodo_generacion    TEXT DEFAULT 'SENTINEL_DIRECTO';

-- ── field_timeseries: serie temporal NDVI histórica por venue (Kalman training) ──
CREATE TABLE IF NOT EXISTS field_timeseries (
    id         BIGSERIAL PRIMARY KEY,
    venue_id   TEXT        NOT NULL,
    fecha      TEXT        NOT NULL,
    ndvi       REAL        NOT NULL,
    fuente     TEXT,
    nubosidad  REAL,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (venue_id, fecha)
);
CREATE INDEX IF NOT EXISTS idx_field_ts_venue_fecha ON field_timeseries (venue_id, fecha ASC);

-- ── fenologia_baselines: modelo fenológico doble-logístico por venue ──
CREATE TABLE IF NOT EXISTS fenologia_baselines (
    venue_id   TEXT PRIMARY KEY,
    baseline   JSONB,
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- ── qa_alerts: alertas post-ciclo del QA watchdog (schema v2 — 2026-06-09) ─────
-- DROP + recreate porque v1 tenía check_name NOT NULL incompatible con watchdog v2
DROP TABLE IF EXISTS qa_alerts CASCADE;
CREATE TABLE qa_alerts (
    id                 BIGSERIAL PRIMARY KEY,
    nivel              TEXT CHECK (nivel IN ('ERROR','WARNING','INFO')),
    modulo             TEXT NOT NULL,
    mensaje            TEXT NOT NULL,
    diagnostico_claude TEXT,
    venue_id           TEXT DEFAULT 'velez',
    resuelto           BOOLEAN DEFAULT FALSE,
    created_at         TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_qa_alerts_ts    ON qa_alerts (created_at DESC);
CREATE INDEX IF NOT EXISTS idx_qa_alerts_nivel ON qa_alerts (nivel, created_at DESC);
ALTER TABLE qa_alerts DISABLE ROW LEVEL SECURITY;
GRANT ALL ON public.qa_alerts TO anon;
GRANT USAGE, SELECT ON SEQUENCE public.qa_alerts_id_seq TO anon;

-- ── Stack científico — columnas nuevas 2026-06-09 ─────────────────────────────
-- soil_metrics: polarimetría SAR H-Alpha, temperatura superficial, perfil Richards, ML, SMAP
ALTER TABLE soil_metrics ADD COLUMN IF NOT EXISTS entropia_h          REAL;
ALTER TABLE soil_metrics ADD COLUMN IF NOT EXISTS angulo_alpha        REAL;
ALTER TABLE soil_metrics ADD COLUMN IF NOT EXISTS temp_superficie_c   REAL;
ALTER TABLE soil_metrics ADD COLUMN IF NOT EXISTS theta_5cm           REAL;
ALTER TABLE soil_metrics ADD COLUMN IF NOT EXISTS theta_10cm          REAL;
ALTER TABLE soil_metrics ADD COLUMN IF NOT EXISTS theta_20cm          REAL;
ALTER TABLE soil_metrics ADD COLUMN IF NOT EXISTS compactacion_index_ml REAL;
ALTER TABLE soil_metrics ADD COLUMN IF NOT EXISTS theta_smap          REAL;

-- climate_metrics: ET termodinámica ECOSTRESS
ALTER TABLE climate_metrics ADD COLUMN IF NOT EXISTS et_ecostress_mm  REAL;

-- vegetation_metrics: super-resolución ESA 2.5m
ALTER TABLE vegetation_metrics ADD COLUMN IF NOT EXISTS ndvi_2_5m     REAL;
ALTER TABLE vegetation_metrics ADD COLUMN IF NOT EXISTS bsi_2_5m      REAL;
ALTER TABLE vegetation_metrics ADD COLUMN IF NOT EXISTS ndwi_2_5m     REAL;

-- ── Forzar recarga del schema cache de PostgREST (fix inmediato de PGRST204) ──
NOTIFY pgrst, 'reload schema';
