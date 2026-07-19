"""
faro_infra.py — Production Infra Blueprint
Routes:     /infra/*
Middleware: audit logging, CSRF protection, per-IP rate limiting

Register in app.py:
    from faro_infra import infra_bp, init_infra
    app.register_blueprint(infra_bp)
    ...after scheduler starts...
    init_infra(app, _scheduler)
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import secrets
import smtplib
import threading
import time
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from functools import wraps
from typing import Optional

import requests as _req
from flask import Blueprint, Flask, Response, jsonify, make_response, request

log = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────

_TENANT_ID   = os.environ.get("FARO_TENANT_ID", "velez")
_SUPA_URL    = os.environ.get("SUPABASE_URL", "").rstrip("/")
_SUPA_KEY    = os.environ.get("SUPABASE_KEY", "")
_GMAIL_USER  = os.environ.get("GMAIL_USER", "protocolfaro@gmail.com")
_GMAIL_PASS  = os.environ.get("GMAIL_APP_PASS", "")
_CSRF_SECRET = os.environ.get("CSRF_SECRET", secrets.token_hex(32))
_RAILWAY_URL = os.environ.get("RAILWAY_URL", "").rstrip("/")

infra_bp = Blueprint("infra", __name__, url_prefix="/infra")


# ── Rate Limiter (sliding window, in-memory, thread-safe) ─────────────────────

class _SlidingWindow:
    def __init__(self):
        self._data: dict[str, deque] = defaultdict(lambda: deque())
        self._lock = threading.Lock()

    def allow(self, key: str, limit: int, window_secs: int) -> bool:
        now = time.monotonic()
        with self._lock:
            dq = self._data[key]
            cutoff = now - window_secs
            while dq and dq[0] < cutoff:
                dq.popleft()
            if len(dq) >= limit:
                return False
            dq.append(now)
            return True

    def cleanup(self):
        """Periodic cleanup — call from a low-frequency cron to prevent unbounded growth."""
        cutoff = time.monotonic() - 3600
        with self._lock:
            stale = [k for k, dq in self._data.items() if not dq or dq[-1] < cutoff]
            for k in stale:
                del self._data[k]


_rl = _SlidingWindow()


def _client_ip() -> str:
    xff = request.headers.get("X-Forwarded-For", "")
    return xff.split(",")[0].strip() if xff else (request.remote_addr or "0.0.0.0")


def _rate_guard(limit: int = 60, window: int = 60) -> Optional[Response]:
    key = f"{_client_ip()}:{request.path}"
    if not _rl.allow(key, limit, window):
        return jsonify({"error": "rate_limit_exceeded", "retry_after": window}), 429
    return None


# ── CSRF — server-side token store (works cross-origin; cookie-less) ──────────

_csrf_store: dict[str, float] = {}  # token → expiry monotonic timestamp
_csrf_lock = threading.Lock()
_CSRF_TTL = 3600  # 1 hour
_CSRF_HEADER = "X-CSRF-Token"


def _issue_csrf() -> str:
    token = secrets.token_urlsafe(32)
    with _csrf_lock:
        now = time.monotonic()
        # Purge expired tokens
        expired = [t for t, exp in _csrf_store.items() if exp < now]
        for t in expired:
            del _csrf_store[t]
        _csrf_store[token] = now + _CSRF_TTL
    return token


def _validate_csrf(token: str) -> bool:
    with _csrf_lock:
        exp = _csrf_store.get(token)
        return exp is not None and exp > time.monotonic()


def csrf_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if request.method in ("GET", "HEAD", "OPTIONS"):
            return fn(*args, **kwargs)
        # Internal service calls (Railway-internal loopback) bypass CSRF
        internal_sig = request.headers.get("X-Internal-Sig", "")
        if internal_sig and secrets.compare_digest(internal_sig, _CSRF_SECRET[:16]):
            return fn(*args, **kwargs)
        token = request.headers.get(_CSRF_HEADER, "")
        if not token or not _validate_csrf(token):
            return jsonify({"error": "csrf_invalid_or_expired"}), 403
        return fn(*args, **kwargs)
    return wrapper


def pin_required(fn):
    """Reuse the existing PIN validation from app.py via import."""
    @wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            import app as _app
            body = request.get_json(silent=True) or {}
            if not _app._ok_pin(body.get("pin")):
                return jsonify({"error": "pin_invalid"}), 401
        except ImportError:
            pass
        return fn(*args, **kwargs)
    return wrapper


# ── Supabase REST helpers ──────────────────────────────────────────────────────

def _sb_ok() -> bool:
    return bool(_SUPA_URL) and bool(_SUPA_KEY)


def _sb_hdrs(extra: dict | None = None) -> dict:
    h = {
        "apikey": _SUPA_KEY,
        "Authorization": f"Bearer {_SUPA_KEY}",
        "Content-Type": "application/json",
        "Prefer": "return=minimal",
    }
    if extra:
        h.update(extra)
    return h


def _sb_insert(table: str, row: dict) -> bool:
    if not _sb_ok():
        return False
    try:
        r = _req.post(
            f"{_SUPA_URL}/rest/v1/{table}",
            headers=_sb_hdrs({"Prefer": "resolution=merge-duplicates,return=minimal"}),
            json=row, timeout=6,
        )
        return r.status_code in (200, 201, 204)
    except Exception as exc:
        log.debug("sb_insert %s: %s", table, exc)
        return False


def _sb_select(table: str, params: dict, *, timeout: int = 8) -> list:
    if not _sb_ok():
        return []
    try:
        r = _req.get(
            f"{_SUPA_URL}/rest/v1/{table}",
            headers=_sb_hdrs({"Prefer": "count=none"}),
            params=params, timeout=timeout,
        )
        return r.json() if r.ok and isinstance(r.json(), list) else []
    except Exception as exc:
        log.debug("sb_select %s: %s", table, exc)
        return []


def _sb_patch(table: str, filters: str, payload: dict) -> bool:
    if not _sb_ok():
        return False
    try:
        r = _req.patch(
            f"{_SUPA_URL}/rest/v1/{table}?{filters}",
            headers=_sb_hdrs(), json=payload, timeout=6,
        )
        return r.ok
    except Exception as exc:
        log.debug("sb_patch %s: %s", table, exc)
        return False


def _sb_delete(table: str, filters: str) -> bool:
    if not _sb_ok():
        return False
    try:
        r = _req.delete(
            f"{_SUPA_URL}/rest/v1/{table}?{filters}",
            headers=_sb_hdrs(), timeout=6,
        )
        return r.ok
    except Exception as exc:
        log.debug("sb_delete %s: %s", table, exc)
        return False


# ── Audit Log — async write ────────────────────────────────────────────────────

def _flush_audit(entry: dict) -> None:
    """Non-blocking: writes audit entry to Supabase in a daemon thread."""
    def _write():
        _sb_insert("audit_log", entry)
    threading.Thread(target=_write, daemon=True, name="audit_flush").start()


# ── Alert Dispatch ─────────────────────────────────────────────────────────────

def _slack_post(webhook_url: str, payload: dict) -> bool:
    try:
        r = _req.post(webhook_url, json=payload, timeout=8)
        return r.status_code == 200
    except Exception as exc:
        log.warning("slack dispatch: %s", exc)
        return False


def _email_send(to_list: list[str], subject: str, body: str) -> bool:
    if not _GMAIL_PASS:
        log.warning("GMAIL_APP_PASS not set — email skipped")
        return False
    if not to_list:
        return False
    try:
        msg = EmailMessage()
        msg["From"] = _GMAIL_USER
        msg["To"] = ", ".join(to_list)
        msg["Subject"] = subject
        msg.set_content(body)
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=15) as smtp:
            smtp.login(_GMAIL_USER, _GMAIL_PASS)
            smtp.send_message(msg)
        return True
    except Exception as exc:
        log.warning("email dispatch %s: %s", to_list, exc)
        return False


def dispatch_alert(cfg: dict, metric_value=None) -> dict:
    """
    Fire an alert rule (Slack and/or email).
    Updates ultimo_disparo + disparos_total in Supabase on success.
    """
    tenant  = cfg.get("tenant_id", _TENANT_ID)
    nombre  = cfg.get("nombre", "unnamed")
    metrica = cfg.get("metrica", "?")
    umbral  = cfg.get("umbral")
    op      = cfg.get("operador", "?")
    canal   = cfg.get("canal", "email")

    val_str = f"{metric_value:.4f}" if isinstance(metric_value, float) else str(metric_value or "N/A")
    text_body = (
        f"FARO PROTOCOL · {tenant.upper()}\n"
        f"Alerta disparada: {nombre}\n"
        f"Métrica: {metrica} = {val_str}  (umbral: {op} {umbral})\n"
        f"UTC: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')}"
    )

    fired = []

    if canal in ("slack", "both"):
        env_key  = cfg.get("slack_webhook_env") or "SLACK_WEBHOOK_URL"
        hook_url = os.environ.get(env_key, "")
        if hook_url:
            slack_payload = {
                "text": f":rotating_light: *[{tenant}] {nombre}*",
                "attachments": [{
                    "color": "#c9a84c",
                    "text": text_body,
                    "footer": "Faro Protocol · faro_infra",
                    "ts": int(time.time()),
                }],
            }
            if _slack_post(hook_url, slack_payload):
                fired.append("slack")
        else:
            log.warning("alert '%s': env var %s not set — slack skipped", nombre, env_key)

    if canal in ("email", "both"):
        emails = cfg.get("email_to") or []
        if isinstance(emails, str):
            emails = [emails]
        if _email_send(emails, f"[Faro] {nombre}", text_body):
            fired.append("email")

    if fired and cfg.get("id"):
        def _bump():
            _sb_patch(
                "alert_config",
                f"id=eq.{cfg['id']}&tenant_id=eq.{tenant}",
                {
                    "ultimo_disparo": datetime.now(timezone.utc).isoformat(),
                    "disparos_total": (cfg.get("disparos_total") or 0) + 1,
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                },
            )
        threading.Thread(target=_bump, daemon=True).start()

    return {"ok": bool(fired), "channels_fired": fired, "nombre": nombre}


# ── Health Snapshot ────────────────────────────────────────────────────────────

def capture_health_snapshot() -> dict:
    """
    Collect current metrics from Supabase and write one health_snapshots row.
    Called by APScheduler every 15 minutes.
    """
    now  = datetime.now(timezone.utc)
    snap: dict = {"tenant_id": _TENANT_ID, "capturado_at": now.isoformat()}

    def _age_hours(ts_str: str) -> int:
        try:
            ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
            return max(0, int((now - ts).total_seconds() / 3600))
        except Exception:
            return 999

    def _median(vals: list) -> float | None:
        v = sorted([x for x in vals if x is not None])
        return v[len(v) // 2] if v else None

    try:
        # Vegetation (NDVI) — sin filtro venue_id: los datos Vélez usan
        # "amalfitani" y "villa_olimpica", no el _TENANT_ID genérico "velez"
        veg = _sb_select("vegetation_metrics", {
            "order": "created_at.desc",
            "limit": "100",
        })
        if veg:
            snap["ndvi_median"]   = _median([r.get("ndvi") for r in veg])
            snap["ndvi_age_hours"] = _age_hours(veg[0].get("created_at", ""))

        # Climate (ET0) — venue_ids reales son "amalfitani"/"villa_olimpica"
        clim = _sb_select("climate_metrics", {
            "venue_id": "in.(amalfitani,villa_olimpica)",
            "order": "created_at.desc",
            "limit": "1",
        })
        if clim:
            snap["et0_latest"]      = clim[0].get("et0_mm_dia")
            snap["climate_age_hours"] = _age_hours(clim[0].get("created_at", ""))

        # Soil (SAR)
        soil = _sb_select("soil_metrics", {
            "venue_id": f"eq.{_TENANT_ID}",
            "order": "created_at.desc",
            "limit": "20",
        })
        if soil:
            snap["sm_theta_median"] = _median([r.get("theta_soil") for r in soil])
            snap["sar_age_hours"]   = _age_hours(soil[0].get("created_at", ""))

        # Pipeline
        runs = _sb_select("pipeline_runs", {"order": "timestamp_utc.desc", "limit": "1"})
        snap["pipeline_ok"] = bool(runs[0].get("accepted")) if runs else False

        # Field scores
        canchas = _sb_select("velez_canchas", {"select": "sem,score"})
        snap["canchas_total"] = len(canchas)
        snap["canchas_ok"]    = sum(1 for c in canchas if c.get("sem") != "rojo")
        scores = [c["score"] for c in canchas if c.get("score") is not None]
        snap["score_global"]  = int(sum(scores) / len(scores)) if scores else None

        # Audit errors last hour
        since = (now - timedelta(hours=1)).isoformat()
        err_rows = _sb_select("audit_log", {
            "tenant_id":   f"eq.{_TENANT_ID}",
            "created_at":  f"gte.{since}",
            "status_code": "gte.500",
            "select":      "id",
            "limit":       "500",
        })
        snap["audit_errors_1h"] = len(err_rows)

        # Active alerts
        active = _sb_select("alert_config", {
            "tenant_id": f"eq.{_TENANT_ID}",
            "activo":    "eq.true",
            "select":    "id",
        })
        snap["active_alerts"] = len(active)

    except Exception as exc:
        log.warning("health_snapshot: %s", exc)

    _sb_insert("health_snapshots", snap)
    log.info(
        "health_snapshot: ndvi=%.3f et0=%.2f pipeline=%s score=%s",
        snap.get("ndvi_median") or 0,
        snap.get("et0_latest") or 0,
        snap.get("pipeline_ok"),
        snap.get("score_global"),
    )
    return snap


# ── Alert Evaluation ───────────────────────────────────────────────────────────

_METRIC_KEYS = {
    "ndvi_below":        ("ndvi",          "<"),
    "ndvi_above":        ("ndvi",          ">"),
    "et0_above":         ("et0",           ">"),
    "sm_below":          ("sm_theta",      "<"),
    "sm_above":          ("sm_theta",      ">"),
    "smith_kerns_above": ("smith_kerns",   ">"),
    "ndvi_stale_hours":  ("ndvi_age_h",    ">"),
    "sar_stale_hours":   ("sar_age_h",     ">"),
}


def evaluate_alerts() -> list[str]:
    """
    Evaluate active alert rules against latest metrics.
    Returns names of fired alerts. Called by APScheduler every 5 minutes.
    """
    alerts = _sb_select("alert_config", {
        "tenant_id": f"eq.{_TENANT_ID}",
        "activo":    "eq.true",
    })
    if not alerts:
        return []

    now = datetime.now(timezone.utc)

    # Gather current values once
    metrics: dict[str, float | None] = {}

    veg = _sb_select("vegetation_metrics", {
        "venue_id": f"eq.{_TENANT_ID}", "order": "created_at.desc", "limit": "50",
    })
    if veg:
        ndvis = [r["ndvi"] for r in veg if r.get("ndvi") is not None]
        metrics["ndvi"] = sorted(ndvis)[len(ndvis) // 2] if ndvis else None
        try:
            ts = datetime.fromisoformat(veg[0]["created_at"].replace("Z", "+00:00"))
            metrics["ndvi_age_h"] = (now - ts).total_seconds() / 3600
        except Exception:
            pass

    clim = _sb_select("climate_metrics", {
        "venue_id": f"eq.{_TENANT_ID}", "order": "created_at.desc", "limit": "1",
    })
    if clim:
        metrics["et0"]         = clim[0].get("et0_mm_dia")
        metrics["smith_kerns"] = clim[0].get("smith_kerns_pct")

    soil = _sb_select("soil_metrics", {
        "venue_id": f"eq.{_TENANT_ID}", "order": "created_at.desc", "limit": "20",
    })
    if soil:
        vals = [r["theta_soil"] for r in soil if r.get("theta_soil") is not None]
        metrics["sm_theta"] = sorted(vals)[len(vals) // 2] if vals else None
        try:
            ts = datetime.fromisoformat(soil[0]["created_at"].replace("Z", "+00:00"))
            metrics["sar_age_h"] = (now - ts).total_seconds() / 3600
        except Exception:
            pass

    fired: list[str] = []

    for cfg in alerts:
        metrica = cfg.get("metrica", "")
        umbral  = cfg.get("umbral")
        if umbral is None:
            continue

        # Cooldown
        if cfg.get("ultimo_disparo"):
            try:
                last = datetime.fromisoformat(
                    cfg["ultimo_disparo"].replace("Z", "+00:00")
                )
                cooldown_s = int(cfg.get("cooldown_minutes", 60)) * 60
                if (now - last).total_seconds() < cooldown_s:
                    continue
            except Exception:
                pass

        # Map metric → value + default operator
        if metrica not in _METRIC_KEYS:
            continue
        metric_key, default_op = _METRIC_KEYS[metrica]
        value = metrics.get(metric_key)
        if value is None:
            continue

        op = cfg.get("operador") or default_op
        threshold = float(umbral)
        triggered = (
            (op == "<"  and value <  threshold) or
            (op == ">"  and value >  threshold) or
            (op == "<=" and value <= threshold) or
            (op == ">=" and value >= threshold) or
            (op == "="  and abs(value - threshold) < 1e-6)
        )

        if triggered:
            result = dispatch_alert(cfg, metric_value=value)
            if result["ok"]:
                fired.append(cfg["nombre"])
                log.info("alert fired: %s (%.4f %s %.4f)", cfg["nombre"], value, op, threshold)

    return fired


# ── Blueprint Routes ───────────────────────────────────────────────────────────

@infra_bp.route("/csrf-token", methods=["GET"])
def csrf_token():
    """Issue a new CSRF token. Clients store this and send as X-CSRF-Token header."""
    token = _issue_csrf()
    return jsonify({"csrf_token": token, "expires_in": _CSRF_TTL})


@infra_bp.route("/health", methods=["GET"])
def infra_health():
    """Latest health snapshot."""
    rl = _rate_guard(30, 60)
    if rl:
        return rl
    rows = _sb_select("health_snapshots", {
        "tenant_id": f"eq.{_TENANT_ID}",
        "order":     "capturado_at.desc",
        "limit":     "1",
    })
    return jsonify({
        "status":    "ok",
        "tenant_id": _TENANT_ID,
        "snapshot":  rows[0] if rows else {},
    })


@infra_bp.route("/snapshot", methods=["POST"])
@csrf_required
def trigger_snapshot():
    """Manually trigger a health snapshot capture."""
    rl = _rate_guard(5, 60)
    if rl:
        return rl
    snap = capture_health_snapshot()
    return jsonify({"status": "ok", "snapshot": snap}), 201


@infra_bp.route("/metrics", methods=["GET"])
def infra_metrics():
    """
    Health time series for Chart.js.
    Query params: days (default 30, max 90)
    """
    rl = _rate_guard(30, 60)
    if rl:
        return rl
    days  = min(int(request.args.get("days", 30)), 90)
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    rows  = _sb_select("health_snapshots", {
        "tenant_id":    f"eq.{_TENANT_ID}",
        "capturado_at": f"gte.{since}",
        "order":        "capturado_at.asc",
        "select":       "capturado_at,ndvi_median,et0_latest,sm_theta_median,score_global,pipeline_ok,canchas_ok,canchas_total",
        "limit":        "3000",
    })
    return jsonify({"status": "ok", "data": rows, "count": len(rows), "days": days})


@infra_bp.route("/audit-log", methods=["GET"])
def audit_log_list():
    """
    Paginated audit log.
    Query params: limit (default 50), errors_only (true/false), path_filter
    """
    rl = _rate_guard(20, 60)
    if rl:
        return rl
    limit  = min(int(request.args.get("limit", 50)), 500)
    params = {
        "tenant_id": f"eq.{_TENANT_ID}",
        "order":     "created_at.desc",
        "limit":     str(limit),
    }
    if request.args.get("errors_only") == "true":
        params["status_code"] = "gte.400"
    if request.args.get("path_filter"):
        params["path"] = f"like.{request.args['path_filter']}*"
    rows = _sb_select("audit_log", params)
    return jsonify({"status": "ok", "data": rows, "count": len(rows)})


@infra_bp.route("/interventions", methods=["GET"])
def list_interventions():
    """List field interventions and calibration events."""
    rl = _rate_guard(20, 60)
    if rl:
        return rl
    params = {
        "tenant_id": f"eq.{_TENANT_ID}",
        "order":     "created_at.desc",
        "limit":     request.args.get("limit", "100"),
    }
    if request.args.get("cancha_id"):
        params["cancha_id"] = f"eq.{request.args['cancha_id']}"
    if request.args.get("tipo"):
        params["tipo"] = f"eq.{request.args['tipo']}"
    rows = _sb_select("intervention_log", params)
    return jsonify({"status": "ok", "data": rows, "count": len(rows)})


@infra_bp.route("/interventions", methods=["POST"])
@csrf_required
def create_intervention():
    """Log a field intervention or system calibration event."""
    rl = _rate_guard(20, 60)
    if rl:
        return rl
    body = request.get_json(silent=True) or {}
    for field in ("cancha_id", "tipo", "operador"):
        if not body.get(field):
            return jsonify({"error": f"missing required field: {field}"}), 400

    # Compute delta if before/after provided
    delta = None
    if body.get("valor_antes") and body.get("valor_despues"):
        try:
            antes    = body["valor_antes"]
            despues  = body["valor_despues"]
            delta = {
                k: round(float(despues[k]) - float(antes[k]), 4)
                for k in antes if k in despues
                and isinstance(antes[k], (int, float))
                and isinstance(despues[k], (int, float))
            }
        except Exception:
            pass

    row = {
        "tenant_id":     _TENANT_ID,
        "cancha_id":     body["cancha_id"],
        "sector_id":     body.get("sector_id"),
        "tipo":          body["tipo"],
        "subtipo":       body.get("subtipo"),
        "valor_antes":   body.get("valor_antes"),
        "valor_despues": body.get("valor_despues"),
        "delta_json":    delta,
        "operador":      body["operador"],
        "notas":         body.get("notas"),
        "archivo_url":   body.get("archivo_url"),
        "created_at":    datetime.now(timezone.utc).isoformat(),
    }
    ok = _sb_insert("intervention_log", row)
    return jsonify({"status": "ok" if ok else "error"}), 201 if ok else 500


@infra_bp.route("/alerts", methods=["GET"])
def list_alerts():
    """List all alert rules for the tenant."""
    rl = _rate_guard(20, 60)
    if rl:
        return rl
    rows = _sb_select("alert_config", {
        "tenant_id": f"eq.{_TENANT_ID}",
        "order":     "created_at.asc",
    })
    # Never expose the actual webhook URL — only the env var name
    return jsonify({"status": "ok", "data": rows, "count": len(rows)})


@infra_bp.route("/alerts", methods=["POST"])
@csrf_required
def create_alert():
    """Create or update an alert rule."""
    rl = _rate_guard(10, 60)
    if rl:
        return rl
    body = request.get_json(silent=True) or {}
    for field in ("nombre", "metrica", "canal"):
        if not body.get(field):
            return jsonify({"error": f"missing required field: {field}"}), 400
    if body.get("canal") not in ("email", "slack", "both"):
        return jsonify({"error": "canal must be email | slack | both"}), 400

    now = datetime.now(timezone.utc).isoformat()
    row = {
        "tenant_id":         _TENANT_ID,
        "nombre":            body["nombre"],
        "metrica":           body["metrica"],
        "umbral":            body.get("umbral"),
        "operador":          body.get("operador", "<"),
        "cancha_id":         body.get("cancha_id"),
        "canal":             body["canal"],
        "slack_webhook_env": body.get("slack_webhook_env", "SLACK_WEBHOOK_URL"),
        "email_to":          body.get("email_to") or [],
        "activo":            bool(body.get("activo", True)),
        "cooldown_minutes":  int(body.get("cooldown_minutes", 60)),
        "created_at":        now,
        "updated_at":        now,
    }
    ok = _sb_insert("alert_config", row)
    return jsonify({"status": "ok" if ok else "error"}), 201 if ok else 500


@infra_bp.route("/alerts/<int:alert_id>", methods=["DELETE"])
@csrf_required
def delete_alert(alert_id: int):
    """Delete an alert rule."""
    rl = _rate_guard(10, 60)
    if rl:
        return rl
    ok = _sb_delete("alert_config", f"id=eq.{alert_id}&tenant_id=eq.{_TENANT_ID}")
    return jsonify({"status": "ok" if ok else "error"}), 200 if ok else 500


@infra_bp.route("/alerts/<int:alert_id>/toggle", methods=["POST"])
@csrf_required
def toggle_alert(alert_id: int):
    """Enable or disable an alert rule."""
    rl = _rate_guard(10, 60)
    if rl:
        return rl
    body   = request.get_json(silent=True) or {}
    activo = bool(body.get("activo", True))
    ok = _sb_patch(
        "alert_config",
        f"id=eq.{alert_id}&tenant_id=eq.{_TENANT_ID}",
        {"activo": activo, "updated_at": datetime.now(timezone.utc).isoformat()},
    )
    return jsonify({"status": "ok" if ok else "error"})


@infra_bp.route("/alerts/<int:alert_id>/test", methods=["POST"])
@csrf_required
def test_alert_dispatch(alert_id: int):
    """Test-fire a specific alert rule (ignores cooldown)."""
    rl = _rate_guard(3, 60)
    if rl:
        return rl
    rows = _sb_select("alert_config", {
        "id":        f"eq.{alert_id}",
        "tenant_id": f"eq.{_TENANT_ID}",
        "limit":     "1",
    })
    if not rows:
        return jsonify({"error": "not_found"}), 404
    result = dispatch_alert(rows[0], metric_value=None)
    return jsonify(result)


@infra_bp.route("/alert-eval", methods=["POST"])
@csrf_required
def manual_alert_eval():
    """Manually trigger an alert evaluation cycle."""
    rl = _rate_guard(2, 60)
    if rl:
        return rl
    fired = evaluate_alerts()
    return jsonify({"status": "ok", "fired": fired, "count": len(fired)})


# ── Middleware + Scheduler Registration ────────────────────────────────────────

def init_infra(app: Flask, scheduler=None) -> None:
    """
    Register audit middleware on app and schedule health/alert jobs.
    Call AFTER app.register_blueprint(infra_bp) and after scheduler starts.
    """
    _req_start: dict[int, float] = {}

    @app.before_request
    def _capture_start():
        _req_start[threading.get_ident()] = time.monotonic()

    @app.after_request
    def _write_audit(response: Response) -> Response:
        try:
            tid  = threading.get_ident()
            t0   = _req_start.pop(tid, None)
            ms   = int((time.monotonic() - t0) * 1000) if t0 else -1
            path = request.path

            # Skip noisy low-value paths
            skip = {"/health", "/metrics", "/infra/csrf-token", "/infra/health",
                    "/favicon.ico"}
            if path in skip or path.startswith("/static"):
                return response

            raw_body = request.get_data(cache=False)
            entry = {
                "tenant_id":   _TENANT_ID,
                "method":      request.method,
                "path":        path[:200],
                "status_code": response.status_code,
                "duration_ms": ms,
                "request_hash": hashlib.sha256(raw_body).hexdigest()[:16] if raw_body else None,
                "ip_addr":     _client_ip()[:64],
                "user_agent":  (request.user_agent.string or "")[:200],
                "pin_used":    b'"pin"' in raw_body,
                "error_msg":   (
                    response.get_data(as_text=True)[:500]
                    if response.status_code >= 400 else None
                ),
                "created_at":  datetime.now(timezone.utc).isoformat(),
            }
            _flush_audit(entry)
        except Exception:
            pass
        return response

    @app.before_request
    def _global_rate_limit():
        """120 req/min per IP across all endpoints (burst protection)."""
        ip = _client_ip()
        if not _rl.allow(f"global:{ip}", 120, 60):
            return jsonify({"error": "rate_limit_exceeded"}), 429

    if scheduler is not None:
        try:
            from apscheduler.triggers.cron import CronTrigger
            scheduler.add_job(
                capture_health_snapshot,
                CronTrigger(minute="*/15"),
                id="infra_health_snapshot",
                replace_existing=True,
            )
            scheduler.add_job(
                evaluate_alerts,
                CronTrigger(minute="*/5"),
                id="infra_alert_eval",
                replace_existing=True,
            )
            # Hourly rate-limiter cleanup
            scheduler.add_job(
                _rl.cleanup,
                CronTrigger(minute=30),
                id="infra_rl_cleanup",
                replace_existing=True,
            )
            log.info("infra: health_snapshot(15min) + alert_eval(5min) + rl_cleanup scheduled")
        except Exception as exc:
            log.warning("infra scheduler registration: %s", exc)

    _run_infra_migrations()
    log.info("faro_infra: middleware registered for tenant=%s", _TENANT_ID)


def _run_infra_migrations():
    """Apply infra SQL migrations 001–004 at startup (non-blocking on failure)."""
    db_url = os.environ.get("SUPABASE_DB_URL", "")
    if not db_url:
        return
    if db_url.startswith("postgres://"):
        db_url = db_url.replace("postgres://", "postgresql://", 1)
    mig_dir = os.path.join(os.path.dirname(__file__), "migrations")
    files   = [
        "infra_001_audit_log.sql",
        "infra_002_intervention_log.sql",
        "infra_003_alert_config.sql",
        "infra_004_health_snapshots.sql",
    ]
    try:
        from sqlalchemy import create_engine, text
        engine = create_engine(db_url, connect_args={"connect_timeout": 10})
        for fname in files:
            fpath = os.path.join(mig_dir, fname)
            if not os.path.exists(fpath):
                continue
            sql   = open(fpath, encoding="utf-8").read()
            stmts = [
                s.strip() for s in sql.split(";")
                if s.strip() and not s.strip().startswith("--")
            ]
            with engine.begin() as conn:
                for stmt in stmts:
                    try:
                        conn.execute(text(stmt))
                    except Exception as se:
                        log.debug("infra mig stmt skip: %s", se)
        log.info("infra migrations 001–004: applied")
    except Exception as exc:
        log.warning("infra migrations (non-fatal — run manually in Supabase SQL Editor): %s", exc)
