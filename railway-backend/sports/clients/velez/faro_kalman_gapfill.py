"""
faro_kalman_gapfill.py — Filtro Kalman + LSTM híbrido para gap-filling temporal de NDVI.

Basado en: "Hybrid Kalman-LSTM approach for NDVI time series reconstruction
in the Argentine Pampas under persistent cloud cover" (preprint agosto 2025).

Pipeline:
  1. Carga EOPatch/FaroPatch del histórico (vía faro_eopatch)
  2. Ajusta modelo estacional sinusoidal (proxy LSTM) sobre observaciones limpias
  3. Ejecuta filtro Kalman estado-espacio [NDVI, dNDVI/dt]
     - Observaciones limpias: R bajo (alta confianza)
     - Gaps cortos (<14d): predicción pura, R→∞
     - Gaps largos (≥14d): usa modelo estacional como medición sintética, R=0.04
  4. margen_error_kalman = sqrt(P[0,0]) por punto temporal

Umbrales de confianza:
  margen < 0.05  → confianza alta   (≥ 95%)
  margen 0.05–0.15 → confianza media (60%)
  margen > 0.15  → confianza baja   — activar física de suelos en Hermes
"""
from __future__ import annotations

import logging
import math
import os
import sys
from datetime import date, timedelta
from typing import Optional

import numpy as np

log = logging.getLogger(__name__)

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

# ── Constantes del filtro ─────────────────────────────────────────────────────
# Ruido de proceso: variabilidad fenológica esperada (m²/d²)
_Q_NDVI   = 1e-5   # varianza en NDVI por paso temporal
_Q_RATE   = 1e-7   # varianza en tasa de cambio

# Ruido de medición: ~5% error NDVI en imagen limpia
_R_CLEAN  = 0.0025  # (0.05)² — imagen con nubosidad < 10%
_R_CLOUDY = 0.04    # (0.20)² — imagen con nubosidad 10-40%
_R_SYNTH  = 0.04    # (0.20)² — medición sintética del modelo estacional

# Duración máxima de gap sin medición para solo predicción (sin prior estacional)
_GAP_DAYS_THRESHOLD = 14


# ── Modelo estacional sinusoidal (proxy LSTM) ─────────────────────────────────

def _day_of_year(d: date) -> int:
    return int(d.strftime("%j"))


def fit_seasonal_model(timestamps: list[date], ndvi_obs: np.ndarray,
                       mask_obs: np.ndarray) -> dict:
    """
    Ajusta NDVI(t) = a + b*sin(2π*doy/365) + c*cos(2π*doy/365) + d*sin(4π*doy/365)
    usando observaciones válidas (mask_obs=False).

    Retorna dict con coeficientes; si no hay suficientes datos, retorna valores por defecto
    para hemisferio sur (BsAs): verano alto, invierno bajo.
    """
    valid_idx = np.where(~mask_obs)[0]
    if len(valid_idx) < 6:
        # Default BsAs: NDVI medio 0.45, amplitud 0.15, mínimo en julio (doy≈198)
        return {"a": 0.45, "b": -0.15, "c": 0.05, "d": 0.02, "fitted": False}

    doys = np.array([_day_of_year(timestamps[i]) for i in valid_idx], dtype=float)
    y    = ndvi_obs[valid_idx]

    # Design matrix
    t = 2 * math.pi * doys / 365.0
    X = np.column_stack([
        np.ones_like(doys),
        np.sin(t),
        np.cos(t),
        np.sin(2 * t),
    ])
    try:
        coeffs, _, _, _ = np.linalg.lstsq(X, y, rcond=None)
        return {"a": float(coeffs[0]), "b": float(coeffs[1]),
                "c": float(coeffs[2]), "d": float(coeffs[3]), "fitted": True}
    except Exception:
        return {"a": 0.45, "b": -0.15, "c": 0.05, "d": 0.02, "fitted": False}


def seasonal_predict(model: dict, d: date) -> float:
    """Predice NDVI desde el modelo estacional para la fecha dada."""
    doy = _day_of_year(d)
    t   = 2 * math.pi * doy / 365.0
    val = (model["a"]
           + model["b"] * math.sin(t)
           + model["c"] * math.cos(t)
           + model["d"] * math.sin(2 * t))
    return float(max(0.05, min(0.95, val)))


# ── Filtro Kalman estado-espacio ──────────────────────────────────────────────

def run_kalman(timestamps: list[date], ndvi_obs: np.ndarray, mask_obs: np.ndarray,
               nubosidad: np.ndarray | None = None,
               seasonal_model: dict | None = None) -> tuple[np.ndarray, np.ndarray]:
    """
    Kalman filter sobre serie temporal NDVI con gaps.

    Estado: x = [NDVI, dNDVI/dt]
    Transición: F = [[1, dt], [0, alpha]] donde alpha=0.98 (mean-reversion leve)

    Returns:
        ndvi_smooth:  array (T,) de NDVI filtrado/interpolado
        margen:       array (T,) de sqrt(P[0,0]) — incertidumbre por punto
    """
    T = len(timestamps)
    ndvi_smooth = np.zeros(T, dtype=np.float32)
    margen      = np.zeros(T, dtype=np.float32)

    if T == 0:
        return ndvi_smooth, margen

    # Estado inicial: primera observación válida o default
    valid = np.where(~mask_obs)[0]
    if len(valid) == 0:
        # Sin ninguna observación — retornar serie estacional
        if seasonal_model:
            for i, d in enumerate(timestamps):
                ndvi_smooth[i] = seasonal_predict(seasonal_model, d)
                margen[i] = 0.25  # máxima incertidumbre
        return ndvi_smooth, margen

    x = np.array([float(ndvi_obs[valid[0]]), 0.0])
    P = np.diag([0.01, 0.001])  # covarianza inicial moderada

    # Matrices del sistema
    alpha = 0.98  # mean-reversion en tasa de cambio
    Q = np.diag([_Q_NDVI, _Q_RATE])
    H = np.array([[1.0, 0.0]])

    last_obs_date = timestamps[valid[0]]

    for i, d in enumerate(timestamps):
        # Paso temporal dt en días
        if i == 0:
            dt = 1.0
        else:
            dt = max(1.0, (d - timestamps[i - 1]).days)

        F = np.array([[1.0, dt], [0.0, alpha]])
        Q_scaled = Q * dt  # escalar ruido por paso temporal

        # ── Predict ─────────────────────────────────────────────────────
        x_pred = F @ x
        P_pred = F @ P @ F.T + Q_scaled

        # Clamp predicción al rango físico
        x_pred[0] = max(0.02, min(0.95, x_pred[0]))

        # ── Measurement / Update ─────────────────────────────────────────
        gap_days = (d - last_obs_date).days

        if not mask_obs[i]:
            # Observación real disponible
            nub = float(nubosidad[i]) if nubosidad is not None else 0.0
            R = _R_CLOUDY if nub > 10 else _R_CLEAN
            z = np.array([float(ndvi_obs[i])])
            last_obs_date = d
        elif seasonal_model and gap_days >= _GAP_DAYS_THRESHOLD:
            # Gap largo: usar modelo estacional como medición sintética
            z = np.array([seasonal_predict(seasonal_model, d)])
            R = _R_SYNTH
        else:
            # Gap corto: solo predicción — no update
            x = x_pred
            P = P_pred
            ndvi_smooth[i] = float(np.clip(x[0], 0.0, 1.0))
            margen[i]      = float(np.sqrt(max(0.0, P[0, 0])))
            continue

        # Kalman gain + update
        S = H @ P_pred @ H.T + np.array([[R]])
        K = P_pred @ H.T / float(S[0, 0])
        inn = z - H @ x_pred
        x = x_pred + K.flatten() * inn[0]
        x[0] = max(0.02, min(0.95, x[0]))
        P = (np.eye(2) - K @ H) @ P_pred

        ndvi_smooth[i] = float(x[0])
        margen[i]      = float(np.sqrt(max(0.0, P[0, 0])))

    return ndvi_smooth, margen


# ── Punto de entrada principal ────────────────────────────────────────────────

def gap_fill(patch) -> dict:
    """
    Ejecuta el pipeline completo Kalman+LSTM sobre un FaroPatch o EOPatch.

    Returns dict:
      {
        "timestamps":          list[str],
        "ndvi_sintetico":      list[float],
        "margen_error_kalman": list[float],
        "seasonal_model":      dict,
        "n_gaps_filled":       int,
        "ok":                  bool,
      }
    """
    try:
        ts    = getattr(patch, "timestamps", None) or []
        data  = getattr(patch, "data", {}) or {}
        masks = getattr(patch, "mask", {}) or {}

        if not ts or "NDVI" not in data:
            return {"ok": False, "error": "patch vacío o sin NDVI"}

        ndvi_obs  = np.array(data["NDVI"], dtype=np.float32)
        mask_obs  = np.array(masks.get("NDVI", np.isnan(ndvi_obs)), dtype=bool)
        nub       = data.get("NUBOSIDAD")

        seasonal = fit_seasonal_model(ts, ndvi_obs, mask_obs)
        log.info("kalman: modelo estacional fitted=%s a=%.3f b=%.3f",
                 seasonal["fitted"], seasonal["a"], seasonal["b"])

        ndvi_smooth, margen = run_kalman(
            ts, ndvi_obs, mask_obs,
            nubosidad=nub,
            seasonal_model=seasonal,
        )

        gaps_filled = int(mask_obs.sum())

        return {
            "timestamps":          [str(d) for d in ts],
            "ndvi_sintetico":      ndvi_smooth.tolist(),
            "margen_error_kalman": margen.tolist(),
            "seasonal_model":      seasonal,
            "n_gaps_filled":       gaps_filled,
            "ok":                  True,
        }
    except Exception as exc:
        log.warning("kalman gap_fill error: %s", exc)
        return {"ok": False, "error": str(exc)}


def gap_fill_today(venue_id: str = "amalfitani",
                   cancha_id: str | None = None) -> Optional[dict]:
    """
    Obtiene el NDVI sintético de hoy via Kalman gap-fill.

    Retorna dict compatible con fetch_ndvi() si hay estimación disponible:
      {
        "fuente":             "kalman_lstm · <venue_id>",
        "fecha_imagen":       str(date.today()),
        "nubosidad_pct":      100.0,
        "canchas":            {cancha_id: {ndvi, margen_error_kalman, ...}},
        "metodo_generacion":  "KALMAN_LSTM",
        "prithvi_enriquecido": False,
      }

    Retorna None si no hay suficientes datos históricos o el margen es > 0.30.
    """
    try:
        from faro_eopatch import build_eopatch
        patch = build_eopatch(venue_id=venue_id, cancha_id=cancha_id)
        if len(patch) < 5:
            log.warning("kalman gap_fill_today: patch insuficiente (%d obs)", len(patch))
            return None

        result = gap_fill(patch)
        if not result.get("ok"):
            return None

        # Predicción para hoy
        today_str = str(date.today())
        ts_list   = result["timestamps"]
        ndvi_list = result["ndvi_sintetico"]
        marg_list = result["margen_error_kalman"]

        # Usar el último punto calculado o extrapolarlo
        if ts_list:
            last_ndvi  = float(ndvi_list[-1])
            last_margen = float(marg_list[-1])
        else:
            return None

        # Si margen > 0.30 la incertidumbre es demasiado alta
        if last_margen > 0.30:
            log.warning("kalman: margen %.3f > 0.30 — rechazando estimación hoy", last_margen)
            return None

        cid = cancha_id or venue_id
        return {
            "fuente":              f"kalman_lstm · {venue_id}",
            "fecha_imagen":        today_str,
            "nubosidad_pct":       100.0,
            "canchas": {
                cid: {
                    "ndvi":               round(last_ndvi, 3),
                    "margen_error_kalman": round(last_margen, 4),
                    "metodo_generacion":  "KALMAN_LSTM",
                }
            },
            "metodo_generacion":   "KALMAN_LSTM",
            "prithvi_enriquecido": False,
            "kalman_result":       result,
        }
    except Exception as exc:
        log.warning("kalman gap_fill_today: %s", exc)
        return None
