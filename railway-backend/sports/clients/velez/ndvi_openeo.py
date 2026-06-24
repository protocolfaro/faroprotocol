"""
ndvi_openeo.py — NDVI composite via OpenEO · Copernicus Data Space Ecosystem (CDSE).

Fuente 4 en la cascada de ndvi_real.fetch_ndvi().
Se activa cuando Planetary Computer, Element84 y el BAP local no producen
cobertura suficiente (p.ej. persistencia de nubes >30 días en invierno austral).

Ventajas sobre fuentes STAC locales:
  - Cloud masking server-side: nunca descarga píxeles nublados
  - Composite temporal (mediana) en una sola request HTTP
  - Acceso directo al archivo oficial ESA sin intermediarios (latencia 2-3h)
  - SENTINEL2_L2A completo: 10m bandas ópticas + SCL en la misma colección

Autenticación: COPERNICUS_USER + COPERNICUS_PASS (ya en Railway env vars).
Dependencias: openeo>=0.26.0 (ya en requirements.txt), rioxarray (ya instalado).

Física del composite:
  SCL mask elimina clases: 0=no_data, 1=defective, 3=shadow,
                            8=med_cloud, 9=high_cloud, 10=cirrus
  Reducción temporal: mediana por píxel → robusto frente a residuos de nubes.
  Resolución output: 10m (nativa B08/B04), reproyectado a EPSG:4326 para
  indexado geográfico sin dependencia de pyproj adicional.
"""
from __future__ import annotations

import logging
import os
import sys
import tempfile
import time
from datetime import date, timedelta
from typing import Optional

log = logging.getLogger(__name__)

_HERE = os.path.dirname(os.path.abspath(__file__))
_CORE = os.path.abspath(os.path.join(_HERE, "..", "..", "..", "core"))
for _p in (_HERE, _CORE):
    if _p not in sys.path:
        sys.path.insert(0, _p)

_CDSE_URL   = "https://openeo.dataspace.copernicus.eu"
_COLLECTION = "SENTINEL2_L2A"
_CLIENT_ID  = "cdse-public"        # cliente público CDSE (no requiere secret)
_PROVIDER   = "CDSE"
_MAX_WAIT_S = 300                   # timeout batch job: 5 minutos

# Clases SCL a enmascarar antes del composite
_SCL_BAD = [0, 1, 3, 8, 9, 10]


# ── Autenticación ─────────────────────────────────────────────────────────────

def _connect():
    """Conecta y autentica en CDSE OpenEO via resource owner password flow."""
    # Prefer CDSE_USER/PASS (current Railway vars); fall back to COPERNICUS_* for compat
    user = os.environ.get("CDSE_USER") or os.environ.get("COPERNICUS_USER", "")
    pwd  = os.environ.get("CDSE_PASS") or os.environ.get("COPERNICUS_PASS", "")
    if not user or not pwd:
        log.warning("openeo: CDSE_USER/PASS (o COPERNICUS_USER/PASS) no configurados")
        return None
    try:
        import openeo
        conn = openeo.connect(_CDSE_URL)
        conn.authenticate_oidc_resource_owner_password_credentials(
            username=user,
            password=pwd,
            client_id=_CLIENT_ID,
            provider_id=_PROVIDER,
        )
        log.info("openeo: autenticado en CDSE (%s)", user)
        return conn
    except Exception as exc:
        log.warning("openeo: auth CDSE falló: %s", exc)
        return None


# ── Proceso OpenEO ────────────────────────────────────────────────────────────

def _build_process(conn, bbox: tuple, dt_from: str, dt_to: str):
    """
    Construye el proceso OpenEO:
      load_collection → mask SCL → reduce temporal (mediana) → resample 4326
    """
    minx, miny, maxx, maxy = bbox

    cube = conn.load_collection(
        _COLLECTION,
        spatial_extent={"west": minx, "south": miny, "east": maxx, "north": maxy},
        temporal_extent=[dt_from, dt_to],
        bands=["B04", "B08", "B03", "B02", "B11", "B05", "SCL"],
        max_cloud_cover=90,    # pre-filtro: descarta escenas >90% nube antes de cargar
    )

    # ── Máscara SCL (server-side) ──────────────────────────────────────
    scl  = cube.band("SCL")
    mask = scl == _SCL_BAD[0]
    for cls in _SCL_BAD[1:]:
        mask = mask | (scl == cls)
    cube = cube.mask(mask)

    # ── Composite temporal: mediana por píxel ──────────────────────────
    # La mediana es más robusta que la media frente a píxeles residualmente
    # nublados no detectados por SCL (sub-pixel cloud contamination).
    cube = cube.reduce_dimension(dimension="t", reducer="median")

    # ── Reproyección a EPSG:4326 para indexado geográfico directo ─────
    # 0.0001° ≈ 11m en latitud -34°, equivalente a la resolución nativa 10m.
    cube = cube.resample_spatial(resolution=0.0001, projection=4326, method="near")

    return cube


def _execute(cube, tmp_path: str) -> bool:
    """
    Ejecuta el proceso en CDSE. Intenta sincrónicamente primero;
    si no soportado, crea batch job y espera hasta _MAX_WAIT_S segundos.
    Retorna True si el archivo fue descargado con éxito.
    """
    # Intento 1: descarga sincrónica (funciona para bbox pequeñas en CDSE)
    try:
        cube.download(tmp_path, format="GTiff")
        if os.path.exists(tmp_path) and os.path.getsize(tmp_path) > 1000:
            log.info("openeo: descarga sincrónica OK (%d B)", os.path.getsize(tmp_path))
            return True
    except Exception as exc_sync:
        log.info("openeo: sincrónico no soportado, usando batch job: %s", exc_sync)

    # Intento 2: batch job con polling
    try:
        job = cube.execute_batch(
            tmp_path,
            out_format="GTiff",
            title="faro_ndvi_composite",
            max_poll_interval=30,
        )
        # execute_batch ya descarga y bloquea hasta que termina o falla
        if os.path.exists(tmp_path) and os.path.getsize(tmp_path) > 1000:
            log.info("openeo: batch job OK (%d B)", os.path.getsize(tmp_path))
            return True
        log.warning("openeo: batch job completó pero archivo vacío/ausente")
    except Exception as exc_batch:
        log.warning("openeo: batch job falló: %s", exc_batch)

    return False


# ── Lectura de resultado y cómputo de índices ─────────────────────────────────

def _parse_geotiff(tmp_path: str) -> Optional[dict]:
    """
    Lee el GeoTIFF resultante y retorna {cid: entry} con NDVI, ndvi_2d, etc.
    por cancha, en el mismo formato que _read_canchas() de ndvi_real.py.
    """
    try:
        import numpy as np
        import rioxarray as rxr
        from ndvi_real import CANCHA_BBOXES, _compute_indices
    except ImportError as exc:
        log.warning("openeo parse: dependencia faltante: %s", exc)
        return None

    try:
        # rioxarray lee el GeoTIFF multi-banda y lo reproyecta si es necesario
        ds = rxr.open_rasterio(tmp_path, masked=True)  # (band, y, x)
        # Bandas en el orden declarado: B04, B08, B03, B02, B11, B05, SCL (índices 0-6)
        # masked=True → nodata como np.nan automáticamente

        bands = {
            "B04": 0,   # red
            "B08": 1,   # nir
            "B03": 2,   # green
            "B02": 3,   # blue
            "B11": 4,   # swir1
            "B05": 5,   # red_edge
            "SCL": 6,
        }

        def _b(name: str) -> "np.ndarray | None":
            idx = bands.get(name)
            if idx is None:
                return None
            try:
                arr = ds.values[idx].astype(np.float32)
                if name == "SCL":
                    return arr
                return np.clip(arr / 10_000.0, 0.0, 1.5)   # reflectancia S2
            except IndexError:
                return None

        nir      = _b("B08")
        red      = _b("B04")
        green    = _b("B03")
        blue     = _b("B02")
        swir1    = _b("B11")
        red_edge = _b("B05")
        scl_arr  = _b("SCL")

        if nir is None or red is None:
            log.warning("openeo parse: bandas NIR/RED ausentes")
            return None

        # Coordenadas geográficas del raster (EPSG:4326 por construcción)
        lons = ds.x.values   # (W)
        lats = ds.y.values   # (H), norte→sur

        ds.close()

        results: dict[str, dict] = {}

        for cid, (cminx, cminy, cmaxx, cmaxy) in CANCHA_BBOXES.items():
            try:
                # Encontrar índices de píxeles dentro de la bbox de la cancha
                col_idx = np.where((lons >= cminx) & (lons <= cmaxx))[0]
                row_idx = np.where((lats >= cminy) & (lats <= cmaxy))[0]

                if len(col_idx) == 0 or len(row_idx) == 0:
                    continue

                ci0, ci1 = int(col_idx[0]),  int(col_idx[-1]) + 1
                ri0, ri1 = int(row_idx[0]),  int(row_idx[-1]) + 1

                def _crop(a: "np.ndarray | None") -> "np.ndarray | None":
                    if a is None or a.ndim < 2:
                        return None
                    return a[ri0:ri1, ci0:ci1]

                # SCL mask: NaN + clases malas residuales
                scl_c   = _crop(scl_arr)
                nan_msk = np.isnan(_crop(nir)) if _crop(nir) is not None else None
                if scl_c is not None and nan_msk is not None:
                    scl_mask = np.isin(scl_c.astype(int),
                                       _SCL_BAD) | nan_msk
                elif nan_msk is not None:
                    scl_mask = nan_msk
                else:
                    scl_mask = None

                entry = _compute_indices(
                    _crop(nir), _crop(red), _crop(green),
                    _crop(blue), _crop(swir1), scl_mask, "s2",
                    red_edge_c=_crop(red_edge),
                )
                if entry is not None:
                    results[cid] = entry

            except Exception as exc_c:
                log.warning("openeo parse: %s: %s", cid, exc_c)

        return results if results else None

    except Exception as exc:
        log.warning("openeo parse: error leyendo GeoTIFF: %s", exc)
        return None


# ── API pública ───────────────────────────────────────────────────────────────

def fetch_ndvi_openeo(
    dt_from: str | None = None,
    dt_to:   str | None = None,
    window_days: int    = 30,
) -> Optional[dict]:
    """
    Obtiene composite NDVI via OpenEO CDSE.

    Proceso server-side: S2 L2A → mask SCL → mediana temporal → GeoTIFF 4326.
    Retorna dict compatible con ndvi_real.fetch_ndvi() o None si falla.

    Args:
        dt_from, dt_to: rango temporal ISO (default: últimos window_days días)
        window_days:    ventana si no se especifican fechas (default 30)
    """
    try:
        import openeo   # noqa — probe import
    except ImportError:
        log.warning("openeo: paquete no instalado")
        return None

    today  = date.today()
    dt_to  = dt_to   or today.isoformat()
    dt_from = dt_from or (today - timedelta(days=window_days)).isoformat()

    conn = _connect()
    if conn is None:
        return None

    # Importamos _CLUSTER_BBOX desde ndvi_real (cubre todas las canchas)
    try:
        from ndvi_real import _CLUSTER_BBOX
    except ImportError:
        log.warning("openeo: no se puede importar _CLUSTER_BBOX")
        return None

    cube = _build_process(conn, _CLUSTER_BBOX, dt_from, dt_to)

    tmp_path = os.path.join(tempfile.gettempdir(), f"faro_openeo_{int(time.time())}.tif")

    try:
        ok = _execute(cube, tmp_path)
        if not ok:
            return None

        canchas = _parse_geotiff(tmp_path)
        if not canchas:
            return None

        log.info("openeo CDSE OK — %d canchas · %s→%s · mediana %dd",
                 len(canchas), dt_from, dt_to, window_days)

        return {
            "fuente":              f"CDSE_OpenEO · SENTINEL2_L2A · mediana_{window_days}d",
            "fecha_imagen":        dt_to,
            "nubosidad_pct":       0.0,    # composite enmascarado es libre de nubes
            "canchas":             canchas,
            "prithvi_enriquecido": False,
            "metodo":              "OPENEO_CDSE_MEDIAN",
        }

    finally:
        try:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)
        except Exception:
            pass
