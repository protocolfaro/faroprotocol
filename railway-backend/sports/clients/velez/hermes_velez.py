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
    Calibra la fusión SAR→NDVI usando histórico de Supabase.

    Orden de intentos (mejor fuente primero):
      0. SAOCOM L-band Quad-Pol  — si hay ≥20 pares en soil_metrics (fuente SAOCOM_L-BAND)
      1. vegetation_metrics      — C-band dual-pol, dias=120, match exacto por fecha
      2. field_timeseries        — NDVI limpio + sigma0-cal, histórico completo, ±3d

    No-destructivo si ninguna fuente alcanza 20 pares.
    """
    sup = _hermes_superficie(cancha_id)
    if sup in {"sintetica", "arcilla", "indoor"}:
        return {"status": "OMITIDO", "razon": f"Superficie {sup} no biológica."}

    try:
        import velez_supabase as _vs
        if not _vs._ok():
            return {"status": "SKIP", "razon": "Supabase no configurado."}

        # ── Intento 0: SAOCOM L-band Quad-Pol (prioridad máxima) ─────────────
        saocom_result = _calibrar_saocom_quad_pol(cancha_id, venue_id)
        if saocom_result.get("status") == "OK":
            return saocom_result

        # ── Intento 1: vegetation_metrics (dias=120, match exacto) ────────────
        soil_rows = _vs.get_soil_metrics_latest(venue_id, cancha_id=cancha_id, dias=120)
        veg_rows  = _vs.get_vegetation_metrics_latest(venue_id, cancha_id=cancha_id, dias=120)

        sar_by_date:  dict[str, float] = {}
        ndvi_by_date: dict[str, float] = {}

        for row in soil_rows or []:
            fecha = (row.get("fecha_imagen") or str(row.get("created_at", ""))[:10])
            val   = row.get("sar_vv_db")
            if fecha and val is not None:
                sar_by_date[fecha] = float(val)

        for row in veg_rows or []:
            fecha = (row.get("fecha_imagen") or str(row.get("created_at", ""))[:10])
            val   = row.get("ndvi")
            if fecha and val is not None:
                ndvi_by_date[fecha] = float(val)

        fechas_comunes = sorted(set(sar_by_date) & set(ndvi_by_date))
        if len(fechas_comunes) >= 20:
            sar_arr  = np.array([sar_by_date[f]  for f in fechas_comunes])
            ndvi_arr = np.array([ndvi_by_date[f] for f in fechas_comunes])
            resultado = calibrar_fusion_sar(cancha_id, sar_arr, ndvi_arr)
            log.info("hermes_velez: SAR calibrado desde VEGETATION_METRICS (n=%d) cancha=%s r2=%.3f",
                     len(fechas_comunes), cancha_id, resultado.get("r2", 0))
            return resultado

        # ── Intento 2: field_timeseries (NDVI verificado) + sigma0-cal ────────
        log.info("hermes_velez: vegetation_metrics insuficiente (%d pares) — fallback field_timeseries cancha=%s",
                 len(fechas_comunes), cancha_id)
        return _calibrar_desde_field_timeseries(cancha_id, venue_id)

    except Exception as exc:
        log.warning("hermes_velez: calibración SAR (non-fatal): %s", exc)
        return {"status": "ERROR", "razon": str(exc)}


def _calibrar_desde_field_timeseries(cancha_id: str, venue_id: str) -> dict:
    """
    Fallback de calibración: field_timeseries (NDVI limpio, bbox correcto) +
    soil_metrics sigma0-cal (histórico completo sin límite de días).
    Match ±3 días. Persiste resultado en fenologia_baselines.sar_ndvi_coef.
    """
    import json
    import os
    import requests as _req
    from datetime import datetime as _dt, timezone

    supa_url = os.environ.get("SUPABASE_URL", "").rstrip("/")
    supa_key = os.environ.get("SUPABASE_KEY", "")
    if not supa_url or not supa_key:
        return {"status": "SKIP", "razon": "SUPABASE_URL/KEY no configurados."}

    hdrs = {"apikey": supa_key, "Authorization": f"Bearer {supa_key}"}

    # SAR: soil_metrics sigma0-cal, sin límite de días (limit=500 cubre 3+ años)
    r_soil = _req.get(
        f"{supa_url}/rest/v1/soil_metrics"
        f"?venue_id=eq.{venue_id}&select=fecha_imagen,sar_vv_db,fuente"
        f"&order=fecha_imagen.asc&limit=500",
        headers=hdrs, timeout=12,
    )
    if r_soil.status_code != 200:
        return {"status": "ERROR", "razon": f"soil_metrics HTTP {r_soil.status_code}"}

    sar_by_date: dict[str, float] = {}
    for row in r_soil.json():
        if "sigma0-cal" not in str(row.get("fuente", "")):
            continue
        fecha = str(row.get("fecha_imagen") or "")[:10]
        val   = row.get("sar_vv_db")
        if fecha and val is not None:
            sar_by_date[fecha] = float(val)

    # NDVI: field_timeseries sin bbox_legacy (41 registros limpios verificados)
    r_ndvi = _req.get(
        f"{supa_url}/rest/v1/field_timeseries"
        f"?venue_id=eq.{venue_id}&select=fecha,ndvi,fuente&order=fecha.asc&limit=200",
        headers=hdrs, timeout=12,
    )
    if r_ndvi.status_code != 200:
        return {"status": "ERROR", "razon": f"field_timeseries HTTP {r_ndvi.status_code}"}

    ndvi_by_date: dict[str, float] = {}
    for row in r_ndvi.json():
        if str(row.get("fuente", "")).startswith("bbox_legacy"):
            continue
        fecha = str(row.get("fecha") or "")[:10]
        val   = row.get("ndvi")
        if fecha and val is not None:
            ndvi_by_date[fecha] = float(val)

    # Emparejamiento ±3 días
    pares: list[tuple[float, float]] = []
    for sar_f, sar_v in sorted(sar_by_date.items()):
        sar_dt = _dt.strptime(sar_f, "%Y-%m-%d")
        best_v, best_d = None, 999
        for ndvi_f, ndvi_v in ndvi_by_date.items():
            d = abs((_dt.strptime(ndvi_f, "%Y-%m-%d") - sar_dt).days)
            if d <= 3 and d < best_d:
                best_v, best_d = ndvi_v, d
        if best_v is not None:
            pares.append((sar_v, best_v))

    if len(pares) < 20:
        return {"status": "INSUFICIENTE",
                "razon": f"field_timeseries fallback: {len(pares)} pares (mín 20)."}

    sar_arr  = np.array([p[0] for p in pares])
    ndvi_arr = np.array([p[1] for p in pares])
    resultado = calibrar_fusion_sar(cancha_id, sar_arr, ndvi_arr)
    resultado["fuente_ndvi"] = "field_timeseries"

    log.info("hermes_velez: SAR calibrado desde FIELD_TIMESERIES fallback (n=%d) cancha=%s r2=%.3f",
             len(pares), cancha_id, resultado.get("r2", 0))

    # Persistir en fenologia_baselines para trazabilidad y Dale Play
    try:
        hdrs_json = {**hdrs, "Content-Type": "application/json"}
        r_fb = _req.get(
            f"{supa_url}/rest/v1/fenologia_baselines?venue_id=eq.{venue_id}&select=baseline",
            headers=hdrs, timeout=8,
        )
        existing: dict = {}
        if r_fb.status_code == 200 and r_fb.json():
            existing = r_fb.json()[0].get("baseline") or {}
        existing["sar_ndvi_coef"] = {
            **{k: resultado[k] for k in ("slope", "intercept", "r2_ref", "n_pares") if k in resultado},
            "fuente_ndvi":   "field_timeseries",
            "fuente_sar":    "soil_metrics·sigma0-cal",
            "calibrado_utc": _dt.now(timezone.utc).isoformat(),
        }
        _req.post(
            f"{supa_url}/rest/v1/fenologia_baselines",
            headers={**hdrs_json, "Prefer": "resolution=merge-duplicates,return=minimal"},
            data=json.dumps({"venue_id": venue_id, "baseline": existing,
                             "updated_at": _dt.now(timezone.utc).isoformat()}),
            timeout=8,
        )
        log.info("hermes_velez: sar_ndvi_coef persistido en fenologia_baselines venue=%s", venue_id)
    except Exception as exc:
        log.warning("hermes_velez: persistir sar_ndvi_coef (non-fatal): %s", exc)

    return resultado


def _ingest_saocom_file(
    hdf5_path: str,
    venue_id: str,
    cancha_id: str,
    bbox_wgs84: tuple[float, float, float, float],
) -> bool:
    """
    Procesa un HDF5 de SAOCOM (CONAE) e inserta las métricas en soil_metrics.

    Columnas estándar: sar_vv_db (VV L-band), sar_vh_db (HV L-band), fuente='SAOCOM_L-BAND'.
    Métricas Quad-Pol (H, alpha, A, eigenvalores, HH, VH) en la columna JSONB extras.

    Retorna True si el insert fue exitoso.
    """
    import json
    import os
    import requests as _req

    supa_url = os.environ.get("SUPABASE_URL", "").rstrip("/")
    supa_key = os.environ.get("SUPABASE_KEY", "")
    if not supa_url or not supa_key:
        log.warning("hermes_velez: _ingest_saocom_file — SUPABASE_URL/KEY no configurados.")
        return False

    try:
        from core.insar.saocom_processor import process_saocom_scene
    except ImportError as e:
        log.warning("hermes_velez: saocom_processor no disponible: %s", e)
        return False

    try:
        metrics = process_saocom_scene(hdf5_path, bbox_wgs84, cancha_id)
    except Exception as exc:
        log.warning("hermes_velez: _ingest_saocom_file process error: %s", exc)
        return False

    row = {
        "venue_id":     venue_id,
        "cancha_id":    cancha_id,
        "sar_vv_db":    metrics["vv_db"],
        "sar_vh_db":    metrics["hv_db"],   # HV ≈ VH (reciprocidad SAOCOM)
        "fuente":       "SAOCOM_L-BAND",
        "fecha_imagen": metrics["fecha_imagen"] or None,
        "extras": {
            "hh_db":       metrics["hh_db"],
            "vh_db":       metrics["vh_db"],
            "h_entropy":   metrics["h_entropy"],
            "alpha_deg":   metrics["alpha_deg"],
            "anisotropy":  metrics["anisotropy"],
            "lambda1":     metrics["lambda1"],
            "lambda2":     metrics["lambda2"],
            "lambda3":     metrics["lambda3"],
            "modo":        metrics["modo"],
            "n_pixels":    metrics["n_pixels"],
        },
    }

    hdrs = {
        "apikey":        supa_key,
        "Authorization": f"Bearer {supa_key}",
        "Content-Type":  "application/json",
        "Prefer":        "return=minimal",
    }
    try:
        r = _req.post(
            f"{supa_url}/rest/v1/soil_metrics",
            headers=hdrs,
            data=json.dumps(row, default=str),
            timeout=10,
        )
        ok = r.status_code in (200, 201, 204)
        if ok:
            log.info("hermes_velez: SAOCOM soil_metrics OK  cancha=%s  VV=%.1fdB  H=%.3f",
                     cancha_id, metrics["vv_db"], metrics["h_entropy"])
        else:
            log.warning("hermes_velez: SAOCOM soil_metrics HTTP %s: %s",
                        r.status_code, r.text[:200])
        return ok
    except Exception as exc:
        log.warning("hermes_velez: _ingest_saocom_file insert: %s", exc)
        return False


def _calibrar_saocom_quad_pol(cancha_id: str, venue_id: str) -> dict:
    """
    Calibración SAR→NDVI usando datos SAOCOM L-band Quad-Pol de soil_metrics.

    Filtra filas con fuente='SAOCOM_L-BAND', empareja con field_timeseries NDVI
    en ventana ±3 días, corre OLS idéntica a calibrar_fusion_sar().

    Persiste en fenologia_baselines.sar_ndvi_coef con fuente_sar='SAOCOM_L-BAND'.
    Si r² > 0.40 loguea que L-band supera el umbral de confianza.

    Retorna {"status": "OK", ...coefs...} o {"status": "INSUFICIENTE"/"SKIP"/...}.
    """
    import json
    import os
    import requests as _req
    from datetime import datetime as _dt, timezone

    supa_url = os.environ.get("SUPABASE_URL", "").rstrip("/")
    supa_key = os.environ.get("SUPABASE_KEY", "")
    if not supa_url or not supa_key:
        return {"status": "SKIP", "razon": "SUPABASE_URL/KEY no configurados."}

    hdrs = {"apikey": supa_key, "Authorization": f"Bearer {supa_key}"}
    MIN_PARES = 20

    # SAR: soil_metrics filtrado a SAOCOM_L-BAND
    r_soil = _req.get(
        f"{supa_url}/rest/v1/soil_metrics"
        f"?venue_id=eq.{venue_id}"
        f"&fuente=like.SAOCOM*"
        f"&select=fecha_imagen,sar_vv_db"
        f"&order=fecha_imagen.asc&limit=500",
        headers=hdrs, timeout=12,
    )
    if r_soil.status_code != 200:
        return {"status": "ERROR", "razon": f"soil_metrics HTTP {r_soil.status_code}"}

    sar_by_date: dict[str, float] = {}
    for row in r_soil.json():
        fecha = str(row.get("fecha_imagen") or "")[:10]
        val   = row.get("sar_vv_db")
        if fecha and val is not None:
            sar_by_date[fecha] = float(val)

    if len(sar_by_date) < MIN_PARES:
        log.info("hermes_velez: SAOCOM L-band insuficiente (%d fechas) — saltear", len(sar_by_date))
        return {"status": "INSUFICIENTE",
                "razon": f"Solo {len(sar_by_date)} fechas SAOCOM (mín {MIN_PARES})."}

    # NDVI: field_timeseries limpio (excluir bbox_legacy)
    r_ndvi = _req.get(
        f"{supa_url}/rest/v1/field_timeseries"
        f"?venue_id=eq.{venue_id}&select=fecha,ndvi,fuente&order=fecha.asc&limit=200",
        headers=hdrs, timeout=12,
    )
    if r_ndvi.status_code != 200:
        return {"status": "ERROR", "razon": f"field_timeseries HTTP {r_ndvi.status_code}"}

    ndvi_by_date: dict[str, float] = {}
    for row in r_ndvi.json():
        if str(row.get("fuente", "")).startswith("bbox_legacy"):
            continue
        fecha = str(row.get("fecha") or "")[:10]
        val   = row.get("ndvi")
        if fecha and val is not None:
            ndvi_by_date[fecha] = float(val)

    # Emparejamiento ±3 días
    pares: list[tuple[float, float]] = []
    for sar_f, sar_v in sorted(sar_by_date.items()):
        sar_dt  = _dt.strptime(sar_f, "%Y-%m-%d")
        best_v, best_d = None, 999
        for ndvi_f, ndvi_v in ndvi_by_date.items():
            d = abs((_dt.strptime(ndvi_f, "%Y-%m-%d") - sar_dt).days)
            if d <= 3 and d < best_d:
                best_v, best_d = ndvi_v, d
        if best_v is not None:
            pares.append((sar_v, best_v))

    if len(pares) < MIN_PARES:
        return {"status": "INSUFICIENTE",
                "razon": f"SAOCOM: {len(pares)} pares SAR+NDVI (mín {MIN_PARES})."}

    sar_arr  = np.array([p[0] for p in pares])
    ndvi_arr = np.array([p[1] for p in pares])
    resultado = calibrar_fusion_sar(cancha_id, sar_arr, ndvi_arr)
    resultado["fuente_ndvi"] = "field_timeseries"
    resultado["fuente_sar"]  = "SAOCOM_L-BAND"
    resultado["status"]      = "OK"

    r2 = resultado.get("r2", 0)
    if r2 >= 0.40:
        log.info("hermes_velez: SAOCOM L-band r2=%.3f supera umbral 0.40 — L-band preferido  cancha=%s",
                 r2, cancha_id)
    else:
        log.info("hermes_velez: SAOCOM L-band calibrado (n=%d) cancha=%s r2=%.3f (bajo esperado para pasto denso)",
                 len(pares), cancha_id, r2)

    # Persistir en fenologia_baselines
    try:
        hdrs_json = {**hdrs, "Content-Type": "application/json"}
        r_fb = _req.get(
            f"{supa_url}/rest/v1/fenologia_baselines?venue_id=eq.{venue_id}&select=baseline",
            headers=hdrs, timeout=8,
        )
        existing: dict = {}
        if r_fb.status_code == 200 and r_fb.json():
            existing = r_fb.json()[0].get("baseline") or {}
        existing["sar_ndvi_coef"] = {
            **{k: resultado[k] for k in ("slope", "intercept", "r2_ref", "n_pares") if k in resultado},
            "fuente_ndvi":   "field_timeseries",
            "fuente_sar":    "SAOCOM_L-BAND",
            "calibrado_utc": _dt.now(timezone.utc).isoformat(),
        }
        _req.post(
            f"{supa_url}/rest/v1/fenologia_baselines",
            headers={**hdrs_json, "Prefer": "resolution=merge-duplicates,return=minimal"},
            data=json.dumps({"venue_id": venue_id, "baseline": existing,
                             "updated_at": _dt.now(timezone.utc).isoformat()}),
            timeout=8,
        )
        log.info("hermes_velez: sar_ndvi_coef SAOCOM persistido en fenologia_baselines venue=%s", venue_id)
    except Exception as exc:
        log.warning("hermes_velez: persistir SAOCOM sar_ndvi_coef (non-fatal): %s", exc)

    return resultado


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
