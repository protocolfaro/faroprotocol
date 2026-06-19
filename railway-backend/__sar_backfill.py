"""
__sar_backfill.py — CLI local para backfill SAR real Sentinel-1 → soil_metrics

Wrapper de faro_sar_s1_backfill.run_s1_backfill() para correr localmente.
En Railway usar el endpoint POST /velez/sar-backfill.

Uso:
    python3 railway-backend/__sar_backfill.py              # dry-run (no inserta)
    python3 railway-backend/__sar_backfill.py --write      # inserta en Supabase
    python3 railway-backend/__sar_backfill.py --days 30    # últimos 30 días
    python3 railway-backend/__sar_backfill.py --limit 5    # máximo 5 escenas

Requiere env: SUPABASE_URL, SUPABASE_KEY
"""

import argparse, json, logging, math, os, sys
from datetime import date, timedelta
from typing import Optional

import requests

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("sar_backfill")

# ── Configuración ─────────────────────────────────────────────────────────────
_VENUE_ID  = "amalfitani"
_CANCHA_ID = "amalfitani"
_BBOX      = (-58.535, -34.645, -58.515, -34.625)
_FUENTE    = "Sentinel-1 GRD · Planetary Computer · 20m · VV+VH"
_PC_STAC   = "https://planetarycomputer.microsoft.com/api/stac/v1"
_S1_COLL   = "sentinel-1-grd"
_PC_SAS    = f"https://planetarycomputer.microsoft.com/api/sas/v1/token/{_S1_COLL}"


def _get_sas_token() -> str:
    try:
        r = requests.get(_PC_SAS, timeout=12)
        return r.json().get("token", "") if r.ok else ""
    except Exception as exc:
        log.warning("SAS token: %s", exc)
        return ""


def _search_scenes(start: date, end: date, limit: int) -> list[dict]:
    minx, miny, maxx, maxy = _BBOX
    payload = {
        "collections": [_S1_COLL],
        "bbox":        [minx, miny, maxx, maxy],
        "datetime":    f"{start.isoformat()}T00:00:00Z/{end.isoformat()}T23:59:59Z",
        "limit":       limit,
        "sortby":      [{"field": "datetime", "direction": "asc"}],
    }
    try:
        r = requests.post(f"{_PC_STAC}/search", json=payload, timeout=25)
        r.raise_for_status()
        return r.json().get("features", [])
    except Exception as exc:
        log.error("STAC search: %s", exc)
        return []


def _read_band_db(item: dict, band: str, token: str) -> Optional[float]:
    try:
        import rasterio, numpy as np
        from rasterio.windows import Window
        href = item.get("assets", {}).get(band, {}).get("href", "")
        if not href:
            return None
        if token:
            href = f"{href}?{token}"
        i_lon0, i_lat0, i_lon1, i_lat1 = item.get("bbox", [0, 0, 1, 1])
        minx, miny, maxx, maxy = _BBOX
        with rasterio.open(href) as src:
            H, W = src.shape
            col_off = max(0, int(((minx - i_lon0) / (i_lon1 - i_lon0)) * W))
            col_end = min(W, max(col_off + 20, int(((maxx - i_lon0) / (i_lon1 - i_lon0)) * W)))
            row_off = max(0, int(((i_lat1 - maxy) / (i_lat1 - i_lat0)) * H))
            row_end = min(H, max(row_off + 20, int(((i_lat1 - miny) / (i_lat1 - i_lat0)) * H)))
            data = src.read(1, window=Window(col_off, row_off, col_end - col_off, row_end - row_off)).astype("float32")
        valid = (data > 0) & np.isfinite(data)
        if valid.sum() < 4:
            return None
        return float(round(10.0 * math.log10(max(1e-9, float(np.mean(data[valid])))), 2))
    except Exception as exc:
        log.debug("read_band %s: %s", band, exc)
        return None


def _insert(row: dict) -> bool:
    base = os.environ.get("SUPABASE_URL", "").rstrip("/")
    key  = os.environ.get("SUPABASE_KEY", "")
    if not base or not key:
        log.error("SUPABASE_URL / SUPABASE_KEY no configurados")
        return False
    hdrs = {"apikey": key, "Authorization": f"Bearer {key}",
            "Content-Type": "application/json", "Prefer": "return=minimal"}
    try:
        r = requests.post(f"{base}/rest/v1/soil_metrics", headers=hdrs,
                          data=json.dumps(row, default=str), timeout=10)
        if r.status_code in (200, 201, 204):
            return True
        log.warning("Supabase HTTP %s: %s", r.status_code, r.text[:200])
    except Exception as exc:
        log.warning("Supabase: %s", exc)
    return False


def run_backfill(days: int = 180, scene_limit: int = 60, dry_run: bool = True) -> None:
    end_dt, start_dt = date.today(), date.today() - timedelta(days=days)
    log.info("=== SAR Backfill Amalfitani | %s → %s | dry_run=%s ===",
             start_dt, end_dt, dry_run)

    scenes = _search_scenes(start_dt, end_dt, limit=scene_limit)
    log.info("Escenas encontradas: %d", len(scenes))
    if not scenes:
        return

    token    = _get_sas_token()
    inserted = errors = skipped = 0
    seen: set[str] = set()

    for i, item in enumerate(scenes, 1):
        props    = item.get("properties", {})
        scene_dt = (props.get("datetime") or "")[:10]
        if not scene_dt or scene_dt in seen:
            skipped += 1
            continue
        seen.add(scene_dt)

        log.info("[%d/%d] %s  %s", i, len(scenes), scene_dt, item.get("id",""))
        vv = _read_band_db(item, "vv", token)
        vh = _read_band_db(item, "vh", token)

        if vv is None and vh is None:
            log.warning("  → sin datos — skip")
            errors += 1
            continue

        log.info("  → VV=%s dB  VH=%s dB", vv, vh)
        row = {"venue_id": _VENUE_ID, "cancha_id": _CANCHA_ID,
               "is_hibrido": False, "sar_vv_db": vv, "sar_vh_db": vh,
               "fuente": _FUENTE, "fecha_imagen": scene_dt}

        if dry_run:
            log.info("  [DRY-RUN] %s", json.dumps(row, default=str))
            inserted += 1
        elif _insert(row):
            log.info("  ✓ OK")
            inserted += 1
        else:
            log.warning("  ✗ Error")
            errors += 1

    log.info("=== Resultado: %d insertadas | %d errores | %d skipped ===",
             inserted, errors, skipped)
    if dry_run:
        log.info("(dry-run — nada escrito en Supabase)")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--days",  type=int, default=180)
    ap.add_argument("--limit", type=int, default=60)
    args = ap.parse_args()
    run_backfill(days=args.days, scene_limit=args.limit, dry_run=not args.write)
