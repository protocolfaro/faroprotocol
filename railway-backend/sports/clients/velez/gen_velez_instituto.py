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

import os as _os, json as _json

_out_path   = _os.environ.get("FARO_OUT_PATH")
_score_inst = None
_sem_inst   = None
_insar_inst = None
_thermal_inst = None
_detalle_inst = None

_vd_path = _os.environ.get("FARO_VD_PATH")
if _vd_path:
    try:
        with open(_vd_path, encoding="utf-8") as _f:
            _vd = _json.load(_f)
        _si = _vd.get("sectores", {}).get("instituto", {})
        if isinstance(_si.get("score"), (int, float)):
            _score_inst = int(_si["score"])
        _sem_inst     = _si.get("sem")
        _insar_inst   = _si.get("insar_deformacion") or _si.get("insar_mm")
        _thermal_inst = _si.get("thermal") or _si.get("thermal_temp")
        _detalle_inst = _si.get("detalle")
    except Exception as _e:
        print(f"FARO_VD_PATH instituto: {_e}")

_has_data = any(v is not None for v in (_score_inst, _insar_inst, _thermal_inst))

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

# Título — calibración o activo según datos disponibles
_SEM_COL = {'verde': '#27ae60', 'amarillo': '#f0b429', 'rojo': '#e74c3c'}
_hdr_color = _SEM_COL.get(_sem_inst, GOLD) if _has_data else GOLD
ax.text(0.5, 0.88, 'ACTIVO' if _has_data else 'EN CALIBRACIÓN',
        color=_hdr_color, fontsize=26, fontweight='bold', ha='center', va='center',
        fontfamily='monospace')

if _has_data:
    # KPI row
    _kpis = [
        ('SCORE',       f'{_score_inst}/100'        if _score_inst   is not None else '—'),
        ('InSAR',       f'{_insar_inst:.2f} mm'     if _insar_inst   is not None else '—'),
        ('TEMP. TECHO', f'{_thermal_inst:.1f} °C'   if _thermal_inst is not None else '—'),
    ]
    for _ki, (_lbl, _val) in enumerate(_kpis):
        _kx = 0.20 + _ki * 0.30
        ax.text(_kx, 0.72, _lbl, color=WDIM, fontsize=8, ha='center', fontfamily='monospace')
        ax.text(_kx, 0.60, _val, color=WHITE, fontsize=14, fontweight='bold',
                ha='center', fontfamily='monospace')
    ax.plot([0.25, 0.75], [0.53, 0.53], color=WDIM + '55', lw=0.8, linestyle=':')
    _det = _detalle_inst or 'Datos satelitales disponibles — pipeline activo.'
    ax.text(0.5, 0.44, _det[:80], color=WDIM, fontsize=8, ha='center',
            fontfamily='monospace')
else:
    ax.text(0.5, 0.66,
            'Sin datos satelitales disponibles para este sector.\n'
            'Primera cobertura disponible cuando el pipeline procese imágenes del área.',
            color=WHITE, fontsize=11, ha='center', va='center')
    ax.plot([0.25, 0.75], [0.53, 0.53], color=WDIM + '55', lw=0.8, linestyle=':')
    ax.text(0.5, 0.44,
            'Instituto Vélez  ·  Lat -34.6380  ·  Lon -58.5230  ·  WGS-84',
            color=WDIM, fontsize=8.5, ha='center', va='center', fontfamily='monospace')
    ax.text(0.5, 0.32,
            'Sentinel-2 MSI  ·  Sentinel-1 SAR / InSAR  ·  Primera pasada pendiente',
            color=WDIM, fontsize=8, ha='center', va='center', fontfamily='monospace',
            style='italic')
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
