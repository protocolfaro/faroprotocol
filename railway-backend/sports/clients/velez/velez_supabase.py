"""velez_supabase.py — Supabase REST client for Vélez satellite pipeline.

Same SUPABASE_URL + SUPABASE_KEY env vars as dale_play_storage.py.
Never raises — failures are logged and silently swallowed.

Tables:
  pipeline_runs — one row per satellite cycle (observability / audit)

SQL (run once in Supabase SQL Editor):
  CREATE TABLE IF NOT EXISTS pipeline_runs (
    id                  BIGSERIAL PRIMARY KEY,
    timestamp_utc       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    fecha_imagen        TEXT,
    ndvi_median         REAL,
    accepted            BOOLEAN     NOT NULL,
    canchas_procesadas  INTEGER,
    skipped_reason      TEXT,
    error               TEXT
  );
  CREATE INDEX IF NOT EXISTS pipeline_runs_ts_idx
    ON pipeline_runs (timestamp_utc DESC);
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
