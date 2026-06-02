"""
dale_play_fii_calibration.py — Auto-calibración de pesos FII.

Después de cada 3 shows nuevos en show_memory, recalcula los pesos
(w_ndvi / w_egms / w_layout) usando varianza de componentes:
  - Componente más variable = más discriminativo = más peso.
  - Cada peso se clampea a [0.10, 0.60] para evitar degeneración.
  - Pesos se normalizan a suma 1.0 y se guardan en fii_weights (Supabase).

pipeline.py llama check_and_recalibrate() después de save_show_memory().
compute_fii() lee los pesos con get_fii_weights() (caché 10 min).

SQL requerido en Supabase SQL Editor:
  CREATE TABLE IF NOT EXISTS fii_weights (
    id                  SERIAL PRIMARY KEY,
    w_ndvi              REAL NOT NULL DEFAULT 0.40,
    w_egms              REAL NOT NULL DEFAULT 0.35,
    w_layout            REAL NOT NULL DEFAULT 0.25,
    n_shows_calibrado   INTEGER DEFAULT 0,
    calibrado_at        TIMESTAMPTZ DEFAULT NOW(),
    motivo              TEXT,
    datos_raw           JSONB
  );
  ALTER TABLE fii_weights DISABLE ROW LEVEL SECURITY;
  INSERT INTO fii_weights (w_ndvi, w_egms, w_layout, motivo)
    VALUES (0.40, 0.35, 0.25, 'pesos_iniciales')
    ON CONFLICT DO NOTHING;
"""
from __future__ import annotations

import logging
import os
import time
from typing import Optional

import requests

log = logging.getLogger(__name__)

_DEFAULTS = {"ndvi": 0.40, "egms": 0.35, "layout": 0.25}
_MIN_SHOWS_FOR_CALIBRATION = 3   # shows mínimos en show_memory para correr regresión
_MIN_SHOWS_TOTAL           = 5   # total mínimo en tabla para que la calibración sea confiable

# Caché en memoria para no consultar Supabase en cada compute_fii()
_weights_cache: dict = {}
_cache_ts: float = 0.0
_CACHE_TTL_S = 600   # 10 minutos


def _supa() -> tuple[str, str]:
    return os.environ.get("SUPABASE_URL", ""), os.environ.get("SUPABASE_KEY", "")


def _headers(key: str, prefer_minimal: bool = False) -> dict:
    h = {
        "apikey":        key,
        "Authorization": f"Bearer {key}",
        "Content-Type":  "application/json",
    }
    h["Prefer" if prefer_minimal else "Accept"] = ("return=minimal" if prefer_minimal else "application/json")
    return h


# ─────────────────────────────────────────────────────────────────────────────
# API pública
# ─────────────────────────────────────────────────────────────────────────────

def get_fii_weights() -> dict:
    """
    Retorna pesos FII actuales: {"ndvi": float, "egms": float, "layout": float}.
    Lee de Supabase con caché de 10 min. Fallback silencioso a defaults.
    """
    global _weights_cache, _cache_ts
    if _weights_cache and time.time() - _cache_ts < _CACHE_TTL_S:
        return _weights_cache

    supa_url, supa_key = _supa()
    if not supa_url or not supa_key:
        return _DEFAULTS.copy()

    try:
        resp = requests.get(
            f"{supa_url}/rest/v1/fii_weights",
            params={"order": "calibrado_at.desc", "limit": "1"},
            headers=_headers(supa_key),
            timeout=5,
        )
        if resp.status_code == 200:
            rows = resp.json()
            if rows:
                r = rows[0]
                w = {
                    "ndvi":   float(r.get("w_ndvi", _DEFAULTS["ndvi"])),
                    "egms":   float(r.get("w_egms", _DEFAULTS["egms"])),
                    "layout": float(r.get("w_layout", _DEFAULTS["layout"])),
                }
                _weights_cache = w
                _cache_ts = time.time()
                return w
    except Exception as exc:
        log.debug("dale_play_fii_calibration: get_fii_weights: %s", exc)

    return _DEFAULTS.copy()


def check_and_recalibrate() -> dict:
    """
    Verifica si hay ≥ _MIN_SHOWS_FOR_CALIBRATION shows nuevos desde la última
    calibración. Si sí, recalibra y guarda nuevos pesos.

    Retorna {"recalibrado": bool, "motivo": str, "pesos": dict}
    """
    supa_url, supa_key = _supa()
    if not supa_url or not supa_key:
        return {"recalibrado": False, "motivo": "supabase no configurado"}

    # Fecha de última calibración
    try:
        resp = requests.get(
            f"{supa_url}/rest/v1/fii_weights",
            params={"order": "calibrado_at.desc", "limit": "1"},
            headers=_headers(supa_key),
            timeout=5,
        )
        last_calibrated_at = None
        n_shows_last = 0
        if resp.status_code == 200 and resp.json():
            row = resp.json()[0]
            last_calibrated_at = row.get("calibrado_at", "")
            n_shows_last = row.get("n_shows_calibrado", 0)
    except Exception as exc:
        log.warning("dale_play_fii_calibration: check: %s", exc)
        return {"recalibrado": False, "motivo": str(exc)}

    # Contar shows en show_memory desde la última calibración
    try:
        params: dict = {"select": "id", "limit": "100", "order": "created_at.desc"}
        if last_calibrated_at:
            params["created_at"] = f"gt.{last_calibrated_at}"
        resp2 = requests.get(
            f"{supa_url}/rest/v1/show_memory",
            params=params,
            headers=_headers(supa_key),
            timeout=5,
        )
        if resp2.status_code != 200:
            return {"recalibrado": False, "motivo": f"show_memory HTTP {resp2.status_code}"}
        n_nuevos = len(resp2.json())
    except Exception as exc:
        log.warning("dale_play_fii_calibration: count shows: %s", exc)
        return {"recalibrado": False, "motivo": str(exc)}

    # Con timeseries disponible (76 pts) podemos calibrar NDVI weight inmediatamente.
    # Sin timeseries, necesitamos suficientes shows en show_memory.
    ts_disponible = False
    try:
        from dale_play_timeseries_baseline import get_timeseries_stats
        ts_disponible = (get_timeseries_stats().get("n", 0) >= 10)
    except Exception:
        pass

    if n_nuevos < _MIN_SHOWS_FOR_CALIBRATION and not ts_disponible:
        log.debug(
            "dale_play_fii_calibration: %d shows nuevos < %d mínimos y sin timeseries — sin recalibración",
            n_nuevos, _MIN_SHOWS_FOR_CALIBRATION,
        )
        return {"recalibrado": False, "motivo": f"solo {n_nuevos} shows nuevos (mín {_MIN_SHOWS_FOR_CALIBRATION})"}

    log.info("dale_play_fii_calibration: %d shows nuevos → iniciando recalibración", n_nuevos)
    result = recalibrate_fii_weights()
    return result


def recalibrate_fii_weights(last_n: int = 20) -> dict:
    """
    Recalcula pesos FII combinando dos fuentes de datos:
      1. field_timeseries (76 puntos 2020-2026): varianza NDVI score — fuente primaria y confiable.
      2. show_memory (últimos N runs): varianza de scores EGMS y layout.

    La varianza de NDVI usa el historial completo del campo, no solo los shows auditados.
    Guarda resultado en fii_weights. Invalida caché.
    """
    global _weights_cache, _cache_ts

    supa_url, supa_key = _supa()
    if not supa_url or not supa_key:
        return {"recalibrado": False, "motivo": "supabase no configurado"}

    # ── 1. NDVI variance desde field_timeseries (76 puntos) ──────────────────
    v_ndvi_timeseries = 0.0
    n_timeseries      = 0
    try:
        from dale_play_timeseries_baseline import get_timeseries_stats
        ts = get_timeseries_stats()
        v_ndvi_timeseries = ts.get("ndvi_score_variance", 0.0)
        n_timeseries      = ts.get("n", 0)
        log.info(
            "dale_play_fii_calibration: timeseries NDVI score_variance=%.2f (n=%d)",
            v_ndvi_timeseries, n_timeseries,
        )
    except Exception as exc:
        log.warning("dale_play_fii_calibration: timeseries load: %s", exc)

    # ── 2. EGMS y layout variance desde show_memory ───────────────────────────
    egms_scores, layout_scores, ndvi_scores_memory = [], [], []
    n_shows_memory = 0
    try:
        resp = requests.get(
            f"{supa_url}/rest/v1/show_memory",
            params={
                "select":    "datos_raw,fii_value",
                "order":     "created_at.desc",
                "limit":     str(last_n),
                "fii_value": "not.is.null",
            },
            headers=_headers(supa_key),
            timeout=10,
        )
        if resp.status_code == 200:
            rows = resp.json()
            n_shows_memory = len(rows)
            for r in rows:
                datos_raw = r.get("datos_raw") or {}
                fii_d     = datos_raw.get("fii") or {}
                comps     = fii_d.get("componentes") or {}

                ns = (comps.get("ndvi") or {}).get("score")
                es = (comps.get("egms") or {}).get("score")
                ls = (comps.get("layout") or {}).get("score")

                if ns is not None: ndvi_scores_memory.append(float(ns))
                if es is not None: egms_scores.append(float(es))
                if ls is not None: layout_scores.append(float(ls))
    except Exception as exc:
        log.warning("dale_play_fii_calibration: show_memory: %s", exc)

    # ── 3. Elegir varianza NDVI: timeseries > show_memory ────────────────────
    # La timeseries tiene 76 puntos con distribución real del campo.
    # show_memory tiene pocos puntos al inicio — usarla solo si no hay timeseries.
    if n_timeseries >= 10:
        v_ndvi  = v_ndvi_timeseries
        src_ndvi = f"timeseries ({n_timeseries} pts)"
    elif ndvi_scores_memory:
        v_ndvi  = _variance(ndvi_scores_memory)
        src_ndvi = f"show_memory ({len(ndvi_scores_memory)} pts)"
    else:
        return {"recalibrado": False, "motivo": "sin datos NDVI (timeseries ni show_memory)"}

    v_egms   = _variance(egms_scores)   if egms_scores   else 0.0
    v_layout = _variance(layout_scores) if layout_scores else 0.0
    total_v  = v_ndvi + v_egms + v_layout

    if total_v < 1e-6:
        new_w  = _DEFAULTS.copy()
        motivo = "varianza total nula — manteniendo pesos default"
    else:
        def clamp(x: float) -> float:
            return max(0.10, min(0.60, x))

        w_ndvi   = clamp(v_ndvi / total_v)
        w_egms   = clamp(v_egms / total_v)
        w_layout = clamp(v_layout / total_v)
        total_w  = w_ndvi + w_egms + w_layout

        new_w = {
            "ndvi":   round(w_ndvi / total_w, 3),
            "egms":   round(w_egms / total_w, 3),
            "layout": round(w_layout / total_w, 3),
        }
        motivo = (
            f"ndvi_var={v_ndvi:.1f} ({src_ndvi}) "
            f"egms_var={v_egms:.1f} (show_memory {len(egms_scores)}pts) "
            f"layout_var={v_layout:.1f} (show_memory {len(layout_scores)}pts)"
        )

    log.info(
        "dale_play_fii_calibration: nuevos pesos ndvi=%.3f egms=%.3f layout=%.3f | %s",
        new_w["ndvi"], new_w["egms"], new_w["layout"], motivo,
    )

    # ── 4. Guardar en Supabase ────────────────────────────────────────────────
    row = {
        "w_ndvi":            new_w["ndvi"],
        "w_egms":            new_w["egms"],
        "w_layout":          new_w["layout"],
        "n_shows_calibrado": n_shows_memory,
        "motivo":            motivo,
        "datos_raw": {
            "varianzas": {
                "ndvi":   round(v_ndvi, 2),
                "egms":   round(v_egms, 2),
                "layout": round(v_layout, 2),
            },
            "fuentes": {
                "ndvi":   src_ndvi if total_v >= 1e-6 else "default",
                "egms":   f"show_memory ({len(egms_scores)}pts)",
                "layout": f"show_memory ({len(layout_scores)}pts)",
            },
            "n_timeseries": n_timeseries,
            "n_shows":      n_shows_memory,
            "pesos_anteriores": get_fii_weights(),
        },
    }

    try:
        resp2 = requests.post(
            f"{supa_url}/rest/v1/fii_weights",
            json=row,
            headers={**_headers(supa_key), "Prefer": "return=minimal"},
            timeout=10,
        )
        saved = resp2.status_code in (200, 201)
    except Exception as exc:
        log.warning("dale_play_fii_calibration: save weights: %s", exc)
        saved = False

    _weights_cache = {}
    _cache_ts      = 0.0

    return {
        "recalibrado":  saved,
        "pesos":        new_w,
        "varianzas":    {"ndvi": round(v_ndvi, 2), "egms": round(v_egms, 2), "layout": round(v_layout, 2)},
        "n_timeseries": n_timeseries,
        "n_shows":      n_shows_memory,
        "motivo":       motivo,
    }


def _variance(xs: list[float]) -> float:
    if len(xs) < 2:
        return 1.0   # neutral — sin datos suficientes asumimos que el componente discrimina
    mean = sum(xs) / len(xs)
    return sum((x - mean) ** 2 for x in xs) / len(xs)
