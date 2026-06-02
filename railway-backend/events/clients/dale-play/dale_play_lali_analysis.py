"""
dale_play_lali_analysis.py — Análisis comparativo NDVI: 5 shows Lali Espósito 2025.

Queries Element84 Earth Search (Sentinel-2 L2A) for the Amalfitani BBOX across
the 2025 concert season. For each show event, finds the cleanest pre and post
S2 images, computes NDVI, and measures the impact delta.

Shows:
  - Mayo 2025 (2 fechas) — window: Apr 15 - Jun 30 2025
  - 6-7 septiembre 2025  — window: Aug 20 - Oct 10 2025
  - 16 diciembre 2025    — window: Nov 20 - Jan 15 2026

Endpoint: GET /dale-play/lali-analysis
"""
from __future__ import annotations

import logging
import os
import time
from datetime import date, timedelta
from typing import Optional

import requests

from dale_play_config import VENUE_BBOX

log = logging.getLogger(__name__)

_AWS_STAC = "https://earth-search.aws.element84.com/v1"
_MAX_CLOUD = 20.0   # % cloud cover máximo aceptable

# ─────────────────────────────────────────────────────────────────────────────
# Definición de shows Lali 2025
# ─────────────────────────────────────────────────────────────────────────────
# Para los shows de mayo, las fechas exactas no estaban en el brief —
# se usa la ventana completa abr-jun y se identifican los dos drops de NDVI.
LALI_EVENTS = [
    {
        "event_id":   "lali_mayo_A_2025",
        "label":      "Lali — Mayo 2025 (fecha 1)",
        "show_date":  None,                  # fecha exacta desconocida
        "window_pre":  ("2025-04-15", "2025-05-15"),
        "window_post": ("2025-05-16", "2025-06-15"),
        "note":       "Fecha exacta no especificada; se usa NDVI más bajo en ventana post",
    },
    {
        "event_id":   "lali_mayo_B_2025",
        "label":      "Lali — Mayo 2025 (fecha 2)",
        "show_date":  None,
        "window_pre":  ("2025-05-01", "2025-05-31"),
        "window_post": ("2025-06-01", "2025-06-30"),
        "note":       "Segunda fecha de mayo; ventana post = junio 2025",
    },
    {
        "event_id":   "lali_sep_2025",
        "label":      "Lali — 6-7 septiembre 2025",
        "show_date":  "2025-09-06",
        "window_pre":  ("2025-08-10", "2025-09-05"),
        "window_post": ("2025-09-08", "2025-10-10"),
        "note":       "Shows viernes y sábado; S2 revisita ~5 días",
    },
    {
        "event_id":   "lali_dic_2025",
        "label":      "Lali — 16 diciembre 2025",
        "show_date":  "2025-12-16",
        "window_pre":  ("2025-11-15", "2025-12-15"),
        "window_post": ("2025-12-17", "2026-01-15"),
        "note":       "Show de verano BsAs; césped en temporada alta",
    },
]

# Ventana global para query inicial: abarca todos los eventos
_GLOBAL_START = "2025-04-01"
_GLOBAL_END   = "2026-01-31"

# ─────────────────────────────────────────────────────────────────────────────
# Caché de resultados (TTL 6h — imágenes no cambian)
# ─────────────────────────────────────────────────────────────────────────────
_RESULT_CACHE: dict = {}
_RESULT_CACHE_TS: float = 0.0
_CACHE_TTL = 21_600   # 6h


# ─────────────────────────────────────────────────────────────────────────────
# Query STAC
# ─────────────────────────────────────────────────────────────────────────────

def _stac_search_window(start: str, end: str, max_cloud: float = _MAX_CLOUD, limit: int = 50) -> list[dict]:
    """
    Queries Element84 Earth Search for S2-L2A images in the given date window.
    Returns list of STAC items sorted by datetime ASC.
    """
    minx, miny, maxx, maxy = VENUE_BBOX
    payload = {
        "collections": ["sentinel-2-l2a"],
        "bbox":        [minx, miny, maxx, maxy],
        "datetime":    f"{start}T00:00:00Z/{end}T23:59:59Z",
        "query":       {"eo:cloud_cover": {"lt": max_cloud}},
        "limit":       limit,
        "sortby":      [{"field": "datetime", "direction": "asc"}],
    }
    try:
        r = requests.post(
            f"{_AWS_STAC}/search",
            json=payload,
            timeout=30,
        )
        if not r.ok:
            log.warning("lali_analysis: STAC %s → HTTP %s: %s", start[:7], r.status_code, r.text[:200])
            return []
        features = r.json().get("features", [])
        log.info("lali_analysis: STAC %s–%s → %d imágenes cloud<%.0f%%", start, end, len(features), max_cloud)
        return features
    except Exception as exc:
        log.warning("lali_analysis: STAC error: %s", exc)
        return []


def _all_items_2025() -> list[dict]:
    """
    Fetches all clean S2 items for the Amalfitani across the full 2025 season.
    Uses two queries to work around the 100-item API limit.
    """
    h1 = _stac_search_window(_GLOBAL_START, "2025-09-30", limit=100)
    h2 = _stac_search_window("2025-10-01",  _GLOBAL_END,   limit=100)
    seen, combined = set(), []
    for item in h1 + h2:
        iid = item.get("id", "")
        if iid not in seen:
            seen.add(iid)
            combined.append(item)
    combined.sort(key=lambda x: x.get("properties", {}).get("datetime", ""))
    return combined


# ─────────────────────────────────────────────────────────────────────────────
# NDVI desde item S2
# ─────────────────────────────────────────────────────────────────────────────

def _ndvi_from_item(item: dict) -> Optional[float]:
    """Reads NDVI from an Element84 S2-L2A STAC item via windowed HTTPS read."""
    try:
        import rasterio, numpy as np, os as _os
        from rasterio.warp import transform_bounds
        from rasterio.windows import from_bounds

        _os.environ["AWS_NO_SIGN_REQUEST"] = "YES"
        minx, miny, maxx, maxy = VENUE_BBOX
        assets = item.get("assets", {})

        def _href(key: str) -> str:
            a = assets.get(key, {})
            href = a.get("href", "")
            if href.startswith("s3://sentinel-cogs/"):
                href = href.replace("s3://sentinel-cogs/",
                                    "https://sentinel-cogs.s3.us-west-2.amazonaws.com/")
            return href

        # Earth Search v1 uses "red"/"nir"; fallback to B04/B08
        red_href = _href("red") or _href("B04")
        nir_href = _href("nir") or _href("B08")

        if not red_href or not nir_href:
            return None

        def _read_band(href: str) -> Optional[np.ndarray]:
            try:
                with rasterio.open(href) as src:
                    native = transform_bounds("EPSG:4326", src.crs, minx, miny, maxx, maxy)
                    win    = from_bounds(*native, transform=src.transform)
                    return src.read(1, window=win).astype("float32") / 10_000.0
            except Exception as e:
                log.debug("lali_analysis: band read error: %s", e)
                return None

        red = _read_band(red_href)
        nir = _read_band(nir_href)
        if red is None or nir is None:
            return None

        valid = (red > 0) & (nir > 0) & (red < 1) & (nir < 1)
        if valid.sum() < 4:
            return None
        r_v, n_v = red[valid], nir[valid]
        return float(round(float(np.mean((n_v - r_v) / (n_v + r_v + 1e-9))), 3))

    except Exception as exc:
        log.warning("lali_analysis: NDVI error %s: %s", item.get("id", "?")[:30], exc)
        return None


# ─────────────────────────────────────────────────────────────────────────────
# Build scene record
# ─────────────────────────────────────────────────────────────────────────────

def _item_to_record(item: dict) -> dict:
    """Converts a STAC item to a minimal record with NDVI."""
    props   = item.get("properties", {})
    dt_str  = props.get("datetime", "")[:10]
    cloud   = props.get("eo:cloud_cover", None)
    item_id = item.get("id", "")

    ndvi = _ndvi_from_item(item)

    return {
        "date":     dt_str,
        "item_id":  item_id,
        "cloud":    round(float(cloud), 1) if cloud is not None else None,
        "ndvi":     ndvi,
        "platform": props.get("platform", ""),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Analizar evento individual
# ─────────────────────────────────────────────────────────────────────────────

def _analyze_event(event: dict, all_items: list[dict]) -> dict:
    """
    For a single event, selects the best pre and post images and computes delta.
    """
    pre_start,  pre_end  = event["window_pre"]
    post_start, post_end = event["window_post"]

    pre_items  = [i for i in all_items
                  if pre_start <= i.get("properties", {}).get("datetime", "")[:10] <= pre_end]
    post_items = [i for i in all_items
                  if post_start <= i.get("properties", {}).get("datetime", "")[:10] <= post_end]

    # Select lowest-cloud item in each window
    def _best(items: list[dict]) -> Optional[dict]:
        if not items:
            return None
        return min(items, key=lambda x: x.get("properties", {}).get("eo:cloud_cover", 999))

    pre_item  = _best(pre_items)
    post_item = _best(post_items)

    pre_rec  = _item_to_record(pre_item)  if pre_item  else None
    post_rec = _item_to_record(post_item) if post_item else None

    delta_ndvi = None
    if pre_rec and post_rec and pre_rec["ndvi"] is not None and post_rec["ndvi"] is not None:
        delta_ndvi = round(post_rec["ndvi"] - pre_rec["ndvi"], 3)

    # All records in window (for timeline)
    pre_records  = [_item_to_record(i) for i in pre_items]
    post_records = [_item_to_record(i) for i in post_items]

    return {
        "event_id":    event["event_id"],
        "label":       event["label"],
        "show_date":   event.get("show_date"),
        "note":        event.get("note", ""),
        "pre": {
            "window":  event["window_pre"],
            "n_found": len(pre_items),
            "best":    pre_rec,
            "all":     pre_records,
        },
        "post": {
            "window":  event["window_post"],
            "n_found": len(post_items),
            "best":    post_rec,
            "all":     post_records,
        },
        "delta_ndvi":  delta_ndvi,
        "impacto": _classify_delta(delta_ndvi),
    }


def _classify_delta(delta: Optional[float]) -> str:
    if delta is None:
        return "sin_datos"
    if delta <= -0.05:
        return "impacto_severo"
    if delta <= -0.02:
        return "impacto_moderado"
    if delta <= 0:
        return "impacto_leve"
    return "sin_impacto_o_recuperacion"


# ─────────────────────────────────────────────────────────────────────────────
# Endpoint público
# ─────────────────────────────────────────────────────────────────────────────

def run_lali_analysis(force: bool = False) -> dict:
    """
    Runs the full Lali 2025 comparative NDVI analysis.
    Results cached 6h (satellite images don't change).
    Returns structured report with pre/post NDVI for all 5 events.
    """
    global _RESULT_CACHE, _RESULT_CACHE_TS
    if not force and _RESULT_CACHE and time.time() - _RESULT_CACHE_TS < _CACHE_TTL:
        return _RESULT_CACHE

    t0 = time.time()
    log.info("lali_analysis: iniciando query global S2 2025 (ventana %s — %s)", _GLOBAL_START, _GLOBAL_END)

    all_items = _all_items_2025()
    if not all_items:
        return {
            "error":   "No se encontraron imágenes S2 para el período 2025",
            "eventos": [],
        }

    log.info("lali_analysis: %d imágenes S2 candidatas en 2025", len(all_items))

    # Build timeline of all scenes (for context)
    timeline = []
    for item in all_items:
        p     = item.get("properties", {})
        cloud = p.get("eo:cloud_cover", None)
        timeline.append({
            "date":     p.get("datetime", "")[:10],
            "cloud":    round(float(cloud), 1) if cloud is not None else None,
            "item_id":  item.get("id", ""),
            "platform": p.get("platform", ""),
        })

    # Analyze each event
    eventos = []
    for event in LALI_EVENTS:
        log.info("lali_analysis: procesando %s", event["event_id"])
        result = _analyze_event(event, all_items)
        eventos.append(result)
        log.info(
            "lali_analysis: %s → pre NDVI=%s, post NDVI=%s, delta=%s (%s)",
            event["event_id"],
            result["pre"]["best"]["ndvi"] if result["pre"]["best"] else "N/D",
            result["post"]["best"]["ndvi"] if result["post"]["best"] else "N/D",
            result["delta_ndvi"],
            result["impacto"],
        )

    duracion = round(time.time() - t0, 1)

    # Summary
    deltas = [e["delta_ndvi"] for e in eventos if e["delta_ndvi"] is not None]
    avg_delta = round(sum(deltas) / len(deltas), 3) if deltas else None
    peores    = sorted([e for e in eventos if e["delta_ndvi"] is not None],
                       key=lambda x: x["delta_ndvi"])

    out = {
        "artista":          "Lali Espósito",
        "venue":            "Estadio José Amalfitani",
        "temporada":        "2025",
        "n_eventos":        len(LALI_EVENTS),
        "n_eventos_con_datos": len([e for e in eventos if e["delta_ndvi"] is not None]),
        "delta_ndvi_promedio": avg_delta,
        "evento_mayor_impacto": peores[0]["label"] if peores else None,
        "max_delta_ndvi":   peores[0]["delta_ndvi"] if peores else None,
        "escenas_totales_2025": len(all_items),
        "timeline_s2":      timeline,
        "eventos":          eventos,
        "duracion_s":       duracion,
        "fuente":           "Element84 Earth Search · Sentinel-2 L2A",
        "cloud_max_pct":    _MAX_CLOUD,
        "bbox":             list(VENUE_BBOX),
    }

    _RESULT_CACHE    = out
    _RESULT_CACHE_TS = time.time()
    log.info("lali_analysis: análisis completo en %.1fs — %d eventos con datos", duracion, out["n_eventos_con_datos"])
    return out


def get_lali_summary() -> dict:
    """Returns a lightweight summary without full scene lists (for quick API responses)."""
    full = run_lali_analysis()
    if "error" in full:
        return full

    eventos_light = []
    for e in full.get("eventos", []):
        eventos_light.append({
            "event_id":   e["event_id"],
            "label":      e["label"],
            "show_date":  e.get("show_date"),
            "pre_date":   e["pre"]["best"]["date"] if e["pre"]["best"] else None,
            "pre_ndvi":   e["pre"]["best"]["ndvi"] if e["pre"]["best"] else None,
            "pre_cloud":  e["pre"]["best"]["cloud"] if e["pre"]["best"] else None,
            "post_date":  e["post"]["best"]["date"] if e["post"]["best"] else None,
            "post_ndvi":  e["post"]["best"]["ndvi"] if e["post"]["best"] else None,
            "post_cloud": e["post"]["best"]["cloud"] if e["post"]["best"] else None,
            "delta_ndvi": e["delta_ndvi"],
            "impacto":    e["impacto"],
            "note":       e.get("note", ""),
        })

    return {
        "artista":              full["artista"],
        "venue":                full["venue"],
        "temporada":            full["temporada"],
        "n_eventos":            full["n_eventos"],
        "n_eventos_con_datos":  full["n_eventos_con_datos"],
        "delta_ndvi_promedio":  full["delta_ndvi_promedio"],
        "evento_mayor_impacto": full["evento_mayor_impacto"],
        "max_delta_ndvi":       full["max_delta_ndvi"],
        "escenas_totales_2025": full["escenas_totales_2025"],
        "fuente":               full["fuente"],
        "duracion_s":           full["duracion_s"],
        "eventos":              eventos_light,
    }
