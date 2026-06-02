"""
dale_play_smap.py — Humedad del suelo para el Amalfitani.

Flujo primario (Railway):
  NASA SMAP SPL4SMGP v7 vía Earthdata:
  1. Token Bearer desde urs.earthdata.nasa.gov
  2. CMR API → granule HDF5 más reciente
  3. Descarga + parse h5py → sm_surface, sm_rootzone

Fallback (local / sin credenciales):
  Open-Meteo ERA5-Land — soil moisture por capas (libre, sin auth)

Output: models/smap_amalfitani.json
  humedad_superficial_%   0-7cm (% volumétrico × 100)
  humedad_raiz_%          7-100cm
  fecha                   ISO date del dato
  estado                  seco / normal / saturado
  fuente                  SMAP | ERA5
"""
from __future__ import annotations
import json, logging, math, os, pathlib
from datetime import datetime, timedelta, timezone

import requests

log = logging.getLogger(__name__)

LAT = -34.6379
LON = -58.5288

SMAP_COLLECTION = "C2531967462-NSIDC_ECS"   # SPL4SMGP v007 @ NSIDC
CMR_URL         = "https://cmr.earthdata.nasa.gov/search/granules.json"
URS_TOKEN_URL   = "https://urs.earthdata.nasa.gov/api/users/token"
NSIDC_BASE      = "https://n5eil01u.ecs.nsidc.org"


# ── Credenciales ──────────────────────────────────────────────────────────────

def _get_credentials() -> tuple[str, str]:
    """
    Lee NASA_EARTHDATA (formato user:pass) o
    NASA_EARTHDATA_USER + NASA_EARTHDATA_PASS de las env vars.
    """
    combined = os.environ.get("NASA_EARTHDATA", "")
    if ":" in combined:
        user, pwd = combined.split(":", 1)
        return user.strip(), pwd.strip()
    user = os.environ.get("NASA_EARTHDATA_USER", "")
    pwd  = (os.environ.get("NASA_EARTHDATA_PASS")
            or os.environ.get("NASA_EARTHDATA_PASSWORD", ""))
    return user, pwd


def _get_bearer_token(user: str, pwd: str) -> str | None:
    """Obtiene Bearer token de NASA URS."""
    try:
        r = requests.post(
            URS_TOKEN_URL,
            auth=(user, pwd),
            json={},
            timeout=15,
        )
        if r.status_code in (200, 201):
            tokens = r.json()
            if isinstance(tokens, list) and tokens:
                return tokens[0].get("access_token")
            if isinstance(tokens, dict):
                return tokens.get("access_token")
        # Token may already exist — GET instead of POST
        r2 = requests.get(URS_TOKEN_URL, auth=(user, pwd), timeout=15)
        if r2.status_code == 200:
            tokens = r2.json()
            if isinstance(tokens, list) and tokens:
                return tokens[0].get("access_token")
    except Exception as e:
        log.warning("URS token error: %s", e)
    return None


# ── Ruta primaria: NASA SMAP ──────────────────────────────────────────────────

def _fetch_smap_primary(user: str, pwd: str) -> dict | None:
    """
    Descarga el último granulo SMAP SPL4SMGP, extrae sm_surface y sm_rootzone
    en el punto (LAT, LON) usando h5py.
    Retorna dict con humedad_superficial_% y humedad_raiz_% o None si falla.
    """
    try:
        import h5py, numpy as np
    except ImportError:
        log.warning("h5py no disponible — usando fallback ERA5")
        return None

    token = _get_bearer_token(user, pwd)
    if not token:
        log.warning("No se pudo obtener Bearer token de NASA URS")
        return None

    # ── CMR: buscar granulo más reciente ──────────────────────────────────────
    now   = datetime.now(timezone.utc)
    start = (now - timedelta(days=5)).strftime("%Y-%m-%dT%H:%M:%SZ")
    end   = now.strftime("%Y-%m-%dT%H:%M:%SZ")

    cmr_r = requests.get(CMR_URL, params={
        "concept_id":     SMAP_COLLECTION,
        "temporal":       f"{start},{end}",
        "sort_key":       "-start_date",
        "page_size":      1,
        "bounding_box":   f"{LON-1},{LAT-1},{LON+1},{LAT+1}",
    }, timeout=20)
    cmr_r.raise_for_status()
    entries = cmr_r.json().get("feed", {}).get("entry", [])
    if not entries:
        log.warning("SMAP CMR: sin granulos recientes")
        return None

    granule = entries[0]
    granule_date = granule.get("time_start", "")[:10]

    # URL de descarga
    links = granule.get("links", [])
    dl_link = next(
        (l["href"] for l in links if l.get("rel","").endswith("/data#") and l["href"].endswith(".h5")),
        None,
    )
    if not dl_link:
        log.warning("SMAP: no se encontró link de descarga en granulo")
        return None

    # ── Descarga HDF5 con Bearer token ───────────────────────────────────────
    log.info("Descargando granulo SMAP: %s", dl_link)
    with requests.get(
        dl_link,
        headers={"Authorization": f"Bearer {token}"},
        stream=True, allow_redirects=True, timeout=120,
    ) as resp:
        resp.raise_for_status()
        tmp_path = pathlib.Path("/tmp/smap_granule.h5")
        with open(tmp_path, "wb") as fh:
            for chunk in resp.iter_content(chunk_size=1 << 20):
                fh.write(chunk)

    # ── Extracción lat/lon más cercano ───────────────────────────────────────
    with h5py.File(tmp_path, "r") as f:
        cell_lat = f["cell_lat"][:]    # shape (rows, cols)
        cell_lon = f["cell_lon"][:]
        sm_surf  = f["Geophysical_Data/sm_surface"][:]
        sm_root  = f["Geophysical_Data/sm_rootzone"][:]

    # Calcular distancia mínima al punto
    dist = np.sqrt((cell_lat - LAT)**2 + (cell_lon - LON)**2)
    idx  = np.unravel_index(np.argmin(dist), dist.shape)

    sm_s = float(sm_surf[idx])
    sm_r = float(sm_root[idx])

    # SMAP fill value = -9999 o -9999.0
    if sm_s < -100 or sm_r < -100:
        log.warning("SMAP: fill value en el punto — sin dato")
        return None

    # m³/m³ → porcentaje volumétrico
    surf_pct = round(sm_s * 100, 1)
    root_pct = round(sm_r * 100, 1)
    estado   = _estado(surf_pct)

    log.info("SMAP OK — surf=%.1f%% root=%.1f%% estado=%s", surf_pct, root_pct, estado)
    return {
        "humedad_superficial_%":  surf_pct,
        "humedad_raiz_%":         root_pct,
        "fecha":                  granule_date,
        "estado":                 estado,
        "fuente":                 "NASA SMAP SPL4SMGP v007",
        "lat":                    LAT,
        "lon":                    LON,
    }


# ── Fallback: Open-Meteo ERA5-Land ───────────────────────────────────────────

def _fetch_era5_fallback() -> dict:
    """
    Obtiene humedad del suelo desde Open-Meteo ERA5-Land archive.
    Variables equivalentes a SMAP:
      soil_moisture_0_to_7cm   → superficie (0-7 cm)
      soil_moisture_7_to_28cm  → raíz superficial
      soil_moisture_28_to_100cm → raíz profunda
    Combina raíz superficial + profunda como proxy de root zone.
    """
    today  = datetime.now(timezone.utc)
    # ERA5 archive tiene ~2-5 días de latencia
    end_dt = today - timedelta(days=3)
    sta_dt = end_dt - timedelta(days=5)

    try:
        r = requests.get(
            "https://archive-api.open-meteo.com/v1/era5",
            params={
                "latitude":  LAT,
                "longitude": LON,
                "start_date": sta_dt.strftime("%Y-%m-%d"),
                "end_date":   end_dt.strftime("%Y-%m-%d"),
                "hourly": ",".join([
                    "soil_moisture_0_to_7cm",
                    "soil_moisture_7_to_28cm",
                    "soil_moisture_28_to_100cm",
                ]),
                "timezone": "UTC",
            },
            timeout=20,
        )
        r.raise_for_status()
        data = r.json()

        hourly = data.get("hourly", {})
        times   = hourly.get("time", [])
        sm0_7   = hourly.get("soil_moisture_0_to_7cm", [])
        sm7_28  = hourly.get("soil_moisture_7_to_28cm", [])
        sm28_100= hourly.get("soil_moisture_28_to_100cm", [])

        # Tomar el último valor no-None
        valid = [(t, a, b, c) for t, a, b, c
                 in zip(times, sm0_7, sm7_28, sm28_100)
                 if a is not None and b is not None and c is not None]

        if not valid:
            raise ValueError("ERA5: sin datos válidos")

        last_time, v0_7, v7_28, v28_100 = valid[-1]
        fecha = last_time[:10]

        # m³/m³ → porcentaje volumétrico
        surf_pct = round(v0_7   * 100, 1)
        # Root zone: promedio ponderado 7-28cm (21cm) y 28-100cm (72cm)
        root_pct = round(((v7_28 * 21 + v28_100 * 72) / 93) * 100, 1)
        estado   = _estado(surf_pct)

        log.info("ERA5 OK — surf=%.1f%% root=%.1f%% fecha=%s", surf_pct, root_pct, fecha)
        return {
            "humedad_superficial_%":  surf_pct,
            "humedad_raiz_%":         root_pct,
            "fecha":                  fecha,
            "estado":                 estado,
            "fuente":                 "ERA5-Land (Open-Meteo) — proxy SMAP",
            "lat":                    LAT,
            "lon":                    LON,
        }

    except Exception as e:
        log.warning("ERA5 fallback error: %s", e)
        # Último recurso: Open-Meteo forecast con past_days
        return _fetch_openmeteo_forecast()


def _fetch_openmeteo_forecast() -> dict:
    """Último recurso: modelo forecast de Open-Meteo con past_days=3."""
    try:
        r = requests.get(
            "https://api.open-meteo.com/v1/forecast",
            params={
                "latitude":  LAT,
                "longitude": LON,
                "hourly": ",".join([
                    "soil_moisture_0_to_1cm",
                    "soil_moisture_3_to_9cm",
                    "soil_moisture_27_to_81cm",
                ]),
                "past_days":      3,
                "forecast_days":  0,
                "timezone":       "UTC",
            },
            timeout=20,
        )
        r.raise_for_status()
        data   = r.json()
        hourly = data.get("hourly", {})
        times  = hourly.get("time", [])
        sm_s   = hourly.get("soil_moisture_0_to_1cm", [])
        sm_r3  = hourly.get("soil_moisture_3_to_9cm", [])
        sm_r81 = hourly.get("soil_moisture_27_to_81cm", [])

        valid = [(t, a, b, c) for t, a, b, c
                 in zip(times, sm_s, sm_r3, sm_r81)
                 if a is not None and b is not None and c is not None]

        if not valid:
            raise ValueError("Forecast: sin datos")

        last_time, vs, vr3, vr81 = valid[-1]
        surf_pct = round(vs   * 100, 1)
        root_pct = round(((vr3 * 6 + vr81 * 54) / 60) * 100, 1)
        estado   = _estado(surf_pct)

        return {
            "humedad_superficial_%":  surf_pct,
            "humedad_raiz_%":         root_pct,
            "fecha":                  last_time[:10],
            "estado":                 estado,
            "fuente":                 "ECMWF IFS (Open-Meteo) — proxy SMAP",
            "lat":                    LAT,
            "lon":                    LON,
        }
    except Exception as e:
        log.error("Forecast fallback error: %s", e)
        return {
            "humedad_superficial_%":  None,
            "humedad_raiz_%":         None,
            "fecha":                  None,
            "estado":                 "sin_datos",
            "fuente":                 "sin_datos",
            "error":                  str(e),
        }


# ── Clasificador ──────────────────────────────────────────────────────────────

def _estado(pct: float | None) -> str:
    if pct is None: return "sin_datos"
    if pct < 15:    return "seco"
    if pct < 32:    return "normal"
    return "saturado"


# ── Orquestador público ───────────────────────────────────────────────────────

def fetch_smap_soil_moisture() -> dict:
    """
    Intenta SMAP primero (si hay credenciales y h5py).
    Cae a ERA5-Land como proxy.
    """
    user, pwd = _get_credentials()
    result = None

    if user and pwd:
        try:
            result = _fetch_smap_primary(user, pwd)
        except Exception as e:
            log.warning("SMAP primary failed: %s", e)

    if result is None:
        result = _fetch_era5_fallback()

    # Guardar JSON
    out = pathlib.Path(__file__).parent / "models" / "smap_amalfitani.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    log.info("SMAP/ERA5 saved → %s", out)
    return result
