#!/usr/bin/env python3
"""
gen_portal_key.py — Faro Protocol: gestor de clientes del portal
=================================================================
CAPA 7 — Gestión de clientes via CLI con Firebase Auth
CAPA 9 — Faro Week: acceso temporal de 7 días

Uso:
  python gen_portal_key.py <email> [--areas cordoba,balcarce] [--name "Empresa SA"] [--manual]
  python gen_portal_key.py --extend <email> [--days 7]
  python gen_portal_key.py --revoke <email>
  python gen_portal_key.py --list
  python gen_portal_key.py --check-expiry   # envía alertas de vencimiento (cron diario)

Variables en .env:
  FIREBASE_SERVICE_ACCOUNT  → path al JSON del service account de Firebase
  GMAIL_USER                → protocolfaro@gmail.com
  GMAIL_APP_PASS            → app password de Gmail (16 chars)
  PORTAL_URL                → https://faroprotocol.netlify.app (default)
  ADMIN_EMAIL               → email del admin para alertas (default: GMAIL_USER)

Primer uso — configurar Firebase:
  1. console.firebase.google.com → Proyecto → Configuración del proyecto
  2. Cuentas de servicio → Generar nueva clave privada → descargar JSON
  3. Poner ruta en .env: FIREBASE_SERVICE_ACCOUNT=/ruta/serviceAccount.json
"""

import argparse
import os
import sys
import json

# Fix encoding en Windows (cp1252 no soporta ✓, etc.)
if hasattr(sys.stdout, 'buffer') and (sys.stdout.encoding or '').lower() != 'utf-8':
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
import secrets
import string
import smtplib
import hashlib
from datetime import datetime, timedelta, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent / '.env')

try:
    import firebase_admin
    from firebase_admin import credentials, auth, firestore
    FIREBASE_OK = True
except ImportError:
    FIREBASE_OK = False

PORTAL_URL  = os.getenv('PORTAL_URL', 'https://faroprotocol.netlify.app')
GMAIL_USER  = os.getenv('GMAIL_USER', '')
GMAIL_PASS  = os.getenv('GMAIL_APP_PASS', '')
ADMIN_EMAIL = os.getenv('ADMIN_EMAIL', GMAIL_USER)
SA_PATH     = os.getenv('FIREBASE_SERVICE_ACCOUNT', '')

AREAS_AVAILABLE = [
    'cordoba', 'balcarce', 'vaca_muerta', 'rotterdam',
    'permian', 'pilbara', 'amazonas', 'indiana', 'malacca',
]

AREA_LABELS = {
    'cordoba':     'Córdoba (Agro · Marcos Juárez)',
    'balcarce':    'Balcarce (Agro)',
    'vaca_muerta': 'Vaca Muerta (Energía)',
    'rotterdam':   'Rotterdam (Marítimo)',
    'permian':     'Permian Basin (O&G)',
    'pilbara':     'Pilbara (Minería de hierro)',
    'amazonas':    'Amazonas (Deforestación)',
    'indiana':     'Indiana (Agro)',
    'malacca':     'Estrecho de Malacca (Shipping)',
}

# ── Firebase Admin ────────────────────────────────────────────────────────────

def _init_firebase():
    if not FIREBASE_OK:
        print("[ERROR] firebase-admin no instalado.")
        print("  Instalá con: pip install firebase-admin")
        sys.exit(1)
    if firebase_admin._apps:
        return firebase_admin.get_app()
    if not SA_PATH or not Path(SA_PATH).exists():
        print(f"[ERROR] FIREBASE_SERVICE_ACCOUNT no configurado o no existe: {SA_PATH!r}")
        print("  1. console.firebase.google.com → Configuración → Cuentas de servicio")
        print("  2. Generar nueva clave privada → descargar JSON")
        print("  3. Poner en .env: FIREBASE_SERVICE_ACCOUNT=/ruta/serviceAccount.json")
        sys.exit(1)
    cred = credentials.Certificate(SA_PATH)
    return firebase_admin.initialize_app(cred)


# ── Utilidades ────────────────────────────────────────────────────────────────

def _gen_temp_password(length: int = 16) -> str:
    chars = string.ascii_letters + string.digits + '!@#$%^&*'
    while True:
        pw = ''.join(secrets.choice(chars) for _ in range(length))
        if (any(c.isupper() for c in pw)
                and any(c.islower() for c in pw)
                and any(c.isdigit() for c in pw)):
            return pw


def _audit_log(entry: dict):
    log_path = Path(__file__).parent / 'audit_log.jsonl'
    entry['ts']     = datetime.now(timezone.utc).isoformat()
    entry['source'] = 'gen_portal_key.py'
    with open(log_path, 'a', encoding='utf-8') as f:
        f.write(json.dumps(entry, ensure_ascii=False) + '\n')


# ── Email ─────────────────────────────────────────────────────────────────────

def _send_email(to: str, subject: str, html: str) -> bool:
    if not GMAIL_USER or not GMAIL_PASS:
        print(f"  [EMAIL SKIP] GMAIL_USER/GMAIL_APP_PASS no configurados en .env")
        print(f"  Destinatario: {to}")
        print(f"  Asunto:       {subject}")
        return False
    try:
        msg = MIMEMultipart('alternative')
        msg['From']    = f'"Faro Protocol" <{GMAIL_USER}>'
        msg['To']      = to
        msg['Subject'] = subject
        msg.attach(MIMEText(html, 'html', 'utf-8'))
        with smtplib.SMTP_SSL('smtp.gmail.com', 465) as server:
            server.login(GMAIL_USER, GMAIL_PASS)
            server.sendmail(GMAIL_USER, to, msg.as_string())
        return True
    except Exception as e:
        print(f"  [EMAIL ERROR] {e}")
        return False


_WELCOME_HTML = """\
<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<style>
*{{margin:0;padding:0;box-sizing:border-box;}}
body{{background:#06080b;color:#f2ede4;font-family:'Helvetica Neue',Arial,sans-serif;}}
.wrap{{max-width:600px;margin:0 auto;padding:48px 28px;}}
.logo{{font-size:22px;letter-spacing:0.35em;color:#c9a84c;text-transform:uppercase;font-weight:300;}}
.logo-sub{{font-family:monospace;font-size:8px;color:rgba(242,237,228,0.25);letter-spacing:0.22em;
           text-transform:uppercase;margin-bottom:40px;margin-top:4px;}}
h2{{color:#e2c97e;font-weight:300;font-size:24px;margin-bottom:14px;border-left:3px solid #c9a84c;
    padding-left:16px;}}
p{{color:rgba(242,237,228,0.72);font-size:14px;line-height:1.85;margin-bottom:18px;}}
.areas{{background:#0d1117;border:1px solid rgba(201,168,76,0.18);border-left:3px solid #c9a84c;
        padding:18px 22px;margin:24px 0;border-radius:2px;}}
.areas-title{{font-family:monospace;font-size:8px;color:rgba(201,168,76,0.45);letter-spacing:0.22em;
              text-transform:uppercase;margin-bottom:12px;}}
.area-item{{font-family:monospace;font-size:12px;color:#e2c97e;margin:6px 0;}}
.btn{{display:inline-block;background:#c9a84c;color:#06080b;padding:14px 30px;
      text-decoration:none;font-weight:700;font-size:12px;letter-spacing:0.12em;
      text-transform:uppercase;margin:24px 0;border-radius:1px;}}
.info-box{{background:rgba(201,168,76,0.04);border:1px solid rgba(201,168,76,0.12);
           padding:16px 20px;margin:24px 0;border-radius:2px;}}
.info-row{{font-family:monospace;font-size:11px;color:rgba(242,237,228,0.5);margin:6px 0;}}
.info-row strong{{color:rgba(242,237,228,0.85);}}
.warn{{color:#c8881a;}}
.footer{{border-top:1px solid rgba(201,168,76,0.08);margin-top:44px;padding-top:20px;
         font-size:10px;color:rgba(242,237,228,0.2);font-family:monospace;line-height:1.8;}}
a{{color:#c9a84c;text-decoration:none;}}
</style>
</head>
<body>
<div class="wrap">
  <div class="logo">FARO</div>
  <div class="logo-sub">PROTOCOL · SATELLITE INTELLIGENCE</div>

  <h2>Bienvenido/a a Faro Protocol</h2>

  <p>Hola <strong style="color:#f2ede4;">{name}</strong>,</p>
  <p>Tu acceso al portal de inteligencia satelital está listo.
     Tenés <strong style="color:#c9a84c;">7 días de Faro Week</strong>
     para explorar los datos de tus áreas asignadas en tiempo real.</p>

  <div class="areas">
    <div class="areas-title">Áreas asignadas</div>
    {areas_html}
  </div>

  <p><strong style="color:#f2ede4;">Primer paso:</strong> hacé clic en el botón
  de abajo para configurar tu contraseña de acceso al portal.
  El link es válido por <strong style="color:#c9a84c;">48 horas</strong>.</p>

  <div style="text-align:center;">
    <a href="{login_link}" class="btn">Configurar contraseña y acceder →</a>
  </div>

  <div class="info-box">
    <div class="info-row">📅 Tu Faro Week vence: <strong class="warn">{expiry_date}</strong></div>
    <div class="info-row">🔗 Portal: <a href="{portal_url}">{portal_url}</a></div>
    <div class="info-row">📧 Email de acceso: <strong>{email}</strong></div>
  </div>

  <p>Si querés continuar después de los 7 días o tenés preguntas,
  escribinos a <a href="mailto:protocolfaro@gmail.com">protocolfaro@gmail.com</a></p>

  <p style="font-size:13px;color:rgba(242,237,228,0.4);">
  Encontrás el manual de uso del portal adjunto a este email.</p>

  <div class="footer">
    <div>Faro Protocol · Inteligencia satelital para decisiones de negocio</div>
    <div>Este mensaje fue generado automáticamente. No compartás tu link de acceso.</div>
    <div style="margin-top:8px;">© 2026 Faro Protocol ·
      <a href="mailto:protocolfaro@gmail.com">protocolfaro@gmail.com</a></div>
  </div>
</div>
</body>
</html>
"""

_EXPIRY_WARNING_HTML = """\
<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<style>
*{{margin:0;padding:0;box-sizing:border-box;}}
body{{background:#06080b;color:#f2ede4;font-family:'Helvetica Neue',Arial,sans-serif;}}
.wrap{{max-width:600px;margin:0 auto;padding:48px 28px;}}
.logo{{font-size:22px;letter-spacing:0.35em;color:#c9a84c;text-transform:uppercase;font-weight:300;}}
.logo-sub{{font-family:monospace;font-size:8px;color:rgba(242,237,228,0.25);letter-spacing:0.22em;
           text-transform:uppercase;margin-bottom:40px;margin-top:4px;}}
h2{{color:#c8881a;font-weight:300;font-size:24px;margin-bottom:14px;}}
p{{color:rgba(242,237,228,0.72);font-size:14px;line-height:1.85;margin-bottom:18px;}}
.timer-box{{background:rgba(200,136,26,0.06);border:1px solid rgba(200,136,26,0.25);
            border-left:3px solid #c8881a;padding:18px 22px;margin:24px 0;border-radius:2px;
            font-family:monospace;font-size:13px;color:#c8881a;}}
.btn{{display:inline-block;background:#c9a84c;color:#06080b;padding:14px 30px;
      text-decoration:none;font-weight:700;font-size:12px;letter-spacing:0.12em;
      text-transform:uppercase;margin:24px 0;border-radius:1px;}}
.plans{{background:#0d1117;border:1px solid rgba(201,168,76,0.12);padding:20px 24px;
        margin:24px 0;border-radius:2px;}}
.plan-item{{font-size:13px;color:rgba(242,237,228,0.65);margin:10px 0;
            padding-left:16px;border-left:2px solid rgba(201,168,76,0.2);}}
.plan-item strong{{color:#e2c97e;}}
.footer{{border-top:1px solid rgba(201,168,76,0.08);margin-top:44px;padding-top:20px;
         font-size:10px;color:rgba(242,237,228,0.2);font-family:monospace;line-height:1.8;}}
a{{color:#c9a84c;text-decoration:none;}}
</style>
</head>
<body>
<div class="wrap">
  <div class="logo">FARO</div>
  <div class="logo-sub">PROTOCOL · SATELLITE INTELLIGENCE</div>

  <h2>⚠ Tu Faro Week vence mañana</h2>

  <p>Hola <strong style="color:#f2ede4;">{name}</strong>,</p>
  <p>Tu período de Faro Week está por terminar.
     Asegurate de revisar todos tus datos antes del vencimiento.</p>

  <div class="timer-box">
    📅 Vencimiento: <strong>{expiry_date}</strong>
  </div>

  <p>Si querés seguir monitoreando tus áreas con inteligencia satelital semanal,
     activá tu suscripción haciendo clic abajo:</p>

  <div style="text-align:center;">
    <a href="mailto:protocolfaro@gmail.com?subject=Quiero%20continuar%20con%20Faro%20Protocol%20-%20{email_encoded}"
       class="btn">Activar suscripción →</a>
  </div>

  <div class="plans">
    <div style="font-family:monospace;font-size:8px;color:rgba(201,168,76,0.4);
                letter-spacing:0.2em;text-transform:uppercase;margin-bottom:14px;">
      Planes disponibles</div>
    <div class="plan-item">
      <strong>Starter — USD 199/mes</strong><br>
      1 área · Reporte semanal · Acceso al portal · SHA-256 certificado
    </div>
    <div class="plan-item">
      <strong>Professional — USD 499/mes</strong><br>
      3 áreas · Reportes semanales · Alertas en tiempo real · Dashboard avanzado
    </div>
    <div class="plan-item">
      <strong>Enterprise — a convenir</strong><br>
      Áreas ilimitadas · API access · White label · SLA garantizado
    </div>
  </div>

  <p style="font-size:13px;color:rgba(242,237,228,0.45);">
  Para consultas: <a href="mailto:protocolfaro@gmail.com">protocolfaro@gmail.com</a></p>

  <div class="footer">
    <div>Faro Protocol · Inteligencia satelital para decisiones de negocio</div>
    <div>© 2026 Faro Protocol · protocolfaro@gmail.com</div>
  </div>
</div>
</body>
</html>
"""


# ── Acciones principales ──────────────────────────────────────────────────────

def create_client(email: str, areas: list, name: str = None, generate_manual: bool = False):
    """CAPA 7 + 9: crea usuario en Firebase, perfil en Firestore, envía email de bienvenida."""
    _init_firebase()

    email = email.strip().lower()
    name  = name or email.split('@')[0].replace('.', ' ').title()

    print(f"\n[FARO] Creando cliente: {email}")
    print(f"  Nombre : {name}")
    print(f"  Áreas  : {', '.join(areas)}")

    # 1. Crear usuario en Firebase Auth
    temp_password = _gen_temp_password()
    try:
        user = auth.create_user(
            email=email,
            display_name=name,
            password=temp_password,
            email_verified=False,
        )
        print(f"  Firebase UID: {user.uid}")
    except Exception as e:
        if 'EMAIL_EXISTS' in str(e) or 'email-already-exists' in str(e):
            print(f"  [WARN] Usuario ya existe en Firebase. Actualizando...")
            user = auth.get_user_by_email(email)
        else:
            print(f"  [ERROR] Firebase Auth: {e}")
            sys.exit(1)

    # 2. Custom claims (áreas + plan)
    auth.set_custom_user_claims(user.uid, {
        'areas':     areas,
        'plan':      'analyst',
        'faro_week': True,
    })

    # 3. Firestore — perfil del cliente
    db  = firestore.client()
    now = datetime.now(timezone.utc)
    expiry = now + timedelta(days=7)

    db.collection('clients').document(user.uid).set({
        'email':          email,
        'name':           name,
        'uid':            user.uid,
        'areas':          areas,
        'plan':           'analyst',
        'faroWeekExpiry': expiry,
        'createdAt':      now,
        'updatedAt':      now,
        'status':         'active',
        'knownIPs':       [],
        'expiryAlertSent': False,
    })
    print(f"  Firestore: perfil creado")
    print(f"  Faro Week expira: {expiry.strftime('%Y-%m-%d %H:%M UTC')}")

    # 4. Link de primer acceso — password reset (48 horas)
    try:
        reset_link = auth.generate_password_reset_link(
            email,
            auth.ActionCodeSettings(url=PORTAL_URL, handle_code_in_app=False),
        )
        print(f"  Link primer acceso generado (válido 48h)")
    except Exception as e:
        reset_link = f"{PORTAL_URL}/faro_client_portal.html"
        print(f"  [WARN] Link de reset: {e}")

    # 5. Audit log
    _audit_log({
        'type':           'client_created',
        'email':          email,
        'uid':            user.uid,
        'areas':          areas,
        'plan':           'analyst',
        'faroWeekExpiry': expiry.isoformat(),
    })

    # 6. Manual PDF (opcional)
    if generate_manual:
        try:
            from faro_manual_cliente import generate_manual as gen_pdf
            manual_path = gen_pdf(email=email, name=name, areas=areas)
            print(f"  Manual PDF: {manual_path}")
        except ImportError:
            print("  [WARN] faro_manual_cliente.py no disponible. Generá con:")
            print("         python faro_manual_cliente.py --email " + email)
        except Exception as e:
            print(f"  [WARN] Error generando manual: {e}")

    # 7. Email de bienvenida
    areas_html = '\n'.join(
        f'    <div class="area-item">▸ {AREA_LABELS.get(a, a)}</div>'
        for a in areas
    )
    html = _WELCOME_HTML.format(
        name=name,
        email=email,
        areas_html=areas_html,
        login_link=reset_link,
        expiry_date=expiry.strftime('%d de %B de %Y, %H:%M UTC'),
        portal_url=PORTAL_URL,
    )
    sent = _send_email(email, 'Bienvenido/a a Faro Protocol — Tu acceso está listo', html)
    if sent:
        print(f"  Email de bienvenida enviado a {email}")

    print(f"\n  ✓ Cliente creado")
    print(f"    UID    : {user.uid}")
    print(f"    Expira : {expiry.strftime('%Y-%m-%d')}")
    print(f"    Link   : {reset_link[:80]}...")


def extend_access(email: str, days: int = 7):
    """CAPA 9: extiende el vencimiento de Faro Week."""
    _init_firebase()

    email = email.strip().lower()
    print(f"\n[FARO] Extendiendo acceso: {email} (+{days} días)")

    try:
        user = auth.get_user_by_email(email)
    except Exception:
        print(f"  [ERROR] Usuario no encontrado: {email}")
        sys.exit(1)

    db  = firestore.client()
    ref = db.collection('clients').document(user.uid)
    snap = ref.get()

    if not snap.exists:
        print(f"  [ERROR] Perfil en Firestore no encontrado. Creá el cliente primero.")
        sys.exit(1)

    profile = snap.to_dict()
    current_expiry = profile.get('faroWeekExpiry')
    now = datetime.now(timezone.utc)

    # Base: actual expiry or now, whichever is later
    if current_expiry:
        exp_dt = (current_expiry.replace(tzinfo=timezone.utc)
                  if getattr(current_expiry, 'tzinfo', None) is None
                  else current_expiry)
        base = max(exp_dt, now)
    else:
        base = now

    new_expiry = base + timedelta(days=days)

    ref.update({
        'faroWeekExpiry':  new_expiry,
        'updatedAt':       now,
        'status':          'active',
        'expiryAlertSent': False,
    })

    # Re-habilitar usuario si estaba desactivado
    auth.update_user(user.uid, disabled=False)
    auth.revoke_refresh_tokens(user.uid)  # Forzar re-login con nuevo estado

    _audit_log({
        'type':      'access_extended',
        'email':     email,
        'uid':       user.uid,
        'days':      days,
        'newExpiry': new_expiry.isoformat(),
    })

    print(f"  Nuevo vencimiento: {new_expiry.strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"  ✓ Acceso extendido")


def revoke_access(email: str):
    """CAPA 5: revoca acceso inmediatamente — deshabilita usuario + invalida tokens."""
    _init_firebase()

    email = email.strip().lower()
    print(f"\n[FARO] Revocando acceso: {email}")

    try:
        user = auth.get_user_by_email(email)
    except Exception:
        print(f"  [ERROR] Usuario no encontrado: {email}")
        sys.exit(1)

    auth.update_user(user.uid, disabled=True)
    auth.revoke_refresh_tokens(user.uid)

    db = firestore.client()
    db.collection('clients').document(user.uid).update({
        'status':    'revoked',
        'revokedAt': datetime.now(timezone.utc),
        'updatedAt': datetime.now(timezone.utc),
    })

    _audit_log({'type': 'access_revoked', 'email': email, 'uid': user.uid})

    print(f"  ✓ Acceso revocado. Sesiones activas invalidadas en tiempo real.")


def list_clients():
    """Lista todos los clientes registrados en Firestore."""
    _init_firebase()

    db      = firestore.client()
    clients = list(db.collection('clients').stream())
    now     = datetime.now(timezone.utc)

    if not clients:
        print("\n  No hay clientes registrados.")
        return

    print(f"\n{'EMAIL':<36} {'NOMBRE':<22} {'ÁREAS':<28} {'ESTADO':<10} VENCIMIENTO")
    print('─' * 110)

    for doc in clients:
        p = doc.to_dict()
        exp = p.get('faroWeekExpiry')
        if exp:
            exp_dt = (exp.replace(tzinfo=timezone.utc)
                      if getattr(exp, 'tzinfo', None) is None else exp)
            days_left = (exp_dt - now).days
            if days_left >= 0:
                expiry_str = f"{exp_dt.strftime('%Y-%m-%d')} (+{days_left}d)"
            else:
                expiry_str = f"VENCIDO {exp_dt.strftime('%Y-%m-%d')}"
        else:
            expiry_str = '—'

        areas_str = ', '.join(p.get('areas', []))[:26]
        print(f"  {p.get('email','—'):<34} {p.get('name','—'):<22} "
              f"{areas_str:<28} {p.get('status','—'):<10} {expiry_str}")


def check_expiry():
    """CAPA 9: envía alertas 24h antes del vencimiento. Llamar desde cron/Task Scheduler."""
    _init_firebase()

    db  = firestore.client()
    now = datetime.now(timezone.utc)
    window_end = now + timedelta(hours=25)

    clients = db.collection('clients').where('status', '==', 'active').stream()
    alerts  = 0

    for doc in clients:
        p   = doc.to_dict()
        exp = p.get('faroWeekExpiry')
        if not exp:
            continue

        exp_dt = (exp.replace(tzinfo=timezone.utc)
                  if getattr(exp, 'tzinfo', None) is None else exp)

        # Skip already expired or already alerted
        if exp_dt <= now:
            continue
        if p.get('expiryAlertSent'):
            continue
        if exp_dt > window_end:
            continue

        email = p.get('email', '')
        name  = p.get('name', '')

        import urllib.parse
        html = _EXPIRY_WARNING_HTML.format(
            name=name,
            expiry_date=exp_dt.strftime('%d/%m/%Y a las %H:%M UTC'),
            email_encoded=urllib.parse.quote(email),
        )
        sent = _send_email(email, 'Faro Protocol — Tu Faro Week vence mañana', html)

        if sent:
            doc.reference.update({
                'expiryAlertSent':   True,
                'expiryAlertSentAt': now,
            })
            _audit_log({
                'type':    'expiry_alert_sent',
                'email':   email,
                'expiry':  exp_dt.isoformat(),
            })
            alerts += 1
            print(f"  ✓ Alerta enviada a {email}")

    print(f"\n  {alerts} alerta(s) de vencimiento enviada(s)")


# ── CLI ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description='Faro Protocol — Gestión de clientes',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument('email', nargs='?',
                        help='Email del cliente a crear')
    parser.add_argument('--areas', default='cordoba',
                        help='Áreas separadas por coma (default: cordoba)')
    parser.add_argument('--name',
                        help='Nombre de la empresa o persona')
    parser.add_argument('--manual', action='store_true',
                        help='Generar manual PDF de bienvenida')
    parser.add_argument('--extend', metavar='EMAIL',
                        help='Extender acceso de un cliente')
    parser.add_argument('--days', '--dias', type=int, default=7,
                        help='Días a extender (default: 7)')
    parser.add_argument('--revoke', metavar='EMAIL',
                        help='Revocar acceso inmediatamente')
    parser.add_argument('--list', action='store_true',
                        help='Listar todos los clientes')
    parser.add_argument('--check-expiry', action='store_true',
                        help='Enviar alertas de vencimiento (24h window)')

    args = parser.parse_args()

    if args.extend:
        extend_access(args.extend, args.days)
    elif args.revoke:
        revoke_access(args.revoke)
    elif args.list:
        list_clients()
    elif args.check_expiry:
        check_expiry()
    elif args.email:
        areas = [a.strip() for a in args.areas.split(',') if a.strip()]
        unknown = [a for a in areas if a not in AREAS_AVAILABLE]
        if unknown:
            print(f"[ERROR] Áreas desconocidas: {unknown}")
            print(f"  Disponibles: {', '.join(AREAS_AVAILABLE)}")
            sys.exit(1)
        create_client(args.email, areas, args.name, args.manual)
    else:
        parser.print_help()
        print('\nEjemplos:')
        print('  python gen_portal_key.py cliente@empresa.com --areas cordoba,balcarce --manual')
        print('  python gen_portal_key.py --extend cliente@empresa.com --days 7')
        print('  python gen_portal_key.py --list')
        print('  python gen_portal_key.py --check-expiry')


if __name__ == '__main__':
    main()
