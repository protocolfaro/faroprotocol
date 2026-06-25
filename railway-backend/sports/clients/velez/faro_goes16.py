"""
faro_goes16.py — GOES-16 ABI nubosidad en tiempo real (Buenos Aires)
Fuente: NOAA GOES-16 ABI-L2-ACM Full Disk — AWS S3 público (sin auth)
Revisita: ~10 min (satélite geoestacionario, 75.2°W)
Latencia: 0 min desde captura

Variable BCM: 0=cielo claro, 1=nublado/incierto
Coordenadas x/y: ángulos de escaneo en radianes (xarray los decodifica automáticamente)
"""
from __future__ import annotations
import logging, os, sys, tempfile, xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone

import numpy as np
import requests

log = logging.getLogger(__name__)
_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

_S3_BASE   = "https://noaa-goes16.s3.amazonaws.com"
_GOES_LON0 = -75.2   # GOES-16 subpoint longitud (°W)
_MAX_MB    = 30       # skip si el archivo es mayor (evitar MCMIP que pesa 150MB+)


# ── Proyección GOES-16 ABI — GOES-R PUG-L2+, Apéndice A ─────────────────────

def _latlon_to_xy(lat: float, lon: float) -> tuple[float, float]:
    """Convierte lat/lon WGS84 a ángulos de escaneo GOES-16 ABI (rad)."""
    H     = 42_164_160.0    # distancia satélite-centro Tierra (m)
    r_eq  = 6_378_137.0
    r_pol = 6_356_752.31414
    e2    = 0.00669437999014
    lon0  = np.radians(_GOES_LON0)
    lat_r = np.radians(lat)
    lon_r = np.radians(lon)

    lat_c = np.arctan((r_pol / r_eq) ** 2 * np.tan(lat_r))
    rc    = r_pol / np.sqrt(1.0 - e2 * np.cos(lat_c) ** 2)
    sx    =  H - rc * np.cos(lat_c) * np.cos(lon_r - lon0)
    sy    = -rc * np.cos(lat_c) * np.sin(lon_r - lon0)
    sz    =  rc * np.sin(lat_c)
    denom = np.sqrt(sx**2 + sy**2 + sz**2)
    x     = np.arcsin(-sy / denom)
    y     = np.arctan(sz / sx)
    return float(x), float(y)


# ── Acceso S3 ─────────────────────────────────────────────────────────────────

def _list_s3(prefix: str) -> list[str]:
    r = requests.get(
        _S3_BASE,
        params={"prefix": prefix, "list-type": "2"},
        timeout=10,
    )
    r.raise_for_status()
    ns   = {"s3": "http://s3.amazonaws.com/doc/2006-03-01/"}
    tree = ET.fromstring(r.text)
    return [k.text for k in tree.findall(".//s3:Key", ns)
            if k.text and k.text.endswith(".nc")]


def _open_nc(path: str):
    import xarray as xr
    for engine in ("netcdf4", "h5netcdf", "scipy"):
        try:
            return xr.open_dataset(path, engine=engine, mask_and_scale=True)
        except Exception:
            continue
    raise RuntimeError("sin engine NetCDF4 disponible (netcdf4/h5netcdf/scipy)")


# ── Pipeline principal ────────────────────────────────────────────────────────

def run_goes16_cycle(venue_id: str) -> dict:
    """
    Descarga la escena GOES-16 ABI-L2-ACM más reciente y calcula
    nubosidad (%) para un radio de ~20 km alrededor del venue.
    Guarda nubosidad_goes16_pct en climate_metrics.
    """
    try:
        import velez_supabase as _vs
    except ImportError:
        _vs = None

    try:
        from faro_v2_engine import VENUE_REGISTRY
        v   = VENUE_REGISTRY.get(venue_id, {})
        lat = v.get("lat", -34.6373)
        lon = v.get("lon", -58.5240)
    except Exception:
        lat, lon = -34.6373, -58.5240

    result: dict = {}
    now = datetime.now(timezone.utc)

    for h_offset in range(3):   # hora actual y 2 anteriores por latencia S3
        t    = now - timedelta(hours=h_offset)
        doy  = t.timetuple().tm_yday
        year = t.year
        hour = t.hour
        try:
            files = _list_s3(f"ABI-L2-ACMF/{year}/{doy:03d}/{hour:02d}/")
            if not files:
                continue
            latest = sorted(files)[-1]
            url    = f"{_S3_BASE}/{latest}"

            # Verificar tamaño antes de descargar (ABI-L2-ACMF ~5-15 MB, MCMIPF ~150 MB)
            head = requests.head(url, timeout=8)
            size_mb = int(head.headers.get("Content-Length", 0)) / 1e6
            if size_mb > _MAX_MB:
                log.warning("goes16: archivo demasiado grande (%.0f MB) — skip", size_mb)
                continue

            log.info("goes16: %s (%.1f MB)", latest.split("/")[-1], size_mb)
            nc_bytes = requests.get(url, timeout=90).content

            with tempfile.NamedTemporaryFile(suffix=".nc", delete=False) as tmp:
                tmp.write(nc_bytes)
                tmp_path = tmp.name

            try:
                ds = _open_nc(tmp_path)

                # x/y son ángulos de escaneo en rad (xarray los decodifica con scale_factor/offset)
                x_vals = np.asarray(ds.x.values, dtype=float)
                y_vals = np.asarray(ds.y.values, dtype=float)
                x_t, y_t = _latlon_to_xy(lat, lon)

                xi   = int(np.argmin(np.abs(x_vals - x_t)))
                yi   = int(np.argmin(np.abs(y_vals - y_t)))
                r_px = 10   # radio ~20 km en full disk 2 km/px

                bcm = ds["BCM"].values[
                    max(0, yi - r_px) : yi + r_px,
                    max(0, xi - r_px) : xi + r_px,
                ]
                cloud_frac    = float(np.nanmean(bcm.astype(float)))
                nubosidad_pct = round(cloud_frac * 100, 1)

                result = {
                    "nubosidad_pct": nubosidad_pct,
                    "escena_clara":  cloud_frac < 0.30,
                    "fuente":        f"GOES-16 ABI-L2-ACM · {latest.split('/')[-1][:25]}",
                    "timestamp_utc": t.strftime("%Y-%m-%dT%H:%M:00Z"),
                }
                ds.close()

            finally:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass

            if result:
                break

        except Exception as _e:
            log.debug("goes16 h_offset=%d: %s", h_offset, _e)

    if result:
        log.info("goes16 %s: nubosidad=%.1f%% clara=%s",
                 venue_id, result["nubosidad_pct"], result["escena_clara"])
        if _vs:
            try:
                row = _vs.get_latest_climate_row(venue_id)
                if row:
                    _vs.patch_row("climate_metrics", row["id"],
                                  {"nubosidad_goes16_pct": result["nubosidad_pct"]})
            except Exception as _pe:
                log.warning("goes16 patch_row (non-fatal): %s", _pe)
    else:
        log.warning("goes16 %s: sin datos disponibles", venue_id)

    return result


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    import json
    print(json.dumps(run_goes16_cycle("amalfitani"), indent=2, default=str))
