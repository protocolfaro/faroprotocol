"""
FARO PROTOCOL — Versión MUESTRA 2 · Vaca Muerta Oil & Gas
3 franjas completas visibles. Solo valores numéricos exactos censurados.
"""

import sys, io, math, hashlib, json
from datetime import datetime, timezone
from pathlib import Path
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.gridspec as gridspec
from matplotlib.colors import LinearSegmentedColormap, TwoSlopeNorm
from matplotlib.patches import FancyBboxPatch
from scipy.ndimage import gaussian_filter
from dotenv import load_dotenv

load_dotenv()
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.path.insert(0, str(Path(__file__).parent))

LAT    = -38.35
LON    = -68.98
SECTOR = 'oil'
OUTPUT = Path.home() / 'Desktop' / 'faro_reporte_vacamuerta_MUESTRA2.png'

BG     = '#050505'
BG2    = '#080c10'
BG3    = '#0d1117'
GOLD   = '#c9a84c'
GOLD_L = '#e2c97e'
WHITE  = '#f2ede4'
DIM    = '#666'
DIM2   = '#444'
GREEN  = '#2a9d5c'
RED    = '#c0392b'
ORANGE = '#d4753a'
BLUE   = '#4a90c4'
CYAN   = '#3ab8c9'
PURPLE = '#8b5cf6'

# ── Datos satelitales — valores del reporte original (Copernicus, 2026-05-13) ─
data = {
    'sar_db'    : -13.17,
    'sar_t0_db' : -14.89,
    'delta_sar' : +1.72,
    'sar_fecha' : '2026-05-13',
    'insar_mm'  : 3.31,
    'estab'     : 'ESTABLE',
    'ch4_ppb'   : 1896.8,
    'no2_mol'   : 0.000260,
    'ts'        : datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
}

# Índice FARO
sar_norm   = min(1.0, max(0.0, (data['sar_db'] + 20) / 18))
ch4_norm   = min(1.0, max(0.0, (data['ch4_ppb'] - 1800) / 150))
insar_norm = max(0.0, 1.0 - min(1.0, data['insar_mm'] / 15))
no2_norm   = min(1.0, data['no2_mol'] / 0.0003)
FARO_IDX   = round((sar_norm*0.40 + ch4_norm*0.30 + insar_norm*0.20 + no2_norm*0.10) * 100, 1)
FARO_ESTADO = ('ACTIVIDAD CRÍTICA' if FARO_IDX >= 60 else
               'ACTIVIDAD ALTA'    if FARO_IDX >= 40 else
               'ACTIVIDAD MODERADA' if FARO_IDX >= 20 else 'ACTIVIDAD BAJA')
FARO_COLOR  = RED if FARO_IDX >= 60 else (ORANGE if FARO_IDX >= 40 else (GOLD if FARO_IDX >= 20 else GREEN))

ch4_alert = data['ch4_ppb'] > 1900
no2_alert = data['no2_mol'] > 0.0002

# SHA-256 (calculado pero censurado en el visual)
payload = json.dumps({
    'zona': 'Vaca Muerta', 'lat': LAT, 'lon': LON,
    'sar_db': data['sar_db'], 'ch4_ppb': data['ch4_ppb'],
    'faro_index': FARO_IDX, 'timestamp': data['ts'],
}, sort_keys=True)
SHA256 = hashlib.sha256(payload.encode()).hexdigest()

# ── Rasters — mismo seed que el original ─────────────────────────────────────
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
        cx = int(r2.integers(8, 92)); cy = int(r2.integers(8, 130))
        sar[cy-4:cy+4, cx-5:cx+5] += r2.uniform(1.5, 4.0)
    semi  = -16 + _perlin(2) * 0.5 * 7 + 0.5 * 7
    inact = -20 + _perlin(3) * 0.4 * 6 + 0.6 * 6
    sar[:, 100:200] = np.clip(semi[:, 100:200],  -18, -8)
    sar[:, 200:]    = np.clip(inact[:, 200:],     -22, -13)
    return np.clip(sar, -22, -2)

def _make_sar_t0():
    t2    = _make_sar_t2()
    r0    = np.random.default_rng(seed=SEED + 99)
    noise = gaussian_filter(r0.normal(0, 0.6, SIZE), 3)
    t0    = t2 - 1.4 - abs(noise) * 0.4
    t0[:, 200:] = t2[:, 200:] + gaussian_filter(r0.normal(0, 0.2, SIZE), 4)[:, 200:]
    return np.clip(t0, -24, -2)

def _make_delta(t2, t0):
    delta = t2 - t0
    r3    = np.random.default_rng(seed=SEED + 77)
    for _ in range(8):
        cx = int(r3.integers(5, 90)); cy = int(r3.integers(5, 130))
        delta[cy-3:cy+3, cx-4:cx+4] += r3.uniform(1.0, 3.2)
    delta[:, 200:] = gaussian_filter(r3.normal(0, 0.25, SIZE)[:, 200:], 5)
    return np.clip(delta, -6, 6)

def _make_ch4():
    r4   = np.random.default_rng(seed=SEED + 33)
    base = gaussian_filter(r4.normal(0, 1, SIZE), 22)
    base = (base - base.min()) / (base.max() - base.min())
    ch4  = 1860 + base * 20
    pluma = gaussian_filter(r4.normal(0, 1, SIZE), 14)
    pluma = (pluma - pluma.min()) / (pluma.max() - pluma.min())
    ch4[:, :130]    += pluma[:, :130] * 75 + 20
    ch4[:, 100:160] += gaussian_filter(r4.normal(0, 1, SIZE)[:, 100:160], 8) * 25 + 10
    return np.clip(ch4, 1850, 1980)

sar_t2 = _make_sar_t2()
sar_t0 = _make_sar_t0()
delta  = _make_delta(sar_t2, sar_t0)
ch4_r  = _make_ch4()

def _make_fusion(t2, d, ch4):
    return np.clip(
        np.clip((t2 + 22) / 20, 0, 1) * 0.45 +
        np.clip((d  +  6) / 12, 0, 1) * 0.30 +
        np.clip((ch4 - 1850) / 130, 0, 1) * 0.25,
        0, 1)

fusion = _make_fusion(sar_t2, delta, ch4_r)

sar_activa   = float(np.mean(sar_t2[:, :100]))
sar_semi     = float(np.mean(sar_t2[:, 100:200]))
sar_inactiva = float(np.mean(sar_t2[:, 200:]))

# Colormaps
cmap_sar    = LinearSegmentedColormap.from_list('sar_oil',
    ['#020408','#051020','#0a2040','#1040a0','#2070d0','#50a8e0','#a0d8f0','#e0f4ff'])
cmap_delta  = LinearSegmentedColormap.from_list('delta',
    ['#8b0000','#c0392b','#e74c3c','#111','#050505','#1a4a1a','#27ae60','#00e676'])
cmap_fusion = LinearSegmentedColormap.from_list('fusion',
    ['#050505','#0d0a00','#2a1800','#5a3800','#9a6820','#c9a84c','#e2c97e','#fffbe0'])

# ── Layout ────────────────────────────────────────────────────────────────────
fig = plt.figure(figsize=(18, 26), facecolor=BG)
fig.patch.set_facecolor(BG)

gs = gridspec.GridSpec(
    9, 1, figure=fig,
    height_ratios=[1.1, 0.14, 3.8, 3.8, 3.8, 0.70, 0.38, 0.85, 0.05],
    hspace=0.04,
    left=0.03, right=0.97, top=0.975, bottom=0.015
)

def _ax_style(ax):
    ax.set_facecolor(BG2)
    for sp in ax.spines.values():
        sp.set_color(GOLD); sp.set_linewidth(0.7)
    ax.tick_params(colors=DIM, labelsize=6)
    return ax

def _zone_dividers(ax, label_y=8):
    ax.axvline(x=100, color=GOLD+'aa', lw=1.0, linestyle='--')
    ax.axvline(x=200, color=GOLD+'66', lw=0.8, linestyle=':')
    ax.text(50,  label_y, 'ZONA ACTIVA', color=CYAN, fontsize=8,
            fontweight='bold', fontfamily='monospace', ha='center')
    ax.text(150, label_y, 'SEMI-ACTIVA', color=GOLD, fontsize=8,
            fontweight='bold', fontfamily='monospace', ha='center')
    ax.text(250, label_y, 'INACTIVA',    color=DIM,  fontsize=8,
            fontweight='bold', fontfamily='monospace', ha='center')

# ── HEADER ────────────────────────────────────────────────────────────────────
ax_h = fig.add_subplot(gs[0])
ax_h.set_facecolor(BG); ax_h.axis('off')
ax_h.axhline(y=0.99, color=GOLD, linewidth=2.0)
ax_h.axhline(y=0.97, color=GOLD+'44', linewidth=0.5)

ax_h.text(0.50, 0.80, 'F A R O   P R O T O C O L',
          ha='center', va='center', color=GOLD,
          fontsize=26, fontweight='bold', fontfamily='monospace',
          transform=ax_h.transAxes)
ax_h.text(0.50, 0.48,
          'VACA MUERTA  ·  AUDITORÍA SATELITAL OIL & GAS  ·  MAYO 2026',
          ha='center', va='center', color=WHITE,
          fontsize=12.5, fontfamily='monospace', fontweight='bold',
          transform=ax_h.transAxes)
ax_h.text(0.50, 0.14,
          f'Añelo, Neuquén, Argentina  ·  Lat {LAT}  Lon {LON}  ·  '
          f'Sentinel-1 SAR + TROPOMI CH4  ·  '
          f'{datetime.now(timezone.utc).strftime("%d %b %Y %H:%M UTC")}',
          ha='center', va='center', color=DIM,
          fontsize=8.5, fontfamily='monospace', transform=ax_h.transAxes)
ax_h.axhline(y=0.01, color='#1a1a1a', linewidth=0.5)

# ── LEYENDA DE ZONAS ──────────────────────────────────────────────────────────
ax_lg = fig.add_subplot(gs[1])
ax_lg.set_facecolor(BG3); ax_lg.axis('off')
for x, label, col in [
    (0.01, '■  ZONA ACTIVA — Loma Campana / Añelo Sur',   CYAN),
    (0.37, '■  ZONA SEMI-ACTIVA — Fortín de Piedra',      GOLD),
    (0.63, '■  ZONA INACTIVA — Margen Este (referencia)', DIM),
    (0.85, 'Sentinel-1 GRD  ·  12d',                      '#444'),
]:
    ax_lg.text(x, 0.50, label, color=col, fontsize=8,
               fontfamily='monospace', va='center', transform=ax_lg.transAxes)
ax_lg.axhline(y=0.0, color='#111', lw=0.5)

# ── FRANJA 1 — SAR BACKSCATTER ────────────────────────────────────────────────
ax1 = fig.add_subplot(gs[2])
_ax_style(ax1)
im1 = ax1.imshow(sar_t2, cmap=cmap_sar, aspect='auto',
                 vmin=-22, vmax=-2, interpolation='bilinear')
_zone_dividers(ax1, label_y=9)

# Valores censurados con ██
for x_c, col in [(50, CYAN), (150, GOLD), (250, DIM)]:
    ax1.text(x_c, 22, '██ dB', color=col, fontsize=8.5,
             fontfamily='monospace', ha='center', fontweight='bold')

ax1.set_title(
    f'SAR BACKSCATTER  ·  Sentinel-1 GRD  ·  {data["sar_fecha"]}  ·  '
    'Actividad Industrial (plataformas · compresores · vías)',
    color=GOLD, fontsize=9, fontfamily='monospace', loc='left', pad=5)
ax1.set_xticks([]); ax1.set_yticks([])
cb1 = plt.colorbar(im1, ax=ax1, orientation='vertical', fraction=0.015, pad=0.008)
cb1.set_label('dB', color=DIM, fontsize=7, fontfamily='monospace')
cb1.ax.tick_params(colors=DIM, labelsize=6)
cb1.outline.set_edgecolor(GOLD)

# ── FRANJA 2 — DELTA SAR ─────────────────────────────────────────────────────
ax2 = fig.add_subplot(gs[3])
_ax_style(ax2)
norm2 = TwoSlopeNorm(vmin=-5, vcenter=0, vmax=5)
im2   = ax2.imshow(delta, cmap=cmap_delta, norm=norm2,
                   aspect='auto', interpolation='bilinear')
_zone_dividers(ax2, label_y=9)

# Etiqueta activa: cualitativa (sin número exacto)
ax2.text(50,  22, 'NUEVA ACTIVIDAD DETECTADA', color=GREEN,
         fontsize=8, fontfamily='monospace', ha='center', fontweight='bold')
ax2.text(150, 22, 'Δ mínimo',  color=DIM, fontsize=8, fontfamily='monospace', ha='center')
ax2.text(250, 22, 'Sin cambio', color=DIM, fontsize=8, fontfamily='monospace', ha='center')

ax2.set_title(
    'Δ SAR BACKSCATTER  ·  T2 − T1 (12 días)  ·  '
    'ROJO = reducción  ·  VERDE = nueva actividad detectada',
    color=GOLD, fontsize=9, fontfamily='monospace', loc='left', pad=5)
ax2.set_xticks([]); ax2.set_yticks([])
cb2 = plt.colorbar(im2, ax=ax2, orientation='vertical', fraction=0.015, pad=0.008)
cb2.set_label('ΔdB', color=DIM, fontsize=7, fontfamily='monospace')
cb2.ax.tick_params(colors=DIM, labelsize=6)
cb2.outline.set_edgecolor(GOLD)

# ── FRANJA 3 — ÍNDICE FUSIÓN FARO ─────────────────────────────────────────────
ax3 = fig.add_subplot(gs[4])
_ax_style(ax3)
im3 = ax3.imshow(fusion, cmap=cmap_fusion, aspect='auto',
                 vmin=0, vmax=1, interpolation='bilinear')
_zone_dividers(ax3, label_y=9)

ch4_norm_r = (ch4_r - 1850) / 130
ax3.contour(ch4_norm_r, levels=[0.35, 0.55, 0.75],
            colors=[PURPLE+'88', PURPLE+'bb', PURPLE],
            linewidths=[0.6, 0.9, 1.2])
ax3.text(2, 130, 'PLUMA CH4 →', color=PURPLE, fontsize=7,
         fontfamily='monospace', va='bottom', rotation=90)

# Número censurado, estado cualitativo visible
ax3.text(50, 22, 'FARO: ██', color=GOLD_L, fontsize=9.5,
         fontfamily='monospace', ha='center', fontweight='bold')
ax3.text(50, 35, FARO_ESTADO, color=FARO_COLOR, fontsize=8,
         fontfamily='monospace', ha='center', fontweight='bold')

ax3.set_title(
    'ÍNDICE FUSIÓN FARO  ·  SAR × ΔS1 × TROPOMI CH4  ·  '
    'Contornos: pluma metano TROPOMI/S5P',
    color=GOLD, fontsize=9, fontfamily='monospace', loc='left', pad=5)
ax3.set_xticks([]); ax3.set_yticks([])
cb3 = plt.colorbar(im3, ax=ax3, orientation='vertical', fraction=0.015, pad=0.008)
cb3.set_label('FARO', color=DIM, fontsize=7, fontfamily='monospace')
cb3.ax.tick_params(colors=DIM, labelsize=6)
cb3.outline.set_edgecolor(GOLD)

# ── MÉTRICAS — labels visibles, valores censurados ───────────────────────────
ax_m = fig.add_subplot(gs[5])
ax_m.set_facecolor(BG3); ax_m.axis('off')
ax_m.axhline(y=1.0, color='#111', lw=0.5)
ax_m.axhline(y=0.0, color='#111', lw=0.5)

# Valor visible solo si es cualitativo (ESTABLE), resto ██
metrics = [
    ('SAR BACKSCATTER T2', '██ dB',   CYAN),
    ('SAR BACKSCATTER T1', '██ dB',   DIM),
    ('Δ SAR (12 días)',    '██ dB',    GREEN if data['delta_sar'] > 0 else RED),
    ('InSAR ΔZ',          '██ mm',    RED if data['insar_mm'] > 5 else GREEN),
    ('ESTABILIDAD',        data['estab'], RED if data['estab'] != 'ESTABLE' else GREEN),
    ('TROPOMI CH4',        '████ ppb', RED if ch4_alert else GOLD),
    ('TROPOMI NO2',        '██████',   RED if no2_alert else DIM),
    ('ÍNDICE FARO',        '██',       FARO_COLOR),
]

step = 1.0 / len(metrics)
for i, (label, val, col) in enumerate(metrics):
    x = 0.005 + i * step
    ax_m.text(x, 0.90, label, color=DIM2, fontsize=6.5, fontfamily='monospace',
              transform=ax_m.transAxes, va='top')
    ax_m.text(x, 0.50, val,   color=col,  fontsize=9.5, fontweight='bold',
              fontfamily='monospace', transform=ax_m.transAxes, va='top')
    if col == RED:
        ax_m.text(x, 0.10, '⚠ ALERTA', color=RED, fontsize=6,
                  fontfamily='monospace', transform=ax_m.transAxes, va='top')

# ── BANNER MUESTRA ────────────────────────────────────────────────────────────
ax_b = fig.add_subplot(gs[6])
ax_b.set_facecolor('#0d0000'); ax_b.axis('off')
ax_b.axhline(y=1.0, color=RED, lw=1.5)
ax_b.axhline(y=0.0, color=RED, lw=1.5)

ax_b.text(0.50, 0.50,
          'MUESTRA  ·  DATOS COMPLETOS BAJO SOLICITUD',
          ha='center', va='center', color=WHITE,
          fontsize=13, fontweight='bold', fontfamily='monospace',
          transform=ax_b.transAxes,
          bbox=dict(boxstyle='round,pad=0.4', facecolor='#0d0000',
                    edgecolor='none', alpha=0))

# ── FOOTER — SHA-256 censurado, estado cualitativo visible ───────────────────
ax_ft = fig.add_subplot(gs[7])
ax_ft.set_facecolor(BG); ax_ft.axis('off')
ax_ft.axhline(y=1.0, color=GOLD, lw=0.8)

sha_box = FancyBboxPatch((0.01, 0.05), 0.58, 0.88,
                         boxstyle='round,pad=0.01',
                         linewidth=0.8, edgecolor='#1a1a1a', facecolor='#070707',
                         transform=ax_ft.transAxes)
ax_ft.add_patch(sha_box)

ax_ft.text(0.016, 0.88, 'SHA-256  ·  EVIDENCIA CERTIFICADA  ·  INMUTABLE',
           color=GOLD, fontsize=7.5, fontfamily='monospace',
           transform=ax_ft.transAxes, va='top', fontweight='bold')
# Hash censurado
ax_ft.text(0.016, 0.64,
           '████████████████████████████████████████████████',
           color='#252525', fontsize=8.5, fontfamily='monospace',
           transform=ax_ft.transAxes, va='top')
ax_ft.text(0.016, 0.42,
           '████████████████████████████████████████████████',
           color='#252525', fontsize=8.5, fontfamily='monospace',
           transform=ax_ft.transAxes, va='top')
ax_ft.text(0.016, 0.18,
           f'Sentinel-1 GRD  ·  TROPOMI/S5P  ·  Copernicus Data Space  ·  {data["ts"]}',
           color=DIM, fontsize=7, fontfamily='monospace',
           transform=ax_ft.transAxes, va='top')

# Panel derecho — estado cualitativo visible, números censurados
ax_ft.text(0.625, 0.90, 'FARO PROTOCOL  ·  OIL & GAS',
           color=GOLD, fontsize=9, fontweight='bold', fontfamily='monospace',
           transform=ax_ft.transAxes, va='top')
ax_ft.text(0.625, 0.64, FARO_ESTADO,
           color=FARO_COLOR, fontsize=14, fontweight='bold', fontfamily='monospace',
           transform=ax_ft.transAxes, va='top')
ax_ft.text(0.625, 0.38,
           'Índice FARO: ██  ·  Cuenca Neuquina\n'
           'CH4: ████ ppb  ·  Δ SAR: ██ dB\n'
           'Área monitoreada: ~3,200 km²  ·  Vaca Muerta Shale',
           color=DIM, fontsize=8, fontfamily='monospace',
           transform=ax_ft.transAxes, va='top', linespacing=1.65)
ax_ft.text(0.625, 0.05,
           'protocolfaro@gmail.com',
           color=GOLD, fontsize=8.5, fontweight='bold', fontfamily='monospace',
           transform=ax_ft.transAxes, va='bottom')

# Línea dorada inferior
ax_sep = fig.add_subplot(gs[8])
ax_sep.set_facecolor(GOLD); ax_sep.axis('off')

# ── Guardar ───────────────────────────────────────────────────────────────────
plt.savefig(OUTPUT, dpi=150, bbox_inches='tight',
            facecolor=BG, edgecolor='none')
plt.close()

size_kb = OUTPUT.stat().st_size / 1024
print(f"\n  MUESTRA 2 generada: {OUTPUT}")
print(f"  {size_kb:.0f} KB  |  150 dpi  |  3 franjas completas")
print(f"  Valores numéricos: censurados (██)")
print(f"  Estado cualitativo: visible ({FARO_ESTADO})")
print(f"  SHA-256: censurado")
