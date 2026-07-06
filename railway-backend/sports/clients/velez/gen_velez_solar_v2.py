"""
Faro Protocol — Reporte Solar Vélez v2
Datos reales vía faro_assembler / FARO_VD_PATH.
Sin datos por panel individual → muestra score global con barra de progreso.
"""
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.gridspec as gridspec
import matplotlib.patches as mpatches
from matplotlib.patches import FancyBboxPatch
import matplotlib.ticker as ticker
import numpy as np
import hashlib, os, json
from datetime import datetime, timedelta

# ─── PALETA ──────────────────────────────────────────────────────────────────
BG    = '#06080b'
BG2   = '#0d1117'
BG3   = '#141c24'
GOLD  = '#c9a84c'
WHITE = '#f2ede4'
WDIM  = '#9aa0a8'
REDL  = '#e74c3c'
YELL  = '#f0b429'
GRNL  = '#27ae60'

INSTALLED_KWP = 120.0

# ─── DATOS REALES ─────────────────────────────────────────────────────────────
avg_eff       = None
actual_kwp    = None
loss_kwp      = None
solar_detalle = None
ghi_kwh_m2    = None   # GHI diario desde faro_v2_reports (Open-Meteo/ERA5)
panel_data    = {}     # zona→{eficiencia_pct, temperatura_c, estado} desde velez_solar

_vd_path = os.environ.get("FARO_VD_PATH")
if _vd_path:
    try:
        with open(_vd_path, encoding="utf-8") as _f:
            _vd = json.load(_f)
        _s = _vd.get("sectores", {}).get("solar", {})
        panel_data = _s.get("panel_data") or {}
        # eficiencia_pct del assembler (calculada desde velez_solar); fallback score
        _eff_src = _s.get("eficiencia_pct") or _s.get("score")
        if not isinstance(_eff_src, (int, float)) and panel_data:
            _effs_init = [p["eficiencia_pct"] for p in panel_data.values()
                          if isinstance(p.get("eficiencia_pct"), (int, float))]
            if _effs_init:
                _eff_src = round(sum(_effs_init) / len(_effs_init), 1)
        if isinstance(_eff_src, (int, float)):
            avg_eff    = float(_eff_src)
            actual_kwp = INSTALLED_KWP * avg_eff / 100
            loss_kwp   = INSTALLED_KWP - actual_kwp
        solar_detalle = _s.get("detalle")
        # GHI real desde faro_v2_reports → _apply_faro_v2_overlay() del assembler
        if isinstance(_s.get("ghi_kwh_m2"), (int, float)):
            ghi_kwh_m2 = float(_s["ghi_kwh_m2"])
    except Exception as _e:
        print(f"FARO_VD_PATH solar: {_e}")

_out_path = os.environ.get("FARO_OUT_PATH")

now       = datetime.now()
SCAN_DATE = now.strftime('%d de %B de %Y  ·  %H:%M UTC-3')
SHA       = hashlib.sha256(f"FARO-VELEZ-SOLAR-V2-{now.isoformat()}".encode()).hexdigest()

# ─── FIGURA ──────────────────────────────────────────────────────────────────
DPI = 200
FW, FH = 13.5, 30
fig = plt.figure(figsize=(FW, FH), dpi=DPI, facecolor=BG)
fig.subplots_adjust(left=0.06, right=0.97, top=0.99, bottom=0.01, hspace=0.04)

gs = gridspec.GridSpec(10, 1, figure=fig, hspace=0.04, height_ratios=[
    1.5,   # 0  header
    1.9,   # 1  KPI ejecutivo
    5.2,   # 2  sin dato mapa térmico
    0.70,  # 3  nota
    5.2,   # 4  sin dato mapa eficiencia
    0.70,  # 5  nota
    4.8,   # 6  tabla sin dato
    2.6,   # 7  curva producción
    1.9,   # 8  alertas
    0.85,  # 9  footer
])

# ─── HELPERS ─────────────────────────────────────────────────────────────────
def logo_badges(ax, ys=0.14):
    for lx, ltxt in [(0.06,'ESA'),(0.14,'COP'),(0.22,'NASA')]:
        ax.add_patch(FancyBboxPatch((lx-.025,.03),.065,.22,
            boxstyle='round,pad=0.01',transform=ax.transAxes,
            facecolor=BG3,edgecolor=GOLD+'88',linewidth=0.8,zorder=3))
        ax.text(lx+.008,ys,ltxt,transform=ax.transAxes,color=GOLD,
            fontsize=7,fontweight='bold',ha='center',va='center',
            fontfamily='monospace',zorder=4)
    for lx, ltxt in [(0.78,'ESA'),(0.86,'COP'),(0.94,'NASA')]:
        ax.add_patch(FancyBboxPatch((lx-.025,.03),.065,.22,
            boxstyle='round,pad=0.01',transform=ax.transAxes,
            facecolor=BG3,edgecolor=GOLD+'88',linewidth=0.8,zorder=3))
        ax.text(lx+.008,ys,ltxt,transform=ax.transAxes,color=GOLD,
            fontsize=7,fontweight='bold',ha='center',va='center',
            fontfamily='monospace',zorder=4)

def sin_dato_panel(ax, titulo):
    ax.set_facecolor(BG2); ax.set_xlim(0,1); ax.set_ylim(0,1); ax.axis('off')
    ax.add_patch(mpatches.Rectangle((0,0.93),1,0.07,
        transform=ax.transAxes,facecolor=BG3,edgecolor='none'))
    ax.axhline(0.93, color=GOLD+'55', linewidth=1.5)
    ax.text(0.5, 0.963, titulo,
        transform=ax.transAxes, color=GOLD+'99', fontsize=11, fontweight='bold',
        ha='center', va='center', fontfamily='monospace')
    ax.add_patch(FancyBboxPatch((0.15, 0.22), 0.70, 0.62,
        boxstyle='round,pad=0.02', transform=ax.transAxes,
        facecolor=BG3, edgecolor=WDIM+'33', linewidth=1.0))
    ax.text(0.5, 0.60, 'SIN DATO',
        transform=ax.transAxes, color=WDIM, fontsize=22, fontweight='bold',
        ha='center', va='center', fontfamily='monospace')
    ax.text(0.5, 0.40,
        'Datos por panel disponibles cuando la tabla\n"velez_solar" tenga lecturas en Supabase.',
        transform=ax.transAxes, color=WDIM+'88', fontsize=9,
        ha='center', va='center', fontfamily='monospace')

# ═══════════════════════════════════════════════════════════════════════════════
# 0  HEADER
# ═══════════════════════════════════════════════════════════════════════════════
ax0 = fig.add_subplot(gs[0])
ax0.set_facecolor(BG); ax0.set_xlim(0,1); ax0.set_ylim(0,1); ax0.axis('off')
ax0.add_patch(mpatches.Rectangle((0,0.93),1,0.07,
    transform=ax0.transAxes,color=GOLD,zorder=2))
ax0.text(0.5,0.88,'FARO PROTOCOL — FORTÍN INTELIGENTE',
    transform=ax0.transAxes,color=GOLD,fontsize=17,fontweight='bold',
    ha='center',va='top',fontfamily='monospace')
ax0.text(0.5,0.70,'VÉLEZ SARSFIELD · MONITOREO SOLAR — TECHO SUR AMALFITANI',
    transform=ax0.transAxes,color=WHITE,fontsize=12,fontweight='bold',
    ha='center',va='top',fontfamily='sans-serif')
ax0.text(0.5,0.51,f'Escaneo satelital: {SCAN_DATE}',
    transform=ax0.transAxes,color=WDIM,fontsize=9,ha='center',va='top',fontfamily='monospace')
ax0.text(0.5,0.35,'Lat -34.6379  ·  Lon -58.5288  ·  210 paneles bifaciales 120 kWp · Abril 2026',
    transform=ax0.transAxes,color=WDIM,fontsize=8.5,ha='center',va='top',fontfamily='monospace')
ax0.add_patch(FancyBboxPatch((0.29,0.02),0.42,0.22,
    boxstyle='round,pad=0.01',transform=ax0.transAxes,
    facecolor=BG3,edgecolor=GOLD,linewidth=1.2,zorder=3))
ax0.text(0.5,0.13,'  ANÁLISIS ENERGÉTICO · LANDSAT 8/9 + SENTINEL-2  ',
    transform=ax0.transAxes,color=GOLD,fontsize=8.5,fontweight='bold',
    ha='center',va='bottom',fontfamily='monospace',zorder=4)
logo_badges(ax0)
ax0.axhline(0.0,color=GOLD,linewidth=1.5)

# ═══════════════════════════════════════════════════════════════════════════════
# 1  PANEL EJECUTIVO — SCORE GLOBAL + BARRA DE PROGRESO
# ═══════════════════════════════════════════════════════════════════════════════
ax1 = fig.add_subplot(gs[1])
ax1.set_facecolor(BG2); ax1.set_xlim(0,1); ax1.set_ylim(0,1); ax1.axis('off')

ax1.add_patch(mpatches.Rectangle((0,0.90),1,0.10,
    transform=ax1.transAxes,facecolor=BG3,edgecolor='none'))
ax1.axhline(0.90,color=GOLD,linewidth=2.0)
ax1.text(0.5,0.95,'PANEL EJECUTIVO · ESTADO DEL SISTEMA SOLAR',
    transform=ax1.transAxes,color=GOLD,fontsize=10.5,fontweight='bold',
    ha='center',va='center',fontfamily='monospace')

if avg_eff is not None:
    eff_col = GRNL if avg_eff >= 85 else (YELL if avg_eff >= 65 else REDL)

    ax1.add_patch(FancyBboxPatch((0.05, 0.05), 0.20, 0.82,
        boxstyle='round,pad=0.008', transform=ax1.transAxes,
        facecolor=eff_col+'18', edgecolor=eff_col, linewidth=1.6, zorder=2))
    ax1.text(0.150, 0.78, 'EFICIENCIA\nGLOBAL', transform=ax1.transAxes, color=WDIM,
        fontsize=7.5, ha='center', va='top', fontfamily='monospace', linespacing=1.3)
    ax1.text(0.150, 0.43, f'{avg_eff:.0f}%', transform=ax1.transAxes, color=eff_col,
        fontsize=26, fontweight='bold', ha='center', va='center', fontfamily='monospace')

    ax1.add_patch(FancyBboxPatch((0.27, 0.05), 0.20, 0.82,
        boxstyle='round,pad=0.008', transform=ax1.transAxes,
        facecolor=BG3, edgecolor=WDIM+'44', linewidth=0.9, zorder=2))
    ax1.text(0.370, 0.78, 'PRODUCCIÓN\nACTUAL', transform=ax1.transAxes, color=WDIM,
        fontsize=7.5, ha='center', va='top', fontfamily='monospace', linespacing=1.3)
    ax1.text(0.370, 0.43, f'{actual_kwp:.1f}', transform=ax1.transAxes, color=WHITE,
        fontsize=22, fontweight='bold', ha='center', va='center', fontfamily='monospace')
    ax1.text(0.370, 0.16, 'kWp', transform=ax1.transAxes, color=WDIM,
        fontsize=9, ha='center', va='center', fontfamily='monospace')

    ax1.add_patch(FancyBboxPatch((0.49, 0.05), 0.20, 0.82,
        boxstyle='round,pad=0.008', transform=ax1.transAxes,
        facecolor=BG3, edgecolor=WDIM+'44', linewidth=0.9, zorder=2))
    ax1.text(0.590, 0.78, 'CAPACIDAD\nMÁXIMA', transform=ax1.transAxes, color=WDIM,
        fontsize=7.5, ha='center', va='top', fontfamily='monospace', linespacing=1.3)
    ax1.text(0.590, 0.43, f'{INSTALLED_KWP:.0f}', transform=ax1.transAxes, color=WDIM,
        fontsize=22, fontweight='bold', ha='center', va='center', fontfamily='monospace')
    ax1.text(0.590, 0.16, 'kWp', transform=ax1.transAxes, color=WDIM,
        fontsize=9, ha='center', va='center', fontfamily='monospace')

    bar_l, bar_r = 0.71, 0.97
    bar_b, bar_h = 0.30, 0.30
    ax1.add_patch(mpatches.Rectangle((bar_l, bar_b), bar_r-bar_l, bar_h,
        transform=ax1.transAxes, facecolor=BG3, edgecolor=WDIM+'33', linewidth=0.8))
    bar_w = (bar_r-bar_l) * (avg_eff/100)
    ax1.add_patch(mpatches.Rectangle((bar_l, bar_b), bar_w, bar_h,
        transform=ax1.transAxes, facecolor=eff_col, alpha=0.7))
    ax1.text((bar_l+bar_r)/2, bar_b+bar_h+0.06, 'BARRA DE EFICIENCIA',
        transform=ax1.transAxes, color=WDIM, fontsize=7, ha='center', fontfamily='monospace')
    ax1.text((bar_l+bar_r)/2, bar_b-0.08, f'{avg_eff:.0f}% de 100%',
        transform=ax1.transAxes, color=eff_col, fontsize=9, fontweight='bold',
        ha='center', fontfamily='monospace')

    if solar_detalle:
        ax1.text(0.5, 0.06, solar_detalle,
            transform=ax1.transAxes, color=WDIM+'aa', fontsize=8,
            ha='center', va='bottom', fontfamily='monospace')

    # GHI real (faro_v2_reports / Open-Meteo ERA5) — cuarto KPI si disponible
    if ghi_kwh_m2 is not None:
        ax1.add_patch(FancyBboxPatch((0.71, 0.05), 0.26, 0.82,
            boxstyle='round,pad=0.008', transform=ax1.transAxes,
            facecolor=BG3, edgecolor=WDIM+'44', linewidth=0.9, zorder=2))
        ax1.text(0.84, 0.78, 'IRRADIACIÓN\nDIARIA (GHI)',
            transform=ax1.transAxes, color=WDIM,
            fontsize=7.5, ha='center', va='top', fontfamily='monospace', linespacing=1.3)
        ax1.text(0.84, 0.43, f'{ghi_kwh_m2:.2f}',
            transform=ax1.transAxes, color=WHITE,
            fontsize=22, fontweight='bold', ha='center', va='center', fontfamily='monospace')
        ax1.text(0.84, 0.16, 'kWh/m²·día',
            transform=ax1.transAxes, color=WDIM,
            fontsize=9, ha='center', va='center', fontfamily='monospace')
        ax1.text(0.84, 0.07, 'Open-Meteo ERA5',
            transform=ax1.transAxes, color=WDIM+'66',
            fontsize=6.5, ha='center', va='bottom', fontfamily='monospace')
    else:
        ax1.text(0.84, 0.78, 'PANELES OK\nDEGRADADOS\nFALLA\nSOMBRA',
            transform=ax1.transAxes, color=WDIM+'55', fontsize=7,
            ha='center', va='top', fontfamily='monospace', linespacing=1.8)
        ax1.text(0.84, 0.52, '—\n—\n—\n—',
            transform=ax1.transAxes, color=WDIM+'44', fontsize=9,
            ha='center', va='top', fontfamily='monospace', linespacing=1.8)
        ax1.text(0.84, 0.08, 'Sin dato individual',
            transform=ax1.transAxes, color=WDIM+'44', fontsize=7,
            ha='center', va='bottom', fontfamily='monospace')
else:
    ax1.text(0.5, 0.50, 'SIN DATO',
        transform=ax1.transAxes, color=WDIM, fontsize=22, fontweight='bold',
        ha='center', va='center', fontfamily='monospace')
    ax1.text(0.5, 0.28, 'Score solar no disponible en el assembler.',
        transform=ax1.transAxes, color=WDIM+'88', fontsize=10,
        ha='center', va='center', fontfamily='monospace')

ax1.axhline(0.0,color=GOLD+'44',linewidth=0.5)

# ═══════════════════════════════════════════════════════════════════════════════
# 2  MAPA TÉRMICO — por zona si hay datos, SIN DATO si no
# ═══════════════════════════════════════════════════════════════════════════════
ax2 = fig.add_subplot(gs[2])
_temps_z = [(pid, float(p["temperatura_c"])) for pid, p in panel_data.items()
            if isinstance(p.get("temperatura_c"), (int, float))]
if _temps_z:
    ax2.set_facecolor(BG2); ax2.axis('off'); ax2.set_xlim(0, 1); ax2.set_ylim(0, 1)
    ax2.add_patch(mpatches.Rectangle((0, 0.93), 1, 0.07,
        transform=ax2.transAxes, facecolor=BG3, edgecolor='none'))
    ax2.axhline(0.93, color=GOLD + '55', linewidth=1.5)
    ax2.text(0.5, 0.963, 'PANEL 1 · TEMPERATURA POR ZONA — MODELO NOCT BIFACIAL',
        transform=ax2.transAxes, color=GOLD + '99', fontsize=11, fontweight='bold',
        ha='center', va='center', fontfamily='monospace')
    _tz_n = len(_temps_z)
    _tz_bh = min(0.14, 0.55 / max(_tz_n, 1))
    _tz_gap = _tz_bh * 0.5
    _tz_start = 0.78
    _tz_max_t = max(t for _, t in _temps_z) or 1.0
    for _ti, (pid, temp) in enumerate(_temps_z):
        _ty = _tz_start - _ti * (_tz_bh + _tz_gap)
        _tc = REDL if temp > 65 else (YELL if temp > 45 else GRNL)
        _tw = max(0.04, 0.70 * temp / max(_tz_max_t, 1.0))
        ax2.add_patch(mpatches.Rectangle((0.25, _ty - _tz_bh), _tw, _tz_bh,
            transform=ax2.transAxes, facecolor=_tc, alpha=0.78, zorder=2))
        ax2.text(0.24, _ty - _tz_bh / 2, pid.replace('_', ' ').upper(),
            transform=ax2.transAxes, color=WHITE, fontsize=9,
            ha='right', va='center', fontfamily='monospace')
        ax2.text(0.25 + _tw + 0.015, _ty - _tz_bh / 2, f'{temp:.1f} °C',
            transform=ax2.transAxes, color=_tc, fontsize=10, fontweight='bold',
            ha='left', va='center', fontfamily='monospace')
    ax2.text(0.5, 0.08, 'T_panel = T_amb + (NOCT−20)/800 × GHI_pico  ·  NOCT bifacial = 43°C',
        transform=ax2.transAxes, color=WDIM + '88', fontsize=7.5,
        ha='center', va='bottom', fontfamily='monospace')
    ax2.text(0.5, 0.03, 'Fuente: NASA POWER ALLSKY_SFC_SW_DWN + T2M_MAX  ·  Cálculo por zona techo sur',
        transform=ax2.transAxes, color=WDIM + '55', fontsize=7,
        ha='center', va='bottom', fontfamily='monospace')
else:
    sin_dato_panel(ax2, 'PANEL 1 · MAPA TÉRMICO — LANDSAT 8/9')

# ═══════════════════════════════════════════════════════════════════════════════
# 3  NOTA
# ═══════════════════════════════════════════════════════════════════════════════
ax3 = fig.add_subplot(gs[3])
ax3.set_facecolor(BG3); ax3.axis('off'); ax3.set_xlim(0,1); ax3.set_ylim(0,1)
ax3.axhline(0.97, color=GOLD+'44', linewidth=0.8)
ax3.axhline(0.03, color=GOLD+'44', linewidth=0.8)
ax3.text(0.5, 0.50,
    'Mapa térmico por panel disponible cuando la tabla velez_solar (Supabase) tenga datos de temperatura_c por panel_id.',
    transform=ax3.transAxes, color=WDIM+'99', fontsize=8.5,
    ha='center', va='center', fontfamily='monospace')

# ═══════════════════════════════════════════════════════════════════════════════
# 4  MAPA DE EFICIENCIA — por zona si hay datos, SIN DATO si no
# ═══════════════════════════════════════════════════════════════════════════════
ax4 = fig.add_subplot(gs[4])
_effs_z = [(pid, float(p["eficiencia_pct"])) for pid, p in panel_data.items()
           if isinstance(p.get("eficiencia_pct"), (int, float))]
if _effs_z:
    ax4.set_facecolor(BG2); ax4.axis('off'); ax4.set_xlim(0, 1); ax4.set_ylim(0, 1)
    ax4.add_patch(mpatches.Rectangle((0, 0.93), 1, 0.07,
        transform=ax4.transAxes, facecolor=BG3, edgecolor='none'))
    ax4.axhline(0.93, color=GOLD + '55', linewidth=1.5)
    ax4.text(0.5, 0.963, 'PANEL 2 · EFICIENCIA POR ZONA — GHI REAL NASA POWER',
        transform=ax4.transAxes, color=GOLD + '99', fontsize=11, fontweight='bold',
        ha='center', va='center', fontfamily='monospace')
    _ez_n = len(_effs_z)
    _ez_bh = min(0.14, 0.55 / max(_ez_n, 1))
    _ez_gap = _ez_bh * 0.5
    _ez_start = 0.78
    for _ei, (pid, eff) in enumerate(_effs_z):
        _ey = _ez_start - _ei * (_ez_bh + _ez_gap)
        _ec = GRNL if eff >= 80 else (YELL if eff >= 60 else REDL)
        _ew = eff / 100.0 * 0.70
        ax4.add_patch(mpatches.Rectangle((0.25, _ey - _ez_bh), _ew, _ez_bh,
            transform=ax4.transAxes, facecolor=_ec, alpha=0.78, zorder=2))
        ax4.add_patch(mpatches.Rectangle((0.25, _ey - _ez_bh), 0.70, _ez_bh,
            transform=ax4.transAxes, facecolor='none', edgecolor=WDIM + '33', linewidth=0.7))
        ax4.text(0.24, _ey - _ez_bh / 2, pid.replace('_', ' ').upper(),
            transform=ax4.transAxes, color=WHITE, fontsize=9,
            ha='right', va='center', fontfamily='monospace')
        ax4.text(0.25 + _ew + 0.015, _ey - _ez_bh / 2, f'{eff:.1f}%',
            transform=ax4.transAxes, color=_ec, fontsize=10, fontweight='bold',
            ha='left', va='center', fontfamily='monospace')
    ax4.text(0.5, 0.08, 'Eficiencia = GHI_medida / GHI_cielo_despejado_estacional · Modelo sinusoidal -34.6°',
        transform=ax4.transAxes, color=WDIM + '88', fontsize=7.5,
        ha='center', va='bottom', fontfamily='monospace')
    ax4.text(0.5, 0.03, 'Fuente: NASA POWER ALLSKY_SFC_SW_DWN  ·  Promedio últimos 7 días',
        transform=ax4.transAxes, color=WDIM + '55', fontsize=7,
        ha='center', va='bottom', fontfamily='monospace')
else:
    sin_dato_panel(ax4, 'PANEL 2 · MAPA DE EFICIENCIA — SENTINEL-2')

# ═══════════════════════════════════════════════════════════════════════════════
# 5  NOTA
# ═══════════════════════════════════════════════════════════════════════════════
ax5 = fig.add_subplot(gs[5])
ax5.set_facecolor(BG3); ax5.axis('off'); ax5.set_xlim(0,1); ax5.set_ylim(0,1)
ax5.axhline(0.97, color=GOLD+'44', linewidth=0.8)
ax5.axhline(0.03, color=GOLD+'44', linewidth=0.8)
ax5.text(0.5, 0.50,
    'Mapa de eficiencia por panel disponible cuando la tabla velez_solar (Supabase) tenga datos de eficiencia_pct por panel_id.',
    transform=ax5.transAxes, color=WDIM+'99', fontsize=8.5,
    ha='center', va='center', fontfamily='monospace')

# ═══════════════════════════════════════════════════════════════════════════════
# 6  TABLA DE RENDIMIENTO — SIN DATO
# ═══════════════════════════════════════════════════════════════════════════════
ax6 = fig.add_subplot(gs[6])
ax6.set_facecolor(BG2); ax6.axis('off'); ax6.set_xlim(0,1); ax6.set_ylim(0,1)

ax6.add_patch(mpatches.Rectangle((0,0.93),1,0.07,
    transform=ax6.transAxes,facecolor=BG3,edgecolor='none'))
ax6.axhline(0.93, color=GOLD, linewidth=2.0)
ax6.text(0.5, 0.963, 'TABLA DE RENDIMIENTO · ACTUAL vs. MÁXIMO TEÓRICO 120 kWp',
    transform=ax6.transAxes, color=GOLD, fontsize=11, fontweight='bold',
    ha='center', va='center', fontfamily='monospace')

if panel_data:
    _cols = ['ZONA', 'EFICIENCIA', 'TEMP. PANEL', 'PRODUCCIÓN kWp', 'ESTADO']
    _col_xs = [0.05, 0.25, 0.43, 0.62, 0.82]
    _hdr_y = 0.82
    for _cx, _ch in zip(_col_xs, _cols):
        ax6.text(_cx, _hdr_y, _ch,
            transform=ax6.transAxes, color=GOLD, fontsize=7.5, fontweight='bold',
            va='top', fontfamily='monospace')
    ax6.plot([0.02, 0.98], [_hdr_y - 0.06, _hdr_y - 0.06],
        color=GOLD + '44', linewidth=0.7, transform=ax6.transAxes)
    _row_h = min(0.13, 0.68 / max(len(panel_data), 1))
    _row_y0 = _hdr_y - 0.10
    for _ri, (pid, pd) in enumerate(panel_data.items()):
        _ry = _row_y0 - _ri * _row_h
        _eff_v  = pd.get("eficiencia_pct")
        _temp_v = pd.get("temperatura_c")
        _est_v  = (pd.get("estado") or "—").upper()
        _ec = (GRNL if _est_v == "OK" else (YELL if _est_v == "DEGRADADO" else REDL))
        _bg = BG3 if _ri % 2 == 0 else BG2
        ax6.add_patch(mpatches.Rectangle((0.02, _ry - _row_h * 0.5), 0.96, _row_h * 0.92,
            transform=ax6.transAxes, facecolor=_bg, edgecolor='none'))
        _kwp_v = (INSTALLED_KWP / max(len(panel_data), 1)) * (_eff_v / 100) if isinstance(_eff_v, (int, float)) else None
        _vals = [
            pid.replace('_', ' ').upper(),
            f'{_eff_v:.1f}%'  if isinstance(_eff_v, (int, float)) else '—',
            f'{_temp_v:.1f}°C' if isinstance(_temp_v, (int, float)) else '—',
            f'{_kwp_v:.1f}'   if isinstance(_kwp_v, (int, float)) else '—',
            _est_v,
        ]
        for _ci, (_cx, _val) in enumerate(zip(_col_xs, _vals)):
            _vc = _ec if _ci == 4 else WHITE
            ax6.text(_cx, _ry - _row_h * 0.05, _val,
                transform=ax6.transAxes, color=_vc, fontsize=7.5,
                va='center', fontfamily='monospace')
    ax6.text(0.5, 0.04,
        f'Total estimado: {actual_kwp:.1f} kWp de {INSTALLED_KWP:.0f} kWp máx '
        f'({avg_eff:.0f}% eficiencia global)' if avg_eff else 'Fuente: NASA POWER + modelo NOCT bifacial',
        transform=ax6.transAxes, color=WDIM + '88', fontsize=7.5,
        ha='center', va='bottom', fontfamily='monospace')
else:
    ax6.add_patch(FancyBboxPatch((0.10, 0.10), 0.80, 0.74,
        boxstyle='round,pad=0.02', transform=ax6.transAxes,
        facecolor=BG3, edgecolor=WDIM+'33', linewidth=1.0))
    ax6.text(0.5, 0.55, 'SIN DATO',
        transform=ax6.transAxes, color=WDIM, fontsize=22, fontweight='bold',
        ha='center', va='center', fontfamily='monospace')
    ax6.text(0.5, 0.32,
        'Rendimiento por zona disponible cuando la tabla velez_solar\ntenga datos de eficiencia_pct y estado por panel_id.',
        transform=ax6.transAxes, color=WDIM+'88', fontsize=9,
        ha='center', va='center', fontfamily='monospace')

# ═══════════════════════════════════════════════════════════════════════════════
# 7  CURVA DE PRODUCCIÓN (basada en score real)
# ═══════════════════════════════════════════════════════════════════════════════
ax7 = fig.add_subplot(gs[7])
ax7.set_facecolor(BG3)
for sp in ax7.spines.values():
    sp.set_edgecolor(GOLD+'33'); sp.set_linewidth(0.6)

ax7.add_patch(mpatches.Rectangle((0,1.00),1,0.06,
    transform=ax7.transAxes,facecolor=BG3,edgecolor='none',clip_on=False))
ax7.axhline(1.002,color=GOLD,linewidth=2.0,clip_on=False)
ax7.text(0.5,1.035,'PANEL 3 · CURVA DE PRODUCCIÓN ESTIMADA — MODELO TEÓRICO',
    transform=ax7.transAxes,color=GOLD,fontsize=10.5,fontweight='bold',
    ha='center',va='bottom',fontfamily='monospace',clip_on=False)

hours = np.arange(6, 19.5, 0.5)
insol_max = INSTALLED_KWP * np.clip(np.sin(np.pi*(hours-6)/13), 0, 1)

ax7.fill_between(hours, 0, insol_max, color=GRNL, alpha=0.10)
ax7.plot(hours, insol_max, color=GRNL, linewidth=1.2, linestyle='--', alpha=0.6,
    label=f'Máximo teórico {INSTALLED_KWP:.0f} kWp')

if avg_eff is not None:
    insol_actual = insol_max * (avg_eff/100)
    ax7.fill_between(hours, 0, insol_actual, color=YELL, alpha=0.35)
    ax7.plot(hours, insol_actual, color=YELL, linewidth=2.2,
        label=f'Estimado score {avg_eff:.0f}% — {actual_kwp:.1f} kWp pico')
    ax7.text(0.5, 0.88, f'* Curva estimada basada en score global {avg_eff:.0f}% del assembler',
        transform=ax7.transAxes, color=WDIM+'88', fontsize=7.5,
        ha='center', fontfamily='monospace')

ax7.axhline(INSTALLED_KWP*0.8, color=YELL, linewidth=1.0, linestyle=':', alpha=0.7)
ax7.text(18.8, INSTALLED_KWP*0.82, '80%', color=YELL, fontsize=9, fontfamily='monospace', va='bottom')

ax7.set_xlim(6,19); ax7.set_ylim(0, INSTALLED_KWP*1.12)
ax7.set_xlabel('Hora local (ART)', color=WDIM, fontsize=9, fontfamily='monospace')
ax7.set_ylabel('Producción (kWp)', color=WDIM, fontsize=9, fontfamily='monospace')
ax7.tick_params(colors=WDIM, labelsize=9)
ax7.xaxis.set_major_formatter(ticker.FuncFormatter(lambda x,_: f'{int(x):02d}:00'))
ax7.legend(loc='upper right', framealpha=0.3, labelcolor=WHITE, fontsize=9, fancybox=True)
ax7.yaxis.label.set_color(WDIM); ax7.xaxis.label.set_color(WDIM)
ax7.tick_params(axis='x', colors=WDIM); ax7.tick_params(axis='y', colors=WDIM)

# ═══════════════════════════════════════════════════════════════════════════════
# 8  ALERTAS — SIN DATO
# ═══════════════════════════════════════════════════════════════════════════════
ax8 = fig.add_subplot(gs[8])
ax8.set_facecolor(BG2); ax8.set_xlim(0,1); ax8.set_ylim(0,1); ax8.axis('off')

ax8.add_patch(mpatches.Rectangle((0,0.88),1,0.12,
    transform=ax8.transAxes,facecolor=BG3,edgecolor='none'))
ax8.axhline(0.88,color=GOLD,linewidth=2.0)
ax8.text(0.5,0.94,'ALERTAS ACTIVAS · ACCIONES RECOMENDADAS',
    transform=ax8.transAxes,color=GOLD,fontsize=10.5,fontweight='bold',
    ha='center',va='center',fontfamily='monospace')

if panel_data:
    _alertas = [(pid, p) for pid, p in panel_data.items()
                if (p.get("estado") or "").lower() in ("falla", "degradado")]
    if not _alertas:
        ax8.add_patch(FancyBboxPatch((0.15, 0.12), 0.70, 0.68,
            boxstyle='round,pad=0.02', transform=ax8.transAxes,
            facecolor=GRNL + '18', edgecolor=GRNL + '55', linewidth=1.0))
        ax8.text(0.5, 0.52, 'SISTEMA NOMINAL',
            transform=ax8.transAxes, color=GRNL, fontsize=18, fontweight='bold',
            ha='center', va='center', fontfamily='monospace')
        ax8.text(0.5, 0.26, 'Todas las zonas dentro de rango operativo',
            transform=ax8.transAxes, color=WDIM, fontsize=9,
            ha='center', va='center', fontfamily='monospace')
    else:
        _ay0 = 0.72
        for _ali, (pid, ap) in enumerate(_alertas[:4]):
            _ac = REDL if ap.get("estado", "").lower() == "falla" else YELL
            _aly = _ay0 - _ali * 0.16
            ax8.add_patch(mpatches.Rectangle((0.04, _aly - 0.12), 0.92, 0.11,
                transform=ax8.transAxes, facecolor=_ac + '22', edgecolor=_ac + '55', linewidth=0.7))
            ax8.text(0.06, _aly - 0.065,
                f'[{ap.get("estado","").upper()}] {pid.replace("_"," ").upper()} — '
                f'Eff: {ap.get("eficiencia_pct","—")}% · T: {ap.get("temperatura_c","—")}°C',
                transform=ax8.transAxes, color=_ac, fontsize=8, fontweight='bold',
                va='center', fontfamily='monospace')
else:
    ax8.add_patch(FancyBboxPatch((0.05, 0.08), 0.90, 0.72,
        boxstyle='round,pad=0.01', transform=ax8.transAxes,
        facecolor=BG3, edgecolor=WDIM+'33', linewidth=0.8))
    ax8.text(0.5, 0.52, 'SIN DATO',
        transform=ax8.transAxes, color=WDIM, fontsize=18, fontweight='bold',
        ha='center', va='center', fontfamily='monospace')
    ax8.text(0.5, 0.28,
        'Alertas individuales disponibles cuando velez_solar tenga lecturas por panel_id.',
        transform=ax8.transAxes, color=WDIM+'88', fontsize=9,
        ha='center', va='center', fontfamily='monospace')

# ═══════════════════════════════════════════════════════════════════════════════
# 9  FOOTER
# ═══════════════════════════════════════════════════════════════════════════════
ax9 = fig.add_subplot(gs[9])
ax9.set_facecolor(BG); ax9.set_xlim(0,1); ax9.set_ylim(0,1); ax9.axis('off')
ax9.axhline(0.99,color=GOLD,linewidth=1.2)
_hoy_solar = datetime.now().date()
_dias_solar = (7 - _hoy_solar.weekday()) % 7 or 7
_lunes_solar = _hoy_solar + timedelta(days=_dias_solar)
_meses_solar = ['enero','febrero','marzo','abril','mayo','junio','julio','agosto',
                'septiembre','octubre','noviembre','diciembre']
_prox_solar = f"lunes {_lunes_solar.day} de {_meses_solar[_lunes_solar.month-1]} {_lunes_solar.year}"
ax9.text(0.5,0.88,f'Próximo escaneo automático solar: {_prox_solar}',
    transform=ax9.transAxes,color=GOLD,fontsize=9,fontweight='bold',
    ha='center',va='top',fontfamily='monospace')
ax9.text(0.5,0.66,'Sistema Fortín Inteligente · Faro Protocol · protocolfaro@gmail.com',
    transform=ax9.transAxes,color=WDIM,fontsize=8,ha='center',va='top',fontfamily='monospace')
ax9.text(0.5,0.45,f'SHA-256: {SHA}',
    transform=ax9.transAxes,color=GOLD+'99',fontsize=6.5,ha='center',va='top',fontfamily='monospace')
ax9.text(0.5,0.20,'Generado automáticamente · Landsat 8/9 TIRS · Sentinel-2 Reflectancia',
    transform=ax9.transAxes,color=WDIM,fontsize=7.5,ha='center',va='top',fontfamily='monospace')
for lx,ltxt in [(0.06,'ESA'),(0.14,'COP'),(0.22,'NASA')]:
    ax9.add_patch(FancyBboxPatch((lx-.03,.02),.07,.28,boxstyle='round,pad=0.01',
        transform=ax9.transAxes,facecolor=BG3,edgecolor=GOLD+'66',linewidth=0.7,zorder=3))
    ax9.text(lx+.005,.16,ltxt,transform=ax9.transAxes,color=GOLD,fontsize=7,
        fontweight='bold',ha='center',va='center',fontfamily='monospace',zorder=4)
for lx,ltxt in [(0.78,'ESA'),(0.86,'COP'),(0.94,'NASA')]:
    ax9.add_patch(FancyBboxPatch((lx-.03,.02),.07,.28,boxstyle='round,pad=0.01',
        transform=ax9.transAxes,facecolor=BG3,edgecolor=GOLD+'66',linewidth=0.7,zorder=3))
    ax9.text(lx+.005,.16,ltxt,transform=ax9.transAxes,color=GOLD,fontsize=7,
        fontweight='bold',ha='center',va='center',fontfamily='monospace',zorder=4)

# ─── SAVE ────────────────────────────────────────────────────────────────────
import pathlib as _pl
_REPORT_DIR = _pl.Path(__file__).parents[4] / 'reportes_velez'
_REPORT_DIR.mkdir(exist_ok=True)
out = str(_out_path or (_REPORT_DIR / 'faro_reporte_velez_solar_v2.png'))
fig.savefig(out, dpi=DPI, facecolor=BG, bbox_inches='tight', pad_inches=0.12)
plt.close(fig)
print(f'Saved: {out}')
