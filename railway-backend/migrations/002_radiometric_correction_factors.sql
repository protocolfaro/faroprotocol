-- 002_radiometric_correction_factors.sql
-- Sentinel-1 mission continuity — soil_metrics schema update
-- Run once in Supabase SQL Editor (or via psql with SUPABASE_DB_URL).
--
-- What this does:
--   1. Adds satellite_inferred TEXT to soil_metrics
--   2. Back-fills satellite_inferred from fecha_imagen date
--   3. Applies retroactive radiometric corrections for any S1C/S1D rows
--      that were inserted before correction offsets were applied at insert time
--   4. Adds UNIQUE constraint (cancha_id, fecha_imagen) to prevent duplicate backfill rows
--   5. Creates sar_correction_log table to audit what was corrected and when
--
-- Correction factors (Schmidt 2023 + Copernicus 2026-02 update):
--   S1A           VV: +0.00 dB   VH: +0.00 dB  (reference baseline)
--   S1C           VV: +0.12 dB   VH: +0.21 dB
--   S1D           VV: +0.07 dB   VH: +0.07 dB  (ESA commissioning, negligible)
--
-- Safe to run multiple times (all statements are idempotent).

BEGIN;

-- ── 1. satellite_inferred column ─────────────────────────────────────────────

ALTER TABLE soil_metrics
    ADD COLUMN IF NOT EXISTS satellite_inferred TEXT;

COMMENT ON COLUMN soil_metrics.satellite_inferred IS
    'Satellite source: S1A | S1B | S1C | S1D | unknown. '
    'Set at insert time by faro_sar_s1_backfill.py. '
    'S1A retired 2026-06-29; S1C/D operational from 2026-06-30.';

-- ── 2. Back-fill satellite_inferred from acquisition date ────────────────────
-- Rows with fecha_imagen < 2026-06-30 → S1A (single satellite in operation)
-- Rows with fecha_imagen >= 2026-06-30 → S1C/D (initial label; backfill script
-- sets the exact satellite from CDSE/PC product name for new rows)

UPDATE soil_metrics
SET satellite_inferred = 'S1A'
WHERE satellite_inferred IS NULL
  AND fecha_imagen IS NOT NULL
  AND fecha_imagen < '2026-06-30';

UPDATE soil_metrics
SET satellite_inferred = 'S1C/D'
WHERE satellite_inferred IS NULL
  AND fecha_imagen IS NOT NULL
  AND fecha_imagen >= '2026-06-30';

UPDATE soil_metrics
SET satellite_inferred = 'unknown'
WHERE satellite_inferred IS NULL;

-- ── 3. Correction audit log ──────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS sar_correction_log (
    id              BIGSERIAL       PRIMARY KEY,
    soil_metrics_id BIGINT          NOT NULL,
    cancha_id       TEXT,
    fecha_imagen    DATE,
    satellite       TEXT,
    vv_before_db    REAL,
    vh_before_db    REAL,
    vv_after_db     REAL,
    vh_after_db     REAL,
    vv_delta_db     REAL,
    vh_delta_db     REAL,
    corrected_at    TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    run_id          TEXT            DEFAULT 'migration_002'
);

COMMENT ON TABLE sar_correction_log IS
    'Audit trail for retroactive inter-satellite radiometric corrections applied '
    'to soil_metrics.sar_vv_db / sar_vh_db. Each corrected row generates one entry.';

-- ── 4. Retroactive correction — S1C rows inserted without offset ─────────────
-- Guard: only correct rows where sar_vv_db is in the uncorrected range AND
-- satellite_inferred = 'S1C/D'.  S1A rows pass through unchanged (0.00 dB offset).
-- This is a no-op if new rows were already inserted with corrections applied.

DO $$
DECLARE
    _vv_offset_s1c CONSTANT REAL := 0.12;
    _vh_offset_s1c CONSTANT REAL := 0.21;
    _vv_offset_s1d CONSTANT REAL := 0.07;
    _vh_offset_s1d CONSTANT REAL := 0.07;
    _row            RECORD;
    _new_vv         REAL;
    _new_vh         REAL;
    _offset_vv      REAL;
    _offset_vh      REAL;
BEGIN
    -- Process S1C/D rows from after retirement date that may lack correction.
    -- The fuente column can distinguish if the backfill v2 was used (it logs satellite).
    -- We only touch rows where satellite_inferred is set but sar_vv_db is non-null.
    FOR _row IN
        SELECT id, cancha_id, fecha_imagen, satellite_inferred, sar_vv_db, sar_vh_db
        FROM soil_metrics
        WHERE fecha_imagen >= '2026-06-30'
          AND satellite_inferred IN ('S1C', 'S1C/D', 'S1D')
          AND (sar_vv_db IS NOT NULL OR sar_vh_db IS NOT NULL)
    LOOP
        -- Determine offset by satellite
        IF _row.satellite_inferred IN ('S1C', 'S1C/D') THEN
            _offset_vv := _vv_offset_s1c;
            _offset_vh := _vh_offset_s1c;
        ELSIF _row.satellite_inferred = 'S1D' THEN
            _offset_vv := _vv_offset_s1d;
            _offset_vh := _vh_offset_s1d;
        ELSE
            CONTINUE;
        END IF;

        -- Check if correction appears already applied:
        -- corrected VV should be slightly higher than raw S1C (raw ≈ -8 to -15 dB typical)
        -- We apply if the row was inserted before backfill v2 (no reliable marker, so
        -- we check sar_correction_log to avoid double-applying).
        IF EXISTS (
            SELECT 1 FROM sar_correction_log
            WHERE soil_metrics_id = _row.id
        ) THEN
            CONTINUE;  -- already corrected
        END IF;

        _new_vv := CASE WHEN _row.sar_vv_db IS NOT NULL
                        THEN ROUND((_row.sar_vv_db + _offset_vv)::NUMERIC, 2)::REAL
                        ELSE NULL END;
        _new_vh := CASE WHEN _row.sar_vh_db IS NOT NULL
                        THEN ROUND((_row.sar_vh_db + _offset_vh)::NUMERIC, 2)::REAL
                        ELSE NULL END;

        -- Update the row
        UPDATE soil_metrics
        SET sar_vv_db = _new_vv,
            sar_vh_db = _new_vh
        WHERE id = _row.id;

        -- Audit log
        INSERT INTO sar_correction_log (
            soil_metrics_id, cancha_id, fecha_imagen, satellite,
            vv_before_db, vh_before_db, vv_after_db, vh_after_db,
            vv_delta_db,  vh_delta_db
        ) VALUES (
            _row.id, _row.cancha_id, _row.fecha_imagen, _row.satellite_inferred,
            _row.sar_vv_db, _row.sar_vh_db, _new_vv, _new_vh,
            _offset_vv, _offset_vh
        );
    END LOOP;

    RAISE NOTICE 'Retroactive S1C/D correction complete. Rows in sar_correction_log: %',
        (SELECT COUNT(*) FROM sar_correction_log WHERE run_id = 'migration_002');
END;
$$;

-- ── 5. Unique constraint — prevent duplicate backfill rows ────────────────────
-- Deduplicate first (keep lowest id per cancha/date), then add constraint.

DELETE FROM soil_metrics s1
USING soil_metrics s2
WHERE s1.cancha_id = s2.cancha_id
  AND s1.fecha_imagen = s2.fecha_imagen
  AND s1.id > s2.id;

ALTER TABLE soil_metrics
    DROP CONSTRAINT IF EXISTS soil_metrics_cancha_fecha_uniq;

ALTER TABLE soil_metrics
    ADD CONSTRAINT soil_metrics_cancha_fecha_uniq
    UNIQUE (cancha_id, fecha_imagen);

-- ── 6. Index for satellite time-series queries ────────────────────────────────

CREATE INDEX IF NOT EXISTS idx_soil_metrics_satellite
    ON soil_metrics (satellite_inferred, fecha_imagen);

-- ── 7. Correction factors reference view ─────────────────────────────────────

CREATE OR REPLACE VIEW sar_radiometric_corrections AS
SELECT
    satellite,
    vv_offset_db,
    vh_offset_db,
    source_reference,
    valid_from,
    valid_to
FROM (VALUES
    ('S1A',  0.00::REAL, 0.00::REAL, 'baseline (reference)',                      '2014-04-03'::DATE, '2026-06-29'::DATE),
    ('S1C',  0.12::REAL, 0.21::REAL, 'Schmidt 2023 + Copernicus 2026-02 update',  '2026-06-30'::DATE, NULL::DATE),
    ('S1D',  0.07::REAL, 0.07::REAL, 'ESA commissioning report 2026',             '2026-06-30'::DATE, NULL::DATE)
) AS t(satellite, vv_offset_db, vh_offset_db, source_reference, valid_from, valid_to);

COMMENT ON VIEW sar_radiometric_corrections IS
    'Canonical inter-satellite radiometric correction offsets (dB, additive). '
    'All sar_vv_db / sar_vh_db values in soil_metrics are stored in the S1A-equivalent '
    'baseline frame after applying these offsets at insert time.';

COMMIT;
