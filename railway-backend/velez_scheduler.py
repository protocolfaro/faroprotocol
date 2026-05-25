"""
velez_scheduler.py — Vélez email + WhatsApp weekly notifications for Railway.
Adapted from faro_velez_scheduler.py: no local scripts, no PDF attachments,
no `schedule` library (uses APScheduler from app.py).

Config is fetched from GitHub raw URL so it works in stateless Railway containers.
"""
import base64, json, logging, os, smtplib, threading
from datetime import datetime
from pathlib import Path
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders
from urllib.request import urlopen, Request as UReq

import requests

log = logging.getLogger(__name__)

# ── Config ────────────────────────────────────────────────────────────────────

PANEL_BASE_URL = "https://protocolfaro.github.io/faro-paneles/velez/"
_CFG_RAW_URL   = "https://raw.githubusercontent.com/protocolfaro/faro-paneles/main/velez/config_velez.json"
_VD_RAW_URL    = "https://raw.githubusercontent.com/protocolfaro/faro-paneles/main/velez/velez_data.json"
_PNG_BASE_URL  = "https://raw.githubusercontent.com/protocolfaro/faro-paneles/main/velez"

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
        log.info("WhatsApp %s -> %s", phone[:8] + "***", "OK" if ok else f"FAIL {r.status_code}")
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

def _fetch_png(url: str):
    """Fetch PNG from URL. Returns (filename, bytes) or None on any failure."""
    try:
        req = UReq(url, headers={"User-Agent": "FaroProtocol/4.0"})
        with urlopen(req, timeout=10) as r:
            data = r.read()
        if len(data) < 200:
            return None
        return (url.split("/")[-1], data)
    except Exception:
        return None


def _fetch_sector_attachments(sector_keys: list) -> list:
    """Fetch PNG heatmaps for given sector keys.
    Priority: local reportes_velez/ dir (supports _FINAL/_v2 suffixes) → GitHub raw.
    Returns list of (filename, bytes) — silently skips any that fail."""
    _local_dir = Path(__file__).parent.parent / "reportes_velez"
    result = []
    for key in sector_keys:
        # 1. Exact local match
        exact = _local_dir / f"faro_reporte_velez_{key}.png"
        if exact.exists():
            result.append((exact.name, exact.read_bytes()))
            continue
        # 2. Glob for local variants (_FINAL, _v2, etc.)
        matches = sorted(_local_dir.glob(f"faro_reporte_velez_{key}*.png"))
        if matches:
            p = matches[0]
            result.append((p.name, p.read_bytes()))
            continue
        # 3. GitHub raw (Railway — only works if file is committed to repo)
        att = _fetch_png(f"{_PNG_BASE_URL}/faro_reporte_velez_{key}.png")
        if att:
            result.append(att)
    return result


def _send_via_brevo(to: str, subject: str, body_html: str,
                    attachments: list = None) -> bool:
    """Send via Brevo SMTP API (HTTPS/443 — not blocked by Railway).
    attachments: list of (filename, bytes) tuples.
    """
    if not BREVO_KEY:
        return False
    payload = {
        "sender":      {"name": "Faro Protocol", "email": GMAIL_USER},
        "to":          [{"email": to}],
        "subject":     subject,
        "htmlContent": body_html,
    }
    if attachments:
        payload["attachment"] = [
            {"name": fn, "content": base64.b64encode(data).decode("ascii")}
            for fn, data in attachments
        ]
    try:
        resp = requests.post(
            "https://api.brevo.com/v3/smtp/email",
            headers={"api-key": BREVO_KEY, "Content-Type": "application/json"},
            json=payload,
            timeout=30,
        )
        if resp.status_code in (200, 201):
            log.info("Brevo OK -> %s: %s", to, subject)
            return True
        log.error("Brevo HTTP %s -> %s: %s", resp.status_code, to, resp.text[:200])
        return False
    except Exception as e:
        log.error("Brevo exception -> %s: %s", to, e)
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


def send_email(to: str, subject: str, body_html: str,
               attachments: list = None) -> bool:
    """Send email. attachments: list of (filename, bytes) tuples."""
    # Primary: Brevo SMTP API (HTTPS/443 — works on Railway)
    if BREVO_KEY:
        return _send_via_brevo(to, subject, body_html, attachments)
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
        for fn, data in (attachments or []):
            part = MIMEBase("application", "octet-stream")
            part.set_payload(data)
            encoders.encode_base64(part)
            part.add_header("Content-Disposition", f'attachment; filename="{fn}"')
            msg.attach(part)
        _smtp_send_ipv4("smtp.gmail.com", 465, GMAIL_USER, GMAIL_PASS,
                        recipients, msg.as_string())
        log.info("SMTP enviado a %s: %s", recipients, subject)
        return True
    except Exception as e:
        log.error("SMTP falló a %s: %s", to, e)
        return False


def _html_wrap(title: str, body: str, panel_url: str = "") -> str:
    panel_line = (
        f'<p style="color:#c9a84c;font-size:12px">📱 Panel móvil disponible en: '
        f'<a href="{panel_url}" style="color:#c9a84c">{panel_url}</a></p>'
    ) if panel_url else ""
    # Styles on <div>, not <body> — Gmail strips <body> inline styles
    return f"""
<html><body style="margin:0;padding:0;background:#06080b">
<div style="font-family:Arial,sans-serif;background:#06080b;color:#f2ede4;padding:20px;max-width:680px;margin:0 auto">
<h2 style="color:#c9a84c;margin-top:0">{title}</h2>
{body}
{panel_line}
<hr style="border-color:#c9a84c44;margin-top:20px">
<p style="color:#9aa0a8;font-size:12px">
Faro Protocol · Fortín Inteligente · protocolfaro@gmail.com<br>
Generado automáticamente · {datetime.now().strftime('%d/%m/%Y %H:%M')} UTC-3
</p>
</div></body></html>"""


# ── Sección Novedades (solo hasta el 01/06/2026) ─────────────────────────────

_NOVEDAD_CUTOFF = datetime(2026, 6, 1)

_NOVEDADES = {
    "roger": """
        <h3 style="color:#c9a84c">Novedades de esta semana en tu reporte</h3>
        <p style="font-size:14px">Esta semana sumamos tres cosas nuevas a tu informe:</p>
        <ul style="font-size:14px;line-height:1.9">
          <li><b>Qué cancha atender viene del satélite, no de un texto fijo.</b>
              Antes siempre decía "4FA urgente". Ahora el reporte lee el estado real de cada
              cancha y te manda a la que realmente está en rojo esa semana.</li>
          <li><b>NDVI y score por cancha.</b>
              Cada cancha muestra su NDVI real (medido por Sentinel-2) y su puntaje /100.
              Así ves exactamente qué tan mal o bien está cada una, no solo un color.</li>
          <li><b>Plan de tareas día por día.</b>
              Ahora tenés Lunes, Martes y Miércoles con las tareas concretas de esa semana,
              no instrucciones genéricas.</li>
        </ul>
    """,
    "juan": """
        <h3 style="color:#c9a84c">Novedades de esta semana en tu reporte</h3>
        <p style="font-size:14px">Esta semana sumamos cuatro bloques nuevos a tu informe:</p>
        <ul style="font-size:14px;line-height:1.9">
          <li><b>Datos reales del Poli y del Área Agronómica.</b>
              Antes aparecían con texto genérico. Ahora ves el score /100, los mm de InSAR
              del Básquet y el NDVI del campo norte, tal como salieron del satélite.</li>
          <li><b>Resumen ejecutivo de tres puntos.</b>
              Un párrafo corto con lo más urgente de la semana, en lenguaje directo.</li>
          <li><b>Presupuesto urgente desglosado.</b>
              Total ARS y cada ítem con monto y prioridad — para que puedas autorizar
              sin tener que preguntar.</li>
          <li><b>Acciones concretas priorizadas.</b>
              Tres acciones con sector y urgencia, no solo un listado de problemas.</li>
        </ul>
    """,
    "banchero": """
        <h3 style="color:#c9a84c">Novedades de esta semana en tu reporte</h3>
        <p style="font-size:14px">Esta semana la tabla del predio tiene dos columnas nuevas y los datos se actualizan solos:</p>
        <ul style="font-size:14px;line-height:1.9">
          <li><b>Columna Score /100 por sector.</b>
              Antes solo había el estado (CRÍTICO/ATENCIÓN/ÓPTIMO). Ahora ves el número
              exacto de cada área.</li>
          <li><b>Columna Detalle satelital.</b>
              Para cada sector aparece el dato crudo del satélite: NDVI, mm de InSAR,
              temperatura Landsat o eficiencia real. Nada inventado.</li>
          <li><b>Solar actualizado.</b>
              El 82.4% que aparecía antes era un valor fijo. Ahora sale del sistema real:
              esta semana es 71%, con 13 paneles en falla identificados.</li>
          <li><b>Acciones recomendadas al pie.</b>
              Tres acciones priorizadas para esta semana, sacadas del análisis de esta corrida.</li>
        </ul>
    """,
    "pait": """
        <h3 style="color:#c9a84c">Novedades de esta semana en tu reporte</h3>
        <p style="font-size:14px">Esta semana la clasificación de canchas pasa a ser dinámica:</p>
        <ul style="font-size:14px;line-height:1.9">
          <li><b>Qué cancha es NO APTA lo decide el satélite.</b>
              Antes siempre ponía "4FA no apta". Ahora la lista viene del estado real de
              esa semana — esta semana la crítica es 1FA (NDVI 0.18, score 26/100).</li>
          <li><b>NDVI y score por cancha.</b>
              Cada superficie muestra su NDVI real y su puntaje /100, no solo un semáforo.</li>
          <li><b>El Poli se clasifica solo.</b>
              Si el InSAR supera el umbral va a NO APTO automáticamente.
              Si no, aparece en Uso condicionado.</li>
          <li><b>Acciones concretas al pie.</b>
              Tres acciones específicas para esta semana basadas en los datos reales.</li>
        </ul>
    """,
    "berlanga": """
        <h3 style="color:#c9a84c">Novedades de esta semana en tu reporte</h3>
        <p style="font-size:14px">Esta semana sumamos dos secciones nuevas y una columna extra:</p>
        <ul style="font-size:14px;line-height:1.9">
          <li><b>Panel de KPIs arriba de la tabla.</b>
              Cuatro indicadores clave de un vistazo: Score Global del predio, alertas rojas,
              eficiencia solar y score del Complejo Acuático. Antes no existía.</li>
          <li><b>Columna Score /100 en la tabla.</b>
              Cada sector ahora muestra su puntaje exacto además del estado.</li>
          <li><b>Datos satelitales reales en la columna Detalle.</b>
              Los valores de solar (71%, no 82.4%), InSAR y NDVI son los que salieron
              del satélite esta semana.</li>
          <li><b>Acciones concretas al pie.</b>
              Tres acciones priorizadas, no solo un listado de problemas.</li>
        </ul>
    """,
    "nelson": """
        <h3 style="color:#c9a84c">Novedades de esta semana en tu reporte</h3>
        <p style="font-size:14px">Esta semana el informe tiene dos bloques completamente nuevos:</p>
        <ul style="font-size:14px;line-height:1.9">
          <li><b>Panel de KPIs al inicio.</b>
              Cuatro números de un vistazo: Score Global, Alertas Activas, Sectores con
              cobertura y pérdida solar en kWp. Antes no existía.</li>
          <li><b>Cada sector en su color real.</b>
              El listado de estado general ahora muestra el score /100 y el color correcto
              (rojo/amarillo/verde) de cada área, directo del satélite de esta semana.</li>
          <li><b>Solar corregido.</b>
              Era 82.4% fijo. Esta semana el sistema mide 71%, con pérdida activa
              de 4.2 kWp identificada.</li>
          <li><b>Acciones concretas priorizadas.</b>
              Tres acciones con sector y urgencia, no solo problemas listados.</li>
        </ul>
    """,
    "aveleyra": """
        <h3 style="color:#c9a84c">Novedades de esta semana en tu reporte</h3>
        <p style="font-size:14px">Esta semana los KPIs del dashboard pasaron a ser datos reales:</p>
        <ul style="font-size:14px;line-height:1.9">
          <li><b>Score predio corregido.</b>
              Estaba en 67/100 fijo. El valor real del sistema esta semana es 70/100.</li>
          <li><b>Solar corregido.</b>
              Estaba en 82.4% fijo. El sistema mide 71% esta semana,
              con pérdida activa de 4.2 kWp.</li>
          <li><b>Todos los KPIs vienen del satélite, no de un número guardado.</b>
              Cada lunes el dashboard va a mostrar los valores reales de esa semana,
              sin intervención manual.</li>
          <li><b>Acciones concretas al pie.</b>
              Tres acciones con contexto operativo y económico, priorizadas por urgencia real.</li>
        </ul>
    """,
}


def _novedad_section(slug: str) -> str:
    """Returns novedades HTML block only until _NOVEDAD_CUTOFF. Empty string after that."""
    if datetime.now() >= _NOVEDAD_CUTOFF:
        return ""
    html = _NOVEDADES.get(slug, "")
    if not html:
        return ""
    return (
        f'<div style="background:#0d1520;border:1px solid #c9a84c55;border-radius:6px;'
        f'padding:16px;margin-top:20px">'
        f'{html}'
        f'<p style="font-size:12px;color:#9aa0a8;margin-top:10px">'
        f'Esta sección aparece solo este lunes. — Faro Protocol</p></div>'
    )


# ── Email bodies — leen datos reales de velez_data.json ───────────────────────

def _body_roger(vd: dict, panel_url: str = "") -> str:
    sectores   = vd.get("sectores", {})
    u          = vd.get("usuarios", {}).get("roger", {})
    canchas    = sectores.get("canchero", {}).get("canchas", [])
    acciones   = u.get("acciones", [])
    tareas_sem = u.get("tareas_semana", [])

    criticas = [c for c in canchas if c.get("sem") == "rojo"]
    atencion = [c for c in canchas if c.get("sem") == "amarillo"]

    def _cancha_li(c):
        return (
            f'<li><b>{c.get("nombre","?")}: </b>'
            f'NDVI {c.get("ndvi","—")} · Score {c.get("score","—")}/100<br>'
            f'<span style="font-size:13px;color:#ccc">{c.get("detalle","")}</span></li>'
        )

    sections = ""
    if criticas:
        rows = "".join(_cancha_li(c) for c in criticas)
        sections += f'<h3 style="color:#e74c3c">HOY — NO PUEDE ESPERAR</h3><ul style="font-size:15px;line-height:1.8">{rows}</ul>'
    if atencion:
        rows = "".join(_cancha_li(c) for c in atencion)
        sections += f'<h3 style="color:#f0b429">Esta semana</h3><ul style="font-size:15px;line-height:1.8">{rows}</ul>'
    if acciones:
        items = "".join(f"<li>{a}</li>" for a in acciones)
        sections += f'<h3 style="color:#c9a84c">Acciones recomendadas</h3><ul style="font-size:14px;line-height:1.8">{items}</ul>'
    if tareas_sem:
        items = ""
        for t in tareas_sem[:3]:
            tasks = " · ".join(t.get("tareas", []))
            items += f'<li><b>{t.get("dia_nombre","?")}: </b>{tasks}</li>'
        sections += f'<h3 style="color:#c9a84c">Próximos días</h3><ul style="font-size:14px;line-height:1.7">{items}</ul>'

    body = f"""
        <p style="font-size:16px;font-weight:bold">Roger, te mando el mapa de esta semana.</p>
        <p style="font-size:14px">Los círculos de colores en el mapa marcan exactamente donde ir.</p>
        {sections}
        {_novedad_section("roger")}
        <p style="font-size:13px;color:#9aa0a8">
          Cualquier duda respondeme por este mail o por WhatsApp. — Faro Protocol
        </p>
    """
    return _html_wrap("Tu Mapa de Trabajo Esta Semana — Campo Amalfitani", body, panel_url)


def _body_juan(vd: dict, panel_url: str = "") -> str:
    sectores = vd.get("sectores", {})
    u        = vd.get("usuarios", {}).get("juan", {})
    canchas  = sectores.get("canchero", {}).get("canchas", [])
    poli     = sectores.get("poli", {})
    agro     = sectores.get("agro", {})
    sede     = sectores.get("sede", {})
    resumen  = u.get("resumen_ejecutivo", [])
    acciones = u.get("acciones", [])
    presup   = u.get("presupuesto_urgente", {})

    criticas = [c for c in canchas if c.get("sem") == "rojo"]
    criticas_li = "".join(
        f'<li><b>{c.get("nombre","?")}:</b> NDVI {c.get("ndvi","—")} · '
        f'Score {c.get("score","—")}/100 — {c.get("detalle","")}</li>'
        for c in criticas
    ) or "<li>Sin canchas en estado crítico esta semana</li>"

    poli_color = _SEM_COLOR.get(poli.get("sem", "amarillo"), "#f0b429")
    poli_label = _SEM_LABEL.get(poli.get("sem", "amarillo"), "ATENCIÓN")

    presup_html = ""
    if presup.get("items"):
        total = presup.get("total", 0)
        items = "".join(
            f'<li style="color:{_SEM_COLOR.get(i.get("sem","amarillo"),"#f0b429")}">'
            f'<b>${i["monto"]:,}:</b> {i["concepto"]}</li>'
            for i in presup["items"]
        )
        presup_html = (
            f'<h3 style="color:#c9a84c">Presupuesto Urgente — ${total:,} ARS</h3>'
            f'<ul style="font-size:14px;line-height:1.8">{items}</ul>'
        )

    resumen_html = "".join(f"<li>{r}</li>" for r in resumen)
    acc_html     = "".join(f"<li>{a}</li>" for a in acciones)

    body = f"""
        <p>Juan, te mando el estado actualizado de las canchas, el campo y el Polideportivo.</p>
        <h3 style="color:#e74c3c">Urgente — Canchas Críticas</h3>
        <ul style="font-size:14px">{criticas_li}</ul>
        <h3 style="color:{poli_color}">Polideportivo Feijóo — {poli_label}</h3>
        <ul style="font-size:14px">
          <li>Score: <b>{poli.get("score","—")}/100</b> · {poli.get("detalle","")}</li>
        </ul>
        <h3 style="color:#c9a84c">Área Agronómica</h3>
        <ul style="font-size:14px">
          <li>Score: <b>{agro.get("score","—")}/100</b> · {agro.get("detalle","")}</li>
        </ul>
        {"<h3 style='color:#c9a84c'>Sede Central</h3><ul style='font-size:14px'><li>Score: <b>" + str(sede.get("score","—")) + "/100</b> · " + sede.get("detalle","") + "</li></ul>" if sede else ""}
        {"<h3 style='color:#c9a84c'>Resumen ejecutivo</h3><ul style='font-size:14px;line-height:1.7'>" + resumen_html + "</ul>" if resumen else ""}
        {presup_html}
        {"<h3 style='color:#c9a84c'>Acciones</h3><ul style='font-size:14px;line-height:1.8'>" + acc_html + "</ul>" if acciones else ""}
        {_novedad_section("juan")}
        <p style="font-size:13px;color:#9aa0a8">Se adjuntan mapas de canchas, reporte agro y estado polideportivo.</p>
    """
    return _html_wrap("Estado Villa Olímpica Esta Semana", body, panel_url)


def _body_banchero(vd: dict, panel_url: str = "") -> str:
    sectores = vd.get("sectores", {})
    u        = vd.get("usuarios", {}).get("banchero", {})
    acciones = u.get("acciones", [])
    fecha    = datetime.now().strftime("%d/%m/%Y")

    SECTOR_ORDER = ["canchero", "poli", "sede", "estadio", "agro", "solar", "piletas"]
    rows = ""
    for key in SECTOR_ORDER:
        s = sectores.get(key, {})
        if not s:
            continue
        nombre  = s.get("nombre", key)
        score   = s.get("score", "—")
        sem     = s.get("sem", "amarillo")
        detalle = s.get("detalle", "")
        color   = _SEM_COLOR.get(sem, "#f0b429")
        label   = _SEM_LABEL.get(sem, "ATENCIÓN")
        rows += (
            f"<tr>"
            f'<td style="padding:6px;border:1px solid #c9a84c22">{nombre}</td>'
            f'<td style="padding:6px;text-align:center;color:{color};border:1px solid #c9a84c22"><b>{score}/100</b></td>'
            f'<td style="padding:6px;color:{color};border:1px solid #c9a84c22"><b>{label}</b></td>'
            f'<td style="padding:6px;border:1px solid #c9a84c22;font-size:13px">{detalle}</td>'
            f"</tr>"
        )

    acc_html = "".join(f"<li>{a}</li>" for a in acciones)

    body = f"""
        <p>Fernando, resumen operativo completo de la semana del {fecha}.</p>
        <h3 style="color:#c9a84c">Predio Completo</h3>
        <table style="color:#f2ede4;border-collapse:collapse;width:100%;font-size:14px">
          <tr style="background:#141c24">
            <th style="padding:8px;border:1px solid #c9a84c44;text-align:left">Área</th>
            <th style="padding:8px;border:1px solid #c9a84c44;text-align:center">Score</th>
            <th style="padding:8px;border:1px solid #c9a84c44;text-align:left">Estado</th>
            <th style="padding:8px;border:1px solid #c9a84c44;text-align:left">Detalle satelital</th>
          </tr>
          {rows}
        </table>
        {"<h3 style='color:#c9a84c'>Acciones recomendadas</h3><ul style='font-size:14px;line-height:1.8'>" + acc_html + "</ul>" if acciones else ""}
        {_novedad_section("banchero")}
        <p style="font-size:13px;color:#9aa0a8">Se adjuntan: reporte general, solar, agro, polideportivo, piletas y sede.</p>
    """
    return _html_wrap(f"Estado Operativo del Predio — Vélez Sarsfield · {fecha}", body, panel_url)


def _body_pait(vd: dict, panel_url: str = "") -> str:
    sectores = vd.get("sectores", {})
    u        = vd.get("usuarios", {}).get("pait", {})
    canchas  = sectores.get("canchero", {}).get("canchas", [])
    poli     = sectores.get("poli", {})
    acciones = u.get("acciones", [])

    def _c_li(c):
        return (
            f'<li><b>{c.get("nombre","?")}:</b> NDVI {c.get("ndvi","—")} · '
            f'Score {c.get("score","—")}/100 — {c.get("detalle","")}</li>'
        )

    no_apto_li      = "".join(_c_li(c) for c in canchas if c.get("sem") == "rojo")
    condicionado_li = "".join(_c_li(c) for c in canchas if c.get("sem") == "amarillo")
    optimas_li      = "".join(_c_li(c) for c in canchas if c.get("sem") == "verde")

    poli_score = poli.get("score", "—")
    poli_det   = poli.get("detalle", "")
    if poli.get("sem") == "rojo":
        no_apto_li += f"<li><b>Polideportivo Feijóo:</b> Score {poli_score}/100 · {poli_det} — evaluar antes de uso intensivo</li>"
    else:
        condicionado_li += f"<li><b>Polideportivo Feijóo:</b> Score {poli_score}/100 · {poli_det}</li>"

    acc_html = "".join(f"<li>{a}</li>" for a in acciones)

    sections = ""
    if no_apto_li:
        sections += f'<h3 style="color:#e74c3c">NO APTO para entrenamiento</h3><ul style="font-size:14px">{no_apto_li}</ul>'
    if condicionado_li:
        sections += f'<h3 style="color:#f0b429">Uso condicionado</h3><ul style="font-size:14px">{condicionado_li}</ul>'
    if optimas_li:
        sections += f'<h3 style="color:#27ae60">Óptimas para uso</h3><ul style="font-size:14px">{optimas_li}</ul>'
    if acciones:
        sections += f'<h3 style="color:#c9a84c">Acciones recomendadas</h3><ul style="font-size:14px;line-height:1.8">{acc_html}</ul>'

    body = f"""
        <p>Sebastián, estado de las superficies para esta semana de entrenamiento.</p>
        {sections}
        {_novedad_section("pait")}
        <p style="font-size:13px;color:#9aa0a8">Se adjuntan mapa de canchas, agro detallado y polideportivo.</p>
    """
    return _html_wrap("Estado Canchas y Campo — Visión Deportiva", body, panel_url)


def _body_berlanga(vd: dict, panel_url: str = "") -> str:
    sectores = vd.get("sectores", {})
    u        = vd.get("usuarios", {}).get("berlanga", {})
    kpis     = u.get("kpis", [])
    acciones = u.get("acciones", [])
    fecha    = datetime.now().strftime("%d/%m/%Y")

    SECTOR_ORDER = ["estadio", "solar", "poli", "piletas", "sede", "canchero"]
    rows = ""
    for key in SECTOR_ORDER:
        s = sectores.get(key, {})
        if not s:
            continue
        nombre  = s.get("nombre", key)
        score   = s.get("score", "—")
        sem     = s.get("sem", "amarillo")
        detalle = s.get("detalle", "")
        color   = _SEM_COLOR.get(sem, "#f0b429")
        label   = _SEM_LABEL.get(sem, "ATENCIÓN")
        rows += (
            f"<tr>"
            f'<td style="padding:6px;border:1px solid #c9a84c22">{nombre}</td>'
            f'<td style="padding:6px;text-align:center;color:{color};border:1px solid #c9a84c22"><b>{score}/100</b></td>'
            f'<td style="padding:6px;text-align:center;color:{color};border:1px solid #c9a84c22"><b>{label}</b></td>'
            f'<td style="padding:6px;border:1px solid #c9a84c22;font-size:13px">{detalle}</td>'
            f"</tr>"
        )

    kpi_html = ""
    if kpis:
        cells = ""
        for k in kpis:
            col = _SEM_COLOR.get(k.get("sem", "amarillo"), "#f0b429")
            val = f'{k["value"]}{k.get("unit","")}'
            cells += (
                f'<td style="text-align:center;padding:10px;border:1px solid #c9a84c22">'
                f'<span style="font-size:22px;color:{col}"><b>{val}</b></span><br>'
                f'<span style="font-size:12px;color:#9aa0a8">{k["label"]}<br>{k.get("sub","")}</span></td>'
            )
        kpi_html = f'<table style="width:100%;border-collapse:collapse;margin-bottom:16px"><tr>{cells}</tr></table>'

    acc_html = "".join(f"<li>{a}</li>" for a in acciones)

    body = f"""
        <p>Fabián, resumen ejecutivo satelital del predio completo,
        semana del {fecha}.</p>
        {kpi_html}
        <h3 style="color:#c9a84c">Indicadores por Área</h3>
        <table style="color:#f2ede4;border-collapse:collapse;width:100%;font-size:14px">
          <tr style="background:#141c24">
            <th style="padding:8px;border:1px solid #c9a84c44;text-align:left">Área</th>
            <th style="padding:8px;border:1px solid #c9a84c44;text-align:center">Score</th>
            <th style="padding:8px;border:1px solid #c9a84c44;text-align:center">Estado</th>
            <th style="padding:8px;border:1px solid #c9a84c44;text-align:left">Detalle</th>
          </tr>
          {rows}
        </table>
        {"<ul style='font-size:14px;line-height:1.8'>" + acc_html + "</ul>" if acciones else ""}
        {_novedad_section("berlanga")}
        <p style="font-size:13px;color:#9aa0a8">Se adjuntan reportes completos del predio.</p>
    """
    return _html_wrap(f"Informe Ejecutivo Semanal — Vélez Sarsfield · {fecha}", body, panel_url)


def _body_nelson(vd: dict, panel_url: str = "") -> str:
    sectores = vd.get("sectores", {})
    u        = vd.get("usuarios", {}).get("nelson", {})
    kpis     = u.get("kpis", [])
    acciones = u.get("acciones", [])
    fecha    = datetime.now().strftime("%d/%m/%Y")

    SECTOR_ORDER = ["canchero", "solar", "poli", "piletas", "sede", "estadio"]
    bullets = ""
    for key in SECTOR_ORDER:
        s = sectores.get(key, {})
        if not s:
            continue
        nombre  = s.get("nombre", key)
        score   = s.get("score", "—")
        sem     = s.get("sem", "amarillo")
        detalle = s.get("detalle", "")
        color   = _SEM_COLOR.get(sem, "#f0b429")
        bullets += f'<li><b style="color:{color}">{nombre} ({score}/100):</b> {detalle}</li>'

    kpi_html = ""
    if kpis:
        cells = ""
        for k in kpis:
            col = _SEM_COLOR.get(k.get("sem", "amarillo"), "#f0b429")
            val = f'{k["value"]}{k.get("unit","")}'
            cells += (
                f'<td style="text-align:center;padding:10px;border:1px solid #c9a84c22">'
                f'<span style="font-size:22px;color:{col}"><b>{val}</b></span><br>'
                f'<span style="font-size:12px;color:#9aa0a8">{k["label"]}<br>{k.get("sub","")}</span></td>'
            )
        kpi_html = f'<table style="width:100%;border-collapse:collapse;margin-bottom:16px"><tr>{cells}</tr></table>'

    acc_html = "".join(f"<li>{a}</li>" for a in acciones)

    body = f"""
        <p>Nelson, resumen ejecutivo satelital del predio,
        semana del {fecha}.</p>
        {kpi_html}
        <h3 style="color:#c9a84c">Estado General</h3>
        <ul style="font-size:14px;line-height:1.8">{bullets}</ul>
        <h3 style="color:#c9a84c">Sustentabilidad</h3>
        <ul style="font-size:14px">
          <li>Cobertura satelital del 100% del predio operativa</li>
          <li>Alerta temprana activa en calidad de agua y estructura</li>
        </ul>
        {"<h3 style='color:#c9a84c'>Acciones</h3><ul style='font-size:14px;line-height:1.8'>" + acc_html + "</ul>" if acciones else ""}
        {_novedad_section("nelson")}
        <p style="font-size:13px;color:#9aa0a8">Se adjuntan reportes completos del predio.</p>
    """
    return _html_wrap(f"Informe Ejecutivo Semanal — Vélez Sarsfield · {fecha}", body, panel_url)


def _body_aveleyra(vd: dict, panel_url: str = "") -> str:
    u        = vd.get("usuarios", {}).get("aveleyra", {})
    kpis     = u.get("kpis", [])
    acciones = u.get("acciones", [])
    fecha    = datetime.now().strftime("%d/%m/%Y")

    rows = ""
    for k in kpis:
        col = _SEM_COLOR.get(k.get("sem", "amarillo"), "#f0b429")
        val = f'{k["value"]}{k.get("unit","")}'
        rows += (
            f"<tr>"
            f'<td style="padding:6px;border:1px solid #c9a84c22">{k["label"]}</td>'
            f'<td style="padding:6px;text-align:center;color:{col};border:1px solid #c9a84c22"><b>{val}</b></td>'
            f'<td style="padding:6px;border:1px solid #c9a84c22;font-size:13px">{k.get("sub","")}</td>'
            f"</tr>"
        )

    acc_html = "".join(f"<li>{a}</li>" for a in acciones)

    body = f"""
        <p>Alberto, dashboard ejecutivo satelital del predio completo,
        semana del {fecha}.</p>
        <h3 style="color:#c9a84c">KPIs de Gestión</h3>
        <table style="color:#f2ede4;border-collapse:collapse;width:100%;font-size:14px">
          <tr style="background:#141c24">
            <th style="padding:8px;border:1px solid #c9a84c44;text-align:left">Indicador</th>
            <th style="padding:8px;border:1px solid #c9a84c44;text-align:center">Valor</th>
            <th style="padding:8px;border:1px solid #c9a84c44;text-align:left">Detalle</th>
          </tr>
          {rows}
        </table>
        {"<ul style='font-size:14px;line-height:1.8'>" + acc_html + "</ul>" if acciones else ""}
        {_novedad_section("aveleyra")}
        <p style="font-size:13px;color:#9aa0a8">Se adjuntan todos los reportes del predio.</p>
    """
    return _html_wrap(f"Dashboard Ejecutivo Semanal — Vélez Sarsfield · {fecha}", body, panel_url)


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

# PNG sectors to try attaching per recipient slug
_SLUG_SECTORS = {
    "roger":    ["canchero"],
    "juan":     ["canchero", "agro", "poli"],
    "banchero": ["canchero", "poli", "sede", "estadio", "agro", "solar", "piletas"],
    "pait":     ["canchero", "poli"],
    "berlanga": ["estadio", "solar", "poli", "piletas", "sede", "canchero"],
    "nelson":   ["canchero", "solar", "poli", "piletas", "sede", "estadio"],
    "aveleyra": ["estadio", "solar", "poli", "piletas", "sede", "canchero"],
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

        # Try to attach sector PNGs (silently skip if not available in GitHub)
        sector_keys = _SLUG_SECTORS.get(slug, [])
        attachments = _fetch_sector_attachments(sector_keys) if sector_keys else []
        if attachments:
            log.info("Adjuntos para %s: %d PNG(s)", nombre, len(attachments))

        ok = send_email(email, f"Faro · Reporte semanal · Vélez · {date_str}",
                        body_html, attachments or None)
        results[nombre] = ok
        log.info("Email %s -> %s", nombre, "OK" if ok else "FAIL")
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
    def _with_ipv4(fn):
        _orig = _sock.getaddrinfo
        def _gai(h, p, family=0, type=0, proto=0, flags=0):  # noqa: A002
            return _orig(h, p, _sock.AF_INET, type, proto, flags)
        _sock.getaddrinfo = _gai
        try:
            return fn()
        finally:
            _sock.getaddrinfo = _orig
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
    target_email  = data.get("email")
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
