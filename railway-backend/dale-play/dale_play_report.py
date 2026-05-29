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
DPI   = 200

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
        ax.text(0.01, 0.94, title, color=GOLD, fontsize=9, fontweight='bold',
                ha='left', va='center', transform=ax.transAxes,
                fontfamily='monospace')


def _gold_line(ax, y: float = 0.97):
    ax.plot([0, 1], [y, y], color=GOLD, lw=2,
            transform=ax.transAxes, clip_on=False)


def _kpi(ax, x: float, label: str, value: str, color: str, w: float = 0.185):
    ax.add_patch(FancyBboxPatch(
        (x - w/2, 0.08), w, 0.84,
        boxstyle="round,pad=0.008",
        facecolor=color + '18', edgecolor=color + '66', lw=0.9,
        transform=ax.transAxes,
    ))
    ax.text(x, 0.80, label, color=WDIM, fontsize=6.5,
            ha='center', transform=ax.transAxes, fontfamily='monospace')
    ax.text(x, 0.40, value, color=color, fontsize=13, fontweight='bold',
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

    # ── Figura ───────────────────────────────────────────────────────────────
    fig = plt.figure(figsize=(13.5, 30), facecolor=BG)
    gs  = gridspec.GridSpec(8, 1, figure=fig, hspace=0.0,
          height_ratios=[1.0, 1.4, 3.8, 4.0, 4.2, 3.8, 1.8, 0.7])

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
        ("INTEGRIDAD\nEST.", ins_sem.upper()[:7] if insar else "PRE-SHOW",   _sc(ins_sem)),
    ]
    for i, (lbl, val, col) in enumerate(kpis):
        _kpi(ax_k, (i + 0.5) / len(kpis), lbl, val, col)

    # ══ BASELINE SATELITAL ════════════════════════════════════════════════════
    ax_s = fig.add_subplot(gs[2])
    _panel(ax_s, '  BASELINE SATELITAL — Sentinel-2 NDVI  +  Landsat TIRS')
    ax_s.set_facecolor('#060d08')
    ax_s.set_xlim(0,10); ax_s.set_ylim(0,8)
    ax_s.tick_params(left=False, bottom=False, labelleft=False, labelbottom=False)
    _gold_line(ax_s)

    cmap = LinearSegmentedColormap.from_list(
        'ndvi', ['#b71c1c','#ef6c00','#fdd835','#66bb6a','#1b5e20'], N=256)
    ndvi_v2 = ndvi_v if ndvi_v is not None else 0.45
    t  = np.clip((ndvi_v2 - 0.1) / 0.7, 0, 1)
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
    ax_s.text(3.05, 4.0, f'NDVI\n{ndvi_v2:.2f}',
              color=WHITE, fontsize=13, fontweight='bold', ha='center', va='center', zorder=5)
    ax_s.text(3.05, 0.3, ndvi_lbl, color=_sc(ndvi_sem), fontsize=8, ha='center',
              fontfamily='monospace')
    ax_s.text(0.03, 0.87,
              f'Sentinel-2 · {sat.get("ndvi_fecha","—")} · Nube {sat.get("ndvi_cloud_pct","—")}%',
              color=WDIM, fontsize=7.5, transform=ax_s.transAxes)

    # Panel TIRS (derecha)
    tirs     = sat.get("tirs_celsius")
    tirs_col = (REDL if tirs and tirs > 35 else YELL if tirs and tirs > 28 else GRNL)
    ax_s.add_patch(mpatches.Rectangle((6.4, 1.3), 3.2, 5.5,
        facecolor=BG3, edgecolor=BORDER, lw=0.7, zorder=1))
    ax_s.text(8.0, 6.4, 'TEMP. SUPERFICIAL', color=GOLD, fontsize=8,
              fontweight='bold', ha='center', fontfamily='monospace')
    ax_s.text(8.0, 4.3, f"{tirs:.1f}°C" if tirs is not None else "N/D",
              color=tirs_col, fontsize=28, fontweight='bold', ha='center', va='center')
    tirs_lbl = ("Estrés térmico — impacto en pasto" if tirs and tirs > 35
                else "Temperatura moderada" if tirs and tirs > 28
                else "Temperatura normal")
    ax_s.text(8.0, 2.7, tirs_lbl, color=tirs_col, fontsize=7.5,
              ha='center', fontfamily='monospace')
    ax_s.text(8.0, 1.9, f'Landsat · {sat.get("tirs_fecha","—")}',
              color=WDIM, fontsize=7.5, ha='center')

    # ══ PRONÓSTICO CLIMÁTICO 72HS ═════════════════════════════════════════════
    ax_w = fig.add_subplot(gs[3])
    _panel(ax_w, '  PRONÓSTICO CLIMÁTICO 72HS — Riesgo Operativo · Open-Meteo')
    ax_w.set_facecolor(BG2)
    ax_w.set_xlim(0,10); ax_w.set_ylim(0,8)
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
            ax_w.text(xc, 5.0, f'💧 {lluvia:.0f} mm', color=rc, fontsize=9, ha='center')
            rachas = day.get("rachas_max_kmh", 0)
            wc = REDL if rachas >= 65 else YELL if rachas >= 40 else GRNL
            ax_w.text(xc, 3.9, f'💨 {rachas:.0f} km/h', color=wc, fontsize=9, ha='center')
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
    ax_a.set_xlim(0,10); ax_a.set_ylim(0,8)
    ax_a.tick_params(left=False, bottom=False, labelleft=False, labelbottom=False)
    _gold_line(ax_a)

    secs     = acoustic.get("sectores") or []
    ac_alerts = acoustic.get("alertas_globales") or []
    spl_prom = acoustic.get("spl_promedio_db", 0)
    cob_opt  = acoustic.get("cobertura_optima_pct", 0)

    if secs:
        ns = len(secs)
        bw = 8.6 / ns
        for i, sec in enumerate(secs):
            x0  = 0.6 + i * bw
            xc  = x0 + bw / 2
            spl = sec.get("spl_db", 0)
            cob = sec.get("cobertura", "baja")
            col = _sc({"optima":"verde","buena":"verde","aceptable":"amarillo","baja":"rojo"}.get(cob,"sin_datos"))
            bh  = max(0.3, min(5.8, (spl - 80) / 40 * 5.8))
            ax_a.add_patch(mpatches.Rectangle((x0 + 0.1, 1.0), bw - 0.25, bh,
                facecolor=col+'55', edgecolor=col, lw=0.8))
            ax_a.text(xc, 7.5, sec.get("name","").replace(" ","\n"),
                      color=WHITE, fontsize=7, ha='center', va='top')
            ax_a.text(xc, 1.0 + bh + 0.25, f'{spl:.0f}dB',
                      color=col, fontsize=7.5, ha='center', fontweight='bold')
            sl = sec.get("sightline","")
            sl_col = {"optima":GRNL,"buena":GRNL,"parcial":YELL,"obstruida":REDL}.get(sl,WDIM)
            ax_a.text(xc, 0.10, sl[:3].upper(), color=sl_col, fontsize=7,
                      ha='center', fontfamily='monospace')

    ax_a.text(9.85, 7.6, f'SPL prom: {spl_prom:.0f} dB',
              color=WDIM, fontsize=8, ha='right', fontfamily='monospace')
    ax_a.text(9.85, 7.1, f'Cobertura óptima: {cob_opt:.0f}%',
              color=_sc("verde" if cob_opt>=60 else "amarillo" if cob_opt>=40 else "rojo"),
              fontsize=8, ha='right', fontfamily='monospace')
    for j, a in enumerate(ac_alerts[:2]):
        ax_a.text(0.02, 0.13 - j*0.10, f'• {a[:80]}',
                  color=YELL, fontsize=6.5, transform=ax_a.transAxes)

    # ══ MAPA CARGA DEL SUELO ══════════════════════════════════════════════════
    ax_sl = fig.add_subplot(gs[5])
    _panel(ax_sl, '  MAPA DE CARGA DEL SUELO — Zonas Seguras / Exclusión')
    ax_sl.set_facecolor(BG2)
    ax_sl.set_xlim(0,10); ax_sl.set_ylim(0,8)
    ax_sl.tick_params(left=False, bottom=False, labelleft=False, labelbottom=False)
    _gold_line(ax_sl)

    zonas     = soil.get("zonas")  or []
    al_soil   = soil.get("alertas") or []
    cap_kpa   = soil.get("capacidad_efectiva_kpa", 120)
    cond_s    = soil.get("condicion_suelo", "normal")
    cond_col  = {"normal":GRNL,"húmedo":YELL,"saturado":REDL}.get(cond_s, WDIM)

    ax_sl.text(9.85, 7.6, f'Suelo: {cond_s.upper()}  ({cap_kpa} kPa)',
               color=cond_col, fontsize=8.5, ha='right',
               fontweight='bold', fontfamily='monospace')

    nz = min(len(zonas), 5) or 1
    cw2 = 9.2 / nz
    for i, z in enumerate(zonas[:5]):
        x0  = 0.3 + i * cw2
        xc  = x0 + cw2 / 2
        zcol = z.get("color", GRNL)
        ax_sl.add_patch(FancyBboxPatch((x0 + 0.1, 0.2), cw2 - 0.25, 7.0,
            boxstyle="round,pad=0.1",
            facecolor=zcol+'18', edgecolor=zcol+'77', lw=1.0))
        ax_sl.text(xc, 6.8, z.get("nombre","")[:16], color=WHITE,
                   fontsize=7.5, ha='center', fontweight='bold')
        ax_sl.text(xc, 5.7, f'{z.get("carga_ton",0):.0f} t',
                   color=WDIM, fontsize=8, ha='center')
        ax_sl.text(xc, 4.7, f'{z.get("presion_kpa",0):.0f} kPa',
                   color=WHITE, fontsize=12, fontweight='bold', ha='center')
        ax_sl.text(xc, 3.6, z.get("clase","").upper(),
                   color=zcol, fontsize=8, fontweight='bold',
                   ha='center', fontfamily='monospace')
        ax_sl.text(xc, 1.7, z.get("label","")[:28],
                   color=WDIM, fontsize=6.5, ha='center')
    for j, a in enumerate(al_soil[:2]):
        ax_sl.text(0.02, 0.13 - j*0.07, f'• {a[:108]}',
                   color=YELL, fontsize=6.5, transform=ax_sl.transAxes)

    # ══ InSAR ESTRUCTURAL ═════════════════════════════════════════════════════
    ax_i = fig.add_subplot(gs[6])
    _panel(ax_i, '  INTEGRIDAD ESTRUCTURAL — Sentinel-1 InSAR Post-Show · Tribunas')
    ax_i.set_facecolor(BG2)
    ax_i.set_xlim(0,10); ax_i.set_ylim(0,8)
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

    # ══ FOOTER ════════════════════════════════════════════════════════════════
    ax_f = fig.add_subplot(gs[7])
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

    fig.savefig(out_path, dpi=DPI, bbox_inches='tight',
                facecolor=BG, edgecolor='none')
    plt.close(fig)
    return out_path
