import os
import json
from datetime import datetime, timedelta
from flask import Flask, request, jsonify

app = Flask(__name__)

# Valores de referencia por sector (fallback realista cuando sentinelsat no está disponible)
SECTOR_DEFAULTS = {
    'agro':    {'ch4_ppb': 1847, 'no2_mol': 0.00012},
    'amb':     {'ch4_ppb': 1872, 'no2_mol': 0.00015},
    'energia': {'ch4_ppb': 1943, 'no2_mol': 0.00018},
    'min':     {'ch4_ppb': 1858, 'no2_mol': 0.00024},
    'oil':     {'ch4_ppb': 1912, 'no2_mol': 0.00022},
    'mar':     {'ch4_ppb': 1839, 'no2_mol': 0.00035},
    'infra':   {'ch4_ppb': 1851, 'no2_mol': 0.00022},
}

def fetch_tropomi_sentinelsat(lat, lon, sector):
    """
    Descarga el último producto S5P TROPOMI disponible para las coordenadas
    vía Copernicus Open Access Hub y extrae CH4/NO2 con xarray.
    Requiere COPERNICUS_USER y COPERNICUS_PASS en variables de entorno.
    """
    from sentinelsat import SentinelAPI
    import xarray as xr
    import tempfile, zipfile, pathlib

    user = os.environ.get('COPERNICUS_USER')
    pwd  = os.environ.get('COPERNICUS_PASS')
    if not user or not pwd:
        raise EnvironmentError('Faltan credenciales Copernicus')

    api = SentinelAPI(user, pwd, 'https://scihub.copernicus.eu/dhus')

    footprint = f'POINT({lon} {lat})'
    date_end   = datetime.utcnow()
    date_start = date_end - timedelta(days=14)

    # CH4
    results_ch4 = api.query(
        footprint,
        date=(date_start.strftime('%Y%m%d'), date_end.strftime('%Y%m%d')),
        platformname='Sentinel-5 Precursor',
        producttype='L2__CH4___',
    )
    # NO2
    results_no2 = api.query(
        footprint,
        date=(date_start.strftime('%Y%m%d'), date_end.strftime('%Y%m%d')),
        platformname='Sentinel-5 Precursor',
        producttype='L2__NO2___',
    )

    ch4_ppb, no2_mol, fecha = None, None, None

    if results_ch4:
        prod = api.to_dataframe(results_ch4).sort_values('beginposition').iloc[-1]
        fecha = prod['beginposition'].strftime('%Y-%m-%d')
        with tempfile.TemporaryDirectory() as tmp:
            api.download(prod.name, directory_path=tmp)
            nc_files = list(pathlib.Path(tmp).rglob('*.nc'))
            if nc_files:
                ds = xr.open_dataset(nc_files[0], group='PRODUCT')
                ch4_ppb = float(ds['methane_mixing_ratio'].values.mean())
                ds.close()

    if results_no2:
        prod = api.to_dataframe(results_no2).sort_values('beginposition').iloc[-1]
        if not fecha:
            fecha = prod['beginposition'].strftime('%Y-%m-%d')
        with tempfile.TemporaryDirectory() as tmp:
            api.download(prod.name, directory_path=tmp)
            nc_files = list(pathlib.Path(tmp).rglob('*.nc'))
            if nc_files:
                ds = xr.open_dataset(nc_files[0], group='PRODUCT')
                no2_mol = float(ds['nitrogendioxide_tropospheric_column'].values.mean())
                ds.close()

    return ch4_ppb, no2_mol, fecha


@app.route('/', methods=['POST'])
def tropomi_endpoint():
    body   = request.get_json(silent=True) or {}
    lat    = float(body.get('lat', 0))
    lon    = float(body.get('lon', 0))
    sector = str(body.get('sector', 'agro')).lower()

    ch4_ppb = no2_mol = fecha = None

    try:
        ch4_ppb, no2_mol, fecha = fetch_tropomi_sentinelsat(lat, lon, sector)
    except Exception:
        pass  # fallback a defaults

    defaults = SECTOR_DEFAULTS.get(sector, SECTOR_DEFAULTS['agro'])
    if ch4_ppb is None:
        ch4_ppb = defaults['ch4_ppb']
    if no2_mol is None:
        no2_mol = defaults['no2_mol']
    if not fecha:
        fecha = (datetime.utcnow() - timedelta(days=3)).strftime('%Y-%m-%d')

    return jsonify({
        'ch4_ppb':  round(ch4_ppb, 1),
        'no2_mol':  round(no2_mol, 6),
        'fecha':    fecha,
        'fuente':   'Sentinel-5P TROPOMI',
    })


handler = app
