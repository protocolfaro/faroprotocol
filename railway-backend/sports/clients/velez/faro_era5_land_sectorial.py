"""
faro_era5_land_sectorial.py — ET0, T°, RH y humedad suelo ERA5-Land por sector.

Pipeline:
  1. Descarga ERA5-Land horario (CDS API v2) para bbox de cada sector
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

# ── Configuración ─────────────────────────────────────────────────────────────

_SUPABASE_URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
_SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "")
_CDS_KEY      = os.environ.get("CDS_API_KEY", "")   # formato: "UID:APIkey"
# Nueva plataforma CDS (2024+): /api — la vieja /api/v2 devuelve 404 con keys nuevas
_CDS_URL      = "https://cds.climate.copernicus.eu/api"

# ── Cargar definiciones de sectores ───────────────────────────────────────────

_DEF_PATH = os.path.join(os.path.dirname(__file__), "sector_definitions.json")
with open(_DEF_PATH, encoding="utf-8") as _f:
    _SECTOR_CFG = json.load(_f)
SECTORS = _SECTOR_CFG["sectors"]


# ── Supabase helpers (REST directo — mismo patron que velez_supabase.py) ──────

def _hdrs() -> dict:
    return {
        "apikey":        _SUPABASE_KEY,
        "Authorization": f"Bearer {_SUPABASE_KEY}",
        "Content-Type":  "application/json",
        "Prefer":        "resolution=merge-duplicates",
    }


def _supabase_ok() -> bool:
    return bool(_SUPABASE_URL) and bool(_SUPABASE_KEY)


def _upsert_sector_row(record: dict) -> bool:
    """UPSERT en climate_metrics_sectorial (ON CONFLICT sector_id, fecha)."""
    if not _supabase_ok() or not _HAS_REQ:
        return False
    url = f"{_SUPABASE_URL}/rest/v1/climate_metrics_sectorial"
    try:
        r = _req.post(url, headers=_hdrs(), json=record, timeout=10)
        if r.status_code in (200, 201):
            return True
        log.warning("Supabase upsert HTTP %s: %s", r.status_code, r.text[:200])
        return False
    except Exception as exc:
        log.warning("Supabase upsert error: %s", exc)
        return False


def _fetch_latest_sector(sector_id: str, fecha: str) -> Optional[dict]:
    """Lee fila existente para (sector_id, fecha) — para idempotencia."""
    if not _supabase_ok() or not _HAS_REQ:
        return None
    url = (f"{_SUPABASE_URL}/rest/v1/climate_metrics_sectorial"
           f"?sector_id=eq.{sector_id}&fecha=eq.{fecha}&limit=1")
    try:
        r = _req.get(url, headers=_hdrs(), timeout=8)
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


# ── Descarga ERA5-Land horario ─────────────────────────────────────────────────

def _download_era5_land(sector_id: str, bbox: dict, date: datetime,
                        out_dir: str) -> Optional[str]:
    """
    Descarga ERA5-Land horario (24h) para el sector y fecha dados.

    Parametros CDS:
      - pev  : potential evapotranspiration (m, acumulado diario, negativo)
      - t2m  : 2m temperature (K)
      - d2m  : 2m dewpoint temperature (K) → usada para calcular RH
      - swvl1: volumetric soil water layer 1 (0-7cm, m3/m3)

    Returns:
      Path al .nc descargado, o None si falla.
    """
    if not _HAS_CDS or not _HAS_XR:
        log.error("cdsapi o xarray no disponible — skip descarga ERA5")
        return None
    if not _CDS_KEY:
        log.error("CDS_API_KEY no configurada en Railway Variables")
        return None

    lat_min = bbox["latitud_min"]
    lat_max = bbox["latitud_max"]
    lon_min = bbox["longitud_min"]
    lon_max = bbox["longitud_max"]

    # CDS area: [norte, oeste, sur, este] — orden específico del API
    area = [lat_max, lon_min, lat_min, lon_max]

    out_path = os.path.join(out_dir, f"era5_{sector_id}_{date.strftime('%Y%m%d')}.nc")

    try:
        c = cdsapi.Client(url=_CDS_URL, key=_CDS_KEY, quiet=True)
        c.retrieve(
            "reanalysis-era5-land",
            {
                "format":   "netcdf",
                "variable": [
                    "potential_evapotranspiration",  # pev  → m/dia
                    "2m_temperature",                # t2m  → K
                    "2m_dewpoint_temperature",       # d2m  → K (para RH)
                    "volumetric_soil_water_layer_1", # swvl1 → m3/m3 (0-7cm)
                ],
                "year":   date.strftime("%Y"),
                "month":  date.strftime("%m"),
                "day":    date.strftime("%d"),
                "time":   [f"{h:02d}:00" for h in range(24)],
                "area":   area,
            },
            out_path,
        )
        log.info("ERA5-Land descargado: %s (%.1f KB)", out_path,
                 os.path.getsize(out_path) / 1024)
        return out_path
    except Exception as exc:
        log.error("ERA5-Land descarga falló [%s]: %s", sector_id, exc)
        return None


# ── Agregacion espacial + temporal ────────────────────────────────────────────

def _aggregate(nc_path: str, sector_id: str) -> Optional[dict]:
    """
    Lee NetCDF ERA5-Land y calcula estadisticas diarias para el sector.

    Returns:
      {sector_id, fecha, et0_mm_dia, temp_2m_c, rh_pct, sm_0_7cm_pct, sm_0_7cm_m3m3}
    """
    if not _HAS_XR:
        return None

    try:
        ds = xr.open_dataset(nc_path)

        # ── ET₀: pev en m (negativo) → mm positivo ──────────────────────────
        # ERA5-Land pev es acumulado desde las 00:00 UTC del dia — el valor
        # total diario es el maximo absoluto del ultimo timestep.
        if "pev" in ds:
            pev_vals = ds["pev"].values.flatten()
            et0_m = float(np.nanmax(np.abs(pev_vals)))
        else:
            et0_m = 0.0
        et0_mm = round(et0_m * 1000, 2)

        # ── T2m: media diaria en °C ──────────────────────────────────────────
        if "t2m" in ds:
            t2m_k = float(np.nanmean(ds["t2m"].values))
            t2m_c = round(t2m_k - 273.15, 1)
        else:
            t2m_c = None

        # ── RH: calculada de T2m + Td2m, media diaria ───────────────────────
        if "d2m" in ds and "t2m" in ds:
            t_arr  = ds["t2m"].values.flatten()
            td_arr = ds["d2m"].values.flatten()
            rh_vals = [_rh_from_t_td(float(t), float(td))
                       for t, td in zip(t_arr, td_arr)
                       if not (math.isnan(t) or math.isnan(td))]
            rh_pct = round(sum(rh_vals) / len(rh_vals), 1) if rh_vals else None
        else:
            rh_pct = None

        # ── SM capa 1 (0-7cm): media diaria en m3/m3 y % ────────────────────
        if "swvl1" in ds:
            sm_m3m3 = round(float(np.nanmean(ds["swvl1"].values)), 4)
            sm_pct  = round(sm_m3m3 * 100, 1)
        else:
            sm_m3m3 = None
            sm_pct  = None

        ds.close()

        result = {
            "sector_id":     sector_id,
            "et0_mm_dia":    et0_mm,
            "temp_2m_c":     t2m_c,
            "rh_pct":        rh_pct,
            "sm_0_7cm_m3m3": sm_m3m3,
            "sm_0_7cm_pct":  sm_pct,
        }
        log.info(
            "ERA5 agg %s: ET0=%.2fmm T=%.1f°C RH=%.0f%% SM=%.1f%%",
            sector_id, et0_mm, t2m_c or 0, rh_pct or 0, sm_pct or 0,
        )
        return result

    except Exception as exc:
        log.error("ERA5 agregacion falló [%s]: %s", sector_id, exc)
        return None


# ── Proceso principal ─────────────────────────────────────────────────────────

def process_all_sectors(target_date: Optional[datetime] = None) -> dict:
    """
    Descarga y procesa todos los sectores para una fecha.

    ERA5-Land tiene ~5 dias de lag — por defecto usa hoy-7 dias
    para garantizar disponibilidad del dato.

    Returns:
      {status, date, sectors_ok, sectors_failed, details}
    """
    if target_date is None:
        # Lag conservador: 7 dias atras garantiza disponibilidad
        target_date = datetime.now(timezone.utc) - timedelta(days=7)

    fecha_str = target_date.strftime("%Y-%m-%d")

    log.info("══════════ ERA5-Land Sectorial — %s ══════════", fecha_str)

    results: list[dict] = []
    ok_count = fail_count = 0

    with tempfile.TemporaryDirectory(prefix="era5_") as tmp:
        for sector_id, sector_def in SECTORS.items():

            # Idempotencia: si ya existe fila, skip
            existing = _fetch_latest_sector(sector_id, fecha_str)
            if existing and existing.get("et0_mm_dia") is not None:
                log.info("Skip %s %s — ya existe en DB", sector_id, fecha_str)
                ok_count += 1
                results.append({"sector_id": sector_id, "status": "skip_existing"})
                continue

            # Descarga
            nc = _download_era5_land(sector_id, sector_def["bbox"], target_date, tmp)
            if nc is None:
                fail_count += 1
                results.append({"sector_id": sector_id, "status": "download_failed"})
                continue

            # Agregacion
            agg = _aggregate(nc, sector_id)
            if agg is None:
                fail_count += 1
                results.append({"sector_id": sector_id, "status": "aggregate_failed"})
                continue

            # Escritura Supabase
            record = {**agg, "fecha": fecha_str, "fuente": "ERA5-Land-hourly",
                      "created_at": datetime.now(timezone.utc).isoformat()}
            saved = _upsert_sector_row(record)
            if saved:
                ok_count += 1
                results.append({"sector_id": sector_id, "status": "ok", "data": agg})
            else:
                fail_count += 1
                results.append({"sector_id": sector_id, "status": "db_write_failed"})

    status = "ok" if fail_count == 0 else ("partial" if ok_count > 0 else "failed")
    log.info("Resultado: %d ok, %d fallidos — status=%s", ok_count, fail_count, status)

    return {
        "status":          status,
        "date":            fecha_str,
        "sectors_ok":      ok_count,
        "sectors_failed":  fail_count,
        "details":         results,
    }


# ── Lookup para prescripciones (lectura live desde Supabase) ─────────────────

def get_sector_climate(sector_id: str, fecha: Optional[str] = None) -> Optional[dict]:
    """
    Lee datos ERA5-Land del sector mas reciente (ultimos 10 dias).
    Usado por faro_prescription.py en tiempo real.

    Returns: dict con et0_mm_dia, temp_2m_c, rh_pct, sm_0_7cm_pct o None.
    """
    if not _supabase_ok() or not _HAS_REQ:
        return None

    since = (datetime.now(timezone.utc) - timedelta(days=10)).strftime("%Y-%m-%d")
    url = (f"{_SUPABASE_URL}/rest/v1/climate_metrics_sectorial"
           f"?sector_id=eq.{sector_id}&fecha=gte.{since}"
           f"&order=fecha.desc&limit=1")
    if fecha:
        url = (f"{_SUPABASE_URL}/rest/v1/climate_metrics_sectorial"
               f"?sector_id=eq.{sector_id}&fecha=eq.{fecha}&limit=1")
    try:
        r = _req.get(url, headers=_hdrs(), timeout=6)
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
    parser.add_argument("--date",  type=str, help="ISO date YYYY-MM-DD (default: hoy-7d)")
    parser.add_argument("--sector", type=str, help="Procesar solo este sector_id")
    args = parser.parse_args()

    target = datetime.fromisoformat(args.date) if args.date else None
    result = process_all_sectors(target)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    sys.exit(0 if result["status"] in ("ok", "partial") else 1)
