"""
FARO PROTOCOL — Portfolio Global
==================================

Genera un panel horizontal 1800×1200px con los 4 sectores estratégicos.
Cada sector muestra el área más representativa con 3 layers satelitales
(Vigor/NDVI · Estructura/SAR · Fusión) + certificación FSI.

Áreas por defecto (una por sector):
    Agro          → cordoba      (datos reales, única con ALERTA real)
    Deforestación → amazonas     (señal activa de pérdida de cobertura)
    O&G / Energía → vaca_muerta  (actividad industrial moderada)
    Marítimo      → rotterdam    (puerto, nivel normal)

Uso:
    python faro_portfolio_global.py
    python faro_portfolio_global.py --areas cordoba amazonas permian malacca
    python faro_portfolio_global.py --out mi_portfolio.png

Output:
    faro_portfolio_global.png   (1800×1200px, dpi=100)
    faro_portfolio_global.sha256
"""

from __future__ import annotations
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
from matplotlib.patches import Rectangle
import numpy as np

try:
    from PIL import Image
    HAS_PIL = True
except ImportError:
    HAS_PIL = False

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

ROOT = Path(__file__).parent

# ── Paleta ────────────────────────────────────────────────────────────────────
BG      = '#06080b'
BG2     = '#0d1117'
GOLD    = '#c9a84c'
GOLD_L  = '#e2c97e'
WHITE   = '#f2ede4'
WHITE60 = '#978f82'
GREEN   = '#2d8c5e'
RED     = '#b03030'
AMBER   = '#c97c1a'

# FSI nivel → color
NIVEL_COLOR = {
    'Sin estres': GREEN,
    'Leve':       '#6aaa5e',
    'Moderado':   AMBER,
    'Alto':       '#c95a1a',
    'Severo':     RED,
}

# ── Crop coords del PNG fuente (2168×1335px) ─────────────────────────────────
# Los paneles dentro del reporte de fusión:
PNG_Y_TOP  = 175
PNG_Y_BOT  = 658
PNG_PANELS = [
    (50,  590,  'Vigor · NDVI Limpio'),
    (592, 1348, 'Estructura · Backscatter SAR'),
    (1348, 2115, 'Fusion Optico+Radar'),
]

SECTOR_LABELS = {
    'agro':          'AGRICULTURA',
    'deforestacion': 'DEFORESTACION',
    'maritimo':      'MARITIMO',
    'shipping':      'MARITIMO',
    'energia':       'ENERGIA / O&G',
    'oil_gas':       'ENERGIA / O&G',
    'mineria':       'MINERIA',
    'mining_iron':   'MINERIA',
}

# Leyenda pública por sector — explica la escala FSI al lector no técnico
FSI_LEGEND = {
    'agro':          'FSI bajo = cultivo sano  ·  FSI alto = estres hidrico',
    'deforestacion': 'FSI bajo = cobertura estable  ·  FSI alto = deforestacion',
    'maritimo':      'FSI bajo = puerto operativo  ·  FSI alto = anomalia',
    'shipping':      'FSI bajo = puerto operativo  ·  FSI alto = anomalia',
    'energia':       'FSI bajo = actividad normal  ·  FSI alto = incidente',
    'oil_gas':       'FSI bajo = actividad normal  ·  FSI alto = incidente',
    'mineria':       'FSI bajo = produccion normal  ·  FSI alto = paralisis',
    'mining_iron':   'FSI bajo = produccion normal  ·  FSI alto = paralisis',
}

SECTOR_COLOR = {
    'agro':          GREEN,
    'deforestacion': '#5eaa3a',
    'maritimo':      '#3a7acc',
    'shipping':      '#3a7acc',
    'energia':       '#c97c1a',
    'oil_gas':       '#c97c1a',
    'mineria':       '#8a6a3a',
    'mining_iron':   '#8a6a3a',
}

DEFAULT_AREAS = ['cordoba', 'amazonas', 'vaca_muerta', 'rotterdam']


# ── Helpers ───────────────────────────────────────────────────────────────────

def _sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, 'rb') as f:
        for chunk in iter(lambda: f.read(65536), b''):
            h.update(chunk)
    return h.hexdigest()


def _load_area_cfg(name: str) -> dict:
    p = ROOT / 'faro_areas' / f'{name}.json'
    if p.exists():
        with open(p, encoding='utf-8') as f:
            return json.load(f)
    return {'name': name, 'label': name.title(), 'vertical': 'desconocido'}


def _load_metrics(name: str) -> dict:
    data_p = ROOT / 'data.json'
    if not data_p.exists():
        return {}
    with open(data_p, encoding='utf-8') as f:
        d = json.load(f)
    return {**d.get('pipeline', {}).get(name, {}),
            **d.get('insights', {}).get(name, {})}


def _load_panel(area: str, panel_idx: int) -> np.ndarray | None:
    """Carga un panel satelital (0=NDVI, 1=SAR, 2=Fusion) desde el PNG de fusión."""
    if not HAS_PIL:
        return None
    png = ROOT / f'faro_reporte_fusion_{area}.png'
    if not png.exists():
        return None
    try:
        img = Image.open(png).convert('RGB')
        arr = np.array(img)
        x0, x1, _ = PNG_PANELS[panel_idx]
        crop = arr[PNG_Y_TOP:PNG_Y_BOT, x0:x1, :]
        return crop
    except Exception:
        return None


def _fsi_evaluate(area: str) -> tuple[float, str, str, list[str]]:
    """Devuelve (score, nivel, certificacion, detalle) via FaroStressIndex."""
    try:
        from faro_stress_index import FaroStressIndex
        r = FaroStressIndex.evaluate(area)
        return r.score, r.nivel, r.certificacion, r.detalle
    except Exception as e:
        return 50.0, 'Moderado', f'FSI no disponible: {e}', []


# ── Renderizado ───────────────────────────────────────────────────────────────

def _render_area_column(fig, outer_gs_cell, area: str, show_panel_labels: bool = True):
    """
    Dibuja una columna completa para un área:
      - Header (nombre, sector, país)
      - 3 paneles satelitales (NDVI / SAR / Fusión)
      - Barra FSI con score
    """
    cfg     = _load_area_cfg(area)
    metrics = _load_metrics(area)
    sector  = cfg.get('vertical', cfg.get('sector', 'desconocido'))
    label   = cfg.get('label', area.title())
    country = cfg.get('country_name', '')
    scol    = SECTOR_COLOR.get(sector, WHITE60)
    slabel  = SECTOR_LABELS.get(sector, sector.upper())

    fsi_score, fsi_nivel, fsi_cert, fsi_detalle = _fsi_evaluate(area)
    nivel_col = NIVEL_COLOR.get(fsi_nivel, WHITE60)

    # Sub-grid: header + 3 panels + FSI bar
    inner = gridspec.GridSpecFromSubplotSpec(
        5, 1,
        subplot_spec=outer_gs_cell,
        height_ratios=[1.0, 3.5, 3.5, 3.5, 1.8],
        hspace=0.06,
    )

    # ── Header ────────────────────────────────────────────────────────────────
    ax_h = fig.add_subplot(inner[0])
    ax_h.set_facecolor(BG2)
    ax_h.set_xlim(0, 1)
    ax_h.set_ylim(0, 1)
    ax_h.axis('off')

    # Franja de color de sector en el borde superior
    ax_h.add_patch(Rectangle((0, 0.92), 1, 0.08,
                              transform=ax_h.transAxes,
                              facecolor=scol, zorder=5))

    ax_h.text(0.5, 0.82, area.upper().replace('_', ' '),
              ha='center', va='top', fontsize=9, fontweight='bold',
              color=WHITE, fontfamily='monospace', transform=ax_h.transAxes)
    ax_h.text(0.5, 0.60, slabel,
              ha='center', va='top', fontsize=6.5,
              color=scol, fontfamily='monospace', transform=ax_h.transAxes)
    ax_h.text(0.5, 0.38, label,
              ha='center', va='top', fontsize=5.5,
              color=WHITE60, fontfamily='monospace', transform=ax_h.transAxes,
              style='italic')

    # Estado en el header (país + score compacto)
    estado_txt = metrics.get('estado', '')
    estado_mark = ' [ALERTA]' if estado_txt == 'ALERTA' else ''
    ax_h.text(0.5, 0.12, f'{country}{estado_mark}',
              ha='center', va='bottom', fontsize=5.5,
              color=RED if estado_txt == 'ALERTA' else WHITE60,
              fontfamily='monospace', transform=ax_h.transAxes)

    # ── 3 paneles satelitales ─────────────────────────────────────────────────
    panel_titles = ['VIGOR · NDVI', 'ESTRUCTURA · SAR', 'FUSION O+R']
    for pi in range(3):
        ax_p = fig.add_subplot(inner[pi + 1])
        ax_p.set_facecolor(BG)
        ax_p.axis('off')

        crop = _load_panel(area, pi)
        if crop is not None:
            # Stretch de contraste por percentil — garantiza visibilidad en
            # zonas oscuras (agua, desierto, puerto) igual que en zonas verdes
            lo = np.percentile(crop, 2)
            hi = np.percentile(crop, 98)
            if hi > lo:
                crop = np.clip((crop.astype(float) - lo) / (hi - lo), 0, 1)
            ax_p.imshow(crop, aspect='auto', extent=[0, 1, 0, 1], zorder=1)
            ax_p.set_xlim(0, 1)
            ax_p.set_ylim(0, 1)
        else:
            # Placeholder cuando no hay PNG fuente
            ax_p.add_patch(Rectangle((0, 0), 1, 1,
                                     facecolor='#1a1f2b', zorder=1))
            ax_p.text(0.5, 0.5, 'SIN DATOS\nSATELITALES',
                      ha='center', va='center', fontsize=6,
                      color=WHITE60, fontfamily='monospace',
                      transform=ax_p.transAxes, zorder=2)

        if show_panel_labels:
            ax_p.text(0.5, 0.97, panel_titles[pi],
                      ha='center', va='top', fontsize=5,
                      color=GOLD_L, fontfamily='monospace',
                      transform=ax_p.transAxes, zorder=3,
                      bbox=dict(boxstyle='round,pad=0.15', facecolor='#000000aa',
                                edgecolor='none'))

    # ── Barra FSI ─────────────────────────────────────────────────────────────
    ax_f = fig.add_subplot(inner[4])
    ax_f.set_facecolor(BG2)
    ax_f.set_xlim(0, 100)
    ax_f.set_ylim(0, 1)
    ax_f.axis('off')

    # Título con score
    ax_f.text(50, 0.97, f'FSI: {fsi_score:.0f} / 100  |  {fsi_nivel.upper()}',
              ha='center', va='top', fontsize=6.5,
              color=nivel_col, fontfamily='monospace', fontweight='bold')

    # Fondo gris de la barra
    ax_f.add_patch(Rectangle((0, 0.68), 100, 0.14,
                              facecolor='#1a1f2b', zorder=1))
    # Relleno proporcional al score
    if fsi_score > 0:
        ax_f.add_patch(Rectangle((0, 0.68), fsi_score, 0.14,
                                  facecolor=nivel_col, alpha=0.85, zorder=2))

    # Leyenda pública del sector (dos líneas: bajo / alto)
    legend_raw = FSI_LEGEND.get(sector, '')
    if legend_raw:
        parts = legend_raw.split('  ·  ')
        line_bajo = parts[0].strip() if len(parts) > 0 else ''
        line_alto = parts[1].strip() if len(parts) > 1 else ''
        ax_f.text(1, 0.58, line_bajo,
                  ha='right', va='top', fontsize=4.8,
                  color=GREEN, fontfamily='monospace',
                  transform=ax_f.transAxes)
        ax_f.text(1, 0.43, line_alto,
                  ha='right', va='top', fontsize=4.8,
                  color='#c07070', fontfamily='monospace',
                  transform=ax_f.transAxes)

    # Primera línea de detalle satelital
    if fsi_detalle:
        txt = fsi_detalle[0]
        if len(txt) > 52:
            txt = txt[:49] + '...'
        ax_f.text(50, 0.16, txt,
                  ha='center', va='bottom', fontsize=4.3,
                  color=WHITE60, fontfamily='monospace',
                  transform=ax_f.transAxes)


def _composite_sha(areas: list[str]) -> str:
    """SHA-256 compuesto de los 4 SHA-256 individuales."""
    parts = []
    for a in areas:
        sha_f = ROOT / f'faro_reporte_fusion_{a}.sha256'
        if sha_f.exists():
            parts.append(sha_f.read_text(encoding='utf-8').split()[0].strip())
        else:
            parts.append(f'NO_SHA_{a}')
    combined = '|'.join(parts)
    return hashlib.sha256(combined.encode()).hexdigest()


def generar_portfolio(areas: list[str], output: str = 'faro_portfolio_global.png'):
    """Genera el portfolio global 1800×1200px."""
    print(f'  [·] Generando Portfolio Global: {len(areas)} areas')

    fig = plt.figure(figsize=(18, 12), facecolor=BG)
    fig.patch.set_facecolor(BG)

    # Grid principal: header(top) + columnas(main) + footer(bottom)
    main_gs = gridspec.GridSpec(
        3, 1,
        height_ratios=[0.7, 10.0, 0.8],
        hspace=0.02,
        figure=fig,
    )

    # ── Banner superior ───────────────────────────────────────────────────────
    ax_top = fig.add_subplot(main_gs[0])
    ax_top.set_facecolor(BG2)
    ax_top.axis('off')

    ax_top.text(0.012, 0.78, 'FARO PROTOCOL',
                ha='left', va='top', fontsize=13, fontweight='bold',
                color=GOLD, fontfamily='monospace',
                transform=ax_top.transAxes)
    ax_top.text(0.012, 0.30, 'PORTFOLIO GLOBAL  —  ANALISIS SATELITAL MULTISECTOR',
                ha='left', va='top', fontsize=7,
                color=WHITE60, fontfamily='monospace',
                transform=ax_top.transAxes)

    ts = datetime.now().strftime('%Y-%m-%d %H:%M UTC')
    ax_top.text(0.988, 0.78, ts,
                ha='right', va='top', fontsize=7,
                color=WHITE60, fontfamily='monospace',
                transform=ax_top.transAxes)
    ax_top.text(0.988, 0.30, 'Sentinel-1 SAR · Sentinel-2 MSI · FSI v1.0',
                ha='right', va='top', fontsize=6,
                color=WHITE60, fontfamily='monospace',
                transform=ax_top.transAxes)

    # ── Columnas de áreas ─────────────────────────────────────────────────────
    n = len(areas)
    col_gs = gridspec.GridSpecFromSubplotSpec(
        1, n,
        subplot_spec=main_gs[1],
        wspace=0.03,
    )

    for i, area in enumerate(areas):
        _render_area_column(fig, col_gs[i], area)
        print(f'  [+] {area}')

    # ── Footer con SHA-256 compuesto ──────────────────────────────────────────
    ax_foot = fig.add_subplot(main_gs[2])
    ax_foot.set_facecolor(BG2)
    ax_foot.axis('off')

    comp_sha = _composite_sha(areas)

    ax_foot.text(0.012, 0.80, 'SHA-256 COMPUESTO',
                 ha='left', va='top', fontsize=8, fontweight='bold',
                 color=GOLD, fontfamily='monospace',
                 transform=ax_foot.transAxes)
    ax_foot.text(0.012, 0.30, comp_sha,
                 ha='left', va='top', fontsize=8,
                 color=WHITE60, fontfamily='monospace',
                 transform=ax_foot.transAxes)

    # SHA individuales
    sha_strs = []
    for a in areas:
        sha_f = ROOT / f'faro_reporte_fusion_{a}.sha256'
        if sha_f.exists():
            h = sha_f.read_text(encoding='utf-8').split()[0].strip()[:16]
            sha_strs.append(f'{a.upper()[:10]}:{h}')
        else:
            sha_strs.append(f'{a.upper()[:10]}:NO_SHA')
    ax_foot.text(0.988, 0.80, '  |  '.join(sha_strs),
                 ha='right', va='top', fontsize=8,
                 color=WHITE60, fontfamily='monospace',
                 transform=ax_foot.transAxes)
    ax_foot.text(0.988, 0.30,
                 'protocolfaro.github.io/faroprotocol  |  Datos: ESA Copernicus, USGS, GEE',
                 ha='right', va='top', fontsize=8,
                 color=WHITE60, fontfamily='monospace',
                 transform=ax_foot.transAxes)

    # ── Guardar ───────────────────────────────────────────────────────────────
    out_path = ROOT / output
    fig.subplots_adjust(left=0.005, right=0.995, top=0.995, bottom=0.005,
                        hspace=0.015, wspace=0.015)
    plt.savefig(out_path, dpi=100, facecolor=BG, edgecolor='none')
    plt.close()

    # Sello SHA-256
    sha_hex = _sha256_file(out_path)
    sha_path = ROOT / output.replace('.png', '.sha256')
    sha_path.write_text(f'{sha_hex}  {out_path.name}\n', encoding='utf-8')

    size_kb = out_path.stat().st_size // 1024
    print(f'  [+] {out_path.name}  ({size_kb} KB)')
    print(f'  [+] SHA-256: {sha_hex[:32]}...')
    print(f'  [+] Compuesto: {comp_sha[:32]}...')
    return out_path, sha_hex


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == '__main__':
    parser = argparse.ArgumentParser(
        description='Faro Portfolio Global — 4 sectores satelitales'
    )
    parser.add_argument('--areas', nargs='+', default=DEFAULT_AREAS,
                        metavar='AREA',
                        help=f'Áreas a incluir (default: {" ".join(DEFAULT_AREAS)})')
    parser.add_argument('--out', default='faro_portfolio_global.png',
                        help='Archivo de salida (default: faro_portfolio_global.png)')
    args = parser.parse_args()

    print('=' * 60)
    print('  FARO PROTOCOL — Portfolio Global')
    print('=' * 60)
    generar_portfolio(args.areas, args.out)
    print()
