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
            'Este manual explica cómo interpretar el Reporte Canchero semanal que recibís cada lunes. '
            'Los datos provienen de ESA Sentinel-2 (óptico 10m), Sentinel-1 SAR (radar) y NASA POWER — '
            'fuentes satelitales independientes y verificables procesadas automáticamente.'
        ),
        'sections': [
            ('Tu reporte semanal: qué contiene', [
                'REPORTE CANCHERO: análisis individual de Amalfitani + 12 canchas de Villa Olímpica (1FA–10FA, 1FP, 2FP).',
                'Cada cancha muestra semáforo (verde/amarillo/rojo), NDVI, NDRE, recomendación de riego y acción prioritaria.',
                'El reporte se genera con la última imagen satelital disponible sin nubes — resolución 10m por píxel.',
            ]),
            ('Índices clave en tu reporte', [
                'NDVI (0–1): vigor de la cobertura vegetal. Óptimo > 0.65. Crítico < 0.40.',
                'NDRE (B05 RedEdge): detecta deficiencia de nitrógeno antes de que sea visible. < 0.15 = intervención urgente.',
                'CCCI: Canopy Chlorophyll Content Index (NDRE/NDVI). Compara vs. histórico estacional de cada cancha.',
                'Riego real (mm): calculado desde el déficit hídrico ET₀ semanal (Penman-Monteith / NASA POWER), no un valor fijo.',
            ]),
            ('Semáforo y plan de acción', [
                'Verde: condición óptima. Riego estándar según ET₀ diario. Sin intervención urgente.',
                'Amarillo: estrés moderado. Revisión de riego, fungicida preventivo si Smith-Kerns > 30%.',
                'Rojo: degradación activa. Resiembra parcial o total, reducción de carga deportiva inmediata.',
                'La columna "Acción" en cada cancha indica la tarea específica para la semana en curso.',
            ]),
        ],
        'footer': 'Datos actualizados cada lunes 07:00h ART  |  Sentinel-2 10m + SAR + ET₀  |  Faro Protocol',
    },
    {
        'filename': 'manual_velez_juan.pdf',
        'name': 'Juan González',
        'role': 'Intendente de Villa Olímpica',
        'institution': 'Club Atlético Vélez Sarsfield',
        'intro': (
            'Tu monitoreo cubre el estado integral de Villa Olímpica: 12 canchas individuales, zona '
            'agropecuaria y drenaje, con análisis satélit ESA Sentinel-2, SAR Sentinel-1 y datos '
            'climáticos NASA POWER procesados automáticamente cada semana.'
        ),
        'sections': [
            ('Tus reportes semanales', [
                'REPORTE CANCHERO: Estado individual de las 12 canchas VO (1FA–10FA, 1FP, 2FP) con NDVI, NDRE y riego real.',
                'REPORTE AGRO FINAL: análisis agronómico del predio completo — suelo, cobertura, tendencias y panel InSAR.',
                'Panel InSAR en Agro Final: deformación estructural medida en milímetros (umbral de alerta: 2mm).',
                'Ambos reportes llegan cada lunes 07:00h ART. Alertas críticas también vía WhatsApp.',
            ]),
            ('Mantenimiento predictivo por cancha', [
                'Cada cancha tiene su propio semáforo: el sistema detecta degradación antes de ser visible a nivel del suelo.',
                'Riego por cancha: calculado desde el déficit hídrico semanal real (ET₀), no valor fijo.',
                'Compactación estimada por modelo SAR VV + humedad de suelo (θ_soil) — sin necesidad de medición manual.',
                'El historial por cancha permite anticipar problemas estacionales con 2–3 semanas de anticipación.',
            ]),
            ('Coordinación de equipos y prioridades', [
                'Usá el semáforo por cancha para asignar prioridades de trabajo al equipo de mantenimiento.',
                'Las canchas en rojo requieren intervención esta semana; amarillo = planificar para los próximos 7 días.',
                'El índice Smith-Kerns (Dollar Spot) en el reporte indica riesgo de hongos por humedad y temperatura.',
            ]),
        ],
        'footer': '12 canchas VO + Amalfitani  |  NDVI · NDRE · ET₀ · SAR  |  Resolución 10m  |  Faro Protocol',
    },
    {
        'filename': 'manual_velez_banchero.pdf',
        'name': 'Fernando Banchero',
        'role': 'Gerente de Operaciones',
        'institution': 'Club Atlético Vélez Sarsfield',
        'intro': (
            'Como Gerente de Operaciones recibís el conjunto completo de reportes Faro: análisis de '
            'canchas, infraestructura estructural (InSAR), sistema solar y zona agropecuaria — '
            'integrados desde satélites ESA y NASA en un dashboard operativo semanal.'
        ),
        'sections': [
            ('Tus reportes semanales', [
                'REPORTE PRINCIPAL VÉLEZ: Health Score global del predio, alertas activas y tendencia de las últimas 6 semanas.',
                'REPORTE CANCHERO: estado individual de Amalfitani y las 12 canchas de Villa Olímpica.',
                'REPORTE SOLAR v2: eficiencia del sistema fotovoltaico (dato en vivo), anomalías y curva de producción estimada.',
                'REPORTE AGRO FINAL: análisis agronómico + Panel InSAR con deformación estructural de tribunas (en mm).',
            ]),
            ('Métricas operativas clave', [
                'Health Score 0–100: sintetiza NDVI, SAR, InSAR y clima en un único indicador de gestión.',
                'Panel InSAR: mide desplazamiento de tribunas en milímetros. Umbral de inspección estructural: 2mm.',
                'Eficiencia solar: porcentaje calculado desde datos en vivo del sistema de paneles.',
                'Déficit hídrico ET₀: milímetros de agua que necesita el predio esta semana (base para riego real).',
            ]),
            ('Gestión de recursos y presupuesto', [
                'Priorizá mantenimiento correctivo según el semáforo por cancha — intervenir en rojo antes que en amarillo.',
                'El panel InSAR documenta el estado estructural de las tribunas semana a semana para planificación de obras.',
                'Usá el Health Score como KPI objetivo en reportes a la Comisión Ejecutiva.',
            ]),
        ],
        'footer': 'Visión 360° del predio  |  Canchero · Solar · Agro · InSAR  |  Actualización semanal  |  Faro Protocol',
    },
    {
        'filename': 'manual_velez_pait.pdf',
        'name': 'Sebastián Pait',
        'role': 'Director Deportivo',
        'institution': 'Club Atlético Vélez Sarsfield',
        'intro': (
            'Tu monitoreo satelital está orientado a la calidad de las superficies de entrenamiento '
            'y competencia. Recibís análisis de canchas individuales con NDVI, NDRE y estimación '
            'de firmeza del suelo — datos objetivos para decisiones deportivas fundadas en evidencia.'
        ),
        'sections': [
            ('Tus reportes semanales', [
                'REPORTE CANCHERO: estado de Amalfitani + 12 canchas de Villa Olímpica con semáforo individual.',
                'REPORTE AGRO FINAL: condiciones del terreno, análisis de suelo y zonas de riesgo agronómico.',
                'Ambos reportes llegan cada lunes 07:00h ART con datos de la última imagen satelital limpia.',
            ]),
            ('Indicadores clave para decisiones deportivas', [
                'NDVI > 0.65 en canchas de primer equipo: superficie óptima para partidos de alta exigencia.',
                'NDVI 0.45–0.65: aceptable, considerar rotación de canchas para distribuir carga.',
                'NDVI < 0.45: reducir carga de entrenamiento en esa cancha — riesgo de lesiones por superficie.',
                'NDRE bajo (< 0.20) indica césped con estrés nitrogenado — fragilidad mecánica elevada.',
            ]),
            ('Planificación semanal basada en datos', [
                'Correlacioná el semáforo de cada cancha con el calendario de partidos y entrenamientos.',
                'Las canchas en amarillo o rojo necesitan al menos 5–7 días de recuperación antes de uso intensivo.',
                'El historial de NDVI permite comparar el estado actual vs. semanas anteriores para cada cancha.',
            ]),
        ],
        'footer': 'Superficies analizadas por satélite  |  NDVI · NDRE · Firmeza estimada  |  Faro Protocol',
    },
    {
        'filename': 'manual_velez_berlanga.pdf',
        'name': 'Fabián Berlanga',
        'role': 'Presidente',
        'institution': 'Club Atlético Vélez Sarsfield',
        'intro': (
            'Como Presidente recibís el informe ejecutivo semanal Faro Protocol — una visión '
            'estratégica del patrimonio inmobiliario y productivo del Club. Los datos provienen de '
            'satélites públicos ESA y NASA, fuentes verificables e independientes de cualquier proveedor.'
        ),
        'sections': [
            ('Tu informe ejecutivo semanal', [
                'INFORME EJECUTIVO VÉLEZ: Health Score global, alertas críticas y tendencia histórica de 6 semanas.',
                'REPORTE AGRO FINAL: estado del predio verde con panel InSAR — deformación estructural en milímetros.',
                'REPORTE SOLAR v2: eficiencia del sistema fotovoltaico en vivo y curva de producción estimada.',
            ]),
            ('Indicadores de valor patrimonial', [
                'Health Score > 70: predio en condición óptima — sin inversiones urgentes requeridas.',
                'Health Score 50–70: mantenimiento preventivo recomendado — bajo costo, alta efectividad.',
                'Health Score < 50: evaluación inmediata — posible impacto en el valor operativo del activo.',
                'InSAR < 1mm: tribunas estables. Entre 1–2mm: monitoreo reforzado. > 2mm: inspección estructural.',
            ]),
            ('Valor estratégico del monitoreo', [
                'Documentación satelital continua con registro histórico descargable — evidencia objetiva del estado del predio.',
                'Datos independientes para negociaciones, auditorías o reportes a socios, patrocinadores e inversores.',
                'El sistema opera autónomamente: los reportes se generan y envían sin intervención manual cada lunes.',
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
            'de excelencia operativa, riesgos estructurales y métricas de sustentabilidad — actualizados '
            'cada lunes con datos satelitales en tiempo casi real.'
        ),
        'sections': [
            ('Tu informe semanal', [
                'INFORME EJECUTIVO VÉLEZ: Health Score del predio, alertas activas y tendencia de las últimas 6 semanas.',
                'REPORTE SOLAR v2: eficiencia real del sistema fotovoltaico y proyección de producción estimada.',
                'REPORTE AGRO FINAL: estado agronómico del predio con análisis InSAR de deformación estructural.',
            ]),
            ('Métricas de excelencia operativa', [
                'Alert Level BAJO: operación normal — sin intervenciones urgentes requeridas esta semana.',
                'Alert Level MEDIO: 1–2 zonas requieren atención — coordinación con responsables de área.',
                'Alert Level ALTO: múltiples zonas afectadas — evaluación gerencial inmediata recomendada.',
                'El semáforo global consolida canchas, infraestructura, energía y zona verde en un solo indicador.',
            ]),
            ('Sustentabilidad y eficiencia energética', [
                'El monitoreo solar documenta el ROI del sistema fotovoltaico semana a semana con datos en vivo.',
                'Los datos agronómicos (ET₀, déficit hídrico) apoyan decisiones sobre uso eficiente del agua.',
                'El registro histórico satelital documenta la evolución del predio bajo gestión continua.',
            ]),
        ],
        'footer': 'Excelencia operativa  |  Health Score · InSAR · Solar  |  Faro Protocol',
    },
    {
        'filename': 'manual_velez_aveleyra.pdf',
        'name': 'Alberto Aveleyra',
        'role': 'Gerente General',
        'institution': 'Club Atlético Vélez Sarsfield',
        'intro': (
            'Como Gerente General recibís el dashboard ejecutivo completo de Faro Protocol: canchas, '
            'infraestructura estructural, energía solar y zona verde — integrados desde satélites '
            'ESA Copernicus y NASA en un sistema de gestión operativa basado en evidencia.'
        ),
        'sections': [
            ('Tu informe ejecutivo completo', [
                'INFORME EJECUTIVO VÉLEZ: dashboard con Health Score, KPIs operativos y plan de acción semanal.',
                'REPORTE CANCHERO: estado individual de Amalfitani y las 12 canchas de Villa Olímpica.',
                'REPORTE AGRO FINAL: análisis completo del predio verde + panel InSAR de deformación estructural (mm).',
                'REPORTE SOLAR v2: eficiencia del sistema fotovoltaico en vivo, anomalías y curva de producción.',
            ]),
            ('Gestión por objetivos y KPIs', [
                'Usá el Health Score como indicador central en reportes de gestión y reuniones ejecutivas.',
                'Asigná responsabilidades por área según las alertas detectadas cada lunes en el informe.',
                'El panel InSAR provee evidencia objetiva del estado estructural para planificación de obras.',
                'El historial de datos permite evaluar el impacto cuantitativo de intervenciones pasadas.',
            ]),
            ('Costo-beneficio del monitoreo satelital', [
                'El sistema opera en forma autónoma — reportes generados y enviados sin intervención manual.',
                'Detección temprana: problemas identificados semanas antes de ser visibles o causar daño.',
                'ROI del sistema solar documentado con datos reales semana a semana — no estimaciones teóricas.',
            ]),
        ],
        'footer': 'Dashboard ejecutivo  |  Canchero · Agro · Solar · InSAR  |  Gestión basada en evidencia  |  Faro Protocol',
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
