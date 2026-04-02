"""
FARO PROTOCOL — Phone Gateway
Alertas WhatsApp via Meta Cloud API para decisiones criticas.
Solo se activa cuando Hermes o Paperclip detectan problemas graves.

Uso:
    python MachinaOS/phone_gateway.py --message "SAR negro en Cordoba" --level critical
    python MachinaOS/phone_gateway.py --message "Pipeline OK" --level info

Desde otro agente:
    from phone_gateway import send_alert
    send_alert("Hermes NO-GO [Cordoba]", "SAR media dB fuera de rango", level="critical")

Requiere en .env:
    WHATSAPP_TOKEN=...
    WHATSAPP_PHONE_ID=...
    WHATSAPP_DEST=+54911...
"""

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path

import requests

# Forzar UTF-8 en Windows
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')
if hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(encoding='utf-8')

PROJECT_ROOT = Path(__file__).parent.parent

WHATSAPP_API_VERSION = "v19.0"
WHATSAPP_API_URL     = f"https://graph.facebook.com/{WHATSAPP_API_VERSION}"

# Prefijos por nivel para identificar urgencia en el mensaje
NIVEL_PREFIJOS = {
    "info":     "[FARO INFO]",
    "warning":  "[FARO WARNING]",
    "critical": "[FARO CRITICAL]",
}


def _cargar_env():
    """Carga .env si existe sin dependencia de python-dotenv."""
    env_file = PROJECT_ROOT / '.env'
    if env_file.exists():
        for linea in env_file.read_text(encoding='utf-8').splitlines():
            linea = linea.strip()
            if linea and not linea.startswith('#') and '=' in linea:
                k, _, v = linea.partition('=')
                os.environ.setdefault(k.strip(), v.strip())


def send_whatsapp(message: str, to: str = None) -> dict:
    """
    Envia un mensaje de texto via WhatsApp Cloud API.

    Parametros:
        message : texto a enviar (max 4096 caracteres)
        to      : numero destino con codigo de pais (ej: +5491122334455)
                  si no se pasa, usa WHATSAPP_DEST del .env

    Retorna:
        dict con 'ok', 'message_id' (si exitoso) o 'error'
    """
    _cargar_env()

    token    = os.environ.get('WHATSAPP_TOKEN', '')
    phone_id = os.environ.get('WHATSAPP_PHONE_ID', '')
    destino  = to or os.environ.get('WHATSAPP_DEST', '')

    if not token or not phone_id or not destino:
        faltantes = [k for k, v in {
            'WHATSAPP_TOKEN': token,
            'WHATSAPP_PHONE_ID': phone_id,
            'WHATSAPP_DEST': destino,
        }.items() if not v]
        return {
            "ok": False,
            "error": f"Variables no configuradas: {', '.join(faltantes)}. Agregalas al .env"
        }

    url     = f"{WHATSAPP_API_URL}/{phone_id}/messages"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type":  "application/json",
    }
    payload = {
        "messaging_product": "whatsapp",
        "to":                destino,
        "type":              "text",
        "text":              {"body": message},
    }

    try:
        response = requests.post(url, headers=headers, json=payload, timeout=10)
        data = response.json()

        if response.status_code == 200 and "messages" in data:
            return {
                "ok":         True,
                "message_id": data["messages"][0].get("id", ""),
                "status":     data["messages"][0].get("message_status", ""),
            }
        else:
            return {
                "ok":    False,
                "error": data.get("error", {}).get("message", str(data)),
                "code":  response.status_code,
            }

    except requests.exceptions.Timeout:
        return {"ok": False, "error": "Timeout al contactar WhatsApp API"}
    except requests.exceptions.ConnectionError:
        return {"ok": False, "error": "Sin conexion a internet o API no alcanzable"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def send_alert(title: str, body: str, level: str = 'warning', to: str = None) -> dict:
    """
    Envia una alerta formateada de Faro Protocol por WhatsApp.

    Parametros:
        title : titulo corto del evento (ej: "Hermes NO-GO [Cordoba]")
        body  : descripcion del problema o accion requerida
        level : 'info' | 'warning' | 'critical'
        to    : numero destino (opcional, usa .env por defecto)

    Solo envia si level es 'warning' o 'critical'.
    Los mensajes 'info' solo se loggean, no generan WhatsApp.
    """
    prefijo    = NIVEL_PREFIJOS.get(level, "[FARO]")
    timestamp  = datetime.now().strftime("%Y-%m-%d %H:%M")
    mensaje    = f"{prefijo}\n{timestamp}\n\n{title}\n\n{body}"

    print(f"[phone_gateway] {prefijo} {title}")

    # Info solo loggea, no envia WhatsApp
    if level == 'info':
        print(f"  [info] Mensaje registrado localmente (no se envia por WhatsApp)")
        return {"ok": True, "sent": False, "reason": "level=info no genera mensaje"}

    resultado = send_whatsapp(mensaje, to=to)

    if resultado["ok"]:
        print(f"  [OK] Alerta enviada. ID: {resultado.get('message_id', 'N/A')}")
    else:
        print(f"  [!!] Error al enviar: {resultado.get('error', 'desconocido')}")

    return resultado


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description='FARO PROTOCOL -- Phone Gateway')
    parser.add_argument('--message', required=True, help='Mensaje a enviar')
    parser.add_argument(
        '--level', default='warning',
        choices=['info', 'warning', 'critical'],
        help='Nivel de alerta (default: warning)'
    )
    parser.add_argument('--to', help='Numero destino (opcional, usa .env por defecto)')
    parser.add_argument('--test', action='store_true', help='Solo muestra el mensaje sin enviarlo')
    args = parser.parse_args()

    if args.test:
        prefijo   = NIVEL_PREFIJOS.get(args.level, "[FARO]")
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
        print("=" * 55)
        print("  FARO PROTOCOL  |  Phone Gateway  |  TEST MODE")
        print("=" * 55)
        print(f"\n{prefijo}\n{timestamp}\n\n{args.message}\n")
        print("  [TEST] No se envio ningun mensaje.")
        return

    print("=" * 55)
    print("  FARO PROTOCOL  |  Phone Gateway")
    print("=" * 55)
    resultado = send_alert(
        title=args.message,
        body="",
        level=args.level,
        to=args.to,
    )

    if not resultado["ok"]:
        sys.exit(1)


if __name__ == '__main__':
    main()
