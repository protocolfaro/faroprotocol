import sys as _sys
from pathlib import Path as _Path
_ED = str(_Path(__file__).parent)
if _ED not in _sys.path:
    _sys.path.insert(0, _ED)
"""
FARO PROTOCOL — Georreferenciación SAR Sentinel-1

Uso:
    python faro_sar_georef.py --area cordoba --sar-file ruta/al/archivo.tiff
    python faro_sar_georef.py --area vaca_muerta --sar-file ruta/al/archivo.tiff
"""

import argparse
import sys
import rasterio
import numpy as np
from rasterio.transform import from_bounds
from rasterio.crs import CRS

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')
if hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(encoding='utf-8')

from faro_areas import load_area, list_areas

# Defaults (solo usados si se llama sin área)
SAR_INPUT  = 'sar_downloads/measurement/s1a-iw-grd-vh.tiff'
SAR_OUTPUT = 'sar_georef.tif'
WEST, SOUTH, EAST, NORTH = -65.5, -33.5, -63.5, -30.5


def main(area=None, sar_input=None):
    """
    Georreferencia un archivo SAR crudo sobre los bounds del área.

    Parámetros:
        area      : dict cargado con load_area(), o None para usar defaults
        sar_input : path al .tiff crudo (sobrescribe area['sar_input'] si se pasa)
    """
    _sar_input  = sar_input or (area.get('sar_input') if area else None) or SAR_INPUT
    _sar_output = area['sar_output'] if area else SAR_OUTPUT
    west  = area['bounds'][0] if area else WEST
    south = area['bounds'][1] if area else SOUTH
    east  = area['bounds'][2] if area else EAST
    north = area['bounds'][3] if area else NORTH

    label = area['label'] if area else 'área no especificada'
    print("=" * 55)
    print(f"  FARO PROTOCOL — Georreferenciación SAR")
    print(f"  Área: {label}")
    print("=" * 55)

    print(f"\nLeyendo SAR crudo: {_sar_input}")
    with rasterio.open(_sar_input) as src:
        data   = src.read(1).astype(np.float32)
        height, width = data.shape
        print(f"  Shape: {height} x {width}")
        print(f"  Media original: {data.mean():.4f}")

    # Convertir amplitud a sigma0 en dB — Sentinel-1 GRD almacena amplitud, no potencia.
    # Fórmula: sigma0_dB = 20*log10(DN) - offset
    # El offset correcto depende del calibration LUT del SAFE (varía por escena).
    # Offset empírico -52 dB da resultados físicamente coherentes (~-12 dB agro VV)
    # para los archivos Copernicus Data Space usados en este proyecto.
    # TODO producción: leer gain del calibration LUT en /annotation/calibration/*.xml
    # NO normalizar aquí — la normalización se hace en faro_fusion.py antes de graficar
    data = np.where(data > 0, 20 * np.log10(data + 1e-10) - 52.0, -30.0)
    print(f"  Media en dB (sigma0): {data.mean():.4f}")

    # Downsampling — Sentinel-1 GRD cubre ~250×170 km; reducir a máx 4000px por lado
    # para que la fusión sea manejable sin perder precisión estadística
    MAX_PX = 4000
    if max(height, width) > MAX_PX:
        factor = max(height, width) / MAX_PX
        new_h = max(1, int(height / factor))
        new_w = max(1, int(width / factor))
        from skimage.transform import resize as sk_resize
        data = sk_resize(data, (new_h, new_w), anti_aliasing=True).astype(np.float32)
        height, width = new_h, new_w
        print(f"  Downsampling aplicado: {new_h} x {new_w} px (factor {factor:.1f}x)")

    # Georreferenciar
    transform = from_bounds(west, south, east, north, width, height)
    crs = CRS.from_epsg(4326)

    print(f"\nGuardando SAR georreferenciado: {_sar_output}")
    with rasterio.open(
        _sar_output, 'w',
        driver='GTiff',
        height=height, width=width,
        count=1, dtype='float32',
        crs=crs, transform=transform,
        compress='lzw',
    ) as dst:
        dst.write(data, 1)

    print(f"✓ SAR listo: {_sar_output}\n")
    return _sar_output


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='FARO PROTOCOL — Georreferenciación SAR')
    parser.add_argument(
        '--area', required=True,
        help=f"Área a procesar. Disponibles: {', '.join(list_areas())}"
    )
    parser.add_argument(
        '--sar-file', required=True,
        help='Path al archivo SAR .tiff crudo descargado de Copernicus'
    )
    args = parser.parse_args()

    area = load_area(args.area)
    main(area=area, sar_input=args.sar_file)
