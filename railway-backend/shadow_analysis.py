"""
shadow_analysis.py — Structural shadow detection via Sentinel-2 historical series.

Pixels in the bottom 25th-percentile of field NDVI in >70% of images
are classified as permanent structural shadows (stadium stands, buildings).

Produces per-location:
  - Shadow classification PNG  → velez/heatmaps/heatmap_{id}_shadow.png
  - velez_data.json update     → shadow_maps key

Run standalone:  python shadow_analysis.py [--no-push]
"""
from __future__ import annotations
import base64, json, logging, os
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from typing import Optional

import numpy as np

log = logging.getLogger(__name__)

# ── Locations ──────────────────────────────────────────────────────────────────
# Amalfitani main pitch: adjacent to Villa Olímpica, south section of complex.
# 1FP/2FP: full-size primer-equipo pitches (coords from ndvi_real.py).
# buf_m extra to capture stand shadows that fall onto the pitch.

SHADOW_LOCATIONS: dict[str, dict] = {
    "amalfitani": {
        "lat": -34.6406, "lon": -58.5210,
        "label": "Estadio J. Amalfitani — Cancha Principal",
        "w_m": 105, "h_m": 68, "buf_m": 30,
    },
    "1fp": {
        "lat": -34.6366, "lon": -58.5213,
        "label": "Cancha 1FP — Primer Equipo",
        "w_m": 105, "h_m": 70, "buf_m": 20,
    },
    "2fp": {
        "lat": -34.6366, "lon": -58.5202,
        "label": "Cancha 2FP — Primer Equipo",
        "w_m": 105, "h_m": 70, "buf_m": 20,
    },
}

# Classification thresholds
_PERM_PCT    = 70     # % of images where pixel is in bottom quartile → permanent shadow
_PARTIAL_PCT = 40     # threshold for partial shadow
_FIELD_PERC  = 25     # bottom N% of field = shadowed in that image

_DEG_PER_M_LAT = 1 / 111_139
_DEG_PER_M_LON = 1 / 91_300

_GH_API = "https://api.github.com"
_OWNER  = "protocolfaro"
_REPO   = "faroprotocol"
_BRANCH = "main"

# ── Helpers ───────────────────────────────────────────────────────────────────

def _field_bbox(lat, lon, w_m, h_m, buf_m) -> tuple:
    dlat = (h_m / 2 + buf_m) * _DEG_PER_M_LAT
    dlon = (w_m / 2 + buf_m) * _DEG_PER_M_LON
    return (lon - dlon, lat - dlat, lon + dlon, lat + dlat)


# ── STAC — one best image per calendar month ──────────────────────────────────

def _search_monthly(bbox, years_back=2, max_cloud=15.0) -> list:
    import pystac_client, planetary_computer

    today   = date.today()
    dt_from = (today - timedelta(days=365 * years_back)).isoformat()

    catalog = pystac_client.Client.open(
        "https://planetarycomputer.microsoft.com/api/stac/v1",
        modifier=planetary_computer.sign_inplace,
    )
    items = list(catalog.search(
        collections=["sentinel-2-l2a"],
        bbox=bbox,
        datetime=f"{dt_from}/{today.isoformat()}",
        query={"eo:cloud_cover": {"lt": max_cloud}},
        sortby="+properties.datetime",
    ).items())

    by_month: dict[str, list] = defaultdict(list)
    for item in items:
        month = item.properties.get("datetime", "")[:7]
        by_month[month].append(item)

    selected = [
        min(by_month[m], key=lambda i: i.properties.get("eo:cloud_cover", 100))
        for m in sorted(by_month)
    ]
    return selected


# ── Windowed COG read ─────────────────────────────────────────────────────────

def _read_ndvi(item, bbox_4326) -> Optional[np.ndarray]:
    import rasterio
    from rasterio.warp import transform_bounds
    from rasterio.windows import from_bounds

    try:
        with (
            rasterio.open(item.assets["B08"].href) as src_nir,
            rasterio.open(item.assets["B04"].href) as src_red,
        ):
            native = transform_bounds("EPSG:4326", src_nir.crs, *bbox_4326)
            win    = from_bounds(*native, transform=src_nir.transform)

            nir = src_nir.read(1, window=win).astype("float32") / 10_000
            red = src_red.read(1, window=win).astype("float32") / 10_000

            valid = (nir + red) > 0.02
            if valid.sum() < nir.size * 0.4:
                return None   # window mostly cloud / masked

            return np.where(valid, (nir - red) / (nir + red + 1e-8), np.nan)
    except Exception as exc:
        log.debug("shadow: read skip: %s", exc)
        return None


# ── Shadow statistics ─────────────────────────────────────────────────────────

def _shadow_freq(stack: list[np.ndarray]) -> Optional[np.ndarray]:
    """Per-pixel frequency (%) of being in bottom _FIELD_PERC% of field NDVI."""
    if len(stack) < 3:
        return None

    shapes = [a.shape for a in stack]
    target = max(set(shapes), key=shapes.count)
    stack  = [a for a in stack if a.shape == target]
    if len(stack) < 3:
        return None

    shadow_n = np.zeros(target, "float32")
    valid_n  = np.zeros(target, "float32")

    for img in stack:
        valid = ~np.isnan(img)
        if valid.sum() < 4:
            continue
        thr = np.nanpercentile(img[valid], _FIELD_PERC)
        shadow_n += (valid & (img <= thr)).astype("float32")
        valid_n  += valid.astype("float32")

    with np.errstate(divide="ignore", invalid="ignore"):
        return np.where(valid_n > 0, shadow_n / valid_n * 100, np.nan)


def _classify(freq: np.ndarray) -> np.ndarray:
    """0=full sun, 1=partial, 2=permanent, 255=no data."""
    cls = np.where(freq >= _PERM_PCT,    2,
          np.where(freq >= _PARTIAL_PCT, 1, 0)).astype("uint8")
    return np.where(np.isnan(freq), 255, cls).astype("uint8")


# ── Visualization ─────────────────────────────────────────────────────────────

_BG    = "#0d1117"
_WHITE = "#ffffff"

_CMAP_SHADOW_DEF = [
    (0.00, "#1b5e20"), (0.35, "#388e3c"), (0.40, "#f0b429"),
    (0.70, "#e05252"), (1.00, "#7b0000"),
]


def _draw_field_lines(ax, W, H):
    import matplotlib.patches as mp
    kw = dict(color=_WHITE, lw=1.2, alpha=0.80)
    ax.plot([0,W,W,0,0], [0,0,H,H,0], **kw)
    ax.plot([W/2, W/2], [0, H], **kw)
    ax.add_patch(mp.Circle((W/2, H/2), 9.15, fill=False, **kw))
    ax.plot(W/2, H/2, "o", color=_WHITE, ms=2.5, alpha=0.8)
    for x0 in [0, W-16.5]:
        ax.add_patch(mp.Rectangle((x0, (H-40.32)/2), 16.5, 40.32, fill=False, **kw))
    for x0 in [0, W-5.5]:
        ax.add_patch(mp.Rectangle((x0, (H-18.32)/2), 5.5, 18.32, fill=False, **kw))
    for xp in [11.0, W-11.0]:
        ax.plot(xp, H/2, "o", color=_WHITE, ms=2, alpha=0.8)
    for cx, t1, t2 in [(11.0, -53, 53), (W-11.0, 127, 233)]:
        ax.add_patch(mp.Arc((cx, H/2), 18.3, 18.3, theta1=t1, theta2=t2, **kw))
    for cx, cy, t1, t2 in [(0,0,0,90),(W,0,90,180),(0,H,270,360),(W,H,180,270)]:
        ax.add_patch(mp.Arc((cx, cy), 2, 2, theta1=t1, theta2=t2, **kw))


def render_shadow_heatmap(
    cls: np.ndarray,
    freq: np.ndarray,
    label: str,
    w_m: float,
    h_m: float,
    n_images: int,
) -> bytes:
    """Return PNG bytes of field diagram with shadow classification overlay."""
    import io
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.patches as mp
    from matplotlib.colors import LinearSegmentedColormap
    from matplotlib.figure import Figure
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from scipy.ndimage import zoom as _zoom
    from PIL import Image

    W_PX, H_PX, DPI = 860, 560, 100
    fig = Figure(figsize=(W_PX/DPI, H_PX/DPI), dpi=DPI)
    fig.patch.set_facecolor(_BG)
    canvas = FigureCanvasAgg(fig)

    ax = fig.add_axes([0.0, 0.08, 0.93, 0.91])
    ax.set_facecolor(_BG)

    cmap = LinearSegmentedColormap.from_list("shd", _CMAP_SHADOW_DEF)

    freq_norm = np.clip(np.where(np.isnan(freq), 0, freq) / 100, 0, 1)
    sr = H_PX * 0.91 / cls.shape[0]
    sc = W_PX * 0.93 / cls.shape[1]
    freq_up = _zoom(freq_norm.astype("float64"), (sr, sc), order=1)

    ax.imshow(freq_up, cmap=cmap, vmin=0, vmax=1, origin="upper",
              aspect="auto", extent=[0, w_m, h_m, 0])
    ax.set_xlim(0, w_m); ax.set_ylim(h_m, 0)

    # Hatch permanent-shadow zones
    perm = (cls == 2).astype(float)
    if perm.any():
        perm_up = _zoom(perm, (sr, sc), order=0)
        ax.imshow(np.ma.masked_where(perm_up < 0.5, perm_up),
                  cmap="Reds", vmin=0, vmax=1, alpha=0.40, origin="upper",
                  aspect="auto", extent=[0, w_m, h_m, 0])

    _draw_field_lines(ax, w_m, h_m)
    ax.axis("off")

    # Title
    ax.text(0.50, 0.97, f"Sombras Estructurales — {label}",
            transform=ax.transAxes, fontsize=9.5, fontweight="bold",
            color="#f0b429", ha="center", va="top",
            bbox=dict(boxstyle="round,pad=0.3", facecolor="#000000CC",
                      edgecolor="#f0b42988", lw=1.2))
    ax.text(0.50, 0.87,
            f"{n_images} imágenes Sentinel-2 · {2} años · nubosidad <15%",
            transform=ax.transAxes, fontsize=7, color="#ffffffaa",
            ha="center", va="top")

    # Legend
    legend = [
        mp.Patch(color="#7b0000", label=f"Sombra permanente (>{_PERM_PCT}% imágenes)"),
        mp.Patch(color="#f0b429", label=f"Sombra parcial ({_PARTIAL_PCT}–{_PERM_PCT}%)"),
        mp.Patch(color="#1b5e20", label=f"Exposicion plena (<{_PARTIAL_PCT}%)"),
    ]
    ax.legend(handles=legend, loc="lower left", fontsize=7,
              facecolor="#000000CC", edgecolor="#ffffff33",
              labelcolor="#ffffffcc", framealpha=0.85)
    ax.text(0.97, 0.97, "N↑", transform=ax.transAxes,
            fontsize=9, color="#ffffff88", ha="right", va="top")

    # Colourbar
    cax = fig.add_axes([0.935, 0.08, 0.025, 0.91])
    cax.set_facecolor(_BG)
    cax.imshow(np.linspace(1, 0, 200).reshape(200, 1), cmap=cmap,
               aspect="auto", vmin=0, vmax=1)
    cax.set_xticks([])
    cax.set_yticks([0, 60, 100, 140, 199])
    cax.set_yticklabels(["100%", "70%", "50%", "40%", "0%"], fontsize=6, color="#ffffffcc")
    for sp in cax.spines.values(): sp.set_visible(False)
    cax.tick_params(length=0)
    cax.text(0.5, -0.025, "% img\nen sombra", transform=cax.transAxes,
             fontsize=5, color="#ffffff88", ha="center")

    # Footer
    ax_f = fig.add_axes([0.0, 0.0, 1.0, 0.08])
    ax_f.set_facecolor("#040404"); ax_f.axis("off")
    perm_px  = int((cls == 2).sum())
    part_px  = int((cls == 1).sum())
    total_px = int((cls != 255).sum())
    ts = datetime.now(timezone.utc).replace(tzinfo=None).strftime("%Y-%m-%dT%H:%M:%SZ")
    ax_f.text(0.005, 0.82,
              f"Sombra permanente: {perm_px}px ({perm_px*100//max(1,total_px)}%)  ·  "
              f"Parcial: {part_px}px ({part_px*100//max(1,total_px)}%)  ·  "
              f"Resolución: 10m/px  ·  {ts}",
              transform=ax_f.transAxes, fontsize=6.5, fontweight="bold",
              color="#f0b429", va="top", fontfamily="monospace")
    ax_f.text(0.88, 0.82, "Faro Protocol", transform=ax_f.transAxes,
              fontsize=5.5, color="#c9a84c88", va="top", ha="right",
              fontfamily="monospace")

    canvas.draw()
    buf = io.BytesIO()
    Image.frombytes("RGBA", canvas.get_width_height(),
                    canvas.buffer_rgba()).convert("RGB").save(buf, "PNG", optimize=True)
    buf.seek(0)
    import matplotlib.pyplot as plt; plt.close(fig)
    return buf.getvalue()


# ── GitHub push ───────────────────────────────────────────────────────────────

def _gh_hdrs():
    import github_push as gp
    return gp._hdrs()


def _push_shadow_results(results: dict) -> dict:
    import requests
    import github_push as gp

    ts     = datetime.now(timezone.utc).replace(tzinfo=None).strftime("%Y-%m-%dT%H:%M:%SZ")
    pushed = {}

    # 1. Push each shadow PNG
    for loc_id, res in results.items():
        if not res.get("png_bytes"):
            continue
        path = f"velez/heatmaps/heatmap_{loc_id}_shadow.png"
        try:
            sha  = gp._sha(path)
            resp = gp._put(path, res["png_bytes"], f"shadow {loc_id} [{ts}]", sha)
            pushed[loc_id] = (
                resp.get("content", {}).get("html_url")
                or f"https://github.com/{_OWNER}/{_REPO}/blob/{_BRANCH}/{path}"
            )
            log.info("Pushed %s", path)
        except Exception as exc:
            log.warning("push %s failed: %s", path, exc)

    # 2. Fetch current velez_data.json
    r = requests.get(
        f"{_GH_API}/repos/{_OWNER}/{_REPO}/contents/velez/velez_data.json",
        headers=_gh_hdrs(), params={"ref": _BRANCH}, timeout=15,
    )
    r.raise_for_status()
    d    = r.json()
    sha  = d.get("sha")
    cfg  = json.loads(base64.b64decode(d["content"]).decode())

    # 3. Build shadow_maps payload
    cfg["shadow_maps"] = {}
    for loc_id, res in results.items():
        if not res.get("ok"):
            continue
        total = max(1, res["total_px"])
        cfg["shadow_maps"][loc_id] = {
            "generado_en":           ts,
            "label":                 res["label"],
            "imagenes_analizadas":   res["n_images"],
            "periodo_analisis":      res["period"],
            "resolucion_m":          10,
            "zonas_total":           res["total_px"],
            "sombra_permanente_px":  res["perm_px"],
            "sombra_parcial_px":     res["part_px"],
            "exposicion_plena_px":   res["sun_px"],
            "sombra_permanente_pct": round(res["perm_px"] / total * 100, 1),
            "sombra_parcial_pct":    round(res["part_px"] / total * 100, 1),
            "png_url":               pushed.get(loc_id, ""),
            "calibracion_riego": {
                "sombra_permanente": {
                    "et0_factor": 0.70,
                    "riesgo_hongos": "alto",
                    "accion": "Reducir riego 30% · aplicar fungicida preventivo semanal",
                },
                "sombra_parcial": {
                    "et0_factor": 0.85,
                    "riesgo_hongos": "medio",
                    "accion": "Reducir riego 15% · monitorear humedad foliar",
                },
                "exposicion_plena": {
                    "et0_factor": 1.00,
                    "riesgo_hongos": "bajo",
                    "accion": "Riego estándar según ET0 diario",
                },
            },
        }

    # 4. Push updated velez_data.json
    payload = {
        "message": f"shadow_maps update [{ts}]",
        "content": base64.b64encode(
            json.dumps(cfg, ensure_ascii=False, indent=2).encode()
        ).decode(),
        "branch": _BRANCH,
        "sha":    sha,
    }
    r = requests.put(
        f"{_GH_API}/repos/{_OWNER}/{_REPO}/contents/velez/velez_data.json",
        headers=_gh_hdrs(), json=payload, timeout=35,
    )
    r.raise_for_status()
    log.info("velez_data.json updated — shadow_maps for %d locations", len(cfg["shadow_maps"]))
    return pushed


# ── Main entry point ──────────────────────────────────────────────────────────

def run_shadow_analysis(
    locations: dict = None,
    years_back: int = 2,
    max_cloud: float = 15.0,
    push: bool = True,
) -> dict:
    """
    Run structural shadow analysis for all locations.
    Returns summary dict (no PNG bytes — only stats).
    push=False to skip GitHub (local testing).
    """
    try:
        import pystac_client, planetary_computer, rasterio, rioxarray  # noqa: F401
    except ImportError as exc:
        log.error("shadow_analysis: missing dep %s", exc)
        return {"ok": False, "error": str(exc)}

    if locations is None:
        locations = SHADOW_LOCATIONS

    today   = date.today()
    period  = (f"{(today - timedelta(days=365*years_back)).isoformat()}"
               f" / {today.isoformat()}")
    results = {}

    for loc_id, loc in locations.items():
        lat, lon     = loc["lat"], loc["lon"]
        w_m, h_m    = loc.get("w_m", 105), loc.get("h_m", 68)
        buf_m        = loc.get("buf_m", 20)
        label        = loc.get("label", loc_id)
        bbox         = _field_bbox(lat, lon, w_m, h_m, buf_m)

        log.info("shadow: %s — searching %d years, cloud <%.0f%%",
                 loc_id, years_back, max_cloud)
        try:
            items = _search_monthly(bbox, years_back, max_cloud)
            months = len(set(i.properties.get("datetime", "")[:7] for i in items))
            log.info("  %s: %d images / %d months", loc_id, len(items), months)

            stack = [a for a in (_read_ndvi(it, bbox) for it in items) if a is not None]
            log.info("  %s: %d/%d valid windows", loc_id, len(stack), len(items))

            freq = _shadow_freq(stack)
            if freq is None:
                log.warning("  %s: insufficient data (<3 valid images)", loc_id)
                results[loc_id] = {"ok": False, "label": label,
                                   "reason": "insufficient images"}
                continue

            cls      = _classify(freq)
            total_px = int((cls != 255).sum())
            perm_px  = int((cls == 2).sum())
            part_px  = int((cls == 1).sum())
            sun_px   = int((cls == 0).sum())

            log.info(
                "  %s: permanent=%d px (%.0f%%)  partial=%d px (%.0f%%)"
                "  full-sun=%d px (%.0f%%)",
                loc_id,
                perm_px, perm_px * 100 / max(1, total_px),
                part_px, part_px * 100 / max(1, total_px),
                sun_px,  sun_px  * 100 / max(1, total_px),
            )

            png = render_shadow_heatmap(cls, freq, label, w_m, h_m, len(stack))
            results[loc_id] = {
                "ok": True, "label": label,
                "n_images": len(stack), "period": period,
                "total_px": total_px, "perm_px": perm_px,
                "part_px":  part_px,  "sun_px":  sun_px,
                "freq_mean": float(np.nanmean(freq)),
                "png_bytes": png,
            }

        except Exception as exc:
            log.error("shadow: %s FAILED: %s", loc_id, exc, exc_info=True)
            results[loc_id] = {"ok": False, "label": label, "error": str(exc)}

    good = {k: v for k, v in results.items() if v.get("ok")}
    log.info("shadow: %d/%d locations OK", len(good), len(results))

    if push and good:
        try:
            _push_shadow_results(good)
        except Exception as exc:
            log.error("shadow: push failed: %s", exc)

    # Strip PNG bytes from summary output
    summary = {
        k: {j: v[j] for j in v if j != "png_bytes"}
        for k, v in results.items()
    }
    return {"ok": len(good) > 0, "locations": summary}


if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    result = run_shadow_analysis(push="--no-push" not in sys.argv)
    print(json.dumps(
        result["locations"], indent=2, ensure_ascii=False,
        default=lambda x: round(x, 3) if isinstance(x, float) else str(x),
    ))
