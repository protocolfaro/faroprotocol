"""
gen_velez_piletas.py
Faro Protocol — Complejo Acuático + Natatorio Olímpico · Vélez Sarsfield
Genera: reportes_velez/faro_reporte_velez_piletas.png
Sentinel-2 NIR + Landsat TIRS + SAR/InSAR · Mayo 2026
Sistema de alerta temprana de calidad de agua
"""
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.gridspec as gridspec
import matplotlib.patches as mpatches
from matplotlib.patches import FancyBboxPatch, Circle, Arc
from matplotlib.colors import LinearSegmentedColormap
from matplotlib.cm import ScalarMappable
import numpy as np
import hashlib, pathlib, shutil
from datetime import datetime

# ─── PALETA ──────────────────────────────────────────────────────────────────
BG    = '#06080b'
BG2   = '#0d1117'
BG3   = '#141c24'
GOLD  = '#c9a84c'
GOLDL = '#e2c97e'
WHITE = '#f2ede4'
WDIM  = '#9aa0a8'
REDL  = '#e74c3c'
YELL  = '#f0b429'
GRNL  = '#27ae60'
BLUE  = '#1565c0'
BLUEL = '#4a9ede'
CYAN  = '#00bcd4'
CYANL = '#40c4d4'
BORDER= '#1e2a38'
DPI   = 200

SEM_COL = {'verde': GRNL, 'amarillo': YELL, 'rojo': REDL}

now  = datetime.now()
CERT = hashlib.sha256(f"FARO-VELEZ-PILETAS-{now.isoformat()}".encode()).hexdigest()[:28].upper()
WEEK = now.strftime('Semana del %d de %B de %Y')

# ─── 6 KPIs ACUÁTICOS ────────────────────────────────────────────────────────
KPIS_ACUATICOS = [
    {
        'label':  'TURBIDEZ',
        'sublabel': 'Sentinel-2 NIR',
        'valor':  2.8,
        'unidad': 'NTU',
        'umbral_aten': 5.0,
        'umbral_alert': 10.0,
        'estado': 'ÓPTIMA',
        'sem':    'verde',
        'nota':   '< 5 NTU = agua transparente',
        'historico': [3.1, 2.9, 3.4, 2.8],
    },
    {
        'label':  'CLOROFILA/ALGAS',
        'sublabel': 'Sentinel-2 B5/B4',
        'valor':  0.12,
        'unidad': 'mg/m³',
        'umbral_aten': 0.5,
        'umbral_alert': 2.0,
        'estado': 'ÓPTIMA',
        'sem':    'verde',
        'nota':   '< 0.5 = sin proliferación',
        'historico': [0.09, 0.11, 0.14, 0.12],
    },
    {
        'label':  'TEMP. AGUA',
        'sublabel': 'Sentinel-2 LST',
        'valor':  23.4,
        'unidad': '°C',
        'umbral_aten': 28.0,
        'umbral_alert': 32.0,
        'estado': 'ÓPTIMA',
        'sem':    'verde',
        'nota':   'Rango óptimo 20–26°C',
        'historico': [22.8, 23.1, 23.8, 23.4],
    },
    {
        'label':  'TEMP. TECHO',
        'sublabel': 'Landsat TIRS',
        'valor':  38.7,
        'unidad': '°C',
        'umbral_aten': 40.0,
        'umbral_alert': 50.0,
        'estado': 'ATENCIÓN',
        'sem':    'amarillo',
        'nota':   'Verificar aislamiento',
        'historico': [35.2, 36.8, 37.9, 38.7],
    },
    {
        'label':  'ASENTAMIENTO',
        'sublabel': 'InSAR Sentinel-1',
        'valor':  0.85,
        'unidad': 'mm/sem',
        'umbral_aten': 2.0,
        'umbral_alert': 3.5,
        'estado': 'ÓPTIMA',
        'sem':    'verde',
        'nota':   '< 2mm = estable',
        'historico': [0.72, 0.78, 0.81, 0.85],
    },
    {
        'label':  'SCORE GLOBAL',
        'sublabel': 'Índice FARO',
        'valor':  91,
        'unidad': '/100',
        'umbral_aten': 60,
        'umbral_alert': 40,
        'estado': 'ÓPTIMA',
        'sem':    'verde',
        'nota':   'Calidad acuática excelente',
        'historico': [88, 89, 90, 91],
    },
]

# Alerta global: ÓPTIMA
N_CRIT  = sum(1 for k in KPIS_ACUATICOS if k['sem'] == 'rojo')
N_ATEN  = sum(1 for k in KPIS_ACUATICOS if k['sem'] == 'amarillo')
if N_CRIT > 0:
    ALERTA_GLOBAL = 'ALERTA ROJA'
    ALERTA_COL    = REDL
elif N_ATEN > 0:
    ALERTA_GLOBAL = 'ATENCIÓN'
    ALERTA_COL    = YELL
else:
    ALERTA_GLOBAL = 'ÓPTIMA'
    ALERTA_COL    = GRNL

# ─── REAL DATA OVERRIDE ──────────────────────────────────────────────────────
import os as _os, json as _json
_vd_path = _os.environ.get("FARO_VD_PATH")
if _vd_path:
    try:
        with open(_vd_path, encoding="utf-8") as _f:
            _vd = _json.load(_f)
        _s = _vd.get("sectores", {}).get("piletas", {})
        if _s:
            if "score"   in _s: KPIS_ACUATICOS[5]["valor"] = _s["score"]
            if "sem"     in _s: KPIS_ACUATICOS[5]["sem"]   = _s["sem"]
            if "detalle" in _s: KPIS_ACUATICOS[5]["nota"]  = _s["detalle"]
            if "kpis" in _s:
                for _i, _ku in enumerate(_s["kpis"]):
                    if _i < len(KPIS_ACUATICOS):
                        KPIS_ACUATICOS[_i].update({_k: _v for _k, _v in _ku.items() if _k in KPIS_ACUATICOS[_i]})
            _nc = sum(1 for _k in KPIS_ACUATICOS if _k['sem'] == 'rojo')
            _na = sum(1 for _k in KPIS_ACUATICOS if _k['sem'] == 'amarillo')
            if _nc > 0:   ALERTA_GLOBAL, ALERTA_COL = 'ALERTA ROJA', REDL
            elif _na > 0: ALERTA_GLOBAL, ALERTA_COL = 'ATENCIÓN',    YELL
            else:         ALERTA_GLOBAL, ALERTA_COL = 'ÓPTIMA',      GRNL
    except Exception as _e:
        print(f"FARO_VD_PATH piletas: {_e}")
_out_path = _os.environ.get("FARO_OUT_PATH")

# ─── HELPERS ─────────────────────────────────────────────────────────────────
def panel_ax(ax, title=''):
    ax.set_facecolor(BG2)
    for sp in ax.spines.values():
        sp.set_color(BORDER); sp.set_linewidth(0.7)
    if title:
        ax.set_title(title, color=GOLD, fontsize=9.5, fontweight='bold',
                     loc='left', pad=5, fontfamily='monospace')

def draw_gauge(ax, cx, cy, r, val, max_val, col, label, sublabel, unidad):
    theta_bg = np.linspace(np.pi, 0, 100)
    xs_bg = cx + r * np.cos(theta_bg)
    ys_bg = cy + r * 0.6 * np.sin(theta_bg)
    ax.plot(xs_bg, ys_bg, color=BG3, linewidth=12, solid_capstyle='round', zorder=2)
    pct = min(val / max_val, 1.0)
    theta_val = np.linspace(np.pi, np.pi - pct * np.pi, 100)
    xs_v = cx + r * np.cos(theta_val)
    ys_v = cy + r * 0.6 * np.sin(theta_val)
    ax.plot(xs_v, ys_v, color=col, linewidth=12, solid_capstyle='round', zorder=3)
    ax.text(cx, cy - r*0.15, label, color=GOLD, fontsize=7.5, fontweight='bold',
           ha='center', va='center', fontfamily='monospace')
    ax.text(cx, cy - r*0.42, sublabel, color=WDIM, fontsize=6.5,
           ha='center', va='center', fontfamily='monospace')

# ─── FIGURA ───────────────────────────────────────────────────────────────────
fig = plt.figure(figsize=(13.5, 30), facecolor=BG)
gs = gridspec.GridSpec(8, 1, figure=fig, hspace=0.0,
    height_ratios=[1.1, 3.5, 4.5, 3.5, 3.8, 3.0, 2.2, 0.7])

# ══════════════════════════════════════════════════════════════════════════════
# HEADER
# ══════════════════════════════════════════════════════════════════════════════
ax_hdr = fig.add_subplot(gs[0])
ax_hdr.set_facecolor(BG3); ax_hdr.axis('off')
ax_hdr.plot([0,1],[0.97,0.97], color=GOLD, lw=3.5, transform=ax_hdr.transAxes, clip_on=False)
ax_hdr.text(0.5, 0.68, 'FARO PROTOCOL  ·  VÉLEZ SARSFIELD',
            color=GOLD, fontsize=20, fontweight='bold', ha='center', va='center',
            transform=ax_hdr.transAxes, fontfamily='monospace')
ax_hdr.text(0.5, 0.26, f'Complejo Acuático + Natatorio Olímpico  ·  {WEEK}',
            color=WHITE, fontsize=10.5, ha='center', transform=ax_hdr.transAxes)
ax_hdr.text(0.01, 0.26, 'Lat -34.6369  ·  Lon -58.5224  ·  WGS-84',
            color=WDIM, fontsize=8, transform=ax_hdr.transAxes, fontfamily='monospace')
ax_hdr.text(0.99, 0.26, 'Sentinel-2 NIR · Landsat TIRS · InSAR · Alerta Calidad Agua',
            color=WDIM, fontsize=8, ha='right', transform=ax_hdr.transAxes, fontfamily='monospace')

# ══════════════════════════════════════════════════════════════════════════════
# 6 KPIs ACUÁTICOS (gauges semicirculares)
# ══════════════════════════════════════════════════════════════════════════════
ax_kpi = fig.add_subplot(gs[1])
ax_kpi.set_facecolor(BG3); ax_kpi.axis('off')
ax_kpi.set_xlim(0, 12); ax_kpi.set_ylim(0, 3)

ax_kpi.text(6, 2.92, 'PANEL EJECUTIVO — 6 KPIs ACUÁTICOS · Sistema de Alerta Temprana',
           color=GOLD, fontsize=9.5, fontweight='bold',
           ha='center', va='top', fontfamily='monospace')

xs_kpi = np.linspace(1.0, 11.0, 6)
for i, kpi in enumerate(KPIS_ACUATICOS):
    cx = xs_kpi[i]
    col = SEM_COL[kpi['sem']]
    max_v = kpi['umbral_alert'] * 1.3 if kpi['valor'] < kpi['umbral_alert'] else kpi['valor'] * 1.1
    if kpi['label'] == 'SCORE GLOBAL':
        pct = kpi['valor'] / 100
    else:
        pct = min(kpi['valor'] / max_v, 1.0)

    # Gauge
    theta_bg = np.linspace(np.pi, 0, 100)
    ax_kpi.plot(cx + 0.7*np.cos(theta_bg), 1.6 + 0.42*np.sin(theta_bg),
               color=BG2, linewidth=10, solid_capstyle='round', zorder=2)
    theta_v = np.linspace(np.pi, np.pi - pct*np.pi, 100)
    ax_kpi.plot(cx + 0.7*np.cos(theta_v), 1.6 + 0.42*np.sin(theta_v),
               color=col, linewidth=10, solid_capstyle='round', zorder=3)

    ax_kpi.text(cx, 2.18, kpi['label'], color=GOLD, fontsize=7, fontweight='bold',
               ha='center', va='bottom', fontfamily='monospace')
    ax_kpi.text(cx, 1.58, f"{kpi['valor']}", color=col, fontsize=16, fontweight='bold',
               ha='center', va='center')
    ax_kpi.text(cx, 1.32, kpi['unidad'], color=WDIM, fontsize=8,
               ha='center', va='top', fontfamily='monospace')
    ax_kpi.text(cx, 1.12, kpi['sublabel'], color=WDIM+'99', fontsize=6,
               ha='center', va='top', fontfamily='monospace')
    ax_kpi.text(cx, 0.82, kpi['estado'], color=col, fontsize=8, fontweight='bold',
               ha='center', va='top', fontfamily='monospace')
    ax_kpi.text(cx, 0.58, kpi['nota'], color=WDIM, fontsize=6.5,
               ha='center', va='top', fontfamily='monospace')

    # Glow
    ax_kpi.add_patch(Circle((cx, 1.58), 0.32, facecolor=col, alpha=0.10, zorder=1))

# ══════════════════════════════════════════════════════════════════════════════
# MAPA COMPLEJO ACUÁTICO
# ══════════════════════════════════════════════════════════════════════════════
ax_map = fig.add_subplot(gs[2])
panel_ax(ax_map, '  MAPA COMPLEJO ACUÁTICO — Sentinel-2 NIR · Calidad y Temperatura del Agua')
ax_map.set_facecolor('#040c14')
ax_map.set_xlim(0, 10); ax_map.set_ylim(0, 8)
ax_map.tick_params(left=False, bottom=False, labelleft=False, labelbottom=False)

# Color del agua según calidad
cmap_agua = LinearSegmentedColormap.from_list('agua',
    ['#b71c1c','#f57f17','#1565c0','#0288d1','#00acc1','#00bcd4','#4dd0e1'], N=256)

piletas = [
    # (x, y, w, h, label, turbidez, temp, sem)
    (0.4, 4.0, 4.2, 3.5, 'NATATORIO OLÍMPICO\n50m · Competencia', 2.8, 23.4, 'verde'),
    (5.0, 4.0, 4.5, 3.5, 'NATATORIO 2\n25m · Entrenamiento',      3.1, 22.8, 'verde'),
    (0.4, 1.0, 2.8, 2.5, 'PILETA EXTERIOR 1',                      4.2, 24.1, 'verde'),
    (3.5, 1.0, 2.8, 2.5, 'PILETA EXTERIOR 2',                      4.8, 25.3, 'verde'),
    (6.6, 1.0, 2.8, 2.5, 'PILETA INFANTIL',                        2.5, 24.8, 'verde'),
]

for (x, y, w, h, lbl, turb, temp, sem) in piletas:
    t = np.clip(1 - turb / 10, 0.2, 1.0)
    col = cmap_agua(t)
    ax_map.add_patch(mpatches.Rectangle((x, y), w, h, facecolor=col,
        edgecolor='#ffffff55', linewidth=1.2, zorder=2, linestyle='-'))
    lines = lbl.split('\n')
    ax_map.text(x+w/2, y+h/2+0.15, lines[0], color='#ffffffee', fontsize=7.5,
               ha='center', va='center', fontweight='bold', zorder=4)
    if len(lines) > 1:
        ax_map.text(x+w/2, y+h/2-0.22, lines[1], color='#ffffffaa', fontsize=6.5,
                   ha='center', va='center', zorder=4)
    ax_map.text(x+w/2, y+0.22, f'Turb: {turb} NTU  ·  {temp}°C',
               color='#ffffffcc', fontsize=6.5, ha='center', va='bottom', zorder=4,
               fontfamily='monospace')
    sem_c = SEM_COL[sem]
    ax_map.add_patch(Circle((x+0.22, y+h-0.22), 0.16, facecolor=sem_c, zorder=5))

# Edificio natatorio (contorno)
ax_map.add_patch(mpatches.Rectangle((0.2, 3.6), 9.6, 4.2,
    facecolor='none', edgecolor=GOLD+'55', linewidth=1.2, linestyle=':', zorder=7))
ax_map.text(5.0, 7.7, 'NATATORIO OLÍMPICO — TECHO CUBIERTO',
           color=GOLD+'88', fontsize=7, ha='center', fontfamily='monospace', zorder=8)

sm_agua = ScalarMappable(cmap=cmap_agua, norm=plt.Normalize(0, 10))
sm_agua.set_array([])
cax = ax_map.inset_axes([0.88, 0.05, 0.025, 0.88])
cb = plt.colorbar(sm_agua, cax=cax)
cb.set_ticks([0, 2, 5, 8, 10])
cb.set_ticklabels(['0\nCryst.', '2', '5\nNTU', '8', '10\nTurb.'], fontsize=6)
cb.ax.yaxis.set_tick_params(color=WDIM, labelcolor=WDIM)
cb.outline.set_edgecolor(BORDER)
cb.set_label('Turbidez', color=GOLD, fontsize=7, rotation=270, labelpad=8)

ax_map.text(9.7, 7.65, 'N', color=GOLD, fontsize=10, fontweight='bold', ha='center')
ax_map.annotate('', xy=(9.7, 7.58), xytext=(9.7, 7.15),
               arrowprops=dict(arrowstyle='->', color=GOLD, lw=1.5))

# ══════════════════════════════════════════════════════════════════════════════
# GRÁFICOS TENDENCIAS CALIDAD AGUA
# ══════════════════════════════════════════════════════════════════════════════
ax_trend = fig.add_subplot(gs[3])
panel_ax(ax_trend, '  TENDENCIAS — Calidad del Agua · Últimas 4 semanas')
ax_trend.set_xlim(0,1); ax_trend.set_ylim(0,1); ax_trend.axis('off')

semanas = ['Sem-3', 'Sem-2', 'Sem-1', 'Actual']
x_sems = np.linspace(0.05, 0.35, 4)

trends = [
    ('Turbidez (NTU)',        KPIS_ACUATICOS[0]['historico'], BLUEL,  0.01,  0.45,  0.36, 5.0),
    ('Clorofila (mg/m³)',     KPIS_ACUATICOS[1]['historico'], GRNL,   0.38,  0.45,  0.36, 0.5),
    ('Temp. Agua (°C)',       KPIS_ACUATICOS[2]['historico'], CYANL,  0.72,  0.45,  0.24, 28.0),
]

for (lbl, hist, col, ox, oy, ow, umbral) in trends:
    ax_s = ax_trend.inset_axes([ox, oy, ow, 0.48])
    ax_s.set_facecolor(BG3)
    for sp in ax_s.spines.values(): sp.set_color(BORDER+'88')
    ax_s.plot(range(4), hist, 'o-', color=col, linewidth=2, markersize=5, zorder=3)
    ax_s.axhline(umbral, color=YELL+'77', linewidth=1.0, linestyle='--')
    ax_s.set_xticks(range(4))
    ax_s.set_xticklabels(semanas, fontsize=6.5, color=WDIM, fontfamily='monospace')
    ax_s.tick_params(colors=WDIM, labelsize=6.5)
    ax_s.set_facecolor(BG3)
    for sp in ax_s.spines.values(): sp.set_edgecolor(BORDER)
    ax_s.yaxis.set_tick_params(labelcolor=WDIM)
    ax_trend.text(ox + ow/2, oy + 0.50, lbl, color=col, fontsize=8,
                 fontweight='bold', ha='center', transform=ax_trend.transAxes,
                 fontfamily='monospace')
    ax_trend.text(ox + ow/2, oy + 0.04, f'Actual: {hist[-1]}',
                 color=WHITE, fontsize=8, ha='center', transform=ax_trend.transAxes,
                 fontfamily='monospace')

# ══════════════════════════════════════════════════════════════════════════════
# InSAR ASENTAMIENTO PERIMETRAL
# ══════════════════════════════════════════════════════════════════════════════
ax_ins = fig.add_subplot(gs[4])
panel_ax(ax_ins, '  InSAR PERIMETRAL — Sentinel-1 · Asentamiento Diferencial Vasos Piletas (mm/sem)')
ax_ins.set_facecolor(BG2)

sectores_ins = ['Natatorio\nOlímpico N', 'Natatorio\nOlímpico S', 'Natatorio\n2 E',
                'Natatorio\n2 O', 'Pileta\nExt. 1', 'Pileta\nExt. 2', 'Piscina\nInfantil']
vals_ins = [0.85, 0.72, 0.68, 0.91, 0.55, 0.62, 0.48]
cols_ins = [GRNL if v < 1.0 else (YELL if v < 2.0 else REDL) for v in vals_ins]
x_pos = np.arange(len(sectores_ins))

bars = ax_ins.bar(x_pos, vals_ins, color=cols_ins, edgecolor=BG, linewidth=0.8,
                  zorder=3, width=0.6)
ax_ins.axhline(1.5, color=YELL+'aa', linewidth=1.2, linestyle='--', zorder=4)
ax_ins.text(6.6, 1.55, 'UMBRAL\n1.5mm', color=YELL, fontsize=7, ha='right', va='bottom',
           fontfamily='monospace')

for bar_, val, col in zip(bars, vals_ins, cols_ins):
    ax_ins.text(bar_.get_x()+bar_.get_width()/2, val+0.02,
               f'{val:.2f}', color=col, fontsize=9, fontweight='bold',
               ha='center', va='bottom', fontfamily='monospace')

ax_ins.set_xlim(-0.5, len(sectores_ins)-0.3)
ax_ins.set_ylim(0, 2.2)
ax_ins.set_xticks(x_pos)
ax_ins.set_xticklabels([s.replace('\n', ' ') for s in sectores_ins],
                       color=WHITE, fontsize=7, fontfamily='monospace')
ax_ins.set_ylabel('Desplazamiento (mm/sem)', color=WDIM, fontsize=8, fontfamily='monospace')
ax_ins.tick_params(colors=WDIM)
ax_ins.yaxis.label.set_color(WDIM)
ax_ins.set_facecolor(BG3)
for sp in ax_ins.spines.values(): sp.set_edgecolor(BORDER)

# ══════════════════════════════════════════════════════════════════════════════
# ALERTA GLOBAL CALIDAD AGUA
# ══════════════════════════════════════════════════════════════════════════════
ax_alert = fig.add_subplot(gs[5])
ax_alert.set_facecolor(BG3); ax_alert.axis('off')
ax_alert.set_xlim(0,1); ax_alert.set_ylim(0,1)

ax_alert.text(0.5, 0.95,
             'SISTEMA DE ALERTA TEMPRANA DE CALIDAD DE AGUA — Faro Protocol',
             color=GOLD, fontsize=10, fontweight='bold', ha='center', va='top',
             transform=ax_alert.transAxes, fontfamily='monospace')

ax_alert.add_patch(FancyBboxPatch((0.3, 0.32), 0.40, 0.52,
    boxstyle="round,pad=0.015", facecolor=ALERTA_COL+'20',
    edgecolor=ALERTA_COL, linewidth=2.5, transform=ax_alert.transAxes))

ax_alert.text(0.5, 0.74, 'CALIDAD AGUA',
             color=WDIM, fontsize=9.5, fontweight='bold', ha='center',
             transform=ax_alert.transAxes, fontfamily='monospace')
ax_alert.text(0.5, 0.52, ALERTA_GLOBAL,
             color=ALERTA_COL, fontsize=24, fontweight='bold', ha='center',
             transform=ax_alert.transAxes, fontfamily='monospace')
ax_alert.text(0.5, 0.37, KPIS_ACUATICOS[5]['nota'],
             color=WDIM, fontsize=9, ha='center',
             transform=ax_alert.transAxes, fontfamily='monospace')

# Indicadores compactos
estados = [('TURB.', KPIS_ACUATICOS[0]['estado'], SEM_COL[KPIS_ACUATICOS[0]['sem']]),
           ('CLOROFILA', KPIS_ACUATICOS[1]['estado'], SEM_COL[KPIS_ACUATICOS[1]['sem']]),
           ('TEMP AGUA', KPIS_ACUATICOS[2]['estado'], SEM_COL[KPIS_ACUATICOS[2]['sem']]),
           ('TECHO NAT.', KPIS_ACUATICOS[3]['estado'], SEM_COL[KPIS_ACUATICOS[3]['sem']]),
           ('INSAR', KPIS_ACUATICOS[4]['estado'], SEM_COL[KPIS_ACUATICOS[4]['sem']]),]
xs_est = np.linspace(0.05, 0.25, 5)
for j, (lbl, est, col) in enumerate(estados):
    xj = xs_est[j]
    ax_alert.add_patch(FancyBboxPatch((xj-0.022, 0.05), 0.044, 0.72,
        boxstyle="round,pad=0.005", facecolor=col+'18', edgecolor=col+'55',
        linewidth=0.7, transform=ax_alert.transAxes))
    ax_alert.text(xj, 0.70, lbl, color=WDIM, fontsize=6.5, ha='center',
                 transform=ax_alert.transAxes, fontfamily='monospace')
    ax_alert.text(xj, 0.42, est, color=col, fontsize=7.5, fontweight='bold',
                 ha='center', transform=ax_alert.transAxes, fontfamily='monospace')
    ax_alert.plot(xj, 0.22, 'o', ms=12, color=col, transform=ax_alert.transAxes, zorder=4)

# Leyenda derecha
leyenda = [
    (GRNL, 'ÓPTIMA   — Agua en condición excelente'),
    (YELL,  'ATENCIÓN  — Verificar parámetro en campo'),
    (REDL,  'ALERTA ROJA — Intervención recomendada'),
]
for k, (lc, ltxt) in enumerate(leyenda):
    yk = 0.78 - k*0.24
    ax_alert.plot(0.76, yk, 'o', ms=10, color=lc, transform=ax_alert.transAxes, zorder=4)
    ax_alert.text(0.78, yk, ltxt, color=WHITE, fontsize=8, va='center',
                 transform=ax_alert.transAxes, fontfamily='monospace')

# ══════════════════════════════════════════════════════════════════════════════
# NOTA SISTEMA
# ══════════════════════════════════════════════════════════════════════════════
ax_nota = fig.add_subplot(gs[6])
ax_nota.set_facecolor(BLUE+'18'); ax_nota.axis('off')
ax_nota.set_xlim(0,1); ax_nota.set_ylim(0,1)
ax_nota.plot([0,1],[0.97,0.97], color=BLUEL+'88', lw=1.5, transform=ax_nota.transAxes)
ax_nota.text(0.5, 0.75,
            'Sistema de alerta temprana de calidad de agua',
            color=BLUEL, fontsize=11, fontweight='bold', ha='center',
            transform=ax_nota.transAxes, fontfamily='monospace')
ax_nota.text(0.5, 0.42,
            'Sentinel-2 NIR Band 5/4 + Landsat TIRS + InSAR Sentinel-1  ·  '
            'Detección precoz de proliferación algal, turbidez y asentamientos',
            color=WDIM, fontsize=8.5, ha='center', transform=ax_nota.transAxes)
ax_nota.text(0.5, 0.18,
            'Actualización automática cada lunes 07:00h ART  ·  '
            'Sin necesidad de muestreo presencial  ·  Cobertura 100% del predio acuático',
            color=WDIM, fontsize=8, ha='center', transform=ax_nota.transAxes,
            fontfamily='monospace')

# ══════════════════════════════════════════════════════════════════════════════
# FOOTER
# ══════════════════════════════════════════════════════════════════════════════
ax_foot = fig.add_subplot(gs[7])
ax_foot.set_facecolor(BG3); ax_foot.axis('off')
ax_foot.plot([0,1],[0.97,0.97], color=GOLD, lw=2.5, transform=ax_foot.transAxes, clip_on=False)
ax_foot.text(0.01, 0.45, f'CERT SHA-256: {CERT}',
            color=WDIM, fontsize=6.5, fontfamily='monospace', transform=ax_foot.transAxes, va='center')
ax_foot.text(0.5, 0.45,
            'Faro Protocol · Fortín Inteligente · Complejo Acuático · Mayo 2026',
            color=WDIM, fontsize=7.5, ha='center', transform=ax_foot.transAxes, va='center')
ax_foot.text(0.99, 0.45,
            f'protocolfaro@gmail.com  ·  {now.strftime("%d/%m/%Y %H:%M")}',
            color=WDIM, fontsize=7.5, ha='right', fontfamily='monospace',
            transform=ax_foot.transAxes, va='center')

# ─── SAVE ────────────────────────────────────────────────────────────────────
plt.subplots_adjust(left=0.03, right=0.97, top=0.995, bottom=0.008, hspace=0.0)
_REPORT_DIR = pathlib.Path(__file__).parents[4] / 'reportes_velez'
_REPORT_DIR.mkdir(exist_ok=True)
out = str(_out_path or (_REPORT_DIR / 'faro_reporte_velez_piletas.png'))
plt.savefig(out, dpi=DPI, facecolor=BG, bbox_inches='tight', pad_inches=0.08)
plt.close()
print(f'Saved: {out}')
