-- Agrega columna cross_trust a velez_sectores
-- Almacena el diagnóstico del Motor Cross-Trust (Faro v3) por sector
-- Ejecutar en Supabase SQL Editor

ALTER TABLE velez_sectores
  ADD COLUMN IF NOT EXISTS cross_trust JSONB;

COMMENT ON COLUMN velez_sectores.cross_trust IS
  'Diagnóstico Motor Cross-Trust Faro v3: global_diagnosis + cuadrantes + timestamp';
