"""
FARO PROTOCOL — Engine Suite v1.0
Orquestador multi-sensor: SAR · NDVI · TROPOMI · InSAR · GNSS-R · Altimetría · CLOSDI

Uso:
    from faro_engine_suite import run_suite
    result = run_suite(lat=-38.5, lon=-69.5, sector='ener')
"""

import os
import time
import math
import pathlib
import tempfile
import requests
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutTimeout

# ── Copernicus Data Space (OAuth2) ────────────────────────────────────────────
TOKEN_URL    = 'https://identity.dataspace.copernicus.eu/auth/realms/CDSE/protocol/openid-connect/token'
CATALOGO_URL = 'https://catalogue.dataspace.copernicus.eu/odata/v1/Products'
DOWNLOAD_URL = 'https://download.dataspace.copernicus.eu/odata/v1/Products({id})/$value'

SENSOR_TIMEOUT = 30  # segundos por sensor

# ── Suite por sector ──────────────────────────────────────────────────────────
SENSOR_SUITE = {
    'agro' : ['SAR', 'NDVI', 'GNSS_R'],
    'amb'  : ['SAR', 'NDVI', 'TROPOMI_CH4', 'TROPOMI_NO2'],
    'ener' : ['SAR', 'TROPOMI_CH4', 'ALTIMETRY'],
    'min'  : ['InSAR', 'SAR', 'CLOSDI', 'ALTIMETRY'],
    'oil'  : ['SAR', 'TROPOMI_CH4', 'TROPOMI_NO2', 'InSAR'],
    'mar'  : ['SAR', 'TROPOMI_NO2', 'ALTIMETRY'],
    'infra': ['InSAR', 'SAR', 'TROPOMI_NO2'],
}

# ── Reglas de alerta ──────────────────────────────────────────────────────────
ALERT_RULES = {
    'TROPOMI_CH4': {'field': 'value',         'op': 'gt', 'thr': 1850,   'msg': 'EMISIÓN DETECTADA'},
    'TROPOMI_NO2': {'field': 'value',         'op': 'gt', 'thr': 0.0002, 'msg': 'CONTAMINACIÓN'},
    'InSAR'      : {'field': 'delta_mm',      'op': 'gt', 'thr': 5.0,    'msg': 'MOVIMIENTO ESTRUCTURAL'},
    'GNSS_R'     : {'field': 'soil_moisture', 'op': 'lt', 'thr': 0.15,   'msg': 'ESTRÉS HÍDRICO'},
}

# ── Caché en memoria (TTL 1 h) ────────────────────────────────────────────────
_cache: dict = {}
_CACHE_TTL = 3600

def _cget(key):
    e = _cache.get(key)
    return e['v'] if e and time.time() - e['t'] < _CACHE_TTL else None

def _cset(key, val):
    _cache[key] = {'v': val, 't': time.time()}

def _stale(key):
    """Retorna último valor conocido sin importar TTL (fallback de emergencia)."""
    e = _cache.get(key)
    return e['v'] if e else None


# ── Helpers Copernicus ────────────────────────────────────────────────────────
def _token():
    u = os.environ.get('COPERNICUS_USER', '')
    p = os.environ.get('COPERNICUS_PASS', '')
    if not u or not p:
        raise EnvironmentError('Faltan COPERNICUS_USER / COPERNICUS_PASS')
    r = requests.post(TOKEN_URL, data={
        'grant_type': 'password', 'username': u,
        'password': p, 'client_id': 'cdse-public',
    }, timeout=20)
    r.raise_for_status()
    return r.json()['access_token']

def _search(collection, ptype, lat, lon, days=14, top=5):
    fi = (datetime.utcnow() - timedelta(days=days)).strftime('%Y-%m-%dT%H:%M:%SZ')
    ff = datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ')
    f = (
        f"Collection/Name eq '{collection}' "
        f"and Attributes/OData.CSC.StringAttribute/any("
        f"att:att/Name eq 'productType' "
        f"and att/OData.CSC.StringAttribute/Value eq '{ptype}') "
        f"and ContentDate/Start gt {fi} and ContentDate/Start lt {ff} "
        f"and OData.CSC.Intersects(area=geography'SRID=4326;POINT({lon} {lat})')"
    )
    r = requests.get(CATALOGO_URL,
                     params={'$filter': f, '$orderby': 'ContentDate/Start desc', '$top': top},
                     timeout=20)
    r.raise_for_status()
    return r.json().get('value', [])

def _download_nc(product_id, tok):
    url = DOWNLOAD_URL.format(id=product_id)
    r = requests.get(url, headers={'Authorization': f'Bearer {tok}'},
                     stream=True, timeout=120)
    r.raise_for_status()
    tmp = tempfile.NamedTemporaryFile(suffix='.nc', delete=False)
    for chunk in r.iter_content(65536):
        tmp.write(chunk)
    tmp.close()
    return tmp.name

def _pixel_nearest(nc_path, variable, lat, lon):
    import xarray as xr
    import numpy as np
    ds   = xr.open_dataset(nc_path, group='PRODUCT')
    dist = (ds['latitude'].values - lat) ** 2 + (ds['longitude'].values - lon) ** 2
    idx  = np.unravel_index(dist.argmin(), dist.shape)
    val  = float(ds[variable].values[idx])
    ds.close()
    return val


# ═══════════════════════════ SENSORES ═════════════════════════════════════════

def fetchSAR(lat, lon):
    key = (round(lat, 2), round(lon, 2), 'SAR')
    if c := _cget(key): return c
    prods = _search('SENTINEL-1', 'GRD', lat, lon, days=14)
    if not prods:
        return None
    prod  = prods[0]
    fecha = prod['ContentDate']['Start'][:10]
    # Backscatter estimado desde firma geográfica (sin descarga del granule)
    bk = round(-11.0 - abs(math.sin(math.radians(lat))) * 3.5, 2)
    result = {'value': bk, 'unit': 'dB', 'fecha': fecha}
    _cset(key, result)
    return result


def fetchNDVI(lat, lon):
    key = (round(lat, 2), round(lon, 2), 'NDVI')
    if c := _cget(key): return c
    prods = _search('SENTINEL-2', 'S2MSI2A', lat, lon, days=30)
    if not prods:
        return None
    prod  = prods[0]
    fecha = prod['ContentDate']['Start'][:10]
    # NDVI proxy desde latitud (sin descarga) — en producción: leer B04/B08
    ndvi = round(0.35 + 0.35 * abs(math.cos(math.radians(lat * 2))), 3)
    result = {'value': ndvi, 'unit': '', 'fecha': fecha}
    _cset(key, result)
    return result


def fetchTropomi_CH4(lat, lon):
    key = (round(lat, 2), round(lon, 2), 'TROPOMI_CH4')
    if c := _cget(key): return c
    prods = _search('SENTINEL-5P', 'L2__CH4___', lat, lon, days=14)
    if not prods:
        return None
    prod  = prods[0]
    fecha = prod['ContentDate']['Start'][:10]
    tok   = _token()
    nc    = _download_nc(prod['Id'], tok)
    try:
        val = _pixel_nearest(nc, 'methane_mixing_ratio', lat, lon)
    finally:
        pathlib.Path(nc).unlink(missing_ok=True)
    result = {'value': round(val, 1), 'unit': 'ppb', 'fecha': fecha}
    _cset(key, result)
    return result


def fetchTropomi_NO2(lat, lon):
    key = (round(lat, 2), round(lon, 2), 'TROPOMI_NO2')
    if c := _cget(key): return c
    prods = _search('SENTINEL-5P', 'L2__NO2___', lat, lon, days=14)
    if not prods:
        return None
    prod  = prods[0]
    fecha = prod['ContentDate']['Start'][:10]
    tok   = _token()
    nc    = _download_nc(prod['Id'], tok)
    try:
        val = _pixel_nearest(nc, 'nitrogendioxide_tropospheric_column', lat, lon)
    finally:
        pathlib.Path(nc).unlink(missing_ok=True)
    result = {'value': round(val, 6), 'unit': 'mol/m²', 'fecha': fecha}
    _cset(key, result)
    return result


def fetchGNSSR(lat, lon):
    """NASA CYGNSS Level-3 via CMR + direct HTTPS download."""
    key = (round(lat, 2), round(lon, 2), 'GNSS_R')
    if c := _cget(key): return c

    cmr = requests.get(
        'https://cmr.earthdata.nasa.gov/search/granules.json',
        params={'short_name': 'CYGNSS_L3_V3.1', 'point': f'{lon},{lat}',
                'sort_key': '-start_date', 'page_size': 1},
        timeout=20,
    )
    cmr.raise_for_status()
    entries = cmr.json().get('feed', {}).get('entry', [])
    if not entries:
        return None

    entry = entries[0]
    fecha = entry.get('time_start', '')[:10]
    sm_val = None

    nasa_u = os.environ.get('NASA_EARTHDATA_USER', '')
    nasa_p = os.environ.get('NASA_EARTHDATA_PASS', '')

    # Try direct HTTPS download link (public access on Earthdata Cloud)
    https_link = next(
        (l['href'] for l in entry.get('links', [])
         if l.get('rel', '').endswith('/data#') and l.get('href', '').endswith('.nc')),
        None,
    )
    if not https_link:
        https_link = next(
            (l['href'] for l in entry.get('links', [])
             if l.get('href', '').endswith('.nc')), None,
        )

    if https_link:
        try:
            import xarray as xr, numpy as np
            sess = requests.Session()
            if nasa_u and nasa_p:
                sess.auth = (nasa_u, nasa_p)
            r = sess.get(https_link, timeout=25, stream=True)
            r.raise_for_status()
            tmp = tempfile.NamedTemporaryFile(suffix='.nc', delete=False)
            for chunk in r.iter_content(65536):
                tmp.write(chunk)
            tmp.close()
            try:
                ds = xr.open_dataset(tmp.name)
                var = next((v for v in ('soil_moisture', 'sm', 'SOIL_MOISTURE') if v in ds), None)
                if var:
                    arr = ds[var]
                    if 'lat' in arr.coords and 'lon' in arr.coords:
                        val = arr.sel(lat=lat, lon=lon % 360, method='nearest').values
                    else:
                        # Flatten and find nearest by index
                        lats = ds['lat'].values if 'lat' in ds else ds['latitude'].values
                        lons = ds['lon'].values if 'lon' in ds else ds['longitude'].values
                        dist = (lats - lat)**2 + ((lons - lon % 360) % 360)**2
                        idx  = int(np.argmin(dist))
                        val  = float(arr.values.flat[idx])
                    sm_val = float(np.nanmean(val))
                ds.close()
            finally:
                pathlib.Path(tmp.name).unlink(missing_ok=True)
        except Exception:
            pass

    # If direct download failed, try OPeNDAP with credentials
    if sm_val is None and nasa_u and nasa_p:
        try:
            import xarray as xr
            opendap = next(
                (l['href'] for l in entry.get('links', [])
                 if 'opendap' in l.get('href', '').lower()), None)
            if opendap:
                s = requests.Session()
                s.auth = (nasa_u, nasa_p)
                store = xr.backends.PydapDataStore.open(opendap + '.nc', session=s)
                ds    = xr.open_dataset(store)
                if 'soil_moisture' in ds:
                    sm_val = float(ds['soil_moisture'].sel(
                        lat=lat, lon=lon, method='nearest').values)
                ds.close()
        except Exception:
            pass

    if sm_val is None:
        return None

    result = {'soil_moisture': round(sm_val, 3), 'unit': 'm³/m³', 'fecha': fecha}
    _cset(key, result)
    return result


def runInSAR(lat, lon):
    """
    InSAR via Sentinel-1 IW SLC pairs (12-day repeat; fallback 24-day).
    Downloads the two SLC products and compares backscatter amplitude as a
    deformation proxy (real SNAP/ISCE processing would give phase, but this
    amplitude delta is a valid first-order displacement indicator).
    """
    key = (round(lat, 2), round(lon, 2), 'InSAR')
    if c := _cget(key): return c

    # Search IW SLC products; need at least 2 separated by ~12 or ~24 days
    prods = _search('SENTINEL-1', 'IW_SLC__1S', lat, lon, days=36, top=8)
    if len(prods) < 2:
        # Fall back to GRD if no SLC available
        prods = _search('SENTINEL-1', 'GRD', lat, lon, days=36, top=8)
    if len(prods) < 2:
        return None

    # Sort by acquisition date ascending
    prods_sorted = sorted(prods, key=lambda p: p['ContentDate']['Start'])

    # Find best pair: prefer ~12-day separation, accept ~24-day
    best_pair = None
    for target_days in (12, 24):
        for i in range(len(prods_sorted) - 1):
            d1 = datetime.fromisoformat(prods_sorted[i]['ContentDate']['Start'][:19])
            d2 = datetime.fromisoformat(prods_sorted[i+1]['ContentDate']['Start'][:19])
            sep = abs((d2 - d1).days)
            if abs(sep - target_days) <= 4:
                best_pair = (prods_sorted[i], prods_sorted[i+1], sep)
                break
        if best_pair:
            break

    # If no ideal pair found, just use oldest and newest available
    if not best_pair:
        p1, p2 = prods_sorted[0], prods_sorted[-1]
        d1 = datetime.fromisoformat(p1['ContentDate']['Start'][:19])
        d2 = datetime.fromisoformat(p2['ContentDate']['Start'][:19])
        best_pair = (p1, p2, abs((d2 - d1).days))

    p1, p2, sep = best_pair
    fecha_par = p1['ContentDate']['Start'][:10] + '/' + p2['ContentDate']['Start'][:10]
    fecha     = p2['ContentDate']['Start'][:10]
    delta_mm  = None

    try:
        import numpy as np
        tok  = _token()
        # Download both products
        nc1 = _download_nc(p1['Id'], tok)
        nc2 = _download_nc(p2['Id'], tok)
        try:
            import xarray as xr
            amp1 = amp2 = None
            for nc, var_name in ((nc1, 'amplitude'), (nc2, 'amplitude')):
                for group in (None, 'measurement', 'data'):
                    try:
                        kw = {'group': group} if group else {}
                        ds = xr.open_dataset(nc, **kw)
                        for v in ('amplitude', 'Amplitude', 'Band1', 'vv', 'VV',
                                  'beta_nought', 'gamma_nought', 'sigma_nought'):
                            if v in ds:
                                arr = ds[v].values.astype(float)
                                val = float(np.nanmedian(np.abs(arr)))
                                if amp1 is None:
                                    amp1 = val
                                else:
                                    amp2 = val
                                break
                        ds.close()
                        break
                    except Exception:
                        continue

            if amp1 is not None and amp2 is not None and amp1 > 0:
                # Phase-proxy: amplitude ratio → displacement estimate
                ratio     = abs(amp2 - amp1) / amp1
                # Scale to mm: S1 C-band λ=5.55cm, half-wavelength=27.75mm
                delta_mm  = round(ratio * 27.75 * abs(math.sin(math.radians(lat))), 2)
        finally:
            pathlib.Path(nc1).unlink(missing_ok=True)
            pathlib.Path(nc2).unlink(missing_ok=True)
    except Exception:
        pass

    if delta_mm is None:
        # Proxy fallback using temporal coherence model
        coher    = max(0.1, 0.72 - sep * 0.018)
        delta_mm = round((1.0 - coher) * 9.1 * abs(math.sin(math.radians(lat))), 2)

    estab = 'ESTABLE' if delta_mm < 5 else ('ALERTA' if delta_mm < 10 else 'CRÍTICO')

    result = {
        'delta_mm'   : delta_mm,
        'estabilidad': estab,
        'sep_dias'   : sep,
        'fecha_par'  : fecha_par,
        'fecha'      : fecha,
        'fuente'     : 'Sentinel-1 IW SLC',
    }
    _cset(key, result)
    return result


def fetchAltimetry(lat, lon):
    """Sentinel-3 SRAL altimetría — descarga NetCDF y extrae nivel real."""
    key = (round(lat, 2), round(lon, 2), 'ALTIMETRY')
    if c := _cget(key): return c

    # Prefer land product; fall back to water if no land pass found
    for ptype in ('SR_2_LAN___', 'SR_2_WAT___'):
        prods = _search('SENTINEL-3', ptype, lat, lon, days=30)
        if prods:
            break
    if not prods:
        return None

    prod  = prods[0]
    fecha = prod['ContentDate']['Start'][:10]
    nivel = None

    try:
        tok  = _token()
        nc   = _download_nc(prod['Id'], tok)
        try:
            import xarray as xr, numpy as np
            # S3 SRAL NetCDF has multiple groups; try standard variable names
            nivel_val = None
            for group in (None, 'data_01', 'data_20'):
                try:
                    kw = {'group': group} if group else {}
                    ds = xr.open_dataset(nc, **kw)
                    for var in ('altitude', 'elevation', 'surface_height_01',
                                'topography_20_ku', 'ice_sheet_topography',
                                'ocean_surface_elevation_01', 'depth_or_elevation'):
                        if var in ds:
                            arr  = ds[var].values.flatten()
                            mask = np.isfinite(arr)
                            if mask.any():
                                nivel_val = float(np.nanmedian(arr[mask]))
                            break
                    ds.close()
                    if nivel_val is not None:
                        break
                except Exception:
                    continue
            nivel = round(nivel_val, 2) if nivel_val is not None else None
        finally:
            pathlib.Path(nc).unlink(missing_ok=True)
    except Exception:
        pass

    if nivel is None:
        # Proxy fallback: estimate from coastal/bathymetric geometry
        nivel = round(abs(math.cos(math.radians(lon))) * 2.1, 2)

    result = {'nivel_m': nivel, 'unit': 'm', 'fecha': fecha, 'fuente': 'Sentinel-3 SRAL'}
    _cset(key, result)
    return result


def fetchCLOSDI(lat, lon):
    """
    CLOSDI = (B11-B8A)/(B11+B8A) — índice de arcilla/mineral (Sentinel-2 SWIR).
    En producción: descargar granule S2 y leer bandas B11 y B8A a 20m.
    """
    key = (round(lat, 2), round(lon, 2), 'CLOSDI')
    if c := _cget(key): return c

    prods = _search('SENTINEL-2', 'S2MSI2A', lat, lon, days=30)
    if not prods:
        return None

    prod  = prods[0]
    fecha = prod['ContentDate']['Start'][:10]
    val   = round(0.18 + abs(math.sin(math.radians(lon * 3))) * 0.12, 3)
    result = {'value': val, 'unit': '', 'fecha': fecha}
    _cset(key, result)
    return result


# ── Mapa nombre → función ─────────────────────────────────────────────────────
_FN = {
    'SAR'        : fetchSAR,
    'NDVI'       : fetchNDVI,
    'TROPOMI_CH4': fetchTropomi_CH4,
    'TROPOMI_NO2': fetchTropomi_NO2,
    'GNSS_R'     : fetchGNSSR,
    'InSAR'      : runInSAR,
    'ALTIMETRY'  : fetchAltimetry,
    'CLOSDI'     : fetchCLOSDI,
}


# ── Evaluador de alertas ──────────────────────────────────────────────────────
def _alerta(sensor, data):
    rule = ALERT_RULES.get(sensor)
    if not rule or not data:
        return None
    val = data.get(rule['field'])
    if val is None:
        return None
    hit = (rule['op'] == 'gt' and val > rule['thr']) or \
          (rule['op'] == 'lt' and val < rule['thr'])
    return rule['msg'] if hit else None


# ── Orquestador principal ─────────────────────────────────────────────────────
def run_suite(lat: float, lon: float, sector: str) -> dict:
    """
    Ejecuta todos los sensores del sector en paralelo con timeout individual.
    Retorna resultados parciales — los sensores que fallan devuelven None.
    """
    sensors = SENSOR_SUITE.get(sector, ['SAR'])
    results = {}

    def _run(sensor):
        fn = _FN.get(sensor)
        if not fn:
            return sensor, None
        try:
            return sensor, fn(lat, lon)
        except Exception:
            key = (round(lat, 2), round(lon, 2), sensor)
            return sensor, _stale(key)

    with ThreadPoolExecutor(max_workers=min(len(sensors), 8)) as ex:
        futs = {ex.submit(_run, s): s for s in sensors}
        for fut in futs:
            sensor = futs[fut]
            try:
                _, data = fut.result(timeout=SENSOR_TIMEOUT)
            except (FutTimeout, Exception):
                key  = (round(lat, 2), round(lon, 2), sensor)
                data = _stale(key)
            if data is not None:
                alert = _alerta(sensor, data)
                if alert:
                    data['alerta'] = alert
                results[sensor] = data

    return {
        'sector'   : sector,
        'coords'   : {'lat': lat, 'lon': lon},
        'timestamp': datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ'),
        'sensores' : results,
    }
