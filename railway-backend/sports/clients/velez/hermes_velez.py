"""
hermes_velez.py — Bridge HermesMotor para Vélez Sarsfield.

Catálogo canónico de superficies + auditoría por cancha + calibración SAR.
Importado por data_refresh.py; nunca toca cooldowns, circuit breaker ni email.
"""
from __future__ import annotations

import logging
import numpy as np
from typing import Optional

from hermes_motor_final import HermesMotor, TenantContract, ResultadoAuditoria

log = logging.getLogger(__name__)

# Traducción tipo_cesped (velez_supabase) → tipo_superficie (HermesMotor)
_SUPERFICIE_MAP: dict[str, str] = {
    "hibrido":        "hibrida",
    "natural":        "natural",
    "sintetico":      "sintetica",
    "polvo_ladrillo": "arcilla",
    "indoor":         "indoor",
}

# Catálogo canónico Vélez — tipo_cesped por cancha_id
# Fuente: velez_supabase._TIPO_CESPED + briefing Emilio 2026-06-18
#   Amalfitani y Villa Olímpica 1FA = hibrida
#   2FA–10FA y 1FP/2FP = natural
#   Polideportivo/hockey = sintetica
#   tenis = arcilla
#   basquet = indoor
_TIPO_CESPED: dict[str, str] = {
    "amalfitani":   "hibrido",
    "1fa":          "hibrido",
    "2fa":          "natural",
    "3fa":          "natural",
    "4fa":          "natural",
    "5fa":          "natural",
    "6fa":          "natural",
    "7fa":          "natural",
    "8fa":          "natural",
    "9fa":          "natural",
    "10fa":         "natural",
    "1fp":          "natural",
    "2fp":          "natural",
    "poli_f11":     "sintetico",
    "poli_f8a":     "sintetico",
    "poli_f8b":     "sintetico",
    "poli_hockey":  "sintetico",
    "poli_tenis1":  "polvo_ladrillo",
    "poli_tenis2":  "polvo_ladrillo",
    "poli_basquet": "indoor",
}

_VELEZ_LAT, _VELEZ_LON = -34.6375, -58.5215
_DEFAULT_POLYGON = [
    [_VELEZ_LON - 0.001, _VELEZ_LAT - 0.001],
    [_VELEZ_LON + 0.001, _VELEZ_LAT - 0.001],
    [_VELEZ_LON + 0.001, _VELEZ_LAT + 0.001],
    [_VELEZ_LON - 0.001, _VELEZ_LAT + 0.001],
]

# Un motor por cancha, reutilizado durante toda la vida del proceso Railway.
_motor_cache: dict[str, HermesMotor] = {}
_RNG_SEED = 42


def _hermes_superficie(cancha_id: str) -> str:
    tipo = _TIPO_CESPED.get(cancha_id, "natural")
    return _SUPERFICIE_MAP.get(tipo, "natural")


def _get_motor(cancha_id: str) -> HermesMotor:
    if cancha_id not in _motor_cache:
        sup = _hermes_superficie(cancha_id)
        contract = TenantContract(
            tenant_id=f"velez_{cancha_id}",
            vertical="faro_sports",
            client_name=f"Vélez Sarsfield — {cancha_id.upper()}",
            polygon_gps=_DEFAULT_POLYGON,
            tipo_superficie=sup,
        )
        _motor_cache[cancha_id] = HermesMotor(contract)
    return _motor_cache[cancha_id]


# ── Calibración SAR — una vez por cancha ──────────────────────────────────────

def calibrar_fusion_sar(
    cancha_id: str,
    sar_vv_historico: np.ndarray,
    ndvi_historico: np.ndarray,
) -> dict:
    """
    Calibra la fusión SAR→NDVI del motor para esta cancha con datos reales.
    Llamar una vez por cancha con >= 20 pares (SAR, NDVI) de días sin nube.
    """
    return _get_motor(cancha_id).calibrar_fusion_sar(sar_vv_historico, ndvi_historico)


def try_calibrar_desde_supabase(cancha_id: str, venue_id: str = "amalfitani") -> dict:
    """
    Calibra la fusión SAR→NDVI usando histórico de Supabase (soil_metrics + vegetation_metrics).
    Empareja filas por fecha_imagen. No-destructivo si < 20 pares.
    """
    sup = _hermes_superficie(cancha_id)
    if sup in {"sintetica", "arcilla", "indoor"}:
        return {"status": "OMITIDO", "razon": f"Superficie {sup} no biológica."}

    try:
        import velez_supabase as _vs
        if not _vs._ok():
            return {"status": "SKIP", "razon": "Supabase no configurado."}

        soil_rows = _vs.get_soil_metrics_latest(venue_id, cancha_id=cancha_id, dias=120)
        veg_rows  = _vs.get_vegetation_metrics_latest(venue_id, cancha_id=cancha_id, dias=120)

        if not soil_rows or not veg_rows:
            return {"status": "INSUFICIENTE", "razon": "Sin histórico SAR o NDVI en Supabase."}

        sar_by_date:  dict[str, float] = {}
        ndvi_by_date: dict[str, float] = {}

        for row in soil_rows:
            fecha = (row.get("fecha_imagen") or str(row.get("created_at", ""))[:10])
            val   = row.get("sar_vv_db")
            if fecha and val is not None:
                sar_by_date[fecha] = float(val)

        for row in veg_rows:
            fecha = (row.get("fecha_imagen") or str(row.get("created_at", ""))[:10])
            val   = row.get("ndvi")
            if fecha and val is not None:
                ndvi_by_date[fecha] = float(val)

        fechas_comunes = sorted(set(sar_by_date) & set(ndvi_by_date))
        if len(fechas_comunes) < 20:
            return {
                "status": "INSUFICIENTE",
                "razon": f"Solo {len(fechas_comunes)} pares SAR+NDVI, mínimo 20.",
            }

        sar_arr  = np.array([sar_by_date[f]  for f in fechas_comunes])
        ndvi_arr = np.array([ndvi_by_date[f] for f in fechas_comunes])
        resultado = calibrar_fusion_sar(cancha_id, sar_arr, ndvi_arr)
        log.info("hermes_velez: calibración SAR OK cancha=%s n=%d r2=%.3f",
                 cancha_id, len(fechas_comunes), resultado.get("r2", 0))
        return resultado

    except Exception as exc:
        log.warning("hermes_velez: calibración SAR (non-fatal): %s", exc)
        return {"status": "ERROR", "razon": str(exc)}


# ── Auditoría por cancha ───────────────────────────────────────────────────────

def _bootstrap_historicos(ndvi_ref: float) -> dict:
    rng = np.random.default_rng(_RNG_SEED)
    return {
        "sano":   np.clip(rng.normal(ndvi_ref + 0.15, 0.04, 40), 0.0, 1.0),
        "estres": np.clip(rng.normal(max(0.01, ndvi_ref - 0.10), 0.04, 30), 0.0, 1.0),
    }


def _bootstrap_geo_series(ndvi_ref: float) -> dict:
    rng  = np.random.default_rng(_RNG_SEED)
    dias = np.arange(1, 26)
    return {
        "dias_actual":  dias,
        "ndvi_actual":  np.clip(ndvi_ref + rng.normal(0, 0.02, len(dias)), 0.0, 1.0),
        "dias_patron":  dias,
        "ndvi_patron":  np.clip(ndvi_ref + 0.05 + rng.normal(0, 0.02, len(dias)), 0.0, 1.0),
    }


def audit_cancha(
    cancha_id: str,
    ndvi_optico: Optional[float],
    sar_vv_db: Optional[float] = None,
    historicos: Optional[dict] = None,
    geo_series: Optional[dict] = None,
) -> ResultadoAuditoria:
    """
    Audita una cancha.  Usa bootstrap sintético si no hay histórico real disponible.
    """
    ndvi_ref = ndvi_optico if ndvi_optico is not None else 0.45
    if historicos is None:
        historicos = _bootstrap_historicos(ndvi_ref)
    if geo_series is None:
        geo_series = _bootstrap_geo_series(ndvi_ref)
    return _get_motor(cancha_id).auditar(ndvi_optico, sar_vv_db, historicos, geo_series)


def audit_all_canchas(
    heatmaps: dict,
    sar_vv_db: Optional[float] = None,
) -> dict[str, ResultadoAuditoria]:
    """
    Audita todas las canchas del catálogo que tengan datos en heatmaps o SAR disponible.
    """
    results: dict[str, ResultadoAuditoria] = {}
    for cid in _TIPO_CESPED:
        hm = heatmaps.get(cid)
        ndvi_val = hm.get("ndvi") if isinstance(hm, dict) else None
        if not isinstance(hm, dict) and sar_vv_db is None:
            continue
        try:
            results[cid] = audit_cancha(cid, ndvi_val, sar_vv_db)
        except Exception as exc:
            log.warning("hermes_velez: audit_cancha %s (non-fatal): %s", cid, exc)
    return results
