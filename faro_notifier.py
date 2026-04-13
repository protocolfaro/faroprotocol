#!/usr/bin/env python3
"""
faro_notifier.py — Faro Protocol: notificaciones WhatsApp via Twilio
====================================================================
Envía mensajes WhatsApp al equipo en dos casos:
  1. Nuevo pago confirmado (disparado desde faro_webhook.py)
  2. Anomalía crítica en pipeline (score < 40, disparado desde faro_auto.py)

Variables en .env (placeholders — pegar valores reales):
  TWILIO_ACCOUNT_SID    → Account SID  (console.twilio.com → Dashboard)
  TWILIO_AUTH_TOKEN     → Auth Token   (console.twilio.com → Dashboard)
  TWILIO_WHATSAPP_FROM  → whatsapp:+14155238886  (Twilio Sandbox)
  TWILIO_WHATSAPP_TO    → whatsapp:+54911XXXXXXXX (número admin)

Activar Sandbox Twilio (gratis):
  1. console.twilio.com → Messaging → Try it out → Send a WhatsApp message
  2. Enviar el mensaje "join <palabra-sandbox>" desde tu WhatsApp al número del sandbox
  3. TWILIO_WHATSAPP_FROM = whatsapp:+14155238886 (número fijo del sandbox)
  4. TWILIO_WHATSAPP_TO   = whatsapp:+54911XXXXXXXX (tu número)

Probar:
  python faro_notifier.py --test-payment test@empresa.com
  python faro_notifier.py --test-alert cordoba --score 35.0
"""

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent / '.env')
except ImportError:
    pass

# ── Credenciales Twilio — completar en .env ───────────────────────────────────
TWILIO_ACCOUNT_SID   = os.getenv('TWILIO_ACCOUNT_SID',   'PLACEHOLDER_TWILIO_ACCOUNT_SID')
TWILIO_AUTH_TOKEN    = os.getenv('TWILIO_AUTH_TOKEN',     'PLACEHOLDER_TWILIO_AUTH_TOKEN')
TWILIO_WHATSAPP_FROM = os.getenv('TWILIO_WHATSAPP_FROM',  'whatsapp:+14155238886')
TWILIO_WHATSAPP_TO   = os.getenv('TWILIO_WHATSAPP_TO',    'whatsapp:+PLACEHOLDER_ADMIN_PHONE')

_IS_PLACEHOLDER = (
    'PLACEHOLDER' in TWILIO_ACCOUNT_SID or
    'PLACEHOLDER' in TWILIO_AUTH_TOKEN
)


# ── Core ──────────────────────────────────────────────────────────────────────

def send_whatsapp(body: str, to: str = None) -> dict:
    """
    Envía un mensaje WhatsApp via Twilio.
    Si las credenciales son placeholders, simula el envío con log local.
    Retorna: {'ok': bool, 'sid': str, 'error': str, 'simulated': bool}
    """
    dest = to or TWILIO_WHATSAPP_TO

    if _IS_PLACEHOLDER:
        print(f"  [NOTIFIER] Twilio sin configurar — simulando envío")
        print(f"  Destino : {dest}")
        print(f"  Mensaje : {body[:140]}...")
        _audit({'event': 'whatsapp_simulated', 'to': dest, 'body': body[:300]})
        return {'ok': True, 'sid': 'SIMULATED', 'simulated': True}

    try:
        from twilio.rest import Client
    except ImportError:
        msg = 'twilio no instalado: pip install twilio'
        _audit({'event': 'whatsapp_error', 'error': msg})
        return {'ok': False, 'error': msg}

    try:
        client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
        message = client.messages.create(
            from_=TWILIO_WHATSAPP_FROM,
            to=dest,
            body=body,
        )
        _audit({
            'event':  'whatsapp_sent',
            'sid':    message.sid,
            'to':     message.to,
            'status': message.status,
        })
        return {'ok': True, 'sid': message.sid, 'simulated': False}
    except Exception as e:
        _audit({'event': 'whatsapp_error', 'error': str(e)})
        return {'ok': False, 'error': str(e)}


def notify_payment_confirmed(email: str, plan: str, areas: list) -> dict:
    """
    Trigger 1: nuevo pago confirmado via Lemon Squeezy.
    Llamar desde faro_webhook.py al recibir order_created con status=paid.
    """
    areas_str = ', '.join(areas) if areas else '—'
    body = (
        f"\u2705 *FARO PROTOCOL \u2014 Nuevo pago confirmado*\n\n"
        f"\U0001f4e7 Cliente: {email}\n"
        f"\U0001f4e6 Plan: {plan.upper()}\n"
        f"\U0001f30d \u00c1reas asignadas: {areas_str}\n"
        f"\U0001f550 {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}\n\n"
        f"\u2192 Cliente activado autom\u00e1ticamente en Firebase."
    )
    return send_whatsapp(body)


def notify_critical_anomaly(area: str, score: float, details: str = '') -> dict:
    """
    Trigger 2: anomalía crítica detectada en pipeline (score < 40 o estado ALERTA).
    Llamar desde faro_auto.py al finalizar el pipeline con score crítico.
    """
    ts   = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')
    body = (
        f"\U0001f6a8 *FARO PROTOCOL \u2014 ALERTA CR\u00cdTICA*\n\n"
        f"\U0001f30d \u00c1rea: {area.upper()}\n"
        f"\U0001f4ca Score Faro: {score:.1f} (umbral cr\u00edtico < 40)\n"
        f"\U0001f550 {ts}"
        + (f"\n\n\U0001f4cb {details}" if details else "")
        + f"\n\n\u2192 Revisar pipeline y notificar al cliente."
    )
    return send_whatsapp(body)


# ── Audit log ─────────────────────────────────────────────────────────────────

def _audit(entry: dict):
    entry.setdefault('ts',     datetime.now(timezone.utc).isoformat())
    entry.setdefault('source', 'faro_notifier.py')
    log_path = Path(__file__).parent / 'audit_log.jsonl'
    try:
        with open(log_path, 'a', encoding='utf-8') as f:
            f.write(json.dumps(entry, ensure_ascii=False) + '\n')
    except Exception:
        pass


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(
        description='Faro Protocol Notifier — test WhatsApp via Twilio',
    )
    parser.add_argument('--test-payment', metavar='EMAIL',
                        help='Simular notificación de pago confirmado')
    parser.add_argument('--test-alert',   metavar='AREA',
                        help='Simular alerta crítica de anomalía')
    parser.add_argument('--score', type=float, default=35.0,
                        help='Score para --test-alert (default: 35.0)')
    parser.add_argument('--to',
                        help='Número WhatsApp destino override (ej: whatsapp:+54911XXXXXXXX)')
    args = parser.parse_args()

    if args.test_payment:
        r = notify_payment_confirmed(
            args.test_payment, 'analyst', ['balcarce', 'cordoba'],
        )
        print(f"Resultado: {r}")
    elif args.test_alert:
        r = notify_critical_anomaly(
            args.test_alert, args.score,
            f'Detectado en pipeline — score {args.score:.1f} por debajo del umbral 40',
        )
        print(f"Resultado: {r}")
    else:
        parser.print_help()
        print('\nEjemplos:')
        print('  python faro_notifier.py --test-payment test@empresa.com')
        print('  python faro_notifier.py --test-alert cordoba --score 35.0')
        print()
        print('Configurar en .env:')
        print('  TWILIO_ACCOUNT_SID=ACxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx')
        print('  TWILIO_AUTH_TOKEN=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx')
        print('  TWILIO_WHATSAPP_FROM=whatsapp:+14155238886')
        print('  TWILIO_WHATSAPP_TO=whatsapp:+54911XXXXXXXX')
