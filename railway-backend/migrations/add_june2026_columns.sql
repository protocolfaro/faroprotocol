-- Faro Protocol — Migration junio 2026
-- Nuevas columnas por módulos: NDRE real, CLMS LST, Open-Meteo soil layers, SAOCOM
-- Idempotente: usa ADD COLUMN IF NOT EXISTS. Seguro para re-ejecutar.

-- ── vegetation_metrics: NDRE real (B05 Sentinel-2) ────────────────────────────
ALTER TABLE vegetation_metrics ADD COLUMN IF NOT EXISTS ndre REAL;
COMMENT ON COLUMN vegetation_metrics.ndre IS
  'NDRE real = (B08-B05)/(B08+B05) Sentinel-2. Reemplaza ndre=ndvi*0.65 hardcodeado. Sensible a nitrógeno.';

-- ── climate_metrics: CLMS LST + Smith-Kerns threshold por superficie ──────────
ALTER TABLE climate_metrics ADD COLUMN IF NOT EXISTS temp_superficie_lst_c REAL;
COMMENT ON COLUMN climate_metrics.temp_superficie_lst_c IS
  'Temperatura superficial pasto en °C. Fuente: CLMS LST v3 3km (CDSE, jun 2026). Fallback: Open-Meteo soil_temperature_0cm.';

ALTER TABLE climate_metrics ADD COLUMN IF NOT EXISTS smith_kerns_threshold_pct REAL DEFAULT 20.0;
COMMENT ON COLUMN climate_metrics.smith_kerns_threshold_pct IS
  'Umbral Smith-Kerns por tipo de césped: natural/hibrido=20%, bermuda_stress=15%. MSU validated.';

-- ── soil_metrics: SAOCOM L-band + Open-Meteo soil layers ─────────────────────
ALTER TABLE soil_metrics ADD COLUMN IF NOT EXISTS theta_saocom_l REAL;
COMMENT ON COLUMN soil_metrics.theta_saocom_l IS
  'Humedad volumétrica m³/m³ estimada de SAOCOM L-band. Penetra hasta 30-40cm (vs C-band 0-5cm).';

ALTER TABLE soil_metrics ADD COLUMN IF NOT EXISTS sigma_l_hh_db REAL;
COMMENT ON COLUMN soil_metrics.sigma_l_hh_db IS
  'Backscatter SAOCOM L-band HH en dB.';

ALTER TABLE soil_metrics ADD COLUMN IF NOT EXISTS sigma_l_hv_db REAL;
COMMENT ON COLUMN soil_metrics.sigma_l_hv_db IS
  'Backscatter SAOCOM L-band HV en dB. Indica volumen de vegetación.';

ALTER TABLE soil_metrics ADD COLUMN IF NOT EXISTS theta_openmeteo_0cm REAL;
COMMENT ON COLUMN soil_metrics.theta_openmeteo_0cm IS
  'Humedad suelo Open-Meteo 0-1cm. Complementa Van Genuchten SAR con dato NWP horario.';

ALTER TABLE soil_metrics ADD COLUMN IF NOT EXISTS theta_openmeteo_3cm REAL;
COMMENT ON COLUMN soil_metrics.theta_openmeteo_3cm IS
  'Humedad suelo Open-Meteo 1-3cm.';

ALTER TABLE soil_metrics ADD COLUMN IF NOT EXISTS theta_openmeteo_9cm REAL;
COMMENT ON COLUMN soil_metrics.theta_openmeteo_9cm IS
  'Humedad suelo Open-Meteo 3-9cm. Nueva capa agregada en junio 2026.';

-- ── weather_live: nuevos campos de Open-Meteo soil layers ─────────────────────
-- (weather_live es JSONB — no requiere ALTER, pero documentar en schema)
-- Los campos nuevos son: humedad_9cm_pct, temp_suelo_0cm, temp_suelo_6cm, temp_suelo_18cm

-- ── velez_canchas: NDRE real ──────────────────────────────────────────────────
ALTER TABLE velez_canchas ADD COLUMN IF NOT EXISTS ndre REAL;
COMMENT ON COLUMN velez_canchas.ndre IS
  'NDRE real por cancha — calculado en ndvi_real.py con B05 Sentinel-2.';

-- ── Forzar reload schema PostgREST ───────────────────────────────────────────
NOTIFY pgrst, 'reload schema';
