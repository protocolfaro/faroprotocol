-- ══════════════════════════════════════════════════════════════════════
-- Panel Roger v3 — Migración Supabase
-- Ejecutar en: Supabase Dashboard > SQL Editor
-- ══════════════════════════════════════════════════════════════════════

-- 1. soil_metrics — nuevas columnas SAR change + VH/VV + ERA5
ALTER TABLE soil_metrics
  ADD COLUMN IF NOT EXISTS sar_vv_change_6d   NUMERIC,   -- cambio VV vs imagen anterior (≈6d)
  ADD COLUMN IF NOT EXISTS vh_vv_ratio         NUMERIC,   -- VH − VV en dB
  ADD COLUMN IF NOT EXISTS vh_vv_change_1d     NUMERIC,   -- cambio VH/VV entre últimas 2 pasadas
  ADD COLUMN IF NOT EXISTS era5_sm_0_7cm       NUMERIC,   -- ERA5-Land SM 0-7cm  (m³/m³)
  ADD COLUMN IF NOT EXISTS era5_sm_7_28cm      NUMERIC,   -- ERA5-Land SM 7-28cm
  ADD COLUMN IF NOT EXISTS era5_sm_28_100cm    NUMERIC,   -- ERA5-Land SM 28-100cm
  ADD COLUMN IF NOT EXISTS era5_sm_100_289cm   NUMERIC;   -- ERA5-Land SM 100-289cm

-- 2. weather_summary — ET₀ acumulado + déficit explícito + precipitación semanal
ALTER TABLE weather_summary
  ADD COLUMN IF NOT EXISTS et0_diario          NUMERIC,   -- ET₀ Penman-Monteith mm/día
  ADD COLUMN IF NOT EXISTS et0_acumulado_7d    NUMERIC,   -- ET₀ acum. últimos 7 días mm
  ADD COLUMN IF NOT EXISTS precip_acumulada_7d NUMERIC,   -- precipitación acum. 7 días mm
  ADD COLUMN IF NOT EXISTS deficit_mm          NUMERIC;   -- déficit hídrico mm (alias de deficit_hidrico_mm)

-- Verificar que quedaron las columnas:
-- SELECT column_name, data_type FROM information_schema.columns
-- WHERE table_name IN ('soil_metrics', 'weather_summary')
-- ORDER BY table_name, column_name;
