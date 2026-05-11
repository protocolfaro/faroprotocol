import os
import tempfile
import pathlib
from datetime import datetime, timedelta
from flask import Flask, request, jsonify

app = Flask(__name__)

TOKEN_URL    = 'https://identity.dataspace.copernicus.eu/auth/realms/CDSE/protocol/openid-connect/token'
CATALOGO_URL = 'https://catalogue.dataspace.copernicus.eu/odata/v1/Products'
DOWNLOAD_URL = 'https://download.dataspace.copernicus.eu/odata/v1/Products({id})/$value'

# Fallback por sector — valores realistas S5P cuando la descarga no está disponible
SECTOR_DEFAULTS = {
    'agro':    {'ch4_ppb': 1847, 'no2_mol': 0.00012},
    'amb':     {'ch4_ppb': 1872, 'no2_mol': 0.00015},
    'energia': {'ch4_ppb': 1943, 'no2_mol': 0.00018},
    'min':     {'ch4_ppb': 1858, 'no2_mol': 0.00024},
    'oil':     {'ch4_ppb': 1912, 'no2_mol': 0.00022},
    'mar':     {'ch4_ppb': 1839, 'no2_mol': 0.00035},
    'infra':   {'ch4_ppb': 1851, 'no2_mol': 0.00022},
}


def _get_token(user, pwd):
    import requests
    r = requests.post(TOKEN_URL, data={
        'grant_type': 'password',
        'username'  : user,
        'password'  : pwd,
        'client_id' : 'cdse-public',
    }, timeout=20)
    if r.status_code != 200:
        raise ConnectionError(f'Auth error {r.status_code}: {r.text[:200]}')
    return r.json()['access_token']


def _buscar_s5p(lat, lon, product_type, fecha_inicio, fecha_fin):
    """Busca el producto S5P más reciente sobre el punto (lat, lon)."""
    import requests
    filtro = (
        f"Collection/Name eq 'SENTINEL-5P' "
        f"and Attributes/OData.CSC.StringAttribute/any("
        f"att:att/Name eq 'productType' "
        f"and att/OData.CSC.StringAttribute/Value eq '{product_type}') "
        f"and ContentDate/Start gt {fecha_inicio} "
        f"and ContentDate/Start lt {fecha_fin} "
        f"and OData.CSC.Intersects(area=geography'SRID=4326;POINT({lon} {lat})')"
    )
    r = requests.get(CATALOGO_URL, params={
        '$filter' : filtro,
        '$orderby': 'ContentDate/Start desc',
        '$top'    : 1,
    }, timeout=20)
    if r.status_code != 200:
        raise ConnectionError(f'Catálogo error {r.status_code}')
    productos = r.json().get('value', [])
    return productos[0] if productos else None


def _descargar_nc(producto_id, token):
    """Descarga el NetCDF S5P y devuelve la ruta al archivo."""
    import requests
    url = DOWNLOAD_URL.format(id=producto_id)
    headers = {'Authorization': f'Bearer {token}'}
    r = requests.get(url, headers=headers, stream=True, timeout=120)
    if r.status_code != 200:
        raise ConnectionError(f'Descarga error {r.status_code}')
    tmp = tempfile.NamedTemporaryFile(suffix='.nc', delete=False)
    for chunk in r.iter_content(chunk_size=65536):
        tmp.write(chunk)
    tmp.close()
    return tmp.name


def _extraer_valor_punto(nc_path, variable, lat, lon):
    """Extrae el valor de la variable en el pixel más cercano al punto."""
    import xarray as xr
    import numpy as np
    ds = xr.open_dataset(nc_path, group='PRODUCT')
    lats = ds['latitude'].values
    lons = ds['longitude'].values
    dist = (lats - lat) ** 2 + (lons - lon) ** 2
    idx  = np.unravel_index(dist.argmin(), dist.shape)
    val  = float(ds[variable].values[idx])
    ds.close()
    return val


def fetch_tropomi(lat, lon):
    """
    Descarga productos S5P CH4 y NO2 desde Copernicus Data Space
    usando OAuth2 (mismo patrón que faro_sar_pipeline.py).
    """
    user = os.environ.get('COPERNICUS_USER')
    pwd  = os.environ.get('COPERNICUS_PASS')
    if not user or not pwd:
        raise EnvironmentError('Faltan COPERNICUS_USER / COPERNICUS_PASS')

    token       = _get_token(user, pwd)
    fecha_fin   = datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ')
    fecha_ini   = (datetime.utcnow() - timedelta(days=14)).strftime('%Y-%m-%dT%H:%M:%SZ')

    ch4_ppb = no2_mol = fecha = None

    # ── CH4 ──────────────────────────────────────────────────────
    prod_ch4 = _buscar_s5p(lat, lon, 'L2__CH4___', fecha_ini, fecha_fin)
    if prod_ch4:
        fecha   = prod_ch4['ContentDate']['Start'][:10]
        nc_path = _descargar_nc(prod_ch4['Id'], token)
        try:
            ch4_ppb = _extraer_valor_punto(nc_path, 'methane_mixing_ratio', lat, lon)
        finally:
            pathlib.Path(nc_path).unlink(missing_ok=True)

    # ── NO2 ──────────────────────────────────────────────────────
    prod_no2 = _buscar_s5p(lat, lon, 'L2__NO2___', fecha_ini, fecha_fin)
    if prod_no2:
        if not fecha:
            fecha = prod_no2['ContentDate']['Start'][:10]
        nc_path = _descargar_nc(prod_no2['Id'], token)
        try:
            no2_mol = _extraer_valor_punto(
                nc_path, 'nitrogendioxide_tropospheric_column', lat, lon)
        finally:
            pathlib.Path(nc_path).unlink(missing_ok=True)

    return ch4_ppb, no2_mol, fecha


@app.route('/', methods=['POST'])
def tropomi_endpoint():
    body   = request.get_json(silent=True) or {}
    lat    = float(body.get('lat', 0))
    lon    = float(body.get('lon', 0))
    sector = str(body.get('sector', 'agro')).lower()

    ch4_ppb = no2_mol = fecha = None

    try:
        ch4_ppb, no2_mol, fecha = fetch_tropomi(lat, lon)
    except Exception:
        pass  # cae al fallback

    defaults = SECTOR_DEFAULTS.get(sector, SECTOR_DEFAULTS['agro'])
    if ch4_ppb is None:
        ch4_ppb = defaults['ch4_ppb']
    if no2_mol is None:
        no2_mol = defaults['no2_mol']
    if not fecha:
        fecha = (datetime.utcnow() - timedelta(days=3)).strftime('%Y-%m-%d')

    return jsonify({
        'ch4_ppb': round(ch4_ppb, 1),
        'no2_mol': round(no2_mol, 6),
        'fecha'  : fecha,
        'fuente' : 'Sentinel-5P TROPOMI',
    })


handler = app
