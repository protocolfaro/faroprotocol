"""
FARO PROTOCOL — Pipeline Sentinel-2 óptico
==========================================

Descarga Sentinel-2 L2A desde Copernicus Data Space, extrae bandas B04 (RED)
y B08 (NIR), calcula NDVI con filtro CLOSDI de sombras y guarda como GeoTIFF
en formato compatible con el pipeline de fusión existente.

Formato de salida: int16 × 10000, EPSG:4326 — igual que los exports GEE.

Uso:
    python faro_s2_pipeline.py --area vaca_muerta
    python faro_s2_pipeline.py --area cordoba --max-nubes 30
    python faro_s2_pipeline.py --area rotterdam --skip-download
"""

import argparse
import os
import sys
import zipfile
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import rasterio
from rasterio.transform import from_bounds
from rasterio.crs import CRS
from rasterio.warp import calculate_default_transform, reproject, Resampling
import requests

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')
if hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(encoding='utf-8')

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent / '.env')
except ImportError:
    pass

import faro_closdi_pipeline as closdi
from faro_areas import load_area, list_areas

PROJECT_ROOT = Path(__file__).parent
DATOS_S2     = PROJECT_ROOT / 'datos_s2'
DATOS_S2.mkdir(exist_ok=True)

TOKEN_URL    = 'https://identity.dataspace.copernicus.eu/auth/realms/CDSE/protocol/openid-connect/token'
CATALOGO_URL = 'https://catalogue.dataspace.copernicus.eu/odata/v1/Products'
DOWNLOAD_URL = 'https://download.dataspace.copernicus.eu/odata/v1/Products({id})/$value'

# Resolución de downsampling de salida (px por grado, ~10m equiv. sin reprojectar)
MAX_PX = 4000


# ── Auth ──────────────────────────────────────────────────────────────────────

def _get_token() -> str:
    user = os.getenv('COPERNICUS_USER', '')
    pw   = os.getenv('COPERNICUS_PASS', '')
    if not user or not pw:
        raise ValueError('COPERNICUS_USER / COPERNICUS_PASS no configurados en .env')
    r = requests.post(TOKEN_URL, data={
        'grant_type': 'password', 'client_id': 'cdse-public',
        'username': user, 'password': pw}, timeout=30)
    r.raise_for_status()
    return r.json()['access_token']


# ── Búsqueda S2 L2A ───────────────────────────────────────────────────────────

def buscar_s2(polygon: str, dias: int = 60, max_nubes: float = 50.0) -> dict | None:
    """
    Busca el producto S2 L2A con menos nubes en los últimos `dias` días.
    Prefiere tiles individuales (más pequeños) sobre productos multi-granule.
    """
    fi = (datetime.now() - timedelta(days=dias)).strftime('%Y-%m-%dT%H:%M:%SZ')
    ff = datetime.now().strftime('%Y-%m-%dT%H:%M:%SZ')

    filtro = (
        f"Collection/Name eq 'SENTINEL-2' "
        f"and Attributes/OData.CSC.StringAttribute/any("
        f"att:att/Name eq 'productType' and att/OData.CSC.StringAttribute/Value eq 'S2MSI2A') "
        f"and ContentDate/Start gt {fi} and ContentDate/Start lt {ff} "
        f"and OData.CSC.Intersects(area=geography'SRID=4326;{polygon}')"
    )
    r = requests.get(CATALOGO_URL, params={
        '$filter': filtro,
        '$orderby': 'ContentDate/Start desc',
        '$top': 20,
        '$expand': 'Attributes',
    }, timeout=30)
    r.raise_for_status()
    prods = r.json().get('value', [])

    if not prods:
        return None

    # Filtrar por cobertura de nubes
    def nubes(p):
        for a in p.get('Attributes', []):
            if a.get('Name') == 'cloudCover':
                return float(a.get('Value', 100))
        return 100.0

    candidatos = [p for p in prods if nubes(p) <= max_nubes]
    if not candidatos:
        # Relajar umbral — tomar el de menos nubes sin importar cuántas
        candidatos = prods
        print(f'  AVISO: ningún producto con ≤{max_nubes}% nubes — usando el más limpio disponible')

    # Ordenar: primero menos nubes, luego más reciente, luego más pequeño (tile)
    candidatos.sort(key=lambda p: (nubes(p), -p.get('ContentLength', 0)))
    return candidatos[0]


# ── Descarga ZIP ──────────────────────────────────────────────────────────────

def descargar_s2_zip(producto: dict, token: str) -> Path:
    """Descarga el ZIP del producto S2. Reutiliza si ya existe."""
    nombre   = producto['Name']
    pid      = producto['Id']
    zip_path = DATOS_S2 / f'{nombre}.zip'

    if zip_path.exists():
        mb = zip_path.stat().st_size / 1e6
        print(f'  ZIP ya existe ({mb:.0f} MB): {zip_path.name}')
        return zip_path

    mb_total = producto.get('ContentLength', 0) / 1e6
    print(f'  Producto: {nombre}')
    print(f'  Tamaño  : {mb_total:.0f} MB')

    url     = DOWNLOAD_URL.format(id=pid)
    session = requests.Session()
    session.headers.update({'Authorization': f'Bearer {token}'})
    resp    = session.get(url, stream=True, timeout=60, allow_redirects=True)

    if resp.status_code == 401:
        token = _get_token()
        session.headers.update({'Authorization': f'Bearer {token}'})
        resp  = session.get(url, stream=True, timeout=60, allow_redirects=True)

    resp.raise_for_status()

    total      = int(resp.headers.get('content-length', 0))
    descargado = 0
    t0         = datetime.now()

    with open(zip_path, 'wb') as f:
        for chunk in resp.iter_content(chunk_size=1024 * 256):
            f.write(chunk)
            descargado += len(chunk)
            if total > 0:
                pct   = descargado / total * 100
                el    = (datetime.now() - t0).total_seconds()
                spd   = descargado / el / 1e6 if el > 1 else 0
                eta   = (total - descargado) / (descargado / el) if descargado > 0 else 0
                print(f'\r  {pct:5.1f}% | {descargado/1e6:6.0f}/{mb_total:.0f} MB'
                      f' | {spd:.1f} MB/s | ETA {eta/60:.0f}min', end='', flush=True)
    print()
    print(f'  Guardado: {zip_path.name}')
    return zip_path


# ── Extracción bandas B04 y B08 ───────────────────────────────────────────────

def extraer_bandas_s2(zip_path: Path) -> tuple[Path, Path]:
    """
    Extrae B04 (RED) y B08 (NIR) del ZIP S2 a datos_s2/<nombre>/.
    Prefiere resolución 10m (R10m). Retorna (b04_path, b08_path).
    """
    extract_dir = zip_path.parent / zip_path.stem

    # Buscar archivos ya extraídos
    b04_existing = list(extract_dir.rglob('*_B04_10m.jp2'))
    b08_existing = list(extract_dir.rglob('*_B08_10m.jp2'))
    # Fallback: resolución 20m o sin sufijo de resolución
    if not b04_existing:
        b04_existing = list(extract_dir.rglob('*_B04*.jp2'))
    if not b08_existing:
        b08_existing = list(extract_dir.rglob('*_B08*.jp2'))

    if b04_existing and b08_existing:
        print(f'  Bandas ya extraídas: {b04_existing[0].name}, {b08_existing[0].name}')
        return b04_existing[0], b08_existing[0]

    print(f'  Abriendo ZIP: {zip_path.name}')
    with zipfile.ZipFile(zip_path) as z:
        members = z.namelist()

        # Prioridad: R10m > R20m > cualquier resolución
        def _encontrar_banda(tag):
            for res in ['R10m', 'R20m', 'R60m', '']:
                for m in members:
                    if f'_{tag}_10m.jp2' in m or (res and f'/{res}/' in m and f'_{tag}' in m and m.endswith('.jp2')):
                        return m
                    if not res and f'_{tag}' in m and m.endswith('.jp2'):
                        return m
            return None

        b04_member = _encontrar_banda('B04') or _encontrar_banda('B4')
        b08_member = _encontrar_banda('B08') or _encontrar_banda('B8') or _encontrar_banda('B8A')

        if not b04_member or not b08_member:
            raise FileNotFoundError(
                f'No se encontraron B04/B08 en {zip_path.name}.\n'
                f'Miembros .jp2 disponibles: {[m for m in members if m.endswith(".jp2")][:10]}'
            )

        print(f'  Extrayendo B04: {Path(b04_member).name}')
        z.extract(b04_member, zip_path.parent)
        print(f'  Extrayendo B08: {Path(b08_member).name}')
        z.extract(b08_member, zip_path.parent)

    b04_path = zip_path.parent / b04_member
    b08_path = zip_path.parent / b08_member
    return b04_path, b08_path


# ── NDVI con CLOSDI ───────────────────────────────────────────────────────────

def calcular_ndvi_y_guardar(b04_path: Path, b08_path: Path,
                             bounds: list, out_path: Path) -> Path:
    """
    Lee B04 y B08, recorta a bounds del área, reproyecta a EPSG:4326,
    aplica filtro CLOSDI de sombras, calcula NDVI.
    Guarda como int16 × 10000 — compatible con faro_fusion.cargar_ndvi().
    """
    west, south, east, north = bounds

    print(f'  Leyendo B04: {b04_path.name}')
    with rasterio.open(b04_path) as src_b04:
        src_crs = src_b04.crs
        src_transform = src_b04.transform

        print(f'    CRS fuente: {src_crs}')
        print(f'    Shape: {src_b04.shape}')

        # Calcular ventana correspondiente a los bounds del área en CRS fuente
        from rasterio.warp import transform_bounds
        try:
            # Transformar bounds EPSG:4326 → CRS fuente para windowed read
            src_bounds = transform_bounds('EPSG:4326', src_crs, west, south, east, north)
            window_b04 = rasterio.windows.from_bounds(*src_bounds, transform=src_transform)
            window_b04 = window_b04.intersection(rasterio.windows.Window(0, 0, src_b04.width, src_b04.height))
            red_raw = src_b04.read(1, window=window_b04).astype(np.float32)
        except Exception:
            # Si falla el clipping, leer todo y recortar después
            red_raw = src_b04.read(1).astype(np.float32)
            window_b04 = None

        if red_raw.size == 0:
            print('    AVISO: ventana vacía — leyendo todo el raster')
            red_raw = src_b04.read(1).astype(np.float32)
            window_b04 = None

    print(f'  Leyendo B08: {b08_path.name}')
    with rasterio.open(b08_path) as src_b08:
        try:
            src_bounds_b08 = transform_bounds('EPSG:4326', src_b08.crs, west, south, east, north)
            window_b08 = rasterio.windows.from_bounds(*src_bounds_b08, transform=src_b08.transform)
            window_b08 = window_b08.intersection(rasterio.windows.Window(0, 0, src_b08.width, src_b08.height))
            nir_raw = src_b08.read(1, window=window_b08).astype(np.float32)
        except Exception:
            nir_raw = src_b08.read(1).astype(np.float32)
            window_b08 = None

        if nir_raw.size == 0:
            nir_raw = src_b08.read(1).astype(np.float32)
            window_b08 = None

    # Asegurar mismo shape (B08 puede ser misma o distinta res que B04)
    if red_raw.shape != nir_raw.shape:
        from skimage.transform import resize
        nir_raw = resize(nir_raw, red_raw.shape, anti_aliasing=True).astype(np.float32)

    # S2 L2A: DN = reflectancia × 10000; nodata = 0
    red_raw = np.where(red_raw == 0, np.nan, red_raw)
    nir_raw = np.where(nir_raw == 0, np.nan, nir_raw)

    # Normalizar a reflectancia [0, 1]
    red = red_raw / 10000.0
    nir = nir_raw / 10000.0

    print(f'  RED range: [{np.nanmin(red):.3f}, {np.nanmax(red):.3f}]')
    print(f'  NIR range: [{np.nanmin(nir):.3f}, {np.nanmax(nir):.3f}]')

    # CLOSDI cloud shadow mask
    mascara_sombra = closdi.generar_mascara_sombra(red, nir)
    pct_sombra = mascara_sombra.sum() / max(mascara_sombra.size, 1) * 100
    print(f'  CLOSDI: {pct_sombra:.1f}% sombras detectadas')

    # Calcular NDVI y enmascarar sombras
    ndvi = closdi.calcular_ndvi(red, nir)
    ndvi = np.where(mascara_sombra, np.nan, ndvi)
    ndvi = np.clip(ndvi, -1.0, 1.0)

    ndvi_validos = ndvi[np.isfinite(ndvi)]
    if len(ndvi_validos) == 0:
        raise ValueError('NDVI resultante vacío — posible problema con las bandas o el clip de área')

    print(f'  NDVI medio: {np.nanmean(ndvi_validos):.4f} | min: {np.nanmin(ndvi_validos):.4f} | max: {np.nanmax(ndvi_validos):.4f}')

    # Downsampling si supera MAX_PX
    h, w = ndvi.shape
    if max(h, w) > MAX_PX:
        factor = max(h, w) / MAX_PX
        new_h  = max(1, int(h / factor))
        new_w  = max(1, int(w / factor))
        from skimage.transform import resize
        # Para NDVI con NaN necesitamos manejar con cuidado
        ndvi_f = np.where(np.isfinite(ndvi), ndvi, 0.0)
        mask_f = np.isfinite(ndvi).astype(float)
        ndvi_f = resize(ndvi_f, (new_h, new_w), anti_aliasing=True).astype(np.float32)
        mask_r = resize(mask_f, (new_h, new_w), anti_aliasing=True) > 0.5
        ndvi   = np.where(mask_r, ndvi_f, np.nan)
        print(f'  Downsampling: {h}×{w} → {new_h}×{new_w} (factor {factor:.1f}x)')
        h, w   = new_h, new_w

    # Reproyección: el raster resultante está en CRS de la fuente (UTM).
    # Lo guardamos georreferenciado a los bounds del área en EPSG:4326.
    # Usamos from_bounds para asignar coordenadas directamente.
    transform_out = from_bounds(west, south, east, north, w, h)
    crs_out       = CRS.from_epsg(4326)

    # Convertir NDVI a int16 × 10000 (formato GEE)
    nodata_val  = -32768
    ndvi_int16  = np.where(
        np.isfinite(ndvi),
        np.clip(ndvi * 10000, -32767, 32767).astype(np.int16),
        nodata_val
    ).astype(np.int16)

    print(f'  Guardando NDVI: {out_path.name}')
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(
        out_path, 'w',
        driver='GTiff',
        height=h, width=w,
        count=1, dtype='int16',
        crs=crs_out, transform=transform_out,
        nodata=nodata_val,
        compress='lzw',
    ) as dst:
        dst.write(ndvi_int16, 1)

    mb = out_path.stat().st_size / 1e6
    print(f'  NDVI guardado: {out_path.name} ({mb:.1f} MB)')
    return out_path


# ── Pipeline completo S2 ──────────────────────────────────────────────────────

def pipeline_s2(area_name: str,
                skip_download: bool = False,
                max_nubes: float = 50.0,
                dias: int = 60) -> Path | None:
    """
    Pipeline completo Sentinel-2 para un área.
    Retorna el path al NDVI TIF generado, o None si no hay datos.
    """
    area = load_area(area_name)
    ndvi_nombre = area.get('ndvi_tif', f'FaroProtocol_NDVI_limpio_{area_name.title()}.tif')
    ndvi_out    = PROJECT_ROOT / ndvi_nombre

    print()
    print('─' * 55)
    print(f'  S2 óptico: {area["label"]}')
    print('─' * 55)

    if ndvi_out.exists():
        print(f'  NDVI ya existe: {ndvi_out.name} — omitiendo S2')
        return ndvi_out

    # 1. Buscar
    print(f'\n  [S2-1] Buscando escena L2A (≤{max_nubes}% nubes, últimos {dias} días)...')
    producto = buscar_s2(area['polygon'], dias=dias, max_nubes=max_nubes)

    if not producto:
        print(f'  SIN DATOS: no se encontró S2 L2A en los últimos {dias} días')
        return None

    nubes_val = next((a['Value'] for a in producto.get('Attributes', []) if a.get('Name') == 'cloudCover'), '?')
    mb        = producto.get('ContentLength', 0) / 1e6
    fecha     = producto.get('ContentDate', {}).get('Start', '?')[:10]
    print(f'  Escena: {producto["Name"][:55]}')
    print(f'  Fecha: {fecha} | Nubes: {nubes_val:.1f}% | Tamaño: {mb:.0f} MB')

    # 2. Descargar
    if not skip_download:
        print(f'\n  [S2-2] Descargando ZIP...')
        token    = _get_token()
        zip_path = descargar_s2_zip(producto, token)
    else:
        # Buscar ZIP existente
        zips = sorted(DATOS_S2.glob('*.zip'), key=lambda p: p.stat().st_mtime, reverse=True)
        if not zips:
            print('  ERROR: --skip-download pero no hay ZIP en datos_s2/')
            return None
        zip_path = zips[0]
        print(f'  Usando ZIP existente: {zip_path.name}')

    # 3. Extraer B04 y B08
    print(f'\n  [S2-3] Extrayendo bandas B04 (RED) y B08 (NIR)...')
    b04_path, b08_path = extraer_bandas_s2(zip_path)
    b04_mb = b04_path.stat().st_size / 1e6
    b08_mb = b08_path.stat().st_size / 1e6
    print(f'  B04: {b04_path.name} ({b04_mb:.0f} MB)')
    print(f'  B08: {b08_path.name} ({b08_mb:.0f} MB)')

    # 4. NDVI + CLOSDI
    print(f'\n  [S2-4] Calculando NDVI (CLOSDI shadow filter)...')
    try:
        ndvi_path = calcular_ndvi_y_guardar(b04_path, b08_path, area['bounds'], ndvi_out)
    except Exception as e:
        print(f'  ERROR en NDVI: {e}')
        return None

    print(f'\n  NDVI listo: {ndvi_path.name}')
    return ndvi_path


# ── CLI ────────────────────────────────────────────────────────────────────────

if __name__ == '__main__':
    parser = argparse.ArgumentParser(
        description='FARO PROTOCOL — Pipeline Sentinel-2 NDVI')
    parser.add_argument('--area',          required=True)
    parser.add_argument('--max-nubes',     type=float, default=50.0)
    parser.add_argument('--dias',          type=int,   default=60)
    parser.add_argument('--skip-download', action='store_true')
    args = parser.parse_args()

    print('=' * 60)
    print('  FARO PROTOCOL — Sentinel-2 NDVI pipeline')
    print(f'  {datetime.now().strftime("%Y-%m-%d %H:%M")}')
    print('=' * 60)

    resultado = pipeline_s2(
        args.area,
        skip_download=args.skip_download,
        max_nubes=args.max_nubes,
        dias=args.dias,
    )

    if resultado:
        print(f'\n  OK: {resultado}')
    else:
        print('\n  FALLO: no se pudo generar NDVI')
        sys.exit(1)
