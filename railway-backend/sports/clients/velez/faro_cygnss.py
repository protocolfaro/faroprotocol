"""
faro_cygnss.py — CYGNSS L3 humedad de suelo macro (contexto venue)
Fuente: NASA CYGNSS_L3_25KM_SOILM_V3.1 — NASA Earthdata via earthaccess
Revisita: diaria (~12-24h entre actualizaciones del producto L3)
Resolución: 25 km (macro contexto — no por cancha individual)

Variable soil_moisture: volumétrica (%) → divido por 100 para m³/m³
Requiere: NASA_EARTHDATA_USER + NASA_EARTHDATA_PASS
"""
from __future__ import annotations
import logging, os, sys, tempfile
from datetime import date, timedelta

import numpy as np

log = logging.getLogger(__name__)
_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

_CYGNSS_SHORTNAME = "CYGNSS_L3_25KM_SOILM_V3.1"
_CYGNSS_FALLBACKS = ["CYGNSS_L3_SM_V1.0", "CYGNSS_CDIP_SM_V1.0"]


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
        log.warning("cygnss: NASA login failed: %s", _e)
        return False


def _search_cygnss(bbox: tuple, days_back: int = 3):
    """Prueba CYGNSS_L3_25KM_SOILM_V3.1 y fallbacks. Retorna (results, short_name)."""
    import earthaccess
    today = date.today()
    start = (today - timedelta(days=days_back)).isoformat()
    end   = today.isoformat()

    for sn in [_CYGNSS_SHORTNAME] + _CYGNSS_FALLBACKS:
        try:
            results = earthaccess.search_data(
                short_name    = sn,
                temporal      = (start, end),
                bounding_box  = bbox,
                count         = 3,
            )
            if results:
                log.info("cygnss: %d granules [%s]", len(results), sn)
                return results, sn
        except Exception as _e:
            log.debug("cygnss search %s: %s", sn, _e)
    return [], ""


def run_cygnss_cycle(venue_id: str) -> dict:
    """
    Descarga el producto CYGNSS L3 SM más reciente y extrae la humedad
    del píxel (~25 km) sobre el venue. Guarda sm_cygnss_m3m3 en soil_metrics.
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

    # Bbox 0.5° alrededor del venue (mayor que el píxel de 25 km = 0.225°)
    bbox = (lon - 0.3, lat - 0.3, lon + 0.3, lat + 0.3)

    if not _nasa_login():
        return {}

    try:
        import earthaccess
        import h5py
    except ImportError as _ie:
        log.warning("cygnss: dep faltante %s — skipping", _ie)
        return {}

    result: dict = {}
    try:
        results, short_name = _search_cygnss(bbox, days_back=5)
        if not results:
            log.warning("cygnss %s: 0 granules (producto puede no estar disponible)", venue_id)
            return {}

        files = earthaccess.open(results[:1])
        if not files:
            return {}

        with h5py.File(files[0], "r") as f:
            # Intentar paths comunes del producto L3 SM de CYGNSS
            sm_var, lat_var, lon_var = None, None, None
            for sm_path in ("soil_moisture", "SM", "sm", "SoilMoisture"):
                if sm_path in f:
                    sm_var = f[sm_path][:]
                    break
                for grp in f.keys():
                    if sm_path in f[grp]:
                        sm_var = f[grp][sm_path][:]
                        break
                if sm_var is not None:
                    break

            for lat_path in ("lat", "latitude", "Latitude"):
                try:
                    lat_var = f[lat_path][:]
                    break
                except KeyError:
                    pass

            for lon_path in ("lon", "longitude", "Longitude"):
                try:
                    lon_var = f[lon_path][:]
                    break
                except KeyError:
                    pass

            if sm_var is None or lat_var is None or lon_var is None:
                log.warning("cygnss: estructura de variables no reconocida — keys: %s", list(f.keys()))
                return {}

            # Extraer píxel más cercano
            lat_arr = np.asarray(lat_var, dtype=float).ravel()
            lon_arr = np.asarray(lon_var, dtype=float).ravel()
            sm_flat = np.asarray(sm_var, dtype=float).ravel()

            dist   = np.sqrt((lat_arr - lat) ** 2 + (lon_arr - lon) ** 2)
            idx    = int(np.argmin(dist))
            sm_pct = float(sm_flat[idx])

            if np.isnan(sm_pct) or sm_pct < 0 or sm_pct > 100:
                log.warning("cygnss: valor inválido (%.2f%%) en píxel más cercano", sm_pct)
                return {}

            sm_m3m3 = round(sm_pct / 100.0, 4)
            result = {
                "sm_cygnss_m3m3": sm_m3m3,
                "sm_cygnss_pct":  round(sm_pct, 1),
                "fuente":         f"CYGNSS L3 25km · {short_name}",
                "fecha":          str(date.today()),
            }
            log.info("cygnss %s: SM=%.4f m³/m³ (%.1f%%)", venue_id, sm_m3m3, sm_pct)

    except Exception as _e:
        log.warning("cygnss (non-fatal) %s: %s", venue_id, _e)
        return {}

    if result and _vs:
        try:
            row = _vs.get_latest_soil_row(venue_id)
            if row:
                _vs.patch_row("soil_metrics", row["id"],
                              {"sm_cygnss_m3m3": result["sm_cygnss_m3m3"]})
        except Exception as _pe:
            log.warning("cygnss patch_row (non-fatal): %s", _pe)

    return result


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    import json
    print(json.dumps(run_cygnss_cycle("amalfitani"), indent=2, default=str))
