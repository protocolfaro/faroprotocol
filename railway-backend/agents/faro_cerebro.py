"""
faro_cerebro.py — Monitor autónomo Faro Protocol.

Corre cada 60 min via APScheduler. Checks:
  1. JSON freshness: FARO_VD_PATH modificado < 25h  → auto-llama run_now
  2. /health responde 200                            → alerta si cae
  3. PNGs en reportes_velez/ existen                → auto-llama run_now
Si no puede auto-corregir → manda alerta a protocolfaro@gmail.com.
Registra cada acción en faro_cerebro_log (Supabase).
"""
from __future__ import annotations
import json, logging, os, sys, time
from datetime import datetime, timezone
from pathlib import Path

import requests

log = logging.getLogger(__name__)

_ROOT     = Path(__file__).parents[1]        # railway-backend/
_PNG_DIR  = _ROOT / "reportes_velez"
_ALERT_TO = "protocolfaro@gmail.com"
_PORT     = os.environ.get("PORT", "8080")
_BASE_URL = f"http://localhost:{_PORT}"

_EXPECTED_PNGS = [
    "faro_reporte_velez.png",
    "faro_reporte_velez_solar_v2.png",
]
_MAX_JSON_AGE_H = 25


# ── Supabase log ──────────────────────────────────────────────────────────────

def _supa_base() -> str:
    return os.environ.get("SUPABASE_URL", "").rstrip("/")


def _supa_key() -> str:
    return os.environ.get("SUPABASE_KEY", "")


def _supa_hdrs() -> dict:
    k = _supa_key()
    return {
        "apikey": k,
        "Authorization": f"Bearer {k}",
        "Content-Type": "application/json",
        "Prefer": "return=minimal",
    }


def _log(accion: str, resultado: str = None, error: str = None, escalado: bool = False):
    base = _supa_base()
    if not base or not _supa_key():
        log.debug("cerebro_log: Supabase no configurado")
        return
    row = {"accion": accion, "resultado": resultado, "error": error, "escalado": escalado}
    try:
        r = requests.post(
            f"{base}/rest/v1/faro_cerebro_log",
            headers=_supa_hdrs(),
            data=json.dumps(row, default=str),
            timeout=8,
        )
        if r.status_code not in (200, 201, 204):
            log.warning("cerebro_log HTTP %s: %s", r.status_code, r.text[:150])
    except Exception as exc:
        log.warning("cerebro_log: %s", exc)


# ── Email alert ───────────────────────────────────────────────────────────────

def _alert(subject: str, body: str):
    try:
        if str(_ROOT) not in sys.path:
            sys.path.insert(0, str(_ROOT))
        from sports.clients.velez.velez_scheduler import send_email
        from datetime import datetime as _dt, timezone as _tz, timedelta as _td
        _ART = _tz(timedelta(hours=-3))
        body_html = f"""
<html><body style="margin:0;padding:0;background:#06080b">
<div style="font-family:Arial,sans-serif;background:#06080b;color:#f2ede4;
            padding:20px;max-width:680px;margin:0 auto">
<h2 style="color:#c9a84c;margin-top:0">{subject}</h2>
<p style="color:#f2ede4">{body}</p>
<hr style="border-color:#c9a84c44;margin-top:20px">
<p style="color:#9aa0a8;font-size:12px">
Faro Cerebro · Monitor autónomo · protocolfaro@gmail.com<br>
{_dt.now(_ART).strftime('%d/%m/%Y %H:%M')} UTC-3
</p>
</div></body></html>"""
        ok = send_email(_ALERT_TO, f"[Faro Cerebro] {subject}", body_html)
        log.info("cerebro alerta → %s: %s", "OK" if ok else "FAIL", subject)
    except Exception as exc:
        log.error("cerebro _alert: %s", exc)


# ── Check 1: JSON freshness ───────────────────────────────────────────────────

def _check_json() -> tuple[bool, str]:
    vd_path = os.environ.get("FARO_VD_PATH", "")
    if not vd_path or not os.path.exists(vd_path):
        return False, f"FARO_VD_PATH no encontrado: {vd_path!r}"
    age_h = (time.time() - os.path.getmtime(vd_path)) / 3600
    if age_h > _MAX_JSON_AGE_H:
        return False, f"JSON tiene {age_h:.1f}h de antigüedad (límite {_MAX_JSON_AGE_H}h)"
    return True, f"JSON OK — {age_h:.1f}h"


def _trigger_run_now() -> bool:
    try:
        r = requests.post(f"{_BASE_URL}/velez/run_now", timeout=120)
        return r.status_code in (200, 201, 202)
    except Exception as exc:
        log.warning("_trigger_run_now: %s", exc)
        return False


# ── Check 2: /health ─────────────────────────────────────────────────────────

def _check_health() -> tuple[bool, str]:
    try:
        r = requests.get(f"{_BASE_URL}/health", timeout=10)
        if r.status_code == 200:
            return True, "health OK"
        return False, f"/health HTTP {r.status_code}"
    except Exception as exc:
        return False, f"/health error: {exc}"


# ── Check 3: PNGs ─────────────────────────────────────────────────────────────

def _check_pngs() -> tuple[bool, str]:
    missing = [p for p in _EXPECTED_PNGS if not (_PNG_DIR / p).exists()]
    if missing:
        return False, f"PNGs faltantes: {missing}"
    return True, f"PNGs OK ({len(_EXPECTED_PNGS)} archivos)"


# ── Main loop ─────────────────────────────────────────────────────────────────

def run_cerebro():
    log.info("Faro Cerebro — ciclo inicio")
    issues: list[str] = []

    # 1 — JSON freshness
    ok_json, msg_json = _check_json()
    _log("check_json", resultado=msg_json, escalado=not ok_json)
    if not ok_json:
        log.warning("Cerebro check_json: %s — intentando run_now", msg_json)
        fired = _trigger_run_now()
        time.sleep(10)
        ok_json2, msg_json2 = _check_json()
        if ok_json2:
            _log("auto_fix_json", resultado="run_now exitoso")
        else:
            err = msg_json2 if fired else f"run_now falló + {msg_json}"
            issues.append(err)
            _log("auto_fix_json_fail", error=err, escalado=True)

    # 2 — /health
    ok_health, msg_health = _check_health()
    _log("check_health", resultado=msg_health, escalado=not ok_health)
    if not ok_health:
        issues.append(msg_health)
        log.error("Cerebro check_health: %s", msg_health)

    # 3 — PNGs
    ok_pngs, msg_pngs = _check_pngs()
    _log("check_pngs", resultado=msg_pngs, escalado=not ok_pngs)
    if not ok_pngs:
        log.warning("Cerebro check_pngs: %s — intentando run_now", msg_pngs)
        _trigger_run_now()
        time.sleep(10)
        ok_pngs2, msg_pngs2 = _check_pngs()
        if ok_pngs2:
            _log("auto_fix_pngs", resultado="run_now exitoso")
        else:
            issues.append(msg_pngs2)
            _log("auto_fix_pngs_fail", error=msg_pngs2, escalado=True)

    if issues:
        body = "<br>".join(f"• {i}" for i in issues)
        _alert("Alerta del sistema", body)
        log.error("Faro Cerebro: %d issue(s) escalado(s)", len(issues))
    else:
        log.info("Faro Cerebro — todo OK")


# ── APScheduler registration ──────────────────────────────────────────────────

def register_cerebro_job(scheduler) -> None:
    scheduler.add_job(
        run_cerebro,
        "interval",
        minutes=60,
        id="faro_cerebro",
        replace_existing=True,
        misfire_grace_time=1800,
    )
    log.info("Faro Cerebro registrado (cada 60 min)")
