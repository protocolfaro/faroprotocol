"""
sar_cancha_fetch.py — Per-cancha Sentinel-1 sigma0 extraction for all Velez facilities.

Extends faro_sar_s1_backfill.py logic to all 12 Villa Olímpica canchas (+ Amalfitani).
Sentinel-1 GRD scenes are searched with the cluster bbox; sigma0 is extracted per-cancha
via windowed rasterio reads (~6–25 pixels per cancha at S1 GRD ~10-20m resolution).

Cascade: CDSE (~6h lag) → Planetary Computer (~3-6d lag).
All inserts use venue_id="amalfitani" to match the existing vegetation_metrics schema.

API:
    fetch_sar_all_canchas(days=7)  → dict[cancha_id, {vv_db, vh_db, fecha, satellite}]
    run_vo_sar_backfill(days=30)   → dict
"""
from __future__ import annotations
import logging, math, os, sys
from datetime import date, timedelta
from typing import Optional

import numpy as np
import requests

log = logging.getLogger(__name__)

_VENUE_ID = "amalfitani"   # matches vegetation_metrics venue_id convention

# Cluster bbox covering all VO canchas (search bbox for S1 scene discovery)
_VO_CLUSTER_BBOX  = (-58.7300, -34.6260, -58.7100, -34.6180)
# Amalfitani sector is handled here too for completeness
_AMALFITANI_BBOX  = (-58.5350, -34.6450, -58.5150, -34.6250)

# VO cancha IDs (excludes Polideportivo Feijóo which has its own bbox set)
_VO_CANCHA_IDS = [
    "1fa", "2fa", "3fa", "4fa", "5fa", "6fa",
    "7fa", "8fa", "9fa", "10fa", "1fp", "2fp",
]

# External endpoints (same as faro_sar_s1_backfill)
_PC_STAC         = "https://planetarycomputer.microsoft.com/api/stac/v1"
_S1_COLL         = "sentinel-1-grd"
_PC_SAS          = f"https://planetarycomputer.microsoft.com/api/sas/v1/token/{_S1_COLL}"
_CDSE_TOKEN_URL  = "https://identity.dataspace.copernicus.eu/auth/realms/CDSE/protocol/openid-connect/token"
_CDSE_ODATA      = "https://catalogue.dataspace.copernicus.eu/odata/v1/Products"
_CDSE_EODATA     = "https://eodata.dataspace.copernicus.eu"


# ── Import helpers from faro_sar_s1_backfill ─────────────────────────────────

def _backfill_mod():
    _here = os.path.dirname(os.path.abspath(__file__))
    if _here not in sys.path:
        sys.path.insert(0, _here)
    import faro_sar_s1_backfill as _m
    return _m


def _cancha_bboxes() -> dict[str, tuple]:
    """
    Runtime import of CANCHA_BBOXES from ndvi_real.py — avoids circular imports.
    Returns dict[cancha_id, (west, south, east, north)].
    """
    try:
        _here = os.path.dirname(os.path.abspath(__file__))
        if _here not in sys.path:
            sys.path.insert(0, _here)
        from ndvi_real import CANCHA_BBOXES
        # Exclude Polideportivo Feijóo from VO backfill
        return {k: v for k, v in CANCHA_BBOXES.items() if not k.startswith("poli_")}
    except Exception as exc:
        log.warning("sar_cancha_fetch: cannot import CANCHA_BBOXES: %s", exc)
        return {"amalfitani": _AMALFITANI_BBOX}


# ── PC STAC helpers ───────────────────────────────────────────────────────────

def _get_sas_token() -> str:
    try:
        r = requests.get(_PC_SAS, timeout=12)
        return r.json().get("token", "") if r.ok else ""
    except Exception as exc:
        log.warning("PC SAS token: %s", exc)
        return ""


def _search_s1_pc_bbox(start: date, end: date,
                        bbox: tuple[float, float, float, float],
                        limit: int = 10) -> list[dict]:
    """Search PC STAC for S1 GRD scenes intersecting bbox."""
    minx, miny, maxx, maxy = bbox
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
        log.warning("PC STAC search: %s", exc)
        return []


def _sigma0_pc_bbox(item: dict, band: str, token: str,
                    cancha_bbox: tuple[float, float, float, float]) -> Optional[float]:
    """
    Extract calibrated sigma0 dB from PC COG for a specific cancha bbox.
    Uses windowed rasterio read; min window = 5 pixels (avoids over-expansion for small canchas).
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
        minx, miny, maxx, maxy = cancha_bbox

        with rasterio.open(href) as src:
            H, W = src.shape
            col_off = max(0, int(((minx - i_lon0) / (i_lon1 - i_lon0)) * W))
            col_end = min(W, max(col_off + 5, int(((maxx - i_lon0) / (i_lon1 - i_lon0)) * W)))
            row_off = max(0, int(((i_lat1 - maxy) / (i_lat1 - i_lat0)) * H))
            row_end = min(H, max(row_off + 5, int(((i_lat1 - miny) / (i_lat1 - i_lat0)) * H)))
            win  = Window(col_off, row_off, col_end - col_off, row_end - row_off)
            data = src.read(1, window=win).astype("float64")

        valid = (data > 0) & np.isfinite(data)
        if valid.sum() < 2:
            return None

        m = _backfill_mod()
        a_cal = m._read_calibration_a_cal(item, band, token, col_off, col_end)
        if a_cal is None or a_cal <= 0:
            return None

        sigma0_lin = np.mean((data[valid] / a_cal) ** 2)
        return round(10.0 * math.log10(max(1e-12, float(sigma0_lin))), 2)

    except Exception as exc:
        log.debug("sigma0_pc_bbox [%s]: %s", band, exc)
        return None


# ── CDSE helpers ──────────────────────────────────────────────────────────────

def _cdse_token() -> Optional[str]:
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
        return r.json().get("access_token") if r.ok else None
    except Exception as exc:
        log.warning("CDSE token: %s", exc)
        return None


def _search_cdse_bbox(start: date, end: date,
                       bbox: tuple[float, float, float, float]) -> list[dict]:
    """Search CDSE OData for S1 GRD scenes covering the bbox center."""
    minx, miny, maxx, maxy = bbox
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
            "$top":     "10",
        }, timeout=20)
        if r.ok:
            raw = r.json().get("value", [])
            return [
                {"id": x.get("Id", ""), "name": x.get("Name", ""),
                 "date": (x.get("ContentDate", {}).get("Start") or "")[:10]}
                for x in raw
                if (x.get("ContentDate", {}).get("Start") or "")[:10]
            ]
        return []
    except Exception as exc:
        log.warning("CDSE search: %s", exc)
        return []


def _sigma0_cdse_s3_bbox(product_name: str, acq_date: str, band: str,
                          cancha_bbox: tuple[float, float, float, float]) -> Optional[float]:
    """
    Windowed sigma0 read from CDSE S3 for a specific cancha bbox.
    Computes scene extent from bbox center ± S1 swath approximation (±1.5°×1.0°).
    """
    s3_key    = os.environ.get("CDSE_S3_KEY", "")
    s3_secret = os.environ.get("CDSE_S3_SECRET", "")
    if not s3_key or not s3_secret:
        return None
    try:
        import rasterio
        from rasterio.windows import Window
        from rasterio.env import Env as RioEnv

        m = _backfill_mod()
        dt = date.fromisoformat(acq_date)
        safe, meas_file, cal_file = m._cdse_file_stems(product_name, band)

        base_s3 = (
            f"/vsis3/{_CDSE_EODATA.replace('https://','')}/Sentinel-1/SAR/GRD/"
            f"{dt.year}/{dt.month:02d}/{dt.day:02d}/{safe}"
        )
        meas_path = f"{base_s3}/measurement/{meas_file}"
        cal_path  = f"{base_s3}/annotation/calibration/{cal_file}"

        gdal_env = {
            "AWS_ACCESS_KEY_ID":            s3_key,
            "AWS_SECRET_ACCESS_KEY":        s3_secret,
            "AWS_S3_ENDPOINT":              _CDSE_EODATA.replace("https://", ""),
            "AWS_VIRTUAL_HOSTING":          "FALSE",
            "AWS_HTTPS":                    "YES",
            "AWS_DEFAULT_REGION":           "default",
            "GDAL_DISABLE_READDIR_ON_OPEN": "EMPTY_DIR",
        }

        # Approximate scene extent from cancha center (S1 IW swath ≈ 250×170km)
        minx, miny, maxx, maxy = cancha_bbox
        c_lon = (minx + maxx) / 2
        c_lat = (miny + maxy) / 2
        i_lon0, i_lat0 = c_lon - 1.5, c_lat - 1.0
        i_lon1, i_lat1 = c_lon + 1.5, c_lat + 1.0

        with RioEnv(**gdal_env):
            with rasterio.open(meas_path) as src:
                H, W = src.shape
                col_off = max(0, int(((minx - i_lon0) / (i_lon1 - i_lon0)) * W))
                col_end = min(W, max(col_off + 5, int(((maxx - i_lon0) / (i_lon1 - i_lon0)) * W)))
                row_off = max(0, int(((i_lat1 - maxy) / (i_lat1 - i_lat0)) * H))
                row_end = min(H, max(row_off + 5, int(((i_lat1 - miny) / (i_lat1 - i_lat0)) * H)))
                win  = Window(col_off, row_off, col_end - col_off, row_end - row_off)
                data = src.read(1, window=win).astype("float64")

            try:
                import rasterio.vsi as vsi
                with vsi.open(cal_path) as cf:
                    xml_text = cf.read().decode("utf-8")
                a_cal = m._parse_a_cal_xml(xml_text, col_off, col_end)
            except Exception:
                a_cal = None

        valid = (data > 0) & np.isfinite(data)
        if valid.sum() < 2 or a_cal is None or a_cal <= 0:
            return None

        return round(10.0 * math.log10(max(1e-12, float(np.mean((data[valid] / a_cal) ** 2)))), 2)

    except Exception as exc:
        log.debug("sigma0_cdse_s3_bbox [%s/%s]: %s", product_name[:30], band, exc)
        return None


# ── Main API ──────────────────────────────────────────────────────────────────

def fetch_sar_all_canchas(days: int = 7) -> dict[str, dict]:
    """
    Fetch latest SAR sigma0 for all VO canchas + Amalfitani.

    Searches CDSE (lag ~6h) → PC (lag ~3-6d) for S1 GRD scenes.
    Returns {cancha_id: {vv_db, vh_db, fecha, satellite}} for each cancha with data.
    """
    _here = os.path.dirname(os.path.abspath(__file__))
    if _here not in sys.path:
        sys.path.insert(0, _here)

    bboxes   = _cancha_bboxes()
    end_dt   = date.today()
    start_dt = end_dt - timedelta(days=days)

    results: dict[str, dict] = {}
    m = _backfill_mod()

    # ── CDSE (fastest, ~6h lag) ────────────────────────────────────────────────
    cdse_tok = _cdse_token()
    if cdse_tok:
        # Search using VO cluster + Amalfitani separately to maximize coverage
        scenes_by_date: dict[str, dict] = {}
        for search_bbox in (_VO_CLUSTER_BBOX, _AMALFITANI_BBOX):
            for sc in _search_cdse_bbox(start_dt, end_dt, search_bbox):
                if sc["date"] not in scenes_by_date:
                    scenes_by_date[sc["date"]] = sc

        if scenes_by_date:
            latest_sc = sorted(scenes_by_date.values(), key=lambda x: x["date"])[-1]
            sat = m._infer_satellite(latest_sc["name"])
            for cid, bbox in bboxes.items():
                vv = _sigma0_cdse_s3_bbox(latest_sc["name"], latest_sc["date"], "vv", bbox)
                vh = _sigma0_cdse_s3_bbox(latest_sc["name"], latest_sc["date"], "vh", bbox)
                if vv is not None or vh is not None:
                    vv_c, vh_c = m._apply_radiometric_correction(vv, vh, sat)
                    results[cid] = {
                        "vv_db":     vv_c, "vh_db":    vh_c,
                        "fecha":     latest_sc["date"],
                        "satellite": sat,
                        "fuente":    "CDSE",
                    }
            log.info("fetch_sar_all_canchas CDSE: %d canchas", len(results))

    if len(results) == len(bboxes):
        return results   # all canchas covered by CDSE

    # ── Planetary Computer (fallback) ─────────────────────────────────────────
    pc_token  = _get_sas_token()
    pc_scenes = _search_s1_pc_bbox(start_dt, end_dt, _VO_CLUSTER_BBOX, limit=5)
    # Also search for Amalfitani (might be same scenes, but ensures coverage)
    pc_scenes_am = _search_s1_pc_bbox(start_dt, end_dt, _AMALFITANI_BBOX, limit=5)

    # Use latest scene from either search
    all_pc = {(item.get("properties", {}).get("datetime") or "")[:10]: item
              for item in pc_scenes + pc_scenes_am}
    if not all_pc:
        return results

    latest_item = sorted(all_pc.items(), key=lambda x: x[0])[-1][1]
    props = latest_item.get("properties", {})
    sat = m._satellite_from_platform(props.get("platform", ""))
    fecha = (props.get("datetime") or "")[:10]

    for cid, bbox in bboxes.items():
        if cid in results:
            continue   # already have CDSE data for this cancha
        vv = _sigma0_pc_bbox(latest_item, "vv", pc_token, bbox)
        vh = _sigma0_pc_bbox(latest_item, "vh", pc_token, bbox)
        if vv is not None or vh is not None:
            vv_c, vh_c = m._apply_radiometric_correction(vv, vh, sat)
            results[cid] = {
                "vv_db":     vv_c, "vh_db":    vh_c,
                "fecha":     fecha,
                "satellite": sat,
                "fuente":    "PC",
            }

    log.info("fetch_sar_all_canchas final: %d/%d canchas", len(results), len(bboxes))
    return results


def run_vo_sar_backfill(days: int = 30) -> dict:
    """
    Backfill soil_metrics for all VO canchas from S1 GRD (CDSE → PC cascade).

    Inserts a row per (cancha_id, date) pair discovered.  Skips dates already in DB.
    Amalfitani is handled by faro_sar_s1_backfill.run_s1_backfill() — excluded here.
    """
    _here = os.path.dirname(os.path.abspath(__file__))
    if _here not in sys.path:
        sys.path.insert(0, _here)

    try:
        import velez_supabase as vs
    except Exception as exc:
        return {"ok": False, "error": str(exc)}

    bboxes = {k: v for k, v in _cancha_bboxes().items() if k in _VO_CANCHA_IDS}
    end_dt   = date.today()
    start_dt = end_dt - timedelta(days=days)

    m         = _backfill_mod()
    pc_token  = _get_sas_token()
    cdse_tok  = _cdse_token()

    # Collect all unique scene dates from PC search over cluster
    pc_scenes  = _search_s1_pc_bbox(start_dt, end_dt, _VO_CLUSTER_BBOX, limit=days)
    cdse_scenes: list[dict] = []
    if cdse_tok:
        cdse_scenes = _search_cdse_bbox(start_dt, end_dt, _VO_CLUSTER_BBOX)

    # Build scene index: date → (item_or_scene, source)
    scene_index: dict[str, tuple[dict, str]] = {}
    for item in pc_scenes:
        fd = (item.get("properties", {}).get("datetime") or "")[:10]
        if fd:
            scene_index[fd] = (item, "PC")
    for sc in cdse_scenes:
        if sc["date"] and sc["date"] not in scene_index:
            scene_index[sc["date"]] = (sc, "CDSE")

    if not scene_index:
        return {"ok": True, "inserted": 0, "skipped": 0, "msg": "no scenes found"}

    inserted = skipped = errors = 0
    for scene_date, (scene_obj, source) in sorted(scene_index.items()):
        if source == "PC":
            props = scene_obj.get("properties", {})
            sat   = m._satellite_from_platform(props.get("platform", ""))
        else:
            sat = m._infer_satellite(scene_obj["name"])

        for cid, bbox in bboxes.items():
            if source == "PC":
                vv = _sigma0_pc_bbox(scene_obj, "vv", pc_token, bbox)
                vh = _sigma0_pc_bbox(scene_obj, "vh", pc_token, bbox)
            else:
                vv = _sigma0_cdse_s3_bbox(scene_obj["name"], scene_date, "vv", bbox)
                vh = _sigma0_cdse_s3_bbox(scene_obj["name"], scene_date, "vh", bbox)

            if vv is None and vh is None:
                skipped += 1
                continue

            vv_c, vh_c = m._apply_radiometric_correction(vv, vh, sat)
            ok = vs.insert_soil_metrics(
                venue_id          = _VENUE_ID,
                cancha_id         = cid,
                sar_vv_db         = vv_c,
                sar_vh_db         = vh_c,
                fuente            = f"Sentinel-1 GRD · {source} · 10-20m · VV+VH",
                fecha_imagen      = scene_date,
                satellite_inferred= sat,
            )
            if ok:
                inserted += 1
            else:
                errors += 1

    msg = (f"VO SAR backfill: {inserted} rows inserted "
           f"({len(scene_index)} scenes × {len(bboxes)} canchas). "
           f"skip={skipped} err={errors}")
    log.info("%s", msg)
    return {
        "ok":       True,
        "inserted": inserted,
        "skipped":  skipped,
        "errors":   errors,
        "scenes":   len(scene_index),
        "canchas":  len(bboxes),
        "msg":      msg,
    }
