"""
FARO PROTOCOL — Generador de certificados PNG
==============================================

Genera un certificado visual para cada área validada.

REGLA: Sello Verde (✓ VERIFIED) solo si:
  1. El PNG de reporte existe en disco
  2. El SHA-256 del .sha256 coincide con el hash real del PNG

Si no hay datos reales → "Pipeline active · Data incoming" (sin sello).

Uso:
    python faro_certificado.py --area cordoba
    python faro_certificado.py --area balcarce
    python faro_certificado.py --all
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
import matplotlib.patheffects as pe

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

PROJECT_ROOT = Path(__file__).parent

# ── Paleta Faro ───────────────────────────────────────────────────────────────
BG      = '#06080b'
BG2     = '#0d1117'
BG3     = '#111820'
GOLD    = '#c9a84c'
GOLD_L  = '#e2c97e'
WHITE   = '#f2ede4'
WHITE3  = '#9a9590'
GREEN   = '#2d8c5e'
GREEN_L = '#4ab87e'
RED     = '#b03030'
RED_L   = '#e05050'
BLUE    = '#2a6496'
BLUE_L  = '#4a90c8'
ORANGE  = '#c07a30'
ORANGE_L= '#e09a50'

VERTICAL_COLOR = {
    'agro':        (GREEN, GREEN_L, 'AGR'),
    'energia':     (BLUE,  BLUE_L,  'O&G'),
    'oil_gas':     (BLUE,  BLUE_L,  'O&G'),
    'maritimo':    (BLUE,  BLUE_L,  'MAR'),
    'shipping':    (BLUE,  BLUE_L,  'MAR'),
    'mineria':     (ORANGE, ORANGE_L, 'MIN'),
    'mining_iron': (ORANGE, ORANGE_L, 'MIN'),
    'deforestacion': (RED, RED_L,   'FOR'),
}


# ── Verificación SHA-256 ───────────────────────────────────────────────────────

def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(65536), b''):
            h.update(chunk)
    return h.hexdigest()


def verificar_sello(area_name: str, report_png: str) -> dict:
    """
    Retorna dict con:
      sello_verde: bool
      sha256: str | None
      razon: str
    """
    png_path  = PROJECT_ROOT / report_png
    sha_path  = PROJECT_ROOT / f"faro_reporte_fusion_{area_name}.sha256"

    if not png_path.exists():
        return {'sello_verde': False, 'sha256': None,
                'razon': 'PNG de reporte no encontrado'}

    if not sha_path.exists():
        return {'sello_verde': False, 'sha256': None,
                'razon': 'Archivo .sha256 no encontrado'}

    # Leer hash archivado
    sha_archivado = sha_path.read_text(encoding='utf-8').split()[0].strip()

    # Calcular hash real del PNG
    sha_real = _sha256_file(png_path)

    if sha_real != sha_archivado:
        return {
            'sello_verde': False,
            'sha256': sha_archivado,
            'sha256_real': sha_real,
            'razon': 'Hash no coincide — reporte fue modificado'
        }

    return {
        'sello_verde': True,
        'sha256': sha_real,
        'razon': 'SHA-256 verificado'
    }


# ── Carga de datos del pipeline ────────────────────────────────────────────────

def _cargar_datos_area(area_name: str) -> dict:
    """Lee data.json y retorna datos del área, o defaults si no existe."""
    data_path = PROJECT_ROOT / 'data.json'
    if not data_path.exists():
        return {}
    with open(data_path, encoding='utf-8') as f:
        data = json.load(f)

    pipeline = data.get('pipeline', {}).get(area_name, {})
    insights = data.get('insights', {}).get(area_name, {})
    interno  = data.get('_interno', {}).get(area_name, {})
    return {**pipeline, **insights, **interno}


def _cargar_area_config(area_name: str) -> dict:
    """Lee faro_areas/<area>.json."""
    cfg_path = PROJECT_ROOT / 'faro_areas' / f'{area_name}.json'
    if not cfg_path.exists():
        return {'label': area_name, 'vertical': 'desconocido'}
    with open(cfg_path, encoding='utf-8') as f:
        return json.load(f)


# ── Renderizado del certificado ────────────────────────────────────────────────

def _dibujar_barra_estado(ax, x, y, w, h, estado, score):
    """Barra horizontal con gradiente de color según score."""
    if score is None:
        col = WHITE3
    elif score >= 70:
        col = GREEN
    elif score >= 45:
        col = GOLD
    else:
        col = RED

    # Fondo
    ax.add_patch(FancyBboxPatch((x, y), w, h,
        boxstyle='round,pad=0.002', linewidth=0,
        facecolor='#1a2030', transform=ax.transAxes, clip_on=False))
    # Relleno proporcional al score
    fill_w = w * (score / 100) if score else 0
    if fill_w > 0:
        ax.add_patch(FancyBboxPatch((x, y), fill_w, h,
            boxstyle='round,pad=0.002', linewidth=0,
            facecolor=col, alpha=0.7, transform=ax.transAxes, clip_on=False))


def generar_certificado(area_name: str, output_dir: Path = None) -> Path:
    """
    Genera el certificado PNG para el área indicada.
    Retorna la ruta del PNG generado.
    """
    if output_dir is None:
        output_dir = PROJECT_ROOT

    cfg  = _cargar_area_config(area_name)
    data = _cargar_datos_area(area_name)

    vertical = cfg.get('vertical', 'desconocido')
    sector   = cfg.get('sector', vertical)
    v_col, v_col_l, v_tag = VERTICAL_COLOR.get(
        sector, VERTICAL_COLOR.get(vertical, (GOLD, GOLD_L, 'FARO')))

    report_png = cfg.get('report_png', f'faro_reporte_fusion_{area_name}.png')
    sello      = verificar_sello(area_name, report_png)

    score  = data.get('score_faro')
    estado = data.get('estado', 'PENDIENTE_DATOS')
    ndvi   = data.get('ndvi_medio')
    sar    = data.get('sar_medio_db')
    rinde  = data.get('rinde_estimado_tha')
    activ  = data.get('indice_actividad')
    pixels = data.get('pixeles_analizados')
    ultimo = data.get('ultimo_run', '')
    confianza = data.get('confianza', '')

    label   = cfg.get('label', area_name.title())
    pais    = cfg.get('country_name', '')
    hectareas = cfg.get('hectareas_ref')

    # ── Figura ────────────────────────────────────────────────────────────────
    fig = plt.figure(figsize=(10, 6), facecolor=BG)
    ax  = fig.add_axes([0, 0, 1, 1])
    ax.set_xlim(0, 1); ax.set_ylim(0, 1)
    ax.axis('off')
    ax.set_facecolor(BG)

    # Fondo principal
    ax.add_patch(plt.Rectangle((0, 0), 1, 1, transform=ax.transAxes,
                                facecolor=BG, zorder=0))

    # Panel izquierdo (30%)
    ax.add_patch(plt.Rectangle((0, 0), 0.30, 1, transform=ax.transAxes,
                                facecolor=BG2, zorder=1))

    # Línea separadora dorada
    ax.axvline(x=0.30, color=GOLD, linewidth=0.8, alpha=0.6, zorder=2)

    # Línea superior
    ax.axhline(y=0.92, xmin=0, xmax=1, color=GOLD, linewidth=0.5, alpha=0.4, zorder=2)

    # ── Header ────────────────────────────────────────────────────────────────
    # Logo text
    ax.text(0.015, 0.96, 'FARO', fontsize=22, fontweight='bold',
            color=GOLD, fontfamily='monospace', va='top', transform=ax.transAxes, zorder=5)
    ax.text(0.015, 0.89, 'P R O T O C O L', fontsize=6, color=GOLD_L, alpha=0.7,
            fontfamily='monospace', va='top', transform=ax.transAxes, zorder=5)

    # Tag vertical
    ax.add_patch(FancyBboxPatch((0.015, 0.76), 0.07, 0.10,
        boxstyle='round,pad=0.01', linewidth=0.5,
        edgecolor=v_col, facecolor=v_col + '22',
        transform=ax.transAxes, zorder=5))
    ax.text(0.05, 0.81, v_tag, fontsize=9, fontweight='bold',
            color=v_col_l, fontfamily='monospace',
            ha='center', va='center', transform=ax.transAxes, zorder=6)

    # Score en panel izquierdo
    if score is not None:
        score_col = GREEN if score >= 70 else (GOLD if score >= 45 else RED)
        ax.text(0.15, 0.60, f'{score:.0f}', fontsize=38, fontweight='bold',
                color=score_col, ha='center', va='center',
                transform=ax.transAxes, zorder=5)
        ax.text(0.15, 0.44, '/ 100', fontsize=10, color=WHITE3,
                ha='center', va='center', transform=ax.transAxes, zorder=5)
        ax.text(0.15, 0.38, 'SCORE FARO', fontsize=6, color=WHITE3,
                fontfamily='monospace', ha='center', va='center',
                transform=ax.transAxes, zorder=5)
        # Barra de score
        _dibujar_barra_estado(ax, 0.03, 0.30, 0.24, 0.04, estado, score)
    else:
        ax.text(0.15, 0.52, '—', fontsize=28, color=WHITE3,
                ha='center', va='center', transform=ax.transAxes, zorder=5)
        ax.text(0.15, 0.38, 'SIN DATOS', fontsize=7, color=WHITE3,
                fontfamily='monospace', ha='center', va='center',
                transform=ax.transAxes, zorder=5)

    # Estado en panel izquierdo
    estado_col = (GREEN if estado == 'OK'
                  else RED if estado == 'ALERTA'
                  else WHITE3)
    ax.text(0.15, 0.22, estado.replace('_', ' '), fontsize=7,
            color=estado_col, fontfamily='monospace',
            ha='center', va='center', transform=ax.transAxes, zorder=5)

    # Fecha
    if ultimo:
        fecha_fmt = ultimo[:10] if len(ultimo) >= 10 else ultimo
    else:
        fecha_fmt = ''
    ax.text(0.15, 0.14, fecha_fmt, fontsize=6, color=WHITE3,
            fontfamily='monospace', ha='center', va='center',
            transform=ax.transAxes, zorder=5)

    # ── Panel derecho — datos ─────────────────────────────────────────────────
    ax.text(0.33, 0.95, label, fontsize=14, fontweight='bold',
            color=WHITE, va='top', transform=ax.transAxes, zorder=5)
    ax.text(0.33, 0.87, pais, fontsize=8, color=WHITE3,
            va='top', transform=ax.transAxes, zorder=5)

    # Subtítulo de sector
    sector_label = {
        'agro': 'Agricultural Intelligence',
        'energia': 'Energy Sector Intelligence',
        'oil_gas': 'Oil & Gas Intelligence',
        'maritimo': 'Maritime Intelligence',
        'shipping': 'Maritime Shipping Intelligence',
        'mineria': 'Mining Intelligence',
        'mining_iron': 'Iron Ore Mining Intelligence',
        'deforestacion': 'Forest Cover Intelligence',
    }.get(sector, sector.title() + ' Intelligence')
    ax.text(0.33, 0.81, sector_label, fontsize=7, color=v_col_l,
            fontfamily='monospace', va='top', transform=ax.transAxes, zorder=5)

    # ── Métricas en grilla ────────────────────────────────────────────────────
    metrics = []

    if ndvi is not None:
        metrics.append(('NDVI', f'{ndvi:.4f}', ''))
    if sar is not None:
        metrics.append(('SAR VV', f'{sar:.1f} dB', ''))
    if rinde is not None:
        metrics.append(('Rinde est.', f'{rinde:.2f} t/ha', ''))
    if activ is not None:
        metrics.append(('Actividad', f'{activ:.1f}', ''))
    if pixels is not None:
        metrics.append(('Píxeles', f'{pixels/1e6:.1f}M', ''))
    if hectareas is not None:
        metrics.append(('Área ref.', f'{hectareas:,} ha', ''))
    if confianza:
        metrics.append(('Confianza', confianza, ''))

    # Grid: 2 columnas × N filas
    col_x = [0.33, 0.67]
    row_y_start = 0.70
    row_h       = 0.095

    for i, (label_m, val_m, _unit) in enumerate(metrics[:6]):
        col  = i % 2
        row  = i // 2
        x    = col_x[col]
        y    = row_y_start - row * row_h

        # Caja de métrica
        ax.add_patch(FancyBboxPatch((x - 0.01, y - 0.065), 0.30, 0.075,
            boxstyle='round,pad=0.008', linewidth=0.4,
            edgecolor=GOLD + '40', facecolor='#0d111780',
            transform=ax.transAxes, zorder=4))

        ax.text(x + 0.04, y - 0.012, label_m, fontsize=6, color=WHITE3,
                fontfamily='monospace', ha='center', va='top',
                transform=ax.transAxes, zorder=5)
        ax.text(x + 0.04, y - 0.038, val_m, fontsize=11, fontweight='bold',
                color=WHITE, ha='center', va='top',
                transform=ax.transAxes, zorder=5)

    # ── Sección SHA-256 / Sello ───────────────────────────────────────────────
    y_sello = 0.18

    if sello['sello_verde']:
        # Sello verde
        ax.add_patch(FancyBboxPatch((0.31, y_sello - 0.01), 0.67, 0.14,
            boxstyle='round,pad=0.01', linewidth=1.0,
            edgecolor=GREEN, facecolor=GREEN + '15',
            transform=ax.transAxes, zorder=4))

        ax.text(0.345, y_sello + 0.10, '✓  VERIFIED', fontsize=10,
                fontweight='bold', color=GREEN_L, fontfamily='monospace',
                va='top', transform=ax.transAxes, zorder=6)

        sha_display = sello['sha256'][:32] + '...'
        ax.text(0.345, y_sello + 0.055, f'SHA-256  {sha_display}',
                fontsize=6.5, color=GREEN_L, alpha=0.85,
                fontfamily='monospace', va='top',
                transform=ax.transAxes, zorder=6)

        ax.text(0.345, y_sello + 0.018, 'Satellite data sealed · Tamper-evident hash',
                fontsize=6, color=GREEN_L, alpha=0.6,
                fontfamily='monospace', va='top',
                transform=ax.transAxes, zorder=6)

    else:
        # Sin sello — pipeline activo
        ax.add_patch(FancyBboxPatch((0.31, y_sello - 0.01), 0.67, 0.14,
            boxstyle='round,pad=0.01', linewidth=0.5,
            edgecolor=WHITE3 + '60', facecolor='#0d111760',
            transform=ax.transAxes, zorder=4))

        ax.text(0.345, y_sello + 0.10, '◌  Pipeline active · Data incoming',
                fontsize=9, fontweight='bold', color=WHITE3,
                fontfamily='monospace', va='top',
                transform=ax.transAxes, zorder=6)

        razon = sello.get('razon', 'Sin datos satelitales')
        ax.text(0.345, y_sello + 0.055, razon, fontsize=6.5, color=WHITE3,
                alpha=0.7, fontfamily='monospace', va='top',
                transform=ax.transAxes, zorder=6)

        ax.text(0.345, y_sello + 0.018,
                'Certificate issued upon verified satellite acquisition',
                fontsize=6, color=WHITE3, alpha=0.5,
                fontfamily='monospace', va='top',
                transform=ax.transAxes, zorder=6)

    # ── Footer ────────────────────────────────────────────────────────────────
    now_str = datetime.now().strftime('%Y-%m-%d %H:%M UTC-3')
    ax.text(0.5, 0.025, f'FARO PROTOCOL  ·  protocolfaro@gmail.com  ·  {now_str}',
            fontsize=6, color=WHITE3, alpha=0.5, fontfamily='monospace',
            ha='center', va='bottom', transform=ax.transAxes, zorder=5)

    # Número de certificado (area + fecha)
    cert_id = f'CERT-{area_name.upper()}-{datetime.now().strftime("%Y%m%d")}'
    ax.text(0.985, 0.025, cert_id, fontsize=5.5, color=GOLD, alpha=0.5,
            fontfamily='monospace', ha='right', va='bottom',
            transform=ax.transAxes, zorder=5)

    # ── Guardar ───────────────────────────────────────────────────────────────
    out_path = output_dir / f'faro_cert_{area_name}.png'
    fig.savefig(out_path, dpi=150, bbox_inches='tight', facecolor=BG,
                edgecolor='none')
    plt.close(fig)

    status = 'SELLO VERDE' if sello['sello_verde'] else 'sin sello'
    print(f'  [{status}]  {out_path.name}')
    return out_path


# ── CLI ────────────────────────────────────────────────────────────────────────

def _listar_areas() -> list[str]:
    areas_dir = PROJECT_ROOT / 'faro_areas'
    return sorted(p.stem for p in areas_dir.glob('*.json')
                  if p.stem != 'areas')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(
        description='FARO PROTOCOL — Generador de certificados PNG')
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--area', help='Nombre del área (ej: cordoba)')
    group.add_argument('--all',  action='store_true', help='Todas las áreas configuradas')
    group.add_argument('--list', action='store_true', help='Listar áreas disponibles')
    parser.add_argument('--out', default='.', help='Directorio de salida')
    args = parser.parse_args()

    print('=' * 60)
    print('  FARO PROTOCOL — Certificados')
    print(f'  {datetime.now().strftime("%Y-%m-%d %H:%M")}')
    print('=' * 60)

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    if args.list:
        areas = _listar_areas()
        print(f'\n  Áreas disponibles ({len(areas)}):')
        for a in areas:
            print(f'    - {a}')

    elif args.area:
        print(f'\n  Generando: {args.area}')
        generar_certificado(args.area, out_dir)

    elif args.all:
        areas = _listar_areas()
        print(f'\n  Generando {len(areas)} certificados...')
        for a in areas:
            generar_certificado(a, out_dir)

    print('\n' + '=' * 60)
