"""
velez_scheduler.py — Vélez email + WhatsApp weekly notifications for Railway.
Adapted from faro_velez_scheduler.py: no local scripts, no PDF attachments,
no `schedule` library (uses APScheduler from app.py).

Config is fetched from GitHub raw URL so it works in stateless Railway containers.
"""
import json, logging, os, smtplib, threading
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from urllib.request import urlopen, Request as UReq

import requests

log = logging.getLogger(__name__)

# ── Config ────────────────────────────────────────────────────────────────────

PANEL_BASE_URL = "https://protocolfaro.github.io/faro-paneles/velez/"
_CFG_RAW_URL   = "https://raw.githubusercontent.com/protocolfaro/faro-paneles/main/velez/config_velez.json"
_VD_RAW_URL    = "https://raw.githubusercontent.com/protocolfaro/faro-paneles/main/velez/velez_data.json"

GMAIL_USER   = os.environ.get("GMAIL_USER", "protocolfaro@gmail.com")
GMAIL_PASS   = os.environ.get("GMAIL_APP_PASS", "")
BREVO_KEY    = os.environ.get("BREVO_API_KEY", "")

_SEM_COLOR = {"verde": "#27ae60", "amarillo": "#f0b429", "rojo": "#e74c3c"}
_SEM_LABEL = {"verde": "ÓPTIMO",  "amarillo": "ATENCIÓN", "rojo": "CRÍTICO"}
_SEM_EMOJI = {"verde": "✅",       "amarillo": "⚠️",        "rojo": "🚨"}
_SEM_ORDER = {"verde": 0,          "amarillo": 1,           "rojo": 2}

NDVI_ALERT  = 0.35
INSAR_ALERT = 3.0


def _env(key: str, default: str = "") -> str:
    return os.environ.get(key, default)


def load_config() -> dict:
    try:
        req = UReq(_CFG_RAW_URL, headers={"User-Agent": "FaroProtocol/4.0"})
        with urlopen(req, timeout=10) as r:
            return json.loads(r.read())
    except Exception as e:
        log.warning("load_config: %s", e)
        return {"zonas": [], "destinatarios": []}


def _get_velez_data() -> dict:
    """Fetch full velez_data.json from GitHub raw. Returns {} on network error."""
    try:
        req = UReq(_VD_RAW_URL, headers={"User-Agent": "FaroProtocol/4.0"})
        with urlopen(req, timeout=10) as r:
            return json.loads(r.read())
    except Exception as e:
        log.warning("_get_velez_data: %s", e)
        return {}


def _get_sectores() -> dict:
    return _get_velez_data().get("sectores", {})


# ── WhatsApp (CallMeBot) ──────────────────────────────────────────────────────

_WA_ALERT_USERS = [
    {"nombre": "Roger Bernal",      "slug": "roger",    "phone": "541124642616", "env_key": "CALLMEBOT_KEY_ROGER",    "sectores": ["canchero"]},
    {"nombre": "Juan González",     "slug": "juan",     "phone": "541151073109", "env_key": "CALLMEBOT_KEY_JUAN",     "sectores": ["canchero","agro","poli"]},
    {"nombre": "Fernando Banchero", "slug": "banchero", "phone": "541167096384", "env_key": "CALLMEBOT_KEY_BANCHERO", "sectores": ["estadio","agro","solar","canchero","sede","poli","piletas"]},
    {"nombre": "Nelson Pugliese",   "slug": "nelson",   "phone": "541156417353", "env_key": "CALLMEBOT_KEY_NELSON",   "sectores": ["estadio","agro","solar","canchero","sede","poli","piletas"]},
]


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
    scores       = [s["score"] for s in user_sects if isinstance(s.get("score"), (int, float))]
    avg          = round(sum(scores) / len(scores)) if scores else 0
    worst        = max(user_sects, key=lambda s: _SEM_ORDER.get(s.get("sem","verde"), 0), default={})
    worst_sem    = worst.get("sem", "verde") if worst else "verde"
    criticos     = [s for s in user_sects if s.get("sem") == "rojo"]
    atencion     = [s for s in user_sects if s.get("sem") == "amarillo"]

    lines = [
        "*Faro Protocol — Vélez Sarsfield*",
        f"Semana {fecha}", "",
        f"Hola {nombre_short}, tu resumen:",
        f"{_SEM_EMOJI[worst_sem]} Score: *{avg}/100*", "",
    ]
    if criticos:
        lines.append("🚨 *Acción urgente:*")
        for s in criticos:
            lines.append(f"  • {s.get('nombre','?')} ({s.get('score','?')}/100)")
            lines.append(f"    {s.get('detalle','')}")
        lines.append("")
    if atencion and len(user["sectores"]) > 1:
        lines.append("⚠️ *En atención:*")
        for s in atencion[:2]:
            lines.append(f"  • {s.get('nombre','?')} ({s.get('score','?')}/100)")
        lines.append("")
    lines.append(f"📱 {PANEL_BASE_URL}#{user['slug']}")
    return "\n".join(lines)


def send_whatsapp_alerts(vd: dict = None) -> dict:
    """Send weekly WhatsApp summary. Silently skips users without a configured key."""
    if vd is None:
        vd = _get_velez_data()
    sectores  = vd.get("sectores", {})
    fecha_str = datetime.now().strftime("%d/%m/%Y")
    results   = {}
    for user in _WA_ALERT_USERS:
        api_key = _env(user["env_key"])
        if not api_key:
            # Expected state until keys are loaded in Railway — not an error
            log.info("WhatsApp: %s sin key configurada — omitido hasta que se cargue %s en Railway",
                     user["nombre"], user["env_key"])
            results[user["nombre"]] = None
            continue
        msg = _build_wa_message(user, sectores, fecha_str)
        results[user["nombre"]] = send_whatsapp(user["phone"], msg, api_key)

    sent = sum(1 for v in results.values() if v is True)
    skip = sum(1 for v in results.values() if v is None)
    fail = sum(1 for v in results.values() if v is False)
    log.info("WhatsApp semanal: %d enviados · %d sin key · %d fallidos", sent, skip, fail)
    return results


# ── Email ─────────────────────────────────────────────────────────────────────

def _send_via_brevo(to: str, subject: str, body_html: str) -> bool:
    """Send via Brevo SMTP API (HTTPS/443 — not blocked by Railway).
    Docs: https://developers.brevo.com/reference/sendtransacemail
    """
    if not BREVO_KEY:
        return False
    try:
        resp = requests.post(
            "https://api.brevo.com/v3/smtp/email",
            headers={"api-key": BREVO_KEY, "Content-Type": "application/json"},
            json={
                "sender":      {"name": "Faro Protocol", "email": GMAIL_USER},
                "to":          [{"email": to}],
                "subject":     subject,
                "htmlContent": body_html,
            },
            timeout=30,
        )
        if resp.status_code in (200, 201):
            log.info("Brevo OK → %s: %s", to, subject)
            return True
        log.error("Brevo HTTP %s → %s: %s", resp.status_code, to, resp.text[:200])
        return False
    except Exception as e:
        log.error("Brevo exception → %s: %s", to, e)
        return False


def _smtp_send_ipv4(host: str, port: int, user: str, password: str,
                    recipients: list, msg_str: str, timeout: int = 30) -> None:
    """Connect to SMTP forcing IPv4 (fallback when Resend not configured)."""
    import ssl, socket as _sock
    _orig = _sock.getaddrinfo
    def _ipv4_only(h, p, family=0, type=0, proto=0, flags=0):  # noqa: A002
        return _orig(h, p, _sock.AF_INET, type, proto, flags)
    _sock.getaddrinfo = _ipv4_only
    try:
        ctx = ssl.create_default_context()
        with smtplib.SMTP_SSL(host, port, timeout=timeout, context=ctx) as server:
            server.login(user, password)
            server.sendmail(user, recipients, msg_str)
    finally:
        _sock.getaddrinfo = _orig


def send_email(to: str, subject: str, body_html: str) -> bool:
    # Primary: Brevo SMTP API (HTTPS/443 — works on Railway)
    if BREVO_KEY:
        return _send_via_brevo(to, subject, body_html)
    # Fallback: direct Gmail SMTP (may be blocked by cloud providers)
    if not GMAIL_PASS:
        log.warning("Email sin configurar: BREVO_API_KEY y GMAIL_APP_PASS ambos vacíos")
        return False
    try:
        recipients = [r.strip() for r in to.split(",") if r.strip()]
        msg = MIMEMultipart("mixed")
        msg["From"]    = GMAIL_USER
        msg["To"]      = ", ".join(recipients)
        msg["Subject"] = subject
        msg.attach(MIMEText(body_html, "html"))
        _smtp_send_ipv4("smtp.gmail.com", 465, GMAIL_USER, GMAIL_PASS,
                        recipients, msg.as_string())
        log.info("SMTP enviado a %s: %s", recipients, subject)
        return True
    except Exception as e:
        log.error("SMTP falló a %s: %s", to, e)
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


# ── Email bodies — leen datos reales de velez_data.json ───────────────────────

def _body_roger(vd: dict, panel_url: str = "") -> str:
    wl  = vd.get("weather_live", {})
    sec = vd.get("sectores", {}).get("canchero", {})

    score    = sec.get("score", "—")
    sem      = sec.get("sem", "verde")

    fung     = wl.get("riesgo_fungosis", {})
    fung_niv = fung.get("nivel", "bajo")
    fung_c   = _SEM_COLOR.get(fung_niv, "#27ae60")
    fung_des = fung.get("descripcion", "Sin datos de riesgo fungoso")
    fung_acc = fung.get("accion_recomendada", "")
    canch_r  = fung.get("canchas_en_riesgo", [])

    deficit  = float(wl.get("deficit_hidrico_mm") or 0)
    riego_m  = wl.get("riego_min_sector", 0)
    hora_r   = wl.get("hora_riego_optima", "06:00")
    dias_c   = wl.get("dias_proximo_corte", "—")

    ndvi_raw   = wl.get("gndvi_por_cancha") or {}
    ndvi_canch = ndvi_raw.get("canchas", {}) if isinstance(ndvi_raw, dict) else {}
    ndvi_fuent = ndvi_raw.get("fuente", "estimado") if isinstance(ndvi_raw, dict) else "estimado"

    _ns_c = {"ok": "#27ae60", "borderline": "#f0b429", "bajo": "#f0b429", "grave": "#e74c3c"}
    ndvi_html = ""
    if ndvi_canch:
        rows = "".join(
            f'<tr>'
            f'<td style="padding:4px 8px;border:1px solid #c9a84c22">{cid.upper()}</td>'
            f'<td style="padding:4px 8px;border:1px solid #c9a84c22">{cd.get("gndvi", cd.get("ndvi","—"))}</td>'
            f'<td style="padding:4px 8px;color:{_ns_c.get(cd.get("n_status","ok"),"#27ae60")};border:1px solid #c9a84c22">{cd.get("n_status","—")}</td>'
            f'<td style="padding:4px 8px;border:1px solid #c9a84c22;font-size:12px">{cd.get("n_rec","")}</td>'
            f'</tr>'
            for cid, cd in sorted(ndvi_canch.items())
        )
        ndvi_html = (
            f'<h3 style="color:#c9a84c">NDVI por Cancha</h3>'
            f'<table style="color:#f2ede4;border-collapse:collapse;width:100%;font-size:13px">'
            f'<tr style="background:#141c24">'
            f'<th style="padding:4px 8px;border:1px solid #c9a84c44">Cancha</th>'
            f'<th style="padding:4px 8px;border:1px solid #c9a84c44">GNDVI</th>'
            f'<th style="padding:4px 8px;border:1px solid #c9a84c44">N</th>'
            f'<th style="padding:4px 8px;border:1px solid #c9a84c44">Recomendación</th>'
            f'</tr>{rows}</table>'
            f'<p style="color:#9aa0a8;font-size:11px">Fuente: {ndvi_fuent}</p>'
        )

    riesgo_html = f'<p style="color:{fung_c}"><b>Riesgo fungoso: {fung_niv.upper()}</b> — {fung_des}</p>'
    if fung_acc:
        riesgo_html += f'<p>→ <b>{fung_acc}</b></p>'
    if canch_r:
        riesgo_html += f'<p style="color:#9aa0a8">Canchas en riesgo: {", ".join(c.upper() for c in canch_r)}</p>'

    riego_html = (
        f'<p><b>Riego:</b> {riego_m} min · hora óptima {hora_r} ART · déficit {deficit:.1f} mm</p>'
        if riego_m > 0 else
        '<p><b>Riego:</b> Sin déficit hídrico significativo esta semana</p>'
    )

    body = (
        f'<p><b>Roger,</b> estado de las canchas esta semana.</p>'
        f'<p>Score canchero: <b style="color:{_SEM_COLOR.get(sem,"#27ae60")}">'
        f'{score}/100 — {_SEM_LABEL.get(sem,"—")}</b></p>'
        f'{riesgo_html}'
        f'{riego_html}'
        f'<p><b>Próximo corte estimado:</b> en {dias_c} días</p>'
        f'{ndvi_html}'
    )
    return _html_wrap("Tu Mapa de Trabajo Esta Semana — Villa Olímpica", body, panel_url)


def _body_juan(vd: dict, panel_url: str = "") -> str:
    wl  = vd.get("weather_live", {})
    sec = vd.get("sectores", {})

    rows = ""
    for k in ("canchero", "agro", "poli"):
        s   = sec.get(k, {})
        sem = s.get("sem", "verde")
        rows += (
            f'<tr>'
            f'<td style="padding:6px;border:1px solid #c9a84c22">{s.get("nombre", k.title())}</td>'
            f'<td style="padding:6px;color:{_SEM_COLOR.get(sem,"#27ae60")};border:1px solid #c9a84c22">'
            f'<b>{_SEM_LABEL.get(sem,"—")}</b> ({s.get("score","—")}/100)</td>'
            f'<td style="padding:6px;border:1px solid #c9a84c22;font-size:13px">{s.get("detalle","—")}</td>'
            f'</tr>'
        )

    fung     = wl.get("riesgo_fungosis", {})
    fung_niv = fung.get("nivel", "bajo")
    fung_c   = _SEM_COLOR.get(fung_niv, "#27ae60")
    fung_acc = fung.get("accion_recomendada", "")
    deficit  = float(wl.get("deficit_hidrico_mm") or 0)

    body = (
        f'<p>Juan, estado actualizado de canchas, campo y Polideportivo.</p>'
        f'<table style="color:#f2ede4;border-collapse:collapse;width:100%;font-size:14px">'
        f'<tr style="background:#141c24">'
        f'<th style="padding:6px;border:1px solid #c9a84c44">Área</th>'
        f'<th style="padding:6px;border:1px solid #c9a84c44">Estado</th>'
        f'<th style="padding:6px;border:1px solid #c9a84c44">Detalle</th>'
        f'</tr>{rows}</table>'
        f'<p style="margin-top:12px"><b>Riesgo fungoso:</b> '
        f'<span style="color:{fung_c}">{fung_niv.upper()}</span>'
        f'{(" — " + fung_acc) if fung_acc else ""}</p>'
        f'<p><b>Déficit hídrico semanal:</b> {deficit:.1f} mm</p>'
    )
    return _html_wrap("Estado Villa Olímpica Esta Semana", body, panel_url)


def _body_banchero(vd: dict, panel_url: str = "") -> str:
    sec   = vd.get("sectores", {})
    wl    = vd.get("weather_live", {})
    fecha = datetime.now().strftime('%d/%m/%Y')

    rows = ""
    for k in ("estadio", "agro", "solar", "canchero", "sede", "poli", "piletas"):
        s   = sec.get(k, {})
        sem = s.get("sem", "verde")
        acc = {"verde": "Sin acción inmediata", "amarillo": "Monitorear",
               "rojo":  "Intervención urgente"}.get(sem, "—")
        rows += (
            f'<tr>'
            f'<td style="padding:6px;border:1px solid #c9a84c22">{s.get("nombre", k.title())}</td>'
            f'<td style="padding:6px;color:{_SEM_COLOR.get(sem,"#27ae60")};border:1px solid #c9a84c22">'
            f'<b>{_SEM_LABEL.get(sem,"—")}</b></td>'
            f'<td style="padding:6px;border:1px solid #c9a84c22">{s.get("score","—")}/100</td>'
            f'<td style="padding:6px;border:1px solid #c9a84c22;font-size:12px">{s.get("detalle","—")}</td>'
            f'<td style="padding:6px;border:1px solid #c9a84c22">{acc}</td>'
            f'</tr>'
        )

    deficit = float(wl.get("deficit_hidrico_mm") or 0)
    ts      = (wl.get("timestamp") or "")[:10] or "—"

    body = (
        f'<p>Fernando, resumen operativo completo del predio.</p>'
        f'<table style="color:#f2ede4;border-collapse:collapse;width:100%;font-size:13px">'
        f'<tr style="background:#141c24">'
        f'<th style="padding:8px;border:1px solid #c9a84c44">Área</th>'
        f'<th style="padding:8px;border:1px solid #c9a84c44">Estado</th>'
        f'<th style="padding:8px;border:1px solid #c9a84c44">Score</th>'
        f'<th style="padding:8px;border:1px solid #c9a84c44">Detalle</th>'
        f'<th style="padding:8px;border:1px solid #c9a84c44">Acción</th>'
        f'</tr>{rows}</table>'
        f'<p style="margin-top:10px;color:#9aa0a8;font-size:12px">'
        f'Déficit hídrico: {deficit:.1f} mm · Datos al {ts}</p>'
    )
    return _html_wrap(f"Estado Operativo del Predio — Vélez Sarsfield · {fecha}", body, panel_url)


def _body_pait(vd: dict, panel_url: str = "") -> str:
    wl  = vd.get("weather_live", {})
    sec = vd.get("sectores", {})

    rows = ""
    for k in ("canchero", "poli"):
        s   = sec.get(k, {})
        sem = s.get("sem", "verde")
        rows += (
            f'<tr>'
            f'<td style="padding:6px;border:1px solid #c9a84c22">{s.get("nombre", k.title())}</td>'
            f'<td style="padding:6px;color:{_SEM_COLOR.get(sem,"#27ae60")};border:1px solid #c9a84c22">'
            f'<b>{_SEM_LABEL.get(sem,"—")}</b> ({s.get("score","—")}/100)</td>'
            f'<td style="padding:6px;border:1px solid #c9a84c22;font-size:13px">{s.get("detalle","—")}</td>'
            f'</tr>'
        )

    fung     = wl.get("riesgo_fungosis", {})
    fung_niv = fung.get("nivel", "bajo")
    fung_c   = _SEM_COLOR.get(fung_niv, "#27ae60")
    fung_acc = fung.get("accion_recomendada", "")
    canch_r  = fung.get("canchas_en_riesgo", [])

    body = (
        f'<p>Sebastián, estado de superficies para esta semana.</p>'
        f'<table style="color:#f2ede4;border-collapse:collapse;width:100%;font-size:14px">'
        f'<tr style="background:#141c24">'
        f'<th style="padding:6px;border:1px solid #c9a84c44">Área</th>'
        f'<th style="padding:6px;border:1px solid #c9a84c44">Estado</th>'
        f'<th style="padding:6px;border:1px solid #c9a84c44">Detalle</th>'
        f'</tr>{rows}</table>'
        f'<p style="margin-top:10px"><b>Riesgo fungoso: '
        f'<span style="color:{fung_c}">{fung_niv.upper()}</span></b>'
        f'{(" — " + fung_acc) if fung_acc else ""}</p>'
        + (f'<p style="color:#f0b429">Canchas en riesgo: {", ".join(c.upper() for c in canch_r)}</p>'
           if canch_r else '')
    )
    return _html_wrap("Estado Canchas — Visión Deportiva", body, panel_url)


def _body_ejecutivo(vd: dict, nombre: str, panel_url: str = "") -> str:
    """Generic executive report: full sector table + timestamp."""
    sec   = vd.get("sectores", {})
    wl    = vd.get("weather_live", {})
    fecha = datetime.now().strftime('%d/%m/%Y')
    ts    = (wl.get("timestamp") or "")[:10] or "—"

    rows = ""
    for k in ("estadio", "agro", "solar", "canchero", "sede", "poli", "piletas"):
        s   = sec.get(k, {})
        sem = s.get("sem", "verde")
        rows += (
            f'<tr>'
            f'<td style="padding:6px;border:1px solid #c9a84c22">{s.get("nombre", k.title())}</td>'
            f'<td style="padding:6px;color:{_SEM_COLOR.get(sem,"#27ae60")};border:1px solid #c9a84c22">'
            f'<b>{_SEM_LABEL.get(sem,"—")}</b></td>'
            f'<td style="padding:6px;border:1px solid #c9a84c22">{s.get("score","—")}/100</td>'
            f'<td style="padding:6px;border:1px solid #c9a84c22;font-size:12px">{s.get("detalle","—")}</td>'
            f'</tr>'
        )

    body = (
        f'<p>{nombre}, resumen ejecutivo satelital del predio.</p>'
        f'<table style="color:#f2ede4;border-collapse:collapse;width:100%;font-size:13px">'
        f'<tr style="background:#141c24">'
        f'<th style="padding:6px;border:1px solid #c9a84c44">Área</th>'
        f'<th style="padding:6px;border:1px solid #c9a84c44">Estado</th>'
        f'<th style="padding:6px;border:1px solid #c9a84c44">Score</th>'
        f'<th style="padding:6px;border:1px solid #c9a84c44">Detalle</th>'
        f'</tr>{rows}</table>'
        f'<p style="color:#9aa0a8;font-size:12px;margin-top:8px">Datos al {ts}</p>'
    )
    return _html_wrap(f"Informe Ejecutivo Semanal — {fecha}", body, panel_url)


def _body_berlanga(vd: dict, panel_url: str = "") -> str:
    return _body_ejecutivo(vd, "Fabián", panel_url)

def _body_nelson(vd: dict, panel_url: str = "") -> str:
    return _body_ejecutivo(vd, "Nelson", panel_url)

def _body_aveleyra(vd: dict, panel_url: str = "") -> str:
    return _body_ejecutivo(vd, "Alberto", panel_url)


_BODY_FN_MAP = {
    "Roger Bernal":      _body_roger,
    "Juan Gonzalez":     _body_juan,
    "Juan González":     _body_juan,
    "Fernando Banchero": _body_banchero,
    "Sebastian Pait":    _body_pait,
    "Sebastián Pait":    _body_pait,
    "Fabian Berlanga":   _body_berlanga,
    "Fabián Berlanga":   _body_berlanga,
    "Nelson Pugliese":   _body_nelson,
    "Alberto Aveleyra":  _body_aveleyra,
}

_SLUG_MAP = {
    "Roger Bernal":      "roger",
    "Juan Gonzalez":     "juan",
    "Juan González":     "juan",
    "Fernando Banchero": "banchero",
    "Sebastian Pait":    "pait",
    "Sebastián Pait":    "pait",
    "Fabian Berlanga":   "berlanga",
    "Fabián Berlanga":   "berlanga",
    "Nelson Pugliese":   "nelson",
    "Alberto Aveleyra":  "aveleyra",
}


def send_all_reports(config: dict = None, vd: dict = None) -> dict:
    """Send weekly HTML email to each destinatario. vd reused to avoid double GitHub fetch."""
    if config is None:
        config = load_config()
    if vd is None:
        vd = _get_velez_data()
    date_str = datetime.now().strftime("%d/%m/%Y")
    results  = {}
    for d in config.get("destinatarios", []):
        nombre = d.get("nombre", "")
        email  = d.get("email", "")
        if not nombre or not email:
            continue
        slug      = _SLUG_MAP.get(nombre, nombre.lower().split()[0])
        panel_url = f"{PANEL_BASE_URL}#{slug}"
        body_fn   = _BODY_FN_MAP.get(nombre)
        if body_fn:
            try:
                body_html = body_fn(vd, panel_url=panel_url)
            except Exception as e:
                log.warning("body_fn %s: %s — usando fallback", nombre, e)
                body_html = _html_wrap(
                    "Reporte Semanal — Vélez Sarsfield",
                    f"<p>{nombre}, reporte satelital semanal del Club Atlético Vélez Sarsfield.</p>",
                    panel_url,
                )
        else:
            body_html = _html_wrap(
                "Reporte Semanal — Vélez Sarsfield",
                f"<p>{nombre}, reporte satelital semanal del Club Atlético Vélez Sarsfield.</p>",
                panel_url,
            )
        ok = send_email(email, f"Faro · Reporte semanal · Vélez · {date_str}", body_html)
        results[nombre] = ok
        log.info("Email %s → %s", nombre, "OK" if ok else "FAIL")
    return results


# ── Weekly job ────────────────────────────────────────────────────────────────

_last_weekly: dict = {}


def run_weekly_job() -> dict:
    """Weekly notifications: WhatsApp + email.
    Weather data is refreshed by the daily cron at 09:00 UTC (1h before this job runs).
    """
    global _last_weekly
    log.info("=== Weekly job starting ===")
    config = load_config()
    vd     = _get_velez_data()   # fetch once — shared by WhatsApp + email

    wa_results    = {}
    email_results = {}

    try:
        wa_results = send_whatsapp_alerts(vd=vd)
    except Exception as e:
        log.error("WhatsApp alerts failed: %s", e)

    try:
        email_results = send_all_reports(config, vd=vd)
    except Exception as e:
        log.error("Email send failed: %s", e)

    _last_weekly = {
        "ran_at":        datetime.utcnow().isoformat(),
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
        "last_weekly": _last_weekly,
        "next_weekly": "Monday 07:00 ART (10:00 UTC)",
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


def route_smtp_diag():
    """Quick SMTP connectivity + auth diagnostic — returns exact error."""
    import socket
    from flask import jsonify
    result: dict = {
        "gmail_user":       GMAIL_USER,
        "pass_set":         bool(GMAIL_PASS),
        "pass_len":         len(GMAIL_PASS),
        "ssl_465":          None,
        "starttls_587":     None,
        "login_ok":         None,
        "error":            None,
    }
    import ssl, socket as _sock
    # Helper: force IPv4 only resolution
    def _with_ipv4(fn):
        _orig = _sock.getaddrinfo
        def _gai(h, p, family=0, type=0, proto=0, flags=0):  # noqa: A002
            return _orig(h, p, _sock.AF_INET, type, proto, flags)
        _sock.getaddrinfo = _gai
        try:
            return fn()
        finally:
            _sock.getaddrinfo = _orig
    # Try SSL 465 with IPv4
    try:
        def _try465():
            ctx = ssl.create_default_context()
            with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=15, context=ctx) as s:
                result["ssl_465"] = True
                s.login(GMAIL_USER, GMAIL_PASS)
                result["login_ok"] = True
        _with_ipv4(_try465)
    except smtplib.SMTPAuthenticationError as e:
        result["ssl_465"] = True
        result["login_ok"] = False
        result["error"] = f"Auth failed (465 IPv4): {e}"
    except Exception as e:
        result["ssl_465"] = False
        result["error"] = f"SSL 465 IPv4 failed: {e}"
        # Try STARTTLS 587 with IPv4
        try:
            def _try587():
                with smtplib.SMTP("smtp.gmail.com", 587, timeout=15) as s:
                    s.starttls()
                    result["starttls_587"] = True
                    s.login(GMAIL_USER, GMAIL_PASS)
                    result["login_ok"] = True
            _with_ipv4(_try587)
        except smtplib.SMTPAuthenticationError as e2:
            result["starttls_587"] = True
            result["login_ok"] = False
            result["error"] = f"Auth failed (587 IPv4): {e2}"
        except Exception as e2:
            result["starttls_587"] = False
            result["error"] = f"Both 465+587 IPv4 failed: {e} | {e2}"
    return jsonify(result)


def route_test_email():
    from flask import request, jsonify
    data    = request.get_json(silent=True, force=True) or {}
    config  = load_config()
    now_str = datetime.now().strftime("%d/%m/%Y %H:%M")
    results = {}
    # Filter to a single recipient if provided, otherwise all destinatarios
    target_email = data.get("email")
    target_nombre = data.get("nombre")
    dest = config.get("destinatarios", [])
    if target_email or target_nombre:
        dest = [d for d in dest if
                (target_email and d.get("email") == target_email) or
                (target_nombre and d.get("nombre") == target_nombre)]
    for d in dest:
        nombre = d.get("nombre", ""); email = d.get("email", "")
        if not nombre or not email:
            continue
        ok = send_email(email, f"TEST · Faro Protocol · {now_str}",
                        _html_wrap("TEST", f"<p>{nombre} — email de prueba desde Railway.</p>"))
        results[nombre] = ok
    smtp_configured = bool(GMAIL_PASS)
    return jsonify({
        "status":           "ok" if results and all(results.values()) else ("partial" if any(results.values()) else "fail"),
        "smtp_configured":  smtp_configured,
        "gmail_user":       GMAIL_USER,
        "destinatarios_n":  len(config.get("destinatarios", [])),
        "results":          results,
    })


if __name__ == "__main__":
    import sys
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
    )
    results = send_all_reports()
    sent   = sum(1 for v in results.values() if v is True)
    failed = sum(1 for v in results.values() if v is False)
    log.info("=== Resultado: %d enviados · %d fallidos ===", sent, failed)
    sys.exit(0 if failed == 0 else 1)
