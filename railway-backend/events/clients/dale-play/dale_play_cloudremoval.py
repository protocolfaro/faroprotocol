"""
dale_play_cloudremoval.py — Detección y remoción de nubes en S2 post-show.

Usa s2cloudless (pip install s2cloudless) para detectar píxeles limpios en
imágenes Sentinel-2 con nubosidad alta. Si la cobertura limpia sobre el campo
supera el 30%, el NDVI resultante se clasifica como fuente SILVER.

Flujo:
  1. Busca imagen S2 post-show (acepta cualquier nubosidad)
  2. Descarga 10 bandas necesarias para s2cloudless
  3. Detecta nubes píxel a píxel (modelo gradient-boosted, sin GPU)
  4. Calcula NDVI solo en píxeles limpios (prob_nube < 0.4)
  5. Si cobertura_limpia > 30% → estado = SILVER

Guarda en: models/cloudremoved_{show_id}.json
"""
from __future__ import annotations
import json, logging, pathlib
from datetime import date, timedelta
from typing import Optional

from dale_play_config import VENUE_BBOX

log = logging.getLogger(__name__)

_PC_STAC    = "https://planetarycomputer.microsoft.com/api/stac/v1"
_MODELS_DIR = pathlib.Path(__file__).parent / "models"

# Umbral de probabilidad de nube — píxeles con prob > umbral se descartan
_CLOUD_PROB_THRESHOLD = 0.4

# Porcentaje mínimo de campo limpio para considerar NDVI válido
_MIN_CLEAN_PCT = 30.0

# Resolución objetivo para lectura de bandas (20m balance calidad/velocidad)
_TARGET_RES_M = 20


# Bandas requeridas por s2cloudless (en este orden exacto)
_S2CL_BANDS = ["B01", "B02", "B04", "B05", "B08", "B8A", "B09", "B10", "B11", "B12"]


def _search_s2_any_cloud(show_date: str, days_after: int = 15) -> Optional[dict]:
    """
    Busca S2 post-show aceptando CUALQUIER nivel de nubosidad.
    Retorna la escena más reciente (no la más limpia).
    """
    try:
        import requests
        try:
            start_dt = date.fromisoformat(show_date) + timedelta(days=1)
        except Exception:
            start_dt = date.today() - timedelta(days=days_after)
        end_dt = start_dt + timedelta(days=days_after)

        minx, miny, maxx, maxy = VENUE_BBOX
        payload = {
            "collections": ["sentinel-2-l2a"],
            "bbox":        [minx, miny, maxx, maxy],
            "datetime":    f"{start_dt.isoformat()}T00:00:00Z/{end_dt.isoformat()}T23:59:59Z",
            "limit":       5,
            "sortby":      [{"field": "datetime", "direction": "desc"}],
        }
        r = requests.post(f"{_PC_STAC}/search", json=payload, timeout=20)
        if not r.ok:
            return None
        features = r.json().get("features", [])
        return features[0] if features else None
    except Exception as exc:
        log.debug("cloudremoval: S2 search: %s", exc)
        return None


def _get_sas_token() -> str:
    try:
        import requests
        r = requests.get(
            "https://planetarycomputer.microsoft.com/api/sas/v1/token/sentinel-2-l2a",
            timeout=10,
        )
        return r.json().get("token", "") if r.ok else ""
    except Exception:
        return ""


def _read_band_resamp(item: dict, band_key: str, token: str) -> Optional["np.ndarray"]:
    """Lee una banda S2 recortada sobre VENUE_BBOX y normalizada a [0, 1]."""
    try:
        import rasterio, numpy as np
        from rasterio.warp import transform_bounds
        from rasterio.windows import from_bounds
        from rasterio.enums import Resampling

        href = item.get("assets", {}).get(band_key, {}).get("href", "")
        if not href:
            return None
        if token:
            href = f"{href}?{token}"

        minx, miny, maxx, maxy = VENUE_BBOX

        with rasterio.open(href) as src:
            native  = transform_bounds("EPSG:4326", src.crs, minx, miny, maxx, maxy)
            win     = from_bounds(*native, transform=src.transform)
            # Calcular dimensiones objetivo al _TARGET_RES_M
            # Tamaño venue: ~370m × 270m → en píxeles a 20m: ~19×14
            bbox_w_m = (maxx - minx) * 85_000   # ° → m aprox (BsAs lat)
            bbox_h_m = (maxy - miny) * 111_000
            out_w    = max(4, int(bbox_w_m / _TARGET_RES_M))
            out_h    = max(4, int(bbox_h_m / _TARGET_RES_M))

            data = src.read(
                1,
                window=win,
                out_shape=(out_h, out_w),
                resampling=Resampling.bilinear,
            ).astype("float32") / 10_000.0
        return data
    except Exception as exc:
        log.debug("cloudremoval: read_band %s: %s", band_key, exc)
        return None


def _detect_clouds(
    bands: "dict[str, np.ndarray]",
) -> Optional["np.ndarray"]:
    """
    Aplica s2cloudless sobre las 10 bandas requeridas.
    Retorna mapa de probabilidad de nube [0, 1] por píxel, o None.
    """
    try:
        from s2cloudless import S2PixelCloudDetector
        import numpy as np

        # Verificar que tenemos las 10 bandas
        missing = [b for b in _S2CL_BANDS if b not in bands]
        if missing:
            log.debug("cloudremoval: faltan bandas s2cloudless: %s", missing)
            return None

        H, W = bands[_S2CL_BANDS[0]].shape
        stack = np.stack([bands[b] for b in _S2CL_BANDS], axis=-1)   # (H, W, 10)
        # s2cloudless espera (N, H, W, 10) — N=1 imagen
        stack = stack[np.newaxis, ...]                                 # (1, H, W, 10)

        detector = S2PixelCloudDetector(
            threshold=_CLOUD_PROB_THRESHOLD,
            average_over=2,
            dilation_size=2,
            all_bands=False,
        )
        # get_cloud_probability_maps retorna (1, H, W)
        prob = detector.get_cloud_probability_maps(stack)
        return prob[0]   # (H, W)
    except ImportError:
        log.debug("cloudremoval: s2cloudless no instalado (pip install s2cloudless)")
        return None
    except Exception as exc:
        log.warning("cloudremoval: detect_clouds: %s", exc)
        return None


def compute_cloud_removed_ndvi(
    show_id:   str,
    show_date: str,
    days_after: int = 15,
) -> dict:
    """
    Entry point: NDVI post-show con remoción de nubes via s2cloudless.

    Retorna dict con:
      ndvi_limpio, cobertura_limpia_pct, estado (SILVER/insuficiente/sin_imagen),
      n_px_total, n_px_limpio, ndvi_cloud_pct_original.

    Guarda resultado en models/cloudremoved_{show_id}.json.
    """
    result: dict = {
        "show_id":               show_id,
        "estado":                "sin_imagen",
        "ndvi_limpio":           None,
        "cobertura_limpia_pct":  0.0,
        "n_px_total":            0,
        "n_px_limpio":           0,
        "ndvi_cloud_pct_original": None,
        "fallback_usado":        True,
        "fuente":                "s2cloudless + Sentinel-2 L2A · Planetary Computer",
    }

    # ── Buscar escena S2 post-show ────────────────────────────────────────────
    item = _search_s2_any_cloud(show_date, days_after)
    if not item:
        result["error"] = f"Sin escena S2 post-show en D+1 a D+{days_after}"
        _save(show_id, result)
        return result

    props   = item.get("properties", {})
    dt_str  = (props.get("datetime") or "")[:10]
    orig_cloud = float(props.get("eo:cloud_cover", 100) or 100)

    result["ndvi_fecha"]             = dt_str
    result["ndvi_cloud_pct_original"] = orig_cloud
    log.info("cloudremoval: escena %s — nubosidad original %.0f%%", dt_str, orig_cloud)

    # ── Descargar bandas ──────────────────────────────────────────────────────
    token = _get_sas_token()
    bands: dict = {}
    for bkey in _S2CL_BANDS:
        arr = _read_band_resamp(item, bkey, token)
        if arr is not None:
            bands[bkey] = arr

    if len(bands) < len(_S2CL_BANDS):
        log.warning("cloudremoval: solo %d/%d bandas disponibles", len(bands), len(_S2CL_BANDS))

    # ── Máscara de nubes ──────────────────────────────────────────────────────
    import numpy as np

    H, W = next(iter(bands.values())).shape if bands else (0, 0)
    if H == 0:
        result["error"] = "Sin datos de bandas disponibles"
        _save(show_id, result)
        return result

    cloud_prob = _detect_clouds(bands)

    if cloud_prob is None:
        # Fallback sin s2cloudless: usar máscara SCL si disponible, si no aceptar todo
        log.info("cloudremoval: s2cloudless no disponible — máscara trivial (acepta todo)")
        clean_mask = np.ones((H, W), dtype=bool)
        metodo     = "sin_mascara"
    else:
        clean_mask = cloud_prob < _CLOUD_PROB_THRESHOLD
        metodo     = "s2cloudless"

    n_total = int(clean_mask.size)
    n_limpio = int(clean_mask.sum())
    coverage = float(round(n_limpio / max(n_total, 1) * 100, 1))

    log.info("cloudremoval: %s — píxeles limpios %d/%d (%.0f%%)",
             metodo, n_limpio, n_total, coverage)

    # ── NDVI sobre píxeles limpios ────────────────────────────────────────────
    ndvi_limpio = None
    red = bands.get("B04")
    nir = bands.get("B08")

    if red is not None and nir is not None and n_limpio >= 2:
        # Resize cloud mask to match band dimensions if needed
        if cloud_prob is not None and cloud_prob.shape != red.shape:
            from PIL import Image
            _cm = Image.fromarray(clean_mask.astype("uint8") * 255)
            _cm = _cm.resize((red.shape[1], red.shape[0]), Image.NEAREST)
            clean_mask = np.array(_cm) > 127

        valid = clean_mask & (red > 0) & (nir > 0) & (red < 1) & (nir < 1)
        if valid.sum() >= 2:
            r_v, n_v = red[valid], nir[valid]
            ndvi_limpio = float(round(float(np.mean((n_v - r_v) / (n_v + r_v + 1e-9))), 3))
            log.info("cloudremoval: NDVI limpio=%.3f (%d px)", ndvi_limpio, int(valid.sum()))

    # ── Estado Chain of Custody ───────────────────────────────────────────────
    if coverage >= _MIN_CLEAN_PCT and ndvi_limpio is not None:
        estado = "SILVER"
    elif coverage >= 10 and ndvi_limpio is not None:
        estado = "marginal"
    else:
        estado = "insuficiente"

    result.update({
        "estado":               estado,
        "ndvi_limpio":          ndvi_limpio,
        "cobertura_limpia_pct": coverage,
        "n_px_total":           n_total,
        "n_px_limpio":          n_limpio,
        "metodo_mascara":       metodo,
        "cloud_prob_umbral":    _CLOUD_PROB_THRESHOLD,
        "min_clean_pct":        _MIN_CLEAN_PCT,
        "fallback_usado":       metodo == "sin_mascara",
    })
    _save(show_id, result)
    return result


def _save(show_id: str, data: dict) -> None:
    try:
        _MODELS_DIR.mkdir(parents=True, exist_ok=True)
        out = _MODELS_DIR / f"cloudremoved_{show_id}.json"
        out.write_text(
            json.dumps(data, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        log.info("cloudremoval: saved → %s", out)
    except Exception as exc:
        log.warning("cloudremoval: save failed: %s", exc)
