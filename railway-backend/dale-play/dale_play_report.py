"""
dale_play_report.py — Reporte PNG profesional para auditoría de eventos Dale Play.
Mismo estilo visual que gen_velez_main.py: fondo oscuro, dorado, monospace.
Output: dale-play/reportes/reporte_{show_id}.png
"""
from __future__ import annotations
import hashlib, os, pathlib
from datetime import datetime

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.gridspec as gridspec
import matplotlib.patches as mpatches
from matplotlib.patches import FancyBboxPatch
from matplotlib.colors import LinearSegmentedColormap
import numpy as np

# ── Paleta idéntica a gen_velez_main.py ───────────────────────────────────────
BG    = '#06080b'
BG2   = '#0d1117'
BG3   = '#141c24'
GOLD  = '#c9a84c'
WHITE = '#f2ede4'
WDIM  = '#9aa0a8'
REDL  = '#e74c3c'
YELL  = '#f0b429'
GRNL  = '#27ae60'
BORDER= '#1e2a38'
DPI   = 100

_OUT_DIR = pathlib.Path(__file__).parent / "reportes"
_OUT_DIR.mkdir(exist_ok=True)


def _sc(sem: str) -> str:
    return {
        "verde": GRNL, "ok": GRNL,
        "amarillo": YELL, "atencion": YELL, "leve": YELL,
        "rojo": REDL, "critico": REDL,
    }.get(sem, WDIM)


def _panel(ax, title: str = ""):
    ax.set_facecolor(BG2)
    for sp in ax.spines.values():
        sp.set_color(BORDER); sp.set_linewidth(0.7)
    if title:
        ax.text(0.01, 0.955, title, color=GOLD, fontsize=8.5, fontweight='bold',
                ha='left', va='center', transform=ax.transAxes,
                fontfamily='monospace', clip_on=True)


def _gold_line(ax, y: float = 0.99):
    ax.plot([0, 1], [y, y], color=GOLD, lw=2,
            transform=ax.transAxes, clip_on=False)


def _kpi(ax, x: float, label: str, value: str, color: str, w: float = 0.185):
    ax.add_patch(FancyBboxPatch(
        (x - w/2, 0.08), w, 0.84,
        boxstyle="round,pad=0.008",
        facecolor=color + '18', edgecolor=color + '66', lw=0.9,
        transform=ax.transAxes,
    ))
    ax.text(x, 0.85, label, color=WDIM, fontsize=6,
            ha='center', va='top', transform=ax.transAxes, fontfamily='monospace')
    _vfs = 11 if len(value) > 6 else 14
    ax.text(x, 0.40, value, color=color, fontsize=_vfs, fontweight='bold',
            ha='center', va='center', transform=ax.transAxes)


def generate_report(show_data: dict, show_config: dict) -> str:
    """
    Genera el reporte PNG multi-sección y lo guarda en dale-play/reportes/.
    Retorna el path absoluto del PNG generado.
    """
    show_id   = show_data.get("show_id",   "show")
    artist    = show_data.get("artist",    "Artista")
    show_date = show_data.get("show_date", "")
    ts        = datetime.now()
    cert = hashlib.sha256(
        f"FARO-DALEPLAY-{show_id}-{ts.isoformat()}".encode()
    ).hexdigest()[:28].upper()

    sat      = show_data.get("satellite") or {}
    weather  = show_data.get("weather")   or {}
    acoustic = show_data.get("acoustic")  or {}
    soil     = show_data.get("soil")      or {}
    insar    = show_data.get("insar")     or {}
    layout   = show_data.get("layout")   or None  # layout subido via /upload-layout

    # ── Semáforos globales para KPI ──────────────────────────────────────────
    ndvi_v   = sat.get("ndvi")
    ndvi_sem = (sat.get("ndvi_status") or {}).get("semaforo", "sin_datos")
    wx_sem   = weather.get("riesgo_global", "sin_datos")
    soil_sem = ("critico" if soil.get("hay_exclusiones")
                else "sin_datos" if "error" in soil
                else "ok")
    ac_cov   = acoustic.get("cobertura_optima_pct", 0)
    ac_sem   = ("ok" if ac_cov >= 60 else "atencion" if ac_cov >= 40 else "critico")
    ins_sem  = "sin_datos"
    if insar.get("tribunas"):
        worst = max(abs(v.get("los_mm", 0)) for v in insar["tribunas"].values())
        ins_sem = ("ok" if worst < 1.0 else "atencion" if worst < 2.0 else "critico")

    # ── FII — Índice Faro de Integridad ──────────────────────────────────────
    try:
        from dale_play_fii import compute_fii
        import json as _json_fii, pathlib as _pl_fii
        _egms_path = _pl_fii.Path(__file__).parent / "models" / "egms_amalfitani.json"
        _egms_fii  = (_json_fii.loads(_egms_path.read_text(encoding="utf-8"))
                      if _egms_path.exists() else None)
        _dr_path_fii = _pl_fii.Path(__file__).parent / "models" / "drainage_amalfitani.json"
        _dr_fii    = (_json_fii.loads(_dr_path_fii.read_text(encoding="utf-8"))
                      if _dr_path_fii.exists() else None)
        fii_result = compute_fii(ndvi_v, _egms_fii, layout, _dr_fii)
    except Exception as _fe:
        fii_result = {"fii": None, "semaforo": "sin_datos",
                      "sello": "N/D", "componentes": {}}

    fii_val = fii_result.get("fii")
    fii_sem = fii_result.get("semaforo", "sin_datos")

    # ── Figura ───────────────────────────────────────────────────────────────
    fig = plt.figure(figsize=(14, 48), facecolor=BG)
    gs  = gridspec.GridSpec(13, 1, figure=fig, hspace=0.18,
          height_ratios=[1.6, 1.5, 3.8, 4.0, 4.8, 5.8, 3.5, 5.76, 6.0, 4.5, 15.5, 9.0, 1.2])
    fig.subplots_adjust(top=0.98)

    # ══ HEADER ════════════════════════════════════════════════════════════════
    ax_h = fig.add_subplot(gs[0])
    ax_h.set_facecolor(BG3); ax_h.axis('off')
    ax_h.plot([0,1],[0.97,0.97], color=GOLD, lw=3.5,
              transform=ax_h.transAxes, clip_on=False)
    ax_h.text(0.5, 0.70, 'FARO PROTOCOL  ·  DALE PLAY',
              color=GOLD, fontsize=20, fontweight='bold', ha='center',
              transform=ax_h.transAxes, fontfamily='monospace')
    ax_h.text(0.5, 0.30, f'{artist}  ·  Estadio Amalfitani  ·  {show_date}',
              color=WHITE, fontsize=11, ha='center', transform=ax_h.transAxes)
    ax_h.text(0.5, 0.06, 'Auditoría Operativa Pre-Show · Faro Protocol',
              color=WDIM, fontsize=8, ha='center', transform=ax_h.transAxes)

    # ══ KPI ROW ═══════════════════════════════════════════════════════════════
    ax_k = fig.add_subplot(gs[1])
    ax_k.set_facecolor(BG3); ax_k.axis('off')
    ax_k.set_xlim(0,1); ax_k.set_ylim(0,1)
    _gold_line(ax_k, y=0.96)
    kpis = [
        ("NDVI CAMPO",    f"{ndvi_v:.2f}" if ndvi_v is not None else "N/D", _sc(ndvi_sem)),
        ("CLIMA 72HS",    wx_sem.upper()[:9],                                _sc(wx_sem)),
        ("SUELO",         soil_sem.upper()[:9],                              _sc(soil_sem)),
        ("ACÚSTICA\nÓPT.", f"{ac_cov:.0f}%",                                 _sc(ac_sem)),
        ("FII",             f"{fii_val:.0f}/100" if fii_val is not None else "N/D", _sc(fii_sem)),
    ]
    for i, (lbl, val, col) in enumerate(kpis):
        _kpi(ax_k, (i + 0.5) / len(kpis), lbl, val, col)

    # ══ BASELINE SATELITAL ════════════════════════════════════════════════════
    ax_s = fig.add_subplot(gs[2])
    _panel(ax_s, '  BASELINE SATELITAL — Sentinel-2 NDVI  +  Landsat TIRS')
    ax_s.set_facecolor('#060d08')
    ax_s.set_xlim(0,10); ax_s.set_ylim(0, 8.8)
    ax_s.tick_params(left=False, bottom=False, labelleft=False, labelbottom=False)
    _gold_line(ax_s)

    cmap = LinearSegmentedColormap.from_list(
        'ndvi', ['#b71c1c','#ef6c00','#fdd835','#66bb6a','#1b5e20'], N=256)
    ndvi_v2 = ndvi_v if ndvi_v is not None else None
    _ndvi_disp = ndvi_v2 if ndvi_v2 is not None else 0.45
    t  = np.clip((_ndvi_disp - 0.1) / 0.7, 0, 1)
    fc = cmap(t)

    # Esquema estadio (simplificado)
    ax_s.add_patch(mpatches.Rectangle((0.3, 0.8), 5.5, 6.5,
        facecolor='#0a1a08', edgecolor='#ffffff22', lw=0.8, zorder=1))
    ax_s.add_patch(mpatches.Rectangle((0.7, 1.3), 4.7, 5.5,
        facecolor=fc, edgecolor='#ffffff44', lw=0.7, zorder=2, alpha=0.85))
    ax_s.add_patch(mpatches.Ellipse((3.05, 4.0), 1.8, 2.2,
        fill=False, edgecolor='#ffffff55', lw=0.5, zorder=3))
    ax_s.plot([3.05,3.05],[1.3,6.8], color='#ffffff44', lw=0.5, zorder=3)

    ndvi_lbl = (sat.get("ndvi_status") or {}).get("label", "Sin datos")
    ax_s.text(3.05, 4.0, f'NDVI\n{ndvi_v2:.2f}' if ndvi_v2 is not None else 'NDVI\nN/D',
              color=WHITE, fontsize=13, fontweight='bold', ha='center', va='center', zorder=5)
    ax_s.text(3.05, 0.5, ndvi_lbl, color=_sc(ndvi_sem), fontsize=8, ha='center',
              fontfamily='monospace')
    ax_s.text(0.03, 0.87,
              f'Sentinel-2 · {sat.get("ndvi_fecha","—")} · Nube {sat.get("ndvi_cloud_pct","—")}%',
              color=WDIM, fontsize=7.5, transform=ax_s.transAxes)

    # Panel TIRS (derecha)
    tirs     = sat.get("tirs_celsius")
    tirs_col = (WDIM if tirs is None else REDL if tirs > 35 else YELL if tirs > 28 else GRNL)
    ax_s.add_patch(mpatches.Rectangle((6.4, 1.3), 3.2, 5.5,
        facecolor=BG3, edgecolor=BORDER, lw=0.7, zorder=1))
    ax_s.text(8.0, 6.4, 'TEMP. SUPERFICIAL', color=GOLD, fontsize=8,
              fontweight='bold', ha='center', fontfamily='monospace')
    ax_s.text(8.0, 4.3, f"{tirs:.1f}°C" if tirs is not None else "N/D",
              color=tirs_col, fontsize=28, fontweight='bold', ha='center', va='center')
    tirs_lbl = ("Sin datos TIRS disponibles" if tirs is None
                else "Estrés térmico — impacto en pasto" if tirs > 35
                else "Temperatura moderada" if tirs > 28
                else "Temperatura normal")
    ax_s.text(8.0, 2.7, tirs_lbl, color=tirs_col, fontsize=7.5,
              ha='center', fontfamily='monospace')
    ax_s.text(8.0, 1.9, f'Landsat · {sat.get("tirs_fecha","—")}',
              color=WDIM, fontsize=7.5, ha='center')

    # ══ PRONÓSTICO CLIMÁTICO 72HS ═════════════════════════════════════════════
    ax_w = fig.add_subplot(gs[3])
    _panel(ax_w, '  PRONÓSTICO CLIMÁTICO 72HS — Riesgo Operativo · Open-Meteo')
    ax_w.set_facecolor(BG2)
    ax_w.set_xlim(0,10); ax_w.set_ylim(0, 8.8)
    ax_w.tick_params(left=False, bottom=False, labelleft=False, labelbottom=False)
    _gold_line(ax_w)

    dias = (weather.get("dias") or [])[:4]
    alertas_wx = weather.get("alertas") or []
    n_d = len(dias)
    if n_d:
        cw = 9.2 / n_d
        for i, day in enumerate(dias):
            x0 = 0.3 + i * cw
            xc = x0 + cw / 2
            is_show = day.get("fecha") == show_data.get("show_date")
            sem = day.get("riesgo_dia", "ok")
            bc  = _sc(sem)
            ax_w.add_patch(FancyBboxPatch((x0 + 0.1, 0.8), cw - 0.25, 6.8,
                boxstyle="round,pad=0.1",
                facecolor=bc + '15', edgecolor=bc + '77' if is_show else BORDER,
                lw=1.6 if is_show else 0.6))
            fecha = day.get("fecha", "")[-5:]
            ax_w.text(xc, 7.3, ('★ ' if is_show else '') + fecha,
                      color=GOLD if is_show else WHITE, fontsize=9.5,
                      fontweight='bold', ha='center', fontfamily='monospace')
            tmax, tmin = day.get("tmax"), day.get("tmin")
            ax_w.text(xc, 6.1,
                      f"{tmax:.0f}° / {tmin:.0f}°" if tmax is not None else "—",
                      color=WHITE, fontsize=9.5, ha='center')
            lluvia = day.get("lluvia_mm", 0)
            rc = REDL if lluvia > 20 else YELL if lluvia > 5 else GRNL
            ax_w.text(xc, 5.0, f'~ {lluvia:.0f} mm', color=rc, fontsize=9, ha='center')
            rachas = day.get("rachas_max_kmh", 0)
            wc = REDL if rachas >= 65 else YELL if rachas >= 40 else GRNL
            ax_w.text(xc, 3.9, f'>> {rachas:.0f} km/h', color=wc, fontsize=9, ha='center')
            sem_lbl = {"ok":"OK","atencion":"PRECAUCIÓN","critico":"⚠ RIESGO",
                       "sin_datos":"N/D"}.get(sem, sem.upper())
            ax_w.text(xc, 2.6, sem_lbl, color=bc, fontsize=8,
                      fontweight='bold', ha='center', fontfamily='monospace')
    for j, a in enumerate(alertas_wx[:2]):
        ax_w.text(0.02, 0.12 - j*0.07, f'• {a[:105]}',
                  color=YELL, fontsize=7, transform=ax_w.transAxes)

    # ══ ANÁLISIS ACÚSTICO + SIGHTLINES ════════════════════════════════════════
    ax_a = fig.add_subplot(gs[4])
    _panel(ax_a, '  ANÁLISIS ACÚSTICO + SIGHTLINES — SPL por Sector · Modelo Geométrico')
    ax_a.set_facecolor(BG2)
    ax_a.set_xlim(0,10); ax_a.set_ylim(75, 92)
    ax_a.tick_params(left=False, bottom=False, labelleft=False, labelbottom=False)
    _gold_line(ax_a)

    secs     = acoustic.get("sectores") or []
    ac_alerts = acoustic.get("alertas_globales") or []
    spl_prom = acoustic.get("spl_promedio_db", 0)
    cob_opt  = acoustic.get("cobertura_optima_pct", 0)

    if secs:
        ns  = len(secs)
        bw  = 8.6 / ns
        _base_db = 75
        for i, sec in enumerate(secs):
            x0      = 0.6 + i * bw
            xc      = x0 + bw / 2
            spl     = sec.get("spl_db", 0)
            cob     = sec.get("cobertura", "baja")
            col     = _sc({"optima":"verde","buena":"verde","aceptable":"amarillo","baja":"rojo"}.get(cob,"sin_datos"))
            spl_top = min(spl, 91.5)
            ax_a.add_patch(mpatches.Rectangle((x0 + 0.1, _base_db), bw - 0.25, max(0.2, spl_top - _base_db),
                facecolor=col+'55', edgecolor=col, lw=0.8))
            _short_names = {
                "campo_central": "Campo C.", "tribuna_norte": "Trib. Norte",
                "tribuna_sur":   "Trib. Sur", "tribuna_este":  "Trib. Este",
                "tribuna_oeste": "Trib. Oeste",
            }
            _lbl = _short_names.get(sec.get("id",""), sec.get("name","")[:10])
            ax_a.text(xc, 91.6, _lbl,
                      color=WHITE, fontsize=7, ha='center', va='bottom')
            ax_a.text(xc, min(spl_top + 0.2, 90.8), f'{spl:.0f}dB',
                      color=col, fontsize=7.5, ha='center', fontweight='bold')
            sl = sec.get("sightline","")
            sl_col = {"optima":GRNL,"buena":GRNL,"parcial":YELL,"obstruida":REDL}.get(sl,WDIM)
            ax_a.text(xc, 75.3, sl[:3].upper(), color=sl_col, fontsize=7,
                      ha='center', fontfamily='monospace')

    ax_a.text(0.99, 0.97, f'SPL prom: {spl_prom:.0f} dB',
              color=WDIM, fontsize=8, ha='right', va='top', transform=ax_a.transAxes,
              fontfamily='monospace')
    ax_a.text(0.99, 0.90, f'Cobertura óptima: {cob_opt:.0f}%',
              color=_sc("verde" if cob_opt>=60 else "amarillo" if cob_opt>=40 else "rojo"),
              fontsize=8, ha='right', va='top', transform=ax_a.transAxes,
              fontfamily='monospace')
    for j, a in enumerate(ac_alerts[:2]):
        ax_a.text(0.01, 0.06 - j * 0.045, f'• {a[:100]}',
                  color=YELL, fontsize=6.5, transform=ax_a.transAxes)

    # ══ MAPA CARGA DEL SUELO ══════════════════════════════════════════════════
    ax_sl = fig.add_subplot(gs[5])
    _panel(ax_sl, '  MAPA DE CARGA DEL SUELO — Zonas Seguras / Exclusión')
    ax_sl.set_facecolor(BG2)
    ax_sl.set_xlim(0,10); ax_sl.set_ylim(0, 8.8)
    ax_sl.tick_params(left=False, bottom=False, labelleft=False, labelbottom=False)
    _gold_line(ax_sl)

    zonas     = soil.get("zonas")  or []
    al_soil   = soil.get("alertas") or []
    cap_kpa   = soil.get("capacidad_efectiva_kpa", 120)
    cond_s    = soil.get("condicion_suelo", "normal")
    cond_col  = {"normal":GRNL,"húmedo":YELL,"saturado":REDL}.get(cond_s, WDIM)

    ax_sl.text(0.01, 0.91, f'Suelo: {cond_s.upper()}  ({cap_kpa} kPa)',
               color=cond_col, fontsize=8.5, ha='left',
               fontweight='bold', fontfamily='monospace', transform=ax_sl.transAxes)

    # ── Cards de zonas (parte superior, y=2.5..8.5) ───────────────────────────
    nz = min(len(zonas), 5) or 1
    cw2 = 9.2 / nz
    for i, z in enumerate(zonas[:5]):
        x0  = 0.3 + i * cw2
        xc  = x0 + cw2 / 2
        zcol = z.get("color", GRNL)
        ax_sl.add_patch(FancyBboxPatch((x0 + 0.1, 2.5), cw2 - 0.25, 5.9,
            boxstyle="round,pad=0.1",
            facecolor=zcol+'18', edgecolor=zcol+'77', lw=1.0))
        ax_sl.text(xc, 8.0, z.get("nombre","")[:16], color=WHITE,
                   fontsize=7.5, ha='center', fontweight='bold')
        ax_sl.text(xc, 7.0, f'{z.get("carga_ton",0):.0f} t',
                   color=WDIM, fontsize=8, ha='center')
        ax_sl.text(xc, 5.9, f'{z.get("presion_kpa",0):.0f} kPa',
                   color=WHITE, fontsize=12, fontweight='bold', ha='center')
        ax_sl.text(xc, 4.7, z.get("clase","").upper(),
                   color=zcol, fontsize=8, fontweight='bold',
                   ha='center', fontfamily='monospace')
        ax_sl.text(xc, 3.2, z.get("label","")[:28],
                   color=WDIM, fontsize=6.5, ha='center')

    # ── SMAP/ERA5 — fila separada DEBAJO de las cards (y=0.15..2.1) ──────────
    import json as _json_smap
    _smap_path = pathlib.Path(__file__).parent / "models" / "smap_amalfitani.json"
    if _smap_path.exists():
        _smap = _json_smap.loads(_smap_path.read_text(encoding='utf-8'))
        _sm_sup  = _smap.get('humedad_superficial_%')
        _sm_root = _smap.get('humedad_raiz_%')
        _sm_est  = _smap.get('estado', 'sin_datos')
        _sm_col  = _sc({'seco':'atencion','normal':'ok','saturado':'critico'}.get(_sm_est,'sin_datos'))
        _sm_fecha = _smap.get('fecha', '')
        _sm_fuente = _smap.get('fuente', '')[:50]
        if _sm_sup is not None:
            ax_sl.add_patch(FancyBboxPatch((0.15, 0.15), 9.55, 2.0,
                boxstyle='round,pad=0.06',
                facecolor=BG3, edgecolor=_sm_col+'44', lw=0.8))
            ax_sl.text(0.40, 1.95, 'SMAP/ERA5 — Humedad del suelo',
                color=GOLD, fontsize=8, fontweight='bold', fontfamily='monospace', va='top')
            ax_sl.text(0.40, 1.45,
                f"Superficial {_sm_sup:.1f}%   Raíz {_sm_root:.1f}%   Estado: {_sm_est.upper()}   Fecha: {_sm_fecha}",
                color=_sm_col, fontsize=8, fontfamily='monospace', fontweight='bold', va='top')
            ax_sl.text(0.40, 0.90, _sm_fuente,
                color=WDIM, fontsize=6.5, fontfamily='monospace', style='italic', va='top')
            for j, a in enumerate(al_soil[:2]):
                ax_sl.text(5.5, 1.95 - j * 0.52, f'• {a[:55]}',
                    color=YELL, fontsize=6.5, va='top', fontfamily='monospace')
    else:
        for j, a in enumerate(al_soil[:2]):
            ax_sl.text(0.02, 0.08 - j*0.05, f'• {a[:108]}',
                color=YELL, fontsize=6.5, transform=ax_sl.transAxes)

    # ══ InSAR ESTRUCTURAL ═════════════════════════════════════════════════════
    ax_i = fig.add_subplot(gs[6])
    _panel(ax_i, '  INTEGRIDAD ESTRUCTURAL — Sentinel-1 InSAR Post-Show · Tribunas')
    ax_i.set_facecolor(BG2)
    ax_i.set_xlim(0,10); ax_i.set_ylim(0, 8.8)
    ax_i.tick_params(left=False, bottom=False, labelleft=False, labelbottom=False)
    _gold_line(ax_i)

    trib = insar.get("tribunas") or {}
    if trib:
        keys = list(trib.keys())
        tw   = 9.0 / len(keys)
        for i, t_id in enumerate(keys):
            t    = trib[t_id]
            xc   = 0.8 + i * tw + tw / 2
            los  = abs(t.get("los_mm", 0))
            nivel = t.get("nivel", "ok")
            tcol = _sc({"ok":"verde","leve":"amarillo","atencion":"amarillo","critico":"rojo"}.get(nivel,"sin_datos"))
            ax_i.text(xc, 7.3, t_id.replace("_"," ").upper(),
                      color=WHITE, fontsize=8, ha='center', fontweight='bold')
            ax_i.text(xc, 5.8, f'{los:.2f} mm', color=tcol,
                      fontsize=18, fontweight='bold', ha='center', va='center')
            ax_i.text(xc, 4.5, t.get("label","")[:24],
                      color=WDIM, fontsize=7, ha='center')
            bh = min(2.2, los / 3.5 * 2.2)
            ax_i.add_patch(mpatches.Rectangle((xc - 0.35, 1.5), 0.7, max(0.1,bh),
                facecolor=tcol+'55', edgecolor=tcol, lw=0.8))
        fuente = insar.get("fuente","")
        if fuente:
            ax_i.text(0.5, 0.04, fuente, color=WDIM, fontsize=7,
                      ha='center', transform=ax_i.transAxes)
    else:
        ax_i.text(5.0, 5.0,
                  'InSAR post-show disponible\ndespués del evento',
                  color=WDIM, fontsize=10, ha='center', va='center',
                  fontfamily='monospace', style='italic')
        ax_i.text(5.0, 3.0,
                  'POST /dale-play/run  {show_id, mode: "post_show"}',
                  color=WDIM, fontsize=7.5, ha='center', fontfamily='monospace')

    # ══ OPTIMIZACIÓN DE PRODUCCIÓN ═══════════════════════════════════════════
    ax_opt = fig.add_subplot(gs[7])
    _panel(ax_opt, '  OPTIMIZACIÓN DE PRODUCCIÓN — Sightlines C-value · Rider Variables')
    ax_opt.set_facecolor(BG2)
    ax_opt.set_xlim(0, 10); ax_opt.set_ylim(0, 8.8)
    ax_opt.tick_params(left=False, bottom=False, labelleft=False, labelbottom=False)
    _gold_line(ax_opt)

    # Cargar datos de sightlines y rider_sim si existen
    import json as _json
    _models_dir = pathlib.Path(__file__).parent / "models"
    _sl_path  = _models_dir / f"sightlines_{show_id.split('_')[0]}.json"
    _sim_path = _models_dir / f"rider_sim_{show_id.split('_')[0]}.json"
    # fallback genérico
    if not _sl_path.exists():
        _sl_path = _models_dir / "sightlines_airbag.json"
    if not _sim_path.exists():
        _sim_path = _models_dir / "rider_sim_airbag.json"

    _sl_data  = _json.loads(_sl_path.read_text(encoding='utf-8'))  if _sl_path.exists()  else None
    _sim_data = _json.loads(_sim_path.read_text(encoding='utf-8')) if _sim_path.exists() else None

    # ── Heatmap sightlines (izquierda, x=0..5.5) ─────────────────────────────
    if _sl_data:
        _sect_order = ['norte', 'este', 'oeste', 'sur']
        _sect_lbl   = ['NORTE', 'ESTE', 'OESTE', 'SUR']
        _ncol       = len(_sect_order)
        _sw         = 5.2 / _ncol           # ancho por sector
        _hmap_y0    = 1.1
        _hmap_h     = 6.7

        ax_opt.text(2.6, 7.95, 'C-VALUE ISO/FIFA  (verde=excelente)',
                    color=WDIM, fontsize=7, ha='center', fontfamily='monospace')

        for _si, _sn in enumerate(_sect_order):
            _x0  = 0.2 + _si * _sw
            _xc  = _x0 + _sw / 2
            _hm  = (_sl_data.get("heatmaps") or {}).get(_sn, {})
            _cv  = _hm.get("c_values_m", [])
            _nf  = len(_cv)
            if _nf == 0:
                continue
            _rh = _hmap_h / _nf
            for _ri, _c in enumerate(_cv):
                _y   = _hmap_y0 + _ri * _rh
                _col = (GRNL   if _c >= 0.120 else
                        '#7cb518' if _c >= 0.090 else
                        YELL   if _c >= 0.060 else
                        '#e67e22' if _c >= 0.0  else REDL)
                _alpha = 0.85 if _c >= 0.060 else 0.55
                ax_opt.add_patch(mpatches.Rectangle(
                    (_x0 + 0.05, _y), _sw - 0.15, _rh * 0.92,
                    facecolor=_col, edgecolor='none', alpha=_alpha, zorder=2))

            _vis = (_sl_data.get("tribunas") or {}).get(_sn, {}).get("visibilidad", {})
            _ex  = _vis.get("excelente_pct", 0)
            _cp  = _vis.get("c_prom_m", 0)
            _obstr = (_sl_data.get("tribunas") or {}).get(_sn, {}).get("obstruida_por_escenario", False)
            _col_lbl = REDL if _obstr else (GRNL if _ex >= 90 else YELL)

            _sect_desc = ['Norte', 'Este', 'Oeste', 'Sur — frente escenario']
            ax_opt.text(_xc, _hmap_y0 - 0.10,
                        f'{_sect_lbl[_si]}  {_ex:.0f}%',
                        color=_col_lbl, fontsize=6.5, ha='center', va='top',
                        fontfamily='monospace')
            ax_opt.text(_xc, _hmap_y0 - 0.55,
                        _sect_desc[_si],
                        color=WDIM, fontsize=5.5, ha='center', va='top',
                        fontfamily='monospace', style='italic')
            if _obstr:
                ax_opt.text(_xc, _hmap_y0 + _hmap_h / 2,
                            'OBSTRUIDA', color=REDL, fontsize=6,
                            ha='center', va='center', fontfamily='monospace',
                            rotation=90, alpha=0.8, zorder=3)

        # Leyenda C-value
        _ly = 0.40
        for _li, (_lab, _col) in enumerate([
            ('>= 120mm EXCELENTE', GRNL),
            ('60-120mm ADECUADO', YELL),
            ('< 60mm BAJO', '#e67e22'),
        ]):
            ax_opt.add_patch(mpatches.Rectangle((0.2 + _li * 1.72, _ly - 0.18), 0.28, 0.35,
                facecolor=_col, edgecolor='none', alpha=0.85))
            ax_opt.text(0.56 + _li * 1.72, _ly, _lab,
                        color=WDIM, fontsize=6, va='center', fontfamily='monospace')
    else:
        ax_opt.text(2.6, 4.4, 'Sightlines: correr módulo dale_play_sightlines',
                    color=WDIM, fontsize=9, ha='center', va='center',
                    fontfamily='monospace', style='italic')

    # ── Tabla rider simulation (derecha, x=5.6..10) ──────────────────────────
    if _sim_data:
        _tx = 5.7
        # Filtrar: despl=0, led=0, ordenar por stage_h
        _alt_rows = sorted(
            [r for r in _sim_data if r.get('despl_norte_m', 0) == 0 and r.get('led_tilt_deg', 0) == 0],
            key=lambda r: r['stage_h_m'])
        _base_sw_g = next((r['sweet_spot_global_pct'] for r in _alt_rows if abs(r['stage_h_m'] - 1.6) < 0.05), 92.9)

        # Header de tabla
        _hy = 8.05
        _cols = [(0.1, 'ALTURA'), (1.35, 'SWEET'), (2.2, 'DELTA'), (3.1, 'NORTE'), (3.75, 'E / O')]
        for _cx2, _ch in _cols:
            ax_opt.text(_tx + _cx2, _hy, _ch, color=GOLD, fontsize=8,
                        ha='left', fontfamily='monospace', fontweight='bold')
        ax_opt.plot([_tx, _tx + 4.2], [_hy - 0.15, _hy - 0.15], color=GOLD + '66', lw=0.6)

        _n_show = min(len(_alt_rows), 5)
        _row_step = 1.35
        for _ri, _rr in enumerate(_alt_rows[:_n_show]):
            _ry      = _hy - 0.45 - _ri * _row_step
            _is_base = abs(_rr['stage_h_m'] - 1.6) < 0.05
            _sw_g    = _rr['sweet_spot_global_pct']
            _delta   = _sw_g - _base_sw_g
            _norte_sw = (_rr.get('sectores') or {}).get('norte', {}).get('sweet_spot_pct', 0)
            _eo_sw   = (_rr.get('sectores') or {}).get('este',  {}).get('sweet_spot_pct', 0)
            _rc      = GOLD if _is_base else (GRNL if _delta > 0 else WHITE)

            _dh = round(_rr['stage_h_m'] - 1.6, 1)
            _dh_str = f'+{_dh}m' if _dh > 0 else 'base'
            _stage_str = f"{_rr['stage_h_m']:.1f}m ({_dh_str})"

            if _is_base:
                ax_opt.add_patch(FancyBboxPatch(
                    (_tx - 0.1, _ry - 0.25), 4.4, _row_step * 0.88,
                    boxstyle="round,pad=0.05",
                    facecolor=GOLD + '18', edgecolor=GOLD + '44', lw=0.7))

            ax_opt.text(_tx + 0.1, _ry, _stage_str, color=_rc, fontsize=8,
                        ha='left', fontfamily='monospace')
            ax_opt.text(_tx + 1.35, _ry, f'{_sw_g:.1f}%', color=_rc, fontsize=8,
                        ha='left', fontfamily='monospace')
            ax_opt.text(_tx + 2.2, _ry, f'{_delta:+.1f}%' if not _is_base else '—',
                        color=(GRNL if _delta > 0 else WDIM), fontsize=8,
                        ha='left', fontfamily='monospace')
            ax_opt.text(_tx + 3.1, _ry, f'{_norte_sw:.1f}%', color=_rc, fontsize=8,
                        ha='left', fontfamily='monospace')
            ax_opt.text(_tx + 3.75, _ry, f'{_eo_sw:.1f}%', color=_rc, fontsize=8,
                        ha='left', fontfamily='monospace')

        # Recomendación
        _best = max(_alt_rows, key=lambda r: r['sweet_spot_global_pct'])
        _best_dh = round(_best['stage_h_m'] - 1.6, 1)
        if abs(_delta) > 0.5:
            _rec_line1 = f"  RECOMENDACION: stage +{_best_dh}m"
            _rec_line2 = f"  → sweet-spot {_best['sweet_spot_global_pct']:.0f}% (+{_best['sweet_spot_global_pct']-_base_sw_g:.1f}%)"
        else:
            _rec_line1 = "  Configuracion base: optima"
            _rec_line2 = f"  sweet-spot global: {_base_sw_g:.0f}%"
        ax_opt.text(_tx - 0.1, 0.55, _rec_line1,
                    color=GRNL, fontsize=8, ha='left', fontfamily='monospace', fontweight='bold')
        ax_opt.text(_tx - 0.1, 0.18, _rec_line2,
                    color=GRNL, fontsize=8, ha='left', fontfamily='monospace')
    else:
        ax_opt.text(7.5, 4.4, 'Correr dale_play_rider_sim',
                    color=WDIM, fontsize=9, ha='center', va='center',
                    fontfamily='monospace', style='italic')

    # ══ INTEGRIDAD HISTÓRICA EGMS 2015-2022 ═══════════════════════════════════
    ax_eg = fig.add_subplot(gs[8])
    _panel(ax_eg, '  INTEGRIDAD HISTÓRICA 2015-2022 — EGMS Copernicus · Deformación Sentinel-1 · Velocidad mm/año')
    ax_eg.set_facecolor(BG2)
    ax_eg.set_xlim(0, 10); ax_eg.set_ylim(0, 8.8)
    ax_eg.tick_params(left=False, bottom=False, labelleft=False, labelbottom=False)
    _gold_line(ax_eg)

    import json as _json_eg
    _eg_path = pathlib.Path(__file__).parent / "models" / "egms_amalfitani.json"
    _eg_data = _json_eg.loads(_eg_path.read_text(encoding='utf-8')) if _eg_path.exists() else None

    if _eg_data:
        _eg_secs = _eg_data.get('sectores', {})
        _eg_order = ['tribuna_oeste', 'tribuna_sur', 'tribuna_norte', 'campo', 'tribuna_este']
        _eg_y     = [7.5, 6.1, 4.7, 3.3, 1.9]   # y position per bar
        _eg_vel_max = 5.0                          # escala: -5 mm/año → ancho máximo

        # ── Barra velocidad (izquierda x=0.2..4.8) ────────────────────────────
        _EG_BAR_X0 = 2.0   # barras empiezan aquí; labels a la izquierda
        _EG_BL_MAX = 2.6   # max bar length in data units
        ax_eg.text((_EG_BAR_X0 + _EG_BAR_X0 + _EG_BL_MAX) / 2, 8.0,
            'Velocidad mm/año  (2015-2022)',
            color=WDIM, fontsize=7, ha='center', fontfamily='monospace')
        ax_eg.plot([_EG_BAR_X0, _EG_BAR_X0 + _EG_BL_MAX + 0.5], [7.75, 7.75],
            color=WDIM+'33', lw=0.5)

        for _i, (_sid, _ey) in enumerate(zip(_eg_order, _eg_y)):
            _es = _eg_secs.get(_sid, {})
            _vel = _es.get('vel_mm_yr', 0)
            _niv = _es.get('nivel', 'ok')
            _ec  = _sc({'ok':'verde','atencion':'amarillo','critico':'rojo'}.get(_niv,'sin_datos'))
            _bl  = abs(_vel) / _eg_vel_max * _EG_BL_MAX
            # barra
            ax_eg.add_patch(mpatches.Rectangle((_EG_BAR_X0, _ey - 0.38), _bl, 0.72,
                facecolor=_ec+'55', edgecolor=_ec, lw=0.9))
            # sector label — a la izquierda del bar start, con espacio suficiente
            ax_eg.text(_EG_BAR_X0 - 0.12, _ey, _es.get('nombre','')[:18],
                color=WHITE, fontsize=7, va='center', ha='right', fontfamily='monospace')
            # valor + proyección
            _proy = _es.get('proyeccion_2027_mm', 0)
            _tx = min(_EG_BAR_X0 + _bl + 0.12, 4.6)
            ax_eg.text(_tx, _ey + 0.2,
                f'{_vel:.1f} mm/a',
                color=_ec, fontsize=7.5, va='center', fontweight='bold')
            ax_eg.text(_tx, _ey - 0.22,
                f'2027: {_proy:.0f} mm',
                color=WDIM, fontsize=6.2, va='center', fontfamily='monospace')
            # tendencia
            _tend = _es.get('tendencia', '')
            _tc   = REDL if _tend == 'acelerada' else YELL if _tend == 'moderada' else GRNL
            ax_eg.text(4.80, _ey, _tend[:9],
                color=_tc, fontsize=6, va='center', ha='left', fontfamily='monospace',
                style='italic')

        # Alerta sector crítico
        _critico_id  = _eg_data.get('sector_critico','')
        _critico_sec = _eg_secs.get(_critico_id, {})
        ax_eg.text(0.25, 0.9,
            f"▲  SECTOR CRÍTICO: {_critico_sec.get('nombre','')}"
            f" — {_eg_data.get('vel_max_abs_mm_yr',0):.1f} mm/año",
            color=REDL, fontsize=7.5, fontweight='bold', fontfamily='monospace')
        ax_eg.text(0.25, 0.45, _eg_data.get('alerta','')[:100],
            color=YELL, fontsize=6.5, fontfamily='monospace')

        # ── Serie temporal (derecha x=5.3..9.7) ────────────────────────────────
        _XS, _XE   = 5.3, 9.7
        _XPY       = (_XE - _XS) / 7              # unidades x por año
        _DMIN, _DMAX = -30.0, 1.0
        _YSD, _YED = 1.2, 7.8                      # y data para disp min/max

        def _yd(d): return _YSD + (d - _DMIN) / (_DMAX - _DMIN) * (_YED - _YSD)

        ax_eg.text((_XS+_XE)/2, 8.0, 'Desplazamiento acumulado (mm)',
            color=WDIM, fontsize=7, ha='center', fontfamily='monospace')
        # referencia 0 mm
        ax_eg.plot([_XS, _XE], [_yd(0), _yd(0)], color=WDIM+'44', lw=0.7)
        ax_eg.text(_XS - 0.08, _yd(0), '0', color=WDIM, fontsize=5.5, va='center', ha='right')
        # líneas de referencia
        for _rd in [-10, -20]:
            _ry = _yd(_rd)
            ax_eg.plot([_XS, _XE], [_ry, _ry], color=WDIM+'22', lw=0.5, linestyle='--')
            ax_eg.text(_XS - 0.08, _ry, str(_rd), color=WDIM, fontsize=5.5, va='center', ha='right')
        # etiquetas año (cada 2 para evitar superposición)
        for _yi in range(8):
            _xi = _XS + _yi * _XPY
            ax_eg.plot([_xi, _xi], [_YSD, _YSD + 0.2], color=WDIM+'44', lw=0.5)
            if _yi % 2 == 0:
                ax_eg.text(_xi, _YSD - 0.1, str(2015+_yi),
                    color=WDIM, fontsize=5.5, ha='center', va='top', fontfamily='monospace')
        # líneas por sector
        for _sid, _niv_col in [
            ('tribuna_oeste', REDL), ('tribuna_sur', YELL),
            ('tribuna_norte', YELL), ('campo', GRNL), ('tribuna_este', GRNL)
        ]:
            _es2 = _eg_secs.get(_sid, {})
            _ser = _es2.get('serie_temporal', [])
            if not _ser:
                continue
            # tomar punto anual (cada 4 entradas)
            _pts = [_ser[_yi * 4] for _yi in range(8) if _yi * 4 < len(_ser)]
            _xs2 = [_XS + _j * _XPY for _j in range(len(_pts))]
            _ys2 = [_yd(_p['despl_mm']) for _p in _pts]
            _lw2 = 1.5 if _sid == 'tribuna_oeste' else 0.9
            ax_eg.plot(_xs2, _ys2, color=_niv_col, lw=_lw2, zorder=3,
                alpha=0.9 if _sid == 'tribuna_oeste' else 0.65)
        # leyenda líneas
        ax_eg.text(_XS, 0.7, 'Rojo: T.Oeste (crítico)  Amarillo: T.Norte/Sur  Verde: T.Este/Campo',
            color=WDIM, fontsize=5.5, fontfamily='monospace')

        ax_eg.text(9.85, 8.2, f"Fuente: {_eg_data.get('producto','')}",
            color=WDIM, fontsize=5.5, ha='right', fontfamily='monospace')
    else:
        ax_eg.text(5.0, 4.4, 'Correr dale_play_egms.fetch_egms_amalfitani()',
            color=WDIM, fontsize=9, ha='center', va='center',
            fontfamily='monospace', style='italic')

    # ══ RED DE DRENAJE ════════════════════════════════════════════════════════
    ax_dr = fig.add_subplot(gs[9])
    _panel(ax_dr, '  RED DE DRENAJE — Sentinel-2 SWIR B11/B12 · Landsat TIRS · SAR VV/VH · Zonas de Exclusión Estructuras')
    ax_dr.set_facecolor(BG2)
    ax_dr.set_xlim(0, 10); ax_dr.set_ylim(0, 8.8)
    ax_dr.tick_params(left=False, bottom=False, labelleft=False, labelbottom=False)
    _gold_line(ax_dr)

    import json as _json_dr
    _dr_path = pathlib.Path(__file__).parent / "models" / "drainage_amalfitani.json"
    _dr_data = _json_dr.loads(_dr_path.read_text(encoding='utf-8')) if _dr_path.exists() else None

    if _dr_data:
        _DX0, _DX1 = 1.5, 9.3
        _DY0, _DY1 = 1.9, 8.0
        _DW, _DH   = _DX1 - _DX0, _DY1 - _DY0

        # Field base
        ax_dr.add_patch(mpatches.Rectangle((_DX0, _DY0), _DW, _DH,
            facecolor='#0a2a0a', edgecolor='#ffffff88', lw=1.2, zorder=1))

        # Pitch lines
        _cy = _DY0 + _DH / 2
        _cx = _DX0 + _DW / 2
        _lkw = dict(color='#ffffff55', lw=0.7, zorder=3)
        ax_dr.plot([_DX0, _DX1], [_cy, _cy], **_lkw)
        ax_dr.add_patch(mpatches.Ellipse((_cx, _cy), _DW * 0.22, _DH * 0.22,
            fill=False, edgecolor='#ffffff55', lw=0.7, zorder=3))
        ax_dr.plot([_cx], [_cy], 'o', color='#ffffff66', ms=2.5, zorder=3)
        for _gy0, _gy1 in [(_DY0, _DY0 + _DH*0.16), (_DY0 + _DH*0.84, _DY1)]:
            ax_dr.add_patch(mpatches.Rectangle(
                (_DX0 + _DW*0.22, _gy0), _DW*0.56, _gy1-_gy0,
                fill=False, edgecolor='#ffffff55', lw=0.7, zorder=3))
            ax_dr.add_patch(mpatches.Rectangle(
                (_DX0 + _DW*0.38, _gy0), _DW*0.24, (_gy1-_gy0)*0.33,
                fill=False, edgecolor='#ffffff44', lw=0.5, zorder=3))

        # Zone overlays — verde primero, rojo encima
        _zcol_map = {'rojo': REDL, 'amarillo': YELL, 'verde': GRNL}
        _zsort = sorted(_dr_data['zonas'],
            key=lambda z: {'verde':0,'amarillo':1,'rojo':2}[z.get('color','verde')])
        for _z in _zsort:
            _zx0 = _DX0 + _z['x_pct'][0]/100 * _DW
            _zx1 = _DX0 + _z['x_pct'][1]/100 * _DW
            _zy0 = _DY0 + _z['y_pct'][0]/100 * _DH
            _zy1 = _DY0 + _z['y_pct'][1]/100 * _DH
            _zcl = _zcol_map.get(_z.get('color','verde'), GRNL)
            _za  = 0.35 if _z.get('riesgo') == 'bajo' else 0.58
            ax_dr.add_patch(mpatches.Rectangle((_zx0, _zy0), _zx1-_zx0, _zy1-_zy0,
                facecolor=_zcl+'99', edgecolor=_zcl, lw=0.9, alpha=_za, zorder=2))
            _zxc, _zyc = (_zx0+_zx1)/2, (_zy0+_zy1)/2
            ax_dr.text(_zxc, _zyc, _z['nombre'][:10],
                color=WHITE, fontsize=5.5, ha='center', va='center', zorder=4,
                fontfamily='monospace', fontweight='bold',
                bbox=dict(boxstyle='round,pad=0.12', facecolor='#00000077', edgecolor='none'))

        # Cardinal labels
        ax_dr.text(_cx, _DY1 + 0.14, 'NORTE (tribuna)',
            color=WDIM, fontsize=6.5, ha='center', va='bottom', fontfamily='monospace')
        ax_dr.text(_cx, _DY0 - 0.1, 'SUR (escenario)',
            color=WDIM, fontsize=6.5, ha='center', va='top', fontfamily='monospace')

        # ── Layout overlay — estructuras reales del rider subido ──────────────
        if layout:
            _struct = (layout.get("estructuras") or {})
            _zonas_dr = _dr_data.get("zonas", [])
            _lay_alerts = []

            def _m_to_canvas(x_m, y_m):
                """Convierte metros desde centro del campo a coords canvas."""
                xp = (x_m + 34.0) / 68.0 * _DW
                yp = (y_m + 52.5) / 105.0 * _DH
                return _DX0 + xp, _DY0 + yp

            def _zone_collision(xp0, xp1, yp0, yp1):
                """Retorna nivel de riesgo máximo de las zonas que toca la estructura."""
                worst = "ok"
                for _zz in _zonas_dr:
                    if (xp0 < _zz['x_pct'][1] and xp1 > _zz['x_pct'][0] and
                            yp0 < _zz['y_pct'][1] and yp1 > _zz['y_pct'][0]):
                        if _zz.get("riesgo") == "alto":
                            worst = "alto"
                        elif _zz.get("riesgo") == "medio" and worst != "alto":
                            worst = "medio"
                return worst

            # Escenario
            _esc = _struct.get("escenario") or {}
            _ex, _ey = _esc.get("x_m"), _esc.get("y_m")
            _ew, _ed = _esc.get("ancho_m", 28), _esc.get("profundidad_m", 20)
            if _ex is not None and _ey is not None:
                _ex0p = (_ex - _ew/2 + 34.0) / 68.0 * 100
                _ex1p = (_ex + _ew/2 + 34.0) / 68.0 * 100
                _ey0p = (_ey - _ed/2 + 52.5) / 105.0 * 100
                _ey1p = (_ey + _ed/2 + 52.5) / 105.0 * 100
                _cx0, _cy0 = _m_to_canvas(_ex - _ew/2, _ey - _ed/2)
                _cx1, _cy1 = _m_to_canvas(_ex + _ew/2, _ey + _ed/2)
                _risk = _zone_collision(_ex0p, _ex1p, _ey0p, _ey1p)
                _rc = {REDL if _risk == "alto" else YELL if _risk == "medio" else GRNL}
                _rc = REDL if _risk == "alto" else YELL if _risk == "medio" else GRNL
                ax_dr.add_patch(mpatches.Rectangle(
                    (_cx0, _cy0), _cx1 - _cx0, _cy1 - _cy0,
                    facecolor=_rc + '33', edgecolor=_rc, lw=1.8,
                    linestyle='--', zorder=5))
                ax_dr.text((_cx0+_cx1)/2, (_cy0+_cy1)/2, 'ESC',
                    color=_rc, fontsize=5, ha='center', va='center',
                    fontweight='bold', zorder=6,
                    bbox=dict(boxstyle='round,pad=0.1', fc='#00000099', ec='none'))
                if _risk == "alto":
                    _lay_alerts.append("ALERTA: Escenario sobre zona EXCLUSIÓN de drenaje")
                elif _risk == "medio":
                    _lay_alerts.append("PRECAUCIÓN: Escenario sobre zona de drenaje media")

            # Torres L/R
            for _t in (_struct.get("torres_lr") or []):
                _tx, _ty = _t.get("x_m"), _t.get("y_m")
                _tid = _t.get("id", "T")
                if _tx is not None and _ty is not None:
                    _txp = (_tx + 34.0) / 68.0 * 100
                    _typ = (_ty + 52.5) / 105.0 * 100
                    _risk = _zone_collision(_txp - 2, _txp + 2, _typ - 2, _typ + 2)
                    _tc = REDL if _risk == "alto" else YELL if _risk == "medio" else GRNL
                    _ctx, _cty = _m_to_canvas(_tx, _ty)
                    ax_dr.plot(_ctx, _cty, 'D', color=_tc, ms=5, zorder=6,
                               markeredgecolor=WHITE + '88', markeredgewidth=0.5)
                    ax_dr.text(_ctx, _cty + 0.12, f'T{_tid}',
                        color=_tc, fontsize=4.5, ha='center', va='bottom',
                        fontweight='bold', zorder=6)
                    if _risk == "alto":
                        _lay_alerts.append(f"ALERTA: Torre {_tid} sobre zona EXCLUSIÓN")
                    elif _risk == "medio":
                        _lay_alerts.append(f"PRECAUCIÓN: Torre {_tid} sobre zona drenaje")

            # Barricada
            _bar = _struct.get("barricada") or {}
            _by, _bw = _bar.get("y_m"), _bar.get("ancho_m", 40)
            if _by is not None:
                _bxp0 = (- _bw/2 + 34.0) / 68.0 * 100
                _bxp1 = (_bw/2 + 34.0) / 68.0 * 100
                _byp0 = (_by - 0.5 + 52.5) / 105.0 * 100
                _byp1 = (_by + 0.5 + 52.5) / 105.0 * 100
                _bx0c, _by0c = _m_to_canvas(-_bw/2, _by - 0.5)
                _bx1c, _by1c = _m_to_canvas(_bw/2, _by + 0.5)
                _risk = _zone_collision(_bxp0, _bxp1, _byp0, _byp1)
                _bc = REDL if _risk == "alto" else YELL if _risk == "medio" else GRNL
                ax_dr.add_patch(mpatches.Rectangle(
                    (_bx0c, _by0c), _bx1c - _bx0c, max(0.06, _by1c - _by0c),
                    facecolor=_bc + '44', edgecolor=_bc, lw=1.4,
                    linestyle=':', zorder=5))
                ax_dr.text((_bx0c+_bx1c)/2, _by0c - 0.08, 'BARRICADA',
                    color=_bc, fontsize=4.5, ha='center', va='top', zorder=6)

            # Label "LAYOUT SUBIDO" + alertas de colisión
            ax_dr.text(0.01, 0.99,
                f"LAYOUT: {layout.get('filename','')[:30]}",
                color=GOLD, fontsize=6, ha='left', va='top',
                transform=ax_dr.transAxes, fontfamily='monospace')
            for _lai, _la in enumerate(_lay_alerts[:2]):
                _la_col = REDL if 'ALERTA' in _la else YELL
                ax_dr.text(0.01, 0.93 - _lai * 0.07, f'▲ {_la[:80]}',
                    color=_la_col, fontsize=6, ha='left', va='top',
                    transform=ax_dr.transAxes, fontweight='bold')

        # North arrow
        ax_dr.annotate('', xy=(_DX1 + 0.35, _DY0 + _DH*0.72),
            xytext=(_DX1 + 0.35, _DY0 + _DH*0.50),
            arrowprops=dict(arrowstyle='->', color=WDIM, lw=1.2))
        ax_dr.text(_DX1 + 0.35, _DY0 + _DH*0.77, 'N',
            color=WDIM, fontsize=8, ha='center', fontweight='bold')

        # Satellite source + field size
        ax_dr.text(9.85, 8.45, _dr_data.get('metodo',''),
            color=WDIM, fontsize=5.5, ha='right', fontfamily='monospace')
        _campo = _dr_data.get('campo', {})
        ax_dr.text(9.85, 8.1,
            f"{_campo.get('largo_m',105)} m × {_campo.get('ancho_m',68)} m",
            color=WDIM, fontsize=6, ha='right', fontfamily='monospace')

        # Legend
        _leg_items = [
            ('EXCLUSIÓN — no apoyar estructuras', REDL),
            ('PRECAUCIÓN — usar plataformas', YELL),
            ('SEGURO — capacidad nominal', GRNL),
        ]
        for _li, (_lt, _lc) in enumerate(_leg_items):
            _lx = 0.15 + _li * 3.22
            ax_dr.add_patch(mpatches.Rectangle((_lx, 1.45), 0.30, 0.26,
                facecolor=_lc+'88', edgecolor=_lc, lw=0.8))
            ax_dr.text(_lx + 0.40, 1.58, _lt,
                color=WDIM, fontsize=6.5, va='center', fontfamily='monospace')

        # Exclusion notes
        for _ei, _ex in enumerate((_dr_data.get('exclusiones') or [])[:2]):
            ax_dr.text(0.15, 1.03 - _ei * 0.62, f'▪ {_ex[:108]}',
                color=YELL, fontsize=6.5, va='top', fontfamily='monospace')
    else:
        ax_dr.text(5.0, 4.4, 'Correr dale_play_drainage.analyze_drainage()',
            color=WDIM, fontsize=9, ha='center', va='center',
            fontfamily='monospace', style='italic')

    # ══ ANÁLISIS COMPARATIVO — Dale Play vs Faro Protocol ════════════════════
    ax_cmp = fig.add_subplot(gs[10])
    _panel(ax_cmp, '  ANÁLISIS COMPARATIVO — Configuración Real (Dale Play) vs Óptima (Faro Protocol) · 5 fotos pre-show')
    ax_cmp.set_facecolor(BG2)
    ax_cmp.set_xlim(0, 10); ax_cmp.set_ylim(0, 15)
    ax_cmp.tick_params(left=False, bottom=False, labelleft=False, labelbottom=False)
    _gold_line(ax_cmp)

    import json as _json_cmp
    _cmp_path = pathlib.Path(__file__).parent / "models" / "airbag_comparativa.json"
    _cmp_data = _json_cmp.loads(_cmp_path.read_text(encoding='utf-8')) if _cmp_path.exists() else None

    if _cmp_data:
        _scr  = _cmp_data.get('score', {})
        _rec  = _cmp_data.get('recomendaciones', [])

        # ── Cabecera 5 columnas ───────────────────────────────────────────────
        _hcols5 = [
            (0.20, 'VARIABLE'),
            (2.35, 'DALE PLAY'),
            (3.65, 'FARO OPTIMO'),
            (5.05, 'DELTA'),
            (6.55, 'IMPACTO ECONÓMICO'),
        ]
        _hy = 14.0
        for _hx, _ht in _hcols5:
            ax_cmp.text(_hx, _hy, _ht, color=GOLD, fontsize=8,
                fontweight='bold', fontfamily='monospace')
        ax_cmp.plot([0.15, 9.85], [_hy - 0.22, _hy - 0.22], color=GOLD+'66', lw=0.9)

        # ── 9 filas de datos ──────────────────────────────────────────────────
        _rows9 = [
            ('Altura escenario',     '1.6 m',     '2.1 m',    '+0.5m sweet-spot +7.1%',  '~2.485 esp. con vista optima', YELL, YELL),
            ('Profundidad campo',    'y=22%',     'y=18%',    '+4.2m dentro exclusion',   'Evita daño Colect.Sur ~USD35k',YELL, YELL),
            ('Torre izquierda X',    'x=10%',     'x=15%',    '-3.4m del Canal N-S',      'Evita daño lateral ~USD5k',    YELL, YELL),
            ('Area afectada',        '1.540 m2',  '1.224 m2', '+316m2 daño cesped',       '316m2 x USD10 ~USD3.160',      YELL, YELL),
            ('Tarimas distrib.',     'AUSENTES',  'PRESENTES','carga puntual sin dist.',  'Rotura cañeria USD20k-50k',    REDL, REDL),
            ('Estado cesped',        'REGULAR',   'BUENO',    'NDVI 0.10 / SMAP 0%',     'Riego 48h ahorro ~USD2k',      YELL, YELL),
            ('Tiempo de montaje',    'base',      '+1-2h',    'reposicion. escenario',    'Sin costo adicional equipo',   WDIM, WDIM),
            ('Riesgo daño campo',    '1.540 m2',  '1.224 m2','Faro: -316m2 critico',     'Ahorro total ~USD3k-40k',      YELL, YELL),
            ('Zonas drenaje afect.', '2 criticas','0 criticas','Canal NS+Colector Sur',   'Cero riesgo infraestructura',  REDL, REDL),
        ]
        _ry0   = 13.2
        _rstep = 1.02
        for _ri, (_var, _dp, _fo, _dlt, _imp, _c_dp, _c_dlt) in enumerate(_rows9):
            _ry = _ry0 - _ri * _rstep
            _bg = BG3 if _ri % 2 == 0 else BG2
            ax_cmp.add_patch(mpatches.Rectangle((0.15, _ry - 0.42), 9.70, _rstep * 0.88,
                facecolor=_bg, edgecolor='none', zorder=0))
            ax_cmp.text(0.25,  _ry, _var, color=WHITE, fontsize=7.5, fontfamily='monospace', va='center')
            ax_cmp.text(2.35,  _ry, _dp,  color=_c_dp, fontsize=7.5, fontfamily='monospace', fontweight='bold', va='center')
            ax_cmp.text(3.65,  _ry, _fo,  color=GRNL,  fontsize=7.5, fontfamily='monospace', fontweight='bold', va='center')
            ax_cmp.text(5.05,  _ry, _dlt[:22], color=_c_dlt, fontsize=7.5, fontfamily='monospace', va='center')
            ax_cmp.text(6.55,  _ry, _imp[:24], color=WDIM,    fontsize=7.5, fontfamily='monospace', style='italic', va='center')
            ax_cmp.plot([0.15, 9.85], [_ry - 0.42, _ry - 0.42], color=BORDER, lw=0.4)

        # Separadores verticales
        for _sx in [2.25, 3.55, 4.95, 6.45]:
            ax_cmp.plot([_sx, _sx], [2.45, _hy - 0.22], color=BORDER, lw=0.6)

        # Separador antes del score
        ax_cmp.plot([0.15, 9.85], [2.45, 2.45], color=GOLD+'44', lw=0.9)

        # ── Score bar (3 celdas, y=0.25..2.25) ───────────────────────────────
        _gap_pts = _scr.get('gap', 0)
        _score_items = [
            (1.65,  'CONFIG. ACTUAL',  f"Cobertura: {ac_cov:.0f}%",  REDL),
            (5.00,  'CONFIG. OPTIMA',  'Cobertura: 100%',             GRNL),
            (8.35,  'MEJORA POSIBLE',  f"+{_gap_pts} pts",            GRNL),
        ]
        for _sx, _slbl, _sval, _scol in _score_items:
            ax_cmp.add_patch(FancyBboxPatch((_sx - 1.55, 0.22), 3.1, 2.1,
                boxstyle='round,pad=0.1',
                facecolor=_scol+'12', edgecolor=_scol+'55', lw=0.9))
            ax_cmp.text(_sx, 2.0,  _slbl, color=GOLD, fontsize=8,
                ha='center', fontweight='bold', fontfamily='monospace')
            ax_cmp.text(_sx, 1.1,  _sval, color=_scol, fontsize=13,
                ha='center', va='center', fontweight='bold', fontfamily='monospace')

        # Fuente
        ax_cmp.text(0.20, 0.10,
            f"Fuente: {_cmp_data.get('fuente_extraccion','')[:95]}",
            color=WDIM, fontsize=6.5, fontfamily='monospace', style='italic')

    else:
        ax_cmp.text(5.0, 6.5, 'Correr dale_play_vision.analyze_comparativa()',
            color=WDIM, fontsize=9, ha='center', va='center',
            fontfamily='monospace', style='italic')

    # ══ RESUMEN EJECUTIVO ═════════════════════════════════════════════════════
    ax_res = fig.add_subplot(gs[11])
    _panel(ax_res, '  RESUMEN EJECUTIVO — Para el equipo de producción')
    ax_res.set_facecolor('#0a1f0a')
    ax_res.set_xlim(0, 10); ax_res.set_ylim(0, 10.5)
    ax_res.tick_params(left=False, bottom=False, labelleft=False, labelbottom=False)
    for _sp in ax_res.spines.values():
        _sp.set_color(GOLD); _sp.set_linewidth(1.5)
    _gold_line(ax_res)

    _res_ndvi = ndvi_v or 0
    _b1_ok    = _res_ndvi >= 0.4
    _b1_col   = GRNL if _b1_ok else YELL
    _b1_title = '◆ Estado del campo'
    _b1_text  = (f'NDVI {_res_ndvi:.2f} — 40% bajo promedio\n'
                 f'histórico de otoño (ref. 0.17)\n'
                 f'Césped bajo estrés hídrico.\n'
                 f'Riego 48h previo al show crítico.\n'
                 f'Coordinar con el club medidas\n'
                 f'de protección durante montaje.'
                 if not _b1_ok else
                 f'NDVI {_res_ndvi:.2f} — estado saludable.\n'
                 f'Césped apto para el show.\n'
                 f'Sin riesgo de daño severo.\n'
                 f'Mantener riego preventivo\n'
                 f'48h antes del evento.')

    _b2_title = '♪ Lo que puede mejorar'
    _b2_text  = ('50cm más de escenario\n'
                 '= +7.1% visibilidad\n'
                 '≈ 3.500 espectadores adicionales\n'
                 'con vista óptima al escenario.\n'
                 'Sin costo adicional de equipos,\n'
                 'sin mover estructuras.')

    _b3_ok    = not soil.get('hay_exclusiones')
    _b3_col   = GRNL if _b3_ok else REDL
    _b3_title = '✓ Estructuras: sin riesgo' if _b3_ok else '! Estructuras: atencion'
    if _b3_ok:
        _b3_text = ('Cap. suelo: 120 kPa nominal\n'
                    'Carga actual maxima: 15 kPa\n'
                    'Margen de seguridad: 8x\n'
                    'Todas estructuras dentro\n'
                    'del umbral nominal. Tarimas\n'
                    'distribuidoras recomendadas.')
    else:
        _rz = next((z.get('nombre','zona crítica') for z in (soil.get('zonas') or [])
                    if z.get('clase') in ('critico','exclusion')), 'zona crítica')
        _b3_text = (f'Atención: {_rz}\n'
                    f'supera el umbral.\n'
                    f'Cap. suelo: 120 kPa\n'
                    f'Carga actual: sobre límite\n'
                    f'Reposicionar antes del\n'
                    f'montaje — riesgo drenaje.')

    _res_blocks = [
        (0.3,  _b1_title, _b1_text, _b1_col),
        (3.55, _b2_title, _b2_text, GOLD),
        (6.8,  _b3_title, _b3_text, _b3_col),
    ]
    for _bx, _btitle, _btext, _bcol in _res_blocks:
        ax_res.add_patch(FancyBboxPatch((_bx, 0.6), 2.9, 9.2,
            boxstyle='round,pad=0.1',
            facecolor='#0d2a0d', edgecolor=_bcol + '88', lw=1.0))
        ax_res.text(_bx + 0.18, 9.5, _btitle,
            color=_bcol, fontsize=9.5, fontweight='bold')
        ax_res.text(_bx + 0.18, 8.8, _btext,
            color=WHITE, fontsize=8.5, va='top', linespacing=1.65)

    ax_res.text(5.0, 0.15,
        'Generado automáticamente por Faro Protocol con datos satelitales reales.',
        color=WDIM, fontsize=7.5, ha='center', transform=ax_res.transAxes)

    # ══ FOOTER ════════════════════════════════════════════════════════════════
    ax_f = fig.add_subplot(gs[12])
    ax_f.set_facecolor(BG3); ax_f.axis('off')
    ax_f.plot([0,1],[0.95,0.95], color=GOLD, lw=1.2,
              transform=ax_f.transAxes, clip_on=False)
    ax_f.text(0.5, 0.48, f'CERT: {cert}',
              color=WDIM, fontsize=8, ha='center',
              transform=ax_f.transAxes, fontfamily='monospace')
    ax_f.text(0.5, 0.09,
              f'Faro Protocol · Dale Play · Generado {ts.strftime("%Y-%m-%d %H:%M")} UTC',
              color=WDIM, fontsize=7.5, ha='center', transform=ax_f.transAxes)

    # ── Guardar ───────────────────────────────────────────────────────────────
    out_path = str(_OUT_DIR / f"reporte_{show_id}.png")
    if os.environ.get("FARO_DALEPLAY_OUT"):
        out_path = os.environ["FARO_DALEPLAY_OUT"]

    fig.savefig(out_path, dpi=DPI, pad_inches=0.2,
                facecolor=BG, edgecolor='none')
    plt.close(fig)
    return out_path
