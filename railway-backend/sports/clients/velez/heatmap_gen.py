"""
heatmap_gen.py — 800×520px PNGs with NDVI + IPOS overlay
Faro Protocol · Vélez Sarsfield
"""
from __future__ import annotations
import hashlib, io
from datetime import datetime, timezone

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.patches as mpatches
import matplotlib.pyplot as plt
from matplotlib.colors import LinearSegmentedColormap
from matplotlib.figure import Figure
from matplotlib.backends.backend_agg import FigureCanvasAgg
from scipy.ndimage import zoom as _zoom, gaussian_filter
from PIL import Image

try:
    import qrcode
    HAS_QR = True
except ImportError:
    HAS_QR = False

W_PX, H_PX, DPI = 800, 520, 100
QR_URL = "https://protocolfaro.github.io/faro-paneles/velez/verify"
BG = "#0d1117"
WHITE = "#ffffff"
SEM_COL = {"verde":"#27ae60","amarillo":"#f0b429","rojo":"#e74c3c"}

DIMS = {
    "1fa":(105,68),"2fa":(105,68),"3fa":(105,68),"4fa":(105,68),
    "5fa":(105,70),"6fa":(105,70),"7fa":(105,70),"8fa":(105,70),
    "9fa":(105,70),"10fa":(105,70),"1fp":(105,68),"2fp":(105,68),
    "amalfitani": (105, 68),
    "poli_f11":   (105, 68),
    "poli_f8a":   (62,  44),
    "poli_f8b":   (62,  44),
    "poli_hockey":(91,  55),
}

# Colormap calibrado para turfgrass profesional: 0.35 (crítico) → 0.90 (óptimo)
# Normalizado [0,1] donde 0=NDVI 0.35, 1=NDVI 0.90
# Breakpoints según FIFA Quality Programme + Burbrink & Straw 2023 + SkimTurf thresholds
_CMAP = LinearSegmentedColormap.from_list("faro_grass", [
    (0.000, "#7f0000"),  # NDVI 0.35 — suelo expuesto / crítico severo
    (0.136, "#c62828"),  # NDVI 0.42 — crítico
    (0.273, "#ef5350"),  # NDVI 0.50 — estrés grave
    (0.400, "#f9a825"),  # NDVI 0.57 — estrés moderado
    (0.545, "#fdd835"),  # NDVI 0.65 — estrés leve (umbral atención)
    (0.636, "#9ccc65"),  # NDVI 0.70 — aceptable
    (0.727, "#4caf50"),  # NDVI 0.75 — bueno
    (0.864, "#2e7d32"),  # NDVI 0.82 — muy bueno
    (1.000, "#1b5e20"),  # NDVI 0.90 — óptimo FIFA Quality Pro
])
_NDVI_VMIN = 0.0   # 0.0 normalizado = NDVI 0.35 (suelo/crítico)
_NDVI_VMAX = 1.0   # 1.0 normalizado = NDVI 0.90 (óptimo)

# Zonas funcionales del campo (eje longitudinal, basado en Wolski et al. 2023)
# GK degrada más rápido, luego ÁREA, CENTRO es más estable
_ZONE_BREAKS = [0.000, 0.157, 0.381, 0.500, 0.619, 0.843, 1.000]  # fracción de W_m
_ZONE_LABELS = ["GK", "ÁREA", "CEN", "CEN", "ÁREA", "GK"]

def _ndvi_grid(rows, cols, base, seed=42):
    rng = np.random.default_rng(seed)
    g = np.full((rows, cols), base, dtype=np.float32)
    g += 0.012 * np.sin(np.arange(cols) * np.pi / 1.4)
    # edge degradation
    yr = np.minimum(np.arange(rows), rows - 1 - np.arange(rows)) / max(1, rows * 0.18)
    xc = np.minimum(np.arange(cols), cols - 1 - np.arange(cols)) / max(1, cols * 0.18)
    edge = np.clip(np.outer(yr, np.ones(cols)) * np.outer(np.ones(rows), xc), 0, 1)
    g -= 0.04 * (1 - edge)
    g += rng.normal(0, 0.015, (rows, cols)).astype(np.float32)
    return np.clip(g, 0.0, 1.0)

def _usage_overlay(rows, cols, ipos):
    Y, X = np.mgrid[0:rows, 0:cols].astype(np.float32)
    spots = [
        (0.50, 0.50, 0.18, 1.00),
        (0.50, 0.11, 0.10, 0.75),
        (0.50, 0.89, 0.10, 0.75),
        (0.50, 0.05, 0.06, 0.50),
        (0.50, 0.95, 0.06, 0.50),
    ]
    m = np.zeros((rows, cols), np.float32)
    for rf, cf, rad_f, w in spots:
        cr, cc = rf * rows, cf * cols
        rr = max(1.0, rad_f * min(rows, cols))
        m += w * np.exp(-((Y-cr)**2 + (X-cc)**2) / (2*(rr*0.45)**2))
    m = np.clip(m / m.max(), 0, 1)
    intensity = min(1.0, ipos / 350.0)
    return m * 0.40 * intensity

def _lines(ax, W, H):
    kw = dict(color=WHITE, lw=1.3, alpha=0.72)
    ax.plot([0,W,W,0,0],[0,0,H,H,0],**kw)
    ax.plot([W/2,W/2],[0,H],**kw)
    ax.add_patch(mpatches.Circle((W/2,H/2),9.15,fill=False,**kw))
    ax.plot(W/2,H/2,"o",color=WHITE,ms=2.5,alpha=0.72)
    for x0 in [0, W-16.5]:
        ax.add_patch(mpatches.Rectangle((x0,(H-40.32)/2),16.5,40.32,fill=False,**kw))
    for x0 in [0, W-5.5]:
        ax.add_patch(mpatches.Rectangle((x0,(H-18.32)/2),5.5,18.32,fill=False,**kw))
    for xp in [11.0, W-11.0]:
        ax.plot(xp, H/2, "o", color=WHITE, ms=2.5, alpha=0.72)
    for cx,t1,t2 in [(11.0,-53,53),(W-11.0,127,233)]:
        ax.add_patch(mpatches.Arc((cx,H/2),18.3,18.3,theta1=t1,theta2=t2,**kw))
    for cx,cy,t1,t2 in [(0,0,0,90),(W,0,90,180),(0,H,270,360),(W,H,180,270)]:
        ax.add_patch(mpatches.Arc((cx,cy),2,2,theta1=t1,theta2=t2,**kw))

def _lines_f8(ax, W, H):
    """Field markings scaled for Fútbol 8 (≈62×44m)."""
    kw = dict(color=WHITE, lw=1.3, alpha=0.72)
    ax.plot([0,W,W,0,0],[0,0,H,H,0],**kw)
    ax.plot([W/2,W/2],[0,H],**kw)
    ax.add_patch(mpatches.Circle((W/2,H/2), W*0.12, fill=False, **kw))
    ax.plot(W/2, H/2, "o", color=WHITE, ms=2.5, alpha=0.72)
    pa_d, pa_w = W*0.145, H*0.68
    for x0 in [0, W-pa_d]:
        ax.add_patch(mpatches.Rectangle((x0,(H-pa_w)/2),pa_d,pa_w,fill=False,**kw))
    ga_d, ga_w = W*0.055, H*0.36
    for x0 in [0, W-ga_d]:
        ax.add_patch(mpatches.Rectangle((x0,(H-ga_w)/2),ga_d,ga_w,fill=False,**kw))
    for xp in [W*0.115, W-W*0.115]:
        ax.plot(xp, H/2, "o", color=WHITE, ms=2.0, alpha=0.72)

def _lines_hockey(ax, W, H):
    """Field markings for hockey (91.4×55m): center, D-circles, 23m lines."""
    kw  = dict(color=WHITE, lw=1.3, alpha=0.72)
    dkw = dict(color=WHITE, lw=0.9, alpha=0.45, linestyle="--")
    ax.plot([0,W,W,0,0],[0,0,H,H,0],**kw)
    ax.plot([W/2,W/2],[0,H],**kw)
    ax.plot(W/2, H/2, "o", color=WHITE, ms=2.5, alpha=0.72)
    # 23m lines
    for x in [W*0.252, W-W*0.252]:
        ax.plot([x,x],[0,H],**dkw)
    # D-circles (shooting circles: 14.63m radius from centre of goal line)
    r = 14.63
    for cx, sign in [(0, 1), (W, -1)]:
        th = np.linspace(-np.pi/2, np.pi/2, 120)
        ax.plot(cx + sign*r*np.cos(th), H/2 + r*np.sin(th), **kw)
    # Goals (3.66m wide, centred on goal line)
    goal_w = 3.66
    for x in [0, W]:
        ax.plot([x,x],[(H-goal_w)/2,(H+goal_w)/2],color=WHITE,lw=2.2,alpha=0.90)

def _zone_stats(ndvi_2d_in, W_m: float) -> dict:
    """Calcula NDVI medio por zona funcional del campo desde el array 2D.

    ndvi_2d_in es la lista normalizada [0-1] (0=NDVI 0.35, 1=NDVI 0.90).
    Retorna {0..5: ndvi_raw} donde ndvi_raw es el NDVI real (0.35-0.90).
    """
    if ndvi_2d_in is None:
        return {}
    try:
        arr = np.array(ndvi_2d_in, dtype=np.float32)
        ncols = arr.shape[1]
        stats = {}
        for z, (b0, b1) in enumerate(zip(_ZONE_BREAKS[:-1], _ZONE_BREAKS[1:])):
            c0, c1 = int(b0 * ncols), max(int(b1 * ncols), int(b0 * ncols) + 1)
            c1 = min(c1, ncols)
            zone_norm = arr[:, c0:c1]
            valid = (zone_norm > 0.01) & (zone_norm < 0.999)
            if valid.sum() >= 1:
                ndvi_raw = float(zone_norm[valid].mean()) * 0.55 + 0.35
                stats[z] = round(ndvi_raw, 2)
        return stats
    except Exception:
        return {}


def _draw_zones(ax, W_m: float, H_m: float, zone_ndvi: dict):
    """Overlay de zonas funcionales: líneas divisorias + NDVI por zona."""
    breaks_m = [b * W_m for b in _ZONE_BREAKS]
    for x in breaks_m[1:-1]:
        ax.axvline(x, color=WHITE, alpha=0.18, lw=0.6, ls="--", zorder=3)
    for z, label in enumerate(_ZONE_LABELS):
        xmid = (breaks_m[z] + breaks_m[z + 1]) / 2
        ndvi_val = zone_ndvi.get(z)
        if ndvi_val is not None:
            col = ("#2e7d32" if ndvi_val >= 0.70 else
                   "#f9a825" if ndvi_val >= 0.57 else "#c62828")
            ax.text(xmid, H_m * 0.06, f"{ndvi_val:.2f}",
                    ha="center", va="top", fontsize=5.2, color=col,
                    fontfamily="monospace", fontweight="bold", zorder=4,
                    bbox=dict(boxstyle="round,pad=0.12",
                              facecolor="#000000BB", edgecolor="none"))
        ax.text(xmid, H_m * 0.97, label,
                ha="center", va="bottom", fontsize=4.5, color="#ffffff44",
                fontfamily="monospace", zorder=4)


def _sha(cid, ndvi, ipos, semana, ts):
    return hashlib.sha256(f"{cid}|{ndvi:.4f}|{ipos:.2f}|{semana}|{ts}".encode()).hexdigest()

def _qr_arr():
    if not HAS_QR:
        return None
    try:
        qr = qrcode.QRCode(version=2, box_size=3, border=1)
        qr.add_data(QR_URL)
        qr.make(fit=True)
        img = qr.make_image(fill_color="white", back_color="#0d1117")
        return np.array(img.convert("RGBA"))
    except Exception:
        return None

def generate_all(ipos_results: dict, semana_label: str, ndvi_map: dict = None) -> tuple[dict[str,bytes], dict[str,str]]:
    """
    Returns (png_bytes_dict, verify_hashes_dict).
    png_bytes_dict: {cancha_id -> PNG bytes}
    verify_hashes_dict: {sha256hex -> {cancha, semana, ipos, ts}}
    """
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    qr = _qr_arr()
    out_bytes: dict[str,bytes] = {}
    verify_hashes: dict[str,str] = {}

    for cid, data in ipos_results.items():
        if cid not in DIMS:
            continue
        W_m, H_m = DIMS[cid]
        ipos  = data["score"]
        sem   = data["semaforo"]
        col   = SEM_COL.get(sem, SEM_COL["verde"])
        label = cid.upper()
        txt   = data["texto"]

        # NDVI: use real satellite value when available, else derive from IPOS
        cid_data  = (ndvi_map or {}).get(cid) or {}
        if isinstance(cid_data, dict):
            ndvi_base  = cid_data.get("ndvi") or max(0.18, 0.72 - (ipos / 350.0) * 0.54)
            ndvi_2d_in = cid_data.get("ndvi_2d")   # normalizado [0-1] (0=0.35, 1=0.90)
            ndre_val   = cid_data.get("ndre")       # scalar NDRE
            gndvi_val  = cid_data.get("gndvi")      # scalar GNDVI
            evi2_val   = cid_data.get("evi2")       # scalar EVI2
        else:
            ndvi_base  = float(cid_data) if cid_data else max(0.18, 0.72 - (ipos / 350.0) * 0.54)
            ndvi_2d_in = None
            ndre_val = gndvi_val = evi2_val = None

        # Estadísticas por zona funcional del campo
        zone_ndvi = _zone_stats(ndvi_2d_in, W_m)
        sha = _sha(cid, ndvi_base, ipos, semana_label, ts)
        verify_hashes[sha] = {
            "cancha": label, "semana": semana_label,
            "ipos": ipos, "ndvi": round(ndvi_base, 4),
            "semaforo": sem,
            "fecha_generacion": ts,
            "fuente_array": "real_s2" if ndvi_2d_in else "sintetico",
        }

        rows = max(4, round(H_m/10)+1)
        cols = max(5, round(W_m/10)+1)
        seed = abs(hash(cid)) % (2**31)

        # Usar array real del satélite si está disponible
        if ndvi_2d_in is not None:
            try:
                arr = np.array(ndvi_2d_in, dtype=np.float32)
                # Redimensionar al tamaño canónico de la grilla
                from scipy.ndimage import zoom as _zoom2
                if arr.shape != (rows, cols):
                    zr = rows / max(1, arr.shape[0])
                    zc = cols / max(1, arr.shape[1])
                    arr = _zoom2(arr, (zr, zc), order=1)
                ndvi_g = np.clip(arr, 0.0, 1.0)
            except Exception:
                ndvi_g = _ndvi_grid(rows, cols, ndvi_base, seed)
        else:
            ndvi_g = _ndvi_grid(rows, cols, ndvi_base, seed)
        use_g  = _usage_overlay(rows, cols, ipos)

        fig = Figure(figsize=(W_PX/DPI, H_PX/DPI), dpi=DPI)
        fig.patch.set_facecolor(BG)
        canvas = FigureCanvasAgg(fig)

        ax = fig.add_axes([0.0, 0.07, 0.92, 0.93])
        ax.set_facecolor(BG)

        # Layer 1: NDVI — bicúbico + gaussian mínimo (sigma=0.8, preserva estructura)
        scale_r = H_PX * 0.93 / rows
        scale_c = W_PX * 0.92 / cols
        ndvi_up = _zoom(ndvi_g.astype(np.float64), (scale_r, scale_c), order=3)
        ndvi_up = gaussian_filter(ndvi_up, sigma=0.8)
        ax.imshow(ndvi_up, cmap=ELEVAGRO_CMAP, vmin=_NDVI_VMIN, vmax=_NDVI_VMAX, origin="upper",
                  aspect="auto", extent=[0,W_m,H_m,0])
        ax.set_xlim(0,W_m); ax.set_ylim(H_m,0)

        # Layer 2: IPOS usage overlay
        use_up = _zoom(use_g.astype(np.float64), (scale_r, scale_c), order=1)
        use_up = gaussian_filter(use_up, sigma=1.8)
        ax.imshow(use_up, cmap="Greys_r", vmin=0, vmax=1, alpha=0.32,
                  origin="upper", aspect="auto", extent=[0,W_m,H_m,0])

        # Layer 3: Zonas funcionales + NDVI por zona
        if zone_ndvi:
            _draw_zones(ax, W_m, H_m, zone_ndvi)

        # Field lines are drawn by the SVG overlay in the panel (z-index:2).
        # PNG is pure NDVI gradient — no baked lines to avoid doubling.
        ax.axis("off")

        # Badge top-right: nombre + alerta
        ax.text(0.984, 0.974, f"  {label}",
                transform=ax.transAxes, fontsize=13, fontweight="bold",
                color=col, ha="right", va="top",
                bbox=dict(boxstyle="round,pad=0.3", facecolor="#000000CC",
                          edgecolor=col, linewidth=1.8))
        ax.text(0.984, 0.895, txt,
                transform=ax.transAxes, fontsize=7, fontweight="bold",
                color=col, ha="right", va="top",
                bbox=dict(boxstyle="round,pad=0.22", facecolor="#000000CC",
                          edgecolor=col+"88", linewidth=0.9))

        # Badge multi-índice: NDVI / NDRE / GNDVI
        idx_parts = [f"NDVI {ndvi_base:.2f}"]
        if ndre_val is not None:
            idx_parts.append(f"NDRE {ndre_val:.2f}")
        if gndvi_val is not None:
            idx_parts.append(f"GNDVI {gndvi_val:.2f}")
        ax.text(0.984, 0.828, "  ".join(idx_parts),
                transform=ax.transAxes, fontsize=5.2, color="#ffffffBB",
                ha="right", va="top", fontfamily="monospace",
                bbox=dict(boxstyle="round,pad=0.15", facecolor="#000000AA",
                          edgecolor="#ffffff22", linewidth=0.5))

        # QR bottom-right
        if qr is not None:
            try:
                ax_qr = ax.inset_axes([0.865, 0.02, 0.125, 0.20])
                ax_qr.imshow(qr); ax_qr.axis("off")
            except Exception:
                pass

        # Footer strip
        ax_f = fig.add_axes([0.0, 0.0, 1.0, 0.07])
        ax_f.set_facecolor("#040404"); ax_f.axis("off")
        p   = data.get("personas",0)
        h   = data.get("horas",0)
        ax_f.text(0.005, 0.78,
                  f"IPOS {ipos:.0f}  ·  {p} personas  ·  {h:.0f}h  ·  Semana {semana_label}",
                  transform=ax_f.transAxes, fontsize=7, fontweight="bold",
                  color=col, va="top", ha="left", fontfamily="monospace")
        ax_f.text(0.005, 0.18,
                  f"SHA-256: {sha[:48]}",
                  transform=ax_f.transAxes, fontsize=5.0, color="#ffffff55",
                  va="top", ha="left", fontfamily="monospace")
        ax_f.text(0.88, 0.78,
                  "Faro Protocol",
                  transform=ax_f.transAxes, fontsize=5.5, color="#c9a84c88",
                  va="top", ha="right", fontfamily="monospace")

        # Colourbar — rango calibrado 0.35 (crítico) → 0.90 (óptimo)
        cbar = fig.add_axes([0.926, 0.07, 0.028, 0.93])
        cbar.set_facecolor(BG)
        cb = np.linspace(1, 0, 200).reshape(200, 1)
        cbar.imshow(cb, cmap=ELEVAGRO_CMAP, aspect="auto", vmin=_NDVI_VMIN, vmax=_NDVI_VMAX)
        cbar.set_xticks([])
        # Ticks en posiciones normalizadas [0-1] correspondientes a NDVI 0.90/0.70/0.57/0.35
        cbar.set_yticks([0, 36, 145, 199])
        cbar.set_yticklabels(["0.90\nÓptimo", "0.70\nBueno", "0.57\nEstrés", "0.35\nCrítico"],
                              fontsize=4.8, color="#ffffffcc")
        for sp in cbar.spines.values(): sp.set_visible(False)
        cbar.tick_params(length=0)

        canvas.draw()
        buf = io.BytesIO()
        raw = canvas.buffer_rgba()
        pil = Image.frombytes("RGBA", canvas.get_width_height(), raw).convert("RGB")
        pil.save(buf, "PNG", optimize=True)
        buf.seek(0)
        out_bytes[cid] = buf.getvalue()
        plt.close(fig)

    return out_bytes, verify_hashes


# ── Paleta Elevagro (LinearSegmentedColormap, 256 niveles) ───────────────────
_ELEVAGRO_COLORS = [
    '#D73027',  # Rojo: Estrés crítico / Suelo desnudo
    '#F46D43',  # Naranja: Alerta / Pérdida de vigor
    '#FDAE61',  # Amarillo: Transición / Falta nutrientes
    '#D9EF8B',  # Verde Lima: Densidad media
    '#66BD63',  # Verde Brillante: Saludable
    '#1A9850',  # Verde Profundo: Óptimo
]
ELEVAGRO_CMAP = LinearSegmentedColormap.from_list("ElevagroStyle", _ELEVAGRO_COLORS, N=256)
ELEVAGRO_CMAP.set_bad(color='#ECEFF1')


def generate_elevagro_style_heatmap(ndvi_matrix: np.ndarray, save_path: str) -> None:
    """
    Genera un PNG NDVI con paleta Elevagro/QGIS de alta fidelidad.
    LinearSegmentedColormap 256 niveles + interpolación hanning.
    NaN/nubes → gris #ECEFF1 neutro.
    """
    display_data = np.copy(ndvi_matrix)

    fig, ax = plt.subplots(figsize=(8, 6), dpi=300)
    ax.imshow(
        display_data,
        cmap=ELEVAGRO_CMAP,
        interpolation='hanning',
        origin='upper',
    )
    ax.axis('off')
    fig.subplots_adjust(left=0, right=1, bottom=0, top=1)
    plt.savefig(save_path, bbox_inches='tight', pad_inches=0, transparent=True, dpi=300)
    plt.close()
    print(f"✓ Heatmap Elevagro-style saved: {save_path}")
