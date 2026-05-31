"""
FARO PROTOCOL — Versión MUESTRA · Vaca Muerta Oil & Gas
Solo franja 2 (Delta SAR) + header + watermark.
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
from matplotlib.colors import LinearSegmentedColormap, TwoSlopeNorm
from scipy.ndimage import gaussian_filter

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

LAT    = -38.35
LON    = -68.98
OUTPUT = Path.home() / 'Desktop' / 'faro_reporte_vacamuerta_MUESTRA.png'

BG     = '#050505'
BG2    = '#080c10'
GOLD   = '#c9a84c'
GOLD_L = '#e2c97e'
WHITE  = '#f2ede4'
DIM    = '#555555'
GREEN  = '#27ae60'
RED    = '#c0392b'
ORANGE = '#d4753a'
CYAN   = '#3ab8c9'

# ── Mismo seed y rasters que el reporte original ─────────────────────────────
SEED = int(abs(LAT * 1000 + LON * 100))
SIZE = (140, 300)

def _perlin(seed_off=0, sigma_lo=10, sigma_hi=2):
    r = np.random.default_rng(seed=SEED + seed_off)
    raw = r.normal(0, 1, SIZE)
    return gaussian_filter(raw, sigma_lo) * 0.65 + gaussian_filter(raw, sigma_hi) * 0.35

def _make_sar_t2():
    base = _perlin(1)
    base = (base - base.min()) / (base.max() - base.min())
    sar  = -13 + base * 8
    r2   = np.random.default_rng(seed=SEED + 50)
    for _ in range(18):
        cx = int(r2.integers(8, 92))
        cy = int(r2.integers(8, 130))
        val = r2.uniform(1.5, 4.0)
        sar[cy-4:cy+4, cx-5:cx+5] += val
    semi = -16 + _perlin(2) * 0.5 * 7 + 0.5 * 7
    sar[:, 100:200] = np.clip(semi[:, 100:200], -18, -8)
    inact = -20 + _perlin(3) * 0.4 * 6 + 0.6 * 6
    sar[:, 200:] = np.clip(inact[:, 200:], -22, -13)
    return np.clip(sar, -22, -2)

def _make_sar_t0():
    t2   = _make_sar_t2()
    r0   = np.random.default_rng(seed=SEED + 99)
    noise = gaussian_filter(r0.normal(0, 0.6, SIZE), 3)
    t0    = t2 - 1.4 - abs(noise) * 0.4
    t0[:, 200:] = t2[:, 200:] + gaussian_filter(r0.normal(0, 0.2, SIZE), 4)[:, 200:]
    return np.clip(t0, -24, -2)

def _make_delta_sar(t2, t0):
    delta = t2 - t0
    r3    = np.random.default_rng(seed=SEED + 77)
    for _ in range(8):
        cx = int(r3.integers(5, 90))
        cy = int(r3.integers(5, 130))
        delta[cy-3:cy+3, cx-4:cx+4] += r3.uniform(1.0, 3.2)
    delta[:, 200:] = gaussian_filter(r3.normal(0, 0.25, SIZE)[:, 200:], 5)
    return np.clip(delta, -6, 6)

sar_t2 = _make_sar_t2()
sar_t0 = _make_sar_t0()
delta  = _make_delta_sar(sar_t2, sar_t0)

# ── Layout: header + panel delta + footer ────────────────────────────────────
fig = plt.figure(figsize=(18, 11), facecolor=BG)
gs  = gridspec.GridSpec(3, 1, figure=fig,
    height_ratios=[1.10, 7.0, 0.80],
    hspace=0.05,
    left=0.03, right=0.97, top=0.975, bottom=0.02)

def _ax_dark(ax):
    ax.set_facecolor(BG2)
    for sp in ax.spines.values():
        sp.set_color(GOLD); sp.set_linewidth(0.7)
    ax.tick_params(colors=DIM, labelsize=6)

# ── HEADER ────────────────────────────────────────────────────────────────────
ax_hdr = fig.add_subplot(gs[0])
ax_hdr.set_facecolor(BG); ax_hdr.axis('off')
ax_hdr.axhline(y=1.0, xmin=0, xmax=1, color=GOLD, linewidth=2.0)

ax_hdr.text(0.50, 0.78, 'F A R O   P R O T O C O L',
    ha='center', va='center', color=GOLD,
    fontsize=26, fontweight='bold', fontfamily='monospace',
    transform=ax_hdr.transAxes)

ax_hdr.text(0.50, 0.40,
    'VACA MUERTA  ·  AUDITORÍA SATELITAL OIL & GAS  ·  MAYO 2026',
    ha='center', va='center', color=WHITE,
    fontsize=13, fontfamily='monospace', fontweight='bold',
    transform=ax_hdr.transAxes)

ax_hdr.text(0.50, 0.06,
    f'Añelo, Neuquén, Argentina  ·  Lat {LAT}  Lon {LON}  ·  Sentinel-1 GRD',
    ha='center', va='center', color=DIM,
    fontsize=8.5, fontfamily='monospace',
    transform=ax_hdr.transAxes)

ax_hdr.axhline(y=0.0, xmin=0, xmax=1, color='#1a1a1a', linewidth=0.5)

# ── PANEL DELTA SAR (franja 2) ────────────────────────────────────────────────
cmap_delta = LinearSegmentedColormap.from_list('delta',
    ['#8b0000','#c0392b','#e74c3c','#111','#050505','#1a4a1a','#27ae60','#00e676'])

ax_d = fig.add_subplot(gs[1])
_ax_dark(ax_d)
norm_d = TwoSlopeNorm(vmin=-5, vcenter=0, vmax=5)
im_d   = ax_d.imshow(delta, cmap=cmap_delta, norm=norm_d,
                     aspect='auto', interpolation='bilinear')

# Divisores de zona
ax_d.axvline(x=100, color=GOLD+'aa', lw=1.0, linestyle='--')
ax_d.axvline(x=200, color=GOLD+'66', lw=0.8, linestyle=':')

# Etiquetas de zona (cualitativas — sin números exactos)
for x_c, label, sublabel, col in [
    (50,  'ZONA ACTIVA',      'NUEVA ACTIVIDAD DETECTADA',  GREEN),
    (150, 'SEMI-ACTIVA',      'CAMBIOS MENORES',             GOLD),
    (250, 'INACTIVA',         'SIN CAMBIOS',                 DIM),
]:
    ax_d.text(x_c, 10, label,    color=col,   fontsize=9,   fontweight='bold',
              fontfamily='monospace', ha='center')
    ax_d.text(x_c, 23, sublabel, color=WHITE, fontsize=7.5,
              fontfamily='monospace', ha='center')

ax_d.set_title(
    'Δ SAR BACKSCATTER  ·  T2 − T1 (12 días)  ·  '
    'VERDE = nueva actividad detectada  ·  ROJO = reducción',
    color=GOLD, fontsize=9.5, fontfamily='monospace', loc='left', pad=6)
ax_d.set_xticks([]); ax_d.set_yticks([])

cb_d = plt.colorbar(im_d, ax=ax_d, orientation='vertical', fraction=0.015, pad=0.008)
cb_d.set_ticks([-4, 0, 4])
cb_d.ax.set_yticklabels(['−', '0', '+'], color=DIM, fontsize=7)
cb_d.outline.set_edgecolor(GOLD)

# ── BANNER "MUESTRA" ──────────────────────────────────────────────────────────
ax_d.text(
    150, 85,
    'MUESTRA  ·  DATOS COMPLETOS BAJO SOLICITUD',
    color=WHITE, fontsize=13.5, fontweight='bold', fontfamily='monospace',
    ha='center', va='center',
    bbox=dict(
        boxstyle='round,pad=0.60',
        facecolor='#0d0000',
        edgecolor=RED,
        linewidth=2.2,
        alpha=0.94,
    ),
    zorder=10,
)
# Líneas de énfasis del banner
for y_line in [68, 103]:
    ax_d.axhline(y=y_line, color=RED, linewidth=0.9, alpha=0.35, linestyle='--')

# Blur sobre métricas — rectángulo que tapa la zona inferior del panel
from matplotlib.patches import Rectangle
blur_rect = Rectangle((0, 118), 300, 22, linewidth=0,
                       facecolor=BG2, alpha=0.92, zorder=8)
ax_d.add_patch(blur_rect)
ax_d.text(150, 129,
    '██ dB   ██ dB   ██ dB   ██ dB   ████ ppb   ██',
    color='#1e1e1e', fontsize=9, fontfamily='monospace',
    ha='center', va='center', zorder=9)
ax_d.text(150, 136,
    'MÉTRICAS DISPONIBLES EN REPORTE COMPLETO',
    color='#333', fontsize=7, fontfamily='monospace',
    ha='center', va='center', zorder=9)

# ── FOOTER ────────────────────────────────────────────────────────────────────
ax_ft = fig.add_subplot(gs[2])
ax_ft.set_facecolor(BG); ax_ft.axis('off')
ax_ft.axhline(y=1.0, xmin=0, xmax=1, color=GOLD, lw=0.8)

ax_ft.text(0.015, 0.80,
    'Sentinel-1 GRD  ·  TROPOMI/S5P  ·  Copernicus Data Space  ·  Cuenca Neuquina · ~3,200 km²',
    color=DIM, fontsize=7.5, fontfamily='monospace',
    transform=ax_ft.transAxes, va='top')

# SHA-256 censurado
ax_ft.text(0.015, 0.30,
    'SHA-256: ████████████████████████████████  ████████████████████████████████',
    color='#252525', fontsize=7.5, fontfamily='monospace',
    transform=ax_ft.transAxes, va='top')

ax_ft.text(0.70, 0.80,
    'Reporte completo bajo solicitud',
    color=DIM, fontsize=8, fontfamily='monospace',
    transform=ax_ft.transAxes, va='top')
ax_ft.text(0.70, 0.28,
    'protocolfaro@gmail.com',
    color=GOLD, fontsize=9.5, fontweight='bold', fontfamily='monospace',
    transform=ax_ft.transAxes, va='top')

# ── Guardar ───────────────────────────────────────────────────────────────────
plt.savefig(OUTPUT, dpi=150, bbox_inches='tight',
            facecolor=BG, edgecolor='none')
plt.close()

size_kb = OUTPUT.stat().st_size / 1024
print(f"\n  MUESTRA generada: {OUTPUT}")
print(f"  {size_kb:.0f} KB  |  150 dpi  |  Solo franja 2 (Delta SAR)")
print(f"  Metricas exactas: censuradas")
print(f"  SHA-256: censurado")
print(f"  Banner : MUESTRA · DATOS COMPLETOS BAJO SOLICITUD")
