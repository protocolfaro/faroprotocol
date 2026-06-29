"""
faro_sar_s1_backfill.py — Backfill SAR calibrado Sentinel-1 GRD → soil_metrics (Amalfitani)

Cascade automático: arranca desde la última fecha en DB, busca en CDSE (lag ~6h)
y en Planetary Computer (lag ~3-6d) para mantener datos siempre actualizados.

sigma0 calibrado: 10*log10((DN/A_cal)^2)
A_cal se lee del XML schema-calibration-{band} de cada escena.
Rango típico sobre césped a 34°S: VV -8 a -15 dB, VH -15 a -22 dB.

API pública:
    run_s1_backfill(days, scene_limit, from_latest, clean_first) -> dict
    delete_wrong_backfill()                                        -> int
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
_BBOX       = (-58.535, -34.645, -58.515, -34.625)
_FUENTE_OLD = "Sentinel-1 GRD · Planetary Computer · 20m · VV+VH"
_FUENTE     = "Sentinel-1 GRD · PC · sigma0-cal · VV+VH"

# ── Planetary Computer ────────────────────────────────────────────────────────
_PC_STAC = "https://planetarycomputer.microsoft.com/api/stac/v1"
_S1_COLL = "sentinel-1-grd"
_PC_SAS  = f"https://planetarycomputer.microsoft.com/api/sas/v1/token/{_S1_COLL}"

# ── CDSE (Copernicus Data Space Ecosystem) — lag ~6h vs ~3-6d PC ─────────────
_CDSE_TOKEN_URL = "https://identity.dataspace.copernicus.eu/auth/realms/CDSE/protocol/openid-connect/token"
_CDSE_ODATA     = "https://catalogue.dataspace.copernicus.eu/odata/v1/Products"
_CDSE_EODATA    = "https://eodata.dataspace.copernicus.eu"


# ══════════════════════════════════════════════════════════════════════════════
# Supabase — cascade: leer última fecha en DB
# ══════════════════════════════════════════════════════════════════════════════

def _get_latest_scene_date() -> Optional[date]:
    """
    Retorna la fecha más reciente en soil_metrics para Amalfitani.
    Usado como punto de inicio del cascade search.
    """
    base = os.environ.get("SUPABASE_URL", "").rstrip("/")
    key  = os.environ.get("SUPABASE_KEY", "")
    if not base or not key:
        return None
    try:
        url    = f"{base}/rest/v1/soil_metrics"
        hdrs   = {"apikey": key, "Authorization": f"Bearer {key}"}
        params = {
            "select":    "fecha_imagen",
            "cancha_id": "eq.amalfitani",
            "order":     "fecha_imagen.desc",
            "limit":     "1",
        }
        r = requests.get(url, headers=hdrs, params=params, timeout=10)
        if r.ok:
            rows = r.json()
            if rows and rows[0].get("fecha_imagen"):
                return date.fromisoformat(rows[0]["fecha_imagen"][:10])
        return None
    except Exception as exc:
        log.debug("_get_latest_scene_date: %s", exc)
        return None


# ══════════════════════════════════════════════════════════════════════════════
# Planetary Computer (fuente histórica, lag 3-6d)
# ══════════════════════════════════════════════════════════════════════════════

def _get_sas_token() -> str:
    try:
        r = requests.get(_PC_SAS, timeout=12)
        return r.json().get("token", "") if r.ok else ""
    except Exception as exc:
        log.warning("PC SAS token: %s", exc)
        return ""


def _search_s1_pc(start: date, end: date, limit: int) -> list[dict]:
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
        log.error("PC STAC search: %s", exc)
        return []


def _read_calibration_a_cal(item: dict, band: str, token: str,
                              col_off: int, col_end: int) -> Optional[float]:
    cal_key  = f"schema-calibration-{band}"
    cal_href = item.get("assets", {}).get(cal_key, {}).get("href", "")
    if not cal_href:
        return None
    if token:
        cal_href = f"{cal_href}?{token}"
    try:
        r = requests.get(cal_href, timeout=15)
        r.raise_for_status()
        return _parse_a_cal_xml(r.text, col_off, col_end)
    except Exception as exc:
        log.debug("PC calibration XML %s: %s", band, exc)
        return None


def _parse_a_cal_xml(xml_text: str, col_off: int, col_end: int) -> Optional[float]:
    try:
        root = ET.fromstring(xml_text)
        cols_target  = np.arange(col_off, col_end, dtype=float)
        a_cal_values: list[float] = []
        for vec in root.findall(".//{*}calibrationVector"):
            pixel_el = vec.find("{*}pixel") or vec.find("pixel")
            sigma_el = vec.find("{*}sigmaNought") or vec.find("sigmaNought")
            if pixel_el is None or sigma_el is None:
                continue
            if not pixel_el.text or not sigma_el.text:
                continue
            pixels = np.array([float(p) for p in pixel_el.text.split()])
            sigmas = np.array([float(s) for s in sigma_el.text.split()])
            a_cal_values.extend(np.interp(cols_target, pixels, sigmas).tolist())
        return float(np.mean(a_cal_values)) if a_cal_values else None
    except Exception as exc:
        log.debug("parse_a_cal_xml: %s", exc)
        return None


def _read_sigma0_db_pc(item: dict, band: str, token: str) -> Optional[float]:
    """Sigma0 calibrado en dB vía Planetary Computer (windowed COG read)."""
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
            return None

        a_cal = _read_calibration_a_cal(item, band, token, col_off, col_end)
        if a_cal is None or a_cal <= 0:
            log.warning("PC %s: sin A_cal — escena omitida", band)
            return None

        sigma0_linear = np.mean((data[valid] / a_cal) ** 2)
        return round(10.0 * math.log10(max(1e-12, float(sigma0_linear))), 2)

    except Exception as exc:
        log.debug("PC sigma0 %s: %s", band, exc)
        return None


# ══════════════════════════════════════════════════════════════════════════════
# CDSE (fuente primaria, lag ~6h — requiere cuenta en dataspace.copernicus.eu)
# ══════════════════════════════════════════════════════════════════════════════

def _cdse_token() -> Optional[str]:
    """
    OAuth2 password grant → access token para CDSE.
    Vars: COPERNICUS_USER / COPERNICUS_PASS
    Registrar cuenta gratis en https://dataspace.copernicus.eu/register
    """
    user = os.environ.get("COPERNICUS_USER", "")
    pwd  = os.environ.get("COPERNICUS_PASS", "")
    if not user or not pwd:
        return None
    try:
        r = requests.post(_CDSE_TOKEN_URL, data={
            "client_id":  "cdse-public",
            "username":   user,
            "password":   pwd,
            "grant_type": "password",
        }, timeout=15)
        if r.ok:
            return r.json().get("access_token")
        log.warning("CDSE auth %s: %s", r.status_code, r.text[:120])
        return None
    except Exception as exc:
        log.warning("CDSE token: %s", exc)
        return None


def _cdse_build_paths(product_name: str, acq_date: str, band: str) -> tuple[str, str]:
    """
    Construye URLs HTTPS para el TIF de medición y el XML de calibración en CDSE eodata.
    Estructura SAFE: measurement/s1x-iw-grd-{pol}-{tstart}-{tstop}-{orbit}-{mission}-001.tiff
                     annotation/calibration/calibration-s1x-iw-grd-{pol}-...-001.xml
    """
    dt   = date.fromisoformat(acq_date)
    safe = product_name if product_name.endswith(".SAFE") else product_name + ".SAFE"
    nm   = safe.replace(".SAFE", "")
    p    = nm.split("_")
    platform = p[0].lower()                              # s1a / s1c / s1d
    pol      = band.lower()                              # vv / vh
    t_start  = p[4].lower() if len(p) > 4 else ""
    t_stop   = p[5].lower() if len(p) > 5 else ""
    orbit    = p[6].lower() if len(p) > 6 else ""
    mission  = p[7].lower() if len(p) > 7 else ""
    pol_idx  = "001" if pol == "vv" else "002"

    stem     = f"{platform}-iw-grd-{pol}-{t_start}-{t_stop}-{orbit}-{mission}-{pol_idx}"
    base_url = (
        f"{_CDSE_EODATA}/Sentinel-1/SAR/GRD/"
        f"{dt.year}/{dt.month:02d}/{dt.day:02d}/{safe}"
    )
    meas_url = f"{base_url}/measurement/{stem}.tiff"
    cal_url  = f"{base_url}/annotation/calibration/calibration-{stem}.xml"
    return meas_url, cal_url


def _search_cdse(start: date, end: date) -> list[dict]:
    """
    Búsqueda anónima en CDSE OData — no requiere auth.
    Retorna lista de {id, name, date} para escenas S1 GRD sobre Amalfitani.
    """
    minx, miny, maxx, maxy = _BBOX
    lon_c = (minx + maxx) / 2
    lat_c = (miny + maxy) / 2
    filt = (
        "Collection/Name eq 'SENTINEL-1' "
        "and Attributes/OData.CSC.StringAttribute/any(att:att/Name eq 'productType' "
        "and att/OData.CSC.StringAttribute/Value eq 'GRD') "
        f"and OData.CSC.Intersects(area=geography'SRID=4326;POINT({lon_c} {lat_c})') "
        f"and ContentDate/Start gt {start.isoformat()}T00:00:00.000Z "
        f"and ContentDate/Start lt {end.isoformat()}T23:59:59.000Z"
    )
    try:
        r = requests.get(_CDSE_ODATA, params={
            "$filter":  filt,
            "$orderby": "ContentDate/Start asc",
            "$top":     "20",
        }, timeout=20)
        if r.ok:
            raw = r.json().get("value", [])
            scenes = []
            for x in raw:
                dt_str = (x.get("ContentDate", {}).get("Start") or "")[:10]
                if dt_str:
                    scenes.append({
                        "id":   x.get("Id", ""),
                        "name": x.get("Name", ""),
                        "date": dt_str,
                    })
            return scenes
        log.warning("CDSE OData %s: %s", r.status_code, r.text[:120])
        return []
    except Exception as exc:
        log.warning("CDSE search: %s", exc)
        return []


def _read_sigma0_db_cdse(product_name: str, acq_date: str,
                          band: str, access_token: str) -> Optional[float]:
    """
    Sigma0 calibrado vía CDSE HTTPS + GDAL Bearer token (HTTP range requests).
    No requiere STS ni boto3 — usa GDAL_HTTP_AUTH directamente sobre eodata.dataspace.copernicus.eu.
    """
    try:
        import rasterio
        from rasterio.windows import Window
        from rasterio.env import Env as RioEnv

        meas_url, cal_url = _cdse_build_paths(product_name, acq_date, band)
        log.debug("CDSE meas: %s", meas_url)

        gdal_env = {
            "GDAL_HTTP_AUTH":                   "BEARER",
            "GDAL_HTTP_BEARER":                 access_token,
            "GDAL_DISABLE_READDIR_ON_OPEN":     "EMPTY_DIR",
            "CPL_VSIL_CURL_ALLOWED_EXTENSIONS": ".tiff,.tif",
            "GDAL_HTTP_MAX_RETRY":              "2",
            "GDAL_HTTP_RETRY_DELAY":            "1",
        }

        # Bbox aproximado para swath IW sobre Buenos Aires (~250km × 170km)
        center_lon, center_lat = -58.525, -34.635
        i_lon0 = center_lon - 1.5
        i_lat0 = center_lat - 1.0
        i_lon1 = center_lon + 1.5
        i_lat1 = center_lat + 1.0
        minx, miny, maxx, maxy = _BBOX

        with RioEnv(**gdal_env):
            with rasterio.open(meas_url) as src:
                H, W = src.shape
                col_off = max(0, int(((minx - i_lon0) / (i_lon1 - i_lon0)) * W))
                col_end = min(W, max(col_off + 20, int(((maxx - i_lon0) / (i_lon1 - i_lon0)) * W)))
                row_off = max(0, int(((i_lat1 - maxy) / (i_lat1 - i_lat0)) * H))
                row_end = min(H, max(row_off + 20, int(((i_lat1 - miny) / (i_lat1 - i_lat0)) * H)))
                win  = Window(col_off, row_off, col_end - col_off, row_end - row_off)
                data = src.read(1, window=win).astype("float64")

        valid = (data > 0) & np.isfinite(data)
        if valid.sum() < 4:
            log.debug("CDSE %s: pocos pixels validos (%d)", band, int(valid.sum()))
            return None

        # Leer calibración via Bearer requests (XML pequeño, ~500KB)
        hdrs   = {"Authorization": f"Bearer {access_token}"}
        cal_r  = requests.get(cal_url, headers=hdrs, timeout=25)
        if not cal_r.ok:
            log.warning("CDSE cal XML %s HTTP %s: %s", band, cal_r.status_code, cal_url[-60:])
            return None

        a_cal = _parse_a_cal_xml(cal_r.text, col_off, col_end)
        if a_cal is None or a_cal <= 0:
            log.warning("CDSE %s: sin A_cal para %s", band, product_name[:40])
            return None

        sigma0_linear = np.mean((data[valid] / a_cal) ** 2)
        db = round(10.0 * math.log10(max(1e-12, float(sigma0_linear))), 2)
        log.debug("CDSE %s %s: VV=%.2f dB (A_cal=%.1f)", acq_date, band, db, a_cal)
        return db

    except Exception as exc:
        log.debug("CDSE sigma0 HTTPS %s/%s: %s", product_name[:30], band, exc)
        return None


# ══════════════════════════════════════════════════════════════════════════════
# Limpiar filas con fórmula incorrecta
# ══════════════════════════════════════════════════════════════════════════════

def delete_wrong_backfill() -> int:
    base = os.environ.get("SUPABASE_URL", "").rstrip("/")
    key  = os.environ.get("SUPABASE_KEY", "")
    if not base or not key:
        return -1
    url  = f"{base}/rest/v1/soil_metrics"
    hdrs = {
        "apikey": key, "Authorization": f"Bearer {key}",
        "Content-Type": "application/json", "Prefer": "return=representation",
    }
    try:
        r = requests.delete(url, headers=hdrs, params={"fuente": f"eq.{_FUENTE_OLD}"}, timeout=15)
        if r.status_code in (200, 204):
            try:    count = len(r.json()) if r.text.strip() else 0
            except: count = 0
            log.info("delete_wrong_backfill: %s filas borradas", count)
            return count
        return -1
    except Exception as exc:
        log.warning("delete_wrong_backfill: %s", exc)
        return -1


# ══════════════════════════════════════════════════════════════════════════════
# Backfill principal — cascade automático CDSE → PC
# ══════════════════════════════════════════════════════════════════════════════

def run_s1_backfill(days: int = 30, scene_limit: int = 20,
                    clean_first: bool = False,
                    from_latest: bool = True) -> dict:
    """
    Cascade SAR automático: siempre parte de la última fecha en DB.

    Estrategia:
      1. Consulta la última fecha en soil_metrics (cascade desde ahí)
      2. Intenta CDSE primero (lag ~6h, más reciente) — requiere COPERNICUS_USER/PASS válidos
      3. Fallback a Planetary Computer (lag ~3-6d) para lo que CDSE no cubrió
      4. Inserta escenas nuevas en soil_metrics

    Args:
        days:        fallback si no hay fecha en DB (buscar últimos N días)
        scene_limit: máximo de escenas a procesar por fuente
        clean_first: borra filas con fórmula incorrecta antes de empezar
        from_latest: True = cascade desde última fecha en DB (recomendado)
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

    # ── Calcular ventana de búsqueda ─────────────────────────────────────────
    end_dt = date.today()

    if from_latest:
        latest = _get_latest_scene_date()
        if latest:
            days_gap = (end_dt - latest).days
            start_dt = latest - timedelta(days=1)   # 1 día overlap para no perder nada
            log.info("Cascade desde %s (%d dias de gap)", latest, days_gap)
        else:
            start_dt = end_dt - timedelta(days=days)
            log.info("Sin datos en DB — buscando ultimos %d dias", days)
    else:
        start_dt = end_dt - timedelta(days=days)

    log.info("Ventana busqueda: %s → %s", start_dt, end_dt)

    # ── Rastrear fechas ya procesadas en esta sesión ──────────────────────────
    seen: set[str]     = set()
    vv_values: list[float] = []
    inserted = errors = skipped = 0

    # ══ FASE 1: CDSE (más reciente — lag ~6h, HTTPS Bearer) ══════════════════
    cdse_token = _cdse_token()
    cdse_dates_inserted: set[str] = set()

    if cdse_token:
        cdse_scenes = _search_cdse(start_dt, end_dt)
        log.info("CDSE: %d escenas encontradas (%s→%s)", len(cdse_scenes), start_dt, end_dt)

        for scene in cdse_scenes:
            scene_dt   = scene["date"]
            product_nm = scene["name"]

            if not scene_dt or scene_dt in seen:
                skipped += 1
                continue

            vv_db = _read_sigma0_db_cdse(product_nm, scene_dt, "vv", cdse_token)
            vh_db = _read_sigma0_db_cdse(product_nm, scene_dt, "vh", cdse_token)

            if vv_db is None and vh_db is None:
                log.warning("CDSE %s: sin sigma0 — PC lo intentara cuando esté disponible", scene_dt)
                # NO añadir a seen: dejar que PC intente (llegará en 3-6d)
                errors += 1
                continue

            seen.add(scene_dt)   # marcar solo si sigma0 fue exitoso
            log.info("CDSE %s: VV=%s dB  VH=%s dB", scene_dt, vv_db, vh_db)
            if vv_db is not None:
                vv_values.append(vv_db)

            ok = vs.insert_soil_metrics(
                venue_id=_VENUE_ID, cancha_id=_CANCHA_ID,
                sar_vv_db=vv_db, sar_vh_db=vh_db,
                fuente=_FUENTE, fecha_imagen=scene_dt,
            )
            if ok:
                inserted += 1
                cdse_dates_inserted.add(scene_dt)
            else:
                errors += 1
    else:
        log.info("CDSE: sin credenciales validas — usando solo PC")

    # ══ FASE 2: Planetary Computer (fallback + histórico) ════════════════════
    pc_scenes = _search_s1_pc(start_dt, end_dt, limit=scene_limit)
    log.info("PC: %d escenas encontradas", len(pc_scenes))

    if pc_scenes:
        token = _get_sas_token()

        for item in pc_scenes:
            props    = item.get("properties", {})
            scene_dt = (props.get("datetime") or "")[:10]

            if not scene_dt or scene_dt in seen:
                skipped += 1
                continue
            seen.add(scene_dt)

            vv_db = _read_sigma0_db_pc(item, "vv", token)
            vh_db = _read_sigma0_db_pc(item, "vh", token)

            if vv_db is None and vh_db is None:
                log.warning("PC %s: sin sigma0 sobre bbox", scene_dt)
                errors += 1
                continue

            log.info("PC %s: VV=%s dB  VH=%s dB", scene_dt, vv_db, vh_db)
            if vv_db is not None:
                vv_values.append(vv_db)

            ok = vs.insert_soil_metrics(
                venue_id=_VENUE_ID, cancha_id=_CANCHA_ID,
                sar_vv_db=vv_db, sar_vh_db=vh_db,
                fuente=_FUENTE, fecha_imagen=scene_dt,
            )
            if ok:
                inserted += 1
            else:
                errors += 1

    rango = f"VV [{min(vv_values):.2f}, {max(vv_values):.2f}] dB" if vv_values else "sin datos VV nuevos"
    msg   = f"Cascade S1: {inserted} escenas nuevas. CDSE={len(cdse_dates_inserted)} PC={inserted-len(cdse_dates_inserted)}. {rango}"
    log.info("%s (err=%d skip=%d del=%d)", msg, errors, skipped, deleted)

    return {
        "ok":               True,
        "start":            start_dt.isoformat(),
        "end":              end_dt.isoformat(),
        "escenas_nuevas":   inserted,
        "cdse_insertadas":  len(cdse_dates_inserted),
        "pc_insertadas":    inserted - len(cdse_dates_inserted),
        "errores":          errors,
        "skipped":          skipped,
        "deleted_wrong":    deleted,
        "vv_range_db":      rango,
        "msg":              msg,
    }
