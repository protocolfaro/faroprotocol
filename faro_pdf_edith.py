"""
FARO PROTOCOL — Generador PDF profesional para La EDITH III & IV
Portada + reporte satelital (PNG) en un único PDF.
Ejecutar: python faro_pdf_edith.py
Output:   ~/Desktop/faro_reporte_edith.pdf
"""

import io
import sys
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

from pathlib import Path
from PIL import Image
from reportlab.pdfgen import canvas
from reportlab.lib.units import inch, mm
from reportlab.lib.colors import HexColor, Color
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

# ── Rutas ─────────────────────────────────────────────────────────────────────
PNG_IN  = Path.home() / 'Desktop' / 'faro_reporte_edith.png'
PDF_OUT = Path.home() / 'Desktop' / 'faro_reporte_edith.pdf'

# ── Paleta ────────────────────────────────────────────────────────────────────
BG       = HexColor('#050505')
BG2      = HexColor('#0a0d10')
BG3      = HexColor('#111518')
GOLD     = HexColor('#c9a84c')
GOLD_L   = HexColor('#e2c97e')
WHITE    = HexColor('#f2ede4')
DIM      = HexColor('#555555')
RED_DIM  = HexColor('#8b1a1a')

# ── Dimensiones: igualar exactamente al PNG (2288×3165 a 150 dpi) ─────────────
DPI = 150
PNG_W_PX, PNG_H_PX = 2288, 3165
PAGE_W = PNG_W_PX / DPI * 72   # puntos
PAGE_H = PNG_H_PX / DPI * 72   # puntos

# Fuentes del sistema (monospace + serif disponibles en Windows)
FONT_DIR = Path(r'C:\Windows\Fonts')

def _reg(name, file):
    path = FONT_DIR / file
    if path.exists():
        pdfmetrics.registerFont(TTFont(name, str(path)))
        return True
    return False

_reg('Courier-Bold-Win', 'courbd.ttf')
_reg('Courier-Win',      'cour.ttf')
_reg('Arial-Win',        'arial.ttf')
_reg('Arial-Bold-Win',   'arialbd.ttf')
_reg('Arial-Italic-Win', 'ariali.ttf')


def _font(preferred, fallback='Helvetica'):
    try:
        pdfmetrics.getFont(preferred)
        return preferred
    except Exception:
        return fallback


MONO_B = _font('Courier-Bold-Win', 'Courier-Bold')
MONO   = _font('Courier-Win',      'Courier')
SANS_B = _font('Arial-Bold-Win',   'Helvetica-Bold')
SANS   = _font('Arial-Win',        'Helvetica')
SANS_I = _font('Arial-Italic-Win', 'Helvetica-Oblique')


# ── Helpers ───────────────────────────────────────────────────────────────────

def hrule(c, y, color=GOLD, alpha=1.0, w=None, lw=0.8):
    """Línea horizontal centrada."""
    width = w or PAGE_W
    c.setStrokeColor(color, alpha=alpha)
    c.setLineWidth(lw)
    c.line(0, y, width, y)


def box_fill(c, x, y, w, h, color, alpha=1.0):
    c.setFillColor(color, alpha=alpha)
    c.rect(x, y, w, h, fill=1, stroke=0)


# ── Portada ───────────────────────────────────────────────────────────────────

def draw_cover(c: canvas.Canvas):
    W, H = PAGE_W, PAGE_H

    # Fondo negro total
    box_fill(c, 0, 0, W, H, BG)

    # Banda superior decorativa (gradient simulado con tres rectángulos)
    for i, alpha in enumerate([0.12, 0.22, 0.35]):
        box_fill(c, 0, H - (i + 1) * 4, W, 4, GOLD, alpha=alpha)

    # Banda inferior decorativa
    for i, alpha in enumerate([0.35, 0.22, 0.12]):
        box_fill(c, 0, i * 4, W, 4, GOLD, alpha=alpha)

    # ── Separador superior ──
    hrule(c, H * 0.88, color=GOLD, lw=1.0)
    hrule(c, H * 0.88 - 3, color=GOLD, alpha=0.25, lw=0.4)

    # ── Isotipo — "⬡" simulado con texto grande ──
    cx = W / 2
    c.setFillColor(GOLD, alpha=0.12)
    c.setFont(MONO_B, 220)
    c.drawCentredString(cx, H * 0.56, 'F')

    # ── Logotipo FARO PROTOCOL ──
    c.setFillColor(GOLD)
    c.setFont(MONO_B, 52)
    c.drawCentredString(cx, H * 0.72, 'F  A  R  O   P  R  O  T  O  C  O  L')

    # subtítulo logotipo
    c.setFillColor(GOLD, alpha=0.55)
    c.setFont(MONO, 14)
    c.drawCentredString(cx, H * 0.72 - 30, 'Sistema de Inteligencia Satelital para Recursos Estratégicos')

    # ── Separador central ──
    hrule(c, H * 0.65, color=GOLD, alpha=0.40, lw=0.6)

    # ── Título del reporte ──
    c.setFillColor(WHITE)
    c.setFont(SANS_B, 38)
    c.drawCentredString(cx, H * 0.53, 'Reporte Satelital Minero')

    c.setFont(SANS_B, 34)
    c.drawCentredString(cx, H * 0.53 - 50, 'La EDITH III & IV')

    # ── Subtítulo ──
    c.setFillColor(GOLD_L)
    c.setFont(SANS_I, 20)
    c.drawCentredString(cx, H * 0.44, 'Puna de Atacama, Argentina  ·  Mayo 2026')

    # ── Bloque de datos clave ──
    hrule(c, H * 0.395, color=GOLD, alpha=0.35, lw=0.5)
    hrule(c, H * 0.395 - 3, color=GOLD, alpha=0.15, lw=0.3)

    data_y = H * 0.36
    c.setFillColor(GOLD, alpha=0.08)
    c.rect(W * 0.12, data_y - 10, W * 0.76, 72, fill=1, stroke=0)
    c.setStrokeColor(GOLD, alpha=0.20)
    c.setLineWidth(0.4)
    c.rect(W * 0.12, data_y - 10, W * 0.76, 72, fill=0, stroke=1)

    items = [
        ('Coordenadas',   '-26.8524 N  |  -67.7550 E'),
        ('Superficie',    '726 ha (III)  +  319 ha (IV)  =  1045 ha totales'),
        ('Índice FARO',   '57.5  —  ALTO POTENCIAL'),
    ]
    for i, (label, value) in enumerate(items):
        iy = data_y + 42 - i * 22
        c.setFont(MONO, 11)
        c.setFillColor(GOLD, alpha=0.75)
        c.drawString(W * 0.15, iy, label.upper() + ' :')
        c.setFont(SANS_B, 12)
        c.setFillColor(WHITE)
        c.drawString(W * 0.15 + 130, iy, value)

    hrule(c, data_y - 14, color=GOLD, alpha=0.35, lw=0.5)

    # ── Aviso de confidencialidad ──
    conf_y = H * 0.24
    # caja roja tenue
    c.setFillColor(RED_DIM, alpha=0.18)
    c.rect(W * 0.20, conf_y - 14, W * 0.60, 36, fill=1, stroke=0)
    c.setStrokeColor(RED_DIM, alpha=0.55)
    c.setLineWidth(0.6)
    c.rect(W * 0.20, conf_y - 14, W * 0.60, 36, fill=0, stroke=1)

    c.setFillColor(HexColor('#e07070'))
    c.setFont(SANS_B, 11)
    c.drawCentredString(cx, conf_y + 10, 'CONFIDENCIAL')
    c.setFont(SANS, 10)
    c.setFillColor(WHITE, alpha=0.70)
    c.drawCentredString(cx, conf_y - 4, 'Preparado para uso exclusivo del destinatario')

    # ── Footer ──
    hrule(c, H * 0.115, color=GOLD, lw=1.0)
    hrule(c, H * 0.115 - 3, color=GOLD, alpha=0.25, lw=0.4)

    c.setFont(MONO, 10)
    c.setFillColor(DIM)
    c.drawString(W * 0.05, H * 0.085, 'FARO PROTOCOL  ·  protocolfaro@gmail.com')
    c.drawRightString(W * 0.95, H * 0.085, 'Página 1 de 2')

    c.setFont(MONO, 9)
    c.setFillColor(GOLD, alpha=0.40)
    c.drawCentredString(cx, H * 0.058, 'Datos satelitales: Copernicus Data Space — Sentinel-1 / Sentinel-2 / Sentinel-3')
    c.drawCentredString(cx, H * 0.042, 'Procesamiento: FARO Engine Suite  ·  SHA-256 verificado')


# ── Página 2: PNG del reporte ─────────────────────────────────────────────────

def draw_report_page(c: canvas.Canvas):
    W, H = PAGE_W, PAGE_H

    # Fondo negro (en caso de márgenes RGBA)
    box_fill(c, 0, 0, W, H, BG)

    # Convertir RGBA → RGB y cargar como ImageReader
    img = Image.open(PNG_IN).convert('RGB')
    buf = io.BytesIO()
    img.save(buf, format='PNG', dpi=(DPI, DPI))
    buf.seek(0)
    ir = ImageReader(buf)

    # Dibujar PNG ocupando toda la página
    c.drawImage(ir, 0, 0, width=W, height=H,
                preserveAspectRatio=True, anchor='c')

    # Footer mínimo
    c.setFont(MONO, 10)
    c.setFillColor(DIM)
    c.drawRightString(W * 0.95, 14, 'Página 2 de 2')


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    print("━" * 60)
    print("  FARO PROTOCOL — Generando PDF profesional")
    print("━" * 60)
    print(f"  Entrada : {PNG_IN}")
    print(f"  Salida  : {PDF_OUT}")
    print()

    if not PNG_IN.exists():
        print(f"  ERROR: no se encontró {PNG_IN}")
        sys.exit(1)

    c = canvas.Canvas(str(PDF_OUT), pagesize=(PAGE_W, PAGE_H))
    c.setTitle('Reporte Satelital Minero — La EDITH III & IV · FARO PROTOCOL')
    c.setAuthor('FARO PROTOCOL')
    c.setSubject('Análisis satelital Puna de Atacama, Argentina · Mayo 2026')
    c.setKeywords('satelital, minería, Sentinel, SAR, InSAR, CLOSDI, Puna de Atacama')

    print("  → Renderizando portada...")
    draw_cover(c)
    c.showPage()

    print("  → Insertando reporte satelital...")
    draw_report_page(c)
    c.showPage()

    c.save()

    size_kb = PDF_OUT.stat().st_size // 1024
    print()
    print("━" * 60)
    print("  ✅  PDF GENERADO")
    print(f"  📄  {PDF_OUT}")
    print(f"  📦  {size_kb} KB  |  2 páginas  |  {PAGE_W:.0f}×{PAGE_H:.0f} pt")
    print("━" * 60)


if __name__ == '__main__':
    main()
