"""
dale_play_openeo.py — S2 NDVI via Element84 Earth Search + AWS sentinel-cogs COG.

Alternativa sin credenciales al fallback post-show. Funciona en Railway hoy.

Flujo:
  1. POST Element84 STAC /search — S2 L2A, bbox Amalfitani, sin auth
  2. Obtener href de B04 (red) y B08 (nir08) — COG en s3://sentinel-cogs (público)
  3. rasterio.open(url) — HTTP range request, sin descarga completa
  4. NDVI = (B08 - B04) / (B08 + B04) sobre bbox

Sin credenciales requeridas — datos públicos AWS Open Data S2 L2A.
Misma resolución S2 10m que CDSE openEO, sin OIDC/device-code.
"""
from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Optional

log = logging.getLogger(__name__)

_E84_STAC  = "https://earth-search.aws.element84.com/v1/search"
# Bbox 1km×1km centrado en Amalfitani — ≥100 px a 10m S2
_BBOX_LIST = [-58.456, -34.640, -58.444, -34.628]   # [W, S, E, N]


def _s3_to_https(url: str) -> str:
    """Convierte s3://sentinel-cogs/... a URL HTTPS pública."""
    if url.startswith("s3://sentinel-cogs/"):
        return url.replace(
            "s3://sentinel-cogs/",
            "https://sentinel-cogs.s3.us-west-2.amazonaws.com/",
        )
    return url


def _search_s2(start_date: str, end_date: str, max_cloud: float = 80.0) -> Optional[dict]:
    """Busca el item S2 L2A más reciente sobre Amalfitani en Element84."""
    import requests

    try:
        r = requests.post(
            _E84_STAC,
            json={
                "collections": ["sentinel-2-l2a"],
                "bbox":        _BBOX_LIST,
                "datetime":    f"{start_date}T00:00:00Z/{end_date}T23:59:59Z",
                "limit":       10,
                "sortby":      [{"field": "properties.datetime", "direction": "desc"}],
            },
            timeout=20,
        )
        r.raise_for_status()
        features = r.json().get("features", [])
        # Filtrar por nubosidad en Python (E84 v1 no soporta query filter sin extensión)
        features = [f for f in features
                    if (f.get("properties", {}).get("eo:cloud_cover") or 100) < max_cloud]
        return features[0] if features else None
    except Exception as exc:
        log.warning("openeo: E84 search: %s", exc)
        return None


def _read_band_cog(url: str, bbox_wsen: list) -> Optional["numpy.ndarray"]:
    """
    Lee una banda S2 desde COG público AWS via HTTP range request.
    Extrae solo el subset sobre bbox — sin descarga completa (~10KB vs ~100MB).
    """
    try:
        import numpy as np
        import rasterio
        from rasterio.warp import transform_bounds
        from rasterio.windows import from_bounds

        w, s, e, n = bbox_wsen
        with rasterio.open(url) as src:
            native = transform_bounds("EPSG:4326", src.crs, w, s, e, n)
            win    = from_bounds(*native, transform=src.transform)
            # out_shape fijo para evitar mismatch entre bandas de distintos COGs
            data   = src.read(1, window=win, out_shape=(64, 64),
                               resampling=rasterio.enums.Resampling.bilinear).astype("float32")
        return data
    except Exception as exc:
        log.warning("openeo: COG read %s: %s", url[:60], exc)
        return None


def fetch_openeo_ndvi(show_date: str = "", days_back: int = 14) -> dict:
    """
    NDVI Sentinel-2 L2A via Element84 Earth Search + AWS sentinel-cogs COG.

    Sin credenciales — datos públicos AWS Open Data.
    Usa rasterio HTTP range request (no descarga completa).

    Args:
        show_date:  YYYY-MM-DD del show. Busca desde show_date-days_back hasta hoy.
        days_back:  ventana de búsqueda hacia atrás desde show_date.

    Returns:
        {"ndvi": float, "fecha": str, "fuente": "S2_AWS_E84", ...}
    """
    import numpy as np

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

    # Primer intento: solo post-show (cloud < 80%)
    item = _search_s2(show_date or start_dt.isoformat(), end_dt.isoformat())
    # Segundo intento: ampliar ventana completa si no hay post-show
    if item is None:
        item = _search_s2(start_dt.isoformat(), end_dt.isoformat(), max_cloud=90.0)

    if item is None:
        return {
            "ndvi":    None,
            "fuente":  "S2_AWS_E84",
            "error":   (
                f"Sin escenas S2 en Element84 para {start_dt} → {end_dt} "
                f"sobre bbox Amalfitani (cloud < 90%)"
            ),
        }

    props  = item.get("properties", {})
    assets = item.get("assets", {})
    fecha  = (props.get("datetime") or "")[:10]
    cloud  = props.get("eo:cloud_cover")

    # Asset name mapping: Element84 usa "red"/"nir08", otros usan "B04"/"B08"
    b04_asset = (assets.get("red") or assets.get("B04") or assets.get("b04") or {})
    b08_asset = (assets.get("nir08") or assets.get("nir") or assets.get("B08") or assets.get("b08") or {})
    b04_url   = _s3_to_https(b04_asset.get("href", ""))
    b08_url   = _s3_to_https(b08_asset.get("href", ""))

    if not b04_url or not b08_url:
        return {
            "ndvi":   None,
            "fuente": "S2_AWS_E84",
            "fecha":  fecha,
            "error":  f"Assets B04/B08 no encontrados. Keys: {list(assets.keys())}",
        }

    log.info("openeo: S2 item %s fecha=%s cloud=%.0f%%", item.get("id", "")[:30], fecha, cloud or 0)
    log.info("openeo: B04=%s…", b04_url[:60])

    # Leer bandas via COG HTTP range request
    b04 = _read_band_cog(b04_url, _BBOX_LIST)
    b08 = _read_band_cog(b08_url, _BBOX_LIST)

    if b04 is None or b08 is None:
        return {
            "ndvi":   None,
            "fuente": "S2_AWS_E84",
            "fecha":  fecha,
            "error":  "Error leyendo COG S2 via HTTP",
        }

    # NDVI — DN o reflectancia escalan igual (ratio, el factor cancela)
    # Filtramos fill-value (0) y saturación (>15000 DN)
    valid = (b04 > 0) & (b08 > 0) & (b04 < 15000) & (b08 < 15000)
    if not valid.any():
        return {
            "ndvi":   None,
            "fuente": "S2_AWS_E84",
            "fecha":  fecha,
            "error":  "Sin píxeles válidos (posible nubosidad total o máscara NoData)",
        }

    r_v = b04[valid].astype("float64")
    n_v = b08[valid].astype("float64")
    ndvi = float(np.mean((n_v - r_v) / (n_v + r_v + 1e-9)))
    ndvi = round(ndvi, 3)

    log.info("openeo: NDVI=%.3f px_válidos=%d cloud=%.0f%%", ndvi, int(valid.sum()), cloud or 0)

    es_post_show = (show_dt is not None and fecha >= show_date) if show_date and fecha else None

    return {
        "ndvi":        ndvi,
        "fecha":       fecha,
        "fuente":      "S2_AWS_E84",
        "post_show":   es_post_show,
        "cloud_pct":   cloud,
        "px_validos":  int(valid.sum()),
        "resolucion":  "10m — Sentinel-2 L2A",
        "coleccion":   "sentinel-2-l2a",
        "fuente_stac": "Element84 Earth Search / AWS Open Data",
        "nota":        (
            "S2 10m público — sin credenciales. "
            "HTTP range request COG (~10KB subset vs ~100MB descarga completa)."
            + ("" if es_post_show else
               " [imagen PRE-show — datos post-show aún no disponibles en E84]")
        ),
    }
