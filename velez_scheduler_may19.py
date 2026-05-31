"""
velez_scheduler.py — Vélez email + WhatsApp weekly notifications for Railway.
Adapted from faro_velez_scheduler.py: no local scripts, no PDF attachments,
no `schedule` library (uses APScheduler from app.py).

Config is fetched from GitHub raw URL so it works in stateless Railway containers.
"""
import json, logging, os, smtplib, threading, traceback
from datetime import datetime, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from typing import Optional
from urllib.request import urlopen, Request as UReq

import requests

log = logging.getLogger(__name__)

# ── Config ────────────────────────────────────────────────────────────────────

PANEL_BASE_URL = "https://protocolfaro.github.io/faroprotocol/velez/"
_CFG_RAW_URL   = "https://raw.githubusercontent.com/protocolfaro/faroprotocol/main/velez/config_velez.json"
_VD_RAW_URL    = "https://raw.githubusercontent.com/protocolfaro/faroprotocol/main/velez/velez_data.json"

GMAIL_USER = os.environ.get("GMAIL_USER", "protocolfaro@gmail.com")
GMAIL_PASS = os.environ.get("GMAIL_APP_PASS", "")

NDVI_ALERT  = 0.35
INSAR_ALERT = 3.0


def _env(key: str, default: str = "") -> str:
    return os.environ.get(key, default)


def load_config() -> dict:
    """Fetch config_velez.json from GitHub. Fallback to empty config on error."""
    try:
        req = UReq(_CFG_RAW_URL, headers={"User-Agent": "FaroProtocol/4.0"})
        with urlopen(req, timeout=10) as r:
            return json.loads(r.read())
    except Exception as e:
        log.warning("load_config: %s", e)
        return {"zonas": [], "destinatarios": []}


def _get_sectores() -> dict:
    """Fetch velez_data.json from GitHub and return sectores dict."""
    try:
        req = UReq(_VD_RAW_URL, headers={"User-Agent": "FaroProtocol/4.0"})
        with urlopen(req, timeout=10) as r:
            return json.loads(r.read()).get("sectores", {})
    except Exception as e:
        log.warning("_get_sectores: %s", e)
        return {}


# ── WhatsApp (CallMeBot) ──────────────────────────────────────────────────────

_WA_ALERT_USERS = [
    {"nombre": "Roger Bernal",      "slug": "roger",    "phone": "541124642616", "env_key": "CALLMEBOT_KEY_ROGER",    "sectores": ["canchero"]},
    {"nombre": "Juan González",     "slug": "juan",     "phone": "541151073109", "env_key": "CALLMEBOT_KEY_JUAN",     "sectores": ["canchero","agro","poli"]},
    {"nombre": "Fernando Banchero", "slug": "banchero", "phone": "541167096384", "env_key": "CALLMEBOT_KEY_BANCHERO", "sectores": ["estadio","agro","solar","canchero","sede","poli","piletas"]},
    {"nombre": "Nelson Pugliese",   "slug": "nelson",   "phone": "541156417353", "env_key": "CALLMEBOT_KEY_NELSON",   "sectores": ["estadio","agro","solar","canchero","sede","poli","piletas"]},
]
_SEM_EMOJI = {"verde": "✅", "amarillo": "⚠️", "rojo": "🚨"}
_SEM_ORDER = {"verde": 0, "amarillo": 1, "rojo": 2}


def send_whatsapp(phone: str, message: str, api_key: str) -> bool:
    if not phone or not api_key:
        return False
    try:
        r = requests.get("https://api.callmebot.com/whatsapp.php",
                         params={"phone": phone, "text": message, "apikey": api_key},
                         timeout=15)
        ok = r.status_code == 200
        log.info("WhatsApp %s → %s", phone[:8] + "***", "OK" if ok else f"FAIL {r.status_code}")
        return ok
    except Exception as e:
        log.error("WhatsApp %s: %s", phone[:8] + "***", e)
        return False


def _build_wa_message(user: dict, sectores: dict, fecha: str) -> str:
    nombre_short = user["nombre"].split()[0]
    user_sects   = [sectores[k] for k in user["sectores"] if k in sectores]
    scores   = [s["score"] for s in user_sects]
    avg      = round(sum(scores) / len(scores)) if scores else 0
    worst    = max(user_sects, key=lambda s: _SEM_ORDER.get(s["sem"], 0), default={})
    worst_sem = worst.get("sem", "verde") if worst else "verde"
    criticos  = [s for s in user_sects if s["sem"] == "rojo"]
    atencion  = [s for s in user_sects if s["sem"] == "amarillo"]

    lines = [
        "*Faro Protocol — Vélez Sarsfield*",
        f"Semana {fecha}", "",
        f"Hola {nombre_short}, tu resumen:",
        f"{_SEM_EMOJI[worst_sem]} Score: *{avg}/100*", "",
    ]
    if criticos:
        lines.append("🚨 *Acción urgente:*")
        for s in criticos:
            lines.append(f"  • {s['nombre']} ({s['score']}/100)")
            lines.append(f"    {s['detalle']}")
        lines.append("")
    if atencion and len(user["sectores"]) > 1:
        lines.append("⚠️ *En atención:*")
        for s in atencion[:2]:
            lines.append(f"  • {s['nombre']} ({s['score']}/100)")
        lines.append("")
    lines.append(f"📱 {PANEL_BASE_URL}#{user['slug']}")
    return "\n".join(lines)


def send_whatsapp_alerts() -> dict:
    """Send weekly WhatsApp summary to all configured users."""
    sectores  = _get_sectores()
    fecha_str = datetime.now().strftime("%d/%m/%Y")
    results   = {}
    for user in _WA_ALERT_USERS:
        api_key = _env(user["env_key"])
        if not api_key:
            log.warning("⚠️ %s no configurada — WhatsApp omitido para %s", user["env_key"], user["nombre"])
            results[user["nombre"]] = None
            continue
        msg = _build_wa_message(user, sectores, fecha_str)
        results[user["nombre"]] = send_whatsapp(user["phone"], msg, api_key)

    sent = sum(1 for v in results.values() if v is True)
    skip = sum(1 for v in results.values() if v is None)
    fail = sum(1 for v in results.values() if v is False)
    log.info("WhatsApp semanal: %d enviados · %d omitidos (sin key) · %d fallidos", sent, skip, fail)
    return results


# ── Email ─────────────────────────────────────────────────────────────────────

def send_email(to: str, subject: str, body_html: str) -> bool:
    if not to or not GMAIL_PASS:
        log.warning("Email no configurado (to=%s, pass=%s)", bool(to), bool(GMAIL_PASS))
        return False
    try:
        recipients = [r.strip() for r in to.split(",") if r.strip()]
        msg = MIMEMultipart("mixed")
        msg["From"]    = GMAIL_USER
        msg["To"]      = ", ".join(recipients)
        msg["Subject"] = subject
        msg.attach(MIMEText(body_html, "html"))
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(GMAIL_USER, GMAIL_PASS)
            server.sendmail(GMAIL_USER, recipients, msg.as_string())
        log.info("Email enviado a %s: %s", recipients, subject)
        return True
    except Exception as e:
        log.error("Email falló a %s: %s", to, e)
        return False


def _html_wrap(title: str, body: str, panel_url: str = "") -> str:
    panel_line = (
        f'<p style="color:#c9a84c;font-size:12px">📱 Panel: '
        f'<a href="{panel_url}" style="color:#c9a84c">{panel_url}</a></p>'
    ) if panel_url else ""
    return f"""
<html><body style="font-family:Arial,sans-serif;background:#06080b;color:#f2ede4;padding:20px">
<h2 style="color:#c9a84c">{title}</h2>
{body}
{panel_line}
<hr style="border-color:#c9a84c44">
<p style="color:#9aa0a8;font-size:12px">
Faro Protocol · Fortín Inteligente · protocolfaro@gmail.com<br>
Generado automáticamente · {datetime.now().strftime('%d/%m/%Y %H:%M')} UTC-3
</p></body></html>"""


def _body_roger(panel_url: str = "") -> str:
    return _html_wrap("Tu Mapa de Trabajo Esta Semana — Villa Olímpica", """
        <p><b>Roger,</b> te mando el estado de las canchas esta semana.</p>
        <h3 style="color:#e74c3c">HOY — NO PUEDE ESPERAR</h3>
        <ul style="font-size:15px;line-height:1.8">
          <li><b>4FA — zona central y lateral sur:</b> fungicida + drenaje + resembrar</li>
        </ul>
        <h3 style="color:#f0b429">Esta semana</h3>
        <ul style="font-size:15px;line-height:1.8">
          <li><b>1FA y 3FA:</b> fungicida preventivo antes de que avance</li>
          <li><b>2FA:</b> fertilizar todo el campo</li>
        </ul>
        <h3 style="color:#27ae60">Sin apuro</h3>
        <ul style="font-size:15px;line-height:1.8">
          <li><b>Amalfitani:</b> aerificar las dos porterías</li>
        </ul>
        """, panel_url)


def _body_juan(panel_url: str = "") -> str:
    return _html_wrap("Estado Villa Olímpica Esta Semana", f"""
        <p>Juan, estado actualizado de las canchas, campo y Polideportivo.</p>
        <h3 style="color:#e74c3c">Urgente — 4FA</h3>
        <ul style="font-size:14px">
          <li>Focos activos de hongo en zona central — fungicida HOY</li>
          <li>Drenaje lateral sur roto — agua estancada confirmada</li>
        </ul>
        <h3 style="color:#f0b429">Polideportivo Feijóo</h3>
        <ul style="font-size:14px">
          <li><b>Básquet (ext):</b> fisuras detectadas — inspección urgente</li>
        </ul>
        <h3 style="color:#f0b429">Amalfitani</h3>
        <ul style="font-size:14px">
          <li>1FA–3FA: tratamientos agronómicos en curso</li>
          <li>Campo principal: buen estado general</li>
        </ul>
        """, panel_url)


def _body_banchero(panel_url: str = "") -> str:
    return _html_wrap(f"Estado Operativo del Predio — Vélez Sarsfield · {datetime.now().strftime('%d/%m/%Y')}", f"""
        <p>Fernando, resumen operativo completo.</p>
        <table style="color:#f2ede4;border-collapse:collapse;width:100%;font-size:14px">
          <tr style="background:#141c24">
            <th style="padding:8px;border:1px solid #c9a84c44">Área</th>
            <th style="padding:8px;border:1px solid #c9a84c44">Estado</th>
            <th style="padding:8px;border:1px solid #c9a84c44">Acción</th>
          </tr>
          <tr><td style="padding:6px;border:1px solid #c9a84c22">4FA</td>
              <td style="padding:6px;color:#e74c3c;border:1px solid #c9a84c22"><b>CRÍTICO</b></td>
              <td style="padding:6px;border:1px solid #c9a84c22">Intervención urgente</td></tr>
          <tr><td style="padding:6px;border:1px solid #c9a84c22">1FA, 2FA, 3FA</td>
              <td style="padding:6px;color:#f0b429;border:1px solid #c9a84c22"><b>ATENCIÓN</b></td>
              <td style="padding:6px;border:1px solid #c9a84c22">Acciones programadas</td></tr>
          <tr><td style="padding:6px;border:1px solid #c9a84c22">Amalfitani</td>
              <td style="padding:6px;color:#27ae60;border:1px solid #c9a84c22"><b>ÓPTIMO</b></td>
              <td style="padding:6px;border:1px solid #c9a84c22">Sin acción inmediata</td></tr>
          <tr><td style="padding:6px;border:1px solid #c9a84c22">Polideportivo</td>
              <td style="padding:6px;color:#f0b429;border:1px solid #c9a84c22"><b>ATENCIÓN</b></td>
              <td style="padding:6px;border:1px solid #c9a84c22">Básquet — inspección</td></tr>
        </table>
        """, panel_url)


def _body_pait(panel_url: str = "") -> str:
    return _html_wrap("Estado Canchas — Visión Deportiva", """
        <p>Sebastián, estado de superficies para esta semana.</p>
        <h3 style="color:#e74c3c">NO APTA para entrenamiento</h3>
        <ul style="font-size:14px"><li><b>4FA:</b> hongo activo + drenaje roto + pasto ralo</li></ul>
        <h3 style="color:#f0b429">Uso condicionado</h3>
        <ul style="font-size:14px">
          <li><b>1FA:</b> apta con fungicida preventivo antes del uso</li>
          <li><b>3FA:</b> trabajos livianos — tratamiento en curso</li>
        </ul>
        <h3 style="color:#27ae60">Óptimas para uso</h3>
        <ul style="font-size:14px">
          <li><b>2FA:</b> apta — fertilización esta semana</li>
          <li><b>Amalfitani:</b> excelente condición</li>
        </ul>
        """, panel_url)


def _body_berlanga(panel_url: str = "") -> str:
    return _html_wrap(f"Informe Ejecutivo Semanal — {datetime.now().strftime('%d/%m/%Y')}", f"""
        <p>Fabián, resumen ejecutivo satelital del predio.</p>
        <ul style="font-size:14px;line-height:1.8">
          <li><b>4FA:</b> intervención urgente esta semana</li>
          <li><b>Sistema Solar:</b> eficiencia por debajo de objetivo — revisión técnica</li>
          <li><b>Piletas:</b> estado óptimo — mantener protocolo</li>
        </ul>
        """, panel_url)


def _body_nelson(panel_url: str = "") -> str:
    return _html_wrap(f"Informe Ejecutivo Semanal — {datetime.now().strftime('%d/%m/%Y')}", f"""
        <p>Nelson, resumen ejecutivo satelital del predio.</p>
        <ul style="font-size:14px;line-height:1.8">
          <li><b>4FA:</b> crítica — intervención urgente esta semana</li>
          <li><b>Sistema Solar:</b> Eficiencia por debajo de objetivo — Zona E requiere revisión</li>
          <li><b>Polideportivo:</b> Básquet y Playón Norte en atención</li>
          <li><b>Complejo Acuático:</b> calidad agua excelente</li>
        </ul>
        """, panel_url)


def _body_aveleyra(panel_url: str = "") -> str:
    return _html_wrap(f"Dashboard Ejecutivo — {datetime.now().strftime('%d/%m/%Y')}", f"""
        <p>Alberto, dashboard ejecutivo satelital.</p>
        <ul style="font-size:14px;line-height:1.8">
          <li><b>4FA + Polideportivo:</b> intervenciones urgentes — impacto directo en operación</li>
          <li><b>Solar:</b> revisar paneles en falla — pérdida económica activa</li>
          <li><b>Detección anticipada:</b> 80% de problemas identificados en etapa temprana</li>
        </ul>
        """, panel_url)


_BODY_FN_MAP = {
    "Roger Bernal":       _body_roger,
    "Juan Gonzalez":      _body_juan,
    "Juan González":      _body_juan,
    "Fernando Banchero":  _body_banchero,
    "Sebastian Pait":     _body_pait,
    "Sebastián Pait":     _body_pait,
    "Fabian Berlanga":    _body_berlanga,
    "Fabián Berlanga":    _body_berlanga,
    "Nelson Pugliese":    _body_nelson,
    "Alberto Aveleyra":   _body_aveleyra,
}

_SLUG_MAP = {
    "Roger Bernal":       "roger",
    "Juan Gonzalez":      "juan",
    "Juan González":      "juan",
    "Fernando Banchero":  "banchero",
    "Sebastian Pait":     "pait",
    "Sebastián Pait":     "pait",
    "Fabian Berlanga":    "berlanga",
    "Fabián Berlanga":    "berlanga",
    "Nelson Pugliese":    "nelson",
    "Alberto Aveleyra":   "aveleyra",
}


def send_all_reports(config: dict = None) -> dict:
    """
    Send weekly email to each recipient in config.destinatarios.
    No PDF attachments (not available on Railway) — HTML body only.
    """
    if config is None:
        config = load_config()
    date_str = datetime.now().strftime("%d/%m/%Y")
    results  = {}
    for d in config.get("destinatarios", []):
        nombre = d.get("nombre", "")
        email  = d.get("email", "")
        if not nombre or not email:
            continue
        slug      = _SLUG_MAP.get(nombre, nombre.lower().split()[0])
        panel_url = f"{PANEL_BASE_URL}#{slug}"
        body_fn   = _BODY_FN_MAP.get(nombre, lambda p="": _html_wrap(
            "Reporte Semanal — Vélez Sarsfield",
            f"<p>{nombre}, reporte satelital semanal del Club Atlético Vélez Sarsfield.</p>"
        ))
        ok = send_email(email, f"Faro · Reporte semanal · Vélez · {date_str}",
                        body_fn(panel_url=panel_url))
        results[nombre] = ok
        log.info("Email %s → %s", nombre, "OK" if ok else "FAIL")
    return results


# ── Weekly job ────────────────────────────────────────────────────────────────

_last_weekly: dict = {}


def run_weekly_job() -> dict:
    """Main weekly job: refresh weather data + send WhatsApp alerts + send emails."""
    global _last_weekly
    log.info("=== Weekly job starting ===")
    config = load_config()

    # 1. Update weather data (push to GitHub)
    try:
        from data_refresh import run_refresh
        refresh_result = run_refresh()
        log.info("Weather refresh: %s", "OK" if refresh_result.get("ok") else refresh_result.get("error"))
    except Exception as e:
        log.error("Weather refresh failed: %s", e)
        refresh_result = {"ok": False, "error": str(e)}

    # 2. Send WhatsApp alerts
    wa_results = {}
    try:
        wa_results = send_whatsapp_alerts()
    except Exception as e:
        log.error("WhatsApp alerts failed: %s", e)

    # 3. Send emails
    email_results = {}
    try:
        email_results = send_all_reports(config)
    except Exception as e:
        log.error("Email send failed: %s", e)

    _last_weekly = {
        "ran_at":        datetime.utcnow().isoformat(),
        "weather_ok":    refresh_result.get("ok"),
        "whatsapp_sent": sum(1 for v in wa_results.values() if v is True),
        "whatsapp_skip": sum(1 for v in wa_results.values() if v is None),
        "emails_sent":   sum(1 for v in email_results.values() if v is True),
    }
    log.info("=== Weekly job done: %s ===", _last_weekly)
    return _last_weekly


def get_last_weekly() -> dict:
    return _last_weekly


# ── APScheduler integration ───────────────────────────────────────────────────

def register_jobs(scheduler) -> None:
    """Register the weekly job on the given APScheduler instance."""
    from apscheduler.triggers.cron import CronTrigger
    # Lunes 07:00 ART = Lunes 10:00 UTC
    scheduler.add_job(
        run_weekly_job,
        CronTrigger(day_of_week="mon", hour=10, minute=0, timezone="UTC"),
        id="weekly_report",
        replace_existing=True,
        misfire_grace_time=7200,
    )
    log.info("Weekly job registered — Lunes 07:00 ART (10:00 UTC)")


# ── Flask route handlers (registered in app.py) ───────────────────────────────

def route_run_now():
    from flask import request, jsonify
    tipo = (request.get_json(silent=True, force=True) or {}).get("tipo", "manual")
    threading.Thread(target=run_weekly_job, daemon=True).start()
    return jsonify({"status": "started", "tipo": tipo})


def route_weekly_status():
    from flask import jsonify
    return jsonify({
        "last_weekly":  _last_weekly,
        "next_weekly":  "Monday 07:00 ART (10:00 UTC)",
    })


def route_test_whatsapp():
    from flask import request, jsonify
    nombre = (request.get_json(silent=True) or {}).get("nombre")
    users  = [u for u in _WA_ALERT_USERS if not nombre or u["nombre"] == nombre]
    results = {}
    for u in users:
        api_key = _env(u["env_key"])
        msg = f"Faro Protocol · TEST desde Railway · {datetime.now().strftime('%d/%m %H:%M')}"
        results[u["nombre"]] = send_whatsapp(u["phone"], msg, api_key) if api_key else None
    return jsonify({"status": "ok", "results": results})


def route_test_email():
    from flask import jsonify
    config   = load_config()
    now_str  = datetime.now().strftime("%d/%m/%Y %H:%M")
    results  = {}
    for d in config.get("destinatarios", []):
        nombre = d.get("nombre", ""); email = d.get("email", "")
        if not nombre or not email:
            continue
        ok = send_email(email, f"TEST · Faro Protocol · {now_str}",
                        _html_wrap("TEST", f"<p>{nombre} — email de prueba desde Railway.</p>"))
        results[nombre] = ok
    return jsonify({"status": "ok" if all(results.values()) else "partial", "results": results})
