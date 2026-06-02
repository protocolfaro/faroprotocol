"""
dale_play_satellite.py — Baseline satelital pre/post evento del Amalfitani.

Jerarquía de fuentes NDVI (de mayor a menor frecuencia/prioridad):
  1. HLS (Harmonized Landsat Sentinel-2) — 30m, ~1.4 días, modo post_show
  2. Sentinel-2 L2A — 10m, ~5 días, fuente principal modo full
  3. Landsat C2L2 TIRS — temperatura superficial (complementario)
  + MODIS MOD09GQ — alerta temprana 250m diario (solo post_show)
  + Sen2SR — super-resolución 10m→2.5m cuando hay S2 limpia (solo post_show)

Fuente NDVI: Microsoft Planetary Computer STAC + NASA CMR (MODIS).
"""
from __future__ import annotations
import logging
from datetime import date, timedelta
from typing import Optional

from dale_play_config import VENUE_BBOX, NDVI_BUENO, NDVI_DEGRADADO

log = logging.getLogger(__name__)

_PC_STAC  = "https://planetarycomputer.microsoft.com/api/stac/v1"
# AWS Earth Search (Element84) — sin autenticación, datos disponibles horas después del pase
_AWS_STAC = "https://earth-search.aws.element84.com/v1"
_CMR_API  = "https://cmr.earthdata.nasa.gov/search/granules.json"

# MODIS tile que cubre Buenos Aires (lat -34.6, lon -58.5)
_MODIS_TILE = "h13v12"


def _classify_ndvi(ndvi: float, month: int | None = None) -> dict:
    # Bermuda grass dormancy: NDVI 0.08-0.25 in months 4-8 (BsAs otoño/invierno)
    if month is not None and 4 <= month <= 8 and 0.08 <= ndvi <= 0.25:
        return {"semaforo": "amarillo", "label": "Dormancia estacional (Bermuda — invierno BsAs)"}
    if ndvi >= NDVI_BUENO:
        return {"semaforo": "verde",    "label": "Césped en buen estado"}
    if ndvi >= NDVI_DEGRADADO:
        return {"semaforo": "amarillo", "label": "Estrés moderado — monitorear"}
    return {"semaforo": "rojo",         "label": "Daño severo — requiere intervención"}


# ─────────────────────────────────────────────────────────────────────────────
# HLS — Harmonized Landsat Sentinel-2 (~1.4 día revisita, 30m)
# ─────────────────────────────────────────────────────────────────────────────

def _search_hls(days_back: int = 15, max_cloud: float = 30.0,
                min_date: str = "") -> Optional[dict]:
    """
    Busca la imagen HLS más reciente sobre el Amalfitani en Planetary Computer.
    HLS fusiona Landsat 8/9 + Sentinel-2A/B/C → ~1.4 días de revisita promedio.
    min_date: si se pasa (YYYY-MM-DD), solo acepta imágenes posteriores a esa fecha.
    """
    try:
        import requests
        end_dt   = date.today()
        start_dt = date.fromisoformat(min_date) if min_date else end_dt - timedelta(days=days_back)
        if start_dt > end_dt:
            return None
        minx, miny, maxx, maxy = VENUE_BBOX
        payload = {
            "collections": ["hls"],
            "bbox":        [minx, miny, maxx, maxy],
            "datetime":    f"{start_dt.isoformat()}T00:00:00Z/{end_dt.isoformat()}T23:59:59Z",
            "query":       {"eo:cloud_cover": {"lt": max_cloud}},
            "limit":       10,
            "sortby":      [{"field": "datetime", "direction": "desc"}],
        }
        r = requests.post(f"{_PC_STAC}/search", json=payload, timeout=20)
        if r.status_code in (400, 404):
            return None
        r.raise_for_status()
        features = r.json().get("features", [])
        return features[0] if features else None
    except Exception as e:
        log.debug("dale_play_satellite: HLS search: %s", e)
        return None


def _ndvi_from_hls_item(item: dict) -> Optional[float]:
    """
    NDVI desde item HLS. Bandas:
    - HLS L30 (Landsat): Red=B04, NIR=B05
    - HLS S30 (Sentinel-2): Red=B04, NIR=B8A
    """
    try:
        import rasterio, numpy as np
        from rasterio.warp import transform_bounds
        from rasterio.windows import from_bounds
        import requests as _req

        item_id  = item.get("id", "")
        nir_key  = "B05" if "L30" in item_id else "B8A"
        col_name = item.get("collection", "hls")

        tok_r = _req.get(
            f"https://planetarycomputer.microsoft.com/api/sas/v1/token/{col_name}",
            timeout=10
        )
        token = tok_r.json().get("token", "") if tok_r.ok else ""

        def _read(key: str) -> Optional[np.ndarray]:
            href = item.get("assets", {}).get(key, {}).get("href", "")
            if not href:
                return None
            if token:
                href = f"{href}?{token}"
            with rasterio.open(href) as src:
                minx, miny, maxx, maxy = VENUE_BBOX
                native = transform_bounds("EPSG:4326", src.crs, minx, miny, maxx, maxy)
                win    = from_bounds(*native, transform=src.transform)
                return src.read(1, window=win).astype("float32") / 10_000.0

        red = _read("B04")
        nir = _read(nir_key)
        if red is None or nir is None:
            return None

        valid = (red > 0) & (nir > 0) & (red < 1) & (nir < 1)
        if valid.sum() < 4:
            return None
        r_v, n_v = red[valid], nir[valid]
        return float(round(float(np.mean((n_v - r_v) / (n_v + r_v + 1e-9))), 3))
    except Exception as e:
        log.warning("dale_play_satellite: HLS NDVI error: %s", e)
        return None


# ─────────────────────────────────────────────────────────────────────────────
# Sentinel-2 — AWS S3 directo (Element84 Earth Search, sin autenticación)
# Disponible ~2-4 horas después del pase de satélite
# ─────────────────────────────────────────────────────────────────────────────

def _search_s2_aws(days_back: int = 30, max_cloud: float = 30.0,
                   min_date: str = "") -> Optional[dict]:
    """
    Busca S2 en AWS Element84 Earth Search.
    Los assets apuntan a s3://sentinel-cogs/ accesibles via HTTPS sin token.
    Ventaja: datos disponibles ~2-4h después del pase (vs ~12-24h en PC).
    min_date: si se pasa (YYYY-MM-DD), solo acepta imágenes posteriores a esa fecha.
    """
    try:
        import requests
        end_dt   = date.today()
        start_dt = date.fromisoformat(min_date) if min_date else end_dt - timedelta(days=days_back)
        if start_dt > end_dt:
            return None
        minx, miny, maxx, maxy = VENUE_BBOX
        payload = {
            "collections": ["sentinel-2-l2a"],
            "bbox":        [minx, miny, maxx, maxy],
            "datetime":    f"{start_dt.isoformat()}T00:00:00Z/{end_dt.isoformat()}T23:59:59Z",
            "query":       {"eo:cloud_cover": {"lt": max_cloud}},
            "limit":       10,
            "sortby":      [{"field": "eo:cloud_cover", "direction": "asc"}],
        }
        r = requests.post(f"{_AWS_STAC}/search", json=payload, timeout=20)
        if not r.ok:
            return None
        features = r.json().get("features", [])
        return features[0] if features else None
    except Exception as exc:
        log.debug("dale_play_satellite: AWS S2 search: %s", exc)
        return None


def _ndvi_from_item_aws(item: dict) -> Optional[float]:
    """NDVI desde S2 via AWS S3 (sin token, HTTPS público)."""
    try:
        import rasterio, numpy as np, os
        from rasterio.warp import transform_bounds
        from rasterio.windows import from_bounds

        os.environ["AWS_NO_SIGN_REQUEST"] = "YES"

        def _read(key: str) -> Optional[np.ndarray]:
            href = item.get("assets", {}).get(key, {}).get("href", "")
            if not href:
                return None
            # Convertir s3:// a HTTPS si es necesario
            if href.startswith("s3://sentinel-cogs/"):
                href = href.replace("s3://sentinel-cogs/",
                                    "https://sentinel-cogs.s3.us-west-2.amazonaws.com/")
            with rasterio.open(href) as src:
                minx, miny, maxx, maxy = VENUE_BBOX
                native = transform_bounds("EPSG:4326", src.crs, minx, miny, maxx, maxy)
                win    = from_bounds(*native, transform=src.transform)
                return src.read(1, window=win).astype("float32") / 10_000.0

        red = _read("red")   # Earth Search usa "red" / "nir" como keys
        nir = _read("nir")
        if red is None:
            red = _read("B04")
        if nir is None:
            nir = _read("B08")

        if red is None or nir is None:
            return None

        valid = (red > 0) & (nir > 0) & (red < 1) & (nir < 1)
        if valid.sum() < 4:
            return None
        r_v, n_v = red[valid], nir[valid]
        return float(round(float(np.mean((n_v - r_v) / (n_v + r_v + 1e-9))), 3))
    except Exception as exc:
        log.debug("dale_play_satellite: AWS S2 NDVI: %s", exc)
        return None


# ─────────────────────────────────────────────────────────────────────────────
# Sentinel-2 — Planetary Computer fallback (10m, ~5 días)
# ─────────────────────────────────────────────────────────────────────────────

def _search_s2(days_back: int = 30, max_cloud: float = 30.0,
               min_date: str = "") -> Optional[dict]:
    import requests
    end_dt   = date.today()
    start_dt = date.fromisoformat(min_date) if min_date else end_dt - timedelta(days=days_back)
    if start_dt > end_dt:
        return None
    minx, miny, maxx, maxy = VENUE_BBOX
    payload = {
        "collections": ["sentinel-2-l2a"],
        "bbox":        [minx, miny, maxx, maxy],
        "datetime":    f"{start_dt.isoformat()}T00:00:00Z/{end_dt.isoformat()}T23:59:59Z",
        "query":       {"eo:cloud_cover": {"lt": max_cloud}},
        "limit":       10,
        "sortby":      [{"field": "eo:cloud_cover", "direction": "asc"}],
    }
    r = requests.post(f"{_PC_STAC}/search", json=payload, timeout=20)
    r.raise_for_status()
    features = r.json().get("features", [])
    return features[0] if features else None


def _ndvi_from_item(item: dict) -> Optional[float]:
    """NDVI medio sobre VENUE_BBOX desde Sentinel-2 B04/B08."""
    try:
        import rasterio, numpy as np
        from rasterio.warp import transform_bounds
        from rasterio.windows import from_bounds
        import requests as _req

        tok_r = _req.get(
            "https://planetarycomputer.microsoft.com/api/sas/v1/token/sentinel-2-l2a",
            timeout=10
        )
        token = tok_r.json().get("token", "") if tok_r.ok else ""

        def _read_band(key: str) -> Optional[np.ndarray]:
            href = item.get("assets", {}).get(key, {}).get("href", "")
            if not href:
                return None
            if token:
                href = f"{href}?{token}"
            with rasterio.open(href) as src:
                minx, miny, maxx, maxy = VENUE_BBOX
                native = transform_bounds("EPSG:4326", src.crs, minx, miny, maxx, maxy)
                win    = from_bounds(*native, transform=src.transform)
                return src.read(1, window=win).astype("float32") / 10_000.0

        red = _read_band("B04")
        nir = _read_band("B08")
        if red is None or nir is None:
            return None

        valid = (red > 0) & (nir > 0) & (red < 1) & (nir < 1)
        if valid.sum() < 4:
            return None
        r_v, n_v = red[valid], nir[valid]
        return float(round(float(np.mean((n_v - r_v) / (n_v + r_v + 1e-9))), 3))
    except Exception as e:
        log.warning("dale_play_satellite: S2 NDVI error: %s", e)
        return None


# ─────────────────────────────────────────────────────────────────────────────
# Landsat TIRS — temperatura superficial
# ─────────────────────────────────────────────────────────────────────────────

def _search_landsat(days_back: int = 45) -> Optional[dict]:
    import requests
    end_dt   = date.today()
    start_dt = end_dt - timedelta(days=days_back)
    minx, miny, maxx, maxy = VENUE_BBOX
    payload = {
        "collections": ["landsat-c2-l2"],
        "bbox":        [minx, miny, maxx, maxy],
        "datetime":    f"{start_dt.isoformat()}T00:00:00Z/{end_dt.isoformat()}T23:59:59Z",
        "query":       {"eo:cloud_cover": {"lt": 40}},
        "limit":       5,
        "sortby":      [{"field": "datetime", "direction": "desc"}],
    }
    r = requests.post(f"{_PC_STAC}/search", json=payload, timeout=20)
    r.raise_for_status()
    features = r.json().get("features", [])
    return features[0] if features else None


def _tirs_celsius(item: dict) -> Optional[float]:
    """Temperatura superficial °C desde Landsat C2 ST_B10."""
    try:
        import rasterio, numpy as np
        from rasterio.warp import transform_bounds
        from rasterio.windows import from_bounds

        href = item.get("assets", {}).get("ST_B10", {}).get("href", "")
        if not href:
            return None
        with rasterio.open(href) as src:
            minx, miny, maxx, maxy = VENUE_BBOX
            native = transform_bounds("EPSG:4326", src.crs, minx, miny, maxx, maxy)
            win    = from_bounds(*native, transform=src.transform)
            data   = src.read(1, window=win).astype("float32")
        valid   = (data > 0) & (data < 65535)
        if valid.sum() < 2:
            return None
        kelvin  = data[valid] * 0.00341802 + 149.0
        celsius = kelvin - 273.15
        return float(round(float(np.mean(celsius)), 1))
    except Exception as e:
        log.warning("dale_play_satellite: TIRS error: %s", e)
        return None


# ─────────────────────────────────────────────────────────────────────────────
# MODIS — alerta temprana diaria 250m (solo post_show)
# ─────────────────────────────────────────────────────────────────────────────

def _check_modis_alert(show_date: str = "", days_back: int = 5) -> dict:
    """
    Consulta NASA CMR para granules MODIS MOD09GQ post-show.
    No descarga píxeles (requeriría NASA Earthdata credentials).
    Solo señaliza disponibilidad de datos para alerta temprana.
    Tile h13v12 cubre Buenos Aires.
    """
    try:
        import requests
        end_dt = date.today()
        if show_date:
            try:
                start_dt = date.fromisoformat(show_date)
            except Exception:
                start_dt = end_dt - timedelta(days=days_back)
        else:
            start_dt = end_dt - timedelta(days=days_back)

        minx, miny, maxx, maxy = VENUE_BBOX
        params = {
            "short_name":   "MOD09GQ",
            "version":      "061",
            "temporal":     f"{start_dt.isoformat()}T00:00:00Z,{end_dt.isoformat()}T23:59:59Z",
            "bounding_box": f"{minx},{miny},{maxx},{maxy}",
            "page_size":    10,
            "sort_key":     "-start_date",
        }
        r = requests.get(_CMR_API, params=params, timeout=15)
        if not r.ok:
            return {"disponible": False, "n_granules": 0, "error": r.status_code}

        granules = r.json().get("feed", {}).get("entry", [])
        n        = len(granules)
        fecha_rec = (granules[0].get("time_start") or "")[:10] if granules else ""
        return {
            "disponible":          n > 0,
            "n_granules":          n,
            "fecha_mas_reciente":  fecha_rec,
            "tile":                _MODIS_TILE,
            "producto":            "MOD09GQ v061 · 250m · diario",
            "fuente":              "NASA CMR · MODIS Terra MOD09GQ",
        }
    except Exception as e:
        log.debug("dale_play_satellite: MODIS CMR: %s", e)
        return {"disponible": False, "n_granules": 0, "error": str(e)}


# ─────────────────────────────────────────────────────────────────────────────
# Sen2SR — super-resolución 10m → 2.5m (opcional, solo post_show + S2 limpia)
# ─────────────────────────────────────────────────────────────────────────────

def _apply_sen2sr(item: dict) -> dict:
    """
    Sen2SR ESA super-resolution: Sentinel-2 10m → 2.5m sobre VENUE_BBOX.
    Solo aplica si: sen2sr instalado (pip install sen2sr) + nubosidad < 15%.
    Si no está instalado, retorna sen2sr_estado='no_instalado' sin error.
    """
    result: dict = {"sen2sr_aplicado": False}
    try:
        import sen2sr  # noqa: F401
    except ImportError:
        result["sen2sr_estado"] = "no_instalado — pip install sen2sr"
        return result

    cloud = (item.get("properties") or {}).get("eo:cloud_cover", 100)
    if cloud >= 15.0:
        result["sen2sr_estado"] = f"omitido — nubosidad {cloud:.0f}% > umbral 15%"
        return result

    try:
        import pathlib, tempfile, numpy as np
        import rasterio, requests as _req
        from rasterio.warp import transform_bounds
        from rasterio.windows import from_bounds

        tok_r = _req.get(
            "https://planetarycomputer.microsoft.com/api/sas/v1/token/sentinel-2-l2a",
            timeout=10
        )
        token = tok_r.json().get("token", "") if tok_r.ok else ""

        bands_out = {}
        for band in ["B02", "B03", "B04", "B08"]:
            href = item.get("assets", {}).get(band, {}).get("href", "")
            if not href:
                continue
            if token:
                href = f"{href}?{token}"
            with rasterio.open(href) as src:
                minx, miny, maxx, maxy = VENUE_BBOX
                native = transform_bounds("EPSG:4326", src.crs, minx, miny, maxx, maxy)
                win    = from_bounds(*native, transform=src.transform)
                bands_out[band] = src.read(1, window=win).astype("float32") / 10_000.0

        if len(bands_out) < 4:
            result["sen2sr_estado"] = "error — bandas incompletas"
            return result

        stack = np.stack(
            [bands_out["B02"], bands_out["B03"], bands_out["B04"], bands_out["B08"]], axis=0
        )
        sr       = sen2sr.superresolve(stack)
        out_path = pathlib.Path(tempfile.gettempdir()) / "sen2sr_amalfitani.npy"
        np.save(str(out_path), sr)
        result.update({
            "sen2sr_aplicado":   True,
            "sen2sr_resolucion": "2.5m",
            "sen2sr_output":     str(out_path),
            "sen2sr_estado":     "ok",
        })
        log.info("dale_play_satellite: Sen2SR → %s", out_path)
    except Exception as e:
        result["sen2sr_estado"] = f"error: {e}"
        log.debug("dale_play_satellite: Sen2SR error: %s", e)

    return result


# ─────────────────────────────────────────────────────────────────────────────
# Entry point principal
# ─────────────────────────────────────────────────────────────────────────────

def fetch_satellite_baseline(
    days_back_s2: int = 30,
    days_back_ls: int = 45,
    mode: str = "full",
    show_date: str = "",
) -> dict:
    """
    Baseline satelital completo para el Amalfitani.

    Jerarquía NDVI:
      1. HLS (modo post_show, days_back=10) — 30m, ~1.4d revisita
      2. Sentinel-2 L2A (ambos modos) — 10m, ~5d revisita
    Extras post_show: MODIS alerta temprana + Sen2SR super-resolución.

    Campos de salida nuevos:
      fuente_tipo    → "HLS" | "S2" | "sin_dato" | "error"
      revisita_dias  → float días de revisita de la fuente usada
      modis_alerta   → dict (solo post_show)
      sen2sr_*       → dict (solo post_show, solo si S2)
    """
    result: dict = {}
    _ndvi_month: Optional[int] = None

    # En modo post_show, solo aceptar imágenes POSTERIORES a la fecha del show.
    # Sin este filtro, se compara pre-show vs pre-show → delta NDVI = 0 (falso negativo).
    _min_date = show_date if (mode == "post_show" and show_date) else ""

    # ── 1. HLS — primaria en modo post_show ───────────────────────────────────
    if mode == "post_show":
        try:
            hls_item = _search_hls(days_back=10, min_date=_min_date)
            if hls_item:
                ndvi  = _ndvi_from_hls_item(hls_item)
                props = hls_item.get("properties", {})
                dt_str = (props.get("datetime") or "")[:10]
                _ndvi_month = int(dt_str[5:7]) if len(dt_str) >= 7 else None
                result.update({
                    "ndvi":           ndvi,
                    "ndvi_fecha":     dt_str,
                    "ndvi_cloud_pct": round(props.get("eo:cloud_cover", 0), 1),
                    "ndvi_status":    _classify_ndvi(ndvi, month=_ndvi_month) if ndvi is not None
                                      else {"semaforo": "sin_datos", "label": "Sin imagen HLS disponible"},
                    "fuente_ndvi":    "HLS · Harmonized Landsat+Sentinel-2 · Planetary Computer",
                    "fuente_tipo":    "HLS",
                    "revisita_dias":  1.4,
                    "hls_producto":   hls_item.get("id", "")[:30],
                })
                log.info("dale_play_satellite: HLS NDVI %s fecha %s", ndvi, dt_str)
        except Exception as e:
            log.warning("dale_play_satellite: HLS failed: %s", e)

    # ── 2. Sentinel-2 — AWS primero (más rápido), PC como fallback ───────────
    if not result.get("ndvi_fecha"):
        s2_item    = None
        s2_source  = None

        # Intentar AWS Earth Search primero (datos disponibles ~2-4h antes)
        try:
            s2_item = _search_s2_aws(days_back=days_back_s2, min_date=_min_date)
            if s2_item:
                s2_source = "AWS"
                log.info("dale_play_satellite: S2 encontrada en AWS Earth Search")
        except Exception as exc_aws:
            log.debug("dale_play_satellite: AWS S2 search: %s", exc_aws)

        # Fallback a Planetary Computer
        if not s2_item:
            try:
                s2_item = _search_s2(days_back=days_back_s2, min_date=_min_date)
                if s2_item:
                    s2_source = "PC"
            except Exception as exc_pc:
                log.warning("dale_play_satellite: PC S2 search: %s", exc_pc)

        if s2_item:
            try:
                # Leer NDVI con el cliente correcto
                ndvi = _ndvi_from_item_aws(s2_item) if s2_source == "AWS" else None
                if ndvi is None:
                    ndvi = _ndvi_from_item(s2_item)
                    s2_source = "PC"

                props  = s2_item.get("properties", {})
                dt_str = (props.get("datetime") or "")[:10]
                _ndvi_month = int(dt_str[5:7]) if len(dt_str) >= 7 else None
                fuente_label = (
                    "Sentinel-2 L2A · AWS Earth Search (s3://sentinel-cogs)"
                    if s2_source == "AWS"
                    else "Sentinel-2 L2A · Planetary Computer"
                )
                result.update({
                    "ndvi":           ndvi,
                    "ndvi_fecha":     dt_str,
                    "ndvi_cloud_pct": round(props.get("eo:cloud_cover", 0), 1),
                    "ndvi_status":    _classify_ndvi(ndvi, month=_ndvi_month) if ndvi is not None
                                      else {"semaforo": "sin_datos", "label": "Sin imagen disponible"},
                    "fuente_ndvi":    fuente_label,
                    "fuente_s2":      fuente_label,
                    "fuente_tipo":    "S2",
                    "s2_fuente_backend": s2_source,
                    "revisita_dias":  5.0,
                })
                log.info("dale_play_satellite: S2 [%s] NDVI %s fecha %s",
                         s2_source, ndvi, dt_str)

                # Sen2SR — solo post_show + imagen limpia
                if mode == "post_show":
                    result.update(_apply_sen2sr(s2_item))
            except Exception as exc:
                log.warning("dale_play_satellite: S2 NDVI read: %s", exc)
                result.update({
                    "ndvi": None, "fuente_tipo": "error",
                    "ndvi_status": {"semaforo": "error", "label": str(exc)},
                })
        else:
            result.update({
                "ndvi":          None,
                "ndvi_fecha":    None,
                "fuente_tipo":   "sin_dato",
                "revisita_dias": None,
                "ndvi_status":   {"semaforo": "sin_datos", "label": "Sin escena disponible"},
            })

    # ── 3. Landsat TIRS — temperatura superficial ─────────────────────────────
    try:
        ls = _search_landsat(days_back=days_back_ls)
        if ls:
            tirs  = _tirs_celsius(ls)
            props = ls.get("properties", {})
            result.update({
                "tirs_celsius": tirs,
                "tirs_fecha":   (props.get("datetime") or "")[:10],
                "fuente_tirs":  "Landsat-9 C2L2 ST_B10 · Planetary Computer",
            })
            log.info("dale_play_satellite: TIRS %.1f°C fecha %s", tirs or 0, result["tirs_fecha"])
        else:
            result.update({"tirs_celsius": None, "tirs_fecha": None})
    except Exception as e:
        log.warning("dale_play_satellite: Landsat TIRS failed: %s", e)
        result.update({"tirs_celsius": None})

    # ── 4. MODIS alerta temprana — solo post_show ─────────────────────────────
    if mode == "post_show":
        try:
            modis = _check_modis_alert(show_date=show_date, days_back=5)
            result["modis_alerta"] = modis
            if modis.get("disponible"):
                log.info(
                    "dale_play_satellite: MODIS %d granules post-show · fecha %s",
                    modis["n_granules"], modis.get("fecha_mas_reciente", "?")
                )
        except Exception as e:
            log.debug("dale_play_satellite: MODIS alert: %s", e)
            result["modis_alerta"] = {"disponible": False, "error": str(e)}

    # Compatibilidad con versión anterior
    result.setdefault("fuente_s2", "Sentinel-2 L2A · Planetary Computer")
    result.setdefault("fuente_tipo", "sin_dato")
    result.setdefault("revisita_dias", None)

    # Flag explícito para que el pipeline sepa si hay imagen real post-show
    # o si tiene que depender de STARFM / SAR como única fuente de daño.
    if mode == "post_show":
        ndvi_fecha = result.get("ndvi_fecha", "")
        tiene_post = bool(ndvi_fecha and _min_date and ndvi_fecha > _min_date)
        result["ndvi_post_disponible"] = tiene_post
        if not tiene_post:
            log.warning(
                "dale_play_satellite: sin imagen post-show disponible (show=%s) "
                "— NDVI de daño dependerá de STARFM/SAR",
                show_date,
            )

    return result
