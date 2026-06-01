"""
Faro Protocol — Reporte Canchero Vélez Sarsfield
Planos de fútbol reales con marcadores de trabajo para el canchero.
Sin términos técnicos en los mapas.
"""
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.gridspec as gridspec
import matplotlib.patches as mpatches
from matplotlib.patches import FancyBboxPatch, Circle, Arc
import numpy as np
import hashlib, os
from datetime import datetime, timedelta

# ─── PALETA ──────────────────────────────────────────────────────────────────
BG    = '#06080b'
BG2   = '#0d1117'
BG3   = '#141c24'
GOLD  = '#c9a84c'
GOLDL = '#e2c97e'
WHITE = '#f2ede4'
WDIM  = '#9aa0a8'
RED   = '#c0392b'
REDL  = '#e74c3c'
REDXL = '#ff6b6b'
YEL   = '#d4a017'
YELL  = '#f0b429'
GRN   = '#1e8449'
GRNL  = '#27ae60'
GRNXL = '#58d68d'
FIELD = '#2d5a1b'      # green field
FIELD2= '#275218'      # darker stripe
LINE  = '#ffffff'
LINE_A= '#ffffffcc'

SEM_COLOR = {'verde': GRNL, 'amarillo': YELL, 'rojo': REDL}
SEM_EMOJI = {'verde': '●', 'amarillo': '●', 'rojo': '●'}

ZONES = [
    {
        'name': 'Campo\nAmalfitani',
        'sem': 'verde',
        'estado': 'ÓPTIMO',
        'accion': 'Aerificar porterías\nSemana 3',
        'ndvi': 0.68, 'ndre': 0.42,
        'n_kg': 0, 'riego': 12,
        'resiembra': 'No', 'hongos': 'No',
        'compact': 'Media', 'drenaje': 'OK', 'malezas': '8%',
        'timeline': 'Semana 3', 'fusion': 87.9,
        'map_accion': 'AERIFICAR PORTERÍAS\nSEMANA 3',
        'comparativa': '→',
        'focos': [
            {'x': 0.5, 'y': 0.88, 'color': YELL, 'label': 'AERIFICAR\nACÁ', 'size': 0.09},
            {'x': 0.5, 'y': 0.12, 'color': YELL, 'label': 'AERIFICAR\nACÁ', 'size': 0.09},
            {'x': 0.5, 'y': 0.50, 'color': YELL, 'label': 'COMPACTACIÓN', 'size': 0.07},
        ],
    },
    {
        'name': 'Cancha 1',
        'sem': 'amarillo',
        'estado': 'ATENCIÓN',
        'accion': 'Fungicida preventivo\nEsta semana',
        'ndvi': 0.48, 'ndre': 0.31,
        'n_kg': 15, 'riego': 18,
        'resiembra': 'Parcial', 'hongos': 'Preventivo',
        'compact': 'Alta', 'drenaje': 'Regular', 'malezas': '15%',
        'timeline': 'Esta semana', 'fusion': 62.0,
        'map_accion': 'FUNGICIDA PREVENTIVO\nESTA SEMANA',
        'comparativa': '↓',
        'focos': [
            {'x': 0.25, 'y': 0.75, 'color': YELL, 'label': 'FUNGICIDA\nACÁ', 'size': 0.09},
            {'x': 0.75, 'y': 0.25, 'color': YELL, 'label': 'REGAR\nACÁ', 'size': 0.08},
        ],
    },
    {
        'name': 'Cancha 2',
        'sem': 'amarillo',
        'estado': 'ATENCIÓN',
        'accion': 'Fertilizar 20kg N/ha\nSemana 2',
        'ndvi': 0.52, 'ndre': 0.35,
        'n_kg': 20, 'riego': 15,
        'resiembra': 'No', 'hongos': 'No',
        'compact': 'Media', 'drenaje': 'Regular', 'malezas': '12%',
        'timeline': 'Semana 2', 'fusion': 68.4,
        'map_accion': 'FERTILIZAR 20kg N/ha\nSEMANA 2',
        'comparativa': '→',
        'focos': [
            {'x': 0.5, 'y': 0.50, 'color': YELL, 'label': 'FERTILIZAR\nACÁ', 'size': 0.10},
            {'x': 0.75, 'y': 0.75, 'color': YELL, 'label': 'MALEZAS\nACÁ', 'size': 0.07},
        ],
    },
    {
        'name': 'Cancha 3',
        'sem': 'amarillo',
        'estado': 'ATENCIÓN',
        'accion': 'Fungicida activo\nEsta semana',
        'ndvi': 0.39, 'ndre': 0.28,
        'n_kg': 25, 'riego': 22,
        'resiembra': 'Parcial', 'hongos': 'Activos',
        'compact': 'Alta', 'drenaje': 'Deficiente', 'malezas': '22%',
        'timeline': 'Esta semana', 'fusion': 43.0,
        'map_accion': 'FUNGICIDA ACTIVO\nESTA SEMANA',
        'comparativa': '↓',
        'focos': [
            {'x': 0.3,  'y': 0.70, 'color': YELL, 'label': 'FUNGICIDA\nACÁ', 'size': 0.10},
            {'x': 0.70, 'y': 0.35, 'color': YELL, 'label': 'REGAR\nACÁ', 'size': 0.08},
            {'x': 0.50, 'y': 0.50, 'color': YELL, 'label': 'RESEMBRAR\nACÁ', 'size': 0.07},
        ],
    },
    {
        'name': 'Cancha 4',
        'sem': 'rojo',
        'estado': 'CRÍTICA',
        'accion': 'Fungicida + drenaje\n+ resiembra · HOY',
        'ndvi': 0.24, 'ndre': 0.18,
        'n_kg': 40, 'riego': 30,
        'resiembra': 'Total', 'hongos': '85m² activos',
        'compact': 'Crítica', 'drenaje': 'Crítico', 'malezas': '35%',
        'timeline': 'HOY — URGENTE', 'fusion': 32.8,
        'map_accion': 'INTERVENCIÓN\nURGENTE HOY',
        'comparativa': '↓',
        'focos': [
            {'x': 0.30, 'y': 0.82, 'color': REDL,  'label': 'HONGOS\nACÁ', 'size': 0.13},
            {'x': 0.75, 'y': 0.22, 'color': REDL,  'label': 'DRENAJE\nROTO', 'size': 0.11},
            {'x': 0.55, 'y': 0.50, 'color': YELL,  'label': 'MALEZAS', 'size': 0.09},
            {'x': 0.20, 'y': 0.35, 'color': REDL,  'label': 'RESEMBRAR\nACÁ', 'size': 0.10},
        ],
    },
]

now = datetime.now()
SCAN_DATE = now.strftime('%d de %B de %Y  ·  %H:%M UTC−3')
SHA = hashlib.sha256(f"FARO-VELEZ-CANCHERO-{now.isoformat()}".encode()).hexdigest()

# ─── REAL DATA OVERRIDE ───────────────────────────────────────────────────────
def _build_zones_from_vd(vd):
    _sem_estado   = {'verde': 'ÓPTIMO',    'amarillo': 'ATENCIÓN', 'rojo': 'CRÍTICA'}
    _sem_n        = {'verde': 0,           'amarillo': 15,         'rojo': 40}
    _sem_riego    = {'verde': 12,          'amarillo': 18,         'rojo': 30}
    _sem_res      = {'verde': 'No',        'amarillo': 'Parcial',  'rojo': 'Total'}
    _sem_hon      = {'verde': 'No',        'amarillo': 'Preventivo','rojo': 'Activos'}
    _sem_comp     = {'verde': 'Media',     'amarillo': 'Alta',     'rojo': 'Crítica'}
    _sem_dren     = {'verde': 'OK',        'amarillo': 'Regular',  'rojo': 'Crítico'}
    _sem_mal      = {'verde': '8%',        'amarillo': '15%',      'rojo': '35%'}
    _sem_timeline = {'verde': 'Semana 3',  'amarillo': 'Esta semana', 'rojo': 'HOY — URGENTE'}
    _focos_verde  = [{'x': 0.5, 'y': 0.88, 'color': YELL, 'label': 'AERIFICAR\nACÁ', 'size': 0.09},
                     {'x': 0.5, 'y': 0.12, 'color': YELL, 'label': 'AERIFICAR\nACÁ', 'size': 0.09}]
    _focos_amari  = [{'x': 0.5, 'y': 0.50, 'color': YELL, 'label': 'ATENCIÓN',       'size': 0.09}]
    _focos_rojo   = [{'x': 0.3, 'y': 0.70, 'color': REDL, 'label': 'URGENTE\nACÁ',   'size': 0.13},
                     {'x': 0.7, 'y': 0.30, 'color': REDL, 'label': 'REPARAR',         'size': 0.11}]
    _focos_map    = {'verde': _focos_verde, 'amarillo': _focos_amari, 'rojo': _focos_rojo}
    canchas = vd.get('sectores', {}).get('canchero', {}).get('canchas', [])
    if not canchas:
        return None
    zones = []
    for c in canchas[:5]:
        sem      = c.get('sem', 'amarillo')
        ndvi     = c.get('ndvi', 0.5)
        score    = c.get('score', 60)
        s_prev   = c.get('score_prev', score)
        detalle  = c.get('detalle', '')
        nombre   = c.get('nombre', c.get('id', '?'))
        comp_sym = '↑' if score > s_prev else ('→' if score == s_prev else '↓')
        accion_short = detalle.split('\xb7')[1].strip() if '\xb7' in detalle else detalle.split('·')[1].strip() if '·' in detalle else detalle
        zones.append({
            'name': nombre,
            'sem': sem,
            'estado': _sem_estado.get(sem, 'ATENCIÓN'),
            'accion': accion_short,
            'ndvi': ndvi,
            'ndre': round(ndvi * 0.65, 2),
            'n_kg': _sem_n.get(sem, 0),
            'riego': _sem_riego.get(sem, 18),
            'resiembra': _sem_res.get(sem, 'No'),
            'hongos': _sem_hon.get(sem, 'No'),
            'compact': _sem_comp.get(sem, 'Media'),
            'drenaje': _sem_dren.get(sem, 'OK'),
            'malezas': _sem_mal.get(sem, '10%'),
            'timeline': _sem_timeline.get(sem, 'Esta semana'),
            'fusion': float(score),
            'map_accion': accion_short.upper(),
            'comparativa': comp_sym,
            'focos': _focos_map.get(sem, _focos_amari),
        })
    return zones

import os as _os, json as _json
_vd_path = _os.environ.get("FARO_VD_PATH")
if _vd_path:
    try:
        with open(_vd_path, encoding="utf-8") as _f:
            _vd_data = _json.load(_f)
        _zones_real = _build_zones_from_vd(_vd_data)
        if _zones_real:
            ZONES = _zones_real
    except Exception as _e:
        print(f"FARO_VD_PATH canchero: {_e} — usando datos hardcodeados")
_out_path = _os.environ.get("FARO_OUT_PATH")

# ─── SOCCER FIELD DRAWING FUNCTION ───────────────────────────────────────────
def draw_soccer_field(ax, x0, y0, w, h, focos=None, is_critical=False, title='',
                       sem='verde', accion='', numero=None):
    """
    Draw a full soccer field plan at the given position in axis coordinates (0-10).
    x0, y0 = bottom-left corner
    w, h   = width and height
    focos  = list of {'x':0-1, 'y':0-1, 'color':..., 'label':..., 'size':...}
    """
    # Stripe background
    n_stripes = 8
    sw = w / n_stripes
    for i in range(n_stripes):
        fc = FIELD if i % 2 == 0 else FIELD2
        ax.add_patch(mpatches.Rectangle(
            (x0 + i * sw, y0), sw, h,
            facecolor=fc, edgecolor='none', zorder=1))

    # Critical overlay
    if is_critical:
        ax.add_patch(mpatches.Rectangle(
            (x0, y0), w, h,
            facecolor=RED, alpha=0.22, zorder=2))

    # Outer boundary
    lw_border = 2.5 if is_critical else 1.5
    ec_border = REDL if is_critical else SEM_COLOR[sem]
    ax.add_patch(mpatches.Rectangle(
        (x0, y0), w, h,
        facecolor='none', edgecolor=ec_border,
        linewidth=lw_border, zorder=6))

    # Field lines (white)
    def line(x1, y1, x2, y2, lw=1.0, alpha=0.9):
        ax.plot([x1, x2], [y1, y2], color=LINE, linewidth=lw,
                alpha=alpha, zorder=5)

    def frac(val, total, base): return base + val * total

    # Outer touchlines (already done by rectangle)
    # Halfway line
    mid_y = y0 + h / 2
    line(x0, mid_y, x0 + w, mid_y, lw=1.0)

    # Centre circle  (r = ~9.15m → scale)
    r_circle = min(w, h) * 0.16
    cx_, cy_ = x0 + w/2, y0 + h/2
    ax.add_patch(mpatches.Circle((cx_, cy_), r_circle,
        fill=False, edgecolor=LINE, linewidth=1.0, alpha=0.9, zorder=5))
    # Centre dot
    ax.plot(cx_, cy_, 'o', ms=3, color=LINE, zorder=5)

    # ---- Penalty areas (both ends) ----
    # Standard pitch: penalty area = 40.32m wide × 16.5m deep
    # Scale relative to field
    pa_w = w * 0.62     # ~40/64 of pitch width
    pa_h = h * 0.17     # ~16.5/100 of pitch length
    pa_x = x0 + (w - pa_w) / 2

    # North (top) penalty area
    pa_top_y = y0 + h - pa_h
    ax.add_patch(mpatches.Rectangle(
        (pa_x, pa_top_y), pa_w, pa_h,
        fill=False, edgecolor=LINE, linewidth=0.9, alpha=0.9, zorder=5))

    # South (bottom) penalty area
    pa_bot_y = y0
    ax.add_patch(mpatches.Rectangle(
        (pa_x, pa_bot_y), pa_w, pa_h,
        fill=False, edgecolor=LINE, linewidth=0.9, alpha=0.9, zorder=5))

    # ---- Goal areas (6-yard boxes) ----
    ga_w = w * 0.30
    ga_h = h * 0.065
    ga_x = x0 + (w - ga_w) / 2

    # Top goal area
    ax.add_patch(mpatches.Rectangle(
        (ga_x, y0 + h - ga_h), ga_w, ga_h,
        fill=False, edgecolor=LINE, linewidth=0.7, alpha=0.85, zorder=5))

    # Bottom goal area
    ax.add_patch(mpatches.Rectangle(
        (ga_x, y0), ga_w, ga_h,
        fill=False, edgecolor=LINE, linewidth=0.7, alpha=0.85, zorder=5))

    # ---- Goals (posts) ----
    goal_w = w * 0.12
    goal_d = h * 0.025
    goal_x = x0 + (w - goal_w) / 2

    # Top goal
    ax.add_patch(mpatches.Rectangle(
        (goal_x, y0 + h), goal_w, goal_d,
        facecolor='none', edgecolor=LINE, linewidth=1.4, alpha=0.95, zorder=5))

    # Bottom goal
    ax.add_patch(mpatches.Rectangle(
        (goal_x, y0 - goal_d), goal_w, goal_d,
        facecolor='none', edgecolor=LINE, linewidth=1.4, alpha=0.95, zorder=5))

    # ---- Penalty spots ----
    pen_x = cx_
    pen_top = y0 + h - h * 0.115
    pen_bot = y0 + h * 0.115
    ax.plot(pen_x, pen_top, 'o', ms=2.5, color=LINE, zorder=5)
    ax.plot(pen_x, pen_bot, 'o', ms=2.5, color=LINE, zorder=5)

    # ---- Penalty arcs ----
    arc_r = r_circle * 1.1
    arc_top = Arc((pen_x, pen_top), arc_r*2, arc_r*2,
                  angle=0, theta1=200, theta2=340,
                  color=LINE, linewidth=0.8, alpha=0.8, zorder=5)
    arc_bot = Arc((pen_x, pen_bot), arc_r*2, arc_r*2,
                  angle=0, theta1=20, theta2=160,
                  color=LINE, linewidth=0.8, alpha=0.8, zorder=5)
    ax.add_patch(arc_top)
    ax.add_patch(arc_bot)

    # ---- Zone name badge ----
    sc = SEM_COLOR[sem]
    ax.add_patch(FancyBboxPatch(
        (x0 + 0.05, y0 + h - 0.30), w - 0.1, 0.26,
        boxstyle='round,pad=0.02',
        facecolor='#000000CC', edgecolor=sc, linewidth=0.8, zorder=8))
    title_clean = title.replace('\n', ' ')
    ax.text(x0 + w/2, y0 + h - 0.17, title_clean,
        color=WHITE, fontsize=6.5, fontweight='bold',
        ha='center', va='center', zorder=9,
        fontfamily='sans-serif')

    # ---- Semáforo dot ----
    ax.plot(x0 + 0.18, y0 + h - 0.18, 'o', ms=7, color=sc, zorder=9)

    # ---- Critical badge ----
    if is_critical:
        ax.text(x0 + w/2, y0 + h/2 - 0.05, '⚠ CRÍTICA',
            color=REDXL, fontsize=10, fontweight='bold',
            ha='center', va='center', zorder=10,
            fontfamily='monospace',
            bbox=dict(facecolor='#1a0000', edgecolor=REDL,
                      linewidth=1.5, pad=4, boxstyle='round,pad=0.3'))

    # ---- Foco markers ----
    if focos:
        for foco in focos:
            fx = x0 + foco['x'] * w
            fy = y0 + foco['y'] * h
            fr = foco['size'] * min(w, h)
            fc = foco['color']
            label = foco['label']

            # Outer glow
            ax.add_patch(mpatches.Circle((fx, fy), fr * 1.35,
                facecolor=fc, alpha=0.18, zorder=7))
            # Main circle
            ax.add_patch(mpatches.Circle((fx, fy), fr,
                facecolor=fc, alpha=0.80, edgecolor='white',
                linewidth=1.2, zorder=8))
            # Label inside circle
            lines = label.split('\n')
            if len(lines) == 2:
                ax.text(fx, fy + fr*0.18, lines[0],
                    color='black', fontsize=5.5, fontweight='bold',
                    ha='center', va='center', zorder=9,
                    fontfamily='monospace')
                ax.text(fx, fy - fr*0.28, lines[1],
                    color='black', fontsize=5.5, fontweight='bold',
                    ha='center', va='center', zorder=9,
                    fontfamily='monospace')
            else:
                ax.text(fx, fy, label,
                    color='black', fontsize=5.5, fontweight='bold',
                    ha='center', va='center', zorder=9,
                    fontfamily='monospace')

    # ---- Accion text bottom ----
    if accion:
        accion_clean = accion.replace('\n', ' · ')
        sc = SEM_COLOR[sem]
        ax.text(x0 + w/2, y0 - 0.22, accion_clean,
            color=sc, fontsize=6.0, fontweight='bold',
            ha='center', va='top', zorder=9,
            fontfamily='monospace',
            bbox=dict(facecolor=BG2+'DD', edgecolor=sc+'88',
                      linewidth=0.6, pad=2, boxstyle='round,pad=0.2'))


# ─── SETUP FIGURE ────────────────────────────────────────────────────────────
DPI = 200
FW, FH = 13.5, 27
fig = plt.figure(figsize=(FW, FH), dpi=DPI, facecolor=BG)
fig.subplots_adjust(left=0.03, right=0.97, top=0.99, bottom=0.01, hspace=0.0)

gs = gridspec.GridSpec(
    9, 1,
    figure=fig,
    hspace=0.06,
    height_ratios=[1.6, 1.8, 4.5, 4.5, 2.0, 2.2, 3.4, 1.5, 0.85],
)

# ══════════════════════════════════════════════════════════════════════════════
# 0 · HEADER
# ══════════════════════════════════════════════════════════════════════════════
ax_hdr = fig.add_subplot(gs[0])
ax_hdr.set_facecolor(BG)
ax_hdr.set_xlim(0, 1); ax_hdr.set_ylim(0, 1)
ax_hdr.axis('off')

ax_hdr.add_patch(mpatches.Rectangle((0, 0.93), 1, 0.07,
    transform=ax_hdr.transAxes, color=GOLD, zorder=2))

ax_hdr.text(0.5, 0.88, 'FARO PROTOCOL — FORTÍN INTELIGENTE',
    transform=ax_hdr.transAxes, color=GOLD,
    fontsize=17, fontweight='bold', ha='center', va='top',
    fontfamily='monospace')

ax_hdr.text(0.5, 0.70, 'VÉLEZ SARSFIELD · MAPA DE TRABAJO PARA EL CANCHERO',
    transform=ax_hdr.transAxes, color=WHITE,
    fontsize=12.5, fontweight='bold', ha='center', va='top',
    fontfamily='sans-serif')

ax_hdr.text(0.5, 0.52, f'Escaneo satelital: {SCAN_DATE}',
    transform=ax_hdr.transAxes, color=WDIM,
    fontsize=9, ha='center', va='top', fontfamily='monospace')

ax_hdr.text(0.5, 0.36, 'Lat −34.6379  ·  Lon −58.5288  ·  WGS-84',
    transform=ax_hdr.transAxes, color=WDIM,
    fontsize=8.5, ha='center', va='top', fontfamily='monospace')

ax_hdr.add_patch(FancyBboxPatch(
    (0.28, 0.02), 0.44, 0.22,
    boxstyle='round,pad=0.01',
    transform=ax_hdr.transAxes,
    facecolor=BG3, edgecolor=GOLD, linewidth=1.2, zorder=3))
ax_hdr.text(0.5, 0.13, '  MAPA SIMPLIFICADO · USO OPERATIVO · MAYO 2026  ',
    transform=ax_hdr.transAxes, color=GOLD,
    fontsize=8.5, fontweight='bold', ha='center', va='bottom',
    fontfamily='monospace', zorder=4)

for lx, ltxt in [(0.06, 'ESA'), (0.14, 'COP'), (0.22, 'NASA')]:
    ax_hdr.add_patch(FancyBboxPatch(
        (lx - 0.025, 0.03), 0.065, 0.22,
        boxstyle='round,pad=0.01', transform=ax_hdr.transAxes,
        facecolor=BG3, edgecolor=GOLD+'88', linewidth=0.8, zorder=3))
    ax_hdr.text(lx + 0.008, 0.14, ltxt,
        transform=ax_hdr.transAxes, color=GOLD,
        fontsize=7, fontweight='bold', ha='center', va='center',
        fontfamily='monospace', zorder=4)
for lx, ltxt in [(0.78, 'ESA'), (0.86, 'COP'), (0.94, 'NASA')]:
    ax_hdr.add_patch(FancyBboxPatch(
        (lx - 0.025, 0.03), 0.065, 0.22,
        boxstyle='round,pad=0.01', transform=ax_hdr.transAxes,
        facecolor=BG3, edgecolor=GOLD+'88', linewidth=0.8, zorder=3))
    ax_hdr.text(lx + 0.008, 0.14, ltxt,
        transform=ax_hdr.transAxes, color=GOLD,
        fontsize=7, fontweight='bold', ha='center', va='center',
        fontfamily='monospace', zorder=4)

ax_hdr.axhline(0.0, color=GOLD, linewidth=1.5, xmin=0, xmax=1)


# ══════════════════════════════════════════════════════════════════════════════
# 1 · SEMÁFORO EJECUTIVO
# ══════════════════════════════════════════════════════════════════════════════
ax_sem = fig.add_subplot(gs[1])
ax_sem.set_facecolor(BG2)
ax_sem.set_xlim(0, 1); ax_sem.set_ylim(0, 1)
ax_sem.axis('off')

ax_sem.text(0.5, 0.98, 'PANEL 0 · SEMÁFORO EJECUTIVO — LECTURA EN 3 SEGUNDOS',
    transform=ax_sem.transAxes, color=GOLD,
    fontsize=8.5, fontweight='bold', ha='center', va='top',
    fontfamily='monospace')

ax_sem.axhline(0.92, color=GOLD+'44', linewidth=0.5)

xs = np.linspace(0.10, 0.90, 5)
bw = 0.16

for i, z in enumerate(ZONES):
    cx = xs[i]
    sc = SEM_COLOR[z['sem']]
    bx = cx - bw/2 + 0.005
    by = 0.06
    bh = 0.84
    border_c = sc + 'AA' if z['sem'] == 'rojo' else sc + '55'
    lw = 2.0 if z['sem'] == 'rojo' else 0.9
    ax_sem.add_patch(FancyBboxPatch(
        (bx, by), bw - 0.01, bh,
        boxstyle='round,pad=0.008',
        transform=ax_sem.transAxes,
        facecolor=BG3 if z['sem'] != 'rojo' else '#1a0505',
        edgecolor=sc, linewidth=lw, zorder=2))

    ax_sem.text(cx + 0.005, by + bh - 0.04, z['name'],
        transform=ax_sem.transAxes,
        color=WHITE, fontsize=8, fontweight='bold',
        ha='center', va='top', fontfamily='sans-serif', zorder=3)

    dot_y = by + bh - 0.32
    ax_sem.plot(cx + 0.005, dot_y, 'o', ms=22, color=sc,
        transform=ax_sem.transAxes, zorder=4, clip_on=False)
    ax_sem.plot(cx + 0.005, dot_y, 'o', ms=30, color=sc, alpha=0.12,
        transform=ax_sem.transAxes, zorder=3, clip_on=False)

    ax_sem.text(cx + 0.005, dot_y - 0.145, z['estado'],
        transform=ax_sem.transAxes,
        color=sc, fontsize=9, fontweight='bold',
        ha='center', va='top', fontfamily='monospace', zorder=3)

    ax_sem.text(cx + 0.005, by + 0.18, z['accion'],
        transform=ax_sem.transAxes,
        color=WHITE if z['sem'] != 'rojo' else REDXL,
        fontsize=7.0, ha='center', va='bottom',
        fontfamily='monospace', zorder=3,
        linespacing=1.35)

ax_sem.axhline(0.0, color=GOLD+'44', linewidth=0.5)


# ══════════════════════════════════════════════════════════════════════════════
# 2 · PANEL 1 — MAPA DE PRESCRIPCIÓN (planos reales)
# ══════════════════════════════════════════════════════════════════════════════
ax_map1 = fig.add_subplot(gs[2])
ax_map1.set_facecolor(BG2)
ax_map1.set_xlim(0, 10); ax_map1.set_ylim(0, 10)
ax_map1.axis('off')

ax_map1.text(5, 9.85, 'PANEL 1 · MAPA DE PRESCRIPCIÓN — DÓNDE TRABAJAR ESTA SEMANA',
    color=GOLD, fontsize=8.5, fontweight='bold',
    ha='center', va='top', fontfamily='monospace')
ax_map1.text(5, 9.65, '[ROJO] = trabajar HOY      [AMARILLO] = trabajar esta semana',
    color=WHITE, fontsize=8, ha='center', va='top', fontfamily='monospace')

# Campo Amalfitani — large left, vertical field (N-S oriented)
draw_soccer_field(ax_map1,
    x0=0.3, y0=0.55, w=2.9, h=8.6,
    focos=ZONES[0]['focos'],
    is_critical=False,
    title='Campo Amalfitani',
    sem='verde',
    accion='AERIFICAR PORTERÍAS · SEMANA 3')

# Cancha 1 — top right area, horizontal
draw_soccer_field(ax_map1,
    x0=3.7, y0=6.6, w=2.8, h=2.8,
    focos=ZONES[1]['focos'],
    is_critical=False,
    title='Cancha 1',
    sem='amarillo',
    accion='FUNGICIDA PREVENTIVO · ESTA SEMANA')

# Cancha 2 — mid right
draw_soccer_field(ax_map1,
    x0=3.7, y0=3.4, w=2.8, h=2.8,
    focos=ZONES[2]['focos'],
    is_critical=False,
    title='Cancha 2',
    sem='amarillo',
    accion='FERTILIZAR · SEMANA 2')

# Cancha 3 — top far right
draw_soccer_field(ax_map1,
    x0=7.0, y0=6.6, w=2.7, h=2.8,
    focos=ZONES[3]['focos'],
    is_critical=False,
    title='Cancha 3',
    sem='amarillo',
    accion='FUNGICIDA + RESEMBRAR · ESTA SEMANA')

# Cancha 4 — bottom far right — CRÍTICA
draw_soccer_field(ax_map1,
    x0=7.0, y0=3.4, w=2.7, h=2.8,
    focos=ZONES[4]['focos'],
    is_critical=True,
    title='Cancha 4 ⚠',
    sem='rojo',
    accion='INTERVENCIÓN URGENTE HOY')

# Legend panel 1
legend_y = 2.4
ax_map1.add_patch(FancyBboxPatch(
    (3.6, 0.3), 6.1, 2.2,
    boxstyle='round,pad=0.1',
    facecolor=BG3, edgecolor=GOLD+'55', linewidth=0.8, zorder=5))

ax_map1.text(6.65, 2.3, 'LEYENDA — QUÉ SIGNIFICAN LOS CÍRCULOS',
    color=GOLD, fontsize=7.5, fontweight='bold',
    ha='center', va='top', fontfamily='monospace', zorder=6)

leyenda_items = [
    (REDL,  '● ROJO GRANDE',  'Zona crítica — trabajar HOY sin falta'),
    (YELL,  '● AMARILLO',     'Zona de atención — trabajar esta semana'),
    (GRNL,  '● VERDE',        'Zona OK — solo monitorear'),
]
for li, (lc, lsym, ldes) in enumerate(leyenda_items):
    yl = 1.85 - li * 0.55
    ax_map1.plot(3.9, yl, 'o', ms=12, color=lc, alpha=0.85, zorder=6)
    ax_map1.text(4.2, yl, f'{lsym}', color=lc, fontsize=7, fontweight='bold',
        va='center', fontfamily='monospace', zorder=6)
    ax_map1.text(5.4, yl, ldes, color=WHITE, fontsize=7,
        va='center', fontfamily='monospace', zorder=6)


# ══════════════════════════════════════════════════════════════════════════════
# 3 · PANEL 2 — MAPA DE ALERTAS (planos reales con focos numerados)
# ══════════════════════════════════════════════════════════════════════════════
ax_map2 = fig.add_subplot(gs[3])
ax_map2.set_facecolor(BG2)
ax_map2.set_xlim(0, 10); ax_map2.set_ylim(0, 10)
ax_map2.axis('off')

ax_map2.text(5, 9.85, 'PANEL 2 · MAPA DE ALERTAS — FOCOS EXACTOS POR CANCHA',
    color=GOLD, fontsize=8.5, fontweight='bold',
    ha='center', va='top', fontfamily='monospace')
ax_map2.text(5, 9.65,
    '1=HONGO  2=AGUA/DRENAJE  3=MALEZA  4=RESEMBRAR  — cada número = lugar exacto',
    color=WHITE, fontsize=7.5, ha='center', va='top', fontfamily='monospace')

# Numbered focos per zone for panel 2
alert_focos = {
    'Campo Amalfitani': [
        {'x': 0.5,  'y': 0.88, 'num': '1', 'color': YELL,  'type': 'AERIFICAR'},
        {'x': 0.5,  'y': 0.12, 'num': '2', 'color': YELL,  'type': 'AERIFICAR'},
        {'x': 0.5,  'y': 0.50, 'num': '3', 'color': YELL,  'type': 'COMPAC.'},
    ],
    'Cancha 1': [
        {'x': 0.25, 'y': 0.75, 'num': '1', 'color': YELL,  'type': 'HONGO'},
        {'x': 0.75, 'y': 0.25, 'num': '2', 'color': '#4fc3f7', 'type': 'AGUA'},
    ],
    'Cancha 2': [
        {'x': 0.5,  'y': 0.50, 'num': '1', 'color': YELL,  'type': 'MALEZA'},
        {'x': 0.75, 'y': 0.75, 'num': '2', 'color': YELL,  'type': 'MALEZA'},
    ],
    'Cancha 3': [
        {'x': 0.3,  'y': 0.70, 'num': '1', 'color': YELL,  'type': 'HONGO'},
        {'x': 0.70, 'y': 0.35, 'num': '2', 'color': '#4fc3f7', 'type': 'AGUA'},
        {'x': 0.50, 'y': 0.50, 'num': '3', 'color': YELL,  'type': 'RESEM.'},
    ],
    'Cancha 4': [
        {'x': 0.30, 'y': 0.82, 'num': '1', 'color': REDL,  'type': 'HONGO'},
        {'x': 0.75, 'y': 0.22, 'num': '2', 'color': '#4fc3f7', 'type': 'AGUA'},
        {'x': 0.55, 'y': 0.50, 'num': '3', 'color': YELL,  'type': 'MALEZA'},
        {'x': 0.20, 'y': 0.35, 'num': '4', 'color': REDL,  'type': 'RESEM.'},
    ],
}

def draw_numbered_focos(ax, x0, y0, w, h, focos_num):
    for f in focos_num:
        fx = x0 + f['x'] * w
        fy = y0 + f['y'] * h
        fc = f['color']
        num = f['num']
        typ = f['type']
        r = min(w, h) * 0.09

        ax.add_patch(mpatches.Circle((fx, fy), r * 1.4,
            facecolor=fc, alpha=0.20, zorder=7))
        ax.add_patch(mpatches.Circle((fx, fy), r,
            facecolor=fc, alpha=0.85, edgecolor='white',
            linewidth=1.2, zorder=8))
        ax.text(fx, fy + r * 0.15, num,
            color='black', fontsize=7.5, fontweight='bold',
            ha='center', va='center', zorder=9,
            fontfamily='monospace')
        ax.text(fx, fy - r * 1.8, typ,
            color=fc, fontsize=5.5, fontweight='bold',
            ha='center', va='center', zorder=9,
            fontfamily='monospace',
            bbox=dict(facecolor=BG+'CC', edgecolor=fc+'88',
                      linewidth=0.5, pad=1.5, boxstyle='round,pad=0.15'))

# Draw all fields for panel 2 (same positions as panel 1)
FIELD_DEFS = [
    (0.3,  0.55, 2.9, 8.6, 'Campo Amalfitani', 'verde',  False),
    (3.7,  6.6,  2.8, 2.8, 'Cancha 1',         'amarillo', False),
    (3.7,  3.4,  2.8, 2.8, 'Cancha 2',         'amarillo', False),
    (7.0,  6.6,  2.7, 2.8, 'Cancha 3',         'amarillo', False),
    (7.0,  3.4,  2.7, 2.8, 'Cancha 4',         'rojo',   True),
]

for (fx0, fy0, fw, fh, fname, fsem, fcrit) in FIELD_DEFS:
    draw_soccer_field(ax_map2,
        x0=fx0, y0=fy0, w=fw, h=fh,
        focos=None,
        is_critical=fcrit,
        title=fname if not fcrit else 'Cancha 4 ⚠',
        sem=fsem,
        accion='')
    fname_key = fname
    if fname_key in alert_focos:
        draw_numbered_focos(ax_map2, fx0, fy0, fw, fh, alert_focos[fname_key])

# Panel 2 legend
ax_map2.add_patch(FancyBboxPatch(
    (3.6, 0.3), 6.1, 2.6,
    boxstyle='round,pad=0.1',
    facecolor=BG3, edgecolor=GOLD+'55', linewidth=0.8, zorder=5))

ax_map2.text(6.65, 2.7, 'LEYENDA — QUÉ ES CADA NÚMERO',
    color=GOLD, fontsize=7.5, fontweight='bold',
    ha='center', va='top', fontfamily='monospace', zorder=6)

legend2_items = [
    (REDL,     'HONGO',    'Aplicar fungicida en ese punto'),
    ('#4fc3f7', 'AGUA',     'Problema de drenaje o riego'),
    (YELL,     'MALEZA',   'Arrancar / herbicida en ese punto'),
    (REDL,     'RESEMBRAR','Colocar semilla nueva en ese punto'),
]
for li, (lc, ltype, ldes) in enumerate(legend2_items):
    yl2 = 2.3 - li * 0.5
    ax_map2.add_patch(mpatches.Circle((4.0, yl2), 0.22,
        facecolor=lc, alpha=0.85, zorder=6))
    ax_map2.text(4.0, yl2, str(li+1),
        color='black', fontsize=7, fontweight='bold',
        ha='center', va='center', zorder=7, fontfamily='monospace')
    ax_map2.text(4.4, yl2, f'{ltype}',
        color=lc, fontsize=7, fontweight='bold',
        va='center', fontfamily='monospace', zorder=6)
    ax_map2.text(5.5, yl2, ldes,
        color=WHITE, fontsize=7,
        va='center', fontfamily='monospace', zorder=6)


# ══════════════════════════════════════════════════════════════════════════════
# 4 · ÍNDICE FUSIÓN FARO (idéntico al FINAL)
# ══════════════════════════════════════════════════════════════════════════════
from matplotlib.colors import LinearSegmentedColormap as LSC

def ndvi_colormap():
    colors = [(0.55, 0.0, 0.0), (0.85, 0.35, 0.0),
              (0.95, 0.75, 0.1), (0.4, 0.75, 0.2),
              (0.05, 0.45, 0.05)]
    return LSC.from_list('ndvi', colors)

ax_fus = fig.add_subplot(gs[4])
ax_fus.set_facecolor(BG2)
ax_fus.set_xlim(0, 1); ax_fus.set_ylim(0, 1)
ax_fus.axis('off')

ax_fus.text(0.5, 0.98, 'PANEL 3 · ÍNDICE FUSIÓN FARO — PUNTUACIÓN INTEGRADA POR ZONA',
    transform=ax_fus.transAxes,
    color=GOLD, fontsize=8.5, fontweight='bold',
    ha='center', va='top', fontfamily='monospace')
ax_fus.axhline(0.92, color=GOLD+"44", linewidth=0.5)

scale_y = 0.10
ax_fus.add_patch(mpatches.Rectangle(
    (0.07, scale_y), 0.86, 0.06,
    transform=ax_fus.transAxes,
    facecolor='none', edgecolor=GOLD+'44', linewidth=0.6))
grad_arr = np.linspace(0, 1, 200)[np.newaxis, :]
ax_fus_img = ax_fus.inset_axes([0.07, scale_y, 0.86, 0.06])
ax_fus_img.imshow(grad_arr, cmap=ndvi_colormap(), aspect='auto', vmin=0, vmax=1)
ax_fus_img.axis('off')
for val, lbl in [(0, '0\nCrítico'), (50, '50\nNormal'), (100, '100\nÓptimo')]:
    sx = 0.07 + val/100 * 0.86
    ax_fus.plot([sx, sx], [scale_y - 0.01, scale_y + 0.07],
        color=WHITE, linewidth=0.8, transform=ax_fus.transAxes, zorder=5)
    ax_fus.text(sx, scale_y - 0.03, lbl,
        transform=ax_fus.transAxes,
        color=WDIM, fontsize=7, ha='center', va='top',
        fontfamily='monospace', linespacing=1.1)

xs5 = np.linspace(0.12, 0.88, 5)
for i, z in enumerate(ZONES):
    cx = xs5[i]
    sc = SEM_COLOR[z['sem']]
    fv = z['fusion']

    ax_fus.text(cx, 0.88, z['name'],
        transform=ax_fus.transAxes,
        color=WHITE, fontsize=8, fontweight='bold',
        ha='center', va='top', fontfamily='sans-serif')

    ax_fus.text(cx, 0.63, f'{fv}',
        transform=ax_fus.transAxes,
        color=sc, fontsize=22, fontweight='bold',
        ha='center', va='center', fontfamily='monospace')

    ax_fus.plot(cx, 0.42, 'o', ms=14, color=sc,
        transform=ax_fus.transAxes, zorder=4)
    ax_fus.plot(cx, 0.42, 'o', ms=22, color=sc, alpha=0.14,
        transform=ax_fus.transAxes, zorder=3)

    ptr_x = 0.07 + fv/100 * 0.86
    ax_fus.annotate('',
        xy=(ptr_x, scale_y + 0.06),
        xytext=(cx, 0.30),
        xycoords='axes fraction', textcoords='axes fraction',
        arrowprops=dict(arrowstyle='->', color=sc+'99', lw=0.8))


# ══════════════════════════════════════════════════════════════════════════════
# 5 · InSAR (idéntico al FINAL)
# ══════════════════════════════════════════════════════════════════════════════
ax_ins = fig.add_subplot(gs[5])
ax_ins.set_facecolor(BG3)
for sp in ax_ins.spines.values():
    sp.set_edgecolor(GOLD+'44'); sp.set_linewidth(0.6)

ax_ins.text(0.5, 1.02, 'PANEL InSAR · DEFORMACIÓN ESTRUCTURAL — TRIBUNAS',
    transform=ax_ins.transAxes,
    color=GOLD, fontsize=8.5, fontweight='bold',
    ha='center', va='bottom', fontfamily='monospace')

labels_ins = ['Norte', 'Sur', 'Este', 'Oeste']
vals_ins   = [0.85, 1.20, 0.60, 2.80]
colors_ins = [GRNL, YELL, GRNL, REDL]
x_pos_ins = np.arange(len(labels_ins))

bars = ax_ins.bar(x_pos_ins, vals_ins, color=colors_ins, edgecolor=BG,
                  linewidth=0.8, zorder=3, width=0.55)

ax_ins.axhline(2.0, color=REDL, linewidth=1.8, linestyle='--', zorder=4)
ax_ins.text(3.55, 2.08, '← umbral 2mm', color=REDL,
    fontsize=8.5, fontweight='bold', va='bottom', fontfamily='monospace')

for bar_, val in zip(bars, vals_ins):
    clr = REDXL if val >= 2.0 else WHITE
    ax_ins.text(bar_.get_x() + bar_.get_width()/2, val + 0.05,
        f'{val:.2f}mm', color=clr, fontsize=9, fontweight='bold',
        ha='center', va='bottom', fontfamily='monospace')

ax_ins.annotate('⚠ INSPECCIÓN\nESTRUCTURAL\nRECOMENDADA',
    xy=(3, 2.80), xytext=(2.3, 3.5),
    color=REDXL, fontsize=8, fontweight='bold', fontfamily='monospace',
    arrowprops=dict(arrowstyle='->', color=REDXL, lw=1.5),
    bbox=dict(facecolor='#1a0000', edgecolor=REDL, linewidth=1.2,
              pad=4, boxstyle='round,pad=0.3'))

ax_ins.set_xlim(-0.6, 3.8); ax_ins.set_ylim(0, 4.8)
ax_ins.set_xticks(x_pos_ins)
ax_ins.set_xticklabels(labels_ins, color=WHITE, fontsize=10, fontfamily='monospace')
ax_ins.set_ylabel('Desplazamiento (mm)', color=WDIM, fontsize=8, fontfamily='monospace')
ax_ins.tick_params(colors=WDIM, labelsize=8)
ax_ins.yaxis.label.set_color(WDIM)
ax_ins.set_facecolor(BG3)
ax_ins.yaxis.set_tick_params(labelcolor=WDIM)
for spine in ax_ins.spines.values():
    spine.set_edgecolor(GOLD+'33')
ax_ins.text(-0.58, 0.2, '0mm\nEstable', color=GRNL,
    fontsize=7, fontfamily='monospace', va='bottom', linespacing=1.2)
ax_ins.text(-0.58, 2.05, '2mm\nUmbral', color=YELL,
    fontsize=7, fontfamily='monospace', va='bottom', linespacing=1.2)
ax_ins.text(-0.58, 4.4, '5mm\nCrítico', color=REDL,
    fontsize=7, fontfamily='monospace', va='top', linespacing=1.2)


# ══════════════════════════════════════════════════════════════════════════════
# 6 · TABLA DE PRESCRIPCIÓN (idéntica al FINAL)
# ══════════════════════════════════════════════════════════════════════════════
ax_tbl = fig.add_subplot(gs[6])
ax_tbl.set_facecolor(BG2)
ax_tbl.axis('off')

ax_tbl.text(0.5, 0.99, 'TABLA DE PRESCRIPCIÓN AGRONÓMICA',
    transform=ax_tbl.transAxes,
    color=GOLD, fontsize=9, fontweight='bold',
    ha='center', va='top', fontfamily='monospace')

cols_tbl = ['ZONA', 'NDVI', 'NDRE', 'N\nkg/ha', 'RIEGO\nmm',
            'RESIEMBRA', 'HONGOS', 'COMPACT.', 'DRENAJE', 'MALEZAS',
            'TIMELINE', 'PRIOR.', 'ACCIÓN INMEDIATA']

rows_tbl = []
for z in ZONES:
    sc = SEM_COLOR[z['sem']]
    rows_tbl.append({
        'data': [
            z['name'].replace('\n', ' '),
            f"{z['ndvi']:.2f}",
            f"{z['ndre']:.2f}",
            str(z['n_kg']),
            str(z['riego']),
            z['resiembra'],
            z['hongos'],
            z['compact'],
            z['drenaje'],
            z['malezas'],
            z['timeline'],
            '●',
            z['accion'].replace('\n', ' '),
        ],
        'sem': z['sem'],
        'color': sc,
    })

table_data_tbl = [[r['data'][j] for j in range(len(cols_tbl))] for r in rows_tbl]

the_table = ax_tbl.table(
    cellText=table_data_tbl,
    colLabels=cols_tbl,
    cellLoc='center',
    loc='center',
    bbox=[0.0, 0.01, 1.0, 0.93],
)
the_table.auto_set_font_size(False)
the_table.set_fontsize(7)

for j in range(len(cols_tbl)):
    cell = the_table[0, j]
    cell.set_facecolor(BG3)
    cell.set_text_props(color=GOLD, fontsize=7, fontweight='bold',
                        fontfamily='monospace')
    cell.set_edgecolor(GOLD + '55')
    cell.set_linewidth(0.5)

for i, row in enumerate(rows_tbl):
    sc = row['color']
    for j in range(len(cols_tbl)):
        cell = the_table[i+1, j]
        if row['sem'] == 'rojo':
            cell.set_facecolor('#1a0505')
        elif row['sem'] == 'amarillo':
            cell.set_facecolor('#181200')
        else:
            cell.set_facecolor(BG3)
        cell.set_edgecolor(GOLD + '33')
        cell.set_linewidth(0.4)

        if j == 11:
            cell.set_text_props(color=sc, fontsize=12, fontweight='bold')
        elif j == 0:
            cell.set_text_props(color=WHITE, fontsize=7, fontweight='bold',
                                fontfamily='monospace')
        elif j == len(cols_tbl)-1:
            cell.set_text_props(
                color=sc if row['sem'] != 'verde' else GRNL,
                fontsize=6.5, fontweight='bold', fontfamily='monospace')
        elif j == 10:
            cell.set_text_props(
                color=sc, fontsize=7, fontweight='bold',
                fontfamily='monospace')
        else:
            cell.set_text_props(color=WHITE, fontsize=7,
                                fontfamily='monospace')

for j in range(len(cols_tbl)):
    the_table[5, j].set_edgecolor(REDL)
    the_table[5, j].set_linewidth(1.0)


# ══════════════════════════════════════════════════════════════════════════════
# 7 · COMPARATIVA
# ══════════════════════════════════════════════════════════════════════════════
ax_cmp = fig.add_subplot(gs[7])
ax_cmp.set_facecolor(BG2)
ax_cmp.set_xlim(0, 1); ax_cmp.set_ylim(0, 1)
ax_cmp.axis('off')

ax_cmp.text(0.5, 0.97, 'COMPARATIVA · TENDENCIA vs. ESCANEO ANTERIOR',
    transform=ax_cmp.transAxes,
    color=GOLD, fontsize=8.5, fontweight='bold',
    ha='center', va='top', fontfamily='monospace')

comp_data = [
    ('Campo\nAmalfitani', 87.9, 85.2, '→', GRNL),
    ('Cancha 1',          62.0, 71.5, '↓', YELL),
    ('Cancha 2',          68.4, 67.0, '→', YELL),
    ('Cancha 3',          43.0, 53.8, '↓', YELL),
    ('Cancha 4',          32.8, 49.2, '↓', REDL),
]
xs5c = np.linspace(0.10, 0.90, 5)
for i, (nm, cur, prev, arrow, clr) in enumerate(comp_data):
    cx = xs5c[i]
    ax_cmp.text(cx, 0.85, nm,
        transform=ax_cmp.transAxes,
        color=WHITE, fontsize=7.5, fontweight='bold',
        ha='center', va='top', fontfamily='sans-serif')
    ax_cmp.text(cx, 0.60, arrow,
        transform=ax_cmp.transAxes,
        color=clr, fontsize=22, fontweight='bold',
        ha='center', va='center')
    ax_cmp.text(cx, 0.38, f'{cur:.1f}',
        transform=ax_cmp.transAxes,
        color=clr, fontsize=11, fontweight='bold',
        ha='center', va='center', fontfamily='monospace')
    ax_cmp.text(cx, 0.20, f'prev: {prev:.1f}',
        transform=ax_cmp.transAxes,
        color=WDIM, fontsize=7.5,
        ha='center', va='center', fontfamily='monospace')

ax_cmp.text(0.5, 0.04, '↑ Mejoró   →  Estable   ↓  Empeoró',
    transform=ax_cmp.transAxes,
    color=WDIM, fontsize=8, ha='center', va='bottom',
    fontfamily='monospace')

ax_cmp.axhline(0.0, color=GOLD+"44", linewidth=0.5)


# ══════════════════════════════════════════════════════════════════════════════
# 8 · FOOTER
# ══════════════════════════════════════════════════════════════════════════════
ax_ftr = fig.add_subplot(gs[8])
ax_ftr.set_facecolor(BG)
ax_ftr.set_xlim(0, 1); ax_ftr.set_ylim(0, 1)
ax_ftr.axis('off')

ax_ftr.axhline(0.99, color=GOLD, linewidth=1.2)

_hoy = datetime.now().date()
_dias = (7 - _hoy.weekday()) % 7 or 7
_lunes = _hoy + timedelta(days=_dias)
_meses = ['enero','febrero','marzo','abril','mayo','junio','julio','agosto','septiembre','octubre','noviembre','diciembre']
_proximo_lunes = f"lunes {_lunes.day} de {_meses[_lunes.month-1]} {_lunes.year}"

ax_ftr.text(0.5, 0.88,
    f'Próximo escaneo automático: {_proximo_lunes}',
    transform=ax_ftr.transAxes,
    color=GOLD, fontsize=9, fontweight='bold',
    ha='center', va='top', fontfamily='monospace')

ax_ftr.text(0.5, 0.66,
    'Sistema Fortín Inteligente · Faro Protocol · protocolfaro@gmail.com',
    transform=ax_ftr.transAxes,
    color=WDIM, fontsize=8,
    ha='center', va='top', fontfamily='monospace')

ax_ftr.text(0.5, 0.45,
    f'SHA-256: {SHA}',
    transform=ax_ftr.transAxes,
    color=GOLD+'99', fontsize=6.5,
    ha='center', va='top', fontfamily='monospace')

ax_ftr.text(0.5, 0.20,
    'Generado automáticamente · Sin intervención manual · Sentinel-2 / SAR / InSAR',
    transform=ax_ftr.transAxes,
    color=WDIM, fontsize=7.5,
    ha='center', va='top', fontfamily='monospace')

for lx, ltxt in [(0.06, 'ESA'), (0.14, 'COP'), (0.22, 'NASA')]:
    ax_ftr.add_patch(FancyBboxPatch(
        (lx - 0.03, 0.02), 0.07, 0.28,
        boxstyle='round,pad=0.01', transform=ax_ftr.transAxes,
        facecolor=BG3, edgecolor=GOLD+'66', linewidth=0.7, zorder=3))
    ax_ftr.text(lx + 0.005, 0.16, ltxt,
        transform=ax_ftr.transAxes, color=GOLD,
        fontsize=7, fontweight='bold', ha='center', va='center',
        fontfamily='monospace', zorder=4)
for lx, ltxt in [(0.78, 'ESA'), (0.86, 'COP'), (0.94, 'NASA')]:
    ax_ftr.add_patch(FancyBboxPatch(
        (lx - 0.03, 0.02), 0.07, 0.28,
        boxstyle='round,pad=0.01', transform=ax_ftr.transAxes,
        facecolor=BG3, edgecolor=GOLD+'66', linewidth=0.7, zorder=3))
    ax_ftr.text(lx + 0.005, 0.16, ltxt,
        transform=ax_ftr.transAxes, color=GOLD,
        fontsize=7, fontweight='bold', ha='center', va='center',
        fontfamily='monospace', zorder=4)


# ─── SAVE ────────────────────────────────────────────────────────────────────
import pathlib as _pl
_REPORT_DIR = _pl.Path(__file__).parent.parent / 'reportes_velez'
_REPORT_DIR.mkdir(exist_ok=True)
out = str(_out_path or (_REPORT_DIR / 'faro_reporte_velez_canchero.png'))
fig.savefig(out, dpi=DPI, facecolor=BG, bbox_inches='tight', pad_inches=0.08)
plt.close(fig)
print(f"Saved: {out}")
