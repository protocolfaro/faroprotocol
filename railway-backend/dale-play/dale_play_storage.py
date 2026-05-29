"""
dale_play_storage.py — Persistencia Supabase para dale-play.

Tablas requeridas (crear en Supabase SQL Editor):

  CREATE TABLE IF NOT EXISTS show_baselines (
    show_id      TEXT PRIMARY KEY,
    ndvi         REAL,
    date         TEXT,
    satellite    JSONB,
    created_at   TIMESTAMPTZ DEFAULT NOW()
  );

  CREATE TABLE IF NOT EXISTS certifications (
    show_id      TEXT PRIMARY KEY,
    cert_hash    TEXT UNIQUE,
    pdf_path     TEXT,
    ndvi_pre     REAL,
    ndvi_post    REAL,
    delta_ndvi   REAL,
    nivel_dano   TEXT,
    data         JSONB,
    created_at   TIMESTAMPTZ DEFAULT NOW()
  );

Variables de entorno Railway: SUPABASE_URL, SUPABASE_KEY

Estrategia de resiliencia:
  1. Cache inmediato en dale-play/cache/ antes de intentar Supabase.
  2. Supabase con un único reintento (backoff 2s) si falla.
  3. Fallback local en models/storage/ si Supabase definitivamente no disponible.
  4. Nunca crashear — loguear todo error en stderr para auditoría.
"""
from __future__ import annotations
import json, logging, os, sys, time
import pathlib as _pathlib

log = logging.getLogger(__name__)

_LOCAL_DIR = _pathlib.Path(__file__).parent / "models" / "storage"
_CACHE_DIR = _pathlib.Path(__file__).parent / "cache"


# ── Config check ──────────────────────────────────────────────────────────────

def check_supabase_config() -> bool:
    """
    Retorna True si SUPABASE_URL y SUPABASE_KEY están configuradas.
    Loguea en stderr si faltan — mensaje visible en Railway logs.
    """
    configured = (bool(os.environ.get("SUPABASE_URL"))
                  and bool(os.environ.get("SUPABASE_KEY")))
    if not configured:
        print(
            "SUPABASE no configurada — usando fallback local. "
            "Configurar SUPABASE_URL y SUPABASE_KEY en Railway para persistencia real.",
            file=sys.stderr, flush=True,
        )
    return configured


def _client():
    """Retorna cliente Supabase o None si no está configurado."""
    url = os.environ.get("SUPABASE_URL", "")
    key = os.environ.get("SUPABASE_KEY", "")
    if not url or not key:
        log.warning("dale_play_storage: SUPABASE_URL/KEY no configuradas — usando fallback local")
        return None
    try:
        from supabase import create_client
        return create_client(url, key)
    except Exception as e:
        print(f"dale_play_storage: supabase client error: {e}", file=sys.stderr, flush=True)
        log.warning("dale_play_storage: supabase client error: %s", e)
        return None


def _supabase_op(fn):
    """
    Ejecuta fn(client). Reintenta una vez tras 2s si falla.
    Retorna resultado de fn o None si falla definitivamente.
    Loguea todo error en stderr para auditoría.
    """
    for attempt in range(2):
        sb = _client()
        if sb is None:
            return None
        try:
            return fn(sb)
        except Exception as e:
            print(
                f"Supabase error (intento {attempt + 1}/2): {e}",
                file=sys.stderr, flush=True,
            )
            log.warning("Supabase error (intento %d/2): %s", attempt + 1, e)
            if attempt == 0:
                time.sleep(2)
    return None


# ── Cache inmediato (dale-play/cache/) ───────────────────────────────────────

def _cache_save(kind: str, show_id: str, data: dict) -> None:
    """Escribe en cache/ antes de intentar Supabase — backup inmediato."""
    try:
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        path = _CACHE_DIR / f"{kind}_{show_id}.json"
        path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
    except Exception as e:
        print(f"cache_save error ({kind}/{show_id}): {e}", file=sys.stderr, flush=True)
        log.warning("cache_save error: %s", e)


def _cache_load(kind: str, show_id: str) -> dict | None:
    path = _CACHE_DIR / f"{kind}_{show_id}.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


# ── Fallback local (models/storage/) ─────────────────────────────────────────

def _local_save(kind: str, show_id: str, data: dict) -> None:
    _LOCAL_DIR.mkdir(parents=True, exist_ok=True)
    path = _LOCAL_DIR / f"{kind}_{show_id}.json"
    try:
        path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        log.info("local storage: %s → %s", kind, path)
    except Exception as e:
        print(f"local_save error ({kind}/{show_id}): {e}", file=sys.stderr, flush=True)
        log.warning("local storage save error: %s", e)


def _local_load(kind: str, show_id: str) -> dict | None:
    path = _LOCAL_DIR / f"{kind}_{show_id}.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _local_load_by_hash(cert_hash: str) -> dict | None:
    """Escanea cache/ y models/storage/ buscando por hash."""
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
    """Guarda NDVI baseline pre-show. Cache inmediato + Supabase upsert."""
    row = {"show_id": show_id, "ndvi": ndvi, "date": date, "satellite": satellite_data}
    _cache_save("baseline", show_id, row)

    def _upsert(sb):
        sb.table("show_baselines").upsert(row).execute()
        log.info("Supabase: baseline guardado para %s (NDVI=%s)", show_id, ndvi)
        return True

    result = _supabase_op(_upsert)
    if result is None:
        _local_save("baseline", show_id, row)
        return False
    return True


def get_show_baseline(show_id: str) -> dict | None:
    """Recupera NDVI baseline: Supabase → cache → local."""
    def _select(sb):
        r = sb.table("show_baselines").select("*").eq("show_id", show_id).execute()
        return r.data[0] if r.data else None

    result = _supabase_op(_select)
    if result is not None:
        log.info("Supabase: baseline recuperado para %s", show_id)
        return result
    return _cache_load("baseline", show_id) or _local_load("baseline", show_id)


# ── certifications ────────────────────────────────────────────────────────────

def save_certification(show_id: str, cert_hash: str,
                       pdf_path: str, data: dict) -> bool:
    """Guarda certificación post-evento. Cache inmediato + Supabase upsert."""
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

    def _upsert(sb):
        sb.table("certifications").upsert(row).execute()
        log.info("Supabase: certificación guardada para %s (hash=%s...)",
                 show_id, cert_hash[:12])
        return True

    result = _supabase_op(_upsert)
    if result is None:
        _local_save("cert", show_id, row)
        return False
    return True


def get_certification(show_id: str) -> dict | None:
    """Recupera certificación: Supabase → cache → local."""
    def _select(sb):
        r = sb.table("certifications").select("*").eq("show_id", show_id).execute()
        return r.data[0] if r.data else None

    result = _supabase_op(_select)
    if result is not None:
        return result
    return _cache_load("cert", show_id) or _local_load("cert", show_id)


def get_certification_by_hash(cert_hash: str) -> dict | None:
    """Busca certificación por hash SHA-256 (endpoint /verify/{hash})."""
    def _select(sb):
        r = (sb.table("certifications")
               .select("*")
               .eq("cert_hash", cert_hash)
               .execute())
        return r.data[0] if r.data else None

    result = _supabase_op(_select)
    if result is not None:
        return result
    return _local_load_by_hash(cert_hash)
