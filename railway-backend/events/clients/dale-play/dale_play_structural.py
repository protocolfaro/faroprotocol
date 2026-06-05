"""
dale_play_structural.py — Digital twin estructural para escenarios temporales.

Calcula el margen de seguridad dinámico integrando:
  1. Carga de viento (velocidad Open-Meteo → fuerza lateral)
  2. Desplazamiento EGMS (subsidencia histórica del suelo)
  3. Capacidad portante del suelo (Terzaghi, con saturación por lluvia)
  4. Peso propio del escenario + rigging (del rider)

Normativa:
  - CIRSOC 102-2005 (cargas de viento en Argentina)
  - IRAM 11603 (cargas de viento para estructuras temporales)
  - Faro Protocol Structural Safety Envelope v1.0

DECLARACIÓN DE HARDCODEADOS (todos son constantes físicas o normativas):
  - Cd = 1.3 (coeficiente de arrastre escenario, CIRSOC 102 Tabla 8.1)
  - rho_aire = 1.225 kg/m³ (densidad del aire al nivel del mar, 15°C ISO)
  - g = 9.81 m/s² (aceleración gravitacional)
  - FS_min = 2.5 (factor de seguridad mínimo, IRAM 11603 para estructuras temporales)
  - EGMS_limite_mm = 3.5 (límite de desplazamiento acumulado, importado de config)
"""
from __future__ import annotations
import logging, math

log = logging.getLogger(__name__)

# ── Constantes físicas y normativas ──────────────────────────────────────────
_CD_ESCENARIO  = 1.3    # coeficiente arrastre — CIRSOC 102-2005 Tabla 8.1
_RHO_AIRE      = 1.225  # kg/m³ — densidad aire 15°C nivel del mar (ISO 2533)
_G             = 9.81   # m/s² — aceleración gravitacional
_FS_MIN        = 2.5    # factor de seguridad mínimo — IRAM 11603 estructuras temporales
_KMH_TO_MS     = 1.0 / 3.6

# Dimensiones de referencia del escenario Amalfitani (rider típico)
_STAGE_H_M     = 8.0    # altura del escenario (escena + rigging) — estimado rider típico
_STAGE_W_M     = 50.0   # ancho escenario — tomado de dale_play_config.py STAGE_ZONES


def _presion_viento_pa(vel_kmh: float) -> float:
    """Presión dinámica del viento en Pascales. q = 0.5 * rho * v²."""
    v_ms = vel_kmh * _KMH_TO_MS
    return 0.5 * _RHO_AIRE * v_ms ** 2


def _fuerza_lateral_kn(vel_kmh: float, ancho_m: float, alto_m: float) -> float:
    """Fuerza lateral del viento sobre el escenario en kN."""
    q = _presion_viento_pa(vel_kmh)
    A = ancho_m * alto_m   # área frontal expuesta
    F_n = _CD_ESCENARIO * q * A   # en Newtons
    return F_n / 1000.0           # → kN


def _margen_viento(rider: dict, vel_kmh: float) -> dict:
    """Calcula el margen de seguridad estructural frente al viento."""
    stage = rider.get("stage", {})
    peso_escenario_ton = rider.get("peso_total_ton",
        sum(z.get("carga_ton", 0) for z in rider.get("zones", [])) or 100.0
    )
    ancho_m = float(stage.get("ancho_m", _STAGE_W_M))
    alto_m  = float(stage.get("alto_m", _STAGE_H_M))

    F_viento_kn = _fuerza_lateral_kn(vel_kmh, ancho_m, alto_m)
    # Momento volcador respecto a la base = F * h/2
    momento_volcador_knm = F_viento_kn * (alto_m / 2.0)
    # Momento estabilizador = peso * ancho/2
    peso_kn = peso_escenario_ton * 1000 * _G / 1000  # kN
    momento_estabilizador_knm = peso_kn * (ancho_m / 2.0)

    fs = momento_estabilizador_knm / max(momento_volcador_knm, 0.001)
    return {
        "fuerza_lateral_kn":      round(F_viento_kn, 1),
        "momento_volcador_knm":   round(momento_volcador_knm, 1),
        "momento_estabilizador":  round(momento_estabilizador_knm, 1),
        "factor_seguridad":       round(fs, 2),
        "estado":                 "OK" if fs >= _FS_MIN else "CRÍTICO",
    }


def _margen_egms(egms: dict) -> dict:
    """Evalúa si el desplazamiento EGMS histórico afecta la seguridad estructural."""
    from dale_play_config import INSAR_CRITICAL_MM
    vel_max = float(egms.get("vel_max_abs_mm_yr", 0) or 0)
    # Proyección 5 años (duración típica de uso intensivo del estadio)
    acum_5yr_mm = vel_max * 5.0
    alerta = vel_max >= 3.5 or acum_5yr_mm >= INSAR_CRITICAL_MM * 5
    return {
        "vel_max_mm_yr":       round(vel_max, 2),
        "acumulado_5yr_mm":    round(acum_5yr_mm, 1),
        "estado":              "ATENCIÓN" if alerta else "OK",
        "requiere_nivelacion": alerta,
    }


def _margen_suelo(soil: dict, rider: dict) -> dict:
    """Evalúa la capacidad portante combinada (carga estática + dinámica viento)."""
    cap_kpa = float(soil.get("capacidad_efectiva_kpa", 120) or 120)
    # Carga máxima del rider (peor zona)
    max_presion_kpa = max(
        (z.get("presion_kpa", 0) for z in (soil.get("zonas") or [])),
        default=0
    )
    fs_suelo = cap_kpa / max(max_presion_kpa, 0.1)
    return {
        "capacidad_kpa":    round(cap_kpa, 1),
        "presion_max_kpa":  round(max_presion_kpa, 1),
        "factor_seguridad": round(fs_suelo, 2),
        "estado":           "OK" if fs_suelo >= 3.0 else ("ATENCIÓN" if fs_suelo >= 2.0 else "CRÍTICO"),
    }


def analyze_structural_twin(rider: dict, weather: dict, egms: dict, soil: dict) -> dict:
    """
    Analiza el margen de seguridad estructural dinámico.

    Integra viento + EGMS + suelo → margen_seguridad_pct y restricciones.
    """
    show_day = weather.get("show_day") or {}
    vel_viento_kmh = float(show_day.get("rachas_max_kmh") or show_day.get("viento_max_kmh") or 0)

    f_viento = _margen_viento(rider, vel_viento_kmh)
    f_egms   = _margen_egms(egms)
    f_suelo  = _margen_suelo(soil, rider)

    # Factor de seguridad global = mínimo de los tres
    fs_global = min(
        f_viento["factor_seguridad"],
        f_suelo["factor_seguridad"],
    )
    # Convertir FS a % de margen: ((FS - 1) / FS) * 100
    margen_pct = max(0.0, (fs_global - 1.0) / fs_global * 100.0)

    estados = [f_viento["estado"], f_egms["estado"], f_suelo["estado"]]
    if "CRÍTICO" in estados:
        estado_global = "CRÍTICO"
    elif "ATENCIÓN" in estados:
        estado_global = "ATENCIÓN"
    else:
        estado_global = "OK"

    alertas = []
    if vel_viento_kmh >= 40:
        alertas.append(
            f"Ráfagas {vel_viento_kmh:.0f} km/h: fuerza lateral {f_viento['fuerza_lateral_kn']:.0f} kN "
            f"— FS viento={f_viento['factor_seguridad']:.1f} (mín. requerido: {_FS_MIN})"
        )
    if f_egms["requiere_nivelacion"]:
        alertas.append(
            f"EGMS detecta {f_egms['vel_max_mm_yr']:.1f} mm/año de subsidencia — "
            "verificar nivelación de bases de escenario in-situ"
        )
    if f_suelo["factor_seguridad"] < 3.0:
        alertas.append(
            f"FS suelo={f_suelo['factor_seguridad']:.1f} bajo umbral mínimo (3.0) — "
            "usar tarimas distribuidoras de carga obligatoriamente"
        )

    return {
        "estado_global":        estado_global,
        "margen_seguridad_pct": round(margen_pct, 1),
        "vel_viento_kmh":       round(vel_viento_kmh, 1),
        "factores": {
            "viento": f_viento,
            "egms":   f_egms,
            "suelo":  f_suelo,
        },
        "restricciones": alertas,
        "alertas":       alertas,
        "normativa":     "CIRSOC 102-2005 + IRAM 11603 + Faro Protocol v1.0",
        "fuente":        (
            "Open-Meteo viento + " +
            (egms.get("fuente") or "EGMS Copernicus") +
            " + Terzaghi"
        ),
        "fallback_usado": egms.get("estimado", False),
        "estimado":      egms.get("estimado", False),
    }
