#!/usr/bin/env python3
"""gen_velez_manuales.py — Genera 7 manuales PDF profesionales para destinatarios Vélez."""

import pathlib
import shutil
from reportlab.pdfgen import canvas
from reportlab.lib.colors import HexColor
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfbase.ttfonts import TTFont

# Registrar Arial como fuente con soporte completo de acentos españoles
pdfmetrics.registerFont(TTFont('Helvetica',      r'C:\Windows\Fonts\arial.ttf'))
pdfmetrics.registerFont(TTFont('Helvetica-Bold', r'C:\Windows\Fonts\arialbd.ttf'))

# ── Paleta ──────────────────────────────────────────────────────────────
BG    = HexColor('#000000')
GOLD  = HexColor('#c9a84c')
WHITE = HexColor('#f0ede8')
LGRAY = HexColor('#aaaaaa')
DGRAY = HexColor('#333333')
MGRAY = HexColor('#1e1e1e')

W, H = 595.27, 841.89   # A4

OUT_DIR     = pathlib.Path(__file__).parent / 'reportes_velez'
DESKTOP_DIR = pathlib.Path.home() / 'Desktop'
OUT_DIR.mkdir(exist_ok=True)

# ── Contenido de cada manual ─────────────────────────────────────────────
MANUALS = [
    {
        'filename': 'manual_velez_roger.pdf',
        'name': 'Roger Bernal',
        'role': 'Responsable de Campo de Juego',
        'institution': 'Club Atlético Vélez Sarsfield',
        'intro': (
            'Este manual explica cómo interpretar los reportes satelitales semanales '
            'que recibís cada lunes. Los datos provienen de sensores ESA Sentinel-2, '
            'Sentinel-1 SAR y NASA SMAP — imágenes independientes y verificables.'
        ),
        'sections': [
            ('Tu reporte semanal', [
                'Recibís el REPORTE CANCHERO — análisis detallado del estado de las canchas del Campo Amalfitani.',
                'El mapa NDVI muestra el vigor de la cobertura vegetal: verde oscuro = salud óptima, amarillo/rojo = estrés o deterioro.',
                'El análisis SAR (radar) detecta humedad subsuperficial invisible al ojo humano.',
            ]),
            ('Índices clave para tu trabajo', [
                'NDVI > 0.7: Canchas en condición óptima para competencia oficial.',
                'NDVI 0.5-0.7: Condición aceptable, monitoreo preventivo recomendado.',
                'NDVI < 0.5: Intervención necesaria — riego, abono o resiembra urgente.',
                'Soil Moisture Index (SMI): 0 = seco crítico, 1 = saturado. Óptimo entre 0.4 y 0.7.',
            ]),
            ('Plan de acción semanal', [
                'Si NDVI cae más del 10% en dos semanas consecutivas: iniciar protocolo de recuperación.',
                'Correlacionar datos SAR con historial de lluvias para ajustar programas de riego.',
                'Zonas marcadas en rojo en el mapa = prioridad alta para intervención esta semana.',
            ]),
        ],
        'footer': 'Datos actualizados cada lunes 07:00h ART  |  Resolución espacial 10m  |  Faro Protocol',
    },
    {
        'filename': 'manual_velez_juan.pdf',
        'name': 'Juan González',
        'role': 'Intendente de Villa Olímpica',
        'institution': 'Club Atlético Vélez Sarsfield',
        'intro': (
            'Tu manual de monitoreo satelital cubre el estado integral de Villa Olímpica: '
            'canchas, zona agropecuaria y sistemas de drenaje, con datos de ESA Copernicus y NASA '
            'procesados automáticamente cada semana.'
        ),
        'sections': [
            ('Tus reportes semanales', [
                'REPORTE CANCHERO: Estado de superficies de juego — NDVI, humedad, zonas de estrés.',
                'REPORTE AGRO FINAL: Análisis agronómico de la zona verde del predio — suelo, cobertura, tendencias.',
                'Ambos reportes llegan cada lunes 07:00h ART a tu correo institucional.',
            ]),
            ('Mantenimiento predictivo', [
                'El sistema identifica automáticamente zonas de riesgo antes de que sean visibles a nivel del suelo.',
                'Planificá el mantenimiento de drenaje basándote en los mapas de humedad del suelo.',
                'Los datos históricos permiten anticipar problemas estacionales con 2-3 semanas de anticipación.',
            ]),
            ('Coordinación con equipos', [
                'Compartí el mapa NDVI con el equipo de mantenimiento para asignar prioridades de trabajo.',
                'El índice SMI te ayuda a optimizar el uso del sistema de riego automatizado.',
                'En caso de anomalía crítica el sistema envía alerta WhatsApp además del email semanal.',
            ]),
        ],
        'footer': 'Cobertura: Campo Amalfitani + Zona Agropecuaria  |  Resolución 10m  |  Faro Protocol',
    },
    {
        'filename': 'manual_velez_banchero.pdf',
        'name': 'Fernando Banchero',
        'role': 'Gerente de Operaciones',
        'institution': 'Club Atlético Vélez Sarsfield',
        'intro': (
            'Como Gerente de Operaciones recibís el conjunto completo de reportes Faro: visión ejecutiva '
            'del predio, análisis agronómico y proyección del sistema solar, integrados desde satélites '
            'ESA Sentinel y NASA SMAP en un dashboard semanal.'
        ),
        'sections': [
            ('Tus 3 reportes semanales', [
                'REPORTE PRINCIPAL VÉLEZ: KPIs ejecutivos del predio completo — score de salud, alertas, tendencias.',
                'REPORTE SOLAR v2: Estado del sistema fotovoltaico, irradiación estimada, anomalías detectadas.',
                'REPORTE AGRO FINAL: Análisis agronómico completo con mapas de cobertura y humedad del suelo.',
            ]),
            ('Métricas operativas clave', [
                'Health Score del predio: índice 0-100 que sintetiza todos los indicadores en un número único.',
                'Potential Solar kWh: estimación semanal de generación basada en condiciones satelitales.',
                'Alert Level: BAJO / MEDIO / ALTO según cantidad y severidad de anomalías detectadas.',
            ]),
            ('Gestión de recursos', [
                'Optimizá el presupuesto de mantenimiento priorizando las zonas con mayor índice de riesgo.',
                'Correlacioná los datos solares con el consumo energético para maximizar la autogeneración.',
                'Usá el Health Score como KPI de gestión en reportes a la Comisión Ejecutiva.',
            ]),
        ],
        'footer': '3 reportes satelitales  |  Visión 360° del predio  |  Actualización semanal  |  Faro Protocol',
    },
    {
        'filename': 'manual_velez_pait.pdf',
        'name': 'Sebastián Pait',
        'role': 'Director Deportivo',
        'institution': 'Club Atlético Vélez Sarsfield',
        'intro': (
            'Tu monitoreo satelital está orientado a la calidad de las superficies de entrenamiento '
            'y competencia, con datos Sentinel-2 de alta resolución para decisiones deportivas '
            'fundadas en evidencia objetiva e independiente.'
        ),
        'sections': [
            ('Tus reportes semanales', [
                'REPORTE CANCHERO: Estado de canchas principales — NDVI, firmeza estimada, zonas de riesgo.',
                'REPORTE AGRO FINAL: Condiciones del terreno de entrenamiento y zonas auxiliares del predio.',
            ]),
            ('Planificación deportiva basada en datos', [
                'NDVI > 0.7 en canchas principales = superficie óptima para partidos de alta exigencia.',
                'Usá los datos de humedad para planificar días de uso intensivo vs. días de recuperación.',
                'Zonas marcadas en amarillo o rojo requieren reducción de carga de entrenamiento esa semana.',
            ]),
            ('Ventanas de recuperación y predicción', [
                'El sistema modela tiempos de recuperación según condición actual, historial de uso y clima.',
                'Podés solicitar reportes comparativos entre canchas para asignar equipos por prioridad.',
                'La correlación NDVI-SAR predice firmeza del suelo para los próximos 7 días con alta precisión.',
            ]),
        ],
        'footer': 'Superficies analizadas por satélite  |  Decisiones respaldadas por datos  |  Faro Protocol',
    },
    {
        'filename': 'manual_velez_berlanga.pdf',
        'name': 'Fabián Berlanga',
        'role': 'Presidente',
        'institution': 'Club Atlético Vélez Sarsfield',
        'intro': (
            'Como Presidente del Club recibís el informe ejecutivo semanal Faro Protocol — una visión '
            'estratégica del estado del patrimonio inmobiliario y productivo del Club, basada en datos '
            'satelitales de organismos internacionales independientes.'
        ),
        'sections': [
            ('Tu informe ejecutivo semanal', [
                'Recibís el INFORME EJECUTIVO VÉLEZ con KPIs de alto nivel: Health Score, alertas críticas y tendencias.',
                'El REPORTE AGRO FINAL y SOLAR v2 complementan la visión sobre producción y energía del predio.',
                'Todos los datos provienen de satélites públicos ESA y NASA — fuentes verificables e independientes.',
            ]),
            ('Indicadores de valor patrimonial', [
                'Health Score > 70: Predio en condición óptima — sin inversiones urgentes requeridas.',
                'Health Score 50-70: Mantenimiento preventivo recomendado — bajo costo, alta efectividad.',
                'Health Score < 50: Evaluación inmediata recomendada — posible impacto en el valor del activo.',
            ]),
            ('Valor estratégico del monitoreo', [
                'Documentación satelital continua que protege el valor del patrimonio del Club.',
                'Datos independientes para negociaciones, auditorías o reportes a socios e inversores.',
                'Visibilidad sobre el retorno de inversiones en infraestructura verde y sistemas solares.',
            ]),
        ],
        'footer': 'Informe ejecutivo semanal  |  Patrimonio monitoreado por satélite  |  Faro Protocol',
    },
    {
        'filename': 'manual_velez_nelson.pdf',
        'name': 'Nelson Pugliese',
        'role': 'Vicepresidente',
        'institution': 'Club Atlético Vélez Sarsfield',
        'intro': (
            'Como Vicepresidente recibís el informe ejecutivo semanal Faro Protocol, con indicadores '
            'de excelencia operativa, riesgos detectados y métricas de sustentabilidad del predio — '
            'actualizados cada lunes con datos satelitales en tiempo casi real.'
        ),
        'sections': [
            ('Tu informe semanal', [
                'INFORME EJECUTIVO: Health Score del predio, alertas activas, tendencias de los últimos 30 días.',
                'REPORTE SOLAR v2: Eficiencia del sistema de energía renovable y proyección de generación.',
                'REPORTE AGRO FINAL: Estado de las zonas productivas y de esparcimiento del predio.',
            ]),
            ('Métricas de excelencia operativa', [
                'Alert Level BAJO: Operación normal — sin intervenciones urgentes requeridas esta semana.',
                'Alert Level MEDIO: 1-2 zonas requieren atención — coordinación con responsables de área.',
                'Alert Level ALTO: Múltiples zonas afectadas — evaluación gerencial inmediata recomendada.',
            ]),
            ('Sustentabilidad y eficiencia', [
                'El monitoreo solar permite evaluar el ROI del sistema fotovoltaico instalado.',
                'Los datos agronómicos apoyan decisiones sobre uso eficiente del agua y el suelo.',
                'El reporte histórico documenta la mejora continua del predio bajo gestión satelital.',
            ]),
        ],
        'footer': 'Excelencia operativa respaldada por datos satelitales  |  Faro Protocol',
    },
    {
        'filename': 'manual_velez_aveleyra.pdf',
        'name': 'Alberto Aveleyra',
        'role': 'Gerente General',
        'institution': 'Club Atlético Vélez Sarsfield',
        'intro': (
            'Como Gerente General recibís el dashboard ejecutivo completo de Faro Protocol, integrando '
            'análisis de infraestructura, producción y energía del predio en un sistema de gestión '
            'basado en datos satelitales de ESA Copernicus y NASA.'
        ),
        'sections': [
            ('Tu informe ejecutivo', [
                'INFORME EJECUTIVO VÉLEZ: Dashboard unificado con Health Score, KPIs y plan de acción semanal.',
                'REPORTE AGRO FINAL: Análisis completo de zonas verdes, productividad y estado del suelo.',
                'REPORTE SOLAR v2: Generación estimada, anomalías del sistema y proyección mensual.',
            ]),
            ('Gestión por objetivos y KPIs', [
                'Establecé benchmarks semanales usando el Health Score como indicador central de gestión.',
                'Asigná responsabilidades por área según las alertas detectadas cada lunes en el informe.',
                'El historial de datos permite evaluar el impacto cuantitativo de intervenciones de mantenimiento.',
            ]),
            ('Costo-beneficio del monitoreo satelital', [
                'Detección temprana: el 80% de los problemas identificados antes de volverse críticos.',
                'Reducción estimada de costos de mantenimiento correctivo: 30-40% en el primer año.',
                'ROI del sistema solar documentado semana a semana con datos de irradiación real.',
            ]),
        ],
        'footer': 'Dashboard ejecutivo satelital  |  Gestión basada en evidencia  |  Faro Protocol',
    },
]


# ── Helpers ──────────────────────────────────────────────────────────────

def bg_fill(c):
    c.setFillColor(BG)
    c.rect(0, 0, W, H, fill=1, stroke=0)


def hline(c, y, x0=40*mm, x1=None, col=GOLD, lw=0.5):
    if x1 is None:
        x1 = W - 40*mm
    c.setStrokeColor(col)
    c.setLineWidth(lw)
    c.line(x0, y, x1, y)


def wrap(text, font, size, max_w):
    """Word-wrap text to fit within max_w points. Returns list of lines."""
    words = text.split()
    lines, cur = [], ''
    for w in words:
        test = (cur + ' ' + w).strip()
        if stringWidth(test, font, size) <= max_w:
            cur = test
        else:
            if cur:
                lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines


# ── Page renderers ───────────────────────────────────────────────────────

def draw_cover(c, m):
    bg_fill(c)

    hline(c, H - 18*mm)

    c.setFont('Helvetica-Bold', 10)
    c.setFillColor(GOLD)
    c.drawCentredString(W / 2, H - 27*mm, 'F A R O   P R O T O C O L')

    c.setFont('Helvetica', 7.5)
    c.setFillColor(LGRAY)
    c.drawCentredString(W / 2, H - 33*mm, 'MONITOREO SATELITAL AVANZADO')

    # satélite gráfico vectorial
    cx = W / 2
    cy = H - 57*mm

    c.setFillColor(MGRAY)
    c.setStrokeColor(GOLD)
    c.setLineWidth(1.2)
    c.roundRect(cx - 8*mm, cy - 4*mm, 16*mm, 8*mm, 2*mm, fill=1, stroke=1)

    c.setFillColor(HexColor('#0d2540'))
    c.setStrokeColor(GOLD)
    c.setLineWidth(0.8)
    c.rect(cx - 22*mm, cy - 2.5*mm, 12*mm, 5*mm, fill=1, stroke=1)
    c.rect(cx + 10*mm,  cy - 2.5*mm, 12*mm, 5*mm, fill=1, stroke=1)

    c.setStrokeColor(GOLD)
    c.setLineWidth(0.8)
    c.line(cx, cy + 4*mm, cx, cy + 10*mm)
    c.circle(cx, cy + 11.5*mm, 1.5*mm, fill=0, stroke=1)

    c.setFont('Helvetica-Bold', 30)
    c.setFillColor(GOLD)
    c.drawCentredString(W / 2, H / 2 + 26*mm, m['name'])

    c.setFont('Helvetica', 13)
    c.setFillColor(WHITE)
    c.drawCentredString(W / 2, H / 2 + 14*mm, m['role'])

    c.setFont('Helvetica', 10)
    c.setFillColor(LGRAY)
    c.drawCentredString(W / 2, H / 2 + 6*mm, m['institution'])

    hline(c, H / 2 + 0.5*mm, W / 2 - 38*mm, W / 2 + 38*mm, GOLD, 0.4)

    c.setFont('Helvetica-Bold', 11)
    c.setFillColor(WHITE)
    c.drawCentredString(W / 2, H / 2 - 9*mm, 'MANUAL DE USO PERSONAL')

    c.setFont('Helvetica', 9)
    c.setFillColor(LGRAY)
    c.drawCentredString(W / 2, H / 2 - 16*mm, 'Guía de interpretación de reportes satelitales semanales')

    agencies = [
        ('ESA',        'Agencia Espacial Europea'),
        ('COPERNICUS', 'Programa Europeo'),
        ('NASA',       'Agencia Espacial USA'),
    ]
    bw = 44*mm
    gap = 5*mm
    total_w = len(agencies) * bw + (len(agencies) - 1) * gap
    bx0 = W / 2 - total_w / 2
    by  = H / 2 - 38*mm

    for i, (ag, sub) in enumerate(agencies):
        bx = bx0 + i * (bw + gap)
        c.setFillColor(MGRAY)
        c.setStrokeColor(GOLD)
        c.setLineWidth(0.5)
        c.roundRect(bx, by - 10*mm, bw, 18*mm, 2.5*mm, fill=1, stroke=1)
        c.setFont('Helvetica-Bold', 10)
        c.setFillColor(GOLD)
        c.drawCentredString(bx + bw / 2, by + 3*mm, ag)
        c.setFont('Helvetica', 6)
        c.setFillColor(LGRAY)
        c.drawCentredString(bx + bw / 2, by - 4*mm, sub)

    hline(c, 18*mm)
    c.setFont('Helvetica', 7)
    c.setFillColor(LGRAY)
    c.drawCentredString(W / 2, 12*mm, m['footer'])


def draw_content(c, m):
    bg_fill(c)
    hline(c, H - 18*mm)

    c.setFont('Helvetica-Bold', 7.5)
    c.setFillColor(GOLD)
    header_text = 'FARO PROTOCOL  ·  ' + m['name'].upper() + '  ·  ' + m['role'].upper()
    c.drawString(40*mm, H - 27*mm, header_text)

    hline(c, H - 32*mm, col=DGRAY, lw=0.3)

    y = H - 43*mm
    max_w = W - 80*mm
    for line in wrap(m['intro'], 'Helvetica', 9, max_w):
        c.setFont('Helvetica', 9)
        c.setFillColor(LGRAY)
        c.drawString(40*mm, y, line)
        y -= 5*mm

    y -= 6*mm

    for title, bullets in m['sections']:
        c.setFont('Helvetica-Bold', 11)
        c.setFillColor(GOLD)
        c.drawString(40*mm, y, title.upper())
        y -= 3.5*mm
        hline(c, y, 40*mm, 40*mm + 65*mm, GOLD, 0.3)
        y -= 7*mm

        for bullet in bullets:
            c.setFillColor(GOLD)
            c.circle(43.5*mm, y + 1.8*mm, 1.3*mm, fill=1, stroke=0)

            lines = wrap(bullet, 'Helvetica', 9, W - 94*mm)
            c.setFont('Helvetica', 9)
            c.setFillColor(WHITE)
            for j, bl in enumerate(lines):
                c.drawString(47.5*mm, y, bl)
                if j < len(lines) - 1:
                    y -= 4.8*mm
            y -= 6.5*mm

        y -= 6*mm

    hline(c, 18*mm)
    c.setFont('Helvetica', 7)
    c.setFillColor(LGRAY)
    c.drawCentredString(
        W / 2, 12*mm,
        'Faro Protocol  |  protocolfaro@gmail.com  |  Datos: ESA Sentinel-2, Sentinel-1 SAR, NASA SMAP'
    )


# ── Main ─────────────────────────────────────────────────────────────────

def generate_manual(m):
    out = OUT_DIR / m['filename']
    c = canvas.Canvas(str(out), pagesize=(W, H))
    draw_cover(c, m)
    c.showPage()
    draw_content(c, m)
    c.showPage()
    c.save()
    # También copiar al Escritorio
    desktop_out = DESKTOP_DIR / m['filename']
    shutil.copy2(out, desktop_out)
    return out


if __name__ == '__main__':
    print('Generando 7 manuales PDF con acentos corregidos...\n')
    for m in MANUALS:
        out = generate_manual(m)
        size_kb = out.stat().st_size // 1024
        print(f'  OK  {m["filename"]}  ({size_kb} KB)  — {m["name"]}, {m["role"]}')
    print(f'\n7 PDFs generados en:')
    print(f'  {OUT_DIR}')
    print(f'  {DESKTOP_DIR}')
