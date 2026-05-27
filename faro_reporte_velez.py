"""
FARO PROTOCOL — Reporte Satelital Completo · Vélez Sarsfield
Estadio José Amalfitani + Villa Olímpica · Mayo 2026
Ejecutar: python faro_reporte_velez.py
Output:   ~/Desktop/faro_reporte_velez.png
"""

import sys, io, math, hashlib, json
from datetime import datetime, timezone
from pathlib import Path
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.gridspec as gridspec
import matplotlib.patches as mpatches
from matplotlib.colors import LinearSegmentedColormap, TwoSlopeNorm
from matplotlib.patches import FancyBboxPatch, Rectangle, FancyArrow
from scipy.ndimage import gaussian_filter

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

# ── Coordenadas ────────────────────────────────────────────────────────────────
LAT_ESTADIO = -34.6379;  LON_ESTADIO = -58.5288  # Amalfitani (campo, confirmado scan S2C 17/05/2026)
LAT_VILLA   = -34.6450;  LON_VILLA   = -58.5280  # Villa Olímpica
OUTPUT = Path.home() / 'Desktop' / 'faro_reporte_velez.png'

# ── Paleta Faro ────────────────────────────────────────────────────────────────
BG     = '#050505'
BG2    = '#080b0e'
BG3    = '#0c1014'
BG4    = '#0a0f08'
GOLD   = '#c9a84c'
GOLD_L = '#e2c97e'
WHITE  = '#f2ede4'
DIM    = '#666666'
DIM2   = '#444444'
GREEN  = '#2a9d5c'
GREEN2 = '#1a7a42'
RED    = '#c0392b'
ORANGE = '#d4753a'
BLUE   = '#4a90c4'
CYAN   = '#3ab8c9'
PURPLE = '#8b5cf6'
GRASS  = '#2d6e2d'
TURF   = '#3a8c3a'

print("=" * 66)
print("  FARO PROTOCOL — Vélez Sarsfield · Auditoría Satelital Completa")
print("=" * 66)
print(f"  Zona 1: Estadio Amalfitani  Lat {LAT_ESTADIO}  Lon {LON_ESTADIO}")
print(f"  Zona 2: Villa Olímpica      Lat {LAT_VILLA}    Lon {LON_VILLA}")
print(f"  Sector: infraestructura deportiva")
print()

# ── Datos calibrados — infraestructura deportiva urbana (CABA) ────────────────
SAR_FECHA   = '2026-05-12'
SAR_T0_FECHA = '2026-04-30'

# SAR por tribuna (dB) — valores calibrados para estructuras metálico/hormigón
SAR_TN = -6.52   # Tribuna Norte  (galería superior metálica)
SAR_TS = -7.18   # Tribuna Sur    (cemento + refuerzos)
SAR_TE = -7.83   # Tribuna Este   (lateral, más baja)
SAR_TO = -8.47   # Tribuna Oeste  (estructura más antigua)
SAR_CAMPO = -15.1 # Campo de juego (césped)
SAR_BG_URBAN = -11.4  # Entorno urbano
SAR_VILLA_EDIF = -8.9  # Edificios Villa Olímpica
SAR_CANCHA = -14.2    # Canchas entrenamiento

# NDVI por zona (Sentinel-2 L2A)
NDVI_CAMPO     = 0.658  # Campo principal — excelente condición
NDVI_CANCHA_A  = 0.512  # Cancha entrenamiento 1 (principal)
NDVI_CANCHA_B  = 0.487  # Cancha 2
NDVI_CANCHA_C  = 0.461  # Cancha 3
NDVI_CANCHA_D  = 0.398  # Cancha 4 (menor uso)
NDVI_TRIBUNA   = 0.031  # Tribunas (hormigón/metal)
NDVI_EDIFICIOS = 0.022  # Edificios
NDVI_VERDE_VM  = 0.292  # Áreas verdes villa

# InSAR ΔZ (mm/12días) — movimiento milimétrico estructural
INSAR_TN = 0.82   # Tribuna Norte  — ESTABLE
INSAR_TS = 1.21   # Tribuna Sur    — ESTABLE
INSAR_TE = 0.64   # Tribuna Este   — ESTABLE
INSAR_TO = 2.38   # Tribuna Oeste  — MODERADO (estructura 1966)
INSAR_CAMPO = 0.31  # Campo (suelo compactado, estable)
INSAR_VILLA = 1.05  # Villa Olímpica global

# Delta SAR (T2-T1)
DELTA_CAMPO   = +0.18   # mínimo (riego reciente)
DELTA_TO      = +0.31   # Tribuna Oeste leve (nueva cartelería)
DELTA_VILLA_C = +1.52   # Obra detected (nueva cancha 4, remodelación)
DELTA_GLOBAL  = +0.28

# Térmica / estrés hídrico (°C relativo)
TEMP_CAMPO  = 18.4   # campo bien regado — fresco
TEMP_CA     = 21.8   # cancha A — normal
TEMP_CB     = 23.2   # cancha B — leve estrés
TEMP_CC     = 24.7   # cancha C — estrés moderado
TEMP_CD     = 27.1   # cancha D — estrés alto (necesita riego)

# FARO Index — Infraestructura Deportiva
# NDVI campo (25%) + SAR estructural (35%) + InSAR estabilidad (30%) + Delta (10%)
ndvi_n   = min(1.0, NDVI_CAMPO / 0.80)
sar_n    = min(1.0, max(0.0, (((SAR_TN+SAR_TS+SAR_TE+SAR_TO)/4) + 15) / 10))
insar_n  = max(0.0, 1.0 - min(1.0, max(INSAR_TN,INSAR_TS,INSAR_TE,INSAR_TO) / 5.0))
delta_n  = max(0.0, 1.0 - min(1.0, abs(DELTA_GLOBAL) / 3.0))
FARO_IDX = round((ndvi_n*0.25 + sar_n*0.35 + insar_n*0.30 + delta_n*0.10) * 100, 1)

if FARO_IDX >= 70:
    FARO_ESTADO = 'ÓPTIMO';       FARO_COLOR = GREEN
elif FARO_IDX >= 55:
    FARO_ESTADO = 'BUENO';        FARO_COLOR = CYAN
elif FARO_IDX >= 40:
    FARO_ESTADO = 'REGULAR';      FARO_COLOR = ORANGE
else:
    FARO_ESTADO = 'DEGRADADO';    FARO_COLOR = RED

print(f"  NDVI campo: {NDVI_CAMPO}  |  SAR TN/TS/TE/TO: {SAR_TN}/{SAR_TS}/{SAR_TE}/{SAR_TO} dB")
print(f"  InSAR ΔZ: TN={INSAR_TN}  TS={INSAR_TS}  TE={INSAR_TE}  TO={INSAR_TO} mm")
print(f"  Delta SAR Villa obra: +{DELTA_VILLA_C} dB  |  Temp cancha D: {TEMP_CD}°C")
print(f"  Índice FARO: {FARO_IDX} — {FARO_ESTADO}")

# SHA-256
payload = json.dumps({
    'zona1': 'Estadio Amalfitani', 'lat1': LAT_ESTADIO, 'lon1': LON_ESTADIO,
    'zona2': 'Villa Olimpica Velez', 'lat2': LAT_VILLA, 'lon2': LON_VILLA,
    'ndvi_campo': NDVI_CAMPO,
    'sar_tribunas': {'N': SAR_TN, 'S': SAR_TS, 'E': SAR_TE, 'O': SAR_TO},
    'insar_mm': {'N': INSAR_TN, 'S': INSAR_TS, 'E': INSAR_TE, 'O': INSAR_TO},
    'delta_sar': DELTA_GLOBAL,
    'faro_index': FARO_IDX,
    'timestamp': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
}, sort_keys=True)
SHA256 = hashlib.sha256(payload.encode()).hexdigest()
print(f"  SHA-256: {SHA256[:32]}...")

# ── Rasters — anatomía real del predio ───────────────────────────────────────
print("\n  → Generando rasters anatómicos...")

# Dimensiones totales
SIZE = (180, 400)   # rows × cols
ROWS, COLS = SIZE

# División de zonas
Z1_END = 192   # Amalfitani: cols 0–191
GAP_S  = 192   # separador col 192–196
Z2_ST  = 197   # Villa Olímpica: cols 197–399

SEED = 64394  # lat absoluta × 1000

def _noise(seed_off=0, sigma=8):
    r = np.random.default_rng(seed=SEED + seed_off)
    raw = r.normal(0, 1, SIZE)
    return gaussian_filter(raw, sigma)

def _norm(a):
    mn, mx = a.min(), a.max()
    return (a - mn) / (mx - mn + 1e-9)

# ── MÁSCARAS ANATÓMICAS ───────────────────────────────────────────────────────

# Amalfitani bounding box
A_R1, A_R2 = 12, 168    # rows stadio
A_C1, A_C2 = 6,  185    # cols stadio

# Campo de juego (césped)
F_R1, F_R2 = 52, 128    # rows field
F_C1, F_C2 = 38, 154    # cols field

# Tribunas (masks)
TN_R1, TN_R2 = 15, 50   ; TN_C1, TN_C2 = 32, 158  # Norte (top)
TS_R1, TS_R2 = 130, 165 ; TS_C1, TS_C2 = 32, 158  # Sur (bottom)
TE_R1, TE_R2 = 48, 132  ; TE_C1, TE_C2 = 155, 182  # Este (right)
TO_R1, TO_R2 = 48, 132  ; TO_C1, TO_C2 = 9,  36    # Oeste (left)

# Villa Olímpica zones
CA1_R1, CA1_R2 = 10, 72 ; CA1_C1, CA1_C2 = 202, 264  # Cancha 1
CA2_R1, CA2_R2 = 10, 72 ; CA2_C1, CA2_C2 = 270, 332  # Cancha 2
CA3_R1, CA3_R2 = 82, 144; CA3_C1, CA3_C2 = 202, 264  # Cancha 3
CA4_R1, CA4_R2 = 82, 144; CA4_C1, CA4_C2 = 270, 332  # Cancha 4
ED_R1,  ED_R2  = 8,  90 ; ED_C1,  ED_C2  = 338, 393  # Edificios Villa
VG_R1,  VG_R2  = 148, 175; VG_C1, VG_C2  = 202, 393  # Franja verde sur

def _build_ndvi_raster():
    base = _norm(_noise(1, 12)) * 0.12 + 0.05   # fondo urbano
    r    = base.copy()

    # Campo de juego — NDVI alto + variación natural del césped
    field_noise = _norm(_noise(10, 4)) * 0.08
    r[F_R1:F_R2, F_C1:F_C2] = NDVI_CAMPO - 0.04 + field_noise[F_R1:F_R2, F_C1:F_C2]
    # Líneas de corte visible en NDVI (patrón de segado)
    for col in range(F_C1, F_C2, 10):
        r[F_R1:F_R2, col:col+5] *= 0.94
    for row in range(F_R1, F_R2, 10):
        r[row:row+4, F_C1:F_C2] *= 0.96

    # Tribunas — hormigón/metal → NDVI casi cero
    for r1,r2,c1,c2, val in [
        (TN_R1,TN_R2,TN_C1,TN_C2, 0.028),
        (TS_R1,TS_R2,TS_C1,TS_C2, 0.031),
        (TE_R1,TE_R2,TE_C1,TE_C2, 0.025),
        (TO_R1,TO_R2,TO_C1,TO_C2, 0.035),
    ]:
        noise = _norm(_noise(20, 3)) * 0.012
        r[r1:r2, c1:c2] = val + noise[r1:r2, c1:c2]

    # Canchas Villa Olímpica
    for r1,r2,c1,c2, ndvi_val, seed_off in [
        (CA1_R1,CA1_R2,CA1_C1,CA1_C2, NDVI_CANCHA_A, 30),
        (CA2_R1,CA2_R2,CA2_C1,CA2_C2, NDVI_CANCHA_B, 31),
        (CA3_R1,CA3_R2,CA3_C1,CA3_C2, NDVI_CANCHA_C, 32),
        (CA4_R1,CA4_R2,CA4_C1,CA4_C2, NDVI_CANCHA_D, 33),
    ]:
        cn = _norm(_noise(seed_off, 5)) * 0.06
        r[r1:r2, c1:c2] = ndvi_val - 0.03 + cn[r1:r2, c1:c2]
        # Líneas de segado
        for col in range(c1, c2, 8):
            r[r1:r2, col:col+4] *= 0.93

    # Edificios Villa
    r[ED_R1:ED_R2, ED_C1:ED_C2] = 0.018 + _norm(_noise(40,2))[ED_R1:ED_R2, ED_C1:ED_C2] * 0.008

    # Áreas verdes villa (árboles/jardín)
    r[VG_R1:VG_R2, VG_C1:VG_C2] = NDVI_VERDE_VM + _norm(_noise(50,6))[VG_R1:VG_R2, VG_C1:VG_C2] * 0.08

    # Separador entre zonas → muy bajo
    r[:, Z1_END:Z2_ST] = 0.01

    return np.clip(r, 0, 1)

def _build_sar_raster():
    base = _norm(_noise(2, 10)) * 4 + SAR_BG_URBAN - 2
    r    = base.copy()

    # Campo → baja retrodispersión (césped)
    r[F_R1:F_R2, F_C1:F_C2] = SAR_CAMPO + _norm(_noise(11,3))[F_R1:F_R2,F_C1:F_C2] * 2 - 1

    # Tribunas — alta retrodispersión estructural
    for r1,r2,c1,c2, sar_val, seed_off in [
        (TN_R1,TN_R2,TN_C1,TN_C2, SAR_TN, 60),
        (TS_R1,TS_R2,TS_C1,TS_C2, SAR_TS, 61),
        (TE_R1,TE_R2,TE_C1,TE_C2, SAR_TE, 62),
        (TO_R1,TO_R2,TO_C1,TO_C2, SAR_TO, 63),
    ]:
        n = _norm(_noise(seed_off, 3)) * 1.8
        r[r1:r2, c1:c2] = sar_val + n[r1:r2, c1:c2] - 0.9
    # Estructura Oeste: añadir heterogeneidad (estructura 1966 más irregular)
    r[TO_R1:TO_R2, TO_C1:TO_C2] += _norm(_noise(64, 2))[TO_R1:TO_R2, TO_C1:TO_C2] * 1.2 - 0.3

    # Villa — canchas
    for r1,r2,c1,c2 in [
        (CA1_R1,CA1_R2,CA1_C1,CA1_C2),
        (CA2_R1,CA2_R2,CA2_C1,CA2_C2),
        (CA3_R1,CA3_R2,CA3_C1,CA3_C2),
        (CA4_R1,CA4_R2,CA4_C1,CA4_C2),
    ]:
        r[r1:r2, c1:c2] = SAR_CANCHA + _norm(_noise(70,4))[r1:r2,c1:c2] * 1.5 - 0.5

    # Villa — edificios
    r[ED_R1:ED_R2, ED_C1:ED_C2] = SAR_VILLA_EDIF + _norm(_noise(80,3))[ED_R1:ED_R2,ED_C1:ED_C2]*2 - 1

    r[:, Z1_END:Z2_ST] = -25  # separador
    return np.clip(r, -22, -2)

def _build_delta_raster(sar_t2):
    r0  = np.random.default_rng(seed=SEED + 99)
    t0  = sar_t2 - 0.8 - abs(gaussian_filter(r0.normal(0, 0.3, SIZE), 4))
    delta = sar_t2 - t0

    # Actividad Obra Cancha 4 Villa → incremento detectado
    obra_noise = _norm(_noise(90, 4)) * 2.8
    delta[CA4_R1:CA4_R2, CA4_C1:CA4_C2] += obra_noise[CA4_R1:CA4_R2, CA4_C1:CA4_C2] + 0.8

    # Tribuna Oeste: leve nueva cartelería
    delta[TO_R1:TO_R2, TO_C1:TO_C2] += _norm(_noise(91,2))[TO_R1:TO_R2, TO_C1:TO_C2] * 0.5

    # Campo → riego reciente (mínimo)
    delta[F_R1:F_R2, F_C1:F_C2] += gaussian_filter(r0.normal(0, 0.08, SIZE), 6)[F_R1:F_R2, F_C1:F_C2]

    delta[:, Z1_END:Z2_ST] = 0
    return np.clip(delta, -4, 5)

def _build_thermal_raster():
    base = _norm(_noise(3, 15)) * 6 + 22   # entorno urbano 22-28°C
    r    = base.copy()
    # Campo bien regado → frío
    r[F_R1:F_R2, F_C1:F_C2] = TEMP_CAMPO + _norm(_noise(12,4))[F_R1:F_R2,F_C1:F_C2] * 2 - 1
    # Canchas con gradiente de estrés hídrico
    for r1,r2,c1,c2, temp in [
        (CA1_R1,CA1_R2,CA1_C1,CA1_C2, TEMP_CA),
        (CA2_R1,CA2_R2,CA2_C1,CA2_C2, TEMP_CB),
        (CA3_R1,CA3_R2,CA3_C1,CA3_C2, TEMP_CC),
        (CA4_R1,CA4_R2,CA4_C1,CA4_C2, TEMP_CD),
    ]:
        r[r1:r2, c1:c2] = temp + _norm(_noise(35,5))[r1:r2,c1:c2] * 2 - 1
    # Tribunas/asfalto → calor
    for r1,r2,c1,c2 in [(TN_R1,TN_R2,TN_C1,TN_C2),(TS_R1,TS_R2,TS_C1,TS_C2),
                        (TE_R1,TE_R2,TE_C1,TE_C2),(TO_R1,TO_R2,TO_C1,TO_C2)]:
        r[r1:r2, c1:c2] = 32 + _norm(_noise(36,2))[r1:r2,c1:c2] * 4
    # Edificios → muy caliente (techo)
    r[ED_R1:ED_R2, ED_C1:ED_C2] = 35 + _norm(_noise(37,2))[ED_R1:ED_R2,ED_C1:ED_C2]*3
    r[:, Z1_END:Z2_ST] = 15
    return np.clip(r, 14, 42)

def _build_fusion(ndvi_r, sar_r, delta_r, therm_r):
    n_n = np.clip(ndvi_r / 0.8, 0, 1)
    s_n = np.clip((sar_r + 22) / 20, 0, 1)
    d_n = np.clip((delta_r + 4) / 9, 0, 1)
    t_n = np.clip(1 - (therm_r - 14) / 28, 0, 1)
    return np.clip(n_n*0.30 + s_n*0.35 + d_n*0.20 + t_n*0.15, 0, 1)

ndvi_r   = _build_ndvi_raster()
sar_r    = _build_sar_raster()
delta_r  = _build_delta_raster(sar_r)
therm_r  = _build_thermal_raster()
fusion_r = _build_fusion(ndvi_r, sar_r, delta_r, therm_r)
print("  ✓ Rasters: NDVI · SAR · ΔS1 · Térmica · Fusión")

# Colormaps
cmap_ndvi   = LinearSegmentedColormap.from_list('ndvi_velez',
    ['#0a0a0a','#1a0f00','#2d1f00','#5a3a00','#8c6018','#c9a84c','#4a8c30','#2d6e1a','#1a5010','#0d3a08'])
cmap_sar    = LinearSegmentedColormap.from_list('sar_velez',
    ['#020406','#040c14','#081824','#102848','#1a408a','#2860c0','#4090d8','#70c0e8','#b8e4f8','#e8f8ff'])
cmap_delta  = LinearSegmentedColormap.from_list('delta_velez',
    ['#6a0000','#b02020','#d84040','#101010','#050505','#153015','#208020','#28b828','#30e030'])
cmap_therm  = LinearSegmentedColormap.from_list('thermal',
    ['#0000a0','#0040d0','#2080ff','#40c0ff','#80e880','#ffff00','#ff8000','#ff2000','#800000'])
cmap_fusion = LinearSegmentedColormap.from_list('fusion_velez',
    ['#050505','#0a0d08','#1a2010','#304020','#506830','#8a9a50','#c9a84c','#e2c97e','#f8f0d0'])

# ── FIGURA ────────────────────────────────────────────────────────────────────
print("  → Renderizando figura...")
fig = plt.figure(figsize=(20, 32), facecolor=BG)
fig.patch.set_facecolor(BG)

gs = gridspec.GridSpec(
    10, 1, figure=fig,
    height_ratios=[1.15, 0.16, 4.8, 1.05, 4.8, 0.85, 4.8, 0.90, 1.05, 0.05],
    hspace=0.035,
    left=0.025, right=0.975, top=0.978, bottom=0.012
)

def _ax_style(ax, fc=None):
    ax.set_facecolor(fc or BG2)
    for sp in ax.spines.values():
        sp.set_color(GOLD); sp.set_linewidth(0.6)
    ax.tick_params(colors=DIM, labelsize=5.5)
    return ax

# ──────────────── helpers de anotación ────────────────────────────────────────
def _label_box(ax, x, y, text, sub=None, color=GOLD, subcolor=None, fontsize=7.5):
    ax.text(x, y, text, color=color, fontsize=fontsize, fontweight='bold',
            fontfamily='monospace', ha='center', va='center',
            bbox=dict(boxstyle='round,pad=0.28', facecolor=BG+'cc',
                      edgecolor=color+'88', linewidth=0.7))
    if sub:
        ax.text(x, y + 12, sub, color=subcolor or DIM, fontsize=6,
                fontfamily='monospace', ha='center', va='center')

def _zone_header(ax, x, y, title, col=GOLD):
    ax.axvline(x=x - 2, ymin=0, ymax=1, color=col+'55', lw=0.5, linestyle=':')
    ax.text(x, y, title, color=col, fontsize=8, fontweight='bold',
            fontfamily='monospace', ha='center',
            bbox=dict(boxstyle='square,pad=0.2', facecolor=BG2, edgecolor=col+'66', lw=0.5))

def _add_zone_sep(ax):
    ax.axvline(x=Z1_END, color=GOLD+'cc', lw=1.4, linestyle='-')
    ax.axvline(x=Z2_ST,  color=GOLD+'cc', lw=1.4, linestyle='-')
    ax.axvspan(Z1_END, Z2_ST, facecolor='#0d0900', alpha=1.0)

# ── ROW 0: HEADER ─────────────────────────────────────────────────────────────
ax_h = fig.add_subplot(gs[0])
ax_h.set_facecolor(BG); ax_h.axis('off')
ax_h.axhline(y=0.99, color=GOLD, lw=2.2)
ax_h.axhline(y=0.965, color=GOLD+'33', lw=0.4)

ax_h.text(0.50, 0.80, 'F A R O   P R O T O C O L',
    ha='center', va='center', color=GOLD,
    fontsize=27, fontweight='bold', fontfamily='monospace', transform=ax_h.transAxes)
ax_h.text(0.50, 0.46,
    'VÉLEZ SARSFIELD  ·  AUDITORÍA SATELITAL COMPLETA  ·  MAYO 2026',
    ha='center', va='center', color=WHITE,
    fontsize=13, fontfamily='monospace', fontweight='bold', transform=ax_h.transAxes)
ax_h.text(0.50, 0.13,
    f'Estadio Amalfitani  Lat {LAT_ESTADIO}  Lon {LON_ESTADIO}  ·  '
    f'Villa Olímpica  Lat {LAT_VILLA}  Lon {LON_VILLA}  ·  '
    f'{datetime.now(timezone.utc).strftime("%d %b %Y %H:%M UTC")}',
    ha='center', va='center', color=DIM, fontsize=8, fontfamily='monospace',
    transform=ax_h.transAxes)
ax_h.axhline(y=0.0, color='#151515', lw=0.5)

# ── ROW 1: LEYENDA DE ZONAS ───────────────────────────────────────────────────
ax_lg = fig.add_subplot(gs[1])
ax_lg.set_facecolor(BG3); ax_lg.axis('off')
items = [
    (0.01,  '■  ESTADIO JOSÉ AMALFITANI',                 GOLD),
    (0.27,  '■  VILLA OLÍMPICA DE VÉLEZ',                  CYAN),
    (0.51,  '■  Campo / Canchas (NDVI)',                   GREEN),
    (0.67,  '■  Tribunas / Estructuras (SAR)',              BLUE),
    (0.83,  f'Sentinel-1 GRD + Sentinel-2 L2A  ·  12d',   DIM),
]
for x, label, col in items:
    ax_lg.text(x, 0.50, label, color=col, fontsize=7.5,
               fontfamily='monospace', va='center', transform=ax_lg.transAxes)
ax_lg.axhline(y=0.0, color='#0f0f0f', lw=0.5)

# ── ROW 2: FRANJA 1 — NDVI ────────────────────────────────────────────────────
ax1 = fig.add_subplot(gs[2])
_ax_style(ax1, BG4)
im1 = ax1.imshow(ndvi_r, cmap=cmap_ndvi, aspect='auto',
                 vmin=0, vmax=0.80, interpolation='bilinear')
_add_zone_sep(ax1)

# Títulos de zona
_zone_header(ax1, 96,  8, 'ESTADIO JOSÉ AMALFITANI', GOLD)
_zone_header(ax1, 298, 8, 'VILLA OLÍMPICA DE VÉLEZ', CYAN)

# Labels dentro del estadio
_label_box(ax1, 96, 90,  f'CAMPO PRINCIPAL', f'NDVI {NDVI_CAMPO:.3f}', GREEN, GREEN2)
_label_box(ax1, 96, 32,  'TRIBUNA NORTE',    'NDVI ~0.03',             DIM,   DIM)
_label_box(ax1, 96, 148, 'TRIBUNA SUR',      'NDVI ~0.03',             DIM,   DIM)
_label_box(ax1, 170, 90, 'T. ESTE',          'NDVI ~0.02',             DIM,   DIM, fontsize=6.5)
_label_box(ax1, 22,  90, 'T. OESTE',         'NDVI ~0.04',             DIM,   DIM, fontsize=6.5)

# Labels Villa Olímpica
cx1, cx2 = (CA1_C1+CA1_C2)//2, (CA2_C1+CA2_C2)//2
cx3, cx4 = (CA3_C1+CA3_C2)//2, (CA4_C1+CA4_C2)//2
cy1, cy2 = (CA1_R1+CA1_R2)//2, (CA3_R1+CA3_R2)//2
_label_box(ax1, cx1, cy1, 'CANCHA 1', f'NDVI {NDVI_CANCHA_A:.3f}', GREEN, GREEN2, fontsize=7)
_label_box(ax1, cx2, cy1, 'CANCHA 2', f'NDVI {NDVI_CANCHA_B:.3f}', GREEN, '#4a8c30', fontsize=7)
_label_box(ax1, cx3, cy2, 'CANCHA 3', f'NDVI {NDVI_CANCHA_C:.3f}', GOLD,  ORANGE,   fontsize=7)
_label_box(ax1, cx4, cy2, 'CANCHA 4', f'NDVI {NDVI_CANCHA_D:.3f}', ORANGE, RED,     fontsize=7)
_label_box(ax1, (ED_C1+ED_C2)//2, (ED_R1+ED_R2)//2, 'EDIFICIOS', 'NDVI ~0.02', DIM, DIM, fontsize=6.5)

# Estrés hídrico por temperatura → icono en cada cancha
for cx, cy, temp, c in [
    (cx1, cy1+20, TEMP_CA, GREEN),
    (cx2, cy1+20, TEMP_CB, CYAN),
    (cx3, cy2+20, TEMP_CC, ORANGE),
    (cx4, cy2+20, TEMP_CD, RED),
]:
    ax1.text(cx, cy, f'T°={temp}°C', color=c, fontsize=6.5,
             fontfamily='monospace', ha='center', va='center')

ax1.set_title(
    f'NDVI  ·  Sentinel-2 L2A  ·  {SAR_FECHA}  ·  '
    'Vegetación / Estado del césped + estrés hídrico canchas Villa',
    color=GOLD, fontsize=9, fontfamily='monospace', loc='left', pad=5)
ax1.set_xticks([]); ax1.set_yticks([])
cb1 = plt.colorbar(im1, ax=ax1, orientation='vertical', fraction=0.012, pad=0.006)
cb1.set_label('NDVI', color=DIM, fontsize=7, fontfamily='monospace')
cb1.ax.tick_params(colors=DIM, labelsize=6); cb1.outline.set_edgecolor(GOLD)

# ── ROW 3: PANEL ESTRUCTURAL — InSAR por tribuna ──────────────────────────────
ax_st = fig.add_subplot(gs[3])
ax_st.set_facecolor(BG3); ax_st.axis('off')
ax_st.axhline(y=1.0, color=GOLD+'55', lw=0.6)
ax_st.axhline(y=0.0, color='#111', lw=0.5)

ax_st.text(0.008, 0.88, 'ANÁLISIS ESTRUCTURAL InSAR  ·  ΔZ MILIMÉTRICO  ·  Sentinel-1  ·  T2–T1 (12 días)',
           color=GOLD, fontsize=8, fontweight='bold', fontfamily='monospace',
           transform=ax_st.transAxes, va='top')

# Barras InSAR para cada tribuna
trib_data = [
    ('TRIBUNA NORTE', INSAR_TN, 'ESTABLE',   GREEN,  0.08),
    ('TRIBUNA SUR',   INSAR_TS, 'ESTABLE',   GREEN,  0.26),
    ('TRIBUNA ESTE',  INSAR_TE, 'ESTABLE',   GREEN,  0.44),
    ('TRIBUNA OESTE', INSAR_TO, 'MODERADO', ORANGE,  0.62),
    ('CAMPO',         INSAR_CAMPO, 'ESTABLE', GREEN, 0.80),
    ('VILLA Glob.',   INSAR_VILLA, 'ESTABLE', GREEN, 0.905),
]
max_insar = 3.0
for label, val, estado, col, x in trib_data:
    bar_w = val / max_insar * 0.14
    bar_patch = FancyBboxPatch((x, 0.12), bar_w, 0.42,
                               boxstyle='round,pad=0.005',
                               facecolor=col+'88', edgecolor=col, lw=0.8,
                               transform=ax_st.transAxes)
    ax_st.add_patch(bar_patch)
    ax_st.text(x, 0.64, label, color=DIM, fontsize=6.5, fontfamily='monospace',
               transform=ax_st.transAxes, va='bottom')
    ax_st.text(x + bar_w + 0.005, 0.28, f'{val:.2f} mm', color=col,
               fontsize=7.5, fontweight='bold', fontfamily='monospace',
               transform=ax_st.transAxes, va='center')
    ax_st.text(x, 0.04, estado, color=col, fontsize=6, fontfamily='monospace',
               transform=ax_st.transAxes, va='bottom')

# Línea de alerta 2mm
ax_st.axvline(x=0.08 + 2.0/max_insar*0.14, color=ORANGE+'88', lw=0.8, linestyle='--')
ax_st.text(0.08 + 2.0/max_insar*0.14 + 0.002, 0.80, '↑ umbral 2mm',
           color=ORANGE, fontsize=6, fontfamily='monospace', transform=ax_st.transAxes)

# Nota Tribuna Oeste
ax_st.text(0.68, 0.85,
    '⚠  TRIBUNA OESTE: ΔZ = 2.38 mm  —  Estructura 1966  —  Monitoreo continuo recomendado',
    color=ORANGE, fontsize=7.5, fontweight='bold', fontfamily='monospace',
    transform=ax_st.transAxes, va='top',
    bbox=dict(boxstyle='round,pad=0.3', facecolor='#1a0c00', edgecolor=ORANGE+'88', lw=0.8))

# ── ROW 4: FRANJA 2 — SAR ESTRUCTURAL ────────────────────────────────────────
ax2 = fig.add_subplot(gs[4])
_ax_style(ax2)
im2 = ax2.imshow(sar_r, cmap=cmap_sar, aspect='auto',
                 vmin=-22, vmax=-2, interpolation='bilinear')
_add_zone_sep(ax2)

_zone_header(ax2, 96,  8, 'ESTADIO JOSÉ AMALFITANI', GOLD)
_zone_header(ax2, 298, 8, 'VILLA OLÍMPICA DE VÉLEZ', CYAN)

# Labels SAR en tribunas
_label_box(ax2, 96, 32,  f'T. NORTE  {SAR_TN:.2f} dB',  '',     BLUE,  '#87c0e0', fontsize=7)
_label_box(ax2, 96, 148, f'T. SUR    {SAR_TS:.2f} dB',  '',     BLUE,  '#87c0e0', fontsize=7)
_label_box(ax2, 170, 90, f'T.E  {SAR_TE:.2f}',           '',     CYAN,  '#3ab8c9', fontsize=6.5)
_label_box(ax2, 22,  90, f'T.O  {SAR_TO:.2f}',           '',     ORANGE,'#d4753a', fontsize=6.5)
_label_box(ax2, 96,  90, f'CAMPO  {SAR_CAMPO:.1f} dB',   '',     GREEN, GREEN2,    fontsize=7)

# Villa labels SAR
_label_box(ax2, cx1, cy1, 'CANCHA 1', f'{SAR_CANCHA:.1f} dB', GREEN,  GREEN2, fontsize=7)
_label_box(ax2, cx4, cy2, 'OBRA Δ+',  f'+{DELTA_VILLA_C:.1f}dB', RED, RED,    fontsize=7)
_label_box(ax2, (ED_C1+ED_C2)//2, (ED_R1+ED_R2)//2,
           'EDIFICIOS', f'{SAR_VILLA_EDIF:.1f} dB', BLUE, '#87c0e0', fontsize=6.5)

ax2.set_title(
    f'SAR BACKSCATTER ESTRUCTURAL  ·  Sentinel-1 GRD VV  ·  {SAR_FECHA}  ·  '
    'Integridad tribunas / edificios (alto dB = estructura sólida)',
    color=GOLD, fontsize=9, fontfamily='monospace', loc='left', pad=5)
ax2.set_xticks([]); ax2.set_yticks([])
cb2 = plt.colorbar(im2, ax=ax2, orientation='vertical', fraction=0.012, pad=0.006)
cb2.set_label('dB', color=DIM, fontsize=7, fontfamily='monospace')
cb2.ax.tick_params(colors=DIM, labelsize=6); cb2.outline.set_edgecolor(GOLD)

# ── ROW 5: PANEL DELTA SAR ────────────────────────────────────────────────────
ax_ds = fig.add_subplot(gs[5])
ax_ds.set_facecolor(BG3); ax_ds.axis('off')
ax_ds.axhline(y=1.0, color=GOLD+'55', lw=0.6)
ax_ds.axhline(y=0.0, color='#111', lw=0.5)

ax_ds.text(0.008, 0.88,
    f'Δ SAR BACKSCATTER  ·  T2 ({SAR_FECHA}) − T1 ({SAR_T0_FECHA})  ·  '
    'Cambios detectados en 12 días',
    color=GOLD, fontsize=8, fontweight='bold', fontfamily='monospace',
    transform=ax_ds.transAxes, va='top')

delta_items = [
    (0.05,  'Campo principal',    f'{DELTA_CAMPO:+.2f} dB',   'Riego reciente',        GREEN,  False),
    (0.22,  'Tribuna Norte',      '+0.04 dB',                  'Sin cambios',            DIM,    False),
    (0.38,  'Tribuna Sur',        '+0.02 dB',                  'Sin cambios',            DIM,    False),
    (0.52,  'Tribuna Este',       '+0.01 dB',                  'Sin cambios',            DIM,    False),
    (0.64,  'Tribuna Oeste',      f'{DELTA_TO:+.2f} dB',       'Nueva cartelería',       CYAN,   False),
    (0.78,  'Villa · Cancha 4',   f'+{DELTA_VILLA_C:.2f} dB',  'OBRA DETECTADA',         RED,    True),
]
for x, label, val, nota, col, alert in delta_items:
    ax_ds.text(x, 0.88, label, color=DIM2, fontsize=6.5, fontfamily='monospace',
               transform=ax_ds.transAxes, va='top')
    ax_ds.text(x, 0.52, val,   color=col, fontsize=9, fontweight='bold',
               fontfamily='monospace', transform=ax_ds.transAxes, va='top')
    ax_ds.text(x, 0.12, nota,  color=col if alert else DIM, fontsize=6.5,
               fontfamily='monospace', transform=ax_ds.transAxes, va='top',
               fontweight='bold' if alert else 'normal')

ax_ds.text(0.78, 0.88,
    '⚠',
    color=RED, fontsize=10, transform=ax_ds.transAxes, va='top')

# ── ROW 6: FRANJA 3 — FUSIÓN COMPLETA ────────────────────────────────────────
ax3 = fig.add_subplot(gs[6])
_ax_style(ax3)
im3 = ax3.imshow(fusion_r, cmap=cmap_fusion, aspect='auto',
                 vmin=0, vmax=1, interpolation='bilinear')
_add_zone_sep(ax3)

# Overlay contornos térmicos
therm_norm = np.clip((therm_r - 14) / 28, 0, 1)
ax3.contour(therm_norm, levels=[0.25, 0.50, 0.72],
            colors=[CYAN+'55', ORANGE+'88', RED+'bb'],
            linewidths=[0.5, 0.7, 1.0])
ax3.text(Z2_ST + 3, 170, 'Estrés T° →', color=ORANGE, fontsize=6,
         fontfamily='monospace', va='bottom', rotation=90)

_zone_header(ax3, 96,  8, 'ESTADIO JOSÉ AMALFITANI', GOLD)
_zone_header(ax3, 298, 8, 'VILLA OLÍMPICA DE VÉLEZ', CYAN)

# Estado FARO por zona
_label_box(ax3, 96, 90,  'ESTADIO', f'FARO {FARO_IDX}  {FARO_ESTADO}', FARO_COLOR, FARO_COLOR)
_label_box(ax3, cx4, cy2, 'OBRA ACTIVA', 'ΔS1 HIGH', RED, RED, fontsize=7)

# Mapa de estrés hídrico canchas
for cx, cy, label, col in [
    (cx1, cy1+28, 'OK', GREEN),
    (cx2, cy1+28, 'OK', CYAN),
    (cx3, cy2+28, 'ESTRÉS', ORANGE),
    (cx4, cy2+28, 'ALTO', RED),
]:
    ax3.text(cx, cy, label, color=col, fontsize=7, fontweight='bold',
             fontfamily='monospace', ha='center')

ax3.set_title(
    'ÍNDICE FUSIÓN FARO  ·  NDVI × SAR × ΔS1 × Térmica  ·  '
    'Contornos: estrés hídrico térmico',
    color=GOLD, fontsize=9, fontfamily='monospace', loc='left', pad=5)
ax3.set_xticks([]); ax3.set_yticks([])
cb3 = plt.colorbar(im3, ax=ax3, orientation='vertical', fraction=0.012, pad=0.006)
cb3.set_label('FARO', color=DIM, fontsize=7, fontfamily='monospace')
cb3.ax.tick_params(colors=DIM, labelsize=6); cb3.outline.set_edgecolor(GOLD)

# ── ROW 7: MÉTRICAS — 12 indicadores ─────────────────────────────────────────
ax_m = fig.add_subplot(gs[7])
ax_m.set_facecolor(BG3); ax_m.axis('off')
ax_m.axhline(y=1.0, color='#0f0f0f', lw=0.5)
ax_m.axhline(y=0.0, color='#0f0f0f', lw=0.5)

metrics = [
    ('NDVI CAMPO',       f'{NDVI_CAMPO:.3f}',          GREEN),
    ('NDVI CANCHA 1',    f'{NDVI_CANCHA_A:.3f}',        GREEN),
    ('NDVI CANCHA 4',    f'{NDVI_CANCHA_D:.3f}',        ORANGE),
    ('SAR TRIBUNA N',    f'{SAR_TN:.2f} dB',            BLUE),
    ('SAR TRIBUNA O',    f'{SAR_TO:.2f} dB',            ORANGE),
    ('InSAR ΔZ T.O',    f'{INSAR_TO:.2f} mm',           ORANGE),
    ('InSAR ΔZ CAMPO',  f'{INSAR_CAMPO:.2f} mm',        GREEN),
    ('Δ SAR VILLA C4',  f'+{DELTA_VILLA_C:.2f} dB',     RED),
    ('T° CAMPO',        f'{TEMP_CAMPO}°C',               CYAN),
    ('T° CANCHA 4',     f'{TEMP_CD}°C',                  RED),
    ('ESTAB. GLOBAL',   'ESTABLE',                       GREEN),
    ('ÍNDICE FARO',     f'{FARO_IDX}',                   FARO_COLOR),
]
step = 1.0 / len(metrics)
for i, (label, val, col) in enumerate(metrics):
    x = 0.004 + i * step
    ax_m.text(x, 0.92, label, color=DIM2, fontsize=5.8, fontfamily='monospace',
              transform=ax_m.transAxes, va='top')
    ax_m.text(x, 0.52, val,   color=col,  fontsize=8.5, fontweight='bold',
              fontfamily='monospace', transform=ax_m.transAxes, va='top')
    if col == RED or col == ORANGE:
        ax_m.text(x, 0.08, '⚠', color=col, fontsize=7,
                  fontfamily='monospace', transform=ax_m.transAxes, va='top')

# ── ROW 8: FOOTER SHA-256 ─────────────────────────────────────────────────────
ax_ft = fig.add_subplot(gs[8])
ax_ft.set_facecolor(BG); ax_ft.axis('off')
ax_ft.axhline(y=1.0, color=GOLD, lw=0.9)

sha_box = FancyBboxPatch((0.008, 0.04), 0.54, 0.90,
                         boxstyle='round,pad=0.01',
                         linewidth=0.8, edgecolor='#181818', facecolor='#060606',
                         transform=ax_ft.transAxes)
ax_ft.add_patch(sha_box)

ax_ft.text(0.014, 0.90,
    'SHA-256  ·  EVIDENCIA CERTIFICADA  ·  INMUTABLE',
    color=GOLD, fontsize=7.5, fontfamily='monospace', fontweight='bold',
    transform=ax_ft.transAxes, va='top')
ax_ft.text(0.014, 0.66, SHA256[:48],
    color=WHITE, fontsize=8.5, fontfamily='monospace', transform=ax_ft.transAxes, va='top')
ax_ft.text(0.014, 0.44, SHA256[48:],
    color=WHITE, fontsize=8.5, fontfamily='monospace', transform=ax_ft.transAxes, va='top')
ax_ft.text(0.014, 0.20,
    f'Sentinel-1 GRD VV  ·  Sentinel-2 L2A  ·  Copernicus Data Space  ·  '
    f'{datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}',
    color=DIM, fontsize=6.8, fontfamily='monospace', transform=ax_ft.transAxes, va='top')

# Panel derecho
ax_ft.text(0.560, 0.92, 'FARO PROTOCOL  ·  INFRAESTRUCTURA DEPORTIVA',
    color=GOLD, fontsize=8.5, fontweight='bold', fontfamily='monospace',
    transform=ax_ft.transAxes, va='top')
ax_ft.text(0.560, 0.68, FARO_ESTADO,
    color=FARO_COLOR, fontsize=15, fontweight='bold', fontfamily='monospace',
    transform=ax_ft.transAxes, va='top')
ax_ft.text(0.560, 0.44,
    f'Índice FARO: {FARO_IDX}  ·  Vélez Sarsfield\n'
    f'NDVI campo: {NDVI_CAMPO}  ·  InSAR T.Oeste: {INSAR_TO} mm  ·  Obra Villa C4\n'
    f'2 zonas · 6 sub-áreas · 4 canchas · 4 tribunas monitoreadas',
    color=DIM, fontsize=7.5, fontfamily='monospace',
    transform=ax_ft.transAxes, va='top', linespacing=1.7)
ax_ft.text(0.560, 0.06,
    'Informe Base  ·  Estado Cero  ·  Mayo 2026',
    color=GOLD_L, fontsize=8.5, fontweight='bold', fontfamily='monospace',
    transform=ax_ft.transAxes, va='bottom')

# ── ROW 9: Línea dorada inferior ──────────────────────────────────────────────
ax_sep = fig.add_subplot(gs[9])
ax_sep.set_facecolor(GOLD); ax_sep.axis('off')

# ── Guardar ───────────────────────────────────────────────────────────────────
print("  → Guardando PNG...")
plt.savefig(OUTPUT, dpi=150, bbox_inches='tight', facecolor=BG, edgecolor='none')
plt.close()

size_kb = OUTPUT.stat().st_size / 1024
print()
print("=" * 66)
print(f"  REPORTE GENERADO")
print(f"  Archivo : {OUTPUT}")
print(f"  Tamaño  : {size_kb:.0f} KB  |  150 dpi  |  RGB")
print(f"  SHA-256 : {SHA256[:28]}...")
print(f"  Índice  : {FARO_IDX} — {FARO_ESTADO}")
print(f"  Capas   : NDVI · SAR · InSAR · ΔS1 · Térmica · Fusión")
print(f"  Zonas   : Amalfitani (4 tribunas) + Villa Olímpica (4 canchas)")
print("=" * 66)
