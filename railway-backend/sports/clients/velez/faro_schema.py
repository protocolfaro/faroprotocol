"""
faro_schema.py — Canonical TypedDict schema for VelezReport.

REGLA ARQUITECTÓNICA: Este archivo es el ÚNICO lugar donde se define el contrato de datos.
Agregar un módulo nuevo = agregar campos Optional aquí. Los renderers nunca se tocan.

Todos los campos son Optional salvo id, nombre, score, sem.
Los renderers acceden vía .get() con default — nunca crashes por campos nuevos o faltantes.
"""
from __future__ import annotations
from typing import List, Optional, TypedDict


class CanchaReport(TypedDict, total=False):
    # REQUIRED
    id: str
    nombre: str
    score: int
    sem: str                          # "verde" | "amarillo" | "rojo"
    # Óptico — vegetation_metrics / Sentinel-2
    ndvi: Optional[float]
    gndvi: Optional[float]
    bsi: Optional[float]
    ndwi: Optional[float]
    evi2: Optional[float]
    # Superficie
    tipo_cesped: Optional[str]        # "natural" | "hibrido"
    # Nitrógeno
    n_status: Optional[str]
    n_rec: Optional[str]
    # Texto descriptivo
    detalle: Optional[str]
    score_prev: Optional[int]
    insar_mm: Optional[float]
    # SAR polarimetría — faro_sar_polimetria
    entropia_h: Optional[float]
    angulo_alpha: Optional[float]
    # Compactación ML — faro_compactacion_ml
    compactacion_index_ml: Optional[float]
    # Landsat 9 TIRS — faro_landsat_thermal
    temp_superficie_c: Optional[float]
    # Richards 1D — faro_richards_profile
    theta_5cm: Optional[float]
    theta_10cm: Optional[float]
    theta_20cm: Optional[float]
    # SMAP 9km — faro_ecostress
    theta_smap: Optional[float]
    # ESA super-resolución 2.5m — faro_superres
    ndvi_2_5m: Optional[float]
    bsi_2_5m: Optional[float]
    ndwi_2_5m: Optional[float]


class SectorReport(TypedDict, total=False):
    # REQUIRED
    nombre: str
    score: int
    sem: str
    # Optional
    detalle: Optional[str]
    score_prev: Optional[int]
    insar_mm: Optional[float]
    canchas: Optional[List[CanchaReport]]   # solo sector "canchero"


class WeatherLive(TypedDict, total=False):
    # NASA POWER / OpenMeteo
    et0_mm_dia: Optional[float]
    et0_semana_mm: Optional[float]
    deficit_hidrico_mm: Optional[float]
    gdd_acumulado_7d: Optional[float]
    precipitacion_semana_mm: Optional[float]
    humedad_suelo_pct: Optional[float]
    humedad_suelo_estado: Optional[str]
    hora_riego_optima: Optional[str]
    hora_corte_optima: Optional[str]
    riego_min_sector: Optional[int]
    ventana_corte: Optional[str]
    altura_corte_mm: Optional[float]
    dias_proximo_corte: Optional[int]
    suelo_tipo: Optional[str]
    suelo_whc_mm: Optional[float]
    riesgo_dollar_spot_pct: Optional[float]
    # Smith-Kerns MSU — climate_metrics
    smith_kerns_pct: Optional[float]
    # ECOSTRESS — faro_ecostress
    et_ecostress_mm: Optional[float]
    # Índices ópticos por cancha — formato velez_data.json
    gndvi_por_cancha: Optional[dict]


class HermesView(TypedDict, total=False):
    """Salida de hermes_consolidate pre-ensamblada. Acceder vía vd['hermes']."""
    humedad_estimada: Optional[float]
    confianza_consolidada: Optional[float]
    fuentes_activas: Optional[List[str]]
    alertas: Optional[List[str]]
    _et0_acumulada_mm: Optional[float]
    _theta_sar: Optional[float]
    soil: Optional[dict]
    vegetation: Optional[dict]
    climate: Optional[dict]


class VelezReport(TypedDict, total=False):
    # Proveniencia del ensamblado
    _assembled_at: str
    _venue_id: str
    # Estructura core — backward compatible con velez_data.json
    meta: Optional[dict]
    weather_live: Optional[WeatherLive]
    sectores: Optional[dict]          # {sector_id: SectorReport}
    usuarios: Optional[dict]
    # Vista Hermes pre-calculada
    hermes: Optional[HermesView]
