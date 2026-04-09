#!/usr/bin/env python3
"""
faro_admin.py — Faro Protocol: panel de administración en tiempo real
=====================================================================
Muestra clientes activos en Firebase, último login y estado Faro Week.
Usar antes de tocar el portal para verificar que no hay sesiones activas.

Uso:
  python faro_admin.py          # tabla de clientes + sesiones
  python faro_admin.py --watch  # actualiza cada 30s
  python faro_admin.py --safe   # solo indica si es seguro hacer cambios
"""

import os
import sys
import argparse
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

if hasattr(sys.stdout, 'buffer') and (sys.stdout.encoding or '').lower() != 'utf-8':
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent / '.env')

try:
    import firebase_admin
    from firebase_admin import credentials, auth, firestore
except ImportError:
    print('[ERROR] firebase-admin no instalado.')
    print('  Instalar con: pip install firebase-admin')
    sys.exit(1)

# ── Colores ANSI ──────────────────────────────────────────────────────────────
R  = '\033[91m'   # rojo
G  = '\033[92m'   # verde
Y  = '\033[93m'   # amarillo
B  = '\033[94m'   # azul
DIM = '\033[2m'
BOLD = '\033[1m'
RESET = '\033[0m'

SA_PATH = os.getenv('FIREBASE_SERVICE_ACCOUNT', '')
RECENT_THRESHOLD_MINUTES = 30   # sesión "activa" si logueó en los últimos 30 min
WARNING_THRESHOLD_HOURS  = 2    # advertir si logueó en las últimas 2 horas


def _init_firebase():
    if firebase_admin._apps:
        return
    if not SA_PATH or not Path(SA_PATH).exists():
        print(f'{R}[ERROR]{RESET} FIREBASE_SERVICE_ACCOUNT no encontrado: {SA_PATH!r}')
        print('  Configurar en .env: FIREBASE_SERVICE_ACCOUNT=firebase-service-account.json')
        sys.exit(1)
    cred = credentials.Certificate(SA_PATH)
    firebase_admin.initialize_app(cred)


def _ago(dt: datetime) -> str:
    """Retorna string legible de cuánto tiempo pasó desde dt."""
    if dt is None:
        return 'nunca'
    now = datetime.now(timezone.utc)
    delta = now - dt
    s = int(delta.total_seconds())
    if s < 60:
        return f'{s}s atrás'
    if s < 3600:
        return f'{s // 60}min atrás'
    if s < 86400:
        return f'{s // 3600}h atrás'
    return f'{s // 86400}d atrás'


def _expiry_str(expiry) -> str:
    """Formatea la fecha de vencimiento de Faro Week."""
    if expiry is None:
        return f'{DIM}sin Faro Week{RESET}'
    now = datetime.now(timezone.utc)
    if hasattr(expiry, 'tzinfo') and expiry.tzinfo is None:
        expiry = expiry.replace(tzinfo=timezone.utc)
    delta = expiry - now
    days = delta.days
    if days < 0:
        return f'{R}VENCIDO{RESET}'
    if days == 0:
        hours = int(delta.total_seconds() // 3600)
        return f'{R}vence hoy ({hours}h){RESET}'
    if days <= 2:
        return f'{Y}vence en {days}d{RESET}'
    return f'{G}vence en {days}d{RESET}'


def get_clients():
    """Obtiene lista de clientes desde Firestore + metadata de Auth."""
    db = firestore.client()
    docs = db.collection('clients').stream()

    clients = []
    for doc in docs:
        data = doc.to_dict()
        uid  = doc.id

        # Metadata de Firebase Auth (último login, disabled)
        last_login = None
        disabled   = False
        try:
            user = auth.get_user(uid)
            disabled = user.disabled
            if user.user_metadata and user.user_metadata.last_sign_in_time:
                # last_sign_in_time es timestamp en ms
                ts = user.user_metadata.last_sign_in_time
                if isinstance(ts, (int, float)):
                    last_login = datetime.fromtimestamp(ts / 1000, tz=timezone.utc)
        except Exception:
            pass

        clients.append({
            'uid':      uid,
            'email':    data.get('email', '—'),
            'name':     data.get('name', '—'),
            'areas':    data.get('areas', []),
            'status':   data.get('status', '—'),
            'expiry':   data.get('faroWeekExpiry'),
            'last_login': last_login,
            'disabled': disabled,
        })

    clients.sort(key=lambda c: c['last_login'] or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
    return clients


def is_session_active(last_login) -> bool:
    if last_login is None:
        return False
    delta = datetime.now(timezone.utc) - last_login
    return delta.total_seconds() < RECENT_THRESHOLD_MINUTES * 60


def is_session_recent(last_login) -> bool:
    if last_login is None:
        return False
    delta = datetime.now(timezone.utc) - last_login
    return delta.total_seconds() < WARNING_THRESHOLD_HOURS * 3600


def print_dashboard(clients):
    now_str = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')
    print()
    print(f'{BOLD}  FARO PROTOCOL — Panel de administración{RESET}')
    print(f'  {DIM}{now_str}{RESET}')
    print(f'  {"─" * 72}')

    active_count = 0

    if not clients:
        print(f'\n  {DIM}No hay clientes en Firestore.{RESET}\n')
    else:
        print(f'\n  {"CLIENTE":<32} {"ÚLTIMO LOGIN":<18} {"FARO WEEK":<20} {"ÁREAS"}')
        print(f'  {"─" * 72}')
        for c in clients:
            email     = c['email']
            ll        = c['last_login']
            ago_str   = _ago(ll)
            exp_str   = _expiry_str(c['expiry'])
            areas_str = ', '.join(c['areas'][:3]) + ('…' if len(c['areas']) > 3 else '')
            disabled  = c['disabled']

            # Color según actividad
            if disabled:
                row_color = DIM
                status_icon = '[OFF]'
            elif is_session_active(ll):
                row_color = G
                status_icon = '[ACTIVO]'
                active_count += 1
            elif is_session_recent(ll):
                row_color = Y
                status_icon = '[RECIENTE]'
            else:
                row_color = RESET
                status_icon = ''

            print(f'  {row_color}{email:<32} {ago_str:<18}{RESET} {exp_str:<30} {DIM}{areas_str}{RESET}  {row_color}{status_icon}{RESET}')

    print(f'\n  {"─" * 72}')

    # Veredicto de seguridad
    if active_count > 0:
        print(f'\n  {R}{BOLD}⚠  NO SEGURO para cambios — {active_count} sesión(es) activa(s){RESET}')
        print(f'  {DIM}Última actividad hace menos de {RECENT_THRESHOLD_MINUTES} min. Esperar.{RESET}')
    else:
        recent = [c for c in clients if is_session_recent(c['last_login']) and not c['disabled']]
        if recent:
            print(f'\n  {Y}{BOLD}⚡  PRECAUCIÓN — hubo actividad en las últimas {WARNING_THRESHOLD_HOURS}h{RESET}')
            print(f'  {DIM}Sin sesión activa confirmada, pero proceder con cuidado.{RESET}')
        else:
            print(f'\n  {G}{BOLD}✓  SEGURO para hacer cambios en el portal{RESET}')
            print(f'  {DIM}Sin sesiones activas en los últimos {WARNING_THRESHOLD_HOURS}h.{RESET}')

    print()


def main():
    parser = argparse.ArgumentParser(description='Panel de administración Faro Protocol')
    parser.add_argument('--watch',  action='store_true', help='Actualizar cada 30 segundos')
    parser.add_argument('--safe',   action='store_true', help='Solo mostrar si es seguro hacer cambios')
    parser.add_argument('--interval', type=int, default=30, help='Intervalo en segundos para --watch')
    args = parser.parse_args()

    _init_firebase()

    if args.safe:
        clients = get_clients()
        active = [c for c in clients if is_session_active(c['last_login']) and not c['disabled']]
        if active:
            print(f'NO SEGURO — {len(active)} sesión(es) activa(s): {", ".join(c["email"] for c in active)}')
            sys.exit(1)
        else:
            print('SEGURO — sin sesiones activas')
            sys.exit(0)

    if args.watch:
        print(f'  Modo watch — actualizando cada {args.interval}s. Ctrl+C para salir.')
        try:
            while True:
                os.system('cls' if os.name == 'nt' else 'clear')
                clients = get_clients()
                print_dashboard(clients)
                time.sleep(args.interval)
        except KeyboardInterrupt:
            print('\n  Saliendo...')
    else:
        clients = get_clients()
        print_dashboard(clients)


if __name__ == '__main__':
    main()
