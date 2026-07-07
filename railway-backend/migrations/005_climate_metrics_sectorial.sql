-- climate_metrics_sectorial: ET₀, T°, RH y humedad suelo por sector ERA5-Land
-- PK (sector_id, fecha) — UPSERT idempotente desde faro_era5_land_sectorial.py
-- Ejecutar en Supabase SQL Editor

CREATE TABLE IF NOT EXISTS climate_metrics_sectorial (
    sector_id       TEXT        NOT NULL,
    fecha           DATE        NOT NULL,
    et0_mm_dia      NUMERIC,
    temp_2m_c       NUMERIC,
    rh_pct          NUMERIC,
    sm_0_7cm_m3m3   NUMERIC,
    sm_0_7cm_pct    NUMERIC,
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (sector_id, fecha)
);

CREATE INDEX IF NOT EXISTS idx_cms_sector_fecha
    ON climate_metrics_sectorial (sector_id, fecha DESC);

COMMENT ON TABLE climate_metrics_sectorial IS
  'ERA5-Land 100m: ET₀ Hargreaves-Samani, T 2m, RH, SM 0-7cm por bloque de canchas (lag ~7d).';

ALTER TABLE climate_metrics_sectorial DISABLE ROW LEVEL SECURITY;
GRANT ALL ON public.climate_metrics_sectorial TO anon;
