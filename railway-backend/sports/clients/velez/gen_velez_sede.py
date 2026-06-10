"""
gen_velez_sede.py
Faro Protocol — Sede Central & Instituto Vélez Sarsfield
Genera: reportes_velez/faro_reporte_velez_sede.png
Datos reales vía faro_assembler / FARO_VD_PATH.
Sin datos por sector → muestra score global únicamente.
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
BORDER= '#1e2a38'
DPI   = 200

SEM_COL = {'verde': GRNL, 'amarillo': YELL, 'rojo': REDL}

now  = datetime.now()
CERT = hashlib.sha256(f"FARO-VELEZ-SEDE-{now.isoformat()}".encode()).hexdigest()[:28].upper()
WEEK = now.strftime('Semana del %d de %B de %Y')

# ─── DATOS REALES ─────────────────────────────────────────────────────────────
score_sede   = None
sem_sede     = 'verde'
detalle_sede = None

_vd_path = os.environ.get("FARO_VD_PATH")
if _vd_path:
    try:
        with open(_vd_path, encoding="utf-8") as _f:
            _vd = json.load(_f)
        _s = _vd.get("sectores", {}).get("sede", {})
        if isinstance(_s.get("score"), (int, float)):
            score_sede = int(_s["score"])
        if _s.get("sem"):
            sem_sede = _s["sem"]
        detalle_sede = _s.get("detalle")
    except Exception as _e:
        print(f"FARO_VD_PATH sede: {_e}")

_out_path = os.environ.get("FARO_OUT_PATH")
score_col = SEM_COL.get(sem_sede, WDIM)

# ─── HELPERS ─────────────────────────────────────────────────────────────────
def panel_ax(ax, title=''):
    ax.set_facecolor(BG2)
    for sp in ax.spines.values():
        sp.set_color(BORDER); sp.set_linewidth(0.7)
    if title:
        ax.set_title(title, color=GOLD, fontsize=9.5, fontweight='bold',
                     loc='left', pad=5, fontfamily='monospace')

def sin_dato_panel(ax, titulo, nota=''):
    panel_ax(ax)
    ax.set_xlim(0,1); ax.set_ylim(0,1); ax.axis('off')
    ax.set_title(titulo, color=GOLD+'99', fontsize=9.5, fontweight='bold',
                 loc='left', pad=5, fontfamily='monospace')
    ax.add_patch(FancyBboxPatch((0.10, 0.16), 0.80, 0.62,
        boxstyle='round,pad=0.02', transform=ax.transAxes,
        facecolor=BG3, edgecolor=WDIM+'33', linewidth=1.0))
    ax.text(0.5, 0.55, 'SIN DATO',
        transform=ax.transAxes, color=WDIM, fontsize=20, fontweight='bold',
        ha='center', va='center', fontfamily='monospace')
    if nota:
        ax.text(0.5, 0.33, nota,
            transform=ax.transAxes, color=WDIM+'88', fontsize=8.5,
            ha='center', va='center', fontfamily='monospace')

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
# KPI ROW — SCORE GLOBAL + SIN DATO PARA MÉTRICAS POR SECTOR
# ══════════════════════════════════════════════════════════════════════════════
ax_kpi = fig.add_subplot(gs[1])
ax_kpi.set_facecolor(BG3); ax_kpi.axis('off')
ax_kpi.set_xlim(0,1); ax_kpi.set_ylim(0,1)

kpi_defs = [
    ('SCORE SEDE',      score_sede,  score_col),
    ('TEMP. MÁX TECHO', None,        WDIM),
    ('InSAR MÁX',       None,        WDIM),
    ('NDVI PROM.',       None,        WDIM),
    ('ALERTAS',         None,        WDIM),
    ('SECTORES OK',     None,        WDIM),
]
for i, (lbl, val, col) in enumerate(kpi_defs):
    xi = (i + 0.5) / len(kpi_defs)
    box = FancyBboxPatch((xi - 0.075, 0.07), 0.150, 0.86,
        boxstyle="round,pad=0.008",
        facecolor=col+'18' if val is not None else BG2,
        edgecolor=col+'55' if val is not None else WDIM+'22',
        linewidth=0.9, transform=ax_kpi.transAxes)
    ax_kpi.add_patch(box)
    ax_kpi.text(xi, 0.78, lbl, color=WDIM, fontsize=7, ha='center',
                transform=ax_kpi.transAxes, fontfamily='monospace')
    if val is not None:
        ax_kpi.text(xi, 0.42, f'{val}/100', color=col, fontsize=16, fontweight='bold',
                    ha='center', va='center', transform=ax_kpi.transAxes)
    else:
        ax_kpi.text(xi, 0.42, '—', color=WDIM+'44', fontsize=16, fontweight='bold',
                    ha='center', va='center', transform=ax_kpi.transAxes)
        ax_kpi.text(xi, 0.18, 'SIN DATO', color=WDIM+'55', fontsize=6.5,
                    ha='center', va='center', transform=ax_kpi.transAxes, fontfamily='monospace')

if detalle_sede:
    ax_kpi.text(0.5, 0.04, detalle_sede, color=WDIM+'aa', fontsize=7.5,
                ha='center', va='bottom', transform=ax_kpi.transAxes, fontfamily='monospace')

# ══════════════════════════════════════════════════════════════════════════════
# MAPA TÉRMICO — SIN DATO
# ══════════════════════════════════════════════════════════════════════════════
ax_map = fig.add_subplot(gs[2])
sin_dato_panel(ax_map,
    '  MAPA TÉRMICO — Landsat TIRS · Temperatura Superficial por Sector',
    'Temperatura por sector disponible cuando Landsat procese imágenes del predio\n'
    'y velez_sectores tenga campo temp_techo por sector_id.')

# ══════════════════════════════════════════════════════════════════════════════
# SAR ESTRUCTURAL — SIN DATO
# ══════════════════════════════════════════════════════════════════════════════
ax_sar = fig.add_subplot(gs[3])
sin_dato_panel(ax_sar,
    '  MONITOREO ESTRUCTURAL — InSAR Sentinel-1 · Asentamiento mm/semana',
    'Datos de asentamiento por sector disponibles cuando soil_metrics\n'
    'tenga registros con sector_id de sede.')

# ══════════════════════════════════════════════════════════════════════════════
# TABLA MULTI-ESPECTRAL — SIN DATO
# ══════════════════════════════════════════════════════════════════════════════
ax_tbl = fig.add_subplot(gs[4])
sin_dato_panel(ax_tbl,
    '  ANÁLISIS MULTI-ESPECTRAL — Estado por Sector · Landsat + Sentinel-2',
    'Tabla por sector disponible cuando velez_sectores tenga campos\n'
    'temp_techo, insar_mm y ndvi por sector_id.')

# ══════════════════════════════════════════════════════════════════════════════
# ÍNDICE FARO — SCORE GLOBAL O SIN DATO
# ══════════════════════════════════════════════════════════════════════════════
ax_idx = fig.add_subplot(gs[5])
panel_ax(ax_idx, '  ÍNDICE FARO — Diagnóstico Integrado por Sector')
ax_idx.set_xlim(0,1); ax_idx.set_ylim(0,1); ax_idx.axis('off')

if score_sede is not None:
    ax_idx.text(0.5, 0.90, 'SCORE GLOBAL SEDE',
               color=WHITE, fontsize=10, fontweight='bold',
               ha='center', va='top', transform=ax_idx.transAxes)
    ax_idx.text(0.5, 0.62, str(score_sede),
               color=score_col, fontsize=40, fontweight='bold',
               ha='center', va='center', transform=ax_idx.transAxes,
               fontfamily='monospace')
    ax_idx.plot(0.5, 0.30, 'o', ms=14, color=score_col, transform=ax_idx.transAxes, zorder=4)
    ax_idx.plot(0.5, 0.30, 'o', ms=22, color=score_col, alpha=0.15,
               transform=ax_idx.transAxes, zorder=3)
    ax_idx.text(0.5, 0.12, '/100', color=WDIM, fontsize=9,
               ha='center', transform=ax_idx.transAxes, fontfamily='monospace')
    if detalle_sede:
        ax_idx.text(0.5, 0.04, detalle_sede, color=WDIM+'99', fontsize=8.5,
                   ha='center', va='bottom', transform=ax_idx.transAxes, fontfamily='monospace')
else:
    ax_idx.add_patch(FancyBboxPatch((0.10, 0.18), 0.80, 0.62,
        boxstyle='round,pad=0.02', transform=ax_idx.transAxes,
        facecolor=BG3, edgecolor=WDIM+'33', linewidth=1.0))
    ax_idx.text(0.5, 0.55, 'SIN DATO',
        transform=ax_idx.transAxes, color=WDIM, fontsize=20, fontweight='bold',
        ha='center', va='center', fontfamily='monospace')
    ax_idx.text(0.5, 0.33, 'Score de sede no disponible en el assembler.',
        transform=ax_idx.transAxes, color=WDIM+'88', fontsize=9,
        ha='center', va='center', fontfamily='monospace')

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
_REPORT_DIR = pathlib.Path(__file__).parents[4] / 'reportes_velez'
_REPORT_DIR.mkdir(exist_ok=True)
out = str(_out_path or (_REPORT_DIR / 'faro_reporte_velez_sede.png'))
plt.savefig(out, dpi=DPI, facecolor=BG, bbox_inches='tight', pad_inches=0.1)
plt.close()
print(f'Saved: {out}')
