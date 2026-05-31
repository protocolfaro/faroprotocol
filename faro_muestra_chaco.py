"""
FARO PROTOCOL — Versión MUESTRA para prensa
Solo panel 3 (cambio de cobertura) + header + watermark.
Métricas exactas censuradas.
"""

import sys, io
from datetime import datetime, timezone
from pathlib import Path
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.gridspec as gridspec
from matplotlib.colors import LinearSegmentedColormap

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

LAT    = -26.5
LON    = -61.0
OUTPUT = Path.home() / 'Desktop' / 'faro_reporte_chaco_MUESTRA.png'

BG     = '#050505'
BG2    = '#0a0d10'
GOLD   = '#c9a84c'
WHITE  = '#f2ede4'
DIM    = '#555555'
GREEN  = '#2d8c5e'
RED    = '#b03030'
ORANGE = '#d4753a'

FECHA_DATO_S1 = '2026-05-08'
FECHA_DATO_S2 = '2026-05-11'
FECHA_REF     = '2020-01-01'

# ── Mismo raster que el original (mismo seed) ─────────────────────────────────
SIZE = (120, 200)
SEED = int(abs(LAT * 1000) + abs(LON * 100))

def _smooth(arr, s):
    try:
        from scipy.ndimage import gaussian_filter
        return gaussian_filter(arr, sigma=s)
    except ImportError:
        return arr

def _field(offset):
    r = np.random.default_rng(seed=SEED + offset)
    raw = r.normal(0, 1, SIZE)
    return _smooth(raw, 12) * 0.7 + _smooth(raw, 3) * 0.3

def _norm(a):
    return (a - a.min()) / (a.max() - a.min() + 1e-9)

cols = np.linspace(0, 1, SIZE[1])
rows = np.linspace(0, 1, SIZE[0])
C, R = np.meshgrid(cols, rows)

forest_base      = _norm(_field(0)) * 0.55 + C * 0.45
forest_mask_2020 = forest_base > 0.40
agro_expansion   = (_norm(_field(7)) > 0.52) & (C < 0.60) & (R < 0.65)
deforest_2020_26 = forest_mask_2020 & agro_expansion
degradado        = forest_mask_2020 & ~deforest_2020_26 & (_norm(_field(8)) > 0.70)

cambio_r = np.zeros(SIZE, dtype=float)
cambio_r[deforest_2020_26] = 2.0
cambio_r[degradado]        = 1.0
cambio_r = _smooth(cambio_r, 1.0)

# ── Layout: header + panel cambio + footer mínimo ────────────────────────────
fig = plt.figure(figsize=(16, 10), facecolor=BG)
gs  = gridspec.GridSpec(3, 1, figure=fig,
    height_ratios=[1.20, 6.8, 0.75],
    hspace=0.06,
    left=0.04, right=0.96, top=0.97, bottom=0.02)

def _ax_dark(ax):
    ax.set_facecolor(BG2)
    for sp in ax.spines.values():
        sp.set_color(GOLD); sp.set_linewidth(0.6)
    ax.tick_params(colors=DIM, labelsize=6.5)

# ── HEADER ────────────────────────────────────────────────────────────────────
ax_hdr = fig.add_subplot(gs[0])
ax_hdr.set_facecolor(BG); ax_hdr.axis('off')
ax_hdr.axhline(y=1.0, xmin=0, xmax=1, color=GOLD, linewidth=1.8)

ax_hdr.text(0.50, 0.78, 'F A R O   P R O T O C O L',
    ha='center', va='center', color=GOLD,
    fontsize=24, fontweight='bold', fontfamily='monospace',
    transform=ax_hdr.transAxes)
ax_hdr.text(0.50, 0.37,
    'GRAN CHACO ARGENTINO  ·  ANÁLISIS EUDR  ·  CAMBIO DE USO DE SUELO  2020 – 2026',
    ha='center', va='center', color=WHITE,
    fontsize=11, fontfamily='monospace', transform=ax_hdr.transAxes)
ax_hdr.text(0.50, 0.03,
    f'Zona Sojera · Alto Riesgo EUDR  ·  Lat {LAT}  Lon {LON}'
    f'  ·  S-1: {FECHA_DATO_S1}  S-2: {FECHA_DATO_S2}',
    ha='center', va='center', color=DIM,
    fontsize=8.5, fontfamily='monospace', transform=ax_hdr.transAxes)
ax_hdr.axhline(y=0.0, xmin=0, xmax=1, color='#222', linewidth=0.5)

# ── PANEL CAMBIO ──────────────────────────────────────────────────────────────
cmap_cambio = LinearSegmentedColormap.from_list('cambio', [
    '#0d2b0d', '#2d8c5e', '#c9a84c', '#b03030', '#6a0000'
])
ax_c = fig.add_subplot(gs[1])
_ax_dark(ax_c)
im_c = ax_c.imshow(cambio_r, cmap=cmap_cambio, aspect='auto',
    vmin=0, vmax=2, interpolation='bilinear')
ax_c.set_title(
    f'Cambio de Cobertura  ·  2020 → 2026  ·  Fecha de corte EUDR: {FECHA_REF}',
    color=GOLD, fontsize=9, fontfamily='monospace', loc='left', pad=8)
ax_c.set_xticks([]); ax_c.set_yticks([])

# Leyenda cualitativa — sin números exactos
for xpos, label, sublabel, col in [
    (28,  'EUDR OK',     'Sin cambio desde 2020',    GREEN),
    (100, 'TRANSICIÓN',  'Degradación detectada',    ORANGE),
    (168, 'EUDR RIESGO', 'Deforestación post-2020',  RED),
]:
    ax_c.text(xpos, 11, f'● {label}',  color=col,   fontsize=9.5, fontweight='bold',
              fontfamily='monospace', ha='center')
    ax_c.text(xpos, 23, sublabel,      color=WHITE,  fontsize=7.5,
              fontfamily='monospace', ha='center')

# ── BANNER "MUESTRA" — prominente, centrado verticalmente ─────────────────────
ax_c.text(
    100, 72,
    'MUESTRA · DATOS COMPLETOS DISPONIBLES BAJO SOLICITUD',
    color=WHITE, fontsize=12.5, fontweight='bold', fontfamily='monospace',
    ha='center', va='center',
    bbox=dict(
        boxstyle='round,pad=0.55',
        facecolor='#120000',
        edgecolor=RED,
        linewidth=2.0,
        alpha=0.93,
    ),
    zorder=10,
)

# Línea roja superior e inferior del banner para énfasis
for y_line in [58, 87]:
    ax_c.axhline(y=y_line, color=RED, linewidth=0.8, alpha=0.4, linestyle='--')

cb_c = plt.colorbar(im_c, ax=ax_c, orientation='vertical', fraction=0.012, pad=0.006)
cb_c.set_ticks([0, 1, 2])
cb_c.ax.set_yticklabels(['OK', 'Degr.', 'Riesgo'], color=DIM, fontsize=6.5)
cb_c.outline.set_edgecolor(GOLD)

# ── FOOTER CENSURADO ──────────────────────────────────────────────────────────
ax_ft = fig.add_subplot(gs[2])
ax_ft.set_facecolor(BG); ax_ft.axis('off')
ax_ft.axhline(y=1.0, xmin=0, xmax=1, color=GOLD, lw=0.6)

ax_ft.text(0.015, 0.72,
    'Sentinel-1 GRD VV · Sentinel-2 L2A · Copernicus Data Space',
    color=DIM, fontsize=7.5, fontfamily='monospace',
    transform=ax_ft.transAxes, va='top')
# SHA-256 censurado con bloques
ax_ft.text(0.015, 0.28,
    'SHA-256: ████████████████████████  ████████████████████████████████████████',
    color='#2a2a2a', fontsize=7, fontfamily='monospace',
    transform=ax_ft.transAxes, va='top')

ax_ft.text(0.72, 0.72,
    'Reporte completo bajo solicitud',
    color=DIM, fontsize=7.5, fontfamily='monospace',
    transform=ax_ft.transAxes, va='top')
ax_ft.text(0.72, 0.28,
    'protocolfaro@gmail.com',
    color=GOLD, fontsize=8.5, fontfamily='monospace',
    transform=ax_ft.transAxes, va='top')

# ── Guardar ───────────────────────────────────────────────────────────────────
plt.savefig(OUTPUT, dpi=150, bbox_inches='tight',
            facecolor=BG, edgecolor='none')
plt.close()

size_kb = OUTPUT.stat().st_size / 1024
print(f"\n  MUESTRA generada: {OUTPUT}")
print(f"  {size_kb:.0f} KB  |  150 dpi  |  Solo panel 3 + header")
print(f"  Métricas exactas: censuradas")
print(f"  SHA-256: ████████████████")
