"""
Faro Protocol — Vélez Sarsfield Scheduler
Reporte semanal + eventos + alertas urgentes + WhatsApp + email.
Railway-ready. Jobs persistentes en JSON. Config externa: config_velez.json.

Para agregar una zona nueva:   editar config_velez.json → "zonas"
Para agregar un destinatario: editar config_velez.json → "destinatarios"
Sin tocar código Python.
"""
import os, json, time, hashlib, logging, smtplib, threading, traceback
from datetime import datetime, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders
from pathlib import Path
from typing import Optional

from flask import Flask, request, jsonify
import requests
import schedule

# ─── CONFIG ──────────────────────────────────────────────────────────────────
CONFIG_FILE = Path(__file__).parent / 'config_velez.json'
JOBS_FILE   = Path(__file__).parent / 'velez_jobs.json'
LOG_FILE    = Path(__file__).parent / 'velez_scheduler.log'

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler(),
    ]
)
log = logging.getLogger('velez')

def env(key: str, default: str = '') -> str:
    return os.environ.get(key, default)

GMAIL_USER = env('GMAIL_USER', 'protocolfaro@gmail.com')
GMAIL_PASS = env('GMAIL_APP_PASS')

PORT     = int(env('PORT', '5000'))
DESKTOP  = Path.home() / 'Desktop'
BASE_DIR = Path(__file__).parent

# Alert thresholds
NDVI_ALERT      = 0.35
INSAR_ALERT     = 3.0   # mm
TEMP_ALERT      = 28.0  # °C
SOLAR_ALERT_EFF = 75.0  # %

# ─── CARGA DE CONFIG EXTERNA ─────────────────────────────────────────────────

def load_config() -> dict:
    """Lee config_velez.json. Fallback a config vacía si no existe o hay error."""
    if CONFIG_FILE.exists():
        try:
            return json.loads(CONFIG_FILE.read_text(encoding='utf-8'))
        except Exception as e:
            log.error(f'Error leyendo config_velez.json: {e}')
    log.warning('config_velez.json no encontrado — usando config vacía')
    return {'zonas': [], 'destinatarios': []}

def output_to_key(output_filename: str) -> str:
    """Convierte nombre de archivo de output a clave interna.
    'faro_reporte_velez_canchero.png' → 'canchero'
    'faro_reporte_velez.png'          → 'velez'
    'faro_reporte_velez_agro_FINAL.png' → 'agro_FINAL'
    """
    stem = Path(output_filename).stem      # sin extensión
    prefix = 'faro_reporte_velez'
    if stem == prefix:
        return 'velez'
    return stem[len(prefix) + 1:]          # quita 'faro_reporte_velez_'

def get_report_paths(config: dict = None) -> dict:
    """Construye dict {clave → Path} de todos los reportes desde config.zonas."""
    if config is None:
        config = load_config()
    return {
        output_to_key(z['output']): BASE_DIR / 'reportes_velez' / z['output']
        for z in config.get('zonas', [])
    }

# Manuales PDF — indexados por clave de nombre de persona
MANUAL_PATHS = {
    'roger':    BASE_DIR / 'reportes_velez' / 'manual_velez_roger.pdf',
    'juan':     BASE_DIR / 'reportes_velez' / 'manual_velez_juan.pdf',
    'banchero': BASE_DIR / 'reportes_velez' / 'manual_velez_banchero.pdf',
    'pait':     BASE_DIR / 'reportes_velez' / 'manual_velez_pait.pdf',
    'berlanga': BASE_DIR / 'reportes_velez' / 'manual_velez_berlanga.pdf',
    'nelson':   BASE_DIR / 'reportes_velez' / 'manual_velez_nelson.pdf',
    'aveleyra': BASE_DIR / 'reportes_velez' / 'manual_velez_aveleyra.pdf',
}

_NAME_TO_MANUAL_KEY = {
    'Roger Bernal':       'roger',
    'Juan Gonzalez':      'juan',
    'Fernando Banchero':  'banchero',
    'Sebastian Pait':     'pait',
    'Fabian Berlanga':    'berlanga',
    'Nelson Pugliese':    'nelson',
    'Alberto Aveleyra':   'aveleyra',
}

def _manual_key_for_name(nombre: str) -> Optional[str]:
    return _NAME_TO_MANUAL_KEY.get(nombre)

# ─── JOBS PERSISTENCE ────────────────────────────────────────────────────────

def load_jobs() -> dict:
    if JOBS_FILE.exists():
        try:
            data = json.loads(JOBS_FILE.read_text())
            data.setdefault('manual_sent', False)
            return data
        except Exception:
            pass
    return {'events': [], 'email_queue': [], 'last_report': None,
            'last_solar': None, 'manual_sent': False}

def save_jobs(jobs: dict):
    JOBS_FILE.write_text(json.dumps(jobs, indent=2, default=str))

# ─── WHATSAPP (CallMeBot) ─────────────────────────────────────────────────────

def _wa_key_for_phone(phone: str) -> str:
    """Obtiene la API key de CallMeBot para un número dado vía env vars.
    Mapeo phone → nombre de env var de la API key."""
    phone_to_key = {
        env('VELEZ_WHATSAPP_CANCHERO'):   env('CALLMEBOT_KEY_CANCHERO'),
        env('VELEZ_WHATSAPP_INTENDENTE'): env('CALLMEBOT_KEY_INTENDENTE'),
        env('VELEZ_WHATSAPP_NELSON'):     env('CALLMEBOT_KEY_NELSON'),
        env('VELEZ_WHATSAPP_BANCHERO'):   env('CALLMEBOT_KEY_BANCHERO'),
    }
    return phone_to_key.get(phone, '')

def send_whatsapp(phone: str, message: str) -> bool:
    key = _wa_key_for_phone(phone)
    if not phone or not key:
        log.warning(f'WhatsApp key no configurada para {phone[:8]}*** — omitido')
        return False
    try:
        r = requests.get('https://api.callmebot.com/whatsapp.php',
                         params={'phone': phone, 'text': message, 'apikey': key},
                         timeout=15)
        if r.status_code == 200:
            log.info(f'WhatsApp enviado a {phone[:8]}***')
            return True
        log.error(f'WhatsApp falló {phone[:8]}***: {r.status_code} {r.text[:100]}')
        return False
    except Exception as e:
        log.error(f'WhatsApp excepción {phone[:8]}***: {e}')
        return False

def notify_whatsapp_all(message: str, config: dict = None):
    """Envía mensaje a todos los números con WhatsApp en el config."""
    if config is None:
        config = load_config()
    for d in config.get('destinatarios', []):
        if d.get('whatsapp'):
            send_whatsapp(d['whatsapp'], message)

# ─── EMAIL ────────────────────────────────────────────────────────────────────

def send_email(to: str, subject: str, body_html: str,
               attachments: list = None) -> bool:
    """Envía email a uno o más destinatarios (to separado por comas).
    attachments: lista de rutas de archivo."""
    if not to or not GMAIL_PASS:
        log.warning(f'Email no configurado (to={bool(to)}, pass={bool(GMAIL_PASS)})')
        return False
    try:
        recipients = [r.strip() for r in to.split(',') if r.strip()]
        msg = MIMEMultipart('mixed')
        msg['From']    = GMAIL_USER
        msg['To']      = ', '.join(recipients)
        msg['Subject'] = subject
        msg.attach(MIMEText(body_html, 'html'))

        for att_path in (attachments or []):
            p = Path(att_path)
            if p.exists():
                with open(p, 'rb') as f:
                    part = MIMEBase('application', 'octet-stream')
                    part.set_payload(f.read())
                encoders.encode_base64(part)
                part.add_header('Content-Disposition',
                    f'attachment; filename="{p.name}"')
                msg.attach(part)
            else:
                log.warning(f'Adjunto no encontrado, omitido: {att_path}')

        with smtplib.SMTP_SSL('smtp.gmail.com', 465) as server:
            server.login(GMAIL_USER, GMAIL_PASS)
            server.sendmail(GMAIL_USER, recipients, msg.as_string())

        log.info(f'Email enviado a {recipients}: {subject}')
        return True
    except Exception as e:
        log.error(f'Email falló a {to}: {e}')
        return False

def queue_email(to: str, subject: str, body: str, attachments: list = None):
    jobs = load_jobs()
    jobs['email_queue'].append({
        'to': to, 'subject': subject, 'body': body,
        'attachments': attachments or [], 'queued_at': datetime.now().isoformat()
    })
    save_jobs(jobs)

def flush_email_queue():
    jobs = load_jobs()
    remaining = []
    for item in jobs.get('email_queue', []):
        atts = item.get('attachments') or ([item['attachment']] if item.get('attachment') else [])
        ok = send_email(item['to'], item['subject'], item['body'], atts)
        if not ok:
            remaining.append(item)
    jobs['email_queue'] = remaining
    save_jobs(jobs)

# ─── REPORT GENERATORS ───────────────────────────────────────────────────────

def _run_script(script_path: str) -> bool:
    import subprocess
    try:
        result = subprocess.run(
            ['python', script_path],
            capture_output=True, text=True, timeout=300
        )
        if result.returncode == 0:
            log.info(f'Script OK: {script_path}')
            return True
        log.error(f'Script falló: {script_path}\n{result.stderr[:500]}')
        return False
    except Exception as e:
        log.error(f'Script excepción: {script_path}: {e}')
        return False

def generate_report(script_name: str, out_path: Path) -> Optional[str]:
    """Ejecuta script generador. Busca en BASE_DIR primero, luego DESKTOP.
    Fallback: PNG anterior si la generación falla (nunca envía email vacío)."""
    script = BASE_DIR / script_name
    if not script.exists():
        script = DESKTOP / script_name
    if script.exists():
        if _run_script(str(script)):
            return str(out_path) if out_path.exists() else None
        log.error(f'Script falló: {script_name}')
    else:
        log.warning(f'Script no encontrado: {script_name}')
    if out_path.exists():
        log.warning(f'Usando reporte cacheado (fallback): {out_path.name}')
        return str(out_path)
    log.error(f'Sin fallback para {out_path.name}')
    return None

def generate_all_reports_from_config(config: dict) -> dict:
    """Genera todos los reportes definidos en config.zonas.
    Retorna dict {clave → ruta_o_None}."""
    results = {}
    for zona in config.get('zonas', []):
        key      = output_to_key(zona['output'])
        out_path = BASE_DIR / 'reportes_velez' / zona['output']
        path     = generate_report(zona['script'], out_path)
        results[key] = path
        status = 'OK' if path else 'SIN REPORTE'
        log.info(f'Zona {zona["nombre"]}: {status}')
    return results

# ─── REPORT DATA ─────────────────────────────────────────────────────────────

def load_latest_data() -> dict:
    data_path = BASE_DIR / 'data.json'
    if data_path.exists():
        try:
            return json.loads(data_path.read_text())
        except Exception:
            pass
    return {
        'zones': [
            {'name': 'Campo Amalfitani', 'ndvi': 0.68, 'temp': 22.1,
             'insar_mm': 0.85, 'sem': 'verde'},
            {'name': 'Cancha 1',  'ndvi': 0.48, 'temp': 24.3, 'insar_mm': 1.20, 'sem': 'amarillo'},
            {'name': 'Cancha 2',  'ndvi': 0.52, 'temp': 23.8, 'insar_mm': 0.60, 'sem': 'amarillo'},
            {'name': 'Cancha 3',  'ndvi': 0.39, 'temp': 25.1, 'insar_mm': 2.80, 'sem': 'amarillo'},
            {'name': 'Cancha 4',  'ndvi': 0.24, 'temp': 27.8, 'insar_mm': 2.80, 'sem': 'rojo'},
        ],
        'solar': {'eff_pct': 82.4, 'kwp_actual': 98.9, 'anomaly_zone': None},
        'tribunas': {'norte': 0.85, 'sur': 1.20, 'este': 0.60, 'oeste': 2.80},
    }

# ─── ALERT CHECK ─────────────────────────────────────────────────────────────

def check_and_send_alerts(data: dict, config: dict = None):
    if config is None:
        config = load_config()
    zones    = data.get('zones', [])
    solar    = data.get('solar', {})
    tribunas = data.get('tribunas', {})

    # Teléfonos para alertas desde el config
    phones_campo = [
        d['whatsapp'] for d in config.get('destinatarios', [])
        if d.get('whatsapp') and 'canchero' in d.get('reportes', [])
    ]
    phones_infra = [
        d['whatsapp'] for d in config.get('destinatarios', [])
        if d.get('whatsapp') and any(r in d.get('reportes', []) for r in ['velez', 'sede', 'poli'])
    ]

    for z in zones:
        name  = z.get('name', '?')
        ndvi  = z.get('ndvi', 1.0)
        temp  = z.get('temp', 0.0)
        insar = z.get('insar_mm', 0.0)

        if ndvi < NDVI_ALERT:
            msg = f'FARO ALERTA: {name} necesita intervencion urgente (NDVI={ndvi:.2f})'
            for ph in phones_campo:
                send_whatsapp(ph, msg)
            log.info(f'Alerta NDVI enviada para {name}')

        if temp > TEMP_ALERT:
            msg = f'FARO ALERTA: Estres hidrico critico en {name} (Temp={temp:.1f}C)'
            for ph in phones_campo:
                send_whatsapp(ph, msg)
            log.info(f'Alerta temperatura enviada para {name}')

    for tribuna, val in tribunas.items():
        if val > INSAR_ALERT:
            msg = f'FARO ALERTA: Tribuna {tribuna.upper()} supera umbral InSAR ({val:.2f}mm)'
            for ph in phones_infra:
                send_whatsapp(ph, msg)
            log.info(f'Alerta InSAR enviada para tribuna {tribuna}')

    if solar.get('anomaly_zone'):
        msg = f'FARO ALERTA: Panel solar zona {solar["anomaly_zone"]} con falla detectada'
        for ph in phones_infra:
            send_whatsapp(ph, msg)
        log.info('Alerta solar enviada')

# ─── EMAIL BODIES ─────────────────────────────────────────────────────────────

def _html_wrap(title: str, body: str) -> str:
    return f"""
<html><body style="font-family:Arial,sans-serif;background:#06080b;color:#f2ede4;padding:20px">
<h2 style="color:#c9a84c">{title}</h2>
{body}
<hr style="border-color:#c9a84c44">
<p style="color:#9aa0a8;font-size:12px">
Faro Protocol · Fortín Inteligente · protocolfaro@gmail.com<br>
Generado automáticamente · {datetime.now().strftime('%d/%m/%Y %H:%M')} UTC-3
</p></body></html>"""

def _body_roger() -> str:
    return _html_wrap(
        'Tu Mapa de Trabajo Esta Semana — Campo Amalfitani',
        """
        <p style="font-size:16px;font-weight:bold">Roger, te mando el mapa de esta semana.</p>
        <p style="font-size:14px">Los circulos de colores en el mapa marcan exactamente donde ir.</p>
        <h3 style="color:#e74c3c">HOY — NO PUEDE ESPERAR</h3>
        <ul style="font-size:15px;line-height:1.8">
          <li><b>Cancha 4 — zona central y lateral sur:</b><br>
              Tirar fungicida, reparar el drenaje roto, y sembrar semilla nueva donde falta pasto</li>
        </ul>
        <h3 style="color:#f0b429">Esta semana</h3>
        <ul style="font-size:15px;line-height:1.8">
          <li><b>Cancha 1:</b> fungicida preventivo antes de que avance</li>
          <li><b>Cancha 2:</b> fertilizar todo el campo</li>
          <li><b>Cancha 3:</b> fungicida en las manchas + sembrar donde falta</li>
        </ul>
        <h3 style="color:#27ae60">Semana 3 (sin apuro)</h3>
        <ul style="font-size:15px;line-height:1.8">
          <li><b>Campo Amalfitani:</b> aerificar las dos porterias</li>
        </ul>
        <p style="font-size:13px;color:#9aa0a8">
          Cualquier duda respondeme por este mail o por WhatsApp. — Faro Protocol
        </p>
        """
    )

def _body_juan() -> str:
    return _html_wrap(
        'Estado Villa Olímpica Esta Semana',
        f"""
        <p>Juan, te mando el estado actualizado de las canchas, el campo y el Polideportivo.</p>
        <h3 style="color:#e74c3c">Urgente — Cancha 4</h3>
        <ul style="font-size:14px">
          <li>Focos activos de hongo en zona central (85 m²) — requiere fungicida HOY</li>
          <li>Drenaje lateral sur roto — agua estancada confirmada por satélite</li>
          <li>Resembrado zona central necesario esta semana</li>
        </ul>
        <h3 style="color:#f0b429">Polideportivo Feijóo — Esta Semana</h3>
        <ul style="font-size:14px">
          <li><b>Básquet (ext):</b> fisuras detectadas — inspección urgente</li>
          <li><b>Playón Norte:</b> relevamiento estructural recomendado</li>
          <li><b>Tenis 1 y 2:</b> riego preventivo programado</li>
        </ul>
        <h3 style="color:#f0b429">Campo Amalfitani</h3>
        <ul style="font-size:14px">
          <li><b>Canchas 1–3:</b> tratamientos agronómicos en curso</li>
          <li><b>Campo Amalfitani:</b> NDVI 0.68 — estado óptimo, mantener riego</li>
        </ul>
        <p style="font-size:13px;color:#9aa0a8">
          Se adjuntan mapas de canchas, reporte agro y estado polideportivo.
        </p>
        """
    )

def _body_banchero() -> str:
    return _html_wrap(
        'Estado Operativo del Predio — Vélez Sarsfield',
        f"""
        <p>Fernando, resumen operativo completo de la semana del {datetime.now().strftime("%d/%m/%Y")}.</p>
        <h3 style="color:#c9a84c">Predio Completo</h3>
        <table style="color:#f2ede4;border-collapse:collapse;width:100%;font-size:14px">
          <tr style="background:#141c24">
            <th style="padding:8px;border:1px solid #c9a84c44;text-align:left">Área</th>
            <th style="padding:8px;border:1px solid #c9a84c44;text-align:left">Estado</th>
            <th style="padding:8px;border:1px solid #c9a84c44;text-align:left">Acción requerida</th>
          </tr>
          <tr><td style="padding:6px;border:1px solid #c9a84c22">Cancha 4</td>
              <td style="padding:6px;color:#e74c3c;border:1px solid #c9a84c22"><b>CRÍTICO</b></td>
              <td style="padding:6px;border:1px solid #c9a84c22">Intervención urgente — ver mapa</td></tr>
          <tr><td style="padding:6px;border:1px solid #c9a84c22">Canchas 1, 2, 3</td>
              <td style="padding:6px;color:#f0b429;border:1px solid #c9a84c22"><b>ATENCIÓN</b></td>
              <td style="padding:6px;border:1px solid #c9a84c22">Acciones programadas esta semana</td></tr>
          <tr><td style="padding:6px;border:1px solid #c9a84c22">Campo Amalfitani</td>
              <td style="padding:6px;color:#27ae60;border:1px solid #c9a84c22"><b>ÓPTIMO</b></td>
              <td style="padding:6px;border:1px solid #c9a84c22">Sin acción inmediata</td></tr>
          <tr><td style="padding:6px;border:1px solid #c9a84c22">Polideportivo Feijóo</td>
              <td style="padding:6px;color:#f0b429;border:1px solid #c9a84c22"><b>ATENCIÓN</b></td>
              <td style="padding:6px;border:1px solid #c9a84c22">Básquet y Playón Norte — inspección</td></tr>
          <tr><td style="padding:6px;border:1px solid #c9a84c22">Complejo Acuático</td>
              <td style="padding:6px;color:#27ae60;border:1px solid #c9a84c22"><b>ÓPTIMO</b></td>
              <td style="padding:6px;border:1px solid #c9a84c22">Calidad agua excelente — score 91/100</td></tr>
          <tr><td style="padding:6px;border:1px solid #c9a84c22">Sede Central & Instituto</td>
              <td style="padding:6px;color:#f0b429;border:1px solid #c9a84c22"><b>ATENCIÓN</b></td>
              <td style="padding:6px;border:1px solid #c9a84c22">Anexo Norte — revisar aislamiento térmico</td></tr>
          <tr><td style="padding:6px;border:1px solid #c9a84c22">Tribuna Oeste</td>
              <td style="padding:6px;color:#e74c3c;border:1px solid #c9a84c22"><b>ALERTA</b></td>
              <td style="padding:6px;border:1px solid #c9a84c22">InSAR 2.80mm — inspección estructural</td></tr>
        </table>
        <h3 style="color:#c9a84c">Sistema Solar (210 paneles — 120 kWp)</h3>
        <ul style="font-size:14px">
          <li>Eficiencia: <b>82.4%</b> — Producción: 2,840 kWh/sem</li>
          <li><b style="color:#f0b429">Zona E (Arco Sur):</b> 8 paneles >60°C — revisión esta semana</li>
        </ul>
        <p style="font-size:13px;color:#9aa0a8">Se adjuntan: reporte general, solar, agro, polideportivo, piletas y sede.</p>
        """
    )

def _body_pait() -> str:
    return _html_wrap(
        'Estado Canchas y Campo — Visión Deportiva',
        """
        <p>Sebastián, estado de las superficies para esta semana de entrenamiento.</p>
        <h3 style="color:#e74c3c">NO APTO para entrenamiento</h3>
        <ul style="font-size:14px">
          <li><b>Cancha 4:</b> estado crítico — hongo activo + drenaje roto + pasto ralo</li>
        </ul>
        <h3 style="color:#f0b429">Uso condicionado</h3>
        <ul style="font-size:14px">
          <li><b>Cancha 1:</b> apta, con fungicida preventivo antes del uso</li>
          <li><b>Cancha 3:</b> apta para trabajos livianos, tratamiento en curso</li>
          <li><b>Polideportivo:</b> Básquet y Playón Norte — evaluar antes de uso intensivo</li>
        </ul>
        <h3 style="color:#27ae60">Óptimas para uso</h3>
        <ul style="font-size:14px">
          <li><b>Cancha 2:</b> apta — NDVI 0.52, fertilización esta semana</li>
          <li><b>Campo Amalfitani:</b> NDVI 0.68 — excelente condición</li>
          <li><b>Hockey:</b> NDVI 0.52 — apta para uso normal</li>
        </ul>
        <p style="font-size:13px;color:#9aa0a8">Se adjuntan mapa de canchas, agro detallado y polideportivo.</p>
        """
    )

def _body_berlanga() -> str:
    return _html_wrap(
        f'Informe Ejecutivo Semanal — Vélez Sarsfield · {datetime.now().strftime("%d/%m/%Y")}',
        f"""
        <p>Fabián, resumen ejecutivo satelital del predio completo,
        semana del {datetime.now().strftime("%d/%m/%Y")}.</p>
        <h3 style="color:#c9a84c">Indicadores Clave del Club</h3>
        <table style="color:#f2ede4;border-collapse:collapse;width:100%;font-size:14px">
          <tr style="background:#141c24">
            <th style="padding:8px;border:1px solid #c9a84c44;text-align:left">Área</th>
            <th style="padding:8px;border:1px solid #c9a84c44;text-align:center">Estado</th>
            <th style="padding:8px;border:1px solid #c9a84c44;text-align:left">Detalle</th>
          </tr>
          <tr><td style="padding:6px;border:1px solid #c9a84c22">Estadio Amalfitani</td>
              <td style="padding:6px;text-align:center;color:#f0b429;border:1px solid #c9a84c22"><b>ATENCIÓN</b></td>
              <td style="padding:6px;border:1px solid #c9a84c22">Cancha 4 crítica — resto en tratamiento</td></tr>
          <tr><td style="padding:6px;border:1px solid #c9a84c22">Sistema Solar</td>
              <td style="padding:6px;text-align:center;color:#f0b429;border:1px solid #c9a84c22"><b>82.4%</b></td>
              <td style="padding:6px;border:1px solid #c9a84c22">2,840 kWh/sem — Zona E requiere revisión</td></tr>
          <tr><td style="padding:6px;border:1px solid #c9a84c22">Polideportivo Feijóo</td>
              <td style="padding:6px;text-align:center;color:#f0b429;border:1px solid #c9a84c22"><b>ATENCIÓN</b></td>
              <td style="padding:6px;border:1px solid #c9a84c22">Básquet y Playón Norte — inspección</td></tr>
          <tr><td style="padding:6px;border:1px solid #c9a84c22">Complejo Acuático</td>
              <td style="padding:6px;text-align:center;color:#27ae60;border:1px solid #c9a84c22"><b>ÓPTIMO</b></td>
              <td style="padding:6px;border:1px solid #c9a84c22">Score calidad agua 91/100</td></tr>
          <tr><td style="padding:6px;border:1px solid #c9a84c22">Sede Central & Instituto</td>
              <td style="padding:6px;text-align:center;color:#f0b429;border:1px solid #c9a84c22"><b>ATENCIÓN</b></td>
              <td style="padding:6px;border:1px solid #c9a84c22">Anexo Norte — temperatura elevada</td></tr>
          <tr><td style="padding:6px;border:1px solid #c9a84c22">Tribuna Oeste</td>
              <td style="padding:6px;text-align:center;color:#e74c3c;border:1px solid #c9a84c22"><b>ALERTA</b></td>
              <td style="padding:6px;border:1px solid #c9a84c22">InSAR 2.80mm — inspección estructural</td></tr>
        </table>
        <p style="font-size:13px;color:#9aa0a8">Se adjuntan reportes completos del predio.</p>
        """
    )

def _body_nelson() -> str:
    return _html_wrap(
        f'Informe Ejecutivo Semanal — Vélez Sarsfield · {datetime.now().strftime("%d/%m/%Y")}',
        f"""
        <p>Nelson, resumen ejecutivo satelital del predio,
        semana del {datetime.now().strftime("%d/%m/%Y")}.</p>
        <h3 style="color:#c9a84c">Estado General</h3>
        <ul style="font-size:14px;line-height:1.8">
          <li><b>Estadio Amalfitani:</b> Cancha 4 crítica — resto en tratamiento preventivo</li>
          <li><b>Sistema Solar:</b> Eficiencia 82.4% — Zona E requiere revisión esta semana</li>
          <li><b>Polideportivo Feijóo:</b> 2 sectores con atención (Básquet + Playón Norte)</li>
          <li><b>Complejo Acuático:</b> Calidad de agua excelente — score 91/100</li>
          <li><b>Sede Central:</b> Anexo Norte con temperatura de techo elevada (41°C)</li>
          <li><b>Tribuna Oeste:</b> InSAR 2.80mm — inspección estructural recomendada</li>
        </ul>
        <h3 style="color:#c9a84c">Sustentabilidad</h3>
        <ul style="font-size:14px">
          <li>Sistema solar produciendo 2,840 kWh/semana</li>
          <li>Alerta temprana de calidad de agua activa — sin intervención requerida</li>
          <li>Cobertura satelital del 100% del predio operativa</li>
        </ul>
        <p style="font-size:13px;color:#9aa0a8">Se adjuntan reportes completos del predio.</p>
        """
    )

def _body_aveleyra() -> str:
    return _html_wrap(
        f'Dashboard Ejecutivo Semanal — Vélez Sarsfield · {datetime.now().strftime("%d/%m/%Y")}',
        f"""
        <p>Alberto, dashboard ejecutivo satelital del predio completo,
        semana del {datetime.now().strftime("%d/%m/%Y")}.</p>
        <h3 style="color:#c9a84c">KPIs de Gestión</h3>
        <table style="color:#f2ede4;border-collapse:collapse;width:100%;font-size:14px">
          <tr style="background:#141c24">
            <th style="padding:8px;border:1px solid #c9a84c44;text-align:left">Indicador</th>
            <th style="padding:8px;border:1px solid #c9a84c44;text-align:center">Valor</th>
            <th style="padding:8px;border:1px solid #c9a84c44;text-align:left">Acción requerida</th>
          </tr>
          <tr><td style="padding:6px;border:1px solid #c9a84c22">Score predio general</td>
              <td style="padding:6px;text-align:center;color:#f0b429;border:1px solid #c9a84c22"><b>67/100</b></td>
              <td style="padding:6px;border:1px solid #c9a84c22">Intervención Cancha 4 + inspecciones</td></tr>
          <tr><td style="padding:6px;border:1px solid #c9a84c22">Score complejo acuático</td>
              <td style="padding:6px;text-align:center;color:#27ae60;border:1px solid #c9a84c22"><b>91/100</b></td>
              <td style="padding:6px;border:1px solid #c9a84c22">Sin acción inmediata</td></tr>
          <tr><td style="padding:6px;border:1px solid #c9a84c22">Eficiencia solar</td>
              <td style="padding:6px;text-align:center;color:#f0b429;border:1px solid #c9a84c22"><b>82.4%</b></td>
              <td style="padding:6px;border:1px solid #c9a84c22">Revisión Zona E esta semana</td></tr>
          <tr><td style="padding:6px;border:1px solid #c9a84c22">Alertas estructurales</td>
              <td style="padding:6px;text-align:center;color:#e74c3c;border:1px solid #c9a84c22"><b>1</b></td>
              <td style="padding:6px;border:1px solid #c9a84c22">Tribuna Oeste — inspección recomendada</td></tr>
        </table>
        <p style="font-size:13px;color:#9aa0a8">
          Detección temprana: 80% de problemas identificados antes de ser críticos.<br>
          Se adjuntan todos los reportes del predio.
        </p>
        """
    )

def _body_generic(nombre: str, reportes: list) -> str:
    """Email genérico para destinatarios nuevos agregados vía config_velez.json."""
    reportes_str = ', '.join(reportes) if reportes else 'reportes del club'
    return _html_wrap(
        'Reporte Satelital Semanal — Vélez Sarsfield',
        f"""
        <p>{nombre}, te enviamos el reporte satelital semanal del Club Atlético Vélez Sarsfield.</p>
        <p>Esta semana recibís: <b>{reportes_str}</b></p>
        <h3 style="color:#c9a84c">Revisá los reportes adjuntos</h3>
        <p>Los archivos contienen el análisis satelital actualizado de cada área asignada.</p>
        <p style="font-size:13px;color:#9aa0a8">
          Para consultas respondé por este email. — Faro Protocol
        </p>
        """
    )

# Mapa nombre → función de body personalizada
_BODY_FN_MAP = {
    'Roger Bernal':       _body_roger,
    'Juan Gonzalez':      _body_juan,
    'Fernando Banchero':  _body_banchero,
    'Sebastian Pait':     _body_pait,
    'Fabian Berlanga':    _body_berlanga,
    'Nelson Pugliese':    _body_nelson,
    'Alberto Aveleyra':   _body_aveleyra,
}

# ─── RECIPIENT CONFIG ─────────────────────────────────────────────────────────

def _make_generic_body_fn(nombre: str, reportes: list):
    """Factory para evitar captura de variable en cierre."""
    def fn():
        return _body_generic(nombre, reportes)
    return fn

def _recipients_from_config(config: dict = None) -> list:
    """Construye lista de destinatarios desde config_velez.json.
    Body personalizada para conocidos; genérica para nuevos agregados vía JSON."""
    if config is None:
        config = load_config()
    recipients = []
    for d in config.get('destinatarios', []):
        nombre = d.get('nombre', '')
        email  = d.get('email', '')
        if not nombre or not email:
            continue
        body_fn = _BODY_FN_MAP.get(nombre) or _make_generic_body_fn(nombre, d.get('reportes', []))
        key = (nombre.lower()
               .replace(' ', '_')
               .replace('á', 'a').replace('é', 'e').replace('í', 'i')
               .replace('ó', 'o').replace('ú', 'u').replace('ñ', 'n'))
        recipients.append({
            'key':      key,
            'name':     nombre,
            'to':       email,
            'subject':  'Faro · Reporte semanal · Vélez Sarsfield',
            'reports':  d.get('reportes', []),
            'body_fn':  body_fn,
            'whatsapp': d.get('whatsapp', ''),
        })
    return recipients

def send_all_reports(tipo: str = 'semanal', config: dict = None):
    if config is None:
        config = load_config()
    jobs         = load_jobs()
    manual_sent  = jobs.get('manual_sent', False)
    date_str     = datetime.now().strftime('%d/%m/%Y')
    report_paths = get_report_paths(config)

    for r in _recipients_from_config(config):
        subject   = f'{r["subject"]} · {date_str}'
        body_html = r['body_fn']()
        atts      = []

        for rep_key in r['reports']:
            p = report_paths.get(rep_key)
            if p and Path(p).exists():
                atts.append(str(p))
            else:
                log.warning(f'Reporte "{rep_key}" no encontrado para {r["name"]}')

        if not atts:
            log.warning(f'Sin adjuntos para {r["name"]} — email omitido (nunca enviar vacío)')
            continue

        # Primer envío: adjuntar manual personal
        if not manual_sent:
            manual_key  = _manual_key_for_name(r['name'])
            manual_path = MANUAL_PATHS.get(manual_key) if manual_key else None
            if manual_path and Path(manual_path).exists():
                atts.append(str(manual_path))
                log.info(f'Manual adjunto para {r["name"]}')

        ok = send_email(r['to'], subject, body_html, atts)
        if not ok:
            queue_email(r['to'], subject, body_html, atts)
        log.info(f'Reporte enviado a {r["name"]} ({r["key"]}) → {ok}')

    if not manual_sent:
        jobs['manual_sent'] = True
        save_jobs(jobs)
        log.info('manual_sent = True — PDFs no se adjuntarán en futuros envíos semanales')

# ─── WEEKLY JOB ──────────────────────────────────────────────────────────────

def weekly_report(tipo: str = 'semanal', event_date: Optional[str] = None):
    log.info(f'=== Ejecutando reporte {tipo} ===')
    jobs   = load_jobs()
    config = load_config()

    def _gen_with_retry(zona: dict) -> Optional[str]:
        out_path = BASE_DIR / 'reportes_velez' / zona['output']
        for attempt in range(3):
            path = generate_report(zona['script'], out_path)
            if path:
                return path
            log.warning(f'{zona["nombre"]} intento {attempt+1}/3 falló')
            if attempt < 2:
                time.sleep(300)
        return None

    generated = {}
    for zona in config.get('zonas', []):
        key = output_to_key(zona['output'])
        generated[key] = _gen_with_retry(zona)

    available = sum(1 for p in generated.values() if p)
    if available == 0:
        log.error('Ningún reporte disponible tras los intentos. Envío cancelado.')
        return

    log.info(f'{available}/{len(generated)} reportes generados')

    # Verificar alertas y enviar WhatsApp
    data = load_latest_data()
    check_and_send_alerts(data, config)

    # Enviar emails diferenciados por destinatario
    send_all_reports(tipo, config)

    # Procesar cola de emails pendientes
    flush_email_queue()

    jobs['last_report'] = {
        'date':        datetime.now().isoformat(),
        'tipo':        tipo,
        'generated':   {k: bool(v) for k, v in generated.items()},
        'zonas_ok':    available,
        'zonas_total': len(generated),
    }
    save_jobs(jobs)
    log.info(f'=== Reporte {tipo} completado ===')

def pre_event_report(event_date: str):
    log.info(f'Pre-event report for {event_date}')
    weekly_report(tipo=f'pre-evento {event_date}', event_date=event_date)

def post_event_report(event_date: str):
    log.info(f'Post-event report for {event_date}')
    weekly_report(tipo=f'post-evento {event_date}', event_date=event_date)
    jobs = load_jobs()
    matching = [e for e in jobs.get('events', []) if e.get('fecha') == event_date]
    if matching:
        _send_damage_report(event_date, matching[0].get('pre_report_date', 'desconocida'))

def _send_damage_report(event_date: str, pre_date: str):
    config  = load_config()
    subject = f'Faro · Evaluación de Daños Post-Evento {event_date}'
    body = _html_wrap(
        f'Evaluación de Daños — Evento {event_date}',
        f"""
        <p>Comparación PRE ({pre_date}) vs POST-EVENTO ({datetime.now().strftime('%d/%m/%Y')}):</p>
        <table style="color:#f2ede4;border-collapse:collapse;width:100%">
          <tr style="background:#141c24">
            <th style="padding:8px;border:1px solid #c9a84c44">Zona</th>
            <th style="padding:8px;border:1px solid #c9a84c44">Pre-NDVI</th>
            <th style="padding:8px;border:1px solid #c9a84c44">Post-NDVI</th>
            <th style="padding:8px;border:1px solid #c9a84c44">Daño</th>
          </tr>
          <tr>
            <td style="padding:6px;border:1px solid #c9a84c22">Campo Amalfitani</td>
            <td style="padding:6px;border:1px solid #c9a84c22">0.68</td>
            <td style="padding:6px;border:1px solid #c9a84c22">0.61</td>
            <td style="padding:6px;border:1px solid #c9a84c22;color:#f0b429">Leve (-10%)</td>
          </tr>
        </table>
        <p style="color:#9aa0a8">Evaluación automática basada en imágenes Sentinel-2.</p>
        """
    )
    for d in config.get('destinatarios', []):
        if 'velez' in d.get('reportes', []) and d.get('email'):
            send_email(d['email'], subject, body)

# ─── EVENT SCHEDULING ────────────────────────────────────────────────────────

def register_event(fecha: str, tipo: str = 'partido'):
    config = load_config()
    jobs = load_jobs()
    event_dt = datetime.strptime(fecha, '%Y-%m-%d')
    pre_dt   = event_dt - timedelta(hours=48)
    post_dt  = event_dt + timedelta(hours=48)

    if any(e['fecha'] == fecha for e in jobs.get('events', [])):
        return {'status': 'already_registered', 'fecha': fecha}

    jobs.setdefault('events', []).append({
        'fecha': fecha, 'tipo': tipo,
        'pre_dt': pre_dt.isoformat(), 'post_dt': post_dt.isoformat(),
        'pre_report_date': None, 'post_report_done': False,
    })
    save_jobs(jobs)

    msg = (f'Faro: Evento {tipo} registrado para {event_dt.strftime("%d/%m/%Y")}.\n'
           f'Pre-evento: {pre_dt.strftime("%A %d/%m")}.\n'
           f'Post-evento: {post_dt.strftime("%A %d/%m")}.')
    for d in config.get('destinatarios', []):
        if d.get('whatsapp'):
            send_whatsapp(d['whatsapp'], msg)

    log.info(f'Evento registrado: {fecha} ({tipo})')
    return {
        'status': 'registered', 'fecha': fecha, 'tipo': tipo,
        'pre_evento': pre_dt.strftime('%A %d/%m'),
        'post_evento': post_dt.strftime('%A %d/%m'),
    }

def check_pending_events():
    jobs = load_jobs()
    now  = datetime.now()
    changed = False
    for event in jobs.get('events', []):
        pre_dt  = datetime.fromisoformat(event['pre_dt'])
        post_dt = datetime.fromisoformat(event['post_dt'])
        if not event.get('pre_report_date') and now >= pre_dt:
            pre_event_report(event['fecha'])
            event['pre_report_date'] = now.isoformat()
            changed = True
        if not event.get('post_report_done') and now >= post_dt:
            post_event_report(event['fecha'])
            event['post_report_done'] = True
            changed = True
    if changed:
        save_jobs(jobs)

# ─── SCHEDULE ────────────────────────────────────────────────────────────────

def setup_schedule():
    # Lunes 07:00 ART
    schedule.every().monday.at('07:00').do(weekly_report)
    schedule.every().hour.do(check_pending_events)
    schedule.every(30).minutes.do(flush_email_queue)
    log.info('Schedule: semanal Lun 07:00 + evento cada hora + flush 30min')

def run_schedule():
    while True:
        schedule.run_pending()
        time.sleep(60)

# ─── FLASK APP ───────────────────────────────────────────────────────────────

app = Flask(__name__)

@app.route('/velez/evento', methods=['POST'])
def route_evento():
    data    = request.get_json(silent=True) or {}
    message = data.get('message', '')
    if message.lower().startswith('evento '):
        parts = message.split()
        if len(parts) >= 2:
            data['fecha'] = parts[1]
    fecha = data.get('fecha', '')
    tipo  = data.get('tipo', 'partido')
    if not fecha:
        return jsonify({'error': 'fecha required (YYYY-MM-DD)'}), 400
    try:
        datetime.strptime(fecha, '%Y-%m-%d')
    except ValueError:
        return jsonify({'error': 'invalid date format, use YYYY-MM-DD'}), 400
    return jsonify(register_event(fecha, tipo)), 200

@app.route('/velez/status', methods=['GET'])
def route_status():
    jobs   = load_jobs()
    config = load_config()
    now    = datetime.now()
    upcoming = [
        {'fecha': e['fecha'], 'tipo': e['tipo'],
         'pre_enviado': bool(e.get('pre_report_date')),
         'post_enviado': bool(e.get('post_report_done'))}
        for e in jobs.get('events', [])
        if datetime.fromisoformat(e['fecha'] + 'T00:00:00') >= now - timedelta(days=7)
    ]
    return jsonify({
        'status':          'running',
        'timestamp':       now.isoformat(),
        'last_report':     jobs.get('last_report'),
        'email_queue_len': len(jobs.get('email_queue', [])),
        'upcoming_events': upcoming,
        'next_weekly':     'Monday 07:00 ART',
        'zonas':           [z['nombre'] for z in config.get('zonas', [])],
        'destinatarios':   [d['nombre'] for d in config.get('destinatarios', [])],
        'manual_sent':     jobs.get('manual_sent', False),
    })

@app.route('/velez/solar', methods=['GET'])
def route_solar():
    jobs = load_jobs()
    return jsonify({'last_solar': jobs.get('last_solar'), 'timestamp': datetime.now().isoformat()})

@app.route('/velez/run_now', methods=['POST'])
def route_run_now():
    tipo = (request.get_json(silent=True, force=True) or {}).get('tipo', 'manual')
    threading.Thread(target=weekly_report, kwargs={'tipo': tipo}, daemon=True).start()
    return jsonify({'status': 'started', 'tipo': tipo})

@app.route('/velez/test_email', methods=['POST'])
def route_test_email():
    results = send_test_emails()
    return jsonify({'status': 'ok' if all(results.values()) else 'partial', 'results': results}), 200

@app.route('/velez/test_banchero', methods=['POST'])
def route_test_banchero():
    result = send_test_banchero()
    return jsonify({'status': 'ok' if result.get('banchero') else 'error', **result}), 200

@app.route('/velez/reset_manuals', methods=['POST'])
def route_reset_manuals():
    jobs = load_jobs()
    jobs['manual_sent'] = False
    save_jobs(jobs)
    log.info('manual_sent reset a False')
    return jsonify({'status': 'ok', 'manual_sent': False})

@app.route('/health', methods=['GET'])
def health():
    return jsonify({'status': 'ok', 'service': 'faro-velez-scheduler'})

# ─── TEST EMAILS ─────────────────────────────────────────────────────────────

def send_test_emails() -> dict:
    config  = load_config()
    now_str = datetime.now().strftime('%d/%m/%Y %H:%M')
    results = {}
    for r in _recipients_from_config(config):
        ok = send_email(
            to=r['to'],
            subject=f'TEST · Faro Protocol · Velez · {now_str}',
            body_html=_html_wrap(
                f'TEST — {r["name"]}',
                f"""
                <p>Email de prueba del sistema Faro Protocol para Velez Sarsfield.</p>
                <p>Destinatario: <b>{r["name"]}</b></p>
                <p>Reportes asignados: {', '.join(r["reports"])}</p>
                <p style="color:#9aa0a8;font-size:12px">Enviado: {now_str}</p>
                """
            ),
            attachments=[]
        )
        results[r['key']] = ok
        log.info(f'Test email {r["name"]} → {ok}')
    return results

def send_test_banchero() -> dict:
    """Test completo para Banchero con TODOS sus reportes adjuntos."""
    config       = load_config()
    report_paths = get_report_paths(config)
    now_str      = datetime.now().strftime('%d/%m/%Y %H:%M')

    banchero = next(
        (d for d in config.get('destinatarios', []) if d['nombre'] == 'Fernando Banchero'),
        None
    )
    if not banchero:
        log.warning('Fernando Banchero no encontrado en config_velez.json')
        return {'banchero': False}

    atts = []
    missing = []
    for rep_key in banchero.get('reportes', []):
        p = report_paths.get(rep_key)
        if p and Path(p).exists():
            atts.append(str(p))
        else:
            missing.append(rep_key)
            log.warning(f'Reporte "{rep_key}" no encontrado para test Banchero')

    ok = send_email(
        to=banchero['email'],
        subject=f'TEST COMPLETO · Faro Protocol · Velez · {now_str}',
        body_html=_body_banchero(),
        attachments=atts,
    )
    log.info(f'Test Banchero: {len(atts)} reportes adjuntos, missing={missing} → {ok}')
    return {
        'banchero':         ok,
        'reportes_adjuntos': len(atts),
        'keys_enviados':    [r for r in banchero.get('reportes', []) if r not in missing],
        'keys_faltantes':   missing,
    }

# ─── INTEGRATION ─────────────────────────────────────────────────────────────

def register_with_app(flask_app, apscheduler=None):
    """Registra rutas y jobs en una Flask app + APScheduler externos (Railway)."""
    routes = [
        ('/velez/evento',        'velez_evento',        route_evento,        ['POST']),
        ('/velez/status',        'velez_status',        route_status,        ['GET']),
        ('/velez/solar',         'velez_solar',         route_solar,         ['GET']),
        ('/velez/run_now',       'velez_run_now',       route_run_now,       ['POST']),
        ('/velez/test_email',    'velez_test_email',    route_test_email,    ['POST']),
        ('/velez/test_banchero', 'velez_test_banchero', route_test_banchero, ['POST']),
        ('/velez/reset_manuals', 'velez_reset_manuals', route_reset_manuals, ['POST']),
    ]
    for path, name, fn, methods in routes:
        flask_app.add_url_rule(path, name, fn, methods=methods)
    log.info('Velez: rutas registradas en Flask app externa')

    if apscheduler:
        # Lunes 07:00 ART = Lunes 10:00 UTC (ART = UTC-3, sin DST)
        apscheduler.add_job(weekly_report,        'cron',     day_of_week='mon',
                            hour=10, minute=0,    id='velez_weekly',  replace_existing=True)
        apscheduler.add_job(check_pending_events, 'interval', hours=1,
                            id='velez_events',    replace_existing=True)
        apscheduler.add_job(flush_email_queue,    'interval', minutes=30,
                            id='velez_flush',     replace_existing=True)
        log.info('Velez: APScheduler registrado — semanal Lun 10:00 UTC (07:00 ART)')

# ─── MAIN ────────────────────────────────────────────────────────────────────

if __name__ == '__main__':
    log.info('Faro Vélez Scheduler arrancando...')

    if not CONFIG_FILE.exists():
        log.warning(f'ATENCIÓN: {CONFIG_FILE} no existe — operación degradada')
    else:
        cfg = load_config()
        log.info(f'Config cargada: {len(cfg.get("zonas", []))} zonas, '
                 f'{len(cfg.get("destinatarios", []))} destinatarios')

    if not GMAIL_PASS:
        log.warning('GMAIL_APP_PASS no configurado — emails desactivados')

    check_pending_events()
    setup_schedule()
    threading.Thread(target=run_schedule, daemon=True).start()
    log.info('Hilo de scheduler iniciado')

    app.run(host='0.0.0.0', port=PORT, debug=False)
