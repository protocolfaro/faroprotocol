-- ============================================================
-- DpRVIc Schema — Faro Protocol / Vélez Sarsfield
-- Ejecutar en Supabase SQL Editor (Settings → SQL Editor)
-- ============================================================

-- Tabla principal: métricas DpRVIc por cancha y fecha
CREATE TABLE IF NOT EXISTS dprvi_metrics (
  id                    BIGSERIAL PRIMARY KEY,
  cancha_id             VARCHAR(32)  NOT NULL,
  fecha                 DATE         NOT NULL,
  dprvi_value           FLOAT,
  dprvi_confidence_pct  INT,
  vh_db                 FLOAT,
  vv_db                 FLOAT,
  vh_vv_ratio           FLOAT,
  created_at            TIMESTAMP    DEFAULT NOW(),
  CONSTRAINT unique_cancha_fecha_dprvi UNIQUE(cancha_id, fecha)
);

CREATE INDEX IF NOT EXISTS idx_dprvi_cancha_fecha
  ON dprvi_metrics(cancha_id, fecha DESC);

-- Tabla diagnóstico: fusión ERA5 + DpRVIc por cancha y fecha
CREATE TABLE IF NOT EXISTS hermes_diagnostico (
  id                      BIGSERIAL PRIMARY KEY,
  cancha_id               VARCHAR(32)  NOT NULL,
  fecha                   DATE         NOT NULL,
  humedad_estimada_m3m3   FLOAT,
  humedad_confianza_pct   INT,
  humedad_fuente          VARCHAR(32),
  riego_detectado         BOOLEAN,
  nota_calidad_texto      TEXT,
  created_at              TIMESTAMP    DEFAULT NOW(),
  CONSTRAINT unique_cancha_fecha_hermes UNIQUE(cancha_id, fecha)
);

CREATE INDEX IF NOT EXISTS idx_hermes_cancha_fecha
  ON hermes_diagnostico(cancha_id, fecha DESC);

-- RLS: habilitar pero permitir service_role
ALTER TABLE dprvi_metrics    ENABLE ROW LEVEL SECURITY;
ALTER TABLE hermes_diagnostico ENABLE ROW LEVEL SECURITY;

-- Política: solo service_role puede escribir; anon solo puede leer su propia data
-- (ajustar según necesidad de portal)
CREATE POLICY dprvi_service_rw ON dprvi_metrics
  USING (auth.role() = 'service_role')
  WITH CHECK (auth.role() = 'service_role');

CREATE POLICY hermes_service_rw ON hermes_diagnostico
  USING (auth.role() = 'service_role')
  WITH CHECK (auth.role() = 'service_role');
