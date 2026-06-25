"""
faro_gpm.py — GPM IMERG precipitación acumulada diaria (venue)
Fuente: NASA GPM_3IMERGDL (Daily Late Run) — NASA Earthdata via earthaccess
Revisita: diaria. Latencia: 4-6 horas desde fin del día UTC.
Resolución: 0.1° × 0.1° (~11 km) — 1 píxel por venue.

Variable: /Grid/precipitationCal — mm/día (acumulado calibrado)
Requiere: NASA_EARTHDATA_USER + NASA_EARTHDATA_PASS
"""
from __future__ import annotations
import logging, os, sys
from datetime import date, timedelta

import numpy as np

log = logging.getLogger(__name__)
_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

_GPM_SHORTNAME  = "GPM_3IMERGDL"
_GPM_SHORTNAMES = ["GPM_3IMERGDL", "GPM_3IMERGDL.07", "GPM_3IMERGDF"]


def _nasa_login() -> bool:
    user = os.environ.get("NASA_EARTHDATA_USER") or os.environ.get("EARTHDATA_USERNAME")
    pwd  = os.environ.get("NASA_EARTHDATA_PASS")  or os.environ.get("EARTHDATA_PASSWORD")
    if user and not os.environ.get("EARTHDATA_USERNAME"):
        os.environ["EARTHDATA_USERNAME"] = user
    if pwd and not os.environ.get("EARTHDATA_PASSWORD"):
        os.environ["EARTHDATA_PASSWORD"] = pwd
    try:
        import earthaccess
        earthaccess.login(strategy="environment")
        return True
    except Exception as _e:
        log.warning("gpm: NASA login failed: %s", _e)
        return False


def run_gpm_cycle(venue_id: str) -> dict:
    """
    Descarga GPM IMERG Daily Late Run y extrae precipitación acumulada (mm/día)
    para el píxel 0.1°×0.1° del venue. Guarda precip_gpm_mm en climate_metrics.
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

    # GPM tiene latencia de 4-6h → buscamos ayer si es antes de las 08:00 UTC
    from datetime import datetime, timezone
    now   = datetime.now(timezone.utc)
    today = date.today()
    # Si son menos de las 08:00 UTC, ayer puede no estar disponible aún → buscar anteayer también
    days_back = 3
    start = (today - timedelta(days=days_back)).isoformat()
    end   = today.isoformat()

    bbox = (lon - 0.15, lat - 0.15, lon + 0.15, lat + 0.15)

    if not _nasa_login():
        return {}

    try:
        import earthaccess
        import h5py
    except ImportError as _ie:
        log.warning("gpm: dep faltante %s — skipping", _ie)
        return {}

    result: dict = {}
    try:
        granules = []
        used_sn  = ""
        for sn in _GPM_SHORTNAMES:
            try:
                granules = earthaccess.search_data(
                    short_name   = sn,
                    temporal     = (start, end),
                    bounding_box = bbox,
                    count        = 5,
                )
                if granules:
                    used_sn = sn
                    log.info("gpm: %d granules [%s]", len(granules), sn)
                    break
            except Exception as _se:
                log.debug("gpm search %s: %s", sn, _se)

        if not granules:
            log.warning("gpm %s: 0 granules en últimos %d días", venue_id, days_back)
            return {}

        # Tomar el granule más reciente (último día disponible)
        files = earthaccess.open(granules[-1:])
        if not files:
            return {}

        with h5py.File(files[0], "r") as f:
            # Estructura GPM IMERG: /Grid/precipitationCal, /Grid/lat, /Grid/lon
            grp = f.get("Grid") or f  # algunos tienen /Grid, otros no

            precip_var = None
            for vname in ("precipitationCal", "precipitation", "HQprecipitation"):
                if vname in grp:
                    precip_var = np.asarray(grp[vname], dtype=float)
                    break

            if precip_var is None:
                log.warning("gpm: variable precipitación no encontrada — keys: %s", list(grp.keys()))
                return {}

            lat_arr = np.asarray(grp["lat"], dtype=float) if "lat" in grp else np.linspace(-90, 90, precip_var.shape[-1])
            lon_arr = np.asarray(grp["lon"], dtype=float) if "lon" in grp else np.linspace(-180, 180, precip_var.shape[-2])

            # GPM shape típico: (time, lon, lat) o (time, lat, lon) — detectar orientación
            # Los datos son (1, 3600, 1800) → (lon_idx, lat_idx) para 0.1° global
            # lat: -90..+90 (1800 valores), lon: -180..+180 (3600 valores)
            lat_idx = int(np.argmin(np.abs(lat_arr - lat)))
            lon_idx = int(np.argmin(np.abs(lon_arr - lon)))

            # Extraer valor según shape
            pv = precip_var.squeeze()  # quitar dimensión time=1
            if pv.ndim == 2:
                if pv.shape[0] == len(lon_arr) and pv.shape[1] == len(lat_arr):
                    val = float(pv[lon_idx, lat_idx])   # (lon, lat)
                else:
                    val = float(pv[lat_idx, lon_idx])   # (lat, lon)
            else:
                val = float(pv.ravel()[0])   # fallback

            if np.isnan(val) or val < 0:
                val = 0.0  # fillvalue típico es -9999.9

            precip_mm = round(max(val, 0.0), 2)
            result = {
                "precip_gpm_mm": precip_mm,
                "fuente":        f"GPM IMERG Daily Late · {used_sn}",
                "fecha":         end,
            }
            log.info("gpm %s: %.2f mm/día [%s]", venue_id, precip_mm, used_sn)

    except Exception as _e:
        log.warning("gpm (non-fatal) %s: %s", venue_id, _e)
        return {}

    if result and _vs:
        try:
            row = _vs.get_latest_climate_row(venue_id)
            if row:
                _vs.patch_row("climate_metrics", row["id"],
                              {"precip_gpm_mm": result["precip_gpm_mm"]})
        except Exception as _pe:
            log.warning("gpm patch_row (non-fatal): %s", _pe)

    return result


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    import json
    print(json.dumps(run_gpm_cycle("amalfitani"), indent=2, default=str))
