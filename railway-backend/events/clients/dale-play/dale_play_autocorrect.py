"""
dale_play_autocorrect.py — Auto-corrección de módulos.

Registra éxito/fallo de cada módulo en module_health (Supabase).
Si un módulo falla 3 veces consecutivas:
  1. Lo marca como degraded en Supabase.
  2. Loguea el evento en SOURCE_LOG.md.
  3. El pipeline lo reporta en el resultado para visibilidad.

El pipeline llama record_module_result() después de cada ejecución de módulo.
El estado se expone en GET /dale-play/health.

SQL requerido en Supabase SQL Editor:
  CREATE TABLE IF NOT EXISTS module_health (
    module_id          TEXT PRIMARY KEY,
    consecutive_fails  INTEGER DEFAULT 0,
    total_fails        INTEGER DEFAULT 0,
    total_runs         INTEGER DEFAULT 0,
    status             TEXT DEFAULT 'healthy',
    degraded_since     TIMESTAMPTZ,
    last_error         TEXT,
    last_run_at        TIMESTAMPTZ DEFAULT NOW(),
    updated_at         TIMESTAMPTZ DEFAULT NOW()
  );
  ALTER TABLE module_health DISABLE ROW LEVEL SECURITY;
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Optional

import requests

log = logging.getLogger(__name__)

_FAIL_THRESHOLD = 3   # fallos consecutivos para marcar degraded


def _supa() -> tuple[str, str]:
    return os.environ.get("SUPABASE_URL", ""), os.environ.get("SUPABASE_KEY", "")


def _json_headers(key: str) -> dict:
    return {
        "apikey":        key,
        "Authorization": f"Bearer {key}",
        "Content-Type":  "application/json",
        "Accept":        "application/json",
    }


# ─────────────────────────────────────────────────────────────────────────────
# Registro de resultado de módulo
# ─────────────────────────────────────────────────────────────────────────────

def record_module_result(module_id: str, success: bool, error: str = "") -> dict:
    """
    Registra el resultado de una ejecución de módulo.
    Si falla por 3ª vez consecutiva → marca degraded → loguea en SOURCE_LOG.md.

    Retorna el estado actual: {"status": "healthy"|"degraded", "consecutive_fails": int}
    """
    supa_url, supa_key = _supa()
    if not supa_url or not supa_key:
        return {"status": "healthy", "consecutive_fails": 0}

    now_iso = datetime.now(timezone.utc).isoformat()

    # 1. Leer estado actual
    current = _get_module_row(supa_url, supa_key, module_id)

    if current is None:
        # Primera vez que se ve este módulo — crear row
        consecutive = 0 if success else 1
        total_fails = 0 if success else 1
        new_status  = "healthy"
        degraded_since = None
    else:
        consecutive    = 0 if success else (current.get("consecutive_fails", 0) + 1)
        total_fails    = current.get("total_fails", 0) + (0 if success else 1)
        prev_status    = current.get("status", "healthy")
        degraded_since = current.get("degraded_since")

        if success:
            new_status = "healthy"
            degraded_since = None
        elif consecutive >= _FAIL_THRESHOLD:
            new_status = "degraded"
            if prev_status != "degraded":
                degraded_since = now_iso   # recién degradado
        else:
            new_status = prev_status

    total_runs = (current.get("total_runs", 0) if current else 0) + 1

    row = {
        "module_id":         module_id,
        "consecutive_fails": consecutive,
        "total_fails":       total_fails,
        "total_runs":        total_runs,
        "status":            new_status,
        "last_error":        (error[:500] if not success else None),
        "last_run_at":       now_iso,
        "updated_at":        now_iso,
    }
    if degraded_since:
        row["degraded_since"] = degraded_since

    _upsert_module_row(supa_url, supa_key, row)

    # 2. Detectar transición a degraded
    was_degraded = (current or {}).get("status") == "degraded"
    is_newly_degraded = (new_status == "degraded" and not was_degraded)

    if is_newly_degraded:
        log.warning(
            "dale_play_autocorrect: MÓDULO DEGRADADO — %s (%d fallos consecutivos)",
            module_id, consecutive,
        )
        _log_degraded_to_source_log(module_id, consecutive, error)

    return {"status": new_status, "consecutive_fails": consecutive}


# ─────────────────────────────────────────────────────────────────────────────
# Lectura del estado
# ─────────────────────────────────────────────────────────────────────────────

def get_module_health() -> dict:
    """
    Retorna el estado de salud de todos los módulos registrados.
    Usado por GET /dale-play/health.
    """
    supa_url, supa_key = _supa()
    if not supa_url or not supa_key:
        return {"error": "supabase no configurado", "modules": {}}

    try:
        resp = requests.get(
            f"{supa_url}/rest/v1/module_health",
            params={"order": "module_id.asc"},
            headers=_json_headers(supa_key),
            timeout=10,
        )
        if resp.status_code != 200:
            return {"error": f"HTTP {resp.status_code}", "modules": {}}

        rows = resp.json()
        modules = {}
        for r in rows:
            mid = r.get("module_id", "?")
            modules[mid] = {
                "status":            r.get("status", "healthy"),
                "consecutive_fails": r.get("consecutive_fails", 0),
                "total_fails":       r.get("total_fails", 0),
                "total_runs":        r.get("total_runs", 0),
                "degraded_since":    r.get("degraded_since"),
                "last_error":        r.get("last_error"),
                "last_run_at":       r.get("last_run_at"),
            }

        n_degraded = sum(1 for m in modules.values() if m["status"] == "degraded")
        return {
            "modules":    modules,
            "n_degraded": n_degraded,
            "n_healthy":  len(modules) - n_degraded,
            "threshold":  _FAIL_THRESHOLD,
        }

    except Exception as exc:
        log.warning("dale_play_autocorrect: get_module_health: %s", exc)
        return {"error": str(exc), "modules": {}}


def get_degraded_modules() -> list[str]:
    """Lista de module_id actualmente degradados. Usada por el pipeline."""
    health = get_module_health()
    return [mid for mid, info in health.get("modules", {}).items()
            if info.get("status") == "degraded"]


# ─────────────────────────────────────────────────────────────────────────────
# Helpers internos
# ─────────────────────────────────────────────────────────────────────────────

def _get_module_row(supa_url: str, supa_key: str, module_id: str) -> Optional[dict]:
    try:
        resp = requests.get(
            f"{supa_url}/rest/v1/module_health",
            params={"module_id": f"eq.{module_id}"},
            headers=_json_headers(supa_key),
            timeout=5,
        )
        if resp.status_code == 200:
            rows = resp.json()
            return rows[0] if rows else None
    except Exception as exc:
        log.debug("dale_play_autocorrect: get row %s: %s", module_id, exc)
    return None


def _upsert_module_row(supa_url: str, supa_key: str, row: dict) -> None:
    try:
        resp = requests.post(
            f"{supa_url}/rest/v1/module_health",
            json=row,
            headers={
                **_json_headers(supa_key),
                "Prefer": "resolution=merge-duplicates,return=minimal",
            },
            timeout=5,
        )
        if resp.status_code not in (200, 201):
            log.warning(
                "dale_play_autocorrect: upsert %s HTTP %s",
                row.get("module_id"), resp.status_code,
            )
    except Exception as exc:
        log.warning("dale_play_autocorrect: upsert: %s", exc)


def _log_degraded_to_source_log(module_id: str, n_fails: int, last_error: str) -> None:
    """Registra degradación en SOURCE_LOG.md."""
    try:
        from dale_play_source_log import log_source_event
        log_source_event(
            modulo          = module_id,
            fuente_usada    = "CASCADA_PERMANENTE",
            confianza       = 0.0,
            venue_cubierto  = False,
            motivo_fallback = f"DEGRADADO: {n_fails} fallos consecutivos",
            extra_nota      = f"auto-corrección activada | último error: {last_error[:80]}",
        )
    except Exception as exc:
        log.warning("dale_play_autocorrect: source_log: %s", exc)
