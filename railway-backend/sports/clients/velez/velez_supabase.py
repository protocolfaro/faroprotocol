"""velez_supabase.py — Supabase REST client for Vélez satellite pipeline.

Same SUPABASE_URL + SUPABASE_KEY env vars as dale_play_storage.py.
Never raises — failures are logged and silently swallowed.

Tables (SQL en migrations/velez_live_tables.sql):
  pipeline_runs   — one row per satellite cycle (observability / audit)
  velez_canchas   — per-cancha NDVI/score/sem, updated by satellite_pipeline
  velez_sectores  — per-sector score/sem/detalle/insar, updated by pipeline + insar_hyp3
  velez_weather_live — single row (id='current') with weather_live JSONB, updated daily

These three tables replace the mutable sections of velez/velez_data.json on GitHub.
GitHub JSON is updated once per week as a static snapshot; SHA conflicts eliminated.
"""
from __future__ import annotations
import json, logging, os, time
from datetime import datetime, timedelta, timezone

import requests as _req

log = logging.getLogger(__name__)


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
