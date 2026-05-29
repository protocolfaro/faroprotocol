"""
dale_play_acoustic.py — Análisis acústico + sightlines para eventos masivos.
Input: rider técnico del show. Output: cobertura SPL por sector, sightlines, alertas.
Modelo geométrico (free-field): L(d) = Lw - 20·log10(d) - 11 dB.
"""
from __future__ import annotations
import logging, math
from typing import Optional

from dale_play_config import VENUE_CAP

log = logging.getLogger(__name__)

# Sectores del Amalfitani con geometría relativa al escenario (fondo oeste)
_SECTORES = [
    {"id": "campo_central",  "name": "Campo Central",  "dist_m": 80,  "cap": 25_000, "angle": 0},
    {"id": "tribuna_norte",  "name": "Tribuna Norte",  "dist_m": 120, "cap": 8_000,  "angle": 30},
    {"id": "tribuna_sur",    "name": "Tribuna Sur",    "dist_m": 140, "cap": 8_000,  "angle": 330},
    {"id": "tribuna_este",   "name": "Tribuna Este",   "dist_m": 130, "cap": 4_000,  "angle": 0},
    {"id": "tribuna_oeste",  "name": "Tribuna Oeste",  "dist_m": 70,  "cap": 4_540,  "angle": 180},
]


def _spl(lw_db: float, dist_m: float) -> float:
    if dist_m <= 0:
        return lw_db
    return lw_db - 20 * math.log10(max(dist_m, 1)) - 11


def _sightline(angle_deg: float) -> str:
    """Calidad de sightline según ángulo desde eje frontal del escenario."""
    a = abs(angle_deg)
    if a <= 60:
        return "optima"
    if a <= 120:
        return "buena"
    if a <= 150:
        return "parcial"
    return "obstruida"


def analyze_acoustic_sightlines(rider: dict) -> dict:
    """
    Analiza cobertura acústica y sightlines para el show.

    rider: {
        "stage": {"lw_db": float, "throws": ["main","delay","front_fill"]},
        "artist": str,
        "show_date": str,
        "capacidad_estimada": int,
    }
    Returns {artista, sectores, alertas_globales, spl_promedio_db,
             cobertura_optima_pct, fuente}
    """
    stage    = rider.get("stage", {})
    lw_db    = float(stage.get("lw_db", 130))
    throws   = stage.get("throws", ["main"])
    cap_est  = int(rider.get("capacidad_estimada", VENUE_CAP))

    has_delay = "delay"      in throws
    has_fill  = "front_fill" in throws

    alertas: list[str] = []
    sectores_out: list[dict] = []
    spl_vals: list[float]    = []
    n_optima = 0

    for sec in _SECTORES:
        dist = sec["dist_m"]
        # Delay towers reduce effective acoustic distance for lateral/rear sectors
        if has_delay and sec["angle"] not in (0, 30, 330):
            dist = dist * 0.65

        spl  = _spl(lw_db, dist)
        sl   = _sightline(sec["angle"])

        if spl >= 103:   cobertura = "optima";    n_optima += 1
        elif spl >= 98:  cobertura = "buena"
        elif spl >= 93:  cobertura = "aceptable"
        else:            cobertura = "baja"

        sec_alertas: list[str] = []
        if sl == "obstruida":
            sec_alertas.append(f"Sightline obstruido en {sec['name']} — pantalla lateral recomendada")
        if cobertura == "baja":
            sec_alertas.append(f"SPL insuficiente ({spl:.0f} dB) en {sec['name']} — agregar delay tower")
        if cobertura in ("baja", "aceptable") and not has_delay:
            sec_alertas.append(f"{sec['name']}: cobertura parcial sin delay towers declaradas")

        sectores_out.append({
            "id":        sec["id"],
            "name":      sec["name"],
            "spl_db":    round(spl, 1),
            "sightline": sl,
            "cobertura": cobertura,
            "dist_m":    sec["dist_m"],
            "alertas":   sec_alertas,
        })
        spl_vals.append(spl)

    # Alertas globales
    if cap_est > VENUE_CAP * 0.85:
        alertas.append(
            f"Capacidad estimada {cap_est:,} supera el 85% del aforo ({VENUE_CAP:,}) — "
            "revisar plan de evacuación"
        )
    if not has_delay:
        alertas.append(
            "Sin delay towers declaradas — cobertura acústica parcial en tribunas laterales"
        )
    low_cov = [s for s in sectores_out if s["cobertura"] == "baja"]
    if low_cov:
        alertas.append(
            f"Cobertura insuficiente en {len(low_cov)} sector(es): " +
            ", ".join(s["name"] for s in low_cov)
        )

    n = len(_SECTORES)
    return {
        "artista":              rider.get("artist", "Artista"),
        "show_date":            rider.get("show_date", ""),
        "sectores":             sectores_out,
        "alertas_globales":     alertas,
        "spl_promedio_db":      round(sum(spl_vals) / n, 1) if n else 0,
        "cobertura_optima_pct": round(n_optima / n * 100, 1) if n else 0,
        "fuente":               "Modelo acústico geométrico · Faro Protocol · Dale Play",
    }
