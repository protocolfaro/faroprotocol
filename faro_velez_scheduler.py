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

EMAIL_CANCHERO    = env('VELEZ_EMAIL_CANCHERO')    # Roger Bernal — Responsable campo
EMAIL_INTENDENTE  = env('VELEZ_EMAIL_INTENDENTE')  # Juan González — Intendente Villa Olímpica
EMAIL_BANCHERO    = env('VELEZ_EMAIL_BANCHERO')    # Fernando Banchero — Gerente Operaciones
EMAIL_PAIT        = env('VELEZ_EMAIL_PAIT')         # Sebastián Pait — Director Deportivo
EMAIL_COMISION    = env('VELEZ_EMAIL_COMISION')    # Berlanga, Pugliese, Aveleyra — Comisión
EMAIL_TODOS       = env('VELEZ_EMAIL_TODOS')

WA_INTENDENTE     = env('VELEZ_WHATSAPP_INTENDENTE')
WA_CANCHERO       = env('VELEZ_WHATSAPP_CANCHERO')
WA_NELSON         = env('VELEZ_WHATSAPP_NELSON')

# CallMeBot: cada número tiene su propia API key (activación individual)
# Claves vacías = ese número no recibirá alertas pero los emails siguen funcionando
def _wa_keys() -> dict:
    return {
        WA_CANCHERO:   env('CALLMEBOT_KEY_CANCHERO'),
        WA_INTENDENTE: env('CALLMEBOT_KEY_INTENDENTE'),
        WA_NELSON:     env('CALLMEBOT_KEY_NELSON'),
    }

PORT     = int(env('PORT', '5000'))
DESKTOP  = Path.home() / 'Desktop'          # solo para scripts generadores (dev local)
BASE_DIR = Path(__file__).parent             # raiz del repo — disponible en Railway

# Rutas centralizadas — carpeta dentro del repo, accesible en Railway y local
REPORT_PATHS = {
    'canchero':   BASE_DIR / 'reportes_velez' / 'faro_reporte_velez_canchero.png',
    'agro_final': BASE_DIR / 'reportes_velez' / 'faro_reporte_velez_agro_FINAL.png',
    'solar_v2':   BASE_DIR / 'reportes_velez' / 'faro_reporte_velez_solar_v2.png',
    'velez':      BASE_DIR / 'reportes_velez' / 'faro_reporte_velez.png',
}

# Manuales PDF — se adjuntan solo en el primer envío (manual_sent flag en velez_jobs.json)
# comision recibe los 3 PDFs ejecutivos en un solo email
MANUAL_PATHS = {
    'roger':    BASE_DIR / 'reportes_velez' / 'manual_velez_roger.pdf',
    'juan':     BASE_DIR / 'reportes_velez' / 'manual_velez_juan.pdf',
    'banchero': BASE_DIR / 'reportes_velez' / 'manual_velez_banchero.pdf',
    'pait':     BASE_DIR / 'reportes_velez' / 'manual_velez_pait.pdf',
    'comision': [
        BASE_DIR / 'reportes_velez' / 'manual_velez_berlanga.pdf',
        BASE_DIR / 'reportes_velez' / 'manual_velez_nelson.pdf',
        BASE_DIR / 'reportes_velez' / 'manual_velez_aveleyra.pdf',
    ],
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
            data = json.loads(JOBS_FILE.read_text())
            # Ensure manual_sent key exists in files created before this feature
            data.setdefault('manual_sent', False)
            return data
        except Exception:
            pass
    return {'events': [], 'email_queue': [], 'last_report': None,
            'last_solar': None, 'manual_sent': False}

def save_jobs(jobs: dict):
    JOBS_FILE.write_text(json.dumps(jobs, indent=2, default=str))

# ─── WHATSAPP (CallMeBot) ─────────────────────────────────────────────────────

def send_whatsapp(phone: str, message: str) -> bool:
    key = _wa_keys().get(phone, '')
    if not phone or not key:
        log.warning(f'WhatsApp key not configured for {phone[:8]}*** — skipping')
        return False
    try:
        r = requests.get('https://api.callmebot.com/whatsapp.php',
                         params={'phone': phone, 'text': message, 'apikey': key},
                         timeout=15)
        if r.status_code == 200:
            log.info(f'WhatsApp sent to {phone[:8]}***')
            return True
        log.error(f'WhatsApp failed {phone[:8]}***: {r.status_code} {r.text[:100]}')
        return False
    except Exception as e:
        log.error(f'WhatsApp exception {phone[:8]}***: {e}')
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
    """Run a generator script. Looks in BASE_DIR (Railway) first, then DESKTOP (local dev).
    Falls back to last cached PNG if generation fails."""
    script = BASE_DIR / script_name
    if not script.exists():
        script = DESKTOP / script_name
    if script.exists():
        if _run_script(str(script)):
            return str(out_path) if out_path.exists() else None
        log.error(f'Script failed: {script_name}')
    else:
        log.warning(f'Script not found: {script_name} (tried BASE_DIR and DESKTOP)')
    if out_path.exists():
        log.warning(f'Using cached report (generation failed): {out_path.name}')
        return str(out_path)
    log.error(f'No cached fallback for {out_path.name}')
    return None

def generate_canchero_report() -> Optional[str]:
    return generate_report('gen_velez_canchero.py', REPORT_PATHS['canchero'])

def generate_solar_v2_report() -> Optional[str]:
    return generate_report('gen_velez_solar_v2.py', REPORT_PATHS['solar_v2'])

def generate_agro_final_report() -> Optional[str]:
    return generate_report('gen_velez_final.py', REPORT_PATHS['agro_final'])

def generate_main_report() -> Optional[str]:
    return generate_report('gen_velez_main.py', REPORT_PATHS['velez'])

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

# ─── EMAIL BODIES POR ROL ─────────────────────────────────────────────────────

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
        'Estado Villa Olimpica Esta Semana',
        """
        <p>Juan, te mando el estado actualizado de las canchas y el campo.</p>
        <h3 style="color:#e74c3c">Urgente — Cancha 4</h3>
        <ul style="font-size:14px">
          <li>Focos activos de hongo en zona central (85 m²) — requiere fungicida HOY</li>
          <li>Drenaje lateral sur roto — agua estancada confirmada por satelite</li>
          <li>Resembrado zona central necesario esta semana</li>
        </ul>
        <h3 style="color:#f0b429">Acciones Esta Semana</h3>
        <ul style="font-size:14px">
          <li><b>Cancha 1:</b> hongo en desarrollo, fungicida preventivo</li>
          <li><b>Cancha 2:</b> fertilizar 20 kg N/ha</li>
          <li><b>Cancha 3:</b> fungicida activo + resembrado parcial</li>
          <li><b>Campo Amalfitani:</b> NDVI 0.68 — estado optimo, mantener riego</li>
        </ul>
        <p style="font-size:13px;color:#9aa0a8">
          Se adjuntan mapa de canchas y reporte agro completo.
        </p>
        """
    )

def _body_banchero() -> str:
    return _html_wrap(
        'Estado Operativo del Predio — Velez Sarsfield',
        f"""
        <p>Fernando, resumen operativo de la semana del {datetime.now().strftime("%d/%m/%Y")}.</p>
        <h3 style="color:#c9a84c">Predio Deportivo</h3>
        <table style="color:#f2ede4;border-collapse:collapse;width:100%;font-size:14px">
          <tr style="background:#141c24">
            <th style="padding:8px;border:1px solid #c9a84c44;text-align:left">Zona</th>
            <th style="padding:8px;border:1px solid #c9a84c44;text-align:left">Estado</th>
            <th style="padding:8px;border:1px solid #c9a84c44;text-align:left">Accion requerida</th>
          </tr>
          <tr><td style="padding:6px;border:1px solid #c9a84c22">Cancha 4</td>
              <td style="padding:6px;color:#e74c3c;border:1px solid #c9a84c22"><b>CRITICO</b></td>
              <td style="padding:6px;border:1px solid #c9a84c22">Intervencion urgente — ver mapa</td></tr>
          <tr><td style="padding:6px;border:1px solid #c9a84c22">Canchas 1, 2, 3</td>
              <td style="padding:6px;color:#f0b429;border:1px solid #c9a84c22"><b>ATENCION</b></td>
              <td style="padding:6px;border:1px solid #c9a84c22">Acciones programadas esta semana</td></tr>
          <tr><td style="padding:6px;border:1px solid #c9a84c22">Campo Amalfitani</td>
              <td style="padding:6px;color:#27ae60;border:1px solid #c9a84c22"><b>OPTIMO</b></td>
              <td style="padding:6px;border:1px solid #c9a84c22">Sin accion inmediata</td></tr>
        </table>
        <h3 style="color:#c9a84c">Sistema Solar (210 paneles — 120 kWp)</h3>
        <ul style="font-size:14px">
          <li>Eficiencia actual: <b>82.4%</b></li>
          <li><b style="color:#f0b429">Zona E (Arco Sur):</b> 8 paneles con temperatura >60°C — inspeccion esta semana</li>
          <li>Produccion estimada semana: 2,840 kWh</li>
        </ul>
        <h3 style="color:#e74c3c">Infraestructura</h3>
        <ul style="font-size:14px">
          <li><b>Tribuna Oeste:</b> desplazamiento InSAR 2.80 mm — supera umbral de 3.0 mm<br>
              Recomendacion: inspección estructural esta semana</li>
        </ul>
        <p style="font-size:13px;color:#9aa0a8">Se adjuntan reporte general, solar y agro.</p>
        """
    )

def _body_pait() -> str:
    return _html_wrap(
        'Estado Canchas y Campo — Vision Deportiva',
        """
        <p>Sebastian, estado de los campos para esta semana de entrenamiento.</p>
        <h3 style="color:#e74c3c">NO APTO para entrenamiento</h3>
        <ul style="font-size:14px">
          <li><b>Cancha 4:</b> estado critico — hongo activo + drenaje roto + pasto ralo<br>
              No recomendada para uso hasta reparacion</li>
        </ul>
        <h3 style="color:#f0b429">Uso condicionado</h3>
        <ul style="font-size:14px">
          <li><b>Cancha 1:</b> apta, con fungicida preventivo aplicado antes del uso</li>
          <li><b>Cancha 3:</b> apta para trabajos livianos, tratamiento en curso</li>
        </ul>
        <h3 style="color:#27ae60">Optimas para uso</h3>
        <ul style="font-size:14px">
          <li><b>Cancha 2:</b> apta — NDVI 0.52, fertilizacion esta semana</li>
          <li><b>Campo Amalfitani:</b> NDVI 0.68 — excelente condicion</li>
        </ul>
        <p style="font-size:13px;color:#9aa0a8">
          Se adjuntan mapa de canchas con marcadores y reporte agro detallado.
        </p>
        """
    )

def _body_comision() -> str:
    return _html_wrap(
        f'Informe Ejecutivo Semanal — Velez Sarsfield · {datetime.now().strftime("%d/%m/%Y")}',
        f"""
        <p>Resumen satelital del Estadio Jose Amalfitani y Villa Olimpica,
        semana del {datetime.now().strftime("%d/%m/%Y")}.</p>
        <h3 style="color:#c9a84c">Indicadores Clave</h3>
        <table style="color:#f2ede4;border-collapse:collapse;width:100%;font-size:14px">
          <tr style="background:#141c24">
            <th style="padding:8px;border:1px solid #c9a84c44;text-align:left">Indicador</th>
            <th style="padding:8px;border:1px solid #c9a84c44;text-align:center">Estado</th>
            <th style="padding:8px;border:1px solid #c9a84c44;text-align:left">Impacto</th>
          </tr>
          <tr><td style="padding:6px;border:1px solid #c9a84c22">Cancha 4</td>
              <td style="padding:6px;text-align:center;border:1px solid #c9a84c22;color:#e74c3c"><b>CRITICO</b></td>
              <td style="padding:6px;border:1px solid #c9a84c22">Fuera de servicio — costo estimado recuperacion: $180k</td></tr>
          <tr><td style="padding:6px;border:1px solid #c9a84c22">Canchas 1–3</td>
              <td style="padding:6px;text-align:center;border:1px solid #c9a84c22;color:#f0b429"><b>ATENCION</b></td>
              <td style="padding:6px;border:1px solid #c9a84c22">Tratamientos preventivos esta semana</td></tr>
          <tr><td style="padding:6px;border:1px solid #c9a84c22">Sistema Solar</td>
              <td style="padding:6px;text-align:center;border:1px solid #c9a84c22;color:#f0b429"><b>82.4%</b></td>
              <td style="padding:6px;border:1px solid #c9a84c22">Produccion 2,840 kWh/semana — Zona E requiere revision</td></tr>
          <tr><td style="padding:6px;border:1px solid #c9a84c22">Campo Amalfitani</td>
              <td style="padding:6px;text-align:center;border:1px solid #c9a84c22;color:#27ae60"><b>OPTIMO</b></td>
              <td style="padding:6px;border:1px solid #c9a84c22">NDVI 0.68 — listo para uso</td></tr>
          <tr><td style="padding:6px;border:1px solid #c9a84c22">Tribuna Oeste</td>
              <td style="padding:6px;text-align:center;border:1px solid #c9a84c22;color:#e74c3c"><b>ALERTA</b></td>
              <td style="padding:6px;border:1px solid #c9a84c22">InSAR 2.80 mm — inspeccion estructural recomendada</td></tr>
        </table>
        <p style="font-size:13px;color:#9aa0a8">
          Se adjuntan 3 reportes completos: reporte general, mapa agro y estado solar.
        </p>
        """
    )

# ─── RECIPIENT CONFIG ─────────────────────────────────────────────────────────

def _velez_recipients() -> list:
    """Build recipient list at call time so env changes are reflected."""
    return [
        dict(key='roger',    name='Roger Bernal',
             to=env('VELEZ_EMAIL_CANCHERO'),
             subject='Faro · Tu mapa de trabajo esta semana · Campo Amalfitani',
             reports=['canchero'],
             body_fn=_body_roger),
        dict(key='juan',     name='Juan Gonzalez',
             to=env('VELEZ_EMAIL_INTENDENTE'),
             subject='Faro · Estado Villa Olimpica esta semana',
             reports=['canchero', 'agro_final'],
             body_fn=_body_juan),
        dict(key='banchero', name='Fernando Banchero',
             to=env('VELEZ_EMAIL_BANCHERO'),
             subject='Faro · Estado operativo del predio · Velez',
             reports=['velez', 'solar_v2', 'agro_final'],
             body_fn=_body_banchero),
        dict(key='pait',     name='Sebastian Pait',
             to=env('VELEZ_EMAIL_PAIT'),
             subject='Faro · Estado canchas y campo · Velez',
             reports=['canchero', 'agro_final'],
             body_fn=_body_pait),
        dict(key='comision', name='Comision Ejecutiva',
             to=env('VELEZ_EMAIL_COMISION'),
             subject='Faro Protocol · Informe ejecutivo semanal · Velez Sarsfield',
             reports=['velez', 'agro_final', 'solar_v2'],
             body_fn=_body_comision),
    ]

def send_all_reports(tipo: str = 'semanal'):
    jobs = load_jobs()
    manual_sent = jobs.get('manual_sent', False)
    date_str = datetime.now().strftime('%d/%m/%Y')

    for r in _velez_recipients():
        if not r['to']:
            log.warning(f'No email configured for {r["name"]} — skipping')
            continue
        subject   = f'{r["subject"]} · {date_str}'
        body_html = r['body_fn']()
        atts      = [str(REPORT_PATHS[k]) for k in r['reports']]

        # First send ever: attach the personal PDF manual
        if not manual_sent:
            manual = MANUAL_PATHS.get(r['key'])
            if isinstance(manual, list):
                atts.extend([str(p) for p in manual if p.exists()])
            elif manual and manual.exists():
                atts.append(str(manual))
            log.info(f'Attaching manual PDF for {r["name"]} (first send)')

        ok = send_email(r['to'], subject, body_html, atts)
        if not ok:
            queue_email(r['to'], subject, body_html, atts)
        log.info(f'Report sent to {r["name"]} ({r["key"]}) -> {ok}')

    # Mark manuals as delivered after all recipients processed
    if not manual_sent:
        jobs['manual_sent'] = True
        save_jobs(jobs)
        log.info('manual_sent = True — PDFs will not be re-attached in future weekly emails')

# ─── WEEKLY JOB ──────────────────────────────────────────────────────────────

def weekly_report(tipo: str = 'semanal', event_date: Optional[str] = None):
    log.info(f'=== Running {tipo} report ===')
    jobs = load_jobs()

    # Generate all 4 reports — each with up to 3 attempts, fallback to cached PNG
    def _gen_with_retry(fn, label):
        for attempt in range(3):
            path = fn()
            if path:
                return path
            log.warning(f'{label} attempt {attempt+1}/3 failed')
            if attempt < 2:
                time.sleep(300)   # 5 min between retries (not 30 — Railway has timeout limits)
        return None

    canchero_path = _gen_with_retry(generate_canchero_report,   'canchero')
    agro_path     = _gen_with_retry(generate_agro_final_report, 'agro_final')
    solar_path    = _gen_with_retry(generate_solar_v2_report,   'solar_v2')
    main_path     = _gen_with_retry(generate_main_report,       'main')

    if not canchero_path:
        log.error('Canchero report unavailable after 3 attempts. Aborting send.')
        return

    # Load data and check alerts
    data = load_latest_data()
    check_and_send_alerts(data)

    # Send differentiated emails — 5 recipients, each with role-specific content
    send_all_reports(tipo)

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
    """Send TEST email to each Vélez recipient. Returns dict of {key: bool}."""
    now_str = datetime.now().strftime('%d/%m/%Y %H:%M')
    results = {}
    for r in _velez_recipients():
        if not r['to']:
            log.warning(f'No email for {r["name"]} ({r["key"]}) — skipping')
            results[r['key']] = False
            continue
        ok = send_email(
            to=r['to'],
            subject=f'TEST · Faro Protocol · Velez · {now_str}',
            body_html=_html_wrap(
                f'TEST — {r["name"]}',
                f"""
                <p>Email de prueba del sistema Faro Protocol para Velez Sarsfield.</p>
                <p>Destinatario: <b>{r["name"]}</b></p>
                <p>Rol: <b>{r["key"]}</b> — recibe reportes: {', '.join(r["reports"])}</p>
                <p>Asunto real que recibira cada lunes:<br>
                   <i style="color:#c9a84c">{r["subject"]}</i></p>
                <p style="color:#9aa0a8;font-size:12px">Enviado: {now_str}</p>
                """
            ),
            attachments=[]
        )
        results[r['key']] = ok
        log.info(f'Test email {r["name"]} -> {ok}')
    return results

@app.route('/velez/reset_manuals', methods=['POST'])
def route_reset_manuals():
    """Reset manual_sent flag so PDFs are re-attached on the next weekly send."""
    jobs = load_jobs()
    jobs['manual_sent'] = False
    save_jobs(jobs)
    log.info('manual_sent reset to False by admin request')
    return jsonify({'status': 'ok', 'manual_sent': False})

@app.route('/health', methods=['GET'])
def health():
    return jsonify({'status': 'ok', 'service': 'faro-velez-scheduler'})

# ─── INTEGRATION — registrar en app externa (server.py / Railway) ────────────

def register_with_app(flask_app, apscheduler=None):
    """Attach Vélez routes and scheduled jobs to an existing Flask app + APScheduler.
    Used when server.py is the Railway entrypoint instead of running standalone."""
    flask_app.add_url_rule('/velez/evento',         'velez_evento',         route_evento,         methods=['POST'])
    flask_app.add_url_rule('/velez/status',         'velez_status',         route_status,         methods=['GET'])
    flask_app.add_url_rule('/velez/solar',          'velez_solar',          route_solar,          methods=['GET'])
    flask_app.add_url_rule('/velez/run_now',        'velez_run_now',        route_run_now,        methods=['POST'])
    flask_app.add_url_rule('/velez/test_email',     'velez_test_email',     route_test_email,     methods=['POST'])
    flask_app.add_url_rule('/velez/reset_manuals',  'velez_reset_manuals',  route_reset_manuals,  methods=['POST'])
    log.info('Velez: routes registered on external Flask app')

    if apscheduler:
        # Monday 07:00 ART = Monday 10:00 UTC  (ART = UTC-3, sin DST)
        apscheduler.add_job(weekly_report,        'cron',     day_of_week='mon',
                            hour=10, minute=0,    id='velez_weekly',  replace_existing=True)
        apscheduler.add_job(check_pending_events, 'interval', hours=1,
                            id='velez_events',    replace_existing=True)
        apscheduler.add_job(flush_email_queue,    'interval', minutes=30,
                            id='velez_flush',     replace_existing=True)
        log.info('Velez: APScheduler jobs registered — weekly Mon 10:00 UTC (07:00 ART)')

# ─── MAIN ────────────────────────────────────────────────────────────────────

if __name__ == '__main__':
    log.info('Faro Vélez Scheduler starting...')

    # Check required env vars
    missing = []
    for var in ['GMAIL_APP_PASS', 'VELEZ_EMAIL_CANCHERO', 'VELEZ_EMAIL_INTENDENTE',
                'VELEZ_EMAIL_BANCHERO', 'VELEZ_EMAIL_PAIT', 'VELEZ_EMAIL_COMISION']:
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
