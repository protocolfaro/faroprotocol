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

-- ── soil_metrics: cancha_id debe ser nullable para inserts a nivel venue ──
ALTER TABLE soil_metrics ALTER COLUMN cancha_id DROP NOT NULL;

-- ── vegetation_metrics: columnas Kalman gap-fill agregadas 2026-06-09 ──
ALTER TABLE vegetation_metrics ADD COLUMN IF NOT EXISTS ndvi_sintetico      REAL;
ALTER TABLE vegetation_metrics ADD COLUMN IF NOT EXISTS margen_error_kalman REAL;
ALTER TABLE vegetation_metrics ADD COLUMN IF NOT EXISTS metodo_generacion    TEXT DEFAULT 'SENTINEL_DIRECTO';

-- ── Forzar recarga del schema cache de PostgREST (fix inmediato de PGRST204) ──
NOTIFY pgrst, 'reload schema';
