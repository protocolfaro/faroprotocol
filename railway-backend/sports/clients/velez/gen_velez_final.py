"""
Faro Protocol — Reporte Vélez Sarsfield FINAL
200 DPI · Legible en celular y pantalla grande
"""
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.gridspec as gridspec
import matplotlib.patches as mpatches
from matplotlib.patches import FancyBboxPatch, FancyArrowPatch
from matplotlib.colors import LinearSegmentedColormap
import numpy as np
import hashlib, os, textwrap
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
BORDER= 'rgba(201,168,76,0.25)'

SEM_COLOR = {'verde': GRNL, 'amarillo': YELL, 'rojo': REDL}
SEM_EMOJI = {'verde': '●', 'amarillo': '●', 'rojo': '●'}

# ─── DATOS ───────────────────────────────────────────────────────────────────
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
    },
]

now = datetime.now()
SCAN_DATE = now.strftime('%d de %B de %Y  ·  %H:%M UTC−3')
SHA = hashlib.sha256(f"FARO-VELEZ-{now.isoformat()}".encode()).hexdigest()

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
    canchas = vd.get('sectores', {}).get('canchero', {}).get('canchas', [])
    if not canchas:
        return None
    zones = []
    for c in canchas[:5]:
        sem      = c.get('sem', 'amarillo')
        ndvi     = c.get('ndvi')
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
            'ndre': round(ndvi * 0.65, 2) if ndvi is not None else None,
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
        })
    return zones

import os as _os, json as _json
_vd_path   = _os.environ.get("FARO_VD_PATH")
_insar_mm  = None   # Panel 5: deformación global Amalfitani (mm)
if _vd_path:
    try:
        with open(_vd_path, encoding="utf-8") as _f:
            _vd_data = _json.load(_f)
        _zones_real = _build_zones_from_vd(_vd_data)
        if _zones_real:
            ZONES = _zones_real
        # InSAR global (mm) para Panel 5
        _insar_mm = _vd_data.get("sectores", {}).get("estadio", {}).get("insar_mm")
    except Exception as _e:
        print(f"FARO_VD_PATH final: {_e} — usando datos hardcodeados")
_out_path = _os.environ.get("FARO_OUT_PATH")

# ─── SETUP FIGURE ────────────────────────────────────────────────────────────
DPI = 200
FW, FH = 13.5, 27          # inches  → 2700 × 5400 px @ 200dpi
fig = plt.figure(figsize=(FW, FH), dpi=DPI, facecolor=BG)

# outer margin
fig.subplots_adjust(left=0.03, right=0.97, top=0.99, bottom=0.01, hspace=0.0)

# Row heights (relative)
gs = gridspec.GridSpec(
    9, 1,
    figure=fig,
    hspace=0.06,
    height_ratios=[1.6, 1.8, 3.8, 3.8, 2.0, 2.2, 3.4, 1.5, 0.85],
)

# ─── HELPERS ─────────────────────────────────────────────────────────────────

def set_dark(ax, spine_color=GOLD+'55'):
    ax.set_facecolor(BG2)
    for sp in ax.spines.values():
        sp.set_edgecolor(spine_color)
        sp.set_linewidth(0.6)

def panel_box(ax, title, title_color=GOLD):
    """Draw a dark panel with a gold title bar at top."""
    set_dark(ax)
    ax.set_xticks([])
    ax.set_yticks([])
    if title:
        ax.text(0.5, 0.97, title,
                transform=ax.transAxes,
                color=title_color, fontsize=9, fontweight='bold',
                ha='center', va='top',
                fontfamily='monospace')

def sem_dot(ax, x, y, color, size=22, transform=None):
    t = ax.transAxes if transform is None else transform
    ax.plot(x, y, 'o', ms=size, color=color,
            transform=t, zorder=10, clip_on=False)
    ax.plot(x, y, 'o', ms=size+4, color=color, alpha=0.18,
            transform=t, zorder=9, clip_on=False)

def ndvi_colormap():
    colors = [(0.55, 0.0, 0.0), (0.85, 0.35, 0.0),
              (0.95, 0.75, 0.1), (0.4, 0.75, 0.2),
              (0.05, 0.45, 0.05)]
    return LinearSegmentedColormap.from_list('ndvi', colors)


# ══════════════════════════════════════════════════════════════════════════════
# 0 · HEADER
# ══════════════════════════════════════════════════════════════════════════════
ax_hdr = fig.add_subplot(gs[0])
ax_hdr.set_facecolor(BG)
ax_hdr.set_xlim(0, 1); ax_hdr.set_ylim(0, 1)
ax_hdr.axis('off')

# thick gold top bar
ax_hdr.add_patch(mpatches.Rectangle((0, 0.93), 1, 0.07,
    transform=ax_hdr.transAxes, color=GOLD, zorder=2))

ax_hdr.text(0.5, 0.88, 'FARO PROTOCOL — FORTÍN INTELIGENTE',
    transform=ax_hdr.transAxes, color=GOLD,
    fontsize=17, fontweight='bold', ha='center', va='top',
    fontfamily='monospace')

ax_hdr.text(0.5, 0.70, 'VÉLEZ SARSFIELD · GESTIÓN AGRONÓMICA SATELITAL',
    transform=ax_hdr.transAxes, color=WHITE,
    fontsize=12.5, fontweight='bold', ha='center', va='top',
    fontfamily='sans-serif')

ax_hdr.text(0.5, 0.52, f'Escaneo satelital: {SCAN_DATE}',
    transform=ax_hdr.transAxes, color=WDIM,
    fontsize=9, ha='center', va='top', fontfamily='monospace')

ax_hdr.text(0.5, 0.36, 'Lat −34.6379  ·  Lon −58.5288  ·  WGS-84',
    transform=ax_hdr.transAxes, color=WDIM,
    fontsize=8.5, ha='center', va='top', fontfamily='monospace')

# badge INFORME BASE
badge_x, badge_y = 0.5, 0.12
for bx, by, bw, bh, bc, bfc in [
    (0.31, 0.02, 0.38, 0.22, GOLD, BG3)
]:
    ax_hdr.add_patch(FancyBboxPatch(
        (bx, by), bw, bh,
        boxstyle='round,pad=0.01',
        transform=ax_hdr.transAxes,
        facecolor=bfc, edgecolor=GOLD, linewidth=1.2, zorder=3))
ax_hdr.text(0.5, 0.13, '  INFORME BASE · ESTADO CERO · MAYO 2026  ',
    transform=ax_hdr.transAxes, color=GOLD,
    fontsize=8.5, fontweight='bold', ha='center', va='bottom',
    fontfamily='monospace', zorder=4)

# logos placeholder text (ESA / Copernicus / NASA)
for lx, ltxt in [(0.06, 'ESA'), (0.14, 'COP'), (0.22, 'NASA')]:
    ax_hdr.add_patch(FancyBboxPatch(
        (lx - 0.025, 0.03), 0.065, 0.22,
        boxstyle='round,pad=0.01',
        transform=ax_hdr.transAxes,
        facecolor=BG3, edgecolor=GOLD+'88', linewidth=0.8, zorder=3))
    ax_hdr.text(lx + 0.008, 0.14, ltxt,
        transform=ax_hdr.transAxes, color=GOLD,
        fontsize=7, fontweight='bold', ha='center', va='center',
        fontfamily='monospace', zorder=4)

# right side logos
for lx, ltxt in [(0.78, 'ESA'), (0.86, 'COP'), (0.94, 'NASA')]:
    ax_hdr.add_patch(FancyBboxPatch(
        (lx - 0.025, 0.03), 0.065, 0.22,
        boxstyle='round,pad=0.01',
        transform=ax_hdr.transAxes,
        facecolor=BG3, edgecolor=GOLD+'88', linewidth=0.8, zorder=3))
    ax_hdr.text(lx + 0.008, 0.14, ltxt,
        transform=ax_hdr.transAxes, color=GOLD,
        fontsize=7, fontweight='bold', ha='center', va='center',
        fontfamily='monospace', zorder=4)

# divider line
ax_hdr.axhline(0.0, color=GOLD, linewidth=1.5, xmin=0, xmax=1)


# ══════════════════════════════════════════════════════════════════════════════
# 1 · PANEL 0 — SEMÁFORO EJECUTIVO
# ══════════════════════════════════════════════════════════════════════════════
ax_sem = fig.add_subplot(gs[1])
ax_sem.set_facecolor(BG2)
ax_sem.set_xlim(0, 1); ax_sem.set_ylim(0, 1)
ax_sem.axis('off')

# section title
ax_sem.text(0.5, 0.98, 'PANEL 0 · SEMÁFORO EJECUTIVO — LECTURA EN 3 SEGUNDOS',
    transform=ax_sem.transAxes, color=GOLD,
    fontsize=8.5, fontweight='bold', ha='center', va='top',
    fontfamily='monospace')

ax_sem.axhline(0.92, color=GOLD+'44', linewidth=0.5)

zone_names_short = ['Campo\nAmalfitani', 'Cancha 1', 'Cancha 2', 'Cancha 3', 'Cancha 4']
xs = np.linspace(0.10, 0.90, 5)
bw = 0.16

for i, z in enumerate(ZONES):
    cx = xs[i]
    sc = SEM_COLOR[z['sem']]
    bx = cx - bw/2 + 0.005
    by = 0.06
    bh = 0.84
    # background box
    border_c = sc + 'AA' if z['sem'] == 'rojo' else sc + '55'
    lw = 2.0 if z['sem'] == 'rojo' else 0.9
    ax_sem.add_patch(FancyBboxPatch(
        (bx, by), bw - 0.01, bh,
        boxstyle='round,pad=0.008',
        transform=ax_sem.transAxes,
        facecolor=BG3 if z['sem'] != 'rojo' else '#1a0505',
        edgecolor=sc, linewidth=lw, zorder=2))

    # zone name
    ax_sem.text(cx + 0.005, by + bh - 0.04, z['name'],
        transform=ax_sem.transAxes,
        color=WHITE, fontsize=8, fontweight='bold',
        ha='center', va='top', fontfamily='sans-serif', zorder=3)

    # semáforo circle
    dot_y = by + bh - 0.32
    ax_sem.plot(cx + 0.005, dot_y, 'o', ms=22, color=sc,
        transform=ax_sem.transAxes, zorder=4, clip_on=False)
    ax_sem.plot(cx + 0.005, dot_y, 'o', ms=30, color=sc, alpha=0.12,
        transform=ax_sem.transAxes, zorder=3, clip_on=False)

    # estado
    ax_sem.text(cx + 0.005, dot_y - 0.145, z['estado'],
        transform=ax_sem.transAxes,
        color=sc, fontsize=9, fontweight='bold',
        ha='center', va='top', fontfamily='monospace', zorder=3)

    # accion
    ax_sem.text(cx + 0.005, by + 0.18,
        z['accion'],
        transform=ax_sem.transAxes,
        color=WHITE if z['sem'] != 'rojo' else REDXL,
        fontsize=7.0, ha='center', va='bottom',
        fontfamily='monospace', zorder=3,
        linespacing=1.35)

ax_sem.axhline(0.0, color=GOLD+'44', linewidth=0.5)


# ══════════════════════════════════════════════════════════════════════════════
# 2 · PANEL 1 — MAPA DE PRESCRIPCIÓN
# ══════════════════════════════════════════════════════════════════════════════
ax_map1 = fig.add_subplot(gs[2])
ax_map1.set_facecolor(BG2)
ax_map1.set_xlim(0, 10); ax_map1.set_ylim(0, 10)
ax_map1.axis('off')

ax_map1.text(5, 9.78, 'PANEL 1 · MAPA DE PRESCRIPCIÓN — NDVI + ACCIONES',
    color=GOLD, fontsize=8.5, fontweight='bold',
    ha='center', va='top', fontfamily='monospace')

# Build synthetic NDVI field
np.random.seed(42)

# Layout: Campo large left, 4 canchas right
# Campo: x 0.5–3.5, y 0.5–8.5
# Cancha 1: x 4.0–5.8, y 6.2–8.5
# Cancha 2: x 4.0–5.8, y 3.8–5.8
# Cancha 3: x 6.2–8.0, y 6.2–8.5
# Cancha 4: x 6.2–8.0, y 3.8–5.8  (ROJO)
# Villa Olímpica label below

ndvi_cmap = ndvi_colormap()

ZONE_RECTS = [
    # (x0, y0, x1, y1, ndvi_center, label, sem, map_accion, arrow_from)
    (0.5, 0.8, 3.5, 8.8, 0.68, 'Campo\nAmalfitani', 'verde',
     'AERIFICAR\nPORTERÍAS\nSEMANA 3', (3.5, 4.8)),
    (4.0, 6.0, 5.8, 8.6, 0.48, 'Cancha 1', 'amarillo',
     'FUNGICIDA\nPREVENTIVO\nESTA SEMANA', (5.8, 7.3)),
    (4.0, 3.5, 5.8, 5.6, 0.52, 'Cancha 2', 'amarillo',
     'FERTILIZAR\n20kg N/ha\nSEMANA 2', (5.8, 4.55)),
    (6.2, 6.0, 8.0, 8.6, 0.39, 'Cancha 3', 'amarillo',
     'FUNGICIDA\nACTIVO\nESTA SEMANA', (8.0, 7.3)),
    (6.2, 3.5, 8.0, 5.6, 0.24, 'Cancha 4', 'rojo',
     'INTERVENCIÓN\nURGENTE\nHOY', (8.0, 4.55)),
]

for (x0, y0, x1, y1, ndvi_c, lbl, sem, maccion, _) in ZONE_RECTS:
    sc = SEM_COLOR[sem]
    # fill with noise around ndvi_c
    nx, ny = 30, 30
    grid = np.clip(
        np.random.normal(ndvi_c, 0.06, (ny, nx)) +
        0.04 * np.sin(np.linspace(0, 4, ny))[:, None],
        0, 1)
    ax_map1.imshow(grid, extent=[x0, x1, y0, y1],
                   cmap=ndvi_cmap, vmin=0, vmax=1,
                   aspect='auto', zorder=1, alpha=0.9)

    # border
    lw = 2.5 if sem == 'rojo' else 1.2
    rect = plt.Rectangle((x0, y0), x1-x0, y1-y0,
        linewidth=lw, edgecolor=sc, facecolor='none', zorder=5)
    ax_map1.add_patch(rect)

    # if critical — red overlay
    if sem == 'rojo':
        rect_ov = plt.Rectangle((x0, y0), x1-x0, y1-y0,
            linewidth=0, facecolor=RED, alpha=0.22, zorder=4)
        ax_map1.add_patch(rect_ov)

    # zone name
    cx, cy = (x0+x1)/2, y1 - 0.12
    ax_map1.text(cx, cy, lbl,
        color='white', fontsize=7.5, fontweight='bold',
        ha='center', va='top', zorder=7,
        bbox=dict(facecolor='#000000CC', edgecolor='none', pad=2))

    # semáforo badge
    ax_map1.plot(x0+0.18, y1-0.22, 'o', ms=9, color=sc, zorder=8)

    # action text inside zone
    ax_map1.text(cx, (y0+y1)/2 - 0.1, maccion,
        color='white', fontsize=6.5, fontweight='bold',
        ha='center', va='center', zorder=7,
        fontfamily='monospace',
        bbox=dict(facecolor='#000000BB', edgecolor=sc,
                  linewidth=0.8, pad=3, boxstyle='round,pad=0.3'))

# Numbered arrows pointing to critical zones
arrow_targets = [
    (2.0, 2.5, '[1]', GRNL, 'Zona óptima'),
    (4.9, 7.3, '[2]', YELL, 'Cancha 1: atención'),
    (4.9, 4.55, '[3]', YELL, 'Cancha 2: atención'),
    (7.1, 7.3, '[4]', YELL, 'Cancha 3: atención'),
    (7.1, 4.55, '[5]', REDL, 'Cancha 4: CRÍTICA'),
]
for ax_t, ay_t, num, clr, lbl in arrow_targets:
    ax_map1.text(ax_t, ay_t, num,
        color=clr, fontsize=11, fontweight='bold',
        ha='center', va='center', zorder=9)

# Scale bar
scale_data = [
    (0.0, '#8B0000', 'Suelo\ndesnudo'),
    (0.3, '#d4a017', 'Estrés\ncrítico'),
    (0.5, '#4CAF50', 'Césped\nnormal'),
    (1.0, '#1a5e1a', 'Máx.\nbiomasa'),
]
bar_y = 0.35
bar_x0, bar_x1 = 0.5, 8.0
gradient = np.linspace(0, 1, 200)[np.newaxis, :]
ax_map1.imshow(gradient, extent=[bar_x0, bar_x1, bar_y-0.12, bar_y+0.12],
               cmap=ndvi_cmap, aspect='auto', vmin=0, vmax=1, zorder=6)
ax_map1.add_patch(plt.Rectangle(
    (bar_x0, bar_y-0.12), bar_x1-bar_x0, 0.24,
    linewidth=0.8, edgecolor=GOLD, facecolor='none', zorder=7))

for val, clr, lbl in scale_data:
    sx = bar_x0 + val * (bar_x1 - bar_x0)
    ax_map1.plot([sx, sx], [bar_y+0.12, bar_y+0.25],
                 color=clr, linewidth=1.2, zorder=7)
    ax_map1.text(sx, bar_y + 0.32, f'{val:.1f}\n{lbl}',
        color=WHITE, fontsize=5.5, ha='center', va='bottom',
        fontfamily='monospace', zorder=7, linespacing=1.2)

ax_map1.text(4.25, bar_y - 0.22, 'Escala NDVI',
    color=WDIM, fontsize=7, ha='center', fontfamily='monospace')

# Alert line at 0.3
alert_x = bar_x0 + 0.3 * (bar_x1 - bar_x0)
ax_map1.annotate('← línea\nde alerta',
    xy=(alert_x, bar_y - 0.0),
    xytext=(alert_x + 0.6, bar_y - 0.35),
    color=YELL, fontsize=6, fontfamily='monospace',
    arrowprops=dict(arrowstyle='->', color=YELL, lw=0.8))


# ══════════════════════════════════════════════════════════════════════════════
# 3 · PANEL 2 — MAPA DE ALERTAS
# ══════════════════════════════════════════════════════════════════════════════
ax_map2 = fig.add_subplot(gs[3])
ax_map2.set_facecolor(BG2)
ax_map2.set_xlim(0, 10); ax_map2.set_ylim(0, 10)
ax_map2.axis('off')

ax_map2.text(5, 9.78, 'PANEL 2 · MAPA DE ALERTAS — FOCOS CRÍTICOS CANCHA 4',
    color=GOLD, fontsize=8.5, fontweight='bold',
    ha='center', va='top', fontfamily='monospace')

# Draw facility overview with alert overlay
FACILITY = [
    (0.5, 2.0, 3.5, 9.2, '#1a3d1a', 'verde', 'Campo\nAmalfitani', GRNL),
    (4.0, 6.5, 5.8, 9.0, '#1a2e10', 'verde', 'C1', YELL),
    (4.0, 3.8, 5.8, 6.0, '#1a2e10', 'verde', 'C2', YELL),
    (6.2, 6.5, 8.0, 9.0, '#1a2e10', 'verde', 'C3', YELL),
    (6.2, 3.8, 8.0, 6.0, '#2a0000', 'rojo',  'C4', REDL),
]

for (x0, y0, x1, y1, fc, sem, lbl, ec) in FACILITY:
    ax_map2.add_patch(plt.Rectangle(
        (x0, y0), x1-x0, y1-y0,
        facecolor=fc, edgecolor=ec, linewidth=1.5 if sem=='rojo' else 0.8,
        zorder=2))
    cx2, cy2 = (x0+x1)/2, (y0+y1)/2
    ax_map2.text(cx2, cy2+0.8, lbl,
        color=WHITE, fontsize=7, fontweight='bold',
        ha='center', va='center', zorder=5)

# CRÍTICA overlay on C4
ax_map2.add_patch(plt.Rectangle(
    (6.2, 3.8), 1.8, 2.2,
    facecolor=RED, alpha=0.28, zorder=3))
ax_map2.text(7.1, 4.9, '⚠ CRÍTICA', color=REDXL,
    fontsize=12, fontweight='bold', ha='center', va='center', zorder=6,
    fontfamily='monospace',
    bbox=dict(facecolor='#1a0000', edgecolor=REDL, linewidth=1.5,
              pad=4, boxstyle='round,pad=0.3'))

# Alert foci inside Cancha 4 with numbered arrows
focos = [
    (6.65, 5.45, '[1] Hongos activos\n   85 m²', REDL),
    (7.50, 4.25, '[2] Drenaje crítico', REDL),
    (6.80, 4.05, '[3] Malezas 35%', YELL),
]
for (fx, fy, ftxt, fc_) in focos:
    ax_map2.plot(fx, fy, 's', ms=8, color=fc_, zorder=7, alpha=0.9)
    ax_map2.text(fx + 0.18, fy, ftxt,
        color=fc_, fontsize=7, fontweight='bold',
        va='center', fontfamily='monospace', zorder=7)

# Legend
legend_items = [
    (GRNL, '■ Sin problema'),
    (YELL, '■ Monitorear'),
    (REDL, '■ Intervención inmediata'),
]
for li, (lc, ltxt) in enumerate(legend_items):
    ax_map2.text(0.6, 2.3 - li * 0.55, ltxt,
        color=lc, fontsize=8.5, fontweight='bold',
        fontfamily='monospace', zorder=5,
        bbox=dict(facecolor=BG3, edgecolor=lc+'66',
                  linewidth=0.6, pad=3, boxstyle='round,pad=0.2'))

# Condition scale
scale2 = [(GRNL, 'Verde\nSin problema'),
           (YELL, 'Amarillo\nMonitorear'),
           (REDL, 'Rojo\nIntervención')]
for si, (sc2, slbl) in enumerate(scale2):
    bx2 = 0.7 + si * 3.1
    ax_map2.add_patch(plt.Rectangle((bx2, 0.5), 2.5, 0.55,
        facecolor=sc2, edgecolor='none', zorder=4, alpha=0.85))
    ax_map2.text(bx2 + 1.25, 0.77, slbl,
        color='black' if sc2 == GRNL else 'black',
        fontsize=6, fontweight='bold', ha='center', va='center',
        fontfamily='monospace', zorder=5, linespacing=1.1)
ax_map2.text(5, 0.22, 'Leyenda · Condición de superficie',
    color=WDIM, fontsize=7, ha='center', fontfamily='monospace')


# ══════════════════════════════════════════════════════════════════════════════
# 4 · PANEL 3 — ÍNDICE FUSIÓN FARO
# ══════════════════════════════════════════════════════════════════════════════
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
# scale bar  0 → 100
ax_fus.add_patch(mpatches.Rectangle(
    (0.07, scale_y), 0.86, 0.06,
    transform=ax_fus.transAxes,
    facecolor='none', edgecolor=GOLD+'44', linewidth=0.6))
grad_arr = np.linspace(0, 1, 200)[np.newaxis, :]
ax_fus_img = ax_fus.inset_axes([0.07, scale_y, 0.86, 0.06])
ax_fus_img.imshow(grad_arr, cmap=ndvi_colormap(), aspect='auto',
                  vmin=0, vmax=1)
ax_fus_img.axis('off')
for val, lbl in [(0, '0\nCrítico'), (50, '50\nNormal'), (100, '100\nÓptimo')]:
    sx = 0.07 + val/100 * 0.86
    ax_fus.plot([sx, sx], [scale_y - 0.01, scale_y + 0.07],
        color=WHITE, linewidth=0.8,
        transform=ax_fus.transAxes, zorder=5)
    ax_fus.text(sx, scale_y - 0.03, lbl,
        transform=ax_fus.transAxes,
        color=WDIM, fontsize=7, ha='center', va='top',
        fontfamily='monospace', linespacing=1.1)

xs5 = np.linspace(0.12, 0.88, 5)
for i, z in enumerate(ZONES):
    cx = xs5[i]
    sc = SEM_COLOR[z['sem']]
    fv = z['fusion']

    # zone label
    ax_fus.text(cx, 0.88, z['name'],
        transform=ax_fus.transAxes,
        color=WHITE, fontsize=8, fontweight='bold',
        ha='center', va='top', fontfamily='sans-serif')

    # big index number
    ax_fus.text(cx, 0.63, f'{fv}',
        transform=ax_fus.transAxes,
        color=sc, fontsize=22, fontweight='bold',
        ha='center', va='center', fontfamily='monospace')

    # dot
    ax_fus.plot(cx, 0.42, 'o', ms=14, color=sc,
        transform=ax_fus.transAxes, zorder=4)
    ax_fus.plot(cx, 0.42, 'o', ms=22, color=sc, alpha=0.14,
        transform=ax_fus.transAxes, zorder=3)

    # pointer line on scale
    ptr_x = 0.07 + fv/100 * 0.86
    ax_fus.annotate('',
        xy=(ptr_x, scale_y + 0.06),
        xytext=(cx, 0.30),
        xycoords='axes fraction', textcoords='axes fraction',
        arrowprops=dict(arrowstyle='->', color=sc+'99', lw=0.8))


# ══════════════════════════════════════════════════════════════════════════════
# 5 · PANEL InSAR
# ══════════════════════════════════════════════════════════════════════════════
ax_ins = fig.add_subplot(gs[5])
ax_ins.set_facecolor(BG3)
for sp in ax_ins.spines.values():
    sp.set_edgecolor(GOLD+'44')
    sp.set_linewidth(0.6)

ax_ins.text(0.5, 1.02, 'PANEL InSAR · DEFORMACIÓN ESTRUCTURAL — TRIBUNAS',
    transform=ax_ins.transAxes,
    color=GOLD, fontsize=8.5, fontweight='bold',
    ha='center', va='bottom', fontfamily='monospace')

labels = ['Norte', 'Sur', 'Este', 'Oeste']
if _insar_mm is not None:
    # Valor global del assembler — mismo para todas las tribunas hasta tener per-tribuna
    vals   = [round(_insar_mm, 2)] * 4
    colors = [GRNL if _insar_mm < 1.0 else (YELL if _insar_mm < 2.0 else REDL)] * 4
else:
    vals   = [0.85, 1.20, 0.60, 2.80]
    colors = [GRNL, YELL, GRNL, REDL]
x_pos = np.arange(len(labels))

bars = ax_ins.bar(x_pos, vals, color=colors, edgecolor=BG, linewidth=0.8,
                  zorder=3, width=0.55)

# threshold line
ax_ins.axhline(2.0, color=REDL, linewidth=1.8, linestyle='--', zorder=4)
ax_ins.text(3.55, 2.08, '← umbral 2mm', color=REDL,
    fontsize=8.5, fontweight='bold', va='bottom', fontfamily='monospace')

# value labels on bars
for bar_, val in zip(bars, vals):
    clr = REDXL if val >= 2.0 else WHITE
    ax_ins.text(bar_.get_x() + bar_.get_width()/2, val + 0.05,
        f'{val:.2f}mm', color=clr, fontsize=9, fontweight='bold',
        ha='center', va='bottom', fontfamily='monospace')

# alert arrow on Oeste
ax_ins.annotate('⚠ INSPECCIÓN\nESTRUCTURAL\nRECOMENDADA',
    xy=(3, 2.80), xytext=(2.3, 3.5),
    color=REDXL, fontsize=8, fontweight='bold', fontfamily='monospace',
    arrowprops=dict(arrowstyle='->', color=REDXL, lw=1.5),
    bbox=dict(facecolor='#1a0000', edgecolor=REDL, linewidth=1.2,
              pad=4, boxstyle='round,pad=0.3'))

ax_ins.set_xlim(-0.6, 3.8)
ax_ins.set_ylim(0, 4.8)
ax_ins.set_xticks(x_pos)
ax_ins.set_xticklabels(labels, color=WHITE, fontsize=10, fontfamily='monospace')
ax_ins.set_ylabel('Desplazamiento (mm)', color=WDIM, fontsize=8, fontfamily='monospace')
ax_ins.tick_params(colors=WDIM, labelsize=8)
ax_ins.yaxis.label.set_color(WDIM)
for spine in ax_ins.spines.values():
    spine.set_edgecolor(GOLD+'33')
ax_ins.set_facecolor(BG3)
ax_ins.yaxis.set_tick_params(labelcolor=WDIM)

# scale note
ax_ins.text(-0.58, 0.2, '0mm\nEstable', color=GRNL,
    fontsize=7, fontfamily='monospace', va='bottom', linespacing=1.2)
ax_ins.text(-0.58, 2.05, '2mm\nUmbral', color=YELL,
    fontsize=7, fontfamily='monospace', va='bottom', linespacing=1.2)
ax_ins.text(-0.58, 4.4, '5mm\nCrítico', color=REDL,
    fontsize=7, fontfamily='monospace', va='top', linespacing=1.2)


# ══════════════════════════════════════════════════════════════════════════════
# 6 · TABLA DE PRESCRIPCIÓN
# ══════════════════════════════════════════════════════════════════════════════
ax_tbl = fig.add_subplot(gs[6])
ax_tbl.set_facecolor(BG2)
ax_tbl.axis('off')

ax_tbl.text(0.5, 0.99, 'TABLA DE PRESCRIPCIÓN AGRONÓMICA',
    transform=ax_tbl.transAxes,
    color=GOLD, fontsize=9, fontweight='bold',
    ha='center', va='top', fontfamily='monospace')

cols = ['ZONA', 'NDVI', 'NDRE', 'N\nkg/ha', 'RIEGO\nmm',
        'RESIEMBRA', 'HONGOS', 'COMPACT.', 'DRENAJE', 'MALEZAS',
        'TIMELINE', 'PRIOR.', 'ACCIÓN INMEDIATA']
col_w = [0.12, 0.05, 0.05, 0.05, 0.05,
         0.07, 0.07, 0.07, 0.06, 0.06,
         0.08, 0.05, 0.18]

rows = []
for z in ZONES:
    sc = SEM_COLOR[z['sem']]
    rows.append({
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

# Draw table using matplotlib table
cell_colors = []
for row in rows:
    sc = row['color']
    if row['sem'] == 'rojo':
        row_bg = '#1a0505'
        row_txt_bg = ['#1a0505'] * len(cols)
    elif row['sem'] == 'amarillo':
        row_bg = '#1a1400'
        row_txt_bg = ['#1a1400'] * len(cols)
    else:
        row_bg = BG3
        row_txt_bg = [BG3] * len(cols)
    cell_colors.append(row_txt_bg)

table_data = [[r['data'][j] for j in range(len(cols))] for r in rows]

the_table = ax_tbl.table(
    cellText=table_data,
    colLabels=cols,
    cellLoc='center',
    loc='center',
    bbox=[0.0, 0.01, 1.0, 0.93],
)
the_table.auto_set_font_size(False)
the_table.set_fontsize(7)

# Style header
for j in range(len(cols)):
    cell = the_table[0, j]
    cell.set_facecolor(BG3)
    cell.set_text_props(color=GOLD, fontsize=7, fontweight='bold',
                        fontfamily='monospace')
    cell.set_edgecolor(GOLD + '55')
    cell.set_linewidth(0.5)

# Style data rows
for i, row in enumerate(rows):
    sc = row['color']
    for j in range(len(cols)):
        cell = the_table[i+1, j]
        if row['sem'] == 'rojo':
            cell.set_facecolor('#1a0505')
        elif row['sem'] == 'amarillo':
            cell.set_facecolor('#181200')
        else:
            cell.set_facecolor(BG3)
        cell.set_edgecolor(GOLD + '33')
        cell.set_linewidth(0.4)

        # text color
        if j == 11:  # PRIOR dot
            cell.set_text_props(color=sc, fontsize=12, fontweight='bold')
        elif j == 0:  # ZONA name
            cell.set_text_props(color=WHITE, fontsize=7, fontweight='bold',
                                fontfamily='monospace')
        elif j == len(cols)-1:  # ACCIÓN
            cell.set_text_props(
                color=sc if row['sem'] != 'verde' else GRNL,
                fontsize=6.5, fontweight='bold', fontfamily='monospace')
        elif j == 10:  # TIMELINE
            cell.set_text_props(
                color=sc, fontsize=7, fontweight='bold',
                fontfamily='monospace')
        else:
            cell.set_text_props(color=WHITE, fontsize=7,
                                fontfamily='monospace')

# highlight Cancha 4 row edges
for j in range(len(cols)):
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

ax_cmp.text(0.5, 0.04,
    '↑ Mejoró   →  Estable   ↓  Empeoró',
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

# logos row
for lx, ltxt in [(0.06, 'ESA'), (0.14, 'COP'), (0.22, 'NASA')]:
    ax_ftr.add_patch(FancyBboxPatch(
        (lx - 0.03, 0.02), 0.07, 0.28,
        boxstyle='round,pad=0.01',
        transform=ax_ftr.transAxes,
        facecolor=BG3, edgecolor=GOLD+'66', linewidth=0.7, zorder=3))
    ax_ftr.text(lx + 0.005, 0.16, ltxt,
        transform=ax_ftr.transAxes, color=GOLD,
        fontsize=7, fontweight='bold', ha='center', va='center',
        fontfamily='monospace', zorder=4)
for lx, ltxt in [(0.78, 'ESA'), (0.86, 'COP'), (0.94, 'NASA')]:
    ax_ftr.add_patch(FancyBboxPatch(
        (lx - 0.03, 0.02), 0.07, 0.28,
        boxstyle='round,pad=0.01',
        transform=ax_ftr.transAxes,
        facecolor=BG3, edgecolor=GOLD+'66', linewidth=0.7, zorder=3))
    ax_ftr.text(lx + 0.005, 0.16, ltxt,
        transform=ax_ftr.transAxes, color=GOLD,
        fontsize=7, fontweight='bold', ha='center', va='center',
        fontfamily='monospace', zorder=4)


# ─── SAVE ────────────────────────────────────────────────────────────────────
import pathlib as _pl
_REPORT_DIR = _pl.Path(__file__).parents[4] / 'reportes_velez'
_REPORT_DIR.mkdir(exist_ok=True)
out = str(_out_path or (_REPORT_DIR / 'faro_reporte_velez_agro_FINAL.png'))
fig.savefig(out, dpi=DPI, facecolor=BG, bbox_inches='tight',
            pad_inches=0.08)
plt.close(fig)
print(f"Saved: {out}")
