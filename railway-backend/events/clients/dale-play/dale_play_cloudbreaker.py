"""
dale_play_cloudbreaker.py — NDVI sintético SAR cuando no hay imagen óptica.

Se activa SOLO cuando (flujo secuencial):
  1. CLOSDI descartó la imagen por cobertura insuficiente (coverage_pct < 20%)
  2. No hay imagen disponible en el catálogo (fuente_tipo == "sin_dato")

Método: ajuste SAR sobre baseline fenológico doble-logístico (Amalfitani).
  1. SAR backscatter VV (dB) desde fetch_sar() — cascada Umbra→S1_RTC→Capella
  2. NDVI estacional esperado para la semana ISO actual desde fenologia_baselines
  3. Anomalía SAR: Δσ0 = backscatter_db − (−12 dB referencia turf gramado)
  4. Ajuste empírico: NDVI_syn = NDVI_baseline + 0.025 × Δσ0
     (C-band VV / grass, empírico: Gao et al. 2018, Dingle Robertson et al. 2020)
  5. Confianza: 0.60 máx (S1 RTC directo) | 0.45 (otros SAR) | 0.30 (solo estacional)

Salida: {"ndvi_sintetico": float, "confianza": float, "fuente": "CloudBreaker_SAR2S2", ...}
Label ESTIMADO en toda la interfaz — NUNCA incorporado en certificado legal.
Siempre falla silenciosa (ningún error propaga al caller).

SQL requerido (fenologia_baselines ya existe — ver field_timeseries.py):
  No requiere tablas adicionales.
"""
from __future__ import annotations

import logging
import os
from typing import Optional

import requests

log = logging.getLogger(__name__)

_VV_REF_DB        = -12.0   # σ0 VV de referencia para NDVI baseline (turf gramado BsAs)
_VV_TO_NDVI_SLOPE =  0.025  # ΔNDVI por dB (C-band VV, gramado, empírico)
_VV_CLIP_MIN      = -18.0   # límite inferior backscatter válido
_VV_CLIP_MAX      =  -6.0   # límite superior backscatter válido
_SUPA_TIMEOUT     =  10


def _supa() -> tuple[str, str]:
    return os.environ.get("SUPABASE_URL", ""), os.environ.get("SUPABASE_KEY", "")


def _load_seasonal_ndvi(venue_id: str = "amalfitani") -> Optional[float]:
    """Lee NDVI esperado para la semana ISO actual desde fenologia_baselines (Supabase)."""
    import datetime

    supa_url, supa_key = _supa()
    if not supa_url or not supa_key:
        return None

    try:
        r = requests.get(
            f"{supa_url}/rest/v1/fenologia_baselines",
            headers={
                "apikey":        supa_key,
                "Authorization": f"Bearer {supa_key}",
            },
            params={"venue_id": f"eq.{venue_id}", "select": "baseline"},
            timeout=_SUPA_TIMEOUT,
        )
        if not r.ok or not r.json():
            return None

        baseline  = r.json()[0].get("baseline") or {}
        semanas   = baseline.get("semanas") or {}
        week      = min(datetime.date.today().isocalendar()[1], 52)
        week_data = semanas.get(str(week))
        if week_data:
            return week_data.get("ndvi_smooth")
        return None

    except Exception as exc:
        log.debug("cloudbreaker: _load_seasonal_ndvi error: %s", exc)
        return None


def fetch_cloudbreaker_ndvi(
    show_date: str = "",
    show_id:   str = "",
    venue_id:  str = "amalfitani",
) -> Optional[dict]:
    """
    Genera NDVI sintético SAR cuando no hay imagen óptica disponible.

    No lanza excepciones. Retorna None si no hay baseline estacional (sin datos
    históricos) — en ese caso el certificado pasa directamente a bifásico pendiente.

    Campos de salida:
      ndvi_sintetico        → float [0, 1]
      confianza             → float [0.30, 0.65]
      fuente                → "CloudBreaker_SAR2S2"
      metodo                → str descriptivo del camino tomado
      sar_backscatter_db    → float | None
      ndvi_baseline_semana  → float (NDVI estacional de referencia)
      semana_iso            → int (semana ISO actual)
      estimado              → True (siempre — para detectar en UI/cert)
    """
    import datetime

    ndvi_baseline = _load_seasonal_ndvi(venue_id)
    current_week  = min(datetime.date.today().isocalendar()[1], 52)

    if ndvi_baseline is None:
        log.debug("cloudbreaker: sin baseline fenológico — no se puede estimar NDVI")
        return None

    # ── SAR backscatter desde cascada Umbra→S1_RTC→Capella ───────────────────
    sar_db:   Optional[float] = None
    sar_conf: float           = 0.0
    sar_tier: str             = ""

    try:
        from dale_play_sar import fetch_sar
        sar      = fetch_sar(show_id=show_id or "cloudbreaker")
        sar_db   = sar.get("backscatter_db_mean")
        sar_conf = sar.get("sar_nivel_confianza", 0.0)
        sar_tier = sar.get("sar_fuente_usada", "")
        log.info(
            "cloudbreaker: SAR tier=%s db=%s conf=%.2f",
            sar_tier,
            f"{sar_db:.1f}" if sar_db is not None else "None",
            sar_conf,
        )
    except Exception as exc:
        log.debug("cloudbreaker: fetch_sar error: %s", exc)

    # ── Calcular NDVI sintético ───────────────────────────────────────────────
    if sar_db is not None:
        sar_db_clipped = max(_VV_CLIP_MIN, min(_VV_CLIP_MAX, sar_db))
        delta_sar      = sar_db_clipped - _VV_REF_DB
        ndvi_syn       = round(max(0.0, min(1.0,
                             ndvi_baseline + _VV_TO_NDVI_SLOPE * delta_sar)), 3)

        # Confianza máx 0.65 — la conversión SAR→NDVI es siempre estimación
        if sar_tier in ("S1_RTC_PC", "UMBRA_X"):
            confianza = round(min(0.65, sar_conf * 0.70), 2)
        else:
            confianza = round(min(0.45, sar_conf * 0.55), 2)

        metodo = f"SAR_ajuste_estacional ({sar_tier})"
    else:
        ndvi_syn  = round(ndvi_baseline, 3)
        confianza = 0.30
        metodo    = "solo_baseline_estacional"
        log.info("cloudbreaker: sin backscatter SAR — usando sólo baseline estacional")

    result = {
        "ndvi_sintetico":       ndvi_syn,
        "confianza":            confianza,
        "fuente":               "CloudBreaker_SAR2S2",
        "metodo":               metodo,
        "sar_backscatter_db":   round(sar_db, 2) if sar_db is not None else None,
        "ndvi_baseline_semana": ndvi_baseline,
        "semana_iso":           current_week,
        "estimado":             True,
    }

    log.info(
        "cloudbreaker: NDVI_syn=%.3f confianza=%.2f metodo=%s semana=%d",
        ndvi_syn, confianza, metodo, current_week,
    )
    return result
