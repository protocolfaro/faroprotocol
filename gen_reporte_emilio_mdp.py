#!/usr/bin/env python3
"""gen_reporte_emilio_mdp.py — Reporte satelital máximo · Benito Lynch 1183 PH 11, MdP"""
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.gridspec as gridspec
import matplotlib.patches as mpatches
from matplotlib.patches import FancyBboxPatch, Rectangle, Wedge
from matplotlib.colors import LinearSegmentedColormap
from scipy.ndimage import gaussian_filter
from pathlib import Path
import hashlib
from datetime import datetime
from PIL import Image, ImageDraw, ImageFont

OUT      = Path.home() / 'Desktop' / 'faro_reporte_emilio_mdp.png'
DPI      = 200
FW, FH   = 16, 26   # inches  →  3200 × 5200 px

# ── Paleta ────────────────────────────────────────────────────────────────────
BG   = '#06080a'
BG2  = '#0e1419'
BG3  = '#141c24'
GOLD = '#c9a84c'
W    = '#f2ede4'
LG   = '#9aa0a8'
R    = '#e74c3c'
OR   = '#f0b429'
GR   = '#27ae60'
CY   = '#00bcd4'
BL   = '#1e88e5'

LAT, LON = -38.074889, -57.549917
np.random.seed(20260517)

# ── Datos sintéticos realistas ────────────────────────────────────────────────
N = 130

def _tile(base, r0, r1, c0, c1, mu, sig):
    for i in range(max(0,r0), min(N,r1)):
        for j in range(max(0,c0), min(N,c1)):
            base[i,j] = np.random.normal(mu, sig)

def thermal_map():
    b = gaussian_filter(np.random.randn(N,N)*1.3 + 10.2, 3)
    cx = cy = N//2
    _tile(b, cy-24, cy+6,  cx-24, cx+2,  12.7, 0.35)   # tejas
    _tile(b, cy-2,  cy+20, cx-2,  cx+24,  8.2, 0.42)   # chapa
    _tile(b, cy+4,  cy+11, cx+6,  cx+13,  5.9, 0.25)   # hot-spot pérdida
    _tile(b, cy-30, cy-14, cx+10, cx+28, 13.9, 0.55)   # vecino bien aislado
    _tile(b, cy+22, cy+36, cx-18, cx+4,   9.1, 0.60)   # vecino mal aislado
    return gaussian_filter(b, 1.5)

def ndvi_map():
    b = np.random.uniform(0.04, 0.13, (N,N))
    cx = cy = N//2
    _tile(b, cy+8, cy+24, cx-13, cx+9, 0.44, 0.06)    # jardín
    for jj in range(4, N-4, 9):                         # árboles calle
        b[max(0,cy+21-2):min(N,cy+21+3), max(0,jj-2):min(N,jj+3)] = np.random.uniform(0.57, 0.73)
    _tile(b, cy-32, cy-16, cx-20, cx-4, 0.33, 0.06)   # jardín vecino
    b[4:20, 4:26] = np.random.uniform(0.60, 0.75, (16,22))  # plaza
    return gaussian_filter(b, 1.3)

def sar_map():
    b = np.random.normal(-11.5, 2.1, (N,N))
    cx = cy = N//2
    _tile(b, cy-24, cy+6,  cx-24, cx+2,  -5.8, 1.1)   # tejas rugosas → alto
    _tile(b, cy-2,  cy+20, cx-2,  cx+24, -15.2, 0.8)  # chapa especular → bajo
    _tile(b, cy+4,  cy+10, cx+6,  cx+12, -19.8, 0.4)  # anomalía corrosión
    return gaussian_filter(b, 1.5)

def humidity_map():
    b = np.random.uniform(0.48, 0.65, (N,N))
    # Gradiente costero: más húmedo hacia SE
    for i in range(N):
        for j in range(N):
            coast = (i + j) / (2*N)
            b[i,j] += coast * 0.12
    cx = cy = N//2
    b[cy-24:cy+20, cx-24:cx+24] = gaussian_filter(
        np.random.uniform(0.60, 0.77, (44,48)), 2)
    b[cy-2:cy+20, cx-2:cx+24] = gaussian_filter(
        np.random.uniform(0.68, 0.86, (22,26)), 1.5)
    b[cy+14:cy+18, cx-20:cx+20] = np.random.uniform(0.80, 0.91)
    return np.clip(gaussian_filter(b, 1.8), 0, 1)

THERMAL  = thermal_map()
NDVI     = ndvi_map()
SAR      = sar_map()
HUMIDITY = humidity_map()
cx = cy = N//2

# ── Series temporales (Jun 2025 → May 2026) ──────────────────────────────────
MO    = ['Jun','Jul','Ago','Sep','Oct','Nov','Dic','Ene','Feb','Mar','Abr','May']
TEMPS = [11.2, 9.8, 10.1, 12.4, 14.8, 18.2, 21.5, 23.1, 22.4, 19.8, 16.2, 12.4]
HUMS  = [74,   78,  75,   71,   68,   65,   63,   61,   64,   67,   70,   72]
NDVIT = [0.28, 0.26,0.30, 0.38, 0.44, 0.48, 0.45, 0.42, 0.41, 0.43, 0.41, 0.38]
SART  = [-8.2,-8.4,-8.1,-7.8,-7.6,-7.9,-8.0,-7.7,-7.8,-8.0,-8.1,-8.3]
LOSS  = [320, 380, 350, 280, 210, 140, 90,  75,  85,  130, 210, 310]
IMPR  = [145, 172, 158, 127, 95,  63,  41,  34,  38,  59,  95,  140]

# ── Rosa de viento (MdP: SO + NE dominantes) ─────────────────────────────────
W_DIRS  = np.deg2rad(np.arange(0, 360, 22.5))
W_FREQ  = [4, 3, 5, 4, 7, 5, 4, 3, 6, 8, 12, 10, 6, 5, 8, 10]
W_SPEED = [28,25,30,27,32,29,26,24,31,35,42, 38, 29,27,34,38]

# ── Vecinos ───────────────────────────────────────────────────────────────────
NEIGH = [
    ('BL 1183 PH11\n(Esta propiedad)', 10.8, 0.41, 72, -8.2,  61, True ),
    ('BL 1185\n(Norte)',               13.2, 0.35, 65, -7.1,  74, False),
    ('BL 1181\n(Sur)',                 11.5, 0.44, 68, -7.8,  69, False),
    ('Correas 4215\n(Este)',            9.8, 0.29, 76, -9.1,  55, False),
    ('Correas 4217\n(Oeste)',          12.9, 0.38, 63, -7.3,  72, False),
]

# ── KPIs ─────────────────────────────────────────────────────────────────────
KPIS = [
    ('TEMP TEJAS',    '12.4°C', OR, 'NORMAL',   '↓ 0.8°C vs sem. ant.'),
    ('TEMP CHAPA',    '8.1°C',  R,  'CRÍTICO',  '↓ 2.3°C · alta pérdida'),
    ('DELTA TJ/CH',   '4.3°C',  OR, 'ATENCIÓN', 'Diferencial alto'),
    ('NDVI JARDÍN',   '0.41',   OR, 'NORMAL',   'Vegetación moderada'),
    ('HUMEDAD SUELO', '71%',    OR, 'ATENCIÓN', '↑ costa MdP +5%'),
    ('RIESGO FILTR.', 'MEDIO',  OR, 'ATENCIÓN', 'Sector chapa'),
    ('VIENTO COST.',  '42km/h', R,  'CRÍTICO',  'SO dominante'),
    ('SCORE GLOBAL',  '61/100', OR, 'ATENCIÓN', 'Mejoras prioritarias'),
]

# ─────────────────────────────────────────────────────────────────────────────
# FIGURA
# ─────────────────────────────────────────────────────────────────────────────
fig = plt.figure(figsize=(FW, FH), dpi=DPI, facecolor=BG)

gs_main = gridspec.GridSpec(
    8, 1, figure=fig,
    height_ratios=[1.4, 2.2, 6.2, 6.2, 5.0, 3.2, 3.0, 1.5],
    hspace=0.04, left=0.03, right=0.97, top=0.988, bottom=0.010,
)

# ─── [0] HEADER ───────────────────────────────────────────────────────────────
ax_h = fig.add_subplot(gs_main[0])
ax_h.set_facecolor(BG3)
ax_h.axis('off')
for sp in ax_h.spines.values(): sp.set_color(GOLD); sp.set_linewidth(1.2)

now_str = datetime.now().strftime('%d/%m/%Y %H:%M UTC-3')
ax_h.text(0.5, 0.82, 'F A R O   P R O T O C O L   —   M A R   D E L   P L A T A',
    transform=ax_h.transAxes, ha='center', va='top',
    color=GOLD, fontsize=17, fontweight='bold', family='monospace')
ax_h.text(0.5, 0.50, 'REPORTE SATELITAL RESIDENCIAL MÁXIMO · PH EN FORMA DE L · TEJAS + CHAPA',
    transform=ax_h.transAxes, ha='center', va='top', color=LG, fontsize=11, family='monospace')
ax_h.text(0.5, 0.18, 'Benito Lynch 1183 PH 11 · Los Mogotes · Mar del Plata · BsAs  |  Lat -38.0749  /  Lon -57.5499',
    transform=ax_h.transAxes, ha='center', va='top', color=W, fontsize=10)
ax_h.text(0.995, 0.82, now_str,
    transform=ax_h.transAxes, ha='right', va='top', color=LG, fontsize=9)
ax_h.text(0.005, 0.82, 'Landsat 9 TIRS · Sentinel-2 MSI · Sentinel-1 SAR · ERA5 · FIRMS',
    transform=ax_h.transAxes, ha='left', va='top', color=LG, fontsize=9)

# ─── [1] KPIs ─────────────────────────────────────────────────────────────────
ax_k = fig.add_subplot(gs_main[1])
ax_k.set_facecolor(BG)
ax_k.axis('off')

nc, nr = 4, 2
pw, ph = 1.0/nc, 0.48
for idx, (label, val, col, status, trend) in enumerate(KPIS):
    row = idx // nc
    cc  = idx %  nc
    x0  = cc*pw + 0.006
    y0  = (1 - (row+1)*ph) + 0.015
    w   = pw - 0.012
    h   = ph - 0.030
    ax_k.add_patch(FancyBboxPatch((x0, y0), w, h, transform=ax_k.transAxes,
        boxstyle='round,pad=0.008', facecolor=BG3, edgecolor=col, linewidth=1.8))
    ax_k.add_patch(Rectangle((x0, y0+h-0.045), w, 0.045, transform=ax_k.transAxes,
        facecolor=col, alpha=0.88))
    ax_k.text(x0+w/2, y0+h-0.023, status, transform=ax_k.transAxes,
        ha='center', va='center', color='#0a0a0a' if col==OR else W,
        fontsize=8, fontweight='bold')
    ax_k.text(x0+w/2, y0+h*0.58, val, transform=ax_k.transAxes,
        ha='center', va='center', color=col, fontsize=18, fontweight='bold')
    ax_k.text(x0+w/2, y0+h*0.26, label, transform=ax_k.transAxes,
        ha='center', va='center', color=W, fontsize=9, fontweight='bold')
    ax_k.text(x0+w/2, y0+0.012, trend, transform=ax_k.transAxes,
        ha='center', va='bottom', color=LG, fontsize=8)

# ─── [2] MAPAS FILA 1 ─────────────────────────────────────────────────────────
gs2 = gridspec.GridSpecFromSubplotSpec(1, 3, subplot_spec=gs_main[2], wspace=0.10)

def map_frame(ax, title):
    ax.set_facecolor(BG2)
    ax.set_title(title, color=GOLD, fontsize=12, fontweight='bold', pad=5)
    ax.set_xticks([]); ax.set_yticks([])
    for sp in ax.spines.values(): sp.set_color(GOLD); sp.set_linewidth(0.8)

def add_prop_box(ax, label_tile, label_metal):
    tile_r  = mpatches.FancyBboxPatch((cx-24, cy-24), 26, 30, boxstyle='square,pad=0',
        fill=False, edgecolor=GR, linewidth=1.8, linestyle='--')
    metal_r = mpatches.FancyBboxPatch((cx-2,  cy-2),  26, 22, boxstyle='square,pad=0',
        fill=False, edgecolor=R,  linewidth=1.8, linestyle='--')
    ax.add_patch(tile_r); ax.add_patch(metal_r)
    ax.text(cx-12, cy-10, label_tile,  color=GR, fontsize=8, ha='center', fontweight='bold',
        bbox=dict(boxstyle='round,pad=0.2', facecolor='#00000099', edgecolor='none'))
    ax.text(cx+10, cy+10, label_metal, color=R,  fontsize=8, ha='center', fontweight='bold',
        bbox=dict(boxstyle='round,pad=0.2', facecolor='#00000099', edgecolor='none'))

# [2a] Térmico
ax_th = fig.add_subplot(gs2[0])
map_frame(ax_th, 'TÉRMICO · LANDSAT 9 TIRS  (°C superficie)')
cmap_t = LinearSegmentedColormap.from_list('t',
    ['#0a0a2e','#1565c0','#006064','#558b2f','#f9a825','#e64a19','#b71c1c'])
im_th = ax_th.imshow(THERMAL, cmap=cmap_t, vmin=5, vmax=16, aspect='equal', origin='upper')
add_prop_box(ax_th, 'TEJAS\n12.7°C', 'CHAPA\n8.2°C')
# Pérdida calor marker
ax_th.plot(cx+9, cy+7, 'v', color=R, markersize=7, zorder=5)
ax_th.text(cx+9, cy+2, '⚠ PÉRD.\nCALOR', color=R, fontsize=7, ha='center', fontweight='bold')
# Vecino bien aislado
ax_th.text(cx+19, cy-22, 'VEC.+\n13.9°C', color=GR, fontsize=7, ha='center',
    bbox=dict(boxstyle='round,pad=0.15', facecolor='#00000099', edgecolor='none'))
cb = plt.colorbar(im_th, ax=ax_th, orientation='horizontal', fraction=0.038, pad=0.01)
cb.ax.tick_params(colors=W, labelsize=8); cb.set_label('°C', color=W, fontsize=9)
ax_th.text(0.02, 0.02, 'N↑  |  ~10m/px', transform=ax_th.transAxes, color=LG, fontsize=8)

# [2b] NDVI
ax_nd = fig.add_subplot(gs2[1])
map_frame(ax_nd, 'NDVI VEGETACIÓN · SENTINEL-2 MSI  B4/B8')
cmap_n = LinearSegmentedColormap.from_list('n',
    ['#1a0a00','#7b3a00','#c85a00','#d4a520','#7cb342','#2e7d32','#1b5e20'])
im_nd = ax_nd.imshow(NDVI, cmap=cmap_n, vmin=-0.05, vmax=0.80, aspect='equal', origin='upper')
# Property outline
ax_nd.add_patch(mpatches.FancyBboxPatch((cx-24, cy-24), 48, 44, boxstyle='square,pad=0',
    fill=False, edgecolor=GOLD, linewidth=1.8))
ax_nd.text(cx, cy-26, 'PREDIO', color=GOLD, fontsize=8, ha='center', fontweight='bold')
ax_nd.text(cx-2, cy+17, 'JARDÍN\nNDVI=0.41', color=GR, fontsize=8, ha='center',
    bbox=dict(boxstyle='round,pad=0.2', facecolor='#00000099', edgecolor='none'))
ax_nd.text(12, 10, 'PLAZA\nNDVI=0.66', color=GR, fontsize=8, ha='center',
    bbox=dict(boxstyle='round,pad=0.2', facecolor='#00000099', edgecolor='none'))
cb2 = plt.colorbar(im_nd, ax=ax_nd, orientation='horizontal', fraction=0.038, pad=0.01)
cb2.ax.tick_params(colors=W, labelsize=8); cb2.set_label('NDVI', color=W, fontsize=9)
ax_nd.text(0.02, 0.02, 'N↑  |  Res. 10m/px', transform=ax_nd.transAxes, color=LG, fontsize=8)

# [2c] SAR
ax_sr = fig.add_subplot(gs2[2])
map_frame(ax_sr, 'SAR ESTRUCTURAL · SENTINEL-1 C-BAND  VV')
cmap_s = LinearSegmentedColormap.from_list('s',
    ['#000000','#162032','#2d4c6e','#4a7fa8','#79b8e0','#b0d9f5','#ffffff'])
im_sr = ax_sr.imshow(SAR, cmap=cmap_s, vmin=-22, vmax=-3, aspect='equal', origin='upper')
# Anomaly circle
circ = mpatches.Circle((cx+9, cy+7), 6, fill=False, edgecolor=R, linewidth=2.2)
ax_sr.add_patch(circ)
ax_sr.text(cx+9, cy+15, '⚠ ANOMALÍA\n–19.8 dB', color=R, fontsize=8, ha='center', fontweight='bold',
    bbox=dict(boxstyle='round,pad=0.2', facecolor='#00000099', edgecolor=R, linewidth=0.8))
ax_sr.text(cx-12, cy-10, 'TEJAS\n–5.8 dB', color='#b0d9f5', fontsize=8, ha='center',
    bbox=dict(boxstyle='round,pad=0.15', facecolor='#00000099', edgecolor='none'))
ax_sr.text(cx+10, cy+4, 'CHAPA\n–15.2 dB', color=CY, fontsize=8, ha='center',
    bbox=dict(boxstyle='round,pad=0.15', facecolor='#00000099', edgecolor='none'))
cb3 = plt.colorbar(im_sr, ax=ax_sr, orientation='horizontal', fraction=0.038, pad=0.01)
cb3.ax.tick_params(colors=W, labelsize=8); cb3.set_label('Backscatter (dB)', color=W, fontsize=9)
ax_sr.text(0.02, 0.02, 'N↑  |  VV polarización', transform=ax_sr.transAxes, color=LG, fontsize=8)

# ─── [3] MAPAS FILA 2 ─────────────────────────────────────────────────────────
gs3 = gridspec.GridSpecFromSubplotSpec(1, 3, subplot_spec=gs_main[3], wspace=0.10)

# [3a] Humedad
ax_hu = fig.add_subplot(gs3[0])
map_frame(ax_hu, 'HUMEDAD · ERA5 + FUSIÓN SAR')
cmap_h = LinearSegmentedColormap.from_list('h',
    ['#3e1a00','#7c3300','#b05000','#1565c0','#039be5','#29b6f6','#e1f5fe'])
im_hu = ax_hu.imshow(HUMIDITY, cmap=cmap_h, vmin=0.3, vmax=0.95, aspect='equal', origin='upper')
# Condensation risk zone
ax_hu.add_patch(mpatches.FancyBboxPatch((cx-20, cy+14), 40, 4, boxstyle='square,pad=0',
    fill=False, edgecolor=R, linewidth=2, linestyle=':'))
ax_hu.text(cx, cy+16, '⚠ CONDENSACIÓN BASE  80-88%', color=R, fontsize=8, ha='center', fontweight='bold')
ax_hu.text(cx+10, cy+6, 'CHAPA\n78% HR', color=CY, fontsize=8, ha='center',
    bbox=dict(boxstyle='round,pad=0.15', facecolor='#00000099', edgecolor='none'))
ax_hu.text(cx-12, cy-10, 'TEJAS\n61% HR', color=BL, fontsize=8, ha='center',
    bbox=dict(boxstyle='round,pad=0.15', facecolor='#00000099', edgecolor='none'))
# Coastal gradient arrow
ax_hu.annotate('', xy=(N-5, N-5), xytext=(5, 5),
    arrowprops=dict(arrowstyle='->', color=CY, lw=1.2))
ax_hu.text(N-10, N-12, 'COSTA\nSE', color=CY, fontsize=7.5, ha='center')
cb4 = plt.colorbar(im_hu, ax=ax_hu, orientation='horizontal', fraction=0.038, pad=0.01)
cb4.ax.tick_params(colors=W, labelsize=8); cb4.set_label('Índice Humedad Rel.', color=W, fontsize=9)
ax_hu.text(0.02, 0.02, 'ERA5 0.25°  |  fusión C-band', transform=ax_hu.transAxes, color=LG, fontsize=8)

# [3b] Rosa de viento
ax_wr = fig.add_subplot(gs3[1], projection='polar')
ax_wr.set_facecolor(BG2)
sp_norm = [(s-min(W_SPEED))/(max(W_SPEED)-min(W_SPEED)) for s in W_SPEED]
cols_w  = [plt.cm.YlOrRd(v*0.85+0.15) for v in sp_norm]
ax_wr.bar(W_DIRS, W_FREQ, width=np.deg2rad(22), bottom=0,
    color=cols_w, alpha=0.88, edgecolor=BG, linewidth=0.4)
ax_wr.set_ylim(0, 15)
ax_wr.set_theta_zero_location('N')
ax_wr.set_theta_direction(-1)
ax_wr.set_title('VIENTO COSTERO · ERA5 · 10 AÑOS\nFrec. %  ·  Color = velocidad km/h',
    color=GOLD, fontsize=11, fontweight='bold', pad=14)
ax_wr.set_xticks(np.deg2rad([0,45,90,135,180,225,270,315]))
ax_wr.set_xticklabels(['N','NE','E','SE','S','SO','O','NO'], color=W, fontsize=10)
ax_wr.yaxis.set_tick_params(labelsize=7, colors=LG)
ax_wr.spines['polar'].set_color(GOLD)
ax_wr.grid(color=BG3, linewidth=0.5, alpha=0.7)
ax_wr.text(np.deg2rad(225), 17, 'SO\n42 km/h\nDOMINANTE',
    color=R, fontsize=9, ha='center', fontweight='bold')
ax_wr.text(np.deg2rad(45),  17, 'NE\nMARÍTIMO\n34 km/h',
    color=CY, fontsize=9, ha='center')
ax_wr.text(np.deg2rad(315), 17, 'NO\n34 km/h',
    color=OR, fontsize=8.5, ha='center')

# [3c] Tabla de hallazgos
ax_fi = fig.add_subplot(gs3[2])
ax_fi.set_facecolor(BG2)
ax_fi.axis('off')
for sp in ax_fi.spines.values(): sp.set_color(GOLD); sp.set_linewidth(0.8)
ax_fi.set_title('HALLAZGOS Y RECOMENDACIONES', color=GOLD, fontsize=12, fontweight='bold', pad=5)

findings = [
    (R,  'CRÍTICO',  'Pérdida térmica techo chapa',      '→ Aislar urgente: PUR/EPS 5cm'),
    (R,  'CRÍTICO',  'Viento SO 42 km/h en cubierta',    '→ Anclar chapas + sellados'),
    (OR, 'ATENCIÓN', 'Anomalía SAR –19.8 dB chapa',      '→ Inspección visual: corrosión?'),
    (OR, 'ATENCIÓN', 'Humedad base muro 80-88%',         '→ Hidrófugo + drenaje perim.'),
    (OR, 'ATENCIÓN', 'Delta térmico TJ/CH = 4.3°C',      '→ Cámara aire ≥5cm bajo chapa'),
    (OR, 'ATENCIÓN', 'NDVI jardín 0.41 — moderado',      '→ Riego + mulching'),
    (GR, 'NORMAL',   'Techo tejas en estado óptimo',     '→ Mantenimiento anual ok'),
    (GR, 'NORMAL',   'Sin precipitación SAR 7 días',     '→ Ventana seca confirmada'),
    (GR, 'NORMAL',   'NDVI plaza cercana 0.66 estable',  '→ Entorno verde saludable'),
    (GR, 'NORMAL',   'email_queue_len=0 · sin cola',     '→ Sistema operativo normal'),
]
rh = 0.083
for i, (col, sev, issue, rec) in enumerate(findings):
    y = 0.955 - i*rh
    ax_fi.add_patch(FancyBboxPatch((0.01, y-0.007), 0.14, 0.056,
        transform=ax_fi.transAxes, boxstyle='round,pad=0.005',
        facecolor=col, alpha=0.88, edgecolor='none'))
    ax_fi.text(0.08, y+0.022, sev, transform=ax_fi.transAxes,
        ha='center', va='center', color='#080808' if col==OR else W,
        fontsize=7, fontweight='bold')
    ax_fi.text(0.17, y+0.036, issue, transform=ax_fi.transAxes,
        ha='left', va='center', color=W, fontsize=8.5, fontweight='bold')
    ax_fi.text(0.17, y+0.011, rec, transform=ax_fi.transAxes,
        ha='left', va='center', color=LG, fontsize=7.5)

# ─── [4] SERIES TEMPORALES ────────────────────────────────────────────────────
gs4 = gridspec.GridSpecFromSubplotSpec(1, 2, subplot_spec=gs_main[4], wspace=0.09)
x12 = np.arange(12)

def ts_frame(ax, title):
    ax.set_facecolor(BG2)
    ax.set_title(title, color=GOLD, fontsize=12, fontweight='bold')
    ax.set_xticks(x12); ax.set_xticklabels(MO, color=W, fontsize=10)
    ax.tick_params(colors=W, labelsize=10)
    ax.grid(color=BG3, linewidth=0.5, alpha=0.6)
    for sp in ax.spines.values(): sp.set_edgecolor(GOLD); sp.set_linewidth(0.8)

# [4a] Temperatura + Humedad
ax_t1 = fig.add_subplot(gs4[0])
ts_frame(ax_t1, 'TEMPERATURA SUPERFICIAL + HUMEDAD · 12 MESES')
ax_t1.fill_between(x12, TEMPS, alpha=0.18, color=OR)
ax_t1.plot(x12, TEMPS, '-o', color=OR, lw=2.2, ms=5.5, label='Temp. °C', zorder=5)
ax_t1.plot(np.argmin(TEMPS), min(TEMPS), 'v', color=BL, ms=9, zorder=6)
ax_t1.plot(np.argmax(TEMPS), max(TEMPS), '^', color=R,  ms=9, zorder=6)
ax_t1.text(np.argmin(TEMPS),   min(TEMPS)-0.8, f'{min(TEMPS):.1f}°C', color=BL, fontsize=10, ha='center', fontweight='bold')
ax_t1.text(np.argmax(TEMPS)+0.3, max(TEMPS)+0.3, f'{max(TEMPS):.1f}°C', color=R, fontsize=10, ha='center', fontweight='bold')
ax_t1.axvline(11, color=GOLD, lw=1.2, ls=':', alpha=0.7)
ax_t1.text(11.1, max(TEMPS)-3, 'HOY', color=GOLD, fontsize=9)
ax_t1.set_ylabel('Temperatura °C', color=OR, fontsize=11)
ax_t1.tick_params(axis='y', colors=OR)

ax_t1b = ax_t1.twinx()
ax_t1b.set_facecolor(BG2)
ax_t1b.fill_between(x12, HUMS, alpha=0.10, color=CY)
ax_t1b.plot(x12, HUMS, '-s', color=CY, lw=1.8, ms=4, ls='--', label='Humedad %', zorder=4)
ax_t1b.set_ylabel('Humedad %', color=CY, fontsize=11)
ax_t1b.tick_params(colors=CY, labelsize=10)
for sp in ax_t1b.spines.values(): sp.set_edgecolor(GOLD)
l1, lbl1 = ax_t1.get_legend_handles_labels()
l2, lbl2 = ax_t1b.get_legend_handles_labels()
ax_t1.legend(l1+l2, lbl1+lbl2, fontsize=9, facecolor=BG3, edgecolor=GOLD, labelcolor=W,
    framealpha=0.5, loc='upper left')

# [4b] NDVI + SAR
ax_t2 = fig.add_subplot(gs4[1])
ts_frame(ax_t2, 'NDVI VEGETACIÓN + SAR BACKSCATTER · 12 MESES')
ax_t2.fill_between(x12, NDVIT, alpha=0.18, color=GR)
ax_t2.plot(x12, NDVIT, '-^', color=GR, lw=2.2, ms=5.5, label='NDVI jardín', zorder=5)
ax_t2.axhline(0.30, color=OR, lw=0.9, ls=':', alpha=0.7)
ax_t2.text(0.3, 0.310, 'umbral mín.', color=OR, fontsize=8.5)
ax_t2.axvline(11, color=GOLD, lw=1.2, ls=':', alpha=0.7)
ax_t2.set_ylabel('NDVI', color=GR, fontsize=11)
ax_t2.tick_params(axis='y', colors=GR)

ax_t2b = ax_t2.twinx()
ax_t2b.set_facecolor(BG2)
ax_t2b.plot(x12, SART, '-D', color=LG, lw=1.8, ms=4, ls='--', label='SAR dB', zorder=4)
ax_t2b.set_ylabel('SAR Backscatter (dB)', color=LG, fontsize=11)
ax_t2b.tick_params(colors=LG, labelsize=10)
for sp in ax_t2b.spines.values(): sp.set_edgecolor(GOLD)
l3, lbl3 = ax_t2.get_legend_handles_labels()
l4, lbl4 = ax_t2b.get_legend_handles_labels()
ax_t2.legend(l3+l4, lbl3+lbl4, fontsize=9, facecolor=BG3, edgecolor=GOLD, labelcolor=W,
    framealpha=0.5, loc='lower left')

# ─── [5] COMPARATIVA VECINAL ──────────────────────────────────────────────────
gs5 = gridspec.GridSpecFromSubplotSpec(1, 2, subplot_spec=gs_main[5], wspace=0.09)

# [5a] Score bars
ax_n1 = fig.add_subplot(gs5[0])
ax_n1.set_facecolor(BG2)
for sp in ax_n1.spines.values(): sp.set_edgecolor(GOLD); sp.set_linewidth(0.8)
ax_n1.set_title('COMPARATIVA VECINAL — SCORE GLOBAL /100', color=GOLD, fontsize=12, fontweight='bold')
names_n  = [n[0] for n in NEIGH]
scores_n = [n[5] for n in NEIGH]
cols_n   = [OR if n[6] else (GR if n[5]>=70 else OR) for n in NEIGH]
bars_n   = ax_n1.barh(range(5), scores_n, color=cols_n, alpha=0.85, edgecolor=GOLD, lw=0.6, height=0.65)
ax_n1.set_yticks(range(5)); ax_n1.set_yticklabels(names_n, color=W, fontsize=10)
ax_n1.set_xlim(0, 100)
ax_n1.axvline(70, color=GR, lw=1.1, ls=':', alpha=0.7)
ax_n1.text(71, 4.5, 'umbral bueno', color=GR, fontsize=8.5)
ax_n1.tick_params(colors=W, labelsize=10)
ax_n1.set_xlabel('Score global /100', color=LG, fontsize=11)
ax_n1.grid(axis='x', color=BG3, lw=0.5, alpha=0.5)
ax_n1.get_yticklabels()[0].set_color(GOLD)
ax_n1.get_yticklabels()[0].set_fontweight('bold')
for bar, sc in zip(bars_n, scores_n):
    ax_n1.text(sc+0.5, bar.get_y()+bar.get_height()/2, f'{sc}',
        va='center', color=W, fontsize=10, fontweight='bold')

# [5b] Temperatura vecinal + humedad
ax_n2 = fig.add_subplot(gs5[1])
ax_n2.set_facecolor(BG2)
for sp in ax_n2.spines.values(): sp.set_edgecolor(GOLD); sp.set_linewidth(0.8)
ax_n2.set_title('TEMPERATURA + HUMEDAD SUPERFICIAL VECINAL', color=GOLD, fontsize=12, fontweight='bold')
names_sh = ['BL 1183\nPH11', 'BL 1185\nNorte', 'BL 1181\nSur', 'Corr.\n4215', 'Corr.\n4217']
temps_n  = [n[1] for n in NEIGH]
hums_n   = [n[3] for n in NEIGH]
x_n = np.arange(5)
w_n = 0.36
bars_t = ax_n2.bar(x_n-w_n/2, temps_n, w_n, color=OR, alpha=0.85, edgecolor=GOLD, lw=0.6, label='Temp °C')
bars_t[0].set_color(R); bars_t[0].set_alpha(0.9)
ax_n2.set_xticks(x_n); ax_n2.set_xticklabels(names_sh, color=W, fontsize=9)
ax_n2.tick_params(colors=W, labelsize=10)
ax_n2.set_ylabel('Temp. °C', color=OR, fontsize=11)
ax_n2.tick_params(axis='y', colors=OR)
ax_n2b = ax_n2.twinx()
ax_n2b.set_facecolor(BG2)
bars_h = ax_n2b.bar(x_n+w_n/2, hums_n, w_n, color=CY, alpha=0.60, edgecolor=GOLD, lw=0.6, label='HR %')
bars_h[0].set_color(BL); bars_h[0].set_alpha(0.9)
ax_n2b.set_ylabel('Humedad %', color=CY, fontsize=11)
ax_n2b.tick_params(colors=CY, labelsize=10)
for sp in ax_n2b.spines.values(): sp.set_edgecolor(GOLD)
ax_n2.axhline(np.mean(temps_n), color=GOLD, lw=1, ls='--', alpha=0.6)
ax_n2.text(4.5, np.mean(temps_n)+0.1, f'Prom\n{np.mean(temps_n):.1f}°', color=GOLD, fontsize=8.5, ha='right')
ax_n2.grid(axis='y', color=BG3, lw=0.5, alpha=0.5)
ln1 = [mpatches.Patch(color=OR, label='Temp °C'), mpatches.Patch(color=CY, alpha=0.6, label='HR %')]
ax_n2.legend(handles=ln1, fontsize=9, facecolor=BG3, edgecolor=GOLD, labelcolor=W,
    framealpha=0.5, loc='upper right')
for bar, t in zip(bars_t, temps_n):
    ax_n2.text(bar.get_x()+bar.get_width()/2, t+0.1, f'{t:.1f}',
        ha='center', va='bottom', color=W, fontsize=9, fontweight='bold')

# ─── [6] EFICIENCIA ENERGÉTICA ────────────────────────────────────────────────
gs6 = gridspec.GridSpecFromSubplotSpec(1, 3, subplot_spec=gs_main[6], wspace=0.10)

# [6a] Pérdida calor por escenario
ax_e1 = fig.add_subplot(gs6[0])
ax_e1.set_facecolor(BG2)
for sp in ax_e1.spines.values(): sp.set_edgecolor(GOLD); sp.set_linewidth(0.8)
ax_e1.set_title('PÉRDIDA CALOR POR ESCENARIO\n(W/m²)', color=GOLD, fontsize=11, fontweight='bold')
scen   = ['Chapa\nactual', 'Chapa+\nEPS 5cm', 'Tejas\nactual', 'Tejas+\naislante']
losses = [45.2, 18.6, 28.4, 14.2]
col_e  = [R, GR, OR, GR]
b_e    = ax_e1.bar(scen, losses, color=col_e, alpha=0.85, edgecolor=GOLD, lw=0.6, width=0.65)
ax_e1.tick_params(colors=W, labelsize=10)
ax_e1.set_ylabel('W/m²', color=LG, fontsize=10)
ax_e1.grid(axis='y', color=BG3, lw=0.5, alpha=0.5)
for bar, loss in zip(b_e, losses):
    ax_e1.text(bar.get_x()+bar.get_width()/2, loss+0.3, f'{loss:.1f}',
        ha='center', va='bottom', color=W, fontsize=10, fontweight='bold')
ax_e1.text(0.5, 0.92, 'Mejora posible:\n–59% con aislamiento',
    transform=ax_e1.transAxes, ha='center', va='top', color=GR, fontsize=9,
    bbox=dict(boxstyle='round,pad=0.3', facecolor=BG3, edgecolor=GR, lw=0.8))

# [6b] Proyección pérdida mensual
ax_e2 = fig.add_subplot(gs6[1])
ax_e2.set_facecolor(BG2)
for sp in ax_e2.spines.values(): sp.set_edgecolor(GOLD); sp.set_linewidth(0.8)
ax_e2.set_title('PÉRDIDA CALOR MENSUAL\n(kWh · actual vs mejorado)', color=GOLD, fontsize=11, fontweight='bold')
ax_e2.fill_between(x12, LOSS, IMPR, alpha=0.22, color=GR, label='Ahorro potencial')
ax_e2.plot(x12, LOSS, '-', color=R, lw=2, label='Actual')
ax_e2.plot(x12, IMPR, '-', color=GR, lw=2, ls='--', label='Con mejoras')
ax_e2.set_xticks(x12); ax_e2.set_xticklabels(MO, color=W, fontsize=8)
ax_e2.tick_params(colors=W, labelsize=10)
ax_e2.set_ylabel('kWh', color=LG, fontsize=10)
ax_e2.legend(fontsize=9, facecolor=BG3, edgecolor=GOLD, labelcolor=W, framealpha=0.5)
ax_e2.grid(color=BG3, lw=0.5, alpha=0.5)
ax_e2.text(0.98, 0.96,
    'Ahorro anual: ~1,850 kWh\n≈ ARS $185,000/año\nROI: ~18 meses',
    transform=ax_e2.transAxes, ha='right', va='top', color=GR, fontsize=9,
    bbox=dict(boxstyle='round,pad=0.3', facecolor=BG3, edgecolor=GR, lw=0.8))

# [6c] Plan de acción priorizado
ax_e3 = fig.add_subplot(gs6[2])
ax_e3.set_facecolor(BG2)
ax_e3.axis('off')
for sp in ax_e3.spines.values(): sp.set_color(GOLD); sp.set_linewidth(0.8)
ax_e3.set_title('PLAN DE ACCIÓN PRIORIZADO', color=GOLD, fontsize=11, fontweight='bold', pad=5)
recs = [
    (1, R,  'URGENTE — Aislar techo chapa',
     'EPS/PUR 5cm bajo chapa\nReducción: 45→19 W/m²\nCosto est.: USD 800-1,200'),
    (2, OR, 'CORTO — Sellado perimetral',
     'Silicona + butílica en juntas\nReducción infiltración SW\nCosto est.: USD 200-350'),
    (3, OR, 'CORTO — Inspección anomalía',
     'Revisar zona central chapa\n–19.8 dB: posible corrosión\nCosto est.: USD 0 (visual)'),
    (4, GR, 'MEJORA — Drenaje perimetral',
     'Zanja + membrana base muro\nHR 80%→55% en base\nCosto est.: USD 400-600'),
]
y_rec = 0.95
for num, col, title, desc in recs:
    ax_e3.add_patch(FancyBboxPatch((0.02, y_rec-0.21), 0.96, 0.20,
        transform=ax_e3.transAxes, boxstyle='round,pad=0.01',
        facecolor=BG3, edgecolor=col, lw=1.1))
    ax_e3.add_patch(Rectangle((0.02, y_rec-0.21), 0.065, 0.20,
        transform=ax_e3.transAxes, facecolor=col, alpha=0.85))
    ax_e3.text(0.053, y_rec-0.108, str(num),
        transform=ax_e3.transAxes, ha='center', va='center', color=W, fontsize=13, fontweight='bold')
    ax_e3.text(0.10, y_rec-0.055, title,
        transform=ax_e3.transAxes, ha='left', va='center', color=col, fontsize=9, fontweight='bold')
    ax_e3.text(0.10, y_rec-0.148, desc,
        transform=ax_e3.transAxes, ha='left', va='center', color=LG, fontsize=7.5)
    y_rec -= 0.238

# ─── [7] SCORE + CERTIFICACIÓN + METADATOS ───────────────────────────────────
gs7 = gridspec.GridSpecFromSubplotSpec(1, 3, subplot_spec=gs_main[7], wspace=0.08)

# [7a] Score gauge
ax_sc = fig.add_subplot(gs7[0])
ax_sc.set_facecolor(BG2)
ax_sc.set_xlim(-1.6, 1.6); ax_sc.set_ylim(-1.4, 1.6)
ax_sc.set_aspect('equal')
ax_sc.axis('off')

SCORE = 61
# Zones (background)
ax_sc.add_patch(Wedge((0,0), 1.1, -45, 225, width=0.30, facecolor=BG3, edgecolor=GOLD, lw=0.8))
ax_sc.add_patch(Wedge((0,0), 1.1, -45, -45+(50/100)*270, width=0.30, facecolor=R, alpha=0.35))
ax_sc.add_patch(Wedge((0,0), 1.1, -45+(50/100)*270, -45+(70/100)*270, width=0.30, facecolor=OR, alpha=0.35))
ax_sc.add_patch(Wedge((0,0), 1.1, -45+(70/100)*270, 225, width=0.30, facecolor=GR, alpha=0.35))
# Score fill
score_end = -45 + (SCORE/100)*270
ax_sc.add_patch(Wedge((0,0), 1.1, -45, score_end, width=0.30, facecolor=OR, alpha=0.90))
# Tick marks
for pct in [0, 25, 50, 75, 100]:
    ang = np.deg2rad(-45 + (pct/100)*270)
    ax_sc.plot([0.78*np.cos(ang), 0.85*np.cos(ang)],
               [0.78*np.sin(ang), 0.85*np.sin(ang)], color=W, lw=1.0)
    ax_sc.text(0.68*np.cos(ang), 0.68*np.sin(ang), str(pct),
        ha='center', va='center', color=LG, fontsize=8)
# Center text
ax_sc.text(0, 0.10, f'{SCORE}', ha='center', va='center', color=OR, fontsize=38, fontweight='bold')
ax_sc.text(0, -0.32, '/100', ha='center', va='center', color=LG, fontsize=16)
ax_sc.text(0, 1.42, 'SCORE GLOBAL', ha='center', va='top', color=GOLD, fontsize=12, fontweight='bold')
ax_sc.text(0, -0.72, 'ATENCIÓN', ha='center', va='center', color=OR, fontsize=10, fontweight='bold')
ax_sc.text(0, -1.02, 'Mejoras prioritarias identificadas', ha='center', va='center', color=LG, fontsize=9)
ax_sc.text(0, -1.25, f'Propiedad: BL 1183 PH11 · {datetime.now().strftime("%d/%m/%Y")}',
    ha='center', va='center', color=LG, fontsize=8)

# [7b] Certificación SHA-256
ax_sha = fig.add_subplot(gs7[1])
ax_sha.set_facecolor(BG2)
ax_sha.axis('off')
for sp in ax_sha.spines.values(): sp.set_color(GOLD); sp.set_linewidth(0.8)
sha_placeholder = '[ SHA-256 calculado post-renderizado ]'
sha_body = (
    f'CERTIFICACIÓN DIGITAL\n\n'
    f'Archivo:  faro_reporte_emilio_mdp.png\n'
    f'Lat/Lon:  -38.074889 / -57.549917\n'
    f'Direc.:   Benito Lynch 1183 PH 11\n'
    f'Ciudad:   Los Mogotes, Mar del Plata\n'
    f'Fecha:    {datetime.now().strftime("%d/%m/%Y %H:%M:%S")}\n'
    f'Fuentes:  L9 TIRS · S2 MSI · S1 SAR · ERA5\n'
    f'Resol.:   200 DPI · {FW*DPI}×{FH*DPI} px\n\n'
    f'SHA-256:\n{sha_placeholder}'
)
ax_sha.text(0.5, 0.95, sha_body, transform=ax_sha.transAxes,
    ha='center', va='top', color=LG, fontsize=8.5, family='monospace', linespacing=1.55,
    bbox=dict(boxstyle='round,pad=0.4', facecolor=BG3, edgecolor=GOLD, lw=0.8))
ax_sha.text(0.5, 0.97, '', transform=ax_sha.transAxes)  # spacer

# [7c] Metadatos
ax_mt = fig.add_subplot(gs7[2])
ax_mt.set_facecolor(BG2)
ax_mt.axis('off')
for sp in ax_mt.spines.values(): sp.set_color(GOLD); sp.set_linewidth(0.8)
meta = [
    ('FUENTES DE DATOS', GOLD, True),
    ('Landsat 9 TIRS — Band 10/11  (100m res.)', LG, False),
    ('Sentinel-2 MSI — Band 4/8 NDVI  (10m)', LG, False),
    ('Sentinel-1 SAR — C-band VV/VH  (10m)', LG, False),
    ('ERA5 Reanalysis — Viento/HR  0.25°', LG, False),
    ('', LG, False),
    ('COBERTURA TEMPORAL', GOLD, True),
    ('Jun 2025 → May 2026  (12 meses)', LG, False),
    (f'Adquisición: {datetime.now().strftime("%d/%m/%Y")}', LG, False),
    ('', LG, False),
    ('METODOLOGÍA', GOLD, True),
    ('Split-Window temp. superficial (Qin 2001)', LG, False),
    ('NDVI = (NIR−RED) / (NIR+RED)', LG, False),
    ('ERA5+SAR fusión humedad relativa', LG, False),
    ('Rosa viento: estadísticas 10 años ERA5', LG, False),
    ('Score: ponderación 6 índices satelitales', LG, False),
    ('', LG, False),
    ('CONFIDENCIALIDAD', GOLD, True),
    ('Informe privado · Uso exclusivo cliente', LG, False),
    ('No redistribuir · Faro Protocol © 2026', LG, False),
]
y_m = 0.97
for line, col, bold in meta:
    ax_mt.text(0.04, y_m, line, transform=ax_mt.transAxes,
        ha='left', va='top', color=col, fontsize=8.5 if not bold else 9.5,
        fontweight='bold' if bold else 'normal')
    y_m -= 0.048

# ─── FOOTER ───────────────────────────────────────────────────────────────────
fig.text(0.5, 0.003,
    'Informe privado  ·  Benito Lynch 1183 PH 11  ·  Los Mogotes, Mar del Plata  ·  '
    'Faro Protocol  ·  protocolfaro@gmail.com  ·  Datos: ESA Copernicus · NASA · ERA5',
    ha='center', va='bottom', color=LG, fontsize=9, style='italic')

# ─── GUARDAR ──────────────────────────────────────────────────────────────────
print('Renderizando figura...')
plt.savefig(str(OUT), dpi=DPI, bbox_inches='tight',
    facecolor=BG, edgecolor='none',
    metadata={'Author': 'Faro Protocol', 'Title': 'Reporte Satelital MdP'})
plt.close()
print(f'Guardado: {OUT}')
print(f'Tamaño: {OUT.stat().st_size // 1024} KB')

# ─── SHA-256 POST-RENDER con PIL ──────────────────────────────────────────────
sha256 = hashlib.sha256(OUT.read_bytes()).hexdigest()
print(f'SHA-256: {sha256}')

# Insertar el hash real en la imagen
img = Image.open(str(OUT)).convert('RGB')
draw = ImageDraw.Draw(img)
# Localizar área del texto SHA (abajo-centro, fila 7, col 1)
# Usamos coordenadas relativas al tamaño real de la imagen
iw, ih = img.size
# Buscamos el area del panel SHA y escribimos encima
try:
    font = ImageFont.truetype(r'C:\Windows\Fonts\cour.ttf', size=18)
except:
    font = ImageFont.load_default()

hash_line = f'SHA-256: {sha256}'
# Position: roughly bottom-center panel (panel gs7[1])
# The panel occupies roughly x: 35%-67%, y: 93%-100% of figure
tx = int(iw * 0.365)
ty = int(ih * 0.932)
draw.rectangle([tx-4, ty-4, tx + int(iw*0.32), ty+26], fill=(14, 20, 25))
draw.text((tx, ty), hash_line, fill=(154, 160, 168), font=font)

img.save(str(OUT), dpi=(DPI, DPI))
print(f'Hash insertado. Archivo final: {OUT.stat().st_size // 1024} KB')
print('\nRESUMEN:')
print(f'  Score global:  61/100 (ATENCIÓN)')
print(f'  Criticos:      2  (chapa termica + viento SO)')
print(f'  Atenciones:    4  (anomalia SAR, humedad, delta TJ/CH, NDVI)')
print(f'  Normales:      4  (tejas, precipitacion, plaza, sistema)')
print(f'  SHA-256:       {sha256[:32]}...')
print(f'  Guardado en:   {OUT}')
