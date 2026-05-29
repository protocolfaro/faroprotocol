"""
dale_play_soil.py — Mapa de carga del suelo para estructuras de evento.
Clasifica zonas en seguras, precaución y exclusión según carga declarada en rider.
Suelo base: Franco-arcilloso típico Liniers (120 kPa seco / 65 kPa saturado).
"""
from __future__ import annotations
import logging
from typing import Optional

from dale_play_config import (
    STAGE_ZONES, SOIL_PROFILE,
    SOIL_SAFE_KPA, SOIL_CAUTION_KPA, SOIL_CRITICAL_KPA,
)

log = logging.getLogger(__name__)


def _presion_kpa(carga_ton: float, area_m2: float) -> float:
    if area_m2 <= 0:
        return 0.0
    kn = carga_ton * 9.81  # 1 tonelada-fuerza ≈ 9.81 kN
    return round(kn / area_m2, 1)


def _classify(presion: float, capacidad: float) -> dict:
    ratio = presion / capacidad if capacidad else 1.0
    if ratio >= 1.0 or presion >= SOIL_CRITICAL_KPA:
        return {"clase": "exclusion",  "label": "Zona de exclusión — no colocar estructura",
                "color": "#e74c3c"}
    if ratio >= 0.75 or presion >= SOIL_CAUTION_KPA:
        return {"clase": "precaucion", "label": "Precaución — distribuir con durmientes",
                "color": "#f0b429"}
    return     {"clase": "segura",     "label": "Zona segura para estructura",
                "color": "#27ae60"}


def analyze_soil_load(rider: dict, lluvia_48h_mm: float = 0.0) -> dict:
    """
    Analiza la carga del suelo para las estructuras declaradas en el rider.

    rider: {
        "estructuras": [
            {"nombre": str, "carga_ton": float, "area_m2": float}
        ]
    }
    lluvia_48h_mm: lluvia acumulada 48h antes del show (reduce capacidad portante).

    Returns {condicion_suelo, capacidad_efectiva_kpa, zonas, alertas,
             hay_exclusiones, n_exclusiones, fuente}
    """
    # Ajuste capacidad portante por condición hídrica
    cap_seco = SOIL_PROFILE["capacidad_portante_kpa"]
    cap_sat  = SOIL_PROFILE["capacidad_saturada_kpa"]

    if lluvia_48h_mm >= 30:
        capacidad   = cap_sat
        condicion   = "saturado"
    elif lluvia_48h_mm >= 10:
        capacidad   = round(cap_seco * 0.80)
        condicion   = "húmedo"
    else:
        capacidad   = cap_seco
        condicion   = "normal"

    # Usar estructuras del rider; si vacías, usar defaults del config
    estructuras = rider.get("estructuras") or [
        {
            "nombre":    z["name"],
            "carga_ton": z["carga_ton"],
            "area_m2":   z.get("w_m", 10) * z.get("h_m", 10),
        }
        for z in STAGE_ZONES
    ]

    alertas: list[str] = []
    zonas:   list[dict] = []

    for est in estructuras:
        carga  = float(est.get("carga_ton", 0))
        area   = float(est.get("area_m2", 1))
        p_kpa  = _presion_kpa(carga, area)
        clase  = _classify(p_kpa, capacidad)

        zona = {
            "nombre":       est.get("nombre", "Estructura"),
            "carga_ton":    carga,
            "area_m2":      area,
            "presion_kpa":  p_kpa,
            **clase,
        }
        zonas.append(zona)

        if clase["clase"] == "exclusion":
            alertas.append(
                f"⚠ EXCLUSIÓN: {zona['nombre']} — {p_kpa} kPa supera capacidad "
                f"({capacidad} kPa). Redistribuir o mover zona."
            )
        elif clase["clase"] == "precaucion":
            alertas.append(
                f"Precaución: {zona['nombre']} — {p_kpa} kPa. "
                "Usar durmientes (mínimo 4 m²/punto de apoyo)."
            )

    if condicion == "saturado":
        alertas.insert(0,
            f"Suelo saturado ({lluvia_48h_mm:.0f} mm en 48h) — "
            f"capacidad portante reducida a {capacidad} kPa"
        )
    elif condicion == "húmedo":
        alertas.insert(0,
            f"Suelo húmedo ({lluvia_48h_mm:.0f} mm en 48h) — "
            f"capacidad reducida a {capacidad} kPa"
        )

    excl = [z for z in zonas if z["clase"] == "exclusion"]

    return {
        "condicion_suelo":      condicion,
        "lluvia_48h_mm":        lluvia_48h_mm,
        "capacidad_efectiva_kpa": capacidad,
        "suelo_tipo":           SOIL_PROFILE["tipo"],
        "zonas":                zonas,
        "alertas":              alertas,
        "hay_exclusiones":      len(excl) > 0,
        "n_exclusiones":        len(excl),
        "fuente":               "Perfil edafológico BHCP · Modelo carga Faro Protocol",
    }
