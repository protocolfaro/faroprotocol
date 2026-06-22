"""
render_reports.py — Self-contained headless PNG generator for Vélez Sarsfield.
Reads velez/velez_data.json, outputs 7 PNG files to reportes_velez/.
Replaces all gen_velez_*.py scripts. Uses matplotlib Agg (no display needed).

Run standalone:  MPLBACKEND=Agg python railway-backend/render_reports.py
From scheduler:  from render_reports import render_all; render_all(vd, out_dir)
"""
from __future__ import annotations
import hashlib, json, logging, os, sys
from datetime import datetime
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.gridspec as gridspec
import matplotlib.patches as mpatches
from matplotlib.patches import FancyBboxPatch
import numpy as np

log = logging.getLogger(__name__)

# ── Palette (same as legacy gen_velez_*.py) ───────────────────────────────────
BG    = "#06080b"
BG2   = "#0d1117"
BG3   = "#141c24"
GOLD  = "#c9a84c"
GOLDL = "#e2c97e"
WHITE = "#f2ede4"
WDIM  = "#9aa0a8"
REDL  = "#e74c3c"
YELL  = "#f0b429"
GRNL  = "#27ae60"
BLUE  = "#4a9ede"
BORDER= "#1e2a38"
DPI   = 150

SEM_COL = {"verde": GRNL, "amarillo": YELL, "rojo": REDL}


def _cert(tag: str) -> str:
    return hashlib.sha256(f"FARO-VLZ-{tag}-{datetime.now().isoformat()}".encode()).hexdigest()[:20].upper()


def _setup_fig(w: float = 12, h: float = 9) -> tuple:
    fig = plt.figure(figsize=(w, h), facecolor=BG)
    return fig


def _header(fig, title: str, subtitle: str = "", w: float = 12):
    ax = fig.add_axes([0, 0.94, 1, 0.06])
    ax.set_facecolor(BG3)
    ax.set_xlim(0, 1); ax.set_ylim(0, 1)
    ax.axis("off")
    ax.axhline(0, color=GOLD, lw=1.5)
    ax.text(0.015, 0.55, "FARO PROTOCOL", color=GOLD, fontsize=9, fontweight="bold",
            fontfamily="monospace", va="center")
    ax.text(0.015, 0.1, subtitle or datetime.now().strftime("Semana del %d de %B de %Y"),
            color=WDIM, fontsize=7, fontfamily="monospace", va="center")
    ax.text(0.5, 0.5, title, color=WHITE, fontsize=11, fontweight="bold",
            ha="center", va="center")
    cert = _cert(title[:8])
    ax.text(0.985, 0.5, f"#{cert}", color=WDIM, fontsize=6,
            fontfamily="monospace", ha="right", va="center")


def _footer(fig, source: str):
    ax = fig.add_axes([0, 0, 1, 0.035])
    ax.set_facecolor(BG3)
    ax.set_xlim(0, 1); ax.set_ylim(0, 1)
    ax.axis("off")
    ax.axhline(1, color=BORDER, lw=0.5)
    ax.text(0.015, 0.5, source, color=WDIM, fontsize=6, fontfamily="monospace", va="center")
    ax.text(0.985, 0.5, f"© Faro Protocol {datetime.now().year}",
            color=WDIM, fontsize=6, fontfamily="monospace", ha="right", va="center")


def _sem_badge(ax, x, y, sem: str, label: str, size: int = 8):
    col = SEM_COL.get(sem, YELL)
    ax.text(x, y, f"● {label}", color=col, fontsize=size,
            fontfamily="monospace", ha="center", va="center", fontweight="bold")


def _score_bar(ax, x0, y0, w, h, score: float, sem: str):
    col = SEM_COL.get(sem, YELL)
    ax.add_patch(FancyBboxPatch((x0, y0), w, h, boxstyle="round,pad=0",
                                facecolor=BG3, edgecolor=BORDER, lw=0.5))
    fill_w = w * min(max(score / 100, 0), 1)
    ax.add_patch(FancyBboxPatch((x0, y0), fill_w, h, boxstyle="round,pad=0",
                                facecolor=col, alpha=0.35, edgecolor="none"))
    ax.text(x0 + w / 2, y0 + h / 2, f"{score:.0f}", color=col,
            fontsize=8, fontweight="bold", ha="center", va="center")


# ── Report 1: canchero.png ─────────────────────────────────────────────────────

def _render_canchero(vd: dict, out: Path):
    wl      = vd.get("weather_live", {})
    gndvi_d = wl.get("gndvi_por_cancha", {})
    canchas = gndvi_d.get("canchas", {})
    roger   = vd.get("usuarios", {}).get("roger", {})
    cancha_list = vd.get("sectores", {}).get("canchero", {}).get("canchas", [])

    # Build unified cancha data
    ALL_IDS = ["1fa","2fa","3fa","4fa","5fa","6fa","7fa","8fa","9fa","10fa","1fp","2fp"]
    CANCHA_NAMES = {
        "1fa":"1FA","2fa":"2FA","3fa":"3FA","4fa":"4FA","5fa":"5FA","6fa":"6FA",
        "7fa":"7FA","8fa":"8FA","9fa":"9FA","10fa":"10FA","1fp":"1FP","2fp":"2FP"
    }
    rows_data = []
    for cid in ALL_IDS:
        cd  = canchas.get(cid, {})
        # Use ndvi when available; fall back to gndvi (GNDVI correlates well with NDVI)
        ndvi = cd.get("ndvi") or cd.get("gndvi")
        n_st = cd.get("n_status", "")
        # Find enriched data from cancha_list
        enr = next((c for c in cancha_list if c.get("id") == cid), {})
        score = enr.get("score", 0)
        sem   = enr.get("sem", "amarillo" if ndvi and ndvi > 0.30 else "rojo") if ndvi else "amarillo"
        if ndvi:
            sem = "verde" if ndvi >= 0.45 else ("rojo" if ndvi < 0.30 else "amarillo")
        rows_data.append({"id": cid, "name": CANCHA_NAMES[cid], "ndvi": ndvi, "score": score,
                          "sem": sem, "n_status": n_st})

    fig = _setup_fig(12, 11)
    _header(fig, "VILLA OLÍMPICA — Estado de Canchas",
            gndvi_d.get("fuente", "Sentinel-2 · Faro Protocol"))

    # Main grid area
    ax_main = fig.add_axes([0.01, 0.07, 0.98, 0.86])
    ax_main.set_facecolor(BG)
    ax_main.set_xlim(0, 1); ax_main.set_ylim(0, 1)
    ax_main.axis("off")

    # 4 columns × 3 rows of cancha cards
    cols, rows = 4, 3
    cw, ch = 0.23, 0.28
    pad_x, pad_y = 0.02, 0.025
    start_x, start_y = 0.01, 0.72

    for i, cd in enumerate(rows_data):
        col_i = i % cols
        row_i = i // cols
        x0 = start_x + col_i * (cw + pad_x)
        y0 = start_y - row_i * (ch + pad_y)
        col = SEM_COL.get(cd["sem"], YELL)

        # Card background
        ax_main.add_patch(FancyBboxPatch((x0, y0), cw, ch,
                                         boxstyle="round,pad=0.01",
                                         facecolor=BG3, edgecolor=col, lw=1.2,
                                         alpha=0.9))
        # Name
        ax_main.text(x0 + cw/2, y0 + ch - 0.025, cd["name"],
                     color=WHITE, fontsize=11, fontweight="bold",
                     ha="center", va="top", fontfamily="monospace")
        # NDVI
        ndvi_str = f"{cd['ndvi']:.3f}" if cd["ndvi"] is not None else "N/D"
        ax_main.text(x0 + cw/2, y0 + ch*0.62, f"NDVI {ndvi_str}",
                     color=col, fontsize=13, fontweight="bold",
                     ha="center", va="center")
        # Score bar
        if cd["score"]:
            bx = x0 + 0.02; bw = cw - 0.04; bh = 0.022
            by = y0 + ch*0.38
            ax_main.add_patch(FancyBboxPatch((bx, by), bw, bh, boxstyle="round,pad=0",
                                              facecolor=BG2, edgecolor=BORDER, lw=0.4))
            ax_main.add_patch(FancyBboxPatch((bx, by), bw * min(cd["score"]/100, 1), bh,
                                              boxstyle="round,pad=0",
                                              facecolor=col, alpha=0.4, edgecolor="none"))
            ax_main.text(bx + bw/2, by + bh/2, f"Score {cd['score']}/100",
                         color=WHITE, fontsize=7, ha="center", va="center",
                         fontfamily="monospace")
        # N status badge
        if cd["n_status"]:
            n_col = {"grave": REDL, "bajo": YELL, "borderline": YELL, "ok": GRNL}.get(cd["n_status"], WDIM)
            n_lbl = {"grave": "N CRÍTICO", "bajo": "N BAJO", "borderline": "N LIMITE", "ok": "N OK"}.get(cd["n_status"], cd["n_status"])
            ax_main.text(x0 + cw/2, y0 + 0.04, n_lbl,
                         color=n_col, fontsize=7, fontweight="bold",
                         ha="center", va="center", fontfamily="monospace")

    # NDVI bar chart at bottom
    ax_bar = fig.add_axes([0.07, 0.04, 0.88, 0.10])
    ax_bar.set_facecolor(BG2)
    for sp in ax_bar.spines.values():
        sp.set_color(BORDER)
    ids = [cd["id"] for cd in rows_data]
    ndvis = [cd["ndvi"] if cd["ndvi"] is not None else 0 for cd in rows_data]
    colors = [SEM_COL.get(cd["sem"], YELL) for cd in rows_data]
    bars = ax_bar.bar(range(len(ids)), ndvis, color=colors, alpha=0.75, width=0.7)
    ax_bar.set_xticks(range(len(ids)))
    ax_bar.set_xticklabels([c.upper() for c in ids], fontsize=6,
                            color=WDIM, fontfamily="monospace")
    ax_bar.set_yticks([0, 0.25, 0.45, 0.70])
    ax_bar.tick_params(axis="y", colors=WDIM, labelsize=6)
    ax_bar.axhline(0.45, color=GRNL, lw=0.5, ls="--", alpha=0.5)
    ax_bar.axhline(0.30, color=REDL, lw=0.5, ls="--", alpha=0.5)
    ax_bar.set_facecolor(BG2)
    ax_bar.set_title("NDVI por cancha", color=GOLD, fontsize=7,
                     fontfamily="monospace", loc="left", pad=3)

    _footer(fig, gndvi_d.get("fuente", "Sentinel-2 · Planetary Computer · Faro Protocol"))
    fig.savefig(out, dpi=DPI, bbox_inches="tight", facecolor=BG)
    plt.close(fig)
    log.info("render: %s", out.name)


# ── Report 2: velez.png (Estadio Amalfitani + resumen ejecutivo) ───────────────

def _render_velez(vd: dict, out: Path):
    sectores = vd.get("sectores", {})
    wl       = vd.get("weather_live", {})
    fecha    = datetime.now().strftime("%d/%m/%Y")

    SECTOR_KEYS = ["estadio", "canchero", "solar", "poli", "piletas", "sede", "agro"]
    SECTOR_NAMES = {
        "estadio": "Estadio Amalfitani", "canchero": "Villa Olímpica",
        "solar": "Solar", "poli": "Polideportivo",
        "piletas": "Complejo Acuático", "sede": "Sede Central", "agro": "Área Agronómica",
    }

    fig = _setup_fig(12, 9)
    _header(fig, "VÉLEZ SARSFIELD — Resumen Ejecutivo Semanal",
            f"Dashboard completo · {fecha}")

    # Score grid
    ax = fig.add_axes([0.02, 0.10, 0.96, 0.82])
    ax.set_facecolor(BG)
    ax.set_xlim(0, 1); ax.set_ylim(0, 1)
    ax.axis("off")

    # KPI row top
    meta = vd.get("meta", {})
    hist = meta.get("historial_global", [])
    if hist:
        n = len(hist)
        ax.text(0.5, 0.94, "HISTORIAL SCORE GLOBAL", color=GOLD, fontsize=8,
                fontweight="bold", ha="center", fontfamily="monospace")
        for j, h in enumerate(hist):
            px = 0.15 + j * (0.72 / max(n-1, 1))
            col = SEM_COL.get(h.get("sem", "amarillo"), YELL)
            is_last = j == n-1
            ax.add_patch(plt.Circle((px, 0.86), 0.025 if is_last else 0.018,
                                    color=col, alpha=0.9 if is_last else 0.5))
            ax.text(px, 0.86, str(h.get("score", "")),
                    color=WHITE if is_last else WDIM,
                    fontsize=7 if is_last else 6,
                    ha="center", va="center", fontweight="bold" if is_last else "normal")
            ax.text(px, 0.83, h.get("semana", ""), color=WDIM, fontsize=5,
                    ha="center", fontfamily="monospace")
        # Connect dots
        xs = [0.15 + j*(0.72/max(n-1,1)) for j in range(n)]
        ys_s = [h.get("score", 0) for h in hist]
        ys_norm = [(s-min(ys_s))/(max(ys_s)-min(ys_s)+1)*0.04+0.84 for s in ys_s]

    # Sector cards
    cols2 = 4
    cw2 = 0.22; ch2 = 0.28; pad2 = 0.02
    items = [(k, sectores.get(k, {})) for k in SECTOR_KEYS if sectores.get(k)]
    for i, (key, s) in enumerate(items):
        col_i = i % cols2; row_i = i // cols2
        x0 = 0.02 + col_i*(cw2+pad2)
        y0 = 0.62 - row_i*(ch2+0.03)
        col = SEM_COL.get(s.get("sem","amarillo"), YELL)
        score = s.get("score", 0); score_p = s.get("score_prev", score)
        trend = "▲" if score > score_p else ("▼" if score < score_p else "—")
        t_col = GRNL if score > score_p else (REDL if score < score_p else WDIM)

        ax.add_patch(FancyBboxPatch((x0, y0), cw2, ch2, boxstyle="round,pad=0.01",
                                    facecolor=BG3, edgecolor=col, lw=1.0, alpha=0.85))
        ax.text(x0+cw2/2, y0+ch2-0.02, SECTOR_NAMES.get(key, key).upper(),
                color=WDIM, fontsize=6.5, fontweight="bold",
                ha="center", va="top", fontfamily="monospace")
        # Score
        ax.text(x0+cw2/2, y0+ch2*0.55, f"{score}", color=col, fontsize=22,
                fontweight="bold", ha="center", va="center")
        ax.text(x0+cw2/2, y0+ch2*0.35, "/100", color=WDIM, fontsize=8,
                ha="center", va="center")
        # Trend
        ax.text(x0+cw2-0.015, y0+ch2-0.02, f"{trend}{abs(score-score_p):.0f}",
                color=t_col, fontsize=8, ha="right", va="top", fontweight="bold")
        # Status label
        lbl = {"verde":"ÓPTIMO","amarillo":"ATENCIÓN","rojo":"CRÍTICO"}.get(s.get("sem",""), "")
        ax.text(x0+cw2/2, y0+0.022, lbl, color=col, fontsize=7,
                ha="center", va="center", fontfamily="monospace", fontweight="bold")

    # Weather strip
    y_strip = 0.10
    strip_items = [
        ("ET₀", f'{wl.get("et0_mm_dia","—")} mm/día'),
        ("Riego min", f'{wl.get("riego_min_sector","—")} min'),
        ("Precipit.", f'{wl.get("precipitacion_semana_mm","—")} mm/sem'),
        ("Humedad", f'{wl.get("humedad_suelo_pct","—")}%'),
        ("GDD 7d", f'{wl.get("gdd_acumulado_7d","—")}'),
    ]
    for j, (lbl, val) in enumerate(strip_items):
        px = 0.05 + j * 0.19
        ax.add_patch(FancyBboxPatch((px, y_strip), 0.17, 0.09, boxstyle="round,pad=0.01",
                                    facecolor=BG3, edgecolor=BORDER, lw=0.5))
        ax.text(px+0.085, y_strip+0.065, lbl, color=GOLD, fontsize=7,
                ha="center", va="center", fontfamily="monospace", fontweight="bold")
        ax.text(px+0.085, y_strip+0.025, val, color=WHITE, fontsize=9,
                ha="center", va="center", fontweight="bold")

    _footer(fig, f"Faro Protocol · NASA POWER / SoilGrids / Sentinel-2 · {fecha}")
    fig.savefig(out, dpi=DPI, bbox_inches="tight", facecolor=BG)
    plt.close(fig)
    log.info("render: %s", out.name)


# ── Report 3: agro_FINAL.png (Agronomía detallada) ────────────────────────────

def _render_agro(vd: dict, out: Path):
    wl      = vd.get("weather_live", {})
    gndvi_d = wl.get("gndvi_por_cancha", {})
    canchas = gndvi_d.get("canchas", {})
    fecha   = datetime.now().strftime("%d/%m/%Y")

    ids  = sorted(canchas.keys())
    ndvi = [canchas[c].get("ndvi", 0) or 0 for c in ids]
    gnvi = [canchas[c].get("gndvi", 0) or 0 for c in ids]
    cols = [GRNL if v >= 0.45 else (REDL if v < 0.30 else YELL) for v in ndvi]

    fig = _setup_fig(12, 10)
    _header(fig, "ANÁLISIS AGRONÓMICO — Villa Olímpica", f"Sentinel-2 · {fecha}")

    # NDVI bars
    ax1 = fig.add_axes([0.06, 0.52, 0.55, 0.38])
    ax1.set_facecolor(BG2)
    for sp in ax1.spines.values(): sp.set_color(BORDER)
    x_pos = np.arange(len(ids))
    ax1.bar(x_pos - 0.2, ndvi, 0.35, color=cols, alpha=0.8, label="NDVI")
    ax1.bar(x_pos + 0.2, gnvi, 0.35, color=[BLUE]*len(ids), alpha=0.6, label="GNDVI")
    ax1.axhline(0.45, color=GRNL, lw=0.6, ls="--", alpha=0.6, label="Umbral sano")
    ax1.axhline(0.30, color=REDL, lw=0.6, ls="--", alpha=0.6, label="Umbral crítico")
    ax1.set_xticks(x_pos)
    ax1.set_xticklabels([c.upper() for c in ids], fontsize=7, color=WDIM)
    ax1.tick_params(axis="y", colors=WDIM, labelsize=7)
    ax1.set_facecolor(BG2)
    ax1.legend(fontsize=6, facecolor=BG3, labelcolor=WDIM, framealpha=0.8)
    ax1.set_title("NDVI / GNDVI por Cancha", color=GOLD, fontsize=8,
                  fontfamily="monospace", loc="left", pad=4)
    ax1.set_ylim(0, 1)

    # N status table
    ax2 = fig.add_axes([0.63, 0.52, 0.35, 0.38])
    ax2.set_facecolor(BG2)
    ax2.axis("off")
    ax2.set_title("Estado Nitrógeno", color=GOLD, fontsize=8,
                  fontfamily="monospace", loc="left")
    n_labels = {"grave":"N CRÍTICO","bajo":"N BAJO","borderline":"N LÍMITE","ok":"N OK"}
    n_colors = {"grave":REDL,"bajo":YELL,"borderline":YELL,"ok":GRNL}
    for i, cid in enumerate(ids):
        n_st = canchas[cid].get("n_status", "")
        n_rec = canchas[cid].get("n_rec", "")[:35]
        col = n_colors.get(n_st, WDIM)
        y = 0.92 - i * (0.85/max(len(ids),1))
        ax2.text(0.0, y, f"{cid.upper()}", color=WHITE, fontsize=7,
                 fontweight="bold", fontfamily="monospace")
        ax2.text(0.28, y, n_labels.get(n_st, n_st), color=col, fontsize=7, fontweight="bold")
        ax2.text(0.0, y-0.04, n_rec, color=WDIM, fontsize=5.5)

    # Weather/soil strip
    ax3 = fig.add_axes([0.06, 0.38, 0.88, 0.11])
    ax3.set_facecolor(BG3)
    ax3.axis("off")
    items = [
        ("Humedad suelo",    f'{wl.get("humedad_suelo_pct","—")}% ({wl.get("humedad_suelo_estado","—")})'),
        ("ET₀ / día",        f'{wl.get("et0_mm_dia","—")} mm'),
        ("Déficit hídrico",  f'{wl.get("deficit_hidrico_mm","—")} mm'),
        ("Riego mín.",       f'{wl.get("riego_min_sector","—")} min/sector'),
        ("GDD acum. 7d",     f'{wl.get("gdd_acumulado_7d","—")}'),
        ("Próximo corte",    f'{wl.get("dias_proximo_corte","—")} días'),
    ]
    for j, (lbl, val) in enumerate(items):
        px = 0.01 + j*0.165
        ax3.text(px, 0.75, lbl, color=GOLD, fontsize=7, fontfamily="monospace", fontweight="bold")
        ax3.text(px, 0.3, val, color=WHITE, fontsize=8.5, fontweight="bold")

    # Satellite info
    ax4 = fig.add_axes([0.06, 0.08, 0.88, 0.27])
    ax4.set_facecolor(BG2)
    for sp in ax4.spines.values(): sp.set_color(BORDER)
    ax4.axis("off")
    ax4.set_title("Prescripciones por Cancha", color=GOLD, fontsize=8,
                  fontfamily="monospace", loc="left")
    CRIT = [c for c in ids if (canchas[c].get("ndvi") or 1) < 0.30]  # sat-fallback: ok — predicado filtro, 1 excluye cancha sin dato de lista CRIT
    WARN = [c for c in ids if 0.30 <= (canchas[c].get("ndvi") or 1) < 0.45]  # sat-fallback: ok — predicado filtro, 1 excluye cancha sin dato de lista WARN
    OK   = [c for c in ids if (canchas[c].get("ndvi") or 0) >= 0.45]  # sat-fallback: ok — predicado filtro, 0 excluye cancha sin dato de lista OK
    y_t = 0.88
    for grp, gids, gcol, glbl in [(CRIT, CRIT, REDL, "URGENTE"), (WARN, WARN, YELL, "ATENCIÓN"), (OK, OK, GRNL, "ÓPTIMO")]:
        if gids:
            ax4.text(0.01, y_t, f"[{glbl}] {', '.join(c.upper() for c in gids)}",
                     color=gcol, fontsize=7.5, fontweight="bold", fontfamily="monospace")
            y_t -= 0.14
            if glbl == "URGENTE":
                ax4.text(0.04, y_t, "Fertilizar URGENTE 20 kg N/ha · fungicida sistémico · reducir carga de uso",
                         color=WDIM, fontsize=6.5)
            elif glbl == "ATENCIÓN":
                ax4.text(0.04, y_t, "Fertilización preventiva recomendable · monitorear humedad",
                         color=WDIM, fontsize=6.5)
            y_t -= 0.16

    src = gndvi_d.get("fuente", "Sentinel-2 · Planetary Computer")
    _footer(fig, f"{src} · {fecha}")
    fig.savefig(out, dpi=DPI, bbox_inches="tight", facecolor=BG)
    plt.close(fig)
    log.info("render: %s", out.name)


# ── Report 4: solar_v2.png ─────────────────────────────────────────────────────

def _render_solar(vd: dict, out: Path):
    solar_s = vd.get("sectores", {}).get("solar", {})
    wl      = vd.get("weather_live", {})
    fecha   = datetime.now().strftime("%d/%m/%Y")
    eff     = solar_s.get("score", 71)
    col     = SEM_COL.get(solar_s.get("sem","amarillo"), YELL)

    fig = _setup_fig(12, 8)
    _header(fig, "SISTEMA SOLAR AMALFITANI", f"Exposición solar · {fecha}")

    ax = fig.add_axes([0.02, 0.08, 0.96, 0.84])
    ax.set_facecolor(BG); ax.axis("off")
    ax.set_xlim(0, 1); ax.set_ylim(0, 1)

    # Efficiency gauge (donut)
    ax_d = fig.add_axes([0.08, 0.25, 0.32, 0.55])
    ax_d.set_facecolor(BG); ax_d.axis("off")
    ax_d.set_aspect("equal")
    theta1 = 90; theta2 = 90 - eff*3.6
    ax_d.add_patch(mpatches.Wedge((0.5,0.5), 0.42, theta2, theta1, width=0.12,
                                   facecolor=col, alpha=0.85))
    ax_d.add_patch(mpatches.Wedge((0.5,0.5), 0.42, theta1, theta2+360, width=0.12,
                                   facecolor=BG3, alpha=0.5))
    ax_d.text(0.5, 0.55, f"{eff}%", color=col, fontsize=24, fontweight="bold",
              ha="center", va="center")
    ax_d.text(0.5, 0.38, "EFICIENCIA", color=WDIM, fontsize=8,
              ha="center", fontfamily="monospace")
    ax_d.set_xlim(0,1); ax_d.set_ylim(0,1)

    # Metrics
    ax.text(0.44, 0.82, "INDICADORES DE RENDIMIENTO", color=GOLD, fontsize=9,
            fontweight="bold", fontfamily="monospace")
    items = [
        ("ET₀ semana", f'{wl.get("et0_semana_mm","—")} mm'),
        ("Riego óptimo", wl.get("hora_riego_optima","—")),
        ("Corte óptimo", wl.get("hora_corte_optima","—")),
        ("Suelo tipo", wl.get("suelo_tipo","—")),
        ("WHC suelo", f'{wl.get("suelo_whc_mm","—")} mm'),
        ("Estado solar", solar_s.get("detalle","—")[:40]),
    ]
    for j, (lbl, val) in enumerate(items):
        row_y = 0.72 - j*0.11
        ax.add_patch(FancyBboxPatch((0.44, row_y-0.04), 0.52, 0.09,
                                    boxstyle="round,pad=0.01",
                                    facecolor=BG3, edgecolor=BORDER, lw=0.4))
        ax.text(0.46, row_y+0.015, lbl, color=GOLD, fontsize=7.5, fontfamily="monospace")
        ax.text(0.83, row_y+0.015, val, color=WHITE, fontsize=8,
                fontweight="bold", ha="right")

    _footer(fig, f"NASA POWER / pvlib · Faro Protocol · {fecha}")
    fig.savefig(out, dpi=DPI, bbox_inches="tight", facecolor=BG)
    plt.close(fig)
    log.info("render: %s", out.name)


# ── Generic sector report (poli / piletas / sede) ─────────────────────────────

def _render_sector(vd: dict, out: Path, key: str, title: str, icon: str = ""):
    s    = vd.get("sectores", {}).get(key, {})
    fecha = datetime.now().strftime("%d/%m/%Y")
    score = s.get("score", 0)
    sem   = s.get("sem", "amarillo")
    col   = SEM_COL.get(sem, YELL)

    fig = _setup_fig(12, 7)
    _header(fig, f"{icon} {title}".strip(), f"Faro Protocol · {fecha}")

    ax = fig.add_axes([0.05, 0.10, 0.90, 0.82])
    ax.set_facecolor(BG); ax.axis("off")
    ax.set_xlim(0, 1); ax.set_ylim(0, 1)

    # Big score
    ax.text(0.5, 0.72, f"{score}", color=col, fontsize=60, fontweight="bold",
            ha="center", va="center")
    ax.text(0.5, 0.54, "/100", color=WDIM, fontsize=18, ha="center", va="center")
    lbl_map = {"verde":"ÓPTIMO","amarillo":"ATENCIÓN","rojo":"CRÍTICO"}
    ax.text(0.5, 0.44, lbl_map.get(sem, sem), color=col, fontsize=14,
            fontweight="bold", ha="center", fontfamily="monospace")

    # Score bar
    ax.add_patch(FancyBboxPatch((0.15, 0.34), 0.70, 0.042, boxstyle="round,pad=0",
                                facecolor=BG3, edgecolor=BORDER, lw=0.5))
    ax.add_patch(FancyBboxPatch((0.15, 0.34), 0.70*score/100, 0.042,
                                boxstyle="round,pad=0", facecolor=col, alpha=0.5, edgecolor="none"))

    # Detail
    det = s.get("detalle", "")
    ax.text(0.5, 0.25, det, color=WHITE, fontsize=9, ha="center", va="center")

    # Score comparison
    sp = s.get("score_prev", score)
    delta = score - sp
    d_str = f"{'▲' if delta>0 else '▼' if delta<0 else '—'} {abs(delta):.0f} vs semana anterior"
    d_col = GRNL if delta > 0 else (REDL if delta < 0 else WDIM)
    ax.text(0.5, 0.16, d_str, color=d_col, fontsize=10, ha="center", fontweight="bold")

    _footer(fig, f"InSAR Sentinel-1 / Sentinel-2 · Faro Protocol · {fecha}")
    fig.savefig(out, dpi=DPI, bbox_inches="tight", facecolor=BG)
    plt.close(fig)
    log.info("render: %s", out.name)


# ── Public API ─────────────────────────────────────────────────────────────────

def render_all(vd: dict, out_dir: Path) -> list[Path]:
    """Generate all 7 PNG reports. Returns list of files written."""
    out_dir.mkdir(parents=True, exist_ok=True)
    outputs = []
    tasks = [
        (_render_canchero, "faro_reporte_velez_canchero.png"),
        (_render_velez,    "faro_reporte_velez.png"),
        (_render_agro,     "faro_reporte_velez_agro_FINAL.png"),
        (_render_solar,    "faro_reporte_velez_solar_v2.png"),
    ]
    for fn, fname in tasks:
        p = out_dir / fname
        try:
            fn(vd, p)
            outputs.append(p)
        except Exception as e:
            log.error("render %s failed: %s", fname, e)

    for key, fname, title, icon in [
        ("poli",      "faro_reporte_velez_poli.png",      "Polideportivo Feijoo",             ""),
        ("piletas",   "faro_reporte_velez_piletas.png",   "Complejo Acuatico",                ""),
        ("sede",      "faro_reporte_velez_sede.png",      "Sede Central",                     ""),
        ("instituto", "faro_reporte_velez_instituto.png", "Instituto Velez - Infanto Juvenil", ""),
    ]:
        p = out_dir / fname
        try:
            _render_sector(vd, p, key, title, icon)
            outputs.append(p)
        except Exception as e:
            log.error("render %s failed: %s", fname, e)

    log.info("render_all: %d/%d PNG generados en %s", len(outputs), 8, out_dir)
    return outputs


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    root    = Path(__file__).parents[4]   # repo root (sports/clients/velez → up 4 levels)
    vd_path = root / "velez" / "velez_data.json"
    out_dir = root / "reportes_velez"
    vd      = json.loads(vd_path.read_text(encoding="utf-8"))
    files   = render_all(vd, out_dir)
    print(f"OK {len(files)}/7 PNG generados en {out_dir}")
