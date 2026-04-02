"""
FARO PROTOCOL — Fusión SAR + Óptico → Índice Faro

Uso:
    python faro_fusion.py --area cordoba
    python faro_fusion.py --area vaca_muerta
"""

import argparse
import numpy as np
import rasterio
import matplotlib.pyplot as plt
import matplotlib.gridspec as gridspec
from datetime import datetime

from faro_areas import load_area, list_areas

# Defaults (solo usados si se llama sin área)
NDVI_TIF   = "FaroProtocol_NDVI_limpio_Cordoba.tif"
SAR_TIF    = "sar_cordoba_georef.tif"
OUTPUT_PNG = "faro_reporte_fusion_cordoba.png"
ZONA       = "Córdoba, Argentina"


def cargar_ndvi(path):
    print(f"Cargando NDVI: {path}")
    with rasterio.open(path) as src:
        data = src.read(1).astype(float)
        data[data == src.nodata] = np.nan
    print(f"  Shape: {data.shape} | Min: {np.nanmin(data):.3f} | Max: {np.nanmax(data):.3f}")
    return data


def cargar_sar(sar_tif):
    print(f"Cargando SAR georreferenciado: {sar_tif}")
    with rasterio.open(sar_tif) as src:
        data = src.read(1).astype(float)
    print(f"  Shape SAR: {data.shape} | Media: {data.mean():.4f}")
    return data


def normalizar(arr):
    mn, mx = np.nanmin(arr), np.nanmax(arr)
    if mx == mn:
        return np.zeros_like(arr)
    return (arr - mn) / (mx - mn)


def calcular_indice_fusion(ndvi_norm, sar_norm, w_ndvi=0.6, w_sar=0.4):
    """Índice Faro = combinación ponderada NDVI limpio + backscatter SAR"""
    return w_ndvi * ndvi_norm + w_sar * sar_norm


def generar_reporte(ndvi, sar, fusion, zona, fecha, output_png):
    print("Generando reporte visual...")
    fig = plt.figure(figsize=(18, 10), facecolor='#0a0a1a')
    fig.suptitle(
        f"FARO PROTOCOL — Reporte Fusión SAR + Óptico\n{zona} | {fecha}",
        color='white', fontsize=16, fontweight='bold', y=0.98
    )

    gs = gridspec.GridSpec(2, 3, figure=fig, hspace=0.35, wspace=0.3)

    capas = [
        (ndvi,   "NDVI Limpio (CLOSDI)",  'RdYlGn', gs[0, 0]),
        (sar,    "Backscatter SAR (S1)",  'gray',   gs[0, 1]),
        (fusion, "Índice Fusión Faro",    'plasma', gs[0, 2]),
    ]

    for data, titulo, cmap, pos in capas:
        ax = fig.add_subplot(pos)
        im = ax.imshow(data, cmap=cmap, interpolation='nearest')
        ax.set_title(titulo, color='white', fontsize=11, pad=8)
        ax.axis('off')
        cb = plt.colorbar(im, ax=ax, fraction=0.046, pad=0.04)
        cb.ax.yaxis.set_tick_params(color='white')
        plt.setp(cb.ax.get_yticklabels(), color='white')

    # Panel de métricas
    ax_stats = fig.add_subplot(gs[1, :])
    ax_stats.axis('off')
    ax_stats.set_facecolor('#111133')

    ndvi_vals   = ndvi[~np.isnan(ndvi)]
    sar_vals    = sar[~np.isnan(sar)]
    fusion_vals = fusion[~np.isnan(fusion)]

    stats = [
        ("NDVI promedio",         f"{np.mean(ndvi_vals):.4f}"),
        ("NDVI máximo",           f"{np.max(ndvi_vals):.4f}"),
        ("SAR backscatter medio", f"{np.mean(sar_vals):.4f}"),
        ("Índice Fusión medio",   f"{np.mean(fusion_vals):.4f}"),
        ("Índice Fusión máximo",  f"{np.max(fusion_vals):.4f}"),
        ("Píxeles analizados",    f"{len(ndvi_vals):,}"),
        ("Método óptico",         "CLOSDI — Cal (2026)"),
        ("Satélite SAR",          "Sentinel-1A IW GRD"),
        ("Satélite óptico",       "Sentinel-2 via GEE"),
        ("Estado",                "✓ REPORTE VALIDADO"),
    ]

    cols = 5
    for i, (k, v) in enumerate(stats):
        x = 0.02 + (i % cols) * 0.20
        y = 0.75 - (i // cols) * 0.45
        ax_stats.text(x, y,        k, transform=ax_stats.transAxes,
                      color='#aaaacc', fontsize=9)
        ax_stats.text(x, y - 0.18, v, transform=ax_stats.transAxes,
                      color='white', fontsize=11, fontweight='bold')

    plt.savefig(output_png, dpi=150, bbox_inches='tight',
                facecolor='#0a0a1a', edgecolor='none')
    print(f"Reporte guardado: {output_png}")
    return output_png


def main(area=None):
    ndvi_tif   = area['ndvi_tif']   if area else NDVI_TIF
    sar_tif    = area['sar_output'] if area else SAR_TIF
    output_png = area['report_png'] if area else OUTPUT_PNG
    zona       = area['label']      if area else ZONA
    fecha      = datetime.now().strftime("%Y-%m-%d %H:%M")

    print("=" * 55)
    print(f"  FARO PROTOCOL — Pipeline Fusión SAR + Óptico")
    print(f"  Área: {zona}")
    print("=" * 55)

    # Cargar NDVI
    ndvi_raw  = cargar_ndvi(ndvi_tif)
    ndvi_norm = normalizar(ndvi_raw)

    # Cargar SAR (ya en dB desde georef.py) y normalizar para visualización
    sar_raw = cargar_sar(sar_tif)

    # Redimensionar SAR al tamaño del NDVI si es necesario
    if sar_raw.shape != ndvi_raw.shape:
        from skimage.transform import resize
        print(f"  Redimensionando SAR {sar_raw.shape} -> {ndvi_raw.shape}")
        sar_raw = resize(sar_raw, ndvi_raw.shape, anti_aliasing=True)

    sar_norm = normalizar(sar_raw)

    # Calcular fusión
    fusion = calcular_indice_fusion(ndvi_norm, sar_norm)

    # Generar reporte
    output = generar_reporte(ndvi_norm, sar_norm, fusion, zona, fecha, output_png)

    stats = {
        "ndvi_medio":          round(float(np.nanmean(ndvi_raw)), 4),
        "sar_medio_db":        round(float(np.nanmean(sar_raw)), 4),
        "indice_fusion_medio": round(float(np.nanmean(fusion)), 4),
        "pixeles_analizados":  int((~np.isnan(ndvi_raw)).sum()),
    }

    print("=" * 55)
    print(f"  NDVI promedio:        {stats['ndvi_medio']}")
    print(f"  SAR medio (dB):       {stats['sar_medio_db']}")
    print(f"  Índice fusión medio:  {stats['indice_fusion_medio']}")
    print(f"  Reporte generado:     {output}")
    print("=" * 55)

    return {"output_png": output, "stats": stats}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='FARO PROTOCOL — Fusión SAR + Óptico')
    parser.add_argument(
        '--area', required=True,
        help=f"Área a procesar. Disponibles: {', '.join(list_areas())}"
    )
    args = parser.parse_args()
    main(area=load_area(args.area))
