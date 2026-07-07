-- ══════════════════════════════════════════════════════════════════════════════
-- migration_instituto_insar_fecha.sql
-- Ejecutar en Supabase SQL Editor — una sola vez
-- ══════════════════════════════════════════════════════════════════════════════

-- 1. Seed row para Instituto Vélez (sector vacío → PNG muestra datos en cuanto
--    el pipeline escriba InSAR real; mientras tanto muestra score inicial)
INSERT INTO velez_sectores (sector_id, nombre, score, score_prev, sem, detalle)
VALUES (
    'instituto',
    'Instituto Vélez Infanto Juvenil',
    72,
    72,
    'amarillo',
    'Primera cobertura InSAR en proceso — Sentinel-1 pendiente'
)
ON CONFLICT (sector_id) DO UPDATE
    SET nombre     = EXCLUDED.nombre,
        detalle    = EXCLUDED.detalle,
        updated_at = now()
WHERE velez_sectores.score = 0 OR velez_sectores.score IS NULL;

-- 2. Verificar que el sector solar tenga detalle dinámico (no el string viejo)
--    Si el pipeline aún no corrió, actualizar manualmente:
UPDATE velez_sectores
SET detalle    = 'Eficiencia modelo pvlib (NASA POWER GHI) — en proceso',
    updated_at = now()
WHERE sector_id = 'solar'
  AND detalle ILIKE '%13 paneles%';

-- 3. Verificar estado actual de todos los sectores
SELECT sector_id, score, sem, insar_mm,
       updated_at,
       (now() - updated_at) AS age,
       left(detalle, 60) AS detalle_preview
FROM velez_sectores
ORDER BY sector_id;
