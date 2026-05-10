import os
import requests
from datetime import datetime, timedelta
import zipfile
import json

# Credenciales desde variables de entorno
USER = os.environ.get("COPERNICUS_USER")
PASS = os.environ.get("COPERNICUS_PASS")

# Área de interés - Vaca Muerta
AOI = {
    "type": "Polygon",
    "coordinates": [[
        [-70.5, -39.5],
        [-68.5, -39.5],
        [-68.5, -37.5],
        [-70.5, -37.5],
        [-70.5, -39.5]
    ]]
}

BASE_URL = "https://catalogue.dataspace.copernicus.eu/odata/v1"
TOKEN_URL = "https://identity.dataspace.copernicus.eu/auth/realms/CDSE/protocol/openid-connect/token"

def get_token():
    print("Obteniendo token de autenticación...")
    response = requests.post(TOKEN_URL, data={
        "grant_type": "password",
        "username": USER,
        "password": PASS,
        "client_id": "cdse-public"
    })
    if response.status_code == 200:
        token = response.json()["access_token"]
        print("Token obtenido OK")
        return token
    else:
        print(f"Error de autenticación: {response.status_code}")
        print(response.text)
        return None

def search_sentinel1(days_back=30):
    print(f"Buscando imágenes Sentinel-1 de los últimos {days_back} días...")
    date_end = datetime.utcnow()
    date_start = date_end - timedelta(days=days_back)

    aoi_wkt = "POLYGON((-70.5 -39.5,-68.5 -39.5,-68.5 -37.5,-70.5 -37.5,-70.5 -39.5))"

    params = {
        "$filter": (
            f"Collection/Name eq 'SENTINEL-1' and "
            f"OData.CSC.Intersects(area=geography'SRID=4326;{aoi_wkt}') and "
            f"ContentDate/Start gt {date_start.strftime('%Y-%m-%dT%H:%M:%S.000Z')} and "
            f"ContentDate/Start lt {date_end.strftime('%Y-%m-%dT%H:%M:%S.000Z')} and "
            f"Attributes/OData.CSC.StringAttribute/any(att:att/Name eq 'productType' and att/OData.CSC.StringAttribute/Value eq 'GRD')"
        ),
        "$top": 5,
        "$orderby": "ContentDate/Start desc"
    }

    response = requests.get(f"{BASE_URL}/Products", params=params)
    if response.status_code == 200:
        products = response.json().get("value", [])
        print(f"Encontradas {len(products)} imágenes")
        return products
    else:
        print(f"Error en búsqueda: {response.status_code}")
        return []

def download_product(token, product_id, product_name):
    output_dir = "sar_downloads"
    os.makedirs(output_dir, exist_ok=True)
    output_path = os.path.join(output_dir, f"{product_name}.zip")

    if os.path.exists(output_path):
        print(f"Ya existe: {output_path}")
        return output_path

    print(f"Descargando {product_name}...")
   # Copernicus requiere URL de descarga separada
    headers = {"Authorization": f"Bearer {token}"}
    url = f"https://download.dataspace.copernicus.eu/odata/v1/Products({product_id})/$value"

    with requests.get(url, headers=headers, stream=True) as r:
        r.raise_for_status()
        total = int(r.headers.get("content-length", 0))
        downloaded = 0
        with open(output_path, "wb") as f:
            for chunk in r.iter_content(chunk_size=8192):
                f.write(chunk)
                downloaded += len(chunk)
                if total:
                    pct = downloaded / total * 100
                    print(f"\r  {pct:.1f}%", end="", flush=True)
    print(f"\nDescargado: {output_path}")
    return output_path

def main():
    if not USER or not PASS:
        print("ERROR: Faltan credenciales.")
        print("Corré primero:")
        print("  set COPERNICUS_USER=tu_usuario")
        print("  set COPERNICUS_PASS=tu_contraseña")
        return

    token = get_token()
    if not token:
        return

    products = search_sentinel1(days_back=30)
    if not products:
        print("No se encontraron imágenes.")
        return

    print("\nImágenes disponibles:")
    for i, p in enumerate(products):
        print(f"  [{i}] {p['Name']} - {p['ContentDate']['Start']}")

    product = products[0]
    print(f"\nDescargando la más reciente: {product['Name']}")
    download_product(token, product["Id"], product["Name"])
    print("\nListo. Imagen SAR descargada en carpeta sar_downloads/")

if __name__ == "__main__":
    main()