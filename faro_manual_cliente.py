#!/usr/bin/env python3
"""
faro_manual_cliente.py — Faro Protocol: Manual de bienvenida (PDF)
==================================================================
TAREA 2 — Manual de Bienvenida para clientes

Genera PDF personalizado por cliente con diseño oscuro premium.
5 secciones + SHA-256 del documento al final.

Uso:
  python faro_manual_cliente.py --email cliente@empresa.com --name "Empresa SA" --areas cordoba,balcarce
  python faro_manual_cliente.py --email cliente@empresa.com  # solo con email

Llamado automático desde gen_portal_key.py con --manual:
  python gen_portal_key.py cliente@empresa.com --areas cordoba --manual

Output:
  faro_manual_<slug>.pdf    — documento principal
  faro_manual_<slug>.sha256 — hash SHA-256 del PDF

Secciones:
  1. Bienvenida — qué es Faro Protocol + áreas asignadas
  2. Cómo leer los datos — Score, FSI, NDVI, SAR, Fusión, Sello Verde
  3. Cómo usar el portal — paso a paso + verificación SHA-256
  4. Glosario técnico — SAR, NDVI, SHA-256, FSI en lenguaje de negocio
  5. Próximos pasos — Faro Week, continuar, planes

Requisito: pip install reportlab
"""

import argparse
import hashlib
import os
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

try:
    from reportlab.pdfgen import canvas as rl_canvas
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.lib.colors import HexColor, Color, white, black
    from reportlab.platypus import (
        SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
        PageBreak, HRFlowable, KeepTogether,
    )
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.enums import TA_LEFT, TA_CENTER, TA_JUSTIFY, TA_RIGHT
    from reportlab.lib.utils import ImageReader
    REPORTLAB_OK = True
except ImportError:
    REPORTLAB_OK = False

try:
    import qrcode as _qrcode
    import io as _qr_io
    QR_OK = True
except ImportError:
    QR_OK = False

# ── Paleta Faro ───────────────────────────────────────────────────────────────

BG      = HexColor('#06080b')       # fondo principal
BG2     = HexColor('#0d1117')       # fondo tarjetas
BG3     = HexColor('#141c24')       # fondo alternativo
GOLD    = HexColor('#c9a84c')       # dorado Faro
GOLD_L  = HexColor('#e2c97e')       # dorado claro
WHITE   = HexColor('#f2ede4')       # texto principal
GREY    = HexColor('#888899')       # texto secundario
GREY_D  = HexColor('#3a3a4a')       # separadores
GREEN   = HexColor('#2d8c5e')       # positivo
AMBER   = HexColor('#c8881a')       # advertencia
RED     = HexColor('#b03030')       # alerta

PW, PH = A4  # 595 x 842 pt

MARGIN_L = 22 * mm
MARGIN_R = 22 * mm
MARGIN_T = 20 * mm
MARGIN_B = 20 * mm
CONTENT_W = PW - MARGIN_L - MARGIN_R

AREA_LABELS = {
    'cordoba':     'Córdoba — Agro (Marcos Juárez)',
    'balcarce':    'Balcarce — Agro',
    'vaca_muerta': 'Vaca Muerta — Energía',
    'rotterdam':   'Rotterdam — Marítimo',
    'permian':     'Permian Basin — Oil & Gas',
    'pilbara':     'Pilbara — Minería de Hierro',
    'amazonas':    'Amazonas — Deforestación',
    'indiana':     'Indiana — Agro',
    'malacca':     'Estrecho de Malacca — Shipping',
}


# ── Canvas helpers ────────────────────────────────────────────────────────────

class FaroManualCanvas:
    """Wrapper sobre reportlab Canvas para el diseño Faro."""

    def __init__(self, path: str):
        self.c = rl_canvas.Canvas(path, pagesize=A4)
        self.c.setTitle('Certified Report Plus — Faro Protocol')
        self.c.setAuthor('Faro Protocol')
        self.c.setSubject('Guía de uso del portal de inteligencia satelital')
        self._page = 0

    # ── Primitivos ────────────────────────────────────────────────────────────

    def fill_rect(self, x, y, w, h, color):
        self.c.setFillColor(color)
        self.c.rect(x, y, w, h, fill=1, stroke=0)

    def stroke_rect(self, x, y, w, h, color, lw=0.5):
        self.c.setStrokeColor(color)
        self.c.setLineWidth(lw)
        self.c.rect(x, y, w, h, fill=0, stroke=1)

    def line(self, x1, y1, x2, y2, color, lw=0.5):
        self.c.setStrokeColor(color)
        self.c.setLineWidth(lw)
        self.c.line(x1, y1, x2, y2)

    def text(self, x, y, t, font='Helvetica', size=10, color=None, align='left'):
        self.c.setFillColor(color or WHITE)
        self.c.setFont(font, size)
        if align == 'center':
            self.c.drawCentredString(x, y, t)
        elif align == 'right':
            self.c.drawRightString(x, y, t)
        else:
            self.c.drawString(x, y, t)

    def text_wrap(self, x, y, w, t, font='Helvetica', size=10, color=None, leading=14):
        """Dibuja texto con wrap manual."""
        self.c.setFillColor(color or WHITE)
        self.c.setFont(font, size)
        words = t.split()
        lines, line = [], []
        for word in words:
            test = ' '.join(line + [word])
            if self.c.stringWidth(test, font, size) <= w:
                line.append(word)
            else:
                if line:
                    lines.append(' '.join(line))
                line = [word]
        if line:
            lines.append(' '.join(line))
        cy = y
        for l in lines:
            self.c.drawString(x, cy, l)
            cy -= leading
        return cy  # y final

    # ── Fondo de página ───────────────────────────────────────────────────────

    def page_background(self):
        # Fondo principal
        self.fill_rect(0, 0, PW, PH, BG)
        # Acento superior: línea dorada
        self.fill_rect(0, PH - 2, PW, 2, GOLD)
        # Grid sutil
        self.c.setStrokeColor(HexColor('#1a1a2e'))
        self.c.setLineWidth(0.3)
        step = 18 * mm
        x = MARGIN_L
        while x < PW - MARGIN_R:
            self.c.line(x, MARGIN_B, x, PH - MARGIN_T)
            x += step

    # ── Header de página interna ──────────────────────────────────────────────

    def page_header(self, section_name: str = ''):
        y = PH - MARGIN_T - 6 * mm
        # Logo pequeño
        self.text(MARGIN_L, y, 'FARO PROTOCOL',
                  font='Helvetica-Bold', size=7,
                  color=HexColor('#3a3a2a'))
        if section_name:
            self.text(PW - MARGIN_R, y, section_name.upper(),
                      font='Helvetica', size=7,
                      color=HexColor('#3a3a2a'), align='right')
        # Línea separadora
        self.line(MARGIN_L, y - 3*mm, PW - MARGIN_R, y - 3*mm,
                  HexColor('#1e1e2e'), lw=0.4)

    # ── Footer de página ──────────────────────────────────────────────────────

    def page_footer(self, page_num: int, total: int = 0):
        y = MARGIN_B
        self.line(MARGIN_L, y + 4*mm, PW - MARGIN_R, y + 4*mm,
                  HexColor('#1e1e2e'), lw=0.4)
        self.text(MARGIN_L, y, 'Confidencial · Uso exclusivo del cliente',
                  font='Helvetica', size=7, color=HexColor('#2a2a3a'))
        pg_str = f'Página {page_num}' + (f' de {total}' if total else '')
        self.text(PW - MARGIN_R, y, pg_str,
                  font='Helvetica', size=7, color=HexColor('#2a2a3a'), align='right')
        # Data source badges — centered
        badges = [('NASA','#0b3d91'),('ESA','#00377b'),('INTA','#1a6b3c'),('INIA','#1a3966')]
        bw, gap = 20, 3
        total_bw = len(badges) * bw + (len(badges) - 1) * gap
        bx = (PW - total_bw) / 2
        for abbr, hx in badges:
            self.fill_rect(bx, y - 1, bw, 8, HexColor(hx))
            self.text(bx + bw / 2, y + 1, abbr,
                      font='Helvetica-Bold', size=5, color=WHITE, align='center')
            bx += bw + gap

    # ── Sección título ────────────────────────────────────────────────────────

    def section_tag(self, x, y, tag: str):
        tw = self.c.stringWidth(tag, 'Helvetica-Bold', 7) + 16
        self.fill_rect(x, y - 3, tw, 13, HexColor('#1a1a0d'))
        self.stroke_rect(x, y - 3, tw, 13, HexColor('#3a3020'), lw=0.4)
        self.text(x + 8, y + 1, tag, font='Helvetica-Bold', size=7, color=GOLD)
        return x + tw + 6

    def section_title(self, x, y, title: str, size: int = 22):
        self.text(x, y, title, font='Helvetica-Bold', size=size, color=WHITE)

    def h2(self, x, y, title: str):
        """Subtítulo de sección."""
        self.text(x, y + 2, title, font='Helvetica-Bold', size=13, color=GOLD_L)
        self.line(x, y - 2, x + CONTENT_W, y - 2, HexColor('#2a2820'), lw=0.5)

    def bullet(self, x, y, text_str: str, color=None, indent=0):
        bx = x + indent
        self.fill_rect(bx, y + 3, 3, 3, color or GOLD)
        self.text(bx + 8, y, text_str, font='Helvetica', size=10, color=WHITE)

    def kv_row(self, x, y, key: str, val: str, key_w: float = 55 * mm):
        self.text(x, y, key, font='Helvetica-Bold', size=9, color=GREY)
        self.text(x + key_w, y, val, font='Helvetica', size=10, color=WHITE)

    def info_box(self, x, y, w, lines: list, title: str = ''):
        h = len(lines) * 14 + (20 if title else 8) + 8
        self.fill_rect(x, y - h + 8, w, h, BG2)
        self.stroke_rect(x, y - h + 8, w, h, HexColor('#2a2820'), lw=0.5)
        # Acento izquierdo dorado
        self.fill_rect(x, y - h + 8, 3, h, GOLD)
        cy = y
        if title:
            self.text(x + 10, cy, title, font='Helvetica-Bold', size=8, color=GOLD)
            cy -= 16
        for line_str in lines:
            self.text(x + 10, cy, line_str, font='Helvetica', size=9, color=WHITE)
            cy -= 13
        return y - h  # y después del box

    def save(self):
        self.c.save()

    def new_page(self):
        self.c.showPage()
        self._page += 1

    # ── Símbolo de satélite ───────────────────────────────────────────────────

    def draw_satellite(self, cx, cy, r=18):
        """Círculo orbital simple como logo."""
        self.c.setStrokeColor(HexColor('#2a2820'))
        self.c.setLineWidth(0.8)
        self.c.circle(cx, cy, r, fill=0, stroke=1)
        self.c.setStrokeColor(HexColor('#1a1810'))
        self.c.setLineWidth(0.4)
        self.c.circle(cx, cy, r * 0.6, fill=0, stroke=1)
        # Punto "satélite"
        self.c.setFillColor(GOLD)
        self.c.circle(cx, cy + r, 3, fill=1, stroke=0)
        # Cruz central
        self.c.setStrokeColor(HexColor('#2a2820'))
        self.c.setLineWidth(0.4)
        self.c.line(cx - r * 0.3, cy, cx + r * 0.3, cy)
        self.c.line(cx, cy - r * 0.3, cx, cy + r * 0.3)


# ── Generador de páginas ──────────────────────────────────────────────────────

def build_cover(m: FaroManualCanvas, name: str, areas: list, expiry: datetime):
    """Página 1: Portada."""
    m.page_background()

    # Franja superior dorada gruesa
    m.fill_rect(0, PH - 8*mm, PW, 8*mm, GOLD)

    # Satélite decorativo
    m.draw_satellite(PW - 55*mm, PH - 60*mm, r=28)

    # Etiqueta
    y = PH - 35*mm
    m.section_tag(MARGIN_L, y, 'CERTIFIED REPORT PLUS · CONFIDENCIAL')

    # Título principal
    y -= 22*mm
    m.text(MARGIN_L, y, 'FARO', font='Helvetica-Bold', size=52, color=WHITE)
    y -= 14*mm
    m.text(MARGIN_L, y, 'PROTOCOL', font='Helvetica-Bold', size=52, color=GOLD)

    # Subtítulo
    y -= 12*mm
    m.text(MARGIN_L, y, 'Satellite Intelligence Platform',
           font='Helvetica', size=14, color=GREY)

    # Línea divisoria
    y -= 10*mm
    m.line(MARGIN_L, y, PW - MARGIN_R, y, GOLD, lw=0.8)

    # Destinatario
    y -= 10*mm
    m.text(MARGIN_L, y, 'Preparado para:', font='Helvetica', size=9, color=GREY)
    y -= 8*mm
    m.text(MARGIN_L, y, name, font='Helvetica-Bold', size=18, color=WHITE)

    # Áreas asignadas
    y -= 14*mm
    m.text(MARGIN_L, y, 'Áreas asignadas:', font='Helvetica-Bold', size=9, color=GOLD)
    y -= 8*mm
    for area in areas:
        label = AREA_LABELS.get(area, area)
        m.bullet(MARGIN_L, y, label)
        y -= 13

    # Período
    y -= 8*mm
    m.fill_rect(MARGIN_L, y - 4*mm, CONTENT_W, 22, HexColor('#0d0e08'))
    m.stroke_rect(MARGIN_L, y - 4*mm, CONTENT_W, 22, HexColor('#2a2820'), lw=0.5)
    m.text(MARGIN_L + 4*mm, y + 2, 'Faro Week',
           font='Helvetica-Bold', size=9, color=GOLD)
    exp_str = expiry.strftime('%d/%m/%Y')
    m.text(MARGIN_L + 38*mm, y + 2, f'Vencimiento: {exp_str}',
           font='Helvetica', size=9, color=WHITE)

    # Footer portada
    y_footer = 30*mm
    m.line(MARGIN_L, y_footer + 4*mm, PW - MARGIN_R, y_footer + 4*mm,
           HexColor('#1e1e2e'), lw=0.4)
    m.text(MARGIN_L, y_footer, '© 2026 Faro Protocol · protocolfaro@gmail.com',
           font='Helvetica', size=8, color=HexColor('#2a2a3a'))
    m.text(MARGIN_L, y_footer - 10, 'Datos satelitales: Copernicus / ESA · CC-BY 4.0',
           font='Helvetica', size=8, color=HexColor('#2a2a3a'))
    m.text(PW - MARGIN_R, y_footer, datetime.now().strftime('%d/%m/%Y'),
           font='Helvetica', size=8, color=HexColor('#2a2a3a'), align='right')

    m.new_page()


def build_section1(m: FaroManualCanvas, name: str, areas: list):
    """Sección 1: Bienvenida."""
    m.page_background()
    m.page_header('Sección 1 · Bienvenida')

    y = PH - MARGIN_T - 20*mm
    m.section_tag(MARGIN_L, y, 'SECCIÓN 1')
    y -= 12*mm
    m.section_title(MARGIN_L, y, 'Bienvenida')

    y -= 14*mm
    m.h2(MARGIN_L, y, '¿Qué es Faro Protocol?')

    y -= 12*mm
    intro_lines = [
        'Faro Protocol es una plataforma de inteligencia satelital que combina imágenes',
        'Sentinel-1 (radar SAR) y Sentinel-2 (óptico) de la Agencia Espacial Europea para',
        'entregar estimaciones verificables de lo que ocurre en el terreno — antes de que',
        'nadie lo publique. Cada dato está sellado con SHA-256 para garantizar inmutabilidad.',
    ]
    for line_str in intro_lines:
        m.text(MARGIN_L, y, line_str, font='Helvetica', size=10, color=WHITE)
        y -= 13

    y -= 6*mm
    m.info_box(MARGIN_L, y, CONTENT_W, [
        'Inteligencia satelital semanal — imágenes procesadas con IA',
        'SHA-256 sellado — cada reporte es verificable e inmutable',
        'Alertas automáticas — te avisamos antes de que salgan los datos oficiales',
    ], title='En tres líneas')
    y -= 80

    y -= 14*mm
    m.h2(MARGIN_L, y, f'Tus áreas asignadas')
    y -= 12*mm

    for area in areas:
        label = AREA_LABELS.get(area, area)
        m.bullet(MARGIN_L, y, label, color=GOLD)
        y -= 13

    y -= 8*mm
    m.text(MARGIN_L, y,
           'Tenés acceso exclusivo a los reportes, datos y alertas de las áreas listadas.',
           font='Helvetica', size=10, color=GREY)
    y -= 13
    m.text(MARGIN_L, y,
           'Los datos de otras áreas no son visibles desde tu cuenta.',
           font='Helvetica', size=10, color=GREY)

    m.page_footer(2)
    m.new_page()


def build_section2(m: FaroManualCanvas):
    """Sección 2: Cómo leer los datos."""
    m.page_background()
    m.page_header('Sección 2 · Cómo leer los datos')

    y = PH - MARGIN_T - 20*mm
    m.section_tag(MARGIN_L, y, 'SECCIÓN 2')
    y -= 12*mm
    m.section_title(MARGIN_L, y, 'Cómo leer los datos')

    indicators = [
        ('Score Faro', '0–100',
         'El número maestro. Integra todos los sensores satelitales en un único índice.',
         'Por encima de 50 es zona sana. Por debajo de 50 es ALERTA — algo cambió.',
         GREEN),
        ('FSI · Índice de Estrés Faro', 'pts',
         'Mide estrés hídrico, biológico o estructural según el sector.',
         '0 = sin estrés. 100 = estrés crítico. Agro: FSI > 40 → riesgo de pérdida de rinde.',
         AMBER),
        ('NDVI · Vegetación', '0.0 – 1.0',
         'Cuánta vegetación activa hay. NDVI > 0.5 = campo verde y vigoroso.',
         'NDVI < 0.3 = suelo seco o cultivo en estrés. NDVI < 0.15 = zona crítica.',
         GREEN),
        ('SAR · Radar', 'dB',
         'Imagen de microondas que atraviesa nubes. Ve humedad del suelo, estructuras.',
         'SAR < -15 dB en zona agrícola → déficit hídrico severo.',
         GOLD),
        ('Índice Fusión', '0.0 – 1.0',
         'Combina SAR + NDVI. Más robusto que cualquier sensor individual.',
         'Fusión > 0.6 = zona bien monitoreada con alta confianza.',
         GOLD_L),
    ]

    for name_i, unit, desc1, desc2, color in indicators:
        if y < 60*mm:
            m.page_footer(3)
            m.new_page()
            m.page_background()
            m.page_header('Sección 2 · Cómo leer los datos')
            y = PH - MARGIN_T - 20*mm

        # Box por indicador
        box_h = 64
        m.fill_rect(MARGIN_L, y - box_h + 10, CONTENT_W, box_h, BG2)
        m.stroke_rect(MARGIN_L, y - box_h + 10, CONTENT_W, box_h, HexColor('#1e1e2e'), lw=0.4)
        m.fill_rect(MARGIN_L, y - box_h + 10, 3, box_h, color)

        m.text(MARGIN_L + 10, y, name_i, font='Helvetica-Bold', size=11, color=color)
        m.text(PW - MARGIN_R - 20, y, unit, font='Helvetica', size=9, color=GREY, align='right')
        m.text(MARGIN_L + 10, y - 15, desc1, font='Helvetica', size=9, color=WHITE)
        m.text(MARGIN_L + 10, y - 29, desc2, font='Helvetica', size=8, color=GREY)

        y -= box_h + 14

    # Sello Verde
    y -= 4*mm
    m.h2(MARGIN_L, y, 'El Sello Verde')
    y -= 12*mm
    m.text(MARGIN_L, y,
           'El Sello Verde se emite cuando un área tiene Score ≥ 50 y sin ALERTA activa.',
           font='Helvetica', size=10, color=WHITE)
    y -= 13
    m.text(MARGIN_L, y,
           'Incluye SHA-256 único — se puede verificar en cualquier momento que el dato',
           font='Helvetica', size=10, color=WHITE)
    y -= 13
    m.text(MARGIN_L, y,
           'no fue modificado después de su emisión.',
           font='Helvetica', size=10, color=WHITE)

    m.page_footer(3)
    m.new_page()


def build_section3(m: FaroManualCanvas, portal_url: str):
    """Sección 3: Cómo usar el portal."""
    m.page_background()
    m.page_header('Sección 3 · Cómo usar el portal')

    y = PH - MARGIN_T - 20*mm
    m.section_tag(MARGIN_L, y, 'SECCIÓN 3')
    y -= 12*mm
    m.section_title(MARGIN_L, y, 'Cómo usar el portal')

    steps = [
        ('01', 'Ingresar al portal',
         f'{portal_url}/faro_client_portal.html',
         'Usá tu email y la contraseña que configuraste al activar tu cuenta.'),
        ('02', 'Explorar el Dashboard',
         'Tab "Overview"',
         'Ves el Score Faro, NDVI, rinde estimado y ROI de tus áreas en tiempo real.'),
        ('03', 'Ver sectores en detalle',
         'Tab "Live Sectors"',
         'Cada área tiene sus métricas completas: SAR, NDVI, Fusión, alertas activas.'),
        ('04', 'Descargar un reporte',
         'Tab "Intelligence Reports" → botón ↓ PNG + SHA-256',
         'Recibirás un link de descarga seguro válido por 1 hora. El PNG incluye SHA-256.'),
        ('05', 'Verificar SHA-256 de forma independiente',
         'Terminal: sha256sum faro_reporte_fusion_<area>.png',
         'Compará el resultado con el hash que aparece en el reporte. Si coincide = íntegro.'),
        ('06', 'Interpretar una alerta',
         'Notificación por email o badge rojo en el portal',
         'ALERTA = variación estadística relevante detectada. Revisá el contexto con campo.'),
    ]

    for num, title_s, detail, desc in steps:
        if y < 50*mm:
            m.page_footer(4)
            m.new_page()
            m.page_background()
            m.page_header('Sección 3 · Cómo usar el portal')
            y = PH - MARGIN_T - 20*mm

        # Número de paso
        m.fill_rect(MARGIN_L, y - 4, 18, 18, GOLD)
        m.text(MARGIN_L + 4, y, num, font='Helvetica-Bold', size=9, color=BG)

        # Contenido
        m.text(MARGIN_L + 24, y + 2, title_s, font='Helvetica-Bold', size=11, color=WHITE)
        m.text(MARGIN_L + 24, y - 10, detail, font='Helvetica-Oblique', size=8, color=GOLD)
        m.text(MARGIN_L + 24, y - 22, desc, font='Helvetica', size=9, color=GREY)
        m.line(MARGIN_L, y - 30, PW - MARGIN_R, y - 30, HexColor('#1a1a2a'), lw=0.3)
        y -= 38

    m.page_footer(4)
    m.new_page()


def build_section4(m: FaroManualCanvas):
    """Sección 4: Glosario técnico en lenguaje de negocio."""
    m.page_background()
    m.page_header('Sección 4 · Glosario')

    y = PH - MARGIN_T - 20*mm
    m.section_tag(MARGIN_L, y, 'SECCIÓN 4')
    y -= 12*mm
    m.section_title(MARGIN_L, y, 'Glosario técnico')

    y -= 8*mm
    m.text(MARGIN_L, y,
           'Los términos técnicos de Faro Protocol explicados en lenguaje de negocio.',
           font='Helvetica', size=10, color=GREY)

    terms = [
        ('SAR',
         'Synthetic Aperture Radar — radar de apertura sintética',
         'Un sensor que manda ondas de microondas y mide cómo rebotan. '
         'Ve a través de nubes, niebla y de noche. '
         'Para vos significa: datos todos los días, llueva o no.',
         'Agro: detecta humedad del suelo. Maritimo: cuenta barcos. O&G: ve actividad en pozos.'),

        ('NDVI',
         'Normalized Difference Vegetation Index',
         'Mide cuánta vegetación activa hay en un área usando luz infrarroja. '
         'Un campo de soja sano tiene NDVI alto; uno estresado o cosechado, bajo. '
         'Para vos: es el "semáforo" del estado del cultivo o la cobertura forestal.',
         'NDVI > 0.5 = verde y sano. NDVI < 0.3 = estrés o pérdida de cobertura.'),

        ('SHA-256',
         'Secure Hash Algorithm 256 bits',
         'Una "huella digital" matemática del documento. Si un solo píxel cambia, '
         'el hash cambia completamente. Para vos: certifica que el reporte que '
         'ves hoy es exactamente el que Faro generó en la fecha indicada. '
         'Nadie puede modificarlo sin que se note.',
         'Verificación: sha256sum faro_reporte_cordoba.png → debe coincidir con el hash del portal.'),

        ('FSI',
         'Faro Stress Index — Índice de Estrés Faro',
         'Modelo propio que combina múltiples variables (SAR, NDVI, histórico, clima) '
         'para estimar el nivel de estrés de un activo. '
         'Para vos: es una señal de alerta temprana. FSI alto = actuar antes de que el daño sea visible.',
         'Agro: FSI > 40 → riesgo de pérdida de rinde. Forestal: FSI > 50 → deforestación activa.'),

        ('Índice Fusión',
         'SAR + NDVI + modelos de IA',
         'Combina lo mejor de radar y óptico. Más robusto que cualquier sensor solo. '
         'Para vos: cuando hay nubes (el NDVI no funciona) usamos SAR. '
         'Cuando hay interferencias de radar, usamos NDVI. Siempre tenés un dato.',
         'Fusión > 0.6 = alta confianza en el dato. < 0.4 = revisar con dato de campo.'),

        ('Score Faro',
         'Indicador maestro 0-100',
         'Un único número que resume todo lo que Faro sabe de tu área. '
         'Facilita la decisión rápida: por encima de 50 es zona sana, '
         'por debajo es señal de alerta. '
         'Diseñado para ejecutivos que no quieren leer tablas complejas.',
         'Score < 50 → ALERTA activa. Score ≥ 70 + sin FSI → candidato a Sello Verde.'),
    ]

    for term, technical, biz, example in terms:
        if y < 50*mm:
            m.page_footer(5)
            m.new_page()
            m.page_background()
            m.page_header('Sección 4 · Glosario')
            y = PH - MARGIN_T - 20*mm

        # Box por término
        box_h = 68
        m.fill_rect(MARGIN_L, y - box_h + 10, CONTENT_W, box_h, BG2)
        m.stroke_rect(MARGIN_L, y - box_h + 10, CONTENT_W, box_h, HexColor('#1e1e2e'), lw=0.4)
        m.fill_rect(MARGIN_L, y - box_h + 10, 3, box_h, GOLD)

        m.text(MARGIN_L + 10, y, term, font='Helvetica-Bold', size=13, color=GOLD_L)
        m.text(MARGIN_L + 10 + m.c.stringWidth(term, 'Helvetica-Bold', 13) + 8,
               y, technical, font='Helvetica-Oblique', size=8, color=GREY)
        y2 = m.text_wrap(MARGIN_L + 10, y - 13, CONTENT_W - 20, biz,
                         font='Helvetica', size=9, color=WHITE, leading=12)
        m.text(MARGIN_L + 10, y2 - 4, f'Ejemplo: {example}',
               font='Helvetica', size=8, color=GREY)

        y -= box_h + 6

    m.page_footer(5)
    m.new_page()


def build_section5(m: FaroManualCanvas, name: str, areas: list,
                   expiry: datetime, portal_url: str):
    """Sección 5: Próximos pasos."""
    m.page_background()
    m.page_header('Sección 5 · Próximos pasos')

    y = PH - MARGIN_T - 20*mm
    m.section_tag(MARGIN_L, y, 'SECCIÓN 5')
    y -= 12*mm
    m.section_title(MARGIN_L, y, 'Próximos pasos')

    # Faro Week countdown
    y -= 14*mm
    m.h2(MARGIN_L, y, 'Tu Faro Week')
    y -= 12*mm
    m.fill_rect(MARGIN_L, y - 8*mm, CONTENT_W, 32, HexColor('#0d0e08'))
    m.stroke_rect(MARGIN_L, y - 8*mm, CONTENT_W, 32, HexColor('#2a2010'), lw=0.5)
    m.fill_rect(MARGIN_L, y - 8*mm, 3, 32, AMBER)
    m.text(MARGIN_L + 8, y, 'Tu Faro Week vence el:',
           font='Helvetica', size=10, color=GREY)
    m.text(MARGIN_L + 60*mm, y, expiry.strftime('%d de %B de %Y'),
           font='Helvetica-Bold', size=12, color=AMBER)
    m.text(MARGIN_L + 8, y - 14, '24 horas antes recibirás un email de aviso automático.',
           font='Helvetica', size=9, color=GREY)
    y -= 8*mm + 20

    # Checklist
    y -= 8*mm
    m.h2(MARGIN_L, y, 'Qué hacer durante tu Faro Week')
    y -= 12*mm
    checklist = [
        'Explorá el Dashboard con los datos de tus áreas',
        'Descargá al menos un reporte y verificá el SHA-256',
        'Revisá las alertas activas y compará con datos de campo',
        'Analizá el Score Faro y el FSI para cada área',
        'Evaluá si los datos son útiles para tu toma de decisiones',
    ]
    for item in checklist:
        m.fill_rect(MARGIN_L, y + 3, 3, 3, GOLD)
        m.text(MARGIN_L + 8, y, '☐  ' + item, font='Helvetica', size=10, color=WHITE)
        y -= 14

    # Si querés continuar
    y -= 8*mm
    m.h2(MARGIN_L, y, 'Si querés continuar')
    y -= 12*mm

    m.text(MARGIN_L, y,
           'Escribinos antes del vencimiento para no perder continuidad en los datos:',
           font='Helvetica', size=10, color=WHITE)
    y -= 14

    m.fill_rect(MARGIN_L, y - 6*mm, CONTENT_W, 28, HexColor('#0d0e0a'))
    m.stroke_rect(MARGIN_L, y - 6*mm, CONTENT_W, 28, HexColor('#1a2a1a'), lw=0.5)
    m.fill_rect(MARGIN_L, y - 6*mm, 3, 28, GREEN)
    m.text(MARGIN_L + 8, y, 'Email: protocolfaro@gmail.com',
           font='Helvetica-Bold', size=11, color=WHITE)
    m.text(MARGIN_L + 8, y - 13, 'Asunto: "Quiero continuar con Faro Protocol"',
           font='Helvetica', size=9, color=GREY)
    y -= 6*mm + 18

    # Planes
    y -= 10*mm
    m.h2(MARGIN_L, y, 'Planes disponibles')
    y -= 12*mm

    planes = [
        ('Observer',   'USD 2.500/mes',       '1 área · Reporte semanal · SHA-256 · Portal',                   False),
        ('Analyst',    'USD 9.000/mes',        '3 áreas · Alertas tiempo real · Dashboard · API básica',        True),
        ('Sovereign',  'USD 17.000/mes',       'Áreas ilimitadas · API completa · White label · SLA',           False),
        ('Enterprise', 'USD 3.200/sector/mes', 'Multi-sector · White label · SLA dedicado · Integración',       False),
    ]
    col_w = CONTENT_W / 4

    for i, (plan_name, price, features, is_rec) in enumerate(planes):
        px = MARGIN_L + i * col_w
        box_color = HexColor('#0d1410') if is_rec else BG2
        border_color = GOLD if is_rec else HexColor('#1e1e2e')
        m.fill_rect(px + 2, y - 46, col_w - 4, 58, box_color)
        m.stroke_rect(px + 2, y - 46, col_w - 4, 58, border_color, lw=0.8 if is_rec else 0.4)
        if is_rec:
            m.fill_rect(px + 2, y + 12, col_w - 4, 14, GOLD)
            m.text(px + col_w / 2 + 2, y + 16, 'MÁS POPULAR',
                   font='Helvetica-Bold', size=7, color=BG, align='center')
        m.text(px + col_w / 2 + 2, y, plan_name,
               font='Helvetica-Bold', size=10, color=GOLD_L, align='center')
        m.text(px + col_w / 2 + 2, y - 13, price,
               font='Helvetica-Bold', size=8, color=WHITE, align='center')
        # Features
        cy = y - 26
        for feat in features.split(' · '):
            m.text(px + col_w / 2 + 2, cy, feat,
                   font='Helvetica', size=6, color=GREY, align='center')
            cy -= 9

    m.page_footer(6)
    m.new_page()


def build_section_opendata(m: FaroManualCanvas, opendata: dict, page_num: int = 7):
    """Open Data page: NDVI Benchmark (bar chart), Flood Risk, Water Balance."""
    m.page_background()
    m.page_header('Open Data · Certificación Detallada')

    y = PH - MARGIN_T - 20*mm
    m.section_tag(MARGIN_L, y, 'OPEN DATA · CERTIFIED REPORT PLUS')
    y -= 12*mm
    m.section_title(MARGIN_L, y, 'Certificación Detallada')

    y -= 8*mm
    m.text(MARGIN_L, y,
           'Módulos de datos abiertos integrados al reporte y firmados en el hash SHA-256.',
           font='Helvetica', size=10, color=GREY)
    y -= 16*mm

    nb = opendata.get('ndvi_benchmark')
    fr = opendata.get('flood_risk_srtm')
    wb = opendata.get('water_balance_inia')

    # ── OD-1: NDVI Benchmark bar chart ────────────────────────────────────────
    if nb:
        m.h2(MARGIN_L, y, 'OD-1 — NDVI vs Media Regional (SIIA · siia.gov.ar)')
        y -= 14*mm

        ndvi_act = float(nb.get('ndvi_actual', 0))
        ndvi_reg = float(nb.get('media_regional', 0))
        delta    = float(nb.get('delta', 0))
        bar_max  = CONTENT_W * 0.68
        bar_h    = 13

        # Reference bar
        reg_w = bar_max * min(ndvi_reg, 1.0)
        m.text(MARGIN_L, y, 'Media regional', font='Helvetica', size=9, color=GREY)
        y -= 16
        m.fill_rect(MARGIN_L, y, reg_w, bar_h, HexColor('#2a2820'))
        m.stroke_rect(MARGIN_L, y, reg_w, bar_h, HexColor('#3a3830'), lw=0.3)
        m.text(MARGIN_L + reg_w + 5, y + 2,
               f'{ndvi_reg:.4f}', font='Helvetica-Bold', size=9, color=GREY)
        y -= 20

        # Actual bar
        act_color = GREEN if delta >= 0 else RED
        act_w = bar_max * min(ndvi_act, 1.0)
        m.text(MARGIN_L, y, 'NDVI actual (lote)', font='Helvetica', size=9, color=WHITE)
        y -= 16
        m.fill_rect(MARGIN_L, y, act_w, bar_h, act_color)
        m.text(MARGIN_L + act_w + 5, y + 2,
               f'{ndvi_act:.4f}', font='Helvetica-Bold', size=9, color=act_color)
        y -= 20

        # Delta box
        sign = '+' if delta >= 0 else ''
        m.fill_rect(MARGIN_L, y - 4, CONTENT_W, 20, BG2)
        m.stroke_rect(MARGIN_L, y - 4, CONTENT_W, 20, HexColor('#1e1e2e'), lw=0.4)
        m.fill_rect(MARGIN_L, y - 4, 3, 20, act_color)
        m.text(MARGIN_L + 10, y + 4, f'Δ vs región: {sign}{delta:.4f}',
               font='Helvetica-Bold', size=10, color=act_color)
        m.text(PW - MARGIN_R, y + 4, 'Fuente: SIIA · siia.gov.ar',
               font='Helvetica', size=7, color=GREY, align='right')
        y -= 36

    # ── OD-2: Flood Risk SRTM ─────────────────────────────────────────────────
    if fr:
        if y < 80*mm:
            m.page_footer(page_num); m.new_page(); page_num += 1
            m.page_background(); m.page_header('Open Data · Certificación Detallada')
            y = PH - MARGIN_T - 20*mm

        y -= 12*mm
        m.h2(MARGIN_L, y, 'OD-2 — Riesgo de Inundación SRTM (opentopodata.org)')
        y -= 14*mm

        risk      = fr.get('flood_risk', 'low')
        slope     = float(fr.get('slope_avg', 0))
        risk_color = RED if risk == 'high' else GREEN
        risk_label = 'ALTO RIESGO' if risk == 'high' else 'BAJO RIESGO'
        risk_desc  = ('Pendiente < 1 % — terreno plano, posible acumulación de agua.'
                      if risk == 'high' else
                      'Pendiente suficiente para drenaje natural — riesgo bajo.')

        badge_w = 72*mm
        m.fill_rect(MARGIN_L, y - 36, badge_w, 44, risk_color)
        m.text(MARGIN_L + badge_w / 2, y - 8,  risk_label,
               font='Helvetica-Bold', size=13, color=WHITE, align='center')
        m.text(MARGIN_L + badge_w / 2, y - 24, f'Pendiente: {slope:.3f} %',
               font='Helvetica', size=9, color=WHITE, align='center')

        tx = MARGIN_L + badge_w + 10
        m.text(tx, y,      risk_desc,                    font='Helvetica', size=9, color=WHITE)
        m.text(tx, y - 14, 'Modelo: SRTM 30m · NASA / USGS', font='Helvetica', size=8, color=GREY)
        m.text(tx, y - 26, 'API: opentopodata.org',      font='Helvetica', size=8, color=GREY)
        y -= 54

    # ── OD-3: Water Balance INIA ──────────────────────────────────────────────
    if wb:
        if y < 70*mm:
            m.page_footer(page_num); m.new_page(); page_num += 1
            m.page_background(); m.page_header('Open Data · Certificación Detallada')
            y = PH - MARGIN_T - 20*mm

        y -= 12*mm
        m.h2(MARGIN_L, y, 'OD-3 — Balance Hídrico IBH (INIA GRAS · gras.inia.uy)')
        y -= 14*mm

        ibh    = float(wb.get('ibh', 0))
        status = wb.get('water_status', 'ok')
        s_map  = {
            'ok':      (GREEN, 'NORMAL',  'Balance hídrico favorable — sin estrés significativo.'),
            'stress':  (AMBER, 'ESTRÉS',  'Balance hídrico en estrés — riesgo de impacto en cultivos.'),
            'deficit': (RED,   'DÉFICIT', 'Déficit hídrico severo — intervención recomendada.'),
        }
        s_color, s_label, s_desc = s_map.get(status, (GREY, status.upper(), ''))

        m.fill_rect(MARGIN_L, y - 36, CONTENT_W, 44, BG2)
        m.stroke_rect(MARGIN_L, y - 36, CONTENT_W, 44, HexColor('#1e1e2e'), lw=0.4)
        m.fill_rect(MARGIN_L, y - 36, 5, 44, s_color)
        m.text(MARGIN_L + 14, y,      f'IBH: {ibh}',    font='Helvetica-Bold', size=14, color=s_color)
        m.text(MARGIN_L + 14, y - 16, s_label,          font='Helvetica-Bold', size=10, color=WHITE)
        m.text(MARGIN_L + 14, y - 28, s_desc,           font='Helvetica', size=9, color=GREY)
        m.text(PW - MARGIN_R, y,      'Fuente: INIA GRAS · gras.inia.uy',
               font='Helvetica', size=7, color=GREY, align='right')

    m.page_footer(page_num)
    m.new_page()


def build_sha_page(m: FaroManualCanvas, sha: str, filename: str, page_num: int = 8):
    """Página final: SHA-256 + QR de verificación."""
    m.page_background()
    m.page_header('Verificación · SHA-256')

    y = PH / 2 + 40*mm
    m.section_tag(MARGIN_L, y, 'VERIFICACIÓN DE INTEGRIDAD')
    y -= 14*mm
    m.text(MARGIN_L, y, 'SHA-256 de este documento',
           font='Helvetica-Bold', size=18, color=WHITE)
    y -= 10*mm
    m.text(MARGIN_L, y,
           'El hash SHA-256 a continuación certifica la integridad de este manual.',
           font='Helvetica', size=10, color=GREY)
    y -= 8
    m.text(MARGIN_L, y,
           'Si el archivo fue modificado en cualquier forma, el hash no coincidirá.',
           font='Helvetica', size=10, color=GREY)

    y -= 14*mm
    # Box del hash — deja espacio a la derecha para el QR
    hash_box_w = CONTENT_W - 52*mm
    m.fill_rect(MARGIN_L, y - 12*mm, hash_box_w, 36, BG2)
    m.stroke_rect(MARGIN_L, y - 12*mm, hash_box_w, 36, GOLD, lw=0.6)
    m.text(MARGIN_L + 6, y, 'SHA-256:', font='Helvetica-Bold', size=8, color=GOLD)
    m.text(MARGIN_L + 6, y - 12, sha[:32], font='Courier-Bold', size=9, color=WHITE)
    m.text(MARGIN_L + 6, y - 24, sha[32:], font='Courier-Bold', size=9, color=WHITE)

    # QR code
    qr_x = MARGIN_L + hash_box_w + 6*mm
    qr_y = y - 12*mm
    qr_size = 44*mm
    if QR_OK:
        try:
            verify_url = f"https://faro-protocol.netlify.app/verify?sha256={sha}"
            qr = _qrcode.QRCode(version=1, box_size=5, border=2,
                                 error_correction=_qrcode.constants.ERROR_CORRECT_M)
            qr.add_data(verify_url)
            qr.make(fit=True)
            img = qr.make_image(fill_color=(10, 10, 10), back_color=(242, 237, 228))
            buf = _qr_io.BytesIO()
            img.save(buf, 'PNG')
            buf.seek(0)
            m.c.drawImage(ImageReader(buf), qr_x, qr_y, width=qr_size, height=qr_size)
            m.text(qr_x + qr_size / 2, qr_y - 8, 'Verificar →',
                   font='Helvetica', size=7, color=GREY, align='center')
        except Exception:
            m.stroke_rect(qr_x, qr_y, qr_size, qr_size, HexColor('#2a2820'), lw=0.5)
            m.text(qr_x + qr_size / 2, qr_y + qr_size / 2, 'QR',
                   font='Helvetica', size=8, color=GREY, align='center')
    else:
        m.stroke_rect(qr_x, qr_y, qr_size, qr_size, HexColor('#2a2820'), lw=0.5)
        m.text(qr_x + qr_size / 2, qr_y + qr_size / 2 + 4, 'pip install',
               font='Helvetica', size=6, color=GREY, align='center')
        m.text(qr_x + qr_size / 2, qr_y + qr_size / 2 - 6, 'qrcode[pil]',
               font='Courier', size=6, color=GOLD, align='center')

    y -= 12*mm + 24
    m.text(MARGIN_L, y, 'Archivo: ' + filename,
           font='Courier', size=9, color=GREY)
    y -= 12
    m.text(MARGIN_L, y, 'Generado: ' + datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC'),
           font='Helvetica', size=9, color=GREY)

    y -= 14*mm
    m.text(MARGIN_L, y, 'Cómo verificar:',
           font='Helvetica-Bold', size=10, color=WHITE)
    y -= 12
    m.text(MARGIN_L + 4, y, '1. Descargá el archivo PDF',
           font='Helvetica', size=9, color=GREY)
    y -= 11
    m.text(MARGIN_L + 4, y, '2. Abrí una terminal y ejecutá:',
           font='Helvetica', size=9, color=GREY)
    y -= 11
    m.fill_rect(MARGIN_L + 4, y - 4, CONTENT_W - 4, 16, HexColor('#0a0a14'))
    m.text(MARGIN_L + 8, y, f'sha256sum {filename}',
           font='Courier', size=9, color=GOLD)
    y -= 20
    m.text(MARGIN_L + 4, y, '3. Comparás el resultado con el hash de arriba.',
           font='Helvetica', size=9, color=GREY)
    y -= 11
    m.text(MARGIN_L + 4, y, '   Si coincide: el documento es auténtico e íntegro.',
           font='Helvetica', size=9, color=GREEN)

    m.page_footer(page_num)
    m.save()


# ── Función pública: generate_manual ─────────────────────────────────────────

def generate_manual(
    email: str,
    name: str = None,
    areas: list = None,
    output_dir: str = None,
    portal_url: str = None,
    opendata: dict = None,
) -> str:
    """
    Genera el PDF del manual de bienvenida.
    Retorna la ruta al archivo PDF generado.
    """
    if not REPORTLAB_OK:
        print("[ERROR] reportlab no está instalado.")
        print("  pip install reportlab")
        raise ImportError("reportlab requerido")

    # Defaults
    email      = email.strip().lower()
    name       = name or email.split('@')[0].replace('.', ' ').title()
    areas      = areas or ['cordoba']
    portal_url = portal_url or os.getenv('PORTAL_URL', 'https://faro-protocol.netlify.app')
    expiry     = datetime.now(timezone.utc) + timedelta(days=7)

    # Nombre de archivo
    slug = email.split('@')[0].replace('.', '_').replace('+', '_')[:30]
    out_dir = Path(output_dir) if output_dir else Path(__file__).parent
    pdf_path = out_dir / f'faro_manual_{slug}.pdf'
    sha_path = out_dir / f'faro_manual_{slug}.sha256'

    print(f"[MANUAL] Generando PDF para: {name} ({email})")
    print(f"  Áreas:  {', '.join(areas)}")
    print(f"  Output: {pdf_path}")

    has_od   = bool(opendata and any(opendata.values()))
    od_page  = 7   # página de open data (solo si hay datos)
    sha_page = od_page + 1 if has_od else 7

    def _build_pages(canvas_obj, include_sha: bool, sha_val: str = ''):
        build_cover(canvas_obj, name, areas, expiry)
        build_section1(canvas_obj, name, areas)
        build_section2(canvas_obj)
        build_section3(canvas_obj, portal_url)
        build_section4(canvas_obj)
        build_section5(canvas_obj, name, areas, expiry, portal_url)
        if has_od:
            build_section_opendata(canvas_obj, opendata, page_num=od_page)
        if include_sha:
            build_sha_page(canvas_obj, sha_val, pdf_path.name, page_num=sha_page)

    # ── Primera pasada: generar PDF sin hash (para calcular SHA-256) ──────────
    tmp_path = out_dir / f'faro_manual_{slug}_tmp.pdf'
    m = FaroManualCanvas(str(tmp_path))
    _build_pages(m, include_sha=False)
    m.c.save()

    # ── Calcular SHA-256 de la versión sin página de hash ─────────────────────
    sha256 = hashlib.sha256(tmp_path.read_bytes()).hexdigest()
    tmp_path.unlink()  # eliminar archivo temporal

    # ── Segunda pasada: generar PDF final con SHA-256 incluido ───────────────
    m2 = FaroManualCanvas(str(pdf_path))
    _build_pages(m2, include_sha=True, sha_val=sha256)

    # SHA-256 del PDF final
    final_sha = hashlib.sha256(pdf_path.read_bytes()).hexdigest()
    sha_path.write_text(f'{final_sha}  {pdf_path.name}\n', encoding='utf-8')

    print(f"  SHA-256 (cuerpo): {sha256}")
    print(f"  SHA-256 (final):  {final_sha}")
    print(f"  OK Manual generado: {pdf_path}")
    print(f"  OK Hash guardado:   {sha_path}")

    return str(pdf_path)


# ── CLI ───────────────────────────────────────────────────────────────────────

def main():
    if not REPORTLAB_OK:
        print("[ERROR] reportlab no instalado.")
        print("  pip install reportlab")
        sys.exit(1)

    parser = argparse.ArgumentParser(
        description='Faro Protocol — Generador de Manual de Bienvenida (PDF)',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument('--email', required=True,
                        help='Email del cliente')
    parser.add_argument('--name',
                        help='Nombre de la empresa o persona')
    parser.add_argument('--areas', default='cordoba',
                        help='Áreas separadas por coma (default: cordoba)')
    parser.add_argument('--output', default='.',
                        help='Directorio de salida (default: directorio actual)')
    parser.add_argument('--portal-url',
                        default=os.getenv('PORTAL_URL', 'https://faro-protocol.netlify.app'),
                        help='URL del portal Faro')

    args = parser.parse_args()

    areas = [a.strip() for a in args.areas.split(',') if a.strip()]
    generate_manual(
        email=args.email,
        name=args.name,
        areas=areas,
        output_dir=args.output,
        portal_url=args.portal_url,
    )


if __name__ == '__main__':
    main()
