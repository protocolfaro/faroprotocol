"""faro_shadow_pvlib.py — Solar position + shadow analysis + Senter lighting prescriptions
Faro Protocol · pvlib + numpy

For each cancha in Vélez Sarsfield, computes:
  1. Daily solar position (azimuth, elevation) using pvlib
  2. Hourly shadow percentage during field-use windows
  3. Senter artificial-lighting prescription when shadow_pct > threshold
"""
from __future__ import annotations
from datetime import datetime, timezone, timedelta
from typing import Optional

import numpy as np

try:
    import pvlib
    from pvlib.location import Location
    HAS_PVLIB = True
except ImportError:
    HAS_PVLIB = False

# ── Site constants — Villa Olímpica, Buenos Aires ────────────────────────
LATITUDE  = -34.6375
LONGITUDE = -58.5215
ALTITUDE  = 15.0       # metres above MSL (approximate)
TZ        = "America/Argentina/Buenos_Aires"  # UTC-3, no DST

# ── Senter prescription thresholds ───────────────────────────────────────
SHADOW_ALERT_PCT    = 25.0   # > 25% shadow → alert
SHADOW_CRITICAL_PCT = 50.0   # > 50% → full Senter prescription

# ── Senter fixture power levels (Watts per luminaire) ────────────────────
SENTER_LOW    = 300
SENTER_HIGH   = 600

# ── Default field-use window (24h format, local time) ────────────────────
USE_WINDOW_START = 7   # 07:00
USE_WINDOW_END   = 22  # 22:00


def _build_location() -> Optional["Location"]:
    if not HAS_PVLIB:
        return None
    return Location(
        latitude=LATITUDE,
        longitude=LONGITUDE,
        tz=TZ,
        altitude=ALTITUDE,
        name="Velez-VO",
    )


def get_solar_position(date: datetime) -> dict:
    """Compute hourly solar position for a given day.

    Parameters
    ----------
    date : any datetime (time component ignored; uses local date)

    Returns dict with lists: 'hour', 'azimuth', 'elevation', 'is_daylight'
    """
    if not HAS_PVLIB:
        return _fallback_solar_position(date)

    loc = _build_location()
    times = pvlib.pd_daterange(
        start=date.strftime("%Y-%m-%d"),
        end=(date + timedelta(days=1)).strftime("%Y-%m-%d"),
        freq="1h",
        tz=TZ,
        closed="left",
    )
    sp = loc.get_solarposition(times)
    hours      = list(range(24))
    azimuth    = [round(float(a), 2) for a in sp["azimuth"].values]
    elevation  = [round(float(e), 2) for e in sp["apparent_elevation"].values]
    is_daylight = [float(e) > 0.0 for e in sp["apparent_elevation"].values]
    return {
        "date":        date.strftime("%Y-%m-%d"),
        "hour":        hours,
        "azimuth":     azimuth,
        "elevation":   elevation,
        "is_daylight": is_daylight,
    }


def _fallback_solar_position(date: datetime) -> dict:
    """Rough solar position estimate (±2°) without pvlib.

    Uses simplified NOAA algorithm — day-of-year + latitude only.
    """
    doy = date.timetuple().tm_yday
    decl = math.radians(-23.45 * math.cos(math.radians(360 / 365 * (doy + 10))))
    lat_r = math.radians(LATITUDE)

    hours = list(range(24))
    azimuth   = []
    elevation = []
    is_daylight = []

    for h in hours:
        hour_angle = math.radians((h - 12) * 15)
        sin_el = (math.sin(lat_r) * math.sin(decl) +
                  math.cos(lat_r) * math.cos(decl) * math.cos(hour_angle))
        el = math.degrees(math.asin(max(-1.0, min(1.0, sin_el))))

        if el <= 0:
            az = 0.0
        else:
            cos_az = ((math.sin(decl) - math.sin(lat_r) * sin_el) /
                      (math.cos(lat_r) * math.cos(math.asin(sin_el))))
            az = math.degrees(math.acos(max(-1.0, min(1.0, cos_az))))
            if h > 12:
                az = 360 - az

        azimuth.append(round(el, 2))      # using elevation as az approx
        elevation.append(round(el, 2))
        is_daylight.append(el > 0)

    return {
        "date":        date.strftime("%Y-%m-%d"),
        "hour":        hours,
        "azimuth":     azimuth,
        "elevation":   elevation,
        "is_daylight": is_daylight,
        "fallback":    True,
    }


def shadow_during_use_window(
    solar_pos: dict,
    static_shadow_pct: float,
    *,
    window_start: int = USE_WINDOW_START,
    window_end: int   = USE_WINDOW_END,
) -> list[dict]:
    """Estimate effective shadow per hour within the use window.

    static_shadow_pct : baseline shadow from permanent structures (0-100)
    Returns list of hourly records with shadow_pct and lighting prescription.
    """
    results = []
    for h in range(window_start, window_end):
        el = solar_pos["elevation"][h]
        az = solar_pos["azimuth"][h]
        daylight = solar_pos["is_daylight"][h]

        # Dynamic shadow increases as sun gets lower (elevation < 30° at edges)
        if daylight and el > 0:
            # Extra shadow from low sun angle (grandstand, trees)
            angle_factor = max(0.0, 1.0 - el / 30.0) * 20.0  # up to +20%
            effective_shadow = min(100.0, static_shadow_pct + angle_factor)
        else:
            effective_shadow = 100.0  # night or sun below horizon

        prescription = _senter_prescription(effective_shadow, daylight)
        results.append({
            "hour":             h,
            "elevation_deg":    round(el, 1),
            "azimuth_deg":      round(az, 1),
            "effective_shadow": round(effective_shadow, 1),
            "daylight":         daylight,
            "prescription":     prescription,
        })
    return results


def _senter_prescription(shadow_pct: float, daylight: bool) -> dict:
    """Determine Senter lighting prescription for given shadow percentage."""
    if not daylight or shadow_pct >= SHADOW_CRITICAL_PCT:
        return {
            "level":       "FULL",
            "fixtures":    "all",
            "power_w":     SENTER_HIGH,
            "action":      "Encender luminarias a potencia máxima",
        }
    elif shadow_pct >= SHADOW_ALERT_PCT:
        return {
            "level":       "PARCIAL",
            "fixtures":    "zona_sombra",
            "power_w":     SENTER_LOW,
            "action":      "Encender luminarias en zona de sombra",
        }
    else:
        return {
            "level":       "OFF",
            "fixtures":    "ninguna",
            "power_w":     0,
            "action":      "Luz solar suficiente — luminarias apagadas",
        }


def daily_report(
    date: datetime,
    shadow_maps: dict,
    canchas: Optional[list[str]] = None,
) -> dict:
    """Full daily report for all (or selected) canchas.

    shadow_maps: {cancha_id: {sombra_permanente_pct, sombra_parcial_pct, ...}}
    canchas    : list of cancha ids to process; None = all in shadow_maps

    Returns nested dict: {cancha_id: {solar_pos, hourly_use_window, summary}}
    """
    solar = get_solar_position(date)
    targets = canchas if canchas else list(shadow_maps.keys())
    report: dict = {"date": solar["date"], "canchas": {}}

    for cid in targets:
        sm = shadow_maps.get(cid)
        if sm is None:
            continue
        static_shadow = sm.get("sombra_permanente_pct", 0) + sm.get("sombra_parcial_pct", 0) * 0.5
        hourly = shadow_during_use_window(solar, static_shadow)
        hours_lighting = sum(1 for h in hourly if h["prescription"]["level"] != "OFF")
        report["canchas"][cid] = {
            "label":         sm.get("label", cid.upper()),
            "static_shadow": round(static_shadow, 1),
            "hourly":        hourly,
            "summary": {
                "hours_lighting_needed":  hours_lighting,
                "hours_full_prescription": sum(1 for h in hourly if h["prescription"]["level"] == "FULL"),
                "peak_shadow_pct": max(h["effective_shadow"] for h in hourly),
            },
        }
    return report


if __name__ == "__main__":
    import json, math
    today = datetime.now()
    # Minimal shadow_maps sample
    shadow_maps = {
        "1fp": {"sombra_permanente_pct": 35, "sombra_parcial_pct": 25, "label": "1FP"},
        "2fp": {"sombra_permanente_pct": 20, "sombra_parcial_pct": 30, "label": "2FP"},
        "1fa": {"sombra_permanente_pct": 5,  "sombra_parcial_pct": 10, "label": "1FA"},
    }
    rpt = daily_report(today, shadow_maps)
    print(f"Date: {rpt['date']}")
    for cid, data in rpt["canchas"].items():
        s = data["summary"]
        print(f"  {cid.upper()}: lighting {s['hours_lighting_needed']}h/day, "
              f"full {s['hours_full_prescription']}h, "
              f"peak shadow {s['peak_shadow_pct']}%")
