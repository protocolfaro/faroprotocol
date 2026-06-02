"""
dale_play_storage.py — Persistencia Supabase via REST API directa (requests).

Elimina supabase-py. Compatible con cualquier formato de API key (eyJ... o sb_publishable_...).
Headers requeridos: apikey + Authorization: Bearer.

Tablas requeridas (SQL Editor de Supabase):

  CREATE TABLE IF NOT EXISTS show_baselines (
    show_id    TEXT PRIMARY KEY,
    ndvi       REAL,
    date       TEXT,
    satellite  JSONB,
    created_at TIMESTAMPTZ DEFAULT NOW()
  );

  CREATE TABLE IF NOT EXISTS certifications (
    show_id    TEXT PRIMARY KEY,
    cert_hash  TEXT UNIQUE,
    pdf_path   TEXT,
    ndvi_pre   REAL,
    ndvi_post  REAL,
    delta_ndvi REAL,
    nivel_dano TEXT,
    data       JSONB,
    created_at TIMESTAMPTZ DEFAULT NOW()
  );

Variables de entorno Railway: SUPABASE_URL, SUPABASE_KEY

Estrategia de resiliencia:
  1. Cache inmediato en dale-play/cache/ antes de intentar Supabase.
  2. Retry único (backoff 2s) en cada operación REST.
  3. Fallback local en models/storage/ si Supabase no disponible.
  4. Nunca crashear — loguear todo error en stderr para auditoría.
"""
from __future__ import annotations
import json, logging, os, sys, time
import pathlib as _pathlib

import requests as _requests

log = logging.getLogger(__name__)

_LOCAL_DIR = _pathlib.Path(__file__).parent / "models" / "storage"
_CACHE_DIR = _pathlib.Path(__file__).parent / "cache"


# ── Config y helpers REST ─────────────────────────────────────────────────────

def _base_url() -> str:
    return os.environ.get("SUPABASE_URL", "").rstrip("/")


def _key() -> str:
    return os.environ.get("SUPABASE_KEY", "")


def _headers(extra: dict | None = None) -> dict:
    k = _key()
    h = {
        "apikey":        k,
        "Authorization": f"Bearer {k}",
        "Content-Type":  "application/json",
        "Prefer":        "return=minimal",
    }
    if extra:
        h.update(extra)
    return h


def _is_configured() -> bool:
    return bool(_base_url()) and bool(_key())


def check_supabase_config() -> bool:
    """Retorna True si env vars presentes. Loguea en stderr si faltan."""
    ok = _is_configured()
    if not ok:
        print(
            "SUPABASE no configurada — usando fallback local. "
            "Configurar SUPABASE_URL y SUPABASE_KEY en Railway para persistencia real.",
            file=sys.stderr, flush=True,
        )
    return ok


def ping() -> tuple[bool, str | None]:
    """
    GET /rest/v1/show_baselines?select=show_id&limit=1
    Retorna (ok: bool, error: str | None).
    Usado por /dale-play/health.
    """
    if not _is_configured():
        return False, "not configured"
    url = f"{_base_url()}/rest/v1/show_baselines?select=show_id&limit=1"
    try:
        r = _requests.get(url, headers=_headers(), timeout=6)
        if r.status_code in (200, 206):
            return True, None
        return False, f"HTTP {r.status_code}: {r.text[:300]}"
    except Exception as e:
        return False, str(e)


# ── REST primitivas con retry ─────────────────────────────────────────────────

def _upsert(table: str, row: dict) -> bool:
    """POST con merge-duplicates. Reintenta una vez. Loguea en stderr."""
    if not _is_configured():
        return False
    url  = f"{_base_url()}/rest/v1/{table}"
    hdrs = _headers({"Prefer": "resolution=merge-duplicates,return=minimal"})
    last_err = ""
    for attempt in range(2):
        try:
            r = _requests.post(url, headers=hdrs,
                               data=json.dumps(row, default=str),
                               timeout=10)
            if r.status_code in (200, 201, 204):
                return True
            last_err = f"HTTP {r.status_code}: {r.text[:300]}"
        except Exception as e:
            last_err = str(e)
        print(f"Supabase upsert/{table} (intento {attempt+1}/2): {last_err}",
              file=sys.stderr, flush=True)
        log.warning("Supabase upsert/%s attempt %d: %s", table, attempt + 1, last_err)
        if attempt == 0:
            time.sleep(2)
    return False


def _select_one(table: str, col: str, val: str) -> dict | None:
    """GET con filtro eq. Reintenta una vez. Retorna primer row o None."""
    if not _is_configured():
        return None
    url = f"{_base_url()}/rest/v1/{table}?select=*&{col}=eq.{val}"
    last_err = ""
    for attempt in range(2):
        try:
            r = _requests.get(url, headers=_headers(), timeout=10)
            if r.status_code == 200:
                data = r.json()
                return data[0] if data else None
            last_err = f"HTTP {r.status_code}: {r.text[:300]}"
        except Exception as e:
            last_err = str(e)
        print(f"Supabase select/{table} (intento {attempt+1}/2): {last_err}",
              file=sys.stderr, flush=True)
        log.warning("Supabase select/%s attempt %d: %s", table, attempt + 1, last_err)
        if attempt == 0:
            time.sleep(2)
    return None


# ── Cache inmediato (dale-play/cache/) ────────────────────────────────────────

def _cache_save(kind: str, show_id: str, data: dict) -> None:
    try:
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        (_CACHE_DIR / f"{kind}_{show_id}.json").write_text(
            json.dumps(data, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8")
    except Exception as e:
        print(f"cache_save error ({kind}/{show_id}): {e}", file=sys.stderr, flush=True)


def _cache_load(kind: str, show_id: str, max_age_h: float = 24) -> dict | None:
    """Carga desde cache solo si existe y tiene menos de max_age_h horas."""
    p = _CACHE_DIR / f"{kind}_{show_id}.json"
    if not p.exists():
        return None
    try:
        age_h = (time.time() - p.stat().st_mtime) / 3600
        if age_h > max_age_h:
            log.info("cache EXPIRED %s/%s (age=%.1fh > %.0fh)", kind, show_id, age_h, max_age_h)
            return None
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


# ── Fallback local (models/storage/) ─────────────────────────────────────────

def _local_save(kind: str, show_id: str, data: dict) -> None:
    _LOCAL_DIR.mkdir(parents=True, exist_ok=True)
    p = _LOCAL_DIR / f"{kind}_{show_id}.json"
    try:
        p.write_text(json.dumps(data, ensure_ascii=False, indent=2, default=str),
                     encoding="utf-8")
        log.info("local storage: %s → %s", kind, p)
    except Exception as e:
        print(f"local_save error ({kind}/{show_id}): {e}", file=sys.stderr, flush=True)


def _local_load(kind: str, show_id: str) -> dict | None:
    p = _LOCAL_DIR / f"{kind}_{show_id}.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def _scan_by_hash(cert_hash: str) -> dict | None:
    for _dir in (_CACHE_DIR, _LOCAL_DIR):
        if not _dir.exists():
            continue
        for p in _dir.glob("cert_*.json"):
            try:
                d = json.loads(p.read_text(encoding="utf-8"))
                if d.get("cert_hash") == cert_hash:
                    return d
            except Exception:
                pass
    return None


# ── show_baselines ────────────────────────────────────────────────────────────

def save_show_baseline(show_id: str, ndvi: float | None,
                       date: str, satellite_data: dict) -> bool:
    row = {"show_id": show_id, "ndvi": ndvi, "date": date, "satellite": satellite_data}
    _cache_save("baseline", show_id, row)
    ok = _upsert("show_baselines", row)
    if not ok:
        _local_save("baseline", show_id, row)
    else:
        log.info("Supabase: baseline guardado para %s (NDVI=%s)", show_id, ndvi)
    return ok


def get_show_baseline(show_id: str) -> dict | None:
    # Cache primero — si está fresco (< 24h) no llamamos a Supabase
    cached = _cache_load("baseline", show_id, max_age_h=24)
    if cached is not None:
        log.info("cache HIT: baseline/%s", show_id)
        return cached
    # Cache miss o expirado — consultar Supabase
    result = _select_one("show_baselines", "show_id", show_id)
    if result is not None:
        log.info("Supabase: baseline recuperado para %s — refrescando cache", show_id)
        _cache_save("baseline", show_id, result)
        return result
    return _local_load("baseline", show_id)


# ── certifications ────────────────────────────────────────────────────────────

def save_certification(show_id: str, cert_hash: str,
                       pdf_path: str, data: dict) -> bool:
    damage = data.get("damage") or {}
    row = {
        "show_id":    show_id,
        "cert_hash":  cert_hash,
        "pdf_path":   pdf_path,
        "ndvi_pre":   data.get("ndvi_pre"),
        "ndvi_post":  data.get("ndvi_post"),
        "delta_ndvi": damage.get("delta_ndvi"),
        "nivel_dano": damage.get("nivel_dano", "sin_datos"),
        "data":       data,
    }
    _cache_save("cert", show_id, row)
    ok = _upsert("certifications", row)
    if not ok:
        _local_save("cert", show_id, row)
    else:
        log.info("Supabase: certificación guardada para %s (hash=%s...)",
                 show_id, cert_hash[:12])
    return ok


def get_certification(show_id: str) -> dict | None:
    # Cache primero — certificados no cambian, TTL 24h
    cached = _cache_load("cert", show_id, max_age_h=24)
    if cached is not None:
        log.info("cache HIT: cert/%s", show_id)
        return cached
    result = _select_one("certifications", "show_id", show_id)
    if result is not None:
        _cache_save("cert", show_id, result)
        return result
    return _local_load("cert", show_id)


def get_certification_by_hash(cert_hash: str) -> dict | None:
    # Local scan first — certificados son inmutables, no hay stale risk
    cached = _scan_by_hash(cert_hash)
    if cached is not None:
        return cached
    result = _select_one("certifications", "cert_hash", cert_hash)
    return result


# ── audit_log (inmutable — solo INSERT, nunca UPDATE) ─────────────────────────
#
#   CREATE TABLE IF NOT EXISTS audit_log (
#     id                 BIGSERIAL PRIMARY KEY,
#     show_id            TEXT        NOT NULL,
#     timestamp          TIMESTAMPTZ DEFAULT NOW(),
#     modulos_ejecutados JSONB,
#     modulos_fallidos   JSONB,
#     fuentes_usadas     JSONB,
#     confianza_general  TEXT,
#     smoke_tests        JSONB
#   );

def _insert(table: str, row: dict) -> bool:
    """POST sin merge-duplicates (INSERT puro). Para tablas append-only."""
    if not _is_configured():
        return False
    url  = f"{_base_url()}/rest/v1/{table}"
    hdrs = _headers({"Prefer": "return=minimal"})
    try:
        r = _requests.post(url, headers=hdrs,
                           data=_json.dumps(row, default=str),
                           timeout=10)
        if r.status_code in (200, 201, 204):
            return True
        print(f"Supabase insert/{table}: HTTP {r.status_code}: {r.text[:300]}",
              file=_sys.stderr, flush=True)
    except Exception as e:
        print(f"Supabase insert/{table}: {e}", file=_sys.stderr, flush=True)
    return False


def save_audit_log(show_id: str, modulos_ejecutados: list, modulos_fallidos: list,
                   fuentes_usadas: dict, confianza_general: str,
                   smoke_tests: dict | None = None) -> bool:
    """
    Guarda una entrada inmutable en audit_log.
    Local siempre (run_log). Supabase si disponible.
    """
    row = {
        "show_id":            show_id,
        "modulos_ejecutados": modulos_ejecutados,
        "modulos_fallidos":   modulos_fallidos,
        "fuentes_usadas":     fuentes_usadas,
        "confianza_general":  confianza_general,
        "smoke_tests":        smoke_tests or {},
    }
    _cache_save("audit_log", show_id, row)
    ok = _insert("audit_log", row)
    if not ok:
        _local_save("audit_log", show_id, row)
    else:
        log.info("Supabase: audit_log guardado para %s", show_id)
    return ok


# ── Supabase sync al arrancar ─────────────────────────────────────────────────

def sync_pending_to_supabase() -> dict:
    """
    Sube a Supabase los registros de models/storage/ que no existen allí todavía.
    Llamar desde app.py al iniciar Railway (non-blocking — errors son logueados).
    Retorna {"synced": int, "skipped": int, "failed": int}.
    """
    if not _is_configured():
        log.info("sync_pending: Supabase no configurado — saltear")
        return {"synced": 0, "skipped": 0, "failed": 0, "nota": "Supabase no configurado"}

    if not _LOCAL_DIR.exists():
        return {"synced": 0, "skipped": 0, "failed": 0}

    synced = skipped = failed = 0
    for p in sorted(_LOCAL_DIR.glob("*.json")):
        stem = p.stem   # e.g. "baseline_airbag_2026-05-31"
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except Exception as exc:
            log.warning("sync_pending: no se pudo leer %s: %s", p.name, exc)
            failed += 1
            continue

        try:
            if stem.startswith("baseline_"):
                show_id = data.get("show_id") or stem[len("baseline_"):]
                if _select_one("show_baselines", "show_id", show_id):
                    skipped += 1
                    continue
                ok = _upsert("show_baselines", {
                    "show_id":   show_id,
                    "ndvi":      data.get("ndvi"),
                    "date":      data.get("date"),
                    "satellite": data.get("satellite", {}),
                })
            elif stem.startswith("cert_"):
                show_id = data.get("show_id") or stem[len("cert_"):]
                if _select_one("certifications", "show_id", show_id):
                    skipped += 1
                    continue
                ok = _upsert("certifications", {
                    "show_id":    show_id,
                    "cert_hash":  data.get("cert_hash", ""),
                    "pdf_path":   data.get("pdf_path", ""),
                    "ndvi_pre":   data.get("ndvi_pre"),
                    "ndvi_post":  data.get("ndvi_post"),
                    "delta_ndvi": data.get("delta_ndvi"),
                    "nivel_dano": data.get("nivel_dano", "sin_datos"),
                    "data":       data.get("data", {}),
                })
            else:
                skipped += 1
                continue

            if ok:
                synced += 1
                log.info("sync_pending: %s → Supabase OK", stem)
            else:
                failed += 1
                log.warning("sync_pending: %s → Supabase FAILED", stem)

        except Exception as exc:
            failed += 1
            log.warning("sync_pending: %s error: %s", p.name, exc)

    log.info("sync_pending_to_supabase: synced=%d skipped=%d failed=%d",
             synced, skipped, failed)
    return {"synced": synced, "skipped": skipped, "failed": failed}


# ── source_discoveries (job semanal de auto-mejora) ──────────────────────────
#
#   CREATE TABLE IF NOT EXISTS source_discoveries (
#     id             BIGSERIAL PRIMARY KEY,
#     discovered_at  TIMESTAMPTZ DEFAULT NOW(),
#     fuente_nombre  TEXT,
#     fuente_url     TEXT,
#     fuente_tipo    TEXT,
#     cobertura      TEXT,
#     resolucion     TEXT,
#     frecuencia     TEXT,
#     costo          TEXT,
#     evaluacion     TEXT,
#     recomendacion  TEXT,
#     datos_raw      JSONB
#   );
#
#   CREATE TABLE IF NOT EXISTS scout_reports (
#     id             BIGSERIAL PRIMARY KEY,
#     created_at     TIMESTAMPTZ DEFAULT NOW(),
#     timestamp      TEXT,
#     modelo         TEXT,
#     n_fuentes      INT,
#     resumen        TEXT,
#     prioridad      JSONB,
#     datos_raw      JSONB
#   );

def save_source_discovery(data: dict) -> bool:
    """Guarda un descubrimiento de nueva fuente en source_discoveries."""
    nombre = data.get("fuente_nombre", "unknown")
    row = {
        "fuente_nombre": nombre,
        "fuente_url":    data.get("fuente_url", ""),
        "fuente_tipo":   data.get("fuente_tipo", ""),
        "cobertura":     data.get("cobertura", ""),
        "resolucion":    data.get("resolucion", ""),
        "frecuencia":    data.get("frecuencia", ""),
        "costo":         data.get("costo", ""),
        "evaluacion":    data.get("evaluacion", ""),
        "recomendacion": data.get("recomendacion", ""),
        "datos_raw":     data.get("datos_raw", {}),
    }
    _cache_save("source_discovery", nombre, row)
    ok = _insert("source_discoveries", row)
    if not ok:
        _local_save("source_discovery", nombre, row)
    else:
        log.info("Supabase: source_discovery guardada: %s (%s)", nombre, row.get("recomendacion"))
    return ok


def save_scout_report(report: dict) -> bool:
    """Guarda el reporte completo del scout semanal en scout_reports."""
    ts = report.get("timestamp", "")
    row = {
        "timestamp":  ts,
        "modelo":     report.get("modelo", ""),
        "n_fuentes":  len(report.get("fuentes_nuevas", [])),
        "resumen":    report.get("resumen_ejecutivo", ""),
        "prioridad":  report.get("prioridad", []),
        "datos_raw":  report,
    }
    _cache_save("scout_report", ts[:10] or "unknown", row)
    ok = _insert("scout_reports", row)
    if not ok:
        _local_save("scout_report", ts[:10] or "unknown", row)
    else:
        log.info("Supabase: scout_report guardado %s — %d fuentes", ts[:10], row["n_fuentes"])
    return ok


# ── aliases para imports legacy ───────────────────────────────────────────────
import json as _json, sys as _sys
