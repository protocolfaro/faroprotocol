"""
dale_play_rider_compliance.py — Compliance del rider técnico vs condiciones reales.

Cruza los requerimientos del rider contra:
  - Módulo acústico (SPL real por sector vs SPL mínimo rider)
  - Módulo de suelo (presión por zona vs capacidad portante)
  - Módulo de drenaje (zonas de uso del escenario vs zonas de exclusión)

Retorna: lista de incumplimientos con severidad, lista de aprobaciones.

Principio: NUNCA genera un incumplimiento sin dato real para comparar.
Si falta un módulo, el compliance de esa categoría queda como INDETERMINADO.
"""
from __future__ import annotations
import logging

log = logging.getLogger(__name__)

# Constantes de compliance — Faro Protocol Rider Standard v1.0
# Fuente: análisis de riders técnicos estándar en Argentina (2022-2025)
SPL_MINIMO_CAMPO_DB    = 98.0   # dB — SPL mínimo aceptable campo central
SPL_MINIMO_TRIBUNA_DB  = 96.0   # dB — SPL mínimo aceptable tribunas
SPL_MINIMO_PLATEA_DB   = 94.0   # dB — SPL mínimo platea alta

_SPL_MINIMOS = {
    "campo_central":     SPL_MINIMO_CAMPO_DB,
    "tribuna_norte":     SPL_MINIMO_TRIBUNA_DB,
    "tribuna_sur":       SPL_MINIMO_TRIBUNA_DB,
    "tribuna_este":      SPL_MINIMO_TRIBUNA_DB,
    "platea_alta_norte": SPL_MINIMO_PLATEA_DB,
    "platea_alta_sur":   SPL_MINIMO_PLATEA_DB,
}


def _check_acoustic_compliance(rider: dict, acoustic: dict) -> tuple[list, list]:
    """Compara SPL rider vs SPL real. Retorna (incumplimientos, aprobaciones)."""
    if not acoustic or acoustic.get("error"):
        return [{"categoria": "acustica", "estado": "INDETERMINADO",
                 "detalle": "Módulo acústico no disponible — compliance no evaluado",
                 "severidad": "info"}], []

    spl_min_rider = float((rider.get("stage") or {}).get("spl_minimo_db", 98.0))
    sectores = acoustic.get("sectores") or []
    incs, aprs = [], []

    for sec in sectores:
        sid  = sec.get("id", "")
        spl  = sec.get("spl_db")
        if spl is None:
            continue
        umbral = max(spl_min_rider, _SPL_MINIMOS.get(sid, SPL_MINIMO_TRIBUNA_DB))
        deficit = round(umbral - float(spl), 1)
        if deficit > 0:
            sev = "critico" if deficit > 4 else ("advertencia" if deficit > 2 else "leve")
            incs.append({
                "categoria": "acustica",
                "sector":    sid,
                "estado":    "INCUMPLIMIENTO",
                "detalle":   f"SPL real {spl:.1f} dB vs mínimo {umbral:.0f} dB (déficit {deficit:.1f} dB)",
                "severidad": sev,
                "accion":    "Instalar delay tower o ajustar hang del main array",
            })
        else:
            aprs.append({
                "categoria": "acustica",
                "sector":    sid,
                "estado":    "CONFORME",
                "detalle":   f"SPL {spl:.1f} dB ≥ mínimo {umbral:.0f} dB (+{abs(deficit):.1f} dB margen)",
            })
    return incs, aprs


def _check_soil_compliance(rider: dict, soil: dict) -> tuple[list, list]:
    """Verifica que las zonas de stage no excedan capacidad portante."""
    if not soil or soil.get("error"):
        return [{"categoria": "suelo", "estado": "INDETERMINADO",
                 "detalle": "Módulo suelo no disponible — compliance no evaluado",
                 "severidad": "info"}], []

    incs, aprs = [], []
    for zona in (soil.get("zonas") or []):
        clase     = zona.get("clase", "")
        nombre    = zona.get("nombre", "")
        presion   = zona.get("presion_kpa", 0)
        if clase == "exclusion":
            incs.append({
                "categoria": "suelo",
                "zona":      nombre,
                "estado":    "INCUMPLIMIENTO",
                "detalle":   f"Zona {nombre}: {presion:.0f} kPa excede capacidad portante → ZONA DE EXCLUSIÓN",
                "severidad": "critico",
                "accion":    "Redistribuir carga con tarimas o reubicar estructura",
            })
        elif clase == "precaucion":
            incs.append({
                "categoria": "suelo",
                "zona":      nombre,
                "estado":    "ATENCIÓN",
                "detalle":   f"Zona {nombre}: {presion:.0f} kPa en zona de precaución",
                "severidad": "advertencia",
                "accion":    "Usar tarimas distribuidoras de carga 2.5×2.5m",
            })
        else:
            aprs.append({
                "categoria": "suelo",
                "zona":      nombre,
                "estado":    "CONFORME",
                "detalle":   f"Zona {nombre}: {presion:.0f} kPa dentro de capacidad portante",
            })
    return incs, aprs


def _check_drainage_compliance(rider: dict, drainage: dict) -> tuple[list, list]:
    """Verifica que el área del stage no coincida con zonas de exclusión hídrica."""
    if not drainage or drainage.get("error"):
        return [{"categoria": "drenaje", "estado": "INDETERMINADO",
                 "detalle": "Módulo drenaje no disponible — compliance no evaluado",
                 "severidad": "info"}], []

    exclusiones = drainage.get("exclusiones") or []
    resumen     = drainage.get("resumen") or {}
    incs, aprs  = [], []

    n_excl = resumen.get("zonas_exclusion", 0)
    if n_excl > 0:
        incs.append({
            "categoria": "drenaje",
            "estado":    "INCUMPLIMIENTO",
            "detalle":   f"{n_excl} zona(s) de exclusión hídrica activas: {', '.join(str(e) for e in exclusiones[:3])}",
            "severidad": "critico",
            "accion":    "No instalar escenario ni estructuras pesadas en zonas de exclusión hídrica",
        })
    else:
        aprs.append({
            "categoria": "drenaje",
            "estado":    "CONFORME",
            "detalle":   "Sin zonas de exclusión hídrica — instalación de escenario habilitada",
        })

    n_prec = resumen.get("zonas_precaucion", 0)
    if n_prec > 0:
        incs.append({
            "categoria": "drenaje",
            "estado":    "ATENCIÓN",
            "detalle":   f"{n_prec} zona(s) de precaución hídrica — verificar drenaje 48h post-lluvia",
            "severidad": "advertencia",
            "accion":    "Programar inspección de suelo 24h antes del show si llueve",
        })

    return incs, aprs


def _check_source_uncertainty(soil: dict, drainage: dict) -> list:
    """
    Si suelo o drenaje usaron fallback/estimación, agrega advertencia explícita.
    El compliance no debe presentarse como definitivo si los datos base son estimados.
    """
    warnings = []
    for modulo, data in [("suelo", soil), ("drenaje", drainage)]:
        if not data:
            continue
        if data.get("fallback_usado") or data.get("estimado"):
            fuente = data.get("fuente", "desconocida")
            warnings.append({
                "categoria": modulo,
                "estado":    "ADVERTENCIA DE INCERTIDUMBRE",
                "detalle":   (
                    f"Análisis de {modulo} basado en estimación — "
                    f"fuente: {fuente}. "
                    "Verificar con dato real antes del show."
                ),
                "severidad": "advertencia",
                "accion":    f"Confirmar capacidad portante del {modulo} con medición in-situ",
            })
    return warnings


def analyze_rider_compliance(rider: dict, acoustic: dict, soil: dict, drainage: dict) -> dict:
    """
    Evalúa compliance del rider contra condiciones reales.
    INDETERMINADO si el módulo no está disponible — nunca inventa datos.
    Si suelo o drenaje son estimados, agrega advertencia de incertidumbre.
    """
    incs, aprs = [], []

    ac_i, ac_a = _check_acoustic_compliance(rider, acoustic)
    so_i, so_a = _check_soil_compliance(rider, soil)
    dr_i, dr_a = _check_drainage_compliance(rider, drainage)

    incs.extend(ac_i); aprs.extend(ac_a)
    incs.extend(so_i); aprs.extend(so_a)
    incs.extend(dr_i); aprs.extend(dr_a)

    # Advertencias de incertidumbre por datos estimados
    uncertainty_warnings = _check_source_uncertainty(soil, drainage)
    incs.extend(uncertainty_warnings)

    criticos     = [i for i in incs if i.get("severidad") == "critico"]
    advertencias = [i for i in incs if i.get("severidad") == "advertencia"]
    indetermins  = [i for i in incs if i.get("estado") == "INDETERMINADO"]

    if criticos:
        estado_global = "NO CONFORME"
    elif advertencias:
        estado_global = "ATENCIÓN"
    elif indetermins and not aprs:
        estado_global = "INDETERMINADO"
    else:
        estado_global = "CONFORME"

    return {
        "estado_global":      estado_global,
        "incumplimientos":    [i for i in incs if i.get("estado") != "INDETERMINADO"],
        "indeterminados":     indetermins,
        "aprobaciones":       aprs,
        "n_criticos":         len(criticos),
        "n_advertencias":     len(advertencias),
        "n_conformes":        len(aprs),
        "fuente":             "Rider técnico + datos acústicos/suelo/drenaje",
        "fallback_usado":     False,
        "estimado":           False,
    }
