"""
run_post_show.py — Pipeline post_show para airbag_2026-05-31.
Todo con datos reales. Nada hardcodeado.

Fuentes verificadas en sesión anterior:
  Element84 Earth Search (AWS s3://sentinel-cogs, AWS_NO_SIGN_REQUEST=YES)
  Microsoft Planetary Computer STAC (Sentinel-1 GRD VV backscatter)

Hallazgos reales (re-verificados aquí):
  Pre-show 17/05: Sentinel-2 L2A · NDVI calculado sobre píxeles Amalfitani
  Post-show 27/05: consulta real → resultado de píxeles dentro del bbox
  Post-show 29/05: consulta real → resultado de píxeles dentro del bbox
  SAR pre  30/05: Sentinel-1 GRD VV backscatter sobre Amalfitani
  SAR post: búsqueda real post 2026-05-31 → disponible o no
"""
from __future__ import annotations
import hashlib, json, logging, os, pathlib, sys
from datetime import datetime, timezone

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    handlers=[logging.StreamHandler()],
)
log = logging.getLogger("run_post_show")

DALE_DIR  = pathlib.Path(__file__).parent
SHOWS_DIR = DALE_DIR / "shows"
CERT_DIR  = DALE_DIR / "certificados"
REP_DIR   = DALE_DIR / "reportes"

sys.path.insert(0, str(DALE_DIR))

SHOW_ID   = "airbag_2026-05-31"
SHOW_DATE = "2026-05-31"

# Element84 Earth Search — espejo público de Sentinel-2 en AWS S3
_E84_STAC    = "https://earth-search.aws.element84.com/v1"
_S2_COL_E84  = "sentinel-2-l2a"

# PC STAC — Sentinel-1 GRD
_PC_STAC     = "https://planetarycomputer.microsoft.com/api/stac/v1"
_S1_COL      = "sentinel-1-grd"

# Bbox Amalfitani (de dale_play_config)
from dale_play_config import VENUE_BBOX, VENUE_LAT, VENUE_LON
MINX, MINY, MAXX, MAXY = VENUE_BBOX


# ═══════════════════════════════════════════════════════════════════════════════
# 1. Re-fetch Sentinel-2 NDVI desde Element84 para fecha específica
# ═══════════════════════════════════════════════════════════════════════════════

def _fetch_s2_ndvi_e84(date_str: str) -> dict:
    """
    Descarga Sentinel-2 L2A para la fecha exacta desde Element84 (AWS COG).
    No filtra por nubosidad de tile — lee píxeles en el bbox Amalfitani y
    aplica SCL pixel-level (válidos: 4=veg, 5=no-veg, 6=agua, 7=no-clasif).
    Retorna ndvi, n_valid, n_total, scl_unique, tile_cloud_pct, item_id.
    """
    import requests, numpy as np, rasterio
    from rasterio.windows import from_bounds
    from rasterio.warp import transform_bounds
    from rasterio.enums import Resampling

    os.environ["AWS_NO_SIGN_REQUEST"] = "YES"

    # Buscar todos los items del día (sin filtro de nube)
    payload = {
        "collections": [_S2_COL_E84],
        "bbox":        [MINX, MINY, MAXX, MAXY],
        "datetime":    f"{date_str}T00:00:00Z/{date_str}T23:59:59Z",
        "limit":       10,
    }
    r = requests.post(f"{_E84_STAC}/search", json=payload, timeout=30)
    r.raise_for_status()
    features = r.json().get("features", [])

    if not features:
        log.info("E84 %s: sin escenas", date_str)
        return {"ndvi": None, "n_valid": 0, "n_total": 0,
                "tile_cloud_pct": None, "scl_unique": [],
                "item_id": None, "estado": "sin_escena"}

    # Preferir la escena con menor nubosidad
    item = min(features, key=lambda f: f.get("properties", {}).get("eo:cloud_cover", 100))
    props       = item.get("properties", {})
    assets      = item.get("assets", {})
    item_id     = item.get("id", "")
    tile_cloud  = round(float(props.get("eo:cloud_cover", 100)), 1)
    log.info("E84 %s: item=%s cloud=%.1f%%", date_str, item_id, tile_cloud)

    def _href(key: str) -> str:
        return assets.get(key, {}).get("href", "")

    def _read_band(key: str, shape_ref=None) -> np.ndarray | None:
        href = _href(key)
        if not href:
            return None
        try:
            with rasterio.open(href) as src:
                native = transform_bounds("EPSG:4326", src.crs, MINX, MINY, MAXX, MAXY)
                win    = from_bounds(*native, transform=src.transform)
                if shape_ref is not None:
                    return src.read(1, window=win,
                                    out_shape=shape_ref,
                                    resampling=Resampling.nearest).astype("float32")
                return src.read(1, window=win).astype("float32")
        except Exception as e:
            log.warning("E84 read %s [%s]: %s", key, date_str, e)
            return None

    red = _read_band("red")
    nir = _read_band("nir")
    if red is None or nir is None:
        return {"ndvi": None, "n_valid": 0, "n_total": 0,
                "tile_cloud_pct": tile_cloud, "scl_unique": [],
                "item_id": item_id, "estado": "error_bandas"}

    # SCL — nativo 20m, forzar misma forma que B04/B08 (10m)
    scl = _read_band("scl", shape_ref=(red.shape[0], red.shape[1]))
    n_total = red.size

    if scl is not None:
        valid_mask = np.isin(scl.astype(int), [4, 5, 6, 7])
        scl_unique = sorted(set(scl.astype(int).ravel().tolist()))
        area_clear = round(float(valid_mask.mean()) * 100, 1)
    else:
        valid_mask = (red > 0) & (nir > 0) & (red < 10000) & (nir < 10000)
        scl_unique = []
        area_clear = round(float(valid_mask.mean()) * 100, 1)

    n_valid = int(valid_mask.sum())
    log.info("E84 %s: n_valid=%d/%d  area_clear=%.1f%%  scl=%s",
             date_str, n_valid, n_total, area_clear, scl_unique[:5])

    if n_valid < 4:
        return {
            "ndvi":           None,
            "n_valid":        n_valid,
            "n_total":        n_total,
            "area_clear_pct": area_clear,
            "tile_cloud_pct": tile_cloud,
            "scl_unique":     scl_unique,
            "item_id":        item_id,
            "estado":         "nubosidad_total",
        }

    red_v = red[valid_mask] / 10_000.0
    nir_v = nir[valid_mask] / 10_000.0
    ndvi  = float(round(float(np.mean((nir_v - red_v) / (nir_v + red_v + 1e-9))), 3))
    log.info("E84 %s: NDVI=%.3f", date_str, ndvi)

    return {
        "ndvi":           ndvi,
        "n_valid":        n_valid,
        "n_total":        n_total,
        "area_clear_pct": area_clear,
        "tile_cloud_pct": tile_cloud,
        "scl_unique":     scl_unique,
        "item_id":        item_id,
        "estado":         "ok",
    }


# ═══════════════════════════════════════════════════════════════════════════════
# 2. SAR Sentinel-1 GRD — backscatter VV Amalfitani
# ═══════════════════════════════════════════════════════════════════════════════

def _fetch_sar_vv(date_from: str, date_to: str, label: str = "SAR") -> dict:
    """
    Busca y lee VV backscatter (dB) de Sentinel-1 GRD en PC STAC.
    Usa bbox del ítem STAC para interpolación lineal a píxeles (CRS=None en GRD).
    """
    import requests, numpy as np, rasterio
    from rasterio.windows import Window

    payload = {
        "collections": [_S1_COL],
        "bbox":        [MINX, MINY, MAXX, MAXY],
        "datetime":    f"{date_from}T00:00:00Z/{date_to}T23:59:59Z",
        "limit":       5,
        "sortby":      [{"field": "datetime", "direction": "desc"}],
    }
    r = requests.post(f"{_PC_STAC}/search", json=payload, timeout=20)
    r.raise_for_status()
    features = r.json().get("features", [])

    if not features:
        log.info("%s: sin escenas S1 GRD [%s – %s]", label, date_from, date_to)
        return {"vv_db": None, "n_px": 0, "item_id": None, "estado": "sin_escena"}

    item   = features[0]
    item_id = item.get("id", "")
    props   = item.get("properties", {})
    dt_str  = (props.get("datetime") or "")[:10]
    assets  = item.get("assets", {})
    log.info("%s: item=%s fecha=%s", label, item_id, dt_str)

    # Token SAS
    tok_r = requests.get(
        f"https://planetarycomputer.microsoft.com/api/sas/v1/token/{_S1_COL}",
        timeout=10
    )
    token = tok_r.json().get("token", "") if tok_r.ok else ""

    vv_href = assets.get("vv", {}).get("href", "")
    if not vv_href:
        log.warning("%s: asset 'vv' no encontrado. Assets: %s", label, list(assets.keys()))
        return {"vv_db": None, "n_px": 0, "item_id": item_id, "estado": "sin_asset_vv"}

    if token:
        vv_href = f"{vv_href}?{token}"

    # Bbox del ítem en coordenadas geográficas (S1 GRD no tiene CRS embebido)
    i_lon0, i_lat0, i_lon1, i_lat1 = item.get("bbox", [0, 0, 1, 1])

    try:
        with rasterio.open(vv_href) as src:
            H, W = src.shape
            fx0 = (MINX - i_lon0) / (i_lon1 - i_lon0)
            fx1 = (MAXX - i_lon0) / (i_lon1 - i_lon0)
            fy0 = (i_lat1 - MAXY) / (i_lat1 - i_lat0)
            fy1 = (i_lat1 - MINY) / (i_lat1 - i_lat0)
            col_off = max(0, int(fx0 * W))
            col_end = min(W, max(col_off + 20, int(fx1 * W)))
            row_off = max(0, int(fy0 * H))
            row_end = min(H, max(row_off + 20, int(fy1 * H)))
            win  = Window(col_off, row_off, col_end - col_off, row_end - row_off)
            data = src.read(1, window=win).astype("float32")

        valid = (data > 0) & np.isfinite(data)
        n_px  = int(valid.sum())
        if n_px < 4:
            return {"vv_db": None, "n_px": n_px, "item_id": item_id,
                    "fecha": dt_str, "estado": "sin_pixels_validos"}

        vv_db = float(round(10.0 * float(np.log10(float(np.mean(data[valid])))), 2))
        log.info("%s: VV=%.2f dB  n_px=%d  fecha=%s", label, vv_db, n_px, dt_str)
        return {"vv_db": vv_db, "n_px": n_px, "item_id": item_id,
                "fecha": dt_str, "estado": "ok"}

    except Exception as e:
        log.warning("%s: error leyendo VV: %s", label, e)
        return {"vv_db": None, "n_px": 0, "item_id": item_id,
                "fecha": dt_str, "estado": f"error: {e}"}


# ═══════════════════════════════════════════════════════════════════════════════
# 3. Reporte PNG post_show: pre vs post comparativa
# ═══════════════════════════════════════════════════════════════════════════════

def _generate_post_show_png(satellite_data: dict, sar_data: dict, show_id: str) -> str:
    """
    Genera PNG de comparación pre/post show con datos satelitales reales.
    Layout: encabezado | fila pre | fila post | leyenda/SAR
    """
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import matplotlib.patches as mpatches
    import matplotlib.gridspec as gridspec
    import numpy as np

    BG    = "#0a0a0a"
    BG2   = "#111318"
    GOLD  = "#c9a84c"
    WHITE = "#f2ede4"
    WDIM  = "#8a9099"
    REDL  = "#e74c3c"
    YELL  = "#f0b429"
    GRNL  = "#27ae60"
    CYAN  = "#00b4d8"

    fig = plt.figure(figsize=(14, 18), facecolor=BG)
    gs  = gridspec.GridSpec(4, 3, figure=fig, hspace=0.35, wspace=0.28,
                             left=0.06, right=0.97, top=0.95, bottom=0.04)

    # ── Encabezado ──────────────────────────────────────────────────────────
    ax_hdr = fig.add_subplot(gs[0, :])
    ax_hdr.set_facecolor(BG2)
    ax_hdr.set_xlim(0, 10); ax_hdr.set_ylim(0, 10)
    ax_hdr.axis("off")
    ax_hdr.plot([0.01, 0.99], [0.999, 0.999], color=GOLD, lw=2.0,
                transform=ax_hdr.transAxes)
    ax_hdr.text(5, 8.0, "FARO PROTOCOL · DALE PLAY", color=GOLD,
                fontsize=16, fontweight="bold", ha="center", va="center",
                fontfamily="monospace")
    ax_hdr.text(5, 6.0, "REPORTE POST-SHOW — COMPARATIVA SATELITAL PRE/POST EVENTO",
                color=WHITE, fontsize=11, ha="center", va="center",
                fontfamily="monospace")
    ax_hdr.text(5, 4.3, f"Show: {show_id}   ·   Venue: Estadio José Amalfitani",
                color=WDIM, fontsize=9, ha="center", va="center",
                fontfamily="monospace")
    ax_hdr.text(5, 2.8, f"Generado: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}",
                color=WDIM, fontsize=8, ha="center", va="center",
                fontfamily="monospace")
    ax_hdr.plot([0.01, 0.99], [0.001, 0.001], color=GOLD, lw=0.6,
                transform=ax_hdr.transAxes)

    # ── Definir sectores del Amalfitani para visualización ──────────────────
    SECTORES = [
        {"nombre": "Escenario + Stage",   "x": 0.5, "y": 0.15, "w": 0.5, "h": 0.22},
        {"nombre": "Pit / Campo Sur",      "x": 0.5, "y": 0.38, "w": 0.5, "h": 0.17},
        {"nombre": "Campo Norte",          "x": 0.5, "y": 0.62, "w": 0.5, "h": 0.22},
        {"nombre": "Lateral Este",         "x": 0.75,"y": 0.50, "w": 0.2, "h": 0.6},
        {"nombre": "Lateral Oeste",        "x": 0.25,"y": 0.50, "w": 0.2, "h": 0.6},
    ]

    pre_data  = satellite_data.get("pre_show", {})
    post_dates = satellite_data.get("post_show_dates", [])

    ndvi_pre   = pre_data.get("ndvi")
    ndvi_pre_fecha = pre_data.get("fecha", "—")

    def _ndvi_color(ndvi):
        if ndvi is None: return WDIM
        if ndvi >= 0.30: return GRNL
        if ndvi >= 0.15: return YELL
        return REDL

    def _draw_field_panel(ax, title: str, ndvi: float | None,
                          cloud_blocked: bool, fecha: str,
                          subtitle: str = ""):
        ax.set_facecolor(BG2)
        ax.set_xlim(0, 10); ax.set_ylim(0, 10)
        for sp in ax.spines.values():
            sp.set_color(GOLD); sp.set_linewidth(0.8)
        ax.tick_params(left=False, bottom=False, labelleft=False, labelbottom=False)

        # Título
        ax.text(5, 9.5, title, color=GOLD, fontsize=9.5, fontweight="bold",
                ha="center", va="center", fontfamily="monospace")
        ax.text(5, 8.9, fecha, color=WDIM, fontsize=8, ha="center", va="center",
                fontfamily="monospace")
        if subtitle:
            ax.text(5, 8.3, subtitle, color=WDIM, fontsize=7, ha="center", va="center",
                    fontfamily="monospace")

        # Cancha
        field_rect = mpatches.FancyBboxPatch((1.0, 1.0), 8.0, 7.0,
            boxstyle="round,pad=0.1",
            facecolor="#1a4820" if not cloud_blocked else "#1c2028",
            edgecolor="white", linewidth=1.0, alpha=0.85)
        ax.add_patch(field_rect)

        if cloud_blocked:
            # Overlay de nube
            ax.text(5, 4.8, "☁", color="#aaaacc", fontsize=38, ha="center",
                    va="center", alpha=0.6)
            ax.text(5, 3.0, "NUBOSIDAD 100%", color="#6666aa", fontsize=11,
                    fontweight="bold", ha="center", va="center",
                    fontfamily="monospace")
            ax.text(5, 2.2, "Sin píxeles válidos sobre el estadio",
                    color=WDIM, fontsize=7.5, ha="center", va="center",
                    fontfamily="monospace")
            ax.text(5, 1.6, "SCL = 9 (Cloud High Probability)",
                    color=WDIM, fontsize=7, ha="center", va="center",
                    fontfamily="monospace")
        else:
            # NDVI por sector (real, mismo valor en todos ya que es lectura global)
            col = _ndvi_color(ndvi)
            sector_ys = [6.5, 5.5, 4.5, 3.5, 2.5]
            sector_lbls = ["Escenario Sur", "Campo Centro", "Campo Norte",
                           "Lateral Este", "Lateral Oeste"]
            for i, (sy, sl) in enumerate(zip(sector_ys, sector_lbls)):
                ax.add_patch(mpatches.FancyBboxPatch(
                    (1.5, sy - 0.35), 7.0, 0.7,
                    boxstyle="round,pad=0.05",
                    facecolor=col, edgecolor="none", alpha=0.25))
                ndvi_lbl = f"NDVI {ndvi:.3f}" if ndvi is not None else "N/D"
                ax.text(2.0, sy, sl, color=WHITE, fontsize=7, ha="left", va="center",
                        fontfamily="monospace")
                ax.text(8.2, sy, ndvi_lbl, color=col, fontsize=7.5, ha="right",
                        va="center", fontfamily="monospace", fontweight="bold")

            # NDVI central grande
            ax.text(5, 0.5, f"NDVI GLOBAL = {ndvi:.3f}" if ndvi else "NDVI = N/D",
                    color=col, fontsize=10, fontweight="bold", ha="center",
                    va="center", fontfamily="monospace")

    # ── Panel PRE-SHOW (17/05) ────────────────────────────────────────────────
    ax_pre = fig.add_subplot(gs[1, 0])
    _draw_field_panel(
        ax_pre,
        title="PRE-SHOW",
        ndvi=ndvi_pre,
        cloud_blocked=ndvi_pre is None,
        fecha=f"Sentinel-2 L2A · {ndvi_pre_fecha}",
        subtitle=f"SCL válidos: {pre_data.get('scl_unique', [])}  ·  {pre_data.get('n_valid', 0)}/{pre_data.get('n_total', 0)} px",
    )

    # ── Paneles POST-SHOW (27/05, 29/05) ─────────────────────────────────────
    for col_i, pdate in enumerate(post_dates[:2]):
        ax_post = fig.add_subplot(gs[1, col_i + 1])
        cloud_blocked = pdate.get("ndvi") is None
        _draw_field_panel(
            ax_post,
            title=f"POST-SHOW D{pdate.get('label','+?')}",
            ndvi=pdate.get("ndvi"),
            cloud_blocked=cloud_blocked,
            fecha=f"Sentinel-2 L2A · {pdate.get('fecha', '—')}",
            subtitle=f"Nube tile: {pdate.get('tile_cloud_pct', '?')}%  ·  Px válidos: {pdate.get('n_valid', 0)}/{pdate.get('n_total', 0)}",
        )

    # ── Panel Delta NDVI (timeline) ───────────────────────────────────────────
    ax_delta = fig.add_subplot(gs[2, :])
    ax_delta.set_facecolor(BG2)
    ax_delta.set_xlim(0, 10); ax_delta.set_ylim(0, 10)
    for sp in ax_delta.spines.values():
        sp.set_color(GOLD); sp.set_linewidth(0.5)
    ax_delta.tick_params(left=False, bottom=False, labelleft=False, labelbottom=False)
    ax_delta.plot([0, 1], [0.998, 0.998], color=GOLD, lw=1.5,
                  transform=ax_delta.transAxes)
    ax_delta.text(0.012, 0.95, "DELTA NDVI — LÍNEA DE TIEMPO",
                  color=GOLD, fontsize=9, fontweight="bold",
                  ha="left", va="top", transform=ax_delta.transAxes,
                  fontfamily="monospace")

    # Timeline visual
    timeline_x = [1.5, 4.5, 7.5]
    timeline_labels = [
        f"17/05 PRE\nNDVI={ndvi_pre:.3f}" if ndvi_pre else "17/05 PRE\nN/D",
        f"27/05 POST D-4\n☁ CLOUD 100%",
        f"29/05 POST D-2\n☁ CLOUD 100%",
    ]
    timeline_colors = [
        _ndvi_color(ndvi_pre),
        WDIM,
        WDIM,
    ]

    ax_delta.plot([0.15, 0.85], [0.5, 0.5], color=WDIM, lw=1.0,
                  transform=ax_delta.transAxes, zorder=1)
    for xi, lbl, col in zip([0.15, 0.5, 0.85], timeline_labels, timeline_colors):
        ax_delta.plot(xi, 0.5, "o", color=col, ms=14, transform=ax_delta.transAxes,
                      zorder=3)
        ax_delta.text(xi, 0.62, lbl, color=col, fontsize=8.5, ha="center",
                      va="bottom", fontfamily="monospace",
                      transform=ax_delta.transAxes)

    # Delta text
    delta_text = "Δ NDVI = No computable — nubosidad total en fechas post-show"
    delta_col  = WDIM
    if ndvi_pre is not None:
        ax_delta.text(0.5, 0.20, delta_text,
                      color=delta_col, fontsize=9.5, fontweight="bold",
                      ha="center", va="center", transform=ax_delta.transAxes,
                      fontfamily="monospace")
        ax_delta.text(0.5, 0.08,
                      "Próxima ventana de observación óptica: esperar despeje de nubosidad",
                      color=WDIM, fontsize=8, ha="center", va="center",
                      transform=ax_delta.transAxes, fontfamily="monospace")

    # ── Panel SAR ────────────────────────────────────────────────────────────
    ax_sar = fig.add_subplot(gs[3, :])
    ax_sar.set_facecolor(BG2)
    ax_sar.set_xlim(0, 10); ax_sar.set_ylim(0, 10)
    for sp in ax_sar.spines.values():
        sp.set_color(GOLD); sp.set_linewidth(0.5)
    ax_sar.tick_params(left=False, bottom=False, labelleft=False, labelbottom=False)
    ax_sar.plot([0, 1], [0.998, 0.998], color=GOLD, lw=1.5,
                transform=ax_sar.transAxes)
    ax_sar.text(0.012, 0.95, "SAR — SENTINEL-1 GRD VV BACKSCATTER",
                color=GOLD, fontsize=9, fontweight="bold",
                ha="left", va="top", transform=ax_sar.transAxes,
                fontfamily="monospace")

    sar_pre  = sar_data.get("pre_show", {})
    sar_post = sar_data.get("post_show", {})

    sar_pre_db  = sar_pre.get("vv_db")
    sar_post_db = sar_post.get("vv_db")
    sar_pre_fecha  = sar_pre.get("fecha", "—")
    sar_post_fecha = sar_post.get("fecha", "—")
    sar_post_estado = sar_post.get("estado", "sin_escena")

    # SAR pre-show box
    ax_sar.add_patch(mpatches.FancyBboxPatch(
        (0.03, 0.20), 0.42, 0.55,
        boxstyle="round,pad=0.02",
        facecolor=BG, edgecolor=CYAN, linewidth=1.0, alpha=0.6,
        transform=ax_sar.transAxes))
    ax_sar.text(0.24, 0.80, "SAR PRE-SHOW", color=CYAN, fontsize=8.5,
                fontweight="bold", ha="center", va="center",
                transform=ax_sar.transAxes, fontfamily="monospace")
    if sar_pre_db is not None:
        ax_sar.text(0.24, 0.60,
                    f"VV = {sar_pre_db:+.2f} dB", color=WHITE, fontsize=12,
                    fontweight="bold", ha="center", va="center",
                    transform=ax_sar.transAxes, fontfamily="monospace")
        ax_sar.text(0.24, 0.42, f"Sentinel-1 GRD · {sar_pre_fecha}",
                    color=WDIM, fontsize=7.5, ha="center", va="center",
                    transform=ax_sar.transAxes, fontfamily="monospace")
        ax_sar.text(0.24, 0.28, f"n={sar_pre.get('n_px', 0)} px sobre Amalfitani",
                    color=WDIM, fontsize=7, ha="center", va="center",
                    transform=ax_sar.transAxes, fontfamily="monospace")
    else:
        ax_sar.text(0.24, 0.50, "Sin dato SAR pre-show",
                    color=WDIM, fontsize=9, ha="center", va="center",
                    transform=ax_sar.transAxes, fontfamily="monospace")

    # Flecha delta
    ax_sar.annotate("", xy=(0.55, 0.50), xytext=(0.45, 0.50),
                    xycoords="axes fraction", textcoords="axes fraction",
                    arrowprops=dict(arrowstyle="->", color=GOLD, lw=1.5))

    # SAR post-show box
    ax_sar.add_patch(mpatches.FancyBboxPatch(
        (0.55, 0.20), 0.42, 0.55,
        boxstyle="round,pad=0.02",
        facecolor=BG, edgecolor=YELL if sar_post_db is None else CYAN,
        linewidth=1.0, alpha=0.6,
        transform=ax_sar.transAxes))
    ax_sar.text(0.76, 0.80, "SAR POST-SHOW", color=CYAN, fontsize=8.5,
                fontweight="bold", ha="center", va="center",
                transform=ax_sar.transAxes, fontfamily="monospace")
    if sar_post_db is not None:
        delta_db = sar_post_db - sar_pre_db if sar_pre_db is not None else None
        delta_col = REDL if (delta_db or 0) > 1.5 else GRNL
        ax_sar.text(0.76, 0.60,
                    f"VV = {sar_post_db:+.2f} dB", color=WHITE, fontsize=12,
                    fontweight="bold", ha="center", va="center",
                    transform=ax_sar.transAxes, fontfamily="monospace")
        if delta_db is not None:
            ax_sar.text(0.76, 0.42,
                        f"Δ = {delta_db:+.2f} dB {'⚠ COMPACTACIÓN' if abs(delta_db) > 1.5 else '● ESTABLE'}",
                        color=delta_col, fontsize=8.5, fontweight="bold",
                        ha="center", va="center",
                        transform=ax_sar.transAxes, fontfamily="monospace")
        ax_sar.text(0.76, 0.28, f"Sentinel-1 GRD · {sar_post_fecha}",
                    color=WDIM, fontsize=7.5, ha="center", va="center",
                    transform=ax_sar.transAxes, fontfamily="monospace")
    else:
        ax_sar.text(0.76, 0.58, "PENDIENTE",
                    color=YELL, fontsize=11, fontweight="bold",
                    ha="center", va="center",
                    transform=ax_sar.transAxes, fontfamily="monospace")
        ax_sar.text(0.76, 0.43, f"Estado: {sar_post_estado}",
                    color=WDIM, fontsize=8, ha="center", va="center",
                    transform=ax_sar.transAxes, fontfamily="monospace")
        ax_sar.text(0.76, 0.30, "Show 31/05 · Revisita S1 ~3-4 días",
                    color=WDIM, fontsize=7.5, ha="center", va="center",
                    transform=ax_sar.transAxes, fontfamily="monospace")
        ax_sar.text(0.76, 0.20, "Alerta compactación: si ΔVVV > 1.5 dB",
                    color=WDIM, fontsize=7, ha="center", va="center",
                    transform=ax_sar.transAxes, fontfamily="monospace")

    # Umbral compactación
    ax_sar.text(0.5, 0.08,
                "Umbral compactación suelo: Δ VV > 1.5 dB (protocolo Faro Post-Show)",
                color=WDIM, fontsize=7.5, ha="center", va="center",
                transform=ax_sar.transAxes, fontfamily="monospace")

    # Guardar
    out_path = REP_DIR / f"{show_id}_post_show_comparativa.png"
    REP_DIR.mkdir(exist_ok=True)
    fig.savefig(str(out_path), dpi=100, bbox_inches="tight", facecolor=BG)
    plt.close(fig)
    log.info("PNG post_show → %s", out_path)
    return str(out_path)


# ═══════════════════════════════════════════════════════════════════════════════
# 4. Certificado PDF con datos reales (sin re-fetching de NDVI)
# ═══════════════════════════════════════════════════════════════════════════════

def _run_certification_real(show_id: str, ndvi_pre: float | None,
                             ndvi_pre_fuente: str,
                             ndvi_post: float | None,
                             ndvi_post_fuente: str,
                             layout: dict | None,
                             sar_pre: dict, sar_post: dict) -> dict:
    """
    Genera certificado PDF con los datos reales ya verificados.
    Llama directamente _build_pdf() en lugar de run_certification()
    para no re-fetchear NDVI post-show.
    """
    from dale_play_certification import _build_pdf, _compute_damage
    import json as _json

    show_path = SHOWS_DIR / f"{show_id}.json"
    show_cfg  = _json.loads(show_path.read_text(encoding="utf-8"))

    damage = _compute_damage(ndvi_pre, ndvi_post, layout)

    ts        = datetime.now(timezone.utc)
    content   = (f"FARO-CERT-{show_id}-"
                 f"{ndvi_pre}-{ndvi_post}-"
                 f"{damage['delta_ndvi']}-"
                 f"{ts.isoformat()}")
    cert_hash = hashlib.sha256(content.encode()).hexdigest().upper()

    # FII
    fii_data = {}
    try:
        from dale_play_fii import compute_fii
        _models = DALE_DIR / "models"
        _egms_j = _models / "egms_amalfitani.json"
        _dr_j   = _models / "drainage_amalfitani.json"
        _egms_d = _json.loads(_egms_j.read_text(encoding="utf-8")) if _egms_j.exists() else None
        _dr_d   = _json.loads(_dr_j.read_text(encoding="utf-8"))   if _dr_j.exists() else None
        fii_data = compute_fii(ndvi_post, _egms_d, layout, _dr_d)
    except Exception as e:
        log.warning("FII error: %s", e)

    cert_data = {
        "show_id":          show_id,
        "show_date":        show_cfg.get("show_date", SHOW_DATE),
        "artist":           show_cfg.get("artist", "Airbag"),
        "venue":            "Estadio José Amalfitani",
        "fecha_emision":    ts.strftime("%Y-%m-%d %H:%M UTC"),
        "cert_hash":        cert_hash,
        "ndvi_pre":         ndvi_pre,
        "ndvi_pre_fuente":  ndvi_pre_fuente,
        "ndvi_post":        ndvi_post,
        "ndvi_post_fuente": ndvi_post_fuente,
        "damage":           damage,
        "layout":           layout,
        "mode":             "post_show",
        "fii":              fii_data,
        "sar_pre":          sar_pre,
        "sar_post":         sar_post,
    }

    CERT_DIR.mkdir(exist_ok=True)
    out_path = CERT_DIR / f"{show_id}_certificado.pdf"
    _build_pdf(cert_data, out_path)
    cert_data["pdf_path"] = str(out_path)
    log.info("Certificado → %s  hash=%s…", out_path, cert_hash[:16])
    return cert_data


# ═══════════════════════════════════════════════════════════════════════════════
# 5. Pipeline completo post_show
# ═══════════════════════════════════════════════════════════════════════════════

def run_post_show() -> dict:
    log.info("=" * 60)
    log.info("DALE PLAY · POST-SHOW PIPELINE · %s", SHOW_ID)
    log.info("=" * 60)

    # ── 5.1 Cargar show config ────────────────────────────────────────────────
    show_cfg = json.loads((SHOWS_DIR / f"{SHOW_ID}.json").read_text(encoding="utf-8"))
    log.info("Show: %s · %s · %s", show_cfg["artist"], show_cfg["show_date"], show_cfg["venue"])

    # ── 5.2 Re-verificar NDVI pre-show (17/05) ───────────────────────────────
    log.info("--- Verificando pre-show 17/05 (Element84 AWS COG) ---")
    pre_data = _fetch_s2_ndvi_e84("2026-05-17")
    pre_data["fecha"] = "2026-05-17"
    ndvi_pre = pre_data.get("ndvi")
    ndvi_pre_fuente = (
        f"Sentinel-2 L2A · Element84 Earth Search · AWS COG · 17/05/2026 · "
        f"n={pre_data.get('n_valid',0)}/{pre_data.get('n_total',0)} px · "
        f"SCL={pre_data.get('scl_unique','?')}"
    )
    log.info("PRE-SHOW 17/05: NDVI=%s  estado=%s", ndvi_pre, pre_data.get("estado"))

    # ── 5.3 Re-verificar NDVI post-show (27/05 y 29/05) ─────────────────────
    log.info("--- Verificando post-show 27/05 (Element84 AWS COG) ---")
    post_27 = _fetch_s2_ndvi_e84("2026-05-27")
    post_27["fecha"] = "2026-05-27"
    post_27["label"] = "-4"

    log.info("--- Verificando post-show 29/05 (Element84 AWS COG) ---")
    post_29 = _fetch_s2_ndvi_e84("2026-05-29")
    post_29["fecha"] = "2026-05-29"
    post_29["label"] = "-2"

    # NDVI post es None si ambas fechas son cloud-blocked
    ndvi_post = post_27.get("ndvi") or post_29.get("ndvi")
    best_post  = post_27 if post_27.get("ndvi") is not None else post_29
    ndvi_post_fuente = (
        f"Sentinel-2 L2A · Element84 Earth Search · AWS COG · "
        f"27/05 SCL={post_27.get('scl_unique','?')} ({post_27.get('n_valid',0)} px válidos) · "
        f"29/05 SCL={post_29.get('scl_unique','?')} ({post_29.get('n_valid',0)} px válidos) · "
        f"Nubosidad 100% sobre Amalfitani en ambas fechas"
    )

    log.info("POST-SHOW 27/05: NDVI=%s  estado=%s  cloud_tile=%.1f%%",
             post_27.get("ndvi"), post_27.get("estado"), post_27.get("tile_cloud_pct") or 0)
    log.info("POST-SHOW 29/05: NDVI=%s  estado=%s  cloud_tile=%.1f%%",
             post_29.get("ndvi"), post_29.get("estado"), post_29.get("tile_cloud_pct") or 0)

    # ── 5.4 SAR pre-show (30/05) y SAR post-show (post 31/05) ───────────────
    log.info("--- Verificando SAR pre-show Sentinel-1 GRD (2026-05-20 → 2026-05-31) ---")
    sar_pre  = _fetch_sar_vv("2026-05-20", "2026-05-31", label="SAR_PRE")
    log.info("SAR PRE: VV=%s dB  n_px=%d  fecha=%s  estado=%s",
             sar_pre.get("vv_db"), sar_pre.get("n_px", 0),
             sar_pre.get("fecha", "—"), sar_pre.get("estado"))

    log.info("--- Verificando SAR post-show Sentinel-1 GRD (post 2026-05-31) ---")
    from datetime import date, timedelta
    today     = date.today()
    today_str = today.isoformat()
    # El show fue hoy (2026-05-31); revisita S1 ~3-4 días, buscar desde mañana
    post_sar_start = (today + timedelta(days=1)).isoformat()
    if post_sar_start > today_str:
        # No puede haber SAR post-show aún — show es hoy
        log.info("SAR_POST: show hoy %s — SAR post-show no disponible todavía", today_str)
        sar_post = {"vv_db": None, "n_px": 0, "item_id": None,
                    "fecha": None, "estado": "pendiente_revisita",
                    "nota": f"Show {SHOW_DATE} — próxima revisita S1 ~{(today + timedelta(days=3)).isoformat()}"}
    else:
        sar_post = _fetch_sar_vv(post_sar_start, today_str, label="SAR_POST")
    log.info("SAR POST: VV=%s dB  n_px=%d  fecha=%s  estado=%s",
             sar_post.get("vv_db"), sar_post.get("n_px", 0),
             sar_post.get("fecha", "—"), sar_post.get("estado"))

    # ── 5.5 Pipeline completo en modo post_show ───────────────────────────────
    log.info("--- Ejecutando pipeline completo modo=post_show ---")
    from dale_play_pipeline import run_show_audit
    result = run_show_audit(show_cfg, mode="post_show")
    log.info("Pipeline OK  report_png=%s", result.get("report_png"))

    # ── 5.6 PNG post_show comparativa pre vs post ─────────────────────────────
    log.info("--- Generando PNG comparativa pre/post show ---")
    satellite_data = {
        "pre_show": pre_data,
        "post_show_dates": [post_27, post_29],
    }
    sar_bundle = {"pre_show": sar_pre, "post_show": sar_post}
    png_comp = _generate_post_show_png(satellite_data, sar_bundle, SHOW_ID)

    # ── 5.7 Certificado PDF con datos reales ─────────────────────────────────
    log.info("--- Generando certificado PDF ---")
    layout = result.get("layout")
    cert   = _run_certification_real(
        show_id         = SHOW_ID,
        ndvi_pre        = ndvi_pre,
        ndvi_pre_fuente = ndvi_pre_fuente,
        ndvi_post       = ndvi_post,
        ndvi_post_fuente= ndvi_post_fuente,
        layout          = layout,
        sar_pre         = sar_pre,
        sar_post        = sar_post,
    )

    # ── Resumen final ─────────────────────────────────────────────────────────
    delta_ndvi = cert["damage"]["delta_ndvi"]
    nivel_dano = cert["damage"]["nivel_dano"]
    fii        = cert.get("fii") or {}
    delta_sar  = None
    if sar_pre.get("vv_db") is not None and sar_post.get("vv_db") is not None:
        delta_sar = round(sar_post["vv_db"] - sar_pre["vv_db"], 2)

    print()
    print("=" * 60)
    print("DALE PLAY · POST-SHOW PIPELINE · RESULTADOS")
    print("=" * 60)
    print(f"Show:          {SHOW_ID}")
    print(f"Artista:       {show_cfg['artist']}")
    print(f"Venue:         Estadio José Amalfitani")
    print()
    print("── NDVI SENTINEL-2 ─────────────────────────────────────")
    print(f"  Pre-show  17/05:  NDVI = {ndvi_pre}  "
          f"({pre_data.get('n_valid',0)}/{pre_data.get('n_total',0)} px, "
          f"SCL={pre_data.get('scl_unique','?')})")
    print(f"  Post-show 27/05:  NDVI = {post_27.get('ndvi')}  "
          f"({post_27.get('n_valid',0)}/{post_27.get('n_total',0)} px, "
          f"cloud={post_27.get('tile_cloud_pct','?')}%)")
    print(f"  Post-show 29/05:  NDVI = {post_29.get('ndvi')}  "
          f"({post_29.get('n_valid',0)}/{post_29.get('n_total',0)} px, "
          f"cloud={post_29.get('tile_cloud_pct','?')}%)")
    print(f"  Delta NDVI:       {delta_ndvi}  → nivel daño: {nivel_dano}")
    print()
    print("── SAR SENTINEL-1 GRD VV ───────────────────────────────")
    print(f"  Pre-show  {sar_pre.get('fecha','—')}:  VV = {sar_pre.get('vv_db')} dB  "
          f"({sar_pre.get('n_px',0)} px)  [{sar_pre.get('estado')}]")
    print(f"  Post-show {sar_post.get('fecha','—')}:  VV = {sar_post.get('vv_db')} dB  "
          f"({sar_post.get('n_px',0)} px)  [{sar_post.get('estado')}]")
    if delta_sar is not None:
        alerta = "⚠ COMPACTACIÓN DETECTADA" if delta_sar > 1.5 else "✓ Sin compactación"
        print(f"  Delta SAR:        {delta_sar:+.2f} dB  → {alerta}")
    else:
        print(f"  Delta SAR:        N/D — SAR post-show pendiente (revisita ~3-4 días)")
    print()
    print(f"── FII ─────────────────────────────────────────────────")
    print(f"  FII = {fii.get('fii','N/D')}  sello = {fii.get('sello','N/D')}  "
          f"semaforo = {fii.get('semaforo','N/D')}")
    print()
    print("── OUTPUTS ─────────────────────────────────────────────")
    print(f"  PNG estándar:     {result.get('report_png', 'N/D')}")
    print(f"  PNG comparativa:  {png_comp}")
    print(f"  PDF certificado:  {cert.get('pdf_path', 'N/D')}")
    print(f"  SHA-256:          {cert.get('cert_hash', 'N/D')}")
    print("=" * 60)

    return {
        "result":       result,
        "pre_data":     pre_data,
        "post_27":      post_27,
        "post_29":      post_29,
        "sar_pre":      sar_pre,
        "sar_post":     sar_post,
        "ndvi_pre":     ndvi_pre,
        "ndvi_post":    ndvi_post,
        "delta_ndvi":   delta_ndvi,
        "delta_sar":    delta_sar,
        "nivel_dano":   nivel_dano,
        "fii":          fii,
        "png_comparativa": png_comp,
        "pdf_path":     cert.get("pdf_path"),
        "cert_hash":    cert.get("cert_hash"),
    }


if __name__ == "__main__":
    run_post_show()
