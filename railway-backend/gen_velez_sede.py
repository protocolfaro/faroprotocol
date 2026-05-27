"""
gen_velez_sede.py
Faro Protocol — Sede Central & Instituto Vélez Sarsfield
Genera: reportes_velez/faro_reporte_velez_sede.png
Landsat térmico + SAR estructural + NDVI · Mayo 2026
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
CERT = hashlib.sha256(f"FARO-VELEZ-SEDE-{now.isoformat()}".encode()).hexdigest()[:28].upper()
WEEK = now.strftime('Semana del %d de %B de %Y')

# ─── DATOS ESTRUCTURALES ─────────────────────────────────────────────────────
SECTORES = [
    {'name': 'Edificio\nSede',      'temp_techo': 32.5, 'insar': 0.35, 'ndvi': 0.28, 'sem': 'verde',
     'accion': 'Sin acción inmediata'},
    {'name': 'Edificio\nInstituto', 'temp_techo': 34.8, 'insar': 0.48, 'ndvi': 0.21, 'sem': 'verde',
     'accion': 'Monitoreo preventivo'},
    {'name': 'Anexo\nNorte',        'temp_techo': 41.2, 'insar': 0.22, 'ndvi': 0.15, 'sem': 'amarillo',
     'accion': 'Revisar aislamiento térmico'},
    {'name': 'Patio\nCentral',      'temp_techo': 28.1, 'insar': 0.18, 'ndvi': 0.45, 'sem': 'verde',
     'accion': 'Sin acción inmediata'},
    {'name': 'Áreas\nVerdes',       'temp_techo': 26.4, 'insar': 0.55, 'ndvi': 0.38, 'sem': 'amarillo',
     'accion': 'Riego preventivo esta semana'},
]

# ─── REAL DATA OVERRIDE ──────────────────────────────────────────────────────
import os as _os, json as _json
_vd_path = _os.environ.get("FARO_VD_PATH")
if _vd_path:
    try:
        with open(_vd_path, encoding="utf-8") as _f:
            _vd = _json.load(_f)
        _s = _vd.get("sectores", {}).get("sede", {})
        if _s:
            if "sectores" in _s:
                for _i, _su in enumerate(_s["sectores"]):
                    if _i < len(SECTORES):
                        SECTORES[_i].update({_k: _v for _k, _v in _su.items() if _k in SECTORES[_i]})
    except Exception as _e:
        print(f"FARO_VD_PATH sede: {_e}")
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
fig = plt.figure(figsize=(14, 28), facecolor=BG)
gs = gridspec.GridSpec(7, 1, figure=fig, hspace=0.0,
    height_ratios=[1.1, 1.5, 4.2, 3.8, 3.8, 3.5, 0.7])

# ══════════════════════════════════════════════════════════════════════════════
# HEADER
# ══════════════════════════════════════════════════════════════════════════════
ax_hdr = fig.add_subplot(gs[0])
ax_hdr.set_facecolor(BG3); ax_hdr.axis('off')
ax_hdr.plot([0,1],[0.97,0.97], color=GOLD, lw=3.5, transform=ax_hdr.transAxes, clip_on=False)
ax_hdr.text(0.5, 0.68, 'FARO PROTOCOL  ·  VÉLEZ SARSFIELD',
            color=GOLD, fontsize=20, fontweight='bold', ha='center', va='center',
            transform=ax_hdr.transAxes, fontfamily='monospace')
ax_hdr.text(0.5, 0.26, f'Sede Central & Instituto Vélez Sarsfield  ·  {WEEK}',
            color=WHITE, fontsize=10.5, ha='center', transform=ax_hdr.transAxes)
ax_hdr.text(0.01, 0.26, 'Av. Juan B. Justo 9200  ·  Lat -34.6358  ·  Lon -58.5235',
            color=WDIM, fontsize=8, transform=ax_hdr.transAxes, fontfamily='monospace')
ax_hdr.text(0.99, 0.26, 'Landsat TIRS · Sentinel-1 SAR · Sentinel-2 NDVI',
            color=WDIM, fontsize=8, ha='right', transform=ax_hdr.transAxes, fontfamily='monospace')

# ══════════════════════════════════════════════════════════════════════════════
# KPI ROW
# ══════════════════════════════════════════════════════════════════════════════
ax_kpi = fig.add_subplot(gs[1])
ax_kpi.set_facecolor(BG3); ax_kpi.axis('off')
ax_kpi.set_xlim(0,1); ax_kpi.set_ylim(0,1)

crit  = sum(1 for s in SECTORES if s['sem'] == 'rojo')
aten  = sum(1 for s in SECTORES if s['sem'] == 'amarillo')
score = 100 - crit*25 - aten*8
temp_max = max(s['temp_techo'] for s in SECTORES)
insar_max = max(s['insar']     for s in SECTORES)
ndvi_avg  = np.mean([s['ndvi'] for s in SECTORES])

score_col = REDL if crit > 0 else (YELL if aten > 1 else GRNL)
temp_col  = REDL if temp_max > 45 else (YELL if temp_max > 38 else GRNL)
ins_col   = REDL if insar_max > 2 else (YELL if insar_max > 1 else GRNL)

kpis = [
    ('SCORE SEDE',        f'{score}/100',           score_col),
    ('TEMP. MÁX TECHO',  f'{temp_max:.1f}°C',      temp_col),
    ('InSAR MÁX',        f'{insar_max:.2f}\nmm',    ins_col),
    ('NDVI PROM.',        f'{ndvi_avg:.2f}',         YELL if ndvi_avg < 0.4 else GRNL),
    ('ALERTAS',          f'{crit+aten}',             YELL if crit+aten > 0 else GRNL),
    ('SECTORES OK',      f'{5-crit-aten}/5',         GRNL),
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
# MAPA TÉRMICO (Landsat TIRS)
# ══════════════════════════════════════════════════════════════════════════════
ax_map = fig.add_subplot(gs[2])
panel_ax(ax_map, '  MAPA TÉRMICO — Landsat TIRS · Temperatura Superficial por Sector')
ax_map.set_facecolor('#060a10')
ax_map.set_xlim(0, 10); ax_map.set_ylim(0, 8)
ax_map.tick_params(left=False, bottom=False, labelleft=False, labelbottom=False)

cmap_heat = LinearSegmentedColormap.from_list('heat',
    ['#0d2480','#1565c0','#00897b','#fdd835','#e65100','#b71c1c'], N=256)

# Layout de edificios
edificios = [
    # (x, y, w, h, sector_idx, label)
    (0.5, 4.8, 3.8, 2.8, 0, 'EDIFICIO SEDE'),
    (4.8, 4.8, 4.5, 2.8, 1, 'EDIFICIO INSTITUTO'),
    (0.5, 1.5, 2.5, 2.8, 2, 'ANEXO NORTE'),
    (3.2, 1.5, 2.4, 2.8, 3, 'PATIO CENTRAL'),
    (5.9, 1.5, 3.8, 2.8, 4, 'ÁREAS VERDES'),
]

norm_heat = plt.Normalize(25, 45)
for (x, y, w, h, idx, lbl) in edificios:
    s = SECTORES[idx]
    t = norm_heat(s['temp_techo'])
    col = cmap_heat(t)
    ax_map.add_patch(mpatches.Rectangle((x, y), w, h, facecolor=col,
        edgecolor='#ffffff33', linewidth=0.8, zorder=2))
    ax_map.text(x+w/2, y+h/2+0.22, lbl,
               color='#ffffffdd', fontsize=7, ha='center', va='center',
               fontweight='bold', zorder=4)
    ax_map.text(x+w/2, y+h/2-0.28, f'{s["temp_techo"]:.1f}°C',
               color='#ffffffcc', fontsize=9, fontweight='bold',
               ha='center', va='center', zorder=4)
    sem_c = SEM_COL[s['sem']]
    ax_map.add_patch(Circle((x+w-0.2, y+h-0.2), 0.15,
        facecolor=sem_c, zorder=5))
    if s['sem'] == 'amarillo':
        ax_map.add_patch(mpatches.Rectangle((x, y), w, h,
            facecolor='none', edgecolor=YELL, linewidth=1.8, linestyle='--', zorder=6))

# Colorbar
sm = ScalarMappable(cmap=cmap_heat, norm=norm_heat)
sm.set_array([])
cax = ax_map.inset_axes([0.88, 0.05, 0.025, 0.88])
cb = plt.colorbar(sm, cax=cax)
cb.set_ticks([25, 30, 35, 40, 45])
cb.set_ticklabels(['25°C\nFrío', '30°C', '35°C', '40°C', '45°C\nCaliente'], fontsize=6)
cb.ax.yaxis.set_tick_params(color=WDIM, labelcolor=WDIM)
cb.outline.set_edgecolor(BORDER)
cb.set_label('Temp. (°C)', color=GOLD, fontsize=7, rotation=270, labelpad=8)

ax_map.text(9.7, 7.65, 'N', color=GOLD, fontsize=10, fontweight='bold', ha='center')
ax_map.annotate('', xy=(9.7, 7.58), xytext=(9.7, 7.15),
               arrowprops=dict(arrowstyle='->', color=GOLD, lw=1.5))
ax_map.text(1.5, 0.2, 'Av. Juan B. Justo 9200', color='#ffffff33', fontsize=7)

# ══════════════════════════════════════════════════════════════════════════════
# SAR ESTRUCTURAL (Sentinel-1 InSAR)
# ══════════════════════════════════════════════════════════════════════════════
ax_sar = fig.add_subplot(gs[3])
panel_ax(ax_sar, '  MONITOREO ESTRUCTURAL — InSAR Sentinel-1 · Asentamiento mm/semana')
ax_sar.set_facecolor(BG2)
ax_sar.set_xlim(0, 10); ax_sar.set_ylim(0, 1.2)
ax_sar.tick_params(left=False, bottom=False, labelleft=False, labelbottom=False)
for sp in ax_sar.spines.values(): sp.set_color(BORDER)

bar_x = np.linspace(1.0, 9.0, len(SECTORES))
bw = 1.2
for i, (bx, s) in enumerate(zip(bar_x, SECTORES)):
    col = SEM_COL[s['sem']]
    bar_h = min(s['insar'] / 1.5, 1.0)
    ax_sar.add_patch(FancyBboxPatch((bx-bw/2, 0.04), bw, bar_h,
        boxstyle="round,pad=0.01", facecolor=col+'55', edgecolor=col, linewidth=1.5))

ax_sar.axhline(1.0/1.5*1.2, color=YELL+'aa', linewidth=1.2, linestyle='--', zorder=5)
ax_sar.text(9.5, 1.0/1.5*1.2+0.02, 'UMBRAL\n1.0mm', color=YELL, fontsize=7, ha='right')
ax_sar.set_xlim(0, 10); ax_sar.set_ylim(0, 1.35)

# Texto asentamiento bajo las barras
for i, (bx, s) in enumerate(zip(bar_x, SECTORES)):
    bar_h = min(s['insar'] / 1.5, 1.0)
    ax_sar.text(bx, bar_h + 0.09, f'{s["insar"]:.2f} mm',
               color=SEM_COL[s['sem']], fontsize=10, fontweight='bold', ha='center')
    name_clean = s['name'].replace('\n', ' ')
    ax_sar.text(bx, 0.02, name_clean, color=WDIM, fontsize=7.5, ha='center', va='bottom')

# ══════════════════════════════════════════════════════════════════════════════
# NDVI + TABLA SECTORES
# ══════════════════════════════════════════════════════════════════════════════
ax_tbl = fig.add_subplot(gs[4])
panel_ax(ax_tbl, '  ANÁLISIS MULTI-ESPECTRAL — Estado por Sector · Landsat + Sentinel-2')
ax_tbl.set_xlim(0,1); ax_tbl.set_ylim(0,1); ax_tbl.axis('off')

headers = ['SECTOR', 'TEMP TECHO', 'InSAR', 'NDVI', 'SEMÁFORO', 'ACCIÓN RECOMENDADA']
col_xs  = [0.01, 0.18, 0.28, 0.36, 0.46, 0.60]

y0 = 0.92
for h, cx in zip(headers, col_xs):
    ax_tbl.text(cx+0.005, y0, h, color=GOLD, fontsize=8, fontweight='bold',
                transform=ax_tbl.transAxes)
ax_tbl.plot([0.005, 0.995], [y0-0.03, y0-0.03],
            color=GOLD+'55', lw=0.8, transform=ax_tbl.transAxes)

rh = 0.152
for i, s in enumerate(SECTORES):
    ry = y0 - 0.06 - i*rh
    col = SEM_COL[s['sem']]
    bg = FancyBboxPatch((0.005, ry-rh+0.018), 0.990, rh-0.010,
        boxstyle="round,pad=0.003", facecolor=col+'14', edgecolor=col+'30',
        linewidth=0.5, transform=ax_tbl.transAxes)
    ax_tbl.add_patch(bg)
    vals = [s['name'].replace('\n',' '), f"{s['temp_techo']:.1f}°C",
            f"{s['insar']:.2f}mm", f"{s['ndvi']:.2f}"]
    for j, (v, cx) in enumerate(zip(vals, col_xs[:4])):
        ax_tbl.text(cx+0.005, ry-rh/2+0.018, v,
                    color=WHITE+'cc', fontsize=8, transform=ax_tbl.transAxes, va='center')
    badge = FancyBboxPatch((col_xs[4], ry-rh+0.022), 0.10, rh-0.022,
        boxstyle="round,pad=0.004", facecolor=col+'44', edgecolor=col,
        linewidth=0.7, transform=ax_tbl.transAxes)
    ax_tbl.add_patch(badge)
    ax_tbl.text(col_xs[4]+0.050, ry-rh/2+0.018, s['sem'].upper(),
                color=col, fontsize=7.5, fontweight='bold',
                ha='center', transform=ax_tbl.transAxes, va='center')
    ax_tbl.text(col_xs[5]+0.005, ry-rh/2+0.018, s['accion'],
                color=WHITE+'cc', fontsize=7.5, transform=ax_tbl.transAxes, va='center')

# ══════════════════════════════════════════════════════════════════════════════
# ÍNDICE FARO COMPUESTO
# ══════════════════════════════════════════════════════════════════════════════
ax_idx = fig.add_subplot(gs[5])
panel_ax(ax_idx, '  ÍNDICE FARO — Diagnóstico Integrado por Sector')
ax_idx.set_xlim(0,1); ax_idx.set_ylim(0,1); ax_idx.axis('off')

indices = []
for s in SECTORES:
    temp_score = max(0, 100 - max(0, s['temp_techo']-30)*4)
    insar_score = max(0, 100 - s['insar']*60)
    ndvi_score  = min(100, s['ndvi']*200)
    idx = round((temp_score*0.4 + insar_score*0.35 + ndvi_score*0.25))
    indices.append(idx)

xs_idx = np.linspace(0.10, 0.90, len(SECTORES))
for i, (s, idx) in enumerate(zip(SECTORES, indices)):
    cx = xs_idx[i]
    col = GRNL if idx >= 75 else (YELL if idx >= 50 else REDL)
    ax_idx.text(cx, 0.92, s['name'].replace('\n','\n'),
               color=WHITE, fontsize=8, fontweight='bold',
               ha='center', va='top', transform=ax_idx.transAxes)
    ax_idx.text(cx, 0.62, str(idx),
               color=col, fontsize=26, fontweight='bold',
               ha='center', va='center', transform=ax_idx.transAxes,
               fontfamily='monospace')
    ax_idx.plot(cx, 0.30, 'o', ms=14, color=col, transform=ax_idx.transAxes, zorder=4)
    ax_idx.plot(cx, 0.30, 'o', ms=22, color=col, alpha=0.15, transform=ax_idx.transAxes, zorder=3)
    ax_idx.text(cx, 0.12, '/100', color=WDIM, fontsize=9,
               ha='center', transform=ax_idx.transAxes, fontfamily='monospace')

ax_idx.text(0.5, 0.02, 'ÍNDICE FARO = 40% Térmico + 35% Estructural (InSAR) + 25% Vegetación (NDVI)',
           color=WDIM, fontsize=8, ha='center', transform=ax_idx.transAxes,
           fontfamily='monospace')

# ══════════════════════════════════════════════════════════════════════════════
# FOOTER
# ══════════════════════════════════════════════════════════════════════════════
ax_foot = fig.add_subplot(gs[6])
ax_foot.set_facecolor(BG3); ax_foot.axis('off')
ax_foot.plot([0,1],[0.97,0.97], color=GOLD, lw=2.5, transform=ax_foot.transAxes, clip_on=False)
ax_foot.text(0.01, 0.45, f'CERT SHA-256: {CERT}',
            color=WDIM, fontsize=6.5, fontfamily='monospace', transform=ax_foot.transAxes, va='center')
ax_foot.text(0.5, 0.45,
            'Faro Protocol · Fortín Inteligente · Sede Central & Instituto · Mayo 2026',
            color=WDIM, fontsize=7.5, ha='center', transform=ax_foot.transAxes, va='center')
ax_foot.text(0.99, 0.45,
            f'protocolfaro@gmail.com  ·  {now.strftime("%d/%m/%Y %H:%M")}',
            color=WDIM, fontsize=7.5, ha='right', fontfamily='monospace',
            transform=ax_foot.transAxes, va='center')

# ─── SAVE ────────────────────────────────────────────────────────────────────
plt.subplots_adjust(left=0.03, right=0.97, top=0.995, bottom=0.008, hspace=0.0)
_REPORT_DIR = pathlib.Path(__file__).parent.parent / 'reportes_velez'
_REPORT_DIR.mkdir(exist_ok=True)
out = str(_out_path or (_REPORT_DIR / 'faro_reporte_velez_sede.png'))
plt.savefig(out, dpi=DPI, facecolor=BG, bbox_inches='tight', pad_inches=0.1)
plt.close()
print(f'Saved: {out}')
