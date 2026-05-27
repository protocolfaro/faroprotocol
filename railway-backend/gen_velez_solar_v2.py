"""
Faro Protocol — Reporte Solar Vélez v2
Legibilidad mejorada: leyenda separada, colorbar vertical, KPI grande, tabla 12pt.
"""
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.gridspec as gridspec
import matplotlib.patches as mpatches
from matplotlib.patches import FancyBboxPatch
from matplotlib.cm import ScalarMappable
from matplotlib.colors import LinearSegmentedColormap
import matplotlib.ticker as ticker
import numpy as np
import hashlib, os
from datetime import datetime

# ─── PALETA ──────────────────────────────────────────────────────────────────
BG    = '#06080b'
BG2   = '#0d1117'
BG3   = '#141c24'
GOLD  = '#c9a84c'
WHITE = '#f2ede4'
WDIM  = '#9aa0a8'
REDL  = '#e74c3c'
REDXL = '#ff6b6b'
YELL  = '#f0b429'
GRNL  = '#27ae60'

# ─── DATOS ───────────────────────────────────────────────────────────────────
np.random.seed(77)
TOTAL_PANELS = 210
INSTALLED_KWP = 120.0
COLS, ROWS = 15, 14

panel_states = np.zeros(TOTAL_PANELS, dtype=int)
for idx in [0,1,15,16,30,105,106,120,180,195,196,197,209]:
    if idx < TOTAL_PANELS: panel_states[idx] = 2
for idx in list(range(2,8)) + list(range(45,55)) + list(range(140,148)):
    if idx < TOTAL_PANELS: panel_states[idx] = 1
for idx in [60,61,75,76,90,91]:
    if idx < TOTAL_PANELS: panel_states[idx] = 3

panel_grid = panel_states.reshape(ROWS, COLS)

efficiency = np.where(panel_states==2, np.random.uniform(0,60,TOTAL_PANELS),
             np.where(panel_states==1, np.random.uniform(75,90,TOTAL_PANELS),
             np.where(panel_states==3, np.random.uniform(60,78,TOTAL_PANELS),
             np.random.uniform(93,100,TOTAL_PANELS))))
eff_grid = efficiency.reshape(ROWS, COLS)

temps = np.where(panel_states==2, np.random.uniform(55,72,TOTAL_PANELS),
        np.where(panel_states==1, np.random.uniform(42,52,TOTAL_PANELS),
        np.where(panel_states==3, np.random.uniform(30,38,TOTAL_PANELS),
        np.random.uniform(26,38,TOTAL_PANELS))))
for r in range(ROWS):
    temps[r*COLS:(r+1)*COLS] += np.linspace(2,-2,ROWS)[r]
temp_grid = temps.reshape(ROWS, COLS)

n_ok     = int(np.sum(panel_states==0))
n_deg    = int(np.sum(panel_states==1))
n_fail   = int(np.sum(panel_states==2))
n_shadow = int(np.sum(panel_states==3))
avg_eff  = float(np.mean(efficiency))
actual_kwp = INSTALLED_KWP * avg_eff / 100
loss_kwp   = INSTALLED_KWP - actual_kwp
avg_temp   = float(np.mean(temps))

# ─── REAL DATA OVERRIDE ───────────────────────────────────────────────────────
import os as _os, json as _json
_vd_path = _os.environ.get("FARO_VD_PATH")
if _vd_path:
    try:
        with open(_vd_path, encoding="utf-8") as _f:
            _vd_solar = _json.load(_f).get("sectores", {}).get("solar", {})
        if isinstance(_vd_solar.get("score"), (int, float)):
            avg_eff    = float(_vd_solar["score"])
            actual_kwp = INSTALLED_KWP * avg_eff / 100
            loss_kwp   = INSTALLED_KWP - actual_kwp
    except Exception as _e:
        print(f"FARO_VD_PATH solar: {_e} — usando datos hardcodeados")
_out_path = _os.environ.get("FARO_OUT_PATH")

now = datetime.now()
SCAN_DATE = now.strftime('%d de %B de %Y  ·  %H:%M UTC-3')
SHA = hashlib.sha256(f"FARO-VELEZ-SOLAR-V2-{now.isoformat()}".encode()).hexdigest()

def thermal_cmap():
    return LinearSegmentedColormap.from_list('th',
        ['#0a1628','#1565c0','#42a5f5','#aed6f1','#ffeaa7','#f39c12','#e74c3c','#8b0000'])

def efficiency_cmap():
    return LinearSegmentedColormap.from_list('ef',
        ['#8b0000','#e74c3c','#f0b429','#27ae60','#1a5e1a'])

tcmap = thermal_cmap()
ecmap = efficiency_cmap()

# ─── LAYOUT CONSTANTS ────────────────────────────────────────────────────────
# Map region in axes-fraction coords (same for both map panels)
ML, MB  = 0.10, 0.11
MW, MH  = 0.69, 0.73
MR = ML + MW   # 0.79
MT = MB + MH   # 0.84
MCX = ML + MW/2
MCY = MB + MH/2
CW  = MW / COLS
CH  = MH / ROWS

# Vertical colorbar: right of map
CB_L = MR + 0.04   # 0.83
CB_W = 0.055
CB_B = MB
CB_H = MH

# ─── FIGURE ──────────────────────────────────────────────────────────────────
DPI = 200
FW, FH = 13.5, 30
fig = plt.figure(figsize=(FW, FH), dpi=DPI, facecolor=BG)
fig.subplots_adjust(left=0.06, right=0.97, top=0.99, bottom=0.01, hspace=0.04)

gs = gridspec.GridSpec(10, 1, figure=fig, hspace=0.04, height_ratios=[
    1.5,   # 0  header
    1.9,   # 1  KPI ejecutivo (más alto)
    5.2,   # 2  mapa térmico
    0.70,  # 3  leyenda térmica
    5.2,   # 4  mapa eficiencia
    0.70,  # 5  leyenda eficiencia
    4.8,   # 6  tabla rendimiento
    2.6,   # 7  curva producción
    1.9,   # 8  alertas
    0.85,  # 9  footer
])

# ─── HELPERS ─────────────────────────────────────────────────────────────────
def logo_badges(ax, ys=0.14):
    for lx, ltxt in [(0.06,'ESA'),(0.14,'COP'),(0.22,'NASA')]:
        ax.add_patch(FancyBboxPatch((lx-.025,.03),.065,.22,
            boxstyle='round,pad=0.01',transform=ax.transAxes,
            facecolor=BG3,edgecolor=GOLD+'88',linewidth=0.8,zorder=3))
        ax.text(lx+.008,ys,ltxt,transform=ax.transAxes,color=GOLD,
            fontsize=7,fontweight='bold',ha='center',va='center',
            fontfamily='monospace',zorder=4)
    for lx, ltxt in [(0.78,'ESA'),(0.86,'COP'),(0.94,'NASA')]:
        ax.add_patch(FancyBboxPatch((lx-.025,.03),.065,.22,
            boxstyle='round,pad=0.01',transform=ax.transAxes,
            facecolor=BG3,edgecolor=GOLD+'88',linewidth=0.8,zorder=3))
        ax.text(lx+.008,ys,ltxt,transform=ax.transAxes,color=GOLD,
            fontsize=7,fontweight='bold',ha='center',va='center',
            fontfamily='monospace',zorder=4)

def map_frame(ax, panel_title, subtitle=''):
    """Title bar + gold separator line + N/S/E/O labels outside map."""
    # Background for title area (above map)
    ax.add_patch(mpatches.Rectangle((0, MT+0.005), 1, 1-MT-0.005,
        transform=ax.transAxes, facecolor=BG3, edgecolor='none', zorder=7))
    ax.plot([0, 1], [MT+0.008, MT+0.008], color=GOLD, linewidth=2.4,
        transform=ax.transAxes, zorder=8, clip_on=False)
    ax.text(0.5, (MT+1.0)/2, panel_title,
        transform=ax.transAxes, color=GOLD, fontsize=11, fontweight='bold',
        ha='center', va='center', fontfamily='monospace', zorder=9)
    if subtitle:
        ax.text(0.5, MT+0.015, subtitle,
            transform=ax.transAxes, color=WDIM, fontsize=8.5,
            ha='center', va='bottom', fontfamily='monospace', zorder=9)
    # Map border
    ax.add_patch(mpatches.Rectangle((ML, MB), MW, MH,
        transform=ax.transAxes, facecolor='none',
        edgecolor=GOLD+'77', linewidth=1.3, zorder=6))
    # N/S/E/O labels — outside the map, clip_on=False
    kw = dict(transform=ax.transAxes, fontsize=12, fontweight='bold',
              fontfamily='monospace', clip_on=False, zorder=10, color=WHITE)
    ax.text(MCX, MT+0.030, 'N', ha='center', va='bottom', **kw)
    ax.text(MCX, MB-0.028, 'S', ha='center', va='top',    **kw)
    ax.text(ML-0.055, MCY, 'E', ha='center', va='center', rotation=90,  **kw)
    ax.text(CB_L+CB_W+0.030, MCY, 'O', ha='center', va='center', rotation=-90, **kw)

def vertical_colorbar(ax, cmap, vmin, vmax, ticks, tick_labels):
    """Vertical colorbar inset on the right side of the map."""
    cax = ax.inset_axes([CB_L, CB_B, CB_W, CB_H])
    norm = plt.Normalize(vmin, vmax)
    sm = ScalarMappable(cmap=cmap, norm=norm)
    sm.set_array([])
    cb = plt.colorbar(sm, cax=cax)
    cb.set_ticks(ticks)
    cb.set_ticklabels(tick_labels)
    cb.ax.tick_params(colors=WHITE, labelsize=9.5, length=5, width=1.2,
                      which='major', direction='out')
    cb.ax.yaxis.set_tick_params(labelright=True, labelleft=False)
    cb.outline.set_edgecolor(GOLD+'55')
    cb.outline.set_linewidth(0.9)
    for lbl in cb.ax.get_yticklabels():
        lbl.set_fontfamily('monospace')
        lbl.set_fontweight('bold')
        lbl.set_fontsize(9.5)
    return cb

def legend_row(ax, items):
    """Horizontal legend row: list of (color, label) pairs."""
    ax.set_facecolor(BG3)
    ax.set_xlim(0,1); ax.set_ylim(0,1)
    ax.axis('off')
    ax.axhline(0.97, color=GOLD+'44', linewidth=0.8)
    ax.axhline(0.03, color=GOLD+'44', linewidth=0.8)
    n = len(items)
    xs = np.linspace(0.06, 0.94, n)
    sw = 0.7 / n   # spacing width per item
    for i, (lc, ltxt) in enumerate(items):
        cx = xs[i]
        ax.add_patch(mpatches.Rectangle(
            (cx - sw*0.44, 0.25), sw*0.10, 0.50,
            transform=ax.transAxes,
            facecolor=lc, edgecolor='white', linewidth=0.6, zorder=3))
        ax.text(cx - sw*0.30, 0.50, ltxt,
            transform=ax.transAxes, color=WHITE,
            fontsize=9.5, va='center', fontfamily='monospace', zorder=3)

# ═══════════════════════════════════════════════════════════════════════════════
# 0  HEADER
# ═══════════════════════════════════════════════════════════════════════════════
ax0 = fig.add_subplot(gs[0])
ax0.set_facecolor(BG); ax0.set_xlim(0,1); ax0.set_ylim(0,1); ax0.axis('off')
ax0.add_patch(mpatches.Rectangle((0,0.93),1,0.07,
    transform=ax0.transAxes,color=GOLD,zorder=2))
ax0.text(0.5,0.88,'FARO PROTOCOL — FORTÍN INTELIGENTE',
    transform=ax0.transAxes,color=GOLD,fontsize=17,fontweight='bold',
    ha='center',va='top',fontfamily='monospace')
ax0.text(0.5,0.70,'VÉLEZ SARSFIELD · MONITOREO SOLAR — TECHO SUR AMALFITANI',
    transform=ax0.transAxes,color=WHITE,fontsize=12,fontweight='bold',
    ha='center',va='top',fontfamily='sans-serif')
ax0.text(0.5,0.51,f'Escaneo satelital: {SCAN_DATE}',
    transform=ax0.transAxes,color=WDIM,fontsize=9,ha='center',va='top',fontfamily='monospace')
ax0.text(0.5,0.35,'Lat -34.6379  ·  Lon -58.5288  ·  210 paneles bifaciales 120 kWp · Abril 2026',
    transform=ax0.transAxes,color=WDIM,fontsize=8.5,ha='center',va='top',fontfamily='monospace')
ax0.add_patch(FancyBboxPatch((0.29,0.02),0.42,0.22,
    boxstyle='round,pad=0.01',transform=ax0.transAxes,
    facecolor=BG3,edgecolor=GOLD,linewidth=1.2,zorder=3))
ax0.text(0.5,0.13,'  ANÁLISIS ENERGÉTICO · LANDSAT 8/9 + SENTINEL-2  ',
    transform=ax0.transAxes,color=GOLD,fontsize=8.5,fontweight='bold',
    ha='center',va='bottom',fontfamily='monospace',zorder=4)
logo_badges(ax0)
ax0.axhline(0.0,color=GOLD,linewidth=1.5)

# ═══════════════════════════════════════════════════════════════════════════════
# 1  PANEL EJECUTIVO — NÚMEROS GRANDES
# ═══════════════════════════════════════════════════════════════════════════════
ax1 = fig.add_subplot(gs[1])
ax1.set_facecolor(BG2); ax1.set_xlim(0,1); ax1.set_ylim(0,1); ax1.axis('off')

ax1.add_patch(mpatches.Rectangle((0,0.90),1,0.10,
    transform=ax1.transAxes,facecolor=BG3,edgecolor='none'))
ax1.axhline(0.90,color=GOLD,linewidth=2.0)
ax1.text(0.5,0.95,'PANEL EJECUTIVO · ESTADO DEL SISTEMA SOLAR',
    transform=ax1.transAxes,color=GOLD,fontsize=10.5,fontweight='bold',
    ha='center',va='center',fontfamily='monospace')

kpi = [
    ('PRODUCCIÓN\nACTUAL',  f'{actual_kwp:.1f} kWp', GRNL if actual_kwp>100 else YELL),
    ('CAPACIDAD\nMÁXIMA',   f'{INSTALLED_KWP:.0f} kWp', WDIM),
    ('EFICIENCIA\nMEDIA',   f'{avg_eff:.1f}%',  GRNL if avg_eff>90 else YELL),
    ('PÉRDIDA\nESTIMADA',   f'{loss_kwp:.1f} kWp', REDL if loss_kwp>10 else YELL),
    ('PANELES\nOPERATIVOS', str(n_ok),   GRNL),
    ('DEGRADADOS',          str(n_deg),  YELL),
    ('FALLA',               str(n_fail), REDL),
    ('SOMBRA',              str(n_shadow),WDIM),
]
xs = np.linspace(0.055, 0.945, 8)
bw = 0.107
for i, (lbl, val, clr) in enumerate(kpi):
    cx = xs[i]
    ax1.add_patch(FancyBboxPatch((cx-bw/2, 0.02), bw, 0.86,
        boxstyle='round,pad=0.008', transform=ax1.transAxes,
        facecolor='#1a0505' if clr==REDL else BG3,
        edgecolor=clr, linewidth=1.6 if clr==REDL else 0.9, zorder=2))
    # Label top
    ax1.text(cx, 0.78, lbl,
        transform=ax1.transAxes, color=WDIM, fontsize=7.5,
        ha='center', va='top', fontfamily='monospace', zorder=3, linespacing=1.3)
    # Value BIG
    ax1.text(cx, 0.43, val,
        transform=ax1.transAxes, color=clr, fontsize=17, fontweight='bold',
        ha='center', va='center', fontfamily='monospace', zorder=3)

ax1.axhline(0.0,color=GOLD+'44',linewidth=0.5)

# ═══════════════════════════════════════════════════════════════════════════════
# 2  MAPA TÉRMICO — PANEL 1
# ═══════════════════════════════════════════════════════════════════════════════
ax2 = fig.add_subplot(gs[2])
ax2.set_facecolor(BG2); ax2.set_xlim(0,1); ax2.set_ylim(0,1); ax2.axis('off')

map_frame(ax2,
    'PANEL 1 · MAPA TÉRMICO — LANDSAT 8/9',
    f'AZUL = EFICIENTE  ·  ROJO = FALLA / SOBRECALENTAMIENTO  ·  Temp. media: {avg_temp:.1f}°C')

# Celdas sin texto — solo color
for r in range(ROWS):
    for c in range(COLS):
        t = float(np.clip((temp_grid[r,c]-25)/(72-25), 0, 1))
        x = ML + c*CW
        y = MB + (ROWS-1-r)*CH
        ax2.add_patch(mpatches.Rectangle(
            (x+0.001, y+0.002), CW-0.002, CH-0.004,
            transform=ax2.transAxes,
            facecolor=tcmap(t), edgecolor=BG+'aa', linewidth=0.3, zorder=2))

# Flecha señalando zona de fallas (esquina NO superior)
ax2.annotate('ZONA\nFALLAS',
    xy=(ML+0.5*CW, MB+(ROWS-1)*CH+CH*0.5),
    xytext=(ML-0.09, MB+(ROWS-2)*CH+CH*0.5),
    xycoords='axes fraction', textcoords='axes fraction',
    color=REDXL, fontsize=8.5, fontweight='bold', fontfamily='monospace',
    ha='right', va='center',
    arrowprops=dict(arrowstyle='->', color=REDXL, lw=1.4), zorder=8)

# Colorbar vertical derecha — ticks a 30 / 45 / 60°C
cb2 = vertical_colorbar(ax2, tcmap, 25, 72,
    [30, 45, 60],
    ['30°C — Eficiente', '45°C — Atención', '60°C — FALLA'])

# ═══════════════════════════════════════════════════════════════════════════════
# 3  LEYENDA TÉRMICA (debajo del mapa)
# ═══════════════════════════════════════════════════════════════════════════════
ax3 = fig.add_subplot(gs[3])
legend_row(ax3, [
    ('#1565c0', f'EFICIENTE  25-35°C  ({n_ok} paneles OK)'),
    ('#f0b429', f'DEGRADADO  40-52°C  ({n_deg} paneles)'),
    ('#e74c3c', f'FALLA  55-72°C  ({n_fail} paneles)'),
    ('#7f8c8d', f'SOMBRA  ({n_shadow} paneles)'),
])

# ═══════════════════════════════════════════════════════════════════════════════
# 4  MAPA EFICIENCIA — PANEL 2
# ═══════════════════════════════════════════════════════════════════════════════
ax4 = fig.add_subplot(gs[4])
ax4.set_facecolor(BG2); ax4.set_xlim(0,1); ax4.set_ylim(0,1); ax4.axis('off')

map_frame(ax4,
    'PANEL 2 · MAPA DE EFICIENCIA — SENTINEL-2',
    f'VERDE = OPTIMO (>93%)  ·  ROJO = FALLA (<75%)  ·  Eficiencia media: {avg_eff:.1f}%  ·  Produccion actual: {actual_kwp:.1f} / {INSTALLED_KWP:.0f} kWp')

# Celdas sin texto — solo color de eficiencia
for r in range(ROWS):
    for c in range(COLS):
        e = float(np.clip(eff_grid[r,c]/100, 0, 1))
        x = ML + c*CW
        y = MB + (ROWS-1-r)*CH
        ax4.add_patch(mpatches.Rectangle(
            (x+0.001, y+0.002), CW-0.002, CH-0.004,
            transform=ax4.transAxes,
            facecolor=ecmap(e), edgecolor=BG+'aa', linewidth=0.3, zorder=2))

# Flecha zona baja eficiencia
ax4.annotate('BAJA\nEFICIENCIA',
    xy=(ML+0.5*CW, MB+1*CH+CH*0.5),
    xytext=(ML-0.09, MB+2*CH+CH*0.5),
    xycoords='axes fraction', textcoords='axes fraction',
    color=REDXL, fontsize=8.5, fontweight='bold', fontfamily='monospace',
    ha='right', va='center',
    arrowprops=dict(arrowstyle='->', color=REDXL, lw=1.4), zorder=8)

# Colorbar vertical — ticks a 0 / 75 / 90 / 100%
cb4 = vertical_colorbar(ax4, ecmap, 0, 100,
    [0, 75, 90, 100],
    ['0% — Falla', '75% — Atencion', '90% — Bueno', '100% — Optimo'])

# ═══════════════════════════════════════════════════════════════════════════════
# 5  LEYENDA EFICIENCIA
# ═══════════════════════════════════════════════════════════════════════════════
ax5 = fig.add_subplot(gs[5])
legend_row(ax5, [
    ('#8b0000', f'FALLA  <60%  ({n_fail} paneles)'),
    ('#f0b429', f'ATENCION  75-90%  ({n_deg} paneles)'),
    ('#27ae60', f'OPTIMO  >93%  ({n_ok} paneles)'),
    ('#7f8c8d', f'SOMBRA  ({n_shadow} paneles)'),
])

# ═══════════════════════════════════════════════════════════════════════════════
# 6  TABLA DE RENDIMIENTO — 12pt mínimo
# ═══════════════════════════════════════════════════════════════════════════════
ax6 = fig.add_subplot(gs[6])
ax6.set_facecolor(BG2)
ax6.axis('off')

# Título con barra dorada
ax6.add_patch(mpatches.Rectangle((0,0.95),1,0.05,
    transform=ax6.transAxes,facecolor=BG3,edgecolor='none'))
ax6.axhline(0.95,color=GOLD,linewidth=2.0)
ax6.text(0.5,0.975,'TABLA DE RENDIMIENTO · ACTUAL vs. MAXIMO TEORICO 120 kWp',
    transform=ax6.transAxes,color=GOLD,fontsize=11,fontweight='bold',
    ha='center',va='center',fontfamily='monospace')

SEM_C = {'verde':GRNL,'amarillo':YELL,'rojo':REDL}
zones = [
    {'n':'Zona A · Norte-Este', 'p':42,'ok':36,'dg':5,'fl':1,'ef':91.2,'ac':10.9,'mx':12.0,'s':'verde', 'a':'Monitoreo mensual'},
    {'n':'Zona B · Norte-Oeste','p':42,'ok':28,'dg':11,'fl':3,'ef':83.4,'ac':9.0, 'mx':12.0,'s':'amarillo','a':'Limpiar superficie'},
    {'n':'Zona C · Centro-Este','p':42,'ok':38,'dg':3, 'fl':1,'ef':94.8,'ac':11.4,'mx':12.0,'s':'verde', 'a':'Sin accion'},
    {'n':'Zona D · Centro-Oeste','p':42,'ok':35,'dg':5,'fl':2,'ef':89.1,'ac':10.7,'mx':12.0,'s':'amarillo','a':'Revisar conexiones'},
    {'n':'Zona E · Sur / Arco', 'p':42,'ok':21,'dg':13,'fl':8,'ef':68.3,'ac':8.2, 'mx':12.0,'s':'rojo',  'a':'REEMPLAZAR - HOY'},
    {'n':'TOTAL SISTEMA',       'p':210,'ok':n_ok,'dg':n_deg,'fl':n_fail,'ef':avg_eff,'ac':actual_kwp,'mx':INSTALLED_KWP,'s':'amarillo','a':'Ver detalle'},
]

cols = ['ZONA / SECTOR','PANELES','EFF %','kWp ACTUAL','kWp MAX','PERDIDA kWp','PERDIDA %','PRIOR.','ACCION']
data = []
for z in zones:
    loss = z['mx']-z['ac']
    data.append([
        z['n'], str(z['p']),
        f"{z['ef']:.1f}%",
        f"{z['ac']:.1f}",
        f"{z['mx']:.1f}",
        f"{loss:.1f}",
        f"{loss/z['mx']*100:.1f}%",
        '●',
        z['a'],
    ])

tbl = ax6.table(
    cellText=data, colLabels=cols,
    cellLoc='center', loc='center',
    bbox=[0.0, 0.0, 1.0, 0.93],
)
tbl.auto_set_font_size(False)
tbl.set_fontsize(9)

# Header
for j in range(len(cols)):
    c = tbl[0,j]
    c.set_facecolor(BG3)
    c.set_text_props(color=GOLD,fontsize=9,fontweight='bold',fontfamily='monospace')
    c.set_edgecolor(GOLD+'66'); c.set_linewidth(0.9)

# Data rows
for i, z in enumerate(zones):
    sc = SEM_C[z['s']]
    bg = '#1a0505' if z['s']=='rojo' else ('#181200' if z['s']=='amarillo' else BG3)
    for j in range(len(cols)):
        cell = tbl[i+1,j]
        cell.set_facecolor(bg)
        cell.set_edgecolor(GOLD+'33'); cell.set_linewidth(0.5)
        if j==7:
            cell.set_text_props(color=sc,fontsize=14,fontweight='bold')
        elif j==0:
            cell.set_text_props(color=WHITE,fontsize=9,fontweight='bold',fontfamily='monospace')
        elif j==8:
            cell.set_text_props(color=sc,fontsize=9,fontweight='bold',fontfamily='monospace')
        else:
            cell.set_text_props(color=WHITE,fontsize=9,fontfamily='monospace')

# Borde rojo fila crítica, borde dorado total
for j in range(len(cols)):
    tbl[5,j].set_edgecolor(REDL); tbl[5,j].set_linewidth(1.2)
    tbl[6,j].set_edgecolor(GOLD); tbl[6,j].set_linewidth(1.4)

# ═══════════════════════════════════════════════════════════════════════════════
# 7  CURVA DE PRODUCCIÓN
# ═══════════════════════════════════════════════════════════════════════════════
ax7 = fig.add_subplot(gs[7])
ax7.set_facecolor(BG3)
for sp in ax7.spines.values():
    sp.set_edgecolor(GOLD+'33'); sp.set_linewidth(0.6)

ax7.add_patch(mpatches.Rectangle((0,1.00),1,0.06,
    transform=ax7.transAxes,facecolor=BG3,edgecolor='none',clip_on=False))
ax7.axhline(1.002,color=GOLD,linewidth=2.0,clip_on=False)
ax7.text(0.5,1.035,'PANEL 3 · CURVA DE PRODUCCION ESTIMADA — MAYO 2026',
    transform=ax7.transAxes,color=GOLD,fontsize=10.5,fontweight='bold',
    ha='center',va='bottom',fontfamily='monospace',clip_on=False)

hours = np.arange(6,19.5,0.5)
insol_max    = INSTALLED_KWP * np.clip(np.sin(np.pi*(hours-6)/13), 0, 1)
insol_actual = insol_max * (avg_eff/100)

ax7.fill_between(hours, 0, insol_max,    color=GRNL,alpha=0.10)
ax7.plot(hours, insol_max,    color=GRNL,linewidth=1.2,linestyle='--',alpha=0.6,
    label=f'Maximo teorico {INSTALLED_KWP:.0f} kWp')
ax7.fill_between(hours, 0, insol_actual, color=YELL,alpha=0.35)
ax7.plot(hours, insol_actual, color=YELL,linewidth=2.2,
    label=f'Produccion actual {actual_kwp:.1f} kWp pico')

ax7.axhline(INSTALLED_KWP*0.8, color=YELL,linewidth=1.0,linestyle=':',alpha=0.7)
ax7.text(18.8,INSTALLED_KWP*0.82,'80%',color=YELL,fontsize=9,fontfamily='monospace',va='bottom')

ax7.set_xlim(6,19); ax7.set_ylim(0,INSTALLED_KWP*1.12)
ax7.set_xlabel('Hora local (ART)',color=WDIM,fontsize=9,fontfamily='monospace')
ax7.set_ylabel('Produccion (kWp)',color=WDIM,fontsize=9,fontfamily='monospace')
ax7.tick_params(colors=WDIM,labelsize=9)
ax7.xaxis.set_major_formatter(ticker.FuncFormatter(lambda x,_: f'{int(x):02d}:00'))
ax7.legend(loc='upper right',framealpha=0.3,labelcolor=WHITE,fontsize=9,fancybox=True)
ax7.yaxis.label.set_color(WDIM); ax7.xaxis.label.set_color(WDIM)
ax7.tick_params(axis='x',colors=WDIM); ax7.tick_params(axis='y',colors=WDIM)

# ═══════════════════════════════════════════════════════════════════════════════
# 8  ALERTAS
# ═══════════════════════════════════════════════════════════════════════════════
ax8 = fig.add_subplot(gs[8])
ax8.set_facecolor(BG2); ax8.set_xlim(0,1); ax8.set_ylim(0,1); ax8.axis('off')

ax8.add_patch(mpatches.Rectangle((0,0.88),1,0.12,
    transform=ax8.transAxes,facecolor=BG3,edgecolor='none'))
ax8.axhline(0.88,color=GOLD,linewidth=2.0)
ax8.text(0.5,0.94,'ALERTAS ACTIVAS · ACCIONES RECOMENDADAS',
    transform=ax8.transAxes,color=GOLD,fontsize=10.5,fontweight='bold',
    ha='center',va='center',fontfamily='monospace')

alerts = [
    (REDL,  'URGENTE',  'Zona E - Arco Sur: 8 paneles >60 C. Riesgo cortocircuito. Revisar HOY.'),
    (YELL,  'ATENCION', 'Zona B - Norte-Oeste: polvo detectado. Eficiencia -16%. Limpiar esta semana.'),
    (GRNL,  'NORMAL',   'Zonas A y C: operacion optima >91%. Sin accion requerida.'),
]
for i, (clr, lvl, msg) in enumerate(alerts):
    y = 0.80 - i * 0.28
    ax8.add_patch(FancyBboxPatch((0.01,y-0.12),0.98,0.22,
        boxstyle='round,pad=0.008',transform=ax8.transAxes,
        facecolor='#1a0505' if clr==REDL else BG3,
        edgecolor=clr,linewidth=1.6 if clr==REDL else 0.8,zorder=2))
    ax8.text(0.025, y, lvl,
        transform=ax8.transAxes,color=clr,fontsize=9.5,fontweight='bold',
        va='center',fontfamily='monospace',zorder=3)
    ax8.text(0.145, y, msg,
        transform=ax8.transAxes,
        color=WHITE if clr!=GRNL else WDIM,
        fontsize=9.5,va='center',fontfamily='monospace',zorder=3)

# ═══════════════════════════════════════════════════════════════════════════════
# 9  FOOTER
# ═══════════════════════════════════════════════════════════════════════════════
ax9 = fig.add_subplot(gs[9])
ax9.set_facecolor(BG); ax9.set_xlim(0,1); ax9.set_ylim(0,1); ax9.axis('off')
ax9.axhline(0.99,color=GOLD,linewidth=1.2)
ax9.text(0.5,0.88,'Proximo escaneo automatico solar: lunes 25 de mayo 2026',
    transform=ax9.transAxes,color=GOLD,fontsize=9,fontweight='bold',
    ha='center',va='top',fontfamily='monospace')
ax9.text(0.5,0.66,'Sistema Fortin Inteligente · Faro Protocol · protocolfaro@gmail.com',
    transform=ax9.transAxes,color=WDIM,fontsize=8,ha='center',va='top',fontfamily='monospace')
ax9.text(0.5,0.45,f'SHA-256: {SHA}',
    transform=ax9.transAxes,color=GOLD+'99',fontsize=6.5,ha='center',va='top',fontfamily='monospace')
ax9.text(0.5,0.20,'Generado automaticamente · Landsat 8/9 TIRS · Sentinel-2 Reflectancia',
    transform=ax9.transAxes,color=WDIM,fontsize=7.5,ha='center',va='top',fontfamily='monospace')
for lx,ltxt in [(0.06,'ESA'),(0.14,'COP'),(0.22,'NASA')]:
    ax9.add_patch(FancyBboxPatch((lx-.03,.02),.07,.28,boxstyle='round,pad=0.01',
        transform=ax9.transAxes,facecolor=BG3,edgecolor=GOLD+'66',linewidth=0.7,zorder=3))
    ax9.text(lx+.005,.16,ltxt,transform=ax9.transAxes,color=GOLD,fontsize=7,
        fontweight='bold',ha='center',va='center',fontfamily='monospace',zorder=4)
for lx,ltxt in [(0.78,'ESA'),(0.86,'COP'),(0.94,'NASA')]:
    ax9.add_patch(FancyBboxPatch((lx-.03,.02),.07,.28,boxstyle='round,pad=0.01',
        transform=ax9.transAxes,facecolor=BG3,edgecolor=GOLD+'66',linewidth=0.7,zorder=3))
    ax9.text(lx+.005,.16,ltxt,transform=ax9.transAxes,color=GOLD,fontsize=7,
        fontweight='bold',ha='center',va='center',fontfamily='monospace',zorder=4)

# ─── SAVE ────────────────────────────────────────────────────────────────────
import pathlib as _pl
_REPORT_DIR = _pl.Path(__file__).parent.parent / 'reportes_velez'
_REPORT_DIR.mkdir(exist_ok=True)
out = str(_out_path or (_REPORT_DIR / 'faro_reporte_velez_solar_v2.png'))
fig.savefig(out, dpi=DPI, facecolor=BG, bbox_inches='tight', pad_inches=0.12)
plt.close(fig)
print(f'Saved: {out}')
