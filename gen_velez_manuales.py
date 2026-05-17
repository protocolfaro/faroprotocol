#!/usr/bin/env python3
"""gen_velez_manuales.py — Genera 7 manuales PDF profesionales para destinatarios Vélez."""

import pathlib
from reportlab.pdfgen import canvas
from reportlab.lib.colors import HexColor
from reportlab.lib.units import mm
from reportlab.pdfbase.pdfmetrics import stringWidth

# ── Paleta ──────────────────────────────────────────────────────────────
BG    = HexColor('#000000')
GOLD  = HexColor('#c9a84c')
WHITE = HexColor('#f0ede8')
LGRAY = HexColor('#aaaaaa')
DGRAY = HexColor('#333333')
MGRAY = HexColor('#1e1e1e')

W, H = 595.27, 841.89   # A4

OUT_DIR = pathlib.Path(__file__).parent / 'reportes_velez'
OUT_DIR.mkdir(exist_ok=True)

# ── Contenido de cada manual ─────────────────────────────────────────────
MANUALS = [
    {
        'filename': 'manual_velez_roger.pdf',
        'name': 'Roger Bernal',
        'role': 'Responsable de Campo de Juego',
        'institution': 'Club Atletico Velez Sarsfield',
        'intro': (
            'Este manual explica como interpretar los reportes satelitales semanales '
            'que recibis cada lunes. Los datos provienen de sensores ESA Sentinel-2, '
            'Sentinel-1 SAR y NASA SMAP — imagenes independientes y verificables.'
        ),
        'sections': [
            ('Tu reporte semanal', [
                'Recibis el REPORTE CANCHERO — analisis detallado del estado de las canchas del Campo Amalfitani.',
                'El mapa NDVI muestra el vigor de la cobertura vegetal: verde oscuro = salud optima, amarillo/rojo = estres o deterioro.',
                'El analisis SAR (radar) detecta humedad subsuperficial invisible al ojo humano.',
            ]),
            ('Indices clave para tu trabajo', [
                'NDVI > 0.7: Canchas en condicion optima para competencia oficial.',
                'NDVI 0.5-0.7: Condicion aceptable, monitoreo preventivo recomendado.',
                'NDVI < 0.5: Intervencion necesaria — riego, abono o resiembra urgente.',
                'Soil Moisture Index (SMI): 0 = seco critico, 1 = saturado. Optimo entre 0.4 y 0.7.',
            ]),
            ('Plan de accion semanal', [
                'Si NDVI cae mas del 10% en dos semanas consecutivas: iniciar protocolo de recuperacion.',
                'Correlacionar datos SAR con historial de lluvias para ajustar programas de riego.',
                'Zonas marcadas en rojo en el mapa = prioridad alta para intervencion esta semana.',
            ]),
        ],
        'footer': 'Datos actualizados cada lunes 07:00h ART  |  Resolucion espacial 10m  |  Faro Protocol',
    },
    {
        'filename': 'manual_velez_juan.pdf',
        'name': 'Juan Gonzalez',
        'role': 'Intendente de Villa Olimpica',
        'institution': 'Club Atletico Velez Sarsfield',
        'intro': (
            'Tu manual de monitoreo satelital cubre el estado integral de Villa Olimpica: '
            'canchas, zona agropecuaria y sistemas de drenaje, con datos de ESA Copernicus y NASA '
            'procesados automaticamente cada semana.'
        ),
        'sections': [
            ('Tus reportes semanales', [
                'REPORTE CANCHERO: Estado de superficies de juego — NDVI, humedad, zonas de estres.',
                'REPORTE AGRO FINAL: Analisis agronomico de la zona verde del predio — suelo, cobertura, tendencias.',
                'Ambos reportes llegan cada lunes 07:00h ART a tu correo institucional.',
            ]),
            ('Mantenimiento predictivo', [
                'El sistema identifica automaticamente zonas de riesgo antes de que sean visibles a nivel del suelo.',
                'Planifica mantenimiento de drenaje basandote en los mapas de humedad del suelo.',
                'Los datos historicos permiten anticipar problemas estacionales con 2-3 semanas de anticipacion.',
            ]),
            ('Coordinacion con equipos', [
                'Comparte el mapa NDVI con el equipo de mantenimiento para asignar prioridades de trabajo.',
                'El indice SMI te ayuda a optimizar el uso del sistema de riego automatizado.',
                'En caso de anomalia critica el sistema envia alerta WhatsApp ademas del email semanal.',
            ]),
        ],
        'footer': 'Cobertura: Campo Amalfitani + Zona Agropecuaria  |  Resolucion 10m  |  Faro Protocol',
    },
    {
        'filename': 'manual_velez_banchero.pdf',
        'name': 'Fernando Banchero',
        'role': 'Gerente de Operaciones',
        'institution': 'Club Atletico Velez Sarsfield',
        'intro': (
            'Como Gerente de Operaciones recibis el conjunto completo de reportes Faro: vision ejecutiva '
            'del predio, analisis agronomico y proyeccion del sistema solar, integrados desde satelites '
            'ESA Sentinel y NASA SMAP en un dashboard semanal.'
        ),
        'sections': [
            ('Tus 3 reportes semanales', [
                'REPORTE PRINCIPAL VELEZ: KPIs ejecutivos del predio completo — score de salud, alertas, tendencias.',
                'REPORTE SOLAR v2: Estado del sistema fotovoltaico, irradiacion estimada, anomalias detectadas.',
                'REPORTE AGRO FINAL: Analisis agronomico completo con mapas de cobertura y humedad del suelo.',
            ]),
            ('Metricas operativas clave', [
                'Health Score del predio: indice 0-100 que sintetiza todos los indicadores en un numero unico.',
                'Potential Solar kWh: estimacion semanal de generacion basada en condiciones satelitales.',
                'Alert Level: BAJO / MEDIO / ALTO segun cantidad y severidad de anomalias detectadas.',
            ]),
            ('Gestion de recursos', [
                'Optimiza el presupuesto de mantenimiento priorizando las zonas con mayor indice de riesgo.',
                'Correlaciona los datos solares con el consumo energetico para maximizar la autogeneracion.',
                'Usa el Health Score como KPI de gestion en reportes a la Comision Ejecutiva.',
            ]),
        ],
        'footer': '3 reportes satelitales  |  Vision 360 del predio  |  Actualizacion semanal  |  Faro Protocol',
    },
    {
        'filename': 'manual_velez_pait.pdf',
        'name': 'Sebastian Pait',
        'role': 'Director Deportivo',
        'institution': 'Club Atletico Velez Sarsfield',
        'intro': (
            'Tu monitoreo satelital esta orientado a la calidad de las superficies de entrenamiento '
            'y competencia, con datos Sentinel-2 de alta resolucion para decisiones deportivas '
            'fundadas en evidencia objetiva e independiente.'
        ),
        'sections': [
            ('Tus reportes semanales', [
                'REPORTE CANCHERO: Estado de canchas principales — NDVI, firmeza estimada, zonas de riesgo.',
                'REPORTE AGRO FINAL: Condiciones del terreno de entrenamiento y zonas auxiliares del predio.',
            ]),
            ('Planificacion deportiva basada en datos', [
                'NDVI > 0.7 en canchas principales = superficie optima para partidos de alta exigencia.',
                'Usa los datos de humedad para planificar dias de uso intensivo vs. dias de recuperacion.',
                'Zonas marcadas en amarillo o rojo requieren reduccion de carga de entrenamiento esa semana.',
            ]),
            ('Ventanas de recuperacion y prediccion', [
                'El sistema modela tiempos de recuperacion segun condicion actual, historial de uso y clima.',
                'Podes solicitar reportes comparativos entre canchas para asignar equipos por prioridad.',
                'La correlacion NDVI-SAR predice firmeza del suelo para los proximos 7 dias con alta precision.',
            ]),
        ],
        'footer': 'Superficies analizadas por satelite  |  Decisiones respaldadas por datos  |  Faro Protocol',
    },
    {
        'filename': 'manual_velez_berlanga.pdf',
        'name': 'Fabian Berlanga',
        'role': 'Presidente',
        'institution': 'Club Atletico Velez Sarsfield',
        'intro': (
            'Como Presidente del Club recibis el informe ejecutivo semanal Faro Protocol — una vision '
            'estrategica del estado del patrimonio inmobiliario y productivo del Club, basada en datos '
            'satelitales de organismos internacionales independientes.'
        ),
        'sections': [
            ('Tu informe ejecutivo semanal', [
                'Recibis el INFORME EJECUTIVO VELEZ con KPIs de alto nivel: Health Score, alertas criticas y tendencias.',
                'El REPORTE AGRO FINAL y SOLAR v2 complementan la vision sobre produccion y energia del predio.',
                'Todos los datos provienen de satelites publicos ESA y NASA — fuentes verificables e independientes.',
            ]),
            ('Indicadores de valor patrimonial', [
                'Health Score > 70: Predio en condicion optima — sin inversiones urgentes requeridas.',
                'Health Score 50-70: Mantenimiento preventivo recomendado — bajo costo, alta efectividad.',
                'Health Score < 50: Evaluacion inmediata recomendada — posible impacto en el valor del activo.',
            ]),
            ('Valor estrategico del monitoreo', [
                'Documentacion satelital continua que protege el valor del patrimonio del Club.',
                'Datos independientes para negociaciones, auditorias o reportes a socios e inversores.',
                'Visibilidad sobre el retorno de inversiones en infraestructura verde y sistemas solares.',
            ]),
        ],
        'footer': 'Informe ejecutivo semanal  |  Patrimonio monitoreado por satelite  |  Faro Protocol',
    },
    {
        'filename': 'manual_velez_nelson.pdf',
        'name': 'Nelson Pugliese',
        'role': 'Vicepresidente',
        'institution': 'Club Atletico Velez Sarsfield',
        'intro': (
            'Como Vicepresidente recibis el informe ejecutivo semanal Faro Protocol, con indicadores '
            'de excelencia operativa, riesgos detectados y metricas de sustentabilidad del predio — '
            'actualizados cada lunes con datos satelitales en tiempo casi real.'
        ),
        'sections': [
            ('Tu informe semanal', [
                'INFORME EJECUTIVO: Health Score del predio, alertas activas, tendencias de los ultimos 30 dias.',
                'REPORTE SOLAR v2: Eficiencia del sistema de energia renovable y proyeccion de generacion.',
                'REPORTE AGRO FINAL: Estado de las zonas productivas y de esparcimiento del predio.',
            ]),
            ('Metricas de excelencia operativa', [
                'Alert Level BAJO: Operacion normal — sin intervenciones urgentes requeridas esta semana.',
                'Alert Level MEDIO: 1-2 zonas requieren atencion — coordinacion con responsables de area.',
                'Alert Level ALTO: Multiples zonas afectadas — evaluacion gerencial inmediata recomendada.',
            ]),
            ('Sustentabilidad y eficiencia', [
                'El monitoreo solar permite evaluar el ROI del sistema fotovoltaico instalado.',
                'Los datos agronomicos apoyan decisiones sobre uso eficiente del agua y el suelo.',
                'El reporte historico documenta la mejora continua del predio bajo gestion satelital.',
            ]),
        ],
        'footer': 'Excelencia operativa respaldada por datos satelitales  |  Faro Protocol',
    },
    {
        'filename': 'manual_velez_aveleyra.pdf',
        'name': 'Alberto Aveleyra',
        'role': 'Gerente General',
        'institution': 'Club Atletico Velez Sarsfield',
        'intro': (
            'Como Gerente General recibis el dashboard ejecutivo completo de Faro Protocol, integrando '
            'analisis de infraestructura, produccion y energia del predio en un sistema de gestion '
            'basado en datos satelitales de ESA Copernicus y NASA.'
        ),
        'sections': [
            ('Tu informe ejecutivo', [
                'INFORME EJECUTIVO VELEZ: Dashboard unificado con Health Score, KPIs y plan de accion semanal.',
                'REPORTE AGRO FINAL: Analisis completo de zonas verdes, productividad y estado del suelo.',
                'REPORTE SOLAR v2: Generacion estimada, anomalias del sistema y proyeccion mensual.',
            ]),
            ('Gestion por objetivos y KPIs', [
                'Establece benchmarks semanales usando el Health Score como indicador central de gestion.',
                'Asigna responsabilidades por area segun las alertas detectadas cada lunes en el informe.',
                'El historial de datos permite evaluar el impacto cuantitativo de intervenciones de mantenimiento.',
            ]),
            ('Costo-beneficio del monitoreo satelital', [
                'Deteccion temprana: el 80% de los problemas identificados antes de volverse criticos.',
                'Reduccion estimada de costos de mantenimiento correctivo: 30-40% en el primer ano.',
                'ROI del sistema solar documentado semana a semana con datos de irradiacion real.',
            ]),
        ],
        'footer': 'Dashboard ejecutivo satelital  |  Gestion basada en evidencia  |  Faro Protocol',
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

    # ── top rule ──
    hline(c, H - 18*mm)

    # FARO PROTOCOL wordmark
    c.setFont('Helvetica-Bold', 10)
    c.setFillColor(GOLD)
    c.drawCentredString(W / 2, H - 27*mm, 'F A R O   P R O T O C O L')

    c.setFont('Helvetica', 7.5)
    c.setFillColor(LGRAY)
    c.drawCentredString(W / 2, H - 33*mm, 'MONITOREO SATELITAL AVANZADO')

    # ── simple satellite graphic ──
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

    # ── name ──
    c.setFont('Helvetica-Bold', 30)
    c.setFillColor(GOLD)
    c.drawCentredString(W / 2, H / 2 + 26*mm, m['name'])

    # ── role ──
    c.setFont('Helvetica', 13)
    c.setFillColor(WHITE)
    c.drawCentredString(W / 2, H / 2 + 14*mm, m['role'])

    # ── institution ──
    c.setFont('Helvetica', 10)
    c.setFillColor(LGRAY)
    c.drawCentredString(W / 2, H / 2 + 6*mm, m['institution'])

    hline(c, H / 2 + 0.5*mm, W / 2 - 38*mm, W / 2 + 38*mm, GOLD, 0.4)

    # ── manual title ──
    c.setFont('Helvetica-Bold', 11)
    c.setFillColor(WHITE)
    c.drawCentredString(W / 2, H / 2 - 9*mm, 'MANUAL DE USO PERSONAL')

    c.setFont('Helvetica', 9)
    c.setFillColor(LGRAY)
    c.drawCentredString(W / 2, H / 2 - 16*mm, 'Guia de interpretacion de reportes satelitales semanales')

    # ── agency badges ──
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

    # ── bottom rule + footer ──
    hline(c, 18*mm)
    c.setFont('Helvetica', 7)
    c.setFillColor(LGRAY)
    c.drawCentredString(W / 2, 12*mm, m['footer'])


def draw_content(c, m):
    bg_fill(c)
    hline(c, H - 18*mm)

    # header strip
    c.setFont('Helvetica-Bold', 7.5)
    c.setFillColor(GOLD)
    header_text = 'FARO PROTOCOL  ·  ' + m['name'].upper() + '  ·  ' + m['role'].upper()
    c.drawString(40*mm, H - 27*mm, header_text)

    hline(c, H - 32*mm, col=DGRAY, lw=0.3)

    # intro paragraph
    y = H - 43*mm
    max_w = W - 80*mm
    for line in wrap(m['intro'], 'Helvetica', 9, max_w):
        c.setFont('Helvetica', 9)
        c.setFillColor(LGRAY)
        c.drawString(40*mm, y, line)
        y -= 5*mm

    y -= 6*mm

    # sections
    for title, bullets in m['sections']:
        # section title
        c.setFont('Helvetica-Bold', 11)
        c.setFillColor(GOLD)
        c.drawString(40*mm, y, title.upper())
        y -= 3.5*mm
        hline(c, y, 40*mm, 40*mm + 65*mm, GOLD, 0.3)
        y -= 7*mm

        for bullet in bullets:
            # gold dot
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

    # bottom
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
    return out


if __name__ == '__main__':
    print('Generando 7 manuales PDF...\n')
    for m in MANUALS:
        out = generate_manual(m)
        size_kb = out.stat().st_size // 1024
        print(f'  OK  {m["filename"]}  ({size_kb} KB)  — {m["name"]}, {m["role"]}')
    print(f'\nEtapa 1 completa — 7 PDFs en {OUT_DIR}')
