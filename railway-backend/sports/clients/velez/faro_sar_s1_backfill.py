"""
faro_sar_s1_backfill.py — Backfill SAR real Sentinel-1 GRD → soil_metrics (Amalfitani)

Descarga backscatter VV y VH de escenas Sentinel-1 GRD vía Planetary Computer STAC,
calcula media dB sobre el bbox del Estadio Amalfitani, e inserta en soil_metrics.

API pública:
    run_s1_backfill(days=180, scene_limit=60) -> dict
        Returns: {ok, escenas_procesadas, insertadas, errores, skipped, msg}
"""

from __future__ import annotations
import logging, math, os, sys
from datetime import date, timedelta
from typing import Optional

import requests

log = logging.getLogger(__name__)

# ── Bbox Estadio Amalfitani — Vélez Sarsfield ────────────────────────────────
_VENUE_ID   = "amalfitani"
_CANCHA_ID  = "amalfitani"
_BBOX       = (-58.535, -34.645, -58.515, -34.625)   # (lon_min, lat_min, lon_max, lat_max)
_FUENTE     = "Sentinel-1 GRD · Planetary Computer · 20m · VV+VH"

# ── Planetary Computer STAC ───────────────────────────────────────────────────
_PC_STAC  = "https://planetarycomputer.microsoft.com/api/stac/v1"
_S1_COLL  = "sentinel-1-grd"
_PC_SAS   = f"https://planetarycomputer.microsoft.com/api/sas/v1/token/{_S1_COLL}"


def _get_sas_token() -> str:
    try:
        r = requests.get(_PC_SAS, timeout=12)
        return r.json().get("token", "") if r.ok else ""
    except Exception as exc:
        log.warning("S1 SAS token: %s", exc)
        return ""


def _search_s1(start: date, end: date, limit: int) -> list[dict]:
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
        log.error("S1 STAC search: %s", exc)
        return []


def _read_band_db(item: dict, band: str, token: str) -> Optional[float]:
    """Mean backscatter en dB sobre el bbox de Amalfitani para la banda dada ('vv'/'vh')."""
    try:
        import rasterio
        import numpy as np
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
            fx0 = (minx - i_lon0) / (i_lon1 - i_lon0)
            fx1 = (maxx - i_lon0) / (i_lon1 - i_lon0)
            fy0 = (i_lat1 - maxy) / (i_lat1 - i_lat0)
            fy1 = (i_lat1 - miny) / (i_lat1 - i_lat0)

            col_off = max(0, int(fx0 * W))
            col_end = min(W, max(col_off + 20, int(fx1 * W)))
            row_off = max(0, int(fy0 * H))
            row_end = min(H, max(row_off + 20, int(fy1 * H)))

            win  = Window(col_off, row_off, col_end - col_off, row_end - row_off)
            data = src.read(1, window=win).astype("float32")

        valid = (data > 0) & np.isfinite(data)
        if valid.sum() < 4:
            return None

        return float(round(10.0 * math.log10(max(1e-9, float(np.mean(data[valid])))), 2))
    except Exception as exc:
        log.debug("read_band_db %s: %s", band, exc)
        return None


def run_s1_backfill(days: int = 180, scene_limit: int = 60) -> dict:
    """
    Backfill Sentinel-1 GRD → soil_metrics para Amalfitani.
    Usa velez_supabase.insert_soil_metrics() con las vars de entorno ya activas.

    Returns dict con ok, escenas_procesadas, insertadas, errores, skipped.
    """
    _here = os.path.dirname(os.path.abspath(__file__))
    if _here not in sys.path:
        sys.path.insert(0, _here)
    try:
        import velez_supabase as vs
    except ImportError as exc:
        return {"ok": False, "error": str(exc)}

    end_dt   = date.today()
    start_dt = end_dt - timedelta(days=days)

    log.info("S1 backfill: %s → %s (días=%d límite=%d)", start_dt, end_dt, days, scene_limit)

    scenes = _search_s1(start_dt, end_dt, limit=scene_limit)
    log.info("S1 backfill: %d escenas encontradas", len(scenes))
    if not scenes:
        return {"ok": True, "escenas_procesadas": 0, "insertadas": 0,
                "errores": 0, "skipped": 0, "msg": "Sin escenas disponibles"}

    token     = _get_sas_token()
    inserted  = 0
    errors    = 0
    skipped   = 0
    seen: set[str] = set()

    for item in scenes:
        props     = item.get("properties", {})
        scene_dt  = (props.get("datetime") or props.get("start_datetime") or "")[:10]

        if not scene_dt or scene_dt in seen:
            skipped += 1
            continue
        seen.add(scene_dt)

        vv_db = _read_band_db(item, "vv", token)
        vh_db = _read_band_db(item, "vh", token)

        if vv_db is None and vh_db is None:
            log.warning("S1 backfill: sin datos sobre bbox — %s", item.get("id"))
            errors += 1
            continue

        log.info("S1 backfill: %s VV=%s VH=%s dB", scene_dt, vv_db, vh_db)

        ok = vs.insert_soil_metrics(
            venue_id     = _VENUE_ID,
            cancha_id    = _CANCHA_ID,
            sar_vv_db    = vv_db,
            sar_vh_db    = vh_db,
            fuente       = _FUENTE,
            fecha_imagen = scene_dt,
        )
        if ok:
            inserted += 1
        else:
            errors += 1

    msg = f"Insertadas {inserted} escenas S1 reales en soil_metrics"
    log.info("S1 backfill: %s (errores=%d skipped=%d)", msg, errors, skipped)
    return {
        "ok":                True,
        "escenas_procesadas": len(seen),
        "insertadas":        inserted,
        "errores":           errors,
        "skipped":           skipped,
        "msg":               msg,
    }
