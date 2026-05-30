"""
dale_play_spl_compliance.py — Compliance SPL regulatorio para eventos en BsAs.

Normativa aplicada:
  - Ley Nacional 1.160-D-2016 (límites ruido urbano)
  - Ordenanza GCBA 11.554/1994 — ruido en espacios públicos
  - Resolución GCBA 5613/2016 — eventos masivos (modificatoria)
  - Estándar ISO 1996-1 (evaluación de ruido ambiente)

Estadio Amalfitani: Liniers, CABA — zona semi-residencial.
Límite exterior en límite predial:
  - Diurno (8h-22h):  65 dB(A) — zona mixta residencial/equipamiento
  - Nocturno (22h-8h): 55 dB(A)
Límite interior evento (por permiso especial GCBA):
  - SPL máximo público: 103 dB(A) — umbral de daño auditivo (NIOSH)
  - SPL recomendado OPS/OMS: 100 dB(A) durante max 4h
"""
from __future__ import annotations
import logging, math
from datetime import datetime

log = logging.getLogger(__name__)

# ── Constantes regulatorias ───────────────────────────────────────────────────
# Fuente: Ordenanza GCBA 11.554/1994 mod. Res. 5613/2016
LIMITE_EXTERIOR_DIURNO_DBA   = 65.0   # dB(A) en límite predial, 8-22h
LIMITE_EXTERIOR_NOCTURNO_DBA = 55.0   # dB(A) en límite predial, 22-8h
LIMITE_INTERIOR_OMS_DBA      = 100.0  # dB(A) interior evento, recomendación OMS
LIMITE_INTERIOR_NIOSH_DBA    = 103.0  # dB(A) interior, umbral daño 4h NIOSH

# Amalfitani: distancia aprox. escenario → límite predial (calle Riestra/Álvarez Jonte)
DIST_PREDIAL_M = 180.0   # metros — estimado conservador
# Fuente: mapa OSM + Google Maps, medición manual, ± 20m

# Atenuación adicional por paredes/estructura del estadio
ATENUACION_ESTRUCTURA_DB = 12.0   # dB — estadio cubierto parcialmente (tribuna techada)
# Fuente: estimación acústica típica para estadios semiabiertos (referencia: Barron 2010)


def _spl_en_distancia(lw_db: float, dist_m: float) -> float:
    """SPL en campo libre a distancia dada. Formula: Lw - 20·log10(d) - 11."""
    if dist_m <= 0:
        return lw_db
    return lw_db - 20 * math.log10(dist_m) - 11


def analyze_spl_compliance(acoustic: dict, rider: dict) -> dict:
    """
    Cruza resultados del módulo acústico contra límites regulatorios BsAs.

    Returns:
        dict con estado_global, limites_aplicados, sectores, alertas
    """
    stage    = rider.get("stage", {})
    lw_db    = float(stage.get("lw_db", 130))
    show_date = rider.get("show_date", "")

    # Determinar si es diurno o nocturno (asumimos horario típico de show 20-24h)
    hora_inicio = rider.get("hora_inicio", 20)
    es_nocturno = int(hora_inicio) >= 20
    limite_ext  = LIMITE_EXTERIOR_NOCTURNO_DBA if es_nocturno else LIMITE_EXTERIOR_DIURNO_DBA
    periodo     = "NOCTURNO (22h-8h)" if es_nocturno else "DIURNO (8h-22h)"

    # SPL estimado en límite predial
    spl_predial_sin_atenuacion = _spl_en_distancia(lw_db, DIST_PREDIAL_M)
    spl_predial_estimado = spl_predial_sin_atenuacion - ATENUACION_ESTRUCTURA_DB

    # Verificar sectores interiores contra límites OMS/NIOSH
    sectores_out = []
    max_spl_interior = 0.0
    n_sobre_oms      = 0
    n_sobre_niosh    = 0

    for sec in (acoustic.get("sectores") or []):
        spl = sec.get("spl_db")
        if spl is None:
            continue
        sobre_oms   = float(spl) > LIMITE_INTERIOR_OMS_DBA
        sobre_niosh = float(spl) > LIMITE_INTERIOR_NIOSH_DBA
        if sobre_oms:
            n_sobre_oms += 1
        if sobre_niosh:
            n_sobre_niosh += 1
        if float(spl) > max_spl_interior:
            max_spl_interior = float(spl)

        sectores_out.append({
            "id":            sec.get("id"),
            "spl_db":        spl,
            "sobre_oms":     sobre_oms,
            "sobre_niosh":   sobre_niosh,
            "estado":        "EXCEDE NIOSH" if sobre_niosh else ("EXCEDE OMS" if sobre_oms else "OK"),
        })

    # Estado exterior
    excede_exterior = spl_predial_estimado > limite_ext
    margen_exterior = round(limite_ext - spl_predial_estimado, 1)

    # Estado global
    if n_sobre_niosh > 0 or excede_exterior:
        estado_global = "NO CONFORME"
    elif n_sobre_oms > 0:
        estado_global = "ATENCIÓN"
    else:
        estado_global = "CONFORME"

    alertas = []
    if excede_exterior:
        alertas.append(
            f"SPL exterior estimado ({spl_predial_estimado:.1f} dB) excede límite {periodo} "
            f"({limite_ext:.0f} dB) en {abs(margen_exterior):.1f} dB — requiere permiso especial GCBA"
        )
    if n_sobre_niosh > 0:
        alertas.append(
            f"{n_sobre_niosh} sector(es) superan umbral NIOSH ({LIMITE_INTERIOR_NIOSH_DBA:.0f} dB) "
            "— requiere aviso de riesgo auditivo y EPP para staff"
        )
    if n_sobre_oms > 0 and n_sobre_niosh == 0:
        alertas.append(
            f"{n_sobre_oms} sector(es) superan recomendación OMS ({LIMITE_INTERIOR_OMS_DBA:.0f} dB) "
            "— informar a público en accesos"
        )

    return {
        "estado_global":       estado_global,
        "periodo":             periodo,
        "spl_max_interior_db": round(max_spl_interior, 1),
        "spl_predial_db":      round(spl_predial_estimado, 1),
        "limite_exterior_dba": limite_ext,
        "margen_exterior_db":  margen_exterior,
        "excede_exterior":     excede_exterior,
        "sectores_sobre_oms":  n_sobre_oms,
        "sectores_sobre_niosh": n_sobre_niosh,
        "sectores":            sectores_out,
        "alertas":             alertas,
        "limites_aplicados": {
            "exterior":  f"Ordenanza GCBA 11.554/1994 — {limite_ext} dB(A) {periodo}",
            "oms":       f"OMS 2018 — {LIMITE_INTERIOR_OMS_DBA} dB(A) interior",
            "niosh":     f"NIOSH 1998 — {LIMITE_INTERIOR_NIOSH_DBA} dB(A) interior",
        },
        "fuente":        "Ordenanza GCBA 11.554/1994 + Res. 5613/2016 + OMS + NIOSH",
        "fallback_usado": False,
        "estimado":      True,  # SPL exterior es estimación geométrica
    }
