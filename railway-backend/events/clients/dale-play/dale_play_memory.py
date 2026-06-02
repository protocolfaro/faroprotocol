"""
dale_play_memory.py — Memoria del sistema Faro Protocol.

Persiste el resultado de cada run del pipeline en show_memory (Supabase).
El endpoint /dale-play/memory expone los últimos N registros para análisis.

SQL requerido en Supabase SQL Editor:
  CREATE TABLE IF NOT EXISTS show_memory (
    id             SERIAL PRIMARY KEY,
    show_id        TEXT NOT NULL,
    show_date      TEXT,
    venue_id       TEXT DEFAULT 'amalfitani',
    sar_fuente     TEXT,
    sat_fuente     TEXT,
    weather_ok     BOOLEAN,
    ndvi_value     REAL,
    fii_value      REAL,
    coc_nivel      TEXT,
    modules_ok     JSONB,
    modules_failed JSONB,
    fuentes_usadas JSONB,
    duracion_s     REAL,
    datos_raw      JSONB,
    created_at     TIMESTAMPTZ DEFAULT NOW()
  );
  ALTER TABLE show_memory DISABLE ROW LEVEL SECURITY;
  CREATE INDEX IF NOT EXISTS show_memory_venue_ts ON show_memory (venue_id, created_at DESC);
"""
from __future__ import annotations

import logging
import os
from typing import Optional

import requests

log = logging.getLogger(__name__)


def _supa() -> tuple[str, str]:
    return os.environ.get("SUPABASE_URL", ""), os.environ.get("SUPABASE_KEY", "")


def _headers(key: str, prefer_minimal: bool = True) -> dict:
    h = {
        "apikey":        key,
        "Authorization": f"Bearer {key}",
        "Content-Type":  "application/json",
    }
    if prefer_minimal:
        h["Prefer"] = "return=minimal"
    else:
        h["Accept"] = "application/json"
    return h


# ─────────────────────────────────────────────────────────────────────────────
# Escritura
# ─────────────────────────────────────────────────────────────────────────────

def save_show_memory(
    show_id:        str,
    result:         dict,
    log_ejecutados: list,
    log_fallidos:   list,
    fuentes:        dict,
    duracion_s:     float = 0.0,
) -> bool:
    """
    Persiste un run del pipeline en show_memory.
    Llamar al final de run_show_audit() después de _guardar_run_log().

    Args:
        show_id:        identificador del show (ej: "dale_play_2025_06_15")
        result:         dict completo retornado por run_show_audit()
        log_ejecutados: lista de módulos OK con confianza y duración
        log_fallidos:   lista de módulos con error
        fuentes:        dict {module_key: fuente_usada}
        duracion_s:     duración total del pipeline
    """
    supa_url, supa_key = _supa()
    if not supa_url or not supa_key:
        log.warning("dale_play_memory: SUPABASE_URL/KEY no configurados")
        return False

    fii_dict = result.get("fii") or {}
    coc_dict = result.get("chain_of_custody") or {}
    sat_dict = result.get("satellite") or {}
    sar_dict = result.get("umbra_sar") or {}
    wea_dict = result.get("weather") or {}

    # Contexto histórico del campo (field_timeseries 6 años)
    ndvi_value  = sat_dict.get("ndvi")
    show_date   = result.get("show_date", "")
    ts_context  = _build_timeseries_context(ndvi_value, show_date)

    row = {
        "show_id":        show_id,
        "show_date":      show_date,
        "venue_id":       str(result.get("venue", "amalfitani")),
        "sar_fuente":     sar_dict.get("sar_fuente_usada") or sar_dict.get("fuente", ""),
        "sat_fuente":     sat_dict.get("fuente", ""),
        "weather_ok":     not bool(wea_dict.get("error")),
        "ndvi_value":     ndvi_value,
        "fii_value":      fii_dict.get("fii"),
        "coc_nivel":      coc_dict.get("nivel", "SIN_DATOS"),
        "modules_ok":     log_ejecutados,
        "modules_failed": log_fallidos,
        "fuentes_usadas": fuentes,
        "duracion_s":     duracion_s,
        "datos_raw": {
            "fii":                fii_dict,
            "coc":                coc_dict,
            "mode":               result.get("mode"),
            "timestamp":          result.get("timestamp"),
            "timeseries_context": ts_context,
        },
    }

    try:
        resp = requests.post(
            f"{supa_url}/rest/v1/show_memory",
            json=row,
            headers=_headers(supa_key),
            timeout=10,
        )
        if resp.status_code in (200, 201):
            log.info(
                "dale_play_memory: guardado show_id=%s fii=%.1f coc=%s",
                show_id, fii_dict.get("fii") or 0, coc_dict.get("nivel"),
            )
            return True
        log.warning("dale_play_memory: insert HTTP %s: %s", resp.status_code, resp.text[:300])
        return False
    except Exception as exc:
        log.warning("dale_play_memory: insert error: %s", exc)
        return False


# ─────────────────────────────────────────────────────────────────────────────
# Lectura
# ─────────────────────────────────────────────────────────────────────────────

def query_memory(venue_id: Optional[str] = None, limit: int = 20) -> list[dict]:
    """Retorna los últimos `limit` registros de show_memory, más recientes primero."""
    supa_url, supa_key = _supa()
    if not supa_url or not supa_key:
        return []
    try:
        params: dict = {"order": "created_at.desc", "limit": str(limit)}
        if venue_id:
            params["venue_id"] = f"eq.{venue_id}"
        resp = requests.get(
            f"{supa_url}/rest/v1/show_memory",
            params=params,
            headers=_headers(supa_key, prefer_minimal=False),
            timeout=10,
        )
        if resp.status_code == 200:
            return resp.json()
        log.warning("dale_play_memory: query HTTP %s: %s", resp.status_code, resp.text[:200])
        return []
    except Exception as exc:
        log.warning("dale_play_memory: query error: %s", exc)
        return []


def memory_summary(venue_id: Optional[str] = None, last_n: int = 20) -> dict:
    """
    Resumen estadístico de show_memory para el endpoint /dale-play/memory.
    """
    rows = query_memory(venue_id=venue_id, limit=last_n)
    if not rows:
        return {"entries": 0, "rows": [], "message": "Sin datos en show_memory"}

    fii_vals  = [r["fii_value"]  for r in rows if r.get("fii_value")  is not None]
    ndvi_vals = [r["ndvi_value"] for r in rows if r.get("ndvi_value") is not None]

    failed_counts: dict[str, int] = {}
    for r in rows:
        for fm in (r.get("modules_failed") or []):
            mod = fm.get("modulo", "?")
            failed_counts[mod] = failed_counts.get(mod, 0) + 1

    # SAR sources distribution
    sar_dist: dict[str, int] = {}
    for r in rows:
        s = r.get("sar_fuente") or "N/D"
        sar_dist[s] = sar_dist.get(s, 0) + 1

    return {
        "entries":         len(rows),
        "fii_promedio":    round(sum(fii_vals) / len(fii_vals), 1) if fii_vals else None,
        "fii_min":         round(min(fii_vals), 1) if fii_vals else None,
        "fii_max":         round(max(fii_vals), 1) if fii_vals else None,
        "ndvi_promedio":   round(sum(ndvi_vals) / len(ndvi_vals), 3) if ndvi_vals else None,
        "coc_distribution":           _count_field(rows, "coc_nivel"),
        "sar_fuente_distribution":    sar_dist,
        "modulos_fallidos_frecuentes": failed_counts,
        "rows": [
            {
                "show_id":        r.get("show_id"),
                "show_date":      r.get("show_date"),
                "fii_value":      r.get("fii_value"),
                "ndvi_value":     r.get("ndvi_value"),
                "coc_nivel":      r.get("coc_nivel"),
                "sar_fuente":     r.get("sar_fuente"),
                "sat_fuente":     r.get("sat_fuente"),
                "weather_ok":     r.get("weather_ok"),
                "n_failed":       len(r.get("modules_failed") or []),
                "duracion_s":     r.get("duracion_s"),
                "created_at":     r.get("created_at"),
            }
            for r in rows
        ],
    }


def _count_field(rows: list[dict], field: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for r in rows:
        v = str(r.get(field) or "N/D")
        counts[v] = counts.get(v, 0) + 1
    return counts


def _build_timeseries_context(ndvi: Optional[float], show_date: str) -> dict:
    """
    Construye contexto histórico del campo usando field_timeseries (76 puntos).
    Incluye: baseline estacional, percentil, recovery estimate.
    """
    try:
        from dale_play_timeseries_baseline import (
            get_timeseries_stats,
            get_seasonal_context,
            get_ndvi_percentile,
            get_recovery_estimate,
        )

        month = int(show_date[5:7]) if show_date and len(show_date) >= 7 else 0
        if not month:
            from datetime import date
            month = date.today().month

        stats      = get_timeseries_stats()
        seasonal   = get_seasonal_context(month) if month else {}
        percentil  = get_ndvi_percentile(ndvi) if ndvi is not None else None
        recovery   = get_recovery_estimate(ndvi, month) if ndvi is not None and month else {}

        return {
            "n_puntos_historicos": stats.get("n", 0),
            "rango_fechas":        stats.get("rango_fechas", ""),
            "ndvi_historico_mean": stats.get("ndvi_mean"),
            "ndvi_historico_std":  stats.get("ndvi_std"),
            "ndvi_mes_mean":       seasonal.get("mean"),
            "ndvi_mes_std":        seasonal.get("std"),
            "ndvi_mes_n":          seasonal.get("n"),
            "ndvi_percentil":      percentil,
            "recovery":            recovery,
        }
    except Exception as exc:
        log.debug("dale_play_memory: timeseries_context: %s", exc)
        return {}
