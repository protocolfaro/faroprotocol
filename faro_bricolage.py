"""
FARO PROTOCOL — Bricolage LinkedIn
===================================

Genera una imagen de 4 paneles para portada de LinkedIn (1584 × 396 px).

Selección automática de paneles:
  - Los 4 primeros certificados con Sello Verde van al frente con datos reales.
  - Si hay menos de 4 con sello, el resto muestra "Pipeline active · Data incoming"
    sin Sello Verde y sin inventar datos.

REGLA: Nunca mostrar Sello Verde en datos simulados.

Uso:
    python faro_bricolage.py
    python faro_bricolage.py --out portada_linkedin.png
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
import matplotlib.patches as mpatches
from matplotlib.patches import FancyBboxPatch
import matplotlib.gridspec as gridspec

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

PROJECT_ROOT = Path(__file__).parent

# ── Paleta Faro ───────────────────────────────────────────────────────────────
BG      = '#06080b'
BG2     = '#0d1117'
BG3     = '#0a0f16'
GOLD    = '#c9a84c'
GOLD_L  = '#e2c97e'
WHITE   = '#f2ede4'
WHITE3  = '#7a7570'
GREEN   = '#2d8c5e'
GREEN_L = '#4ab87e'
RED     = '#b03030'
RED_L   = '#e05050'
BLUE    = '#2a6496'
BLUE_L  = '#4a90c8'
ORANGE  = '#c07a30'
ORANGE_L= '#e09a50'

VERTICAL_META = {
    'agro':        {'color': GREEN,  'color_l': GREEN_L,  'tag': 'AGRO',    'icon': 'AGR'},
    'energia':     {'color': BLUE,   'color_l': BLUE_L,   'tag': 'O&G',     'icon': 'O&G'},
    'oil_gas':     {'color': BLUE,   'color_l': BLUE_L,   'tag': 'O&G',     'icon': 'O&G'},
    'maritimo':    {'color': BLUE,   'color_l': BLUE_L,   'tag': 'MARITIME','icon': 'MAR'},
    'shipping':    {'color': BLUE,   'color_l': BLUE_L,   'tag': 'MARITIME','icon': 'MAR'},
    'mineria':     {'color': ORANGE, 'color_l': ORANGE_L, 'tag': 'MINING',  'icon': 'MIN'},
    'mining_iron': {'color': ORANGE, 'color_l': ORANGE_L, 'tag': 'MINING',  'icon': 'MIN'},
    'deforestacion':{'color': RED,   'color_l': RED_L,    'tag': 'FOREST',  'icon': 'FOR'},
}


# ── SHA-256 verification (inline, no dep on faro_certificado) ─────────────────

def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(65536), b''):
            h.update(chunk)
    return h.hexdigest()


def _verificar_sello(area_name: str, report_png: str) -> bool:
    png_path = PROJECT_ROOT / report_png
    sha_path = PROJECT_ROOT / f'faro_reporte_fusion_{area_name}.sha256'
    if not png_path.exists() or not sha_path.exists():
        return False
    archivado = sha_path.read_text(encoding='utf-8').split()[0].strip()
    return _sha256_file(png_path) == archivado


# ── Carga de datos ────────────────────────────────────────────────────────────

def _load_area(area_name: str) -> dict:
    cfg_path = PROJECT_ROOT / 'faro_areas' / f'{area_name}.json'
    if not cfg_path.exists():
        return {}
    with open(cfg_path, encoding='utf-8') as f:
        return json.load(f)


def _load_pipeline(area_name: str) -> dict:
    data_path = PROJECT_ROOT / 'data.json'
    if not data_path.exists():
        return {}
    with open(data_path, encoding='utf-8') as f:
        d = json.load(f)
    pipe = d.get('pipeline', {}).get(area_name, {})
    ins  = d.get('insights', {}).get(area_name, {})
    return {**pipe, **ins}


def _preparar_paneles() -> list[dict]:
    """
    Devuelve lista de 4 dicts listos para renderizar.
    Orden: verificados primero, luego el resto.
    """
    areas_dir = PROJECT_ROOT / 'faro_areas'
    all_areas = sorted(p.stem for p in areas_dir.glob('*.json') if p.stem != 'areas')

    paneles = []
    for a in all_areas:
        cfg  = _load_area(a)
        pipe = _load_pipeline(a)
        rpt  = cfg.get('report_png', f'faro_reporte_fusion_{a}.png')
        sello = _verificar_sello(a, rpt)

        vertical = cfg.get('vertical', 'desconocido')
        sector   = cfg.get('sector', vertical)
        meta     = VERTICAL_META.get(sector, VERTICAL_META.get(vertical, {
            'color': GOLD, 'color_l': GOLD_L, 'tag': 'FARO', 'icon': '???'}))

        paneles.append({
            'name':     a,
            'label':    cfg.get('label', a.title()),
            'country':  cfg.get('country_name', ''),
            'vertical': vertical,
            'sector':   sector,
            'meta':     meta,
            'sello':    sello,
            'score':    pipe.get('score_faro'),
            'ndvi':     pipe.get('ndvi_medio'),
            'sar':      pipe.get('sar_medio_db'),
            'rinde':    pipe.get('rinde_estimado_tha'),
            'activ':    pipe.get('indice_actividad'),
            'estado':   pipe.get('estado', 'PENDIENTE_DATOS'),
            'pixels':   pipe.get('pixeles_analizados'),
            'confianza':pipe.get('confianza', ''),
            'ultimo':   pipe.get('ultimo_run', ''),
        })

    # Ordenar: verificados primero
    paneles.sort(key=lambda p: (0 if p['sello'] else 1, p['name']))

    # Tomar exactamente 4
    while len(paneles) < 4:
        paneles.append({
            'name': f'slot_{len(paneles)}', 'label': 'Area Pending',
            'country': '', 'vertical': 'desconocido', 'sector': 'desconocido',
            'meta': {'color': GOLD, 'color_l': GOLD_L, 'tag': 'TBD', 'icon': '···'},
            'sello': False, 'score': None, 'ndvi': None, 'sar': None,
            'rinde': None, 'activ': None, 'estado': 'PENDIENTE',
            'pixels': None, 'confianza': '', 'ultimo': '',
        })

    return paneles[:4]


# ── Render de un panel ────────────────────────────────────────────────────────

def _render_panel(ax, panel: dict, idx: int):
    ax.set_xlim(0, 1); ax.set_ylim(0, 1)
    ax.axis('off')
    ax.set_facecolor(BG2 if idx % 2 == 0 else BG3)

    m     = panel['meta']
    col   = m['color']
    col_l = m['color_l']
    tag   = m['tag']
    score = panel['score']
    sello = panel['sello']

    # Borde superior coloreado
    ax.add_patch(plt.Rectangle((0, 0.96), 1, 0.04, transform=ax.transAxes,
                                facecolor=col, alpha=0.85, zorder=3))

    # Tag vertical
    ax.text(0.5, 0.98, tag, fontsize=7, fontweight='bold', color=WHITE,
            fontfamily='monospace', ha='center', va='center',
            transform=ax.transAxes, zorder=4)

    # Nombre del área (primera línea del label)
    parts = panel['label'].split(',')
    name_short = parts[0].strip()
    ax.text(0.06, 0.88, name_short, fontsize=9.5, fontweight='bold',
            color=WHITE, va='top', transform=ax.transAxes, zorder=4,
            wrap=True)

    # País / región
    region = ', '.join(p.strip() for p in parts[1:]) if len(parts) > 1 else panel['country']
    ax.text(0.06, 0.76, region[:28], fontsize=6, color=WHITE3,
            fontfamily='monospace', va='top', transform=ax.transAxes, zorder=4)

    # Score
    if score is not None:
        score_col = GREEN if score >= 70 else (GOLD if score >= 45 else RED)
        ax.text(0.5, 0.60, f'{score:.0f}', fontsize=30, fontweight='bold',
                color=score_col, ha='center', va='center',
                transform=ax.transAxes, zorder=4)
        ax.text(0.5, 0.44, 'SCORE', fontsize=5.5, color=WHITE3,
                fontfamily='monospace', ha='center', va='center',
                transform=ax.transAxes, zorder=4)

        # Barra de score
        bar_w = 0.85
        bar_x = 0.075
        ax.add_patch(plt.Rectangle((bar_x, 0.37), bar_w, 0.022,
                                    transform=ax.transAxes,
                                    facecolor='#1a2030', zorder=3))
        ax.add_patch(plt.Rectangle((bar_x, 0.37), bar_w * score / 100, 0.022,
                                    transform=ax.transAxes,
                                    facecolor=score_col, alpha=0.75, zorder=4))
    else:
        ax.text(0.5, 0.58, 'Pipeline active', fontsize=7.5, color=WHITE3,
                fontfamily='monospace', ha='center', va='center',
                transform=ax.transAxes, zorder=4)
        ax.text(0.5, 0.48, 'Data incoming', fontsize=7, color=WHITE3,
                alpha=0.6, fontfamily='monospace', ha='center', va='center',
                transform=ax.transAxes, zorder=4)

    # Métricas rápidas
    metrics_pairs = []
    if panel['ndvi'] is not None:
        metrics_pairs.append(('NDVI', f"{panel['ndvi']:.3f}"))
    if panel['sar'] is not None:
        metrics_pairs.append(('SAR', f"{panel['sar']:.1f}dB"))
    if panel['rinde'] is not None:
        metrics_pairs.append(('RINDE', f"{panel['rinde']:.2f}t/ha"))
    if panel['activ'] is not None:
        metrics_pairs.append(('ACT', f"{panel['activ']:.1f}"))
    if panel['pixels'] is not None:
        metrics_pairs.append(('PIX', f"{panel['pixels']/1e6:.0f}M"))

    y_m = 0.30
    for j, (lbl, val) in enumerate(metrics_pairs[:4]):
        x_m = 0.06 + j * 0.23
        ax.text(x_m, y_m, lbl, fontsize=4.5, color=WHITE3,
                fontfamily='monospace', va='top', transform=ax.transAxes, zorder=4)
        ax.text(x_m, y_m - 0.085, val, fontsize=6.5, fontweight='bold',
                color=col_l, va='top', transform=ax.transAxes, zorder=4)

    # ── Sello / Estado ────────────────────────────────────────────────────────
    y_s = 0.09

    if sello:
        ax.add_patch(FancyBboxPatch((0.06, y_s - 0.01), 0.88, 0.11,
            boxstyle='round,pad=0.01', linewidth=0.8,
            edgecolor=GREEN, facecolor=GREEN + '20',
            transform=ax.transAxes, zorder=4))
        ax.text(0.5, y_s + 0.05, '✓  VERIFIED · SHA-256', fontsize=7,
                fontweight='bold', color=GREEN_L, fontfamily='monospace',
                ha='center', va='center', transform=ax.transAxes, zorder=5)
    else:
        # Solo mostrar estado sin sello
        estado_txt = panel['estado'].replace('_', ' ')
        ax.text(0.5, y_s + 0.05, f'[ {estado_txt} ]', fontsize=6.5,
                color=WHITE3, fontfamily='monospace',
                ha='center', va='center', transform=ax.transAxes, zorder=5)

    # Línea divisoria vertical derecha (excepto el último panel)
    if idx < 3:
        ax.axvline(x=0.99, color=GOLD, linewidth=0.4, alpha=0.3, zorder=5)


# ── Render del conjunto ───────────────────────────────────────────────────────

def generar_bricolage(output_path: Path = None) -> Path:
    if output_path is None:
        output_path = PROJECT_ROOT / 'faro_bricolage_linkedin.png'

    paneles = _preparar_paneles()

    # LinkedIn cover: 1584 × 396 px → ratio 4:1
    fig = plt.figure(figsize=(15.84, 3.96), facecolor=BG)

    # Header strip
    gs = gridspec.GridSpec(2, 4,
                           height_ratios=[0.12, 0.88],
                           hspace=0, wspace=0.008,
                           left=0, right=1, top=1, bottom=0)

    # Header row — full width
    ax_header = fig.add_subplot(gs[0, :])
    ax_header.set_xlim(0, 1); ax_header.set_ylim(0, 1)
    ax_header.axis('off')
    ax_header.set_facecolor(BG)

    # Header: logo + tagline + fecha
    ax_header.text(0.012, 0.55, 'FARO PROTOCOL', fontsize=11,
                   fontweight='bold', color=GOLD,
                   fontfamily='monospace', va='center',
                   transform=ax_header.transAxes, zorder=5)

    tagline = 'Satellite Intelligence · Verified by SHA-256 · Global Coverage'
    ax_header.text(0.20, 0.55, tagline, fontsize=7, color=WHITE3,
                   fontfamily='monospace', va='center',
                   transform=ax_header.transAxes, zorder=5)

    n_verified = sum(1 for p in paneles if p['sello'])
    ax_header.text(0.985, 0.55,
                   f'{n_verified} verified  ·  {datetime.now().strftime("%Y-%m-%d")}',
                   fontsize=6.5, color=GREEN_L if n_verified > 0 else WHITE3,
                   fontfamily='monospace', ha='right', va='center',
                   transform=ax_header.transAxes, zorder=5)

    # Línea bajo el header
    ax_header.axhline(y=0.02, color=GOLD, linewidth=0.5, alpha=0.4)

    # 4 paneles en la fila inferior
    for i, panel in enumerate(paneles):
        ax = fig.add_subplot(gs[1, i])
        _render_panel(ax, panel, i)

    fig.savefig(output_path, dpi=150, bbox_inches='tight',
                facecolor=BG, edgecolor='none')
    plt.close(fig)

    verified_names = [p['name'] for p in paneles if p['sello']]
    pending_names  = [p['name'] for p in paneles if not p['sello']]
    print(f'  Bricolage generado: {output_path.name}')
    print(f'  Con sello  : {", ".join(verified_names) if verified_names else "ninguno"}')
    print(f'  Sin sello  : {", ".join(pending_names) if pending_names else "ninguno"}')
    return output_path


# ── CLI ────────────────────────────────────────────────────────────────────────

if __name__ == '__main__':
    parser = argparse.ArgumentParser(
        description='FARO PROTOCOL — Portada LinkedIn 4 paneles')
    parser.add_argument('--out', default='faro_bricolage_linkedin.png',
                        help='Archivo de salida PNG')
    args = parser.parse_args()

    print('=' * 60)
    print('  FARO PROTOCOL — Bricolage LinkedIn')
    print(f'  {datetime.now().strftime("%Y-%m-%d %H:%M")}')
    print('=' * 60)
    print()

    generar_bricolage(PROJECT_ROOT / args.out)

    print()
    print('  Dimensiones: 1584 x 396 px (ratio LinkedIn 4:1)')
    print('=' * 60)
