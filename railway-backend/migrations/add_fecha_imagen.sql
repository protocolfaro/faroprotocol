-- Faro Protocol — Migration add_fecha_imagen
-- Agrega columna fecha_imagen (date) a soil_metrics y vegetation_metrics.
-- Ambas tablas fueron creadas sin esta columna; el pipeline la envía en cada insert
-- causando HTTP 400 PGRST204 y dejando las dos tablas siempre en 0 filas.
-- Idempotente: ADD COLUMN IF NOT EXISTS. Seguro para re-ejecutar.

ALTER TABLE vegetation_metrics ADD COLUMN IF NOT EXISTS fecha_imagen DATE;
ALTER TABLE soil_metrics       ADD COLUMN IF NOT EXISTS fecha_imagen DATE;

-- Forzar recarga del schema cache de PostgREST (fix inmediato de PGRST204)
NOTIFY pgrst, 'reload schema';
