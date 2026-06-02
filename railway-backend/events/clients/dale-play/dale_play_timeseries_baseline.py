"""
dale_play_timeseries_baseline.py — Baseline histórico del campo (6 años).

Lee los 76 puntos de field_timeseries (Supabase) y provee:
  - Estadísticas globales: mean, std, min, max, varianza de scores FII
  - Modelo estacional: mean/std NDVI por mes del año
  - Percentil: dónde cae un NDVI en el historial completo
  - Recovery estimate: cuántas semanas para volver al baseline estacional
  - NDVI scores (0-100) de los 76 puntos → para calibración de pesos FII

Caché en memoria: 24h (la timeseries se actualiza semanalmente).
Tabla: field_timeseries — columnas: id, venue_id, fecha, ndvi, nubosidad, created_at
"""
from __future__ import annotations

import logging
import os
import time
from datetime import datetime
from typing import Optional

import requests

log = logging.getLogger(__name__)

_CACHE: dict[str, dict] = {}
_CACHE_TS: dict[str, float] = {}
_CACHE_TTL_S = 86_400   # 24h


def _supa() -> tuple[str, str]:
    return os.environ.get("SUPABASE_URL", ""), os.environ.get("SUPABASE_KEY", "")


# ─────────────────────────────────────────────────────────────────────────────
# Carga de datos
# ─────────────────────────────────────────────────────────────────────────────

def load_timeseries(venue_id: str = "amalfitani", force: bool = False) -> list[dict]:
    """
    Retorna los puntos de field_timeseries ordenados por fecha ASC.
    Caché de 24h. Cada punto: {fecha, ndvi, nubosidad, ndvi_score, month}.
    """
    cache_key = f"ts_{venue_id}"
    if not force and cache_key in _CACHE and time.time() - _CACHE_TS.get(cache_key, 0) < _CACHE_TTL_S:
        return _CACHE[cache_key]["rows"]

    supa_url, supa_key = _supa()
    if not supa_url or not supa_key:
        return []

    try:
        resp = requests.get(
            f"{supa_url}/rest/v1/field_timeseries",
            params={
                "venue_id": f"eq.{venue_id}",
                "order":    "fecha.asc",
                "limit":    "200",
            },
            headers={
                "apikey":        supa_key,
                "Authorization": f"Bearer {supa_key}",
                "Accept":        "application/json",
            },
            timeout=10,
        )
        if resp.status_code != 200:
            log.warning("dale_play_timeseries_baseline: HTTP %s", resp.status_code)
            return []

        raw = resp.json()
        rows = []
        for r in raw:
            ndvi  = r.get("ndvi")
            fecha = r.get("fecha", "")
            if ndvi is None or not fecha:
                continue
            month = int(fecha[5:7]) if len(fecha) >= 7 else 0
            rows.append({
                "fecha":      fecha,
                "ndvi":       float(ndvi),
                "nubosidad":  float(r.get("nubosidad") or 0),
                "month":      month,
                "ndvi_score": _ndvi_to_score(float(ndvi), month),
            })

        _CACHE[cache_key] = {"rows": rows}
        _CACHE_TS[cache_key] = time.time()
        log.info("dale_play_timeseries_baseline: cargados %d puntos para %s", len(rows), venue_id)
        return rows

    except Exception as exc:
        log.warning("dale_play_timeseries_baseline: load error: %s", exc)
        return []


def _ndvi_to_score(ndvi: float, month: int) -> float:
    """
    Convierte NDVI crudo a score FII (0-100) usando los umbrales absolutos.
    Incluye corrección por dormancia estacional (meses 4-8 en BsAs).
    """
    dormancia = (0.08 <= ndvi <= 0.25 and 4 <= month <= 8)
    if dormancia:
        return 35.0 + (ndvi - 0.08) / (0.25 - 0.08) * 15.0
    elif ndvi >= 0.45:
        return 100.0
    elif ndvi >= 0.30:
        return 70.0 + (ndvi - 0.30) / 0.15 * 30.0
    elif ndvi >= 0.20:
        return 40.0 + (ndvi - 0.20) / 0.10 * 30.0
    elif ndvi >= 0.10:
        return 10.0 + (ndvi - 0.10) / 0.10 * 30.0
    else:
        return max(0.0, ndvi / 0.10 * 10.0)


# ─────────────────────────────────────────────────────────────────────────────
# Estadísticas globales
# ─────────────────────────────────────────────────────────────────────────────

def get_timeseries_stats(venue_id: str = "amalfitani") -> dict:
    """
    Estadísticas globales del timeseries.
    Retorna: {n, ndvi_mean, ndvi_std, ndvi_min, ndvi_max,
              ndvi_score_mean, ndvi_score_variance, seasonal, rango_fechas}
    """
    rows = load_timeseries(venue_id)
    if not rows:
        return {}

    ndvi_vals  = [r["ndvi"] for r in rows]
    score_vals = [r["ndvi_score"] for r in rows]

    # Modelo estacional: mean/std por mes
    seasonal: dict[int, dict] = {}
    for m in range(1, 13):
        month_ndvis = [r["ndvi"] for r in rows if r["month"] == m]
        if month_ndvis:
            mean = sum(month_ndvis) / len(month_ndvis)
            std  = _std(month_ndvis)
            seasonal[m] = {"mean": round(mean, 4), "std": round(std, 4), "n": len(month_ndvis)}
        else:
            seasonal[m] = {"mean": None, "std": None, "n": 0}

    return {
        "n":                   len(rows),
        "ndvi_mean":           round(sum(ndvi_vals) / len(ndvi_vals), 4),
        "ndvi_std":            round(_std(ndvi_vals), 4),
        "ndvi_min":            round(min(ndvi_vals), 4),
        "ndvi_max":            round(max(ndvi_vals), 4),
        "ndvi_score_mean":     round(sum(score_vals) / len(score_vals), 2),
        "ndvi_score_variance": round(_variance(score_vals), 2),
        "seasonal":            seasonal,
        "rango_fechas":        f"{rows[0]['fecha']} → {rows[-1]['fecha']}",
    }


# ─────────────────────────────────────────────────────────────────────────────
# Contexto estacional
# ─────────────────────────────────────────────────────────────────────────────

def get_seasonal_context(month: int, venue_id: str = "amalfitani") -> dict:
    """
    Estadísticas NDVI para un mes específico del año.
    Retorna: {mean, std, n, score_range_expected}
    """
    rows = load_timeseries(venue_id)
    if not rows:
        return {}

    month_data = [r for r in rows if r["month"] == month]
    # Usar también meses adyacentes si hay pocos datos
    if len(month_data) < 3:
        adj_months = {month % 12 + 1, (month - 2) % 12 + 1}
        month_data = [r for r in rows if r["month"] in adj_months | {month}]

    if not month_data:
        return {}

    ndvis = [r["ndvi"] for r in month_data]
    mean  = sum(ndvis) / len(ndvis)
    std   = max(_std(ndvis), 0.005)

    return {
        "mean":               round(mean, 4),
        "std":                round(std, 4),
        "n":                  len(month_data),
        "ndvi_min_historico": round(min(ndvis), 4),
        "ndvi_max_historico": round(max(ndvis), 4),
        "score_mean":         round(_ndvi_to_score(mean, month), 1),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Scoring NDVI vs baseline estacional
# ─────────────────────────────────────────────────────────────────────────────

def score_ndvi_vs_seasonal(ndvi: float, month: int, venue_id: str = "amalfitani") -> tuple[float, str]:
    """
    Score NDVI (0-100) relativo al baseline estacional de 6 años.

    50 = en la media histórica del mes.
    75 = +1σ sobre la media (buen estado).
    25 = -1σ bajo la media (estado deteriorado).
    100 / 0 = ±2σ extremos.

    Retorna (score, label). Si no hay datos, retorna (None, None).
    """
    ctx = get_seasonal_context(month, venue_id)
    if not ctx or ctx.get("n", 0) < 2:
        return None, None

    mean = ctx["mean"]
    std  = max(ctx["std"], 0.005)

    z     = (ndvi - mean) / std
    score = max(0.0, min(100.0, 50.0 + z * 25.0))

    if ndvi >= mean + std:
        label = f"Sobre baseline {month_name(month)} ({mean:.3f} + {std:.3f})"
    elif ndvi >= mean - std:
        label = f"En rango histórico {month_name(month)} ({mean:.3f} ± {std:.3f})"
    else:
        label = f"Bajo baseline {month_name(month)} ({mean:.3f} - {std:.3f})"

    return round(score, 1), label


def month_name(m: int) -> str:
    names = ["","Ene","Feb","Mar","Abr","May","Jun","Jul","Ago","Sep","Oct","Nov","Dic"]
    return names[m] if 1 <= m <= 12 else str(m)


# ─────────────────────────────────────────────────────────────────────────────
# Percentil en distribución histórica
# ─────────────────────────────────────────────────────────────────────────────

def get_ndvi_percentile(ndvi: float, venue_id: str = "amalfitani") -> Optional[float]:
    """
    Dónde cae este NDVI en la distribución histórica completa (0-100).
    50 = mediana histórica.
    """
    rows = load_timeseries(venue_id)
    if not rows:
        return None
    all_ndvi = sorted(r["ndvi"] for r in rows)
    below = sum(1 for x in all_ndvi if x <= ndvi)
    return round(below / len(all_ndvi) * 100, 1)


# ─────────────────────────────────────────────────────────────────────────────
# Recovery model
# ─────────────────────────────────────────────────────────────────────────────

def get_recovery_estimate(
    current_ndvi: float,
    month:        int,
    venue_id:     str = "amalfitani",
) -> dict:
    """
    Estima semanas de recuperación desde current_ndvi hasta la media estacional.

    Tasa de recovery: promedio de incrementos mensuales positivos en el historial.
    Target: media del mes actual + (siguiente mes de pico según estacional).
    """
    rows = load_timeseries(venue_id)
    if len(rows) < 6:
        return {}

    ctx = get_seasonal_context(month, venue_id)
    if not ctx:
        return {}

    target_ndvi = ctx["mean"]

    if current_ndvi >= target_ndvi:
        return {
            "recovery_semanas":   0,
            "recovery_target":    round(target_ndvi, 3),
            "estado":             "sobre_baseline",
            "current_ndvi":       round(current_ndvi, 3),
        }

    # Tasa de recovery: promedio de incrementos positivos mes a mes
    increments = []
    for i in range(1, len(rows)):
        delta = rows[i]["ndvi"] - rows[i-1]["ndvi"]
        if delta > 0:
            # Días entre observaciones
            d0 = _parse_date(rows[i-1]["fecha"])
            d1 = _parse_date(rows[i]["fecha"])
            days = (d1 - d0).days if d0 and d1 else 30
            if 10 < days < 100:   # descartar gaps muy grandes o muy cortos
                increments.append(delta / (days / 7.0))   # NDVI por semana

    if not increments:
        return {}

    ndvi_por_semana = sum(increments) / len(increments)
    ndvi_gap = target_ndvi - current_ndvi
    semanas  = max(1, round(ndvi_gap / max(ndvi_por_semana, 0.001)))

    return {
        "recovery_semanas":         semanas,
        "recovery_target":          round(target_ndvi, 3),
        "ndvi_gap":                 round(ndvi_gap, 3),
        "ndvi_por_semana_historico": round(ndvi_por_semana, 4),
        "estado":                   "bajo_baseline" if ndvi_gap > ctx["std"] else "en_rango",
        "current_ndvi":             round(current_ndvi, 3),
    }


def _parse_date(s: str):
    try:
        return datetime.strptime(s[:10], "%Y-%m-%d")
    except Exception:
        return None


# ─────────────────────────────────────────────────────────────────────────────
# Utils estadísticos
# ─────────────────────────────────────────────────────────────────────────────

def _variance(xs: list[float]) -> float:
    if len(xs) < 2:
        return 0.0
    mean = sum(xs) / len(xs)
    return sum((x - mean) ** 2 for x in xs) / len(xs)


def _std(xs: list[float]) -> float:
    return _variance(xs) ** 0.5
