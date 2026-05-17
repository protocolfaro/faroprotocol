"""
gen_velez_main.py
Faro Protocol — Reporte Principal Vélez Sarsfield
Genera: reportes_velez/faro_reporte_velez.png
Resumen ejecutivo completo: agro + solar + estructural + alertas
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
import hashlib, os, pathlib
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
CERT = hashlib.sha256(f"FARO-VELEZ-MAIN-{now.isoformat()}".encode()).hexdigest()[:28].upper()
WEEK = now.strftime('Semana del %d de %B de %Y')

# ─── DATOS AGRO ───────────────────────────────────────────────────────────────
ZONES = [
    {'name': 'Campo\nAmalfitani', 'ndvi': 0.68, 'temp': 22.1, 'insar': 0.42, 'sem': 'verde',
     'accion': 'Aerificar porterías semana 3'},
    {'name': 'Cancha 1',          'ndvi': 0.48, 'temp': 24.3, 'insar': 1.20, 'sem': 'amarillo',
     'accion': 'Fungicida preventivo'},
    {'name': 'Cancha 2',          'ndvi': 0.52, 'temp': 23.8, 'insar': 0.60, 'sem': 'amarillo',
     'accion': 'Fertilizar 20 kg N/ha'},
    {'name': 'Cancha 3',          'ndvi': 0.39, 'temp': 25.1, 'insar': 1.85, 'sem': 'amarillo',
     'accion': 'Fungicida activo + resembrar'},
    {'name': 'Cancha 4',          'ndvi': 0.24, 'temp': 27.8, 'insar': 2.80, 'sem': 'rojo',
     'accion': '⚠ URGENTE — Drenaje + fungicida + resembrar'},
]

# ─── DATOS SOLAR ─────────────────────────────────────────────────────────────
SOLAR = {
    'total_panels': 210, 'kwp': 120.0,
    'eff_pct': 82.4, 'kwh_week': 2840,
    'fallas': 13, 'atencion': 22, 'ok': 175,
    'zona_alerta': 'Zona E (Arco Sur)',
}

# ─── DATOS ESTRUCTURAL ───────────────────────────────────────────────────────
TRIBUNAS = [
    {'name': 'Tribuna Norte',  'insar': 0.85, 'nivel': 'verde'},
    {'name': 'Tribuna Sur',    'insar': 1.20, 'nivel': 'amarillo'},
    {'name': 'Tribuna Este',   'insar': 0.60, 'nivel': 'verde'},
    {'name': 'Tribuna Oeste',  'insar': 2.80, 'nivel': 'rojo'},
]

# ─── HELPERS ─────────────────────────────────────────────────────────────────
def panel_ax(ax, title=''):
    ax.set_facecolor(BG2)
    for sp in ax.spines.values():
        sp.set_color(BORDER); sp.set_linewidth(0.7)
    if title:
        ax.set_title(title, color=GOLD, fontsize=9.5, fontweight='bold',
                     loc='left', pad=5, fontfamily='monospace')

def gold_line(ax, y=0.97):
    ax.plot([0, 1], [y, y], color=GOLD, linewidth=2.2,
            transform=ax.transAxes, clip_on=False)

# ─── FIGURA ───────────────────────────────────────────────────────────────────
fig = plt.figure(figsize=(13.5, 28), facecolor=BG)
gs = gridspec.GridSpec(8, 1, figure=fig, hspace=0.0,
    height_ratios=[1.1, 1.6, 4.5, 3.8, 3.5, 3.5, 3.8, 0.7])

# ══════════════════════════════════════════════════════════════════════════════
# HEADER
# ══════════════════════════════════════════════════════════════════════════════
ax_hdr = fig.add_subplot(gs[0])
ax_hdr.set_facecolor(BG3); ax_hdr.axis('off')
ax_hdr.plot([0,1],[0.97,0.97], color=GOLD, lw=3.5, transform=ax_hdr.transAxes, clip_on=False)
ax_hdr.text(0.5, 0.68, 'FARO PROTOCOL  ·  VÉLEZ SARSFIELD',
            color=GOLD, fontsize=20, fontweight='bold', ha='center', va='center',
            transform=ax_hdr.transAxes, fontfamily='monospace')
ax_hdr.text(0.5, 0.26, f'Estadio José Amalfitani  ·  Reporte Ejecutivo  ·  {WEEK}',
            color=WHITE, fontsize=10.5, ha='center', transform=ax_hdr.transAxes)

# ══════════════════════════════════════════════════════════════════════════════
# KPI ROW
# ══════════════════════════════════════════════════════════════════════════════
ax_kpi = fig.add_subplot(gs[1])
ax_kpi.set_facecolor(BG3); ax_kpi.axis('off')
ax_kpi.set_xlim(0,1); ax_kpi.set_ylim(0,1)

# Score global
crit = sum(1 for z in ZONES if z['sem'] == 'rojo')
aten = sum(1 for z in ZONES if z['sem'] == 'amarillo')
score_color = REDL if crit > 0 else (YELL if aten > 1 else GRNL)
score_val = 100 - crit*25 - aten*8

kpis = [
    ('SCORE PREDIO', f'{score_val}/100',  score_color),
    ('ZONAS CRÍTICAS', str(crit),          REDL if crit else GRNL),
    ('ZONAS ATENCIÓN', str(aten),          YELL if aten else GRNL),
    ('SOLAR EFIC.',   f'{SOLAR["eff_pct"]}%', YELL),
    ('PRODUCCIÓN',    f'{SOLAR["kwh_week"]:,}\nkWh/sem', GRNL),
    ('TRIBUNA ALERTA','OESTE\n2.80 mm',   REDL),
    ('PRÓX. ACCIÓN',  'HOY\nCancha 4',    REDL),
]
for i, (lbl, val, col) in enumerate(kpis):
    xi = (i + 0.5) / len(kpis)
    box = FancyBboxPatch((xi - 0.062, 0.07), 0.124, 0.86,
        boxstyle="round,pad=0.008", facecolor=col+'18', edgecolor=col+'55',
        linewidth=0.9, transform=ax_kpi.transAxes)
    ax_kpi.add_patch(box)
    ax_kpi.text(xi, 0.78, lbl, color=WDIM, fontsize=7, ha='center',
                transform=ax_kpi.transAxes, fontfamily='monospace')
    ax_kpi.text(xi, 0.42, val, color=col, fontsize=16, fontweight='bold',
                ha='center', va='center', transform=ax_kpi.transAxes)

# ══════════════════════════════════════════════════════════════════════════════
# MAPA NDVI PREDIO
# ══════════════════════════════════════════════════════════════════════════════
ax_map = fig.add_subplot(gs[2])
panel_ax(ax_map, '  CAPA NDVI — Vista Satelital del Predio · Sentinel-2 MSI')
ax_map.set_facecolor('#060d08')
ax_map.set_xlim(0, 10); ax_map.set_ylim(0, 8)
ax_map.tick_params(left=False, bottom=False, labelleft=False, labelbottom=False)

# Fondo pasto base
ax_map.add_patch(mpatches.Rectangle((0,0), 10, 8, facecolor='#122008', zorder=1))

cmap_ndvi = LinearSegmentedColormap.from_list('ndvi',
    ['#b71c1c','#ef6c00','#fdd835','#66bb6a','#1b5e20'], N=256)

# Zonas con NDVI color
field_zones = [
    (0.3, 4.8, 4.2, 2.8, ZONES[0]),   # Campo Amalfitani
    (5.2, 5.4, 2.0, 1.8, ZONES[1]),   # Cancha 1
    (7.5, 5.4, 2.0, 1.8, ZONES[2]),   # Cancha 2
    (5.2, 3.2, 2.0, 1.8, ZONES[3]),   # Cancha 3
    (7.5, 3.2, 2.0, 1.8, ZONES[4]),   # Cancha 4
]
for (x, y, w, h, z) in field_zones:
    t = np.clip((z['ndvi'] - 0.1) / 0.7, 0, 1)
    col = cmap_ndvi(t)
    ax_map.add_patch(mpatches.Rectangle((x, y), w, h, facecolor=col,
        edgecolor='#ffffff33', linewidth=0.6, zorder=2))
    ax_map.text(x + w/2, y + h/2 + 0.2, z['name'].replace('\n', ' '),
               color='#ffffffcc', fontsize=7, ha='center', va='center',
               fontweight='bold', zorder=4)
    ax_map.text(x + w/2, y + h/2 - 0.3, f'NDVI {z["ndvi"]:.2f}',
               color='#ffffffaa', fontsize=6.5, ha='center', va='center', zorder=4)
    # Semáforo badge
    bcol = SEM_COL[z['sem']]
    ax_map.add_patch(Circle((x + w - 0.2, y + h - 0.2), 0.15,
        facecolor=bcol, zorder=5))

# Cancha 4 alerta overlay
ax_map.add_patch(mpatches.Rectangle((7.5, 3.2), 2.0, 1.8,
    facecolor='none', edgecolor=REDL, linewidth=2.0, linestyle='--', zorder=6))
ax_map.text(8.5, 5.15, 'CRÍTICA', color=REDL, fontsize=7.5, fontweight='bold',
           ha='center', zorder=7)

# Estadio outline
ax_map.add_patch(mpatches.Rectangle((0.1, 0.3), 9.8, 7.4,
    facecolor='none', edgecolor=GOLD+'55', linewidth=1.2, linestyle=':', zorder=7))

# Tribuna Oeste alerta
ax_map.add_patch(mpatches.Rectangle((0.05, 0.3), 0.25, 7.4,
    facecolor=REDL+'33', edgecolor=REDL, linewidth=1.0, zorder=6))
ax_map.text(0.18, 3.9, 'ALERTA\nInSAR', color=REDL, fontsize=6,
           ha='center', rotation=90, fontweight='bold', zorder=7)

# Colorbar NDVI
sm_ndvi = ScalarMappable(cmap=cmap_ndvi, norm=plt.Normalize(0.1, 0.8))
sm_ndvi.set_array([])
cax = ax_map.inset_axes([0.88, 0.05, 0.025, 0.88])
cb = plt.colorbar(sm_ndvi, cax=cax)
cb.set_ticks([0.1, 0.3, 0.5, 0.7])
cb.set_ticklabels(['0.1\nSeco', '0.3\nPobre', '0.5\nMedio', '0.7\nOptimo'],
                  fontsize=6.5)
cb.ax.yaxis.set_tick_params(color=WDIM, labelcolor=WDIM)
cb.outline.set_edgecolor(BORDER)
cb.set_label('NDVI', color=GOLD, fontsize=7, rotation=270, labelpad=8)

# Compass + labels
ax_map.text(9.7, 7.65, 'N', color=GOLD, fontsize=10, fontweight='bold', ha='center')
ax_map.annotate('', xy=(9.7, 7.58), xytext=(9.7, 7.15),
               arrowprops=dict(arrowstyle='->', color=GOLD, lw=1.5))
ax_map.text(0.5, 0.08, 'Av. Juan B. Justo  →', color='#ffffff33',
           fontsize=7, ha='center')

# ══════════════════════════════════════════════════════════════════════════════
# TABLA AGRONÓMICA
# ══════════════════════════════════════════════════════════════════════════════
ax_agro = fig.add_subplot(gs[3])
panel_ax(ax_agro, '  ESTADO AGRONÓMICO — Análisis Multi-Espectral por Zona')
ax_agro.set_xlim(0,1); ax_agro.set_ylim(0,1); ax_agro.axis('off')

headers = ['ZONA', 'NDVI', 'TEMP', 'InSAR', 'SEMÁFORO', 'ACCIÓN ESTA SEMANA']
col_xs  = [0.01, 0.15, 0.23, 0.31, 0.41, 0.54]

y0 = 0.90
for j, (h, cx) in enumerate(zip(headers, col_xs)):
    ax_agro.text(cx + 0.005, y0, h, color=GOLD, fontsize=8, fontweight='bold',
                transform=ax_agro.transAxes)
ax_agro.plot([0.005, 0.995], [y0 - 0.03, y0 - 0.03],
            color=GOLD + '55', lw=0.8, transform=ax_agro.transAxes)

rh = 0.155
for i, z in enumerate(ZONES):
    ry = y0 - 0.06 - i * rh
    col = SEM_COL[z['sem']]
    bg = FancyBboxPatch((0.005, ry - rh + 0.020), 0.990, rh - 0.010,
        boxstyle="round,pad=0.003", facecolor=col + '14', edgecolor=col + '30',
        linewidth=0.5, transform=ax_agro.transAxes)
    ax_agro.add_patch(bg)
    vals = [z['name'].replace('\n',' '), f"{z['ndvi']:.2f}",
            f"{z['temp']:.1f}°C", f"{z['insar']:.2f}mm"]
    for j, (v, cx) in enumerate(zip(vals, col_xs[:4])):
        ax_agro.text(cx + 0.005, ry - rh/2 + 0.018, v,
                    color=WHITE + 'cc', fontsize=8, transform=ax_agro.transAxes, va='center')
    badge = FancyBboxPatch((col_xs[4], ry - rh + 0.025), 0.09, rh - 0.022,
        boxstyle="round,pad=0.004", facecolor=col + '44', edgecolor=col,
        linewidth=0.7, transform=ax_agro.transAxes)
    ax_agro.add_patch(badge)
    ax_agro.text(col_xs[4] + 0.045, ry - rh/2 + 0.018,
                z['sem'].upper(), color=col, fontsize=7.5, fontweight='bold',
                ha='center', transform=ax_agro.transAxes, va='center')
    ax_agro.text(col_xs[5] + 0.005, ry - rh/2 + 0.018, z['accion'],
                color=WHITE + 'cc', fontsize=7.5, transform=ax_agro.transAxes, va='center')

# ══════════════════════════════════════════════════════════════════════════════
# SISTEMA SOLAR
# ══════════════════════════════════════════════════════════════════════════════
ax_sol = fig.add_subplot(gs[4])
panel_ax(ax_sol, '  SISTEMA SOLAR — 210 Paneles · 120 kWp · Techo Sur Amalfitani')
ax_sol.set_xlim(0,1); ax_sol.set_ylim(0,1); ax_sol.axis('off')

# Gauge de eficiencia
eff = SOLAR['eff_pct'] / 100
theta = np.linspace(np.pi, 0, 100)
rx, ry, rr = 0.18, 0.5, 0.28
ax_sol.plot(rx + rr * np.cos(theta), ry + rr * 0.55 * np.sin(theta),
           color=BG3, linewidth=14, solid_capstyle='round', transform=ax_sol.transAxes, zorder=2)
eff_col = GRNL if eff > 0.9 else (YELL if eff > 0.75 else REDL)
theta_eff = np.linspace(np.pi, np.pi - eff * np.pi, 100)
ax_sol.plot(rx + rr * np.cos(theta_eff), ry + rr * 0.55 * np.sin(theta_eff),
           color=eff_col, linewidth=14, solid_capstyle='round', transform=ax_sol.transAxes, zorder=3)
ax_sol.text(rx, ry - 0.08, f'{SOLAR["eff_pct"]}%', color=eff_col, fontsize=22,
           fontweight='bold', ha='center', transform=ax_sol.transAxes)
ax_sol.text(rx, ry - 0.26, 'EFICIENCIA', color=WDIM, fontsize=8,
           ha='center', transform=ax_sol.transAxes, fontfamily='monospace')

# Panel stats
solar_stats = [
    ('Paneles OK',      f'{SOLAR["ok"]}',       GRNL),
    ('Atención',        f'{SOLAR["atencion"]}', YELL),
    ('Falla',           f'{SOLAR["fallas"]}',   REDL),
    ('Prod. semana',    f'{SOLAR["kwh_week"]:,} kWh', CYAN),
    ('Zona alerta',     SOLAR['zona_alerta'],   YELL),
]
for i, (lbl, val, col) in enumerate(solar_stats):
    xi = 0.40 + (i % 3) * 0.20
    yi = 0.68 - (i // 3) * 0.35
    box = FancyBboxPatch((xi - 0.075, yi - 0.12), 0.15, 0.22,
        boxstyle="round,pad=0.006", facecolor=col + '15', edgecolor=col + '50',
        linewidth=0.7, transform=ax_sol.transAxes)
    ax_sol.add_patch(box)
    ax_sol.text(xi, yi + 0.04, val, color=col, fontsize=14, fontweight='bold',
               ha='center', transform=ax_sol.transAxes)
    ax_sol.text(xi, yi - 0.07, lbl, color=WDIM, fontsize=7, ha='center',
               transform=ax_sol.transAxes, fontfamily='monospace')

# ══════════════════════════════════════════════════════════════════════════════
# ESTRUCTURAL / InSAR
# ══════════════════════════════════════════════════════════════════════════════
ax_str = fig.add_subplot(gs[5])
panel_ax(ax_str, '  MONITOREO ESTRUCTURAL — InSAR Sentinel-1 · Desplazamiento mm/semana')
ax_str.set_facecolor(BG2)
ax_str.set_xlim(0, 10); ax_str.set_ylim(0, 1)
ax_str.tick_params(left=False, bottom=False, labelleft=False, labelbottom=False)
for sp in ax_str.spines.values(): sp.set_color(BORDER)

bw = 1.8
for i, t in enumerate(TRIBUNAS):
    bx = 1.0 + i * 2.2
    bar_h = min(t['insar'] / 3.5, 0.92)
    col = SEM_COL[t['nivel']]
    ax_str.add_patch(FancyBboxPatch((bx, 0.06), bw, bar_h,
        boxstyle="round,pad=0.01", facecolor=col + '55', edgecolor=col, linewidth=1.5))
    ax_str.text(bx + bw/2, bar_h + 0.10, f'{t["insar"]:.2f} mm',
               color=col, fontsize=11, fontweight='bold', ha='center')
    ax_str.text(bx + bw/2, 0.02, t['name'], color=WDIM, fontsize=8, ha='center')

# Umbral line
ax_str.axhline(3.0/3.5, color=REDL + 'aa', linewidth=1.2, linestyle='--', zorder=5)
ax_str.text(9.6, 3.0/3.5 + 0.03, 'UMBRAL\n3.0 mm', color=REDL, fontsize=7,
           ha='right', va='bottom')
ax_str.set_xlim(0, 10); ax_str.set_ylim(0, 1.1)

# ══════════════════════════════════════════════════════════════════════════════
# PLAN DE ACCIÓN SEMANAL
# ══════════════════════════════════════════════════════════════════════════════
ax_plan = fig.add_subplot(gs[6])
panel_ax(ax_plan, '  PLAN DE ACCIÓN — Tareas por Prioridad y Responsable')
ax_plan.set_xlim(0,1); ax_plan.set_ylim(0,1); ax_plan.axis('off')

acciones = [
    (REDL,  'HOY',        'Cancha 4',           'Fungicida + reparar drenaje + resembrar',     'Roger Bernal'),
    (REDL,  'ESTA SEMANA','Tribuna Oeste',       'Solicitar inspección estructural externa',    'Intendencia'),
    (YELL,  'ESTA SEMANA','Canchas 1, 3',        'Fungicida activo — ver mapa de focos',       'Roger Bernal'),
    (YELL,  'ESTA SEMANA','Cancha 2',            'Fertilizar 20 kg N/ha uniformemente',        'Roger Bernal'),
    (YELL,  'ESTA SEMANA','Solar Zona E',        'Revisar 8 paneles con temperatura >60°C',   'Mantenimiento'),
    (GRNL,  'SEMANA 3',   'Campo Amalfitani',   'Aerificar las dos porterías',                 'Roger Bernal'),
    (GRNL,  'SEMANA 3',   'Sistema Solar',      'Limpieza preventiva panel completo',          'Mantenimiento'),
]

hdrs = ['PRIORIDAD', 'CUÁNDO', 'ZONA', 'TAREA', 'RESPONSABLE']
hxs  = [0.010, 0.095, 0.190, 0.340, 0.770]
y0p  = 0.935
for h, hx in zip(hdrs, hxs):
    ax_plan.text(hx+0.004, y0p, h, color=GOLD, fontsize=7.8, fontweight='bold',
                transform=ax_plan.transAxes)
ax_plan.plot([0.005,0.995],[y0p-0.025,y0p-0.025],
            color=GOLD+'55', lw=0.8, transform=ax_plan.transAxes)

rh_p = 0.116
for i, (col, cuando, zona, tarea, resp) in enumerate(acciones):
    ry = y0p - 0.040 - i * rh_p
    ax_plan.add_patch(FancyBboxPatch((0.005, ry - rh_p + 0.012), 0.990, rh_p - 0.004,
        boxstyle="round,pad=0.003", facecolor=col + '12', edgecolor=col + '28',
        linewidth=0.4, transform=ax_plan.transAxes))
    badge = FancyBboxPatch((hxs[0], ry - rh_p + 0.016), 0.077, rh_p - 0.016,
        boxstyle="round,pad=0.004", facecolor=col + '44', edgecolor=col,
        linewidth=0.7, transform=ax_plan.transAxes)
    ax_plan.add_patch(badge)
    ax_plan.text(hxs[0]+0.038, ry - rh_p/2 + 0.006,
                '!!!' if col == REDL else ('!!' if col == YELL else 'OK'),
                color=col, fontsize=8, fontweight='bold', ha='center',
                transform=ax_plan.transAxes, va='center')
    for txt, hx in zip([cuando, zona, tarea, resp], hxs[1:]):
        ax_plan.text(hx + 0.004, ry - rh_p/2 + 0.006, txt,
                    color=WHITE + 'cc', fontsize=7.5, transform=ax_plan.transAxes, va='center')

# ══════════════════════════════════════════════════════════════════════════════
# FOOTER
# ══════════════════════════════════════════════════════════════════════════════
ax_foot = fig.add_subplot(gs[7])
ax_foot.set_facecolor(BG3); ax_foot.axis('off')
ax_foot.plot([0,1],[0.97,0.97], color=GOLD, lw=2.5, transform=ax_foot.transAxes, clip_on=False)
ax_foot.text(0.01, 0.45, f'CERT SHA-256: {CERT}',
            color=WDIM, fontsize=6.5, fontfamily='monospace', transform=ax_foot.transAxes, va='center')
ax_foot.text(0.5, 0.45,
            'Faro Protocol · Fortín Inteligente · Datos: Sentinel-2 / Sentinel-1 / ERA5',
            color=WDIM, fontsize=7.5, ha='center', transform=ax_foot.transAxes, va='center')
ax_foot.text(0.99, 0.45,
            f'protocolfaro@gmail.com  ·  {now.strftime("%d/%m/%Y %H:%M")}',
            color=WDIM, fontsize=7.5, ha='right', fontfamily='monospace',
            transform=ax_foot.transAxes, va='center')

# ─── SAVE ────────────────────────────────────────────────────────────────────
plt.subplots_adjust(left=0.03, right=0.97, top=0.995, bottom=0.008, hspace=0.0)
_REPORT_DIR = pathlib.Path(__file__).parent / 'reportes_velez'
_REPORT_DIR.mkdir(exist_ok=True)
out = str(_REPORT_DIR / 'faro_reporte_velez.png')
plt.savefig(out, dpi=DPI, facecolor=BG, bbox_inches='tight', pad_inches=0.08)
plt.close()
print(f'Saved: {out}')
