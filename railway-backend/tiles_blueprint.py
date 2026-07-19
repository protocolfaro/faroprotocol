"""
tiles_blueprint.py — Flask Blueprint /tiles/* — TiTiler-style COG tile service
Faro Protocol · 2026-06 · rio-tiler backend

Endpoints:
  GET /tiles/ndvi/{z}/{x}/{y}  — NDVI desde Sentinel-2 L2A COGs (Element84 S3)
                                  Expresión: (B08-B04)/(B08+B04), colormap RdYlGn
  GET /tiles/sar/{z}/{x}/{y}   — SAR VV backscatter desde OPERA RTC-S1 (NASA/AWS)
  GET /tiles/health            — Estado del servicio y caché

Query params (ambos endpoints):
  venue=velez|amalfitani|balcarce|anelo  — bbox predefinida del campo
  b04=<url> b08=<url>                    — URLs COG directas (NDVI, anula STAC search)
  vv=<url>                               — URL COG directa (SAR, anula CMR search)
  opacity=0-255                          — transparencia SAR (default 102 ≈ 40%)

Cache: 1h en memoria, max 64 tiles. AWS_NO_SIGN_REQUEST=YES (buckets públicos).
"""
from __future__ import annotations

import hashlib
import io
import logging
import os
import time
from typing import Optional

from flask import Blueprint, Response, jsonify, request

log = logging.getLogger(__name__)

tiles_bp = Blueprint("tiles", __name__, url_prefix="/tiles")

# ── Cache en memoria ───────────────────────────────────────────────────────────
_TILE_CACHE: dict[str, tuple[bytes, float]] = {}
_CACHE_TTL   = 3600          # 1 hora
_CACHE_MAX   = 64            # entradas máximo (reducido para bajar RSS)

# Scene URL cache — evita llamar STAC search en cada tile del mismo viewport
_SCENE_CACHE: dict[str, tuple] = {}  # key → (result, timestamp)


def _cache_get(key: str) -> Optional[bytes]:
    entry = _TILE_CACHE.get(key)
    if entry and (time.time() - entry[1]) < _CACHE_TTL:
        return entry[0]
    _TILE_CACHE.pop(key, None)
    return None


def _cache_set(key: str, data: bytes) -> None:
    if len(_TILE_CACHE) >= _CACHE_MAX:
        oldest = min(_TILE_CACHE, key=lambda k: _TILE_CACHE[k][1])
        del _TILE_CACHE[oldest]
    _TILE_CACHE[key] = (data, time.time())


def _tile_key(*parts) -> str:
    return hashlib.sha1("|".join(str(p) for p in parts).encode()).hexdigest()


# ── Bboxes de campos conocidos ─────────────────────────────────────────────────
_BBOXES: dict[str, tuple[float, float, float, float]] = {
    "velez":      (-58.540, -34.650, -58.520, -34.630),
    "amalfitani": (-58.5305, -34.6391, -58.5271, -34.6367),
    "balcarce":   (-58.250, -37.850, -58.200, -37.800),
    "anelo":      (-68.860, -38.380, -68.790, -38.330),
}

# ── Element84 STAC ─────────────────────────────────────────────────────────────
_E84_STAC = "https://earth-search.aws.element84.com/v1"
_S2_COLL  = "sentinel-2-l2a"


def _find_s2_bands(bbox: tuple, max_cloud: int = 25) -> Optional[dict]:
    """
    Busca la escena Sentinel-2 L2A más reciente con baja nubosidad sobre bbox.
    Retorna {"B04": href, "B08": href, "scene_id": str, "cloud_pct": float} o None.
    """
    try:
        import requests
        from datetime import date, timedelta

        w, s, e, n = bbox
        end_dt   = date.today()
        start_dt = end_dt - timedelta(days=60)

        payload: dict = {
            "collections": [_S2_COLL],
            "bbox":        [w, s, e, n],
            "datetime":    f"{start_dt}T00:00:00Z/{end_dt}T23:59:59Z",
            "limit":       5,
            "query":       {"eo:cloud_cover": {"lt": max_cloud}},
            "sortby":      [{"field": "properties.eo:cloud_cover", "direction": "asc"}],
        }
        r = requests.post(f"{_E84_STAC}/search", json=payload, timeout=15)
        features = r.json().get("features", []) if r.ok else []

        if not features:
            payload["query"] = {"eo:cloud_cover": {"lt": 60}}
            r2 = requests.post(f"{_E84_STAC}/search", json=payload, timeout=15)
            features = r2.json().get("features", []) if r2.ok else []

        if not features:
            return None

        item   = features[0]
        assets = item.get("assets", {})

        # Element84 v1 usa claves "red" / "nir" (en collection sentinel-2-l2a)
        b04 = (
            assets.get("red",    {}).get("href") or
            assets.get("B04",    {}).get("href") or
            assets.get("band04", {}).get("href") or ""
        )
        b08 = (
            assets.get("nir",    {}).get("href") or
            assets.get("B08",    {}).get("href") or
            assets.get("band08", {}).get("href") or ""
        )

        if b04 and b08:
            return {
                "B04":       b04,
                "B08":       b08,
                "scene_id":  item.get("id", ""),
                "cloud_pct": item.get("properties", {}).get("eo:cloud_cover"),
            }
        return None

    except Exception as exc:
        log.warning("tiles/_find_s2_bands: %s", exc)
        return None


def _find_opera_vv(bbox: tuple) -> Optional[str]:
    """
    Busca OPERA RTC-S1 VV más reciente sobre bbox via NASA CMR STAC.
    Retorna URL HTTPS del COG VV o None.
    """
    try:
        import requests

        w, s, e, n = bbox
        r = requests.post(
            "https://cmr.earthdata.nasa.gov/stac/POCLOUD/search",
            json={
                "collections": ["OPERA_L2_RTC-S1_V1"],
                "bbox":        [w, s, e, n],
                "limit":       3,
                "sortby":      [{"field": "properties.datetime", "direction": "desc"}],
            },
            timeout=20,
        )
        if not r.ok:
            return None

        for feat in r.json().get("features", []):
            for key, asset in feat.get("assets", {}).items():
                href = asset.get("href", "")
                if "VV" in key.upper() and href.endswith(".tif"):
                    if href.startswith("s3://"):
                        bucket, _, path = href[5:].partition("/")
                        return f"https://{bucket}.s3.amazonaws.com/{path}"
                    return href
        return None

    except Exception as exc:
        log.warning("tiles/_find_opera_vv: %s", exc)
        return None


# ── Motores de renderizado ─────────────────────────────────────────────────────

def _render_ndvi_tile(z: int, x: int, y: int, b04_url: str, b08_url: str) -> bytes:
    import numpy as np
    from rio_tiler.io import COGReader
    from rio_tiler.colormap import cmap
    from rio_tiler.models import TileData

    os.environ.setdefault("AWS_NO_SIGN_REQUEST", "YES")

    with COGReader(b04_url) as cog:
        red_tile = cog.tile(x, y, z, tilesize=256)
    with COGReader(b08_url) as cog:
        nir_tile = cog.tile(x, y, z, tilesize=256)

    red  = red_tile.data[0].astype(np.float32)
    nir  = nir_tile.data[0].astype(np.float32)
    mask = np.bitwise_and(red_tile.mask, nir_tile.mask)

    with np.errstate(divide="ignore", invalid="ignore"):
        ndvi = np.where((nir + red) > 0, (nir - red) / (nir + red), 0.0)

    # NDVI [-1,1] → [0,255] para colormap
    scaled = np.clip((ndvi + 1.0) / 2.0 * 255, 0, 255).astype(np.uint8)

    cm = cmap.get("rdylgn")
    tile_data = TileData(scaled[np.newaxis], mask)
    return tile_data.render(colormap=cm, img_format="PNG")


def _render_sar_tile(z: int, x: int, y: int, vv_url: str, opacity: int = 102) -> bytes:
    import numpy as np
    from rio_tiler.io import COGReader
    from PIL import Image

    os.environ.setdefault("AWS_NO_SIGN_REQUEST", "YES")

    with COGReader(vv_url) as cog:
        tile = cog.tile(x, y, z, indexes=[1], tilesize=256)

    data = tile.data[0].astype(np.float32)
    mask = tile.mask

    with np.errstate(divide="ignore", invalid="ignore"):
        db = np.where(data > 0, 10.0 * np.log10(data + 1e-10), -30.0)

    # SAR típico: -25 dB (agua/suelo húmedo) → +5 dB (urbano/estructuras)
    scaled = np.clip((db - (-25.0)) / (5.0 - (-25.0)) * 255, 0, 255).astype(np.uint8)

    a    = np.where(mask > 0, opacity, 0).astype(np.uint8)
    rgba = np.stack([scaled, scaled, scaled, a], axis=-1)

    buf = io.BytesIO()
    Image.fromarray(rgba, mode="RGBA").save(buf, format="PNG", optimize=True)
    return buf.getvalue()


# ── Respuesta estándar con CORS ────────────────────────────────────────────────

def _tile_response(data: bytes, hit: bool) -> Response:
    resp = Response(data, mimetype="image/png")
    resp.headers["Access-Control-Allow-Origin"] = "*"
    resp.headers["Cache-Control"]               = f"public, max-age={_CACHE_TTL}"
    resp.headers["X-Tile-Cache"]                = "HIT" if hit else "MISS"
    return resp


# ── Rutas ──────────────────────────────────────────────────────────────────────

@tiles_bp.route("/ndvi/<int:z>/<int:x>/<int:y>")
def ndvi_tile(z: int, x: int, y: int):
    venue   = request.args.get("venue", "velez")
    b04_url = request.args.get("b04", "")
    b08_url = request.args.get("b08", "")
    bbox    = _BBOXES.get(venue, _BBOXES["velez"])
    ckey    = _tile_key("ndvi", z, x, y, b04_url, b08_url, venue)

    cached = _cache_get(ckey)
    if cached:
        return _tile_response(cached, True)

    try:
        if not (b04_url and b08_url):
            skey = f"s2_{venue}"
            se = _SCENE_CACHE.get(skey)
            if se and (time.time() - se[1]) < _CACHE_TTL:
                scene = se[0]
            else:
                scene = _find_s2_bands(bbox)
                _SCENE_CACHE[skey] = (scene, time.time())
            if not scene:
                return Response(b"", status=503, mimetype="image/png")
            b04_url = scene["B04"]
            b08_url = scene["B08"]
            log.info("tiles/ndvi: scene=%s cloud=%.1f%%",
                     scene["scene_id"][:30], scene.get("cloud_pct") or 0)

        png = _render_ndvi_tile(z, x, y, b04_url, b08_url)
        _cache_set(ckey, png)
        return _tile_response(png, False)

    except Exception as exc:
        log.error("tiles/ndvi z=%d x=%d y=%d: %s", z, x, y, exc)
        return Response(b"", status=500, mimetype="image/png")


@tiles_bp.route("/sar/<int:z>/<int:x>/<int:y>")
def sar_tile(z: int, x: int, y: int):
    venue   = request.args.get("venue", "velez")
    vv_url  = request.args.get("vv", "")
    opacity = int(request.args.get("opacity", 102))
    bbox    = _BBOXES.get(venue, _BBOXES["velez"])
    ckey    = _tile_key("sar", z, x, y, vv_url, venue, opacity)

    cached = _cache_get(ckey)
    if cached:
        return _tile_response(cached, True)

    try:
        if not vv_url:
            skey = f"sar_{venue}"
            se = _SCENE_CACHE.get(skey)
            if se and (time.time() - se[1]) < _CACHE_TTL:
                vv_url = se[0]
            else:
                vv_url = _find_opera_vv(bbox)
                _SCENE_CACHE[skey] = (vv_url, time.time())
            if not vv_url:
                return Response(b"", status=503, mimetype="image/png")
            log.info("tiles/sar: url=%s", vv_url[:60])

        png = _render_sar_tile(z, x, y, vv_url, opacity)
        _cache_set(ckey, png)
        return _tile_response(png, False)

    except Exception as exc:
        log.error("tiles/sar z=%d x=%d y=%d: %s", z, x, y, exc)
        return Response(b"", status=500, mimetype="image/png")


@tiles_bp.route("/health")
def tiles_health():
    now    = time.time()
    active = sum(1 for _, (_, ts) in _TILE_CACHE.items() if (now - ts) < _CACHE_TTL)
    return jsonify({
        "status":       "ok",
        "service":      "faro-tiles",
        "cache_active": active,
        "cache_total":  len(_TILE_CACHE),
        "cache_ttl_h":  _CACHE_TTL / 3600,
        "venues":       list(_BBOXES.keys()),
        "sources": {
            "ndvi": f"{_E84_STAC} (Sentinel-2 L2A COGs, Element84 S3)",
            "sar":  "NASA CMR STAC → OPERA RTC-S1 (s3://opera-pds-rtc, público)",
        },
    })
