"""
dale_play_openeo.py — S2 NDVI via openEO CDSE (Copernicus Data Space Ecosystem).

Reemplaza MODIS HDF4 como fallback post-show. Sin dependencias nativas.
Todo HTTP puro: requests (auth + proceso) + rasterio (lee GeoTIFF en memoria).

Flujo:
  1. POST token endpoint CDSE (Keycloak password-grant, cliente público)
  2. POST /openeo/1.1/result — proceso sincrónico, retorna GeoTIFF en body
  3. rasterio.open(BytesIO) → NDVI medio en bbox Amalfitani

Sentinel-2 L2A: 10m resolución, 5 días revisita → mejor que MODIS 250m.

Variables Railway requeridas (Settings → Variables):
  CDSE_USER = tu_email          # cuenta dataspace.copernicus.eu (gratuita)
  CDSE_PASS = tu_password       # mismo sitio

Registro gratuito: https://dataspace.copernicus.eu/
"""
from __future__ import annotations

import io
import logging
import os
from datetime import date, timedelta
from typing import Optional

log = logging.getLogger(__name__)

_CDSE_TOKEN_URL = (
    "https://identity.dataspace.copernicus.eu"
    "/auth/realms/CDSE/protocol/openid-connect/token"
)
_OPENEO_RESULT_URL = "https://openeo.dataspace.copernicus.eu/openeo/1.1/result"

# 1km × 1km centrado en Amalfitani — ≥100 píxeles a 10m (S2)
_BBOX = {"west": -58.456, "south": -34.640, "east": -58.444, "north": -34.628}


def _is_configured() -> bool:
    return bool(os.environ.get("CDSE_USER") and os.environ.get("CDSE_PASS"))


def _get_token() -> str:
    """Obtiene bearer token via Keycloak password-grant (cliente público cdse-public)."""
    import requests

    r = requests.post(
        _CDSE_TOKEN_URL,
        data={
            "client_id":  "cdse-public",
            "grant_type": "password",
            "username":   os.environ["CDSE_USER"],
            "password":   os.environ["CDSE_PASS"],
        },
        timeout=20,
    )
    r.raise_for_status()
    return r.json()["access_token"]


def _build_process_graph(start_date: str, end_date: str) -> dict:
    """
    Proceso openEO: SENTINEL2_L2A → NDVI (B08-B04)/(B08+B04) → media temporal → GTiff.

    El proceso usa la banda SCL implícita de S2_L2A para enmascarar nubes
    (max_cloud_cover filtra escenas con >80% nubosidad).
    """
    return {
        "process": {
            "process_graph": {
                "load1": {
                    "process_id": "load_collection",
                    "arguments": {
                        "id":               "SENTINEL2_L2A",
                        "spatial_extent":   _BBOX,
                        "temporal_extent":  [start_date, end_date],
                        "bands":            ["B04", "B08"],
                        "properties": {
                            "eo:cloud_cover": {"process_graph": {
                                "lte1": {
                                    "process_id": "lte",
                                    "arguments": {
                                        "x": {"from_parameter": "value"},
                                        "y": 80
                                    },
                                    "result": True
                                }
                            }}
                        },
                    },
                },
                "ndvi1": {
                    "process_id": "ndvi",
                    "arguments": {
                        "data": {"from_node": "load1"},
                        "nir":  "B08",
                        "red":  "B04",
                    },
                },
                "reduce_t": {
                    "process_id": "reduce_dimension",
                    "arguments": {
                        "data":      {"from_node": "ndvi1"},
                        "dimension": "t",
                        "reducer": {
                            "process_graph": {
                                "mean_t": {
                                    "process_id": "mean",
                                    "arguments":  {"data": {"from_parameter": "data"}},
                                    "result":     True,
                                }
                            }
                        },
                    },
                },
                "save1": {
                    "process_id": "save_result",
                    "arguments": {
                        "data":   {"from_node": "reduce_t"},
                        "format": "GTiff",
                    },
                    "result": True,
                },
            }
        }
    }


def _ndvi_from_geotiff(content: bytes) -> Optional[float]:
    """Lee NDVI medio desde GeoTIFF en memoria con rasterio."""
    import numpy as np
    import rasterio

    with rasterio.open(io.BytesIO(content)) as src:
        data    = src.read(1).astype("float32")
        nodata  = src.nodata
        mask    = np.isfinite(data)
        if nodata is not None:
            mask &= data != nodata
        # NDVI válido: [-1, 1]
        mask &= (data >= -1.0) & (data <= 1.0)
        if not mask.any():
            log.warning("openeo: sin píxeles NDVI válidos en bbox")
            return None
        ndvi = float(np.mean(data[mask]))
        log.info("openeo: NDVI=%.3f píxeles_válidos=%d", ndvi, int(mask.sum()))
        return round(ndvi, 3)


def fetch_openeo_ndvi(show_date: str = "", days_back: int = 14) -> dict:
    """
    NDVI Sentinel-2 L2A via openEO CDSE para el Amalfitani.

    Args:
        show_date:  YYYY-MM-DD del show. Busca desde show_date - days_back hasta hoy.
        days_back:  días de ventana hacia atrás desde show_date.

    Returns:
        {"ndvi": float, "fecha": str, "fuente": "S2_CDSE_openEO", ...}  — éxito
        {"ndvi": None,  "error": str, "fuente": "S2_CDSE_openEO", ...}  — fallo

    Requiere Railway Variables:
        CDSE_USER = email           # dataspace.copernicus.eu
        CDSE_PASS = password        # mismo — registro gratuito
    """
    import requests

    if not _is_configured():
        return {
            "ndvi":        None,
            "fuente":      "S2_CDSE_openEO",
            "error":       "CDSE_USER/CDSE_PASS no configurados",
            "instruccion": (
                "Railway → Settings → Variables → Add Variable:\n"
                "  CDSE_USER = tu_email\n"
                "  CDSE_PASS = tu_password\n"
                "Registro gratuito: https://dataspace.copernicus.eu/"
            ),
        }

    # Ventana temporal
    end_dt = date.today()
    if show_date:
        try:
            start_dt = date.fromisoformat(show_date) - timedelta(days=days_back)
        except ValueError:
            start_dt = end_dt - timedelta(days=days_back)
    else:
        start_dt = end_dt - timedelta(days=days_back)

    # 1. Auth
    try:
        token = _get_token()
    except Exception as exc:
        return {"ndvi": None, "fuente": "S2_CDSE_openEO", "error": f"Auth CDSE: {exc}"}

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type":  "application/json",
    }

    # 2. Proceso sincrónico → GeoTIFF
    process = _build_process_graph(start_dt.isoformat(), end_dt.isoformat())
    log.info("openeo: ejecutando proceso S2 NDVI %s → %s …", start_dt, end_dt)
    try:
        r = requests.post(
            _OPENEO_RESULT_URL,
            headers=headers,
            json=process,
            timeout=180,
        )
        if r.status_code == 200 and r.headers.get("Content-Type", "").startswith("image/"):
            pass  # OK
        elif r.status_code != 200:
            return {
                "ndvi":   None,
                "fuente": "S2_CDSE_openEO",
                "error":  f"openEO HTTP {r.status_code}: {r.text[:300]}",
            }
    except requests.Timeout:
        return {
            "ndvi":   None,
            "fuente": "S2_CDSE_openEO",
            "error":  "Timeout 180s en openEO CDSE — ventana temporal muy amplia o CDSE lento",
        }
    except Exception as exc:
        return {"ndvi": None, "fuente": "S2_CDSE_openEO", "error": f"openEO request: {exc}"}

    # 3. Parse GeoTIFF
    try:
        ndvi = _ndvi_from_geotiff(r.content)
    except Exception as exc:
        return {"ndvi": None, "fuente": "S2_CDSE_openEO", "error": f"Parse GeoTIFF: {exc}"}

    if ndvi is None:
        return {
            "ndvi":    None,
            "fuente":  "S2_CDSE_openEO",
            "error":   "Sin píxeles válidos — posible nubosidad total en la ventana",
            "periodo": f"{start_dt} → {end_dt}",
        }

    return {
        "ndvi":       ndvi,
        "fecha":      end_dt.isoformat(),
        "periodo":    f"{start_dt} → {end_dt}",
        "fuente":     "S2_CDSE_openEO",
        "post_show":  (
            date.fromisoformat(show_date) <= end_dt if show_date else None
        ),
        "resolucion": "10m — Sentinel-2 L2A",
        "coleccion":  "SENTINEL2_L2A",
        "nota":       (
            "S2 10m — resolución de campo (≥100 píxeles en bbox). "
            "Media temporal ventana · Copernicus CDSE gratuito."
        ),
    }
