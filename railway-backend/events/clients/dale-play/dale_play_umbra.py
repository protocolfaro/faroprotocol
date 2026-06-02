"""
dale_play_umbra.py — Umbra Space X-band SAR spotlight (~20cm resolución).

Fuente: Umbra Open Data Catalog (CC-BY-4.0, sin autenticación)
  s3://umbra-open-data-catalog/sar-data/tasks/
  STAC: https://s3.us-west-2.amazonaws.com/umbra-open-data-catalog/stac/catalog.json

Producto: GEC.tif (Geocoded Ellipsoid Corrected)
  - Banda X (9.8 GHz) · Polarización VV · Modo SPOTLIGHT
  - Resolución: ~0.18m EW × 0.20m NS (25cm spec nominal)
  - Formato: COG (Cloud-Optimized GeoTIFF), uint8 amplitud, EPSG:4326
  - Sin descarga del archivo completo: lectura via HTTP range requests (COG)

Comportamiento:
  - Busca en STAC si hay escena Umbra que cubre VENUE_BBOX
  - Si SÍ cubre: extrae backscatter sobre la zona exacta del estadio
  - Si NO cubre: retorna la escena más cercana de Buenos Aires con su bbox y stats
  - Detección de cambio: si hay dos escenas (pre/post), calcula delta amplitud
"""
from __future__ import annotations
import logging
import os
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import quote

import numpy as np

log = logging.getLogger(__name__)

_UMBRA_S3_BASE  = "https://umbra-open-data-catalog.s3.us-west-2.amazonaws.com"
_UMBRA_STAC     = "https://s3.us-west-2.amazonaws.com/umbra-open-data-catalog/stac/catalog.json"

# Buenos Aires conocida (de la sesión de investigación 2026-05-31)
_KNOWN_BA_SCENE = {
    "item_id":    "eadb4e53-c7fc-4f10-8da6-2cdf2995b37d",
    "task_path":  "sar-data/tasks/Buenos Aires, ARG/0ef0eb34-90ac-44f9-8221-26d5ed431de6/2024-07-15-01-38-25_UMBRA-05/2024-07-15-01-38-25_UMBRA-05_GEC.tif",
    "datetime":   "2024-07-15T01:38:26Z",
    "platform":   "UMBRA-05",
    "bbox":       (-58.41833, -34.63345, -58.34628, -34.57387),
    "polarization":  "VV",
    "range_res_m":   0.267,
    "az_res_m":      0.251,
    "incidence_deg": 32.63,
    "band":          "X",
    "license":       "CC-BY-4.0",
}


def _s3_to_https(s3_path: str) -> str:
    """Convierte ruta relativa del catálogo a URL HTTPS pública."""
    return f"{_UMBRA_S3_BASE}/{quote(s3_path)}"


def _venue_in_scene(venue_lon: float, venue_lat: float, bbox: tuple) -> bool:
    west, south, east, north = bbox
    return west <= venue_lon <= east and south <= venue_lat <= north


def _read_gec_window(
    url: str,
    lon_center: float,
    lat_center: float,
    half_deg: float = 0.002,      # ~200m a cada lado
    overview_level: int = 0,      # 0 = resolución completa
) -> Optional[dict]:
    """
    Lee una ventana del GEC.tif centrada en (lon_center, lat_center).
    Usa rasterio COG vía HTTP range request — sin descargar el archivo completo.
    half_deg: mitad del lado de la ventana en grados. 0.002° ≈ 170-220m.
    """
    try:
        import rasterio
        from rasterio.windows import Window

        os.environ["AWS_NO_SIGN_REQUEST"] = "YES"

        open_kwargs: dict = {}
        if overview_level > 0:
            open_kwargs["overview_level"] = overview_level - 1

        with rasterio.open(url, **open_kwargs) as src:
            t = src.transform
            H, W = src.height, src.width

            # Coordenadas → píxeles mediante transform inversa
            # transform: (a, b, c, d, e, f) → lon = c + a*col,  lat = f + e*row
            a, e = t.a, t.e
            c, f = t.c, t.f

            col_center = (lon_center - c) / a
            row_center = (lat_center - f) / e
            half_col   = half_deg / abs(a)
            half_row   = half_deg / abs(e)

            col_off = max(0, int(col_center - half_col))
            row_off = max(0, int(row_center - half_row))
            col_end = min(W, int(col_center + half_col))
            row_end = min(H, int(row_center + half_row))

            if col_end <= col_off or row_end <= row_off:
                return {"error": "ventana fuera del raster", "n_px": 0}

            win  = Window(col_off, row_off, col_end - col_off, row_end - row_off)
            data = src.read(1, window=win)

        valid = data[data > 0]
        n_px  = int(valid.size)

        if n_px < 4:
            return {"error": "sin píxeles válidos en la ventana", "n_px": 0}

        # Amplitud uint8 → dB relativo (referencia: valor máximo 255)
        amp_mean = float(np.mean(valid.astype(np.float32)))
        amp_std  = float(np.std(valid.astype(np.float32)))
        db_vals  = 20.0 * np.log10(valid.astype(np.float64) / 255.0 + 1e-12)
        db_mean  = float(round(np.mean(db_vals), 2))
        db_std   = float(round(np.std(db_vals), 2))
        db_p10   = float(round(np.percentile(db_vals, 10), 2))
        db_p50   = float(round(np.percentile(db_vals, 50), 2))
        db_p90   = float(round(np.percentile(db_vals, 90), 2))

        pixel_size_m_ew = abs(a) * 85_000   # ° → m aprox (BsAs)
        pixel_size_m_ns = abs(e) * 111_000

        return {
            "n_px":           n_px,
            "amp_mean_u8":    round(amp_mean, 2),
            "amp_std_u8":     round(amp_std, 2),
            "db_mean":        db_mean,
            "db_std":         db_std,
            "db_p10":         db_p10,
            "db_p50":         db_p50,
            "db_p90":         db_p90,
            "ventana_deg":    half_deg * 2,
            "ventana_m_aprox": round(half_deg * 2 * 100_000, 0),
            "pixel_size_m":   round((pixel_size_m_ew + pixel_size_m_ns) / 2, 3),
        }

    except Exception as e:
        log.warning("dale_play_umbra: read error: %s", e)
        return {"error": str(e), "n_px": 0}


def _search_umbra_stac_for_venue(venue_lon: float, venue_lat: float) -> list[dict]:
    """
    Consulta el catálogo STAC de Umbra para escenas que cubran (venue_lon, venue_lat).
    Retorna lista de items con bbox, datetime y asset path para GEC.tif.
    """
    try:
        import requests

        # Intentar búsqueda STAC via POST (si hay API de búsqueda)
        stac_search = "https://s3.us-west-2.amazonaws.com/umbra-open-data-catalog/stac/catalog.json"
        r = requests.get(stac_search, timeout=15)
        if not r.ok:
            log.warning("dale_play_umbra: STAC catalog error %s", r.status_code)
            return []

        catalog = r.json()
        items   = []

        # Iterar links del catálogo buscando collections/items
        for link in catalog.get("links", []):
            if link.get("rel") not in ("child", "item"):
                continue
            try:
                child_url = link.get("href", "")
                if not child_url.startswith("http"):
                    child_url = _UMBRA_S3_BASE + "/" + child_url.lstrip("/")
                rc = requests.get(child_url, timeout=10)
                if not rc.ok:
                    continue
                child = rc.json()
                # Es un item STAC?
                if child.get("type") == "Feature":
                    bbox = child.get("bbox", [])
                    if len(bbox) == 4:
                        if _venue_in_scene(venue_lon, venue_lat, tuple(bbox)):
                            items.append(child)
            except Exception:
                continue

        return items
    except Exception as e:
        log.warning("dale_play_umbra: STAC search error: %s", e)
        return []


def fetch_umbra_sar(
    venue_lat: float,
    venue_lon: float,
    show_id:   str = "unknown",
    window_m:  int = 500,          # tamaño del área de extracción en metros
) -> dict:
    """
    Fuente principal del módulo Umbra.

    1. Busca escenas en STAC que cubran el venue.
    2. Si encuentra: lee backscatter via COG HTTP range request.
    3. Si no encuentra: usa la escena BA conocida y declara cobertura parcial.

    Retorna:
        umbra_cobre_venue: bool — si la escena cubre el estadio exacto
        escena_*: metadatos de la escena usada
        backscatter_*: estadísticas de amplitud SAR en el área
        fuente, fallback_usado: campos estándar del pipeline
    """
    from dale_play_config import VENUE_BBOX

    os.environ["AWS_NO_SIGN_REQUEST"] = "YES"
    half_deg = (window_m / 2) / 100_000.0   # m → ° aprox

    result: dict = {}

    # 1. Buscar en STAC si hay escena que cubre el venue
    stac_items = _search_umbra_stac_for_venue(venue_lon, venue_lat)

    if stac_items:
        item = stac_items[0]
        assets = item.get("assets", {})
        gec_href = (
            assets.get("GEC.tif", {}).get("href") or
            assets.get("GEC", {}).get("href") or
            next((a.get("href","") for a in assets.values() if "GEC" in a.get("href","")), "")
        )
        if gec_href and gec_href.startswith("s3://"):
            gec_href = gec_href.replace("s3://umbra-open-data-catalog/", f"{_UMBRA_S3_BASE}/")

        stats = _read_gec_window(gec_href, venue_lon, venue_lat, half_deg) if gec_href else {}
        bbox  = tuple(item.get("bbox", []))
        props = item.get("properties", {})

        result.update({
            "umbra_cobre_venue":  True,
            "escena_id":          item.get("id", ""),
            "escena_datetime":    (props.get("datetime") or "")[:10],
            "escena_platform":    props.get("platform", ""),
            "escena_bbox":        bbox,
            "escena_res_m":       props.get("umbra:range_resolution_m", 0.267),
            "fuente_ndvi":        None,
            "fallback_usado":     False,
        })
        result.update({f"backscatter_{k}": v for k, v in (stats or {}).items()})

    else:
        # 2. Fallback: escena BA conocida — no cubre Amalfitani
        sc        = _KNOWN_BA_SCENE
        url       = _s3_to_https(sc["task_path"])
        bbox      = sc["bbox"]
        covers    = _venue_in_scene(venue_lon, venue_lat, bbox)

        # Centro de la escena disponible
        cx = (bbox[0] + bbox[2]) / 2
        cy = (bbox[1] + bbox[3]) / 2

        # Leer estadísticas del centro de la escena
        log.info("dale_play_umbra: usando escena BA conocida %s", sc["datetime"])
        stats = _read_gec_window(url, cx, cy, half_deg=0.02, overview_level=2) or {}

        dist_km = abs(venue_lon - bbox[0]) * 85.0 if not covers else 0.0

        result.update({
            "umbra_cobre_venue":  False,
            "escena_id":          sc["item_id"],
            "escena_datetime":    sc["datetime"][:10],
            "escena_platform":    sc["platform"],
            "escena_bbox":        bbox,
            "escena_res_m":       sc["range_res_m"],
            "escena_polarization":sc["polarization"],
            "escena_banda":       sc["band"],
            "escena_incidencia":  sc["incidence_deg"],
            "escena_licencia":    sc["license"],
            "cobertura_nota":     (
                f"Escena BA {sc['datetime'][:10]} no cubre Amalfitani "
                f"({dist_km:.1f} km al oeste del bbox). "
                f"Cubre aprox: Flores / Caballito / Palermo / Retiro."
            ),
            "fallback_usado":     True,
        })
        result.update({f"backscatter_{k}": v for k, v in stats.items()})

    # Campos fijos
    result["fuente"] = (
        "Umbra Open Data Catalog CC-BY-4.0 · X-band VV Spotlight · "
        f"{result.get('escena_res_m', 0.267):.3f}m resolución · COG HTTP range"
    )
    result["fuente_tipo"]    = "UMBRA_SAR_X"
    result["pixel_size_m"]   = result.get("backscatter_pixel_size_m", 0.20)
    result["polarizacion"]   = "VV"
    result["banda"]          = "X"

    log.info(
        "dale_play_umbra: escena=%s  cubre_venue=%s  db_mean=%s  n_px=%s",
        result.get("escena_id", "")[:12],
        result.get("umbra_cobre_venue"),
        result.get("backscatter_db_mean"),
        result.get("backscatter_n_px"),
    )
    return result


def compare_umbra_scenes(
    scene_pre:  dict,
    scene_post: dict,
) -> dict:
    """
    Calcula delta de amplitud entre dos escenas Umbra.
    Umbral de cambio estructural: delta > 3 dB (coherent change > alta probabilidad).
    Umbral de compactación suelo: delta > 1.5 dB (consistente con S1 C-band).
    """
    db_pre  = scene_pre.get("backscatter_db_mean")
    db_post = scene_post.get("backscatter_db_mean")

    if db_pre is None or db_post is None:
        return {
            "delta_db":           None,
            "alerta_compactacion": False,
            "alerta_estructural":  False,
            "interpretacion":      "Delta no computable — falta escena pre o post",
        }

    delta = round(db_post - db_pre, 2)
    alerta_comp = abs(delta) > 1.5
    alerta_est  = abs(delta) > 3.0

    if alerta_est:
        interp = (f"CAMBIO ESTRUCTURAL DETECTADO: Δ={delta:+.2f} dB. "
                  "Posible deformación de superficie o alteración de estructuras.")
    elif alerta_comp:
        interp = (f"ALERTA COMPACTACIÓN: Δ={delta:+.2f} dB. "
                  "Cambio consistente con compactación del suelo post-evento.")
    elif abs(delta) > 0.5:
        interp = f"Cambio leve detectado: Δ={delta:+.2f} dB. Monitoreo recomendado."
    else:
        interp = f"Sin cambio significativo: Δ={delta:+.2f} dB. Suelo estable."

    return {
        "delta_db":            delta,
        "db_pre":              db_pre,
        "db_post":             db_post,
        "alerta_compactacion": alerta_comp,
        "alerta_estructural":  alerta_est,
        "umbral_compactacion_db": 1.5,
        "umbral_estructural_db":  3.0,
        "interpretacion":      interp,
    }
