-- velez_intervenciones: tabla ya existe — índice de búsqueda rápida y columna ipos_pts
-- Ejecutar en Supabase SQL Editor

-- Asegurar índice para rolling 7d lookup por cancha + fecha
CREATE INDEX IF NOT EXISTS idx_interv_cancha_ts
    ON velez_intervenciones (cancha_id, created_at DESC);

-- Columna ipos_pts opcional (pre-calculado para auditoría; no requerida para compute_ipos_from_db)
ALTER TABLE velez_intervenciones
    ADD COLUMN IF NOT EXISTS ipos_pts NUMERIC;

COMMENT ON COLUMN velez_intervenciones.ipos_pts IS
  'IPOS points contribution: partido=120, entrenamiento=30×h, lluvia=-60/-100, aireacion=-40, corte=-10';

-- RLS y permisos (ya configurados si la tabla existía, son idempotentes)
ALTER TABLE velez_intervenciones DISABLE ROW LEVEL SECURITY;
GRANT ALL ON public.velez_intervenciones TO anon;
