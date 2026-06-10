"""velez_supabase.py — Supabase REST client for Vélez satellite pipeline.

Same SUPABASE_URL + SUPABASE_KEY env vars as dale_play_storage.py.
Never raises — failures are logged and silently swallowed.

Tables (SQL en migrations/velez_live_tables.sql):
  pipeline_runs        — one row per satellite cycle (observability / audit)
  velez_canchas        — per-cancha NDVI/score/sem/tipo_cesped, updated by satellite_pipeline
  velez_sectores       — per-sector score/sem/detalle/insar, updated by pipeline + insar_hyp3
  velez_weather_live   — single row (id='current') with weather_live JSONB, updated daily
  velez_intervenciones — operaciones de campo logueadas por Roger (riego, corte, etc.)
  soil_metrics         — SAR backscatter + Van Genuchten por cancha/sector (fuente: insar_hyp3 + dale_play_sar)
  vegetation_metrics   — índices ópticos por cancha (fuente: satellite_pipeline)
  climate_metrics      — ET0/déficit/GDD/Smith-Kerns por venue (fuente: data_refresh)

Migration notes:
  ALTER TABLE velez_canchas ADD COLUMN IF NOT EXISTS tipo_cesped TEXT DEFAULT 'natural';
  CREATE TABLE IF NOT EXISTS velez_intervenciones (
      id BIGSERIAL PRIMARY KEY,
      cancha_id TEXT NOT NULL,
      tipo TEXT NOT NULL,  -- riego|corte|fertilizante|fungicida|resiembra|aireacion
      detalle TEXT DEFAULT '',
      horas_uso NUMERIC,
      foto_url TEXT,
      created_at TIMESTAMPTZ DEFAULT NOW()
  );
  CREATE INDEX IF NOT EXISTS idx_interv_cancha_ts ON velez_intervenciones (cancha_id, created_at DESC);
  CREATE TABLE IF NOT EXISTS soil_metrics (
      id BIGSERIAL PRIMARY KEY,
      venue_id TEXT NOT NULL,
      cancha_id TEXT,
      is_hibrido BOOLEAN DEFAULT FALSE,
      sar_vv_db NUMERIC,
      sar_vh_db NUMERIC,
      theta_soil NUMERIC,
      h_suction_cm NUMERIC,
      fuente TEXT,
      fecha_imagen DATE,
      created_at TIMESTAMPTZ DEFAULT NOW()
  );
  CREATE INDEX IF NOT EXISTS idx_soil_metrics_venue_ts ON soil_metrics (venue_id, created_at DESC);
  CREATE INDEX IF NOT EXISTS idx_soil_metrics_cancha_ts ON soil_metrics (cancha_id, created_at DESC);
  CREATE TABLE IF NOT EXISTS vegetation_metrics (
      id BIGSERIAL PRIMARY KEY,
      venue_id TEXT NOT NULL,
      cancha_id TEXT,
      is_hibrido BOOLEAN DEFAULT FALSE,
      fecha_imagen DATE,
      dias_antiguedad SMALLINT,
      ndvi NUMERIC, gndvi NUMERIC, evi2 NUMERIC, bsi NUMERIC, ndwi NUMERIC,
      confianza_pct SMALLINT,
      ndvi_sintetico REAL,
      margen_error_kalman REAL,
      metodo_generacion TEXT DEFAULT 'SENTINEL_DIRECTO',
      fuente TEXT,
      created_at TIMESTAMPTZ DEFAULT NOW()
  );
  CREATE INDEX IF NOT EXISTS idx_veg_metrics_venue_ts ON vegetation_metrics (venue_id, created_at DESC);
  CREATE INDEX IF NOT EXISTS idx_veg_metrics_cancha_ts ON vegetation_metrics (cancha_id, created_at DESC);
  CREATE TABLE IF NOT EXISTS climate_metrics (
      id BIGSERIAL PRIMARY KEY,
      venue_id TEXT NOT NULL,
      fecha DATE,
      et0_mm_dia NUMERIC, deficit_hidrico_mm NUMERIC,
      gdd_acumulado_7d NUMERIC, smith_kerns_pct NUMERIC,
      riego_min SMALLINT, ventana_corte TEXT, altura_corte_mm NUMERIC,
      fuente TEXT,
      created_at TIMESTAMPTZ DEFAULT NOW()
  );
  CREATE INDEX IF NOT EXISTS idx_climate_metrics_venue_ts ON climate_metrics (venue_id, created_at DESC);
"""
from __future__ import annotations
import json, logging, os, time
from datetime import datetime, timedelta, timezone

import requests as _req

log = logging.getLogger(__name__)

# Canchas con césped híbrido (Bermuda Tifway 419 type) — NDVI referencial, SAR primario
_TIPO_CESPED: dict[str, str] = {
    "amalfitani": "hibrido",
    "1fa":        "hibrido",   # Cancha 1 Villa Olímpica
}


def _tipo_for(cancha_id: str) -> str:
    return _TIPO_CESPED.get(cancha_id, "natural")


def _base() -> str:
    return os.environ.get("SUPABASE_URL", "").rstrip("/")


def _key() -> str:
    return os.environ.get("SUPABASE_KEY", "")


def _hdrs(extra: dict | None = None) -> dict:
    k = _key()
    h = {"apikey": k, "Authorization": f"Bearer {k}",
         "Content-Type": "application/json", "Prefer": "return=minimal"}
    if extra:
        h.update(extra)
    return h


def _ok() -> bool:
    return bool(_base()) and bool(_key())


# ── pipeline_runs ─────────────────────────────────────────────────────────────

def insert_pipeline_run(
    *,
    timestamp_utc:      str,
    fecha_imagen:       str | None,
    ndvi_median:        float | None,
    accepted:           bool,
    canchas_procesadas: int | None = None,
    skipped_reason:     str | None = None,
    error:              str | None = None,
) -> bool:
    """Insert one observability row. Returns True on success, False silently on failure."""
    if not _ok():
        return False
    row = {
        "timestamp_utc":      timestamp_utc,
        "fecha_imagen":       fecha_imagen,
        "ndvi_median":        ndvi_median,
        "accepted":           accepted,
        "canchas_procesadas": canchas_procesadas,
        "skipped_reason":     skipped_reason,
        "error":              error,
    }
    url = f"{_base()}/rest/v1/pipeline_runs"
    for attempt in range(2):
        try:
            r = _req.post(url, headers=_hdrs(),
                          data=json.dumps(row, default=str), timeout=8)
            if r.status_code in (200, 201, 204):
                return True
            log.warning("pipeline_runs insert (%d/2): HTTP %s — %s",
                        attempt + 1, r.status_code, r.text[:150])
        except Exception as exc:
            log.warning("pipeline_runs insert (%d/2): %s", attempt + 1, exc)
        if attempt == 0:
            time.sleep(1)
    return False


def query_pipeline_runs(days: int = 14) -> list[dict]:
    """Return rows from pipeline_runs over the last `days` days, newest first."""
    if not _ok():
        return []
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    url = (f"{_base()}/rest/v1/pipeline_runs"
           f"?timestamp_utc=gte.{since}&order=timestamp_utc.desc&limit=200")
    try:
        r = _req.get(url, headers=_hdrs(), timeout=10)
        if r.status_code == 200:
            return r.json()
        log.warning("pipeline_runs query: HTTP %s — %s", r.status_code, r.text[:150])
    except Exception as exc:
        log.warning("pipeline_runs query: %s", exc)
    return []


# ── velez_canchas ─────────────────────────────────────────────────────────────

def upsert_canchas(canchas: dict, fuente: str = "") -> bool:
    """
    Upsert per-cancha NDVI/score/sem rows into velez_canchas.
    canchas: {cancha_id: {ndvi, gndvi, bsi, ndwi, n_status, n_rec, score, sem, detalle, ...}}
    Returns True if all rows succeeded.
    """
    if not _ok() or not canchas:
        return False
    url = f"{_base()}/rest/v1/velez_canchas"
    hdrs = _hdrs({"Prefer": "resolution=merge-duplicates,return=minimal"})
    ts  = datetime.now(timezone.utc).isoformat()
    rows = []
    for cid, cd in canchas.items():
        row: dict = {"cancha_id": cid, "updated_at": ts}
        for field in ("ndvi", "gndvi", "bsi", "ndwi", "n_status", "n_rec",
                      "score", "score_prev", "sem", "detalle"):
            if field in cd:
                row[field] = cd[field]
        row["tipo_cesped"] = cd.get("tipo_cesped") or _tipo_for(cid)
        if fuente:
            row["fuente"] = fuente
        rows.append(row)
    try:
        r = _req.post(url, headers=hdrs,
                      data=json.dumps(rows, default=str), timeout=12)
        if r.status_code in (200, 201, 204):
            log.info("velez_canchas: %d canchas upserted", len(rows))
            return True
        log.warning("velez_canchas upsert HTTP %s: %s", r.status_code, r.text[:200])
    except Exception as exc:
        log.warning("velez_canchas upsert: %s", exc)
    return False


def get_canchas() -> dict:
    """Return {cancha_id: row_dict} from velez_canchas. Empty dict on failure."""
    if not _ok():
        return {}
    try:
        r = _req.get(f"{_base()}/rest/v1/velez_canchas?select=*",
                     headers=_hdrs(), timeout=8)
        if r.status_code == 200:
            return {row["cancha_id"]: row for row in r.json()}
        log.warning("velez_canchas get HTTP %s", r.status_code)
    except Exception as exc:
        log.warning("velez_canchas get: %s", exc)
    return {}


# ── velez_sectores ────────────────────────────────────────────────────────────

def upsert_sectores(sectores: dict) -> bool:
    """
    Upsert per-sector rows into velez_sectores.
    sectores: {sector_id: {nombre, score, score_prev, sem, detalle, insar_mm, ...}}
    Returns True if all rows succeeded.
    """
    if not _ok() or not sectores:
        return False
    url  = f"{_base()}/rest/v1/velez_sectores"
    hdrs = _hdrs({"Prefer": "resolution=merge-duplicates,return=minimal"})
    ts   = datetime.now(timezone.utc).isoformat()
    rows = []
    for sid, s in sectores.items():
        row: dict = {"sector_id": sid, "updated_at": ts}
        for field in ("nombre", "score", "score_prev", "sem", "detalle", "insar_mm"):
            if field in s:
                row[field] = s[field]
        rows.append(row)
    try:
        r = _req.post(url, headers=hdrs,
                      data=json.dumps(rows, default=str), timeout=12)
        if r.status_code in (200, 201, 204):
            log.info("velez_sectores: %d sectores upserted", len(rows))
            return True
        log.warning("velez_sectores upsert HTTP %s: %s", r.status_code, r.text[:200])
    except Exception as exc:
        log.warning("velez_sectores upsert: %s", exc)
    return False


def get_sectores() -> dict:
    """Return {sector_id: row_dict} from velez_sectores. Empty dict on failure."""
    if not _ok():
        return {}
    try:
        r = _req.get(f"{_base()}/rest/v1/velez_sectores?select=*",
                     headers=_hdrs(), timeout=8)
        if r.status_code == 200:
            return {row["sector_id"]: row for row in r.json()}
        log.warning("velez_sectores get HTTP %s", r.status_code)
    except Exception as exc:
        log.warning("velez_sectores get: %s", exc)
    return {}


# ── velez_weather_live ────────────────────────────────────────────────────────

def upsert_weather_live(weather: dict) -> bool:
    """
    Upsert the single weather_live row (id='current') in velez_weather_live.
    Non-blocking replacement for push_weather_update() GitHub write.
    """
    if not _ok():
        return False
    url  = f"{_base()}/rest/v1/velez_weather_live"
    hdrs = _hdrs({"Prefer": "resolution=merge-duplicates,return=minimal"})
    row  = {
        "id":         "current",
        "data":       weather,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    try:
        r = _req.post(url, headers=hdrs,
                      data=json.dumps(row, default=str), timeout=10)
        if r.status_code in (200, 201, 204):
            log.info("velez_weather_live: upserted (ts=%s)", weather.get("timestamp", "?"))
            return True
        log.warning("velez_weather_live upsert HTTP %s: %s", r.status_code, r.text[:200])
    except Exception as exc:
        log.warning("velez_weather_live upsert: %s", exc)
    return False


def get_weather_live() -> dict:
    """Return current weather_live dict from Supabase. Empty dict on failure."""
    if not _ok():
        return {}
    try:
        r = _req.get(f"{_base()}/rest/v1/velez_weather_live?id=eq.current&select=data",
                     headers=_hdrs(), timeout=8)
        if r.status_code == 200:
            rows = r.json()
            if rows:
                return rows[0].get("data") or {}
        log.warning("velez_weather_live get HTTP %s", r.status_code)
    except Exception as exc:
        log.warning("velez_weather_live get: %s", exc)
    return {}


# ── Assembled live state (overlay sobre GitHub JSON) ─────────────────────────

def get_live_overlay() -> dict:
    """
    Returns {weather_live, sectores, canchas} from Supabase to overlay onto
    the static GitHub velez_data.json. Returns empty dict if Supabase unavailable.

    Usage (in _get_velez_data):
        vd = read_github_json()
        overlay = get_live_overlay()
        if overlay:
            vd["weather_live"] = overlay["weather_live"] or vd.get("weather_live", {})
            for sid, s in overlay["sectores"].items():
                vd.setdefault("sectores", {}).setdefault(sid, {}).update(
                    {k: v for k, v in s.items() if k != "sector_id"})
            gn = vd.setdefault("weather_live", {}).setdefault("gndvi_por_cancha", {})
            gn_c = gn.setdefault("canchas", {})
            for cid, cd in overlay["canchas"].items():
                gn_c.setdefault(cid, {}).update(
                    {k: v for k, v in cd.items() if k not in ("cancha_id","updated_at","fuente")})
    """
    if not _ok():
        return {}
    weather   = get_weather_live()
    sectores  = get_sectores()
    canchas   = get_canchas()
    if not weather and not sectores and not canchas:
        return {}
    return {"weather_live": weather, "sectores": sectores, "canchas": canchas}


# ── velez_intervenciones ──────────────────────────────────────────────────────

def insert_intervencion(
    *,
    cancha_id: str,
    tipo:      str,
    detalle:   str = "",
    horas_uso: float | None = None,
    foto_url:  str | None = None,
) -> bool:
    """Insert one field-operation log row. Returns True on success."""
    if not _ok():
        return False
    row = {
        "cancha_id": cancha_id,
        "tipo":      tipo,
        "detalle":   detalle,
        "horas_uso": horas_uso,
        "foto_url":  foto_url,
    }
    url = f"{_base()}/rest/v1/velez_intervenciones"
    try:
        r = _req.post(url, headers=_hdrs(),
                      data=json.dumps(row, default=str), timeout=8)
        if r.status_code in (200, 201, 204):
            log.info("velez_intervenciones: %s en %s registrada", tipo, cancha_id)
            return True
        log.warning("velez_intervenciones insert HTTP %s: %s", r.status_code, r.text[:200])
    except Exception as exc:
        log.warning("velez_intervenciones insert: %s", exc)
    return False


def get_intervenciones_recientes(cancha_id: str | None = None, dias: int = 14) -> list[dict]:
    """
    Return recent intervention rows, newest first.
    If cancha_id is given, filter by it; otherwise returns all canchas.
    """
    if not _ok():
        return []
    since = (datetime.now(timezone.utc) - timedelta(days=dias)).isoformat()
    base_url = (f"{_base()}/rest/v1/velez_intervenciones"
                f"?created_at=gte.{since}&order=created_at.desc&limit=100")
    if cancha_id:
        base_url += f"&cancha_id=eq.{cancha_id}"
    try:
        r = _req.get(base_url, headers=_hdrs(), timeout=8)
        if r.status_code == 200:
            return r.json()
        log.warning("velez_intervenciones get HTTP %s", r.status_code)
    except Exception as exc:
        log.warning("velez_intervenciones get: %s", exc)
    return []


# ── soil_metrics ──────────────────────────────────────────────────────────────

def insert_soil_metrics(
    *,
    venue_id:     str,
    cancha_id:    str | None = None,
    is_hibrido:   bool = False,
    sar_vv_db:    float | None = None,
    sar_vh_db:    float | None = None,
    theta_soil:   float | None = None,
    h_suction_cm: float | None = None,
    fuente:       str = "",
    fecha_imagen: str | None = None,
) -> bool:
    """
    Insert one SAR soil row into soil_metrics. Silent fallback on failure.
    is_hibrido is auto-resolved from _TIPO_CESPED if not passed.
    """
    if not _ok():
        return False
    if cancha_id and is_hibrido is False:
        is_hibrido = _tipo_for(cancha_id) == "hibrido"
    row = {
        "venue_id":     venue_id,
        "cancha_id":    cancha_id,
        "is_hibrido":   is_hibrido,
        "sar_vv_db":    sar_vv_db,
        "sar_vh_db":    sar_vh_db,
        "theta_soil":   theta_soil,
        "h_suction_cm": h_suction_cm,
        "fuente":       fuente,
        "fecha_imagen": fecha_imagen,
    }
    url = f"{_base()}/rest/v1/soil_metrics"
    try:
        r = _req.post(url, headers=_hdrs(),
                      data=json.dumps(row, default=str), timeout=8)
        if r.status_code in (200, 201, 204):
            log.info("soil_metrics: insert OK venue=%s cancha=%s vv=%.1f θ=%.3f",
                     venue_id, cancha_id, sar_vv_db or 0, theta_soil or 0)
            return True
        log.warning("soil_metrics insert HTTP %s: %s", r.status_code, r.text[:200])
    except Exception as exc:
        log.warning("soil_metrics insert: %s", exc)
    return False


def get_soil_metrics_latest(venue_id: str, cancha_id: str | None = None,
                             dias: int = 30) -> list[dict]:
    """Return most recent soil_metrics rows for a venue/cancha. Newest first."""
    if not _ok():
        return []
    since = (datetime.now(timezone.utc) - timedelta(days=dias)).isoformat()
    url = (f"{_base()}/rest/v1/soil_metrics"
           f"?venue_id=eq.{venue_id}&created_at=gte.{since}"
           f"&order=created_at.desc&limit=50")
    if cancha_id:
        url += f"&cancha_id=eq.{cancha_id}"
    try:
        r = _req.get(url, headers=_hdrs(), timeout=8)
        if r.status_code == 200:
            return r.json()
        log.warning("soil_metrics get HTTP %s", r.status_code)
    except Exception as exc:
        log.warning("soil_metrics get: %s", exc)
    return []


# ── vegetation_metrics ────────────────────────────────────────────────────────

def insert_vegetation_metrics(*, venue_id: str, cancha_id: str | None = None,
                               fecha_imagen: str | None = None,
                               ndvi: float | None = None, gndvi: float | None = None,
                               evi2: float | None = None, bsi: float | None = None,
                               ndwi: float | None = None, confianza_pct: int | None = None,
                               ndvi_sintetico: float | None = None,
                               margen_error_kalman: float | None = None,
                               metodo_generacion: str = "SENTINEL_DIRECTO",
                               fuente: str = "") -> bool:
    """INSERT one row into vegetation_metrics. is_hibrido auto-derived from cancha_id."""
    if not _ok():
        return False
    from datetime import date as _date
    is_hibrido = _tipo_for(cancha_id) == "hibrido" if cancha_id else False
    dias_ant: int | None = None
    if fecha_imagen:
        try:
            dias_ant = (_date.today() - _date.fromisoformat(str(fecha_imagen))).days
        except Exception:
            pass
    row = {
        "venue_id": venue_id, "cancha_id": cancha_id, "is_hibrido": is_hibrido,
        "fecha_imagen": fecha_imagen, "dias_antiguedad": dias_ant,
        "ndvi": ndvi, "gndvi": gndvi, "evi2": evi2, "bsi": bsi, "ndwi": ndwi,
        "confianza_pct": confianza_pct, "fuente": fuente,
        "ndvi_sintetico": ndvi_sintetico,
        "margen_error_kalman": margen_error_kalman,
        "metodo_generacion": metodo_generacion,
    }
    url = f"{_base()}/rest/v1/vegetation_metrics"
    try:
        r = _req.post(url, headers=_hdrs(), data=json.dumps(row, default=str), timeout=8)
        if r.status_code in (200, 201, 204):
            return True
        log.warning("vegetation_metrics insert HTTP %s: %s", r.status_code, r.text[:200])
    except Exception as exc:
        log.warning("vegetation_metrics insert: %s", exc)
    return False


def get_vegetation_metrics_latest(venue_id: str, cancha_id: str | None = None,
                                   dias: int = 30) -> list[dict]:
    """Return most recent vegetation_metrics rows for a venue/cancha. Newest first."""
    if not _ok():
        return []
    since = (datetime.now(timezone.utc) - timedelta(days=dias)).isoformat()
    url = (f"{_base()}/rest/v1/vegetation_metrics"
           f"?venue_id=eq.{venue_id}&created_at=gte.{since}"
           f"&order=created_at.desc&limit=100")
    if cancha_id:
        url += f"&cancha_id=eq.{cancha_id}"
    try:
        r = _req.get(url, headers=_hdrs(), timeout=8)
        if r.status_code == 200:
            return r.json()
        log.warning("vegetation_metrics get HTTP %s", r.status_code)
    except Exception as exc:
        log.warning("vegetation_metrics get: %s", exc)
    return []


# ── climate_metrics ───────────────────────────────────────────────────────────

def insert_climate_metrics(*, venue_id: str, fecha: str | None = None,
                            et0_mm_dia: float | None = None,
                            deficit_hidrico_mm: float | None = None,
                            gdd_acumulado_7d: float | None = None,
                            smith_kerns_pct: float | None = None,
                            riego_min: int | None = None,
                            ventana_corte: str | None = None,
                            altura_corte_mm: float | None = None,
                            fuente: str = "nasa+openmeteo") -> bool:
    """INSERT one row into climate_metrics for a venue's daily climate cycle."""
    if not _ok():
        return False
    from datetime import date as _date
    row = {
        "venue_id": venue_id,
        "fecha": fecha or str(_date.today()),
        "et0_mm_dia": et0_mm_dia, "deficit_hidrico_mm": deficit_hidrico_mm,
        "gdd_acumulado_7d": gdd_acumulado_7d, "smith_kerns_pct": smith_kerns_pct,
        "riego_min": riego_min, "ventana_corte": ventana_corte,
        "altura_corte_mm": altura_corte_mm, "fuente": fuente,
    }
    url = f"{_base()}/rest/v1/climate_metrics"
    try:
        r = _req.post(url, headers=_hdrs(), data=json.dumps(row, default=str), timeout=8)
        if r.status_code in (200, 201, 204):
            return True
        log.warning("climate_metrics insert HTTP %s: %s", r.status_code, r.text[:200])
    except Exception as exc:
        log.warning("climate_metrics insert: %s", exc)
    return False


def patch_row(table: str, row_id: int, fields: dict) -> bool:
    """
    PATCH specific columns on a row by primary key id.
    Used by scientific enrichment modules (polimetría, ECOSTRESS, Landsat, etc.)
    to add computed values to existing rows without overwriting other columns.
    """
    if not _ok() or not fields or row_id is None:
        return False
    url = f"{_base()}/rest/v1/{table}?id=eq.{row_id}"
    try:
        r = _req.patch(url, headers=_hdrs(),
                       data=json.dumps(fields, default=str), timeout=8)
        if r.status_code in (200, 204):
            log.info("%s PATCH id=%d OK — %s", table, row_id, list(fields.keys()))
            return True
        log.warning("%s PATCH id=%d HTTP %s: %s", table, row_id, r.status_code, r.text[:150])
    except Exception as exc:
        log.warning("%s patch_row: %s", table, exc)
    return False


def get_latest_soil_row(venue_id: str, cancha_id: str | None = None) -> dict | None:
    """Return the most recent soil_metrics row (full columns). Used by enrichment modules."""
    rows = get_soil_metrics_latest(venue_id, cancha_id, dias=3)
    return rows[0] if rows else None


def get_latest_climate_row(venue_id: str) -> dict | None:
    """Return the most recent climate_metrics row. Used by enrichment modules."""
    rows = get_climate_metrics_latest(venue_id, dias=3)
    return rows[0] if rows else None


def get_latest_vegetation_row(venue_id: str, cancha_id: str | None = None) -> dict | None:
    """Return the most recent vegetation_metrics row. Used by enrichment modules."""
    rows = get_vegetation_metrics_latest(venue_id, cancha_id, dias=3)
    return rows[0] if rows else None


def get_climate_metrics_latest(venue_id: str, dias: int = 30) -> list[dict]:
    """Return most recent climate_metrics rows for a venue. Newest first."""
    if not _ok():
        return []
    since = (datetime.now(timezone.utc) - timedelta(days=dias)).isoformat()
    url = (f"{_base()}/rest/v1/climate_metrics"
           f"?venue_id=eq.{venue_id}&created_at=gte.{since}"
           f"&order=created_at.desc&limit=50")
    try:
        r = _req.get(url, headers=_hdrs(), timeout=8)
        if r.status_code == 200:
            return r.json()
        log.warning("climate_metrics get HTTP %s", r.status_code)
    except Exception as exc:
        log.warning("climate_metrics get: %s", exc)
    return []


# ── velez_solar ───────────────────────────────────────────────────────────────
# SQL:
#   CREATE TABLE IF NOT EXISTS velez_solar (
#       id BIGSERIAL PRIMARY KEY,
#       panel_id TEXT NOT NULL,
#       eficiencia_pct REAL,
#       temperatura_c REAL,
#       estado TEXT,
#       created_at TIMESTAMPTZ DEFAULT NOW()
#   );
#   ALTER TABLE velez_solar DISABLE ROW LEVEL SECURITY;
#   GRANT ALL ON public.velez_solar TO anon;
#   GRANT USAGE, SELECT ON SEQUENCE public.velez_solar_id_seq TO anon;

def upsert_solar(panels: list[dict]) -> bool:
    """
    Insert solar panel readings into velez_solar.
    panels: list of {panel_id, eficiencia_pct, temperatura_c, estado}
    Returns True on success.
    """
    if not _ok() or not panels:
        return False
    url  = f"{_base()}/rest/v1/velez_solar"
    hdrs = _hdrs({"Prefer": "return=minimal"})
    rows = []
    for p in panels:
        row: dict = {}
        for field in ("panel_id", "eficiencia_pct", "temperatura_c", "estado"):
            if p.get(field) is not None:
                row[field] = p[field]
        if "panel_id" not in row:
            continue
        rows.append(row)
    if not rows:
        return False
    try:
        r = _req.post(url, headers=hdrs,
                      data=json.dumps(rows, default=str), timeout=12)
        if r.status_code in (200, 201, 204):
            log.info("velez_solar: %d paneles inserted", len(rows))
            return True
        log.warning("velez_solar upsert HTTP %s: %s", r.status_code, r.text[:200])
    except Exception as exc:
        log.warning("velez_solar upsert: %s", exc)
    return False


def get_solar_latest(dias: int = 7) -> list[dict]:
    """Return most recent velez_solar rows (one per panel_id). Newest first."""
    if not _ok():
        return []
    since = (datetime.now(timezone.utc) - timedelta(days=dias)).isoformat()
    url = (f"{_base()}/rest/v1/velez_solar"
           f"?created_at=gte.{since}&order=created_at.desc&limit=500")
    try:
        r = _req.get(url, headers=_hdrs(), timeout=8)
        if r.status_code == 200:
            return r.json()
        log.warning("velez_solar get HTTP %s", r.status_code)
    except Exception as exc:
        log.warning("velez_solar get: %s", exc)
    return []


# ── velez_piletas ─────────────────────────────────────────────────────────────
# SQL:
#   CREATE TABLE IF NOT EXISTS velez_piletas (
#       id BIGSERIAL PRIMARY KEY,
#       pileta_id TEXT NOT NULL,
#       turbidez_ntu REAL,
#       ph REAL,
#       cloro_ppm REAL,
#       temperatura_c REAL,
#       created_at TIMESTAMPTZ DEFAULT NOW()
#   );
#   ALTER TABLE velez_piletas DISABLE ROW LEVEL SECURITY;
#   GRANT ALL ON public.velez_piletas TO anon;
#   GRANT USAGE, SELECT ON SEQUENCE public.velez_piletas_id_seq TO anon;

def upsert_piletas(readings: list[dict]) -> bool:
    """
    Insert aquatic quality readings into velez_piletas.
    readings: list of {pileta_id, turbidez_ntu, ph, cloro_ppm, temperatura_c}
    Returns True on success.
    """
    if not _ok() or not readings:
        return False
    url  = f"{_base()}/rest/v1/velez_piletas"
    hdrs = _hdrs({"Prefer": "return=minimal"})
    rows = []
    for p in readings:
        row: dict = {}
        for field in ("pileta_id", "turbidez_ntu", "ph", "cloro_ppm", "temperatura_c"):
            if p.get(field) is not None:
                row[field] = p[field]
        if "pileta_id" not in row:
            continue
        rows.append(row)
    if not rows:
        return False
    try:
        r = _req.post(url, headers=hdrs,
                      data=json.dumps(rows, default=str), timeout=12)
        if r.status_code in (200, 201, 204):
            log.info("velez_piletas: %d lecturas inserted", len(rows))
            return True
        log.warning("velez_piletas upsert HTTP %s: %s", r.status_code, r.text[:200])
    except Exception as exc:
        log.warning("velez_piletas upsert: %s", exc)
    return False


def get_piletas_latest(dias: int = 7) -> list[dict]:
    """Return most recent velez_piletas rows. Newest first."""
    if not _ok():
        return []
    since = (datetime.now(timezone.utc) - timedelta(days=dias)).isoformat()
    url = (f"{_base()}/rest/v1/velez_piletas"
           f"?created_at=gte.{since}&order=created_at.desc&limit=200")
    try:
        r = _req.get(url, headers=_hdrs(), timeout=8)
        if r.status_code == 200:
            return r.json()
        log.warning("velez_piletas get HTTP %s", r.status_code)
    except Exception as exc:
        log.warning("velez_piletas get: %s", exc)
    return []
