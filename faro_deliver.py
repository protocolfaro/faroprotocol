"""
FARO PROTOCOL — Entrega de reportes por email

Envía el reporte PNG semanal + SHA-256 a los clientes configurados para cada área.

Uso:
    python faro_deliver.py --area cordoba
    python faro_deliver.py --area cordoba --test   # solo verifica config, no envía

Configuración en .env:
    GMAIL_USER=protocolfaro@gmail.com
    GMAIL_APP_PASS=xxxx xxxx xxxx xxxx   # Google App Password (no la contraseña normal)
    # Obtener en: myaccount.google.com → Seguridad → Contraseñas de aplicaciones

Clientes en faro_clientes.json (crear en la raíz del proyecto):
    {
      "cordoba":    ["cliente@empresa.com", "otro@fondo.com"],
      "balcarce":   ["productor@campo.com"],
      "vaca_muerta":[]
    }
"""

import argparse
import json
import os
import smtplib
import sys
from datetime import datetime
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')
if hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(encoding='utf-8')

PROJECT_ROOT    = Path(__file__).parent
CLIENTES_JSON   = PROJECT_ROOT / 'faro_clientes.json'
DATA_JSON       = PROJECT_ROOT / 'data.json'
GMAIL_SMTP_HOST = 'smtp.gmail.com'
GMAIL_SMTP_PORT = 587


# ── Env loader ────────────────────────────────────────────────────────────────

def _cargar_env():
    env_file = PROJECT_ROOT / '.env'
    if env_file.exists():
        for linea in env_file.read_text(encoding='utf-8').splitlines():
            linea = linea.strip()
            if linea and not linea.startswith('#') and '=' in linea:
                k, _, v = linea.partition('=')
                os.environ.setdefault(k.strip(), v.strip())


# ── Clientes ──────────────────────────────────────────────────────────────────

def cargar_clientes() -> dict:
    """Carga faro_clientes.json. Si no existe, crea uno vacío con comentario."""
    if not CLIENTES_JSON.exists():
        template = {
            "_instrucciones": (
                "Agregar emails de clientes por área. "
                "Formato: 'area_name': ['email1@empresa.com', 'email2@empresa.com']"
            ),
            "cordoba":     [],
            "balcarce":    [],
            "vaca_muerta": [],
        }
        with open(CLIENTES_JSON, 'w', encoding='utf-8') as f:
            json.dump(template, f, indent=2, ensure_ascii=False)
        print(f"  [faro_deliver] Creado faro_clientes.json — agregar emails de clientes")

    with open(CLIENTES_JSON, encoding='utf-8') as f:
        data = json.load(f)

    # Filtrar claves internas
    return {k: v for k, v in data.items() if not k.startswith('_') and isinstance(v, list)}


def obtener_emails_area(area_name: str) -> list:
    clientes = cargar_clientes()
    return clientes.get(area_name, [])


# ── Data helpers ──────────────────────────────────────────────────────────────

def cargar_stats_area(area_name: str) -> dict:
    """Lee stats del área desde data.json."""
    if not DATA_JSON.exists():
        return {}
    with open(DATA_JSON, encoding='utf-8') as f:
        data = json.load(f)

    pipeline = data.get('pipeline', {}).get(area_name, {})
    finance  = data.get('finance',  {}).get('business_cases', {}).get(area_name, {})

    return {
        'ultimo_run':      pipeline.get('ultimo_run', 'N/A'),
        'ndvi_medio':      pipeline.get('ndvi_medio'),
        'sar_medio_db':    pipeline.get('sar_medio_db'),
        'indice_fusion':   pipeline.get('indice_fusion_medio'),
        'sha256':          pipeline.get('sha256', 'N/A'),
        'score_faro':      pipeline.get('score_faro'),
        'rinde_estimado':  pipeline.get('rinde_estimado_tha'),
        'roi_x':           finance.get('roi_x'),
        'ahorro_ha':       finance.get('ahorro_total_ha'),
    }


# ── Email builder ─────────────────────────────────────────────────────────────

def construir_email(
    area_name: str,
    area_label: str,
    destinatario: str,
    stats: dict,
    reporte_png: Path,
    sha256_file: Path,
) -> MIMEMultipart:

    fecha = datetime.now().strftime('%Y-%m-%d')
    asunto = f"FARO PROTOCOL — Reporte semanal {area_label} · {fecha}"

    # Cuerpo del email (texto plano, sin HTML — legible en cualquier cliente)
    score_s  = f"{stats['score_faro']:.1f}" if stats.get('score_faro') else 'N/A'
    ndvi_s   = f"{stats['ndvi_medio']:.4f}" if stats.get('ndvi_medio') else 'N/A'
    sar_s    = f"{stats['sar_medio_db']:.2f} dB" if stats.get('sar_medio_db') else 'N/A'
    fusion_s = f"{stats['indice_fusion']:.4f}" if stats.get('indice_fusion') else 'N/A'
    rinde_s  = f"{stats['rinde_estimado']:.2f} t/ha" if stats.get('rinde_estimado') else 'N/A'
    roi_s    = f"{stats['roi_x']}x" if stats.get('roi_x') else 'N/A'
    ahorro_s = f"USD {stats['ahorro_ha']}/ha" if stats.get('ahorro_ha') else 'N/A'

    cuerpo = f"""FARO PROTOCOL — Reporte Semanal
Area: {area_label}
Fecha: {fecha}
Generado: {stats.get('ultimo_run', 'N/A')}
{"=" * 60}

METRICAS SATELITALES
  Score Faro           : {score_s} / 100
  NDVI medio           : {ndvi_s}
  SAR backscatter      : {sar_s}
  Indice Fusion        : {fusion_s}

MODELO ECONOMICO
  Rinde estimado       : {rinde_s}
  ROI                  : {roi_s}
  Ahorro               : {ahorro_s}

INTEGRIDAD
  SHA-256 del reporte  : {stats.get('sha256', 'N/A')}
  Verificacion         : Adjunto el reporte PNG y el archivo .sha256
  Para verificar:      sha256sum {reporte_png.name if reporte_png else 'reporte.png'}

{"=" * 60}
El reporte PNG adjunto fue sellado criptograficamente con SHA-256.
El hash no puede ser alterado retroactivamente.

FARO PROTOCOL
https://protocolfaro.github.io/faroprotocol
"""

    msg = MIMEMultipart()
    msg['From']    = os.environ.get('GMAIL_USER', 'protocolfaro@gmail.com')
    msg['To']      = destinatario
    msg['Subject'] = asunto
    msg.attach(MIMEText(cuerpo, 'plain', 'utf-8'))

    # Adjuntar PNG
    if reporte_png and reporte_png.exists():
        with open(reporte_png, 'rb') as f:
            adjunto = MIMEApplication(f.read(), _subtype='png')
            adjunto.add_header('Content-Disposition', 'attachment',
                               filename=reporte_png.name)
            msg.attach(adjunto)

    # Adjuntar .sha256
    if sha256_file and sha256_file.exists():
        with open(sha256_file, 'rb') as f:
            adjunto = MIMEApplication(f.read(), _subtype='octet-stream')
            adjunto.add_header('Content-Disposition', 'attachment',
                               filename=sha256_file.name)
            msg.attach(adjunto)

    return msg


# ── Sender ────────────────────────────────────────────────────────────────────

def enviar_email(msg: MIMEMultipart, test_mode: bool = False) -> bool:
    gmail_user = os.environ.get('GMAIL_USER', '')
    gmail_pass = os.environ.get('GMAIL_APP_PASS', '')

    if not gmail_user or not gmail_pass:
        print("  [ERROR] GMAIL_USER y/o GMAIL_APP_PASS no configurados en .env")
        print("  Instrucciones: myaccount.google.com → Seguridad → Contraseñas de aplicaciones")
        return False

    if test_mode:
        print(f"  [TEST] Email listo para: {msg['To']}")
        print(f"  [TEST] Asunto: {msg['Subject']}")
        print(f"  [TEST] Partes: {len(msg.get_payload())}")
        return True

    try:
        with smtplib.SMTP(GMAIL_SMTP_HOST, GMAIL_SMTP_PORT) as server:
            server.ehlo()
            server.starttls()
            server.login(gmail_user, gmail_pass)
            server.sendmail(gmail_user, msg['To'], msg.as_string())
        print(f"  [OK] Email enviado a: {msg['To']}")
        return True
    except smtplib.SMTPAuthenticationError:
        print("  [ERROR] Autenticacion Gmail fallida.")
        print("  Verificar GMAIL_APP_PASS en .env (necesita App Password, no contraseña normal)")
        return False
    except Exception as e:
        print(f"  [ERROR] {e}")
        return False


# ── Main ──────────────────────────────────────────────────────────────────────

def main(area_name: str, test_mode: bool = False) -> int:
    _cargar_env()

    sys.path.insert(0, str(PROJECT_ROOT))
    from faro_areas import load_area

    try:
        area = load_area(area_name)
    except Exception as e:
        print(f"[ERROR] Area '{area_name}' no encontrada: {e}")
        return 1

    area_label  = area['label']
    reporte_png = PROJECT_ROOT / area['report_png']
    sha256_file = reporte_png.with_suffix('.sha256')

    print("=" * 60)
    print(f"  FARO PROTOCOL — Entrega de reporte")
    print(f"  Area  : {area_label}")
    print(f"  Fecha : {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("=" * 60)

    if not reporte_png.exists():
        print(f"  [ERROR] Reporte PNG no encontrado: {reporte_png}")
        print(f"  Correr el pipeline primero: python faro_engine.py --area {area_name}")
        return 1

    emails = obtener_emails_area(area_name)
    if not emails:
        print(f"  Sin clientes configurados para '{area_name}' en faro_clientes.json")
        print(f"  Agregar emails al archivo y volver a correr.")
        return 0

    stats = cargar_stats_area(area_name)
    print(f"\n  Clientes a notificar: {len(emails)}")
    print(f"  Reporte: {reporte_png.name} ({reporte_png.stat().st_size // 1024} KB)")

    enviados = 0
    for email_dest in emails:
        print(f"\n  Enviando a {email_dest}...")
        msg = construir_email(
            area_name, area_label, email_dest,
            stats, reporte_png, sha256_file
        )
        if enviar_email(msg, test_mode=test_mode):
            enviados += 1

    print(f"\n  Resultado: {enviados}/{len(emails)} emails enviados")
    return 0 if enviados == len(emails) else 1


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='FARO PROTOCOL — Entrega de reportes')
    parser.add_argument('--area', required=True,
                        help='Area a entregar (ej: cordoba)')
    parser.add_argument('--test', action='store_true',
                        help='Solo verificar config, no enviar emails')
    args = parser.parse_args()
    sys.exit(main(args.area, test_mode=args.test))
