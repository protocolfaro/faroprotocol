"""
ndvi_real.py — Multi-source NDVI real via STAC (Sentinel-2 + Landsat fallback).

Fuentes en cascada:
  1. Planetary Computer   — Sentinel-2-L2A, firmado automáticamente
  2. Element84 Earth Search — mismo Sentinel-2-L2A, infraestructura independiente
  3. Landsat-C2-L2 (E84) — satélite distinto, ciclo 16d offset al de S2

Estrategia progresiva de búsqueda (se itera por todas las fuentes antes de pasar):
  Ronda 1: cloud ≤20 %, ventana 7 días
  Ronda 2: cloud ≤35 %, ventana 14 días
  Ronda 3: cloud ≤50 %, ventana 30 días  (mejor disponible)
"""
from __future__ import annotations
import logging, sys, os
from datetime import date, timedelta
from typing import Optional

log = logging.getLogger(__name__)

# ── Acceso a core/utils ───────────────────────────────────────────────────────
_CORE = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "core"))
if _CORE not in sys.path:
    sys.path.insert(0, _CORE)
from utils import coords_to_bbox

# ── Coordenadas de canchas ────────────────────────────────────────────────────
#
# Layout (plano "NUMERACION DE CANCHAS 2024"):
#
#  [General de la Guitarra]          [Dardo Cabo]
#   1FA  2FA    [4FA]                6FA  9FA
#                 [3FA]       5FA    7FA
#   1FP  2FP               [asfalt]  8FA  10FA
#  [═══════════ Camino del Buen Ayre ═════════════]

def _bbox(lat: float, lon: float,
          w_m: float = 105, h_m: float = 68, buf_m: float = 12) -> tuple:
    """Cancha bbox con buffer, usando coords_to_bbox de core/utils."""
    box = coords_to_bbox(lat, lon, h_m=h_m + 2 * buf_m, w_m=w_m + 2 * buf_m)
    return tuple(box)


CANCHA_BBOXES: dict[str, tuple] = {
    # WEST — diagonal, near General de la Guitarra
    "1fa":  _bbox(-34.6355, -58.5220),
    "2fa":  _bbox(-34.6355, -58.5209),
    # SW — Primer Equipo
    "1fp":  _bbox(-34.6366, -58.5213, w_m=105, h_m=70),
    "2fp":  _bbox(-34.6366, -58.5202, w_m=105, h_m=70),
    # CENTER — rotadas ~40°, buffer mayor
    "4fa":  _bbox(-34.6353, -58.5190, buf_m=18),
    "3fa":  _bbox(-34.6359, -58.5188, buf_m=18),
    # EAST
    "5fa":  _bbox(-34.6366, -58.5182),
    "6fa":  _bbox(-34.6360, -58.5174),
    "7fa":  _bbox(-34.6360, -58.5167),
    "8fa":  _bbox(-34.6359, -58.5160),
    "9fa":  _bbox(-34.6355, -58.5162),
    "10fa": _bbox(-34.6357, -58.5149),
    # Amalfitani
    "amalfitani": _bbox(-34.6353, -58.5207),
    # Polideportivo Feijóo
    "poli_f11":    _bbox(-34.6345, -58.5152, w_m=105, h_m=68),
    "poli_f8a":    _bbox(-34.6325, -58.5143, w_m=62,  h_m=44),
    "poli_f8b":    _bbox(-34.6338, -58.5118, w_m=62,  h_m=44),
    "poli_hockey": _bbox(-34.6320, -58.5122, w_m=91,  h_m=55),
}

_CLUSTER_BBOX = (
    min(b[0] for b in CANCHA_BBOXES.values()),
    min(b[1] for b in CANCHA_BBOXES.values()),
    max(b[2] for b in CANCHA_BBOXES.values()),
    max(b[3] for b in CANCHA_BBOXES.values()),
)

# ── Fuentes STAC ──────────────────────────────────────────────────────────────
#
# scale:
#   "s2" → reflectancia = raw / 10_000
#   "ls" → reflectancia = clip(raw * 2.75e-5 − 0.2, 0, 1)  [Landsat C2 L2 SR]
#
# red / nir / green: lista de nombres de asset a probar en orden
_STAC_SOURCES = [
    {
        "name":       "Planetary Computer",
        "url":        "https://planetarycomputer.microsoft.com/api/stac/v1",
        "collection": "sentinel-2-l2a",
        "sign":       True,
        "cloud_prop": "eo:cloud_cover",
        "red":        ["B04"],
        "nir":        ["B08"],
        "green":      ["B03"],
        "blue":       ["B02"],          # for BSI
        "swir1":      ["B11"],          # for BSI + NDWI
        "scl":        ["SCL"],          # Scene Classification Layer — cloud/shadow masking
        "scale":      "s2",
    },
    {
        "name":       "Element84 Earth Search",
        "url":        "https://earth-search.aws.element84.com/v1",
        "collection": "sentinel-2-l2a",
        "sign":       False,
        "cloud_prop": "eo:cloud_cover",
        "red":        ["B04", "red"],
        "nir":        ["B08", "nir"],
        "green":      ["B03", "green"],
        "blue":       ["B02", "blue"],
        "swir1":      ["B11", "swir16"],
        "scl":        ["scl", "SCL"],
        "scale":      "s2",
    },
    {
        "name":       "Landsat-C2-L2 (Element84)",
        "url":        "https://earth-search.aws.element84.com/v1",
        "collection": "landsat-c2-l2",
        "sign":       False,
        "cloud_prop": "eo:cloud_cover",
        "red":        ["SR_B4", "red"],
        "nir":        ["SR_B5", "nir"],
        "green":      ["SR_B3", "green"],
        "blue":       ["SR_B2", "blue"],
        "swir1":      ["SR_B6", "swir16"],
        # no SCL for Landsat — CLOSDI handles shadows inline
        "scale":      "ls",
    },
]

# Rondas progresivas: (max_cloud_pct, ventana_dias)
_ROUNDS = [
    (20, 7),
    (35, 14),
    (50, 30),
]


# ── Helpers ───────────────────────────────────────────────────────────────────

def _asset_href(item, candidates: list[str]) -> str:
    for name in candidates:
        asset = item.assets.get(name)
        if asset:
            return asset.href
    raise KeyError(f"Ningún asset de {candidates} encontrado en el item")


def _to_refl(arr, mode: str):
    import numpy as np
    if mode == "s2":
        return arr / 10_000.0
    if mode == "ls":
        return np.clip(arr * 2.75e-5 - 0.2, 0.0, 1.0)
    return arr / 10_000.0


def _n_status(gndvi: float) -> tuple[str, str]:
    if gndvi < 0.30:
        return "grave",      "Fertilizar URGENTE — deficiencia N grave"
    if gndvi < 0.38:
        return "bajo",       "Fertilizar esta semana — déficit N moderado"
    if gndvi < 0.45:
        return "borderline", "Fertilización preventiva recomendable"
    return     "ok",         "Nitrógeno adecuado"


def _search_source(src: dict, dt_from: str, dt_to: str,
                   max_cloud: float) -> list:
    """Busca items en una fuente STAC. Retorna lista vacía si falla."""
    try:
        import pystac_client
        kwargs = {"url": src["url"]}
        if src["sign"]:
            try:
                import planetary_computer
                kwargs["modifier"] = planetary_computer.sign_inplace
            except ImportError:
                log.warning("ndvi_real: planetary_computer no disponible — %s omitido", src["name"])
                return []

        catalog = pystac_client.Client.open(**kwargs)
        items = list(catalog.search(
            collections=[src["collection"]],
            bbox=_CLUSTER_BBOX,
            datetime=f"{dt_from}/{dt_to}",
            query={src["cloud_prop"]: {"lt": max_cloud}},
            sortby="-properties.datetime",
        ).items())
        log.info("ndvi_real [%s]: %d items (cloud<%.0f%%, %s→%s)",
                 src["name"], len(items), max_cloud, dt_from, dt_to)
        return items
    except Exception as exc:
        log.warning("ndvi_real [%s]: búsqueda falló: %s", src["name"], exc)
        return []


def _read_canchas(item, src: dict) -> dict[str, dict]:
    """Windowed COG read for all canchas.

    Applies SCL + CLOSDI pixel-level masking before computing NDVI.
    Computes NDVI, GNDVI (nitrogen), BSI (bare soil), NDWI (water stress).
    BSI and NDWI require SWIR1 (B11) — omitted silently if asset unavailable.
    """
    import contextlib
    import rasterio
    from rasterio.enums import Resampling
    from rasterio.env import Env as _RioEnv
    from rasterio.warp import transform_bounds
    from rasterio.windows import from_bounds
    import numpy as np

    try:
        import faro_ndvi_clean as _fnc
        _USE_CLEAN = True
    except ImportError:
        _USE_CLEAN = False

    mode = src["scale"]

    # Required bands
    red_url   = _asset_href(item, src["red"])
    nir_url   = _asset_href(item, src["nir"])
    green_url = _asset_href(item, src["green"])

    # Optional bands — None if asset not in item
    def _opt(key: str):
        cands = src.get(key)
        if not cands:
            return None
        try:
            return _asset_href(item, cands)
        except KeyError:
            return None

    blue_url  = _opt("blue")
    swir1_url = _opt("swir1")
    scl_url   = _opt("scl")

    # SCL classes to discard: no_data, defective, cloud shadow, medium cloud, high cloud, cirrus
    _SCL_BAD = frozenset({0, 1, 3, 8, 9, 10})

    results: dict[str, dict] = {}

    with contextlib.ExitStack() as stack:
        stack.enter_context(_RioEnv(GDAL_HTTP_TIMEOUT=30, GDAL_HTTP_CONNECTTIMEOUT=15))
        s_nir   = stack.enter_context(rasterio.open(nir_url))
        s_red   = stack.enter_context(rasterio.open(red_url))
        s_green = stack.enter_context(rasterio.open(green_url))
        s_blue  = stack.enter_context(rasterio.open(blue_url))  if blue_url  else None
        s_swir1 = stack.enter_context(rasterio.open(swir1_url)) if swir1_url else None
        s_scl   = stack.enter_context(rasterio.open(scl_url))   if scl_url   else None

        crs = s_nir.crs

        for cid, (minx, miny, maxx, maxy) in CANCHA_BBOXES.items():
            try:
                native = transform_bounds("EPSG:4326", crs, minx, miny, maxx, maxy)

                def _win(s):
                    return from_bounds(*native, transform=s.transform)

                nir_raw   = s_nir.read(1,   window=_win(s_nir)).astype("float32")
                red_raw   = s_red.read(1,   window=_win(s_red)).astype("float32")
                green_raw = s_green.read(1, window=_win(s_green)).astype("float32")

                nir_r   = _to_refl(nir_raw, mode)
                red_r   = _to_refl(red_raw, mode)
                green_r = _to_refl(green_raw, mode)

                # SCL mask — upsample to NIR pixel grid (SCL is 20m, NIR is 10m)
                scl_mask = None
                if s_scl is not None:
                    try:
                        scl_raw  = s_scl.read(
                            1, window=_win(s_scl),
                            out_shape=nir_raw.shape,
                            resampling=Resampling.nearest,
                        )
                        scl_mask = np.isin(scl_raw, list(_SCL_BAD))
                    except Exception as _se:
                        log.debug("ndvi_real: %s SCL non-fatal: %s", cid, _se)

                # NDVI via faro_ndvi_clean (CLOSDI + unmix) for S2; inline CLOSDI fallback otherwise
                if _USE_CLEAN and mode == "s2":
                    cleaned = _fnc.clean_ndvi(
                        nir_raw.astype(np.int32), red_raw.astype(np.int32),
                        do_unmix=True,
                        external_mask=scl_mask,
                    )
                    if cleaned["coverage_pct"] < 20.0:
                        log.warning("ndvi_real: %s coverage %.0f%% post-clean — skip",
                                    cid, cleaned["coverage_pct"])
                        continue
                    ndvi     = round(max(-1.0, min(1.0, cleaned["mean_ndvi"])), 3)
                    coverage = round(cleaned["coverage_pct"], 1)
                else:
                    # Fallback: inline CLOSDI + basic signal check
                    with np.errstate(invalid="ignore", divide="ignore"):
                        _closdi = 250.0 * (nir_r + red_r) / (nir_r + 2.4 * red_r + 1.0)
                    valid = (nir_r + red_r) > 0.02
                    valid &= (_closdi >= 34.0) | ((nir_r + red_r) <= 0.02)  # keep no-signal as-is
                    valid &= (nir_r >= red_r)                                 # exclude negative NDVI
                    if scl_mask is not None:
                        valid &= ~scl_mask
                    if valid.sum() < 2:
                        log.warning("ndvi_real: %s — menos de 2 píxeles válidos", cid)
                        continue
                    with np.errstate(invalid="ignore", divide="ignore"):
                        ndvi = round(float(
                            ((nir_r - red_r) / (nir_r + red_r + 1e-9))[valid].mean()), 3)
                    ndvi     = max(-1.0, min(1.0, ndvi))
                    coverage = None

                # GNDVI — nitrogen proxy
                valid_g = (nir_r + green_r) > 0.02
                if scl_mask is not None:
                    valid_g &= ~scl_mask
                if valid_g.sum() >= 2:
                    with np.errstate(invalid="ignore", divide="ignore"):
                        gndvi = round(float(
                            ((nir_r - green_r) / (nir_r + green_r + 1e-9))[valid_g].mean()), 3)
                    gndvi = max(-1.0, min(1.0, gndvi))
                else:
                    gndvi = round(ndvi * 0.93, 3)

                nst, nrec = _n_status(gndvi)
                entry: dict = {"ndvi": ndvi, "gndvi": gndvi, "n_status": nst, "n_rec": nrec}
                if coverage is not None:
                    entry["coverage_pct"] = coverage

                # Base valid mask for optional indices
                valid_base = (nir_r + red_r) > 0.02
                if scl_mask is not None:
                    valid_base &= ~scl_mask

                # Optional bands
                blue_r  = None
                swir1_r = None
                if s_blue is not None:
                    try:
                        blue_r = _to_refl(
                            s_blue.read(1, window=_win(s_blue)).astype("float32"), mode)
                    except Exception:
                        pass
                if s_swir1 is not None:
                    try:
                        swir1_r = _to_refl(
                            s_swir1.read(1, window=_win(s_swir1)).astype("float32"), mode)
                    except Exception:
                        pass

                # BSI = ((SWIR1+RED)-(NIR+BLUE)) / ((SWIR1+RED)+(NIR+BLUE))
                # Positive → bare/exposed soil. Key for post-show damage assessment.
                if swir1_r is not None and blue_r is not None:
                    v = valid_base & ((swir1_r + red_r + nir_r + blue_r) > 0.04)
                    if v.sum() >= 2:
                        with np.errstate(invalid="ignore", divide="ignore"):
                            num = (swir1_r + red_r) - (nir_r + blue_r)
                            den = (swir1_r + red_r) + (nir_r + blue_r) + 1e-9
                            bsi = round(max(-1.0, min(1.0, float((num / den)[v].mean()))), 3)
                        entry["bsi"] = bsi

                # NDWI (Gao) = (NIR-SWIR1)/(NIR+SWIR1) — vegetation water stress.
                # Detects hydraulic stress 3-5 days before NDVI shows it.
                # Positive → high leaf water content; negative → stress / drought.
                if swir1_r is not None:
                    v = valid_base & ((nir_r + swir1_r) > 0.02)
                    if v.sum() >= 2:
                        with np.errstate(invalid="ignore", divide="ignore"):
                            ndwi = round(max(-1.0, min(1.0,
                                float(((nir_r - swir1_r) / (nir_r + swir1_r + 1e-9))[v].mean())
                            )), 3)
                        entry["ndwi"] = ndwi

                results[cid] = entry

            except Exception as exc:
                log.warning("ndvi_real: %s: %s", cid, exc)

    return results


# ── API pública ───────────────────────────────────────────────────────────────

def fetch_ndvi() -> Optional[dict]:
    """
    Obtiene NDVI real de la mejor imagen disponible, probando todas las fuentes
    en orden con rondas progresivas de cloud cover y ventana temporal.

    Retorna dict compatible con weather_live.gndvi_por_cancha, o None si
    ninguna fuente produce datos válidos.
    """
    try:
        import pystac_client  # noqa — verifica que la dependencia base esté
    except ImportError as exc:
        log.warning("ndvi_real: pystac_client no instalado — skipping: %s", exc)
        return None

    today = date.today()

    for max_cloud, days in _ROUNDS:
        dt_from = (today - timedelta(days=days)).isoformat()
        dt_to   = today.isoformat()
        log.info("ndvi_real: ronda cloud≤%d%% ventana %dd (%s→%s)",
                 max_cloud, days, dt_from, dt_to)

        for src in _STAC_SOURCES:
            items = _search_source(src, dt_from, dt_to, max_cloud)
            if not items:
                continue

            item   = items[0]
            cloud  = item.properties.get(src["cloud_prop"], 0)
            img_dt = item.properties.get("datetime", "?")[:10]

            try:
                canchas = _read_canchas(item, src)
            except Exception as exc:
                log.warning("ndvi_real [%s]: lectura COG falló: %s", src["name"], exc)
                continue

            if not canchas:
                log.warning("ndvi_real [%s]: sin píxeles válidos en %s", src["name"], img_dt)
                continue

            log.info("ndvi_real OK [%s] — %s · nub %.0f%% · %d canchas",
                     src["name"], img_dt, cloud, len(canchas))
            return {
                "fuente":        f"{src['collection']} · {src['name']} · {img_dt} · nub {cloud:.0f}%",
                "fecha_imagen":  img_dt,
                "nubosidad_pct": round(cloud, 1),
                "canchas":       canchas,
            }

    log.warning("ndvi_real: ⚠️ ninguna fuente devolvió datos — NDVI anterior mantenido")
    return None
