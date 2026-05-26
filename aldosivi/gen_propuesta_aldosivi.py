"""Generate aldosivi/propuesta_aldosivi.pdf using reportlab."""

import os
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.units import cm
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, HRFlowable, Table, TableStyle, Image
)
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_JUSTIFY
from reportlab.platypus import PageBreak

# ── Colores ──────────────────────────────────────────────────────────────────
BG      = colors.HexColor("#0d0d0d")
GOLD    = colors.HexColor("#c9a84c")
WHITE   = colors.white
GRAY    = colors.HexColor("#888888")
DARK_GRAY = colors.HexColor("#1a1a1a")

# ── Rutas ────────────────────────────────────────────────────────────────────
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(BASE_DIR)
IMG_PATH = os.path.join(ROOT_DIR, "velez", "heatmaps", "heatmap_amalfitani_shadow.png")
OUT_PATH = os.path.join(BASE_DIR, "propuesta_aldosivi.pdf")

W, H = A4

# ── Estilos ──────────────────────────────────────────────────────────────────
def style(name, **kw):
    base = dict(
        fontName="Helvetica",
        fontSize=11,
        textColor=WHITE,
        leading=16,
        spaceAfter=6,
    )
    base.update(kw)
    return ParagraphStyle(name, **base)

S_BRAND      = style("brand",     fontName="Helvetica-Bold", fontSize=42,
                     textColor=GOLD, alignment=TA_CENTER, leading=50, spaceAfter=10)
S_SUBTITLE   = style("subtitle",  fontName="Helvetica-Bold", fontSize=20,
                     textColor=WHITE, alignment=TA_CENTER, leading=26, spaceAfter=8)
S_DATE       = style("date",      fontSize=12, textColor=GRAY, alignment=TA_CENTER)
S_TAG        = style("tag",       fontSize=11, textColor=GOLD, alignment=TA_CENTER,
                     leading=18)
S_H1         = style("h1",        fontName="Helvetica-Bold", fontSize=22,
                     textColor=GOLD, leading=28, spaceAfter=14)
S_BODY       = style("body",      fontSize=11, textColor=WHITE, leading=18,
                     alignment=TA_JUSTIFY, spaceAfter=8)
S_BULLET     = style("bullet",    fontSize=11, textColor=WHITE, leading=18,
                     leftIndent=16, spaceAfter=6)
S_ROI_TITLE  = style("roi_title", fontName="Helvetica-Bold", fontSize=13,
                     textColor=GOLD, leading=18, spaceAfter=4)
S_ROI_BODY   = style("roi_body",  fontSize=11, textColor=WHITE, leading=16, spaceAfter=6)
S_STEP       = style("step",      fontSize=11, textColor=WHITE, leading=18,
                     spaceAfter=6)
S_CAPTION    = style("caption",   fontSize=9, textColor=GRAY, alignment=TA_CENTER)
S_PRICE_BIG  = style("price_big", fontName="Helvetica-Bold", fontSize=28,
                     textColor=GOLD, alignment=TA_CENTER, leading=34, spaceAfter=4)
S_PRICE_SUB  = style("price_sub", fontSize=11, textColor=GRAY, alignment=TA_CENTER,
                     leading=16)
S_ITALIC     = style("italic",    fontName="Helvetica-Oblique", fontSize=10,
                     textColor=GRAY, alignment=TA_JUSTIFY, leading=16)

# ── Fondo ────────────────────────────────────────────────────────────────────
def dark_background(canvas, doc):
    canvas.saveState()
    canvas.setFillColor(BG)
    canvas.rect(0, 0, W, H, fill=1, stroke=0)
    canvas.restoreState()

# ── Separador dorado ─────────────────────────────────────────────────────────
def gold_rule():
    return HRFlowable(width="100%", thickness=0.8, color=GOLD, spaceAfter=12, spaceBefore=6)

# ── Páginas ──────────────────────────────────────────────────────────────────
def page_portada():
    return [
        Spacer(1, 3.5 * cm),
        Paragraph("FARO PROTOCOL", S_BRAND),
        gold_rule(),
        Spacer(1, 0.5 * cm),
        Paragraph("Aldosivi — Gestión Inteligente del Predio", S_SUBTITLE),
        Spacer(1, 1.2 * cm),
        Paragraph("26 de mayo de 2026", S_DATE),
        Spacer(1, 1.8 * cm),
        Paragraph(
            "Monitoreo satelital continuo · Sin sensores · Sin instalaciones",
            S_TAG,
        ),
    ]


def page_problema():
    return [
        Paragraph("EL PROBLEMA ACTUAL", S_H1),
        gold_rule(),
        Paragraph(
            "El predio se gestiona sin datos objetivos. Decisiones de riego, "
            "siembra y mantenimiento se toman a ojo.",
            S_BODY,
        ),
        Paragraph(
            "Costos evitables que se repiten semana a semana sin saberlo.",
            S_BODY,
        ),
        Paragraph(
            "Sin historial, sin alertas tempranas, sin forma de medir si las "
            "intervenciones funcionaron.",
            S_BODY,
        ),
    ]


def page_que_es():
    bullets = [
        "NDVI semanal por cancha",
        "Zonas de compactación y estrés hídrico",
        "Eficiencia del sistema de riego",
        "Deformaciones estructurales (InSAR)",
        "Clima local integrado al reporte",
    ]
    items = [Paragraph(f"· {b}", S_BULLET) for b in bullets]
    return [
        Paragraph("QUÉ ES FARO PROTOCOL", S_H1),
        gold_rule(),
        Paragraph(
            "Sistema de monitoreo satelital continuo. Sin sensores, sin instalaciones "
            "físicas, sin intervenir el predio.",
            S_BODY,
        ),
        Paragraph(
            "Los satélites Sentinel-2 ya están en órbita — Faro Protocol los interpreta "
            "y los convierte en decisiones concretas para el club.",
            S_BODY,
        ),
        Spacer(1, 0.4 * cm),
        *items,
    ]


def page_roi():
    roi_data = [
        ("RIEGO",         "Ahorro estimado 25–40% en agua y energía"),
        ("AGROQUÍMICOS",  "Reducción de hasta 30% en fungicidas y fertilizantes"),
        ("MANO DE OBRA",  "Recupera 4–6 horas semanales"),
        ("PREVENCIÓN",    "Evitar resembrado de emergencia (USD 800–2000 por cancha)"),
    ]

    blocks = []
    for title, body in roi_data:
        inner = [
            [Paragraph(title, S_ROI_TITLE)],
            [Paragraph(body, S_ROI_BODY)],
        ]
        t = Table(inner, colWidths=[14 * cm])
        t.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), DARK_GRAY),
            ("BOX",        (0, 0), (-1, -1), 0.8, GOLD),
            ("TOPPADDING",    (0, 0), (-1, -1), 10),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
            ("LEFTPADDING",   (0, 0), (-1, -1), 14),
            ("RIGHTPADDING",  (0, 0), (-1, -1), 14),
        ]))
        blocks.append(t)
        blocks.append(Spacer(1, 0.35 * cm))

    return [
        Paragraph("ROI", S_H1),
        Paragraph(
            "El sistema no es un gasto — es una reducción de costos medible",
            S_SUBTITLE,
        ),
        gold_rule(),
        Spacer(1, 0.3 * cm),
        *blocks,
    ]


def page_como_funciona():
    steps = [
        "Cada lunes a las 07:00 el sistema genera el reporte automáticamente.",
        "Cada rol recibe solo lo que le compete — por email.",
        "Panel web privado sin instalación ni app.",
        "Sin dependencia de hardware — Faro Protocol mantiene todo.",
        "El club no gestiona nada técnico.",
    ]
    items = [Paragraph(f"{i+1}.  {s}", S_STEP) for i, s in enumerate(steps)]
    return [
        Paragraph("CÓMO FUNCIONA", S_H1),
        gold_rule(),
        *items,
    ]


def page_caso_velez():
    elems = [
        Paragraph("CASO REAL — VÉLEZ SARSFIELD", S_H1),
        gold_rule(),
        Paragraph(
            "Sistema activo desde mayo 2026. 7 personas del club reciben reportes "
            "diferenciados por rol cada lunes. Datos reales — identidades omitidas.",
            S_BODY,
        ),
        Spacer(1, 0.5 * cm),
    ]

    if os.path.exists(IMG_PATH):
        img = Image(IMG_PATH, width=13 * cm, height=7 * cm)
        img.hAlign = "CENTER"
        elems.append(img)
        elems.append(Spacer(1, 0.3 * cm))
        elems.append(Paragraph(
            "Análisis de sombras estructurales · Estadio J. Amalfitani · Sentinel-2",
            S_CAPTION,
        ))
    else:
        elems.append(Paragraph(f"[imagen no disponible: {IMG_PATH}]", S_CAPTION))

    return elems


def page_inversion():
    return [
        Paragraph("INVERSIÓN", S_H1),
        gold_rule(),
        Spacer(1, 0.4 * cm),
        Paragraph("Valor de mercado", S_PRICE_SUB),
        Paragraph("USD 4.500 / mes", S_PRICE_BIG),
        Spacer(1, 0.6 * cm),
        Paragraph("Aldosivi pagaría", S_PRICE_SUB),
        Paragraph("USD 250 / mes", S_PRICE_BIG),
        Spacer(1, 1 * cm),
        Paragraph(
            "Faro Protocol nació en Mar del Plata. Somos simpatizantes del club y "
            "queremos que Aldosivi tenga acceso a la misma tecnología que usan clubes "
            "de Primera División. El precio cubre únicamente los costos operativos — "
            "el resto es nuestra contribución.",
            S_ITALIC,
        ),
    ]


# ── Main ─────────────────────────────────────────────────────────────────────
def build():
    os.makedirs(BASE_DIR, exist_ok=True)

    doc = SimpleDocTemplate(
        OUT_PATH,
        pagesize=A4,
        leftMargin=2.5 * cm,
        rightMargin=2.5 * cm,
        topMargin=2.5 * cm,
        bottomMargin=2.5 * cm,
    )

    pages = [
        page_portada(),
        page_problema(),
        page_que_es(),
        page_roi(),
        page_como_funciona(),
        page_caso_velez(),
        page_inversion(),
    ]

    story = []
    for i, page in enumerate(pages):
        story.extend(page)
        if i < len(pages) - 1:
            story.append(PageBreak())

    doc.build(story, onFirstPage=dark_background, onLaterPages=dark_background)
    print(f"PDF generado: {OUT_PATH}")


if __name__ == "__main__":
    build()
