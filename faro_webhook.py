#!/usr/bin/env python3
"""
faro_webhook.py — Faro Protocol: servidor de webhooks Lemon Squeezy
====================================================================
Recibe webhooks POST de Lemon Squeezy. Cuando llega un pago confirmado
activa el cliente en Firebase con áreas según el plan y envía notificación
WhatsApp al equipo.

Deploy:
  Local (con ngrok)  : python faro_webhook.py
  Heroku/Railway     : Procfile → web: gunicorn faro_webhook:app
  Fly.io             : fly deploy (con Procfile)

Instalar dependencias:
  pip install flask gunicorn

Variables en .env:
  LEMON_WEBHOOK_SECRET  → Signing secret del webhook (Lemon Squeezy Dashboard)
  PORT                  → Puerto del servidor (default: 5000)
  (hereda Firebase y Twilio de gen_portal_key.py / faro_notifier.py)

Configurar Lemon Squeezy:
  Dashboard → Settings → Webhooks → Add endpoint
  URL    : https://tu-server.com/webhooks/lemon
  Events : order_created  subscription_created  subscription_payment_success
  Secret : copiar a LEMON_WEBHOOK_SECRET en .env

Probar localmente:
  ngrok http 5000
  → pegar la URL https://xxxx.ngrok.io/webhooks/lemon en Lemon Squeezy
  → enviar test payload desde Lemon Squeezy Dashboard → Webhooks → Send test
"""

import hashlib
import hmac
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

try:
    from flask import Flask, request, jsonify
    FLASK_OK = True
except ImportError:
    FLASK_OK = False

# ── Config ────────────────────────────────────────────────────────────────────

LEMON_WEBHOOK_SECRET = os.getenv('LEMON_WEBHOOK_SECRET', 'PLACEHOLDER_LEMON_WEBHOOK_SECRET')
PORT                 = int(os.getenv('PORT', 5000))

# Plan → áreas asignadas automáticamente
# Observer   = 1 área (primera disponible)
# Analyst    = 3 áreas
# Sovereign  = todas las áreas
# Enterprise = todas las áreas + flag enterprise
ALL_AREAS = [
    'cordoba', 'balcarce', 'vaca_muerta', 'rotterdam',
    'permian', 'pilbara', 'amazonas', 'indiana', 'malacca', 'punta_colorada',
]

PLAN_MAX_AREAS = {
    'observer':   1,
    'analyst':    3,
    'sovereign':  None,   # None = ilimitado
    'enterprise': None,
}


# ── Helpers ───────────────────────────────────────────────────────────────────

def _detect_plan(variant_name: str, product_name: str) -> str:
    """Detecta el plan desde el nombre del variante/producto de Lemon Squeezy."""
    text = (variant_name + ' ' + product_name).lower()
    if 'sovereign'  in text: return 'sovereign'
    if 'enterprise' in text: return 'enterprise'
    if 'analyst'    in text: return 'analyst'
    if 'observer'   in text: return 'observer'
    return 'observer'   # fallback al plan mínimo


def _areas_for_plan(plan: str) -> list:
    """Retorna las áreas asignadas según el plan."""
    max_n = PLAN_MAX_AREAS.get(plan)
    return ALL_AREAS[:] if max_n is None else ALL_AREAS[:max_n]


def _verify_signature(payload: bytes, header_sig: str) -> bool:
    """Verifica la firma HMAC-SHA256 del webhook de Lemon Squeezy."""
    if 'PLACEHOLDER' in LEMON_WEBHOOK_SECRET:
        return True   # Sin verificación en modo desarrollo
    sig = header_sig.removeprefix('sha256=').strip()
    expected = hmac.new(
        LEMON_WEBHOOK_SECRET.encode('utf-8'),
        payload,
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected, sig)


def _audit(entry: dict):
    entry.setdefault('ts',     datetime.now(timezone.utc).isoformat())
    entry.setdefault('source', 'faro_webhook.py')
    try:
        with open(Path(__file__).parent / 'audit_log.jsonl', 'a', encoding='utf-8') as f:
            f.write(json.dumps(entry, ensure_ascii=False) + '\n')
    except Exception:
        pass


# ── Flask app ─────────────────────────────────────────────────────────────────

if not FLASK_OK:
    print("[ERROR] Flask no instalado.")
    print("  Instalá con: pip install flask gunicorn")
    sys.exit(1)

app = Flask(__name__)


@app.route('/webhooks/lemon', methods=['POST'])
def lemon_webhook():
    """Receptor principal de webhooks de Lemon Squeezy."""

    # 1. Verificar firma HMAC-SHA256
    sig = request.headers.get('X-Signature', '')
    if not _verify_signature(request.data, sig):
        _audit({'event': 'webhook_invalid_signature', 'ip': request.remote_addr})
        return jsonify({'error': 'invalid signature'}), 401

    try:
        payload = request.get_json(force=True) or {}
    except Exception:
        return jsonify({'error': 'invalid JSON'}), 400

    event_name = payload.get('meta', {}).get('event_name', '')
    data       = payload.get('data', {})
    attrs      = data.get('attributes', {})

    _audit({'event': 'webhook_received', 'event_name': event_name})

    # 2. Solo procesar eventos de pago/suscripción confirmada
    PAID_EVENTS = {
        'order_created',
        'subscription_created',
        'subscription_payment_success',
    }
    if event_name not in PAID_EVENTS:
        return jsonify({'status': 'ignored', 'event': event_name}), 200

    status = attrs.get('status', '')
    if status not in ('paid', 'active'):
        return jsonify({'status': 'ignored', 'reason': f'status={status!r}'}), 200

    # 3. Extraer email y nombre del comprador
    customer_email = (
        attrs.get('user_email') or
        attrs.get('email') or
        ''
    ).strip().lower()
    customer_name = (
        attrs.get('user_name') or
        attrs.get('customer_name') or
        ''
    ).strip()

    # Fallback: estructura alternativa de Lemon Squeezy
    if not customer_email:
        try:
            customer_email = (
                data.get('relationships', {})
                    .get('customer', {})
                    .get('data', {})
                    .get('attributes', {})
                    .get('email', '')
            ).strip().lower()
        except Exception:
            pass

    if not customer_email:
        _audit({'event': 'webhook_no_email', 'attr_keys': list(attrs.keys())})
        return jsonify({'error': 'no customer email in payload'}), 400

    # 4. Detectar plan desde nombre de variante / producto
    items = (
        attrs.get('first_order_item') or
        next(iter(attrs.get('order_items', [{}])), {})
    )
    if not isinstance(items, dict):
        items = {}
    variant_name = str(items.get('variant_name', '')).lower()
    product_name = str(items.get('product_name', '')).lower()

    plan  = _detect_plan(variant_name, product_name)
    areas = _areas_for_plan(plan)

    _audit({
        'event':        'payment_confirmed',
        'email':        customer_email,
        'plan':         plan,
        'areas':        areas,
        'lemon_event':  event_name,
        'variant_name': variant_name,
    })

    # 5. Activar cliente en Firebase (gen_portal_key.create_from_payment)
    firebase_ok = False
    try:
        from gen_portal_key import create_from_payment
        create_from_payment(
            email=customer_email,
            plan=plan,
            name=customer_name or None,
        )
        firebase_ok = True
    except Exception as e:
        _audit({
            'event': 'firebase_activation_error',
            'email': customer_email,
            'error': str(e),
        })

    # 6. Notificación WhatsApp al equipo
    try:
        from faro_notifier import notify_payment_confirmed
        notify_payment_confirmed(customer_email, plan, areas)
    except Exception as e:
        _audit({'event': 'whatsapp_notify_error', 'error': str(e)})

    return jsonify({
        'status':   'ok',
        'email':    customer_email,
        'plan':     plan,
        'areas':    areas,
        'firebase': firebase_ok,
    }), 200


@app.route('/health', methods=['GET'])
def health():
    """Health check — verificar que el servidor está activo."""
    return jsonify({
        'status':  'ok',
        'service': 'faro-webhook',
        'ts':      datetime.now(timezone.utc).isoformat(),
    }), 200


@app.route('/', methods=['GET'])
def root():
    return jsonify({'service': 'Faro Protocol Webhook Server', 'version': '1.0'}), 200


# ── Entrypoint ────────────────────────────────────────────────────────────────

if __name__ == '__main__':
    print("=" * 58)
    print("  FARO PROTOCOL — Webhook Server (Lemon Squeezy)")
    print(f"  Endpoint : http://0.0.0.0:{PORT}/webhooks/lemon")
    print(f"  Health   : http://0.0.0.0:{PORT}/health")
    if 'PLACEHOLDER' in LEMON_WEBHOOK_SECRET:
        print("  [WARN] LEMON_WEBHOOK_SECRET sin configurar")
        print("         → verificación de firma DESACTIVADA")
    print("  [INFO] Para recibir webhooks de Lemon Squeezy en local:")
    print("         ngrok http " + str(PORT))
    print("=" * 58)
    app.run(host='0.0.0.0', port=PORT, debug=False)
