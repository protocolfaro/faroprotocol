"""
dale_play_satellite.py — Baseline satelital pre/post evento del Amalfitani.
NDVI via Sentinel-2 MSI L2A + temperatura superficial via Landsat TIRS (ST_B10).
Fuente: Microsoft Planetary Computer STAC API.
"""
from __future__ import annotations
import logging
from datetime import date, timedelta
from typing import Optional

from dale_play_config import VENUE_BBOX, NDVI_BUENO, NDVI_DEGRADADO

log = logging.getLogger(__name__)

_PC_STAC = "https://planetarycomputer.microsoft.com/api/stac/v1"


def _classify_ndvi(ndvi: float, month: int | None = None) -> dict:
    # Bermuda grass dormancy: NDVI 0.08-0.25 in months 4-8 (BsAs otoño/invierno)
    if month is not None and 4 <= month <= 8 and 0.08 <= ndvi <= 0.25:
        return {"semaforo": "amarillo", "label": "Dormancia estacional (Bermuda — invierno BsAs)"}
    if ndvi >= NDVI_BUENO:
        return {"semaforo": "verde",    "label": "Césped en buen estado"}
    if ndvi >= NDVI_DEGRADADO:
        return {"semaforo": "amarillo", "label": "Estrés moderado — monitorear"}
    return {"semaforo": "rojo",         "label": "Daño severo — requiere intervención"}


def _search_s2(days_back: int = 30, max_cloud: float = 30.0) -> Optional[dict]:
    import requests
    end_dt   = date.today()
    start_dt = end_dt - timedelta(days=days_back)
    minx, miny, maxx, maxy = VENUE_BBOX
    payload = {
        "collections": ["sentinel-2-l2a"],
        "bbox":        [minx, miny, maxx, maxy],
        "datetime":    f"{start_dt.isoformat()}T00:00:00Z/{end_dt.isoformat()}T23:59:59Z",
        "query":       {"eo:cloud_cover": {"lt": max_cloud}},
        "limit":       10,
        "sortby":      [{"field": "eo:cloud_cover", "direction": "asc"}],
    }
    r = requests.post(f"{_PC_STAC}/search", json=payload, timeout=20)
    r.raise_for_status()
    features = r.json().get("features", [])
    return features[0] if features else None


def _ndvi_from_item(item: dict) -> Optional[float]:
    """Mean NDVI over VENUE_BBOX from Sentinel-2 B04/B08 assets."""
    try:
        import rasterio, numpy as np
        from rasterio.warp import transform_bounds
        from rasterio.windows import from_bounds

        token_url = (
            "https://planetarycomputer.microsoft.com/api/sas/v1/token/sentinel-2-l2a"
        )
        import requests as _req
        tok_r = _req.get(token_url, timeout=10)
        token = tok_r.json().get("token", "") if tok_r.ok else ""

        def _read_band(key: str) -> Optional[np.ndarray]:
            href = item.get("assets", {}).get(key, {}).get("href", "")
            if not href:
                return None
            if token:
                href = f"{href}?{token}"
            with rasterio.open(href) as src:
                minx, miny, maxx, maxy = VENUE_BBOX
                native = transform_bounds("EPSG:4326", src.crs, minx, miny, maxx, maxy)
                win    = from_bounds(*native, transform=src.transform)
                arr    = src.read(1, window=win).astype("float32")
                return arr / 10_000.0

        red = _read_band("B04")
        nir = _read_band("B08")
        if red is None or nir is None:
            return None

        valid = (red > 0) & (nir > 0) & (red < 1) & (nir < 1)
        if valid.sum() < 4:
            return None
        r_v, n_v = red[valid], nir[valid]
        ndvi = (n_v - r_v) / (n_v + r_v + 1e-9)
        return float(round(float(np.mean(ndvi)), 3))
    except Exception as e:
        log.warning("dale_play_satellite: NDVI error: %s", e)
        return None


def _search_landsat(days_back: int = 45) -> Optional[dict]:
    import requests
    end_dt   = date.today()
    start_dt = end_dt - timedelta(days=days_back)
    minx, miny, maxx, maxy = VENUE_BBOX
    payload = {
        "collections": ["landsat-c2-l2"],
        "bbox":        [minx, miny, maxx, maxy],
        "datetime":    f"{start_dt.isoformat()}T00:00:00Z/{end_dt.isoformat()}T23:59:59Z",
        "query":       {"eo:cloud_cover": {"lt": 40}},
        "limit":       5,
        "sortby":      [{"field": "datetime", "direction": "desc"}],
    }
    r = requests.post(f"{_PC_STAC}/search", json=payload, timeout=20)
    r.raise_for_status()
    features = r.json().get("features", [])
    return features[0] if features else None


def _tirs_celsius(item: dict) -> Optional[float]:
    """Surface temperature °C from Landsat Collection 2 ST_B10."""
    try:
        import rasterio, numpy as np
        from rasterio.warp import transform_bounds
        from rasterio.windows import from_bounds

        href = item.get("assets", {}).get("ST_B10", {}).get("href", "")
        if not href:
            return None
        with rasterio.open(href) as src:
            minx, miny, maxx, maxy = VENUE_BBOX
            native = transform_bounds("EPSG:4326", src.crs, minx, miny, maxx, maxy)
            win    = from_bounds(*native, transform=src.transform)
            data   = src.read(1, window=win).astype("float32")
        valid = (data > 0) & (data < 65535)
        if valid.sum() < 2:
            return None
        kelvin  = data[valid] * 0.00341802 + 149.0
        celsius = kelvin - 273.15
        return float(round(float(np.mean(celsius)), 1))
    except Exception as e:
        log.warning("dale_play_satellite: TIRS error: %s", e)
        return None


def fetch_satellite_baseline(days_back_s2: int = 30, days_back_ls: int = 45) -> dict:
    """
    Baseline satelital completo para el Amalfitani.
    Returns {ndvi, ndvi_fecha, ndvi_cloud_pct, ndvi_status, tirs_celsius, tirs_fecha, fuente_*}
    """
    result: dict = {}

    # ── Sentinel-2 NDVI ──────────────────────────────────────────────────────
    try:
        item = _search_s2(days_back=days_back_s2)
        if item:
            ndvi  = _ndvi_from_item(item)
            props = item.get("properties", {})
            result.update({
                "ndvi":           ndvi,
                "ndvi_fecha":     (props.get("datetime") or "")[:10],
                "ndvi_cloud_pct": round(props.get("eo:cloud_cover", 0), 1),
                "ndvi_status":    _classify_ndvi(ndvi, month=int((props.get("datetime") or "")[:7].split("-")[1]) if (props.get("datetime") or "")[:7] else None) if ndvi is not None
                                  else {"semaforo": "sin_datos", "label": "Sin imagen disponible"},
                "fuente_s2":      "Sentinel-2 L2A · Planetary Computer",
            })
            log.info("dale_play_satellite: NDVI %s fecha %s", ndvi, result["ndvi_fecha"])
        else:
            result.update({"ndvi": None, "ndvi_fecha": None,
                           "ndvi_status": {"semaforo": "sin_datos", "label": "Sin escena disponible"}})
    except Exception as e:
        log.warning("dale_play_satellite: S2 failed: %s", e)
        result.update({"ndvi": None, "ndvi_status": {"semaforo": "error", "label": str(e)}})

    # ── Landsat TIRS ─────────────────────────────────────────────────────────
    try:
        ls = _search_landsat(days_back=days_back_ls)
        if ls:
            tirs  = _tirs_celsius(ls)
            props = ls.get("properties", {})
            result.update({
                "tirs_celsius": tirs,
                "tirs_fecha":   (props.get("datetime") or "")[:10],
                "fuente_tirs":  "Landsat-9 C2L2 ST_B10 · Planetary Computer",
            })
            log.info("dale_play_satellite: TIRS %.1f°C fecha %s", tirs or 0, result["tirs_fecha"])
        else:
            result.update({"tirs_celsius": None, "tirs_fecha": None})
    except Exception as e:
        log.warning("dale_play_satellite: Landsat failed: %s", e)
        result.update({"tirs_celsius": None})

    return result
