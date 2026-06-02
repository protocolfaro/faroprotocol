"""
ndvi_real.py — Real Sentinel-2 NDVI via Microsoft Planetary Computer.
Windowed COG read: fetches only the exact pixels of each cancha.
No auth required for public STAC access. Called from data_refresh.run_refresh().

Cancha coordinates derived from architectural plan "NUMERACION DE CANCHAS 2024.pdf".
Scale anchored at Acceso Calle Dardo Cabo. Accuracy ±50-100m — refine with GPS survey.
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


# ── Cancha centers — derived from plano "NUMERACION DE CANCHAS 2024" ─────────
#
# Layout (NOT a simple grid):
#
#  [General de la Guitarra]          [Dardo Cabo]
#   1FA  2FA    [4FA]                6FA  9FA
#                 [3FA]       5FA    7FA
#   1FP  2FP               [asfalt]  8FA  10FA
#  [═══════════ Camino del Buen Ayre ═════════════]
#
# Zones:
#  WEST  — 1FA, 2FA: large angled fields (~15° rotation), near Gral. de la Guitarra
#  SW    — 1FP, 2FP: full-size primer equipo pitches, horizontal, south of west block
#  CENTER— 3FA, 4FA: significantly rotated (~40°), near Dardo Cabo entrance
#  EAST  — 5FA (isolated south), 6FA/7FA/8FA (column), 9FA/10FA (upper-right)

CANCHA_BBOXES: dict[str, tuple] = {
    # WEST block — diagonal orientation, near General de la Guitarra
    "1fa":  _bbox(-34.6355, -58.5220),   # westernmost, larger field
    "2fa":  _bbox(-34.6355, -58.5209),   # adjacent east of 1FA

    # SW — Primer Equipo full-size pitches (south of west block)
    "1fp":  _bbox(-34.6366, -58.5213, w_m=105, h_m=70),
    "2fp":  _bbox(-34.6366, -58.5202, w_m=105, h_m=70),

    # CENTER — rotated ~40°, near Dardo Cabo (use larger buffer for rotation)
    "4fa":  _bbox(-34.6353, -58.5190, buf_m=18),   # upper of rotated pair
    "3fa":  _bbox(-34.6359, -58.5188, buf_m=18),   # lower of rotated pair

    # EAST — right section
    "5fa":  _bbox(-34.6366, -58.5182),   # isolated, south of east block
    "6fa":  _bbox(-34.6360, -58.5174),   # east block, leftmost column
    "7fa":  _bbox(-34.6360, -58.5167),   # east block, center column
    "8fa":  _bbox(-34.6359, -58.5160),   # east block, right column
    "9fa":  _bbox(-34.6355, -58.5162),   # east block, upper row
    "10fa": _bbox(-34.6357, -58.5149),   # far east

    # Campo Principal Amalfitani (Liniers)
    "amalfitani": _bbox(-34.6353, -58.5207),

    # Polideportivo Feijóo (Liniers) — grass fields
    "poli_f11":    _bbox(-34.6345, -58.5152, w_m=105, h_m=68),
    "poli_f8a":    _bbox(-34.6325, -58.5143, w_m=62,  h_m=44),
    "poli_f8b":    _bbox(-34.6338, -58.5118, w_m=62,  h_m=44),
    "poli_hockey": _bbox(-34.6320, -58.5122, w_m=91,  h_m=55),
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
    from rasterio.env import Env as _RioEnv
    from rasterio.warp import transform_bounds
    from rasterio.windows import from_bounds

    try:
        import faro_ndvi_clean as _fnc
        _USE_CLEAN = True
    except ImportError:
        _USE_CLEAN = False

    red_url   = item.assets["B04"].href
    nir_url   = item.assets["B08"].href
    green_url = item.assets["B03"].href

    results: dict[str, dict] = {}
    with _RioEnv(GDAL_HTTP_TIMEOUT=30, GDAL_HTTP_CONNECTTIMEOUT=15):
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

                if _USE_CLEAN:
                    # Sub-pixel cleaning: removes saturated + cloud pixels, optional unmixing
                    import numpy as np
                    cleaned = _fnc.clean_ndvi(nir_px.astype(np.int32), red_px.astype(np.int32),
                                              do_unmix=False)
                    if cleaned["coverage_pct"] < 20.0:
                        log.warning("ndvi_real: %s — coverage %.0f%% after cleaning, skipping",
                                    cid, cleaned["coverage_pct"])
                        continue
                    ndvi = round(max(-1.0, min(1.0, cleaned["mean_ndvi"])), 3)
                    coverage = round(cleaned["coverage_pct"], 1)
                else:
                    nir, red = nir_px / 10_000, red_px / 10_000
                    valid = (nir + red) > 0.02
                    if valid.sum() < 2:
                        log.warning("ndvi_real: %s — fewer than 2 valid pixels", cid)
                        continue
                    ndvi = round(max(-1.0, min(1.0, float(((nir - red) / (nir + red))[valid].mean()))), 3)
                    coverage = None

                # GNDVI uses green band (not in faro_ndvi_clean — computed separately)
                nir_r   = nir_px / 10_000
                green_r = green_px / 10_000
                valid_g = (nir_r + green_r) > 0.02
                if valid_g.sum() < 2:
                    gndvi = ndvi * 0.93  # fallback estimate
                else:
                    gndvi = round(max(-1.0, min(1.0,
                        float(((nir_r - green_r) / (nir_r + green_r))[valid_g].mean()))), 3)

                nst, nrec = _n_status(gndvi)
                entry = {"ndvi": ndvi, "gndvi": gndvi, "n_status": nst, "n_rec": nrec}
                if coverage is not None:
                    entry["coverage_pct"] = coverage
                results[cid] = entry
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
