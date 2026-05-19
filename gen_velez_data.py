"""
gen_velez_data.py v3.0 — Multi-source live data pipeline
Sources: NASA POWER · ISRIC SoilGrids · Open-Meteo hourly · CONAE SAR (best-effort)
All calls in parallel, 10-second timeout each, everything in memory.
"""

import json, os, sys, math, copy, logging
from datetime import date, timedelta, datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.request import urlopen, Request
from urllib.parse import urlencode
from urllib.error import URLError

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s", stream=sys.stdout)
log = logging.getLogger(__name__)

LAT, LON, ELEV_M = -34.6375, -58.5215, 25
API_TIMEOUT = 10
FECHA_EMISION = date.today().isoformat()

# ── SECTOR DATA ───────────────────────────────────────────────────────────────
SECTOR_DATA = {
    "estadio": {
        "nombre": "Estadio J. Amalfitani",
        "score": 78, "score_prev": 74,
        "sem": "amarillo",
        "detalle": "NDVI cubierta: 0.61 · InSAR: 0.22 mm",
    },
    "agro": {
        "nombre": "Área Agronómica",
        "score": 82, "score_prev": 80,
        "sem": "amarillo",
        "detalle": "NDVI campo norte: 0.58 · riego activo",
    },
    "solar": {
        "nombre": "Sistema Solar",
        "score": 71, "score_prev": 75,
        "sem": "amarillo",
        "detalle": "Eficiencia: 71% · 3 paneles en falla",
    },
    "canchero": {
        "nombre": "Villa Olímpica",
        "score": 59, "score_prev": 63,
        "sem": "rojo",
        "detalle": "Cancha 4: fungicida urgente · NDVI: 0.31",
        "canchas": [
            {"id":"c1","nombre":"Cancha 1","score":68,"score_prev":72,"sem":"amarillo","ndvi":0.48,"ndvi_prev":0.52,"detalle":"Focos fungosos leve · tratamiento preventivo"},
            {"id":"c2","nombre":"Cancha 2","score":75,"score_prev":70,"sem":"amarillo","ndvi":0.52,"ndvi_prev":0.48,"detalle":"Fertilización requerida · NDVI estable"},
            {"id":"c3","nombre":"Cancha 3","score":55,"score_prev":61,"sem":"rojo","ndvi":0.38,"ndvi_prev":0.44,"detalle":"Focos fungosos activos · fungicida urgente"},
            {"id":"c4","nombre":"Cancha 4","score":31,"score_prev":42,"sem":"rojo","ndvi":0.31,"ndvi_prev":0.38,"detalle":"CRÍTICO · fungicida + drenaje + resembrado"},
        ],
    },
    "sede": {
        "nombre": "Sede Central",
        "score": 75, "score_prev": 75,
        "sem": "amarillo",
        "detalle": "InSAR Anexo Norte: 0.55 mm · Landsat: 41.2°C",
    },
    "poli": {
        "nombre": "Polideportivo Feijóo",
        "score": 39, "score_prev": 44,
        "sem": "rojo",
        "detalle": "Básquet InSAR: 0.85 mm · Playón: 0.72 mm",
    },
    "piletas": {
        "nombre": "Complejo Acuático",
        "score": 91, "score_prev": 89,
        "sem": "verde",
        "detalle": "Calidad agua OK · InSAR: 0.35 mm",
    },
}

HISTORIAL_GLOBAL = [
    {"semana":"S-5","score":65,"sem":"amarillo"},
    {"semana":"S-4","score":68,"sem":"amarillo"},
    {"semana":"S-3","score":72,"sem":"amarillo"},
    {"semana":"S-2","score":69,"sem":"amarillo"},
    {"semana":"S-1","score":71,"sem":"amarillo"},
    {"semana":"HOY","score":70,"sem":"amarillo"},
]

PROXIMO_PARTIDO_ROGER = {
    "fecha": "2026-05-24",
    "rival": "Independiente",
    "tipo": "local",
    "cancha": "Estadio José Amalfitani",
    "checklist": [
        "Corte de cancha día previo",
        "Marcación de líneas",
        "Riego final (2 h antes del partido)",
        "Revisión arcos y redes",
        "Banderines de córner",
        "Vestuarios y accesos habilitados",
    ],
}

_TAREAS_BASE = [
    ["Aplicar fungicida C4 zona central y lateral sur", "Verificar drenaje lateral C4"],
    ["Regar C3 y C4: 25 min por sector", "Fungicida preventivo C1/C3"],
    ["Cortar C2 y C4 temprano (7-9 hs)", "Fertilizar C2: 20 kg N/ha"],
    ["Sembrar C4 zona central (85 m²)", "Regar C1 y C3"],
    ["Aerificar porterías Amalfitani", "Preparación previa partido: corte y riego"],
    ["Marcar líneas estadio", "Riego final: 2 h antes del partido"],
    ["PARTIDO vs Independiente — estadio abierto 2 h antes",
     "Post-partido: revisión cancha y sistema de riego"],
]
_DIA_NOMBRES = ["Lun", "Mar", "Mié", "Jue", "Vie", "Sáb", "Dom"]


def _build_roger_calendar() -> list:
    today = date.today()
    monday = today - timedelta(days=today.weekday())
    return [
        {
            "dia_offset": i,
            "dia_nombre": _DIA_NOMBRES[i],
            "fecha_iso": (monday + timedelta(days=i)).isoformat(),
            "tareas": _TAREAS_BASE[i],
            "es_partido": (monday + timedelta(days=i)).isoformat() == PROXIMO_PARTIDO_ROGER["fecha"],
        }
        for i in range(7)
    ]


_ALL = ["estadio", "agro", "solar", "canchero", "sede", "poli", "piletas"]

USUARIO_CONFIG = {
    "roger": {
        "nombre": "Roger Bernal",
        "tipo": "canchero",
        "sectores": ["canchero"],
        "sort_by_score": False,
        "proximo_partido": PROXIMO_PARTIDO_ROGER,
        "kpis": [
            {"label":"NDVI C4",     "value":"0.31","value_prev":"0.38","unit":"","sub":"CRÍTICO — resembrar",    "sem":"rojo"},
            {"label":"NDVI C1/C3",  "value":"0.38","value_prev":"0.44","unit":"","sub":"Focos fungosos activos", "sem":"rojo"},
            {"label":"NDVI C2",     "value":"0.52","value_prev":"0.48","unit":"","sub":"Fertilización requerida","sem":"amarillo"},
            {"label":"Score Fusión","value":"59",  "value_prev":"63",  "unit":"","sub":"Promedio 4 canchas",     "sem":"rojo"},
        ],
        "acciones": [
            "Cancha 4: intervención URGENTE — fungicida + drenaje + resembrar HOY",
            "Canchas 1 y 3: aplicar fungicida activo — focos exactos en reporte PDF",
            "Cancha 2: fertilizar 20 kg N/ha uniformemente esta semana",
        ],
    },
    "juan": {
        "nombre": "Juan González",
        "tipo": "intendente",
        "sectores": ["canchero", "agro", "poli"],
        "sort_by_score": True,
        "resumen_ejecutivo": [
            "2 sectores en rojo: Poli Básquet (InSAR 0.85 mm) y Villa Olímpica C4 — acción urgente esta semana.",
            "Sistema solar al 71% de eficiencia — 3 paneles en falla, pérdida de 4.2 kWp activa.",
            "Área agronómica estable (NDVI 0.58). Complejo acuático y estadio sin intervención inmediata.",
        ],
        "presupuesto_urgente": {
            "moneda": "ARS",
            "total": 180000,
            "items": [
                {"concepto":"Fungicida + insumos resembrado C4",     "monto":85000,"sem":"rojo"},
                {"concepto":"Inspección estructural Básquet Feijóo", "monto":45000,"sem":"rojo"},
                {"concepto":"Limpieza y revisión 7 paneles solares", "monto":32000,"sem":"amarillo"},
                {"concepto":"Reparación drenaje lateral C4",         "monto":18000,"sem":"amarillo"},
            ],
        },
        "sectores_autorizacion": [
            {"sector":"Villa Olímpica","accion":"Compra fungicida + insumos resembrado","monto":85000,"urgencia":"HOY"},
            {"sector":"Poli Feijóo",   "accion":"Contratar empresa inspección estructural","monto":45000,"urgencia":"esta-sem"},
        ],
        "kpis": [
            {"label":"InSAR Básquet","value":"0.85","value_prev":"0.70","unit":"mm","sub":"SUPERA umbral 0.8 mm","sem":"rojo"},
            {"label":"NDVI C4",      "value":"0.31","value_prev":"0.38","unit":"",  "sub":"Cancha crítica",      "sem":"rojo"},
            {"label":"NDVI Agro",    "value":"0.58","value_prev":"0.56","unit":"",  "sub":"Riego activo OK",     "sem":"amarillo"},
            {"label":"Score Poli",   "value":"39",  "value_prev":"44",  "unit":"",  "sub":"Atención urgente",    "sem":"rojo"},
        ],
        "acciones": [
            "Poli Básquet: inspección fisuras + drenaje URGENTE (InSAR 0.85 mm)",
            "Cancha 4: intervención urgente — fungicida + drenaje esta semana",
            "Agro: riego preventivo — monitorear NDVI campo norte",
        ],
    },
    "banchero": {
        "nombre": "Fernando Banchero",
        "tipo": "ejecutivo",
        "sectores": _ALL,
        "sort_by_score": True,
        "acciones": [
            "Poli + Villa Olímpica C4: intervenciones urgentes esta semana",
            "Sistema Solar: mantenimiento técnico + limpieza paneles degradados",
            "Sede Anexo Norte + Piletas Techo: inspección térmica preventiva",
        ],
        "kpis": [
            {"label":"Sectores OK",    "value":"2", "unit":"/7","sub":"Verde: piletas, estadio",     "sem":"verde"},
            {"label":"Sectores Alerta","value":"3", "unit":"/7","sub":"Amarillo: agro, solar, sede", "sem":"amarillo"},
            {"label":"Sectores Crit.", "value":"2", "unit":"/7","sub":"Rojo: canchero, poli",        "sem":"rojo"},
            {"label":"Score Global",   "value":"70","unit":"",  "sub":"Promedio 7 sectores",         "sem":"amarillo"},
        ],
    },
    "pait": {
        "nombre": "Sebastián Pait",
        "tipo": "intendente",
        "sectores": ["canchero", "agro", "poli"],
        "sort_by_score": True,
        "resumen_ejecutivo": [
            "Cancha 4 y Poli Básquet en estado crítico — intervención urgente requerida.",
            "Cancha 2 y Área Agronómica en condiciones aptas para uso normal.",
            "Poli Playón Norte: evaluar antes de actividades de alto impacto.",
        ],
        "presupuesto_urgente": {
            "moneda": "ARS",
            "total": 130000,
            "items": [
                {"concepto":"Fungicida + resembrado C4",     "monto":85000,"sem":"rojo"},
                {"concepto":"Inspección estructural Básquet","monto":45000,"sem":"rojo"},
            ],
        },
        "sectores_autorizacion": [
            {"sector":"Villa Olímpica","accion":"Compra fungicida + resembrado",      "monto":85000,"urgencia":"HOY"},
            {"sector":"Poli Feijóo",   "accion":"Inspección estructural Básquet",     "monto":45000,"urgencia":"esta-sem"},
        ],
        "acciones": [
            "Cancha 4: NO APTA — hongo activo + drenaje roto + pasto ralo",
            "Poli Básquet: evaluar antes de uso intensivo (InSAR 0.85 mm)",
            "Cancha 2 y Campo Agro: estado óptimo — sin restricciones",
        ],
        "kpis": [
            {"label":"Canchas Aptas","value":"2",   "unit":"/4","sub":"C2 y campo OK",        "sem":"amarillo"},
            {"label":"NDVI C4",      "value":"0.31","unit":"",  "sub":"NO APTA — crítica",    "sem":"rojo"},
            {"label":"NDVI Agro",    "value":"0.58","unit":"",  "sub":"Uso normal",           "sem":"amarillo"},
            {"label":"InSAR Básquet","value":"0.85","unit":"mm","sub":"Evaluar antes de uso", "sem":"rojo"},
        ],
    },
    "berlanga": {
        "nombre": "Fabián Berlanga",
        "tipo": "ejecutivo",
        "sectores": _ALL,
        "sort_by_score": True,
        "acciones": [
            "Poli + Canchero: sectores críticos — intervención esta semana",
            "Solar: eficiencia por debajo de objetivo — revisión técnica",
            "Piletas: estado óptimo — mantener protocolo de calidad",
        ],
        "kpis": [
            {"label":"Score Global", "value":"70","unit":"", "sub":"Predio completo",       "sem":"amarillo"},
            {"label":"Alertas Rojas","value":"2", "unit":"", "sub":"Poli + Villa Olímpica", "sem":"rojo"},
            {"label":"Efic. Solar",  "value":"71","unit":"%","sub":"Objetivo: ≥85%",        "sem":"amarillo"},
            {"label":"Piletas",      "value":"91","unit":"", "sub":"Calidad agua excelente","sem":"verde"},
        ],
    },
    "nelson": {
        "nombre": "Nelson Pugliese",
        "tipo": "ejecutivo",
        "sectores": _ALL,
        "sort_by_score": True,
        "acciones": [
            "Poli + Villa Olímpica: acciones estructurales urgentes esta semana",
            "Sistema Solar: 3 paneles en falla — pérdida 4.2 kWp activa",
            "Sede + Piletas: monitoreo térmico continuo — sin acción urgente",
        ],
        "kpis": [
            {"label":"Score Global",   "value":"70",  "unit":"",   "sub":"Promedio 7 sectores",     "sem":"amarillo"},
            {"label":"Alertas Activas","value":"4",   "unit":"",   "sub":"2 rojas · 2 amarillas",   "sem":"rojo"},
            {"label":"Sectores",       "value":"7",   "unit":"",   "sub":"100% con cobertura",      "sem":"verde"},
            {"label":"Solar kWp",      "value":"18.4","unit":"kWp","sub":"Pérdida activa: 4.2 kWp", "sem":"amarillo"},
        ],
    },
    "aveleyra": {
        "nombre": "Alberto Aveleyra",
        "tipo": "ejecutivo",
        "sectores": _ALL,
        "sort_by_score": True,
        "acciones": [
            "Poli + Canchero: intervenciones urgentes — impacto directo en operación",
            "Solar: revisar paneles en falla esta semana — pérdida económica activa",
            "80% de problemas identificados en etapa temprana — detección anticipada",
        ],
        "kpis": [
            {"label":"Score Predio",   "value":"70","unit":"", "sub":"Global ponderado",     "sem":"amarillo"},
            {"label":"Score Piletas",  "value":"91","unit":"", "sub":"Sin acción inmediata", "sem":"verde"},
            {"label":"Efic. Solar",    "value":"71","unit":"%","sub":"Pérdida: 4.2 kWp/sem", "sem":"amarillo"},
            {"label":"Alertas Estruc.","value":"1", "unit":"", "sub":"InSAR crítico — poli", "sem":"rojo"},
        ],
    },
    "admin": {
        "nombre": "Administración General",
        "tipo": "ejecutivo",
        "sectores": _ALL,
        "sort_by_score": True,
        "acciones": [
            "Poli Básquet + Villa Olímpica C4: intervenciones urgentes esta semana",
            "Sistema Solar: mantenimiento técnico + limpieza paneles degradados",
            "Sede Anexo Norte + Piletas Techo: inspección térmica preventiva",
        ],
        "kpis": [
            {"label":"Sectores OK",    "value":"2", "unit":"/7","sub":"Verde: piletas, estadio",     "sem":"verde"},
            {"label":"Sectores Alerta","value":"3", "unit":"/7","sub":"Amarillo: agro, solar, sede", "sem":"amarillo"},
            {"label":"Sectores Crit.", "value":"2", "unit":"/7","sub":"Rojo: canchero, poli",        "sem":"rojo"},
            {"label":"Score Global",   "value":"70","unit":"",  "sub":"Promedio 7 sectores",         "sem":"amarillo"},
        ],
    },
}


# ── LIVE DATA — FETCH ─────────────────────────────────────────────────────────

def _fetch_json(url: str, timeout: int = API_TIMEOUT) -> dict:
    try:
        req = Request(url, headers={"User-Agent": "FaroProtocol/3.0 (protocolfaro@gmail.com)"})
        with urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as exc:
        log.warning("fetch %s: %s", url[:70], exc)
        return {}


def _fetch_nasa_power() -> dict:
    """Avg of last 7 days: EVPTRNS, T2M_MAX, T2M_MIN, PRECTOTCORR, RH2M, WS2M, ALLSKY_SFC_SW_DWN"""
    end = date.today() - timedelta(days=1)
    start = end - timedelta(days=6)
    url = (
        "https://power.larc.nasa.gov/api/temporal/daily/point?"
        + urlencode({
            "start": start.strftime("%Y%m%d"),
            "end": end.strftime("%Y%m%d"),
            "latitude": LAT,
            "longitude": LON,
            "community": "AG",
            "parameters": "EVPTRNS,T2M_MAX,T2M_MIN,PRECTOTCORR,RH2M,WS2M,ALLSKY_SFC_SW_DWN",
            "format": "JSON",
        })
    )
    raw = _fetch_json(url)
    params = raw.get("properties", {}).get("parameter", {})
    result = {}
    for key, vals in params.items():
        valid = [v for v in vals.values() if v is not None and v != -999 and v > -900]
        if valid:
            result[key] = round(sum(valid) / len(valid), 3)
    return result


def _fetch_soilgrids() -> dict:
    """clay%, sand%, silt%, bdod (g/cm³), WHC estimate (mm/30cm)."""
    url = (
        "https://rest.isric.org/soilgrids/v2.0/properties/query"
        f"?lon={LON}&lat={LAT}"
        "&property=clay&property=sand&property=silt&property=bdod"
        "&property=wv0033&property=wv1500"
        "&depth=0-5cm&value=mean"
    )
    raw = _fetch_json(url, timeout=12)
    result = {}
    for layer in raw.get("properties", {}).get("layers", []):
        name = layer["name"]
        d_factor = (layer.get("unit_measure") or {}).get("d_factor") or 1
        for depth_obj in layer.get("depths", []):
            mv = depth_obj.get("values", {}).get("mean")
            if mv is not None:
                result[name] = mv / d_factor
    # Estimate WHC for 30cm grass root zone
    fc = result.get("wv0033", 0)
    wp = result.get("wv1500", 0)
    fc_frac = fc / 100 if fc > 1 else fc
    wp_frac = wp / 100 if wp > 1 else wp
    result["fc_pct"] = round(fc_frac * 100, 1)
    result["wp_pct"] = round(wp_frac * 100, 1)
    result["whc_mm"] = round(max(0, fc_frac - wp_frac) * 300)
    return result


def _fetch_open_meteo_hourly() -> dict:
    """72-hour forecast with ET0 FAO, soil moisture, wind, precip probability."""
    url = (
        "https://api.open-meteo.com/v1/forecast"
        f"?latitude={LAT}&longitude={LON}"
        "&hourly=precipitation_probability,wind_speed_10m,et0_fao_evapotranspiration"
        ",soil_moisture_0_to_1cm,soil_temperature_0cm"
        "&timezone=America%2FArgentina%2FBuenos_Aires"
        "&forecast_days=3&wind_speed_unit=kmh"
    )
    return _fetch_json(url)


def _fetch_conae() -> dict:
    """Best-effort check for recent SAOCOM SAR imagery over Vélez."""
    base = "https://catalogos.conae.gov.ar"
    # Check STAC root
    root = _fetch_json(f"{base}/stac/v1", timeout=8)
    if not root:
        log.info("CONAE STAC no disponible")
        return {"disponible": False, "mensaje": "Catálogo CONAE no respondió (timeout)"}
    # Try item search
    start_dt = (date.today() - timedelta(days=7)).isoformat() + "T00:00:00Z"
    end_dt   = date.today().isoformat() + "T23:59:59Z"
    search_url = (
        f"{base}/stac/v1/search"
        f"?bbox={LON-0.15},{LAT-0.15},{LON+0.15},{LAT+0.15}"
        f"&datetime={start_dt}/{end_dt}&limit=1"
    )
    search = _fetch_json(search_url, timeout=8)
    features = search.get("features", [])
    if features:
        feat = features[0]
        fecha_img = (feat.get("properties") or {}).get("datetime", "")[:10]
        col_id = feat.get("collection", "SAR")
        return {"disponible": True, "mensaje": f"Imagen {col_id}: {fecha_img}", "fecha": fecha_img}
    return {"disponible": False, "mensaje": "Sin imagen SAR en los últimos 7 días"}


# ── LIVE DATA — COMPUTE ───────────────────────────────────────────────────────

def _penman_monteith(tmax: float, tmin: float, rh: float, ws2m: float,
                     allsky_kwh: float, doy: int) -> float:
    """FAO-56 Penman-Monteith ETo (mm/day)."""
    T = (tmax + tmin) / 2
    es = (0.6108 * math.exp(17.27 * tmax / (tmax + 237.3)) +
          0.6108 * math.exp(17.27 * tmin / (tmin + 237.3))) / 2
    ea = es * max(0, rh) / 100
    delta = 4098 * 0.6108 * math.exp(17.27 * T / (T + 237.3)) / (T + 237.3) ** 2
    P = 101.3 * ((293 - 0.0065 * ELEV_M) / 293) ** 5.26
    gamma = 0.000665 * P
    Rs = allsky_kwh * 3.6  # kWh/m²/day → MJ/m²/day
    lat_r = math.radians(LAT)
    dr = 1 + 0.033 * math.cos(2 * math.pi / 365 * doy)
    decl = 0.409 * math.sin(2 * math.pi / 365 * doy - 1.39)
    ws_a = math.acos(max(-1.0, min(1.0, -math.tan(lat_r) * math.tan(decl))))
    Ra = (24 * 60 / math.pi * 0.0820 * dr
          * (ws_a * math.sin(lat_r) * math.sin(decl)
             + math.cos(lat_r) * math.cos(decl) * math.sin(ws_a)))
    Rso = (0.75 + 2e-5 * ELEV_M) * Ra
    Rns = (1 - 0.23) * Rs
    sigma = 4.903e-9
    Rnl = (sigma * ((tmax + 273.16) ** 4 + (tmin + 273.16) ** 4) / 2
           * (0.34 - 0.14 * math.sqrt(max(0, ea)))
           * (1.35 * min(1.5, Rs / max(0.01, Rso)) - 0.35))
    Rn = Rns - Rnl
    num = 0.408 * delta * Rn + gamma * (900 / (T + 273)) * ws2m * (es - ea)
    den = delta + gamma * (1 + 0.34 * ws2m)
    return max(0.0, round(num / den, 2))


def _classify_soil(clay: float, sand: float) -> str:
    silt = max(0, 100 - clay - sand)
    if sand >= 70:               return "Arenoso"
    if clay >= 45:               return "Arcilloso"
    if clay >= 35:               return "Arcilloso-Limoso" if silt >= 40 else "Franco Arcilloso"
    if clay >= 25:               return "Franco Arcilloso-Limoso" if silt >= 40 else "Franco Arcilloso"
    if silt >= 50:               return "Limoso"
    if clay >= 20:               return "Franco"
    if sand >= 50:               return "Franco Arenoso"
    return "Franco"


def _best_riego_hour(h: dict) -> tuple:
    """Returns (hora_str, dia_offset) for optimal irrigation. Prefer 05-08, wind<10, rain<20%."""
    times = h.get("time", [])
    winds = h.get("wind_speed_10m", [])
    probs = h.get("precipitation_probability", [])
    for i, t in enumerate(times[:72]):
        hr = int(t[11:13]) if len(t) >= 13 else -1
        if 5 <= hr <= 8:
            w = winds[i] if i < len(winds) else 99
            p = probs[i] if i < len(probs) else 99
            if w is not None and p is not None and w < 10 and p < 20:
                return f"{hr:02d}:00", i // 24
    return "06:00", 1  # default tomorrow morning


def _best_corte_hour(h: dict) -> str:
    """Returns hora_str for optimal mowing. Prefer 06-09, wind<15, rain<30%."""
    times = h.get("time", [])
    winds = h.get("wind_speed_10m", [])
    probs = h.get("precipitation_probability", [])
    for i, t in enumerate(times[:72]):
        hr = int(t[11:13]) if len(t) >= 13 else -1
        if 6 <= hr <= 9:
            w = winds[i] if i < len(winds) else 99
            p = probs[i] if i < len(probs) else 99
            if w is not None and p is not None and w < 15 and p < 30:
                return f"{hr:02d}:00"
    return "07:00"


def _compute_weather_live(nasa: dict, soil: dict, hourly_resp: dict, conae: dict) -> dict:
    h = hourly_resp.get("hourly", {})

    # ── ETo ──────────────────────────────────────────────────────────────────
    et0_nasa = nasa.get("EVPTRNS")
    et0_pm = None
    if all(k in nasa for k in ("T2M_MAX", "T2M_MIN", "RH2M", "WS2M", "ALLSKY_SFC_SW_DWN")):
        doy = date.today().timetuple().tm_yday
        try:
            et0_pm = _penman_monteith(
                nasa["T2M_MAX"], nasa["T2M_MIN"], nasa["RH2M"],
                nasa["WS2M"], nasa["ALLSKY_SFC_SW_DWN"], doy
            )
        except Exception as e:
            log.warning("PM calc error: %s", e)

    if et0_nasa and et0_nasa > 0:
        et0, et0_fuente = round(et0_nasa, 2), "nasa-power"
        if et0_pm:
            # Blend: 60% NASA, 40% PM
            et0 = round(0.6 * et0_nasa + 0.4 * et0_pm, 2)
            et0_fuente = "nasa+penman-monteith"
    elif et0_pm:
        et0, et0_fuente = et0_pm, "penman-monteith"
    else:
        et0, et0_fuente = 3.2, "estimado"  # seasonal avg Buenos Aires May

    # ── Weekly balance ────────────────────────────────────────────────────────
    prec_avg = nasa.get("PRECTOTCORR", 1.2)
    et0_sem  = round(et0 * 7, 1)
    prec_sem = round(prec_avg * 7, 1)
    deficit  = round(max(0.0, et0_sem - prec_sem), 1)

    # ── Soil humidity from Open-Meteo hourly ─────────────────────────────────
    sm_vals = [v for v in (h.get("soil_moisture_0_to_1cm") or [])[:24] if v is not None]
    if sm_vals:
        sm_avg   = sum(sm_vals) / len(sm_vals)
        hum_pct  = round(sm_avg * 100, 1)
        hum_est  = ("seco" if sm_avg < 0.10 else "bajo" if sm_avg < 0.22
                    else "normal" if sm_avg < 0.34 else "humedo")
    else:
        hum_pct, hum_est = 18.0, "bajo"

    # ── Optimal hours ─────────────────────────────────────────────────────────
    hora_riego, dia_riego = _best_riego_hour(h)
    hora_corte            = _best_corte_hour(h)

    # ── Soil type from SoilGrids ──────────────────────────────────────────────
    clay_pct = round(soil.get("clay", 32), 1)
    sand_pct = round(soil.get("sand", 28), 1)
    suelo    = _classify_soil(clay_pct, sand_pct)
    whc      = soil.get("whc_mm", 42)

    # ── Irrigation duration (10 mm/h sprinkler, split across 2 sessions) ─────
    riego_min = max(10, round(deficit / 0.167 / 2)) if deficit > 3 else 0

    # ── Water cost for 4 canchas × 7000 m² ───────────────────────────────────
    m3 = deficit * 28_000 / 1000
    costo_agua = int(round(m3 * 200 / 500) * 500) if deficit > 3 else 0

    fuentes = [k for k, d in [("nasa-power", nasa), ("soilgrids", soil), ("open-meteo-hourly", h)] if d]

    return {
        "timestamp":             datetime.now().isoformat(timespec="seconds"),
        "fuentes":               fuentes,
        "et0_mm_dia":            et0,
        "et0_fuente":            et0_fuente,
        "et0_semana_mm":         et0_sem,
        "precipitacion_semana_mm": prec_sem,
        "deficit_hidrico_mm":    deficit,
        "deficit_hidrico":       deficit > 5,
        "litros_m2_semana":      deficit,
        "riego_min_sector":      riego_min,
        "hora_riego_optima":     hora_riego,
        "dia_riego_offset":      dia_riego,
        "hora_corte_optima":     hora_corte,
        "humedad_suelo_pct":     hum_pct,
        "humedad_suelo_estado":  hum_est,
        "suelo_tipo":            suelo,
        "suelo_clay_pct":        clay_pct,
        "suelo_sand_pct":        sand_pct,
        "suelo_whc_mm":          int(whc) if whc else 42,
        "sar_disponible":        conae.get("disponible", False),
        "sar_mensaje":           conae.get("mensaje", "Sin datos SAR"),
        "costo_agua_ars":        costo_agua,
    }


def _fetch_all_live() -> tuple:
    """Returns (nasa, soil, hourly, conae) with parallel 10s-timeout calls."""
    tasks = {
        "nasa":   _fetch_nasa_power,
        "soil":   _fetch_soilgrids,
        "hourly": _fetch_open_meteo_hourly,
        "conae":  _fetch_conae,
    }
    res = {k: {} for k in tasks}
    with ThreadPoolExecutor(max_workers=4) as pool:
        futs = {pool.submit(fn): key for key, fn in tasks.items()}
        try:
            for fut in as_completed(futs, timeout=20):
                key = futs[fut]
                try:
                    res[key] = fut.result()
                    log.info("  OK %s", key)
                except Exception as e:
                    log.warning("  FAIL %s: %s", key, e)
        except Exception:
            pass
    return res["nasa"], res["soil"], res["hourly"], res["conae"]


# ── BUILD ─────────────────────────────────────────────────────────────────────

def build_velez_data(fecha: str = None, live: bool = True) -> dict:
    f = fecha or FECHA_EMISION
    usuarios = copy.deepcopy(USUARIO_CONFIG)
    usuarios["roger"]["tareas_semana"] = _build_roger_calendar()

    weather_live = {}
    if live:
        log.info("Fetching live data sources…")
        try:
            nasa, soil, hourly, conae = _fetch_all_live()
            weather_live = _compute_weather_live(nasa, soil, hourly, conae)
            log.info("weather_live: ET0=%.2f (%s) déficit=%.1f mm suelo=%s",
                     weather_live["et0_mm_dia"], weather_live["et0_fuente"],
                     weather_live["deficit_hidrico_mm"], weather_live["suelo_tipo"])

            # Inject water cost into Juan + Pait budget if deficit
            if weather_live.get("deficit_hidrico") and weather_live.get("costo_agua_ars", 0) > 0:
                deficit_mm = weather_live["deficit_hidrico_mm"]
                costo      = weather_live["costo_agua_ars"]
                for slug in ("juan", "pait"):
                    u = usuarios[slug]
                    u["presupuesto_urgente"]["items"].append({
                        "concepto": f"Riego déficit hídrico — {deficit_mm:.1f} mm · 4 canchas",
                        "monto": costo,
                        "sem": "amarillo",
                    })
                    u["presupuesto_urgente"]["total"] += costo

        except Exception as e:
            log.error("live data pipeline error: %s", e)

    return {
        "meta": {
            "version": "3.0",
            "fecha": f,
            "cliente": "Club Atletico Velez Sarsfield",
            "coords": {"lat": LAT, "lon": LON},
            "historial_global": HISTORIAL_GLOBAL,
        },
        "weather_live": weather_live,
        "sectores": SECTOR_DATA,
        "usuarios": usuarios,
    }


def write_velez_data(output_path: str = None, fecha: str = None, live: bool = True) -> str:
    if output_path is None:
        here = os.path.dirname(os.path.abspath(__file__))
        output_path = os.path.join(here, "velez", "velez_data.json")
    data = build_velez_data(fecha, live=live)
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, separators=(",", ":"))
    return output_path


if __name__ == "__main__":
    if "--stdout" in sys.argv:
        print(json.dumps(build_velez_data(), ensure_ascii=False, indent=2))
    elif "--no-live" in sys.argv:
        path = write_velez_data(live=False)
        print(f"OK (static)  {path}  ({os.path.getsize(path)} bytes)")
    else:
        path = write_velez_data()
        print(f"OK  {path}  ({os.path.getsize(path)} bytes)")
