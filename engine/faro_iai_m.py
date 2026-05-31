"""
FARO PROTOCOL — Índice de Actividad Industrial Metálica (IAI-M)
===============================================================
Detecta firmas espectrales de infraestructura metálica a escala de
facilidad usando SAR backscatter + NDVI inverso.

Principio físico:
  - Alta retroreflexión SAR (sigma_0 alto en dB) → superficie rugosa/metálica
  - NDVI bajo → sin vegetación → compatible con metal/hormigón/arena
  - IAI-M = SAR_norm × (1 − NDVI_norm)  → score 0-100

Resolución mínima detectable: ~50 m² con Sentinel-2 @ 10m.
Los tornillos individuales (< 5 cm) están fuera del alcance físico
de cualquier sensor gratuito ESA/NASA. IAI-M detecta FACILIDADES,
no hardware individual.

Uso:
    python engine/faro_iai_m.py --area vaca_muerta
    python engine/faro_iai_m.py --area vaca_muerta --save-map --save-tif
"""

import argparse
import json
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.colors as mcolors
import matplotlib.pyplot as plt
import numpy as np
import rasterio
from rasterio.crs import CRS
from rasterio.enums import Resampling
from rasterio.warp import reproject, transform_bounds

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

try:
    import h3
    _H3_OK = True
except ImportError:
    _H3_OK = False
    print("[IAI-M] h3 no instalado — indexación H3 omitida", flush=True)

PROJECT_ROOT = Path(__file__).parent.parent
WGS84        = CRS.from_epsg(4326)

# SAR típico en Vaca Muerta (Sentinel-1 C-band, VV)
SAR_MIN, SAR_MAX   = -25.0, -2.0   # dB
NDVI_MIN, NDVI_MAX = -0.2,   0.85
IAI_THRESHOLD      = 60.0           # > 60 = "Alta actividad metálica"
H3_RESOLUTION      = 12             # ~9.4 m borde / ~330 m² por celda


class FaroIAIM:
    def __init__(self):
        pass

    def compute(self, area_slug: str, save_map: bool = False,
                save_tif: bool = False) -> dict:
        cfg = self._load_cfg(area_slug)
        ndvi_path = PROJECT_ROOT / cfg.get("ndvi_tif", f"FaroProtocol_NDVI_limpio_{area_slug}.tif")
        sar_path  = PROJECT_ROOT / cfg.get("sar_output", f"sar_{area_slug}_georef.tif")

        ndvi, meta = self._load_band(ndvi_path)
        if ndvi is None:
            return {"error": f"NDVI no encontrado: {ndvi_path.name}", "score": None}

        sar = self._load_and_align(sar_path, ndvi, meta) if sar_path.exists() else None

        iai_m = self._compute_index(ndvi, sar)
        score = float(np.nanmean(iai_m[np.isfinite(iai_m)]))

        result = {
            "area":           area_slug,
            "iai_m_score":    round(score, 2),
            "iai_m_max":      round(float(np.nanmax(iai_m)), 2),
            "iai_m_pct_high": round(float(np.mean(iai_m[np.isfinite(iai_m)] > IAI_THRESHOLD) * 100), 1),
            "sar_used":       sar is not None,
            "h3_cells":       [],
        }

        if _H3_OK:
            result["h3_cells"] = self._h3_index(iai_m, meta)

        if save_map:
            map_path = self._save_map(iai_m, area_slug, score, cfg)
            result["map_png"] = str(map_path)

        if save_tif:
            tif_path = self._save_tif(iai_m, meta, area_slug)
            result["iai_m_tif"] = str(tif_path)

        return result

    # ── Loaders ──────────────────────────────────────────────────────────────

    def _load_cfg(self, slug: str) -> dict:
        p = PROJECT_ROOT / "faro_areas" / f"{slug}.json"
        if p.exists():
            with open(p, encoding="utf-8") as f:
                return json.load(f)
        return {}

    def _load_band(self, path: Path) -> tuple[np.ndarray | None, dict | None]:
        if not path.exists():
            return None, None
        with rasterio.open(path) as src:
            data = src.read(1).astype(np.float32)
            meta = src.meta.copy()
            nodata = src.nodata
        if nodata is not None:
            data[data == nodata] = np.nan
        # NDVI int16 → float (escala /10000 típica de CDSE exports)
        if np.nanmax(np.abs(data)) > 2:
            data = data / 10_000.0
        return data, meta

    def _load_and_align(self, path: Path, ref: np.ndarray, ref_meta: dict) -> np.ndarray:
        """Reprojecta y resamplea SAR al mismo grid que NDVI."""
        dst = np.full_like(ref, np.nan)
        with rasterio.open(path) as src:
            reproject(
                source=rasterio.band(src, 1),
                destination=dst,
                src_transform=src.transform,
                src_crs=src.crs,
                dst_transform=ref_meta["transform"],
                dst_crs=ref_meta["crs"],
                resampling=Resampling.bilinear,
                src_nodata=src.nodata,
                dst_nodata=np.nan,
            )
        return dst

    # ── Índice ────────────────────────────────────────────────────────────────

    def _compute_index(self, ndvi: np.ndarray, sar: np.ndarray | None) -> np.ndarray:
        ndvi_norm = np.clip((ndvi - NDVI_MIN) / (NDVI_MAX - NDVI_MIN), 0, 1)
        metal_ndvi = 1.0 - ndvi_norm  # alto cuando no hay vegetación

        if sar is not None:
            sar_norm = np.clip((sar - SAR_MIN) / (SAR_MAX - SAR_MIN), 0, 1)
            # Peso 60% SAR + 40% NDVI inverso
            raw = 0.60 * sar_norm + 0.40 * metal_ndvi
        else:
            # Solo NDVI inverso — proxy válido pero menos preciso
            raw = metal_ndvi

        iai_m = raw * 100.0
        iai_m[~np.isfinite(ndvi)] = np.nan
        return iai_m

    # ── H3 indexación ────────────────────────────────────────────────────────

    def _h3_index(self, iai_m: np.ndarray, meta: dict) -> list[dict]:
        """Retorna las celdas H3 nivel 12 con IAI-M > threshold."""
        transform = meta["transform"]
        rows, cols = np.where(np.isfinite(iai_m) & (iai_m > IAI_THRESHOLD))
        if len(rows) == 0:
            return []

        cell_scores: dict[str, list] = {}
        # Sample each 5th pixel to keep performance reasonable
        for r, c in zip(rows[::5], cols[::5]):
            lon, lat = transform * (c + 0.5, r + 0.5)
            # Reproject pixel center to WGS84 if needed
            if meta["crs"] != WGS84:
                from pyproj import Transformer
                t = Transformer.from_crs(meta["crs"].to_epsg(), 4326, always_xy=True)
                lon, lat = t.transform(lon, lat)
            try:
                cell = h3.latlng_to_cell(lat, lon, H3_RESOLUTION)
            except Exception:
                try:
                    cell = h3.geo_to_h3(lat, lon, H3_RESOLUTION)  # h3 3.x fallback
                except Exception:
                    continue
            cell_scores.setdefault(cell, []).append(float(iai_m[r, c]))

        return [
            {"cell": cell, "iai_m_mean": round(float(np.mean(scores)), 1),
             "n_pixels": len(scores)}
            for cell, scores in sorted(cell_scores.items(),
                                       key=lambda kv: -np.mean(kv[1]))
        ]

    # ── Outputs ──────────────────────────────────────────────────────────────

    def _save_map(self, iai_m: np.ndarray, slug: str,
                  score: float, cfg: dict) -> Path:
        fig, ax = plt.subplots(figsize=(10, 8), facecolor="#06080b")
        ax.set_facecolor("#06080b")

        norm  = mcolors.Normalize(vmin=0, vmax=100)
        cmap  = plt.cm.YlOrRd
        im    = ax.imshow(iai_m, cmap=cmap, norm=norm, interpolation="nearest")

        # Contorno de actividad alta
        high = np.where(iai_m > IAI_THRESHOLD, 1, np.nan)
        ax.contour(high, levels=[0.5], colors=["#ff4500"], linewidths=1.2, alpha=0.85)

        cbar = fig.colorbar(im, ax=ax, fraction=0.035, pad=0.02)
        cbar.set_label("IAI-M  (0 = sin firma metálica · 100 = máxima actividad)",
                       color="#c9a84c", fontsize=9)
        cbar.ax.yaxis.set_tick_params(color="#c9a84c")
        plt.setp(cbar.ax.yaxis.get_ticklabels(), color="#c9a84c")

        ax.set_title(
            f"FARO PROTOCOL — IAI-M · {cfg.get('label', slug)}\n"
            f"Score medio: {score:.1f} / 100  ·  Sentinel-2 SWIR+NIR  ·  Resolución: 20 m",
            color="#c9a84c", fontsize=11, pad=12,
        )
        ax.axis("off")

        out = PROJECT_ROOT / f"iai_m_{slug}.png"
        plt.savefig(out, dpi=150, bbox_inches="tight",
                    facecolor="#06080b", edgecolor="none")
        plt.close(fig)
        print(f"[IAI-M] Mapa guardado: {out.name}")
        return out

    def _save_tif(self, iai_m: np.ndarray, meta: dict, slug: str) -> Path:
        out_meta = meta.copy()
        out_meta.update({"dtype": "float32", "count": 1, "nodata": np.nan})
        out = PROJECT_ROOT / f"iai_m_{slug}.tif"
        with rasterio.open(out, "w", **out_meta) as dst:
            dst.write(iai_m.astype(np.float32), 1)
        print(f"[IAI-M] GeoTIFF guardado: {out.name}")
        return out


# ── CLI ───────────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(description="Índice de Actividad Industrial Metálica")
    ap.add_argument("--area",     required=True, help="Slug del área (ej: vaca_muerta)")
    ap.add_argument("--save-map", action="store_true", help="Generar PNG del mapa IAI-M")
    ap.add_argument("--save-tif", action="store_true", help="Guardar GeoTIFF del índice")
    args = ap.parse_args()

    engine = FaroIAIM()
    result = engine.compute(args.area, save_map=args.save_map, save_tif=args.save_tif)

    print("\n── IAI-M ─────────────────────────────────────")
    for k, v in result.items():
        if k != "h3_cells":
            print(f"  {k:20s}: {v}")
    if result.get("h3_cells"):
        print(f"  {'h3_cells':20s}: {len(result['h3_cells'])} celdas activas (nivel 12)")
        for cell in result["h3_cells"][:5]:
            print(f"    · {cell['cell']}  IAI-M={cell['iai_m_mean']}")
    print("──────────────────────────────────────────────\n")


if __name__ == "__main__":
    main()
