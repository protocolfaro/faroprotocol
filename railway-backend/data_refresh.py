"""
data_refresh.py — Daily live weather refresh for Vélez panel.
Fetches NASA POWER, SoilGrids, Open-Meteo, ECOSTRESS/SMAP metadata.
Updates ONLY weather_live section of velez/velez_data.json in GitHub.
All other sections (heatmaps, IPOS, sector scores) are left untouched.
"""
from __future__ import annotations
import asyncio, base64, json, logging, math, os
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

_CANCHA_NDVI = {"1fa": 0.48, "2fa": 0.52, "3fa": 0.38, "4fa": 0.31}
_GNDVI_K     = {"1fa": 0.91, "2fa": 0.93, "3fa": 0.88, "4fa": 0.84}


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
    return result


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
        "&hourly=precipitation_probability,wind_speed_10m,et0_fao_evapotranspiration"
        ",soil_moisture_0_to_1cm,soil_moisture_1_to_3cm,temperature_2m,relative_humidity_2m"
        "&timezone=America%2FArgentina%2FBuenos_Aires&past_days=2&forecast_days=7"
    )
    return _fetch_json(url)


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
    Rs    = allsky_kwh * 3.6
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
    def _n(g):
        if g < 0.30: return "grave",      "Fertilizar URGENTE — deficiencia N grave"
        if g < 0.38: return "bajo",       "Fertilizar esta semana — déficit N moderado"
        if g < 0.45: return "borderline", "Fertilización preventiva recomendable"
        return          "ok",             "Nitrógeno adecuado"
    canchas = {}
    for cid, ndvi in _CANCHA_NDVI.items():
        gndvi = round(max(0, ndvi * _GNDVI_K[cid] - 0.01), 2)
        nst, nrec = _n(gndvi)
        canchas[cid] = {"gndvi": gndvi, "n_status": nst, "n_rec": nrec}
    return {"fuente": "estimado-ndvi", "canchas": canchas}


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
        base_canchas = ["1fa","3fa","2fa"]
        canchas_riesgo = list(dict.fromkeys(base_canchas + shadowed))
        return {"nivel":"alto","horas_favorables_48h":h_dollar,"horas_brown_patch_48h":h_brown,
                "descripcion":f"ALTO: {h_dollar}h Dollar Spot · {h_brown}h Brown Patch (48h)",
                "accion_recomendada":"Aplicar fungicida preventivo HOY",
                "canchas_en_riesgo":canchas_riesgo,"enfermedad":"Dollar Spot + Brown Patch"}
    if h_dollar >= 6 or h_brown >= 4:
        base_canchas = ["1fa","3fa"]
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

    sm_vals = [v for v in (h.get("soil_moisture_0_to_1cm") or [])[:24] if v is not None]
    hum_pct = round(sum(sm_vals)/len(sm_vals)*100, 1) if sm_vals else 18.0
    hum_est = ("seco" if hum_pct < 10 else "bajo" if hum_pct < 22
               else "normal" if hum_pct < 34 else "humedo")

    sm3  = [v for v in (h.get("soil_moisture_1_to_3cm") or [])[:24] if v is not None]
    hum3 = round(sum(sm3)/len(sm3)*100, 1) if sm3 else hum_pct

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
        "suelo_tipo": "Suelo pesado", "suelo_clay_pct": clay_pct, "suelo_sand_pct": sand_pct,
        "suelo_whc_mm": int(whc) if whc else 42,
        "gdd_acumulado_7d": gdd_7d, "gdd_rate_diario": gdd_rate,
        "dias_proximo_corte": dias_corte,
        "gndvi_por_cancha": _gndvi_per_cancha(),
        "riesgo_fungosis": _fungal_risk(h),
        "ecostress": ecostress, "smap": smap,
        "sar_disponible": False, "sar_mensaje": "Sin integración SAR activa",
        "costo_agua_ars": costo_agua,
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
    ts = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    sha, cfg = _gh_get_sha_and_content(_VD_PATH)
    cfg["weather_live"] = weather_live
    today_d = date.today()
    cfg.setdefault("meta", {})["fecha"]   = today_d.isoformat()
    cfg["meta"]["semana"] = (today_d - timedelta(days=today_d.weekday())).isoformat()
    cfg["updated_at"] = ts
    _update_tareas_dates(cfg)

    payload = {
        "message": f"data refresh: weather_live [{ts}]",
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

# insar_hyp3 sector_id → (velez_data.json sector key, display label)
_INSAR_SECTOR_MAP: dict[str, tuple[str, str]] = {
    "estadio":           ("estadio", "InSAR Estadio"),
    "poli_basquet":      ("poli",    "InSAR Básquet"),
    "poli_playon_norte": ("poli",    "InSAR Playón Norte"),
    "sede_anexo_norte":  ("sede",    "InSAR Anexo Norte"),
    "piletas":           ("piletas", "InSAR Piletas"),
}


def push_insar_update(insar_result: dict) -> str:
    """Update sectores.{key}.insar_mm and .detalle in velez_data.json and push to GitHub."""
    ts  = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    sha, cfg = _gh_get_sha_and_content(_VD_PATH)
    sectores  = cfg.setdefault("sectores", {})
    sector_mm = insar_result.get("sectores", {})

    # Aggregate poli (may have basquet + playon readings — use mean)
    poli_vals: list[tuple[float, str]] = []

    for insar_id, val_mm in sector_mm.items():
        json_key, label = _INSAR_SECTOR_MAP.get(insar_id, (None, None))
        if json_key is None:
            continue
        if json_key == "poli":
            poli_vals.append((val_mm, label))
            continue
        sec = sectores.setdefault(json_key, {})
        sec["insar_mm"] = val_mm
        sec["detalle"]  = f"{label}: {val_mm:+.2f} mm"

    if poli_vals:
        mean_mm   = round(sum(v for v, _ in poli_vals) / len(poli_vals), 2)
        poli_label = poli_vals[0][1]
        poli_sec   = sectores.setdefault("poli", {})
        poli_sec["insar_mm"] = mean_mm
        poli_sec["detalle"]  = f"{poli_label}: {mean_mm:+.2f} mm"

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

        # Read velez_data.json for shadow_maps and config_velez.json for aspersores
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
        weather["riesgo_fungosis"] = _fungal_risk(hourly_h, shadow_pct_by_cancha)

        try:
            ndvi_data = ndvi_real.fetch_ndvi()
            if ndvi_data:
                weather["gndvi_por_cancha"] = ndvi_data
        except Exception as _ndvi_err:
            log.warning("ndvi_real (non-fatal): %s", _ndvi_err)
        commit_url = push_weather_update(weather)
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
        return {"ok": True, "ts": ts, "commit": commit_url}
    except Exception as e:
        log.error("data_refresh FAILED: %s", e)
        return {"ok": False, "error": str(e)}
