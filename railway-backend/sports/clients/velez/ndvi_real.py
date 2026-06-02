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
    """Windowed read COG para todas las canchas usando la fuente especificada."""
    import rasterio
    from rasterio.env import Env as _RioEnv
    from rasterio.warp import transform_bounds
    from rasterio.windows import from_bounds
    import numpy as np

    try:
        import faro_ndvi_clean as _fnc
        _USE_CLEAN = True
    except ImportError:
        _USE_CLEAN = False

    red_url   = _asset_href(item, src["red"])
    nir_url   = _asset_href(item, src["nir"])
    green_url = _asset_href(item, src["green"])
    mode      = src["scale"]

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

                    nir_raw   = src_nir.read(1, window=win).astype("float32")
                    red_raw   = src_red.read(1, window=win).astype("float32")
                    green_raw = src_green.read(1, window=win).astype("float32")

                    nir_r   = _to_refl(nir_raw, mode)
                    red_r   = _to_refl(red_raw, mode)
                    green_r = _to_refl(green_raw, mode)

                    if _USE_CLEAN and mode == "s2":
                        cleaned = _fnc.clean_ndvi(
                            nir_raw.astype(np.int32), red_raw.astype(np.int32),
                            do_unmix=False
                        )
                        if cleaned["coverage_pct"] < 20.0:
                            log.warning("ndvi_real: %s coverage %.0f%% post-clean — skip",
                                        cid, cleaned["coverage_pct"])
                            continue
                        ndvi     = round(max(-1.0, min(1.0, cleaned["mean_ndvi"])), 3)
                        coverage = round(cleaned["coverage_pct"], 1)
                    else:
                        valid = (nir_r + red_r) > 0.02
                        if valid.sum() < 2:
                            log.warning("ndvi_real: %s — menos de 2 píxeles válidos", cid)
                            continue
                        ndvi     = round(float(((nir_r - red_r) / (nir_r + red_r))[valid].mean()), 3)
                        ndvi     = max(-1.0, min(1.0, ndvi))
                        coverage = None

                    valid_g = (nir_r + green_r) > 0.02
                    if valid_g.sum() < 2:
                        gndvi = round(ndvi * 0.93, 3)
                    else:
                        gndvi = round(float(((nir_r - green_r) / (nir_r + green_r))[valid_g].mean()), 3)
                        gndvi = max(-1.0, min(1.0, gndvi))

                    nst, nrec = _n_status(gndvi)
                    entry = {"ndvi": ndvi, "gndvi": gndvi, "n_status": nst, "n_rec": nrec}
                    if coverage is not None:
                        entry["coverage_pct"] = coverage
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
