"""
test_velez_email.py
Envía emails de prueba a los 3 destinatarios Vélez y reporta resultado.
Uso: python test_velez_email.py
"""
import sys, os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / '.env')
sys.path.insert(0, str(Path(__file__).parent))

# Patch env antes de importar el scheduler
os.environ.setdefault('GMAIL_APP_PASS', os.environ.get('GMAIL_APP_PASS', ''))

from faro_velez_scheduler import send_test_emails, EMAIL_CANCHERO, EMAIL_INTENDENTE, EMAIL_COMISION, GMAIL_PASS

print('=== TEST DE ENVÍO — Faro Protocol · Vélez ===\n')
print(f'  Gmail user:   protocolfaro@gmail.com')
print(f'  Canchero:     {EMAIL_CANCHERO or "(no configurado)"}')
print(f'  Intendente:   {EMAIL_INTENDENTE or "(no configurado)"}')
print(f'  Comisión:     {EMAIL_COMISION or "(no configurado)"}')
print(f'  App pass:     {"configurado" if GMAIL_PASS else "FALTA — verificar .env"}\n')

if not GMAIL_PASS:
    print('[ERROR] GMAIL_APP_PASS no está en .env — abortando.')
    sys.exit(1)

results = send_test_emails()

print('\n=== RESULTADO ===')
for dest, ok in results.items():
    icon = '[OK]   ' if ok else '[FAIL] '
    print(f'  {icon}{dest}')

all_ok = all(results.values())
if all_ok:
    print('\nTodos los emails enviados correctamente. Sistema listo para el lunes.')
else:
    failed = [k for k, v in results.items() if not v]
    print(f'\nFallaron: {failed}. Verificar logs en velez_scheduler.log')

sys.exit(0 if all_ok else 1)
