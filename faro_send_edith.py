"""
FARO PROTOCOL — Envío de reporte EDITH III & IV por email
Ejecutar: python faro_send_edith.py
"""

import io
import sys
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

import os
import smtplib
from pathlib import Path
from email.message import EmailMessage
from dotenv import load_dotenv

load_dotenv()

GMAIL_USER = os.getenv('GMAIL_USER', 'protocolfaro@gmail.com')
GMAIL_PASS = os.getenv('GMAIL_APP_PASS', '')
TO         = 'angelmaldd@gmail.com'
SUBJECT    = 'Reporte Satelital Certificado — La EDITH III & IV · Puna de Atacama'
PDF_PATH   = Path.home() / 'Desktop' / 'faro_reporte_edith.pdf'

BODY = """\
Estimado Ángel,

Adjunto el Reporte Satelital Minero de La EDITH III & IV, procesado con datos \
reales de Sentinel-1 y Sentinel-2 del 12 de mayo de 2026.

Puntos clave:
— Índice FARO: 57.5 — ALTO POTENCIAL
— Terreno ESTABLE · InSAR: 2.93 mm
— Detección de arcillas y minerales · CLOSDI: 0.227
— Altimetría: 3.812 m s.n.m.
— Comparativa directa con Proyecto 3 Quebradas · Zijin Mining
— Certificado SHA-256 inmutable

Este documento certifica el estado real de las propiedades hoy. \
Válido para due diligence e inversores.

Quedamos a disposición para cualquier consulta.

Emilio Lustrini
Faro Protocol
protocolfaro@gmail.com
"""

def main():
    print("━" * 60)
    print("  FARO PROTOCOL — Envío de reporte satelital")
    print("━" * 60)

    if not GMAIL_PASS:
        print("  ERROR: GMAIL_APP_PASS no encontrado en .env")
        sys.exit(1)

    if not PDF_PATH.exists():
        print(f"  ERROR: PDF no encontrado en {PDF_PATH}")
        sys.exit(1)

    size_kb = PDF_PATH.stat().st_size // 1024
    print(f"  De      : {GMAIL_USER}")
    print(f"  Para    : {TO}")
    print(f"  Asunto  : {SUBJECT}")
    print(f"  Adjunto : {PDF_PATH.name}  ({size_kb} KB)")
    print()

    msg = EmailMessage()
    msg['From']    = f'Faro Protocol <{GMAIL_USER}>'
    msg['To']      = TO
    msg['Subject'] = SUBJECT
    msg.set_content(BODY)

    with open(PDF_PATH, 'rb') as f:
        msg.add_attachment(f.read(),
                           maintype='application',
                           subtype='pdf',
                           filename=PDF_PATH.name)

    print("  → Conectando con smtp.gmail.com:587 ...")
    with smtplib.SMTP('smtp.gmail.com', 587) as smtp:
        smtp.ehlo()
        smtp.starttls()
        smtp.ehlo()
        smtp.login(GMAIL_USER, GMAIL_PASS)
        smtp.send_message(msg)

    print("  ✅  Email enviado correctamente")
    print("━" * 60)


if __name__ == '__main__':
    main()
