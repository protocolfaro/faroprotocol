"""
faro_clms_lst.py — CLMS Hourly Land Surface Temperature (versión 3, 3km resolución).
Copernicus Land Monitoring Service · disponible en CDSE desde junio 2026.

Producto: Global Hourly LST v3 (3km, geostationary fusion GOES+HIMAWARI+MSG)
Endpoint: CDSE OData / S3 EODATA
Latencia: 4 horas post-adquisición
Cobertura: Global, incluyendo Argentina (Liniers: -34.64°, -58.52°)

Uso en Faro Protocol:
  - Temperatura superficial horaria del pasto → Smith-Kerns Dollar Spot más preciso
  - Reemplaza proxy Open-Meteo soil_temperature_0cm con dato satelital real
  - Agrega temp_superficie_lst_c a climate_metrics (PATCH sobre fila más reciente)

Modo de acceso:
  1. CDSE OData REST API (no requiere credenciales para productos CLMS globales)
  2. Fallback: Open-Meteo soil_temperature_0cm (ya en data_refresh)

Llamado desde _daily_enrichment() en app.py — 09:15 UTC.
"""
from __future__ import annotations
import logging, os
from datetime import datetime, timezone, timedelta
from urllib.request import urlopen, Request as UReq
import json

log = logging.getLogger(__name__)

# Coordenadas Vélez (Liniers y Villa Olímpica — mismo tile a 3km)
LAT, LON = -34.64, -58.52

# CDSE OData endpoint para CLMS LST
_CDSE_LST_SEARCH = (
    "https://catalogue.dataspace.copernicus.eu/odata/v1/Products"
    "?$filter=Collection/Name eq 'CLMS_GLOBAL_LST_5KM_V2' "  # fallback a v2 si v3 no disponible
    "and OData.CSC.Intersects(area=geography'SRID=4326;POINT({lon} {lat})')"
    " and ContentDate/Start gt {start} and ContentDate/Start lt {end}"
    "&$orderby=ContentDate/Start desc&$top=1&$expand=Attributes"
)

# CDSE endpoint para v3 (3km) — disponible desde jun 2026
_CDSE_LST_V3_SEARCH = (
    "https://catalogue.dataspace.copernicus.eu/odata/v1/Products"
    "?$filter=Collection/Name eq 'CLMS_GLOBAL_LST_3KM_V3' "
    "and OData.CSC.Intersects(area=geography'SRID=4326;POINT({lon} {lat})')"
    " and ContentDate/Start gt {start} and ContentDate/Start lt {end}"
    "&$orderby=ContentDate/Start desc&$top=1&$expand=Attributes"
)


def _fetch_json(url: str, timeout: int = 12) -> dict:
    try:
        req = UReq(url, headers={"User-Agent": "FaroProtocol/5.0 (protocolfaro@gmail.com)"})
        with urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception as exc:
        log.debug("clms_lst fetch: %s — %s", url[:80], exc)
        return {}


def _get_latest_lst_from_cdse(hours_back: int = 6) -> dict | None:
    """
    Busca el último producto LST disponible en CDSE para las coordenadas de Vélez.
    Prueba v3 (3km) primero, fallback a v2 (5km).
    Retorna dict con temp_k y metadata, o None si no hay datos.
    """
    now = datetime.now(timezone.utc)
    start = (now - timedelta(hours=hours_back)).strftime("%Y-%m-%dT%H:%M:%SZ")
    end   = now.strftime("%Y-%m-%dT%H:%M:%SZ")

    for version, url_tmpl in [("v3", _CDSE_LST_V3_SEARCH), ("v2", _CDSE_LST_SEARCH)]:
        url = url_tmpl.format(lat=LAT, lon=LON, start=start, end=end)
        raw = _fetch_json(url)
        items = raw.get("value", [])
        if items:
            item = items[0]
            # Extraer temperatura de los atributos
            attrs = {a["Name"]: a.get("Value") for a in item.get("Attributes", [])}
            temp_k = attrs.get("LST") or attrs.get("land_surface_temperature")
            fecha  = (item.get("ContentDate") or {}).get("Start", "")[:16]
            if temp_k is not None:
                temp_c = round(float(temp_k) - 273.15, 1)
                log.info("clms_lst: %s OK — LST=%.1f°C (%.1f K) [%s]",
                         version, temp_c, float(temp_k), fecha)
                return {
                    "temp_superficie_lst_c": temp_c,
                    "temp_superficie_lst_k": float(temp_k),
                    "fuente": f"CLMS LST {version.upper()} · CDSE · {fecha}",
                    "fecha": fecha,
                    "version": version,
                }
            log.debug("clms_lst: %s item encontrado pero sin atributo LST — %s", version, attrs)
        log.debug("clms_lst: %s sin items en las últimas %dh", version, hours_back)

    return None


def run_clms_lst_cycle(venue_id: str = "amalfitani") -> dict:
    """
    Ciclo de enriquecimiento CLMS LST.
    1. Busca último LST disponible en CDSE (últimas 6h)
    2. Si encuentra → PATCH en climate_metrics (fila más reciente)
    3. También devuelve el valor para uso en pipeline
    """
    result: dict = {
        "venue_id":   venue_id,
        "ok":         False,
        "fuente":     None,
        "temp_c":     None,
    }

    lst_data = _get_latest_lst_from_cdse(hours_back=6)

    if lst_data is None:
        # Intentar con ventana más amplia (últimas 24h — puede haber cobertura de noche)
        lst_data = _get_latest_lst_from_cdse(hours_back=24)

    if lst_data is None:
        log.info("clms_lst: sin datos disponibles en CDSE — usando fallback Open-Meteo")
        result["fuente"] = "no_data"
        return result

    temp_c = lst_data["temp_superficie_lst_c"]
    result["temp_c"]  = temp_c
    result["fuente"]  = lst_data["fuente"]
    result["fecha"]   = lst_data.get("fecha")

    # PATCH en climate_metrics — agregar temp_superficie_lst_c a fila más reciente
    try:
        import sys as _sys, os as _os
        _here = _os.path.dirname(_os.path.abspath(__file__))
        if _here not in _sys.path:
            _sys.path.insert(0, _here)
        import velez_supabase as _vs
        clim_row = _vs.get_latest_climate_row(venue_id)
        if clim_row and clim_row.get("id"):
            ok = _vs.patch_row("climate_metrics", clim_row["id"], {
                "temp_superficie_lst_c": temp_c,
                "fuente": lst_data["fuente"],  # actualiza fuente para trazabilidad
            })
            if ok:
                log.info("clms_lst: PATCH climate_metrics id=%d temp=%.1f°C OK",
                         clim_row["id"], temp_c)
                result["ok"] = True
            else:
                log.warning("clms_lst: PATCH climate_metrics falló")
        else:
            log.warning("clms_lst: sin fila climate_metrics reciente para PATCH")
    except Exception as exc:
        log.warning("clms_lst: Supabase patch (non-fatal): %s", exc)

    # También actualizar soil_metrics más reciente con temp_superficie_c (de LST)
    try:
        import velez_supabase as _vs2
        soil_row = _vs2.get_latest_soil_row(venue_id)
        if soil_row and soil_row.get("id"):
            _vs2.patch_row("soil_metrics", soil_row["id"], {
                "temp_superficie_c": temp_c,  # campo ya definido en schema
            })
            log.info("clms_lst: PATCH soil_metrics temp_superficie_c=%.1f°C OK", temp_c)
    except Exception as exc:
        log.debug("clms_lst: soil PATCH (non-fatal): %s", exc)

    return result
