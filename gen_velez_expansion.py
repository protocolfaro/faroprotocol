#!/usr/bin/env python3
"""
gen_velez_expansion.py
Agrega página 'Expansión Fase 2' a 5 manuales PDF de Vélez Sarsfield.
Manuales: banchero, pait, berlanga, nelson, aveleyra.
Usa reportlab para generar la página + pypdf para fusionar.
"""
import io, pathlib, shutil
from reportlab.pdfgen import canvas
from reportlab.lib.units import mm
from reportlab.lib.colors import HexColor
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from pypdf import PdfWriter, PdfReader

# Fuente con soporte completo de acentos españoles
pdfmetrics.registerFont(TTFont('Helvetica',      r'C:\Windows\Fonts\arial.ttf'))
pdfmetrics.registerFont(TTFont('Helvetica-Bold', r'C:\Windows\Fonts\arialbd.ttf'))

# ── Paleta ───────────────────────────────────────────────────────────────────
BG    = HexColor('#000000')
GOLD  = HexColor('#c9a84c')
WHITE = HexColor('#f0ede8')
LGRAY = HexColor('#aaaaaa')
MGRAY = HexColor('#1e1e1e')
GRNL  = HexColor('#27ae60')

W, H = 595.27, 841.89   # A4

OUT_DIR     = pathlib.Path(__file__).parent / 'reportes_velez'
DESKTOP_DIR = pathlib.Path.home() / 'Desktop'

# Manuales que reciben la nueva página
TARGETS = [
    'manual_velez_banchero.pdf',
    'manual_velez_pait.pdf',
    'manual_velez_berlanga.pdf',
    'manual_velez_nelson.pdf',
    'manual_velez_aveleyra.pdf',
]

# ── Helpers ──────────────────────────────────────────────────────────────────

def hline(c, y, x0=40*mm, x1=None, col=GOLD, lw=0.5):
    if x1 is None:
        x1 = W - 40*mm
    c.setStrokeColor(col)
    c.setLineWidth(lw)
    c.line(x0, y, x1, y)


def draw_bullet_icon(c, x, y, icon_char, col=GOLD):
    """Dibuja un cuadrado de color con símbolo como ícono."""
    size = 7*mm
    c.setFillColor(col)
    c.setStrokeColor(col)
    c.setLineWidth(0.5)
    c.roundRect(x, y - size*0.75, size, size, 1.5*mm, fill=1, stroke=0)
    c.setFillColor(BG)
    c.setFont('Helvetica-Bold', 8)
    c.drawCentredString(x + size/2, y - size*0.4, icon_char)


# ── Generador de la página de expansión ──────────────────────────────────────

def create_expansion_page() -> bytes:
    """Genera la página 'Expansión Fase 2' como bytes PDF."""
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=(W, H))

    # Fondo negro
    c.setFillColor(BG)
    c.rect(0, 0, W, H, fill=1, stroke=0)

    # Líneas decorativas doradas
    hline(c, H - 18*mm, lw=1.0)
    hline(c, 18*mm, lw=1.0)

    # Header Faro Protocol
    c.setFont('Helvetica-Bold', 10)
    c.setFillColor(GOLD)
    c.drawCentredString(W/2, H - 27*mm, 'F A R O   P R O T O C O L')
    c.setFont('Helvetica', 7.5)
    c.setFillColor(LGRAY)
    c.drawCentredString(W/2, H - 33*mm, 'MONITOREO SATELITAL AVANZADO')

    hline(c, H - 37*mm, col=HexColor('#c9a84c44'), lw=0.3)

    # Título principal
    c.setFont('Helvetica-Bold', 22)
    c.setFillColor(GOLD)
    c.drawCentredString(W/2, H - 55*mm, 'FORTÍN INTELIGENTE')

    c.setFont('Helvetica-Bold', 14)
    c.setFillColor(WHITE)
    c.drawCentredString(W/2, H - 66*mm, 'COBERTURA COMPLETA DEL CLUB')

    hline(c, H - 72*mm, W/2 - 50*mm, W/2 + 50*mm, GOLD, 0.4)

    # Subtítulo
    c.setFont('Helvetica', 10.5)
    c.setFillColor(LGRAY)
    c.drawCentredString(W/2, H - 82*mm,
                        'El sistema ahora monitorea el 100% del predio:')

    # ── Secciones ────────────────────────────────────────────────────────────
    secciones = [
        ('E', GOLD,               'Estadio José Amalfitani',
         'Campo principal + tribunas + paneles solares'),
        ('V', HexColor('#27ae60'), 'Villa Olímpica',
         '4 canchas de entrenamiento'),
        ('P', HexColor('#4a9ede'), 'Polideportivo José Ramón Feijóo',
         'Canchas de tenis, hockey, básquet y playones'),
        ('A', HexColor('#00bcd4'), 'Complejo Acuático',
         'Natatorio olímpico + piletas exteriores\n'
         'Alerta temprana de calidad del agua'),
        ('S', HexColor('#c9a84c'), 'Sede Central & Instituto Vélez Sarsfield',
         'Edificios administrativos y educativos'),
    ]

    y = H - 103*mm
    row_h = 28*mm
    left  = 40*mm
    icon_w = 9*mm
    text_x = left + icon_w + 6*mm

    for (char, col, titulo, desc) in secciones:
        # Fondo sutil de fila
        c.setFillColor(HexColor('#0d1117'))
        c.setStrokeColor(HexColor(col.hexval() + '33') if hasattr(col, 'hexval') else GOLD)
        c.roundRect(left - 3*mm, y - row_h + 4*mm,
                    W - 80*mm + 3*mm, row_h - 2*mm,
                    2*mm, fill=1, stroke=0)

        # Ícono cuadrado
        draw_bullet_icon(c, left, y - 4*mm, char, col)

        # Título de sección
        c.setFont('Helvetica-Bold', 11.5)
        c.setFillColor(col)
        c.drawString(text_x, y - 4*mm, titulo)

        # Descripción (puede tener \n)
        lines = desc.split('\n')
        dy = 0
        for line in lines:
            c.setFont('Helvetica', 9)
            c.setFillColor(LGRAY)
            c.drawString(text_x, y - 12*mm - dy, line)
            dy += 5*mm

        y -= row_h

    # ── Mensaje final ─────────────────────────────────────────────────────────
    y_msg = y - 8*mm
    c.setFillColor(HexColor('#0a1520'))
    c.setStrokeColor(HexColor('#c9a84c55'))
    c.setLineWidth(0.8)
    c.roundRect(40*mm, y_msg - 22*mm, W - 80*mm, 22*mm, 3*mm, fill=1, stroke=1)

    c.setFont('Helvetica-Bold', 11)
    c.setFillColor(WHITE)
    c.drawCentredString(W/2, y_msg - 8*mm,
                        'Todo monitoreado desde el espacio.')

    c.setFont('Helvetica-Bold', 11)
    c.setFillColor(GOLD)
    c.drawCentredString(W/2, y_msg - 16*mm,
                        'Cada lunes. A costo cero para el club.')

    # Footer
    c.setFont('Helvetica', 7)
    c.setFillColor(LGRAY)
    c.drawCentredString(
        W/2, 12*mm,
        'Faro Protocol  |  protocolfaro@gmail.com  |  '
        'Datos: ESA Sentinel-2, Sentinel-1 SAR, Landsat TIRS, NASA SMAP'
    )

    c.save()
    buf.seek(0)
    return buf.read()


# ── Merge y guardar ──────────────────────────────────────────────────────────

def append_expansion_page(pdf_path: pathlib.Path, expansion_bytes: bytes) -> bool:
    """Agrega la página de expansión al PDF existente."""
    if not pdf_path.exists():
        print(f'  ERROR: no encontrado → {pdf_path.name}')
        return False

    try:
        writer   = PdfWriter()
        existing = PdfReader(str(pdf_path))
        new_page = PdfReader(io.BytesIO(expansion_bytes))

        # Copiar páginas existentes
        for page in existing.pages:
            writer.add_page(page)

        # Agregar nueva página
        writer.add_page(new_page.pages[0])

        # Guardar en el mismo archivo (temp → rename)
        tmp = pdf_path.with_suffix('.tmp.pdf')
        with open(tmp, 'wb') as f:
            writer.write(f)
        tmp.replace(pdf_path)

        # Copiar también al Escritorio
        desktop_out = DESKTOP_DIR / pdf_path.name
        shutil.copy2(pdf_path, desktop_out)
        return True

    except Exception as e:
        print(f'  ERROR procesando {pdf_path.name}: {e}')
        return False


# ── Main ─────────────────────────────────────────────────────────────────────

if __name__ == '__main__':
    print('Generando página "Expansión Fase 2"...')
    expansion_bytes = create_expansion_page()
    print(f'  Página generada: {len(expansion_bytes):,} bytes')

    print('\nAgregando página a 5 manuales:')
    ok_count = 0
    for filename in TARGETS:
        pdf_path = OUT_DIR / filename
        ok = append_expansion_page(pdf_path, expansion_bytes)
        if ok:
            size_kb = pdf_path.stat().st_size // 1024
            print(f'  OK  {filename}  ({size_kb} KB)')
            ok_count += 1
        else:
            print(f'  FAIL {filename}')

    print(f'\n{ok_count}/{len(TARGETS)} manuales actualizados.')
    print(f'  reportes_velez/  + Escritorio/')
