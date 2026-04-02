"""
FARO PROTOCOL — Pipeline SAR Sentinel-1
Descarga imágenes SAR desde Copernicus Data Space (API 2024+)

Uso:
    python faro_sar_pipeline.py --area cordoba
    python faro_sar_pipeline.py --area vaca_muerta
    python faro_sar_pipeline.py --area balcarce

Requiere:
    pip install requests

IMPORTANTE: Nunca guardes usuario/contraseña en el código.
Usá variables de entorno o el archivo .env
"""

import requests
import os
import argparse
from datetime import datetime, timedelta
from pathlib import Path

from faro_areas import load_area, list_areas

# ─────────────────────────────────────────────────────────────
# CONFIGURACIÓN
# ─────────────────────────────────────────────────────────────

COPERNICUS_USER = os.environ.get('COPERNICUS_USER', '')
COPERNICUS_PASS = os.environ.get('COPERNICUS_PASS', '')

FECHA_FIN    = datetime.now().strftime('%Y-%m-%dT%H:%M:%SZ')
FECHA_INICIO = (datetime.now() - timedelta(days=30)).strftime('%Y-%m-%dT%H:%M:%SZ')

CARPETA_DESCARGA = Path('./datos_sar')

TOKEN_URL    = "https://identity.dataspace.copernicus.eu/auth/realms/CDSE/protocol/openid-connect/token"
CATALOGO_URL = "https://catalogue.dataspace.copernicus.eu/odata/v1/Products"


# ─────────────────────────────────────────────────────────────
# FUNCIONES
# ─────────────────────────────────────────────────────────────

def obtener_token(usuario: str, contrasena: str) -> str:
    """Obtiene token de acceso de Copernicus Data Space."""
    if not usuario or not contrasena:
        raise ValueError(
            "Credenciales no configuradas.\n"
            "Ejecutá en Windows:\n"
            "  set COPERNICUS_USER=tu_email\n"
            "  set COPERNICUS_PASS=tu_contraseña\n"
            "Y luego corré el script de nuevo."
        )

    response = requests.post(TOKEN_URL, data={
        'grant_type': 'password',
        'username'  : usuario,
        'password'  : contrasena,
        'client_id' : 'cdse-public',
    })

    if response.status_code != 200:
        raise ConnectionError(f"Error de autenticación: {response.status_code} — {response.text}")

    return response.json()['access_token']


def buscar_imagenes_sar(polygon: str, fecha_inicio: str, fecha_fin: str,
                        max_resultados: int = 10) -> list:
    """Busca imágenes Sentinel-1 GRD en Copernicus Data Space."""
    filtro = (
        f"Collection/Name eq 'SENTINEL-1' "
        f"and Attributes/OData.CSC.StringAttribute/any(att:att/Name eq 'productType' "
        f"and att/OData.CSC.StringAttribute/Value eq 'GRD') "
        f"and ContentDate/Start gt {fecha_inicio} "
        f"and ContentDate/Start lt {fecha_fin} "
        f"and OData.CSC.Intersects(area=geography'SRID=4326;{polygon}')"
    )

    params = {
        '$filter'  : filtro,
        '$orderby' : 'ContentDate/Start desc',
        '$top'     : max_resultados,
        '$expand'  : 'Attributes',
    }

    response = requests.get(CATALOGO_URL, params=params)

    if response.status_code != 200:
        raise ConnectionError(f"Error en búsqueda: {response.status_code}")

    return response.json().get('value', [])


def mostrar_resultados(productos: list, label: str) -> None:
    """Muestra los resultados de búsqueda formateados."""
    print(f"\n{'='*60}")
    print(f"  FARO PROTOCOL — Resultados SAR | {label}")
    print(f"{'='*60}")
    print(f"  Imágenes encontradas: {len(productos)}")
    print(f"{'─'*60}")

    for i, p in enumerate(productos):
        nombre  = p.get('Name', 'N/A')
        fecha   = p.get('ContentDate', {}).get('Start', 'N/A')[:10]
        size_mb = p.get('ContentLength', 0) / (1024 * 1024)
        pid     = p.get('Id', 'N/A')

        print(f"\n  [{i+1}] {nombre}")
        print(f"       Fecha  : {fecha}")
        print(f"       Tamaño : {size_mb:.1f} MB")
        print(f"       ID     : {pid}")

    print(f"\n{'='*60}\n")


def descargar_imagen(producto_id: str, nombre: str, token: str,
                     carpeta: Path = CARPETA_DESCARGA) -> Path:
    """Descarga una imagen SAR de Copernicus Data Space."""
    carpeta.mkdir(exist_ok=True)
    archivo = carpeta / f"{nombre}.zip"

    if archivo.exists():
        print(f"  Ya existe: {archivo}")
        return archivo

    url = f"https://download.dataspace.copernicus.eu/odata/v1/Products({producto_id})/$value"
    headers = {'Authorization': f'Bearer {token}'}
    response = requests.get(url, headers=headers, stream=True)

    if response.status_code != 200:
        raise ConnectionError(f"Error de descarga: {response.status_code}")

    total      = int(response.headers.get('content-length', 0))
    descargado = 0

    with open(archivo, 'wb') as f:
        for chunk in response.iter_content(chunk_size=8192):
            f.write(chunk)
            descargado += len(chunk)
            if total > 0:
                pct = descargado / total * 100
                print(f"\r  Descargando: {pct:.1f}%", end='', flush=True)

    print(f"\n  ✓ Guardado en: {archivo}")
    return archivo


# ─────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────

def main(area_name: str) -> list:
    """
    Busca imágenes SAR para el área especificada.
    Retorna la lista de productos encontrados.
    """
    area = load_area(area_name)

    print("=" * 60)
    print(f"  FARO PROTOCOL — Pipeline SAR Sentinel-1")
    print(f"  Área: {area['label']}")
    print("=" * 60)

    # 1. Autenticación
    print("\n[1/2] Autenticando en Copernicus Data Space...")
    token = obtener_token(COPERNICUS_USER, COPERNICUS_PASS)
    print("  ✓ Token obtenido correctamente")

    # 2. Búsqueda
    print(f"\n[2/2] Buscando imágenes SAR — {area['label']}...")
    productos = buscar_imagenes_sar(
        polygon        = area['polygon'],
        fecha_inicio   = FECHA_INICIO,
        fecha_fin      = FECHA_FIN,
        max_resultados = 5,
    )
    mostrar_resultados(productos, area['label'])

    # ── DESCOMENTAR PARA DESCARGAR ──
    # if productos:
    #     primer_producto = productos[0]
    #     descargar_imagen(
    #         producto_id = primer_producto['Id'],
    #         nombre      = primer_producto['Name'],
    #         token       = token,
    #     )

    print("=" * 60)
    print("  Pipeline SAR completado")
    print("  Siguiente paso: faro_sar_georef.py --area", area_name)
    print("=" * 60)

    return productos


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='FARO PROTOCOL — Descarga SAR Sentinel-1')
    parser.add_argument(
        '--area', required=True,
        help=f"Área a procesar. Disponibles: {', '.join(list_areas())}"
    )
    args = parser.parse_args()
    main(args.area)
