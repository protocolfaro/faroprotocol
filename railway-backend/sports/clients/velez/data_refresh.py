"""
data_refresh.py — Daily live weather refresh for Vélez panel.
Fetches NASA POWER, SoilGrids, Open-Meteo, ECOSTRESS/SMAP metadata.
Updates weather_live section + roger.acciones (physics prescriptions) in velez/velez_data.json.
All other sections (heatmaps, IPOS, sector scores) are left untouched.
"""
from __future__ import annotations
import asyncio, base64, gc, json, logging, math, os
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
from urllib.request import urlopen, Request
from urllib.parse import urlencode
from urllib.error import URLError

import requests as _req

import ndvi_real

log = logging.getLogger(__name__)

LAT, LON, ELEV_M = -34.6375, -58.5215, 25
API_TIMEOUT = 12

# Fallback estimates used when Sentinel-2 fetch fails; overridden by ndvi_real on success
_CANCHA_NDVI = {
    "1fa": 0.48, "2fa": 0.52, "3fa": 0.38, "4fa": 0.31,
    "5fa": 0.40, "6fa": 0.35, "7fa": 0.38, "8fa": 0.36, "9fa": 0.33, "10fa": 0.45,
    "1fp": 0.55, "2fp": 0.52,
    "amalfitani": 0.60, "poli_f11": 0.45, "poli_f8a": 0.42,
    "poli_f8b": 0.42, "poli_hockey": 0.38,
}
_GNDVI_K = {
    "1fa": 0.91, "2fa": 0.93, "3fa": 0.88, "4fa": 0.84,
    "5fa": 0.89, "6fa": 0.87, "7fa": 0.88, "8fa": 0.87, "9fa": 0.86, "10fa": 0.90,
    "1fp": 0.92, "2fp": 0.91,
    "amalfitani": 0.92, "poli_f11": 0.90, "poli_f8a": 0.89,
    "poli_f8b": 0.89, "poli_hockey": 0.88,
}
# All cancha IDs — used for dynamic fungal risk lists
_ALL_CANCHA_IDS = list(_CANCHA_NDVI.keys())


# ── Fetch helpers ─────────────────────────────────────────────────────────────

def _fetch_json(url: str, timeout: int = API_TIMEOUT) -> dict:
    try:
        req = Request(url, headers={"User-Agent": "FaroProtocol/4.0 (protocolfaro@gmail.com)"})
        with urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception as exc:
        log.warning("fetch %s: %s", url[:70], exc)
        return {}


def _fetch_nasa_power() -> dict:
    end   = date.today() - timedelta(days=1)
    start = end - timedelta(days=6)
    url = (
        "https://power.larc.nasa.gov/api/temporal/daily/point?"
        + urlencode({
            "start": start.strftime("%Y%m%d"),
            "end":   end.strftime("%Y%m%d"),
            "latitude": LAT, "longitude": LON,
            "community": "AG",
            "parameters": "EVPTRNS,T2M_MAX,T2M_MIN,PRECTOTCORR,RH2M,WS2M,ALLSKY_SFC_SW_DWN",
            "format": "JSON",
        })
    )
    raw = _fetch_json(url)
    params = raw.get("properties", {}).get("parameter", {})
    result, daily = {}, {}
    for key, vals in params.items():
        ordered = [v for _, v in sorted(vals.items()) if v is not None and v > -900]
        daily[key] = ordered
        if ordered:
            result[key] = round(sum(ordered) / len(ordered), 3)
    tmax_d = daily.get("T2M_MAX", [])
    tmin_d = daily.get("T2M_MIN", [])
    gdd    = [max(0.0, (mx + mn) / 2 - 10) for mx, mn in zip(tmax_d, tmin_d)]
    result["_gdd_7d"]   = round(sum(gdd), 1)
    result["_gdd_rate"] = round(sum(gdd) / len(gdd), 1) if gdd else 5.0
    # 5-day averages for Smith-Kerns Dollar Spot model (MSU validated on 5-day window)
    rh2m_d = daily.get("RH2M", [])
    tmax_5 = tmax_d[-5:] if len(tmax_d) >= 5 else tmax_d
    tmin_5 = tmin_d[-5:] if len(tmin_d) >= 5 else tmin_d
    rh2m_5 = rh2m_d[-5:] if len(rh2m_d) >= 5 else rh2m_d
    if tmax_5 and tmin_5:
        tmean_5 = [(mx + mn) / 2.0 for mx, mn in zip(tmax_5, tmin_5)]
        result["_t2m_avg_5d"] = round(sum(tmean_5) / len(tmean_5), 2)
    if rh2m_5:
        result["_rh2m_avg_5d"] = round(sum(rh2m_5) / len(rh2m_5), 1)
    return result


def _calculate_solar_metrics(nasa: dict) -> list[dict]:
    """
    Deriva métricas por zona del techo solar (Techo Sur Amalfitani) desde NASA POWER GHI.
    Genera 4 registros zonales para velez_solar — datos reales derivados de GHI medido.
    Modelo: eficiencia = GHI_real / GHI_cielo_despejado_estacional · NOCT bifacial para temperatura.
    """
    import math
    ghi_kwh = nasa.get("ALLSKY_SFC_SW_DWN")   # kWh/m²/day (NASA POWER daily, AG community)
    t2m_max = float(nasa.get("T2M_MAX") or 20.0)
    if ghi_kwh is None or float(ghi_kwh) < 0:
        return []
    ghi_kwh = float(ghi_kwh)

    # Clear-sky GHI estacional para Buenos Aires (-34.6°, sinusoidal)
    # Max ≈ 7.5 kWh en diciembre (doy 355), min ≈ 3.0 kWh en junio (doy 172)
    doy = date.today().timetuple().tm_yday
    ghi_clearsky = 5.25 - 2.25 * math.cos(2 * math.pi * (doy - 355) / 365)
    ghi_clearsky = max(2.5, min(7.8, ghi_clearsky))

    eficiencia_base = round(min(100.0, max(0.0, ghi_kwh / ghi_clearsky * 100.0)), 1)

    # Temperatura panel: NOCT bifacial = 43°C → T_panel = T_amb + (NOCT-20)/800 × GHI_pico
    # GHI_pico_Wm2 ≈ GHI_diario_kWh × 1000 / peak_sun_hours ≈ GHI_kWh × 1000 / ghi_clearsky
    ghi_peak_wm2 = ghi_kwh * 1000.0 / max(ghi_clearsky, 1.0)
    t_panel_base = round(t2m_max + (43.0 - 20.0) / 800.0 * ghi_peak_wm2, 1)

    def _estado(eff: float) -> str:
        return "ok" if eff >= 80.0 else ("degradado" if eff >= 60.0 else "falla")

    # Techo sur Amalfitani — 4 zonas con variación física por ángulo de incidencia
    zonas = [
        ("zona_norte",  -7.0, -1.5),   # orientación más desfavorable
        ("zona_sur",     0.0,  0.0),   # referencia (sur = óptimo para -34.6°)
        ("zona_este",   -4.0, +0.5),   # producción sesgada a mañana
        ("zona_oeste",  -4.0, +0.5),   # producción sesgada a tarde
    ]
    panels = []
    for panel_id, delta_eff, delta_t in zonas:
        eff  = round(max(0.0, min(100.0, eficiencia_base + delta_eff)), 1)
        temp = round(t_panel_base + delta_t, 1)
        panels.append({
            "panel_id":       panel_id,
            "eficiencia_pct": eff,
            "temperatura_c":  temp,
            "estado":         _estado(eff),
        })
    return panels


def _fetch_soilgrids() -> dict:
    url = (
        "https://rest.isric.org/soilgrids/v2.0/properties/query"
        f"?lon={LON}&lat={LAT}"
        "&property=clay&property=sand&property=silt&property=bdod"
        "&property=wv0033&property=wv1500&depth=0-5cm&value=mean"
    )
    raw = _fetch_json(url, timeout=14)
    result = {}
    for layer in raw.get("properties", {}).get("layers", []):
        name     = layer["name"]
        d_factor = (layer.get("unit_measure") or {}).get("d_factor") or 1
        for depth_obj in layer.get("depths", []):
            mv = depth_obj.get("values", {}).get("mean")
            if mv is not None:
                result[name] = mv / d_factor
    fc = result.get("wv0033", 0); wp = result.get("wv1500", 0)
    fc_f = fc / 100 if fc > 1 else fc
    wp_f = wp / 100 if wp > 1 else wp
    result["whc_mm"] = round(max(0, fc_f - wp_f) * 300)
    return result


# SoilGrids is a static dataset — soil texture never changes.
# Cache the result for the lifetime of the Railway process (avoids 14s call every day).
_soil_cache: dict = {}

def _fetch_soilgrids_cached() -> dict:
    global _soil_cache
    if _soil_cache:
        log.info("SoilGrids: using cached data (static dataset)")
        return _soil_cache
    result = _fetch_soilgrids()
    if result:
        _soil_cache = result
        log.info("SoilGrids: data cached for process lifetime")
    return result


def _fetch_open_meteo() -> dict:
    url = (
        "https://api.open-meteo.com/v1/forecast"
        f"?latitude={LAT}&longitude={LON}"
        "&hourly=precipitation_probability,precipitation,wind_speed_10m,et0_fao_evapotranspiration"
        ",soil_moisture_0_to_1cm,soil_moisture_1_to_3cm,soil_moisture_3_to_9cm"
        ",soil_moisture_0_to_7cm,soil_moisture_7_to_28cm,soil_moisture_28_to_100cm,soil_moisture_100_to_255cm"
        ",soil_temperature_0cm,soil_temperature_6cm,soil_temperature_18cm"
        ",temperature_2m,relative_humidity_2m"
        "&timezone=America%2FArgentina%2FBuenos_Aires&past_days=2&forecast_days=7"
    )
    result = _fetch_json(url, timeout=20)
    if not result.get("hourly"):
        log.warning("open-meteo retry (no hourly in first attempt)")
        result = _fetch_json(url, timeout=25)
    return result


def _fetch_ecostress() -> dict:
    end_dt   = date.today().isoformat() + "T23:59:59Z"
    start_dt = (date.today() - timedelta(days=7)).isoformat() + "T00:00:00Z"
    bbox     = f"{LON-0.06},{LAT-0.06},{LON+0.06},{LAT+0.06}"
    url = (
        "https://cmr.earthdata.nasa.gov/search/granules.json"
        f"?short_name=ECO2LSTE&bounding_box={bbox}"
        f"&temporal[]={start_dt},{end_dt}&page_size=1&sort_key=-start_date"
    )
    raw     = _fetch_json(url, timeout=8)
    entries = raw.get("feed", {}).get("entry", [])
    if entries:
        fecha = entries[0].get("time_start", "")[:10]
        return {"disponible": True, "fecha": fecha,
                "fuente": "NASA ECOSTRESS ECO2LSTE 70m",
                "mensaje": f"Imagen ECOSTRESS disponible: {fecha}"}
    return {"disponible": False, "fuente": "Landsat TIRS 100m (fallback)",
            "mensaje": "Sin imagen ECOSTRESS últimos 7 días — Landsat activo como fallback"}


def _fetch_smap() -> dict:
    end_dt   = date.today().isoformat() + "T23:59:59Z"
    start_dt = (date.today() - timedelta(days=4)).isoformat() + "T00:00:00Z"
    bbox     = f"{LON-0.5},{LAT-0.5},{LON+0.5},{LAT+0.5}"
    for short in ("SPL3SMP_E", "SPL3SMP"):
        url = (
            "https://cmr.earthdata.nasa.gov/search/granules.json"
            f"?short_name={short}&bounding_box={bbox}"
            f"&temporal[]={start_dt},{end_dt}&page_size=1&sort_key=-start_date"
        )
        raw     = _fetch_json(url, timeout=8)
        entries = raw.get("feed", {}).get("entry", [])
        if entries:
            fecha = entries[0].get("time_start", "")[:10]
            return {"disponible": True, "producto": short, "fecha": fecha,
                    "mensaje": f"SMAP {short} disponible: {fecha}",
                    "nota": "Valor exacto requiere Earthdata auth — Open-Meteo como proxy"}
    return {"disponible": False, "mensaje": "Sin datos SMAP recientes"}


# ── Compute ───────────────────────────────────────────────────────────────────

def _penman_monteith(tmax, tmin, rh, ws2m, allsky_kwh, doy):
    T  = (tmax + tmin) / 2
    es = (0.6108*math.exp(17.27*tmax/(tmax+237.3)) + 0.6108*math.exp(17.27*tmin/(tmin+237.3))) / 2
    ea = es * max(0, rh) / 100
    delta = 4098*0.6108*math.exp(17.27*T/(T+237.3))/(T+237.3)**2
    P     = 101.3*((293-0.0065*ELEV_M)/293)**5.26
    gamma = 0.000665*P
    Rs    = allsky_kwh * 3.6   # kWh/m² → MJ/m² (NASA POWER ALLSKY_SFC_SW_DWN is kWh/m²)
    lat_r = math.radians(LAT)
    dr    = 1 + 0.033*math.cos(2*math.pi/365*doy)
    decl  = 0.409*math.sin(2*math.pi/365*doy - 1.39)
    ws_a  = math.acos(max(-1.0, min(1.0, -math.tan(lat_r)*math.tan(decl))))
    Ra    = (24*60/math.pi*0.0820*dr*(ws_a*math.sin(lat_r)*math.sin(decl)
             + math.cos(lat_r)*math.cos(decl)*math.sin(ws_a)))
    Rso   = (0.75 + 2e-5*ELEV_M)*Ra
    Rns   = (1 - 0.23)*Rs
    Rnl   = (4.903e-9*((tmax+273.16)**4+(tmin+273.16)**4)/2
             * (0.34-0.14*math.sqrt(max(0,ea)))
             * (1.35*min(1.5, Rs/max(0.01,Rso))-0.35))
    Rn    = Rns - Rnl
    num   = 0.408*delta*Rn + gamma*(900/(T+273))*ws2m*(es-ea)
    den   = delta + gamma*(1+0.34*ws2m)
    return max(0.0, round(num/den, 2))


def _gndvi_per_cancha():
    """Lee GNDVI/NDVI per-cancha desde Supabase (velez_canchas). Sin datos reales → vacío."""
    def _n(g):
        if g < 0.30: return "grave",      "Fertilizar URGENTE — deficiencia N grave"
        if g < 0.38: return "bajo",       "Fertilizar esta semana — déficit N moderado"
        if g < 0.45: return "borderline", "Fertilización preventiva recomendable"
        return          "ok",             "Nitrógeno adecuado"
    canchas: dict = {}
    try:
        import sys as _sys_gn, pathlib as _pl_gn
        _here_gn = str(_pl_gn.Path(__file__).resolve().parent)
        if _here_gn not in _sys_gn.path:
            _sys_gn.path.insert(0, _here_gn)
        import velez_supabase as _vs_gn
        cancha_map = _vs_gn.get_canchas()  # {cancha_id: row} from velez_canchas
        fecha = None
        for cid, row in cancha_map.items():
            gndvi = row.get("gndvi")
            ndvi  = row.get("ndvi")
            if fecha is None:
                fecha = row.get("fecha_imagen")
            if gndvi is not None:
                nst, nrec = _n(float(gndvi))
                canchas[cid] = {"gndvi": round(float(gndvi), 3),
                                 "ndvi":  round(float(ndvi), 3) if ndvi is not None else None,
                                 "n_status": nst, "n_rec": nrec}
        if canchas:
            return {"fuente": "sentinel2-real", "fecha_imagen": fecha, "canchas": canchas}
    except Exception as _e:
        log.warning("_gndvi_per_cancha supabase: %s", _e)
    # Sin datos reales: estructura vacía — el assembler completará desde velez_canchas
    return {"fuente": "sin-datos", "canchas": {}}


def _fungal_risk(h: dict, shadow_pct_by_cancha: dict = None) -> dict:
    """Fungal risk model. shadow_pct_by_cancha: {cid: sombra_permanente_pct}."""
    temps = h.get("temperature_2m", [])
    rh    = h.get("relative_humidity_2m", [])
    shadow_pct_by_cancha = shadow_pct_by_cancha or {}
    # Canchas with >20% permanent shadow accumulate moisture — elevated baseline risk
    shadowed = sorted(c for c, p in shadow_pct_by_cancha.items() if p > 20)

    if not temps or not rh:
        return {"nivel":"bajo","horas_favorables_48h":0,"horas_brown_patch_48h":0,
                "descripcion":"Sin datos T/RH","accion_recomendada":"",
                "canchas_en_riesgo":shadowed,"enfermedad":None}
    h_dollar = sum(1 for t,r in zip(temps[:48],rh[:48]) if t and r and 10<t<32 and r>80)
    h_brown  = sum(1 for t,r in zip(temps[:48],rh[:48]) if t and r and t>20 and r>85)

    if h_brown >= 10 or h_dollar >= 16:
        base_canchas = _ALL_CANCHA_IDS
        canchas_riesgo = list(dict.fromkeys(base_canchas + shadowed))
        return {"nivel":"alto","horas_favorables_48h":h_dollar,"horas_brown_patch_48h":h_brown,
                "descripcion":f"ALTO: {h_dollar}h Dollar Spot · {h_brown}h Brown Patch (48h)",
                "accion_recomendada":"Aplicar fungicida preventivo HOY",
                "canchas_en_riesgo":canchas_riesgo,"enfermedad":"Dollar Spot + Brown Patch"}
    if h_dollar >= 6 or h_brown >= 4:
        base_canchas = [c for c in _ALL_CANCHA_IDS if c in ("1fa","2fa","3fa","amalfitani","poli_f11")]
        canchas_riesgo = list(dict.fromkeys(base_canchas + shadowed))
        return {"nivel":"medio","horas_favorables_48h":h_dollar,"horas_brown_patch_48h":h_brown,
                "descripcion":f"MEDIO: {h_dollar}h favorables Dollar Spot en últimas 48h",
                "accion_recomendada":"Monitorear canchas afectadas — aplicar fungicida si aparecen manchas",
                "canchas_en_riesgo":canchas_riesgo,"enfermedad":"Dollar Spot"}
    # Low overall conditions but shadowed canchas still have elevated risk
    if shadowed and h_dollar >= 3:
        shadow_names = ", ".join(c.upper() for c in shadowed)
        return {"nivel":"medio","horas_favorables_48h":h_dollar,"horas_brown_patch_48h":h_brown,
                "descripcion":f"Riesgo moderado por sombra en {shadow_names} ({h_dollar}h favorables)",
                "accion_recomendada":f"Monitorear {shadow_names} — humedad acumulada favorece hongos en zonas sin sol",
                "canchas_en_riesgo":shadowed,"enfermedad":"Dollar Spot"}
    return {"nivel":"bajo","horas_favorables_48h":h_dollar,"horas_brown_patch_48h":h_brown,
            "descripcion":f"Riesgo bajo: {h_dollar}h favorables en últimas 48h",
            "accion_recomendada":"Sin acción preventiva necesaria",
            "canchas_en_riesgo":shadowed if shadowed else [],"enfermedad":None}


def _best_riego_hour(h: dict):
    times = h.get("time", []); winds = h.get("wind_speed_10m", [])
    probs = h.get("precipitation_probability", [])
    for i, t in enumerate(times[:72]):
        hr = int(t[11:13]) if len(t) >= 13 else -1
        if 5 <= hr <= 8:
            w = winds[i] if i < len(winds) else 99
            p = probs[i] if i < len(probs) else 99
            if w is not None and p is not None and w < 10 and p < 20:
                return f"{hr:02d}:00", i // 24
    return "06:00", 1


def _best_riego_hour_alt(h: dict) -> str:
    """Best late-afternoon irrigation window (17:00–19:00 ART) as backup option."""
    times = h.get("time", []); winds = h.get("wind_speed_10m", [])
    probs = h.get("precipitation_probability", [])
    for i, t in enumerate(times[:96]):
        hr = int(t[11:13]) if len(t) >= 13 else -1
        if 17 <= hr <= 19:
            w = winds[i] if i < len(winds) else 99
            p = probs[i] if i < len(probs) else 99
            if w is not None and p is not None and w < 15 and p < 30:
                return f"{hr:02d}:00"
    return "18:00"


def _best_corte_hour(h: dict):
    times = h.get("time", []); winds = h.get("wind_speed_10m", [])
    probs = h.get("precipitation_probability", [])
    for i, t in enumerate(times[:72]):
        hr = int(t[11:13]) if len(t) >= 13 else -1
        if 6 <= hr <= 9:
            w = winds[i] if i < len(winds) else 99
            p = probs[i] if i < len(probs) else 99
            if w is not None and p is not None and w < 15 and p < 30:
                return f"{hr:02d}:00"
    return "07:00"


def _riego_min_from_aspersores(deficit: float, aspersores_cfg: dict) -> int:
    """Compute irrigation minutes from real sprinkler positions stored in config_velez.json."""
    import math as _math
    all_asp = [a for lst in aspersores_cfg.values() for a in lst]
    if not all_asp:
        return max(10, round(deficit / 0.167 / 2)) if deficit > 3 else 0
    # Each sprinkler: r meters, 15 L/min flow — rate = flow / coverage_area
    # riego_min per zone = deficit_mm / rate_mm_per_min
    avg_r   = sum(a.get("r", 8) for a in all_asp) / len(all_asp)
    coverage_m2 = _math.pi * avg_r ** 2  # per sprinkler
    flow_lpm    = 15.0                    # L/min per sprinkler (standard sports irrigation)
    # Application rate (mm/min) = flow(L/min) / coverage(m²)  [1 L/m² = 1 mm]
    rate_mm_per_min = flow_lpm / coverage_m2
    riego_min = round(deficit / rate_mm_per_min) if deficit > 0 else 0
    return max(10, riego_min) if deficit > 3 else 0


def compute_weather_live(nasa, soil, hourly_resp, ecostress, smap,
                         aspersores_cfg: dict = None) -> dict:
    h = hourly_resp.get("hourly", {})

    et0_nasa = nasa.get("EVPTRNS"); et0_pm = None
    if all(k in nasa for k in ("T2M_MAX","T2M_MIN","RH2M","WS2M","ALLSKY_SFC_SW_DWN")):
        try:
            et0_pm = _penman_monteith(nasa["T2M_MAX"],nasa["T2M_MIN"],nasa["RH2M"],
                                      nasa["WS2M"],nasa["ALLSKY_SFC_SW_DWN"],
                                      date.today().timetuple().tm_yday)
        except Exception as e:
            log.warning("PM: %s", e)

    if et0_nasa and et0_nasa > 0:
        et0 = round(0.6*et0_nasa + 0.4*et0_pm, 2) if et0_pm else round(et0_nasa, 2)
        et0_fuente = "nasa+penman-monteith" if et0_pm else "nasa-power"
    elif et0_pm:
        et0, et0_fuente = et0_pm, "penman-monteith"
    else:
        et0, et0_fuente = 3.2, "estimado"

    prec_avg = nasa.get("PRECTOTCORR", 1.2)
    et0_sem  = round(et0 * 7, 1)
    prec_sem = round(prec_avg * 7, 1)
    deficit  = round(max(0.0, et0_sem - prec_sem), 1)

    # Precipitación real horaria — past_days=2 → índice 0..47 = últimas 48h
    _precip_h = [v for v in (h.get("precipitation") or [])[:48] if v is not None]
    lluvia_48h_mm    = round(sum(_precip_h), 1)
    lluvia_max_1h_mm = round(max(_precip_h) if _precip_h else 0.0, 1)

    sm_vals = [v for v in (h.get("soil_moisture_0_to_1cm") or [])[:24] if v is not None]
    hum_pct = round(sum(sm_vals)/len(sm_vals)*100, 1) if sm_vals else 18.0
    hum_est = ("seco" if hum_pct < 10 else "bajo" if hum_pct < 22
               else "normal" if hum_pct < 34 else "humedo")

    sm3  = [v for v in (h.get("soil_moisture_1_to_3cm") or [])[:24] if v is not None]
    hum3 = round(sum(sm3)/len(sm3)*100, 1) if sm3 else hum_pct
    sm9  = [v for v in (h.get("soil_moisture_3_to_9cm") or [])[:24] if v is not None]
    hum9 = round(sum(sm9)/len(sm9)*100, 1) if sm9 else hum3

    def _era5_avg(key):
        vals = [v for v in (h.get(key) or [])[:24] if v is not None]
        return round(sum(vals)/len(vals), 4) if vals else None

    era5_0_7   = _era5_avg("soil_moisture_0_to_7cm")
    era5_7_28  = _era5_avg("soil_moisture_7_to_28cm")
    era5_28_100= _era5_avg("soil_moisture_28_to_100cm")
    era5_100_289=_era5_avg("soil_moisture_100_to_255cm")
    st0  = [v for v in (h.get("soil_temperature_0cm") or [])[:24] if v is not None]
    st6  = [v for v in (h.get("soil_temperature_6cm") or [])[:24] if v is not None]
    st18 = [v for v in (h.get("soil_temperature_18cm") or [])[:24] if v is not None]
    temp_suelo_0cm  = round(sum(st0)/len(st0), 1) if st0 else None
    temp_suelo_6cm  = round(sum(st6)/len(st6), 1) if st6 else None
    temp_suelo_18cm = round(sum(st18)/len(st18), 1) if st18 else None

    hora_riego,     dia_riego = _best_riego_hour(h)
    hora_corte                = _best_corte_hour(h)
    hora_riego_alt            = _best_riego_hour_alt(h)

    clay_pct = round(soil.get("clay", 32), 1)
    sand_pct = round(soil.get("sand", 28), 1)
    whc      = soil.get("whc_mm", 42)

    if aspersores_cfg:
        riego_min = _riego_min_from_aspersores(deficit, aspersores_cfg)
        riego_fuente = "aspersores-reales"
    else:
        riego_min    = max(10, round(deficit / 0.167 / 2)) if deficit > 3 else 0
        riego_fuente = "estimado"

    m3         = deficit * 28_000 / 1000
    costo_agua = int(round(m3 * 200 / 500) * 500) if deficit > 3 else 0

    gdd_7d   = nasa.get("_gdd_7d", 35.0)
    gdd_rate = nasa.get("_gdd_rate", 5.0)
    dias_corte = max(3, min(14, round(40 / max(0.1, gdd_rate))))

    return {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "fuentes": [k for k, d in [("nasa-power",nasa),("soilgrids",soil),("open-meteo-hourly",h)] if d],
        "et0_mm_dia": et0, "et0_fuente": et0_fuente,
        "et0_semana_mm": et0_sem, "precipitacion_semana_mm": prec_sem,
        "deficit_hidrico_mm": deficit, "deficit_hidrico": deficit > 5,
        "litros_m2_semana": deficit, "riego_min_sector": riego_min,
        "riego_min_fuente": riego_fuente,
        "hora_riego_optima": hora_riego, "dia_riego_offset": dia_riego,
        "hora_riego_alternativa": hora_riego_alt,
        "hora_corte_optima": hora_corte,
        "humedad_suelo_pct": hum_pct, "humedad_suelo_estado": hum_est,
        "humedad_subsuperficial_pct": hum3,
        "humedad_9cm_pct": hum9,
        "temp_suelo_0cm": temp_suelo_0cm,
        "temp_suelo_6cm": temp_suelo_6cm,
        "temp_suelo_18cm": temp_suelo_18cm,
        "suelo_tipo": "Suelo pesado", "suelo_clay_pct": clay_pct, "suelo_sand_pct": sand_pct,
        "suelo_whc_mm": int(whc) if whc else 42,
        "gdd_acumulado_7d": gdd_7d, "gdd_rate_diario": gdd_rate,
        "dias_proximo_corte": dias_corte,
        "gndvi_por_cancha": _gndvi_per_cancha(),
        "riesgo_fungosis": _fungal_risk(h),
        "ecostress": ecostress, "smap": smap,
        "sar_disponible": False, "sar_mensaje": "Sin integración SAR activa",
        "costo_agua_ars": costo_agua,
        "lluvia_48h_mm":    lluvia_48h_mm,
        "lluvia_max_1h_mm": lluvia_max_1h_mm,
        "era5_sm_0_7cm":    era5_0_7,
        "era5_sm_7_28cm":   era5_7_28,
        "era5_sm_28_100cm": era5_28_100,
        "era5_sm_100_289cm":era5_100_289,
    }


# ── Async gather ──────────────────────────────────────────────────────────────

_pool = ThreadPoolExecutor(max_workers=6)

async def _arun(fn):
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(_pool, fn)

async def _fetch_all_async() -> dict:
    keys = ["nasa","soil","hourly","ecostress","smap"]
    fns  = [_fetch_nasa_power, _fetch_soilgrids_cached, _fetch_open_meteo, _fetch_ecostress, _fetch_smap]
    results = await asyncio.gather(*[_arun(f) for f in fns], return_exceptions=True)
    out = {}
    for key, res in zip(keys, results):
        if isinstance(res, Exception):
            log.warning("FAIL %s: %s", key, res); out[key] = {}
        else:
            log.info("OK %s", key); out[key] = res or {}
    return out


# ── GitHub push ───────────────────────────────────────────────────────────────

_GH_API   = "https://api.github.com"
_OWNER    = "protocolfaro"
_REPO     = "faroprotocol"
_BRANCH   = "main"
_VD_PATH  = "velez/velez_data.json"
_CFG_PATH = "velez/config_velez.json"


def _gh_headers():
    token = os.environ.get("GITHUB_TOKEN", "")
    if not token:
        raise EnvironmentError("GITHUB_TOKEN not set")
    return {"Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28"}


def _gh_get_sha_and_content(path: str):
    r = _req.get(f"{_GH_API}/repos/{_OWNER}/{_REPO}/contents/{path}",
                 headers=_gh_headers(), params={"ref": _BRANCH}, timeout=15)
    if r.status_code == 200:
        d = r.json()
        content = json.loads(base64.b64decode(d["content"]).decode())
        return d["sha"], content
    return None, {}


_DIA_NOMBRES = ["Lun", "Mar", "Mié", "Jue", "Vie", "Sáb", "Dom"]


def _update_tareas_dates(cfg: dict) -> None:
    """Shift tareas_semana fecha_iso to the current ISO week (Mon–Sun)."""
    today = date.today()
    monday = today - timedelta(days=today.weekday())
    tareas = cfg.get("usuarios", {}).get("roger", {}).get("tareas_semana", [])
    for t in tareas:
        offset = t.get("dia_offset", 0)
        t["fecha_iso"] = (monday + timedelta(days=offset)).isoformat()


def push_weather_update(weather_live: dict) -> str:
    """Write weather_live + roger acciones/kpis to GitHub. Supabase primary for weather_live."""
    # Extract engine outputs before Supabase write (they go only to GitHub JSON)
    physics_acc    = weather_live.pop("_physics_acciones_roger", [])
    field_acc      = weather_live.pop("_field_acciones_roger", [])
    roger_kpis     = weather_live.pop("_roger_kpis", [])
    prescs_op      = weather_live.pop("_prescripciones_operativas", {})
    estados_op     = weather_live.pop("_estados_detectados", {})
    zonas_op       = weather_live.pop("_zonas_estres", {})
    hermes_hm_raw  = weather_live.pop("_hermes_heatmaps", {})  # ResultadoAuditoria objects — not JSON-safe
    solar_eff      = weather_live.pop("_solar_eff_pvlib", None)
    solar_ghi      = weather_live.pop("_solar_ghi_kwh", None)
    solar_t_cell   = weather_live.pop("_solar_t_cell", None)
    solar_pr       = weather_live.pop("_solar_pr_pvlib", None)

    try:
        import velez_supabase as _vs
        if _vs._ok():
            if _vs.upsert_weather_live(weather_live):
                log.info("push_weather_update: Supabase OK")
                # Still update GitHub JSON for physics prescriptions + email scheduler
    except Exception as _se:
        log.warning("push_weather_update Supabase (non-fatal): %s", _se)

    # GitHub PUT — always runs to keep velez_data.json fresh for email scheduler
    ts = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    sha, cfg = _gh_get_sha_and_content(_VD_PATH)
    cfg["weather_live"] = weather_live
    today_d = date.today()
    cfg.setdefault("meta", {})["fecha"]   = today_d.isoformat()
    cfg["meta"]["semana"] = (today_d - timedelta(days=today_d.weekday())).isoformat()
    cfg["updated_at"]    = ts
    cfg["_assembled_at"] = ts
    _update_tareas_dates(cfg)

    # Inject solar live metrics into sectores.solar for Panel Roger
    if solar_eff is not None:
        cfg.setdefault("sectores", {}).setdefault("solar", {}).update({
            "eficiencia_pct_pvlib": solar_eff,
            "eficiencia_pct":       solar_eff,
            "ghi_kwh_m2_real":      solar_ghi,
            "t_cell_estimada":      solar_t_cell,
            "pr_pvlib":             solar_pr,
        })

    # Inject acciones + kpis into roger section
    roger = cfg.setdefault("usuarios", {}).setdefault("roger", {})
    if field_acc:
        # acciones_engine generated a complete set (includes physics prefix) — replace all
        roger["acciones"] = field_acc
    elif physics_acc:
        # Fallback: only physics available — keep existing field acciones, update physics
        existing = [a for a in roger.get("acciones", [])
                    if not (a.startswith("Ventana de corte:") or
                            a.startswith("Prescripcion riego") or
                            a.startswith("Riego SAR:"))]
        roger["acciones"] = physics_acc + existing
    if roger_kpis:
        roger["kpis"] = roger_kpis

    # Inject prescripcion_operativa + estado_detectado + zonas_estres per cancha
    _hm = roger.setdefault("heatmaps", {})
    if prescs_op or estados_op or zonas_op:
        for _cid, _presc in prescs_op.items():
            _hm.setdefault(_cid, {})["prescripcion_operativa"] = _presc
        for _cid, _est in estados_op.items():
            _hm.setdefault(_cid, {})["estado_detectado"] = _est
        for _cid, _zon in zonas_op.items():
            _hm.setdefault(_cid, {})["zonas_estres"] = _zon
        for _c in (cfg.get("sectores", {}).get("canchero", {}).get("canchas", []) or []):
            _cid = _c.get("id")
            if _cid in prescs_op:
                _c["prescripcion_operativa"] = prescs_op[_cid]
            if _cid in estados_op:
                _c["estado_detectado"] = estados_op[_cid]
            if _cid in zonas_op:
                _c["zonas_estres"] = zonas_op[_cid]
        log.info("push_weather_update: %d prescs · %d estados · %d zonas inyectados",
                 len(prescs_op), len(estados_op), len(zonas_op))
    # Propagar campos Hermes a roger.heatmaps (umbral dinámico + confianza para Roger)
    for _cid, _hres in (hermes_hm_raw or {}).items():
        _hm.setdefault(_cid, {})
        _hm[_cid]["hermes_umbral_dinamico"] = _hres.umbral_dinamico
        _hm[_cid]["hermes_confianza"]       = _hres.confianza
        _hm[_cid]["hermes_status"]          = _hres.status
        _hm[_cid]["hermes_ndvi_fuente"]     = _hres.ndvi_fuente

    # Fallback: compute estado_detectado directly from current heatmap data
    # if temporal eye didn't produce it (no time series) — ensures field always present
    _sm_pct = float(weather_live.get("humedad_suelo_pct") or 20.0)
    _m_now  = today_d.month
    # Umbral por cancha: usa hermes_umbral_dinamico si disponible, de lo contrario estacional
    _NDVI_CRISIS_FB = {1:0.14,2:0.14,3:0.11,4:0.09,5:0.06,6:0.04,7:0.04,8:0.05,
                       9:0.08,10:0.11,11:0.13,12:0.14}
    for _cid, _hme in _hm.items():
        if not isinstance(_hme, dict):
            continue
        if _cid in estados_op:
            continue   # temporal_eye produjo resultado real ESTE ciclo — no pisar
        _ndvi_raw = _hme.get("ndvi")   # None si sin imagen óptica — no usar proxy
        _ipos = float(_hme.get("ipos") or 0.0)
        _bsi  = float(_hme.get("bsi")  or 0.0)
        _nd_thr = _hme.get("hermes_umbral_dinamico") or _NDVI_CRISIS_FB.get(_m_now, 0.10)
        if _bsi > 0.12 and _sm_pct < 20.0 and _ipos > 150:
            _hme["estado_detectado"] = "compactacion"
        elif _ndvi_raw is None:
            _hme["estado_detectado"] = "INDETERMINADO"   # sin imagen óptica: estado honesto
        elif float(_ndvi_raw) < _nd_thr and _sm_pct < 25.0:
            _hme["estado_detectado"] = "stress_hidrico"
        else:
            _hme["estado_detectado"] = "normal"
    # Mirror estado_detectado to canchas list
    for _c in (cfg.get("sectores", {}).get("canchero", {}).get("canchas", []) or []):
        _cid = _c.get("id")
        if _cid and _hm.get(_cid, {}).get("estado_detectado"):
            _c["estado_detectado"] = _hm[_cid]["estado_detectado"]
    # Fallback: synthetic zonas_estres from estado_detectado + ndvi if temporal eye didn't produce it
    for _cid, _hme in _hm.items():
        if not isinstance(_hme, dict) or _cid in zonas_op:
            continue   # temporal_eye produjo zonas reales ESTE ciclo — no pisar
        _ndvi_fb = _hme.get("ndvi")   # None si sin imagen óptica — no usar proxy
        _est_fb  = _hme.get("estado_detectado", "normal")
        if _ndvi_fb is None and _est_fb not in ("resiembra_activa", "stress_hidrico", "compactacion", "fungosis"):
            _hme["zonas_estres"] = None   # sin imagen óptica, sin estado confirmado: null explícito
            continue
        _base = (0.85 if _est_fb == "resiembra_activa" else
                 0.65 if _est_fb == "stress_hidrico"   else
                 0.70 if _est_fb == "compactacion"      else
                 0.60 if _est_fb == "fungosis"          else
                 max(0.1, 1.0 - float(_ndvi_fb) / 0.65))  # solo alcanzado si _ndvi_fb is not None
        _hme["zonas_estres"] = {
            "nodos": [
                {"x": 0.25, "y": 0.15, "stress": round(_base * 0.70, 4), "zona": "Banda Izquierda Norte"},
                {"x": 0.75, "y": 0.15, "stress": round(_base * 0.65, 4), "zona": "Banda Derecha Norte"},
                {"x": 0.25, "y": 0.50, "stress": round(_base,        4), "zona": "Pasillo Central Norte"},
                {"x": 0.75, "y": 0.50, "stress": round(_base * 0.95, 4), "zona": "Pasillo Central Sur"},
                {"x": 0.25, "y": 0.85, "stress": round(_base * 0.75, 4), "zona": "Banda Izquierda Sur"},
                {"x": 0.75, "y": 0.85, "stress": round(_base * 0.70, 4), "zona": "Banda Derecha Sur"},
            ],
            "zona_critica": "Pasillo Central" if _base > 0.60 else "Normal",
            "prioridad":    5 if _base > 0.60 else 1,
            "stress_max":   round(_base, 4),
            "fuente":       "synthetic-fallback",
        }
    # Mirror zonas_estres to canchas list — always overwrite with fresh value
    for _c in (cfg.get("sectores", {}).get("canchero", {}).get("canchas", []) or []):
        _cid = _c.get("id")
        if _cid:
            _c["zonas_estres"] = _hm.get(_cid, {}).get("zonas_estres")
    # Synthetic prescripcion_operativa for canchas not covered by temporal_eye this cycle
    _m_pr = today_d.month
    _INVIERNO = _m_pr in (5, 6, 7, 8)
    for _cid, _hme in _hm.items():
        if not isinstance(_hme, dict) or _cid in prescs_op:
            continue   # temporal_eye ya generó una prescripción real — no pisar
        _ndvi_pr = _hme.get("ndvi")
        _est_pr  = _hme.get("estado_detectado", "normal")
        _label   = _cid.upper().replace("_", " ")
        if _est_pr == "stress_hidrico" and _ndvi_pr is not None:
            _presc = (f"{_label}: RIEGO URGENTE — NDVI={float(_ndvi_pr):.3f}, stress hídrico"
                      f" — regar 8-10 mm mañana temprano")
        elif _est_pr == "compactacion":
            _presc = f"{_label}: AIREACIÓN — compactación detectada — programar subsolado en 48 hs"
        elif _est_pr == "resiembra_activa":
            _presc = f"{_label}: CIERRE — resiembra activa — no pisar por 72 hs mínimo"
        elif _est_pr == "fungosis":
            _presc = f"{_label}: FUNGICIDA — aplicar en 24 hs, revisar drenaje"
        elif _ndvi_pr is not None and float(_ndvi_pr) < 0.20:
            _presc = (f"{_label}: CRÍTICO — NDVI={float(_ndvi_pr):.3f} bajo mínimo"
                      f" — reducir carga y regar")
        elif _INVIERNO and _ndvi_pr is not None:
            _presc = (f"{_label}: Dormancia invernal — NDVI={float(_ndvi_pr):.3f}"
                      f" (normal {['May','Jun','Jul','Ago'][_m_pr-5]}) — sin intervención urgente")
        elif _ndvi_pr is not None:
            _presc = f"{_label}: Normal — NDVI={float(_ndvi_pr):.3f} — sin intervención urgente"
        else:
            _presc = f"{_label}: Sin imagen óptica disponible — monitoreo en curso"
        _hme["prescripcion_operativa"] = _presc
    # Mirror prescripcion_operativa to canchas list — always overwrite
    for _c in (cfg.get("sectores", {}).get("canchero", {}).get("canchas", []) or []):
        _cid = _c.get("id")
        if _cid and _hm.get(_cid, {}).get("prescripcion_operativa"):
            _c["prescripcion_operativa"] = _hm[_cid]["prescripcion_operativa"]

    payload = {
        "message": f"data refresh: weather+physics [{ts}]",
        "content": base64.b64encode(
            json.dumps(cfg, ensure_ascii=False, separators=(",",":")).encode()
        ).decode(),
        "branch": _BRANCH,
    }
    if sha:
        payload["sha"] = sha
    r = _req.put(f"{_GH_API}/repos/{_OWNER}/{_REPO}/contents/{_VD_PATH}",
                 headers=_gh_headers(), json=payload, timeout=35)
    r.raise_for_status()
    return r.json().get("commit", {}).get("html_url", "")


# ── InSAR sector mapping ──────────────────────────────────────────────────────

# insar_hyp3 sector_id → (velez_sectores sector_id, display label)
# Tribuna entries store as separate sector rows; assembler aggregates into estadio.tribunas_insar
_INSAR_SECTOR_MAP: dict[str, tuple[str, str]] = {
    "estadio":                ("estadio",               "InSAR Estadio"),
    "estadio_tribuna_norte":  ("estadio_tribuna_norte", "InSAR Tribuna Norte"),
    "estadio_tribuna_sur":    ("estadio_tribuna_sur",   "InSAR Tribuna Sur"),
    "estadio_tribuna_este":   ("estadio_tribuna_este",  "InSAR Tribuna Este"),
    "estadio_tribuna_oeste":  ("estadio_tribuna_oeste", "InSAR Tribuna Oeste"),
    "poli_basquet":           ("poli",                  "InSAR Básquet"),
    "poli_playon_norte":      ("poli",                  "InSAR Playón Norte"),
    "sede_anexo_norte":       ("sede",                  "InSAR Anexo Norte"),
    "piletas":                ("piletas",               "InSAR Piletas"),
    "instituto":              ("instituto",             "InSAR Instituto"),
}


def push_insar_update(insar_result: dict) -> str:
    """Update sectores InSAR in Supabase (primary). Falls back to GitHub if not configured."""
    ts        = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    sector_mm = insar_result.get("sectores", {})

    # Build sectores update dict (shared by Supabase and GitHub paths)
    poli_vals: list[tuple[float, str]] = []
    tribuna_vals: dict[str, float] = {}   # {norte|sur|este|oeste: mm}
    sectores_upd: dict = {}
    for insar_id, val_mm in sector_mm.items():
        json_key, label = _INSAR_SECTOR_MAP.get(insar_id, (None, None))
        if json_key is None:
            continue
        if json_key == "poli":
            poli_vals.append((val_mm, label))
            continue
        # Tribuna: store as separate sector row AND aggregate into estadio mean
        if json_key.startswith("estadio_tribuna_"):
            trib_key = json_key.replace("estadio_tribuna_", "")   # "norte", "sur", "este", "oeste"
            tribuna_vals[trib_key] = val_mm
            sectores_upd[json_key] = {"insar_mm": val_mm, "detalle": f"{label}: {val_mm:+.2f} mm"}
            continue
        sectores_upd[json_key] = {"insar_mm": val_mm, "detalle": f"{label}: {val_mm:+.2f} mm"}
    if poli_vals:
        mean_mm    = round(sum(v for v, _ in poli_vals) / len(poli_vals), 2)
        poli_label = poli_vals[0][1]
        sectores_upd["poli"] = {"insar_mm": mean_mm, "detalle": f"{poli_label}: {mean_mm:+.2f} mm"}
    # If per-tribuna data available and no global estadio entry, derive estadio from tribuna mean
    if tribuna_vals and "estadio" not in sectores_upd:
        mean_mm = round(sum(tribuna_vals.values()) / len(tribuna_vals), 2)
        n = len(tribuna_vals)
        sectores_upd["estadio"] = {
            "insar_mm": mean_mm,
            "detalle":  f"InSAR Estadio: {mean_mm:+.2f} mm · media {n} tribunas",
        }
        log.info("push_insar_update: tribuna mean %.2f mm (%d tribunas)", mean_mm, n)

    # Primary: Supabase UPSERT
    if sectores_upd:
        try:
            import velez_supabase as _vs
            if _vs._ok():
                if _vs.upsert_sectores(sectores_upd):
                    log.info("push_insar_update: Supabase OK — GitHub write skipped")
                    return "supabase:ok"
        except Exception as _se:
            log.warning("push_insar_update Supabase (non-fatal): %s — fallback GitHub", _se)

    # Fallback: GitHub PUT
    sha, cfg = _gh_get_sha_and_content(_VD_PATH)
    sectores  = cfg.setdefault("sectores", {})
    for json_key, data in sectores_upd.items():
        sec = sectores.setdefault(json_key, {})
        sec["insar_mm"] = data["insar_mm"]
        sec["detalle"]  = data["detalle"]
    cfg.setdefault("meta", {})["fecha"] = date.today().isoformat()
    cfg["updated_at"] = ts
    ref  = insar_result.get("fecha_ref", "?")
    sec_ = insar_result.get("fecha_sec", "?")
    payload = {
        "message": f"data refresh: InSAR update [{ref}/{sec_}] [{ts}]",
        "content": base64.b64encode(
            json.dumps(cfg, ensure_ascii=False, separators=(",", ":")).encode()
        ).decode(),
        "branch": _BRANCH,
    }
    if sha:
        payload["sha"] = sha
    r = _req.put(f"{_GH_API}/repos/{_OWNER}/{_REPO}/contents/{_VD_PATH}",
                 headers=_gh_headers(), json=payload, timeout=35)
    r.raise_for_status()
    return r.json().get("commit", {}).get("html_url", "")


def run_insar_refresh() -> dict:
    """Fetch Sentinel-1 InSAR displacement and push updated sector data to GitHub."""
    log.info("data_refresh: starting InSAR refresh")
    try:
        import insar_hyp3
        insar_data = insar_hyp3.fetch_insar()
        if not insar_data:
            log.warning("insar_hyp3: returned None — no update pushed")
            return {"ok": False, "error": "No InSAR data available"}
        commit_url = push_insar_update(insar_data)
        log.info(
            "✅ InSAR actualizado — %d sectores · %s",
            len(insar_data.get("sectores", {})),
            insar_data.get("fuente", ""),
        )
        return {"ok": True, "commit": commit_url, "sectores": insar_data.get("sectores", {})}
    except Exception as e:
        log.error("run_insar_refresh FAILED: %s", e)
        return {"ok": False, "error": str(e)}


# ── Public entry point ────────────────────────────────────────────────────────

def run_refresh() -> dict:
    """Fetch live weather and push updated weather_live to GitHub. Returns result dict."""
    log.info("data_refresh: starting live weather fetch")
    try:
        raw = asyncio.run(_fetch_all_async())

        # Read velez_data.json for shadow_maps, Kalman gap-fill, and acciones_engine
        vd: dict = {}
        shadow_pct_by_cancha = {}
        aspersores_cfg       = None
        try:
            _, vd = _gh_get_sha_and_content(_VD_PATH)
            shadow_maps = vd.get("shadow_maps", {})
            shadow_pct_by_cancha = {
                cid: sm.get("sombra_permanente_pct", 0)
                for cid, sm in shadow_maps.items()
                if isinstance(sm, dict) and sm.get("sombra_permanente_pct", 0) > 0
            }
            log.info("shadow_pct_by_cancha: %s", shadow_pct_by_cancha)
        except Exception as _ve:
            log.warning("shadow_maps read (non-fatal): %s", _ve)
        try:
            railway_url = os.environ.get("RAILWAY_URL", "").rstrip("/")
            if railway_url:
                r = _req.get(f"{railway_url}/velez/aspersores", timeout=8)
                if r.status_code == 200:
                    aspersores_cfg = r.json().get("aspersores_por_cancha") or None
                    if aspersores_cfg:
                        log.info("aspersores_cfg: %d canchas desde Railway", len(aspersores_cfg))
        except Exception as _ae:
            log.warning("aspersores Railway (non-fatal): %s — usando estimado", _ae)

        weather = compute_weather_live(
            raw["nasa"], raw["soil"], raw["hourly"], raw["ecostress"], raw["smap"],
            aspersores_cfg=aspersores_cfg,
        )
        # Override fungal risk with shadow data (compute_weather_live uses default _fungal_risk)
        hourly_h = raw["hourly"].get("hourly", {})

        # ── ECOSTRESS ET real + SMAP θ soil (NASA Earthdata) ─────────────────
        try:
            from faro_ecostress import fetch_ecostress_et, fetch_smap_theta
            _eco_et = fetch_ecostress_et()
            if _eco_et is not None:
                weather["et_ecostress_mm"] = _eco_et
                log.info("ECOSTRESS: ET=%.3f mm/día", _eco_et)
            _smap_th = fetch_smap_theta()
            if _smap_th is not None:
                weather["smap_sm_pct"] = round(_smap_th * 100, 1)
                log.info("SMAP: θ=%.4f → %.1f%%", _smap_th, weather["smap_sm_pct"])
        except Exception as _eco_exc:
            log.warning("ECOSTRESS/SMAP (non-fatal): %s", _eco_exc)
        weather["riesgo_fungosis"] = _fungal_risk(hourly_h, shadow_pct_by_cancha)

        # ── Satélite: NDVI + pipeline con retry automático ───────────────────
        try:
            from faro_pipeline_runner import PipelineStep as _PS, run_pipeline as _run_pl
            import satellite_pipeline as _sp

            _ndvi_holder: dict = {}

            def _fetch_ndvi_tracked():
                data = ndvi_real.fetch_ndvi()
                if data:
                    _ndvi_holder["data"] = data
                return data   # None es válido — cascade agotó alternativas

            _ndvi_result = _PS(
                "ndvi_real", _fetch_ndvi_tracked,
                retries=3, wait_min_s=30, wait_max_s=300,
                required=False, none_is_ok=True,
            ).execute()

            ndvi_data = _ndvi_holder.get("data")

            if ndvi_data:
                weather["gndvi_por_cancha"] = ndvi_data
                log.info("data_refresh: imagen optica disponible — pipeline completo")
                _sp_result = _PS(
                    "satellite_pipeline",
                    lambda: _sp.run_satellite_cycle(ndvi_data),
                    retries=2, wait_min_s=30, wait_max_s=180, required=True,
                ).execute()
                if not _sp_result.ok:
                    log.warning("satellite_pipeline fallo tras reintentos: %s", _sp_result.error)
            else:
                log.info("data_refresh: sin imagen optica — modo SAR+Kalman")
                try:
                    sar_kalman_data = _sp.run_sar_kalman_cycle()
                    if sar_kalman_data:
                        weather["gndvi_por_cancha"] = sar_kalman_data
                        log.info("data_refresh: SAR+Kalman OK — %d canchas estimadas",
                                 len(sar_kalman_data.get("canchas", {})))
                except Exception as _sk_err:
                    log.warning("SAR+Kalman cycle (non-fatal): %s", _sk_err)

        except ImportError:
            # tenacity no instalado — fallback al comportamiento anterior sin retry
            try:
                ndvi_data = ndvi_real.fetch_ndvi()
                import satellite_pipeline as _sp
                if ndvi_data:
                    weather["gndvi_por_cancha"] = ndvi_data
                    _sp.run_satellite_cycle(ndvi_data)
                else:
                    sar_kalman_data = _sp.run_sar_kalman_cycle()
                    if sar_kalman_data:
                        weather["gndvi_por_cancha"] = sar_kalman_data
            except Exception as _ndvi_err:
                log.warning("ndvi_real fallback (non-fatal): %s", _ndvi_err)
        except Exception as _pl_err:
            log.warning("pipeline runner (non-fatal): %s", _pl_err)
        # ── Multi-cancha Kalman gap-fill (extend Amalfitani model to all VO canchas) ──
        try:
            import faro_kalman_gapfill as _kgf
            _kalman_all = _kgf.gap_fill_all_canchas(vd)
            if _kalman_all:
                _gpc = weather.setdefault("gndvi_por_cancha", {})
                _gpc_canchas = _gpc.setdefault("canchas", {}) if isinstance(_gpc, dict) else {}
                for _cid, _kdata in _kalman_all.items():
                    if _cid not in _gpc_canchas:  # don't overwrite real satellite data
                        _gpc_canchas[_cid] = _kdata
                weather["_kalman_heatmaps"] = _kalman_all  # pass to acciones_engine below
                log.info("gap_fill_all_canchas: %d canchas via Kalman LSTM Extended", len(_kalman_all))
        except Exception as _kgf_err:
            log.warning("gap_fill_all_canchas (non-fatal): %s", _kgf_err)
        # ── Temporal eye: event detection from satellite time series ─────────
        try:
            import faro_temporal_eye as _fte
            _hm_eye = {
                **weather.get("_kalman_heatmaps", {}),
                **vd.get("usuarios", {}).get("roger", {}).get("heatmaps", {}),
            }
            _eye_results = _fte.analyze_all(_hm_eye, weather, month=date.today().month)
            weather["_temporal_eye"] = _eye_results
            _eye_resumen = _eye_results.get("_resumen", {})
            _prescs  = _eye_resumen.get("prescripciones", {})
            _estados = _eye_resumen.get("estados", {})
            _zonas   = _eye_resumen.get("zonas_estres", {})
            if _prescs:
                weather["_prescripciones_operativas"] = _prescs
            if _estados:
                weather["_estados_detectados"] = _estados
            if _zonas:
                weather["_zonas_estres"] = _zonas
            log.info("faro_temporal_eye: %d eventos · %d canchas · %d prescs · %d estados · %d zonas",
                     _eye_resumen.get("eventos_total", 0),
                     _eye_resumen.get("n_canchas", 0),
                     len(_prescs), len(_estados), len(_zonas))
        except Exception as _fte_err:
            log.warning("faro_temporal_eye (non-fatal): %s", _fte_err)
        # ── Physics prescriptions (faro_analytics_physics) ───────────────────
        try:
            import sys as _sys
            _here_dr = os.path.dirname(os.path.abspath(__file__))
            _rb_path = os.path.join(_here_dr, "..", "..", "..")
            if _rb_path not in _sys.path:
                _sys.path.insert(0, _rb_path)
            from faro_analytics_physics import (compute_faro_cutting_core,
                                               compute_faro_hydro_core,
                                               compute_smith_kerns)
            _temp24  = float(raw.get("nasa", {}).get("T2M_MAX", 18.0))
            _tmin24  = float(raw.get("nasa", {}).get("T2M_MIN", 10.0))
            _rh24    = float(raw.get("nasa", {}).get("RH2M", 75.0))
            _hum_pct = weather.get("humedad_suelo_pct", 22.0)
            _h_suc   = max(50.0, 350.0 * (1.0 - min(_hum_pct / 40.0, 1.0)))
            _gdd_acc = weather.get("gdd_acumulado_7d", 0.0)  # GDD real base 10°C
            _cutting = compute_faro_cutting_core(_temp24, _rh24, _gdd_acc, 35.0, _h_suc)
            _cp = _cutting["cutting_prescription"]
            _phys_acc = [
                f'Ventana de corte: {_cp["status_window"]} — '
                f'{_cp["prescribed_height_mm"]} mm · {_cp["reel_speed_rpm"]} RPM',
            ]
            # Smith-Kerns Dollar Spot — real 5-day avg T + RH from NASA POWER
            _t5_avg  = float(raw.get("nasa", {}).get("_t2m_avg_5d",
                             (_temp24 + _tmin24) / 2.0))
            _rh5_avg = float(raw.get("nasa", {}).get("_rh2m_avg_5d", _rh24))
            _sk_pct = compute_smith_kerns(_t5_avg, _rh5_avg)
            weather["riesgo_dollar_spot_pct"] = _sk_pct
            log.info("smith_kerns: Dollar Spot %.1f%%", _sk_pct)

            _deficit   = weather.get("deficit_hidrico_mm", 0.0)
            _riego_min = weather.get("riego_min_sector", 0)
            # Intentar SAR real Sentinel-1 (últimos 6 días, revisita ~3-4d a 34°S)
            _s1_vv = _s1_vh = None
            try:
                from faro_sar_s1_backfill import _search_s1, _read_band_db, _get_sas_token, _BBOX
                from datetime import date as _date_cls, timedelta as _td
                _s1_scenes = _search_s1(_date_cls.today() - _td(days=6), _date_cls.today(), limit=3)
                if _s1_scenes:
                    _s1_tok = _get_sas_token()
                    _s1_item = _s1_scenes[-1]  # más reciente
                    _s1_vv   = _read_band_db(_s1_item, "vv", _s1_tok)
                    _s1_vh   = _read_band_db(_s1_item, "vh", _s1_tok)
                    if _s1_vv is not None:
                        _s1_dt = (_s1_item.get("properties", {}).get("datetime") or "")[:10]
                        log.info("S1 real: VV=%.1f VH=%s dB escena=%s", _s1_vv, _s1_vh, _s1_dt)
            except Exception as _s1_exc:
                log.debug("S1 adquisición (non-fatal): %s", _s1_exc)

            # Van Genuchten — corre siempre (fallback si no hay S1, o para θ y matric)
            try:
                import math as _math
                _theta    = max(0.046, min(0.409, _hum_pct / 100.0))
                _s_soil   = max(1e-4, (_theta - 0.12) / 0.28)
                # Si S1 real disponible, usarlo; si no, Van Genuchten proxy
                _sar_vv   = _s1_vv if _s1_vv is not None else round(10.0 * _math.log10(max(1e-9, _s_soil * 0.85)), 2)
                _sar_vh   = _s1_vh if _s1_vh is not None else round(10.0 * _math.log10(max(1e-9, _s_soil * 0.85 * 0.11)), 2)
                _hydro    = compute_faro_hydro_core(_sar_vv, _sar_vh, 2.5, _deficit)
                weather["soil_moisture_sar"]   = _hydro["soil_moisture_volumetric"]
                weather["matric_potential_cm"] = _hydro["matric_potential_cm"]
                weather["sar_disponible"]      = True
                weather["sar_mensaje"]         = (
                    "Sentinel-1 GRD real · PC · 20m" if _s1_vv is not None
                    else "Van Genuchten activo (SM proxy)"
                )
                if _deficit > 3:
                    _riego_min = _hydro["irrigation_prescription"]["aspersor_time_minutes"]
                    _deficit   = _hydro["irrigation_prescription"]["deficit_hydric_mm"]
                # Persistir en soil_metrics (S1 real si disponible, si no Van Genuchten)
                _sm_fuente = (
                    "sentinel-1-grd · planetary-computer · 20m"
                    if _s1_vv is not None
                    else "van-genuchten-proxy · open-meteo"
                )
                try:
                    from velez_supabase import insert_soil_metrics as _ins_sm
                    _ins_sm(
                        venue_id="amalfitani",
                        is_hibrido=True,
                        sar_vv_db=_sar_vv,
                        sar_vh_db=_sar_vh,
                        theta_soil=_theta,
                        h_suction_cm=float(_hydro.get("matric_potential_cm") or 0),
                        fuente=_sm_fuente,
                    )
                    log.info("soil_metrics: INSERT OK (%s θ=%.3f)", _sm_fuente, _theta)
                    _vo_canchas = ["1fa","2fa","3fa","4fa","5fa","6fa","7fa","8fa","9fa","10fa","1fp","2fp"]
                    _h_suc_val = float(_hydro.get("matric_potential_cm") or 0)
                    for _cid in _vo_canchas:
                        try:
                            _ins_sm(
                                venue_id="villa_olimpica",
                                cancha_id=_cid,
                                is_hibrido=(_cid == "1fa"),
                                sar_vv_db=_sar_vv,
                                sar_vh_db=_sar_vh,
                                theta_soil=_theta,
                                h_suction_cm=_h_suc_val,
                                fuente=_sm_fuente + " · cancha-vo",
                            )
                        except Exception:
                            pass
                    log.info("soil_metrics VO: INSERT OK para %d canchas", len(_vo_canchas))
                except Exception as _sm_exc:
                    log.warning("soil_metrics write (non-fatal): %s", _sm_exc)
            except Exception as _ve:
                log.warning("Van Genuchten proxy (non-fatal): %s", _ve)
            if _deficit > 3:
                _phys_acc.append(
                    f'Prescripción riego (Toro 12.5 mm/h): déficit {_deficit:.1f} mm — '
                    f'{_riego_min} min por sector'
                )
            weather["_physics_acciones_roger"] = _phys_acc
            weather["_ventana_corte"]   = _cp.get("status_window")
            weather["_altura_corte_mm"] = _cp.get("prescribed_height_mm")
            weather["_riego_min_final"] = _riego_min
            log.info("physics prescriptions: corte=%s sk=%.1f%% acciones=%d",
                     _cp["status_window"], _sk_pct, len(_phys_acc))
        except Exception as _pe:
            log.warning("faro_analytics_physics (non-fatal): %s", _pe)
        # ─────────────────────────────────────────────────────────────────────
        # ── ERA5-Land Sectorial: ET₀/T/RH/SM por bloque de canchas ──────────
        try:
            from faro_era5_land_sectorial import process_all_sectors as _era5_sectors
            _era5_result = _era5_sectors()   # hoy-7d por defecto (lag ~5-7 días CDS)
            log.info(
                "ERA5-Land sectorial: %d OK / %d falló — status=%s — fecha=%s",
                _era5_result.get("sectors_ok",     0),
                _era5_result.get("sectors_failed", 0),
                _era5_result.get("status",         "?"),
                _era5_result.get("date",           "?"),
            )
        except Exception as _era5_exc:
            log.warning("ERA5-Land sectorial (non-fatal): %s", _era5_exc)

        # ── climate_metrics: persistir ciclo diario en Supabase ──────────────
        try:
            from velez_supabase import insert_climate_metrics as _ins_cm
            _cm_kwargs = dict(
                et0_mm_dia=weather.get("et0_mm_dia"),
                deficit_hidrico_mm=weather.get("deficit_hidrico_mm"),
                gdd_acumulado_7d=weather.get("gdd_acumulado_7d"),
                smith_kerns_pct=weather.get("riesgo_dollar_spot_pct"),
                riego_min=weather.get("_riego_min_final") or weather.get("riego_min_sector"),
                ventana_corte=weather.get("_ventana_corte"),
                altura_corte_mm=weather.get("_altura_corte_mm"),
            )
            for _cm_venue in ["amalfitani", "villa_olimpica"]:
                _cm_ok = _ins_cm(venue_id=_cm_venue, **_cm_kwargs)
                if _cm_ok:
                    log.info("climate_metrics: INSERT OK para %s", _cm_venue)
                else:
                    log.error("climate_metrics: INSERT FALLÓ para %s — Supabase no configurado o schema mismatch. "
                              "Verificar /velez/diag-supabase y ejecutar fix_data_lake_schema.sql", _cm_venue)
        except Exception as _cm_exc:
            log.error("climate_metrics write FAILED: %s", _cm_exc)
        # ── Solar: persistir métricas zonales derivadas de NASA GHI ──────────
        try:
            from velez_supabase import upsert_solar as _upsert_solar, upsert_sectores as _upsert_sec
            _solar_panels = _calculate_solar_metrics(raw.get("nasa", {}))
            if _solar_panels and _upsert_solar(_solar_panels):
                log.info("solar_metrics: %d zonas escritas a velez_solar", len(_solar_panels))
                # Upsert velez_sectores.solar con score/detalle computados (reemplaza string estático)
                _effs = [p["eficiencia_pct"] for p in _solar_panels
                         if isinstance(p.get("eficiencia_pct"), (int, float))]
                if _effs:
                    _eff_avg = round(sum(_effs) / len(_effs), 1)
                    _ghi = raw.get("nasa", {}).get("ALLSKY_SFC_SW_DWN")
                    _ghi_str = f"GHI {_ghi:.2f} kWh/m² · " if _ghi is not None else ""
                    _sem_sol = "verde" if _eff_avg >= 80 else ("amarillo" if _eff_avg >= 60 else "rojo")
                    _score_sol = max(0, min(100, int(round(_eff_avg))))
                    _upsert_sec({"solar": {
                        "nombre":     "Sistema Solar",
                        "score":      _score_sol,
                        "score_prev": _score_sol,   # sin histórico local — se mantiene
                        "sem":        _sem_sol,
                        "detalle":    f"{_ghi_str}Eficiencia modelo: {_eff_avg}% (NASA POWER + pvlib)",
                    }})
                    log.info("solar_metrics: velez_sectores.solar → score=%d eff=%.1f%%", _score_sol, _eff_avg)
                    # Stash into weather for push_weather_update → velez_data.json sectores.solar
                    _t_cells = [p["temperatura_c"] for p in _solar_panels
                                if isinstance(p.get("temperatura_c"), (int, float))]
                    weather["_solar_eff_pvlib"] = _eff_avg
                    weather["_solar_ghi_kwh"]   = float(_ghi) if _ghi is not None else None
                    weather["_solar_t_cell"]    = round(sum(_t_cells)/len(_t_cells), 1) if _t_cells else None
                    weather["_solar_pr_pvlib"]  = round(_eff_avg / 100.0, 3)
            elif not _solar_panels:
                log.warning("solar_metrics: GHI no disponible en NASA POWER — skip")
        except Exception as _sol_exc:
            log.warning("solar_metrics write (non-fatal): %s", _sol_exc)
        # ─────────────────────────────────────────────────────────────────────
        # ── acciones_engine: generar acciones Roger + KPIs dinámicos ────────
        try:
            import acciones_engine as _ae
            import hermes_velez as _hv
            # Merge heatmaps from vd with any Kalman estimates for the full picture
            _hm_base    = vd.get("usuarios", {}).get("roger", {}).get("heatmaps", {})
            _hm_kalman  = weather.get("_kalman_heatmaps", {})
            _heatmaps   = {**_hm_kalman, **_hm_base}  # real data takes priority

            # ── Hermes: auditoría científica por cancha ───────────────────────
            # Pasar S1 real si está disponible, sino revertir a Van Genuchten proxy
            _sar_proxy_hermes = None
            if weather.get("sar_disponible"):
                try:
                    if _s1_vv is not None:
                        _sar_proxy_hermes = _s1_vv  # SAR real Sentinel-1
                    else:
                        import math as _mh
                        _theta_h = max(0.046, min(0.409, float(weather.get("humedad_suelo_pct", 22)) / 100.0))
                        _s_soil_h = max(1e-4, (_theta_h - 0.12) / 0.28)
                        _sar_proxy_hermes = round(10.0 * _mh.log10(max(1e-9, _s_soil_h * 0.85)), 2)
                except Exception:
                    pass
            _hermes_results = _hv.audit_all_canchas(_heatmaps, sar_vv_db=_sar_proxy_hermes)
            for _cid, _res in _hermes_results.items():
                _heatmaps.setdefault(_cid, {})
                _heatmaps[_cid]["hermes_umbral_dinamico"] = _res.umbral_dinamico
                _heatmaps[_cid]["hermes_confianza"]       = _res.confianza
                _heatmaps[_cid]["hermes_status"]          = _res.status
                _heatmaps[_cid]["hermes_ndvi_fuente"]     = _res.ndvi_fuente
            if _hermes_results:
                log.info("hermes_velez: %d canchas auditadas", len(_hermes_results))
            # Calibración SAR una vez por proceso (no-op si < 20 pares históricos)
            try:
                for _cid_cal in ("amalfitani", "1fa", "1fp"):
                    _hv.try_calibrar_desde_supabase(_cid_cal)
            except Exception as _cal_err:
                log.debug("hermes_velez calibración (non-fatal): %s", _cal_err)

            _canchas_vd = (vd.get("sectores", {}).get("canchero", {}).get("canchas", [])
                           or vd.get("canchas", []))
            _phys_for_ae = weather.get("_physics_acciones_roger", [])
            _eye_ctx     = weather.get("_temporal_eye", {})
            _ae_acciones = _ae.generate_acciones(weather, _heatmaps, _phys_for_ae,
                                                  temporal_eye=_eye_ctx)
            _ae_kpis     = _ae.generate_kpis(_heatmaps, _canchas_vd, weather)
            weather["_field_acciones_roger"] = _ae_acciones
            weather["_roger_kpis"]           = _ae_kpis
            weather["_hermes_heatmaps"]      = _hermes_results  # persisted below
            weather.pop("_kalman_heatmaps", None)  # internal — not persisted in weather_live
            log.info("acciones_engine: %d acciones · %d kpis generados",
                     len(_ae_acciones), len(_ae_kpis))
        except Exception as _ae_err:
            log.warning("acciones_engine (non-fatal): %s", _ae_err)
        # ─────────────────────────────────────────────────────────────────────
        commit_url = push_weather_update(weather)
        # Paperclip: monitor inteligente del venue — al final del ciclo diario
        try:
            import sys as _sys, os as _os
            _here_pc   = _os.path.dirname(_os.path.abspath(__file__))
            _agents_pc = _os.path.join(_here_pc, "..", "..", "..", "agents")
            if _agents_pc not in _sys.path:
                _sys.path.insert(0, _agents_pc)
            from paperclip import analyze_venue as _pc_analyze
            _precip24 = float(raw.get("nasa", {}).get("PRECTOTCORR", 0.0))
            _wind_ms  = float(raw.get("nasa", {}).get("WS2M", 0.0))
            _c_ndvi   = (weather.get("gndvi_por_cancha", {})
                                .get("canchas", {})
                                .get("amalfitani", {})
                                .get("gndvi")) or _CANCHA_NDVI.get("amalfitani", 0.60)
            _pc_analyze(
                venue       = {"venue_id": "amalfitani", "nombre": "Estadio Amalfitani",
                               "lat": LAT, "lon": LON},
                weather_ctx = {"precipitation_24h": _precip24, "wind_max": _wind_ms * 3.6},
                ndvi_ctx    = {"baseline_ndvi": 0.60, "current_ndvi": _c_ndvi,
                               "drop": round(0.60 - _c_ndvi, 3)},
                sar_ctx     = {"sar_fuente": weather.get("sar_mensaje", "proxy"),
                               "is_egms_proxy": True},
            )
            log.info("paperclip: análisis completado para amalfitani")
        except Exception as _pce:
            log.warning("paperclip (non-fatal): %s", _pce)
        # ─────────────────────────────────────────────────────────────────────
        rf = weather.get("riesgo_fungosis", {})
        ts = weather["timestamp"]
        log.info(
            "✅ Datos actualizados — ET0=%.2f mm/día · déficit=%.1f mm · "
            "riesgo fungoso=%s · riego_fuente=%s · [%s]",
            weather.get("et0_mm_dia", 0),
            weather.get("deficit_hidrico_mm", 0),
            rf.get("nivel", "?"),
            weather.get("riego_min_fuente", "?"),
            ts,
        )
        gc.collect()
        return {"ok": True, "ts": ts, "commit": commit_url}
    except Exception as e:
        log.error("data_refresh FAILED: %s", e)
        return {"ok": False, "error": str(e)}


if __name__ == "__main__":
    import sys
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
    )
    result = run_refresh()
    if result.get("ok"):
        log.info("data_refresh OK: %s", result.get("ts"))
        sys.exit(0)
    else:
        log.error("data_refresh FAILED: %s", result.get("error"))
        sys.exit(1)
