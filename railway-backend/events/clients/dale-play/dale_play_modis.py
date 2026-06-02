"""
dale_play_modis.py — MODIS MOD09GQ v6.1 NDVI diario (250m) · alerta temprana post-show.

Flujo:
  1. earthaccess auth  (lee NASA_EARTHDATA_USER / NASA_EARTHDATA_PASS)
  2. CMR search        → granule h13v12 más reciente en ventana post-show
  3. earthaccess.download() → HDF4 a /tmp/modis_cache/ (caché por nombre de archivo)
  4. rasterio HDF4 subdatasets → B01 Red + B02 NIR → NDVI

Por qué se descarga en lugar de stream:
  MOD09GQ es HDF4-EOS (no Cloud-Optimized GeoTIFF). earthaccess.open() devuelve
  objetos S3 que xarray no puede leer como HDF4. El download es ~50MB pero solo
  se hace una vez por día gracias al caché en /tmp.

Variables de entorno requeridas (Railway → Settings → Variables):
  NASA_EARTHDATA_USER   →  tu usuario en https://urs.earthdata.nasa.gov
  NASA_EARTHDATA_PASS   →  tu password NASA Earthdata Login
  (registro gratuito en https://urs.earthdata.nasa.gov/users/new)

Tile Buenos Aires: h13v12 | Revisita: ~diaria (Terra, ~10:30am hora local)
Resolución: 250m → integra campo + estructura stadium → alerta temprana de área,
no daño específico del campo. Usar S2/HLS cuando disponibles para diagnóstico preciso.
"""
from __future__ import annotations

import logging
import os
from datetime import date, timedelta
from typing import Optional

log = logging.getLogger(__name__)

_CMR_API    = "https://cmr.earthdata.nasa.gov/search/granules.json"
_MODIS_TILE = "h13v12"

# 1km × 1km centrado en Amalfitani — mínimo para obtener ≥4 píxeles a 250m.
# El bbox de campo (360m×440m) da ≤2 píxeles: insuficiente para media robusta.
_MODIS_BBOX = (-58.456, -34.640, -58.444, -34.628)   # (W, S, E, N)


def _is_configured() -> bool:
    return bool(
        os.environ.get("NASA_EARTHDATA_USER") and os.environ.get("NASA_EARTHDATA_PASS")
    )


def _earthaccess_login():
    """
    Login con earthaccess mapeando nuestras env vars a las que espera la librería.
    earthaccess lee EARTHDATA_USERNAME / EARTHDATA_PASSWORD.
    """
    import earthaccess

    os.environ.setdefault("EARTHDATA_USERNAME", os.environ.get("NASA_EARTHDATA_USER", ""))
    os.environ.setdefault("EARTHDATA_PASSWORD", os.environ.get("NASA_EARTHDATA_PASS", ""))

    auth = earthaccess.login(strategy="environment")
    return auth


def _search_granule(start_dt: date, end_dt: date) -> Optional[list]:
    """
    CMR search → lista de granules MOD09GQ v061 sobre tile h13v12 en el rango dado.
    Retorna lista (puede estar vacía) o None si falla el request.
    """
    import requests

    w, s, e, n = _MODIS_BBOX
    params = {
        "short_name":   "MOD09GQ",
        "version":      "061",
        "temporal":     f"{start_dt.isoformat()}T00:00:00Z,{end_dt.isoformat()}T23:59:59Z",
        "bounding_box": f"{w},{s},{e},{n}",
        "page_size":    7,
        "sort_key":     "-start_date",
    }
    try:
        r = requests.get(_CMR_API, params=params, timeout=20)
        r.raise_for_status()
        return r.json().get("feed", {}).get("entry", [])
    except Exception as exc:
        log.warning("modis: CMR search error: %s", exc)
        return None


def _download_granule(granule: dict) -> Optional[str]:
    """
    Descarga el granule HDF4 a /tmp/modis_cache/ vía earthaccess.
    Usa caché local por nombre de archivo para no re-descargar en el mismo día.
    Retorna path local o None si falla.
    """
    import earthaccess

    # Obtener nombre de archivo desde links CMR para caché
    fname = ""
    for link in granule.get("links", []):
        href = link.get("href", "")
        if href.endswith(".hdf") and "data#" in link.get("rel", ""):
            fname = href.split("/")[-1]
            break

    cache_dir  = "/tmp/modis_cache"
    os.makedirs(cache_dir, exist_ok=True)

    if fname:
        local_path = os.path.join(cache_dir, fname)
        if os.path.exists(local_path) and os.path.getsize(local_path) > 1_000_000:
            log.info("modis: caché local → %s", fname)
            return local_path

    # Buscar el granule en earthaccess para obtener el objeto descargable
    try:
        concept_id = granule.get("id", "")
        time_start = (granule.get("time_start") or "")[:10]

        if concept_id:
            ea_results = earthaccess.search_data(
                short_name="MOD09GQ",
                version="061",
                temporal=(time_start, time_start),
                bounding_box=_MODIS_BBOX,
            )
        else:
            ea_results = []

        if not ea_results:
            log.warning("modis: earthaccess no encontró granule para concept_id=%s", concept_id)
            return None

        log.info("modis: descargando granule (~50MB) …")
        files = earthaccess.download(ea_results[:1], local_path=cache_dir)
        return files[0] if files else None

    except Exception as exc:
        log.warning("modis: download error: %s", exc)
        return None


def _bbox_to_tile_pixels(ul_x: float, ul_y: float, lr_x: float, lr_y: float,
                          n_pixels: int = 2400) -> tuple[int, int, int, int]:
    """
    Convierte _MODIS_BBOX (lon/lat WGS84) a índices de pixel en el tile MODIS sinusoidal.

    MODIS sinusoidal: x = R × lon × cos(lat), y = R × lat  (R = 6371007.181 m)
    ul_x/ul_y, lr_x/lr_y: esquinas del tile en metros sinusoidales (de StructMetadata).
    Retorna (row_start, row_end, col_start, col_end) dentro del tile n_pixels×n_pixels.
    """
    import math
    R = 6371007.181

    w, s, e, n = _MODIS_BBOX  # grados

    def to_sinu(lon_deg: float, lat_deg: float) -> tuple[float, float]:
        lat_r = math.radians(lat_deg)
        lon_r = math.radians(lon_deg)
        return R * lon_r * math.cos(lat_r), R * lat_r

    nw_x, nw_y = to_sinu(w, n)
    se_x, se_y = to_sinu(e, s)

    tile_w = lr_x - ul_x
    tile_h = ul_y - lr_y   # positivo porque ul_y > lr_y

    col0 = max(0, int((nw_x - ul_x) / tile_w * n_pixels) - 2)
    col1 = min(n_pixels - 1, int((se_x - ul_x) / tile_w * n_pixels) + 2)
    row0 = max(0, int((ul_y - nw_y) / tile_h * n_pixels) - 2)
    row1 = min(n_pixels - 1, int((ul_y - se_y) / tile_h * n_pixels) + 2)
    return row0, row1, col0, col1


def _read_ndvi_pyhdf(hdf_path: str) -> Optional[float]:
    """
    Lee NDVI de MOD09GQ con pyhdf + math sinusoidal MODIS.
    No requiere GDAL/rasterio — solo libhdf4 (instalado via nixpacks.toml).
    """
    import re
    import numpy as np
    try:
        from pyhdf.SD import SD, SDC
    except ImportError:
        return None

    try:
        hdf     = SD(hdf_path, SDC.READ)
        attrs   = hdf.attributes()

        # Extraer extent del tile desde StructMetadata.0
        struct  = attrs.get("StructMetadata.0", "")
        ul_m    = re.search(r"UpperLeftPointMtrs=\(([^,]+),([^)]+)\)", struct)
        lr_m    = re.search(r"LowerRightMtrs=\(([^,]+),([^)]+)\)", struct)

        if ul_m and lr_m:
            ul_x = float(ul_m.group(1)); ul_y = float(ul_m.group(2))
            lr_x = float(lr_m.group(1)); lr_y = float(lr_m.group(2))
            r0, r1, c0, c1 = _bbox_to_tile_pixels(ul_x, ul_y, lr_x, lr_y)
        else:
            # Fallback: use approximate center of tile (less accurate)
            r0, r1, c0, c1 = 1100, 1120, 490, 510
            log.warning("modis: StructMetadata no encontrado — usando centro de tile como fallback")

        b01 = hdf.select("sur_refl_b01")[r0:r1+1, c0:c1+1].astype("float32") * 0.0001
        b02 = hdf.select("sur_refl_b02")[r0:r1+1, c0:c1+1].astype("float32") * 0.0001
        hdf.end()

        valid = (b01 > -0.01) & (b02 > -0.01) & (b01 < 1.5) & (b02 < 1.5)
        if valid.sum() < 1:
            log.warning("modis pyhdf: sin píxeles válidos (shape=%s)", b01.shape)
            return None

        r_v, n_v = b01[valid], b02[valid]
        ndvi = float(np.mean((n_v - r_v) / (n_v + r_v + 1e-9)))
        log.info("modis pyhdf: NDVI=%.3f píxeles=%d", ndvi, int(valid.sum()))
        return round(ndvi, 3)

    except Exception as exc:
        log.warning("modis pyhdf: error: %s", exc)
        return None


def _read_ndvi_hdf4(hdf_path: str) -> Optional[float]:
    """
    Lee B01/B02 de MOD09GQ → NDVI.
    Intenta pyhdf primero (no requiere GDAL HDF4 driver).
    Fallback a rasterio HDF4_EOS (requiere GDAL con libhdf4).
    """
    # Intento 1: pyhdf (funciona con nixpacks hdf4, sin GDAL)
    ndvi = _read_ndvi_pyhdf(hdf_path)
    if ndvi is not None:
        return ndvi

    # Intento 2: rasterio HDF4_EOS (GDAL compilado con libhdf4)
    try:
        import rasterio
        import numpy as np
        from rasterio.warp import transform_bounds
        from rasterio.windows import from_bounds

        def _read_band(subdataset: str) -> Optional["np.ndarray"]:
            with rasterio.open(subdataset) as src:
                w, s, e, n = _MODIS_BBOX
                native = transform_bounds("EPSG:4326", src.crs, w, s, e, n)
                win    = from_bounds(*native, transform=src.transform)
                return src.read(1, window=win).astype("float32") * 0.0001

        b01 = _read_band(f"HDF4_EOS:EOS_GRID:{hdf_path}:MOD_Grid_250m_Surface_Refl:sur_refl_b01")
        b02 = _read_band(f"HDF4_EOS:EOS_GRID:{hdf_path}:MOD_Grid_250m_Surface_Refl:sur_refl_b02")

        valid = (b01 > -0.01) & (b02 > -0.01) & (b01 < 1.5) & (b02 < 1.5)
        if valid.sum() < 1:
            return None

        r_v, n_v = b01[valid], b02[valid]
        ndvi = float(np.mean((n_v - r_v) / (n_v + r_v + 1e-9)))
        log.info("modis rasterio: NDVI=%.3f píxeles=%d", ndvi, int(valid.sum()))
        return round(ndvi, 3)

    except Exception as exc:
        hdf4_missing = "HDF4" in str(exc).upper() or "HDF4_EOS" in str(exc)
        if hdf4_missing:
            log.warning("modis: GDAL HDF4 + pyhdf no disponibles en este entorno")
        else:
            log.warning("modis rasterio: error: %s", exc)
        return None


def fetch_modis_ndvi(show_date: str = "", days_back: int = 7) -> dict:
    """
    NDVI diario MODIS MOD09GQ v6.1 (250m) para el entorno del Amalfitani.

    Args:
        show_date:  YYYY-MM-DD del show. Solo acepta imágenes posteriores a esta fecha.
        days_back:  ventana de búsqueda hacia atrás desde hoy si no hay show_date.

    Returns:
        {"ndvi": float, "fecha": str, "fuente": "MODIS_MOD09GQ", ...}  — éxito
        {"ndvi": None,  "error": str, "fuente": "MODIS_MOD09GQ", ...}  — fallo

    Requiere en Railway (Settings → Variables):
        NASA_EARTHDATA_USER = <usuario>   # urs.earthdata.nasa.gov
        NASA_EARTHDATA_PASS = <password>  # mismo sitio — registro gratuito
    """
    if not _is_configured():
        return {
            "ndvi":        None,
            "fuente":      "MODIS_MOD09GQ",
            "error":       "NASA_EARTHDATA_USER/PASS no configurados",
            "instruccion": (
                "Railway → tu servicio → Settings → Variables → Add Variable:\n"
                "  NASA_EARTHDATA_USER = tu_usuario\n"
                "  NASA_EARTHDATA_PASS = tu_password\n"
                "Registro gratuito: https://urs.earthdata.nasa.gov/users/new"
            ),
        }

    # Ventana temporal: show_date - days_back → hoy
    # MODIS (Terra) tiene latencia de 1-3 días en CMR. Buscamos desde days_back
    # antes del show para garantizar un granule disponible aunque el post-show
    # aún no esté ingestado. El granule se etiqueta como pre/post show según fecha.
    end_dt   = date.today()
    show_dt  = None
    if show_date:
        try:
            show_dt  = date.fromisoformat(show_date)
            start_dt = show_dt - timedelta(days=days_back)
        except ValueError:
            start_dt = end_dt - timedelta(days=days_back)
    else:
        start_dt = end_dt - timedelta(days=days_back)

    if start_dt > end_dt:
        return {
            "ndvi":  None,
            "fuente": "MODIS_MOD09GQ",
            "error": f"show_date {show_date} es muy futuro — sin datos MODIS disponibles",
        }

    # 1. Auth
    try:
        _earthaccess_login()
    except Exception as exc:
        return {"ndvi": None, "fuente": "MODIS_MOD09GQ", "error": f"Auth earthaccess: {exc}"}

    # 2. CMR search
    granules = _search_granule(start_dt, end_dt)
    if granules is None:
        return {"ndvi": None, "fuente": "MODIS_MOD09GQ", "error": "CMR search fallido"}
    if not granules:
        return {
            "ndvi":  None,
            "fuente": "MODIS_MOD09GQ",
            "error": f"Sin granules MOD09GQ para {start_dt} → {end_dt} sobre tile {_MODIS_TILE}",
        }

    granule      = granules[0]   # más reciente por sort_key=-start_date
    fecha_granule = (granule.get("time_start") or "")[:10]
    log.info("modis: granule encontrado %s fecha=%s", granule.get("id", "")[:40], fecha_granule)

    # 3. Download
    hdf_path = _download_granule(granule)
    if not hdf_path:
        return {
            "ndvi":   None,
            "fuente": "MODIS_MOD09GQ",
            "fecha":  fecha_granule,
            "error":  "Download del granule HDF4 fallido — verificar credenciales NASA",
        }

    # 4. NDVI
    ndvi = _read_ndvi_hdf4(hdf_path)
    if ndvi is None:
        return {
            "ndvi":   None,
            "fuente": "MODIS_MOD09GQ",
            "fecha":  fecha_granule,
            "error":  (
                "pyhdf y GDAL HDF4 driver no disponibles en este entorno. "
                "Railway: verificar nixpacks.toml con hdf4 + pip install pyhdf."
            ),
        }

    es_post_show = (show_dt is not None) and (
        date.fromisoformat(fecha_granule) >= show_dt
    ) if fecha_granule else None

    return {
        "ndvi":         ndvi,
        "fecha":        fecha_granule,
        "fuente":       "MODIS_MOD09GQ",
        "post_show":    es_post_show,           # True=imagen post-show, False=pre-show (latencia)
        "resolucion":   "250m — 1km² entorno stadium",
        "tile":         _MODIS_TILE,
        "producto":     "MOD09GQ v061 · Terra Surface Reflectance Daily",
        "nota":         (
            "250m integra campo + tribuna + entorno — "
            "alerta temprana de área, no daño específico del campo. "
            "Confirmar con S2/HLS cuando disponibles."
            + ("" if es_post_show else
               " [imagen PRE-show — datos post-show con latencia 1-3 días en CMR]")
        ),
    }
