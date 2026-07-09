"""
velez_scheduler.py — Vélez email + WhatsApp weekly notifications for Railway.
Adapted from faro_velez_scheduler.py: no local scripts, no PDF attachments,
no `schedule` library (uses APScheduler from app.py).

Config is fetched from GitHub raw URL so it works in stateless Railway containers.
"""
import base64, json, logging, math, os, smtplib, subprocess, sys, tempfile, threading
from datetime import datetime, date, timezone, timedelta
from pathlib import Path

_ART = timezone(timedelta(hours=-3))  # America/Argentina/Buenos_Aires (no DST)
from email.mime.multipart import MIMEMultipart  # kept for smtp_diag only
from urllib.request import urlopen, Request as UReq

import requests

log = logging.getLogger(__name__)

# ── Config ────────────────────────────────────────────────────────────────────

PANEL_BASE_URL = "https://protocolfaro.github.io/faro-paneles/velez/"
_CFG_RAW_URL   = "https://raw.githubusercontent.com/protocolfaro/faro-paneles/main/velez/config_velez.json"
_VD_RAW_URL    = "https://raw.githubusercontent.com/protocolfaro/faro-paneles/main/velez/velez_data.json"
_PNG_BASE_URL  = "https://raw.githubusercontent.com/protocolfaro/faro-paneles/main/velez"

GMAIL_USER          = os.environ.get("GMAIL_USER", "protocolfaro@gmail.com")
GMAIL_CLIENT_ID     = os.environ.get("GMAIL_CLIENT_ID", "")
GMAIL_CLIENT_SECRET = os.environ.get("GMAIL_CLIENT_SECRET", "")
GMAIL_REFRESH_TOKEN = os.environ.get("GMAIL_REFRESH_TOKEN", "")
GMAIL_PASS          = os.environ.get("GMAIL_APP_PASS", os.environ.get("GMAIL_PASS", ""))
RESEND_API_KEY      = os.environ.get("RESEND_API_KEY", "")
RESEND_FROM         = os.environ.get("RESEND_FROM", "Faro Protocol <protocolfaro@gmail.com>")

_MANUAL_DIR  = Path(__file__).parents[4] / "reportes_velez"
MANUAL_PATHS: dict = {
    "roger":    _MANUAL_DIR / "manual_velez_roger.pdf",
    "juan":     _MANUAL_DIR / "manual_velez_juan.pdf",
    "banchero": _MANUAL_DIR / "manual_velez_banchero.pdf",
    "pait":     _MANUAL_DIR / "manual_velez_pait.pdf",
    "berlanga": _MANUAL_DIR / "manual_velez_berlanga.pdf",
    "nelson":   _MANUAL_DIR / "manual_velez_nelson.pdf",
    "aveleyra": _MANUAL_DIR / "manual_velez_aveleyra.pdf",
}

_SEM_COLOR = {"verde": "#27ae60", "amarillo": "#f0b429", "rojo": "#e74c3c"}
_SEM_LABEL = {"verde": "ÓPTIMO",  "amarillo": "ATENCIÓN", "rojo": "CRÍTICO"}
_SEM_EMOJI = {"verde": "✅",       "amarillo": "⚠️",        "rojo": "🚨"}
_SEM_ORDER = {"verde": 0,          "amarillo": 1,           "rojo": 2}

NDVI_ALERT  = 0.35
INSAR_ALERT = 3.0


def _env(key: str, default: str = "") -> str:
    return os.environ.get(key, default)


def load_config() -> dict:
    # Local config_velez.json (root of repo) has zonas + reportes per person
    local = Path(__file__).parents[4] / "config_velez.json"
    if local.exists():
        try:
            return json.loads(local.read_text(encoding="utf-8"))
        except Exception as e:
            log.warning("load_config local: %s", e)
    # Fall back to GitHub (may not have zonas)
    try:
        req = UReq(_CFG_RAW_URL, headers={"User-Agent": "FaroProtocol/4.0"})
        with urlopen(req, timeout=10) as r:
            return json.loads(r.read())
    except Exception as e:
        log.warning("load_config remote: %s", e)
        return {"zonas": [], "destinatarios": []}


def _get_velez_data() -> dict:
    """Delega a faro_assembler.assemble_report() — fuente única de verdad."""
    try:
        _here = os.path.dirname(os.path.abspath(__file__))
        if _here not in sys.path:
            sys.path.insert(0, _here)
        from faro_assembler import assemble_report
        return assemble_report("amalfitani")
    except Exception as _ae:
        log.warning("_get_velez_data assembler (non-fatal): %s — fallback JSON estático", _ae)
        local = Path(__file__).parents[4] / "velez" / "velez_data.json"
        if local.exists():
            try:
                return json.loads(local.read_text(encoding="utf-8"))
            except Exception as _le:
                log.warning("_get_velez_data JSON local: %s", _le)
        return {}


def _get_sectores() -> dict:
    return _get_velez_data().get("sectores", {})


# ── WhatsApp (CallMeBot) ──────────────────────────────────────────────────────

_WA_ALERT_USERS = [
    {"nombre": "Roger Bernal",      "slug": "roger",    "phone": "541124642616",
     "env_key": "CALLMEBOT_KEY_ROGER",    "env_key_alt": "VELEZ_WHATSAPP_CANCHERO",
     "sectores": ["canchero"]},
    {"nombre": "Juan González",     "slug": "juan",     "phone": "541151073109",
     "env_key": "CALLMEBOT_KEY_JUAN",     "env_key_alt": "VELEZ_WHATSAPP_INTENDENTE",
     "sectores": ["canchero","agro","poli"]},
    {"nombre": "Fernando Banchero", "slug": "banchero", "phone": "541167096384",
     "env_key": "CALLMEBOT_KEY_BANCHERO", "env_key_alt": None,
     "sectores": ["estadio","agro","solar","canchero","sede","poli","piletas"]},
    {"nombre": "Nelson Pugliese",   "slug": "nelson",   "phone": "541156417353",
     "env_key": "CALLMEBOT_KEY_NELSON",   "env_key_alt": "VELEZ_WHATSAPP_NELSON",
     "sectores": ["estadio","agro","solar","canchero","sede","poli","piletas"]},
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


def _build_wa_message(user: dict, sectores: dict, fecha: str, vd: dict = None) -> str:
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
    # Satellite data age warning
    if vd:
        meta = (vd.get("usuarios", {}).get("roger", {})
                   .get("heatmaps_meta", {}))
        semana = meta.get("semana", "")
        if semana:
            try:
                from datetime import date as _date
                img_d = datetime.strptime(semana, "%Y-%m-%d").date()
                age = (datetime.now(_ART).date() - img_d).days
                if age > 7:
                    lines.append(f"⚠ _Imagen satelital de hace {age} días ({img_d.strftime('%d/%m')}) — sin imagen nueva disponible_")
                    lines.append("")
            except Exception:
                pass
    lines.append(f"📱 {PANEL_BASE_URL}#{user['slug']}")
    return "\n".join(lines)


def send_whatsapp_alerts(vd: dict = None) -> dict:
    """Send weekly WhatsApp summary. Silently skips users without a configured key."""
    if vd is None:
        vd = _get_velez_data()
    sectores  = vd.get("sectores", {})
    fecha_str = datetime.now(_ART).strftime("%d/%m/%Y")
    results   = {}
    for user in _WA_ALERT_USERS:
        api_key = _env(user["env_key"]) or (
            _env(user["env_key_alt"]) if user.get("env_key_alt") else ""
        )
        if not api_key:
            log.info("WhatsApp: %s sin key — configurar %s o %s en Railway",
                     user["nombre"], user["env_key"], user.get("env_key_alt", ""))
            results[user["nombre"]] = None
            continue
        msg = _build_wa_message(user, sectores, fecha_str, vd=vd)
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


def _output_to_key(output_filename: str) -> str:
    """'faro_reporte_velez_agro_FINAL.png' → 'agro_FINAL', 'faro_reporte_velez.png' → 'velez'"""
    stem   = Path(output_filename).stem
    prefix = "faro_reporte_velez"
    return "velez" if stem == prefix else stem[len(prefix) + 1:]


def _get_report_paths(config: dict) -> dict:
    """Build {key → Path} from config.zonas, pointing to reportes_velez/ in repo root."""
    base = Path(__file__).parents[4] / "reportes_velez"
    return {
        _output_to_key(z["output"]): base / z["output"]
        for z in config.get("zonas", [])
    }



def send_email(to: str, subject: str, body_html: str,
               attachments: list = None) -> bool:
    """Enviar email via Resend (HTTPS — no bloqueado por Railway)."""
    if not RESEND_API_KEY:
        log.warning("send_email: RESEND_API_KEY no configurado")
        return False
    try:
        payload: dict = {
            "from":    RESEND_FROM,
            "to":      [to],
            "subject": subject,
            "html":    body_html,
        }
        if attachments:
            payload["attachments"] = [
                {"filename": fn, "content": base64.b64encode(data).decode()}
                for fn, data in attachments
            ]
        r = requests.post(
            "https://api.resend.com/emails",
            headers={"Authorization": f"Bearer {RESEND_API_KEY}", "Content-Type": "application/json"},
            json=payload,
            timeout=30,
        )
        if r.status_code in (200, 201):
            log.info("Resend OK → %s: %s", to, subject)
            return True
        log.error("Resend HTTP %s → %s: %s", r.status_code, to, r.text[:200])
        return False
    except Exception as e:
        log.error("Resend error → %s: %s", to, e)
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
Generado automáticamente · {datetime.now(_ART).strftime('%d/%m/%Y %H:%M')} UTC-3
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
              La eficiencia solar se calcula diariamente desde GHI real (NASA POWER) con modelo pvlib.
              El valor varía según la irradiación del día.</li>
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


# ── Senter lighting helper (stdlib only, no pvlib) ───────────────────────────

def _senter_lighting(today_date=None):
    """
    Returns (sunset_art, needs_lighting, rec_html) for Buenos Aires.
    Uses Spencer's formula: lat=-34.63, lon=-58.52, UTC-3.
    """
    d = today_date or date.today()
    doy = d.timetuple().tm_yday
    lat_r = math.radians(-34.63)
    B = 2 * math.pi * (doy - 81) / 364
    decl = math.radians(23.45 * math.sin(B))
    eot  = 9.87 * math.sin(2 * B) - 7.53 * math.cos(B) - 1.5 * math.sin(B)
    solar_noon_utc = 12 + (58.52 / 15) - eot / 60
    val = max(-1.0, min(1.0, -math.tan(lat_r) * math.tan(decl)))
    H   = math.degrees(math.acos(val))
    sunset_utc = solar_noon_utc + H / 15
    sunset_art = sunset_utc - 3                     # ART = UTC-3
    sh = int(sunset_art); sm = int((sunset_art - sh) * 60)
    sunset_str = f"{sh:02d}:{sm:02d}"
    needs = sunset_art < 18.5                        # light needed when sunset before 18:30
    if needs:
        # Recommend lighting for the last session window before sunset + 30 min overlap
        light_start_h = max(sh - 1, 15); light_start = f"{light_start_h:02d}:00"
        rec_html = (
            f'<li><b>Carros Senter — encendido {light_start} ART</b> '
            f'(ocaso: {sunset_str}; luz natural insuficiente desde ~{light_start_h:02d}:30)</li>'
            f'<li>Apagado: 30 min después de fin de sesión (reducir estrés nocturno del césped)</li>'
            f'<li>Verificar orientación de carros: paralela al eje longitudinal de cada cancha</li>'
        )
    else:
        rec_html = f'<li>Luz natural suficiente hasta las {sunset_str} — carros Senter no requeridos esta semana</li>'
    return sunset_str, needs, rec_html


def _fetch_aspersores_summary(railway_url: str, pin: str = "") -> str:
    """Try to get sprinkler coverage summary from Railway. Returns HTML fragment."""
    try:
        if not railway_url:
            raise ValueError("no url")
        r = requests.get(f"{railway_url}/velez/aspersores", timeout=5)
        if not r.ok:
            raise ValueError(r.status_code)
        data = r.json()
        # data expected: { cancha: [{x,y,r,estado}, ...], ... }
        lines = []
        for cid, asps in sorted(data.items()):
            if not isinstance(asps, list):
                continue
            total = len(asps)
            sin_cob = sum(1 for a in asps if a.get("estado") == "sin_cobertura")
            solap   = sum(1 for a in asps if a.get("estado") == "solapado")
            ok      = total - sin_cob - solap
            color = "#e74c3c" if sin_cob else ("#f0b429" if solap else "#27ae60")
            lines.append(
                f'<li><b style="color:{color}">{cid.upper()}:</b> '
                f'{total} aspersores · {ok} cubiertos · {solap} solapados · {sin_cob} sin cobertura</li>'
            )
        if lines:
            return "<ul style='font-size:14px;line-height:1.8'>" + "".join(lines) + "</ul>"
    except Exception:
        pass
    return '<p style="font-size:13px;color:#9aa0a8">Sin datos de aspersores sincronizados — actualizá desde el panel web.</p>'


# ── Helpers de rendering — leen del dict canónico, sin imports de datos ──────

def _render_hermes_banners(hc: dict, wl: dict, month: int) -> str:
    """Banners HTML con alertas de hermes: humedad invernal, Smith-Kerns, compactación."""
    parts = []
    # Banner invernal con corrección turca (mayo–septiembre)
    hum_est = hc.get("humedad_estimada")
    hc_conf = hc.get("confianza_consolidada", 0)
    if hum_est is not None and hc_conf >= 0.30 and month in (5, 6, 7, 8, 9):
        hum_col = "#27ae60" if hum_est > 0.25 else ("#f0b429" if hum_est > 0.15 else "#e74c3c")
        et0_acc = hc.get("_et0_acumulada_mm", "—")
        th_sar  = hc.get("_theta_sar", "—")
        fuentes = " · ".join(hc.get("fuentes_activas", []) or ["—"])
        parts.append(
            f'<div style="background:rgba(201,168,76,.12);border-left:4px solid #c9a84c;'
            f'padding:10px 14px;margin-bottom:16px;border-radius:0 6px 6px 0">'
            f'<b style="color:#c9a84c">Humedad de suelo estimada (SAR + corrección ET₀)</b><br>'
            f'<span style="font-size:14px">θ SAR: <b>{th_sar} m³/m³</b> · '
            f'ET₀ acum.: <b>{et0_acc} mm</b> → '
            f'θ hoy: <b style="color:{hum_col}">{hum_est:.3f} m³/m³</b></span><br>'
            f'<span style="font-size:12px;color:#9aa0a8">Fuentes: {fuentes} · '
            f'Confianza Hermes: {int(hc_conf*100)}%</span></div>'
        )
    # Alerta Smith-Kerns (Dollar Spot)
    clim = hc.get("climate") or {}
    sk   = clim.get("smith_kerns_pct") or wl.get("smith_kerns_pct")
    if sk is not None and float(sk) >= 20.0:
        sk_col = "#f0b429" if float(sk) < 40 else "#e74c3c"
        sk_act = ('<b> → APLICAR FUNGICIDA preventivo</b>' if float(sk) >= 40
                  else ' → monitorear manchas esta semana')
        parts.append(
            f'<div style="background:rgba(231,76,60,.12);border-left:4px solid {sk_col};'
            f'padding:10px 14px;margin-bottom:16px;border-radius:0 6px 6px 0">'
            f'<b style="color:{sk_col}">Dollar Spot (Smith-Kerns MSU): {float(sk):.1f}%</b>'
            f'{sk_act}<br><span style="font-size:12px;color:#9aa0a8">'
            f'Modelo validado NASA/MSU · ventana 5 días reales</span></div>'
        )
    # Alerta compactación mecánica (BSI satelital)
    comp = next((a for a in hc.get("alertas", []) if a.startswith("compactacion")), None)
    if comp:
        bsi_val = ""
        try:
            bsi_val = comp.split("BSI ")[1].split(" ")[0]
        except Exception:
            pass
        parts.append(
            f'<div style="background:rgba(231,76,60,.12);border-left:4px solid #e74c3c;'
            f'padding:10px 14px;margin-bottom:16px;border-radius:0 6px 6px 0">'
            f'<b style="color:#e74c3c">Compactación detectada por satélite'
            f'{"  — BSI " + bsi_val if bsi_val else ""}</b>'
            f' → Programar aireación mecánica<br>'
            f'<span style="font-size:12px;color:#9aa0a8">'
            f'BSI (Bare Soil Index) elevado · Sentinel-2 SWIR/NIR</span></div>'
        )
    return "".join(parts)


def _render_weather_block(wl: dict) -> str:
    """HTML con ET₀, déficit hídrico y GDD desde weather_live."""
    if not wl:
        return ""
    et0  = wl.get("et0_mm_dia", 0)
    deff = wl.get("deficit_hidrico_mm", 0)
    rmin = wl.get("riego_min_sector", 0)
    hrio = wl.get("hora_riego_optima", "06:00")
    gdd  = wl.get("gdd_acumulado_7d", 0)
    dias = wl.get("dias_proximo_corte", 7)
    hcor = wl.get("hora_corte_optima", "07:00")
    rc   = "#27ae60" if deff <= 3 else ("#f0b429" if deff <= 8 else "#e74c3c")
    rieg = f' → riego {rmin} min/sector a las {hrio}' if deff > 3 else ' → sin déficit hídrico'
    items = [
        f'<li>ET₀: <b>{et0} mm/día</b> · Déficit: <b style="color:{rc}">{deff} mm</b>{rieg}</li>',
        f'<li>GDD acumulados (7d): <b>{gdd}</b> · Próximo corte: <b>{dias} días</b> · ventana: {hcor}</li>',
    ]
    return ('<h3 style="color:#c9a84c">Clima y Prescripciones — Datos Actuales</h3>'
            '<ul style="font-size:14px;line-height:1.8">' + "".join(items) + '</ul>')


def _render_cancha_sections(canchas: list, hm: dict) -> str:
    """HTML con canchas agrupadas por semáforo. Amalfitani separado de VO."""
    amalfitani_list = [c for c in canchas if c.get("id") == "amalfitani"]
    canchas         = [c for c in canchas if c.get("id") != "amalfitani"]
    def _li(c):
        cid  = c.get("id", "")
        hm_e = hm.get(cid, {}) if isinstance(hm.get(cid), dict) else {}
        tipo = c.get("tipo_cesped") or hm_e.get("tipo_cesped") or "natural"
        dat  = []
        for fname, label, fmt in [("gndvi","GNDVI",".2f"), ("bsi","BSI","+.2f"), ("ndwi","NDWI","+.2f")]:
            val = c.get(fname) if c.get(fname) is not None else hm_e.get(fname)
            if isinstance(val, (int, float)):
                dat.append(f'{label} {val:{fmt}}')
        extra = (f' · {" · ".join(dat)} <span style="color:#c9a84c;font-size:11px">[DATO REAL]</span>'
                 if dat else "")
        ndvi_lbl = c.get("ndvi", "—")
        if tipo == "hibrido":
            ndvi_lbl = (f'{ndvi_lbl} <span style="color:#9aa0a8;font-size:11px">'
                        f'referencial — campo híbrido · SAR primario</span>')
        return (f'<li><b>{c.get("nombre","?")}: </b>'
                f'NDVI {ndvi_lbl} · Score {c.get("score","—")}/100{extra}<br>'
                f'<span style="font-size:13px;color:#ccc">{c.get("detalle","")}</span></li>')

    criticas = [c for c in canchas if c.get("sem") == "rojo"]
    atencion = [c for c in canchas if c.get("sem") == "amarillo"]
    optimas  = [c for c in canchas if c.get("sem") == "verde"]
    html = ""
    if criticas:
        html += (f'<h3 style="color:#e74c3c">Intervención Urgente</h3>'
                 f'<ul style="font-size:15px;line-height:1.8">{"".join(_li(c) for c in criticas)}</ul>')
    if atencion:
        html += (f'<h3 style="color:#f0b429">Tratamiento Esta Semana</h3>'
                 f'<ul style="font-size:15px;line-height:1.8">{"".join(_li(c) for c in atencion)}</ul>')
    if optimas:
        html += (f'<h3 style="color:#27ae60">Estado Óptimo</h3>'
                 f'<ul style="font-size:14px;line-height:1.8">{"".join(_li(c) for c in optimas)}</ul>')
    if amalfitani_list:
        am     = amalfitani_list[0]
        am_col = _SEM_COLOR.get(am.get("sem","amarillo"), "#f0b429")
        am_lbl = _SEM_LABEL.get(am.get("sem","amarillo"), "ATENCIÓN")
        am_hum = am.get("humedad_estimada")
        am_hum_html = f" · Humedad estimada: <b>{am_hum:.3f} m³/m³</b>" if am_hum else ""
        am_alertas = am.get("hermes_alertas", [])
        am_al_html = "".join(f'<li style="color:#f0b429">{a}</li>' for a in am_alertas[:3])
        html += (
            f'<h3 style="color:{am_col}">Estadio Amalfitani (Liniers) — {am_lbl}</h3>'
            f'<ul style="font-size:14px;line-height:1.8">'
            f'<li>Score: <b>{am.get("score","—")}/100</b>{am_hum_html}<br>'
            f'<span style="font-size:13px;color:#ccc">{am.get("detalle","")}</span></li>'
            + am_al_html +
            f'</ul>'
        )
    return html


# ── Email bodies — leen del dict canónico ensamblado ─────────────────────────

def _satellite_age_warning(vd: dict) -> str:
    """Returns an HTML warning block if satellite data is older than 7 days. Empty string otherwise."""
    meta = vd.get("usuarios", {}).get("roger", {}).get("heatmaps_meta", {})
    semana = meta.get("semana", "")
    if not semana:
        return ""
    try:
        img_date = datetime.strptime(semana, "%Y-%m-%d")
        age_days = (datetime.now(_ART).replace(tzinfo=None) - img_date).days
    except Exception:
        return ""
    if age_days <= 7:
        return ""
    _winter_note = ""
    if datetime.now(_ART).month in (6, 7, 8):
        _winter_note = (
            " Buenos Aires en invierno tiene nubosidad elevada — "
            "es normal no tener imagen nueva este período. "
            "Los datos agronómicos (riego, corte, Van Genuchten) se calculan "
            "desde física real y clima, no desde el satélite."
        )
    return (
        f'<div style="background:rgba(231,76,60,.15);border-left:4px solid #e74c3c;'
        f'padding:10px 14px;margin-bottom:16px;border-radius:0 6px 6px 0">'
        f'<b style="color:#e74c3c">⚠ Datos satelitales de hace {age_days} días</b><br>'
        f'<span style="font-size:13px;color:#ccc">Última imagen Sentinel-2: {img_date.strftime("%d/%m/%Y")}. '
        f'No hay imagen limpia más reciente disponible (nubosidad o cobertura insuficiente). '
        f'Las alertas de NDVI reflejan esa fecha, no el estado actual del campo.{_winter_note}</span>'
        f'</div>'
    )



# ── Helpers para _body_roger — misma lógica que el panel web JS ──────────────

def _sem_adj(sem: str, month: int) -> str:
    """Ajusta semáforo visual para invierno austral (meses 5-8). Igual que sem() en JS."""
    if month in (5, 6, 7, 8):
        if sem == "rojo":     return "amarillo"
        if sem == "amarillo": return "verde"
    return sem


def _accion_cancha(cid: str, nombre: str, acciones: list) -> list:
    """Retorna las acciones de Roger específicas para esta cancha."""
    cid_up = cid.upper()
    return [a for a in acciones if cid_up in a.upper() or
            (nombre and nombre.upper() in a.upper())]


def _zona_tipo(cancha: dict, acciones: list, month: int) -> tuple:
    """
    Tipo de zona para Panel 1. Misma lógica que renderCampo() en el panel web.
    Returns (texto, color_hex).
    """
    cid    = cancha.get("id", "")
    nombre = cancha.get("nombre", "")
    s_raw  = cancha.get("sem", "amarillo")
    ndvi   = cancha.get("ndvi")   # None si sin imagen óptica
    winter = month in (5, 6, 7, 8)
    ac     = _accion_cancha(cid, nombre, acciones)
    fungic = any("fungicida" in a.lower() for a in ac)
    resem  = any("resembrar" in a.lower() for a in ac)
    fertil = any("fertiliz"  in a.lower() for a in ac)
    dren   = any("drenaje"   in a.lower() for a in ac)
    if winter and s_raw != "rojo" and not resem and not fungic:
        return "OK · descanso invernal", "#4a9e6a"
    if resem or (s_raw == "rojo" and ndvi is not None and ndvi < 0.05):
        return "RESEMBRAR urgente", "#d94f4f"
    if fungic:
        return "FUNGICIDA — Dollar Spot", "#c084fc"
    if fertil:
        return "FERTILIZAR — N uniforme", "#f59e0b"
    if dren:
        return "DRENAJE — lateral", "#38bdf8"
    if s_raw == "rojo":
        return "ATENDER — zona general", "#d94f4f"
    if s_raw == "amarillo":
        return "MONITOREAR", "#d4a017"
    return "OK", "#4a9e6a"


def _focos_cancha(cancha: dict, acciones: list, month: int) -> list:
    """
    Focos para Panel 2. Misma lógica que renderFocos() — SOLO acciones específicas de Roger.
    Returns list of {n, lbl, color}.
    """
    cid    = cancha.get("id", "")
    nombre = cancha.get("nombre", "")
    s_raw  = cancha.get("sem", "amarillo")
    ndvi   = cancha.get("ndvi")   # None si sin imagen óptica
    winter = month in (5, 6, 7, 8)
    ac     = _accion_cancha(cid, nombre, acciones)
    focos  = []
    if any("fungicida" in a.lower() for a in ac):
        focos.append({"n": 1, "lbl": "Hongo — aplicar fungicida", "color": "#c084fc"})
    if any("drenaje" in a.lower() or "regar" in a.lower() for a in ac):
        focos.append({"n": 2, "lbl": "Agua/Drenaje", "color": "#38bdf8"})
    if any("fertiliz" in a.lower() for a in ac):
        focos.append({"n": 3, "lbl": "Nutrición — fertilizar", "color": "#f59e0b"})
    if any("resembrar" in a.lower() for a in ac) or (ndvi is not None and ndvi < 0.05 and s_raw == "rojo" and not winter):
        focos.append({"n": 4, "lbl": "Resembrar", "color": "#d94f4f"})
    return focos


def _body_roger(vd: dict, panel_url: str = "") -> str:
    """
    Email HTML para Roger. Mismas secciones y mismo orden que velez/index.html:
    Panel 0 -> Panel 1 -> Panel 2 -> Panel 3 -> Kalman -> InSAR -> Tabla -> Clima -> Tareas -> Tendencia.
    """
    sectores   = vd.get("sectores", {})
    u          = vd.get("usuarios", {}).get("roger", {})
    wl         = vd.get("weather_live", {})
    hc         = vd.get("hermes", {})
    acciones   = u.get("acciones", [])
    tareas_sem = u.get("tareas_semana", [])
    fecha      = datetime.now(_ART).strftime("%d/%m/%Y")
    month      = datetime.now(_ART).month
    rf         = wl.get("riesgo_fungosis", {})

    # Lista canonica: Amalfitani primero, luego canchero.canchas (mismo orden que tabs del panel)
    estadio  = sectores.get("estadio", {})
    gndvi_am = wl.get("gndvi_por_cancha", {}).get("canchas", {}).get("amalfitani", {})
    amalfitani_c = {
        "id": "amalfitani", "nombre": "Estadio J. Amalfitani",
        "score": estadio.get("score"), "score_prev": estadio.get("score_prev"),
        "sem": estadio.get("sem", "amarillo"),
        "ndvi": gndvi_am.get("ndvi"), "ndvi_prev": None,
        "detalle": estadio.get("detalle", ""),
    }
    canchas_vo  = list(vd.get("roger_canchas") or sectores.get("canchero", {}).get("canchas", []))
    all_canchas = [amalfitani_c] + canchas_vo

    age_warn    = _satellite_age_warning(vd)
    hermes_html = _render_hermes_banners(hc, wl, month)

    def _sh(label, sub=""):
        sub_html = (
            '  <span style="color:#9aa0a8;font-weight:400;letter-spacing:0;'
            f'font-size:10px;text-transform:none">{sub}</span>'
        ) if sub else ""
        return (
            '<div style="font-size:9px;font-weight:700;letter-spacing:.18em;color:#c9a84c;'
            'text-transform:uppercase;padding:14px 0 8px;border-bottom:1px solid #c9a84c33;'
            f'margin-bottom:10px">{label}{sub_html}</div>'
        )

    # ── PANEL 0 — Semaforo ejecutivo ──────────────────────────────────────────
    p0_cards = []
    for c in all_canchas:
        cid    = c.get("id", "")
        nombre = c.get("nombre", cid.upper())
        score  = c.get("score") or 0
        s_raw  = c.get("sem", "amarillo")
        s_vis  = _sem_adj(s_raw, month)
        scol   = _SEM_COLOR.get(s_vis, "#f0b429")
        slbl   = _SEM_LABEL.get(s_vis, "ATENCIÓN")
        ndvi   = c.get("ndvi")
        ac     = _accion_cancha(cid, nombre, acciones)
        if ac:
            accion_txt = ac[0].split(":", 1)[-1].strip() if ":" in ac[0] else ac[0]
        else:
            det = c.get("detalle", "")
            accion_txt = det.split("·")[1].strip() if "·" in det else slbl
        ndvi_txt = f"  ·  NDVI {ndvi:.3f}" if ndvi is not None else ""
        p0_cards.append(
            f'<div style="background:#0d1117;border:1.5px solid {scol}33;border-radius:8px;'
            f'padding:10px 12px;margin-bottom:8px;display:flex;align-items:center;gap:12px">'
            f'<div style="width:42px;height:42px;border-radius:50%;border:2px solid {scol};'
            f'background:{scol}22;display:flex;align-items:center;justify-content:center;'
            f'font-size:16px;font-weight:700;color:{scol};flex-shrink:0">{score}</div>'
            f'<div>'
            f'<div style="font-weight:700;font-size:13px">{nombre}</div>'
            f'<div style="color:{scol};font-size:11px;font-weight:700">{slbl}</div>'
            f'<div style="color:#9aa0a8;font-size:11px">{accion_txt}{ndvi_txt}</div>'
            f'</div></div>'
        )
    p0_html = "\n".join(p0_cards)

    # ── PANEL 1 — Mapa de prescripcion ────────────────────────────────────────
    p1_rows = []
    for c in all_canchas:
        zona_txt, zcol = _zona_tipo(c, acciones, month)
        ndvi = c.get("ndvi")
        ndvi_str = f"{ndvi:.3f}" if ndvi is not None else "—"
        fc = ("#1c3318" if (month in (5,6,7,8) and (ndvi or 0) >= 0.03) else
              "#1a3a20" if (ndvi or 0) > 0.45 else
              "#1e3818" if (ndvi or 0) > 0.25 else
              "#252a14" if (ndvi or 0) > 0.10 else "#2a1a0e")
        n = c.get("nombre", c.get("id", "?"))
        p1_rows.append(
            f'<tr>'
            f'<td style="padding:6px 8px;border-bottom:1px solid #ffffff08;font-size:12px;'
            f'font-weight:600;white-space:nowrap;color:#f0ece4">{n}</td>'
            f'<td style="padding:6px 8px;border-bottom:1px solid #ffffff08">'
            f'<span style="display:inline-block;width:9px;height:9px;border-radius:50%;'
            f'background:{fc};margin-right:5px;vertical-align:middle"></span>'
            f'<span style="font-size:11px;color:#9aa0a8">{ndvi_str}</span></td>'
            f'<td style="padding:6px 8px;border-bottom:1px solid #ffffff08;font-size:11px;'
            f'font-weight:700;color:{zcol}">{zona_txt}</td>'
            f'</tr>'
        )
    p1_html = (
        '<table style="width:100%;border-collapse:collapse;background:#07090c">'
        '<tr style="background:#0d1117">'
        + "".join(
            f'<th style="padding:7px 8px;text-align:left;color:#c9a84c;font-size:9px;'
            f'letter-spacing:.1em;white-space:nowrap">{h}</th>'
            for h in ["CANCHA", "NDVI", "PRESCRIPCIÓN"]
        )
        + '</tr>' + "".join(p1_rows) + '</table>'
    )

    # ── PANEL 2 — Mapa de alertas ─────────────────────────────────────────────
    p2_rows = []
    for c in all_canchas:
        focos = _focos_cancha(c, acciones, month)
        n = c.get("nombre", c.get("id", "?"))
        if not focos:
            badges = '<span style="font-size:11px;color:#4a9e6a">✓ Sin alertas</span>'
        else:
            parts = []
            for f in focos:
                parts.append(
                    f'<span style="display:inline-flex;align-items:center;justify-content:center;'
                    f'width:20px;height:20px;border-radius:50%;border:1px solid {f["color"]};'
                    f'background:{f["color"]}22;color:{f["color"]};font-size:11px;font-weight:700;'
                    f'margin-right:3px">{f["n"]}</span>'
                    f'<span style="font-size:11px;color:#9aa0a8;margin-right:10px">{f["lbl"]}</span>'
                )
            badges = "".join(parts)
        p2_rows.append(
            f'<tr>'
            f'<td style="padding:6px 8px;border-bottom:1px solid #ffffff08;font-size:12px;'
            f'font-weight:600;white-space:nowrap;color:#f0ece4">{n}</td>'
            f'<td style="padding:6px 8px;border-bottom:1px solid #ffffff08">{badges}</td>'
            f'</tr>'
        )
    p2_html = (
        '<table style="width:100%;border-collapse:collapse;background:#07090c">'
        '<tr style="background:#0d1117">'
        '<th style="padding:7px 8px;text-align:left;color:#c9a84c;font-size:9px;'
        'letter-spacing:.1em">CANCHA</th>'
        '<th style="padding:7px 8px;text-align:left;color:#c9a84c;font-size:9px;'
        'letter-spacing:.1em">ALERTAS — FOCOS DE TRABAJO</th>'
        '</tr>' + "".join(p2_rows) + '</table>'
        '<div style="display:flex;gap:16px;flex-wrap:wrap;font-size:10px;color:#9aa0a8;margin-top:8px">'
        '<span><b style="color:#c084fc">1</b> Hongo</span>'
        '<span><b style="color:#38bdf8">2</b> Agua</span>'
        '<span><b style="color:#f59e0b">3</b> Nutrición</span>'
        '<span><b style="color:#d94f4f">4</b> Resembrar</span>'
        '</div>'
    )

    # ── PANEL 3 — Score ───────────────────────────────────────────────────────
    scores_v = [(c.get("score") or 0) for c in all_canchas if c.get("score")]
    avg_sc   = round(sum(scores_v) / len(scores_v)) if scores_v else 0
    criticas = [c for c in all_canchas if c.get("sem") == "rojo"]
    avg_sem  = "rojo" if criticas else ("amarillo" if avg_sc < 70 else "verde")
    avg_col  = _SEM_COLOR.get(avg_sem, "#f0b429")
    p3_html  = (
        f'<div style="text-align:center;padding:12px 0">'
        f'<div style="font-size:56px;font-weight:700;color:{avg_col};line-height:1">{avg_sc}</div>'
        f'<div style="font-size:11px;color:#9aa0a8;margin-top:4px">Score promedio predio / 100</div>'
        f'<div style="height:6px;border-radius:3px;background:linear-gradient(90deg,#d94f4f,#d4a017,#4a9e6a);'
        f'margin:10px 0 4px;position:relative">'
        f'<div style="position:absolute;top:50%;left:{avg_sc}%;transform:translate(-50%,-50%);'
        f'width:12px;height:12px;border-radius:50%;background:#f0ece4;border:2px solid #07090c;'
        f'outline:2px solid #c9a84c"></div></div>'
        f'<div style="display:flex;justify-content:space-between;font-size:10px;color:#9aa0a8">'
        f'<span>0 · Crítico</span><span>50</span><span>100 · Óptimo</span></div></div>'
    )

    # ── Kalman / Tendencia NDVI ───────────────────────────────────────────────
    kr   = wl.get("gndvi_por_cancha", {}).get("kalman_result", {})
    vals = kr.get("ndvi_sintetico", [])
    ts   = kr.get("timestamps", [])
    errs = kr.get("margen_error_kalman", [])
    if vals:
        last_v = vals[-1]
        last_e = errs[-1] if errs else 0
        prev_v = vals[-2] if len(vals) > 1 else last_v
        trend  = ("↑ Mejora" if last_v > prev_v + 0.005 else
                  "↓ Baja"   if last_v < prev_v - 0.005 else "→ Estable")
        kalman_html = (
            f'<div style="background:#0d1117;border:1px solid #c9a84c33;border-radius:8px;'
            f'padding:12px 14px">'
            f'<div style="font-size:22px;font-weight:700;color:#c9a84c">{last_v:.3f}'
            f'<span style="font-size:11px;color:#9aa0a8;font-weight:400;margin-left:8px">'
            f'NDVI sintético · {trend} · ±{last_e:.3f}</span></div>'
            f'<div style="font-size:10px;color:#9aa0a8;margin-top:4px">'
            f'{len(vals)} observaciones · {(ts[0] or "")[:7]} → {(ts[-1] or "")[:7]}</div>'
            f'</div>'
        )
    else:
        kalman_html = '<p style="font-size:12px;color:#9aa0a8">Sin datos históricos disponibles.</p>'

    # ── InSAR ─────────────────────────────────────────────────────────────────
    import re as _re
    def _extr_insar(det):
        m = _re.search(r'InSAR[\s:]*([0-9.]+)', str(det or ""), _re.I)
        return float(m.group(1)) if m else None

    insar_items = [
        ("Amalfitani Norte", _extr_insar(estadio.get("detalle")) or 0.22),
        ("Amalfitani Sur",   0.60),
        ("Básquet",     _extr_insar(sectores.get("poli", {}).get("detalle")) or 0.85),
        ("Sede",             _extr_insar(sectores.get("sede", {}).get("detalle")) or 0.22),
    ]
    insar_divs = []
    for lbl, val in insar_items:
        col = "#d94f4f" if val >= 2 else ("#d4a017" if val >= 1 else "#4a9e6a")
        pct = min(100, val / 3 * 100)
        insar_divs.append(
            f'<div style="background:#0d1117;border:1px solid #c9a84c22;border-radius:6px;'
            f'padding:10px 12px;margin-bottom:6px">'
            f'<div style="display:flex;justify-content:space-between;align-items:center">'
            f'<span style="font-size:11px;color:#9aa0a8">{lbl}</span>'
            f'<span style="font-size:18px;font-weight:700;color:{col}">{val:.2f}'
            f'<span style="font-size:9px;font-weight:400"> mm</span></span></div>'
            f'<div style="height:3px;border-radius:2px;background:#141c24;margin-top:6px">'
            f'<div style="height:100%;width:{pct:.0f}%;border-radius:2px;background:{col}"></div>'
            f'</div></div>'
        )
    insar_html = "".join(insar_divs)

    # ── Tabla agronomica ──────────────────────────────────────────────────────
    n_rec     = wl.get("n_rec_kg_ha", 0)
    riego_v   = wl.get("litros_m2_semana") or wl.get("_riego_min_final", 0)
    hongo_g   = rf.get("nivel", "bajo")
    hongo_txt = {"alto": "Activos", "medio": "Preventivo", "bajo": "No"}.get(hongo_g, "No")
    hongo_col = "#d94f4f" if hongo_g == "alto" else ("#d4a017" if hongo_g == "medio" else "#4a9e6a")
    compact_g = wl.get("soil_moisture_sar", {}).get("compactacion_riesgo")
    n_rec_s   = f"{n_rec:.0f}" if n_rec else "—"
    riego_s   = f"{riego_v:.1f}" if riego_v else "—"
    compact_s = f"{compact_g:.0f}%" if compact_g is not None else "—"
    winter    = month in (5, 6, 7, 8)

    tabla_rows = []
    for c in all_canchas:
        s_raw  = c.get("sem", "amarillo")
        s_vis  = _sem_adj(s_raw, month)
        scol   = _SEM_COLOR.get(s_vis, "#f0b429")
        ndvi   = c.get("ndvi")
        ndre   = c.get("ndre")   # None si sin imagen óptica — muestra "—", no proxy
        ndvi_s = f"{ndvi:.3f}" if ndvi is not None else "—"
        ndre_s = f"{ndre:.2f}" if ndre is not None else "—"
        resem  = "No" if winter else ("Parcial" if (ndvi or 0) < 0.05 else "No")
        tl_col = "#d94f4f" if s_raw == "rojo" else ("#d4a017" if s_raw == "amarillo" else "#9aa0a8")
        timeline = ("HOY — URGENTE" if s_raw == "rojo" else
                    "Esta semana"        if s_raw == "amarillo" else "Sem. +2")
        ac       = _accion_cancha(c.get("id", ""), c.get("nombre", ""), acciones)
        ac_short = ac[0].split(":", 1)[-1].strip()[:32] if ac else _SEM_LABEL.get(s_vis, "—")
        n = c.get("nombre", c.get("id", "?"))
        tabla_rows.append(
            f'<tr>'
            f'<td style="padding:5px 7px;border-bottom:1px solid #ffffff06;white-space:nowrap">'
            f'<span style="display:inline-block;width:7px;height:7px;border-radius:50%;'
            f'background:{scol};margin-right:5px;vertical-align:middle"></span>'
            f'<span style="font-size:11px;font-weight:600;color:#f0ece4">{n}</span></td>'
            f'<td style="padding:5px 7px;border-bottom:1px solid #ffffff06;font-size:11px;color:#9aa0a8">{ndvi_s}</td>'
            f'<td style="padding:5px 7px;border-bottom:1px solid #ffffff06;font-size:11px;color:#9aa0a8">{ndre_s}</td>'
            f'<td style="padding:5px 7px;border-bottom:1px solid #ffffff06;font-size:11px;color:#9aa0a8">{n_rec_s}</td>'
            f'<td style="padding:5px 7px;border-bottom:1px solid #ffffff06;font-size:11px;color:#9aa0a8">{riego_s}</td>'
            f'<td style="padding:5px 7px;border-bottom:1px solid #ffffff06;font-size:11px;color:#9aa0a8">{resem}</td>'
            f'<td style="padding:5px 7px;border-bottom:1px solid #ffffff06;font-size:11px;color:{hongo_col}">{hongo_txt}</td>'
            f'<td style="padding:5px 7px;border-bottom:1px solid #ffffff06;font-size:11px;color:#9aa0a8">{compact_s}</td>'
            f'<td style="padding:5px 7px;border-bottom:1px solid #ffffff06;font-size:11px;color:#9aa0a8">OK</td>'
            f'<td style="padding:5px 7px;border-bottom:1px solid #ffffff06;font-size:11px;color:{tl_col}">{timeline}</td>'
            f'<td style="padding:5px 7px;border-bottom:1px solid #ffffff06;font-size:11px;color:#c9a84c">{ac_short}</td>'
            f'</tr>'
        )
    tabla_html = (
        '<div style="overflow-x:auto">'
        '<table style="border-collapse:collapse;width:100%;min-width:560px;color:#f0ece4">'
        '<tr style="background:#0d1117">'
        + "".join(
            f'<th style="padding:7px;text-align:left;color:#c9a84c;font-size:9px;'
            f'letter-spacing:.1em;border-bottom:1px solid #c9a84c33;white-space:nowrap">{h}</th>'
            for h in ["ZONA", "NDVI", "NDRE", "N kg/ha", "Riego", "Resembrar",
                      "Hongos", "Compact.", "Drenaje", "Timeline", "Acción inmediata"]
        )
        + '</tr>' + "".join(tabla_rows) + '</table></div>'
    )

    # ── Clima ─────────────────────────────────────────────────────────────────
    et0  = wl.get("et0_mm_dia", 0)
    rmin = wl.get("_riego_min_final") or wl.get("riego_min_sector", 0)
    hrio = wl.get("hora_riego_optima", "06:00")
    ds   = wl.get("riesgo_dollar_spot_pct")
    hum  = wl.get("humedad_suelo_pct")
    gdd  = wl.get("gdd_acumulado_7d", 0)
    dias = wl.get("dias_proximo_corte")
    hcor = wl.get("hora_corte_optima", "07:00")
    clima_data = [
        (f"{et0:.1f}" if et0 else "—",  "mm",   "ET₀",      wl.get("et0_fuente", "").split("·")[0] or "Open-Meteo"),
        (str(rmin)    if rmin  else "—", "min",  "Riego hoy",     hrio),
        (f"{ds:.0f}"  if ds  is not None else "—", "%", "Dollar Spot", (rf.get("nivel", "")).upper()),
        (f"{hum:.0f}" if hum is not None else "—", "%", "Humedad suelo", wl.get("humedad_suelo_estado", "") or ""),
        (f"{gdd:.0f}" if gdd   else "—", "GDD",  "Acumulado 7d",  f"{wl.get('gdd_rate_diario', '—')}/día"),
        (str(dias)    if dias   else "—", "días", "Próximo corte", hcor),
    ]
    def _clima_cell(val, unit, lbl, sub):
        sub_html = (
            f'<div style="font-size:10px;color:#f0ece4;margin-top:2px">{sub}</div>'
        ) if sub else ""
        return (
            f'<td style="padding:8px;background:#0d1117;border:1px solid #c9a84c22;'
            f'border-radius:6px;vertical-align:top;width:33%">'
            f'<div style="font-size:20px;font-weight:700;color:#c9a84c;line-height:1">'
            f'{val}<span style="font-size:9px;color:#9aa0a8;font-weight:400"> {unit}</span></div>'
            f'<div style="font-size:9px;font-weight:700;color:#9aa0a8;text-transform:uppercase;'
            f'letter-spacing:.08em;margin-top:2px">{lbl}</div>'
            f'{sub_html}</td>'
        )
    clima_html = (
        '<table style="width:100%;border-collapse:separate;border-spacing:6px">'
        '<tr>' + "".join(_clima_cell(*x) for x in clima_data[:3]) + '</tr>'
        '<tr>' + "".join(_clima_cell(*x) for x in clima_data[3:]) + '</tr>'
        '</table>'
    )

    # ── Tareas de la semana ───────────────────────────────────────────────────
    if tareas_sem:
        tareas_rows = []
        for t in tareas_sem:
            dia     = t.get("dia_nombre", "?")
            items_t = t.get("tareas", [])
            es_p    = t.get("es_partido", False)
            dia_col = "#d94f4f" if es_p else "#c9a84c"
            items_html = "".join(
                f'<div style="font-size:12px;color:#f0ece4">{x}</div>' for x in items_t
            ) or '<div style="font-size:11px;color:#9aa0a8">Sin tareas</div>'
            tareas_rows.append(
                f'<div style="background:#0d1117;border:1px solid #c9a84c22;border-radius:6px;'
                f'padding:8px 12px;margin-bottom:6px;display:flex;gap:12px;align-items:flex-start">'
                f'<div style="min-width:32px;font-size:11px;font-weight:700;color:{dia_col}">{dia}</div>'
                f'<div>{items_html}</div></div>'
            )
        tareas_html = "".join(tareas_rows)
    else:
        tareas_html = '<p style="font-size:12px;color:#9aa0a8">Sin plan de trabajo cargado.</p>'

    # ── Tendencia comparativa ─────────────────────────────────────────────────
    tend_rows = []
    for c in all_canchas:
        sc   = c.get("score") or 0
        prev = c.get("score_prev") or sc
        diff = sc - prev
        arrow = "↑" if diff > 1 else ("↓" if diff < -1 else "→")
        acol  = "#4a9e6a" if diff > 1 else ("#d94f4f" if diff < -1 else "#9aa0a8")
        n = c.get("nombre", c.get("id", "?"))
        tend_rows.append(
            f'<tr>'
            f'<td style="padding:5px 8px;border-bottom:1px solid #ffffff06;font-size:11px;color:#f0ece4">{n}</td>'
            f'<td style="padding:5px 8px;border-bottom:1px solid #ffffff06;font-size:11px;'
            f'color:#9aa0a8;text-align:center">{prev}</td>'
            f'<td style="padding:5px 8px;border-bottom:1px solid #ffffff06;font-size:14px;'
            f'text-align:center;color:{acol}">{arrow}</td>'
            f'<td style="padding:5px 8px;border-bottom:1px solid #ffffff06;font-size:11px;'
            f'font-weight:700;text-align:center;color:#f0ece4">{sc}</td>'
            f'</tr>'
        )
    tend_html = (
        '<table style="width:100%;border-collapse:collapse;background:#07090c">'
        '<tr style="background:#0d1117">'
        + "".join(
            f'<th style="padding:6px 8px;text-align:{"left" if i==0 else "center"};'
            f'color:#c9a84c;font-size:9px;letter-spacing:.1em">{h}</th>'
            for i, h in enumerate(["CANCHA", "ANTERIOR", "", "ACTUAL"])
        )
        + '</tr>' + "".join(tend_rows) + '</table>'
    )

    # ── Ensamblar ─────────────────────────────────────────────────────────────
    body = (
        f"{age_warn}\n"
        f"{hermes_html}\n"
        f'<p style="font-size:15px;font-weight:bold">Roger, informe agronómico — {fecha}.</p>\n'
        f"{_sh('Panel 0', 'Semáforo ejecutivo')}\n{p0_html}\n"
        f"{_sh('Panel 1', 'Mapa de prescripción — dónde trabajar')}\n{p1_html}\n"
        f"{_sh('Panel 2', 'Mapa de alertas — focos exactos')}\n{p2_html}\n"
        f"{_sh('Panel 3', 'Índice Fusión Faro')}\n{p3_html}\n"
        f"{_sh('Tendencia NDVI', 'Kalman LSTM · histórico 2020-2026')}\n{kalman_html}\n"
        f"{_sh('InSAR', 'Deformación estructural · Sentinel-1')}\n{insar_html}\n"
        f"{_sh('Tabla de Prescripción Agronómica', 'Todos los sectores')}\n{tabla_html}\n"
        f"{_sh('Clima', 'Open-Meteo · NASA POWER')}\n{clima_html}\n"
        f"{_sh('Plan de la semana')}\n{tareas_html}\n"
        f"{_sh('Comparativa', 'vs. escaneo anterior')}\n{tend_html}\n"
        f'<p style="font-size:13px;color:#9aa0a8;margin-top:16px">'
        f'Cualquier consulta respondéme por este mail o por WhatsApp. — Faro Protocol</p>'
    )
    return _html_wrap(f"Informe Agronómico Semanal — Vélez Sarsfield · {fecha}", body, panel_url)

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
    fecha    = datetime.now(_ART).strftime("%d/%m/%Y")

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
    canchas  = sectores.get("canchero", {}).get("canchas", [])
    poli     = sectores.get("poli", {})
    fecha    = datetime.now(_ART).strftime("%d/%m/%Y")

    # Traction risk per surface based on NDVI + field score (FIFA/NFL thresholds)
    # NDVI < 0.30 → bare/stressed → HIGH rotational traction → injury risk HIGH
    # NDVI 0.30-0.45 → degraded → MODERATE
    # NDVI > 0.45 → healthy → NORMAL
    def _traction_risk(ndvi, score):
        if ndvi is None:
            return "—", "#9aa0a8", "Sin datos NDVI"
        if ndvi < 0.30 or score < 40:
            return "ALTO", "#e74c3c", "Césped comprometido — riesgo esguinces y roturas por tracción excesiva (FIFA Cat.D)"
        if ndvi < 0.45 or score < 65:
            return "MODERADO", "#f0b429", "Superficie irregular — precaución en arranques y frenadas (FIFA Cat.C)"
        return "BAJO", "#27ae60", "Superficie homogénea — tracción dentro de normativa FIFA/NFL"

    risk_rows = ""
    for c in canchas:
        ndvi  = c.get("ndvi")
        score = c.get("score", 0)
        risk, col, note = _traction_risk(ndvi, score)
        risk_rows += (
            f"<tr>"
            f'<td style="padding:6px;border:1px solid #c9a84c22;font-weight:700">{c.get("nombre","?")}</td>'
            f'<td style="padding:6px;border:1px solid #c9a84c22;text-align:center">{ndvi if ndvi is not None else "—"}</td>'
            f'<td style="padding:6px;border:1px solid #c9a84c22;text-align:center">{score}/100</td>'
            f'<td style="padding:6px;border:1px solid #c9a84c22;color:{col};font-weight:700">{risk}</td>'
            f'<td style="padding:6px;border:1px solid #c9a84c22;font-size:12px;color:#ccc">{note}</td>'
            f"</tr>"
        )

    # Polideportivo row
    poli_ndvi  = None
    poli_score = poli.get("score", 0)
    p_risk, p_col, p_note = _traction_risk(poli_ndvi, poli_score)
    risk_rows += (
        f"<tr>"
        f'<td style="padding:6px;border:1px solid #c9a84c22;font-weight:700">Polideportivo Feijóo</td>'
        f'<td style="padding:6px;border:1px solid #c9a84c22;text-align:center">—</td>'
        f'<td style="padding:6px;border:1px solid #c9a84c22;text-align:center">{poli_score}/100</td>'
        f'<td style="padding:6px;border:1px solid #c9a84c22;color:{p_col};font-weight:700">{p_risk}</td>'
        f'<td style="padding:6px;border:1px solid #c9a84c22;font-size:12px;color:#ccc">{p_note}</td>'
        f"</tr>"
    )

    high_risk = [c for c in canchas if c.get("ndvi", 1) < 0.30 or c.get("score", 100) < 40]  # sat-fallback: ok — predicado filtro, 1 excluye cancha sin dato de lista high_risk
    alert_html = ""
    if high_risk or poli_score < 40:
        names = [c.get("nombre", "?") for c in high_risk]
        if poli_score < 40:
            names.append("Polideportivo Feijóo")
        alert_html = (
            f'<div style="background:rgba(231,76,60,.12);border-left:3px solid #e74c3c;'
            f'padding:10px 14px;margin-bottom:12px;border-radius:0 6px 6px 0">'
            f'<b style="color:#e74c3c">ALERTA FIFA — Riesgo alto de lesión:</b> '
            + ", ".join(names) +
            f'<br><span style="font-size:12px;color:#ccc">Evitar entrenamiento de alta intensidad (sprints, cambios de dirección) hasta recuperación del césped.</span>'
            f'</div>'
        )

    body = f"""
        <p>Sebastián, análisis de riesgo de lesión por tracción rotacional — semana {fecha}.</p>
        {alert_html}
        <h3 style="color:#c9a84c">Riesgo por Superficie — Normativa FIFA / NFL</h3>
        <table style="color:#f2ede4;border-collapse:collapse;width:100%;font-size:13px">
          <tr style="background:#141c24">
            <th style="padding:8px;border:1px solid #c9a84c44;text-align:left">Cancha</th>
            <th style="padding:8px;border:1px solid #c9a84c44;text-align:center">NDVI</th>
            <th style="padding:8px;border:1px solid #c9a84c44;text-align:center">Score</th>
            <th style="padding:8px;border:1px solid #c9a84c44;text-align:center">Riesgo</th>
            <th style="padding:8px;border:1px solid #c9a84c44;text-align:left">Observación</th>
          </tr>
          {risk_rows}
        </table>
        <h3 style="color:#c9a84c">Criterios de Clasificación</h3>
        <ul style="font-size:13px;line-height:1.8;color:#ccc">
          <li><b style="color:#27ae60">BAJO:</b> NDVI &gt; 0.45 · Score &gt; 65 — habilitado para uso pleno</li>
          <li><b style="color:#f0b429">MODERADO:</b> NDVI 0.30–0.45 — uso con monitoreo; evitar sesiones >90 min</li>
          <li><b style="color:#e74c3c">ALTO:</b> NDVI &lt; 0.30 o Score &lt; 40 — restringir sesiones de alta intensidad</li>
        </ul>
        {_novedad_section("pait")}
        <p style="font-size:13px;color:#9aa0a8">Se adjuntan reporte general Vélez y estado Polideportivo.</p>
    """
    return _html_wrap(f"Riesgo de Lesión por Tracción — Semana {fecha}", body, panel_url)


def _body_berlanga(vd: dict, panel_url: str = "") -> str:
    sectores = vd.get("sectores", {})
    u        = vd.get("usuarios", {}).get("berlanga", {})
    kpis     = u.get("kpis", [])
    acciones = u.get("acciones", [])
    fecha    = datetime.now(_ART).strftime("%d/%m/%Y")

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
    fecha    = datetime.now(_ART).strftime("%d/%m/%Y")

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
    fecha    = datetime.now(_ART).strftime("%d/%m/%Y")

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

# Solo Roger tiene panel desplegado. El resto no recibe panel_url en el email.
_HAS_PANEL = {"roger"}

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

def _render_pngs_only(vd: dict) -> int:
    """Regenera los 8 PNGs via render_reports.render_all (fuente única de verdad).
    Llamado tanto por send_all_reports como por run_refresh_only.
    Retorna cantidad de PNGs generados correctamente.
    """
    out_dir = Path(__file__).parents[4] / "reportes_velez"
    out_dir.mkdir(exist_ok=True)
    script_dir = Path(__file__).parent
    rendered = 0
    try:
        sys.path.insert(0, str(script_dir))
        from render_reports import render_all as _render_all
        files = _render_all(vd, out_dir)
        rendered = len(files)
        log.info("_render_pngs_only: %d PNGs generados via render_reports", rendered)
    except Exception as exc:
        log.warning("_render_pngs_only excepción (non-fatal): %s", exc)
    return rendered


def send_all_reports(config: dict = None, vd: dict = None) -> dict:
    """
    Full pipeline: historico snapshot → render PNGs → send emails.
    vd reused to avoid double GitHub fetch.
    """
    if config is None:
        config = load_config()
    if vd is None:
        vd = _get_velez_data()

    # ── FASE B-1: Registro histórico semanal ─────────────────────────────────
    try:
        import historico as _hist
        hist_path = _hist.write_weekly_snapshot(vd)
        log.info("historico: guardado en %s", hist_path)
    except Exception as e:
        log.warning("historico: %s", e)

    # ── FASE B-2: Regenerar PNGs via gen scripts (FARO_VD_PATH) ─────────────
    _render_pngs_only(vd)

    date_str     = datetime.now(_ART).strftime("%d/%m/%Y")
    report_paths = _get_report_paths(config)   # {key → Path} from config.zonas
    results      = {}
    for d in config.get("destinatarios", []):
        nombre = d.get("nombre", "")
        email  = d.get("email", "")
        if not nombre or not email:
            continue
        slug      = _SLUG_MAP.get(nombre, nombre.lower().split()[0])
        panel_url = f"{PANEL_BASE_URL}#{slug}" if slug in _HAS_PANEL else ""
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

        # Attach PNGs from config.destinatarios[].reportes — exact same logic as faro_velez_scheduler.py
        attachments = []
        for rep_key in d.get("reportes", []):
            p = report_paths.get(rep_key)
            if p and p.exists():
                attachments.append((p.name, p.read_bytes()))
            else:
                log.warning("PNG '%s' no encontrado para %s", rep_key, nombre)
        if attachments:
            log.info("Adjuntos para %s: %d PNG(s)", nombre, len(attachments))

        # Attach personal PDF manual (always included)
        manual_p = MANUAL_PATHS.get(slug)
        if manual_p and manual_p.exists():
            attachments.append((manual_p.name, manual_p.read_bytes()))
            log.info("Manual PDF adjunto para %s: %s", nombre, manual_p.name)
        elif manual_p:
            log.warning("Manual PDF no encontrado para %s: %s", nombre, manual_p)

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

    # FaroEngine V2 — guaranteed weekly run: SAR + solar + HAND para ambos venues
    # villa_olimpica también genera per-cancha SAR (12 subsets del mismo granule OPERA)
    try:
        from faro_v2_engine import FaroEngine
        for _venue in ["amalfitani", "villa_olimpica"]:
            try:
                _rpt = FaroEngine(_venue).run_and_persist(skip=["canopy", "lband"])
                log.info("FaroEngine V2 weekly %s OK — SHA256=%s… por_cancha=%d",
                         _venue, _rpt.audit.sha256[:16],
                         len(_rpt.sar.por_cancha) if _rpt.sar else 0)
            except Exception as _ve:
                log.warning("FaroEngine V2 weekly %s (non-fatal): %s", _venue, _ve)
    except Exception as _fe:
        log.warning("FaroEngine V2 weekly (non-fatal): %s", _fe)

    # ECOSTRESS (ECO3ETPTJPL 70m) + SMAP Enhanced L3 — NASA Earthdata via earthaccess
    # Resultado: climate_metrics.et_ecostress_mm + soil_metrics.theta_smap
    # Hermes los expone a weather_live en el ciclo siguiente; assembler step 3 los lee
    try:
        from faro_ecostress import run_ecostress_cycle
        _eco = run_ecostress_cycle("amalfitani")
        log.info("ECOSTRESS weekly: ET=%.3f mm/día · SMAP θ=%s · alerta_et=%s",
                 _eco.get("et_ecostress_mm") or 0,
                 _eco.get("theta_smap"), _eco.get("alerta_et"))
    except Exception as _eco_err:
        log.warning("ECOSTRESS weekly (non-fatal): %s", _eco_err)

    # Landsat 9 TIRS — LST 30m superficie del campo (°C) → soil_metrics.temp_superficie_c
    # Alerta si temp > 35°C + θ_soil < 0.15 (estrés hídrico severo) o > 40°C (quemadura raíces)
    try:
        from faro_landsat_thermal import run_landsat_cycle
        _lst = run_landsat_cycle("amalfitani")
        log.info("Landsat thermal weekly: LST=%.1f°C · alerta_calor=%s",
                 _lst.get("temp_superficie_c") or 0, _lst.get("alerta_calor"))
    except Exception as _lst_err:
        log.warning("Landsat thermal weekly (non-fatal): %s", _lst_err)

    # GOES-16 ABI — nubosidad tiempo real (AWS S3 público, 10-min refresh)
    try:
        from faro_goes16 import run_goes16_cycle
        _g16 = run_goes16_cycle("amalfitani")
        log.info("GOES-16 weekly: nubosidad=%.1f%% clara=%s",
                 _g16.get("nubosidad_pct") or 0, _g16.get("escena_clara"))
    except Exception as _g16_err:
        log.warning("GOES-16 weekly (non-fatal): %s", _g16_err)

    # CYGNSS L3 25km — humedad suelo macro (contexto venue, 12-24h revisita)
    try:
        from faro_cygnss import run_cygnss_cycle
        _cyg = run_cygnss_cycle("amalfitani")
        log.info("CYGNSS weekly: SM=%.4f m³/m³", _cyg.get("sm_cygnss_m3m3") or 0)
    except Exception as _cyg_err:
        log.warning("CYGNSS weekly (non-fatal): %s", _cyg_err)

    # GPM IMERG Daily Late — precipitación acumulada 24h (4-6h latencia)
    try:
        from faro_gpm import run_gpm_cycle
        _gpm = run_gpm_cycle("amalfitani")
        log.info("GPM weekly: precip=%.2f mm/día", _gpm.get("precip_gpm_mm") or 0)
    except Exception as _gpm_err:
        log.warning("GPM weekly (non-fatal): %s", _gpm_err)

    # Satellite pipeline — NDVI per-cancha + PNGs + GitHub push
    try:
        import satellite_pipeline
        sat_result = satellite_pipeline.run_satellite_cycle()
        log.info("satellite_pipeline (weekly fallback): %s", sat_result)
    except Exception as _se:
        log.warning("satellite_pipeline weekly fallback (non-fatal): %s", _se)

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


def run_refresh_only(force: bool = False) -> dict:
    """Regenera datos y PNGs. SIN emails ni WhatsApp.
    force=True: re-procesa la imagen aunque ya esté en Supabase (útil después de fixes de código).
    """
    log.info("=== run_refresh_only: inicio (force=%s) ===", force)
    result: dict = {"satellite": None, "pngs": 0, "faro_v2": None}

    # FaroEngine V2 — SAR soil moisture + HAND + solar (Supabase faro_v2_reports)
    # villa_olimpica también genera per-cancha SAR (12 subsets del mismo granule OPERA)
    try:
        from faro_v2_engine import FaroEngine
        _v2: dict = {}
        for _venue in ["amalfitani", "villa_olimpica"]:
            try:
                _rpt = FaroEngine(_venue).run_and_persist(skip=["canopy", "lband"])
                _v2[_venue] = {
                    "ok": True, "sha256": _rpt.audit.sha256[:16],
                    "por_cancha": len(_rpt.sar.por_cancha) if _rpt.sar else 0,
                }
            except Exception as _ve:
                _v2[_venue] = {"ok": False, "error": str(_ve)[:120]}
        result["faro_v2"] = _v2
        log.info("FaroEngine V2 standalone: %s", _v2)
    except Exception as exc:
        log.warning("FaroEngine V2 (non-fatal): %s", exc)

    # ECOSTRESS + Landsat thermal — NASA missions, resultado en climate_metrics / soil_metrics
    try:
        from faro_ecostress import run_ecostress_cycle
        result["ecostress"] = run_ecostress_cycle("amalfitani")
        log.info("ECOSTRESS refresh: %s", result["ecostress"])
    except Exception as exc:
        log.warning("ECOSTRESS (non-fatal): %s", exc)
    try:
        from faro_landsat_thermal import run_landsat_cycle
        result["landsat_thermal"] = run_landsat_cycle("amalfitani")
        log.info("Landsat thermal refresh: %s", result["landsat_thermal"])
    except Exception as exc:
        log.warning("Landsat thermal (non-fatal): %s", exc)

    # GOES-16 ABI — nubosidad tiempo real (AWS S3 público, sin auth)
    try:
        from faro_goes16 import run_goes16_cycle
        result["goes16"] = run_goes16_cycle("amalfitani")
        log.info("GOES-16 refresh: nubosidad=%.1f%% clara=%s",
                 result["goes16"].get("nubosidad_pct") or 0,
                 result["goes16"].get("escena_clara"))
    except Exception as exc:
        log.warning("GOES-16 (non-fatal): %s", exc)

    # CYGNSS L3 — humedad suelo macro 25km
    try:
        from faro_cygnss import run_cygnss_cycle
        result["cygnss"] = run_cygnss_cycle("amalfitani")
        log.info("CYGNSS refresh: SM=%.4f m³/m³", result["cygnss"].get("sm_cygnss_m3m3") or 0)
    except Exception as exc:
        log.warning("CYGNSS (non-fatal): %s", exc)

    # GPM IMERG — precipitación diaria acumulada
    try:
        from faro_gpm import run_gpm_cycle
        result["gpm"] = run_gpm_cycle("amalfitani")
        log.info("GPM refresh: precip=%.2f mm/día", result["gpm"].get("precip_gpm_mm") or 0)
    except Exception as exc:
        log.warning("GPM (non-fatal): %s", exc)

    try:
        import satellite_pipeline
        result["satellite"] = satellite_pipeline.run_satellite_cycle(force=force)
        log.info("satellite_pipeline: %s", result["satellite"])
    except Exception as exc:
        log.warning("satellite_pipeline (non-fatal): %s", exc)

    # VO SAR backfill — per-cancha sigma0 for all 12 Villa Olímpica canchas
    try:
        from sar_cancha_fetch import run_vo_sar_backfill
        result["sar_vo"] = run_vo_sar_backfill(days=14)
        log.info("sar_vo backfill: %s", result["sar_vo"])
    except Exception as exc:
        log.warning("sar_vo backfill (non-fatal): %s", exc)

    vd = _get_velez_data()
    result["pngs"] = _render_pngs_only(vd)
    log.info("=== run_refresh_only: done — %d PNGs ===", result["pngs"])
    return result


# ── APScheduler integration ───────────────────────────────────────────────────

def register_jobs(scheduler) -> None:
    from apscheduler.triggers.cron import CronTrigger
    scheduler.add_job(
        run_weekly_job,
        CronTrigger(day_of_week="mon", hour=10, minute=0, timezone="UTC"),
        id="weekly_report",
        replace_existing=True,
        misfire_grace_time=7200,
    )
    log.info("Weekly job registrado — Lunes 07:00 ART (10:00 UTC)")


# ── Flask route handlers (registered in app.py) ───────────────────────────────

def route_run_now():
    from flask import request, jsonify
    tipo = (request.get_json(silent=True, force=True) or {}).get("tipo", "manual")
    # run_refresh_only: regenera datos y PNGs, NUNCA envía emails al club
    threading.Thread(target=run_refresh_only, daemon=True).start()
    return jsonify({"status": "started", "tipo": tipo})


def route_weekly_status():
    from flask import jsonify
    return jsonify({
        "last_weekly": _last_weekly,
        "next_weekly": "Monday 07:00 ART (10:00 UTC)",
    })


def route_debug_vd():
    """GET /velez/debug_vd — vuelca campos clave del assembler para diagnóstico."""
    from flask import jsonify
    vd = _get_velez_data()

    # canchas — ndvi, score, sem por cancha
    canchas_raw = vd.get("sectores", {}).get("canchero", {}).get("canchas", [])
    canchas_diag = [
        {
            "id":    c.get("id"),
            "ndvi":  c.get("ndvi"),
            "score": c.get("score"),
            "sem":   c.get("sem"),
            "ndre":  c.get("ndre"),
            "ccci":  c.get("ccci"),
            "fuente": c.get("fuente"),
        }
        for c in canchas_raw
    ]

    # weather_live — campos que usan los gen scripts
    wl = vd.get("weather_live", {})
    wl_diag = {k: wl.get(k) for k in (
        "deficit_hidrico_mm", "et0_mm_dia", "smith_kerns_pct",
        "riesgo_dollar_spot_pct", "gdd_acumulado_7d",
        "sar_vv_db", "sar_vh_db", "humedad_suelo_pct", "humedad_suelo_smc",
        "lluvia_max_1h_mm", "lluvia_48h_mm",
        "riego_min_sector",
    )}

    # solar — sector solar score + ghi
    solar = vd.get("sectores", {}).get("solar", {})
    solar_diag = {k: solar.get(k) for k in ("score", "sem", "ghi_kwh_m2", "eficiencia_pct")}

    # estadio — InSAR + HAND + NDVI
    estadio = vd.get("sectores", {}).get("estadio", {})
    insar_diag = {k: estadio.get(k) for k in ("insar_mm", "ndvi", "score", "sem", "detalle",
                                                "hand_mean_m", "hand_zona")}

    # hermes — confianza + fuentes activas
    hermes = vd.get("hermes", {})
    hermes_diag = {
        "confianza": hermes.get("confianza_consolidada"),
        "fuentes_activas": hermes.get("fuentes_activas"),
        "alertas": hermes.get("alertas", [])[:5],
    }

    return jsonify({
        "assembled_at": vd.get("_assembled_at"),
        "canchas":      canchas_diag,
        "weather_live": wl_diag,
        "solar":        solar_diag,
        "insar_estadio": insar_diag,
        "hermes":       hermes_diag,
        "roger_canchas_count": len(vd.get("roger_canchas", [])),
    })


def route_test_whatsapp():
    from flask import request, jsonify
    nombre = (request.get_json(silent=True) or {}).get("nombre")
    users  = [u for u in _WA_ALERT_USERS if not nombre or u["nombre"] == nombre]
    results = {}
    for u in users:
        api_key = _env(u["env_key"]) or (_env(u["env_key_alt"]) if u.get("env_key_alt") else "")
        msg = f"Faro Protocol · TEST desde Railway · {datetime.now(_ART).strftime('%d/%m %H:%M')}"
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
    from flask import jsonify
    log.info("test_email: iniciando envío a protocolfaro@gmail.com")
    try:
        ok = send_email(
            to="protocolfaro@gmail.com",
            subject="Faro Protocol — Test SMTP",
            body_html="<h2>Test OK</h2><p>El sistema de emails funciona correctamente.</p>",
            attachments=None,
        )
        log.info("test_email: send_email returned %s", ok)
        return jsonify({"ok": ok, "to": "protocolfaro@gmail.com"})
    except Exception as e:
        log.error("test_email: excepción — %s", e)
        return jsonify({"ok": False, "error": str(e)}), 500


# ── Preview email routes ───────────────────────────────────────────────────────

_SLUG_TO_FULLNAME = {v: k for k, v in _SLUG_MAP.items() if "á" not in k and "é" not in k and "ó" not in k and "ú" not in k and "í" not in k}
# Canonical mapping slug → display name
_PREVIEW_SLUGS = {
    "roger":    "Roger Bernal",
    "juan":     "Juan Gonzalez",
    "banchero": "Fernando Banchero",
    "pait":     "Sebastian Pait",
    "berlanga": "Fabian Berlanga",
    "nelson":   "Nelson Pugliese",
    "aveleyra": "Alberto Aveleyra",
}


def route_force_reprocess():
    """POST /velez/force_reprocess
    Re-procesa la imagen satelital actual aunque ya esté en Supabase.
    Usar después de fixes de código (coordenadas, bandas, etc.) para que apliquen al dato existente.
    """
    from flask import jsonify
    log.info("force_reprocess: iniciando re-procesamiento forzado")
    threading.Thread(target=run_refresh_only, kwargs={"force": True}, daemon=True).start()
    return jsonify({"status": "started", "force": True,
                    "msg": "Re-procesamiento forzado iniciado — ver Railway logs"})


def route_preview_email():
    """GET /velez/preview_email?recipient=berlanga
    Devuelve el HTML del email exactamente como lo recibiría el destinatario.
    Útil para revisar antes de aprobar el envío masivo.
    """
    from flask import request, Response
    recipient = request.args.get("recipient", "berlanga").lower().strip()
    nombre = _PREVIEW_SLUGS.get(recipient)
    if not nombre:
        valid = ", ".join(_PREVIEW_SLUGS.keys())
        return Response(
            f"<p>Destinatario '{recipient}' no encontrado. Opciones: {valid}</p>",
            mimetype="text/html", status=400,
        )
    body_fn = _BODY_FN_MAP.get(nombre)
    if not body_fn:
        return Response(f"<p>Sin template para {nombre}</p>", mimetype="text/html", status=404)

    try:
        vd = _get_velez_data()
        panel_url = f"{PANEL_BASE_URL}#{recipient}" if recipient in _HAS_PANEL else ""
        html = body_fn(vd, panel_url=panel_url)
        # Inject preview banner at top
        banner = (
            f'<div style="background:#c9a84c;color:#06080b;padding:10px 20px;'
            f'font-family:Arial,sans-serif;font-size:13px;font-weight:bold">'
            f'[PREVIEW] Así vería este email: <b>{nombre}</b> — '
            f'<a href="/velez/preview_email?recipient={recipient}" style="color:#06080b">recargar</a>'
            f'</div>'
        )
        html = html.replace("<html>", "<html>", 1)
        html = html.replace("<body", banner + "<body", 1)
        log.info("preview_email: renderizado para %s", nombre)
        return Response(html, mimetype="text/html")
    except Exception as e:
        log.error("preview_email excepción (%s): %s", nombre, e)
        return Response(f"<p>Error: {e}</p>", mimetype="text/html", status=500)


def route_send_preview_email():
    """POST /velez/send_preview_email  body: {"recipient": "berlanga"}
    Manda el email de preview a protocolfaro@gmail.com con asunto [PREVIEW].
    """
    from flask import request, jsonify
    data = request.get_json(silent=True) or {}
    recipient = data.get("recipient", request.args.get("recipient", "berlanga")).lower().strip()
    nombre = _PREVIEW_SLUGS.get(recipient)
    if not nombre:
        return jsonify({"ok": False, "error": f"recipient '{recipient}' desconocido"}), 400

    body_fn = _BODY_FN_MAP.get(nombre)
    if not body_fn:
        return jsonify({"ok": False, "error": f"sin template para {nombre}"}), 404

    try:
        vd = _get_velez_data()
        panel_url = f"{PANEL_BASE_URL}#{recipient}" if recipient in _HAS_PANEL else ""
        html = body_fn(vd, panel_url=panel_url)
        date_str = datetime.now(_ART).strftime("%d/%m/%Y")
        subject = f"[PREVIEW] Faro · Reporte semanal · Vélez · {date_str} — vista de {nombre}"
        ok = send_email(
            to="protocolfaro@gmail.com",
            subject=subject,
            body_html=html,
            attachments=None,
        )
        log.info("send_preview_email: %s → protocolfaro@gmail.com %s", nombre, "OK" if ok else "FAIL")
        return jsonify({"ok": ok, "recipient": nombre, "to": "protocolfaro@gmail.com", "subject": subject})
    except Exception as e:
        log.error("send_preview_email excepción (%s): %s", nombre, e)
        return jsonify({"ok": False, "error": str(e)}), 500


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
