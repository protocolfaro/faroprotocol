"""
faro_design_std.py
Estándar de diseño canónico Faro Protocol v1.0

Importar en cada gen_velez_*.py con:
    from faro_design_std import draw_closing_block as _dcb

Reglas de layout obligatorias
──────────────────────────────
1. NUNCA mezclar transform=ax.transAxes con coordenadas de datos en el mismo text().
   Usar SÓLO coordenadas de datos O SÓLO transAxes — nunca ambos en el mismo llamado.
2. clip_on=False únicamente en decoraciones que CONSCIENTEMENTE salen del bbox.
3. Guardar siempre con: facecolor=BG, bbox_inches='tight', pad_inches=0.08, dpi=200.
4. Si el contenido no entra en una imagen: generar _p2.png. Nunca comprimir.

Alturas GridSpec recomendadas (height_ratios)
──────────────────────────────────────────────
  header        : 1.2
  kpi_row       : 1.6
  map/heatmap   : 4.5
  bar_chart     : 3.0   # necesita espacio para xtick labels de eje X
  table         : 3.5   # 1 fila ≈ 0.5 unidades
  closing_block : 2.8   # bloque de cierre ejecutivo — ver draw_closing_block()
  footer        : 0.8
"""
import pathlib, shutil
from matplotlib.patches import FancyBboxPatch

# ── Paleta canónica ───────────────────────────────────────────────────────────
BG     = '#06080b'
BG2    = '#0d1117'
BG3    = '#141c24'
GOLD   = '#c9a84c'
GOLDL  = '#e2c97e'
WHITE  = '#f2ede4'
WDIM   = '#9aa0a8'
REDL   = '#e74c3c'
REDXL  = '#ff6b6b'
YELL   = '#f0b429'
GRNL   = '#27ae60'
BLUE   = '#4a9ede'
CYAN   = '#40c4d4'
BORDER = '#1e2a38'

SEM_COL = {'verde': GRNL, 'amarillo': YELL, 'rojo': REDL}
DPI = 200


def draw_closing_block(ax, score: int, sem: str, actions: list, label: str = ''):
    """
    Dibuja el bloque de cierre ejecutivo estándar Faro Protocol.

    Parámetros
    ----------
    ax      : matplotlib Axes del subplot reservado (height_ratio >= 2.8).
    score   : int 0-100 — puntuación global del reporte.
    sem     : 'verde' | 'amarillo' | 'rojo' — estado general.
    actions : list[str] — 2 o 3 acciones concretas.  Se muestran hasta 3.
              Ordenar de mayor a menor urgencia.
    label   : str — nombre del reporte/sector, e.g. 'SEDE CENTRAL'.
    """
    col = SEM_COL.get(sem, WDIM)

    ax.set_facecolor(BG2)
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.axis('off')

    # Línea dorada superior
    ax.plot([0, 1], [0.975, 0.975], color=GOLD, lw=2.0,
            transform=ax.transAxes, clip_on=False)

    # Título del bloque
    header = f'RESUMEN EJECUTIVO  ·  {label}' if label else 'RESUMEN EJECUTIVO'
    ax.text(0.5, 0.935, header,
            color=GOLD, fontsize=9.5, fontweight='bold', ha='center',
            transform=ax.transAxes, fontfamily='monospace')

    # ── Score + semáforo (columna izquierda) ─────────────────────────────────
    ax.add_patch(FancyBboxPatch(
        (0.01, 0.05), 0.185, 0.845,
        boxstyle='round,pad=0.008', transform=ax.transAxes,
        facecolor=col + '18', edgecolor=col + '66', linewidth=1.4, zorder=2))

    ax.text(0.103, 0.72, str(score),
            color=col, fontsize=28, fontweight='bold', ha='center',
            transform=ax.transAxes, fontfamily='monospace')
    ax.text(0.103, 0.56, '/100',
            color=col + 'cc', fontsize=10, ha='center',
            transform=ax.transAxes, fontfamily='monospace')
    ax.text(0.103, 0.44, 'SCORE\nGLOBAL',
            color=WDIM, fontsize=7, ha='center',
            transform=ax.transAxes, fontfamily='monospace', linespacing=1.3)

    ax.plot(0.103, 0.25, 'o', ms=17, color=col,
            transform=ax.transAxes, zorder=4)
    ax.plot(0.103, 0.25, 'o', ms=27, color=col, alpha=0.12,
            transform=ax.transAxes, zorder=3)
    ax.text(0.103, 0.10, sem.upper(),
            color=col, fontsize=7.5, fontweight='bold', ha='center',
            transform=ax.transAxes, fontfamily='monospace')

    # ── Acciones prioritarias (columna derecha) ───────────────────────────────
    n = min(len(actions), 3)
    if n == 0:
        return

    ax.text(0.23, 0.935, 'ACCIONES PRIORITARIAS ESTA SEMANA',
            color=GOLDL, fontsize=8.5, fontweight='bold',
            transform=ax.transAxes, fontfamily='monospace')

    prio_colors = [REDL, YELL, GRNL]
    prio_labels = ['URGENTE  ', 'ESTA SEM.', 'PRÓXIMA  ']
    row_h = 0.26
    y0    = 0.73

    for i in range(n):
        ry = y0 - i * row_h
        pc = prio_colors[i]

        # Fondo de fila
        ax.add_patch(FancyBboxPatch(
            (0.22, ry - row_h + 0.04), 0.77, row_h - 0.03,
            boxstyle='round,pad=0.005', transform=ax.transAxes,
            facecolor=pc + '12', edgecolor=pc + '44', linewidth=0.8, zorder=2))

        # Badge de prioridad
        ax.add_patch(FancyBboxPatch(
            (0.225, ry - row_h + 0.055), 0.092, row_h - 0.06,
            boxstyle='round,pad=0.004', transform=ax.transAxes,
            facecolor=pc + '44', edgecolor=pc, linewidth=0.9, zorder=3))
        ax.text(0.271, ry - row_h / 2 + 0.014, prio_labels[i],
                color=pc, fontsize=6.5, fontweight='bold', ha='center',
                transform=ax.transAxes, va='center', fontfamily='monospace', zorder=4)

        # Texto de la acción
        ax.text(0.33, ry - row_h / 2 + 0.014, actions[i],
                color=WHITE + 'dd', fontsize=8.5,
                transform=ax.transAxes, va='center', zorder=4)


def faro_save(fig, out_path, copy_desktop: bool = True, dpi: int = DPI):
    """
    Guarda el reporte con los parámetros estándar Faro Protocol.
    Copia al escritorio si copy_desktop=True.
    Retorna pathlib.Path del archivo guardado.
    """
    import matplotlib.pyplot as plt
    out = pathlib.Path(out_path)
    out.parent.mkdir(exist_ok=True)
    import matplotlib
    fig.subplots_adjust(left=0.03, right=0.97, top=0.995, bottom=0.008, hspace=0.0)
    fig.savefig(str(out), dpi=dpi, facecolor=BG, bbox_inches='tight', pad_inches=0.08)
    plt.close(fig)
    print(f'Saved: {out}')
    if copy_desktop:
        dst = pathlib.Path.home() / 'Desktop' / out.name
        shutil.copy2(str(out), str(dst))
        print(f'Desktop: {dst}')
    return out
