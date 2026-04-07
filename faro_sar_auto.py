"""
FARO PROTOCOL — Automatización SAR end-to-end
===============================================

Pipeline completo para un área:
  1. Buscar el producto SAR más pequeño disponible en Copernicus
  2. Descargar el ZIP
  3. Extraer solo el VV TIFF (sin descomprimir todo el SAFE)
  4. Georreferenciar → sar_<area>_georef.tif
  5. Fusión SAR + NDVI (SAR-only si no hay NDVI)
  6. Motor económico → data.json + SHA-256
  7. Certificado PNG

Uso:
    python faro_sar_auto.py --area vaca_muerta
    python faro_sar_auto.py --area rotterdam --skip-download  (si ZIP ya descargado)
    python faro_sar_auto.py --area permian   --skip-georef    (si .tif ya existe)
"""

import argparse
import hashlib
import json
import os
import sys
import zipfile
from datetime import datetime, timedelta
from pathlib import Path

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

from faro_areas import load_area

PROJECT_ROOT = Path(__file__).parent
DATOS_SAR    = PROJECT_ROOT / 'datos_sar'
DATOS_SAR.mkdir(exist_ok=True)

TOKEN_URL    = 'https://identity.dataspace.copernicus.eu/auth/realms/CDSE/protocol/openid-connect/token'
CATALOGO_URL = 'https://catalogue.dataspace.copernicus.eu/odata/v1/Products'
DOWNLOAD_URL = 'https://download.dataspace.copernicus.eu/odata/v1/Products({id})/$value'


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


# ── Búsqueda ──────────────────────────────────────────────────────────────────

def buscar_producto(polygon: str, dias: int = 30) -> dict | None:
    """Devuelve el producto GRD más pequeño de los últimos `dias` días."""
    fi = (datetime.now() - timedelta(days=dias)).strftime('%Y-%m-%dT%H:%M:%SZ')
    ff = datetime.now().strftime('%Y-%m-%dT%H:%M:%SZ')

    filtro = (
        f"Collection/Name eq 'SENTINEL-1' "
        f"and Attributes/OData.CSC.StringAttribute/any("
        f"att:att/Name eq 'productType' and att/OData.CSC.StringAttribute/Value eq 'GRD') "
        f"and ContentDate/Start gt {fi} and ContentDate/Start lt {ff} "
        f"and OData.CSC.Intersects(area=geography'SRID=4326;{polygon}')"
    )
    r = requests.get(CATALOGO_URL, params={
        '$filter': filtro, '$orderby': 'ContentDate/Start desc',
        '$top': 10, '$expand': 'Attributes'
    }, timeout=30)
    r.raise_for_status()
    prods = r.json().get('value', [])
    if not prods:
        return None
    # Elegir el más pequeño (menos tiempo de descarga)
    return min(prods, key=lambda p: p.get('ContentLength', float('inf')))


# ── Descarga ──────────────────────────────────────────────────────────────────

def descargar_zip(producto: dict, token: str) -> Path:
    """Descarga el ZIP del producto. Retorna path al ZIP."""
    nombre = producto['Name']
    pid    = producto['Id']
    zip_path = DATOS_SAR / f"{nombre}.zip"

    if zip_path.exists():
        mb = zip_path.stat().st_size / 1e6
        print(f'  ZIP ya existe ({mb:.0f} MB): {zip_path.name}')
        return zip_path

    mb_total = producto.get('ContentLength', 0) / 1e6
    print(f'  Descargando: {nombre}')
    print(f'  Tamaño: {mb_total:.0f} MB')

    url = DOWNLOAD_URL.format(id=pid)
    headers = {'Authorization': f'Bearer {token}'}

    # La descarga puede requerir seguir redirecciones con el mismo header
    session = requests.Session()
    session.headers.update(headers)
    resp = session.get(url, stream=True, timeout=60, allow_redirects=True)

    if resp.status_code == 401:
        # Token expirado — renovar
        token = _get_token()
        session.headers.update({'Authorization': f'Bearer {token}'})
        resp = session.get(url, stream=True, timeout=60, allow_redirects=True)

    resp.raise_for_status()

    total      = int(resp.headers.get('content-length', 0))
    descargado = 0
    t0         = datetime.now()

    with open(zip_path, 'wb') as f:
        for chunk in resp.iter_content(chunk_size=1024 * 256):  # 256KB chunks
            f.write(chunk)
            descargado += len(chunk)
            if total > 0:
                pct     = descargado / total * 100
                elapsed = (datetime.now() - t0).total_seconds()
                speed   = descargado / elapsed / 1e6 if elapsed > 1 else 0
                eta     = (total - descargado) / (descargado / elapsed) if descargado > 0 else 0
                print(f'\r  {pct:5.1f}% | {descargado/1e6:6.0f}/{total/1e6:.0f} MB'
                      f' | {speed:.1f} MB/s | ETA {eta/60:.0f}min', end='', flush=True)
    print()
    print(f'  Guardado: {zip_path}')
    return zip_path


# ── Extracción VV TIFF ────────────────────────────────────────────────────────

def extraer_vv_tiff(zip_path: Path) -> Path:
    """
    Extrae solo el archivo VV TIFF del ZIP (sin descomprimir el SAFE completo).
    Retorna el path al TIFF extraído.
    """
    extract_dir = zip_path.parent / zip_path.stem  # directorio con nombre del SAFE

    # Buscar TIFF VV ya extraído
    existing = list(extract_dir.glob('*/measurement/*-vv-*.tiff'))
    if existing:
        print(f'  VV TIFF ya extraído: {existing[0].name}')
        return existing[0]

    print(f'  Abriendo ZIP: {zip_path.name}')
    with zipfile.ZipFile(zip_path) as z:
        # Buscar el archivo VV en measurement/
        vv_candidates = [
            m for m in z.namelist()
            if '/measurement/' in m
            and '-vv-' in m
            and m.endswith('.tiff')
        ]

        if not vv_candidates:
            # Intentar nombre alternativo (GRDH sin discriminación de pol.)
            vv_candidates = [
                m for m in z.namelist()
                if '/measurement/' in m and m.endswith('.tiff')
            ]

        if not vv_candidates:
            raise FileNotFoundError(f'No se encontró TIFF de medición en {zip_path.name}')

        # Usar el primero (o preferir VV si hay múltiples)
        vv_member = vv_candidates[0]
        for m in vv_candidates:
            if '-vv-' in m:
                vv_member = m
                break

        print(f'  Extrayendo: {vv_member}')
        # Extraer conservando la estructura de directorios
        z.extract(vv_member, zip_path.parent)
        extracted = zip_path.parent / vv_member
        print(f'  Extraído: {extracted.name} ({extracted.stat().st_size/1e6:.0f} MB)')
        return extracted


# ── Pipeline completo ─────────────────────────────────────────────────────────

def pipeline_area(area_name: str,
                  skip_download: bool = False,
                  skip_georef:   bool = False) -> dict:
    """
    Ejecuta el pipeline completo para un área.
    Retorna un dict con estado y SHA-256.
    """
    area = load_area(area_name)
    print()
    print('=' * 65)
    print(f'  FARO PROTOCOL — Pipeline SAR-only')
    print(f'  Área     : {area["label"]}')
    print(f'  Vertical : {area.get("vertical","?").upper()}')
    print(f'  Inicio   : {datetime.now().strftime("%Y-%m-%d %H:%M")}')
    print('=' * 65)

    sar_georef_path = PROJECT_ROOT / area['sar_output']

    # ── Paso 1–3: Búsqueda + descarga + extracción ────────────────────────────
    if skip_georef or sar_georef_path.exists():
        print(f'\n[1-3/6] Georef ya existe ({sar_georef_path.name}) — omitiendo descarga')
        skip_download = True
        skip_georef   = True
    elif not skip_download:
        print(f'\n[1/6] Buscando escena SAR más reciente...')
        producto = buscar_producto(area['polygon'], dias=30)
        if not producto:
            print(f'  ERROR: No se encontraron escenas en los últimos 30 días')
            return {'area': area_name, 'estado': 'SIN_ESCENAS', 'sha256': None}

        mb = producto.get('ContentLength', 0) / 1e6
        fecha_escena = producto.get('ContentDate', {}).get('Start', '?')[:10]
        print(f'  Escena seleccionada: {producto["Name"][:55]}')
        print(f'  Fecha: {fecha_escena} | Tamaño: {mb:.0f} MB')

        print(f'\n[2/6] Descargando ZIP...')
        token   = _get_token()
        zip_path = descargar_zip(producto, token)

        print(f'\n[3/6] Extrayendo VV TIFF...')
        vv_tiff = extraer_vv_tiff(zip_path)
    else:
        # skip_download activo — buscar el VV TIFF en datos_sar
        existing_tiffs = list(DATOS_SAR.glob('*/measurement/*-vv-*.tiff'))
        if not existing_tiffs:
            existing_tiffs = list(DATOS_SAR.glob('*/measurement/*.tiff'))
        if not existing_tiffs:
            print('  ERROR: --skip-download pero no se encontró VV TIFF en datos_sar/')
            return {'area': area_name, 'estado': 'ERROR', 'sha256': None}
        vv_tiff = existing_tiffs[0]
        print(f'  Usando VV TIFF existente: {vv_tiff.name}')

    # ── Paso 4: Georreferenciación ─────────────────────────────────────────────
    if not skip_georef:
        print(f'\n[4/6] Georreferenciando SAR...')
        import faro_sar_georef
        faro_sar_georef.main(area=area, sar_input=str(vv_tiff))
        if not sar_georef_path.exists():
            return {'area': area_name, 'estado': 'ERROR_GEOREF', 'sha256': None}
    else:
        print(f'\n[4/6] Georef omitido (archivo ya existe)')

    # ── Paso 5: Fusión SAR + NDVI (SAR-only si falta NDVI) ───────────────────
    print(f'\n[5/6] Fusión SAR + óptico...')
    ndvi_path = PROJECT_ROOT / area.get('ndvi_tif', 'NOEXISTE')
    if ndvi_path.exists():
        print(f'  NDVI disponible: {ndvi_path.name}')
    else:
        print(f'  NDVI no disponible — modo SAR-only (NDVI pendiente GEE)')

    import faro_fusion
    resultado  = faro_fusion.main(area=area)
    output_png = resultado['output_png']
    stats      = resultado['stats']

    # ── Paso 6: SHA-256 + engine + certificado ────────────────────────────────
    print(f'\n[6/6] SHA-256 + modelo económico + certificado...')
    # SHA-256 del PNG de reporte
    h = hashlib.sha256()
    with open(output_png, 'rb') as f:
        for chunk in iter(lambda: f.read(65536), b''):
            h.update(chunk)
    digest   = h.hexdigest()
    sha_path = Path(output_png).with_suffix('.sha256')
    sha_path.write_text(f'{digest}  {Path(output_png).name}\n', encoding='utf-8')
    print(f'  SHA-256: {digest}')

    # Motor económico
    from faro_engine import FaroEngine, DatosCrudos
    crudos = DatosCrudos(
        area_name           = area['name'],
        sar_medio_db        = stats.get('sar_medio_db'),
        ndvi_medio          = stats.get('ndvi_medio'),
        indice_fusion_medio = stats.get('indice_fusion_medio'),
        pixeles_analizados  = stats.get('pixeles_analizados'),
        pct_anomalia        = 0.0,
        reporte_png         = output_png,
        sha256_completo     = digest,
        tiene_sar           = True,
        tiene_ndvi          = stats.get('ndvi_disponible', False),
        tiene_vision        = False,
    )
    engine  = FaroEngine()
    insight = engine._generar_insight(area, crudos)
    engine._publicar(crudos, insight)

    # Certificado
    from faro_certificado import generar_certificado
    generar_certificado(area_name, PROJECT_ROOT)

    print()
    print('=' * 65)
    print(f'  COMPLETADO: {area["label"]}')
    print(f'  Reporte   : {output_png}')
    print(f'  Score     : {insight.score_faro} / 100')
    print(f'  SHA-256   : {digest[:32]}...')
    print(f'  NDVI      : {"disponible" if stats.get("ndvi_disponible") else "SAR-only (pendiente GEE)"}')
    print('=' * 65)

    return {
        'area':       area_name,
        'estado':     'OK',
        'sha256':     digest,
        'score':      insight.score_faro,
        'ndvi_mode':  'real' if stats.get('ndvi_disponible') else 'sar_only',
        'output_png': output_png,
    }


# ── CLI ────────────────────────────────────────────────────────────────────────

if __name__ == '__main__':
    parser = argparse.ArgumentParser(
        description='FARO PROTOCOL — Pipeline SAR auto end-to-end')
    parser.add_argument('--area', required=True, help='Nombre del área')
    parser.add_argument('--skip-download', action='store_true',
                        help='Usar ZIP ya descargado en datos_sar/')
    parser.add_argument('--skip-georef', action='store_true',
                        help='Usar sar_<area>_georef.tif ya existente')
    args = parser.parse_args()

    resultado = pipeline_area(
        args.area,
        skip_download=args.skip_download,
        skip_georef=args.skip_georef,
    )
    sys.exit(0 if resultado.get('estado') == 'OK' else 1)
