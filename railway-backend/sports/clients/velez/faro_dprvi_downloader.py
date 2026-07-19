"""
faro_dprvi_downloader.py — Computa DpRVIc desde SAR ya en Supabase. SIN GCS.

Pipeline (zero-cost, sin dependencias externas):
  1. Lee VV/VH por cancha desde soil_metrics (Supabase REST API — anon key)
  2. Promedia VV/VH por sector DpRVIc (amalfitani_central, vo_bloque_a/b/c/d)
  3. DpRVIc = (VV_lin - VH_lin) / (VV_lin + VH_lin)  ∈ [0,1]
  4. UPSERT vía Supabase REST API (service_role key)

Variables de entorno requeridas:
  SUPABASE_URL              — https://xljxpzudgwhbzcnrvylo.supabase.co
  SUPABASE_KEY              — anon key (lectura soil_metrics)
  SUPABASE_SERVICE_ROLE_KEY — service role key (escritura dprvi_metrics)

Ejecutar: 13:00 UTC diariamente (datos SAR disponibles desde 09:00 UTC refresh)
"""
from __future__ import annotations

import json
import logging
import os
import sys
from datetime import date, timedelta

import requests

log = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

# ── Config ────────────────────────────────────────────────────────────────────

SUPA_URL     = os.environ.get("SUPABASE_URL", "https://xljxpzudgwhbzcnrvylo.supabase.co").rstrip("/")
SUPA_KEY     = os.environ.get("SUPABASE_KEY", "")
SUPA_SVC_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")

# DpRVIc formula: (VV_lin - VH_lin) / (VV_lin + VH_lin)
# Alto DpRVIc → suelo seco / baja vegetación
# Bajo DpRVIc → vegetación densa / húmeda

# Sector DpRVIc → (venue_id, [cancha_ids en ese sector])
SECTOR_CANCHAS: dict[str, tuple[str, list[str]]] = {
    "amalfitani_central": ("amalfitani",    []),           # venue-level, sin cancha_id específico
    "vo_bloque_a":        ("villa_olimpica", ["1fa", "2fa", "3fa"]),
    "vo_bloque_b":        ("villa_olimpica", ["4fa", "5fa", "6fa"]),
    "vo_bloque_c":        ("villa_olimpica", ["7fa", "8fa", "9fa", "10fa"]),
    "vo_bloque_d":        ("villa_olimpica", ["1fp", "2fp"]),
}

# DDL para crear la tabla manualmente en Supabase SQL Editor (una sola vez):
# CREATE TABLE IF NOT EXISTS public.dprvi_metrics (
#     id                   BIGSERIAL PRIMARY KEY,
#     cancha_id            VARCHAR(32)  NOT NULL,
#     fecha                DATE         NOT NULL,
#     dprvi_value          FLOAT,
#     dprvi_confidence_pct INT,
#     vh_db                FLOAT,
#     vv_db                FLOAT,
#     vh_vv_ratio          FLOAT,
#     created_at           TIMESTAMP DEFAULT NOW(),
#     CONSTRAINT unique_cancha_fecha_dprvi UNIQUE(cancha_id, fecha)
# );
# CREATE INDEX IF NOT EXISTS idx_dprvi_cancha_fecha ON public.dprvi_metrics(cancha_id, fecha DESC);
"""


# ── SAR data desde Supabase ───────────────────────────────────────────────────

def _sb_headers() -> dict:
    return {"apikey": SUPA_KEY, "Authorization": f"Bearer {SUPA_KEY}"}


def _get_sar(venue_id: str, dias: int = 14) -> dict[str, dict]:
    """Lee VV/VH por cancha desde soil_metrics. Retorna {cancha_id: row}."""
    since = (date.today() - timedelta(days=dias)).isoformat()
    url = (f"{SUPA_URL}/rest/v1/soil_metrics"
           f"?venue_id=eq.{venue_id}"
           f"&created_at=gte.{since}"
           f"&order=created_at.desc&limit=300"
           f"&select=cancha_id,sar_vv_db,sar_vh_db,fecha_imagen")
    try:
        r = requests.get(url, headers=_sb_headers(), timeout=15)
        if r.status_code != 200:
            log.warning("soil_metrics %s HTTP %s", venue_id, r.status_code)
            return {}
        latest: dict[str, dict] = {}
        for row in r.json():
            cid = row.get("cancha_id") or venue_id   # Amalfitani puede no tener cancha_id
            if cid not in latest and row.get("sar_vv_db") is not None and row.get("sar_vh_db") is not None:
                latest[cid] = row
        return latest
    except Exception as exc:
        log.warning("get_sar %s: %s", venue_id, exc)
        return {}


# ── DpRVIc computation ────────────────────────────────────────────────────────

def _dprvi(vv_db: float, vh_db: float) -> tuple[float, float, float]:
    """
    Returns (dprvi_value, vh_vv_ratio, confidence_pct).
    Formula: DpRVIc = (VV_lin - VH_lin) / (VV_lin + VH_lin)
    """
    vv_lin = 10.0 ** (vv_db / 10.0)
    vh_lin = 10.0 ** (vh_db / 10.0)
    denom = vv_lin + vh_lin
    if denom < 1e-12:
        return 0.5, 1.0, 0
    dprvi = max(0.0, min(1.0, (vv_lin - vh_lin) / denom))
    ratio = round(vh_lin / vv_lin, 4)
    return round(dprvi, 4), ratio, 82  # 82% confianza base desde SAR propio


def _sector_vv_vh(sector_id: str, sar_cache: dict[str, dict[str, dict]]) -> tuple[float, float, str] | None:
    """
    Promedia VV/VH de las canchas del sector.
    Retorna (avg_vv_db, avg_vh_db, fecha) o None si no hay datos.
    """
    venue_id, cancha_ids = SECTOR_CANCHAS[sector_id]
    sar = sar_cache.get(venue_id, {})

    if not cancha_ids:
        # Amalfitani: tomar el dato del venue directamente
        row = sar.get("amalfitani") or next(iter(sar.values()), None)
        if row:
            return float(row["sar_vv_db"]), float(row["sar_vh_db"]), str(row.get("fecha_imagen", ""))[:10]
        return None

    vv_vals, vh_vals, fechas = [], [], []
    for cid in cancha_ids:
        row = sar.get(cid)
        if row and row.get("sar_vv_db") is not None and row.get("sar_vh_db") is not None:
            vv_vals.append(float(row["sar_vv_db"]))
            vh_vals.append(float(row["sar_vh_db"]))
            fechas.append(str(row.get("fecha_imagen", ""))[:10])

    if not vv_vals:
        return None

    avg_vv = sum(vv_vals) / len(vv_vals)
    avg_vh = sum(vh_vals) / len(vh_vals)
    fecha  = max(fechas) if fechas else str(date.today())
    return round(avg_vv, 3), round(avg_vh, 3), fecha


# ── Supabase write via REST API ───────────────────────────────────────────────

def _upsert(rows: list[dict]) -> bool:
    if not SUPA_SVC_KEY:
        log.error("SUPABASE_SERVICE_ROLE_KEY no configurado")
        return False
    url  = f"{SUPA_URL}/rest/v1/dprvi_metrics"
    hdrs = {
        "apikey":        SUPA_SVC_KEY,
        "Authorization": f"Bearer {SUPA_SVC_KEY}",
        "Content-Type":  "application/json",
        "Prefer":        "resolution=merge-duplicates,return=minimal",
    }
    try:
        r = requests.post(url, headers=hdrs, data=json.dumps(rows), timeout=15)
        if r.status_code in (200, 201, 204):
            log.info("dprvi_metrics upsert OK — %d filas", len(rows))
            return True
        log.error("dprvi_metrics upsert HTTP %s: %s", r.status_code, r.text[:200])
        return False
    except Exception as exc:
        log.error("dprvi_metrics upsert: %s", exc)
        return False


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> int:
    log.info("=== faro_dprvi_downloader START (SAR-native, no GCS) ===")

    if not SUPA_KEY:
        log.error("SUPABASE_KEY no configurado")
        return 1
    if not SUPA_SVC_KEY:
        log.error("SUPABASE_SERVICE_ROLE_KEY no configurado")
        return 1

    # 1. Fetch SAR data por venue (2 queries)
    sar_cache: dict[str, dict[str, dict]] = {}
    for venue_id in ("amalfitani", "villa_olimpica"):
        sar_cache[venue_id] = _get_sar(venue_id, dias=14)
        log.info("SAR %s: %d canchas con datos", venue_id, len(sar_cache[venue_id]))

    # 2. Computar DpRVIc por sector
    rows: list[dict] = []
    today = str(date.today())

    for sector_id in SECTOR_CANCHAS:
        result = _sector_vv_vh(sector_id, sar_cache)
        if result is None:
            log.warning("Sector %s — sin datos SAR, skip", sector_id)
            continue
        vv_db, vh_db, fecha_sar = result
        dprvi_val, ratio, conf = _dprvi(vv_db, vh_db)

        row = {
            "cancha_id":             sector_id,
            "fecha":                 today,
            "dprvi_value":           dprvi_val,
            "dprvi_confidence_pct":  conf,
            "vh_db":                 vh_db,
            "vv_db":                 vv_db,
            "vh_vv_ratio":           ratio,
        }
        rows.append(row)
        log.info("  %s → DpRVIc=%.4f (VV=%.1f dB, VH=%.1f dB, conf=%d%%)",
                 sector_id, dprvi_val, vv_db, vh_db, conf)

    if not rows:
        log.error("Sin sectores con datos SAR — abortando")
        return 1

    # 3. Upsert en dprvi_metrics
    ok = _upsert(rows)
    if not ok:
        return 1

    log.info("=== faro_dprvi_downloader OK — %d sectores ===", len(rows))
    return 0


if __name__ == "__main__":
    sys.exit(main())
