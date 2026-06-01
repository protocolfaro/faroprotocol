"""
gen_propuesta_faro_events.py
Genera propuesta_faro_events.pdf (9 páginas) con reportlab.
"""
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4
from reportlab.lib.colors import HexColor
from reportlab.lib.utils import ImageReader
import pathlib, tempfile
from PIL import Image

W, H   = A4          # 595.27 x 841.89 pts
MG     = 48          # margin
TW     = W - 2 * MG  # text width

BG   = HexColor("#1a2744")
GOLD = HexColor("#c9a84c")
WITE = HexColor("#ffffff")
DARK = HexColor("#0d1a33")
GOLD2= HexColor("#e8c97a")

ROOT     = pathlib.Path(__file__).parent.parent.parent
LOGO_IMG = ROOT / "reportes_velez" / "Gemini_Generated_Image_e2w4txe2w4txe2w4.png"
OUT_PDF  = pathlib.Path(__file__).parent / "propuesta_faro_events.pdf"

def _crop_symbol_logo(src, crop_frac=0.57):
    """Crop logo to FP symbol only, removing FARO SPORTS text baked into image."""
    img = Image.open(str(src))
    w, h = img.size
    cropped = img.crop((0, 0, w, int(h * crop_frac)))
    tmp = pathlib.Path(tempfile.mktemp(suffix=".png"))
    cropped.save(str(tmp))
    return tmp

LOGO_SYMBOL = _crop_symbol_logo(LOGO_IMG)

# ── helpers ──────────────────────────────────────────────────────────────────

def bg(cv):
    cv.setFillColor(BG)
    cv.rect(0, 0, W, H, fill=1, stroke=0)

def header(cv):
    img = ImageReader(str(LOGO_SYMBOL))
    cv.drawImage(img, MG, H - 60, width=72, height=42, mask="auto")
    cv.setFillColor(GOLD)
    cv.setFont("Helvetica-Bold", 11)
    cv.drawString(MG + 80, H - 36, "FARO EVENTS")
    cv.setFillColor(WITE)
    cv.setFont("Helvetica", 7)
    cv.drawString(MG + 80, H - 48, "UN PRODUCTO DE FARO PROTOCOL")
    cv.setStrokeColor(GOLD)
    cv.setLineWidth(0.8)
    cv.line(MG, H - 66, W - MG, H - 66)

def footer(cv, n):
    cv.setStrokeColor(GOLD)
    cv.setLineWidth(0.4)
    cv.line(MG, 40, W - MG, 40)
    cv.setFillColor(GOLD)
    cv.setFont("Helvetica", 7)
    cv.drawString(MG, 27, "FARO PROTOCOL · CONFIDENCIAL · 2026")
    cv.setFillColor(WITE)
    cv.drawRightString(W - MG, 27, f"Página {n}")

def gold_sep(cv, y, thick=1.2):
    cv.setStrokeColor(GOLD)
    cv.setLineWidth(thick)
    cv.line(MG, y, W - MG, y)

def thin_sep(cv, y):
    cv.setStrokeColor(GOLD)
    cv.setLineWidth(0.4)
    cv.line(MG, y, W - MG, y)

def title(cv, text, y, size=22):
    cv.setFillColor(GOLD)
    cv.setFont("Helvetica-Bold", size)
    cv.drawString(MG, y, text)
    return y - size * 1.6

def subtitle(cv, text, y, size=13):
    cv.setFillColor(WITE)
    cv.setFont("Helvetica-Bold", size)
    cv.drawString(MG, y, text)
    return y - size * 1.8

def para(cv, text, y, size=10.5, color=None, indent=0):
    cv.setFillColor(color or WITE)
    cv.setFont("Helvetica", size)
    words = text.split()
    lines, line, lw = [], [], 0
    max_w = TW - indent
    for word in words:
        ww = cv.stringWidth(word + " ", "Helvetica", size)
        if lw + ww > max_w and line:
            lines.append(" ".join(line)); line, lw = [word], ww
        else:
            line.append(word); lw += ww
    if line:
        lines.append(" ".join(line))
    lh = size * 1.55
    for i, ln in enumerate(lines):
        cv.drawString(MG + indent, y - i * lh, ln)
    return y - len(lines) * lh - 4

def bullet(cv, text, y, size=10.5):
    cv.setFillColor(GOLD)
    cv.setFont("Helvetica-Bold", size)
    cv.drawString(MG + 4, y, "·")
    cv.setFillColor(WITE)
    cv.setFont("Helvetica", size)
    words = text.split()
    lines, line, lw = [], [], 0
    max_w = TW - 20
    for word in words:
        ww = cv.stringWidth(word + " ", "Helvetica", size)
        if lw + ww > max_w and line:
            lines.append(" ".join(line)); line, lw = [word], ww
        else:
            line.append(word); lw += ww
    if line:
        lines.append(" ".join(line))
    lh = size * 1.55
    for i, ln in enumerate(lines):
        cv.drawString(MG + 20, y - i * lh, ln)
    return y - len(lines) * lh - 6

def highlight_box(cv, text, y, bg_col=None, border_col=None, size=11, pad=14):
    bg_col     = bg_col or DARK
    border_col = border_col or GOLD
    cv.setFillColor(bg_col)
    cv.setStrokeColor(border_col)
    cv.setLineWidth(1.5)
    # measure text height
    words = text.split()
    lines, line, lw = [], [], 0
    for word in words:
        ww = cv.stringWidth(word + " ", "Helvetica-Bold", size)
        if lw + ww > TW - pad * 2 and line:
            lines.append(" ".join(line)); line, lw = [word], ww
        else:
            line.append(word); lw += ww
    if line:
        lines.append(" ".join(line))
    lh   = size * 1.6
    bh   = len(lines) * lh + pad * 2
    cv.roundRect(MG, y - bh, TW, bh, 6, fill=1, stroke=1)
    cv.setFillColor(GOLD)
    cv.setFont("Helvetica-Bold", size)
    ty = y - pad - size
    for ln in lines:
        cv.drawCentredString(W / 2, ty, ln)
        ty -= lh
    return y - bh - 10


# ═══════════════════════════════════════════════════════════════════════════
# BUILD PDF
# ═══════════════════════════════════════════════════════════════════════════

c = canvas.Canvas(str(OUT_PDF), pagesize=A4)

# ── PAGE 1 — PORTADA ──────────────────────────────────────────────────────
bg(c)

# Logo grande centrado (símbolo FP sin texto FARO SPORTS)
img = ImageReader(str(LOGO_SYMBOL))
lw_px, lh_px = 300, 172
c.drawImage(img, (W - lw_px) / 2, H * 0.53, width=lw_px, height=lh_px, mask="auto")

# FARO EVENTS
c.setFillColor(GOLD)
c.setFont("Helvetica-Bold", 46)
c.drawCentredString(W / 2, H * 0.46, "FARO EVENTS")

# Subtítulo
c.setFillColor(WITE)
c.setFont("Helvetica", 15)
c.drawCentredString(W / 2, H * 0.415, "Un producto de Faro Protocol")

# Doble línea dorada
gold_sep(c, H * 0.385, thick=2)
gold_sep(c, H * 0.377, thick=0.5)

# Tagline
c.setFillColor(WITE)
c.setFont("Helvetica-Bold", 16)
c.drawCentredString(W / 2, H * 0.34, "Auditoría Técnica para Eventos Masivos en Estadios")

# Preparado para
c.setFillColor(GOLD)
c.setFont("Helvetica-Bold", 10)
c.drawCentredString(W / 2, H * 0.21, "Preparado para: Dale Play")
c.setFillColor(WITE)
c.setFont("Helvetica", 10)
c.drawCentredString(W / 2, H * 0.185, "En colaboración con: Club Atlético Vélez Sarsfield")

# Año
c.setFillColor(GOLD)
c.setFont("Helvetica-Bold", 18)
c.drawCentredString(W / 2, H * 0.12, "2026")

# Línea pie de portada
thin_sep(c, H * 0.09)
c.setFillColor(WITE)
c.setFont("Helvetica", 7)
c.drawCentredString(W / 2, H * 0.07, "CONFIDENCIAL · NO DISTRIBUIR SIN AUTORIZACIÓN")

c.showPage()


# ── PAGE 2 — ORIGEN ───────────────────────────────────────────────────────
bg(c); header(c); footer(c, 2)
y = H - 90

y = title(c, "Origen del proyecto", y)
y -= 6
y = para(c,
    "Durante el trabajo de monitoreo satelital que desarrollamos en conjunto con el "
    "Ing. Roger Bernal, Jefe de Canchas de Vélez Sarsfield, identificamos algo que "
    "ninguna productora tiene hoy: la posibilidad de ver el estadio desde adentro — "
    "el subsuelo, el drenaje, la estructura — antes de montar el primer caño. "
    "De esa experiencia nació Faro Events.",
    y, size=11)
y -= 20
gold_sep(c, y)
y -= 22

y = subtitle(c, "¿Qué hacemos?", y)
y -= 4
y = para(c,
    "Utilizamos tecnología de radar satelital (SAR / Sentinel-1) e inteligencia "
    "artificial para medir el impacto real de cada evento sobre el terreno — con "
    "precisión milimétrica, desde el espacio, sin depender del clima ni de "
    "inspecciones visuales subjetivas.",
    y, size=11)
y -= 12
y = para(c,
    "El resultado es un certificado técnico irrefutable que documenta el estado "
    "exacto del campo antes y después de cada show.",
    y, size=11)
y -= 28

# Caja de beneficio
y = highlight_box(c,
    "El primer sistema satelital de auditoría técnica para eventos masivos en "
    "Argentina — operativo, verificado con datos reales.",
    y, size=11)
y -= 20

# Visual de tres pilares
pilares = [
    ("RADAR SAR",  "Sentinel-1 GRD · ESA Copernicus"),
    ("ÓPTICO S2",  "Sentinel-2 L2A · NDVI real"),
    ("IA + DATOS", "Análisis automático 48 h"),
]
bw  = (TW - 20) / 3
bh2 = 62
bx  = MG
for tit, sub in pilares:
    c.setFillColor(DARK)
    c.setStrokeColor(GOLD)
    c.setLineWidth(1)
    c.roundRect(bx, y - bh2, bw, bh2, 5, fill=1, stroke=1)
    c.setFillColor(GOLD)
    c.setFont("Helvetica-Bold", 10)
    c.drawCentredString(bx + bw / 2, y - 22, tit)
    c.setFillColor(WITE)
    c.setFont("Helvetica", 8)
    c.drawCentredString(bx + bw / 2, y - 38, sub)
    bx += bw + 10

c.showPage()


# ── PAGE 3 — CARGÁ TU RIDER ───────────────────────────────────────────────
bg(c); header(c); footer(c, 3)
y = H - 90

y = title(c, "Cargá tu rider. Nosotros hacemos el resto.", y)
y -= 6
y = para(c,
    "Subís el PDF o DWG con el layout de tu producción. Nuestro sistema interno "
    "procesa el modelo 3D del estadio cruzado con los datos reales del subsuelo. "
    "En 48 horas recibís un reporte con tu configuración analizada:",
    y, size=11)
y -= 18

bullets_p3 = [
    "Posición óptima de cada estructura sobre el mapa real del subsuelo",
    "Zonas de drenaje identificadas — sabés exactamente dónde no podés apoyar peso",
    "Carga por sector en kPa — cada estructura validada contra la capacidad real del suelo esa semana",
    "Rutas de circulación de equipos que evitan las zonas críticas del campo",
    "Alertas automáticas si alguna estructura cae sobre zona de drenaje crítica",
]
for b in bullets_p3:
    y = bullet(c, b, y)
    y -= 2

y -= 20
gold_sep(c, y)
y -= 22

y = subtitle(c, "Tecnología de fondo", y)
y -= 4
y = para(c,
    "El modelo de suelo usa datos reales de SoilGrids (ISRIC), la red global de "
    "monitoreo de suelos, combinados con escenas Sentinel-2 del día de la semana "
    "del evento. No usamos tablas genéricas — cada análisis es específico para "
    "ese estadio, esa semana, esas condiciones.",
    y, size=11)
y -= 20

# Box proceso
steps = [
    ("01", "Subís el rider", "PDF / DWG / imagen"),
    ("02", "Procesamiento", "Modelo 3D + suelo real"),
    ("03", "Reporte", "48 h · entrega digital"),
]
bw3 = (TW - 20) / 3
bx3 = MG
bh3 = 72
for num, tit3, sub3 in steps:
    c.setFillColor(DARK)
    c.setStrokeColor(GOLD)
    c.setLineWidth(1)
    c.roundRect(bx3, y - bh3, bw3, bh3, 5, fill=1, stroke=1)
    c.setFillColor(GOLD)
    c.setFont("Helvetica-Bold", 18)
    c.drawCentredString(bx3 + bw3 / 2, y - 24, num)
    c.setFillColor(WITE)
    c.setFont("Helvetica-Bold", 9)
    c.drawCentredString(bx3 + bw3 / 2, y - 40, tit3)
    c.setFillColor(WITE)
    c.setFont("Helvetica", 8)
    c.drawCentredString(bx3 + bw3 / 2, y - 54, sub3)
    # Arrow (not for last)
    if num != "03":
        c.setStrokeColor(GOLD)
        c.setLineWidth(1.5)
        ax = bx3 + bw3 + 4
        ay = y - bh3 / 2
        c.line(ax, ay, ax + 6, ay)
        c.line(ax + 4, ay - 3, ax + 8, ay)
        c.line(ax + 4, ay + 3, ax + 8, ay)
    bx3 += bw3 + 10

c.showPage()


# ── PAGE 4 — EL SHOW QUE EL PÚBLICO MERECE ───────────────────────────────
bg(c); header(c); footer(c, 4)
y = H - 90

y = title(c, "El show que el público merece", y)
y -= 6

# Acústica
y = subtitle(c, "Acústica — Estándar FIFA / UEFA", y, size=12)
y -= 2
y = para(c,
    "Analizamos la cobertura de sonido sector por sector con tu sistema real "
    "(L-Acoustics, d&b). Si subís las torres 50 cm mejorás la cobertura en "
    "tribuna. Si agregás una torre de delay en posición óptima cubrís los "
    "sectores que hoy no llega el sonido directo. Con el archivo SDE de tu "
    "sistema el análisis es sobre tu equipo real — no estimaciones genéricas.",
    y, size=11)
y -= 16
thin_sep(c, y)
y -= 18

# Visibilidad
y = subtitle(c, "Visibilidad — C-value ISO", y, size=12)
y -= 2
y = para(c,
    "Calculamos la línea de visión de cada sector de tribuna hacia el escenario. "
    "Si la pantalla está a esta altura y en esta posición, el 97 % del público "
    "tiene visión directa. Si la corrés 2 metros mejorás un 15 % en platea alta. "
    "Dato concreto, no intuición.",
    y, size=11)
y -= 20
gold_sep(c, y)
y -= 22

# Métricas grid
metrics = [
    ("ACÚSTICA",   "SPL ± 3 dB",    "cobertura sectorial"),
    ("VISIBILIDAD","C-value ≥ 60",   "línea de visión ISO"),
    ("SUELO",      "kPa por sector", "carga real validada"),
    ("SAR",        "Δ VV dB",        "compactación pre/post"),
]
mw = (TW - 30) / 4
mx = MG
mh = 80
for mname, mval, msub in metrics:
    c.setFillColor(DARK)
    c.setStrokeColor(GOLD)
    c.setLineWidth(1)
    c.roundRect(mx, y - mh, mw, mh, 5, fill=1, stroke=1)
    c.setFillColor(GOLD)
    c.setFont("Helvetica-Bold", 8.5)
    c.drawCentredString(mx + mw / 2, y - 18, mname)
    c.setFillColor(WITE)
    c.setFont("Helvetica-Bold", 11)
    c.drawCentredString(mx + mw / 2, y - 36, mval)
    c.setFillColor(WITE)
    c.setFont("Helvetica", 7.5)
    c.drawCentredString(mx + mw / 2, y - 52, msub)
    mx += mw + 10

y -= mh + 16
y = para(c,
    "Todos los análisis son generados antes del show, con datos satelitales de "
    "la semana del evento. El equipo llega al estadio con el análisis en mano.",
    y, size=10.5)

c.showPage()


# ── PAGE 5 — DURANTE EL MONTAJE ───────────────────────────────────────────
bg(c); header(c); footer(c, 5)
y = H - 90

y = title(c, "Durante el montaje", y)
y -= 6
y = para(c,
    "Con el análisis pre-show en mano, el equipo trabaja con un mapa claro de "
    "zonas seguras y críticas. Si el clima cambia — viento, lluvia — el sistema "
    "manda una alerta automática antes de que supere los umbrales de seguridad "
    "de tus estructuras.",
    y, size=11)
y -= 20
gold_sep(c, y)
y -= 22

y = subtitle(c, "Compliance normativo automático", y, size=12)
y -= 4
y = para(c,
    "Verificación automática contra Ordenanza GCBA 11.554, estándares OMS y "
    "NIOSH. Sabés si el nivel de sonido exterior cumple la normativa antes de "
    "que llegue un inspector.",
    y, size=11)
y -= 18

# Normas en cajas
normas = [
    ("Ord. GCBA\n11.554", "Ruido urbano"),
    ("OMS / NIOSH",       "Exposición auditiva"),
    ("FIFA / UEFA",       "Estándar de campo"),
    ("ESA Copernicus",    "Datos satelitales"),
]
nw = (TW - 30) / 4
nx = MG
nh = 72
for ntit, nsub in normas:
    c.setFillColor(DARK)
    c.setStrokeColor(GOLD)
    c.setLineWidth(1)
    c.roundRect(nx, y - nh, nw, nh, 5, fill=1, stroke=1)
    c.setFillColor(GOLD)
    c.setFont("Helvetica-Bold", 8)
    lines_n = ntit.split("\n")
    ty_n = y - 20
    for ln_n in lines_n:
        c.drawCentredString(nx + nw / 2, ty_n, ln_n)
        ty_n -= 13
    c.setFillColor(WITE)
    c.setFont("Helvetica", 7.5)
    c.drawCentredString(nx + nw / 2, y - 52, nsub)
    nx += nw + 10

y -= nh + 18
gold_sep(c, y)
y -= 22

y = subtitle(c, "El flujo completo", y, size=12)
y -= 8

# Timeline horizontal
fases = [
    ("PRE-SHOW",  "Análisis SAR,\nsuelo, acústica"),
    ("MONTAJE",   "Mapa de zonas,\nalertas clima"),
    ("SHOW",      "Alertas\nclimáticas auto."),
    ("POST-SHOW", "Certificado\nSHA-256 + QR"),
]
fw = (TW - 30) / 4
fx = MG
fh = 80
for ftit, fsub in fases:
    c.setFillColor(DARK)
    c.setStrokeColor(GOLD)
    c.setLineWidth(1.2)
    c.roundRect(fx, y - fh, fw, fh, 5, fill=1, stroke=1)
    c.setFillColor(GOLD)
    c.setFont("Helvetica-Bold", 8.5)
    c.drawCentredString(fx + fw / 2, y - 20, ftit)
    c.setFillColor(WITE)
    c.setFont("Helvetica", 7.5)
    ty_f = y - 36
    for ln_f in fsub.split("\n"):
        c.drawCentredString(fx + fw / 2, ty_f, ln_f)
        ty_f -= 13
    # Flecha entre fases
    if ftit != "POST-SHOW":
        c.setStrokeColor(GOLD)
        c.setLineWidth(1.5)
        ax_f = fx + fw + 3
        ay_f = y - fh / 2
        c.line(ax_f, ay_f, ax_f + 5, ay_f)
        c.line(ax_f + 3, ay_f - 3, ax_f + 7, ay_f)
        c.line(ax_f + 3, ay_f + 3, ax_f + 7, ay_f)
    fx += fw + 10

c.showPage()


# ── PAGE 6 — CASO REAL AIRBAG ─────────────────────────────────────────────
bg(c); header(c); footer(c, 6)
y = H - 90

y = title(c, "Caso real verificado: Airbag × Estadio Amalfitani", y, size=20)
y -= 2
c.setFillColor(WITE)
c.setFont("Helvetica-Bold", 12)
c.drawString(MG, y, "Mayo 2026 — El primer show auditado con tecnología satelital en Argentina")
y -= 24

# Tabla PRE / POST
col_w = TW / 2 - 4
row_h = 32
headers_t = ["PRE-SHOW", "POST-SHOW"]
rows_t = [
    ("SAR VV:", "+20.30 dB", "+18.27 dB"),
    ("Fecha:", "29/05/2026", "30/05/2026"),
    ("Fuente:", "Sentinel-1 GRD", "Sentinel-1 GRD"),
    ("Píxeles:", "540 px", "540 px"),
]
# Header row
tx0 = MG
ty0 = y
for i, hdr in enumerate(headers_t):
    c.setFillColor(GOLD)
    c.setStrokeColor(GOLD)
    c.setLineWidth(0.5)
    c.roundRect(tx0 + i * (col_w + 8), ty0 - row_h, col_w, row_h, 3, fill=1, stroke=0)
    c.setFillColor(BG)
    c.setFont("Helvetica-Bold", 11)
    c.drawCentredString(tx0 + i * (col_w + 8) + col_w / 2, ty0 - 20, hdr)
y -= row_h

for label, val1, val2 in rows_t:
    c.setFillColor(DARK)
    c.setStrokeColor(GOLD)
    c.setLineWidth(0.5)
    # row bg
    c.roundRect(MG, y - row_h + 2, TW, row_h - 4, 2, fill=1, stroke=0)
    c.setFillColor(GOLD)
    c.setFont("Helvetica-Bold", 9.5)
    c.drawString(MG + 10, y - 18, label)
    c.setFillColor(WITE)
    c.setFont("Helvetica", 10)
    c.drawCentredString(MG + col_w / 2 + 30, y - 18, val1)
    c.drawCentredString(MG + col_w + 8 + col_w / 2, y - 18, val2)
    # border line
    c.setStrokeColor(GOLD)
    c.setLineWidth(0.2)
    c.line(MG, y - row_h + 2, MG + TW, y - row_h + 2)
    y -= row_h

# outer border
table_h = row_h * (len(rows_t) + 1)
c.setStrokeColor(GOLD)
c.setLineWidth(1.2)
c.roundRect(MG, y, TW, table_h, 4, fill=0, stroke=1)

y -= 18
y = highlight_box(c,
    "DELTA: -2.03 dB — COMPACTACIÓN DETECTADA",
    y, size=13, bg_col=DARK, border_col=GOLD)
y -= 6
c.setFillColor(WITE)
c.setFont("Helvetica", 9)
c.drawCentredString(W / 2, y, "Umbral de seguridad: 1.5 dB  ·  Zona afectada: sector sur, frente al escenario")
y -= 22
gold_sep(c, y)
y -= 16

y = para(c,
    "Este dato existe. Está fechado. Tiene firma digital SHA-256 y QR verificable. "
    "Si el club reclama daños — tenés la auditoría satelital. "
    "Si no hay daños — tenés la prueba de que trabajaste bien.",
    y, size=10.5)
y -= 14

entregables = [
    "Reporte comparativo SAR pre/post evento",
    "Certificado de Integridad Faro — SHA-256 + QR verificable",
    "Mapa de sectores afectados con recomendaciones",
    "Hoja de ruta de recuperación — aireación, riego, resembrado por zona",
]
for i, e in enumerate(entregables, 1):
    c.setFillColor(GOLD)
    c.setFont("Helvetica-Bold", 10)
    c.drawString(MG + 4, y, f"{i}.")
    c.setFillColor(WITE)
    c.setFont("Helvetica", 10)
    c.drawString(MG + 22, y, e)
    y -= 16

c.showPage()


# ── PAGE 7 — REPORTE POST-SHOW (imagen) ──────────────────────────────────
bg(c); header(c); footer(c, 7)
y = H - 90

y = title(c, "Reporte Post-Show — Airbag × Amalfitani", y, size=20)
y -= 4
c.setFillColor(WITE)
c.setFont("Helvetica", 11)
c.drawCentredString(W / 2, y, "Generado automáticamente por Faro Events  ·  01/06/2026")
y -= 22

# Imagen centrada al 85% del ancho de página
_ps_img_path = ROOT / "railway-backend" / "dale-play" / "reportes" / "airbag_2026-05-29_post_show_comparativa.png"
_ps_img = ImageReader(str(_ps_img_path))
_iw_max = W * 0.85
_ih_max = y - 55   # espacio hasta footer + caption
_aspect = 18 / 14  # figsize original 14x18
_iw = _iw_max
_ih = _iw * _aspect
if _ih > _ih_max:
    _ih = _ih_max
    _iw = _ih / _aspect
_ix = (W - _iw) / 2
_iy = y - _ih
c.drawImage(_ps_img, _ix, _iy, width=_iw, height=_ih, preserveAspectRatio=True, mask="auto")

# Caption bajo la imagen
thin_sep(c, _iy - 8)
c.setFillColor(WITE)
c.setFont("Helvetica", 8)
c.drawCentredString(W / 2, _iy - 22,
    "Dato real  ·  Sentinel-1 GRD  ·  ESA Copernicus  ·  SHA-256 verificable")

c.showPage()


# ── PAGE 8 — SISTEMA COMPLETO (reporte estándar) ─────────────────────────
bg(c); header(c); footer(c, 8)
y = H - 90

y = title(c, "El sistema completo — Faro Events en acción", y, size=20)
y -= 6
c.setFillColor(WITE)
c.setFont("Helvetica", 10.5)
c.drawCentredString(W / 2, y,
    "Reporte operativo generado automáticamente para el show de Airbag  ·  Amalfitani  ·  Mayo 2026")
y -= 22

_op_img_path = ROOT / "railway-backend" / "dale-play" / "reportes" / "reporte_airbag_2026-05-31.png"
_op_img  = ImageReader(str(_op_img_path))
_ow_max  = W * 0.70
_oh_max  = y - 52
_oaspect = 14 / 18   # figsize 14×18
_ow = _ow_max
_oh = _ow / _oaspect
if _oh > _oh_max:
    _oh = _oh_max
    _ow = _oh * _oaspect
_ox = (W - _ow) / 2
_oy = 52 + (_oh_max - _oh) / 2   # center vertically in available space
c.drawImage(_op_img, _ox, _oy, width=_ow, height=_oh,
            preserveAspectRatio=True, mask="auto")

thin_sep(c, _oy - 8)
c.setFillColor(WITE)
c.setFont("Helvetica", 8)
c.drawCentredString(W / 2, _oy - 22,
    "Generado automáticamente  ·  Sin intervención manual  ·  Tecnología ESA Copernicus / NASA Earthdata")

c.showPage()


# ── PAGE 9 — ÍNDICE DE SALUD ACUMULADO ───────────────────────────────────
bg(c); header(c); footer(c, 9)  # noqa (renumbered)
y = H - 90

y = title(c, "Índice de Salud Acumulado del Campo", y)
y -= 6
y = para(c,
    "En mayo 2026 el Amalfitani recibió 4 shows en un mes — Lali (24-25/05) y "
    "Airbag (29-31/05). Sin un sistema de salud acumulada, nadie sabía cuánto "
    "estrés quedó en el campo después de cada evento.",
    y, size=11)
y -= 18

y = highlight_box(c,
    "Cada show suma estrés al campo. Tres shows en un mes sin tiempo de "
    "recuperación pueden destruir el césped aunque cada uno individualmente "
    "esté dentro del límite.",
    y, size=10.5)
y -= 18

y = para(c,
    "Faro Events lleva el historial técnico de cada evento sobre el campo y "
    "genera un índice de salud acumulada. El estadio sabe cuándo necesita "
    "intervenir. La productora sabe cuánto margen tiene antes del próximo show.",
    y, size=11)
y -= 24
gold_sep(c, y)
y -= 22

# Timeline shows mayo 2026
y = subtitle(c, "Mayo 2026 — Amalfitani · Cronología de estrés", y, size=11)
y -= 8
shows_mayo = [
    ("24/05", "Lali D1", "SAR —"),
    ("25/05", "Lali D2", "SAR —"),
    ("29/05", "Airbag D1", "+20.30 dB"),
    ("30/05", "Post D1",   "+18.27 dB"),
    ("31/05", "Airbag D2", "SAR pendiente"),
]
tw5 = TW / len(shows_mayo)
tx5 = MG
th5 = 72
for fecha5, nombre5, sar5 in shows_mayo:
    bg5 = DARK if "pendiente" not in sar5 else HexColor("#1e2e50")
    c.setFillColor(bg5)
    c.setStrokeColor(GOLD)
    c.setLineWidth(0.8)
    c.roundRect(tx5, y - th5, tw5 - 4, th5, 4, fill=1, stroke=1)
    c.setFillColor(GOLD)
    c.setFont("Helvetica-Bold", 8)
    c.drawCentredString(tx5 + (tw5 - 4) / 2, y - 16, fecha5)
    c.setFillColor(WITE)
    c.setFont("Helvetica", 7.5)
    c.drawCentredString(tx5 + (tw5 - 4) / 2, y - 30, nombre5)
    c.setFillColor(GOLD if "pendiente" not in sar5 else WITE)
    c.setFont("Helvetica-Bold", 7)
    c.drawCentredString(tx5 + (tw5 - 4) / 2, y - 46, sar5)
    tx5 += tw5
y -= th5 + 18

y = subtitle(c, "¿Qué incluye el índice?", y, size=11)
y -= 6
items_idx = [
    "Historial SAR de todos los eventos del estadio",
    "NDVI acumulado — evolución del césped semana a semana",
    "Recomendaciones de recuperación calibradas al daño real medido",
    "Proyección de disponibilidad del campo para el próximo show",
]
for item in items_idx:
    y = bullet(c, item, y)

c.showPage()


# ── PAGE 10 — ESCALABILIDAD ───────────────────────────────────────────────
bg(c); header(c); footer(c, 10)
y = H - 90

y = title(c, "Aplicable a cualquier estadio", y)
y -= 6
y = para(c,
    "El sistema usa coordenadas GPS y datos satelitales globales (ESA Copernicus, "
    "NASA Earthdata). Se adapta a cualquier venue en 48 horas — con el mismo nivel "
    "de precisión y los mismos entregables.",
    y, size=11)
y -= 20
gold_sep(c, y)
y -= 22

y = subtitle(c, "Estadios donde puede operar hoy", y, size=12)
y -= 8

stadiums = [
    ("Estadio Monumental",         "River Plate · 84.000 cap."),
    ("Estadio Malvinas Argentinas", "Mendoza · 40.000 cap."),
    ("La Bombonera",               "Boca Juniors · 54.000 cap."),
    ("José Amalfitani",            "Vélez Sarsfield · 49.000 cap. ✓ ACTIVO"),
    ("Cualquier estadio del mundo", "Coordenadas GPS · 48 h de setup"),
]
sh = 38
for sname, sdet in stadiums:
    activo = "ACTIVO" in sdet
    c.setFillColor(DARK if not activo else HexColor("#0d2b1a"))
    c.setStrokeColor(GOLD)
    c.setLineWidth(1.2 if activo else 0.5)
    c.roundRect(MG, y - sh + 4, TW, sh - 6, 4, fill=1, stroke=1)
    c.setFillColor(GOLD)
    c.setFont("Helvetica-Bold", 10)
    c.drawString(MG + 14, y - 14, sname)
    c.setFillColor(WITE)
    c.setFont("Helvetica", 9)
    c.drawRightString(MG + TW - 14, y - 14, sdet)
    y -= sh

y -= 16
y = highlight_box(c,
    "Ya operativo: Estadio José Amalfitani · caso base verificado con datos satelitales reales · Mayo 2026",
    y, size=10.5)
y -= 14

y = para(c,
    "Cada nuevo venue requiere únicamente las coordenadas GPS del campo y el archivo "
    "de rider. El resto del proceso — consulta STAC, descarga SAR, análisis de suelo, "
    "generación de certificado — es completamente automático.",
    y, size=11)

c.showPage()


# ── PAGE 11 — CONTACTO ────────────────────────────────────────────────────
bg(c); header(c); footer(c, 11)
y = H - 90

y = title(c, "¿Cómo empezamos?", y)
y -= 10

c.setFillColor(WITE)
c.setFont("Helvetica-Bold", 14)
c.drawCentredString(W / 2, y,
    "Mandanos el layout de tu próximo show.")
y -= 22
c.setFont("Helvetica", 13)
c.drawCentredString(W / 2, y, "En 48 horas tenés el análisis completo.")
y -= 38

# Email grande
c.setFillColor(GOLD)
c.setFont("Helvetica-Bold", 20)
c.drawCentredString(W / 2, y, "protocolfaro@gmail.com")
y -= 38
gold_sep(c, y, thick=1.5)
y -= 30

# 3 pasos contacto
pasos = [
    ("01", "Mandá el layout", "PDF, DWG o imagen"),
    ("02", "Recibís el análisis", "48 h · formato digital"),
    ("03", "Operamos juntos", "Show a show, dato a dato"),
]
pw3 = (TW - 20) / 3
px3 = MG
ph3 = 80
for pnum, ptit, psub in pasos:
    c.setFillColor(DARK)
    c.setStrokeColor(GOLD)
    c.setLineWidth(1.2)
    c.roundRect(px3, y - ph3, pw3, ph3, 6, fill=1, stroke=1)
    c.setFillColor(GOLD)
    c.setFont("Helvetica-Bold", 20)
    c.drawCentredString(px3 + pw3 / 2, y - 26, pnum)
    c.setFillColor(WITE)
    c.setFont("Helvetica-Bold", 9.5)
    c.drawCentredString(px3 + pw3 / 2, y - 44, ptit)
    c.setFillColor(WITE)
    c.setFont("Helvetica", 8)
    c.drawCentredString(px3 + pw3 / 2, y - 58, psub)
    if pnum != "03":
        c.setStrokeColor(GOLD)
        c.setLineWidth(1.5)
        ax3 = px3 + pw3 + 3
        ay3 = y - ph3 / 2
        c.line(ax3, ay3, ax3 + 5, ay3)
        c.line(ax3 + 3, ay3 - 3, ax3 + 7, ay3)
        c.line(ax3 + 3, ay3 + 3, ax3 + 7, ay3)
    px3 += pw3 + 10
y -= ph3 + 22

# Logo centrado grande al final
try:
    img_f = ImageReader(str(LOGO_SYMBOL))
    lfw, lfh = 220, 126
    c.drawImage(img_f, (W - lfw) / 2, y - lfh, width=lfw, height=lfh, mask="auto")
    y -= lfh + 14
except Exception as e:
    print(f"logo final: {e}")

# Tech footer
thin_sep(c, y)
y -= 16
c.setFillColor(GOLD)
c.setFont("Helvetica", 7.5)
c.drawCentredString(W / 2, y,
    "Tecnología ESA Copernicus · NASA Earthdata · Estándar FIFA / UEFA / ISO")
y -= 14
c.setFillColor(WITE)
c.setFont("Helvetica-Bold", 8)
c.drawCentredString(W / 2, y, "Faro Protocol · 2026")

c.showPage()

# ── SAVE ─────────────────────────────────────────────────────────────────
c.save()
try:
    LOGO_SYMBOL.unlink()
except Exception:
    pass
print(f"PDF generado: {OUT_PDF}")
