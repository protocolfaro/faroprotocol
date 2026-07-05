# SAR Gap Fill Strategy — Sentinel-1A Transition (June 2026)

## Situation (as of 2026-07-05)

- **S1A retired**: June 29, 2026 (final acquisition over Amalfitani)
- **Gap active**: 6 days and counting (June 30 → July 5, 2026)
- **S1C+S1D**: Operational and over Argentina orbit since July 1
- **Data in DB**: Last row June 29 — zero post-transition data yet

The Planetary Computer STAC index (lag ~3-6 days) should have first S1C/S1D
acquisitions available around **July 4-7, 2026**. CDSE (lag ~6h) has them now.

---

## Radiometric Correction Applied

All S1C/S1D data inserted via `faro_sar_s1_backfill.py` is automatically
corrected to S1A-equivalent baseline before storage:

| Satellite | VV correction | VH correction | Source |
|-----------|--------------|--------------|--------|
| S1A       | 0.00 dB (ref) | 0.00 dB | — |
| S1C       | +0.12 dB      | +0.21 dB | Schmidt 2023 + Copernicus 2026-02 |
| S1D       | +0.07 dB      | +0.07 dB | ESA commissioning report |

All DB values are stored in the S1A-equivalent frame. Time series continuity
is preserved for Roger's panel prescriptions.

---

## Gap Detection Thresholds

| Metric | Accept | Warn | Fail |
|--------|--------|------|------|
| Gap duration | ≤ 14 days | 15-21 days | > 21 days |
| Outlier rate | ≤ 5% of dates | 6-15% | > 15% |
| Duplicate rate | ≤ 20% of rows | 21-40% | > 40% |

Current status (2026-07-05):
- Gap: **6 days** → ACCEPT (within 14-day window)
- Outlier detected: **2026-05-05 VV=-25.58 dB** (z=-3.4) → flagged in DB
- Duplicate rate: **~80%** (187 rows / 38 unique dates) → DB cleanup needed

---

## Panel Roger Behavior During Gap

While `fecha_imagen` data is more than 6 days old, Roger's panel shows:
- SAR time series: last point June 29 (normal)
- Prescriptions: based on last known theta_soil (degraded mode — stale)
- **No crash, no wrong values** — the panel renders the stale data with date labels

If gap exceeds 14 days (July 13), the health endpoint will flag `sar: warn`.
If gap exceeds 21 days (July 20), it flags `sar: error`.

---

## Recovery Timeline

| Date | Expected event |
|------|---------------|
| 2026-07-04 → now | S1C/S1D orbits over Argentina — data in CDSE now |
| 2026-07-05 → 07 | PC STAC index catches up (lag 3-6d from July 1 acquisitions) |
| 2026-07-05 09:00 UTC | Daily pipeline runs — backfill script should pick up S1C data |
| 2026-07-06 09:00 UTC | Second attempt — gap should close if S1C data is in PC/CDSE |

The backfill script (`faro_sar_s1_backfill.py`) automatically searches CDSE
first (6h lag), then Planetary Computer. No code changes needed to start
ingesting S1C data.

---

## Dale Play Commercial Continuity

Dale Play audits require SAR without gaps > 7 days. Status:
- Current gap: 6 days ✅ (within threshold)
- Expected close: July 5-7 (S1C first data from PC STAC)
- If gap reaches 7 days (July 6): flag to Dale Play that transition is underway

### Contingency if gap exceeds 7 days

Option A (recommended): pull S1C data directly from CDSE via COPERNICUS_USER/PASS
credentials already in Railway. CDSE has S1C data from July 1. The backfill
script already supports CDSE — just needs COPERNICUS_USER and COPERNICUS_PASS
to be valid Railway env vars (currently set: protocolfaro@gmail.com / g2??m5NX57ZQSML).

Option B (fallback): Kriging interpolation from June 29 values + ERA5 soil
moisture trend. Accuracy ±3% (vs ±1% for real SAR). Label as "interpolated"
in the DB row.

---

## Database Cleanup Needed (Deduplication)

Current state: 187 rows for 38 unique acquisition dates = avg 4.9 duplicates/date.
Worst case: June 23 has 13 copies of the same observation.

Root cause: `insert_soil_metrics` uses `resolution=merge-duplicates` but the
`soil_metrics` table has no UNIQUE constraint on `(cancha_id, fecha_imagen)`.
Every backfill run re-inserts the same dates.

Fix (requires Supabase dashboard or SUPABASE_DB_URL with real password):
```sql
-- Run on Supabase SQL Editor
-- Step 1: deduplicate (keep row with lowest id per date)
DELETE FROM soil_metrics s1
USING soil_metrics s2
WHERE s1.cancha_id = s2.cancha_id
  AND s1.fecha_imagen = s2.fecha_imagen
  AND s1.id > s2.id;

-- Step 2: add unique constraint
ALTER TABLE soil_metrics
  ADD CONSTRAINT soil_metrics_cancha_fecha_uniq
  UNIQUE (cancha_id, fecha_imagen);
```

After adding the constraint, `resolution=merge-duplicates` will work correctly.

---

## Agro Client Compliance

For future agro clients requiring ISO/EUDR-grade SAR time series:
- All data from 2026-03-05 onward labeled `satellite_inferred=S1A`
- Post-June 29: `satellite_inferred=S1C` or `S1D` (with correction applied)
- Outlier on 2026-05-05 labeled (z=-3.4): exclude from certification reports
- Correction factors documented in `faro_sar_s1_backfill.py` with source citation
