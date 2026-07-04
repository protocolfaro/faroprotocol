from __future__ import annotations
import hashlib
import json
import logging
import os
import threading
import time
from datetime import datetime, timezone
from functools import wraps
from typing import Any

import requests as _req

log = logging.getLogger(__name__)

_SUPA_URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
_SUPA_KEY = os.environ.get("SUPABASE_KEY", "")
_VENUE_ID = os.environ.get("FARO_VENUE_ID", "velez")


def _hdrs() -> dict:
    return {
        "apikey":        _SUPA_KEY,
        "Authorization": f"Bearer {_SUPA_KEY}",
        "Content-Type":  "application/json",
        "Prefer":        "return=minimal",
    }


class AuditLog:
    def __init__(self, venue_id: str = _VENUE_ID):
        self.venue_id = venue_id

    def write(
        self,
        *,
        action_type: str,
        source: str,
        method: str | None = None,
        path: str | None = None,
        status_code: int | None = None,
        duration_ms: int | None = None,
        confidence_pct: int | None = None,
        metadata: dict | None = None,
        error_msg: str | None = None,
        ip_addr: str | None = None,
    ) -> None:
        row = {
            "venue_id":       self.venue_id,
            "action_type":    action_type,
            "source":         source,
            "method":         method,
            "path":           (path or "")[:200],
            "status_code":    status_code,
            "duration_ms":    duration_ms,
            "confidence_pct": confidence_pct,
            "metadata":       metadata or {},
            "error_msg":      (error_msg or "")[:1000] or None,
            "ip_addr":        (ip_addr or "")[:64] or None,
            "created_at":     datetime.now(timezone.utc).isoformat(),
        }
        threading.Thread(target=self._flush, args=(row,), daemon=True).start()

    def _flush(self, row: dict) -> None:
        if not _SUPA_URL or not _SUPA_KEY:
            return
        try:
            r = _req.post(
                f"{_SUPA_URL}/rest/v1/audit_log",
                headers=_hdrs(),
                json=row,
                timeout=6,
            )
            if not r.ok:
                log.debug("audit_log write HTTP %s", r.status_code)
        except Exception as exc:
            log.debug("audit_log flush: %s", exc)

    def query(
        self,
        *,
        limit: int = 20,
        errors_only: bool = False,
        action_type: str | None = None,
        source: str | None = None,
    ) -> list[dict]:
        if not _SUPA_URL or not _SUPA_KEY:
            return []
        params: dict[str, str] = {
            "venue_id": f"eq.{self.venue_id}",
            "order":    "created_at.desc",
            "limit":    str(min(limit, 500)),
        }
        if errors_only:
            params["status_code"] = "gte.400"
        if action_type:
            params["action_type"] = f"eq.{action_type}"
        if source:
            params["source"] = f"eq.{source}"
        try:
            r = _req.get(
                f"{_SUPA_URL}/rest/v1/audit_log",
                headers=_hdrs(),
                params=params,
                timeout=8,
            )
            return r.json() if r.ok and isinstance(r.json(), list) else []
        except Exception as exc:
            log.debug("audit_log query: %s", exc)
            return []


_default = AuditLog()


def audit_route(
    action_type: str,
    source: str = "api",
    confidence_pct: int | None = None,
):
    def decorator(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            from flask import request as _req_ctx
            t0 = time.monotonic()
            status = 500
            error  = None
            try:
                resp = fn(*args, **kwargs)
                status = resp[1] if isinstance(resp, tuple) else getattr(resp, "status_code", 200)
                return resp
            except Exception as exc:
                error = str(exc)
                raise
            finally:
                ms  = int((time.monotonic() - t0) * 1000)
                xff = _req_ctx.headers.get("X-Forwarded-For", "")
                ip  = xff.split(",")[0].strip() if xff else _req_ctx.remote_addr
                body = _req_ctx.get_data(cache=False)
                _default.write(
                    action_type=action_type,
                    source=source,
                    method=_req_ctx.method,
                    path=_req_ctx.path,
                    status_code=status,
                    duration_ms=ms,
                    confidence_pct=confidence_pct,
                    metadata={
                        "request_hash": hashlib.sha256(body).hexdigest()[:12] if body else None,
                        "content_length": len(body) if body else 0,
                    },
                    error_msg=error,
                    ip_addr=ip or "",
                )
        return wrapper
    return decorator
