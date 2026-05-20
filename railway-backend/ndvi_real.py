"""
ndvi_real.py — Real Sentinel-2 NDVI via Microsoft Planetary Computer.
Windowed COG read: fetches only the exact pixels of each cancha.
No auth required for public STAC access. Called from data_refresh.run_refresh().
"""
from __future__ import annotations
import logging
from datetime import date, timedelta
from typing import Optional

log = logging.getLogger(__name__)

_DEG_PER_M_LAT = 1 / 111_139          # constant
_DEG_PER_M_LON = 1 / 91_300           # at lat ≈ -34.6 (Argentina)


def _bbox(lat: float, lon: float, w_m: float = 105, h_m: float = 68, buf_m: float = 12) -> tuple:
    dlat = (h_m / 2 + buf_m) * _DEG_PER_M_LAT
    dlon = (w_m / 2 + buf_m) * _DEG_PER_M_LON
    return (lon - dlon, lat - dlat, lon + dlon, lat + dlat)


# Villa Olímpica cluster center — canchas 1FA-10FA
_VO_LAT, _VO_LON = -34.6375, -58.5215
_ROW = 120 * _DEG_PER_M_LAT   # ~0.00108° N→S between cancha centers
_COL = 120 * _DEG_PER_M_LON   # ~0.00132° E→W between cancha centers

# Primer equipo / Amalfitani
_PE_LAT, _PE_LON = -34.6358, -58.5224

# Per-cancha bounding boxes (WGS84). Grid: 5 rows × 2 cols for Villa Olímpica.
CANCHA_BBOXES: dict[str, tuple] = {
    "1fa":  _bbox(_VO_LAT,           _VO_LON),
    "2fa":  _bbox(_VO_LAT,           _VO_LON + _COL),
    "3fa":  _bbox(_VO_LAT - _ROW,    _VO_LON),
    "4fa":  _bbox(_VO_LAT - _ROW,    _VO_LON + _COL),
    "5fa":  _bbox(_VO_LAT - 2*_ROW,  _VO_LON),
    "6fa":  _bbox(_VO_LAT - 2*_ROW,  _VO_LON + _COL),
    "7fa":  _bbox(_VO_LAT - 3*_ROW,  _VO_LON),
    "8fa":  _bbox(_VO_LAT - 3*_ROW,  _VO_LON + _COL),
    "9fa":  _bbox(_VO_LAT - 4*_ROW,  _VO_LON),
    "10fa": _bbox(_VO_LAT - 4*_ROW,  _VO_LON + _COL),
    "1fp":  _bbox(_PE_LAT,           _PE_LON),
    "2fp":  _bbox(_PE_LAT,           _PE_LON + _COL),
}

# Cluster bbox covering all canchas for STAC search
_CLUSTER_BBOX = (
    min(b[0] for b in CANCHA_BBOXES.values()),
    min(b[1] for b in CANCHA_BBOXES.values()),
    max(b[2] for b in CANCHA_BBOXES.values()),
    max(b[3] for b in CANCHA_BBOXES.values()),
)


def _n_status(gndvi: float) -> tuple[str, str]:
    if gndvi < 0.30:
        return "grave",      "Fertilizar URGENTE — deficiencia N grave"
    if gndvi < 0.38:
        return "bajo",       "Fertilizar esta semana — déficit N moderado"
    if gndvi < 0.45:
        return "borderline", "Fertilización preventiva recomendable"
    return     "ok",         "Nitrógeno adecuado"


def _read_canchas(item) -> dict[str, dict]:
    """Windowed read of B03/B04/B08 COGs for every cancha from one STAC item."""
    import rasterio
    from rasterio.warp import transform_bounds
    from rasterio.windows import from_bounds

    red_url   = item.assets["B04"].href
    nir_url   = item.assets["B08"].href
    green_url = item.assets["B03"].href

    results: dict[str, dict] = {}
    with (
        rasterio.open(red_url)   as src_red,
        rasterio.open(nir_url)   as src_nir,
        rasterio.open(green_url) as src_green,
    ):
        crs = src_nir.crs
        for cid, (minx, miny, maxx, maxy) in CANCHA_BBOXES.items():
            try:
                native = transform_bounds("EPSG:4326", crs, minx, miny, maxx, maxy)
                win    = from_bounds(*native, transform=src_nir.transform)

                nir_px   = src_nir.read(1, window=win).astype("float32")
                red_px   = src_red.read(1, window=win).astype("float32")
                green_px = src_green.read(1, window=win).astype("float32")

                # Sentinel-2 L2A: reflectance = DN / 10000
                nir, red, green = nir_px / 10_000, red_px / 10_000, green_px / 10_000

                valid = (nir + red) > 0.02
                if valid.sum() < 2:
                    log.warning("ndvi_real: %s — fewer than 2 valid pixels", cid)
                    continue

                ndvi  = float(((nir - red)   / (nir + red)  )[valid].mean())
                gndvi = float(((nir - green) / (nir + green))[valid].mean())
                ndvi  = round(max(-1.0, min(1.0, ndvi)),  3)
                gndvi = round(max(-1.0, min(1.0, gndvi)), 3)

                nst, nrec = _n_status(gndvi)
                results[cid] = {
                    "ndvi":     ndvi,
                    "gndvi":    gndvi,
                    "n_status": nst,
                    "n_rec":    nrec,
                }
            except Exception as exc:
                log.warning("ndvi_real: %s: %s", cid, exc)

    return results


def fetch_ndvi(max_cloud: float = 20.0, _days: int = 7) -> Optional[dict]:
    """
    Fetch real Sentinel-2 NDVI for all canchas via Planetary Computer STAC.
    Falls back to 14-day window if no clean image found in 7 days.
    Returns dict compatible with weather_live.gndvi_por_cancha, or None on failure.
    """
    try:
        import pystac_client
        import planetary_computer
        import rioxarray  # noqa: F401 — registers the rio accessor
    except ImportError as exc:
        log.warning("ndvi_real: missing dependency %s — skipping", exc)
        return None

    today   = date.today()
    dt_from = (today - timedelta(days=_days)).isoformat()
    dt_to   = today.isoformat()

    try:
        catalog = pystac_client.Client.open(
            "https://planetarycomputer.microsoft.com/api/stac/v1",
            modifier=planetary_computer.sign_inplace,
        )
        items = list(catalog.search(
            collections=["sentinel-2-l2a"],
            bbox=_CLUSTER_BBOX,
            datetime=f"{dt_from}/{dt_to}",
            query={"eo:cloud_cover": {"lt": max_cloud}},
            sortby="-properties.datetime",
        ).items())
    except Exception as exc:
        log.warning("ndvi_real: STAC search failed: %s", exc)
        return None

    if not items and _days == 7:
        log.info("ndvi_real: no image ≤%.0f%% cloud in 7d — expanding to 14d", max_cloud)
        return fetch_ndvi(max_cloud=max_cloud, _days=14)

    if not items:
        log.warning("ndvi_real: ⚠️  Sin imagen limpia en %dd — NDVI anterior mantenido", _days)
        return None

    item   = items[0]
    cloud  = item.properties.get("eo:cloud_cover", 0)
    img_dt = item.properties.get("datetime", "?")[:10]

    try:
        canchas = _read_canchas(item)
    except Exception as exc:
        log.warning("ndvi_real: COG read failed: %s", exc)
        return None

    if not canchas:
        log.warning("ndvi_real: ⚠️  Sin píxeles válidos — NDVI anterior mantenido")
        return None

    log.info(
        "✅ NDVI real — Sentinel-2 %s · nubosidad %.0f%% · %d canchas",
        img_dt, cloud, len(canchas),
    )
    return {
        "fuente":       f"sentinel-2-l2a · Planetary Computer · {img_dt} · nubosidad {cloud:.0f}%",
        "fecha_imagen": img_dt,
        "nubosidad_pct": round(cloud, 1),
        "canchas":      canchas,
    }
