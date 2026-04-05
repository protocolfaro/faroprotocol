#!/usr/bin/env python3
"""
gen_portal_key.py — Faro Protocol portal credential generator
Generates PBKDF2-SHA256 hashes to add clients to faro_client_portal.html

Usage:
    python gen_portal_key.py <email> <password>

Example:
    python gen_portal_key.py cliente@fondo.com MiClaveSegura123

Output: copy-paste block ready to insert in ACCOUNTS dict in the portal HTML.
"""

import sys
import hashlib
import os
import json

def gen_key(email: str, password: str) -> dict:
    salt = os.urandom(16)
    salt_hex = salt.hex()
    dk = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), salt, 100_000)
    hash_hex = dk.hex()
    return {
        'email': email.strip().lower(),
        'salt': salt_hex,
        'hash': hash_hex,
    }

def main():
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(1)

    email = sys.argv[1]
    password = sys.argv[2]

    result = gen_key(email, password)

    initials = ''.join(p[0].upper() for p in email.split('@')[0].split('.')[:2]) or 'XX'
    name_guess = email.split('@')[0].replace('.', ' ').title()

    print()
    print(f"  // Copy this block into ACCOUNTS in faro_client_portal.html")
    print(f"  '{result['email']}': {{")
    print(f"    name:'{name_guess}', plan:'Analyst', initials:'{initials}',")
    print(f"    salt:'{result['salt']}',")
    print(f"    hash:'{result['hash']}'")
    print(f"  }},")
    print()
    print(f"  Algorithm : PBKDF2-SHA256, 100,000 iterations")
    print(f"  Salt      : {result['salt']}")
    print(f"  Hash      : {result['hash']}")
    print()
    print(f"  IMPORTANT: Do NOT store the plaintext password '{password}' anywhere.")
    print(f"  Send the client only: URL + {result['email']} + {password}")
    print(f"  Then discard this terminal session.")
    print()


if __name__ == '__main__':
    main()
