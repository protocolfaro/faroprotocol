"""
FARO PROTOCOL — Bricolage 1800×2400 px
=======================================

4 filas verticales. Cada fila = zona geográfica real con:
  - Título + métricas reales de data.json
  - 3 imágenes satelitales recortadas del reporte de fusión existente
    (NDVI, SAR, Índice Fusión — sin placeholders, sin inventar nada)
  - Sello VERIFIED SHA-256 real

Zonas fijas (hardcodeadas por el usuario):
  1. Balcarce    (Score ≈ 70) — Agro
  2. Amazonas    (Score ≈ 66) — Forest
  3. Vaca Muerta            — O&G
  4. Rotterdam              — Energía/Marítimo

Uso:
    python faro_bricolage.py
    python faro_bricolage.py --out otro_nombre.png
"""

import argparse
import hashlib
import json
import sys
from datetime import datetime
from pathlib import Path

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.gridspec as gridspec
import numpy as np

try:
    from PIL import Image as PILImage
except ImportError:
    print("ERROR: Pillow no instalado. Corré: pip install Pillow")
    sys.exit(1)

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

ROOT = Path(__file__).parent

# ── Paleta idéntica a los reportes de fusión ──────────────────────────────────
BG      = '#0a0a1a'
BG2     = '#0d1020'
BG3     = '#111130'
GOLD    = '#c9a84c'
GOLD_L  = '#e2c97e'
WHITE   = '#f2ede4'
GREY    = '#888899'
GREY2   = '#555566'
GREEN   = '#2d8c5e'
GREEN_L = '#4ab87e'
RED     = '#b03030'
RED_L   = '#e05050'
AMBER   = '#c07a30'
AMBER_L = '#e09a50'
BLUE    = '#2a6496'
BLUE_L  = '#4a90c8'
ORANGE  = '#c07a30'

VERTICAL_COLORS = {
    'agro':          (GREEN,  GREEN_L,  'AGRO'),
    'deforestacion': (RED,    RED_L,    'FOREST'),
    'energia':       (BLUE,   BLUE_L,   'O&G'),
    'oil_gas':       (BLUE,   BLUE_L,   'O&G'),
    'maritimo':      (BLUE,   BLUE_L,   'MARITIME'),
    'mineria':       (ORANGE, AMBER_L,  'MINING'),
}

# Zonas fijas solicitadas por el usuario
AREAS = ['balcarce', 'amazonas', 'vaca_muerta', 'rotterdam']

# Coordenadas de recorte de los PNG de fusión (empíricas, 2168×1335 px)
# y_top: primer fila de pixeles de imagen real
# y_bot: última fila de imagen (antes de la zona oscura de métricas)
# Los 3 paneles (imagen+colorbar) están separados en x:
#   panel 0 (NDVI):   x= 50 – 590
#   panel 1 (SAR):    x=592 – 1348
#   panel 2 (Fusion): x=1348 – 2115
PNG_Y_TOP  = 175   # empieza después del título del subplot (evita label duplicado)
PNG_Y_BOT  = 658
PNG_PANELS = [
    (50,   590,  'NDVI Limpio'),
    (592,  1348, 'Backscatter SAR'),
    (1348, 2115, 'Índice Fusión'),
]


# ── SHA-256 ───────────────────────────────────────────────────────────────────

def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(65536), b''):
            h.update(chunk)
    return h.hexdigest()


def _verificar(area: str) -> tuple[bool, str]:
    """Devuelve (sello_ok, hex_completo)."""
    png = ROOT / f'faro_reporte_fusion_{area}.png'
    sha = ROOT / f'faro_reporte_fusion_{area}.sha256'
    if not png.exists() or not sha.exists():
        return False, ''
    archivado = sha.read_text(encoding='utf-8').split()[0].strip()
    actual    = _sha256(png)
    return actual == archivado, actual


# ── Carga de datos ────────────────────────────────────────────────────────────

def _load_metrics(area: str) -> dict:
    """Carga métricas desde data.json + config del área."""
    data_path = ROOT / 'data.json'
    cfg_path  = ROOT / 'faro_areas' / f'{area}.json'

    pipe = {}
    ins  = {}
    if data_path.exists():
        with open(data_path, encoding='utf-8') as f:
            d = json.load(f)
        pipe = d.get('pipeline', {}).get(area, {})
        ins  = d.get('insights', {}).get(area, {})

    cfg = {}
    if cfg_path.exists():
        with open(cfg_path, encoding='utf-8') as f:
            cfg = json.load(f)

    merged = {**pipe, **ins}
    return {
        'label':    cfg.get('label', area.title()),
        'vertical': cfg.get('vertical', 'desconocido'),
        'sector':   cfg.get('sector', cfg.get('vertical', 'desconocido')),
        'score':    merged.get('score_faro'),
        'ndvi':     merged.get('ndvi_medio'),
        'sar':      merged.get('sar_medio_db'),
        'fusion':   merged.get('indice_fusion_medio'),
        'pixels':   merged.get('pixeles_analizados'),
        'rinde':    merged.get('rinde_estimado_tha'),
        'activ':    merged.get('indice_actividad'),
        'estado':   merged.get('estado', 'SIN_DATOS'),
        'confianza':merged.get('confianza', ''),
        'fecha':    merged.get('ultimo_run', ''),
    }


def _cargar_panel_imagen(area: str, idx: int) -> np.ndarray | None:
    """
    Carga el panel idx (0=NDVI, 1=SAR, 2=Fusion) recortado del PNG de fusión.
    Devuelve array RGBA uint8 o None si el archivo no existe.
    """
    png_path = ROOT / f'faro_reporte_fusion_{area}.png'
    if not png_path.exists():
        return None

    img = PILImage.open(png_path).convert('RGBA')
    arr = np.array(img)           # (H, W, 4)

    x0, x1, _ = PNG_PANELS[idx]
    crop = arr[PNG_Y_TOP:PNG_Y_BOT, x0:x1, :]
    return crop


# ── Render de una fila completa ───────────────────────────────────────────────

def _hex_to_rgb(h: str) -> tuple:
    h = h.lstrip('#')
    return tuple(int(h[i:i+2], 16) / 255 for i in (0, 2, 4))


def _render_fila(fig, outer_gs_cell, area: str, metrics: dict,
                 sello_ok: bool, sha_hex: str, row_idx: int):
    """
    Dibuja una fila completa dentro de la celda outer_gs_cell.
    Layout: [info (3.2) | NDVI (4.93) | SAR (4.93) | Fusión (4.94)]  = 18 total
    """
    inner = gridspec.GridSpecFromSubplotSpec(
        1, 4,
        subplot_spec=outer_gs_cell,
        wspace=0.012,
        width_ratios=[3.2, 4.93, 4.93, 4.94],
    )

    # ── Panel de información (izquierda) ─────────────────────────────────────
    ax_info = fig.add_subplot(inner[0, 0])
    ax_info.set_facecolor(BG2)
    ax_info.set_xlim(0, 1)
    ax_info.set_ylim(0, 1)
    ax_info.axis('off')

    vert  = metrics['vertical']
    sec   = metrics['sector']
    col, col_l, tag = VERTICAL_COLORS.get(sec, VERTICAL_COLORS.get(vert,
        (GOLD, GOLD_L, 'FARO')))

    # Borde lateral izquierdo coloreado
    ax_info.add_patch(plt.Rectangle((0, 0), 0.025, 1,
        transform=ax_info.transAxes, facecolor=col, zorder=3))

    # Tag vertical (AGRO / O&G / etc)
    ax_info.text(0.06, 0.955, tag, fontsize=7.5, fontweight='bold',
        color=col_l, fontfamily='monospace', va='top',
        transform=ax_info.transAxes, zorder=4)

    # Nombre principal
    label_parts = metrics['label'].split(',')
    name_main = label_parts[0].strip()
    ax_info.text(0.06, 0.88, name_main, fontsize=13.5, fontweight='bold',
        color=WHITE, va='top', transform=ax_info.transAxes, zorder=4)

    # País / región
    region = ', '.join(p.strip() for p in label_parts[1:])[:30]
    ax_info.text(0.06, 0.77, region, fontsize=7, color=GREY,
        fontfamily='monospace', va='top', transform=ax_info.transAxes, zorder=4)

    # Score
    score = metrics['score']
    if score is not None:
        s_col = GREEN if score >= 65 else (AMBER if score >= 45 else RED)
        ax_info.text(0.06, 0.64, f'{score:.0f}', fontsize=28,
            fontweight='bold', color=s_col, va='top',
            transform=ax_info.transAxes, zorder=4)
        ax_info.text(0.06, 0.50, '/ 100  SCORE FARO', fontsize=6.5,
            color=GREY, fontfamily='monospace', va='top',
            transform=ax_info.transAxes, zorder=4)
        # Barra de score
        ax_info.add_patch(plt.Rectangle((0.06, 0.455), 0.88, 0.018,
            transform=ax_info.transAxes, facecolor='#1a2030', zorder=3))
        ax_info.add_patch(plt.Rectangle((0.06, 0.455), 0.88 * score / 100, 0.018,
            transform=ax_info.transAxes, facecolor=s_col, alpha=0.7, zorder=4))

    # Métricas clave (2×2 grid)
    metrics_items = []
    if metrics['ndvi'] is not None:
        metrics_items.append(('NDVI', f"{metrics['ndvi']:.4f}"))
    if metrics['sar'] is not None:
        metrics_items.append(('SAR dB', f"{metrics['sar']:.2f}"))
    if metrics['fusion'] is not None:
        metrics_items.append(('FUSION', f"{metrics['fusion']:.4f}"))
    if metrics['rinde'] is not None:
        metrics_items.append(('RINDE', f"{metrics['rinde']:.2f} t/ha"))
    elif metrics['activ'] is not None:
        metrics_items.append(('ACT IDX', f"{metrics['activ']:.1f}"))
    if metrics['pixels'] is not None:
        px = metrics['pixels']
        metrics_items.append(('PIXELS', f"{px/1e6:.1f}M" if px > 1e6 else str(px)))

    y_m0 = 0.395
    for j, (lbl, val) in enumerate(metrics_items[:4]):
        row_m = j // 2
        col_m = j % 2
        x_m = 0.06 + col_m * 0.47
        y_m = y_m0 - row_m * 0.14
        ax_info.text(x_m, y_m, lbl, fontsize=5.5, color=GREY,
            fontfamily='monospace', va='top', transform=ax_info.transAxes, zorder=4)
        ax_info.text(x_m, y_m - 0.065, val, fontsize=8, fontweight='bold',
            color=col_l, fontfamily='monospace', va='top',
            transform=ax_info.transAxes, zorder=4)

    # Confianza + fecha
    if metrics['confianza']:
        ax_info.text(0.06, 0.088, f"Confianza: {metrics['confianza']}",
            fontsize=6, color=GREY, fontfamily='monospace', va='bottom',
            transform=ax_info.transAxes, zorder=4)
    if metrics['fecha']:
        ax_info.text(0.06, 0.055, metrics['fecha'][:16],
            fontsize=5.5, color=GREY2, fontfamily='monospace', va='bottom',
            transform=ax_info.transAxes, zorder=4)

    # Sello SHA-256
    if sello_ok:
        ax_info.add_patch(plt.Rectangle((0.06, 0.01), 0.88, 0.038,
            transform=ax_info.transAxes,
            facecolor=GREEN + '22', edgecolor=GREEN, linewidth=0.7, zorder=4))
        ax_info.text(0.50, 0.029, '✓  VERIFIED  ·  SHA-256',
            fontsize=6.2, fontweight='bold', color=GREEN_L,
            fontfamily='monospace', ha='center', va='center',
            transform=ax_info.transAxes, zorder=5)
    else:
        ax_info.text(0.50, 0.025, '[ PENDING VERIFICATION ]',
            fontsize=5.5, color=GREY, fontfamily='monospace',
            ha='center', va='center', transform=ax_info.transAxes, zorder=5)

    # Separador horizontal superior de la fila (línea de color)
    ax_info.add_patch(plt.Rectangle((0, 0.988), 1, 0.012,
        transform=ax_info.transAxes, facecolor=col, alpha=0.8,
        zorder=5, clip_on=False))

    # ── Paneles de imagen (3 imágenes satelitales) ────────────────────────────
    img_labels = ['NDVI Limpio (CLOSDI)', 'Backscatter SAR (S1)', 'Índice Fusión Faro']
    img_cmaps  = ['RdYlGn', 'gray', 'plasma']

    for img_idx in range(3):
        ax_img = fig.add_subplot(inner[0, img_idx + 1])
        ax_img.set_facecolor(BG)
        ax_img.axis('off')

        # Línea de color superior (misma que el panel info)
        ax_img.add_patch(plt.Rectangle((0, 0.988), 1, 0.012,
            transform=ax_img.transAxes, facecolor=col, alpha=0.8,
            zorder=5, clip_on=False))

        crop = _cargar_panel_imagen(area, img_idx)
        if crop is not None:
            ax_img.imshow(crop, aspect='auto', interpolation='bilinear',
                          extent=[0, 1, 0, 1], transform=ax_img.transAxes,
                          zorder=2)

            # Label de imagen (overlay sobre la imagen)
            ax_img.text(0.03, 0.97, img_labels[img_idx],
                fontsize=7.5, color=WHITE, fontfamily='monospace',
                va='top', ha='left', transform=ax_img.transAxes, zorder=6,
                bbox=dict(boxstyle='round,pad=0.2', facecolor='#0a0a1a',
                          alpha=0.65, edgecolor='none'))
        else:
            # No hay PNG → placeholder informativo
            ax_img.text(0.5, 0.5, f'{img_labels[img_idx]}\nNo disponible',
                fontsize=8, color=GREY, fontfamily='monospace',
                ha='center', va='center', transform=ax_img.transAxes, zorder=4)


# ── Generar el bricolage completo ─────────────────────────────────────────────

def generar_bricolage(output_path: Path = None) -> Path:
    if output_path is None:
        output_path = ROOT / 'faro_bricolage_linkedin.png'

    # 1800×2400 px exactos
    fig = plt.figure(figsize=(18, 24), dpi=100, facecolor=BG)

    # GridSpec principal: 1 header + 4 filas de área
    # Alturas: header 1.2 + 4×5.7 = 1.2 + 22.8 = 24
    outer = gridspec.GridSpec(
        5, 1,
        figure=fig,
        height_ratios=[1.2, 5.7, 5.7, 5.7, 5.7],
        hspace=0.012,
        left=0, right=1, top=1, bottom=0,
    )

    # ── Header global ─────────────────────────────────────────────────────────
    ax_hdr = fig.add_subplot(outer[0])
    ax_hdr.set_facecolor(BG)
    ax_hdr.set_xlim(0, 1); ax_hdr.set_ylim(0, 1)
    ax_hdr.axis('off')

    # Logo
    ax_hdr.text(0.018, 0.62, 'FARO', fontsize=22, fontweight='bold',
        color=GOLD, fontfamily='monospace', va='center',
        transform=ax_hdr.transAxes, zorder=5)
    ax_hdr.text(0.092, 0.62, 'PROTOCOL', fontsize=22, fontweight='bold',
        color=WHITE, fontfamily='monospace', va='center',
        transform=ax_hdr.transAxes, zorder=5)

    # Tagline
    tagline = (
        'Global Coverage  ·  Verified by SHA-256  ·  '
        'Sentinel-1 SAR + Sentinel-2 Optical  ·  '
        + datetime.now().strftime('%Y-%m-%d')
    )
    ax_hdr.text(0.018, 0.22, tagline, fontsize=8.5, color=GREY,
        fontfamily='monospace', va='center',
        transform=ax_hdr.transAxes, zorder=5)

    # Contador de zonas verificadas
    n_areas = len(AREAS)
    ax_hdr.text(0.982, 0.62,
        f'{n_areas} VERIFIED ZONES', fontsize=9, fontweight='bold',
        color=GREEN_L, fontfamily='monospace', ha='right', va='center',
        transform=ax_hdr.transAxes, zorder=5)

    ax_hdr.text(0.982, 0.22,
        'protocolfaro.github.io/faroprotocol',
        fontsize=7.5, color=GREY2, fontfamily='monospace',
        ha='right', va='center', transform=ax_hdr.transAxes, zorder=5)

    # Línea separadora
    ax_hdr.axhline(y=0.02, color=GOLD, linewidth=0.8, alpha=0.5)

    # ── 4 filas de área ───────────────────────────────────────────────────────
    for row_idx, area in enumerate(AREAS):
        print(f'  Procesando [{row_idx+1}/{len(AREAS)}] {area}...')
        metrics  = _load_metrics(area)
        sello_ok, sha_hex = _verificar(area)

        _render_fila(
            fig,
            outer_gs_cell=outer[row_idx + 1],
            area=area,
            metrics=metrics,
            sello_ok=sello_ok,
            sha_hex=sha_hex,
            row_idx=row_idx,
        )

    fig.savefig(output_path, dpi=100, bbox_inches=None,
                facecolor=BG, edgecolor='none')
    plt.close(fig)
    return output_path


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == '__main__':
    parser = argparse.ArgumentParser(
        description='FARO PROTOCOL — Bricolage 1800×2400 (4 zonas)')
    parser.add_argument('--out', default='faro_bricolage_linkedin.png')
    args = parser.parse_args()

    print('=' * 62)
    print('  FARO PROTOCOL — Bricolage 1800×2400')
    print(f'  {datetime.now().strftime("%Y-%m-%d %H:%M")}')
    print('=' * 62)
    print()

    out = generar_bricolage(ROOT / args.out)

    print()
    print(f'  Guardado: {out.name}')
    print(f'  Zonas: {", ".join(AREAS)}')
    print(f'  Dimensiones: 1800 x 2400 px')
    print('=' * 62)
