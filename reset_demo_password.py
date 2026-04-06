"""
Resetea las credenciales del portal demo y actualiza faro_client_portal.html.
Uso: python reset_demo_password.py
"""
import hashlib, os, re
from pathlib import Path

EMAIL    = 'demo@faroprotocol.io'
PASSWORD = 'faro2026'
PORTAL   = Path(__file__).parent / 'faro_client_portal.html'

salt     = os.urandom(16)
salt_hex = salt.hex()
dk       = hashlib.pbkdf2_hmac('sha256', PASSWORD.encode('utf-8'), salt, 100_000)
hash_hex = dk.hex()

new_block = (
    f"  'demo@faroprotocol.io': {{\n"
    f"    name:'Faro Protocol', plan:'Analyst', initials:'FP',\n"
    f"    salt:'{salt_hex}',\n"
    f"    hash:'{hash_hex}'\n"
    f"  }},"
)

html = PORTAL.read_text(encoding='utf-8')
html = re.sub(
    r"'demo@faroprotocol\.io':\s*\{[^}]+\},",
    new_block,
    html,
    flags=re.DOTALL
)
PORTAL.write_text(html, encoding='utf-8')

print(f"\nListo.")
print(f"  Email    : {EMAIL}")
print(f"  Password : {PASSWORD}")
print(f"  Portal   : faro_client_portal.html actualizado\n")
