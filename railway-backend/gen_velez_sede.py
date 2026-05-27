"""
gen_velez_sede.py
Faro Protocol - Reporte Sede Central - Velez Sarsfield
"""
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch
import hashlib
from datetime import datetime

# ─── PALETA ──────────────────────────────────────────────────────────────────
BG     = '#06080b'
BG2    = '#0d1117'
BG3    = '#141c24'
GOLD   = '#c9a84c'
WHITE  = '#f2ede4'
WDIM   = '#9aa0a8'
REDL   = '#e74c3c'
YELL   = '#f0b429'
GRNL   = '#27ae60'
BORDER = '#1e2a38'
DPI    = 150

SEM_COL   = {'verde': GRNL, 'amarillo': YELL, 'rojo': REDL}
SEM_LABEL = {'verde': 'ÓPTIMO', 'amarillo': 'ATENCIÓN', 'rojo': 'CRÍTICO'}

# ─── DATOS (fallback hardcodeado) ────────────────────────────────────────────
SCORE      = 75
SCORE_PREV = 75
SEM        = 'amarillo'
DETALLE    = 'InSAR Anexo Norte: 0.22 mm · Landsat: 41.2°C'
TITULO     = 'Sede Central'
SECTOR_KEY = 'sede'
OUT_NAME   = 'faro_reporte_velez_sede.png'

now   = datetime.now()
FECHA = now.strftime('%d/%m/%Y')
CERT  = hashlib.sha256(f"FARO-VLZ-SEDE-{now.isoformat()}".encode()).hexdigest()[:20].upper()

# ─── REAL DATA OVERRIDE ──────────────────────────────────────────────────────
import os as _os, json as _json
_vd_path = _os.environ.get("FARO_VD_PATH")
if _vd_path:
    try:
        with open(_vd_path, encoding="utf-8") as _f:
            _vd = _json.load(_f)
        _s = _vd.get("sectores", {}).get(SECTOR_KEY, {})
        if _s:
            SCORE      = _s.get("score",      SCORE)
            SCORE_PREV = _s.get("score_prev",  SCORE_PREV)
            SEM        = _s.get("sem",         SEM)
            DETALLE    = _s.get("detalle",     DETALLE)
    except Exception as _e:
        print(f"FARO_VD_PATH {SECTOR_KEY}: {_e}")
_out_path = _os.environ.get("FARO_OUT_PATH")

# ─── FIGURA ──────────────────────────────────────────────────────────────────
col = SEM_COL.get(SEM, YELL)

fig = plt.figure(figsize=(12, 7), facecolor=BG)

# Header
ax_hdr = fig.add_axes([0, 0.94, 1, 0.06])
ax_hdr.set_facecolor(BG3)
ax_hdr.set_xlim(0, 1); ax_hdr.set_ylim(0, 1)
ax_hdr.axis('off')
ax_hdr.axhline(0, color=GOLD, lw=1.5)
ax_hdr.text(0.015, 0.55, "FARO PROTOCOL", color=GOLD, fontsize=9,
            fontweight='bold', fontfamily='monospace', va='center')
ax_hdr.text(0.015, 0.1,  now.strftime("Semana del %d de %B de %Y"),
            color=WDIM, fontsize=7, fontfamily='monospace', va='center')
ax_hdr.text(0.5,   0.5,  TITULO, color=WHITE, fontsize=11,
            fontweight='bold', ha='center', va='center')
ax_hdr.text(0.985, 0.5,  f"#{CERT}", color=WDIM, fontsize=6,
            fontfamily='monospace', ha='right', va='center')

# Footer
ax_ftr = fig.add_axes([0, 0, 1, 0.035])
ax_ftr.set_facecolor(BG3)
ax_ftr.set_xlim(0, 1); ax_ftr.set_ylim(0, 1)
ax_ftr.axis('off')
ax_ftr.axhline(1, color=BORDER, lw=0.5)
ax_ftr.text(0.015, 0.5, f"InSAR Sentinel-1 / Sentinel-2 · Faro Protocol · {FECHA}",
            color=WDIM, fontsize=6, fontfamily='monospace', va='center')
ax_ftr.text(0.985, 0.5, f"© Faro Protocol {now.year}",
            color=WDIM, fontsize=6, fontfamily='monospace', ha='right', va='center')

# Main area
ax = fig.add_axes([0.05, 0.10, 0.90, 0.82])
ax.set_facecolor(BG); ax.axis('off')
ax.set_xlim(0, 1); ax.set_ylim(0, 1)

# Score
ax.text(0.5, 0.72, str(SCORE), color=col, fontsize=60, fontweight='bold',
        ha='center', va='center')
ax.text(0.5, 0.54, "/100", color=WDIM, fontsize=18, ha='center', va='center')
ax.text(0.5, 0.44, SEM_LABEL.get(SEM, SEM.upper()), color=col, fontsize=14,
        fontweight='bold', ha='center', fontfamily='monospace')

# Bar
ax.add_patch(FancyBboxPatch((0.15, 0.34), 0.70, 0.042, boxstyle='round,pad=0',
                             facecolor=BG3, edgecolor=BORDER, lw=0.5))
fill = 0.70 * min(max(SCORE / 100, 0), 1)
ax.add_patch(FancyBboxPatch((0.15, 0.34), fill, 0.042, boxstyle='round,pad=0',
                             facecolor=col, alpha=0.5, edgecolor='none'))

# Detail
ax.text(0.5, 0.25, DETALLE, color=WHITE, fontsize=9, ha='center', va='center')

# Delta
delta = SCORE - SCORE_PREV
d_col = GRNL if delta > 0 else (REDL if delta < 0 else WDIM)
d_sym = '▲' if delta > 0 else ('▼' if delta < 0 else '—')
ax.text(0.5, 0.16, f"{d_sym} {abs(delta):.0f} vs semana anterior",
        color=d_col, fontsize=10, ha='center', fontweight='bold')

# ─── SAVE ────────────────────────────────────────────────────────────────────
import pathlib as _pl
_REPORT_DIR = _pl.Path(__file__).parent.parent / 'reportes_velez'
_REPORT_DIR.mkdir(exist_ok=True)
out = str(_out_path or (_REPORT_DIR / OUT_NAME))
fig.savefig(out, dpi=DPI, facecolor=BG, bbox_inches='tight', pad_inches=0.04)
plt.close(fig)
print(f"Saved: {out}")
