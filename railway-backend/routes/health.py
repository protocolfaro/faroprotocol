from __future__ import annotations
import logging
import os
import sys
import time
from datetime import datetime, timedelta, timezone

import requests as _req
from flask import Blueprint, jsonify, request

log = logging.getLogger(__name__)

health_bp = Blueprint("health", __name__, url_prefix="/api/health")

_SUPA_URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
_SUPA_KEY = os.environ.get("SUPABASE_KEY", "")
_VENUE_ID = os.environ.get("FARO_VENUE_ID", "velez")

_THRESHOLDS = {
    "era5_lag_warn_h":   12,
    "era5_lag_err_h":    24,
    "dprvi_lag_warn_h":  24,
    "dprvi_lag_err_h":   36,
    "kalman_lag_warn_h": 48,
    "kalman_lag_err_h":  96,
    "sar_lag_warn_h":    48,
    "sar_lag_err_h":     96,
}


def _hdrs() -> dict:
    return {
        "apikey":        _SUPA_KEY,
        "Authorization": f"Bearer {_SUPA_KEY}",
        "Content-Type":  "application/json",
        "Prefer":        "count=none",
    }


def _sb(table: str, params: dict, timeout: int = 8) -> list:
    if not _SUPA_URL or not _SUPA_KEY:
        return []
    try:
        r = _req.get(f"{_SUPA_URL}/rest/v1/{table}", headers=_hdrs(),
                     params=params, timeout=timeout)
        return r.json() if r.ok and isinstance(r.json(), list) else []
    except Exception:
        return []


def _age_hours(ts: str | None) -> int | None:
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        return max(0, int((datetime.now(timezone.utc) - dt).total_seconds() / 3600))
    except Exception:
        return None


def _svc_status(lag_h: int | None, warn_h: int, err_h: int) -> str:
    if lag_h is None:
        return "unknown"
    if lag_h >= err_h:
        return "error"
    if lag_h >= warn_h:
        return "warn"
    return "ok"


def _system_metrics() -> dict:
    try:
        import psutil
        return {
            "cpu_pct":  round(psutil.cpu_percent(interval=0.3), 1),
            "mem_pct":  round(psutil.virtual_memory().percent, 1),
            "disk_pct": round(psutil.disk_usage("/").percent, 1),
            "uptime_h": int((time.time() - psutil.boot_time()) / 3600),
        }
    except ImportError:
        return {"cpu_pct": None, "mem_pct": None, "disk_pct": None, "uptime_h": None}
    except Exception as exc:
        log.debug("psutil: %s", exc)
        return {"cpu_pct": None, "mem_pct": None, "disk_pct": None, "uptime_h": None}


def _era5_status() -> dict:
    # climate_metrics_sectorial uses sector_id (no venue_id) — query without filter
    rows = _sb("climate_metrics_sectorial", {
        "order":  "created_at.desc",
        "limit":  "1",
        "select": "created_at,et0_mm_dia",
    })
    if not rows:
        # climate_metrics uses venue_id="amalfitani"/"villa_olimpica" (not "velez")
        rows = _sb("climate_metrics", {
            "order":  "created_at.desc",
            "limit":  "1",
            "select": "created_at,et0_mm_dia",
        })
    lag = _age_hours(rows[0].get("created_at") if rows else None)
    return {
        "status":     _svc_status(lag, _THRESHOLDS["era5_lag_warn_h"], _THRESHOLDS["era5_lag_err_h"]),
        "lag_hours":  lag,
        "last_value": rows[0].get("et0_mm_dia") if rows else None,
        "label":      "ERA5-Land",
    }


def _dprvi_status() -> dict:
    rows = _sb("dprvi_metrics", {
        "order":  "created_at.desc",
        "limit":  "1",
        "select": "created_at,dprvi_value,cancha_id",
    })
    lag = _age_hours(rows[0].get("created_at") if rows else None)
    return {
        "status":     _svc_status(lag, _THRESHOLDS["dprvi_lag_warn_h"], _THRESHOLDS["dprvi_lag_err_h"]),
        "lag_hours":  lag,
        "last_value": rows[0].get("dprvi_value") if rows else None,
        "label":      "DpRVIc",
    }


def _kalman_status() -> dict:
    # vegetation_metrics uses venue_id="amalfitani" — query without venue filter
    rows = _sb("vegetation_metrics", {
        "ndvi_sintetico": "not.is.null",
        "order":          "created_at.desc",
        "limit":          "1",
        "select":         "created_at,ndvi_sintetico,margen_error_kalman",
    })
    lag = _age_hours(rows[0].get("created_at") if rows else None)
    return {
        "status":        _svc_status(lag, _THRESHOLDS["kalman_lag_warn_h"], _THRESHOLDS["kalman_lag_err_h"]),
        "lag_hours":     lag,
        "last_ndvi_syn": rows[0].get("ndvi_sintetico") if rows else None,
        "margen_error":  rows[0].get("margen_error_kalman") if rows else None,
        "label":         "Kalman",
    }


def _sar_status() -> dict:
    # Query by fecha_imagen (acquisition date) not created_at — avoids picking up
    # diag test rows (sar_vv_db=null) or rows inserted without real SAR data.
    rows = _sb("soil_metrics", {
        "sar_vv_db":    "not.is.null",
        "fecha_imagen": "not.is.null",
        "order":        "fecha_imagen.desc",
        "limit":        "1",
        "select":       "created_at,fecha_imagen,sar_vv_db,theta_soil",
    })
    # lag based on acquisition date, not insert time
    lag = None
    if rows:
        fi = rows[0].get("fecha_imagen")
        if fi:
            try:
                from datetime import date as _date
                acq = _date.fromisoformat(fi[:10])
                lag = (datetime.now(timezone.utc).date() - acq).days * 24
            except Exception:
                lag = _age_hours(rows[0].get("created_at"))
    return {
        "status":    _svc_status(lag, _THRESHOLDS["sar_lag_warn_h"], _THRESHOLDS["sar_lag_err_h"]),
        "lag_hours": lag,
        "sar_vv_db": rows[0].get("sar_vv_db") if rows else None,
        "last_fecha": rows[0].get("fecha_imagen") if rows else None,
        "label":     "SAR/S1",
    }


@health_bp.route("/status")
def status():
    t0 = time.monotonic()
    era5    = _era5_status()
    dprvi   = _dprvi_status()
    kalman  = _kalman_status()
    sar     = _sar_status()
    system  = _system_metrics()

    overall = "ok"
    for svc in (era5, dprvi, kalman, sar):
        if svc["status"] == "error":
            overall = "error"
            break
        if svc["status"] in ("warn", "unknown") and overall == "ok":
            overall = "warn"

    return jsonify({
        "status":    overall,
        "venue_id":  _VENUE_ID,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "elapsed_ms": int((time.monotonic() - t0) * 1000),
        "services": {
            "era5":   era5,
            "dprvi":  dprvi,
            "kalman": kalman,
            "sar":    sar,
            "system": system,
        },
    })


@health_bp.route("/audit-log")
def audit_log():
    limit       = min(int(request.args.get("limit", 20)), 200)
    errors_only = request.args.get("errors_only", "false").lower() == "true"
    source      = request.args.get("source")
    action_type = request.args.get("action_type")

    params: dict = {
        "venue_id": f"eq.{_VENUE_ID}",
        "order":    "created_at.desc",
        "limit":    str(limit),
    }
    if errors_only:
        params["status_code"] = "gte.400"
    if source:
        params["source"] = f"eq.{source}"
    if action_type:
        params["action_type"] = f"eq.{action_type}"

    rows = _sb("audit_log", params)
    return jsonify({"status": "ok", "data": rows, "count": len(rows)})


@health_bp.route("/intervention-log")
def intervention_log():
    limit     = min(int(request.args.get("limit", 20)), 200)
    cancha_id = request.args.get("cancha_id")
    tipo      = request.args.get("tipo")

    params: dict = {
        "venue_id": f"eq.{_VENUE_ID}",
        "order":    "created_at.desc",
        "limit":    str(limit),
    }
    if cancha_id:
        params["cancha_id"] = f"eq.{cancha_id}"
    if tipo:
        params["tipo"] = f"eq.{tipo}"

    rows = _sb("intervention_log", params)
    return jsonify({"status": "ok", "data": rows, "count": len(rows)})


@health_bp.route("/intervention-log", methods=["POST"])
def create_intervention():
    from flask import request as req
    body = req.get_json(silent=True) or {}
    for f in ("cancha_id", "tipo", "operador"):
        if not body.get(f):
            return jsonify({"error": f"missing: {f}"}), 400

    antes   = body.get("estado_antes", {})
    despues = body.get("estado_despues", {})
    delta   = {
        k: round(float(despues[k]) - float(antes[k]), 4)
        for k in antes
        if k in despues
        and isinstance(antes[k], (int, float))
        and isinstance(despues[k], (int, float))
    }

    row = {
        "venue_id":         _VENUE_ID,
        "cancha_id":        body["cancha_id"],
        "sector_id":        body.get("sector_id"),
        "tipo":             body["tipo"],
        "subtipo":          body.get("subtipo"),
        "delta_ndvi":       delta.get("ndvi"),
        "delta_sar_vv_db":  delta.get("sar_vv_db"),
        "delta_sar_vh_db":  delta.get("sar_vh_db"),
        "delta_theta_soil": delta.get("theta_soil"),
        "delta_et0_mm":     delta.get("et0_mm"),
        "delta_score":      delta.get("score"),
        "estado_antes":     antes,
        "estado_despues":   despues,
        "operador":         body["operador"],
        "notas":            body.get("notas"),
        "created_at":       datetime.now(timezone.utc).isoformat(),
    }

    if not _SUPA_URL or not _SUPA_KEY:
        return jsonify({"error": "supabase_unavailable"}), 503
    try:
        r = _req.post(
            f"{_SUPA_URL}/rest/v1/intervention_log",
            headers={**_hdrs(), "Prefer": "return=minimal"},
            json=row, timeout=8,
        )
        return jsonify({"status": "ok" if r.ok else "error"}), 201 if r.ok else 500
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500
