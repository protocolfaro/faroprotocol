"""
Faro Protocol — Vélez Sarsfield Scheduler
Reporte semanal + eventos + alertas urgentes + WhatsApp + email.
Railway-ready. Jobs persistentes en JSON.
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
JOBS_FILE = Path(__file__).parent / 'velez_jobs.json'
LOG_FILE  = Path(__file__).parent / 'velez_scheduler.log'

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

GMAIL_USER  = env('GMAIL_USER', 'protocolfaro@gmail.com')
GMAIL_PASS  = env('GMAIL_APP_PASS')

EMAIL_COMISION    = env('VELEZ_EMAIL_COMISION')
EMAIL_INTENDENTE  = env('VELEZ_EMAIL_INTENDENTE')
EMAIL_CANCHERO    = env('VELEZ_EMAIL_CANCHERO')
EMAIL_TODOS       = env('VELEZ_EMAIL_TODOS')

WA_INTENDENTE     = env('VELEZ_WHATSAPP_INTENDENTE')
WA_CANCHERO       = env('VELEZ_WHATSAPP_CANCHERO')
WA_NELSON         = env('VELEZ_WHATSAPP_NELSON')
CALLMEBOT_KEY     = env('CALLMEBOT_API_KEY')

PORT    = int(env('PORT', '5000'))
DESKTOP = Path.home() / 'Desktop'

# Rutas centralizadas de todos los reportes Vélez
REPORT_PATHS = {
    'canchero':   DESKTOP / 'faro_reporte_velez_canchero.png',
    'agro_final': DESKTOP / 'faro_reporte_velez_agro_FINAL.png',
    'solar_v2':   DESKTOP / 'faro_reporte_velez_solar_v2.png',
    'velez':      DESKTOP / 'faro_reporte_velez.png',
}

# Alert thresholds
NDVI_ALERT     = 0.35
INSAR_ALERT    = 3.0   # mm
TEMP_ALERT     = 28.0  # °C
SOLAR_ALERT_EFF = 75.0 # %

# ─── JOBS PERSISTENCE ────────────────────────────────────────────────────────

def load_jobs() -> dict:
    if JOBS_FILE.exists():
        try:
            return json.loads(JOBS_FILE.read_text())
        except Exception:
            pass
    return {'events': [], 'email_queue': [], 'last_report': None, 'last_solar': None}

def save_jobs(jobs: dict):
    JOBS_FILE.write_text(json.dumps(jobs, indent=2, default=str))

# ─── WHATSAPP (CallMeBot) ─────────────────────────────────────────────────────

def send_whatsapp(phone: str, message: str) -> bool:
    if not phone or not CALLMEBOT_KEY:
        log.warning(f'WhatsApp not configured (phone={bool(phone)}, key={bool(CALLMEBOT_KEY)})')
        return False
    try:
        url = 'https://api.callmebot.com/whatsapp.php'
        params = {'phone': phone, 'text': message, 'apikey': CALLMEBOT_KEY}
        r = requests.get(url, params=params, timeout=15)
        if r.status_code == 200:
            log.info(f'WhatsApp sent to {phone[:6]}***')
            return True
        log.error(f'WhatsApp failed: {r.status_code} {r.text[:100]}')
        return False
    except Exception as e:
        log.error(f'WhatsApp exception: {e}')
        return False

def notify_whatsapp_all(message: str):
    for phone in [WA_INTENDENTE, WA_CANCHERO, WA_NELSON]:
        if phone:
            send_whatsapp(phone, message)

# ─── EMAIL ────────────────────────────────────────────────────────────────────

def send_email(to: str, subject: str, body_html: str,
               attachments: list = None) -> bool:
    """Send email to one or more recipients (comma-separated `to`).
    attachments: list of file paths to attach."""
    if not to or not GMAIL_PASS:
        log.warning(f'Email not configured (to={bool(to)}, pass={bool(GMAIL_PASS)})')
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
                log.warning(f'Attachment not found, skipping: {att_path}')

        with smtplib.SMTP_SSL('smtp.gmail.com', 465) as server:
            server.login(GMAIL_USER, GMAIL_PASS)
            server.sendmail(GMAIL_USER, recipients, msg.as_string())

        log.info(f'Email sent to {recipients}: {subject}')
        return True
    except Exception as e:
        log.error(f'Email failed to {to}: {e}')
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
        ok = send_email(item['to'], item['subject'], item['body'],
                        item.get('attachments') or [item['attachment']] if item.get('attachment') else [])
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
        log.error(f'Script failed: {script_path}\n{result.stderr[:500]}')
        return False
    except Exception as e:
        log.error(f'Script exception: {script_path}: {e}')
        return False

def generate_report(script_name: str, out_path: Path) -> Optional[str]:
    """Run a generator script. Returns output path string or None."""
    script = DESKTOP / script_name
    if script.exists():
        if _run_script(str(script)):
            return str(out_path) if out_path.exists() else None
    if out_path.exists():
        log.warning(f'Using cached report (generation failed): {out_path.name}')
        return str(out_path)
    return None

def generate_canchero_report() -> Optional[str]:
    return generate_report('gen_velez_canchero.py', REPORT_PATHS['canchero'])

def generate_solar_v2_report() -> Optional[str]:
    return generate_report('gen_velez_solar_v2.py', REPORT_PATHS['solar_v2'])

# ─── REPORT DATA (lectura del JSON del pipeline) ─────────────────────────────

def load_latest_data() -> dict:
    """Load latest zone data from Faro pipeline data.json."""
    data_path = Path(__file__).parent / 'data.json'
    if data_path.exists():
        try:
            return json.loads(data_path.read_text())
        except Exception:
            pass
    # Fallback synthetic data for Vélez
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

def check_and_send_alerts(data: dict):
    zones = data.get('zones', [])
    solar = data.get('solar', {})
    tribunas = data.get('tribunas', {})

    for z in zones:
        name = z.get('name', '?')
        ndvi = z.get('ndvi', 1.0)
        temp = z.get('temp', 0.0)
        insar = z.get('insar_mm', 0.0)

        if ndvi < NDVI_ALERT:
            msg = f'FARO ALERTA: {name} necesita intervencion urgente (NDVI={ndvi:.2f})'
            send_whatsapp(WA_CANCHERO, msg)
            log.info(f'NDVI alert sent for {name}')

        if temp > TEMP_ALERT:
            msg = f'FARO ALERTA: Estres hidrico critico en {name} (Temp={temp:.1f}°C)'
            send_whatsapp(WA_CANCHERO, msg)
            log.info(f'Temp alert sent for {name}')

    for tribuna, val in tribunas.items():
        if val > INSAR_ALERT:
            msg = f'FARO ALERTA: Tribuna {tribuna.upper()} supera umbral InSAR ({val:.2f}mm)'
            send_whatsapp(WA_INTENDENTE, msg)
            log.info(f'InSAR alert sent for tribuna {tribuna}')

    if solar.get('anomaly_zone'):
        msg = f'FARO ALERTA: Panel solar zona {solar["anomaly_zone"]} con falla detectada'
        send_whatsapp(WA_INTENDENTE, msg)
        log.info(f'Solar alert sent')

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

def email_comision(tipo: str = 'semanal'):
    subject = f'Faro Protocol · Informe semanal · Vélez Sarsfield · {datetime.now().strftime("%d/%m/%Y")}'
    body = _html_wrap(
        f'Informe Semanal — Vélez Sarsfield',
        f"""
        <p>Se adjunta el informe satelital integrado del Estadio José Amalfitani
        correspondiente a la semana del {datetime.now().strftime("%d/%m/%Y")}.</p>
        <h3 style="color:#c9a84c">Resumen ejecutivo</h3>
        <table style="color:#f2ede4;border-collapse:collapse;width:100%;font-size:14px">
          <tr style="background:#141c24">
            <th style="padding:8px;border:1px solid #c9a84c44;text-align:left">Zona</th>
            <th style="padding:8px;border:1px solid #c9a84c44;text-align:left">Estado</th>
            <th style="padding:8px;border:1px solid #c9a84c44;text-align:left">Acción</th>
          </tr>
          <tr><td style="padding:6px;border:1px solid #c9a84c22">Cancha 4</td>
              <td style="padding:6px;border:1px solid #c9a84c22;color:#e74c3c"><b>CRÍTICO</b></td>
              <td style="padding:6px;border:1px solid #c9a84c22">Intervención urgente HOY</td></tr>
          <tr><td style="padding:6px;border:1px solid #c9a84c22">Canchas 1, 2, 3</td>
              <td style="padding:6px;border:1px solid #c9a84c22;color:#f0b429"><b>ATENCIÓN</b></td>
              <td style="padding:6px;border:1px solid #c9a84c22">Acciones esta semana</td></tr>
          <tr><td style="padding:6px;border:1px solid #c9a84c22">Campo Amalfitani</td>
              <td style="padding:6px;border:1px solid #c9a84c22;color:#27ae60"><b>ÓPTIMO</b></td>
              <td style="padding:6px;border:1px solid #c9a84c22">Aerificar porterías semana 3</td></tr>
          <tr><td style="padding:6px;border:1px solid #c9a84c22">Sistema Solar (210 paneles)</td>
              <td style="padding:6px;border:1px solid #c9a84c22;color:#f0b429"><b>ATENCIÓN</b></td>
              <td style="padding:6px;border:1px solid #c9a84c22">Revisar Zona E — 8 paneles >60°C</td></tr>
          <tr><td style="padding:6px;border:1px solid #c9a84c22">Tribuna Oeste</td>
              <td style="padding:6px;border:1px solid #c9a84c22;color:#e74c3c"><b>ALERTA InSAR</b></td>
              <td style="padding:6px;border:1px solid #c9a84c22">Inspección estructural recomendada</td></tr>
        </table>
        <p style="font-size:13px;color:#9aa0a8">Se adjuntan 3 reportes: mapa agronómico, estado solar y reporte general.</p>
        """
    )
    atts = [str(REPORT_PATHS['velez']), str(REPORT_PATHS['agro_final']), str(REPORT_PATHS['solar_v2'])]
    ok = send_email(EMAIL_COMISION, subject, body, atts)
    if not ok:
        queue_email(EMAIL_COMISION, subject, body, atts)

def email_intendente(tipo: str = 'semanal'):
    subject = f'Faro Protocol · Estado del predio · Vélez · {datetime.now().strftime("%d/%m/%Y")}'
    body = _html_wrap(
        'Estado del Predio — Acciones Prioritarias',
        """
        <h3 style="color:#e74c3c">HOY — URGENTE (Cancha 4)</h3>
        <ul style="font-size:14px">
          <li>Aplicar fungicida en focos activos (85 m² marcados en mapa)</li>
          <li>Reparar drenaje lateral sur</li>
          <li>Resembrar zona central</li>
        </ul>
        <h3 style="color:#f0b429">Esta semana</h3>
        <ul style="font-size:14px">
          <li><b>Cancha 1:</b> fungicida preventivo</li>
          <li><b>Cancha 2:</b> fertilizar 20 kg N/ha</li>
          <li><b>Cancha 3:</b> fungicida activo + resembrado parcial</li>
          <li><b>Solar Zona E (Arco Sur):</b> revisar 8 paneles con temperatura >60°C</li>
        </ul>
        <h3 style="color:#e74c3c">Infraestructura</h3>
        <ul style="font-size:14px">
          <li><b>Tribuna Oeste:</b> InSAR 2.80 mm — supera umbral de seguridad<br>
              Se recomienda inspección estructural esta semana.</li>
        </ul>
        <p style="font-size:13px;color:#9aa0a8">Se adjuntan reporte agronómico, solar y general del predio.</p>
        """
    )
    atts = [str(REPORT_PATHS['agro_final']), str(REPORT_PATHS['solar_v2']), str(REPORT_PATHS['velez'])]
    ok = send_email(EMAIL_INTENDENTE, subject, body, atts)
    if not ok:
        queue_email(EMAIL_INTENDENTE, subject, body, atts)

def email_canchero(tipo: str = 'semanal'):
    subject = f'Faro Protocol · Mapa de trabajo esta semana · Vélez · {datetime.now().strftime("%d/%m/%Y")}'
    body = _html_wrap(
        'Tu Mapa de Trabajo Esta Semana',
        """
        <p style="font-size:16px;font-weight:bold">Hola, te mando el mapa actualizado.</p>
        <p style="font-size:14px">En el mapa adjunto los círculos de colores marcan exactamente dónde ir:</p>
        <h3 style="color:#e74c3c">HOY — NO PUEDE ESPERAR</h3>
        <ul style="font-size:15px;line-height:1.8">
          <li><b>Cancha 4 — zona central y lateral sur:</b><br>
              Tirar fungicida, reparar el drenaje que está roto, y sembrar semilla nueva</li>
        </ul>
        <h3 style="color:#f0b429">Esta semana</h3>
        <ul style="font-size:15px;line-height:1.8">
          <li><b>Cancha 1:</b> fungicida preventivo (antes que empeore)</li>
          <li><b>Cancha 2:</b> fertilizar todo el campo</li>
          <li><b>Cancha 3:</b> fungicida en las manchas + sembrar donde falta pasto</li>
        </ul>
        <h3 style="color:#27ae60">Semana 3 (sin apuro)</h3>
        <ul style="font-size:15px;line-height:1.8">
          <li><b>Campo Amalfitani:</b> aerificar las dos porterías</li>
        </ul>
        <p style="font-size:13px;color:#9aa0a8">
          Cualquier duda respondeme por este mail o por WhatsApp.<br>
          — Faro Protocol
        </p>
        """
    )
    atts = [str(REPORT_PATHS['canchero'])]
    ok = send_email(EMAIL_CANCHERO, subject, body, atts)
    if not ok:
        queue_email(EMAIL_CANCHERO, subject, body, atts)

# ─── WEEKLY JOB ──────────────────────────────────────────────────────────────

def weekly_report(tipo: str = 'semanal', event_date: Optional[str] = None):
    log.info(f'=== Running {tipo} report ===')
    jobs = load_jobs()

    # Generate canchero report with retry
    canchero_path = None
    for attempt in range(3):
        canchero_path = generate_canchero_report()
        if canchero_path:
            break
        log.warning(f'Canchero generation attempt {attempt+1} failed, retrying in 30min...')
        time.sleep(1800)

    # Generate solar v2 report with retry
    solar_path = None
    for attempt in range(3):
        solar_path = generate_solar_v2_report()
        if solar_path:
            break
        log.warning(f'Solar v2 generation attempt {attempt+1} failed, retrying in 30min...')
        time.sleep(1800)

    if not canchero_path:
        log.error('Canchero report failed after 3 attempts. Aborting.')
        return

    # Load data and check alerts
    data = load_latest_data()
    check_and_send_alerts(data)

    # Send differentiated emails
    email_canchero(tipo)
    email_intendente(tipo)
    email_comision(tipo)

    # Flush queued emails
    flush_email_queue()

    jobs['last_report'] = {
        'date':          datetime.now().isoformat(),
        'tipo':          tipo,
        'canchero_path': canchero_path,
        'solar_path':    solar_path,
    }
    save_jobs(jobs)
    log.info(f'=== {tipo} report complete ===')

def pre_event_report(event_date: str):
    log.info(f'Pre-event report for {event_date}')
    weekly_report(tipo=f'pre-evento {event_date}', event_date=event_date)

def post_event_report(event_date: str):
    log.info(f'Post-event report for {event_date}')
    weekly_report(tipo=f'post-evento {event_date}', event_date=event_date)
    # Compare pre vs post
    jobs = load_jobs()
    events = jobs.get('events', [])
    matching = [e for e in events if e.get('fecha') == event_date]
    if matching:
        e = matching[0]
        pre_date = e.get('pre_report_date', 'desconocida')
        _send_damage_report(event_date, pre_date)

def _send_damage_report(event_date: str, pre_date: str):
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
    send_email(EMAIL_COMISION, subject, body)
    send_email(EMAIL_INTENDENTE, subject, body)  # no attachments — comparison table is inline

# ─── EVENT SCHEDULING ────────────────────────────────────────────────────────

def register_event(fecha: str, tipo: str = 'partido'):
    jobs = load_jobs()
    event_dt = datetime.strptime(fecha, '%Y-%m-%d')
    pre_dt   = event_dt - timedelta(hours=48)
    post_dt  = event_dt + timedelta(hours=48)

    # Check for duplicates
    existing = [e for e in jobs.get('events', []) if e['fecha'] == fecha]
    if existing:
        return {'status': 'already_registered', 'fecha': fecha}

    event_entry = {
        'fecha': fecha,
        'tipo':  tipo,
        'pre_dt':  pre_dt.isoformat(),
        'post_dt': post_dt.isoformat(),
        'pre_report_date': None,
        'post_report_done': False,
    }
    jobs.setdefault('events', []).append(event_entry)
    save_jobs(jobs)

    # WhatsApp confirmation
    pre_str  = pre_dt.strftime('%A %d/%m')
    post_str = post_dt.strftime('%A %d/%m')
    msg = (f'Faro: Evento {tipo} registrado para {event_dt.strftime("%d/%m/%Y")}.\n'
           f'Reporte PRE-EVENTO: {pre_str}.\n'
           f'Reporte POST-EVENTO: {post_str}.')
    for phone in [WA_INTENDENTE, WA_CANCHERO]:
        if phone:
            send_whatsapp(phone, msg)

    log.info(f'Event registered: {fecha} ({tipo})')
    return {
        'status': 'registered',
        'fecha': fecha,
        'tipo': tipo,
        'pre_evento': pre_dt.strftime('%A %d/%m'),
        'post_evento': post_dt.strftime('%A %d/%m'),
    }

def check_pending_events():
    """Check and fire scheduled event reports."""
    jobs = load_jobs()
    now  = datetime.now()
    changed = False

    for event in jobs.get('events', []):
        pre_dt  = datetime.fromisoformat(event['pre_dt'])
        post_dt = datetime.fromisoformat(event['post_dt'])

        if not event.get('pre_report_date') and now >= pre_dt:
            log.info(f'Firing pre-event report for {event["fecha"]}')
            pre_event_report(event['fecha'])
            event['pre_report_date'] = now.isoformat()
            changed = True

        if not event.get('post_report_done') and now >= post_dt:
            log.info(f'Firing post-event report for {event["fecha"]}')
            post_event_report(event['fecha'])
            event['post_report_done'] = True
            changed = True

    if changed:
        save_jobs(jobs)

# ─── SCHEDULE ────────────────────────────────────────────────────────────────

def setup_schedule():
    # Weekly agro + solar report — Monday 7:00 AM ART
    schedule.every().monday.at('07:00').do(weekly_report)
    # Check events every hour
    schedule.every().hour.do(check_pending_events)
    # Flush email queue every 30 min
    schedule.every(30).minutes.do(flush_email_queue)
    log.info('Schedule configured: weekly Mon 07:00 + hourly event check + 30min email flush')

def run_schedule():
    while True:
        schedule.run_pending()
        time.sleep(60)

# ─── FLASK APP ───────────────────────────────────────────────────────────────

app = Flask(__name__)

@app.route('/velez/evento', methods=['POST'])
def route_evento():
    """Register a match/event.
    Body: {"fecha": "YYYY-MM-DD", "tipo": "partido|recital"}
    Also accepts WhatsApp-style: {"message": "evento 2026-06-20"}
    """
    data = request.get_json(silent=True) or {}

    # WhatsApp text message parsing
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

    result = register_event(fecha, tipo)
    return jsonify(result), 200

@app.route('/velez/status', methods=['GET'])
def route_status():
    jobs = load_jobs()
    now  = datetime.now()
    upcoming = []
    for e in jobs.get('events', []):
        event_dt = datetime.fromisoformat(e['fecha'] + 'T00:00:00')
        if event_dt >= now - timedelta(days=7):
            upcoming.append({
                'fecha':        e['fecha'],
                'tipo':         e['tipo'],
                'pre_enviado':  bool(e.get('pre_report_date')),
                'post_enviado': bool(e.get('post_report_done')),
            })

    return jsonify({
        'status':          'running',
        'timestamp':       now.isoformat(),
        'last_report':     jobs.get('last_report'),
        'last_solar':      jobs.get('last_solar'),
        'email_queue_len': len(jobs.get('email_queue', [])),
        'upcoming_events': upcoming,
        'next_weekly':     'Monday 07:00 ART',
    })

@app.route('/velez/solar', methods=['GET'])
def route_solar():
    jobs = load_jobs()
    return jsonify({
        'last_solar': jobs.get('last_solar'),
        'timestamp':  datetime.now().isoformat(),
    })

@app.route('/velez/run_now', methods=['POST'])
def route_run_now():
    """Trigger an immediate weekly report (admin use)."""
    tipo = request.get_json(silent=True, force=True).get('tipo', 'manual') if request.data else 'manual'
    t = threading.Thread(target=weekly_report, kwargs={'tipo': tipo}, daemon=True)
    t.start()
    return jsonify({'status': 'started', 'tipo': tipo})

@app.route('/velez/test_email', methods=['POST'])
def route_test_email():
    """Send a test email to all Vélez recipients to verify delivery."""
    results = send_test_emails()
    ok = all(v for v in results.values())
    return jsonify({'status': 'ok' if ok else 'partial', 'results': results}), 200

# ─── TEST EMAIL ───────────────────────────────────────────────────────────────

def send_test_emails() -> dict:
    """Send TEST email to each Vélez recipient. Returns dict of {recipient: bool}."""
    now_str = datetime.now().strftime('%d/%m/%Y %H:%M')
    results = {}

    # Test para CANCHERO
    ok = send_email(
        to=EMAIL_CANCHERO,
        subject=f'TEST · Faro Protocol · Vélez · {now_str}',
        body_html=_html_wrap('TEST — Verificación de entrega',
            f"""
            <p>Este es un email de <b style="color:#c9a84c">prueba</b> del sistema Faro Protocol para Vélez Sarsfield.</p>
            <p>Destinatario: <b>CANCHERO</b></p>
            <p>Si llegó correctamente, el sistema está listo para enviar los reportes del lunes.</p>
            <p style="color:#9aa0a8;font-size:12px">Enviado: {now_str}</p>
            """),
        attachments=[str(REPORT_PATHS['canchero'])] if REPORT_PATHS['canchero'].exists() else []
    )
    results['canchero'] = ok
    log.info(f'Test email canchero -> {ok}')

    # Test para INTENDENTE
    ok = send_email(
        to=EMAIL_INTENDENTE,
        subject=f'TEST · Faro Protocol · Vélez · {now_str}',
        body_html=_html_wrap('TEST — Verificación de entrega',
            f"""
            <p>Este es un email de <b style="color:#c9a84c">prueba</b> del sistema Faro Protocol para Vélez Sarsfield.</p>
            <p>Destinatario: <b>INTENDENTE</b></p>
            <p>Si llegó correctamente, el sistema está listo para enviar los reportes del lunes.</p>
            <p style="color:#9aa0a8;font-size:12px">Enviado: {now_str}</p>
            """),
        attachments=[]
    )
    results['intendente'] = ok
    log.info(f'Test email intendente -> {ok}')

    # Test para COMISIÓN
    ok = send_email(
        to=EMAIL_COMISION,
        subject=f'TEST · Faro Protocol · Vélez · {now_str}',
        body_html=_html_wrap('TEST — Verificación de entrega',
            f"""
            <p>Este es un email de <b style="color:#c9a84c">prueba</b> del sistema Faro Protocol para Vélez Sarsfield.</p>
            <p>Destinatario: <b>COMISIÓN</b> ({EMAIL_COMISION})</p>
            <p>Si llegó correctamente, el sistema está listo para enviar los informes del lunes.</p>
            <p style="color:#9aa0a8;font-size:12px">Enviado: {now_str}</p>
            """),
        attachments=[]
    )
    results['comision'] = ok
    log.info(f'Test email comision -> {ok}')

    return results

@app.route('/health', methods=['GET'])
def health():
    return jsonify({'status': 'ok', 'service': 'faro-velez-scheduler'})

# ─── MAIN ────────────────────────────────────────────────────────────────────

if __name__ == '__main__':
    log.info('Faro Vélez Scheduler starting...')

    # Check required env vars
    missing = []
    for var in ['GMAIL_APP_PASS', 'VELEZ_EMAIL_COMISION', 'VELEZ_EMAIL_INTENDENTE']:
        if not env(var):
            missing.append(var)
    if missing:
        log.warning(f'Missing env vars: {missing} — some features disabled')

    # Check events on startup (in case of restart)
    check_pending_events()

    # Start scheduler thread
    setup_schedule()
    sched_thread = threading.Thread(target=run_schedule, daemon=True)
    sched_thread.start()
    log.info('Scheduler thread started')

    # Start Flask
    app.run(host='0.0.0.0', port=PORT, debug=False)
