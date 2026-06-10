"""
gen_velez_piletas.py
Faro Protocol — Complejo Acuático + Natatorio Olímpico · Vélez Sarsfield
Genera: reportes_velez/faro_reporte_velez_piletas.png
Datos reales vía faro_assembler / FARO_VD_PATH.
Sin datos individuales → muestra score global únicamente.
"""
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.gridspec as gridspec
import matplotlib.patches as mpatches
from matplotlib.patches import FancyBboxPatch, Circle
import numpy as np
import hashlib, pathlib, json, os
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
BORDER= '#1e2a38'
DPI   = 200

SEM_COL = {'verde': GRNL, 'amarillo': YELL, 'rojo': REDL}

now  = datetime.now()
CERT = hashlib.sha256(f"FARO-VELEZ-PILETAS-{now.isoformat()}".encode()).hexdigest()[:28].upper()
WEEK = now.strftime('Semana del %d de %B de %Y')

# ─── DATOS REALES ─────────────────────────────────────────────────────────────
score_piletas   = None
sem_piletas     = 'verde'
detalle_piletas = None

_vd_path = os.environ.get("FARO_VD_PATH")
if _vd_path:
    try:
        with open(_vd_path, encoding="utf-8") as _f:
            _vd = json.load(_f)
        _s = _vd.get("sectores", {}).get("piletas", {})
        if isinstance(_s.get("score"), (int, float)):
            score_piletas = int(_s["score"])
        if _s.get("sem"):
            sem_piletas = _s["sem"]
        detalle_piletas = _s.get("detalle")
    except Exception as _e:
        print(f"FARO_VD_PATH piletas: {_e}")

_out_path = os.environ.get("FARO_OUT_PATH")

ALERTA_COL = SEM_COL.get(sem_piletas, GRNL)
if sem_piletas == 'rojo':
    ALERTA_GLOBAL = 'ALERTA ROJA'
elif sem_piletas == 'amarillo':
    ALERTA_GLOBAL = 'ATENCIÓN'
else:
    ALERTA_GLOBAL = 'ÓPTIMA'

# ─── HELPERS ─────────────────────────────────────────────────────────────────
def panel_ax(ax, title=''):
    ax.set_facecolor(BG2)
    for sp in ax.spines.values():
        sp.set_color(BORDER); sp.set_linewidth(0.7)
    if title:
        ax.set_title(title, color=GOLD, fontsize=9.5, fontweight='bold',
                     loc='left', pad=5, fontfamily='monospace')

def sin_dato_panel(ax, titulo, nota=''):
    ax.set_facecolor(BG2); ax.set_xlim(0,1); ax.set_ylim(0,1); ax.axis('off')
    ax.set_title(titulo, color=GOLD+'99', fontsize=9.5, fontweight='bold',
                 loc='left', pad=5, fontfamily='monospace')
    ax.add_patch(FancyBboxPatch((0.10, 0.18), 0.80, 0.60,
        boxstyle='round,pad=0.02', transform=ax.transAxes,
        facecolor=BG3, edgecolor=WDIM+'33', linewidth=1.0))
    ax.text(0.5, 0.56, 'SIN DATO',
        transform=ax.transAxes, color=WDIM, fontsize=20, fontweight='bold',
        ha='center', va='center', fontfamily='monospace')
    if nota:
        ax.text(0.5, 0.32, nota,
            transform=ax.transAxes, color=WDIM+'88', fontsize=8.5,
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
# PANEL EJECUTIVO — SCORE GLOBAL + SIN DATO PARA KPIs
# ══════════════════════════════════════════════════════════════════════════════
ax_kpi = fig.add_subplot(gs[1])
ax_kpi.set_facecolor(BG3); ax_kpi.axis('off')
ax_kpi.set_xlim(0, 12); ax_kpi.set_ylim(0, 3)

ax_kpi.text(6, 2.92, 'PANEL EJECUTIVO — KPIs ACUÁTICOS · Sistema de Alerta Temprana',
           color=GOLD, fontsize=9.5, fontweight='bold',
           ha='center', va='top', fontfamily='monospace')

KPI_LABELS = ['TURBIDEZ', 'CLOROFILA/\nALGAS', 'TEMP.\nAGUA', 'TEMP.\nTECHO',
              'ASENTA-\nMIENTO', 'SCORE\nGLOBAL']
KPI_SUB    = ['Sentinel-2 NIR', 'Sentinel-2 B5/B4', 'Sentinel-2 LST',
              'Landsat TIRS', 'InSAR Sentinel-1', 'Índice FARO']
xs_kpi = np.linspace(1.0, 11.0, 6)

for i in range(6):
    cx = xs_kpi[i]
    is_score = (i == 5)

    if is_score and score_piletas is not None:
        col = ALERTA_COL
        pct = score_piletas / 100
        theta_bg = np.linspace(np.pi, 0, 100)
        ax_kpi.plot(cx + 0.7*np.cos(theta_bg), 1.6 + 0.42*np.sin(theta_bg),
                   color=BG2, linewidth=10, solid_capstyle='round', zorder=2)
        theta_v = np.linspace(np.pi, np.pi - pct*np.pi, 100)
        ax_kpi.plot(cx + 0.7*np.cos(theta_v), 1.6 + 0.42*np.sin(theta_v),
                   color=col, linewidth=10, solid_capstyle='round', zorder=3)
        ax_kpi.text(cx, 1.58, str(score_piletas), color=col, fontsize=16, fontweight='bold',
                   ha='center', va='center')
        ax_kpi.text(cx, 1.32, '/100', color=WDIM, fontsize=8,
                   ha='center', va='top', fontfamily='monospace')
        ax_kpi.text(cx, 0.82, ALERTA_GLOBAL, color=col, fontsize=8, fontweight='bold',
                   ha='center', va='top', fontfamily='monospace')
        if detalle_piletas:
            ax_kpi.text(cx, 0.58, detalle_piletas[:30], color=WDIM, fontsize=5.5,
                       ha='center', va='top', fontfamily='monospace')
        ax_kpi.add_patch(Circle((cx, 1.58), 0.32, facecolor=col, alpha=0.10, zorder=1))
    else:
        ax_kpi.add_patch(FancyBboxPatch((cx-0.60, 0.45), 1.20, 1.50,
            boxstyle='round,pad=0.04', facecolor=BG2, edgecolor=WDIM+'22',
            linewidth=0.7, zorder=2))
        ax_kpi.text(cx, 1.60, '—', color=WDIM+'55', fontsize=18, fontweight='bold',
                   ha='center', va='center')
        ax_kpi.text(cx, 0.82, 'SIN DATO', color=WDIM+'66', fontsize=6.5, fontweight='bold',
                   ha='center', va='top', fontfamily='monospace')

    ax_kpi.text(cx, 2.18, KPI_LABELS[i], color=GOLD, fontsize=7, fontweight='bold',
               ha='center', va='bottom', fontfamily='monospace')
    ax_kpi.text(cx, 0.38, KPI_SUB[i], color=WDIM+'66', fontsize=6,
               ha='center', va='top', fontfamily='monospace')

# ══════════════════════════════════════════════════════════════════════════════
# MAPA COMPLEJO ACUÁTICO — SIN DATO
# ══════════════════════════════════════════════════════════════════════════════
ax_map = fig.add_subplot(gs[2])
sin_dato_panel(ax_map,
    '  MAPA COMPLEJO ACUÁTICO — Sentinel-2 NIR · Calidad y Temperatura del Agua',
    'Disponible cuando velez_piletas (Supabase) tenga datos de turbidez y temperatura por pileta_id.')

# ══════════════════════════════════════════════════════════════════════════════
# TENDENCIAS — SIN DATO
# ══════════════════════════════════════════════════════════════════════════════
ax_trend = fig.add_subplot(gs[3])
sin_dato_panel(ax_trend,
    '  TENDENCIAS — Calidad del Agua · Últimas 4 semanas',
    'Series históricas disponibles cuando velez_piletas tenga registros por pileta_id y fecha.')

# ══════════════════════════════════════════════════════════════════════════════
# InSAR ASENTAMIENTO — SIN DATO
# ══════════════════════════════════════════════════════════════════════════════
ax_ins = fig.add_subplot(gs[4])
sin_dato_panel(ax_ins,
    '  InSAR PERIMETRAL — Sentinel-1 · Asentamiento Diferencial Vasos Piletas (mm/sem)',
    'Datos de asentamiento disponibles cuando soil_metrics tenga registros para sector piletas.')

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
ax_alert.text(0.5, 0.37,
             detalle_piletas if detalle_piletas else 'Score global vía Faro assembler',
             color=WDIM, fontsize=9, ha='center',
             transform=ax_alert.transAxes, fontfamily='monospace')

if score_piletas is not None:
    ax_alert.text(0.5, 0.15, f'Score: {score_piletas}/100',
                 color=ALERTA_COL, fontsize=12, fontweight='bold', ha='center',
                 transform=ax_alert.transAxes, fontfamily='monospace')

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
