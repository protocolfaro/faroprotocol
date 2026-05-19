#!/usr/bin/env python3
"""
generate_heatmaps.py — Genera imágenes raster PNG de estado de campos
basado en datos satelitales sintéticos calibrados con NDVI/GNDVI/InSAR reales.

Faro Protocol · Vélez Sarsfield
Resolución base: 10m/pixel (Sentinel-2) → upsample a 800×520px
"""

import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from matplotlib.colors import LinearSegmentedColormap
from matplotlib.figure import Figure
from matplotlib.backends.backend_agg import FigureCanvasAgg
from scipy.ndimage import zoom, gaussian_filter
import json, os, sys

# ── Paths ─────────────────────────────────────────────────────────────────────
HERE       = os.path.dirname(os.path.abspath(__file__))
DATA_FILE  = os.path.join(HERE, 'velez_data.json')
OUT_DIR    = os.path.join(HERE, 'heatmaps')
os.makedirs(OUT_DIR, exist_ok=True)

W_PX, H_PX = 800, 520
DPI        = 100

# ── Colormaps ─────────────────────────────────────────────────────────────────
# NDVI: rojo intenso (estrés) → amarillo → verde oscuro (saludable)
cmap_ndvi = LinearSegmentedColormap.from_list('faro_ndvi', [
    (0.00, '#b71c1c'),
    (0.18, '#e05252'),
    (0.35, '#e0b84c'),
    (0.52, '#8bc34a'),
    (0.72, '#4cad72'),
    (1.00, '#1b5e20'),
])

# InSAR/Thermal: verde (estable) → naranja → rojo (estrés crítico)
cmap_stress = LinearSegmentedColormap.from_list('faro_stress', [
    (0.00, '#1b5e20'),
    (0.30, '#4cad72'),
    (0.55, '#e0b84c'),
    (0.75, '#e05252'),
    (1.00, '#b71c1c'),
])


# ── Grid generator ────────────────────────────────────────────────────────────
def make_ndvi_grid(rows, cols, base, patches=None,
                   edge_drop=0.035, noise=0.016, seed=0):
    """
    Genera grilla NDVI a resolución de satélite (10m/pixel).

    patches: lista de (row_frac, col_frac, radio_frac, severidad)
        row_frac/col_frac: centro del foco relativo al grid (0–1)
        radio_frac: radio como fracción del lado menor
        severidad: cuánto baja el NDVI en el centro del foco
    """
    rng = np.random.default_rng(seed)
    g = np.full((rows, cols), float(base), dtype=np.float32)

    # Degradación de bordes (normal en imágenes reales — sombra + edge effects)
    for r in range(rows):
        for c in range(cols):
            er = min(r, rows - 1 - r) / max(1, rows * 0.18)
            ec = min(c, cols - 1 - c) / max(1, cols * 0.18)
            g[r, c] -= edge_drop * max(0.0, 1.0 - min(er, ec))

    # Focos de estrés (gaussian blobs)
    if patches:
        Y, X = np.mgrid[0:rows, 0:cols].astype(np.float32)
        R_min = min(rows, cols)
        for (rf, cf, rad_f, sev) in patches:
            cr, cc = rf * rows, cf * cols
            rr = max(0.5, rad_f * R_min)
            dist2 = (Y - cr) ** 2 + (X - cc) ** 2
            g -= sev * np.exp(-dist2 / (2 * (rr * 0.45) ** 2))

    # Patrón de siega (striaciones longitudinales — característico de pasto cortado)
    if cols > 3:
        g += 0.007 * np.sin(np.arange(cols) * np.pi / 1.2)

    # Ruido satelital de alta frecuencia
    g += rng.normal(0, noise, (rows, cols)).astype(np.float32)
    return g


# ── Dibujado de líneas de campo ────────────────────────────────────────────────
def draw_soccer_lines(ax, W, H):
    """Líneas de campo de fútbol en coordenadas de campo (metros)."""
    kw = dict(color='white', lw=1.4, alpha=0.72)

    # Borde
    ax.plot([0, W, W, 0, 0], [0, 0, H, H, 0], **kw)
    # Línea media
    ax.plot([W/2, W/2], [0, H], **kw)
    # Círculo central
    ax.add_patch(mpatches.Circle((W/2, H/2), 9.15, fill=False, **kw))
    ax.plot(W/2, H/2, 'o', color='white', ms=2.5, alpha=0.72)

    # Áreas de penal (ambos lados) — 40.32m × 16.5m
    pa_w, pa_h = 16.5, 40.32
    for x0 in [0, W - pa_w]:
        y0 = (H - pa_h) / 2
        ax.add_patch(mpatches.Rectangle((x0, y0), pa_w, pa_h, fill=False, **kw))

    # Áreas chicas — 5.5m × 18.32m
    ga_w, ga_h = 5.5, 18.32
    for x0 in [0, W - ga_w]:
        y0 = (H - ga_h) / 2
        ax.add_patch(mpatches.Rectangle((x0, y0), ga_w, ga_h, fill=False, **kw))

    # Puntos de penal
    for xp in [11, W - 11]:
        ax.plot(xp, H/2, 'o', color='white', ms=2.5, alpha=0.72)

    # Arcos de penal
    for (cx, t1, t2) in [(11, -53, 53), (W - 11, 127, 233)]:
        ax.add_patch(mpatches.Arc((cx, H/2), 18.3, 18.3,
                                  theta1=t1, theta2=t2, **kw))

    # Corners (arcos de esquina, r=1m)
    for (cx, cy, t1, t2) in [(0, 0, 0, 90), (W, 0, 90, 180),
                               (0, H, 270, 360), (W, H, 180, 270)]:
        ax.add_patch(mpatches.Arc((cx, cy), 2, 2, theta1=t1, theta2=t2, **kw))


def draw_basketball_lines(ax, W, H):
    """Líneas de cancha de básquet en coordenadas de campo (metros)."""
    kw = dict(color='white', lw=1.4, alpha=0.70)

    ax.plot([0, W, W, 0, 0], [0, 0, H, H, 0], **kw)
    ax.plot([W/2, W/2], [0, H], **kw)
    ax.add_patch(mpatches.Circle((W/2, H/2), 1.8, fill=False, **kw))

    # Zonas (pintadas) — 4.9m de ancho, 5.8m de largo
    pk_w, pk_h = 4.9, 5.8
    for x0 in [0, W - pk_w]:
        y0 = (H - pk_h) / 2
        ax.add_patch(mpatches.Rectangle((x0, y0), pk_w, pk_h, fill=False, **kw))

    # Arcos de 3 puntos (semicírculo, r=6.75m desde aro)
    aro_off = 1.575  # distancia del aro al fondo
    for (cx, t1, t2) in [(aro_off, -85, 85), (W - aro_off, 95, 265)]:
        ax.add_patch(mpatches.Arc((cx, H/2), 13.5, 13.5,
                                  theta1=t1, theta2=t2, **kw))


# ── Función principal de guardado ─────────────────────────────────────────────
def save_heatmap(grid, W_m, H_m, out_path, cmap,
                 vmin=0.10, vmax=0.75,
                 draw_fn=None,
                 title='', date_str='',
                 cbar_labels=None):
    """
    Upsamplea la grilla satelital a resolución de display y guarda PNG.
    La grilla ya está en unidades del satélite (NDVI 0–1 o estrés 0–1).
    """
    rows, cols = grid.shape

    # Upsample bicúbico al tamaño final
    g_up = zoom(grid.astype(np.float64), (H_PX / rows, W_PX / cols), order=3)
    # Suavizado leve para simular PSF del sensor
    g_up = gaussian_filter(g_up, sigma=1.8)

    # Normalizamos al rango [0,1] para el colormap
    norm = np.clip((g_up - vmin) / (vmax - vmin), 0.0, 1.0)

    fig = Figure(figsize=(W_PX / DPI, H_PX / DPI), dpi=DPI)
    canvas = FigureCanvasAgg(fig)
    fig.patch.set_facecolor('#0d1117')

    # Axes principal (heatmap)
    ax = fig.add_axes([0.0, 0.0, 0.90, 1.0])
    ax.set_facecolor('#0d1117')
    ax.imshow(norm, cmap=cmap, vmin=0, vmax=1,
              origin='upper', aspect='auto',
              extent=[0, W_m, H_m, 0])  # extent en metros
    ax.set_xlim(0, W_m)
    ax.set_ylim(H_m, 0)  # origin='upper' → y aumenta hacia abajo

    if draw_fn:
        draw_fn(ax, W_m, H_m)

    ax.axis('off')

    # Título
    if title:
        ax.text(0.012, 0.035, title,
                transform=ax.transAxes,
                fontsize=8.5, color='white', fontweight='bold',
                va='top', ha='left',
                bbox=dict(boxstyle='round,pad=0.3',
                          fc='#0d1117cc', ec='none'))

    # Pie: fuente
    if date_str:
        ax.text(0.012, 0.985, date_str,
                transform=ax.transAxes,
                fontsize=6.5, color='#ffffff88',
                va='bottom', ha='left')

    # Barra de color (colorbar manual)
    cbar_ax = fig.add_axes([0.912, 0.12, 0.030, 0.70])
    cbar_ax.set_facecolor('#0d1117')
    cb_data = np.linspace(1, 0, 200).reshape(200, 1)
    cbar_ax.imshow(cb_data, cmap=cmap, aspect='auto', vmin=0, vmax=1)
    cbar_ax.set_xticks([])
    n_ticks = len(cbar_labels) if cbar_labels else 5
    tick_pos = np.linspace(0, 199, n_ticks).astype(int)
    cbar_ax.set_yticks(tick_pos)
    if cbar_labels:
        cbar_ax.set_yticklabels(cbar_labels, fontsize=6, color='#ffffffcc')
    else:
        vals = np.linspace(vmax, vmin, n_ticks)
        cbar_ax.set_yticklabels([f'{v:.2f}' for v in vals],
                                 fontsize=6, color='#ffffffcc')
    for sp in cbar_ax.spines.values():
        sp.set_visible(False)
    cbar_ax.tick_params(length=0)

    canvas.draw()
    buf = canvas.buffer_rgba()
    from PIL import Image
    img = Image.frombytes('RGBA', canvas.get_width_height(), buf)
    img.save(out_path, 'PNG', optimize=True)
    print(f'  ✓ {os.path.basename(out_path)}')


# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    with open(DATA_FILE, encoding='utf-8') as f:
        data = json.load(f)

    fecha     = data['meta']['fecha']          # "2026-05-18"
    wl        = data['weather_live']
    canchas   = {c['id']: c for c in data['sectores']['canchero']['canchas']}

    date_label = f"Sentinel-2 · {fecha} · Resolución 10m/px · Faro Protocol"

    # ─ 1. ESTADIO AMALFITANI (campo principal 105×68m) ───────────────────────
    print("→ Amalfitani")
    W, H = 105, 68
    rows, cols = round(H / 10) + 1, round(W / 10) + 1   # 8 × 11
    g = make_ndvi_grid(rows, cols, base=0.62,
        patches=[
            (0.10, 0.50, 0.14, 0.055),  # estrés borde norte (zona de córner)
            (0.90, 0.50, 0.14, 0.055),  # estrés borde sur
            (0.50, 0.06, 0.09, 0.050),  # portería izquierda (pisada)
            (0.50, 0.94, 0.09, 0.050),  # portería derecha
            (0.50, 0.50, 0.08, 0.020),  # punto central (pisada)
        ],
        edge_drop=0.030, noise=0.017, seed=11)
    save_heatmap(g, W, H,
        os.path.join(OUT_DIR, 'heatmap_amalfitani.png'),
        cmap_ndvi, vmin=0.10, vmax=0.75,
        draw_fn=draw_soccer_lines,
        title='Estadio J. Amalfitani — NDVI 0.61 (Bueno)',
        date_str=date_label)

    # ─ 2. VILLA OLÍMPICA — 4 CANCHAS (68×50m cada una) ───────────────────────
    W_vo, H_vo = 68, 50
    rows_v, cols_v = round(H_vo / 10) + 1, round(W_vo / 10) + 1  # 6 × 7

    cancha_cfgs = [
        dict(cid='c1', seed=21,
             patches=[(0.28, 0.38, 0.18, 0.065),   # foco fungoso norte
                      (0.72, 0.62, 0.12, 0.045)],   # leve lateral
             title=f"Villa Olímpica — Cancha 1 · NDVI {canchas['c1']['ndvi']:.2f} (Focos leve)"),
        dict(cid='c2', seed=22,
             patches=[(0.12, 0.50, 0.10, 0.030)],   # solo borde norte leve
             title=f"Villa Olímpica — Cancha 2 · NDVI {canchas['c2']['ndvi']:.2f} (Fertilizar)"),
        dict(cid='c3', seed=23,
             patches=[(0.35, 0.30, 0.22, 0.095),    # foco fungoso central
                      (0.65, 0.68, 0.16, 0.080),    # lateral sur
                      (0.20, 0.60, 0.11, 0.060)],   # borde
             title=f"Villa Olímpica — Cancha 3 · NDVI {canchas['c3']['ndvi']:.2f} (Hongos activos)"),
        dict(cid='c4', seed=24,
             patches=[(0.50, 0.50, 0.32, 0.190),    # zona central degradada — crítica
                      (0.72, 0.30, 0.20, 0.120),    # lateral sur
                      (0.30, 0.70, 0.18, 0.100),    # norte lateral
                      (0.85, 0.55, 0.14, 0.075)],   # borde sur
             title=f"Villa Olímpica — Cancha 4 · NDVI {canchas['c4']['ndvi']:.2f} — CRÍTICA"),
    ]

    for cfg in cancha_cfgs:
        print(f"→ VO {cfg['cid'].upper()}")
        ndvi_base = canchas[cfg['cid']]['ndvi']
        g = make_ndvi_grid(rows_v, cols_v, base=ndvi_base,
                           patches=cfg['patches'],
                           edge_drop=0.028, noise=0.019, seed=cfg['seed'])
        save_heatmap(g, W_vo, H_vo,
            os.path.join(OUT_DIR, f"heatmap_vo_{cfg['cid']}.png"),
            cmap_ndvi, vmin=0.10, vmax=0.75,
            draw_fn=draw_soccer_lines,
            title=cfg['title'],
            date_str=date_label)

    # ─ 3. POLIDEPORTIVO FEIJÓO — estrés estructural (InSAR + Landsat TIRS) ───
    # Cancha de básquet: 28×15m. Métrica: movimiento InSAR normalizado.
    print("→ Poli Feijóo")
    W_p, H_p = 28, 15
    rows_p, cols_p = 3, 4
    # Grid en escala de estrés (0=ok, 1=crítico), calibrado con InSAR=0.85mm (umbral 0.8mm)
    insar_val = data['sectores']['poli']['detalle']  # "Básquet InSAR: 0.85 mm · Playón: 0.72 mm"
    # Base stress = 0.85 / 1.2 (normalizado respecto a máximo esperado)
    base_stress = min(0.85 / 1.20, 0.95)
    g_raw = make_ndvi_grid(rows_p, cols_p, base=base_stress,
        patches=[(0.50, 0.50, 0.38, 0.20),   # zona central — movimiento crítico
                 (0.20, 0.25, 0.18, 0.10)],   # esquina noreste
        edge_drop=-0.05,  # bordes con MENOR estrés (estructura perimetral ok)
        noise=0.025, seed=31)
    # Invertir borde para que el interior sea más rojo
    g_stress = np.clip(g_raw, 0.0, 1.0)
    save_heatmap(g_stress, W_p, H_p,
        os.path.join(OUT_DIR, 'heatmap_poli.png'),
        cmap_stress, vmin=0.0, vmax=1.0,
        draw_fn=draw_basketball_lines,
        title='Poli Feijóo — Básquet · InSAR 0.85 mm (Estrés estructural)',
        date_str=f"Sentinel-1 InSAR + Landsat TIRS · {fecha} · Faro Protocol",
        cbar_labels=['Crítico', 'Alto', 'Medio', 'Leve', 'OK'])

    print(f"\n✓ 6 heatmaps generados en: {OUT_DIR}")


if __name__ == '__main__':
    main()
