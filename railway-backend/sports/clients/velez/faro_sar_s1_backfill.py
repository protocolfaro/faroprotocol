"""
faro_sar_s1_backfill.py — Backfill SAR calibrado Sentinel-1 GRD → soil_metrics (Amalfitani)

sigma0 calibrado: 10*log10((DN/A_cal)^2) = 20*log10(DN) - 20*log10(A_cal)
A_cal se lee del XML schema-calibration-{band} de cada escena (asset PC STAC).
Rango típico sobre césped/urbano a 34°S: VV -8 a -15 dB, VH -15 a -22 dB.

API pública:
    run_s1_backfill(days, scene_limit) -> dict
    delete_wrong_backfill()            -> int  (filas borradas)
"""

from __future__ import annotations
import logging, math, os, sys, xml.etree.ElementTree as ET
from datetime import date, timedelta
from typing import Optional

import numpy as np
import requests

log = logging.getLogger(__name__)

# ── Bbox Estadio Amalfitani ───────────────────────────────────────────────────
_VENUE_ID   = "amalfitani"
_CANCHA_ID  = "amalfitani"
_BBOX       = (-58.535, -34.645, -58.515, -34.625)   # (lon_min, lat_min, lon_max, lat_max)
_FUENTE_OLD = "Sentinel-1 GRD · Planetary Computer · 20m · VV+VH"   # string incorrecto
_FUENTE     = "Sentinel-1 GRD · PC · sigma0-cal · VV+VH"             # string correcto

# ── Planetary Computer ────────────────────────────────────────────────────────
_PC_STAC = "https://planetarycomputer.microsoft.com/api/stac/v1"
_S1_COLL = "sentinel-1-grd"
_PC_SAS  = f"https://planetarycomputer.microsoft.com/api/sas/v1/token/{_S1_COLL}"


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


def _read_calibration_a_cal(item: dict, band: str, token: str,
                              col_off: int, col_end: int) -> Optional[float]:
    """
    Lee A_cal (sigmaNought) del XML de calibración de la escena.
    Interpola en la posición de rango (columna) que corresponde al bbox.

    Returns: A_cal promedio sobre el rango de columnas del bbox.
    """
    cal_key  = f"schema-calibration-{band}"
    cal_href = item.get("assets", {}).get(cal_key, {}).get("href", "")
    if not cal_href:
        return None
    if token:
        cal_href = f"{cal_href}?{token}"

    try:
        r = requests.get(cal_href, timeout=15)
        r.raise_for_status()
        root = ET.fromstring(r.text)

        # calibrationVector tiene pixel[] y sigmaNought[] por línea de azimut
        cols_target = np.arange(col_off, col_end, dtype=float)
        a_cal_values: list[float] = []

        for vec in root.findall(".//{*}calibrationVector"):
            pixel_el = vec.find("{*}pixel")
            sigma_el = vec.find("{*}sigmaNought")
            if pixel_el is None or sigma_el is None:
                pixel_el = vec.find("pixel")
                sigma_el = vec.find("sigmaNought")
            if pixel_el is None or sigma_el is None or not pixel_el.text or not sigma_el.text:
                continue

            pixels = np.array([float(p) for p in pixel_el.text.split()])
            sigmas = np.array([float(s) for s in sigma_el.text.split()])

            # Interpolar sigmaNought en las columnas de nuestro bbox
            a_interp = np.interp(cols_target, pixels, sigmas)
            a_cal_values.extend(a_interp.tolist())

        if not a_cal_values:
            return None

        a_cal = float(np.mean(a_cal_values))
        log.debug("A_cal %s: %.2f (n=%d cols=[%d,%d])", band, a_cal, len(a_cal_values), col_off, col_end)
        return a_cal

    except Exception as exc:
        log.debug("calibration XML %s: %s", band, exc)
        return None


def _read_sigma0_db(item: dict, band: str, token: str) -> Optional[float]:
    """
    Lee sigma0 calibrado en dB sobre el bbox de Amalfitani.

    Formula: sigma0_dB = 10 * log10( mean((DN / A_cal)^2) )
                       = 20 * log10(DN_rms) - 20 * log10(A_cal)

    A_cal se lee del XML schema-calibration-{band} de la escena.
    Rango esperado sobre cesped/urbano: VV -8 a -15 dB, VH -15 a -22 dB.
    """
    try:
        import rasterio
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

            win  = Window(col_off, row_off, col_end - col_off, row_end - row_off)
            data = src.read(1, window=win).astype("float64")

        valid = (data > 0) & np.isfinite(data)
        if valid.sum() < 4:
            log.debug("%s: pocos pixels validos (%d)", band, int(valid.sum()))
            return None

        # Leer A_cal del XML de calibracion para las columnas de nuestro bbox
        a_cal = _read_calibration_a_cal(item, band, token, col_off, col_end)
        if a_cal is None or a_cal <= 0:
            log.warning("%s: no se pudo leer A_cal del XML — escena omitida", band)
            return None

        # sigma0 calibrado: mean de las potencias individuales, luego dB
        sigma0_linear = np.mean((data[valid] / a_cal) ** 2)
        sigma0_db     = 10.0 * math.log10(max(1e-12, float(sigma0_linear)))

        log.debug("%s: A_cal=%.2f DN_mean=%.1f sigma0=%.2f dB", band, a_cal, float(data[valid].mean()), sigma0_db)
        return round(sigma0_db, 2)

    except Exception as exc:
        log.debug("sigma0 %s: %s", band, exc)
        return None


# ── Borrar filas con formula incorrecta ──────────────────────────────────────

def delete_wrong_backfill() -> int:
    """
    Borra las filas de soil_metrics insertadas con la formula incorrecta
    (fuente = _FUENTE_OLD). Retorna el numero de filas borradas, o -1 si error.
    """
    base = os.environ.get("SUPABASE_URL", "").rstrip("/")
    key  = os.environ.get("SUPABASE_KEY", "")
    if not base or not key:
        log.error("delete_wrong_backfill: SUPABASE_URL/KEY no configurados")
        return -1

    url  = f"{base}/rest/v1/soil_metrics"
    hdrs = {
        "apikey":        key,
        "Authorization": f"Bearer {key}",
        "Content-Type":  "application/json",
        "Prefer":        "return=representation",
    }
    params = {"fuente": f"eq.{_FUENTE_OLD}"}

    try:
        r = requests.delete(url, headers=hdrs, params=params, timeout=15)
        if r.status_code in (200, 204):
            try:
                rows = r.json()
                count = len(rows) if isinstance(rows, list) else -1
            except Exception:
                count = 0  # 204 no content
            log.info("delete_wrong_backfill: %s filas borradas (HTTP %s)", count, r.status_code)
            return count
        log.warning("delete_wrong_backfill HTTP %s: %s", r.status_code, r.text[:200])
        return -1
    except Exception as exc:
        log.warning("delete_wrong_backfill: %s", exc)
        return -1


# ── Backfill principal ────────────────────────────────────────────────────────

def run_s1_backfill(days: int = 180, scene_limit: int = 60,
                    clean_first: bool = False) -> dict:
    """
    Backfill Sentinel-1 GRD sigma0 calibrado → soil_metrics para Amalfitani.

    Si clean_first=True, borra primero las filas con la formula incorrecta.
    Usa velez_supabase.insert_soil_metrics() con las vars de entorno activas.
    """
    _here = os.path.dirname(os.path.abspath(__file__))
    if _here not in sys.path:
        sys.path.insert(0, _here)
    try:
        import velez_supabase as vs
    except ImportError as exc:
        return {"ok": False, "error": str(exc)}

    deleted = 0
    if clean_first:
        deleted = delete_wrong_backfill()
        log.info("Limpieza previa: %s filas borradas", deleted)

    end_dt   = date.today()
    start_dt = end_dt - timedelta(days=days)

    log.info("S1 backfill sigma0-cal: %s -> %s (dias=%d limite=%d)", start_dt, end_dt, days, scene_limit)

    scenes = _search_s1(start_dt, end_dt, limit=scene_limit)
    log.info("S1 backfill: %d escenas encontradas", len(scenes))
    if not scenes:
        return {"ok": True, "escenas_procesadas": 0, "insertadas": 0,
                "errores": 0, "skipped": 0, "deleted": deleted,
                "msg": "Sin escenas disponibles"}

    token    = _get_sas_token()
    inserted = errors = skipped = 0
    seen: set[str] = set()
    vv_values: list[float] = []

    for item in scenes:
        props    = item.get("properties", {})
        scene_dt = (props.get("datetime") or "")[:10]

        if not scene_dt or scene_dt in seen:
            skipped += 1
            continue
        seen.add(scene_dt)

        vv_db = _read_sigma0_db(item, "vv", token)
        vh_db = _read_sigma0_db(item, "vh", token)

        if vv_db is None and vh_db is None:
            log.warning("S1 %s: sin sigma0 sobre bbox", scene_dt)
            errors += 1
            continue

        log.info("S1 %s: VV=%s dB  VH=%s dB", scene_dt, vv_db, vh_db)
        if vv_db is not None:
            vv_values.append(vv_db)

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

    rango = (f"VV [{min(vv_values):.2f}, {max(vv_values):.2f}] dB"
             if vv_values else "sin datos VV")
    msg = f"Insertadas {inserted} escenas S1 sigma0-cal. {rango}"
    log.info("S1 backfill: %s (err=%d skip=%d del=%d)", msg, errors, skipped, deleted)
    return {
        "ok":                True,
        "escenas_procesadas": len(seen),
        "insertadas":        inserted,
        "errores":           errors,
        "skipped":           skipped,
        "deleted_wrong":     deleted,
        "vv_range_db":       rango,
        "msg":               msg,
    }
