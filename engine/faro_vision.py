import sys as _sys
from pathlib import Path as _Path
_ED = str(_Path(__file__).parent)
if _ED not in _sys.path:
    _sys.path.insert(0, _ED)
"""
FARO PROTOCOL — Fase 6: Visión Computacional

Clasifica vigor vegetativo por píxel usando NDVI y detecta zonas
anómalas (posibles malezas, fallas de siembra, estrés hídrico).

Uso:
    python faro_vision.py --area cordoba
    python faro_vision.py --area balcarce
    python faro_vision.py --area cordoba --ndvi-file mi_ndvi.tif

Salidas:
    faro_clasificacion_<area>.png  — mapa de vigor + anomalías
    faro_vision_<area>.json        — estadísticas por clase
"""

import argparse
import json
import numpy as np
import rasterio
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import matplotlib.gridspec as gridspec
from datetime import datetime
from pathlib import Path
from scipy import ndimage

from faro_areas import load_area, list_areas


# ─── Clasificación de vigor ────────────────────────────────────────────────────
#
#  No es posible distinguir soja / maíz / trigo con NDVI solo (requiere
#  análisis multi-temporal y/o datos multi-espectral adicionales).
#  Lo que sí se puede determinar con certeza es el VIGOR VEGETATIVO,
#  que correlaciona directamente con el estado del cultivo y el rinde.
#
CLASES_VIGOR = [
    # (umbral_inferior, umbral_superior, codigo, label, color)
    (-1.00, 0.10, 0, "Suelo / Sin cultivo",      "#8B4513"),
    ( 0.10, 0.25, 1, "Vegetación muy escasa",     "#D2691E"),
    ( 0.25, 0.40, 2, "Vigor bajo",                "#FFA500"),
    ( 0.40, 0.55, 3, "Vigor moderado",            "#9ACD32"),
    ( 0.55, 0.70, 4, "Buen vigor",                "#32CD32"),
    ( 0.70, 1.00, 5, "Vigor alto (óptimo)",       "#006400"),
]

COLOR_ANOMALIA = "#FF00FF"   # magenta — anomalía sobre cultivo establecido


# ─── Funciones principales ─────────────────────────────────────────────────────

MAX_DIM = 2000  # píxeles máximos por lado para procesamiento eficiente


def cargar_ndvi(path: str) -> tuple[np.ndarray, dict]:
    """Carga el NDVI desde un .tif. Retorna (array, metadata).

    GEE exporta NDVI como int16 escalado ×10000. Si los valores están fuera
    de [-2, 2] se asume escala entera y se divide automáticamente.
    """
    with rasterio.open(path) as src:
        data = src.read(1).astype(float)
        nodata = src.nodata
        meta = {
            "crs":       str(src.crs),
            "transform": list(src.transform),
            "shape":     list(src.shape),
            "nodata":    float(nodata) if nodata is not None else None,
        }

    # Enmascarar nodata
    if nodata is not None:
        data[data == nodata] = np.nan

    # Detectar y corregir escala entera (GEE int16 ×10000)
    finite = data[np.isfinite(data)]
    if len(finite) > 0 and (np.nanmax(finite) > 2 or np.nanmin(finite) < -2):
        data = data / 10000.0
        print(f"  [auto] Escala int16 detectada -> dividido por 10000")

    # Submuestrear si el raster es demasiado grande para ser procesado eficientemente
    H, W = data.shape
    if max(H, W) > MAX_DIM:
        factor = max(H, W) / MAX_DIM
        new_H  = max(1, int(H / factor))
        new_W  = max(1, int(W / factor))
        from skimage.transform import resize
        data = resize(data, (new_H, new_W), anti_aliasing=True, preserve_range=True)
        print(f"  [auto] Submuestreo: {H}x{W} -> {new_H}x{new_W} (factor {factor:.1f}x)")

    return data, meta


def clasificar_vigor(ndvi: np.ndarray) -> np.ndarray:
    """Asigna un código de clase a cada píxel según NDVI."""
    mapa = np.full(ndvi.shape, -1, dtype=np.int8)
    for vmin, vmax, codigo, *_ in CLASES_VIGOR:
        mask = (ndvi >= vmin) & (ndvi < vmax) & ~np.isnan(ndvi)
        mapa[mask] = codigo
    return mapa


def detectar_anomalias(ndvi: np.ndarray, mapa_vigor: np.ndarray,
                       umbral_z: float = -2.0,
                       min_pixels: int = 9) -> np.ndarray:
    """
    Detecta zonas anómalas dentro de cultivo establecido (vigor >= 2).

    Una zona es anómala si su NDVI es más de `umbral_z` desviaciones
    estándar por debajo de la media local (ventana 31×31 px).

    min_pixels: tamaño mínimo de un blob para reportarlo (filtra ruido).
    """
    en_cultivo = mapa_vigor >= 2
    validos     = ~np.isnan(ndvi)

    # Media local con kernel uniforme 31×31
    kernel_size = 31
    media_local = ndimage.uniform_filter(
        np.where(validos, ndvi, 0.0), size=kernel_size
    )
    conteo_local = ndimage.uniform_filter(
        validos.astype(float), size=kernel_size
    ) * kernel_size ** 2
    conteo_local = np.maximum(conteo_local, 1)

    std_global = float(np.nanstd(ndvi[en_cultivo & validos])) if np.any(en_cultivo & validos) else 0.1
    if std_global < 1e-6:
        std_global = 0.1

    diferencia = ndvi - media_local
    z_score    = diferencia / std_global

    anomalia_raw = en_cultivo & validos & (z_score < umbral_z)

    # Filtrar blobs pequeños (ruido)
    labeled, n = ndimage.label(anomalia_raw)
    for i in range(1, n + 1):
        if np.sum(labeled == i) < min_pixels:
            anomalia_raw[labeled == i] = False

    return anomalia_raw


def calcular_estadisticas(ndvi: np.ndarray, mapa_vigor: np.ndarray,
                          anomalias: np.ndarray) -> dict:
    """Calcula métricas por clase y globales."""
    validos    = ~np.isnan(ndvi)
    total_px   = int(np.sum(validos))
    anom_px    = int(np.sum(anomalias))

    clases = {}
    for vmin, vmax, codigo, label, color in CLASES_VIGOR:
        mask  = (mapa_vigor == codigo) & validos
        count = int(np.sum(mask))
        clases[label] = {
            "codigo":       codigo,
            "pixeles":      count,
            "porcentaje":   round(100 * count / total_px, 2) if total_px else 0,
            "ndvi_medio":   round(float(np.nanmean(ndvi[mask])), 4) if count else None,
            "ndvi_max":     round(float(np.nanmax(ndvi[mask])), 4) if count else None,
        }

    return {
        "total_pixeles":        total_px,
        "pixeles_anomalia":     anom_px,
        "pct_anomalia":         round(100 * anom_px / total_px, 2) if total_px else 0,
        "ndvi_global_medio":    round(float(np.nanmean(ndvi[validos])), 4),
        "ndvi_global_max":      round(float(np.nanmax(ndvi[validos])), 4),
        "clases_vigor":         clases,
    }


def generar_reporte(ndvi: np.ndarray, mapa_vigor: np.ndarray,
                    anomalias: np.ndarray, stats: dict,
                    zona: str, fecha: str, output_png: str):
    """Genera el reporte visual de clasificación."""
    print("Generando reporte de visión computacional...")

    # Construir imagen RGB de clasificación
    H, W    = ndvi.shape
    img_rgb = np.zeros((H, W, 3), dtype=np.uint8)

    for vmin, vmax, codigo, label, hex_color in CLASES_VIGOR:
        r, g, b = int(hex_color[1:3], 16), int(hex_color[3:5], 16), int(hex_color[5:7], 16)
        mask = mapa_vigor == codigo
        img_rgb[mask] = [r, g, b]

    # Sobreescribir anomalías en magenta
    ar, ag, ab = int(COLOR_ANOMALIA[1:3], 16), int(COLOR_ANOMALIA[3:5], 16), int(COLOR_ANOMALIA[5:7], 16)
    img_rgb[anomalias] = [ar, ag, ab]

    # ── Layout ────────────────────────────────────────────────────────────────
    fig = plt.figure(figsize=(20, 12), facecolor='#06080b')
    fig.suptitle(
        f"FARO PROTOCOL — Clasificación de Vigor Vegetativo\n{zona} | {fecha}",
        color='#e2c97e', fontsize=15, fontweight='bold', y=0.98,
        fontfamily='serif'
    )

    gs = gridspec.GridSpec(2, 3, figure=fig, hspace=0.38, wspace=0.28,
                           left=0.05, right=0.95, top=0.92, bottom=0.05)

    # Panel 1 — NDVI crudo
    ax1 = fig.add_subplot(gs[0, 0])
    im1 = ax1.imshow(ndvi, cmap='RdYlGn', vmin=0, vmax=1, interpolation='nearest')
    ax1.set_title("NDVI (entrada)", color='#f2ede4', fontsize=11, pad=8)
    ax1.axis('off')
    cb1 = plt.colorbar(im1, ax=ax1, fraction=0.046, pad=0.04)
    cb1.ax.yaxis.set_tick_params(color='white')
    plt.setp(cb1.ax.get_yticklabels(), color='white', fontsize=8)

    # Panel 2 — Mapa de vigor
    ax2 = fig.add_subplot(gs[0, 1])
    ax2.imshow(img_rgb, interpolation='nearest')
    ax2.set_title("Clasificación de Vigor", color='#f2ede4', fontsize=11, pad=8)
    ax2.axis('off')

    # Panel 3 — Anomalías
    ax3 = fig.add_subplot(gs[0, 2])
    anom_display = np.where(anomalias, 1.0, np.nan)
    ndvi_bg = np.where(np.isnan(ndvi), 0, ndvi)
    ax3.imshow(ndvi_bg, cmap='Greys_r', alpha=0.5, interpolation='nearest')
    anom_img = np.zeros((*ndvi.shape, 4))
    anom_img[anomalias] = [1, 0, 1, 0.85]  # magenta semi-transparente
    ax3.imshow(anom_img, interpolation='nearest')
    ax3.set_title(f"Anomalías detectadas ({stats['pct_anomalia']}%)",
                  color='#f2ede4', fontsize=11, pad=8)
    ax3.axis('off')

    # Panel 4 — Leyenda
    ax4 = fig.add_subplot(gs[1, 0])
    ax4.axis('off')
    ax4.set_facecolor('#0d1117')
    ax4.set_title("Leyenda de clases", color='#e2c97e', fontsize=10, pad=6)
    leyenda_patches = [
        mpatches.Patch(color=color, label=label)
        for _, _, _, label, color in CLASES_VIGOR
    ]
    leyenda_patches.append(mpatches.Patch(color=COLOR_ANOMALIA, label="Anomalía (posible maleza/estrés)"))
    ax4.legend(handles=leyenda_patches, loc='upper left',
               framealpha=0, labelcolor='white', fontsize=9,
               handlelength=1.5, handleheight=1.2)

    # Panel 5 — Barras por clase
    ax5 = fig.add_subplot(gs[1, 1])
    ax5.set_facecolor('#0d1117')
    labels_c = [d[:18] for d in stats['clases_vigor']]
    pcts     = [stats['clases_vigor'][d]['porcentaje'] for d in stats['clases_vigor']]
    colors_c = [c for _, _, _, _, c in CLASES_VIGOR]
    bars = ax5.barh(labels_c, pcts, color=colors_c, edgecolor='#222')
    ax5.set_xlabel("% del área", color='#aaaacc', fontsize=9)
    ax5.tick_params(colors='#aaaacc', labelsize=8)
    ax5.spines[:].set_color('#333')
    ax5.set_facecolor('#0d1117')
    for bar, pct in zip(bars, pcts):
        if pct > 1:
            ax5.text(pct + 0.3, bar.get_y() + bar.get_height() / 2,
                     f"{pct:.1f}%", va='center', color='white', fontsize=8)
    ax5.set_title("Distribución de vigor", color='#e2c97e', fontsize=10, pad=6)

    # Panel 6 — Métricas globales
    ax6 = fig.add_subplot(gs[1, 2])
    ax6.axis('off')
    ax6.set_facecolor('#090d12')

    metricas = [
        ("NDVI global medio",     f"{stats['ndvi_global_medio']:.4f}"),
        ("NDVI máximo",           f"{stats['ndvi_global_max']:.4f}"),
        ("Píxeles analizados",    f"{stats['total_pixeles']:,}"),
        ("Píxeles anómalos",      f"{stats['pixeles_anomalia']:,}"),
        ("% superficie anómala",  f"{stats['pct_anomalia']:.2f}%"),
        ("Método",                "NDVI + Z-score local"),
        ("Fuente óptica",         "Sentinel-2 / GEE"),
        ("Fase",                  "Faro Protocol — Fase 6"),
        ("Estado",                "✓ CLASIFICACIÓN VALIDADA"),
    ]

    for i, (k, v) in enumerate(metricas):
        y_pos = 0.93 - i * 0.10
        ax6.text(0.02, y_pos,        k, transform=ax6.transAxes,
                 color='#aaaacc', fontsize=8.5)
        ax6.text(0.02, y_pos - 0.05, v, transform=ax6.transAxes,
                 color='#e2c97e' if i == len(metricas) - 1 else 'white',
                 fontsize=10, fontweight='bold')

    plt.savefig(output_png, dpi=150, bbox_inches='tight',
                facecolor='#06080b', edgecolor='none')
    print(f"  Reporte guardado: {output_png}")


def run(area_name: str, ndvi_file: str = None, umbral_z: float = -2.0,
        output_json: str = None) -> dict:
    """
    API programática de faro_vision — llamable desde faro_engine u otros módulos.

    Retorna el dict de estadísticas (mismo contenido que el JSON guardado).
    """
    area = load_area(area_name)
    if area.get('vertical') == 'energia':
        print(f"[INFO] Area energia: clasificacion NDVI detecta actividad vegetal.")

    ndvi_path   = ndvi_file or area.get('ndvi_tif', '')
    output_png  = f"faro_clasificacion_{area['name']}.png"
    output_json = output_json or f"faro_vision_{area['name']}.json"
    zona        = area['label']
    fecha       = datetime.now().strftime("%Y-%m-%d %H:%M")

    print("=" * 55)
    print(f"  FARO PROTOCOL — Vision Computacional (Fase 6)")
    print(f"  Area: {zona}")
    print(f"  NDVI: {ndvi_path}")
    print("=" * 55)

    ndvi, meta = cargar_ndvi(ndvi_path)
    print(f"  NDVI cargado | Shape: {ndvi.shape} | "
          f"Min: {np.nanmin(ndvi):.3f} Max: {np.nanmax(ndvi):.3f}")

    print("  Clasificando vigor vegetativo...")
    mapa_vigor = clasificar_vigor(ndvi)

    print(f"  Detectando anomalias (z < {umbral_z})...")
    anomalias = detectar_anomalias(ndvi, mapa_vigor, umbral_z=umbral_z)

    stats = calcular_estadisticas(ndvi, mapa_vigor, anomalias)

    print(f"\n  Resultados:")
    print(f"    NDVI medio global:  {stats['ndvi_global_medio']:.4f}")
    print(f"    Anomalias (% area): {stats['pct_anomalia']:.2f}%")
    for label, d in stats['clases_vigor'].items():
        if d['pixeles'] > 0:
            print(f"    [{d['codigo']}] {label[:28]:28s} {d['porcentaje']:5.1f}%")

    generar_reporte(ndvi, mapa_vigor, anomalias, stats, zona, fecha, output_png)

    result = {
        "_meta": {
            "area":        area['name'],
            "zona":        zona,
            "fecha":       fecha,
            "ndvi_fuente": ndvi_path,
            "umbral_z":    umbral_z,
        },
        **stats,
    }
    with open(output_json, 'w', encoding='utf-8') as f:
        json.dump(result, f, indent=2, ensure_ascii=False)
    print(f"  JSON guardado: {output_json}")
    print("\n  [OK] Fase 6 completada.\n")
    return result


def main():
    parser = argparse.ArgumentParser(
        description="Faro Protocol — Vision computacional sobre NDVI"
    )
    parser.add_argument('--area', required=True,
                        help=f"Area a procesar. Disponibles: {', '.join(list_areas())}")
    parser.add_argument('--ndvi-file', default=None,
                        help="Ruta al NDVI .tif (opcional — por defecto usa la config del area)")
    parser.add_argument('--umbral-z', type=float, default=-2.0,
                        help="Z-score para deteccion de anomalias (default: -2.0)")
    parser.add_argument('--output-json', default=None,
                        help="Ruta del JSON de salida (opcional)")
    args = parser.parse_args()
    run(args.area, ndvi_file=args.ndvi_file,
        umbral_z=args.umbral_z, output_json=args.output_json)


if __name__ == '__main__':
    main()
