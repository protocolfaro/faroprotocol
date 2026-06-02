"""
gen_velez_instituto.py
Faro Protocol - Instituto Velez Infanto Juvenil - Velez Sarsfield
Placeholder profesional — en calibracion satelital.
"""
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch
import hashlib
from datetime import datetime

BG     = '#0a0a0a'
BG2    = '#0d1117'
BG3    = '#141c24'
GOLD   = '#c9a84c'
GOLDL  = '#e2c97e'
WHITE  = '#f2ede4'
WDIM   = '#9aa0a8'
BORDER = '#1e2a38'
DPI    = 150

now   = datetime.now()
WEEK  = now.strftime('Semana del %d de %B de %Y')
FECHA = now.strftime('%d/%m/%Y')
CERT  = hashlib.sha256(f"FARO-VLZ-INST-{now.isoformat()}".encode()).hexdigest()[:20].upper()

import os as _os
_out_path = _os.environ.get("FARO_OUT_PATH")

fig = plt.figure(figsize=(12, 7), facecolor=BG)

# ── HEADER ───────────────────────────────────────────────────────────────────
ax_hdr = fig.add_axes([0, 0.875, 1, 0.125])
ax_hdr.set_facecolor(BG3)
ax_hdr.set_xlim(0, 1); ax_hdr.set_ylim(0, 1)
ax_hdr.axis('off')
ax_hdr.plot([0, 1], [0.04, 0.04], color=GOLD, lw=2.5, transform=ax_hdr.transAxes)
ax_hdr.text(0.5, 0.76,
            'FARO PROTOCOL  ·  Instituto Vélez — Infanto Juvenil',
            color=GOLD, fontsize=13, fontweight='bold', ha='center', va='center',
            transform=ax_hdr.transAxes, fontfamily='monospace')
ax_hdr.text(0.5, 0.32, WEEK,
            color=WHITE, fontsize=9.5, ha='center', va='center',
            transform=ax_hdr.transAxes)
ax_hdr.text(0.985, 0.55, f'#{CERT}',
            color=WDIM, fontsize=6, fontfamily='monospace', ha='right', va='center',
            transform=ax_hdr.transAxes)

# ── MAIN AREA ────────────────────────────────────────────────────────────────
ax = fig.add_axes([0.04, 0.115, 0.92, 0.745])
ax.set_facecolor(BG); ax.axis('off')
ax.set_xlim(0, 1); ax.set_ylim(0, 1)

# Marco central
ax.add_patch(FancyBboxPatch((0.10, 0.12), 0.80, 0.72,
             boxstyle='round,pad=0.02',
             facecolor=BG3, edgecolor=GOLD + '55', lw=1.2))

# Línea decorativa superior dentro del marco
ax.plot([0.12, 0.88], [0.80, 0.80], color=GOLD + '44', lw=0.8)

# Título EN CALIBRACIÓN
ax.text(0.5, 0.88, 'EN CALIBRACIÓN',
        color=GOLD, fontsize=26, fontweight='bold', ha='center', va='center',
        fontfamily='monospace')

# Texto principal
ax.text(0.5, 0.66,
        'Primera cobertura satelital disponible próxima semana',
        color=WHITE, fontsize=13, ha='center', va='center')

# Separador puntado
ax.plot([0.25, 0.75], [0.53, 0.53], color=WDIM + '55', lw=0.8, linestyle=':')

# Coordenadas + fuentes
ax.text(0.5, 0.44,
        'Instituto Vélez  ·  Lat -34.6380  ·  Lon -58.5230  ·  WGS-84',
        color=WDIM, fontsize=8.5, ha='center', va='center', fontfamily='monospace')
ax.text(0.5, 0.32,
        'Sentinel-2 MSI  ·  Sentinel-1 SAR / InSAR  ·  Primera pasada pendiente',
        color=WDIM, fontsize=8, ha='center', va='center', fontfamily='monospace',
        style='italic')

# Nota inferior
ax.text(0.5, 0.20,
        'Los datos aparecerán automáticamente en el siguiente ciclo de cobertura.',
        color=WDIM + 'aa', fontsize=7.5, ha='center', va='center')

# ── FOOTER ───────────────────────────────────────────────────────────────────
ax_ftr = fig.add_axes([0, 0, 1, 0.105])
ax_ftr.set_facecolor(BG3)
ax_ftr.set_xlim(0, 1); ax_ftr.set_ylim(0, 1)
ax_ftr.axis('off')
ax_ftr.plot([0, 1], [0.92, 0.92], color=BORDER, lw=0.5, transform=ax_ftr.transAxes)
ax_ftr.text(0.015, 0.48,
            f'InSAR Sentinel-1 / Sentinel-2  ·  Faro Protocol  ·  {FECHA}',
            color=WDIM, fontsize=6.5, fontfamily='monospace', va='center',
            transform=ax_ftr.transAxes)
ax_ftr.text(0.985, 0.48,
            f'© Faro Protocol {now.year}',
            color=WDIM, fontsize=6.5, fontfamily='monospace', ha='right', va='center',
            transform=ax_ftr.transAxes)

# ── SAVE ─────────────────────────────────────────────────────────────────────
import pathlib as _pl
_REPORT_DIR = _pl.Path(__file__).parents[4] / 'reportes_velez'
_REPORT_DIR.mkdir(exist_ok=True)
out = str(_out_path or (_REPORT_DIR / 'faro_reporte_velez_instituto.png'))
fig.savefig(out, dpi=DPI, facecolor=BG, bbox_inches='tight', pad_inches=0.04)
plt.close(fig)
print(f'Saved: {out}')
