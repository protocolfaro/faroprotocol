"""
faro_era5_land_sectorial.py — ET0, T°, RH y humedad suelo ERA5-Land por sector.

Pipeline:
  1. Descarga ERA5-Land horario (CDS API) para bbox de cada sector
  2. Agrega a diario (promedio espacial + temporal)
  3. Escribe en Supabase climate_metrics_sectorial

Notas técnicas:
  - ERA5-Land lag ~5 dias → scheduler usa date = hoy - 7 dias para garantizar disponibilidad
  - PET en ERA5-Land: m/dia acumulado, convencion negativa → abs(pev) * 1000 = mm ET0
  - RH no es variable directa: se calcula de T2m y Td2m via formula de Magnus
  - SM capa 1 (0-7cm): variable ERA5 'swvl1', unidad m3/m3
  - Credenciales CDS: una sola API key del perfil en cds.climate.copernicus.eu
    (NO es username+password — es el UID:APIkey que aparece en tu perfil)

Variables de entorno requeridas:
  CDS_API_KEY  — formato "UID:APIkey" segun CDS profile (ej: "12345:abc123...")
  SUPABASE_URL — https://xljxpzudgwhbzcnrvylo.supabase.co
  SUPABASE_KEY — service role key o anon key con permisos INSERT
"""
from __future__ import annotations

import json
import logging
import math
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from typing import Optional

import numpy as np

log = logging.getLogger(__name__)

# ── Imports con fallback informativo ──────────────────────────────────────────

try:
    import cdsapi
    _HAS_CDS = True
except ImportError:
    _HAS_CDS = False
    log.warning("cdsapi no instalado — ERA5-Land desactivado. pip install cdsapi")

try:
    import xarray as xr
    _HAS_XR = True
except ImportError:
    _HAS_XR = False
    log.warning("xarray no instalado — ERA5-Land desactivado. pip install xarray netCDF4")

try:
    import requests as _req
    _HAS_REQ = True
except ImportError:
    _HAS_REQ = False

try:
    from tenacity import (
        retry,
        stop_after_attempt,
        wait_exponential,
        wait_fixed,
        retry_if_exception_type,
        before_sleep_log,
        RetryError,
    )
    _HAS_TENACITY = True
except ImportError:
    _HAS_TENACITY = False
    log.warning("tenacity no instalado — sin retry automático. pip install tenacity")

# ── Configuración ─────────────────────────────────────────────────────────────

_SUPABASE_URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
_SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "")
_CDS_KEY      = os.environ.get("CDS_API_KEY", "")   # formato: "UID:APIkey"
# Nueva plataforma CDS (2024+): /api — la vieja /api/v2 devuelve 404 con keys nuevas
_CDS_URL      = "https://cds.climate.copernicus.eu/api"

# Thresholds de datos de salida para detectar valores anómalos
_ET0_MAX_MM   = 15.0    # ET0 > 15 mm/día es sospechoso para Buenos Aires
_RH_MIN_PCT   = 10.0    # RH < 10% es anómalo para el clima local
_SM_MAX_M3M3  = 0.60    # Saturación máxima física del suelo

# ── Cargar definiciones de sectores ───────────────────────────────────────────

_DEF_PATH = os.path.join(os.path.dirname(__file__), "sector_definitions.json")
try:
    with open(_DEF_PATH, encoding="utf-8") as _f:
        _SECTOR_CFG = json.load(_f)
    SECTORS = _SECTOR_CFG["sectors"]
except FileNotFoundError:
    log.error("sector_definitions.json no encontrado en %s", _DEF_PATH)
    SECTORS = {}
except (json.JSONDecodeError, KeyError) as exc:
    log.error("sector_definitions.json inválido: %s", exc)
    SECTORS = {}


# ── Supabase helpers (REST directo) ───────────────────────────────────────────

def _hdrs() -> dict:
    return {
        "apikey":        _SUPABASE_KEY,
        "Authorization": f"Bearer {_SUPABASE_KEY}",
        "Content-Type":  "application/json",
        "Prefer":        "resolution=merge-duplicates",
    }


def _supabase_ok() -> bool:
    return bool(_SUPABASE_URL) and bool(_SUPABASE_KEY)


def _upsert_sector_row_raw(record: dict) -> bool:
    """UPSERT en climate_metrics_sectorial — un intento, sin retry."""
    if not _supabase_ok() or not _HAS_REQ:
        return False
    url = f"{_SUPABASE_URL}/rest/v1/climate_metrics_sectorial"
    r = _req.post(url, headers=_hdrs(), json=record, timeout=15)
    if r.status_code in (200, 201):
        return True
    log.warning("Supabase upsert HTTP %s: %s", r.status_code, r.text[:200])
    # Raise para que tenacity pueda reintentar
    raise OSError(f"Supabase HTTP {r.status_code}: {r.text[:100]}")


def _upsert_sector_row(record: dict) -> bool:
    """UPSERT con retry 3x / espera fija 8s entre intentos."""
    if not _HAS_TENACITY:
        return _upsert_sector_row_raw(record)

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_fixed(8),
        retry=retry_if_exception_type((OSError, _req.exceptions.RequestException if _HAS_REQ else OSError)),
        before_sleep=before_sleep_log(log, logging.WARNING),
        reraise=False,
    )
    def _with_retry() -> bool:
        return _upsert_sector_row_raw(record)

    try:
        return _with_retry()
    except (RetryError, Exception) as exc:
        log.error("Supabase upsert falló tras 3 intentos: %s", exc)
        return False


def _fetch_latest_sector(sector_id: str, fecha: str) -> Optional[dict]:
    """Lee fila existente para (sector_id, fecha) — para idempotencia."""
    if not _supabase_ok() or not _HAS_REQ:
        return None
    url = (f"{_SUPABASE_URL}/rest/v1/climate_metrics_sectorial"
           f"?sector_id=eq.{sector_id}&fecha=eq.{fecha}&limit=1")
    try:
        r = _req.get(url, headers=_hdrs(), timeout=10)
        data = r.json()
        return data[0] if data else None
    except Exception:
        return None


# ── Cálculo de RH desde T2m y Td2m (Magnus) ──────────────────────────────────

def _rh_from_t_td(t_k: float, td_k: float) -> float:
    """
    Humedad relativa (%) desde temperatura T y punto de rocio Td en Kelvin.
    Formula de Magnus / Tetens.
    """
    t  = t_k  - 273.15
    td = td_k - 273.15
    es = 6.112 * math.exp(17.67 * t  / (t  + 243.5))
    ea = 6.112 * math.exp(17.67 * td / (td + 243.5))
    return round(min(100.0, max(0.0, (ea / es) * 100)), 1)


# ── Descarga ERA5-Land con retry ─────────────────────────────────────────────

def _download_era5_land_raw(
    sector_id: str, bbox: dict, date: datetime, out_path: str
) -> bool:
    """
    Un intento de descarga. Lanza excepción si falla (para que retry la capture).
    Returns True si descarga fue exitosa.
    """
    if not _HAS_CDS or not _HAS_XR:
        raise RuntimeError("cdsapi o xarray no disponible")
    if not _CDS_KEY:
        raise ValueError("CDS_API_KEY no configurada en variables de entorno")
    if ":" not in _CDS_KEY:
        raise ValueError(f"CDS_API_KEY formato incorrecto — debe ser 'UID:APIkey', recibido: {_CDS_KEY[:8]}...")

    lat_min = bbox["latitud_min"]
    lat_max = bbox["latitud_max"]
    lon_min = bbox["longitud_min"]
    lon_max = bbox["longitud_max"]
    area = [lat_max, lon_min, lat_min, lon_max]  # [N, O, S, E]

    # cdsapi Client con retry interno (sleep_max=120s entre polls)
    c = cdsapi.Client(url=_CDS_URL, key=_CDS_KEY, quiet=True,
                      retry_max=3, sleep_max=120, timeout=300)
    c.retrieve(
        "reanalysis-era5-land",
        {
            "format":   "netcdf",
            "variable": [
                "potential_evapotranspiration",
                "2m_temperature",
                "2m_dewpoint_temperature",
                "volumetric_soil_water_layer_1",
            ],
            "year":   date.strftime("%Y"),
            "month":  date.strftime("%m"),
            "day":    date.strftime("%d"),
            "time":   [f"{h:02d}:00" for h in range(24)],
            "area":   area,
        },
        out_path,
    )

    if not os.path.exists(out_path) or os.path.getsize(out_path) < 1024:
        raise RuntimeError(f"Archivo descargado demasiado pequeño o inexistente: {out_path}")

    log.info("ERA5-Land descargado: %s (%.1f KB)", out_path,
             os.path.getsize(out_path) / 1024)
    return True


def _download_era5_land(
    sector_id: str, bbox: dict, date: datetime, out_dir: str
) -> Optional[str]:
    """
    Descarga ERA5-Land con retry exponencial.

    Estrategia:
      - 3 intentos en total
      - Espera 60s → 120s → 240s entre reintentos (exponencial, cap 300s)
      - No-auth errors (ValueError): no se reintenta (fallan rápido)
      - Timeout / red / CDS queue: se reintenta
    """
    out_path = os.path.join(out_dir, f"era5_{sector_id}_{date.strftime('%Y%m%d')}.nc")

    # Errores de configuración (key inválida) — no reintentar
    if not _HAS_CDS or not _HAS_XR:
        log.error("cdsapi o xarray no disponible — skip descarga ERA5")
        return None
    if not _CDS_KEY:
        log.error("CDS_API_KEY no configurada — abortar sector %s", sector_id)
        return None

    if not _HAS_TENACITY:
        # Sin tenacity: un solo intento
        try:
            ok = _download_era5_land_raw(sector_id, bbox, date, out_path)
            return out_path if ok else None
        except Exception as exc:
            log.error("ERA5-Land descarga falló [%s]: %s", sector_id, exc)
            return None

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=60, min=60, max=300),
        retry=retry_if_exception_type((RuntimeError, OSError, Exception)),
        before_sleep=before_sleep_log(log, logging.WARNING),
        reraise=False,
    )
    def _attempt() -> bool:
        # Si el archivo parcial existe de un intento anterior, borrarlo
        if os.path.exists(out_path):
            try:
                os.remove(out_path)
            except OSError:
                pass
        return _download_era5_land_raw(sector_id, bbox, date, out_path)

    try:
        ok = _attempt()
        return out_path if ok else None
    except ValueError as exc:
        # Error de configuración — no reintentar, falla rápida
        log.error("ERA5-Land configuración inválida [%s]: %s", sector_id, exc)
        return None
    except (RetryError, Exception) as exc:
        log.error("ERA5-Land descarga falló tras 3 intentos [%s]: %s", sector_id, exc)
        return None


# ── Agregacion espacial + temporal ────────────────────────────────────────────

def _validate_output(result: dict, sector_id: str) -> dict:
    """
    Detecta valores anómalos en el resultado agregado y los loguea como warnings.
    No descarta el dato — solo advierte. Returns result unchanged.
    """
    et0 = result.get("et0_mm_dia")
    rh  = result.get("rh_pct")
    sm  = result.get("sm_0_7cm_m3m3")

    if et0 is not None and et0 > _ET0_MAX_MM:
        log.warning("[%s] ET0=%.2f mm/día fuera de rango esperado (>%s) — verificar datos",
                    sector_id, et0, _ET0_MAX_MM)
    if rh is not None and rh < _RH_MIN_PCT:
        log.warning("[%s] RH=%.1f%% anormalmente baja (<%.0f%%) — posible error CDS",
                    sector_id, rh, _RH_MIN_PCT)
    if sm is not None and sm > _SM_MAX_M3M3:
        log.warning("[%s] SM=%.4f m³/m³ supera saturación física (%.2f) — verificar",
                    sector_id, sm, _SM_MAX_M3M3)

    return result


def _aggregate(nc_path: str, sector_id: str) -> Optional[dict]:
    """
    Lee NetCDF ERA5-Land y calcula estadísticas diarias para el sector.

    Returns:
      {sector_id, et0_mm_dia, temp_2m_c, rh_pct, sm_0_7cm_pct, sm_0_7cm_m3m3}
      None si el archivo es inválido o faltan todas las variables.
    """
    if not _HAS_XR:
        return None

    try:
        ds = xr.open_dataset(nc_path)

        # Verificar que el dataset tiene al menos una variable esperada
        expected_vars = {"pev", "t2m", "d2m", "swvl1"}
        found_vars = set(ds.data_vars) | set(ds.coords)
        has_any = bool(expected_vars & found_vars)
        if not has_any:
            log.error("[%s] NetCDF no contiene variables ERA5-Land esperadas. Vars: %s",
                      sector_id, list(ds.data_vars)[:5])
            ds.close()
            return None

        # ── ET₀: pev en m (negativo) → mm positivo ──────────────────────────
        if "pev" in ds:
            pev_vals = ds["pev"].values.flatten()
            valid_pev = pev_vals[~np.isnan(pev_vals)]
            et0_m = float(np.max(np.abs(valid_pev))) if len(valid_pev) > 0 else 0.0
        else:
            et0_m = 0.0
            log.debug("[%s] 'pev' no en dataset — ET0=0", sector_id)
        et0_mm = round(et0_m * 1000, 2)

        # ── T2m: media diaria en °C ──────────────────────────────────────────
        if "t2m" in ds:
            t2m_vals = ds["t2m"].values.flatten()
            valid_t = t2m_vals[~np.isnan(t2m_vals)]
            t2m_k = float(np.mean(valid_t)) if len(valid_t) > 0 else None
            t2m_c = round(t2m_k - 273.15, 1) if t2m_k is not None else None
        else:
            t2m_c = None
            log.debug("[%s] 't2m' no en dataset", sector_id)

        # ── RH: calculada de T2m + Td2m, media diaria ───────────────────────
        if "d2m" in ds and "t2m" in ds:
            t_arr  = ds["t2m"].values.flatten()
            td_arr = ds["d2m"].values.flatten()
            rh_vals = [
                _rh_from_t_td(float(t), float(td))
                for t, td in zip(t_arr, td_arr)
                if not (math.isnan(float(t)) or math.isnan(float(td)))
            ]
            rh_pct = round(sum(rh_vals) / len(rh_vals), 1) if rh_vals else None
        else:
            rh_pct = None
            log.debug("[%s] 'd2m'/'t2m' no en dataset — RH no calculable", sector_id)

        # ── SM capa 1 (0-7cm): media diaria en m3/m3 y % ────────────────────
        if "swvl1" in ds:
            sm_vals = ds["swvl1"].values.flatten()
            valid_sm = sm_vals[~np.isnan(sm_vals)]
            sm_m3m3 = round(float(np.mean(valid_sm)), 4) if len(valid_sm) > 0 else None
            sm_pct  = round(sm_m3m3 * 100, 1) if sm_m3m3 is not None else None
        else:
            sm_m3m3 = None
            sm_pct  = None
            log.debug("[%s] 'swvl1' no en dataset — SM no disponible", sector_id)

        ds.close()

        result = {
            "sector_id":     sector_id,
            "et0_mm_dia":    et0_mm,
            "temp_2m_c":     t2m_c,
            "rh_pct":        rh_pct,
            "sm_0_7cm_m3m3": sm_m3m3,
            "sm_0_7cm_pct":  sm_pct,
        }
        _validate_output(result, sector_id)

        log.info(
            "ERA5 agg %s: ET0=%.2fmm T=%s°C RH=%s%% SM=%s%%",
            sector_id, et0_mm,
            f"{t2m_c:.1f}" if t2m_c is not None else "N/A",
            f"{rh_pct:.0f}" if rh_pct is not None else "N/A",
            f"{sm_pct:.1f}" if sm_pct is not None else "N/A",
        )
        return result

    except Exception as exc:
        log.error("ERA5 agregacion falló [%s]: %s", sector_id, exc)
        return None


# ── Pre-flight validation ─────────────────────────────────────────────────────

def preflight_check() -> list[str]:
    """
    Verifica pre-condiciones antes de iniciar el pipeline.
    Returns lista de errores (vacía = OK).
    """
    errors: list[str] = []

    if not _CDS_KEY:
        errors.append("CDS_API_KEY no configurada en variables de entorno")
    elif ":" not in _CDS_KEY:
        errors.append(f"CDS_API_KEY formato incorrecto — debe ser 'UID:APIkey'")

    if not _SUPABASE_URL:
        errors.append("SUPABASE_URL no configurada")
    if not _SUPABASE_KEY:
        errors.append("SUPABASE_KEY no configurada")

    if not _HAS_CDS:
        errors.append("cdsapi no instalado — pip install cdsapi")
    if not _HAS_XR:
        errors.append("xarray no instalado — pip install xarray netCDF4")
    if not _HAS_REQ:
        errors.append("requests no instalado — pip install requests")

    if not SECTORS:
        errors.append(f"sector_definitions.json vacío o no encontrado: {_DEF_PATH}")

    return errors


# ── Proceso principal ─────────────────────────────────────────────────────────

def process_all_sectors(
    target_date: Optional[datetime] = None,
    only_sector: Optional[str] = None,
) -> dict:
    """
    Descarga y procesa todos los sectores para una fecha.

    ERA5-Land tiene ~5 días de lag — por defecto usa hoy-7 días
    para garantizar disponibilidad del dato.

    Args:
        target_date: fecha a procesar (default: hoy-7d)
        only_sector: si se especifica, procesa solo este sector_id

    Returns:
        {status, date, sectors_ok, sectors_failed, details}
    """
    # Pre-flight
    errors = preflight_check()
    if errors:
        for e in errors:
            log.error("PRE-FLIGHT FAIL: %s", e)
        return {
            "status":         "failed",
            "date":           None,
            "sectors_ok":     0,
            "sectors_failed": 0,
            "details":        [],
            "preflight_errors": errors,
        }

    if target_date is None:
        target_date = datetime.now(timezone.utc) - timedelta(days=7)

    fecha_str = target_date.strftime("%Y-%m-%d")
    log.info("══════════ ERA5-Land Sectorial — %s ══════════", fecha_str)

    sectors_to_run = SECTORS
    if only_sector:
        if only_sector not in SECTORS:
            log.error("Sector '%s' no existe en sector_definitions.json. Disponibles: %s",
                      only_sector, list(SECTORS.keys()))
            return {"status": "failed", "date": fecha_str, "sectors_ok": 0,
                    "sectors_failed": 1, "details": [],
                    "preflight_errors": [f"sector '{only_sector}' not found"]}
        sectors_to_run = {only_sector: SECTORS[only_sector]}

    results: list[dict] = []
    ok_count = fail_count = 0

    with tempfile.TemporaryDirectory(prefix="era5_") as tmp:
        for sector_id, sector_def in sectors_to_run.items():

            # Idempotencia: si ya existe fila con ET0 no-null, skip
            existing = _fetch_latest_sector(sector_id, fecha_str)
            if existing and existing.get("et0_mm_dia") is not None:
                log.info("Skip %s %s — ya existe en DB (ET0=%.2f mm)",
                         sector_id, fecha_str, existing["et0_mm_dia"])
                ok_count += 1
                results.append({"sector_id": sector_id, "status": "skip_existing",
                                 "fecha": fecha_str})
                continue

            # Descarga (con retry interno)
            nc = _download_era5_land(sector_id, sector_def["bbox"], target_date, tmp)
            if nc is None:
                fail_count += 1
                results.append({"sector_id": sector_id, "status": "download_failed",
                                 "fecha": fecha_str})
                log.warning("Sector %s: descarga fallida — continuando con siguiente sector",
                            sector_id)
                continue  # No fail-fast — continúa con el siguiente sector

            # Agregación
            agg = _aggregate(nc, sector_id)
            if agg is None:
                fail_count += 1
                results.append({"sector_id": sector_id, "status": "aggregate_failed",
                                 "fecha": fecha_str})
                continue

            # Escritura Supabase (con retry interno)
            record = {
                **agg,
                "fecha":      fecha_str,
                "fuente":     "ERA5-Land-hourly",
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
            saved = _upsert_sector_row(record)
            if saved:
                ok_count += 1
                results.append({"sector_id": sector_id, "status": "ok",
                                 "fecha": fecha_str, "data": agg})
            else:
                fail_count += 1
                results.append({"sector_id": sector_id, "status": "db_write_failed",
                                 "fecha": fecha_str})

    status = "ok" if fail_count == 0 else ("partial" if ok_count > 0 else "failed")
    log.info("Resultado: %d ok, %d fallidos — status=%s", ok_count, fail_count, status)

    return {
        "status":         status,
        "date":           fecha_str,
        "sectors_ok":     ok_count,
        "sectors_failed": fail_count,
        "details":        results,
    }


# ── Lookup para prescripciones (lectura live desde Supabase) ─────────────────

def get_sector_climate(sector_id: str, fecha: Optional[str] = None) -> Optional[dict]:
    """
    Lee datos ERA5-Land del sector más reciente (últimos 10 días).
    Usado por faro_prescription.py en tiempo real.

    Returns: dict con et0_mm_dia, temp_2m_c, rh_pct, sm_0_7cm_pct o None.
    """
    if not _supabase_ok() or not _HAS_REQ:
        return None

    since = (datetime.now(timezone.utc) - timedelta(days=10)).strftime("%Y-%m-%d")

    if fecha:
        url = (f"{_SUPABASE_URL}/rest/v1/climate_metrics_sectorial"
               f"?sector_id=eq.{sector_id}&fecha=eq.{fecha}&limit=1")
    else:
        url = (f"{_SUPABASE_URL}/rest/v1/climate_metrics_sectorial"
               f"?sector_id=eq.{sector_id}&fecha=gte.{since}"
               f"&order=fecha.desc&limit=1")
    try:
        r = _req.get(url, headers=_hdrs(), timeout=8)
        data = r.json()
        return data[0] if data else None
    except Exception as exc:
        log.debug("ERA5 lookup %s: %s", sector_id, exc)
        return None


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [ERA5] %(levelname)s %(message)s",
    )
    parser = argparse.ArgumentParser(description="ERA5-Land Sectorial — Velez")
    parser.add_argument("--date",   type=str, help="ISO date YYYY-MM-DD (default: hoy-7d)")
    parser.add_argument("--sector", type=str, help="Procesar solo este sector_id")
    args = parser.parse_args()

    target = datetime.fromisoformat(args.date) if args.date else None
    result = process_all_sectors(target, only_sector=args.sector or None)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    sys.exit(0 if result["status"] in ("ok", "partial") else 1)
