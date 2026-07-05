-- 002_sar_satellite_column.sql
-- Agrega satellite_inferred a soil_metrics para tracking de continuidad orbital
-- Run via: psql $SUPABASE_DB_URL -f migrations/002_sar_satellite_column.sql
--
-- Context:
--   Sentinel-1A retired 2026-06-29. Last acquisition in DB: 2026-06-29.
--   S1C/D will replace S1A once operational. This column tracks which
--   satellite produced each row so continuity gaps are queryable.

-- ---------------------------------------------------------------------------
-- 1. Add column (idempotent)
-- ---------------------------------------------------------------------------

ALTER TABLE soil_metrics
  ADD COLUMN IF NOT EXISTS satellite_inferred TEXT DEFAULT NULL;

COMMENT ON COLUMN soil_metrics.satellite_inferred IS
  'Sentinel-1 satellite identifier: S1A (before 2026-06-30), S1C/D (after). Inferred from product name or acquisition date.';

-- ---------------------------------------------------------------------------
-- 2. Backfill: all data before S1A retirement → S1A
-- ---------------------------------------------------------------------------

UPDATE soil_metrics
SET satellite_inferred = 'S1A'
WHERE satellite_inferred IS NULL
  AND fecha_imagen < '2026-06-30';

-- ---------------------------------------------------------------------------
-- 3. Mark the final S1A acquisition distinctly for audit traceability
-- ---------------------------------------------------------------------------

UPDATE soil_metrics
SET satellite_inferred = 'S1A_last'
WHERE fecha_imagen = '2026-06-29'
  AND satellite_inferred = 'S1A';

-- ---------------------------------------------------------------------------
-- 4. Post-transition data (if any exists): mark as S1C/D pending validation
--    These rows indicate early S1C/D acquisitions before full calibration.
-- ---------------------------------------------------------------------------

UPDATE soil_metrics
SET satellite_inferred = 'S1C/D_unvalidated'
WHERE satellite_inferred IS NULL
  AND fecha_imagen >= '2026-06-30';

-- ---------------------------------------------------------------------------
-- 5. Index for fast satellite-based queries
-- ---------------------------------------------------------------------------

CREATE INDEX IF NOT EXISTS idx_soil_metrics_satellite
  ON soil_metrics(satellite_inferred)
  WHERE satellite_inferred IS NOT NULL;

-- Compound index: satellite + date range queries (common access pattern)
CREATE INDEX IF NOT EXISTS idx_soil_metrics_satellite_date
  ON soil_metrics(satellite_inferred, fecha_imagen)
  WHERE satellite_inferred IS NOT NULL;

-- ---------------------------------------------------------------------------
-- 6. Report: verify the backfill results
-- ---------------------------------------------------------------------------

SELECT
  satellite_inferred,
  COUNT(*)               AS row_count,
  COUNT(DISTINCT fecha_imagen) AS unique_dates,
  MIN(fecha_imagen)      AS earliest,
  MAX(fecha_imagen)      AS latest
FROM soil_metrics
GROUP BY satellite_inferred
ORDER BY MIN(fecha_imagen);
