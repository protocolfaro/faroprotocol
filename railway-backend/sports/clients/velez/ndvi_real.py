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
    # Más sensible a nitrógeno que GNDVI. Reemplaza ndre=ndvi*0.65 hardcodeado.
    if red_edge_c is not None:
        v_re = valid_base & ((nir_c + red_edge_c) > 0.02)
        if scl_mask is not None:
            v_re &= ~scl_mask
        if v_re.sum() >= 2:
            with np.errstate(invalid="ignore", divide="ignore"):
                ndre = round(max(-1.0, min(1.0,
                    float(((nir_c - red_edge_c) / (nir_c + red_edge_c + 1e-9))[v_re].mean()))), 3)
            entry["ndre"] = ndre

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
        ("nir",   src["nir"]),
        ("red",   src["red"]),
        ("green", src["green"]),
        ("blue",  src.get("blue", [])),
        ("swir1", src.get("swir1", [])),
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

    nir_full   = _band_np("nir")
    red_full   = _band_np("red")
    green_full = _band_np("green")
    blue_full  = _band_np("blue")
    swir1_full = _band_np("swir1")

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

            if ri0 >= ri1 or ci0 >= ci1:
                continue

            def _crop(a: np.ndarray | None) -> np.ndarray | None:
                return a[ri0:ri1, ci0:ci1] if a is not None else None

            nir_c   = _crop(nir_full)
            red_c   = _crop(red_full)
            green_c = _crop(green_full)
            blue_c  = _crop(blue_full)
            swir1_c = _crop(swir1_full)

            # SCL mask: upsample 20m→10m via nearest-neighbour (np.repeat)
            scl_mask = None
            if scl_cluster is not None:
                try:
                    factor = resolution // 20  # 0.5 for s2 10m, but we use 2× for 20m SCL
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

            entry = _compute_indices(nir_c, red_c, green_c, blue_c, swir1_c, scl_mask, mode)
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
                red_edge_r = None
                if s_red_edge is not None:
                    try:
                        red_edge_r = _to_refl(
                            s_red_edge.read(1, window=_win(s_red_edge)).astype("float32"), mode)
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

    # ── Kalman gap-fill — si no hay imagen limpia disponible ─────────────
    try:
        import faro_kalman_gapfill as _kgf
        _k_result = _kgf.gap_fill_today(venue_id="amalfitani")
        if _k_result:
            log.info("ndvi_real: Kalman gap-fill activado — margen=%.3f",
                     list(_k_result["canchas"].values())[0].get("margen_error_kalman", 0))
            return _k_result
    except Exception as _ke:
        log.warning("ndvi_real: kalman gap-fill (non-fatal): %s", _ke)

    # ── CloudBreaker HF — segundo fallback SAR→S2 fusion ─────────────────
    try:
        import faro_cloudbreaker_hf as _cbhf
        _cb_result = _cbhf.reconstruct(venue_id="amalfitani")
        if _cb_result:
            log.info("ndvi_real: CloudBreaker HF activado — %d canchas",
                     len(_cb_result.get("canchas", {})))
            return _cb_result
    except Exception as _cbe:
        log.warning("ndvi_real: cloudbreaker HF (non-fatal): %s", _cbe)

    log.warning("ndvi_real: ⚠️ ninguna fuente devolvió datos — NDVI anterior mantenido")
    return None
