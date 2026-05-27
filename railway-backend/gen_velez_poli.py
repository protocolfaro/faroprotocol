"""
gen_velez_poli.py
Faro Protocol — Polideportivo José Ramón Feijóo · Vélez Sarsfield
Genera: reportes_velez/faro_reporte_velez_poli.png
NDVI canchas + térmico techos + SAR + InSAR · Mayo 2026
"""
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.gridspec as gridspec
import matplotlib.patches as mpatches
from matplotlib.patches import FancyBboxPatch, Circle
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
BLUE  = '#4a9ede'
CYAN  = '#40c4d4'
BORDER= '#1e2a38'
DPI   = 200

SEM_COL = {'verde': GRNL, 'amarillo': YELL, 'rojo': REDL}

now  = datetime.now()
CERT = hashlib.sha256(f"FARO-VELEZ-POLI-{now.isoformat()}".encode()).hexdigest()[:28].upper()
WEEK = now.strftime('Semana del %d de %B de %Y')

# ─── DATOS SECTORES ───────────────────────────────────────────────────────────
CANCHAS = [
    {'name': 'Cancha\nTenis 1',   'tipo': 'Tenis',    'ndvi': 0.41, 'temp_techo': 26.8,
     'insar': 0.48, 'sem': 'amarillo', 'accion': 'Riego preventivo · Esta semana'},
    {'name': 'Cancha\nTenis 2',   'tipo': 'Tenis',    'ndvi': 0.38, 'temp_techo': 27.2,
     'insar': 0.52, 'sem': 'amarillo', 'accion': 'Riego + fertilizar · Esta semana'},
    {'name': 'Cancha\nHockey',    'tipo': 'Hockey',   'ndvi': 0.52, 'temp_techo': 24.5,
     'insar': 0.30, 'sem': 'verde',    'accion': 'Sin acción inmediata'},
    {'name': 'Cancha\nBásquet',   'tipo': 'Básquet',  'ndvi': 0.12, 'temp_techo': 31.4,
     'insar': 0.85, 'sem': 'rojo',     'accion': 'Inspección fisuras + drenaje urgente'},
    {'name': 'Playón\nNorte',     'tipo': 'Multiuso', 'ndvi': 0.09, 'temp_techo': 33.1,
     'insar': 0.72, 'sem': 'rojo',     'accion': 'Relevamiento estructural esta semana'},
    {'name': 'Playón\nSur',       'tipo': 'Multiuso', 'ndvi': 0.14, 'temp_techo': 29.8,
     'insar': 0.60, 'sem': 'amarillo', 'accion': 'Monitoreo InSAR continuo'},
]

TECHO_POLI = {'temp': 44.2, 'insar': 0.62, 'area': '2.800 m²', 'sem': 'amarillo'}

# ─── REAL DATA OVERRIDE ──────────────────────────────────────────────────────
import os as _os, json as _json
_vd_path = _os.environ.get("FARO_VD_PATH")
if _vd_path:
    try:
        with open(_vd_path, encoding="utf-8") as _f:
            _vd = _json.load(_f)
        _s = _vd.get("sectores", {}).get("poli", {})
        if _s:
            if "techo_temp"  in _s: TECHO_POLI['temp']  = _s["techo_temp"]
            if "techo_insar" in _s: TECHO_POLI['insar'] = _s["techo_insar"]
            if "techo_sem"   in _s: TECHO_POLI['sem']   = _s["techo_sem"]
            if "canchas" in _s:
                for _i, _cu in enumerate(_s["canchas"]):
                    if _i < len(CANCHAS):
                        CANCHAS[_i].update({_k: _v for _k, _v in _cu.items() if _k in CANCHAS[_i]})
    except Exception as _e:
        print(f"FARO_VD_PATH poli: {_e}")
_out_path = _os.environ.get("FARO_OUT_PATH")

# ─── HELPERS ─────────────────────────────────────────────────────────────────
def panel_ax(ax, title=''):
    ax.set_facecolor(BG2)
    for sp in ax.spines.values():
        sp.set_color(BORDER); sp.set_linewidth(0.7)
    if title:
        ax.set_title(title, color=GOLD, fontsize=9.5, fontweight='bold',
                     loc='left', pad=5, fontfamily='monospace')

# ─── FIGURA ───────────────────────────────────────────────────────────────────
fig = plt.figure(figsize=(13.5, 28), facecolor=BG)
gs = gridspec.GridSpec(7, 1, figure=fig, hspace=0.0,
    height_ratios=[1.1, 1.5, 4.5, 4.2, 3.5, 3.5, 0.7])

# ══════════════════════════════════════════════════════════════════════════════
# HEADER
# ══════════════════════════════════════════════════════════════════════════════
ax_hdr = fig.add_subplot(gs[0])
ax_hdr.set_facecolor(BG3); ax_hdr.axis('off')
ax_hdr.plot([0,1],[0.97,0.97], color=GOLD, lw=3.5, transform=ax_hdr.transAxes, clip_on=False)
ax_hdr.text(0.5, 0.68, 'FARO PROTOCOL  ·  VÉLEZ SARSFIELD',
            color=GOLD, fontsize=20, fontweight='bold', ha='center', va='center',
            transform=ax_hdr.transAxes, fontfamily='monospace')
ax_hdr.text(0.5, 0.26, f'Polideportivo José Ramón Feijóo  ·  {WEEK}',
            color=WHITE, fontsize=10.5, ha='center', transform=ax_hdr.transAxes)
ax_hdr.text(0.01, 0.26, 'Lat -34.6375  ·  Lon -58.5215  ·  WGS-84',
            color=WDIM, fontsize=8, transform=ax_hdr.transAxes, fontfamily='monospace')
ax_hdr.text(0.99, 0.26, 'Sentinel-2 NDVI · Landsat TIRS · SAR · InSAR',
            color=WDIM, fontsize=8, ha='right', transform=ax_hdr.transAxes, fontfamily='monospace')

# ══════════════════════════════════════════════════════════════════════════════
# KPI ROW
# ══════════════════════════════════════════════════════════════════════════════
ax_kpi = fig.add_subplot(gs[1])
ax_kpi.set_facecolor(BG3); ax_kpi.axis('off')
ax_kpi.set_xlim(0,1); ax_kpi.set_ylim(0,1)

crit  = sum(1 for c in CANCHAS if c['sem'] == 'rojo')
aten  = sum(1 for c in CANCHAS if c['sem'] == 'amarillo')
ok    = len(CANCHAS) - crit - aten
score = 100 - crit*20 - aten*7

kpis = [
    ('SCORE POLI',       f'{score}/100',           REDL if crit > 0 else (YELL if aten > 1 else GRNL)),
    ('CANCHAS CRÍTICAS', str(crit),                REDL if crit else GRNL),
    ('CANCHAS ATENCIÓN', str(aten),                YELL if aten else GRNL),
    ('CANCHAS ÓPTIMAS',  str(ok),                  GRNL),
    ('TEMP TECHO POLI',  f'{TECHO_POLI["temp"]:.1f}°C',
                                                   YELL if TECHO_POLI['temp'] > 40 else GRNL),
    ('InSAR MÁX',        f'{max(c["insar"] for c in CANCHAS):.2f}\nmm',
                                                   REDL if max(c['insar'] for c in CANCHAS) > 2 else (
                                                   YELL if max(c['insar'] for c in CANCHAS) > 1 else GRNL)),
]
for i, (lbl, val, col) in enumerate(kpis):
    xi = (i + 0.5) / len(kpis)
    box = FancyBboxPatch((xi - 0.075, 0.07), 0.150, 0.86,
        boxstyle="round,pad=0.008", facecolor=col+'18', edgecolor=col+'55',
        linewidth=0.9, transform=ax_kpi.transAxes)
    ax_kpi.add_patch(box)
    ax_kpi.text(xi, 0.78, lbl, color=WDIM, fontsize=7, ha='center',
                transform=ax_kpi.transAxes, fontfamily='monospace')
    ax_kpi.text(xi, 0.42, val, color=col, fontsize=16, fontweight='bold',
                ha='center', va='center', transform=ax_kpi.transAxes)

# ══════════════════════════════════════════════════════════════════════════════
# MAPA NDVI CANCHAS
# ══════════════════════════════════════════════════════════════════════════════
ax_map = fig.add_subplot(gs[2])
panel_ax(ax_map, '  MAPA NDVI CANCHAS — Sentinel-2 MSI · Cobertura Vegetal por Sector')
ax_map.set_facecolor('#060d08')
ax_map.set_xlim(0, 10); ax_map.set_ylim(0, 8)
ax_map.tick_params(left=False, bottom=False, labelleft=False, labelbottom=False)

cmap_ndvi = LinearSegmentedColormap.from_list('ndvi',
    ['#b71c1c','#ef6c00','#fdd835','#66bb6a','#1b5e20'], N=256)

# Layout canchas polideportivo
layout = [
    # (x, y, w, h, cancha_idx)
    (0.3, 4.5, 3.2, 3.0, 0),   # Tenis 1
    (3.8, 4.5, 3.2, 3.0, 1),   # Tenis 2
    (7.3, 4.5, 2.2, 3.0, 2),   # Hockey
    (0.3, 0.8, 2.8, 3.2, 3),   # Básquet (ext)
    (3.4, 0.8, 3.0, 3.2, 4),   # Playón Norte
    (6.7, 0.8, 2.8, 3.2, 5),   # Playón Sur
]

ax_map.add_patch(mpatches.Rectangle((0,0), 10, 8, facecolor='#0a1408', zorder=1))

for (x, y, w, h, idx) in layout:
    s = CANCHAS[idx]
    t = np.clip((s['ndvi'] - 0.05) / 0.6, 0, 1)
    col = cmap_ndvi(t)
    ax_map.add_patch(mpatches.Rectangle((x, y), w, h, facecolor=col,
        edgecolor='#ffffff33', linewidth=0.8, zorder=2))
    name_clean = s['name'].replace('\n', ' ')
    ax_map.text(x+w/2, y+h/2+0.22, name_clean,
               color='#ffffffdd', fontsize=7.5, ha='center', va='center',
               fontweight='bold', zorder=4)
    ax_map.text(x+w/2, y+h/2-0.28, f'NDVI {s["ndvi"]:.2f}',
               color='#ffffffaa', fontsize=7, ha='center', va='center', zorder=4)
    sem_c = SEM_COL[s['sem']]
    ax_map.add_patch(Circle((x+w-0.2, y+h-0.2), 0.16, facecolor=sem_c, zorder=5))
    if s['sem'] in ('rojo', 'amarillo'):
        lw = 2.2 if s['sem'] == 'rojo' else 1.5
        ls = '--' if s['sem'] == 'rojo' else ':'
        ax_map.add_patch(mpatches.Rectangle((x, y), w, h,
            facecolor='none', edgecolor=sem_c, linewidth=lw, linestyle=ls, zorder=6))

# Edificio polideportivo cubierto
ax_map.add_patch(mpatches.Rectangle((0.05, 0.05), 9.9, 7.85,
    facecolor='none', edgecolor=GOLD+'44', linewidth=1.0, linestyle=':', zorder=7))
ax_map.text(0.5, 7.65, 'POLIDEPORTIVO JOSÉ RAMÓN FEIJÓO', color=GOLD+'88',
           fontsize=7, fontfamily='monospace', zorder=8)

sm_ndvi = ScalarMappable(cmap=cmap_ndvi, norm=plt.Normalize(0.05, 0.65))
sm_ndvi.set_array([])
cax = ax_map.inset_axes([0.88, 0.05, 0.025, 0.88])
cb = plt.colorbar(sm_ndvi, cax=cax)
cb.set_ticks([0.05, 0.2, 0.4, 0.6])
cb.set_ticklabels(['0.05\nSeco', '0.2\nPobre', '0.4\nMedio', '0.6\nÓptimo'], fontsize=6)
cb.ax.yaxis.set_tick_params(color=WDIM, labelcolor=WDIM)
cb.outline.set_edgecolor(BORDER)
cb.set_label('NDVI', color=GOLD, fontsize=7, rotation=270, labelpad=8)

ax_map.text(9.7, 7.65, 'N', color=GOLD, fontsize=10, fontweight='bold', ha='center')
ax_map.annotate('', xy=(9.7, 7.58), xytext=(9.7, 7.15),
               arrowprops=dict(arrowstyle='->', color=GOLD, lw=1.5))

# ══════════════════════════════════════════════════════════════════════════════
# TABLA SECTORES
# ══════════════════════════════════════════════════════════════════════════════
ax_tbl = fig.add_subplot(gs[3])
panel_ax(ax_tbl, '  ESTADO POR SECTOR — NDVI + Térmico + InSAR + Índice FARO')
ax_tbl.set_xlim(0,1); ax_tbl.set_ylim(0,1); ax_tbl.axis('off')

headers = ['SECTOR', 'TIPO', 'NDVI', 'TEMP (°C)', 'InSAR', 'FARO IDX', 'ACCIÓN SEMANAL']
col_xs  = [0.01, 0.16, 0.23, 0.31, 0.40, 0.49, 0.60]

y0 = 0.93
for h, cx in zip(headers, col_xs):
    ax_tbl.text(cx+0.005, y0, h, color=GOLD, fontsize=8, fontweight='bold',
                transform=ax_tbl.transAxes)
ax_tbl.plot([0.005, 0.995], [y0-0.025, y0-0.025],
            color=GOLD+'55', lw=0.8, transform=ax_tbl.transAxes)

rh = 0.132
for i, c in enumerate(CANCHAS):
    ry = y0 - 0.05 - i*rh
    col = SEM_COL[c['sem']]
    idx = round(max(0, 100 - (1-c['ndvi'])*80 - max(0, c['temp_techo']-28)*2.5 - c['insar']*30))
    bg = FancyBboxPatch((0.005, ry-rh+0.015), 0.990, rh-0.008,
        boxstyle="round,pad=0.003", facecolor=col+'14', edgecolor=col+'30',
        linewidth=0.5, transform=ax_tbl.transAxes)
    ax_tbl.add_patch(bg)
    vals = [c['name'].replace('\n',' '), c['tipo'],
            f"{c['ndvi']:.2f}", f"{c['temp_techo']:.1f}°C", f"{c['insar']:.2f}mm"]
    for j, (v, cx) in enumerate(zip(vals, col_xs[:5])):
        ax_tbl.text(cx+0.005, ry-rh/2+0.016, v,
                    color=WHITE+'cc', fontsize=7.5, transform=ax_tbl.transAxes, va='center')
    idx_col = GRNL if idx >= 70 else (YELL if idx >= 45 else REDL)
    ax_tbl.text(col_xs[5]+0.040, ry-rh/2+0.016, str(idx),
                color=idx_col, fontsize=11, fontweight='bold',
                ha='center', transform=ax_tbl.transAxes, va='center')
    ax_tbl.text(col_xs[6]+0.005, ry-rh/2+0.016, c['accion'],
                color=WHITE+'cc', fontsize=7.0, transform=ax_tbl.transAxes, va='center')

# ══════════════════════════════════════════════════════════════════════════════
# TÉRMICO TECHO POLIDEPORTIVO
# ══════════════════════════════════════════════════════════════════════════════
ax_therm = fig.add_subplot(gs[4])
panel_ax(ax_therm, '  TÉRMICO TECHO — Landsat TIRS · Temperatura Cubierta Polideportivo')
ax_therm.set_facecolor(BG2)
ax_therm.set_xlim(0, 1); ax_therm.set_ylim(0, 1); ax_therm.axis('off')

# Representación gráfica del techo con gradiente de temperatura
techo_data = np.random.seed(42) or np.array([
    [38.5, 40.2, 42.8, 44.2, 43.6, 41.9, 39.8],
    [36.4, 39.1, 41.5, 43.0, 42.1, 40.3, 37.6],
    [35.8, 38.4, 40.2, 41.8, 40.5, 38.7, 36.2],
    [34.2, 36.8, 38.5, 39.6, 38.4, 36.5, 34.8],
])
np.random.seed(42)
techo_data = np.random.uniform(34, 45, (4, 7))
techo_data[1][3] = 44.2  # hot spot principal

ax_img = ax_therm.inset_axes([0.05, 0.12, 0.60, 0.80])
cmap_t = LinearSegmentedColormap.from_list('techo',
    ['#0d47a1','#1976d2','#4fc3f7','#fff176','#ff6f00','#b71c1c'], N=256)
im = ax_img.imshow(techo_data, cmap=cmap_t, vmin=32, vmax=46, aspect='auto')
ax_img.axis('off')
ax_img.set_title('Distribución Temperatura Techo (°C)', color=WDIM, fontsize=8,
                  fontfamily='monospace')

cax_t = ax_therm.inset_axes([0.68, 0.12, 0.025, 0.80])
cbt = plt.colorbar(im, cax=cax_t)
cbt.set_ticks([32, 36, 40, 44])
cbt.set_ticklabels(['32°C', '36°C', '40°C', '44°C'], fontsize=7)
cbt.ax.yaxis.set_tick_params(color=WDIM, labelcolor=WDIM)
cbt.outline.set_edgecolor(BORDER)

# Stats del techo
ax_therm.text(0.75, 0.90, 'CUBIERTA\nPOLIDEPORTIVO', color=GOLD, fontsize=9,
             fontweight='bold', ha='center', transform=ax_therm.transAxes,
             fontfamily='monospace')
ax_therm.text(0.75, 0.70, f'{TECHO_POLI["temp"]}°C', color=YELL, fontsize=28,
             fontweight='bold', ha='center', transform=ax_therm.transAxes)
ax_therm.text(0.75, 0.54, 'Punto caliente\nmáximo', color=WDIM, fontsize=8,
             ha='center', transform=ax_therm.transAxes, fontfamily='monospace')
ax_therm.text(0.75, 0.40, f'Área: {TECHO_POLI["area"]}', color=WHITE,
             fontsize=9, ha='center', transform=ax_therm.transAxes)
ax_therm.text(0.75, 0.28, f'InSAR: {TECHO_POLI["insar"]:.2f} mm/sem', color=YELL,
             fontsize=9, ha='center', transform=ax_therm.transAxes, fontfamily='monospace')
ax_therm.text(0.75, 0.15, 'Estado: REVISAR AISLAMIENTO', color=YELL,
             fontsize=8, ha='center', transform=ax_therm.transAxes,
             fontfamily='monospace', fontweight='bold')

# ══════════════════════════════════════════════════════════════════════════════
# InSAR ASENTAMIENTO
# ══════════════════════════════════════════════════════════════════════════════
ax_ins = fig.add_subplot(gs[5])
panel_ax(ax_ins, '  MONITOREO InSAR — Sentinel-1 · Asentamiento Diferencial por Sector (mm/sem)')
ax_ins.set_facecolor(BG2)

names_ins = [c['name'].replace('\n',' ') for c in CANCHAS]
vals_ins  = [c['insar'] for c in CANCHAS]
cols_ins  = [SEM_COL[c['sem']] for c in CANCHAS]
x_pos = np.arange(len(names_ins))

bars = ax_ins.bar(x_pos, vals_ins, color=cols_ins, edgecolor=BG, linewidth=0.8,
                  zorder=3, width=0.55)
ax_ins.axhline(1.5, color=YELL+'aa', linewidth=1.2, linestyle='--', zorder=4)
ax_ins.text(5.6, 1.55, 'UMBRAL 1.5mm', color=YELL, fontsize=7.5,
           fontweight='bold', va='bottom', fontfamily='monospace')

for bar_, val, col in zip(bars, vals_ins, cols_ins):
    ax_ins.text(bar_.get_x()+bar_.get_width()/2, val+0.02,
               f'{val:.2f}mm', color=col, fontsize=9, fontweight='bold',
               ha='center', va='bottom', fontfamily='monospace')

ax_ins.set_xlim(-0.5, len(names_ins)-0.3)
ax_ins.set_ylim(0, 1.25)
ax_ins.set_xticks(x_pos)
ax_ins.set_xticklabels(names_ins, color=WHITE, fontsize=8, fontfamily='monospace')
ax_ins.set_ylabel('Desplazamiento (mm/sem)', color=WDIM, fontsize=8, fontfamily='monospace')
ax_ins.tick_params(colors=WDIM)
ax_ins.yaxis.label.set_color(WDIM)
ax_ins.set_facecolor(BG3)
for sp in ax_ins.spines.values(): sp.set_edgecolor(BORDER)

# ══════════════════════════════════════════════════════════════════════════════
# FOOTER
# ══════════════════════════════════════════════════════════════════════════════
ax_foot = fig.add_subplot(gs[6])
ax_foot.set_facecolor(BG3); ax_foot.axis('off')
ax_foot.plot([0,1],[0.97,0.97], color=GOLD, lw=2.5, transform=ax_foot.transAxes, clip_on=False)
ax_foot.text(0.01, 0.45, f'CERT SHA-256: {CERT}',
            color=WDIM, fontsize=6.5, fontfamily='monospace', transform=ax_foot.transAxes, va='center')
ax_foot.text(0.5, 0.45,
            'Faro Protocol · Fortín Inteligente · Polideportivo Feijóo · Mayo 2026',
            color=WDIM, fontsize=7.5, ha='center', transform=ax_foot.transAxes, va='center')
ax_foot.text(0.99, 0.45,
            f'protocolfaro@gmail.com  ·  {now.strftime("%d/%m/%Y %H:%M")}',
            color=WDIM, fontsize=7.5, ha='right', fontfamily='monospace',
            transform=ax_foot.transAxes, va='center')

# ─── SAVE ────────────────────────────────────────────────────────────────────
plt.subplots_adjust(left=0.03, right=0.97, top=0.995, bottom=0.008, hspace=0.0)
_REPORT_DIR = pathlib.Path(__file__).parent.parent / 'reportes_velez'
_REPORT_DIR.mkdir(exist_ok=True)
out = str(_out_path or (_REPORT_DIR / 'faro_reporte_velez_poli.png'))
plt.savefig(out, dpi=DPI, facecolor=BG, bbox_inches='tight', pad_inches=0.08)
plt.close()
print(f'Saved: {out}')
