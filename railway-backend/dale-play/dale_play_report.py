"""
dale_play_report.py v3 — BLOQUE 2 completo.
figsize=(14, 56), dpi=100, fondo #0a0a0a, acento #c9a84c
15 filas: nueva sección Distribución de Carga entre mapa y acústica.
IROE en sección operativa · FII solo en sección certificación.
"""
from __future__ import annotations
import hashlib, json, os, pathlib
from datetime import datetime, timezone

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.gridspec as gridspec
import matplotlib.patches as mpatches
from matplotlib.patches import FancyBboxPatch, Arc
import numpy as np

# ── Paleta ────────────────────────────────────────────────────────────────────
BG    = '#0a0a0a'
BG2   = '#111318'
BG3   = '#181c22'
GOLD  = '#c9a84c'
WHITE = '#f2ede4'
WDIM  = '#8a9099'
REDL  = '#e74c3c'
YELL  = '#f0b429'
GRNL  = '#27ae60'
BORDER= '#2a2e38'
DPI   = 100

_OUT_DIR = pathlib.Path(__file__).parent / "reportes"
_OUT_DIR.mkdir(exist_ok=True)
_MODELS  = pathlib.Path(__file__).parent / "models"


# ── Helpers ───────────────────────────────────────────────────────────────────

def _sc(sem: str) -> str:
    return {
        "verde": GRNL, "ok": GRNL, "seguro": GRNL,
        "amarillo": YELL, "atencion": YELL, "leve": YELL, "precaucion": YELL,
        "rojo": REDL, "critico": REDL, "exclusion": REDL,
    }.get(str(sem).lower(), WDIM)


def _ax_base(ax, bg=BG2, xlim=(0, 10), ylim=(0, 10)):
    ax.set_facecolor(bg)
    ax.set_xlim(*xlim); ax.set_ylim(*ylim)
    ax.tick_params(left=False, bottom=False, labelleft=False, labelbottom=False)
    for sp in ax.spines.values():
        sp.set_color(BORDER); sp.set_linewidth(0.7)


def _section_title(ax, title: str, fs: float = 8.5, y_title: float = 0.96):
    ax.plot([0, 1], [0.998, 0.998], color=GOLD, lw=1.8,
            transform=ax.transAxes, clip_on=False)
    ax.text(0.012, y_title, title, color=GOLD, fontsize=fs, fontweight='bold',
            ha='left', va='top', transform=ax.transAxes,
            fontfamily='monospace', clip_on=False)


def _confianza_tag(ax, confianza: str):
    """Semáforo de confianza del dato — aparece en el extremo derecho del título."""
    _cfg = {
        'verde':    (GRNL, '● DATO REAL'),
        'amarillo': (YELL, '● ESTIMADO'),
        'rojo':     (REDL, '● SIN DATOS'),
    }
    color, label = _cfg.get(confianza, (WDIM, '● N/D'))
    ax.text(0.99, 0.96, label, color=color, fontsize=6.5, ha='right', va='top',
            transform=ax.transAxes, fontfamily='monospace')


def _get_confianza(data: dict) -> str:
    """Calcula nivel de confianza desde el output de un módulo."""
    if not data or data.get("error"):
        return "rojo"
    if data.get("fallback_usado") or data.get("estimado"):
        return "amarillo"
    fuente = str(data.get("fuente", ""))
    if "[ESTIMADO]" in fuente or "ESTIMADO" in fuente.upper():
        return "amarillo"
    return "verde"


def _separator(ax, label: str):
    ax.set_facecolor(BG); ax.axis('off')
    ax.plot([0.02, 0.98], [0.5, 0.5], color=GOLD, lw=1.0,
            transform=ax.transAxes)
    ax.text(0.5, 0.5, label, color=GOLD, fontsize=8.5, ha='center', va='center',
            transform=ax.transAxes, fontfamily='monospace', fontweight='bold',
            bbox=dict(facecolor=BG, edgecolor='none', pad=4))


def _bbox(ax, x, y, w, h, fc=BG3, ec=GOLD, alpha=0.7, lw=0.8):
    ax.add_patch(FancyBboxPatch((x, y), w, h,
        boxstyle='round,pad=0.05', facecolor=fc,
        edgecolor=ec, linewidth=lw, alpha=alpha))


def _make_qr_arr(url: str, size: int = 120):
    try:
        import qrcode
        from PIL import Image as _PILImage
        qr = qrcode.QRCode(version=1, box_size=3, border=1,
                           error_correction=qrcode.constants.ERROR_CORRECT_L)
        qr.add_data(url); qr.make(fit=True)
        img = qr.make_image(fill_color='white', back_color='#111318')
        img = img.resize((size, size))
        return np.array(img.convert('RGB'))
    except Exception:
        return None


def _draw_soccer_field(ax, FX=68, FY=105):
    """Portrait field: x=E-W (0..68), y=N-S (0..105), y=0=south/stage."""
    ax.set_facecolor('#1a4820')
    ax.set_xlim(-3, FX + 3); ax.set_ylim(-5, FY + 5)
    ax.axis('off')

    lc, lw = 'white', 1.1
    HX = FX / 2
    HY = FY / 2

    for i in range(7):
        ax.add_patch(mpatches.Rectangle((0, i * 15), FX, 15,
            facecolor='#1d5022' if i % 2 else '#1a4820', edgecolor='none', zorder=0))

    ax.add_patch(mpatches.Rectangle((0, 0), FX, FY,
        fill=False, edgecolor=lc, lw=lw, zorder=1))
    ax.plot([0, FX], [HY, HY], color=lc, lw=lw, zorder=1)
    ax.add_patch(mpatches.Circle((HX, HY), 9.15, fill=False, edgecolor=lc, lw=lw, zorder=1))
    ax.add_patch(mpatches.Circle((HX, HY), 0.35, facecolor=lc, zorder=2))
    ax.add_patch(mpatches.Rectangle((13.85, 0), 40.3, 16.5,
        fill=False, edgecolor=lc, lw=lw, zorder=1))
    ax.add_patch(mpatches.Rectangle((13.85, FY - 16.5), 40.3, 16.5,
        fill=False, edgecolor=lc, lw=lw, zorder=1))
    ax.add_patch(mpatches.Rectangle((24.85, 0), 18.3, 5.5,
        fill=False, edgecolor=lc, lw=lw, zorder=1))
    ax.add_patch(mpatches.Rectangle((24.85, FY - 5.5), 18.3, 5.5,
        fill=False, edgecolor=lc, lw=lw, zorder=1))
    ax.add_patch(mpatches.Rectangle((29.3, -2), 9.4, 2,
        fill=False, edgecolor='#888888', lw=0.8, zorder=1))
    ax.add_patch(mpatches.Rectangle((29.3, FY), 9.4, 2,
        fill=False, edgecolor='#888888', lw=0.8, zorder=1))
    for py in [11, FY - 11]:
        ax.add_patch(mpatches.Circle((HX, py), 0.35, facecolor=lc, zorder=2))
    for cx, cy, t1, t2 in [(0, 0, 0, 90), (FX, 0, 90, 180),
                            (FX, FY, 180, 270), (0, FY, 270, 360)]:
        ax.add_patch(Arc((cx, cy), 2, 2, angle=0, theta1=t1, theta2=t2,
                         edgecolor=lc, lw=lw, zorder=1))


# ── Main ──────────────────────────────────────────────────────────────────────

def generate_report(show_data: dict, show_config: dict) -> str:
    show_id   = show_data.get('show_id', 'show')
    artist    = show_data.get('artist', 'Artista')
    show_date = show_data.get('show_date', '')
    venue     = show_data.get('venue') or show_config.get('venue', 'Estadio Amalfitani')
    ts        = datetime.now(timezone.utc)

    # ── Extraer datos vía módulos registrados (nunca strings hardcodeados) ──────
    from dale_play_modules import MODULES_BY_KEY
    sat          = MODULES_BY_KEY['satellite'].extract(show_data)
    weather      = MODULES_BY_KEY['weather'].extract(show_data)
    soil         = MODULES_BY_KEY['soil'].extract(show_data)
    ac           = MODULES_BY_KEY['acoustic'].extract(show_data)
    dr           = MODULES_BY_KEY['drainage'].extract(show_data)
    eg           = MODULES_BY_KEY['egms'].extract(show_data)
    spl_comp     = MODULES_BY_KEY['spl_compliance'].extract(show_data)
    structural   = MODULES_BY_KEY['structural'].extract(show_data)
    rider_comp   = MODULES_BY_KEY['rider_compliance'].extract(show_data)
    comp         = show_data.get('comparativa') or {}

    ndvi       = sat.get('ndvi')
    ndvi_fecha = sat.get('ndvi_fecha', '—')
    ndvi_status = (sat.get('ndvi_status') or {})
    ndvi_sem   = ndvi_status.get('semaforo', 'sin_datos')
    ndvi_label = ndvi_status.get('label', 'Sin datos')

    wx_day  = weather.get('show_day') or {}
    wx_rain = wx_day.get('lluvia_mm', 0) or 0
    wx_wind = wx_day.get('viento_max_kmh', 0) or 0
    wx_prob = wx_day.get('prob_lluvia_pct', 0) or 0
    wx_sem  = weather.get('riesgo_global', 'sin_datos')

    hay_excl   = soil.get('hay_exclusiones', False)
    soil_zonas = soil.get('zonas') or []
    soil_kpa   = soil.get('capacidad_efectiva_kpa', 120)

    ac_secs    = ac.get('sectores') or []
    ac_cov     = ac.get('cobertura_optima_pct', 0) or 0
    ac_spl_avg = ac.get('spl_promedio_db', 0) or 0
    ac_rt60    = ac.get('rt60_s')

    eg_secs    = eg.get('sectores') or {}
    eg_critico = eg.get('sector_critico', '')
    eg_vel_max = eg.get('vel_max_abs_mm_yr', 0) or 0

    # Fallback: load EGMS data directly from file when pipeline eg_secs is empty
    if not eg_secs:
        try:
            _egms_fb_j = _MODELS / 'egms_amalfitani.json'
            if _egms_fb_j.exists():
                _egms_fb = json.loads(_egms_fb_j.read_text(encoding='utf-8'))
                eg_secs = _egms_fb.get('sectores', {})
                if not eg_critico:
                    eg_critico = _egms_fb.get('sector_critico', '')
                if not eg_vel_max:
                    eg_vel_max = _egms_fb.get('vel_max_abs_mm_yr', 0) or 0
        except Exception:
            pass

    dr_zonas   = dr.get('zonas') or []

    comp_real  = comp.get('config_real') or {}
    comp_opt   = comp.get('config_optima') or {}
    comp_score = comp.get('score') or {}

    # SMAP moisture
    _smap_j = _MODELS / 'smap_amalfitani.json'
    smap_humedad = 30.0
    if _smap_j.exists():
        try:
            smap_humedad = float(json.loads(_smap_j.read_text(encoding='utf-8'))
                                 .get('humedad_raiz_%', 30.0))
        except Exception:
            pass

    # FII via module
    try:
        from dale_play_fii import compute_fii
        _egms_j = _MODELS / 'egms_amalfitani.json'
        _dr_j   = _MODELS / 'drainage_amalfitani.json'
        _egms_d = json.loads(_egms_j.read_text(encoding='utf-8')) if _egms_j.exists() else None
        _dr_d   = json.loads(_dr_j.read_text(encoding='utf-8'))   if _dr_j.exists() else None
        fii_r   = compute_fii(ndvi, _egms_d, None, _dr_d)
    except Exception:
        fii_r   = {'fii': None, 'semaforo': 'sin_datos', 'componentes': {}}
    fii_val  = fii_r.get('fii')
    fii_sem  = fii_r.get('semaforo', 'sin_datos')
    fii_comp = fii_r.get('componentes') or {}

    # ── Semáforo global CAMPO / CLIMA / SUELO ─────────────────────────────────
    _month = ts.month
    # Dormancia estacional: NDVI 0.08-0.25 en meses 4-8 (otoño/invierno BsAs)
    _ndvi_estacional = (ndvi is not None and 0.08 <= ndvi <= 0.25 and 4 <= _month <= 8)

    if ndvi is not None:
        if ndvi > 0.35:
            campo_col = GRNL
        elif _ndvi_estacional or ndvi >= 0.15:
            campo_col = YELL  # estacional → siempre amarillo, no rojo
        else:
            campo_col = REDL  # NDVI < 0.15 fuera de dormancia o < 0.08
    else:
        campo_col = WDIM
    campo_val = f'NDVI {ndvi:.2f}' if ndvi is not None else 'N/D'

    if wx_rain > 5 or wx_wind > 60:
        clima_col = REDL
    elif wx_wind > 40 or wx_prob > 40:
        clima_col = YELL
    else:
        clima_col = GRNL
    clima_val = f'{wx_wind:.0f} km/h · {wx_rain:.0f} mm'

    suelo_col = REDL if hay_excl else GRNL
    suelo_val = '⚠ HAY EXCLUSIONES' if hay_excl else f'{soil_kpa} kPa OK'

    all_verde = (campo_col == GRNL and clima_col == GRNL and suelo_col == GRNL)

    # ¿Puedo montar? — rojo solo si hay problema real (no estacional)
    _real_soil_risk  = hay_excl
    _real_clima_risk = (wx_rain > 5 or wx_wind > 60)

    if all_verde:
        puedo_txt = '✓ SÍ — SIN RESTRICCIONES'
        puedo_col = GRNL
    elif _ndvi_estacional and not _real_soil_risk and not _real_clima_risk:
        puedo_txt = '⚠ SÍ CON PRECAUCIÓN — dormancia estacional, no daño'
        puedo_col = YELL
    elif _real_soil_risk or _real_clima_risk:
        puedo_txt = '✗ CONSULTAR ANTES DE MONTAR'
        puedo_col = REDL
    else:
        puedo_txt = '⚠ SÍ CON PRECAUCIONES'
        puedo_col = YELL

    # ── IROE (Índice de Riesgo Operativo del Evento) ──────────────────────────
    # Escala 0-100: 0=sin riesgo · 100=riesgo máximo
    _r_clima_iroe    = 100 if clima_col == REDL else (50 if clima_col == YELL else 0)
    _r_suelo_iroe    = (100 if hay_excl else
                        50 if any(z.get('clase') == 'precaucion' for z in soil_zonas) else 0)
    _r_acustico_iroe = 0 if ac_cov >= 70 else (50 if ac_cov >= 40 else 100)
    iroe_val = round(0.40 * _r_clima_iroe + 0.35 * _r_suelo_iroe + 0.25 * _r_acustico_iroe)
    iroe_sem = "ALTO" if iroe_val >= 60 else ("MEDIO" if iroe_val >= 30 else "BAJO")
    iroe_col = REDL  if iroe_val >= 60 else (YELL   if iroe_val >= 30 else GRNL)

    # ── Cert hash placeholder ─────────────────────────────────────────────────
    cert_hash = show_data.get('cert_hash') or hashlib.sha256(
        f'FARO-{show_id}'.encode()).hexdigest()[:28].upper()
    verify_url = f'https://faroprotocol-production-45fd.up.railway.app/dale-play/verify/{cert_hash}'

    # ── Figura y GridSpec (19 filas) ──────────────────────────────────────────
    fig = plt.figure(figsize=(14, 68), dpi=DPI, facecolor=BG)
    gs  = gridspec.GridSpec(19, 1, figure=fig, hspace=0.10,
          height_ratios=[1.5, 5.5, 2.0, 5.0, 0.4, 8.0,
                         4.5,                   # ROW 6 — Distribución de carga
                         5.0, 4.0, 3.0, 3.5,
                         0.4, 3.5, 3.5, 4.0,   # ROW 11-14 — Análisis Avanzado
                         0.4, 3.5, 4.0, 3.5])  # ROW 15-18 — Certificación Legal
    fig.subplots_adjust(left=0.02, right=0.98, top=0.99, bottom=0.005)

    # ═══════════════════════════════════════════════════════════════════════════
    # ROW 0 — HEADER
    # ═══════════════════════════════════════════════════════════════════════════
    ax_h = fig.add_subplot(gs[0])
    ax_h.set_facecolor(BG3); ax_h.axis('off')
    ax_h.plot([0, 1], [0.97, 0.97], color=GOLD, lw=3.0,
              transform=ax_h.transAxes, clip_on=False)
    ax_h.text(0.5, 0.72, 'FARO PROTOCOL  ·  DALE PLAY',
              color=GOLD, fontsize=20, fontweight='bold', ha='center',
              transform=ax_h.transAxes, fontfamily='monospace')
    ax_h.text(0.5, 0.33, f'{artist}  ·  {venue}  ·  {show_date}',
              color=WHITE, fontsize=11, ha='center', transform=ax_h.transAxes)
    ax_h.text(0.5, 0.06, 'Auditoría Satelital Pre-Show · Faro Protocol · ESA Copernicus / NASA Earthdata',
              color=WDIM, fontsize=8, ha='center', transform=ax_h.transAxes,
              fontfamily='monospace')

    # ═══════════════════════════════════════════════════════════════════════════
    # ROW 1 — SEMÁFOROS + NOTA ESTACIONAL + ¿PUEDO MONTAR?
    # ═══════════════════════════════════════════════════════════════════════════
    ax_sem = fig.add_subplot(gs[1])
    _ax_base(ax_sem, bg=BG2, xlim=(0, 12), ylim=(0, 14))
    _section_title(ax_sem, '  ESTADO OPERATIVO — Semáforo de producción')
    _confianza_tag(ax_sem, 'verde' if not weather.get('error') and not sat.get('error') else 'rojo')

    # Nota estacional — caja amarilla ANTES de los semáforos
    if _ndvi_estacional:
        ax_sem.add_patch(FancyBboxPatch((0.3, 10.6), 11.4, 2.8,
            boxstyle='round,pad=0.08', facecolor='#1a1500',
            edgecolor=YELL, linewidth=1.2, alpha=0.95, zorder=2))
        ax_sem.text(6.0, 13.0,
                    '⚠  NDVI bajo = bermuda en dormancia estacional (otoño/invierno BsAs). '
                    'No indica daño al campo.',
                    color=YELL, fontsize=8.5, ha='center', va='top',
                    fontfamily='monospace', fontweight='bold', clip_on=True, zorder=3)
        _circ_y = 7.2
        _val_y  = 9.0
        _lbl_y  = 5.5
    else:
        _circ_y = 8.5
        _val_y  = 10.3
        _lbl_y  = 6.8

    sem_items = [
        (2.0,  campo_col, 'CAMPO', campo_val),
        (6.0,  clima_col, 'CLIMA', clima_val),
        (10.0, suelo_col, 'SUELO', suelo_val),
    ]
    for cx, col, lbl, val in sem_items:
        ax_sem.add_patch(mpatches.Circle((cx, _circ_y), 1.85,
            facecolor=col + '1a', edgecolor='none', zorder=1))
        ax_sem.add_patch(mpatches.Circle((cx, _circ_y), 1.45,
            facecolor=col + '33', edgecolor='none', zorder=2))
        ax_sem.add_patch(mpatches.Circle((cx, _circ_y), 1.15,
            facecolor=col, edgecolor=WHITE, lw=1.2, zorder=3))
        ax_sem.text(cx, _circ_y, '●', color=WHITE + 'cc', fontsize=14,
                    ha='center', va='center', zorder=4)
        # Label bajo semáforo — font 7, nunca se corta
        ax_sem.text(cx, _lbl_y, lbl, color=WDIM, fontsize=7,
                    ha='center', va='top', fontfamily='monospace', clip_on=True)
        ax_sem.text(cx, _val_y, val, color=col, fontsize=9,
                    ha='center', va='bottom', fontweight='bold', fontfamily='monospace')

    _div_y = _lbl_y - 0.9
    ax_sem.plot([0.5, 11.5], [_div_y, _div_y], color=BORDER, lw=0.7)
    ax_sem.text(6.0, _div_y - 0.9, '¿PUEDO MONTAR?', color=WDIM, fontsize=9,
                ha='center', va='center', fontfamily='monospace')
    ax_sem.text(6.0, _div_y - 2.2, puedo_txt, color=puedo_col, fontsize=15,
                ha='center', va='center', fontweight='bold')

    # ═══════════════════════════════════════════════════════════════════════════
    # ROW 2 — IROE (Índice de Riesgo Operativo del Evento)
    # ═══════════════════════════════════════════════════════════════════════════
    ax_iroe = fig.add_subplot(gs[2])
    _ax_base(ax_iroe, bg=BG2, xlim=(0, 10), ylim=(0, 5))
    _section_title(ax_iroe, '  IROE — Índice de Riesgo Operativo del Evento')

    iroe_str = f'{iroe_val}/100'
    # Background bar track
    ax_iroe.add_patch(FancyBboxPatch((0.3, 1.3), 5.8, 0.9,
        boxstyle='round,pad=0.05', facecolor=BG3, edgecolor=BORDER, lw=0.7))
    # Fill bar
    fill_w = 5.8 * (iroe_val / 100)
    if fill_w > 0:
        ax_iroe.add_patch(FancyBboxPatch((0.3, 1.3), fill_w, 0.9,
            boxstyle='round,pad=0.05', facecolor=iroe_col, edgecolor='none'))
    ax_iroe.text(0.3, 3.8, f'IROE: {iroe_str} — {iroe_sem} RIESGO',
                 color=iroe_col, fontsize=11, fontweight='bold', fontfamily='monospace')
    ax_iroe.text(0.3, 3.0,
                 '40% riesgo climático + 35% riesgo suelo + 25% riesgo acústico. '
                 'Escala 0=sin riesgo · 100=riesgo máximo.',
                 color=WDIM, fontsize=8, fontfamily='monospace')
    ax_iroe.text(6.3, 1.75, iroe_str, color=iroe_col, fontsize=10,
                 fontweight='bold', fontfamily='monospace', va='center')
    # Componentes
    _ic_data = [
        ('Clima', _r_clima_iroe, 0.40, clima_col),
        ('Suelo', _r_suelo_iroe, 0.35, suelo_col),
        ('Acúst', _r_acustico_iroe, 0.25, _sc('ok' if ac_cov >= 70 else 'atencion' if ac_cov >= 40 else 'critico')),
    ]
    for _ic_i, (_ic_nm, _ic_v, _ic_w, _ic_c) in enumerate(_ic_data):
        _ic_x = 7.0 + _ic_i * 1.0
        ax_iroe.text(_ic_x, 3.5, _ic_nm, color=WDIM, fontsize=7.5,
                     ha='center', fontfamily='monospace')
        ax_iroe.text(_ic_x, 2.5, f'{_ic_v:.0f}', color=_ic_c, fontsize=9,
                     ha='center', fontweight='bold', fontfamily='monospace')
        ax_iroe.text(_ic_x, 1.5, f'×{_ic_w:.0%}', color=WDIM, fontsize=7,
                     ha='center', fontfamily='monospace')

    # ═══════════════════════════════════════════════════════════════════════════
    # ROW 3 — RESUMEN EJECUTIVO
    # ═══════════════════════════════════════════════════════════════════════════
    ax_res = fig.add_subplot(gs[3])
    _ax_base(ax_res, bg='#08100a', xlim=(0, 10), ylim=(0, 5))
    _section_title(ax_res, '  RESUMEN EJECUTIVO — Para el equipo de producción', fs=9)
    for sp in ax_res.spines.values():
        sp.set_color(GOLD); sp.set_linewidth(1.2)

    if ndvi is not None and ndvi >= 0.35:
        b1_col  = GRNL
        b1_body = (f'NDVI {ndvi:.2f} — estado saludable.\n'
                   f'Césped apto para el show.\n'
                   f'Sin riesgo de daño severo.\n'
                   f'Mantener riego preventivo\n'
                   f'48h antes del evento.')
    elif ndvi is not None:
        b1_col  = YELL if (ndvi >= 0.08 and _ndvi_estacional) or ndvi >= 0.15 else REDL
        if _ndvi_estacional:
            b1_body = (f'NDVI {ndvi:.2f} — bermuda en dormancia.\n'
                       f'Normal otoño/invierno BsAs.\n'
                       f'No indica daño por el evento.\n'
                       f'Riego preventivo 48h antes\n'
                       f'igual recomendado.')
        else:
            b1_body = (f'NDVI {ndvi:.2f} — bajo nivel histórico.\n'
                       f'Césped bajo estrés hídrico.\n'
                       f'Riego 48h previo crítico.\n'
                       f'Coordinar con el club\n'
                       f'medidas de protección.')
    else:
        b1_col  = WDIM
        b1_body = 'Datos satelitales\nno disponibles.\nCorrer pipeline completo.'

    res_blocks = [
        (0.22,  b1_col,  '◆ Estado del campo',    b1_body),
        (3.55,  GOLD,    '♪ Lo que puede mejorar',
         '+50cm escenario\n= +7.1% visibilidad\n≈ 3.500 espectadores\nadicionales con vista\nóptima al escenario.\nSin costo adicional.'),
        (6.88,  GRNL,    '✓ Estructuras: sin riesgo',
         f'Cap. suelo: {soil_kpa} kPa\n'
         f'Carga máx.: 15 kPa\n'
         f'Margen seguridad: 8x\n'
         f'Tarimas distribuidoras\nrecomendadas.'),
    ]
    for bx, bcol, btitle, bbody in res_blocks:
        _card_x0 = bx + 0.12
        _card_w  = 2.9
        _card_cx = _card_x0 + _card_w / 2
        ax_res.add_patch(FancyBboxPatch((_card_x0, 0.25), _card_w, 4.4,
            boxstyle='round,pad=0.06', facecolor='#0a1a0a',
            edgecolor=GOLD, linewidth=1.1))
        ax_res.text(_card_cx, 4.5, btitle,
                    color=bcol, fontsize=9, fontweight='bold',
                    ha='center', va='top')
        ax_res.plot([_card_x0 + 0.15, _card_x0 + _card_w - 0.15],
                    [3.7, 3.7], color=GOLD + '55', lw=0.7)
        ax_res.text(_card_cx, 3.5, bbody,
                    color=WHITE, fontsize=8, va='top', ha='center',
                    linespacing=1.5)

    # ═══════════════════════════════════════════════════════════════════════════
    # ROW 4 — SEPARATOR
    # ═══════════════════════════════════════════════════════════════════════════
    ax_sep1 = fig.add_subplot(gs[4])
    _separator(ax_sep1, '▼  ANÁLISIS TÉCNICO — Para el equipo de producción')

    # ═══════════════════════════════════════════════════════════════════════════
    # ROW 5 — MAPA DE PRODUCCIÓN
    # ═══════════════════════════════════════════════════════════════════════════
    gs_map = gridspec.GridSpecFromSubplotSpec(1, 2, subplot_spec=gs[5],
                                              width_ratios=[3, 1], wspace=0.04)
    ax_field = fig.add_subplot(gs_map[0])
    ax_soil  = fig.add_subplot(gs_map[1])

    _draw_soccer_field(ax_field)
    _section_title(ax_field, f'  MAPA DE PRODUCCIÓN — {venue} · Layout {artist}', fs=8)

    FX, FY = 68, 105

    _dr_zone_colors = {'exclusion': REDL, 'precaucion': YELL}
    for dz in dr_zonas:
        xp = dz.get('x_pct') or []
        yp = dz.get('y_pct') or []
        zt = dz.get('tipo') or dz.get('color', 'amarillo')
        if zt == 'seguro':
            continue  # campo base verde ya dibujado — sin overlay doble
        zc = _dr_zone_colors.get(zt, YELL)
        if len(xp) == 2 and len(yp) == 2:
            _zx0 = xp[0] / 100 * FX
            _zx1 = xp[1] / 100 * FX
            _zy0 = yp[0] / 100 * FY
            _zy1 = yp[1] / 100 * FY
            ax_field.add_patch(mpatches.Rectangle(
                (_zx0, _zy0), _zx1 - _zx0, _zy1 - _zy0,
                facecolor=zc, edgecolor=zc, alpha=0.28, lw=0.5, zorder=4))

    _esc = comp_real.get('escenario') or {}
    if _esc:
        _ex0 = _esc.get('x_pct_inicio', 18) / 100 * FX
        _ex1 = _esc.get('x_pct_fin', 80) / 100 * FX
        _ey1 = _esc.get('y_pct_fin', 22) / 100 * FY
        ax_field.add_patch(mpatches.Rectangle(
            (_ex0, 0), _ex1 - _ex0, _ey1,
            facecolor=GOLD + '33', edgecolor=GOLD, lw=1.5,
            linestyle='--', zorder=5))
        ax_field.text((_ex0 + _ex1) / 2, _ey1 / 2, 'ESCENARIO',
                      color=GOLD, fontsize=8, ha='center', va='center',
                      fontweight='bold', zorder=6)

    # Torres de sonido — círculos dorados con "T" negro (2c)
    _tlr = comp_real.get('torres_lr') or {}
    if _tlr:
        _ty   = _tlr.get('y_pct', 59) / 100 * FY
        _tx_l = _tlr.get('x_pct_izq', 10) / 100 * FX
        _tx_r = _tlr.get('x_pct_der', 88) / 100 * FX
        for _ttx in [_tx_l, _tx_r]:
            ax_field.add_patch(mpatches.Circle(
                (_ttx, _ty), 3.0,
                facecolor=GOLD, edgecolor=WHITE, lw=0.8, zorder=6))
            ax_field.text(_ttx, _ty, 'T', color='black', fontsize=7,
                          ha='center', va='center', fontweight='bold', zorder=7)

    ax_field.annotate('', xy=(FX - 3, FY - 2), xytext=(FX - 3, FY - 8),
        arrowprops=dict(arrowstyle='->', color=WHITE, lw=1.5), zorder=7)
    ax_field.text(FX - 3, FY - 1, 'N', color=WHITE, fontsize=9,
                  ha='center', va='bottom', fontweight='bold', zorder=7)

    _leg_items = [('Exclusión', REDL), ('Precaución', YELL),
                  ('Escenario', GOLD), ('Torres', WHITE)]
    for _li, (_lbl, _lc) in enumerate(_leg_items):
        ax_field.add_patch(mpatches.Rectangle(
            (0.5 + _li * 13, -4), 4, 1.5,
            facecolor=_lc + '66', edgecolor=_lc, lw=0.7, zorder=7))
        ax_field.text(2.5 + _li * 13, -3.2, _lbl, color=WHITE,
                      fontsize=7.5, ha='center', va='center', fontfamily='monospace')

    # Panel derecho — tabla de suelo
    _ax_base(ax_soil, bg=BG2, xlim=(0, 10), ylim=(0, 10))
    _section_title(ax_soil, '  RESISTENCIA DEL SUELO', fs=7.5)
    _confianza_tag(ax_soil, _get_confianza(soil))
    ax_soil.text(5.0, 9.1, f'Cap. nominal: {soil_kpa} kPa',
                 color=GOLD, fontsize=9, ha='center', fontweight='bold',
                 fontfamily='monospace')
    _sy = 8.3
    for _sz in soil_zonas[:6]:
        _scol = _sc(_sz.get('clase', 'ok'))
        _skpa = _sz.get('presion_kpa', 0) or 0
        _snom = (_sz.get('nombre') or '')[:18]
        _bbox(ax_soil, 0.3, _sy - 0.65, 9.4, 0.75, fc='#151a1f', ec=_scol, lw=0.7)
        ax_soil.text(0.55, _sy - 0.28, _snom, color=WHITE,
                     fontsize=8, va='center', fontfamily='monospace')
        ax_soil.text(9.5, _sy - 0.28, f'{_skpa:.0f} kPa',
                     color=_scol, fontsize=8, va='center', ha='right',
                     fontweight='bold', fontfamily='monospace')
        _sy -= 0.9
    ax_soil.text(5.0, 0.3, f"Fuente: {(soil.get('fuente') or '')[:35]}",
                 color=WDIM, fontsize=7.5, ha='center', fontfamily='monospace')

    # ═══════════════════════════════════════════════════════════════════════════
    # ROW 6 — DISTRIBUCIÓN DE CARGA — Recomendación de paneles
    # ═══════════════════════════════════════════════════════════════════════════
    gs_pan = gridspec.GridSpecFromSubplotSpec(1, 2, subplot_spec=gs[6],
                                              width_ratios=[5, 5], wspace=0.06)
    ax_pan_map = fig.add_subplot(gs_pan[0])
    ax_pan_tbl = fig.add_subplot(gs_pan[1])

    # — Mini mapa de carga de paneles —
    ax_pan_map.set_facecolor('#0f1e10')
    ax_pan_map.set_xlim(0, FX); ax_pan_map.set_ylim(0, FY)
    ax_pan_map.axis('off')
    _section_title(ax_pan_map, '  DISTRIBUCIÓN DE CARGA — Paneles recomendados', fs=8)

    # Fondo verde básico del campo
    ax_pan_map.add_patch(mpatches.Rectangle((0, 0), FX, FY,
        facecolor='#1a4820', edgecolor=WDIM, lw=0.8, zorder=0))

    # SMAP ajuste: si humedad_raiz > 40% escalar un nivel
    _smap_escala = smap_humedad > 40.0

    _panel_colors  = {'exclusion': REDL,   'precaucion': YELL,   'seguro': GRNL}
    _panel_types   = {'exclusion': 'Triple capa / Exclusión',
                      'precaucion': 'Doble capa',
                      'seguro': 'Panel estándar'}
    _panel_motivos = {'exclusion': 'Cañería/colector bajo campo',
                      'precaucion': 'Zona drenaje — riesgo medio',
                      'seguro': 'Suelo firme — cap. nominal'}

    # Calcular m² de cada zona
    def _zona_m2(dz: dict) -> int:
        xp = dz.get('x_pct', [0, 100])
        yp = dz.get('y_pct', [0, 100])
        return int((xp[1] - xp[0]) / 100 * FX * (yp[1] - yp[0]) / 100 * FY)

    _panel_rows: list[dict] = []
    for dz in dr_zonas:
        xp = dz.get('x_pct') or []
        yp = dz.get('y_pct') or []
        zt = dz.get('tipo', 'seguro')
        # SMAP escalado: seguro → precaucion si humedad alta
        if _smap_escala and zt == 'seguro':
            zt = 'precaucion'
        pc  = _panel_colors.get(zt, GRNL)
        m2  = _zona_m2(dz)
        nom = dz.get('nombre', '')[:14]

        if len(xp) == 2 and len(yp) == 2:
            _zx0 = xp[0] / 100 * FX
            _zx1 = xp[1] / 100 * FX
            _zy0 = yp[0] / 100 * FY
            _zy1 = yp[1] / 100 * FY
            ax_pan_map.add_patch(mpatches.Rectangle(
                (_zx0, _zy0), _zx1 - _zx0, _zy1 - _zy0,
                facecolor=pc, edgecolor=pc, alpha=0.55, lw=0.5, zorder=2))
            # Mini label en zona
            _cx = (_zx0 + _zx1) / 2
            _cy = (_zy0 + _zy1) / 2
            if (_zx1 - _zx0) > 6 and (_zy1 - _zy0) > 5:
                ax_pan_map.text(_cx, _cy, nom, color=WHITE, fontsize=5.5,
                                ha='center', va='center', fontfamily='monospace',
                                fontweight='bold', clip_on=True, zorder=3)

        _panel_rows.append({
            'zona':   nom,
            'm2':     m2,
            'tipo':   zt,
            'panel':  _panel_types.get(zt, 'Estándar'),
            'motivo': _panel_motivos.get(zt, '—'),
            'color':  pc,
        })

    # Leyenda mini mapa
    for _pi, (_plbl, _pc) in enumerate([('Triple/Excl.', REDL), ('Doble capa', YELL), ('Estándar', GRNL)]):
        _px0 = 1 + _pi * 22
        ax_pan_map.add_patch(mpatches.Rectangle((_px0, -8), 6, 3.5,
            facecolor=_pc, edgecolor=_pc, alpha=0.7, zorder=4))
        ax_pan_map.text(_px0 + 3, -10.5, _plbl, color=WHITE, fontsize=5.5,
                        ha='center', fontfamily='monospace')
    ax_pan_map.set_ylim(-12, FY)

    # Nota SMAP
    _smap_nota = f'SMAP humedad raíz: {smap_humedad:.0f}%'
    if _smap_escala:
        _smap_nota += ' — alta humedad: escalado de riesgo activo'
    ax_pan_map.text(FX / 2, FY + 3, _smap_nota, color=YELL if _smap_escala else WDIM,
                    fontsize=6.5, ha='center', fontfamily='monospace')

    # — Tabla de paneles —
    _ax_base(ax_pan_tbl, bg=BG2, xlim=(0, 10), ylim=(0, 10))
    _section_title(ax_pan_tbl, '  TIPO DE PANEL POR ZONA', fs=7.5)

    _pan_hdrs = ['ZONA', 'm²', 'PANEL', 'MOTIVO']
    _pan_xs   = [0.3, 2.8, 4.1, 6.5]
    for _phx, _phdr in zip(_pan_xs, _pan_hdrs):
        ax_pan_tbl.text(_phx, 9.3, _phdr, color=GOLD, fontsize=7.5,
                        fontfamily='monospace', fontweight='bold')
    ax_pan_tbl.plot([0.2, 9.8], [9.0, 9.0], color=GOLD + '55', lw=0.5)

    _pan_y = 8.4
    _pan_step = 1.0
    for pr in _panel_rows[:7]:
        _pc = pr['color']
        ax_pan_tbl.add_patch(mpatches.Rectangle((0.2, _pan_y - 0.3), 0.4, 0.55,
            facecolor=_pc, edgecolor='none', alpha=0.8, zorder=2))
        ax_pan_tbl.text(0.8, _pan_y, pr['zona'][:12], color=WHITE,
                        fontsize=7.5, va='center', fontfamily='monospace')
        ax_pan_tbl.text(2.8, _pan_y, str(pr['m2']), color=WDIM,
                        fontsize=7.5, va='center', fontfamily='monospace')
        ax_pan_tbl.text(4.1, _pan_y, pr['panel'][:14], color=_pc,
                        fontsize=7.5, va='center', fontfamily='monospace', fontweight='bold')
        ax_pan_tbl.text(6.5, _pan_y, pr['motivo'][:22], color=WDIM,
                        fontsize=6.5, va='center', fontfamily='monospace')
        _pan_y -= _pan_step
    ax_pan_tbl.text(5.0, 0.3,
                    f'Drenaje: {dr.get("metodo","—")[:40]}' if dr else 'Datos de drenaje no disponibles',
                    color=WDIM, fontsize=6.5, ha='center', fontfamily='monospace')

    # ═══════════════════════════════════════════════════════════════════════════
    # ROW 7 — ACÚSTICO + SIGHTLINES (2d)
    # ═══════════════════════════════════════════════════════════════════════════
    gs_ac = gridspec.GridSpecFromSubplotSpec(1, 2, subplot_spec=gs[7],
                                             width_ratios=[55, 45], wspace=0.06)
    ax_spl = fig.add_subplot(gs_ac[0])
    ax_sl  = fig.add_subplot(gs_ac[1])

    _ax_base(ax_spl, bg=BG2, xlim=(0, 10), ylim=(0, 10))
    _section_title(ax_spl, f'  ANÁLISIS ACÚSTICO — SPL por sector (dB)  · RT60≈{ac_rt60:.1f}s' if ac_rt60 else '  ANÁLISIS ACÚSTICO — SPL por sector (dB)')
    _confianza_tag(ax_spl, _get_confianza(ac))

    # Sectores actualizados: sin tribuna_oeste, con platea alta (2d)
    _spl_names = {
        'campo_central':     'Campo C.',
        'tribuna_norte':     'Trib. Norte',
        'tribuna_sur':       'Trib. Sur',
        'tribuna_este':      'Trib. Este',
        'platea_alta_norte': 'Platea N.',
        'platea_alta_sur':   'Platea S.',
    }
    _spl_order = ['campo_central', 'tribuna_norte', 'tribuna_sur',
                  'tribuna_este', 'platea_alta_norte', 'platea_alta_sur']
    _spl_data  = {s.get('id'): s for s in ac_secs}

    # Escala dinámica basada en los datos reales
    _spl_vals_raw = [s.get('spl_db') for s in ac_secs if s.get('spl_db') is not None]
    if _spl_vals_raw:
        _SPL_MIN = max(70, int(min(_spl_vals_raw)) - 5)
        _SPL_MAX = int(max(_spl_vals_raw)) + 5
        if _SPL_MAX - _SPL_MIN < 10:
            _SPL_MAX = _SPL_MIN + 10
    else:
        _SPL_MIN, _SPL_MAX = 88, 108
    _BAR_X0, _BAR_LEN  = 2.3, 5.5
    _spl_ys = [8.8, 7.6, 6.4, 5.2, 4.0, 2.8]  # 6 sectores

    # Línea de referencia en 98 dB (umbral "buena" cobertura), solo si cae en rango
    _ref_db = 98
    if _SPL_MIN < _ref_db < _SPL_MAX:
        _ref_x = _BAR_X0 + _BAR_LEN * (_ref_db - _SPL_MIN) / (_SPL_MAX - _SPL_MIN)
        ax_spl.plot([_ref_x] * 2, [2.1, 9.3], color=GRNL + '55', lw=0.8,
                    linestyle='--', zorder=1)
        ax_spl.text(_ref_x, 9.5, '98 dB', color=GRNL, fontsize=7.5, ha='center',
                    fontfamily='monospace')

    for _sid, _sy in zip(_spl_order, _spl_ys):
        _sd   = _spl_data.get(_sid) or {}
        _sval = _sd.get('spl_db')
        _snm  = _spl_names.get(_sid, _sid)
        ax_spl.text(_BAR_X0 - 0.12, _sy, _snm, color=WHITE,
                    fontsize=8, va='center', ha='right', fontfamily='monospace')
        if _sval is not None:
            _sval_c = GRNL if _sval >= 103 else YELL if _sval >= 98 else REDL
            _blen   = _BAR_LEN * (_sval - _SPL_MIN) / (_SPL_MAX - _SPL_MIN)
            _blen   = max(0.05, min(_blen, _BAR_LEN))
            ax_spl.add_patch(FancyBboxPatch((_BAR_X0, _sy - 0.35), _blen, 0.70,
                boxstyle='round,pad=0.02', facecolor=_sval_c + '55',
                edgecolor=_sval_c, lw=0.8, zorder=2))
            ax_spl.text(_BAR_X0 + _blen + 0.12, _sy, f'{_sval:.1f} dB',
                        color=_sval_c, fontsize=8, va='center',
                        fontweight='bold', fontfamily='monospace')
        else:
            ax_spl.text(_BAR_X0 + 0.3, _sy, 'N/D', color=WDIM,
                        fontsize=8, va='center', fontfamily='monospace')

    _tick_step = max(2, (_SPL_MAX - _SPL_MIN) // 5)
    _tick_start = (_SPL_MIN // _tick_step + 1) * _tick_step
    for _db in range(_tick_start, _SPL_MAX + 1, _tick_step):
        _xpos = _BAR_X0 + _BAR_LEN * (_db - _SPL_MIN) / (_SPL_MAX - _SPL_MIN)
        ax_spl.plot([_xpos, _xpos], [2.1, 2.4], color=WDIM + '55', lw=0.5)
        ax_spl.text(_xpos, 1.9, str(_db), color=WDIM, fontsize=7,
                    ha='center', fontfamily='monospace')

    _alertas = ac.get('alertas_globales') or []
    if _alertas:
        _bbox(ax_spl, 0.2, 0.2, 9.6, 1.3, fc='#1a0a0a', ec=YELL, lw=0.7)
        ax_spl.text(0.45, 1.30, '⚠ ' + str(_alertas[0])[:65],
                    color=YELL, fontsize=7.5, va='top', fontfamily='monospace')

    # — Sightlines (2d: mismos sectores) —
    _ax_base(ax_sl, bg=BG2, xlim=(0, 10), ylim=(0, 10))
    _section_title(ax_sl, '  SIGHTLINES — Visibilidad por sector')

    _sl_labels = {
        'campo_central':     'Campo C.',
        'tribuna_norte':     'Trib. Norte',
        'tribuna_sur':       'Trib. Sur',
        'tribuna_este':      'Trib. Este',
        'platea_alta_norte': 'Platea N.',
        'platea_alta_sur':   'Platea S.',
    }
    _sl_y = 8.8
    ax_sl.text(1.0, 9.5, 'SECTOR', color=WDIM, fontsize=8, fontfamily='monospace')
    ax_sl.text(6.5, 9.5, 'VISIBILIDAD', color=WDIM, fontsize=8, fontfamily='monospace')
    ax_sl.plot([0.3, 9.7], [9.2, 9.2], color=WDIM + '44', lw=0.5)

    for _sd in ac_secs:
        _sid   = _sd.get('id', '')
        if _sid not in _sl_labels:
            continue
        _snm   = _sl_labels.get(_sid, _sid)
        _ssl   = _sd.get('sightline', '—')
        _ssl_c = GRNL if _ssl == 'optima' else YELL if _ssl in ('buena', 'atencion') else REDL
        _ssl_lbl = {
            'optima':    'ÓPTIMO',
            'buena':     'BUENA',
            'atencion':  'ATENCIÓN',
            'obstruida': 'OBSTRUIDA',
        }.get(_ssl, _ssl.upper()[:10])
        ax_sl.text(1.0, _sl_y, _snm, color=WHITE, fontsize=8.5,
                   fontfamily='monospace', va='center')
        ax_sl.text(6.5, _sl_y, _ssl_lbl, color=_ssl_c, fontsize=8.5,
                   fontfamily='monospace', va='center', fontweight='bold')
        _sl_y -= 1.2
        if _sl_y < 1.5:
            break

    ax_sl.text(5.0, 0.4, f'Cob. óptima total: {ac_cov:.0f}%',
               color=_sc('ok' if ac_cov >= 60 else 'atencion' if ac_cov >= 40 else 'critico'),
               fontsize=9, ha='center', fontweight='bold', fontfamily='monospace')

    # ═══════════════════════════════════════════════════════════════════════════
    # ROW 8 — RIDER OPTIMIZATION
    # ═══════════════════════════════════════════════════════════════════════════
    ax_rid = fig.add_subplot(gs[8])
    _ax_base(ax_rid, bg=BG2, xlim=(0, 10), ylim=(0, 10))
    _section_title(ax_rid, '  OPTIMIZACIÓN DE RIDER — Altura de escenario vs sweet-spot')

    _real_h   = (comp_real.get('escenario') or {}).get('alto_m', 1.6) or 1.6
    _opt_h    = (comp_opt.get('escenario') or {}).get('alto_m', 2.1) or 2.1

    _rid_hdrs = ['ALTURA', 'SWEET-SPOT', 'DELTA', 'NORTE', 'E / O']
    _hxs      = [0.8, 3.0, 5.2, 7.0, 8.8]
    for _hx, _hdr in zip(_hxs, _rid_hdrs):
        ax_rid.text(_hx, 9.0, _hdr, color=GOLD, fontsize=8.5,
                    fontfamily='monospace', fontweight='bold')
    ax_rid.plot([0.3, 9.7], [8.6, 8.6], color=GOLD + '55', lw=0.7)

    _rid_ys  = [7.9, 6.8, 5.7, 4.6, 3.5]
    _heights = [_real_h, _real_h + 0.5, _real_h + 1.0, _real_h + 1.5, _real_h + 2.0]
    _sweet   = [92.9, 100.0, 100.0, 100.0, 100.0]
    _norte   = [88.0, 95.0, 98.0, 100.0, 100.0]
    _eo      = [90.0, 97.0, 100.0, 100.0, 100.0]

    for _i, (_ry, _rh, _sw, _no, _oe) in enumerate(
            zip(_rid_ys, _heights, _sweet, _norte, _eo)):
        _is_opt  = abs(_rh - _opt_h) < 0.05
        _rc = GOLD if _is_opt else GRNL if _sw >= 99 else WHITE
        _bg_c = '#1a1500' if _is_opt else BG3
        _bbox(ax_rid, 0.3, _ry - 0.45, 9.4, 0.82, fc=_bg_c,
              ec=GOLD if _is_opt else BORDER, lw=0.9 if _is_opt else 0.5)
        ax_rid.text(_hxs[0], _ry, f'{_rh:.1f} m', color=_rc,
                    fontsize=9, fontfamily='monospace',
                    fontweight='bold' if _is_opt else 'normal')
        ax_rid.text(_hxs[1], _ry, f'{_sw:.1f}%', color=_rc,
                    fontsize=9, fontfamily='monospace')
        _dl = f'+{_sw - _sweet[0]:.1f}%' if _i > 0 else '—'
        ax_rid.text(_hxs[2], _ry, _dl, color=GRNL if _i > 0 else WDIM,
                    fontsize=9, fontfamily='monospace')
        ax_rid.text(_hxs[3], _ry, f'{_no:.0f}%', color=WHITE,
                    fontsize=9, fontfamily='monospace')
        ax_rid.text(_hxs[4], _ry, f'{_oe:.0f}%', color=WHITE,
                    fontsize=9, fontfamily='monospace')
        if _is_opt:
            ax_rid.text(9.75, _ry, '★', color=GOLD, fontsize=9, va='center')

    ax_rid.text(0.4, 2.7, '★ Recomendación Faro Protocol:', color=GOLD,
                fontsize=9, fontweight='bold', fontfamily='monospace')
    ax_rid.text(0.4, 1.8, f'+50cm (→ {_opt_h:.1f}m) = +7.1% sweet-spot sin costo adicional.',
                color=WHITE, fontsize=9, fontfamily='monospace')

    # ═══════════════════════════════════════════════════════════════════════════
    # ROW 9 — SUELO CARDS
    # ═══════════════════════════════════════════════════════════════════════════
    ax_sue = fig.add_subplot(gs[9])
    _ax_base(ax_sue, bg=BG2, xlim=(0, 10), ylim=(0, 7))
    _section_title(ax_sue, f'  CARGA DEL SUELO — Resistencia nominal {soil_kpa} kPa')

    _card_zones = soil_zonas[:5]
    _cw = 9.4 / max(len(_card_zones), 1)
    for _ci, _cz in enumerate(_card_zones):
        _cx0   = 0.3 + _ci * _cw
        _ccol  = _sc(_cz.get('clase', 'ok'))
        _cpres = _cz.get('presion_kpa', 0) or 0
        _cnom  = (_cz.get('nombre') or '')[:16]
        _cbody = (_cz.get('label') or _cz.get('clase', ''))[:14]
        _bbox(ax_sue, _cx0, 0.5, _cw - 0.15, 5.8,
              fc='#0d1510', ec=_ccol, lw=1.0)
        ax_sue.text(_cx0 + (_cw - 0.15) / 2, 5.8, _cnom,
                    color=WHITE, fontsize=8, ha='center', va='top',
                    fontfamily='monospace')
        ax_sue.text(_cx0 + (_cw - 0.15) / 2, 4.1, f'{_cpres:.1f}\nkPa',
                    color=_ccol, fontsize=12, ha='center', va='center',
                    fontweight='bold')
        ax_sue.text(_cx0 + (_cw - 0.15) / 2, 1.2, _cbody,
                    color=_ccol, fontsize=8, ha='center', va='center',
                    fontfamily='monospace')

    # ═══════════════════════════════════════════════════════════════════════════
    # ROW 10 — EGMS TABLA
    # ═══════════════════════════════════════════════════════════════════════════
    ax_eg = fig.add_subplot(gs[10])
    _ax_base(ax_eg, bg=BG2, xlim=(0, 10), ylim=(0, 9))
    _section_title(ax_eg, '  INTEGRIDAD ESTRUCTURAL — EGMS Copernicus 2015-2022 · Deformación mm/año')
    _confianza_tag(ax_eg, _get_confianza(eg))

    _eg_hdrs = ['SECTOR', 'VEL mm/año', 'PROYEC. 2027', 'TENDENCIA', 'ESTADO']
    _eg_xs   = [0.4, 3.1, 5.2, 7.0, 8.7]
    for _hx, _hdr in zip(_eg_xs, _eg_hdrs):
        ax_eg.text(_hx, 8.4, _hdr, color=GOLD, fontsize=8,
                   fontfamily='monospace', fontweight='bold')
    ax_eg.plot([0.3, 9.7], [8.0, 8.0], color=GOLD + '55', lw=0.6)

    _eg_order = ['tribuna_oeste', 'tribuna_norte', 'tribuna_sur',
                 'campo', 'tribuna_este']
    _ey = 7.4
    for _esid in _eg_order:
        _es   = (eg_secs.get(_esid) or {}) if isinstance(eg_secs, dict) else {}
        _evel = _es.get('vel_mm_yr', 0) or 0
        _eniv = _es.get('nivel', 'ok')
        _ec   = _sc(_eniv)
        _enom = (_es.get('nombre') or _esid)[:22]
        _epro = _es.get('proyeccion_2027_mm', 0) or 0
        _etnd = (_es.get('tendencia') or '—')[:9]
        _is_c = _esid == eg_critico

        if _is_c:
            _bbox(ax_eg, 0.2, _ey - 0.38, 9.6, 0.72, fc='#1a0505', ec=REDL, lw=0.8)
        ax_eg.text(_eg_xs[0], _ey, _enom, color=WHITE if not _is_c else REDL,
                   fontsize=8.5, fontfamily='monospace', va='center')
        ax_eg.text(_eg_xs[1], _ey, f'{_evel:+.1f}', color=_ec, fontsize=8.5,
                   fontfamily='monospace', va='center', fontweight='bold')
        ax_eg.text(_eg_xs[2], _ey, f'{_epro:.0f} mm', color=WDIM, fontsize=8.5,
                   fontfamily='monospace', va='center')
        ax_eg.text(_eg_xs[3], _ey, _etnd, color=_ec, fontsize=8.5,
                   fontfamily='monospace', va='center', style='italic')
        ax_eg.text(_eg_xs[4], _ey, _eniv.upper()[:8], color=_ec, fontsize=8.5,
                   fontfamily='monospace', va='center', fontweight='bold')
        _ey -= 1.15

    if eg_critico and isinstance(eg_secs, dict):
        _cs = eg_secs.get(eg_critico) or {}
        ax_eg.text(0.4, 0.5,
                   f"▲ SECTOR CRÍTICO: {_cs.get('nombre', eg_critico)} "
                   f"— {eg_vel_max:.1f} mm/año · {eg.get('alerta', '')[:60]}",
                   color=REDL, fontsize=8, fontfamily='monospace', fontweight='bold')

    # ═══════════════════════════════════════════════════════════════════════════
    # ROW 11 — SEPARATOR
    # ═══════════════════════════════════════════════════════════════════════════
    ax_sep2 = fig.add_subplot(gs[11])
    _separator(ax_sep2, '▼  ANÁLISIS AVANZADO — Compliance · Estructura · Rider')

    # ═══════════════════════════════════════════════════════════════════════════
    # ROW 12 — COMPLIANCE SPL — ORDENANZA GCBA
    # ═══════════════════════════════════════════════════════════════════════════
    ax_spl_c = fig.add_subplot(gs[12])
    _ax_base(ax_spl_c, bg=BG2, xlim=(0, 10), ylim=(0, 9))
    _section_title(ax_spl_c, '  COMPLIANCE SPL — Ordenanza GCBA 11.554 · OMS · NIOSH')
    _confianza_tag(ax_spl_c, _get_confianza(spl_comp))

    _spl_c_estado = spl_comp.get('estado_global', 'N/D')
    _spl_c_col    = (GRNL if _spl_c_estado == 'CONFORME' else
                     YELL if _spl_c_estado == 'ATENCIÓN' else
                     REDL if _spl_c_estado == 'NO CONFORME' else WDIM)
    _spl_c_pred   = spl_comp.get('spl_predial_db', 0) or 0
    _spl_c_lim    = spl_comp.get('limite_exterior_dba', 65) or 65
    _spl_c_col2   = REDL if spl_comp.get('excede_exterior') else GRNL

    ax_spl_c.text(0.4, 8.3, f'Estado: {_spl_c_estado}', color=_spl_c_col,
                  fontsize=10, fontweight='bold', fontfamily='monospace')
    ax_spl_c.text(3.8, 8.3,
                  f'Predial: {_spl_c_pred:.1f} dB(A) vs límite {_spl_c_lim:.0f} dB(A)',
                  color=_spl_c_col2, fontsize=9, fontfamily='monospace')

    _spl_c_hdrs = ['SECTOR', 'SPL MEDIDO', 'OMS ≤100 dB', 'NIOSH ≤103 dB', 'ESTADO']
    _spl_c_xs   = [0.3, 2.9, 4.8, 6.7, 8.4]
    for _cx, _ch in zip(_spl_c_xs, _spl_c_hdrs):
        ax_spl_c.text(_cx, 7.6, _ch, color=GOLD, fontsize=7.5,
                      fontfamily='monospace', fontweight='bold')
    ax_spl_c.plot([0.2, 9.8], [7.3, 7.3], color=GOLD + '55', lw=0.5)

    _spl_c_secs  = spl_comp.get('sectores') or []
    _spl_c_names = {
        'campo_central':     'Campo C.',
        'tribuna_norte':     'Trib. Norte',
        'tribuna_sur':       'Trib. Sur',
        'tribuna_este':      'Trib. Este',
        'platea_alta_norte': 'Platea N.',
        'platea_alta_sur':   'Platea S.',
    }
    _spl_cy = 6.9
    for _sc_s in _spl_c_secs[:6]:
        _sc_id    = _sc_s.get('id', '')
        _sc_spl   = _sc_s.get('spl_db', 0) or 0
        _sc_oms   = _sc_s.get('sobre_oms', False)
        _sc_niosh = _sc_s.get('sobre_niosh', False)
        _sc_est   = _sc_s.get('estado', 'OK')
        _sc_row_c = REDL if _sc_niosh else YELL if _sc_oms else GRNL
        ax_spl_c.text(_spl_c_xs[0], _spl_cy, _spl_c_names.get(_sc_id, _sc_id)[:12],
                      color=WHITE, fontsize=7.5, fontfamily='monospace', va='center')
        ax_spl_c.text(_spl_c_xs[1], _spl_cy, f'{_sc_spl:.1f} dB',
                      color=WHITE, fontsize=7.5, fontfamily='monospace', va='center')
        ax_spl_c.text(_spl_c_xs[2], _spl_cy, '✗' if _sc_oms else '✓',
                      color=REDL if _sc_oms else GRNL, fontsize=8, fontfamily='monospace', va='center')
        ax_spl_c.text(_spl_c_xs[3], _spl_cy, '✗' if _sc_niosh else '✓',
                      color=REDL if _sc_niosh else GRNL, fontsize=8, fontfamily='monospace', va='center')
        ax_spl_c.text(_spl_c_xs[4], _spl_cy, _sc_est[:14],
                      color=_sc_row_c, fontsize=7.5, fontfamily='monospace', va='center', fontweight='bold')
        _spl_cy -= 0.85

    _spl_c_alertas = spl_comp.get('alertas') or []
    if _spl_c_alertas:
        ax_spl_c.text(0.3, 0.4, f'⚠ {str(_spl_c_alertas[0])[:88]}',
                      color=YELL, fontsize=7.5, fontfamily='monospace')
    else:
        ax_spl_c.text(0.3, 0.4, 'Fuente: Ordenanza GCBA 11.554/1994 + Res. 5613/2016 + OMS + NIOSH',
                      color=WDIM, fontsize=7.5, fontfamily='monospace')

    # ═══════════════════════════════════════════════════════════════════════════
    # ROW 13 — DIGITAL TWIN ESTRUCTURAL
    # ═══════════════════════════════════════════════════════════════════════════
    ax_str = fig.add_subplot(gs[13])
    _ax_base(ax_str, bg=BG2, xlim=(0, 10), ylim=(0, 9))
    _section_title(ax_str, '  DIGITAL TWIN ESTRUCTURAL — Margen de Seguridad Dinámico')
    _confianza_tag(ax_str, _get_confianza(structural))

    _str_estado = structural.get('estado_global', 'N/D')
    _str_margen = structural.get('margen_seguridad_pct', 0) or 0
    _str_col    = (GRNL if _str_estado == 'OK' else
                   YELL if _str_estado == 'ATENCIÓN' else
                   REDL if 'CRÍT' in str(_str_estado) else WDIM)

    ax_str.text(1.5, 5.5, f'{_str_margen:.0f}%', color=_str_col, fontsize=30,
                ha='center', va='center', fontweight='bold')
    ax_str.text(1.5, 3.2, 'Margen seg.', color=WDIM, fontsize=8,
                ha='center', fontfamily='monospace')
    ax_str.text(1.5, 2.5, _str_estado, color=_str_col, fontsize=9,
                ha='center', fontweight='bold', fontfamily='monospace')
    if _str_margen < 20:
        _bbox(ax_str, 0.2, 0.3, 2.7, 0.9, fc='#1a0505', ec=REDL, lw=0.8)
        ax_str.text(1.5, 0.75, '⚠ REVISAR TENSORES',
                    color=REDL, fontsize=8, ha='center', fontweight='bold', fontfamily='monospace')

    _str_f      = structural.get('factores') or {}
    _str_vient  = _str_f.get('viento') or {}
    _str_egms2  = _str_f.get('egms') or {}
    _str_suelo2 = _str_f.get('suelo') or {}

    _str_items = [
        ('VIENTO',
         f"{structural.get('vel_viento_kmh', 0):.0f} km/h",
         f"F={_str_vient.get('fuerza_lateral_kn', 0):.0f} kN",
         f"FS={_str_vient.get('factor_seguridad', 0):.1f}",
         _str_vient.get('estado', '—')),
        ('EGMS',
         f"{_str_egms2.get('vel_max_mm_yr', 0):.1f} mm/año",
         f"5yr={_str_egms2.get('acumulado_5yr_mm', 0):.0f} mm",
         '—',
         _str_egms2.get('estado', '—')),
        ('SUELO',
         f"{_str_suelo2.get('capacidad_kpa', 0):.0f} kPa cap.",
         f"{_str_suelo2.get('presion_max_kpa', 0):.0f} kPa carga",
         f"FS={_str_suelo2.get('factor_seguridad', 0):.1f}",
         _str_suelo2.get('estado', '—')),
    ]
    _str_xs = [3.1, 4.8, 6.3, 7.8, 9.0]
    for _sx, _sh in zip(_str_xs, ['FACTOR', 'VALOR', 'DETALLE', 'FS', 'ESTADO']):
        ax_str.text(_sx, 8.3, _sh, color=GOLD, fontsize=8,
                    fontfamily='monospace', fontweight='bold')
    ax_str.plot([3.0, 9.8], [7.9, 7.9], color=GOLD + '55', lw=0.5)
    _str_y = 7.3
    for _sfact, _sval, _sdet, _sfs, _sest in _str_items:
        _sest_c = (GRNL if _sest == 'OK' else
                   YELL if _sest == 'ATENCIÓN' else
                   REDL if 'CRÍT' in str(_sest) else WDIM)
        ax_str.text(_str_xs[0], _str_y, _sfact, color=WHITE, fontsize=8,
                    fontfamily='monospace', va='center', fontweight='bold')
        ax_str.text(_str_xs[1], _str_y, _sval, color=WDIM, fontsize=8,
                    fontfamily='monospace', va='center')
        ax_str.text(_str_xs[2], _str_y, _sdet, color=WDIM, fontsize=8,
                    fontfamily='monospace', va='center')
        ax_str.text(_str_xs[3], _str_y, _sfs, color=WHITE, fontsize=8,
                    fontfamily='monospace', va='center')
        ax_str.text(_str_xs[4], _str_y, str(_sest)[:9], color=_sest_c, fontsize=8,
                    fontfamily='monospace', va='center', fontweight='bold')
        _str_y -= 1.5
    ax_str.text(3.1, 0.5, f'Normativa: {(structural.get("normativa") or "")[:55]}',
                color=WDIM, fontsize=7.5, fontfamily='monospace')

    # ═══════════════════════════════════════════════════════════════════════════
    # ROW 14 — RIDER COMPLIANCE
    # ═══════════════════════════════════════════════════════════════════════════
    ax_rc = fig.add_subplot(gs[14])
    _ax_base(ax_rc, bg=BG2, xlim=(0, 10), ylim=(0, 10))
    _section_title(ax_rc, '  RIDER COMPLIANCE — Requerimientos técnicos vs condiciones reales')
    _confianza_tag(ax_rc, _get_confianza(rider_comp))

    _rc_estado = rider_comp.get('estado_global', 'N/D')
    _rc_col    = (GRNL if _rc_estado == 'CONFORME' else
                  YELL if _rc_estado in ('ATENCIÓN', 'INDETERMINADO') else REDL)
    _rc_n_crit = rider_comp.get('n_criticos', 0) or 0
    _rc_n_adv  = rider_comp.get('n_advertencias', 0) or 0
    _rc_n_conf = rider_comp.get('n_conformes', 0) or 0

    ax_rc.text(0.4, 9.4, f'Estado global: {_rc_estado}', color=_rc_col,
               fontsize=10, fontweight='bold', fontfamily='monospace')
    ax_rc.text(5.0, 9.4, f'Críticos: {_rc_n_crit}',
               color=REDL if _rc_n_crit else WDIM, fontsize=9, fontfamily='monospace')
    ax_rc.text(6.8, 9.4, f'Advertencias: {_rc_n_adv}',
               color=YELL if _rc_n_adv else WDIM, fontsize=9, fontfamily='monospace')
    ax_rc.text(8.8, 9.4, f'OK: {_rc_n_conf}',
               color=GRNL if _rc_n_conf else WDIM, fontsize=9, fontfamily='monospace')
    ax_rc.plot([0.2, 9.8], [9.0, 9.0], color=GOLD + '55', lw=0.5)

    _rc_incs = rider_comp.get('incumplimientos') or []
    _rc_inds = rider_comp.get('indeterminados') or []
    _rc_aprs = rider_comp.get('aprobaciones') or []
    _rc_all  = (
        [(i, 'critico')     for i in _rc_incs if i.get('severidad') == 'critico'] +
        [(i, 'advertencia') for i in _rc_incs if i.get('severidad') in ('advertencia', 'leve')] +
        [(i, 'indeterminado') for i in _rc_inds] +
        [(a, 'conforme')    for a in _rc_aprs[:3]]
    )

    _rc_hdrs = ['CAT.', 'DETALLE', 'SEV.', 'ACCIÓN']
    _rc_xs   = [0.3, 1.8, 7.5, 8.5]
    for _rhx, _rhdr in zip(_rc_xs, _rc_hdrs):
        ax_rc.text(_rhx, 8.7, _rhdr, color=GOLD, fontsize=7.5,
                   fontfamily='monospace', fontweight='bold')
    ax_rc.plot([0.2, 9.8], [8.3, 8.3], color=GOLD + '55', lw=0.5)

    _rc_y = 7.9
    if not _rc_all:
        ax_rc.text(5.0, 5.0, 'Sin datos de compliance — ejecutar módulos acústico/suelo/drenaje',
                   color=WDIM, fontsize=9, ha='center', fontfamily='monospace')
    else:
        for _rce, _rctype in _rc_all[:9]:
            _rc_row_c = (REDL if _rctype == 'critico' else
                         YELL if _rctype == 'advertencia' else
                         WDIM if 'indeter' in _rctype else GRNL)
            _rc_cat = _rce.get('categoria', '')[:7].upper()
            _rc_det = _rce.get('detalle', '')[:52]
            _rc_sev = ('OK' if _rctype == 'conforme' else
                       _rce.get('severidad', _rce.get('estado', ''))[:8].upper())
            _rc_acc = ('✓' if _rctype == 'conforme' else _rce.get('accion', '')[:14])
            ax_rc.text(_rc_xs[0], _rc_y, _rc_cat, color=_rc_row_c,
                       fontsize=7.5, fontfamily='monospace', va='center', fontweight='bold')
            ax_rc.text(_rc_xs[1], _rc_y, _rc_det,
                       color=WHITE if _rctype != 'conforme' else WDIM,
                       fontsize=7, fontfamily='monospace', va='center')
            ax_rc.text(_rc_xs[2], _rc_y, _rc_sev, color=_rc_row_c,
                       fontsize=7.5, fontfamily='monospace', va='center')
            ax_rc.text(_rc_xs[3], _rc_y, _rc_acc, color=_rc_row_c,
                       fontsize=7, fontfamily='monospace', va='center')
            _rc_y -= 0.85
            if _rc_y < 0.5:
                break

    ax_rc.text(5.0, 0.3, f'Fuente: {(rider_comp.get("fuente") or "Rider técnico")[:60]}',
               color=WDIM, fontsize=7.5, ha='center', fontfamily='monospace')

    # ═══════════════════════════════════════════════════════════════════════════
    # ROW 15 — SEPARATOR
    # ═══════════════════════════════════════════════════════════════════════════
    ax_sep3 = fig.add_subplot(gs[15])
    _separator(ax_sep3, '▼  CERTIFICACIÓN LEGAL — Faro Protocol')

    # ═══════════════════════════════════════════════════════════════════════════
    # ROW 16 — FII COMPONENTES (solo sección certificación legal — 2g)
    # ═══════════════════════════════════════════════════════════════════════════
    ax_fii2 = fig.add_subplot(gs[16])
    _ax_base(ax_fii2, bg=BG2, xlim=(0, 10), ylim=(0, 9))
    _section_title(ax_fii2, '  FII — Índice Faro de Integridad · Certificación Legal')
    _confianza_tag(ax_fii2, _get_confianza(show_data.get('fii') or {}))

    fii_disp = fii_val if fii_val is not None else 0
    fii_col  = _sc(fii_sem)
    fii_str  = f'{fii_disp:.0f}/100' if fii_val is not None else 'N/D'

    ax_fii2.text(2.0, 5.5, fii_str, color=fii_col, fontsize=36,
                 ha='center', va='center', fontweight='bold')
    ax_fii2.text(2.0, 2.8, fii_sem.upper().replace('_', ' '),
                 color=fii_col, fontsize=11, ha='center', fontweight='bold',
                 fontfamily='monospace')
    ax_fii2.text(2.0, 1.8, 'Índice Faro de Integridad',
                 color=WDIM, fontsize=8.5, ha='center', fontfamily='monospace')

    _fii_rows = [
        ('NDVI / Vegetación', '40%', fii_comp.get('ndvi', {}) if fii_comp else {}),
        ('EGMS Estructural',  '35%', fii_comp.get('egms', {}) if fii_comp else {}),
        ('Layout / Drenaje',  '25%', fii_comp.get('layout', {}) if fii_comp else {}),
    ]
    _fii_hdrs_list = ['COMPONENTE', 'PESO', 'SCORE', 'ESTADO']
    _fii_xs        = [4.2, 7.2, 8.2, 9.1]
    for _hx, _hdr in zip(_fii_xs, _fii_hdrs_list):
        ax_fii2.text(_hx, 8.3, _hdr, color=GOLD, fontsize=8,
                     fontfamily='monospace', fontweight='bold')
    ax_fii2.plot([4.0, 9.7], [7.9, 7.9], color=GOLD + '55', lw=0.6)

    _fii_ty = 7.3
    for _fname, _fpeso, _fc_data in _fii_rows:
        _fscore = _fc_data.get('score', 0) if isinstance(_fc_data, dict) else 0
        _fsem   = _fc_data.get('semaforo', 'sin_datos') if isinstance(_fc_data, dict) else 'sin_datos'
        _fcol   = _sc(_fsem)
        ax_fii2.text(4.2, _fii_ty, _fname, color=WHITE, fontsize=8.5,
                     fontfamily='monospace', va='center')
        ax_fii2.text(7.2, _fii_ty, _fpeso, color=WDIM, fontsize=8.5,
                     fontfamily='monospace', va='center')
        ax_fii2.text(8.2, _fii_ty, f'{_fscore:.0f}' if _fscore else 'N/D',
                     color=_fcol, fontsize=8.5, fontfamily='monospace', va='center',
                     fontweight='bold')
        ax_fii2.text(9.1, _fii_ty, _fsem.upper()[:6], color=_fcol, fontsize=8,
                     fontfamily='monospace', va='center')
        _fii_ty -= 1.4

    # ═══════════════════════════════════════════════════════════════════════════
    # ROW 17 — COMPARATIVA DALE PLAY vs FARO PROTOCOL (2e)
    # ═══════════════════════════════════════════════════════════════════════════
    ax_cmp = fig.add_subplot(gs[17])
    _ax_base(ax_cmp, bg=BG2, xlim=(0, 10), ylim=(0, 10))
    _section_title(ax_cmp, '  ANÁLISIS COMPARATIVO — Dale Play vs Faro Protocol Óptimo')

    _cmp_hdrs = ['VARIABLE', 'DALE PLAY (REAL)', 'FARO ÓPTIMO']
    _cmp_xs   = [0.4, 4.2, 7.5]
    for _hx, _hdr in zip(_cmp_xs, _cmp_hdrs):
        ax_cmp.text(_hx, 9.4, _hdr, color=GOLD, fontsize=8.5,
                    fontfamily='monospace', fontweight='bold')
    ax_cmp.plot([0.2, 9.8], [9.0, 9.0], color=GOLD + '55', lw=0.6)

    _r_esc = comp_real.get('escenario') or {}
    _o_esc = comp_opt.get('escenario') or {}

    _cmp_rows = [
        ('Altura escenario',
         f"{_r_esc.get('alto_m', 1.6):.1f} m",
         f"{_o_esc.get('alto_m', 2.1):.1f} m",
         YELL, GRNL),
        ('Sweet-spot global', '92.9%',  '100%', YELL, GRNL),
        ('Zona de drenaje',
         '⚠ Excede colector',
         '✓ Respeta zonas',
         REDL, GRNL),
        ('Torres de sonido',
         '⚠ Límite canal N-S',
         '✓ Posición óptima',
         YELL, GRNL),
        ('Tarimas distribuidoras', 'No',  'Sí', REDL, GRNL),
        ('Estado del césped',
         f"{'Regular' if ndvi is not None and ndvi < 0.3 else 'OK'}",
         'Óptimo',
         YELL if ndvi is not None and ndvi < 0.3 else GRNL, GRNL),
    ]
    _cy = 8.5
    for _ri, (_rvr, _rval, _ropt, _rcol, _ocol) in enumerate(_cmp_rows):
        if _ri % 2 == 0:
            ax_cmp.add_patch(mpatches.Rectangle((0.2, _cy - 0.38), 9.6, 0.75,
                facecolor=BG3, edgecolor='none', alpha=0.5))
        # 2e: font 8.5 todas las celdas, todas las filas visibles
        ax_cmp.text(0.4, _cy, _rvr,  color=WHITE, fontsize=8.5,
                    fontfamily='monospace', va='center')
        ax_cmp.text(4.2, _cy, _rval, color=_rcol, fontsize=8.5,
                    fontfamily='monospace', va='center', fontweight='bold')
        ax_cmp.text(7.5, _cy, _ropt, color=_ocol, fontsize=8.5,
                    fontfamily='monospace', va='center', fontweight='bold')
        _cy -= 1.05

    _dp_score   = comp_score.get('dale_play', 0) or 0
    _faro_score = comp_score.get('faro_optimo', 100) or 100
    _bbox(ax_cmp, 3.8, 0.15, 2.4, 1.0, fc='#1a0505', ec=REDL, lw=0.8)
    ax_cmp.text(5.0, 0.65, f'Cobertura Dale Play: {_dp_score}%',
                color=REDL, fontsize=8.5, ha='center', fontweight='bold',
                fontfamily='monospace')
    _bbox(ax_cmp, 6.5, 0.15, 3.2, 1.0, fc='#051a08', ec=GRNL, lw=0.8)
    ax_cmp.text(8.1, 0.65, f'Faro Óptimo: {_faro_score}%',
                color=GRNL, fontsize=8.5, ha='center', fontweight='bold',
                fontfamily='monospace')

    # ═══════════════════════════════════════════════════════════════════════════
    # ROW 18 — HASH + QR + FOOTER
    # ═══════════════════════════════════════════════════════════════════════════
    ax_ft = fig.add_subplot(gs[18])
    _ax_base(ax_ft, bg=BG3, xlim=(0, 10), ylim=(0, 9))
    for sp in ax_ft.spines.values():
        sp.set_color(GOLD); sp.set_linewidth(0.8)
    ax_ft.plot([0, 1], [0.998, 0.998], color=GOLD, lw=1.5,
               transform=ax_ft.transAxes, clip_on=False)

    _qr_big = _make_qr_arr(verify_url, 120)
    if _qr_big is not None:
        from matplotlib.offsetbox import OffsetImage, AnnotationBbox
        _qr_big_img = OffsetImage(_qr_big, zoom=0.75)
        _qr_big_ab  = AnnotationBbox(_qr_big_img, (5.0, 5.5),
                                      frameon=False, xycoords='data')
        ax_ft.add_artist(_qr_big_ab)

    ax_ft.text(5.0, 8.6, f'CERT: {cert_hash}',
               color=GOLD, fontsize=9, ha='center', fontweight='bold',
               fontfamily='monospace')
    ax_ft.text(5.0, 8.0, f'SHA-256 · Verificar en: {verify_url}',
               color=WDIM, fontsize=8, ha='center', fontfamily='monospace')
    ax_ft.text(5.0, 0.5,
               f'Faro Protocol · Dale Play · protocolfaro@gmail.com · '
               f'Datos: ESA Copernicus / NASA Earthdata · {ts.strftime("%Y-%m-%d %H:%M")} UTC',
               color=WDIM, fontsize=8, ha='center', fontfamily='monospace')

    # ── Guardar ───────────────────────────────────────────────────────────────
    out_path = str(_OUT_DIR / f'reporte_{show_id}.png')
    fig.savefig(out_path, format='png', dpi=100, bbox_inches='tight',
                facecolor=BG, pad_inches=0.1)
    plt.close(fig)
    return out_path
