"""
dale_play_weather.py — Pronóstico climático de riesgo 72hs pre-show.
Fuente: Open-Meteo API (sin key, acceso libre).
"""
from __future__ import annotations
import logging
from datetime import date, timedelta
from typing import Optional

from dale_play_config import (
    VENUE_LAT, VENUE_LON,
    WIND_CAUTION_KMH, WIND_CRITICAL_KMH,
    RAIN_CAUTION_MM, TEMP_MIN_SAFE, TEMP_MAX_SAFE,
)

log = logging.getLogger(__name__)

_OM_URL = (
    f"https://api.open-meteo.com/v1/forecast"
    f"?latitude={VENUE_LAT}&longitude={VENUE_LON}"
    "&daily=precipitation_sum,windspeed_10m_max,windgusts_10m_max,"
    "temperature_2m_max,temperature_2m_min,weathercode,precipitation_probability_max"
    "&timezone=America%2FArgentina%2FBuenos_Aires"
    "&forecast_days=4"
    "&wind_speed_unit=kmh"
)


def _risk(val: float, caution: float, critical: float) -> str:
    if val >= critical:
        return "critico"
    if val >= caution:
        return "atencion"
    return "ok"


def fetch_72h_forecast(show_date: Optional[str] = None) -> dict:
    """
    Pronóstico 72hs centrado en la fecha del show.
    show_date: ISO YYYY-MM-DD; None → mañana.

    Returns {show_date, dias, show_day, alertas, riesgo_global, fuente}
    """
    import requests

    if show_date is None:
        show_date = (date.today() + timedelta(days=1)).isoformat()

    try:
        r = requests.get(_OM_URL, timeout=15)
        r.raise_for_status()
        d = r.json()
    except Exception as e:
        log.warning("dale_play_weather: Open-Meteo failed: %s", e)
        return {"error": str(e), "riesgo_global": "sin_datos", "show_date": show_date}

    daily = d.get("daily", {})
    times = daily.get("time", [])

    def _v(key: str, i: int, default=0):
        lst = daily.get(key) or []
        return lst[i] if i < len(lst) else default

    alertas: list[str] = []
    dias: list[dict]   = []

    for i, dt in enumerate(times[:4]):
        tmax   = _v("temperature_2m_max",           i)
        tmin   = _v("temperature_2m_min",           i)
        lluvia = _v("precipitation_sum",             i) or 0.0
        viento = _v("windspeed_10m_max",             i) or 0.0
        rachas = _v("windgusts_10m_max",             i) or 0.0
        pp_max = _v("precipitation_probability_max", i) or 0

        r_viento = _risk(rachas, WIND_CAUTION_KMH, WIND_CRITICAL_KMH)
        r_lluvia = _risk(lluvia, RAIN_CAUTION_MM,  RAIN_CAUTION_MM * 3)
        r_temp   = ("critico"
                    if (tmax is not None and tmax > TEMP_MAX_SAFE)
                    or (tmin is not None and tmin < TEMP_MIN_SAFE)
                    else "ok")

        riesgo_dia = ("critico"  if "critico"  in (r_viento, r_lluvia, r_temp) else
                      "atencion" if "atencion" in (r_viento, r_lluvia)          else "ok")

        dias.append({
            "fecha":           dt,
            "tmax":            tmax,
            "tmin":            tmin,
            "lluvia_mm":       round(float(lluvia), 1),
            "prob_lluvia_pct": int(pp_max),
            "viento_max_kmh":  round(float(viento), 1),
            "rachas_max_kmh":  round(float(rachas), 1),
            "riesgo_viento":   r_viento,
            "riesgo_lluvia":   r_lluvia,
            "riesgo_dia":      riesgo_dia,
        })

        if dt == show_date:
            if r_viento == "critico":
                alertas.append(
                    f"⚠ RACHAS CRÍTICAS {rachas:.0f} km/h — riesgo para estructuras elevadas y torres"
                )
            elif r_viento == "atencion":
                alertas.append(
                    f"Viento moderado {rachas:.0f} km/h — verificar anclaje de estructuras"
                )
            if r_lluvia != "ok":
                alertas.append(
                    f"Lluvia {lluvia:.0f} mm prevista — evaluar drenaje del campo y accesos"
                )
            if r_temp == "critico":
                extremo = f"{tmax:.0f}°C" if tmax > TEMP_MAX_SAFE else f"{tmin:.0f}°C"
                alertas.append(
                    f"Temperatura extrema ({extremo}) — protocolo hidratación y refrigeración"
                )

    niveles      = [d["riesgo_dia"] for d in dias]
    riesgo_global = ("critico"  if "critico"  in niveles else
                     "atencion" if "atencion" in niveles else "ok")

    show_day = next((d for d in dias if d["fecha"] == show_date), dias[0] if dias else {})

    return {
        "show_date":    show_date,
        "dias":         dias,
        "show_day":     show_day,
        "alertas":      alertas,
        "riesgo_global": riesgo_global,
        "fuente":       "Open-Meteo API · Pronóstico 72hs",
    }
