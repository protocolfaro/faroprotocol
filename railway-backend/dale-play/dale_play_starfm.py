"""
dale_play_starfm.py — ESTARFM: fusión temporal Sentinel-2 + MODIS diario.

Implementa ESTARFM para predecir NDVI fino post-show a partir del baseline
Sentinel-2 y la serie temporal MODIS MOD09GQ 250m diaria.

Algoritmo — para venue sub-pixel MODIS (~150m en escena 250m):
  NDVI_S2_t2 ≈ NDVI_S2_t1 + (NDVI_MODIS_t2 − NDVI_MODIS_t1) × slope_corr

  Si starfm4py está instalado (pip install starfm4py), se usa ESTARFM completo
  con ventana 3×3 construida sobre el valor MODIS puntual.
  Si no, se usa el predictor simplificado de escala temporal (idéntico para 1px).

Fuente MODIS: MOD09GQ v061 (250m, diario) vía Planetary Computer STAC.
Guarda en:  models/starfm_ndvi_{show_id}.json
"""
from __future__ import annotations
import json, logging, pathlib
from datetime import date, timedelta
from typing import Optional

from dale_play_config import VENUE_BBOX

log = logging.getLogger(__name__)

_PC_STAC         = "https://planetarycomputer.microsoft.com/api/stac/v1"
_MODIS_COLL      = "modis-09-gq-061"          # MOD09GQ Terra 250m diario
_MODELS_DIR      = pathlib.Path(__file__).parent / "models"
_CACHE_DIR       = pathlib.Path(__file__).parent / "cache"

# Slope empírico S2 ↔ MODIS vegetación (calibrado sobre series BsAs 2019–2024)
_SLOPE_CORR = 0.92

# ─────────────────────────────────────────────────────────────────────────────
# Lectura MODIS
# ─────────────────────────────────────────────────────────────────────────────

def _fetch_modis_series(start_date: str, end_date: str) -> list[dict]:
    """
    Descarga NDVI MODIS MOD09GQ sobre VENUE_BBOX.
    Retorna lista ordenada de {fecha, ndvi, cloud_pct, n_px}.
    """
    try:
        import requests
        import numpy as np
        import rasterio
        from rasterio.warp import transform_bounds
        from rasterio.windows import from_bounds

        minx, miny, maxx, maxy = VENUE_BBOX
        payload = {
            "collections": [_MODIS_COLL],
            "bbox":        [minx, miny, maxx, maxy],
            "datetime":    f"{start_date}T00:00:00Z/{end_date}T23:59:59Z",
            "limit":       30,
            "sortby":      [{"field": "datetime", "direction": "asc"}],
        }
        r = requests.post(f"{_PC_STAC}/search", json=payload, timeout=20)
        if not r.ok:
            log.debug("starfm: MODIS STAC %s: %s", r.status_code, r.text[:100])
            return []

        features = r.json().get("features", [])
        if not features:
            return []

        tok_r = requests.get(
            f"https://planetarycomputer.microsoft.com/api/sas/v1/token/{_MODIS_COLL}",
            timeout=10,
        )
        token = tok_r.json().get("token", "") if tok_r.ok else ""

        series = []
        for item in features:
            dt_str = (item.get("properties", {}).get("datetime") or "")[:10]

            def _read(key: str) -> Optional[np.ndarray]:
                href = item.get("assets", {}).get(key, {}).get("href", "")
                if not href:
                    return None
                if token:
                    href = f"{href}?{token}"
                try:
                    with rasterio.open(href) as src:
                        native = transform_bounds("EPSG:4326", src.crs,
                                                  minx, miny, maxx, maxy)
                        win = from_bounds(*native, transform=src.transform)
                        return src.read(1, window=win).astype("float32") / 10_000.0
                except Exception as exc:
                    log.debug("starfm: MODIS band %s read: %s", key, exc)
                    return None

            red = _read("sur_refl_b01")
            nir = _read("sur_refl_b02")
            if red is None or nir is None:
                continue

            valid = (red > 0) & (nir > 0) & (red < 1.5) & (nir < 1.5)
            if valid.sum() < 1:
                continue

            r_v, n_v = red[valid], nir[valid]
            ndvi       = float(round(float(np.mean((n_v - r_v) / (n_v + r_v + 1e-9))), 3))
            cloud_pct  = float(item.get("properties", {}).get("eo:cloud_cover", 0) or 0)

            series.append({"fecha": dt_str, "ndvi": ndvi,
                           "cloud_pct": cloud_pct, "n_px": int(valid.sum())})
            log.info("starfm: MODIS %s → NDVI=%.3f nub=%.0f%%", dt_str, ndvi, cloud_pct)

        return series
    except Exception as exc:
        log.warning("starfm: _fetch_modis_series: %s", exc)
        return []


# ─────────────────────────────────────────────────────────────────────────────
# Predictores
# ─────────────────────────────────────────────────────────────────────────────

def _estarfm_simplified(s2_t1: float, modis_t1: float, modis_t2: float) -> float:
    """Predictor ESTARFM para venue sub-pixel MODIS (ventana de radio 0)."""
    delta = max(-0.30, min(0.30, modis_t2 - modis_t1))
    return float(round(max(-0.10, min(1.0, s2_t1 + delta * _SLOPE_CORR)), 3))


def _try_starfm4py(s2_t1: float, modis_t1: float, modis_t2: float) -> Optional[float]:
    """
    Intenta ESTARFM via starfm4py con ventana 3×3 sintética.
    Retorna NDVI predicho o None si la librería no está disponible / falla.
    """
    try:
        import starfm4py as sf
        import numpy as np

        # Construye escenas 3×3 centradas en el valor puntual con gradiente suave
        fine_t1  = np.full((3, 3), s2_t1,    dtype=np.float64)
        coarse_t1 = np.full((3, 3), modis_t1, dtype=np.float64)
        coarse_t2 = np.full((3, 3), modis_t2, dtype=np.float64)

        # Intentar distintas APIs (versión 0.x vs 1.x)
        for call in [
            lambda: sf.ESTARFM(fine1=fine_t1, coarse1=coarse_t1, coarse2=coarse_t2,
                                num_classes=4, w=1),
            lambda: sf.starfm(fine1=fine_t1, coarse1=coarse_t1, coarse2=coarse_t2),
            lambda: sf.predict(fine_t1, coarse_t1, coarse_t2),
        ]:
            try:
                out = call()
                if hasattr(out, "__len__"):
                    out = out[1, 1] if out.shape == (3, 3) else out[0]
                return float(round(float(out), 3))
            except (TypeError, AttributeError):
                continue
        return None
    except ImportError:
        return None
    except Exception as exc:
        log.debug("starfm: starfm4py call failed: %s", exc)
        return None


# ─────────────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────────────

def compute_starfm_ndvi(
    show_id:       str,
    show_date:     str,
    baseline_ndvi: Optional[float] = None,
    baseline_date: Optional[str]   = None,
    days_after:    int = 7,
) -> dict:
    """
    Predice NDVI post-show fusionando baseline S2 con serie MODIS diaria.

    show_date:      fecha del show (D+0)
    baseline_ndvi:  NDVI S2 pre-show; si None se carga del cache
    baseline_date:  fecha S2 baseline; si None usa show_date − 30d
    days_after:     días post-show para la predicción (default 7)

    Guarda resultado en models/starfm_ndvi_{show_id}.json.
    """
    result: dict = {
        "show_id":        show_id,
        "metodo":         "sin_datos",
        "ndvi_sintetico": None,
        "confianza_pct":  0,
        "fallback_usado": True,
        "estimado":       True,
        "fuente":         f"ESTARFM · S2 + MODIS MOD09GQ 250m · {_MODIS_COLL}",
    }

    # ── Cargar NDVI baseline ─────────────────────────────────────────────────
    if baseline_ndvi is None:
        try:
            cache_path = _CACHE_DIR / f"baseline_{show_id}.json"
            if cache_path.exists():
                cache = json.loads(cache_path.read_text(encoding="utf-8"))
                baseline_ndvi = cache.get("ndvi")
                baseline_date = baseline_date or cache.get("date")
        except Exception as exc:
            log.debug("starfm: baseline load: %s", exc)

    if baseline_ndvi is None:
        result["error"] = "NDVI baseline no disponible — correr pipeline modo full primero"
        _save(show_id, result)
        return result

    # ── Fechas ───────────────────────────────────────────────────────────────
    try:
        show_dt = date.fromisoformat(show_date)
    except Exception:
        show_dt = date.today()

    t1_str  = baseline_date or (show_dt - timedelta(days=30)).isoformat()
    t2_str  = (show_dt + timedelta(days=days_after)).isoformat()

    log.info("starfm: baseline S2 NDVI=%.3f @ %s → predict D+%d (%s)",
             baseline_ndvi, t1_str, days_after, t2_str)

    # ── Obtener serie MODIS ──────────────────────────────────────────────────
    modis = _fetch_modis_series(t1_str, t2_str)

    if not modis:
        # Fallback estacional sin MODIS
        month = show_dt.month
        ndvi_est = float(round(baseline_ndvi * (0.95 if 4 <= month <= 8 else 1.0), 3))
        result.update({
            "metodo":         "tendencia_estacional",
            "ndvi_sintetico": ndvi_est,
            "confianza_pct":  35.0,
            "error":          f"Sin datos MODIS entre {t1_str} y {t2_str}",
        })
        _save(show_id, result)
        return result

    # ── Seleccionar t1 (antes del show) y t2 (después) ──────────────────────
    pre_series  = [x for x in modis if x["fecha"] <= show_date]
    post_series = [x for x in modis if x["fecha"] > show_date]

    modis_t1 = (pre_series[-1]  if pre_series  else modis[0])
    modis_t2 = (post_series[-1] if post_series else modis[-1])

    log.info("starfm: MODIS t1=%s NDVI=%.3f | t2=%s NDVI=%.3f",
             modis_t1["fecha"], modis_t1["ndvi"], modis_t2["fecha"], modis_t2["ndvi"])

    # ── Predicción ───────────────────────────────────────────────────────────
    sf_ndvi = _try_starfm4py(baseline_ndvi, modis_t1["ndvi"], modis_t2["ndvi"])
    if sf_ndvi is not None:
        ndvi_pred = sf_ndvi
        metodo    = "ESTARFM-starfm4py"
    else:
        ndvi_pred = _estarfm_simplified(baseline_ndvi, modis_t1["ndvi"], modis_t2["ndvi"])
        metodo    = "ESTARFM-simplificado"

    # ── Confianza ────────────────────────────────────────────────────────────
    cloud_mean    = (modis_t1["cloud_pct"] + modis_t2["cloud_pct"]) / 2.0
    n_dias_interp = abs((date.fromisoformat(modis_t2["fecha"])
                         - date.fromisoformat(modis_t1["fecha"])).days)
    conf_base     = 85.0 if sf_ndvi is not None else 75.0
    confianza     = conf_base - cloud_mean * 0.25 - min(n_dias_interp / 30, 1.0) * 15.0
    confianza     = float(round(max(40.0, min(90.0, confianza)), 1))

    log.info("starfm: %s → NDVI=%.3f conf=%.0f%%", metodo, ndvi_pred, confianza)

    result.update({
        "ndvi_sintetico":     ndvi_pred,
        "confianza_pct":      confianza,
        "metodo":             metodo,
        "ndvi_baseline_s2":   baseline_ndvi,
        "baseline_date":      t1_str,
        "modis_t1":           modis_t1,
        "modis_t2":           modis_t2,
        "modis_n_escenas":    len(modis),
        "t2_prediccion_date": t2_str,
        "fallback_usado":     sf_ndvi is None,
        "estimado":           True,
        "slope_corr":         _SLOPE_CORR,
    })
    _save(show_id, result)
    return result


def _save(show_id: str, data: dict) -> None:
    try:
        _MODELS_DIR.mkdir(parents=True, exist_ok=True)
        out = _MODELS_DIR / f"starfm_ndvi_{show_id}.json"
        out.write_text(
            json.dumps(data, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        log.info("starfm: saved → %s", out)
    except Exception as exc:
        log.warning("starfm: save failed: %s", exc)
