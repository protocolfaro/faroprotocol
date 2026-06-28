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
# Layout Villa Olimpica (plano "NUMERACION DE CANCHAS 2024", AutoCAD 2017):
#
#  [General de la Guitarra]          [Dardo Cabo]
#   1FA  2FA    [4FA]                6FA  9FA
#                 [3FA]       5FA    7FA
#   1FP  2FP               [asfalt]  8FA  10FA
#  [============= Camino del Buen Ayre =============]
#
# Coordenadas: Villa Olimpica Velez Sarsfield, Ituzaingo (-34.6221, -58.7200).
# Metodo: extraccion vectorial PyMuPDF + escala calibrada desde poligonos FIFA
# reales de 1FA/2FA (185x120 PDF units, ratio 0.6508 vs FIFA 68/105=0.6476).
# Scale=1.766 PDF/m. Centros desde quad-centroid del plano; 6FA/10FA/1FP/2FP
# desde label text (~12m de offset tipico del CAD, aceptado).
# Dimensiones reales por grupo (del plano vectorial):
#   1FA/2FA/1FP/2FP: 105x68m (FIFA) / 105x70m (FP)
#   3FA/4FA/5FA/6FA: 88x61m
#   7FA/8FA/9FA/10FA: 95x63m

def _bbox(lat: float, lon: float,
          w_m: float = 105, h_m: float = 68, buf_m: float = 0) -> tuple:
    """Cancha bbox exacto al campo — sin buffer para alineación pixel-perfecta con SVG."""
    box = coords_to_bbox(lat, lon, h_m=h_m + 2 * buf_m, w_m=w_m + 2 * buf_m)
    return tuple(box)


CANCHA_BBOXES: dict[str, tuple] = {
    # Villa Olimpica — coordenadas corregidas 2026-06-19
    # (previas estaban en Liniers/Amalfitani, 12km de distancia)
    "1fa":  _bbox(-34.6219, -58.7243, w_m=105, h_m=68),
    "2fa":  _bbox(-34.6219, -58.7231, w_m=105, h_m=68),
    "3fa":  _bbox(-34.6219, -58.7198, w_m=88,  h_m=61),
    "4fa":  _bbox(-34.6214, -58.7194, w_m=88,  h_m=61),
    "5fa":  _bbox(-34.6228, -58.7186, w_m=88,  h_m=61),
    "6fa":  _bbox(-34.6220, -58.7185, w_m=88,  h_m=61),
    "7fa":  _bbox(-34.6219, -58.7175, w_m=95,  h_m=63),
    "8fa":  _bbox(-34.6217, -58.7168, w_m=95,  h_m=63),
    "9fa":  _bbox(-34.6216, -58.7160, w_m=95,  h_m=63),
    "10fa": _bbox(-34.6215, -58.7154, w_m=95,  h_m=63),
    "1fp":  _bbox(-34.6230, -58.7231, w_m=105, h_m=70),
    "2fp":  _bbox(-34.6230, -58.7218, w_m=105, h_m=70),
    # Amalfitani — bbox confirmed from field survey / field_timeseries.py
    "amalfitani": (-58.529227, -34.638442, -58.528373, -34.637358),
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
    # HLS (NASA LP DAAC) removido: HLSS30 = mismo S2 reprocesado a 30m (peor que 10m de PC/E84);
    # HLSL30 = mismo Landsat ya en fuente #3. Ambos requieren Earthdata Login sin credenciales
    # en Railway, fallaban con HTTP 404 en posiciones 1-2, sumando ~30s de overhead por ronda.
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
        "red_edge":   ["B05"],          # Red-edge 705nm — NDRE real para nitrógeno
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
        "red_edge":   ["B05", "rededge"],   # Red-edge para NDRE
        "scl":        ["scl", "SCL"],
        "scale":      "s2",
    },
    {
        "name":       "Landsat-C2-L2 (Element84)",
        "url":        "https://earth-search.aws.element84.com/v1",
        "collection": "landsat-c2-l2",
        "sign":       False,
        "cloud_prop": "eo:cloud_cover",
        "red":        ["red", "SR_B4", "sr_b4"],
        "nir":        ["nir08", "nir", "SR_B5", "sr_b5"],   # nir08 = Landsat 8/9 OLI Band 5 in E84
        "green":      ["green", "SR_B3", "sr_b3"],
        "blue":       ["blue", "SR_B2", "sr_b2"],
        "swir1":      ["swir16", "SR_B6", "sr_b6"],
        "qa_pixel":   ["qa_pixel", "QA_PIXEL"],   # C2 L2 cloud/shadow bitmap — replaces CLOSDI
        "scale":      "ls",
    },
]

# Rondas progresivas: (max_cloud_pct, ventana_dias)
# Rondas progresivas de búsqueda: (max_cloud_pct, ventana_dias)
# Invierno austral (jun-ago BsAs): nubosidad alta → ampliar ventana y umbral
import datetime as _dt
_MES = _dt.date.today().month
_INVIERNO = _MES in (5, 6, 7, 8)
_ROUNDS = [
    (25,  7),   # Ronda 1: imagen limpia reciente
    (45, 14),   # Ronda 2: algo de nubes, 2 semanas
    (65, 30),   # Ronda 3: invierno — mejor imagen parcialmente nublada que dato viejo
    (80, 45) if _INVIERNO else (65, 30),   # Ronda 4: solo en invierno — 45 días
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
                   max_cloud: float, limit: int = 1) -> list:
    """Busca items en una fuente STAC. Retorna lista vacía si falla.

    limit=1  → comportamiento original (rondas normales)
    limit>1  → retorna múltiples escenas para compositing BAP
    """
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
        search_kwargs: dict = dict(
            collections=[src["collection"]],
            bbox=_CLUSTER_BBOX,
            datetime=f"{dt_from}/{dt_to}",
            sortby="-properties.datetime",
        )
        if max_cloud < 100:
            search_kwargs["query"] = {src["cloud_prop"]: {"lt": max_cloud}}
        items = list(catalog.search(**search_kwargs).items())
        items = items[:limit]
        log.info("ndvi_real [%s]: %d items (cloud<%.0f%%, %s→%s)",
                 src["name"], len(items), max_cloud, dt_from, dt_to)
        for _it in items:
            _it_cloud = _it.properties.get(src.get("cloud_prop", "eo:cloud_cover"), "?")
            _it_dt    = _it.properties.get("datetime", "?")[:19]
            log.info("ndvi_real [%s]:   └─ id=%s date=%s cloud=%s%%",
                     src["name"], _it.id, _it_dt, _it_cloud)
        return items
    except Exception as exc:
        log.warning("ndvi_real [%s]: búsqueda falló: %s", src["name"], exc)
        return []


# ── BAP (Best Available Pixel) compositing ────────────────────────────────────
#
# Física: cada píxel de la cancha recibe el valor de la fecha donde el SCL
# (Scene Classification Layer) tiene mayor calidad, priorizando vegetación y
# suelo desnudo sobre nubes y sombras. Con 6-8 escenas en 30 días, la
# probabilidad de que algún píxel quede sin cobertura válida es <5% incluso
# en invierno austral (BsAs jun-ago).
#
# SCL classes (ESA Sentinel-2 L2A):
#   0=no_data, 1=defective, 2=dark, 3=shadow, 4=vegetation★, 5=bare_soil★,
#   6=water, 7=low_prob_cloud, 8=med_cloud, 9=high_cloud, 10=cirrus, 11=snow

def _scl_priority(scl_arr: "np.ndarray") -> "np.ndarray":
    """Convierte valores SCL a prioridad (mayor=mejor). Shape preservado."""
    import numpy as np
    p = np.zeros(scl_arr.shape, dtype=np.int8)
    p[scl_arr == 4] = 6   # vegetation — óptimo para NDVI
    p[scl_arr == 5] = 5   # bare soil
    p[scl_arr == 7] = 4   # low prob cloud — aceptable
    p[scl_arr == 6] = 3   # water (riego) — válido
    p[scl_arr == 2] = 2   # dark area — último recurso
    p[scl_arr == 11] = 1  # snow (no ocurre en BsAs, but safe)
    # 0 para shadow, cloud, cirrus, defective, no_data
    return p


def _landsat_cloud_mask(qa_arr: "np.ndarray") -> "np.ndarray":
    """Landsat C2 L2 QA_PIXEL: bits 1-4 (dilated cloud, cirrus, cloud, cloud shadow)."""
    import numpy as np
    qa = qa_arr.astype(np.uint16)
    return (((qa >> 1) | (qa >> 2) | (qa >> 3) | (qa >> 4)) & 1).astype(bool)


def _composite_scene(items: list, src: dict) -> "dict[str, dict] | None":
    """
    Best Available Pixel (BAP) composite sobre múltiples escenas STAC.

    Para cada píxel de cada cancha selecciona el valor espectral de la fecha
    con mejor clasificación SCL dentro de la ventana temporal. Genera ndvi_2d
    con cobertura próxima al 100% aunque cada escena individual esté parcialmente
    nublada.

    Requiere stackstac (Dask). Si no está disponible retorna None → fallback
    automático a las rondas normales.
    """
    import numpy as np
    try:
        import stackstac
    except ImportError:
        log.warning("ndvi composite: stackstac no disponible — omitiendo BAP")
        return None

    from rasterio.warp import transform_bounds

    mode = src["scale"]

    # Resolver assets contra el primer item (todos del mismo collection/sensor)
    band_map: dict[str, str | None] = {}
    spectral_assets: list[str] = []
    scl_asset: str | None = None

    for logical, candidates in [
        ("nir",   src["nir"]),
        ("red",   src["red"]),
        ("green", src["green"]),
        ("blue",  src.get("blue", [])),
        ("swir1", src.get("swir1", [])),
        ("red_edge", src.get("red_edge", [])),
    ]:
        for c in candidates:
            if c in items[0].assets:
                band_map[logical] = c
                if c not in spectral_assets:
                    spectral_assets.append(c)
                break
        else:
            band_map[logical] = None

    for c in src.get("scl", []):
        if c in items[0].assets:
            scl_asset = c
            break

    qa_asset: str | None = None
    if src.get("scale") == "ls":
        for c in src.get("qa_pixel", []):
            if c in items[0].assets:
                qa_asset = c
                break

    if not band_map.get("nir") or not band_map.get("red"):
        log.warning("ndvi composite: NIR/RED no encontrados en assets")
        return None

    resolution = 10 if mode == "s2" else 30

    try:
        da = stackstac.stack(
            items,
            assets=spectral_assets,
            bounds_latlon=list(_CLUSTER_BBOX),
            resolution=resolution,
            dtype="float32",
            fill_value=float("nan"),
            xy_coords="center",
            rescale=False,  # _to_refl() applies the correct scale per sensor
        ).compute()   # (time, band, Y, X)
    except Exception as exc:
        log.warning("ndvi composite: stackstac load falló: %s", exc)
        return None

    nT   = da.shape[0]
    H    = da.shape[-2]
    W    = da.shape[-1]

    def _band_all(name: str) -> "np.ndarray | None":
        key = band_map.get(name)
        if not key:
            return None
        try:
            raw = da.sel(band=key).values   # (T, H, W)
        except Exception:
            return None
        out = np.full_like(raw, np.nan)
        for t in range(nT):
            out[t] = _to_refl(raw[t], mode)
        return out

    nir_all      = _band_all("nir")
    red_all      = _band_all("red")
    green_all    = _band_all("green")
    blue_all     = _band_all("blue")
    swir1_all    = _band_all("swir1")
    red_edge_all = _band_all("red_edge")

    if nir_all is None or red_all is None:
        return None

    # ── SCL → prioridad por píxel ──────────────────────────────────────
    if scl_asset:
        try:
            da_scl = stackstac.stack(
                items,
                assets=[scl_asset],
                bounds_latlon=list(_CLUSTER_BBOX),
                resolution=20,
                dtype="float32",
                fill_value=0.0,
                xy_coords="center",
            ).compute()
            scl_all = da_scl.squeeze("band").values.astype(np.int16)  # (T, H_20m, W_20m)

            # Upsample 20m → 10m (nearest neighbour)
            h20, w20 = scl_all.shape[-2], scl_all.shape[-1]
            ry = max(1, int(round(H / h20)))
            rx = max(1, int(round(W / w20)))
            scl_up = np.repeat(np.repeat(scl_all, ry, axis=-2), rx, axis=-1)
            scl_up = scl_up[:, :H, :W]   # trim to exact size

            priority = np.stack([_scl_priority(scl_up[t]) for t in range(nT)])  # (T,H,W)
        except Exception as exc:
            log.warning("ndvi composite: SCL load falló: %s", exc)
            priority = None
    elif qa_asset:
        # ── QA_PIXEL → NDVI-weighted priority para Landsat C2 L2 BAP ──────────
        # Priority 1-99 (NDVI-ranked) for cloud-free pixels, 0 for masked pixels.
        # Picks the greenest valid observation per pixel across the composite window.
        try:
            da_qa = stackstac.stack(
                items,
                assets=[qa_asset],
                bounds_latlon=list(_CLUSTER_BBOX),
                resolution=30,
                dtype="float32",
                fill_value=0.0,
                xy_coords="center",
            ).compute()
            qa_all  = da_qa.squeeze("band").values.astype(np.uint16)
            cloud_t = np.stack([_landsat_cloud_mask(qa_all[t]) for t in range(nT)])
            if cloud_t.shape[-2:] != (H, W):
                ry = max(1, int(round(H / cloud_t.shape[-2])))
                rx = max(1, int(round(W / cloud_t.shape[-1])))
                cloud_t = np.repeat(np.repeat(cloud_t, ry, axis=-2), rx, axis=-1)
                cloud_t = cloud_t[:, :H, :W]
            with np.errstate(invalid="ignore", divide="ignore"):
                ndvi_t = (nir_all - red_all) / (nir_all + red_all + 1e-9)
            cloud_bad   = cloud_t | np.isnan(nir_all)
            ndvi_scaled = np.clip(ndvi_t * 50 + 51, 1, 99).astype(np.int8)
            priority    = np.where(cloud_bad, np.int8(0), ndvi_scaled)
        except Exception as exc:
            log.debug("ndvi composite: QA_PIXEL load non-fatal: %s", exc)
            priority = None
    else:
        priority = None

    if priority is not None:
        best_t        = np.argmax(priority, axis=0)      # (H, W)
        max_priority  = priority.max(axis=0)             # (H, W)
        bad_px        = max_priority == 0                # sin ningún píxel válido
    else:
        # Sin SCL ni QA_PIXEL: argmax NDVI como proxy (nubes → NDVI negativo/bajo)
        with np.errstate(invalid="ignore", divide="ignore"):
            ndvi_t = (nir_all - red_all) / (nir_all + red_all + 1e-9)
        ndvi_t = np.where(np.isnan(nir_all), -2.0, ndvi_t)
        best_t = np.argmax(ndvi_t, axis=0)
        bad_px = np.all(np.isnan(nir_all), axis=0)

    # ── Composite: fancy indexing (T,H,W) → (H,W) ────────────────────
    flat_t  = best_t.reshape(-1)
    flat_hw = np.arange(H * W)

    def _comp(arr: "np.ndarray | None") -> "np.ndarray | None":
        if arr is None:
            return None
        c = arr.reshape(nT, -1)[flat_t, flat_hw].reshape(H, W).astype(np.float32)
        c[bad_px] = np.nan
        return c

    nir_c      = _comp(nir_all)
    red_c      = _comp(red_all)
    green_c    = _comp(green_all)
    blue_c     = _comp(blue_all)
    swir1_c    = _comp(swir1_all)
    red_edge_c = _comp(red_edge_all)

    # Fecha dominante = la que más píxeles aportó al composite
    counts       = np.bincount(flat_t[~bad_px.reshape(-1)], minlength=nT)
    dom_t        = int(np.argmax(counts))
    dom_date     = items[dom_t].properties.get("datetime", "?")[:10]

    crs_str  = str(da.attrs.get("crs", "EPSG:32721"))
    affine_t = da.attrs.get("transform")

    results: dict[str, dict] = {}

    for cid, (minx, miny, maxx, maxy) in CANCHA_BBOXES.items():
        try:
            nx0, ny0, nx1, ny1 = transform_bounds("EPSG:4326", crs_str,
                                                   minx, miny, maxx, maxy)
            if affine_t is None:
                continue
            inv = ~affine_t
            c0, r0 = inv * (nx0, ny1)
            c1, r1 = inv * (nx1, ny0)
            ri0 = max(0, int(r0)); ri1 = min(H, int(r1) + 1)
            ci0 = max(0, int(c0)); ci1 = min(W, int(c1) + 1)
            if ri0 >= ri1 or ci0 >= ci1:
                continue

            def _crop(a: "np.ndarray | None") -> "np.ndarray | None":
                return a[ri0:ri1, ci0:ci1] if a is not None else None

            scl_mask = _crop(bad_px).copy() if bad_px is not None else None

            entry = _compute_indices(
                _crop(nir_c), _crop(red_c), _crop(green_c),
                _crop(blue_c), _crop(swir1_c), scl_mask, mode,
                red_edge_c=_crop(red_edge_c),
            )
            if entry is not None:
                entry["composite_date"]     = dom_date
                entry["composite_n_scenes"] = nT
                results[cid] = entry
        except Exception as exc:
            log.warning("ndvi composite: %s: %s", cid, exc)

    return results if results else None


def _composite_multisource(src_items: list) -> "dict[str, dict] | None":
    """
    Multi-sensor BAP: merges _composite_scene results from (src, items) pairs.

    Landsat 30m results are processed first; Sentinel-2 10m overrides them so
    the final result always uses the highest-resolution sensor per cancha.
    Only the first S2 source is composited — PC and E84 index the same scenes.
    """
    merged: dict[str, dict] = {}
    s2_done = False
    ls_groups = [(s, i) for s, i in src_items if s["scale"] != "s2"]
    s2_groups = [(s, i) for s, i in src_items if s["scale"] == "s2"]
    for src, items in ls_groups + s2_groups:
        if not items:
            continue
        if src["scale"] == "s2" and s2_done:
            continue
        try:
            result = _composite_scene(items, src)
        except Exception as _cse:
            log.error("ndvi_real multisource: _composite_scene [%s] falló: %s", src["name"], _cse)
            continue
        if not result:
            log.warning("ndvi_real multisource: [%s] composite sin píxeles válidos (%d escenas)",
                        src["name"], len(items))
            continue
        for entry in result.values():
            entry["sensor"] = src["name"]
        merged.update(result)
        if src["scale"] == "s2":
            s2_done = True
        log.info("ndvi_real multisource: +%d canchas [%s · %d esc]",
                 len(result), src["name"], len(items))
    return merged if merged else None


def _compute_indices(
    nir_c, red_c, green_c, blue_c, swir1_c, scl_mask, mode: str,
    red_edge_c=None
) -> dict | None:
    """Shared index computation used by both rasterio and stackstac paths.

    Returns entry dict with ndvi + optional gndvi/bsi/ndwi/coverage_pct,
    or None if insufficient valid pixels.
    """
    import numpy as np
    try:
        import faro_ndvi_clean as _fnc
        _USE_CLEAN = True
    except ImportError:
        _USE_CLEAN = False

    _SCL_BAD = frozenset({0, 1, 3, 8, 9, 10})

    if nir_c is None or red_c is None or nir_c.size == 0:
        return None

    if _USE_CLEAN and mode == "s2":
        nir_dn = (nir_c * 10000).astype(np.int32)
        red_dn = (red_c * 10000).astype(np.int32)
        cleaned = _fnc.clean_ndvi(nir_dn, red_dn, do_unmix=True, external_mask=scl_mask)
        if cleaned["coverage_pct"] < 20.0:
            return None
        ndvi     = round(max(-1.0, min(1.0, cleaned["mean_ndvi"])), 3)
        coverage = round(cleaned["coverage_pct"], 1)
    else:
        with np.errstate(invalid="ignore", divide="ignore"):
            _closdi = 250.0 * (nir_c + red_c) / (nir_c + 2.4 * red_c + 1.0)
        valid = (nir_c + red_c) > 0.02
        valid &= (_closdi >= 34.0) | ((nir_c + red_c) <= 0.02)
        valid &= (nir_c >= red_c)
        if scl_mask is not None:
            valid &= ~scl_mask
        if valid.sum() < 2:
            return None
        with np.errstate(invalid="ignore", divide="ignore"):
            ndvi = round(float(((nir_c - red_c) / (nir_c + red_c + 1e-9))[valid].mean()), 3)
        ndvi     = max(-1.0, min(1.0, ndvi))
        coverage = None

    valid_g = ((nir_c + green_c) > 0.02) if green_c is not None else np.zeros(nir_c.shape, bool)
    if scl_mask is not None:
        valid_g &= ~scl_mask
    if valid_g.sum() >= 2:
        with np.errstate(invalid="ignore", divide="ignore"):
            gndvi = round(float(((nir_c - green_c) / (nir_c + green_c + 1e-9))[valid_g].mean()), 3)
        gndvi = max(-1.0, min(1.0, gndvi))
    else:
        gndvi = round(ndvi * 0.93, 3)

    nst, nrec = _n_status(gndvi)
    # Array 2D normalizado [0-1] para heatmap_gen — pixel-level real del satélite
    ndvi_2d_raw = ((nir_c - red_c) / (nir_c + red_c + 1e-9))
    ndvi_2d_raw = np.clip(ndvi_2d_raw, -1.0, 1.0)
    if scl_mask is not None:
        ndvi_2d_raw[scl_mask] = np.nan
    # Normalizar a [0,1] para colormap (vmin=0.10, vmax=0.65 igual que heatmap_gen)
    ndvi_2d_norm = np.clip((ndvi_2d_raw - 0.10) / (0.65 - 0.10), 0.0, 1.0)
    # Serializar como lista de listas compacta (float16 para no inflar el JSON)
    ndvi_2d_list = ndvi_2d_norm.astype(np.float16).tolist()
    entry: dict = {"ndvi": ndvi, "gndvi": gndvi, "n_status": nst, "n_rec": nrec,
                   "ndvi_2d": ndvi_2d_list}
    if coverage is not None:
        entry["coverage_pct"] = coverage

    valid_base = (nir_c + red_c) > 0.02
    if scl_mask is not None:
        valid_base &= ~scl_mask

    if swir1_c is not None and blue_c is not None:
        v = valid_base & ((swir1_c + red_c + nir_c + blue_c) > 0.04)
        if v.sum() >= 2:
            with np.errstate(invalid="ignore", divide="ignore"):
                num = (swir1_c + red_c) - (nir_c + blue_c)
                den = (swir1_c + red_c) + (nir_c + blue_c) + 1e-9
                bsi = round(max(-1.0, min(1.0, float((num / den)[v].mean()))), 3)
            entry["bsi"] = bsi

    if swir1_c is not None:
        v = valid_base & ((nir_c + swir1_c) > 0.02)
        if v.sum() >= 2:
            with np.errstate(invalid="ignore", divide="ignore"):
                ndwi = round(max(-1.0, min(1.0,
                    float(((nir_c - swir1_c) / (nir_c + swir1_c + 1e-9))[v].mean()))), 3)
            entry["ndwi"] = ndwi

    # EVI2 = 2.5 * (NIR - RED) / (NIR + 2.4*RED + 1) — no requiere banda azul
    v_evi = valid_base & ((nir_c + red_c) > 0.02)
    if v_evi.sum() >= 2:
        with np.errstate(invalid="ignore", divide="ignore"):
            evi2 = round(max(-1.0, min(2.5,
                float((2.5 * (nir_c - red_c) / (nir_c + 2.4 * red_c + 1.0))[v_evi].mean()))), 3)
        entry["evi2"] = evi2

    # NDRE = (NIR - RedEdge) / (NIR + RedEdge) — B08/B05 Sentinel-2
    # CCCI = NDRE / NDVI — Canopy Chlorophyll Content Index (Barnes et al. 2000)
    # Normaliza el contenido de clorofila por biomasa: canchas con más biomasa pero
    # menos clorofila relativa muestran CCCI bajo aunque su NDVI absoluto sea alto.
    if red_edge_c is not None:
        v_re = valid_base & ((nir_c + red_edge_c) > 0.02)
        if scl_mask is not None:
            v_re &= ~scl_mask
        if v_re.sum() >= 2:
            with np.errstate(invalid="ignore", divide="ignore"):
                ndre = round(max(-1.0, min(1.0,
                    float(((nir_c - red_edge_c) / (nir_c + red_edge_c + 1e-9))[v_re].mean()))), 3)
            entry["ndre"] = ndre
            if entry.get("ndvi") and entry["ndvi"] > 0:
                entry["ccci"] = round(ndre / entry["ndvi"], 4)

    return entry


def _read_canchas_stackstac(item, src: dict) -> dict[str, dict]:
    """Load all canchas in one Dask operation via stackstac.

    Fetches the entire cluster bbox as a single xarray DataArray,
    then slices per cancha in pure numpy — no per-cancha HTTP round-trips.
    Falls back to _read_canchas_rasterio() automatically on import error.
    """
    import numpy as np
    import stackstac
    from rasterio.warp import transform_bounds

    mode = src["scale"]

    # Build asset list from src config (first candidate present in item.assets wins)
    band_map: dict[str, str | None] = {}
    spectral_assets: list[str] = []
    scl_asset: str | None = None

    for logical, candidates in [
        ("nir",      src["nir"]),
        ("red",      src["red"]),
        ("green",    src["green"]),
        ("blue",     src.get("blue", [])),
        ("swir1",    src.get("swir1", [])),
        ("red_edge", src.get("red_edge", [])),
    ]:
        for c in candidates:
            if c in item.assets:
                band_map[logical] = c
                if c not in spectral_assets:
                    spectral_assets.append(c)
                break
        else:
            band_map[logical] = None

    for c in src.get("scl", []):
        if c in item.assets:
            scl_asset = c
            break

    if not band_map.get("nir") or not band_map.get("red"):
        raise ValueError("NIR or RED band not found in STAC item")

    resolution = 10 if mode == "s2" else 30

    # ── Load all spectral bands for the full cluster bbox in one Dask compute ──
    da = stackstac.stack(
        [item],
        assets=spectral_assets,
        bounds_latlon=list(_CLUSTER_BBOX),
        resolution=resolution,
        dtype="float32",
        fill_value=0.0,
        xy_coords="center",
    )
    cluster = da.squeeze("time").compute()   # (band, y, x)

    # ── Load SCL at 20m separately (needs integer class values, no float scale) ──
    scl_cluster: np.ndarray | None = None
    if scl_asset:
        try:
            da_scl = stackstac.stack(
                [item],
                assets=[scl_asset],
                bounds_latlon=list(_CLUSTER_BBOX),
                resolution=20,
                dtype="float32",  # will cast to int after load
                fill_value=0.0,
                xy_coords="center",
            )
            scl_cluster = da_scl.squeeze("time").squeeze("band").compute().values.astype(np.int16)
        except Exception as _scl_e:
            log.debug("ndvi_real stackstac: SCL load non-fatal: %s", _scl_e)

    # ── QA_PIXEL for Landsat C2 L2 cloud masking (when no SCL available) ─────
    qa_cluster: np.ndarray | None = None
    if mode == "ls" and scl_cluster is None:
        _qa_name = next((c for c in src.get("qa_pixel", []) if c in item.assets), None)
        if _qa_name:
            try:
                da_qa = stackstac.stack(
                    [item],
                    assets=[_qa_name],
                    bounds_latlon=list(_CLUSTER_BBOX),
                    resolution=30,
                    dtype="float32",
                    fill_value=0.0,
                    xy_coords="center",
                )
                qa_cluster = (da_qa.squeeze("time").squeeze("band")
                              .compute().values.astype(np.uint16))
            except Exception as _qa_e:
                log.debug("ndvi_real stackstac: QA_PIXEL load non-fatal: %s", _qa_e)

    # ── Spatial helpers ───────────────────────────────────────────────────────
    crs_str  = str(da.attrs.get("crs", "EPSG:32721"))
    affine_t = da.attrs.get("transform")   # affine.Affine

    h_full = cluster.shape[-2]
    w_full = cluster.shape[-1]

    def _band_np(name: str) -> np.ndarray | None:
        key = band_map.get(name)
        if not key:
            return None
        arr = cluster.sel(band=key).values
        return _to_refl(arr, mode)

    nir_full      = _band_np("nir")
    red_full      = _band_np("red")
    green_full    = _band_np("green")
    blue_full     = _band_np("blue")
    swir1_full    = _band_np("swir1")
    red_edge_full = _band_np("red_edge")

    results: dict[str, dict] = {}

    for cid, (minx, miny, maxx, maxy) in CANCHA_BBOXES.items():
        try:
            # Project cancha bbox to native CRS then find pixel indices
            nx0, ny0, nx1, ny1 = transform_bounds("EPSG:4326", crs_str, minx, miny, maxx, maxy)

            if affine_t is not None:
                # Inverse affine: (col, row) = ~T * (x, y)  [y decreases with row]
                inv = ~affine_t
                c0, r0 = inv * (nx0, ny1)   # top-left in pixel space
                c1, r1 = inv * (nx1, ny0)   # bottom-right
                ri0, ri1 = max(0, int(r0)),  min(h_full, int(r1) + 1)
                ci0, ci1 = max(0, int(c0)),  min(w_full, int(c1) + 1)
            else:
                # Fallback: scan coordinate arrays
                x_v = cluster.x.values
                y_v = cluster.y.values
                ci0 = int(np.searchsorted(x_v, nx0))
                ci1 = int(np.searchsorted(x_v, nx1)) + 1
                ri0 = int(np.searchsorted(-y_v, -ny1))
                ri1 = int(np.searchsorted(-y_v, -ny0)) + 1
                ri0 = max(0, ri0); ri1 = min(h_full, ri1)
                ci0 = max(0, ci0); ci1 = min(w_full, ci1)

            # Minimum 2×2 px — integer truncation can collapse 30m Landsat crops to 0
            ri1 = min(h_full, max(ri1, ri0 + 2))
            ci1 = min(w_full, max(ci1, ci0 + 2))
            if ri0 >= ri1 or ci0 >= ci1:
                continue

            def _crop(a: np.ndarray | None) -> np.ndarray | None:
                return a[ri0:ri1, ci0:ci1] if a is not None else None

            nir_c      = _crop(nir_full)
            red_c      = _crop(red_full)
            green_c    = _crop(green_full)
            blue_c     = _crop(blue_full)
            swir1_c    = _crop(swir1_full)
            red_edge_c = _crop(red_edge_full)

            # SCL mask: upsample 20m→10m via nearest-neighbour (np.repeat)
            scl_mask = None
            if scl_cluster is not None:
                try:
                    scl_h, scl_w = scl_cluster.shape
                    sr0 = max(0, int(ri0 * scl_h / h_full))
                    sr1 = min(scl_h, int(ri1 * scl_h / h_full) + 1)
                    sc0 = max(0, int(ci0 * scl_w / w_full))
                    sc1 = min(scl_w, int(ci1 * scl_w / w_full) + 1)
                    scl_crop = scl_cluster[sr0:sr1, sc0:sc1]
                    # Nearest-neighbour upsample to 10m
                    up = np.repeat(np.repeat(scl_crop, 2, axis=0), 2, axis=1)
                    # Trim to match nir_c dimensions
                    h_c = ri1 - ri0; w_c = ci1 - ci0
                    scl_mask = np.isin(up[:h_c, :w_c], list(frozenset({0, 1, 3, 8, 9, 10})))
                except Exception as _sm_e:
                    log.debug("ndvi_real stackstac: %s SCL crop: %s", cid, _sm_e)
            elif qa_cluster is not None:
                try:
                    h_c = ri1 - ri0; w_c = ci1 - ci0
                    qa_crop = qa_cluster[ri0:ri1, ci0:ci1]
                    if qa_crop.shape != (h_c, w_c):
                        ry = max(1, h_c // max(1, qa_crop.shape[0]))
                        rx = max(1, w_c // max(1, qa_crop.shape[1]))
                        qa_up = np.repeat(np.repeat(qa_crop, ry, axis=0), rx, axis=1)
                        qa_crop = qa_up[:h_c, :w_c]
                    scl_mask = _landsat_cloud_mask(qa_crop)
                except Exception as _qam_e:
                    log.debug("ndvi_real stackstac: %s QA_PIXEL crop: %s", cid, _qam_e)

            entry = _compute_indices(nir_c, red_c, green_c, blue_c, swir1_c, scl_mask, mode,
                                     red_edge_c=red_edge_c)
            if entry is None:
                log.warning("ndvi_real stackstac: %s — cobertura insuficiente", cid)
                continue
            results[cid] = entry

        except Exception as exc:
            log.warning("ndvi_real stackstac: %s: %s", cid, exc)

    return results


def _read_canchas(item, src: dict) -> dict[str, dict]:
    """Dispatch to stackstac (batch Dask) or rasterio (per-window) path.

    stackstac fetches all bands for the cluster bbox in one compute() call,
    then slices per cancha in numpy — significantly fewer HTTP round-trips.
    Falls back automatically if stackstac is unavailable or errors.
    """
    try:
        import stackstac as _ss  # noqa: F401 — probe import only
        return _read_canchas_stackstac(item, src)
    except ImportError:
        pass
    except Exception as exc:
        log.warning("ndvi_real: stackstac error, fallback rasterio: %s", exc)
    return _read_canchas_rasterio(item, src)


def _read_canchas_rasterio(item, src: dict) -> dict[str, dict]:
    """Per-cancha windowed COG read for all canchas.

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

    blue_url     = _opt("blue")
    swir1_url    = _opt("swir1")
    red_edge_url = _opt("red_edge")  # B05 Sentinel-2 705nm — NDRE real
    scl_url      = _opt("scl")
    qa_url       = _opt("qa_pixel") if mode == "ls" else None

    # SCL classes to discard: no_data, defective, cloud shadow, medium cloud, high cloud, cirrus
    _SCL_BAD = frozenset({0, 1, 3, 8, 9, 10})

    results: dict[str, dict] = {}

    with contextlib.ExitStack() as stack:
        stack.enter_context(_RioEnv(GDAL_HTTP_TIMEOUT=30, GDAL_HTTP_CONNECTTIMEOUT=15))
        s_nir   = stack.enter_context(rasterio.open(nir_url))
        s_red   = stack.enter_context(rasterio.open(red_url))
        s_green = stack.enter_context(rasterio.open(green_url))
        s_blue     = stack.enter_context(rasterio.open(blue_url))     if blue_url     else None
        s_swir1    = stack.enter_context(rasterio.open(swir1_url))    if swir1_url    else None
        s_red_edge = stack.enter_context(rasterio.open(red_edge_url)) if red_edge_url else None
        s_scl      = stack.enter_context(rasterio.open(scl_url))      if scl_url      else None
        s_qa       = stack.enter_context(rasterio.open(qa_url))       if qa_url       else None

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

                # Helper: nearest-neighbor resize to NIR grid (for 20m bands)
                def _resize(arr):
                    if arr is None or arr.shape == nir_raw.shape:
                        return arr
                    if arr.size == 0:
                        return None
                    ri = (np.arange(nir_raw.shape[0]) * arr.shape[0]
                          / nir_raw.shape[0]).astype(int)
                    ci = (np.arange(nir_raw.shape[1]) * arr.shape[1]
                          / nir_raw.shape[1]).astype(int)
                    return arr[np.ix_(ri, ci)]

                # SCL mask — resample to NIR pixel grid (SCL 20m, NIR 10m)
                scl_mask = None
                if s_scl is not None:
                    try:
                        scl_raw = s_scl.read(1, window=_win(s_scl),
                                             resampling=Resampling.nearest)
                        scl_raw = _resize(scl_raw)
                        if scl_raw is not None and scl_raw.shape == nir_raw.shape:
                            scl_mask = np.isin(scl_raw, list(_SCL_BAD))
                    except Exception as _se:
                        log.debug("ndvi_real: %s SCL non-fatal: %s", cid, _se)
                elif s_qa is not None:
                    try:
                        qa_raw = s_qa.read(1, window=_win(s_qa),
                                           resampling=Resampling.nearest).astype("float32")
                        qa_raw = _resize(qa_raw)
                        if qa_raw is not None and qa_raw.shape == nir_raw.shape:
                            scl_mask = _landsat_cloud_mask(qa_raw.astype(np.uint16))
                    except Exception as _qe:
                        log.debug("ndvi_real: %s QA_PIXEL non-fatal: %s", cid, _qe)

                # Optional bands — 20m bands (swir1, red_edge) are resized to 10m NIR grid
                blue_r  = None
                swir1_r = None
                if s_blue is not None:
                    try:
                        blue_r = _resize(_to_refl(
                            s_blue.read(1, window=_win(s_blue)).astype("float32"), mode))
                    except Exception:
                        pass
                if s_swir1 is not None:
                    try:
                        swir1_r = _resize(_to_refl(
                            s_swir1.read(1, window=_win(s_swir1)).astype("float32"), mode))
                    except Exception:
                        pass
                red_edge_r = None
                if s_red_edge is not None:
                    try:
                        red_edge_r = _resize(_to_refl(
                            s_red_edge.read(1, window=_win(s_red_edge)).astype("float32"), mode))
                    except Exception:
                        pass

                entry = _compute_indices(nir_r, red_r, green_r, blue_r, swir1_r,
                                         scl_mask, mode, red_edge_c=red_edge_r)
                if entry is None:
                    log.warning("ndvi_real: %s — cobertura insuficiente post-clean", cid)
                    continue
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

    for _rd_i, (max_cloud, days) in enumerate(_ROUNDS):
        dt_from = (today - timedelta(days=days)).isoformat()
        dt_to   = today.isoformat()
        log.info("ndvi_real: ronda %d cloud≤%d%% ventana %dd (%s→%s)",
                 _rd_i + 1, max_cloud, days, dt_from, dt_to)

        for src in _STAC_SOURCES:
            items = _search_source(src, dt_from, dt_to, max_cloud, limit=1)
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

            # Prithvi-EO-2.0 enrichment — adds prithvi_confidence + prithvi_flag per cancha
            prithvi_ok = False
            try:
                import prithvi_enrich
                p_result = prithvi_enrich.enrich_canchas(
                    canchas, item=item, src=src["name"].lower().split()[0], mes=today.month
                )
                if p_result["enriquecido"]:
                    canchas    = p_result["canchas"]
                    prithvi_ok = True
                    log.info("ndvi_real: prithvi OK — fuente=%s", p_result["fuente"])
                else:
                    log.debug("ndvi_real: prithvi enrich returned no enriched data")
            except Exception as _pe:
                log.warning("ndvi_real: prithvi_enrich (non-fatal): %s", _pe)

            log.info("ndvi_real OK [%s] — %s · nub %.0f%% · %d canchas · prithvi=%s",
                     src["name"], img_dt, cloud, len(canchas), prithvi_ok)
            return {
                "fuente":             f"{src['collection']} · {src['name']} · {img_dt} · nub {cloud:.0f}%",
                "fecha_imagen":       img_dt,
                "nubosidad_pct":      round(cloud, 1),
                "canchas":            canchas,
                "prithvi_enriquecido": prithvi_ok,
            }

        # ── Mini-BAP 5d: after Round 1 fails, composite S2+Landsat before escalating ──
        # Combina todas las escenas de los últimos 5 días de todos los sensores.
        # Landsat Jun-19 (1%, 5d) + S2 Jun-23 (40%, 2d) → selección píxel-a-píxel →
        # efectivamente ≤5d de frescura aunque ninguna escena individual esté limpia.
        if _rd_i == 0:
            _mbap_from = (today - timedelta(days=5)).isoformat()
            _mbap_to   = today.isoformat()
            _mbap_src_items: list = []
            _ls_srcs = [s for s in _STAC_SOURCES if s["scale"] != "s2"]
            _s2_srcs = [s for s in _STAC_SOURCES if s["scale"] == "s2"]
            # Landsat first (lower priority → overridden by S2 in _composite_multisource)
            for _msrc in _ls_srcs + _s2_srcs[:1]:
                _mitems = _search_source(_msrc, _mbap_from, _mbap_to, max_cloud=80, limit=4)
                if _mitems:
                    _mbap_src_items.append((_msrc, _mitems))
            if not _mbap_src_items:
                _next_s2 = (today + timedelta(days=max(0, 5 - (today - date.fromisoformat(_mbap_from)).days % 5))).isoformat()
                log.warning(
                    "Mini-BAP 5d: 0 escenas en ventana %s/%s (cloud<80%%) — "
                    "sin pasada S2/Landsat en 5 dias; proxima S2 estimada ~%s",
                    _mbap_from, _mbap_to, _next_s2,
                )
            else:
                _n_esc = sum(len(x[1]) for x in _mbap_src_items)
                log.info("ndvi_real Mini-BAP 5d: %d escenas · %d fuentes", _n_esc, len(_mbap_src_items))
                _mbap_result = _composite_multisource(_mbap_src_items)
                if _mbap_result:
                    _mbap_dates = sorted({
                        v.get("composite_date", _mbap_from)
                        for v in _mbap_result.values() if isinstance(v, dict)
                    })
                    log.info("ndvi_real Mini-BAP OK — %d canchas · fechas=%s",
                             len(_mbap_result), _mbap_dates)
                    return {
                        "fuente":              "miniBAP_5d · " + "+".join(s["name"] for s, _ in _mbap_src_items),
                        "fecha_imagen":        _mbap_dates[-1] if _mbap_dates else _mbap_from,
                        "nubosidad_pct":       0.0,
                        "canchas":             _mbap_result,
                        "prithvi_enriquecido": False,
                        "metodo":              "COMPOSITE_MINI_BAP_5D",
                    }
                else:
                    log.warning(
                        "Mini-BAP 5d: encontradas %d escenas (%d fuentes) pero composite retorno vacio"
                        " — revisar _composite_multisource()",
                        _n_esc, len(_mbap_src_items),
                    )

            # ── BAP 30d: composite antes de aceptar imágenes viejas de Round 2+ ──
            # Solo S2 10m (PC + E84); Landsat 30m no aporta resolución al composite.
            # _composite_scene retorna None si stackstac no está → fallback natural a Round 2.
            log.info("ndvi_real BAP 30d: buscando composite pixel-a-pixel antes de Round 2")
            _bap_from = (today - timedelta(days=30)).isoformat()
            _bap_to   = today.isoformat()
            for _bap_src in _STAC_SOURCES[:2]:
                _bap_items = _search_source(_bap_src, _bap_from, _bap_to, max_cloud=95, limit=8)
                if not _bap_items:
                    log.info("ndvi_real BAP 30d [%s]: 0 escenas en 30d", _bap_src["name"])
                    continue
                log.info("ndvi_real BAP 30d [%s]: compositing %d escenas", _bap_src["name"], len(_bap_items))
                _bap_canchas = _composite_scene(_bap_items, _bap_src)
                if _bap_canchas:
                    _dom = next(iter(_bap_canchas.values())).get("composite_date", _bap_from)
                    log.info("ndvi_real BAP 30d OK — %d canchas · fecha_dom=%s · %d escenas",
                             len(_bap_canchas), _dom, len(_bap_items))
                    return {
                        "fuente":              f"BAP_composite · {_bap_src['name']} · 30d · {len(_bap_items)}esc",
                        "fecha_imagen":        _dom,
                        "nubosidad_pct":       0.0,
                        "canchas":             _bap_canchas,
                        "prithvi_enriquecido": False,
                        "metodo":              "COMPOSITE_BAP_30D",
                    }
                log.warning("ndvi_real BAP 30d [%s]: composite sin pixeles validos", _bap_src["name"])
            log.info("ndvi_real BAP 30d: sin resultado — continuando a Round 2 (fallback imagen vieja)")

    # ── OpenEO CDSE — composite server-side cuando BAP local falla ────────
    # Ventaja: procesamiento en CDSE sin descargar tiles completos; SCL mask
    # server-side; latencia 2-3h desde el pase. Requiere COPERNICUS_USER+PASS.
    try:
        from ndvi_openeo import fetch_ndvi_openeo as _oeo_fetch
        _oeo_result = _oeo_fetch(window_days=45)
        if _oeo_result:
            log.info("ndvi_real: OpenEO CDSE OK — %d canchas",
                     len(_oeo_result.get("canchas", {})))
            return _oeo_result
    except Exception as _oeo_e:
        log.warning("ndvi_real: openeo CDSE (non-fatal): %s", _oeo_e)

    # ── Kalman gap-fill — fallback temporal cuando no hay óptico ─────────
    _kalman_merged: dict = {}
    for _kv in ["amalfitani", "villa_olimpica"]:
        try:
            import faro_kalman_gapfill as _kgf
            _k = _kgf.gap_fill_today(venue_id=_kv)
            if _k and _k.get("canchas"):
                _kalman_merged.update(_k["canchas"])
        except Exception as _ke:
            log.warning("ndvi_real: kalman %s (non-fatal): %s", _kv, _ke)
    if _kalman_merged:
        log.info("ndvi_real: Kalman gap-fill activado — %d canchas", len(_kalman_merged))
        return {
            "fuente":        "kalman_gapfill",
            "fecha_imagen":  today.isoformat(),
            "nubosidad_pct": 100.0,
            "canchas":       _kalman_merged,
            "prithvi_enriquecido": False,
        }

    # ── CloudBreaker HF — fusión SAR→S2 para cada venue ─────────────────
    # CB_VENUES env var (comma-separated) controla qué venues se intentan.
    # Ejemplo Railway: CB_VENUES=amalfitani,villa_olimpica,poli
    _cb_venues = [v.strip() for v in
                  os.environ.get("CB_VENUES", "amalfitani,villa_olimpica").split(",")
                  if v.strip()]
    _cb_merged: dict = {}
    for _cbv in _cb_venues:
        try:
            import faro_cloudbreaker_hf as _cbhf
            _cb = _cbhf.reconstruct(venue_id=_cbv)
            if _cb and _cb.get("canchas"):
                _cb_merged.update(_cb["canchas"])
        except Exception as _cbe:
            log.warning("ndvi_real: cloudbreaker %s (non-fatal): %s", _cbv, _cbe)
    if _cb_merged:
        log.info("ndvi_real: CloudBreaker HF activado — %d canchas", len(_cb_merged))
        return {
            "fuente":              "cloudbreaker_sar_fusion",
            "fecha_imagen":        today.isoformat(),
            "nubosidad_pct":       100.0,
            "canchas":             _cb_merged,
            "prithvi_enriquecido": False,
            "metodo_generacion":   "CLOUDBREAKER_SAR_FUSION",
        }

    log.warning("ndvi_real: ninguna fuente devolvio datos — NDVI anterior mantenido")
    return None


if __name__ == "__main__":
    import sys as _sys
    _debug = "--debug" in _sys.argv
    logging.basicConfig(
        level=logging.DEBUG if _debug else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[logging.StreamHandler(_sys.stdout)],
    )
    if _debug:
        log.info("ndvi_real __main__: modo DEBUG activo")
    log.info("ndvi_real __main__: fetch + persist a Supabase via satellite_pipeline")
    _data = fetch_ndvi()
    if not _data:
        log.warning("ndvi_real __main__: sin datos NDVI — Supabase no actualizado")
        _sys.exit(1)
    log.info("ndvi_real __main__: NDVI OK (%d canchas) fuente=%s fecha=%s",
             len(_data.get("canchas", {})), _data.get("fuente", "?"), _data.get("fecha_imagen", "?"))
    try:
        import satellite_pipeline as _sp
        _result = _sp.run_satellite_cycle(_data, force=True)
        log.info("ndvi_real __main__: satellite_pipeline OK — %s", _result)
    except Exception as _exc:
        log.error("ndvi_real __main__: satellite_pipeline falló: %s", _exc)
        _sys.exit(1)
