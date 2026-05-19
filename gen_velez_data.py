"""
gen_velez_data.py v3.0 — Multi-source live data pipeline
Sources: NASA POWER · ISRIC SoilGrids · Open-Meteo hourly · CONAE SAR (best-effort)
All calls in parallel, 10-second timeout each, everything in memory.
"""

import asyncio
import json, os, sys, math, copy, logging
from datetime import date, timedelta, datetime
from concurrent.futures import ThreadPoolExecutor
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
        "detalle": "4FA: fungicida urgente · NDVI: 0.31",
        "canchas": [
            {"id":"1fa","nombre":"1FA","score":68,"score_prev":72,"sem":"amarillo","ndvi":0.48,"ndvi_prev":0.52,"detalle":"Focos fungosos leve · tratamiento preventivo"},
            {"id":"2fa","nombre":"2FA","score":75,"score_prev":70,"sem":"amarillo","ndvi":0.52,"ndvi_prev":0.48,"detalle":"Fertilización requerida · NDVI estable"},
            {"id":"3fa","nombre":"3FA","score":55,"score_prev":61,"sem":"rojo","ndvi":0.38,"ndvi_prev":0.44,"detalle":"Focos fungosos activos · fungicida urgente"},
            {"id":"4fa","nombre":"4FA","score":31,"score_prev":42,"sem":"rojo","ndvi":0.31,"ndvi_prev":0.38,"detalle":"CRÍTICO · fungicida + drenaje + resembrado"},
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
    ["Aplicar fungicida 4FA zona central y lateral sur", "Verificar drenaje lateral 4FA"],
    ["Regar 3FA y 4FA: 25 min por sector", "Fungicida preventivo 1FA/3FA"],
    ["Cortar 2FA y 4FA temprano (7-9 hs)", "Fertilizar 2FA: 20 kg N/ha"],
    ["Sembrar 4FA zona central (85 m²)", "Regar 1FA y 3FA"],
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
            {"label":"NDVI 4FA",     "value":"0.31","value_prev":"0.38","unit":"","sub":"CRÍTICO — resembrar",    "sem":"rojo"},
            {"label":"NDVI 1FA/3FA",  "value":"0.38","value_prev":"0.44","unit":"","sub":"Focos fungosos activos", "sem":"rojo"},
            {"label":"NDVI 2FA",     "value":"0.52","value_prev":"0.48","unit":"","sub":"Fertilización requerida","sem":"amarillo"},
            {"label":"Score Fusión","value":"59",  "value_prev":"63",  "unit":"","sub":"Promedio 4 canchas",     "sem":"rojo"},
        ],
        "acciones": [
            "4FA: intervención URGENTE — fungicida + drenaje + resembrar HOY",
            "1FA y 3FA: aplicar fungicida activo — focos exactos en reporte PDF",
            "2FA: fertilizar 20 kg N/ha uniformemente esta semana",
        ],
    },
    "juan": {
        "nombre": "Juan González",
        "tipo": "intendente",
        "sectores": ["canchero", "agro", "poli"],
        "sort_by_score": True,
        "resumen_ejecutivo": [
            "2 sectores en rojo: Poli Básquet (InSAR 0.85 mm) y 4FA — acción urgente esta semana.",
            "Sistema solar al 71% de eficiencia — 3 paneles en falla, pérdida de 4.2 kWp activa.",
            "Área agronómica estable (NDVI 0.58). Complejo acuático y estadio sin intervención inmediata.",
        ],
        "presupuesto_urgente": {
            "moneda": "ARS",
            "total": 180000,
            "items": [
                {"concepto":"Fungicida + insumos resembrado 4FA",     "monto":85000,"sem":"rojo"},
                {"concepto":"Inspección estructural Básquet Feijóo", "monto":45000,"sem":"rojo"},
                {"concepto":"Limpieza y revisión 7 paneles solares", "monto":32000,"sem":"amarillo"},
                {"concepto":"Reparación drenaje lateral 4FA",         "monto":18000,"sem":"amarillo"},
            ],
        },
        "sectores_autorizacion": [
            {"sector":"Villa Olímpica","accion":"Compra fungicida + insumos resembrado","monto":85000,"urgencia":"HOY"},
            {"sector":"Poli Feijóo",   "accion":"Contratar empresa inspección estructural","monto":45000,"urgencia":"esta-sem"},
        ],
        "kpis": [
            {"label":"InSAR Básquet","value":"0.85","value_prev":"0.70","unit":"mm","sub":"SUPERA umbral 0.8 mm","sem":"rojo"},
            {"label":"NDVI 4FA",      "value":"0.31","value_prev":"0.38","unit":"",  "sub":"Cancha crítica",      "sem":"rojo"},
            {"label":"NDVI Agro",    "value":"0.58","value_prev":"0.56","unit":"",  "sub":"Riego activo OK",     "sem":"amarillo"},
            {"label":"Score Poli",   "value":"39",  "value_prev":"44",  "unit":"",  "sub":"Atención urgente",    "sem":"rojo"},
        ],
        "acciones": [
            "Poli Básquet: inspección fisuras + drenaje URGENTE (InSAR 0.85 mm)",
            "4FA: intervención urgente — fungicida + drenaje esta semana",
            "Agro: riego preventivo — monitorear NDVI campo norte",
        ],
    },
    "banchero": {
        "nombre": "Fernando Banchero",
        "tipo": "ejecutivo",
        "sectores": _ALL,
        "sort_by_score": True,
        "acciones": [
            "Poli + 4FA: intervenciones urgentes esta semana",
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
            "4FA y Poli Básquet en estado crítico — intervención urgente requerida.",
            "2FA y Área Agronómica en condiciones aptas para uso normal.",
            "Poli Playón Norte: evaluar antes de actividades de alto impacto.",
        ],
        "presupuesto_urgente": {
            "moneda": "ARS",
            "total": 130000,
            "items": [
                {"concepto":"Fungicida + resembrado 4FA",     "monto":85000,"sem":"rojo"},
                {"concepto":"Inspección estructural Básquet","monto":45000,"sem":"rojo"},
            ],
        },
        "sectores_autorizacion": [
            {"sector":"Villa Olímpica","accion":"Compra fungicida + resembrado",      "monto":85000,"urgencia":"HOY"},
            {"sector":"Poli Feijóo",   "accion":"Inspección estructural Básquet",     "monto":45000,"urgencia":"esta-sem"},
        ],
        "acciones": [
            "4FA: NO APTA — hongo activo + drenaje roto + pasto ralo",
            "Poli Básquet: evaluar antes de uso intensivo (InSAR 0.85 mm)",
            "2FA y Campo Agro: estado óptimo — sin restricciones",
        ],
        "kpis": [
            {"label":"Canchas Aptas","value":"2",   "unit":"/4","sub":"2FA y campo OK",        "sem":"amarillo"},
            {"label":"NDVI 4FA",      "value":"0.31","unit":"",  "sub":"NO APTA — crítica",    "sem":"rojo"},
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
            "Poli Básquet + 4FA: intervenciones urgentes esta semana",
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
    """Last 7 days avg + daily lists for GDD: EVPTRNS, T2M_MAX, T2M_MIN, PRECTOTCORR, RH2M, WS2M, ALLSKY_SFC_SW_DWN"""
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
    daily = {}   # key → [daily values sorted by date]
    result = {}  # key → 7-day average
    for key, vals in params.items():
        ordered = [v for _, v in sorted(vals.items()) if v is not None and v > -900]
        daily[key] = ordered
        if ordered:
            result[key] = round(sum(ordered) / len(ordered), 3)
    # Growing Degree Days base 10°C
    tmax_d = daily.get("T2M_MAX", [])
    tmin_d = daily.get("T2M_MIN", [])
    gdd_days = [max(0.0, (mx + mn) / 2 - 10) for mx, mn in zip(tmax_d, tmin_d)]
    result["_gdd_7d"]   = round(sum(gdd_days), 1)
    result["_gdd_rate"] = round(sum(gdd_days) / len(gdd_days), 1) if gdd_days else 5.0
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
    """2 days past + 7 days forecast hourly: ET0, soil moisture, T2m, RH2m, wind, precip."""
    url = (
        "https://api.open-meteo.com/v1/forecast"
        f"?latitude={LAT}&longitude={LON}"
        "&hourly=precipitation_probability,wind_speed_10m,et0_fao_evapotranspiration"
        ",soil_moisture_0_to_1cm,soil_moisture_1_to_3cm,soil_temperature_0cm"
        ",temperature_2m,relative_humidity_2m"
        "&timezone=America%2FArgentina%2FBuenos_Aires"
        "&past_days=2&forecast_days=7&wind_speed_unit=kmh"
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


def _fetch_ecostress_cmr() -> dict:
    """CMR metadata search for NASA ECOSTRESS ECO2LSTE (LST 70m). Public, no auth."""
    end_dt   = date.today().isoformat() + "T23:59:59Z"
    start_dt = (date.today() - timedelta(days=7)).isoformat() + "T00:00:00Z"
    bbox     = f"{LON-0.06},{LAT-0.06},{LON+0.06},{LAT+0.06}"
    url = (
        "https://cmr.earthdata.nasa.gov/search/granules.json"
        f"?short_name=ECO2LSTE&bounding_box={bbox}"
        f"&temporal[]={start_dt},{end_dt}&page_size=2&sort_key=-start_date"
    )
    raw = _fetch_json(url, timeout=8)
    entries = raw.get("feed", {}).get("entry", [])
    if entries:
        e = entries[0]
        fecha = e.get("time_start", "")[:10]
        log.info("OK ecostress: imagen %s", fecha)
        return {
            "disponible": True,
            "fecha": fecha,
            "fuente": "NASA ECOSTRESS ECO2LSTE 70m",
            "mensaje": f"Imagen ECOSTRESS disponible: {fecha}",
            "nota": "LST exacto requiere Earthdata auth — usando Open-Meteo soil_temp como fallback",
        }
    log.info("OK ecostress: sin imagen reciente — usando Landsat fallback")
    return {
        "disponible": False,
        "fuente": "Landsat TIRS 100m (fallback)",
        "mensaje": "Sin imagen ECOSTRESS últimos 7 días — Landsat activo como fallback",
    }


def _fetch_smap_cmr() -> dict:
    """CMR metadata search for NASA SMAP SPL3SMP_E (soil moisture 9km). Public, no auth."""
    end_dt   = date.today().isoformat() + "T23:59:59Z"
    start_dt = (date.today() - timedelta(days=4)).isoformat() + "T00:00:00Z"
    bbox     = f"{LON-0.5},{LAT-0.5},{LON+0.5},{LAT+0.5}"
    for short in ("SPL3SMP_E", "SPL3SMP"):
        url = (
            "https://cmr.earthdata.nasa.gov/search/granules.json"
            f"?short_name={short}&bounding_box={bbox}"
            f"&temporal[]={start_dt},{end_dt}&page_size=1&sort_key=-start_date"
        )
        raw = _fetch_json(url, timeout=8)
        entries = raw.get("feed", {}).get("entry", [])
        if entries:
            fecha = entries[0].get("time_start", "")[:10]
            log.info("OK smap: %s %s", short, fecha)
            return {
                "disponible": True,
                "producto": short,
                "fecha": fecha,
                "mensaje": f"SMAP {short} disponible: {fecha}",
                "nota": "Valor exacto requiere Earthdata auth — usando Open-Meteo soil_moisture como proxy",
            }
    log.info("OK smap: sin datos recientes")
    return {"disponible": False, "mensaje": "Sin datos SMAP recientes — usando Open-Meteo soil moisture"}


# GNDVI per-cancha (Sentinel-2 B3+B8). Without Sentinel Hub auth the values are
# estimated from NDVI using a published turfgrass regression (Gitelson 1996) with
# health-adjusted multipliers per cancha.  Label: "fuente":"estimado-ndvi".
_CANCHA_NDVI     = {"1fa": 0.48, "2fa": 0.52, "3fa": 0.38, "4fa": 0.31}
_GNDVI_K         = {"1fa": 0.91, "2fa": 0.93, "3fa": 0.88, "4fa": 0.84}

def _estimate_gndvi_per_cancha() -> dict:
    """GNDVI ≈ NDVI × k − 0.01 per cancha with N-stress interpretation."""
    def _n_status(g):
        if g < 0.30: return "grave",      "Fertilizar URGENTE — deficiencia N grave"
        if g < 0.38: return "bajo",       "Fertilizar esta semana — déficit N moderado"
        if g < 0.45: return "borderline", "Fertilización preventiva recomendable"
        return          "ok",             "Nitrógeno adecuado"

    canchas = {}
    for cid, ndvi in _CANCHA_NDVI.items():
        gndvi = round(max(0, ndvi * _GNDVI_K[cid] - 0.01), 2)
        nst, nrec = _n_status(gndvi)
        canchas[cid] = {"gndvi": gndvi, "n_status": nst, "n_rec": nrec}
    return {"fuente": "estimado-ndvi", "canchas": canchas}


def _calc_fungal_risk(h: dict) -> dict:
    """Smith-Kearns index for Dollar Spot & Brown Patch using past 48h T2m + RH2m."""
    temps = h.get("temperature_2m", [])
    rh    = h.get("relative_humidity_2m", [])
    if not temps or not rh:
        return {"nivel": "bajo", "horas_favorables_48h": 0, "horas_brown_patch_48h": 0,
                "descripcion": "Sin datos T/RH disponibles", "accion_recomendada": "",
                "canchas_en_riesgo": [], "enfermedad": None}

    h_dollar = 0  # Dollar Spot: T 10-32°C, RH > 80%
    h_brown  = 0  # Brown Patch: T > 20°C,  RH > 85%
    for t, r in zip(temps[:48], rh[:48]):
        if t is None or r is None:
            continue
        if 10 < t < 32 and r > 80:
            h_dollar += 1
        if t > 20 and r > 85:
            h_brown  += 1

    if h_brown >= 10 or h_dollar >= 16:
        nivel    = "alto"
        enf      = "Dollar Spot + Brown Patch"
        desc     = f"ALTO: {h_dollar}h Dollar Spot · {h_brown}h Brown Patch (últimas 48h)"
        accion   = "Aplicar fungicida preventivo HOY — ventana de 3-5 días antes del foco"
        canchas  = ["1fa", "3fa", "2fa"]
    elif h_dollar >= 6 or h_brown >= 4:
        nivel    = "medio"
        enf      = "Dollar Spot"
        desc     = f"MEDIO: {h_dollar}h favorables Dollar Spot en últimas 48h"
        accion   = "Monitorear 1FA/3FA — aplicar fungicida si aparecen manchas amarillas"
        canchas  = ["1fa", "3fa"]
    else:
        nivel    = "bajo"
        enf      = None
        desc     = f"Riesgo bajo: {h_dollar}h favorables para hongos en últimas 48h"
        accion   = "Sin acción preventiva necesaria"
        canchas  = []

    return {
        "nivel":                nivel,
        "horas_favorables_48h": h_dollar,
        "horas_brown_patch_48h":h_brown,
        "descripcion":          desc,
        "accion_recomendada":   accion,
        "canchas_en_riesgo":    canchas,
        "enfermedad":           enf,
    }


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


def _compute_weather_live(nasa: dict, soil: dict, hourly_resp: dict, conae: dict,
                          ecostress: dict, smap: dict) -> dict:
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

    fuentes = [k for k, d in [("nasa-power", nasa), ("soilgrids", soil),
                               ("open-meteo-hourly", h)] if d]

    # ── GDD (Growing Degree Days, base 10°C) ─────────────────────────────────
    gdd_7d   = nasa.get("_gdd_7d",   35.0)
    gdd_rate = nasa.get("_gdd_rate",  5.0)
    # Days to next cut: 40 GDD needed from typical 28mm height to cut point
    dias_corte = max(3, min(14, round(40 / max(0.1, gdd_rate))))

    # ── GNDVI per cancha ──────────────────────────────────────────────────────
    gndvi_data = _estimate_gndvi_per_cancha()

    # ── Fungal risk (Smith-Kearns) ────────────────────────────────────────────
    riesgo_fung = _calc_fungal_risk(h)

    # ── Subsurface soil moisture (SMAP proxy = Open-Meteo 1-3cm layer) ───────
    sm3_vals = [v for v in (h.get("soil_moisture_1_to_3cm") or [])[:24] if v is not None]
    hum_sub_pct = round(sum(sm3_vals) / len(sm3_vals) * 100, 1) if sm3_vals else hum_pct

    return {
        "timestamp":               datetime.now().isoformat(timespec="seconds"),
        "fuentes":                 fuentes,
        # ET / riego
        "et0_mm_dia":              et0,
        "et0_fuente":              et0_fuente,
        "et0_semana_mm":           et0_sem,
        "precipitacion_semana_mm": prec_sem,
        "deficit_hidrico_mm":      deficit,
        "deficit_hidrico":         deficit > 5,
        "litros_m2_semana":        deficit,
        "riego_min_sector":        riego_min,
        "hora_riego_optima":       hora_riego,
        "dia_riego_offset":        dia_riego,
        "hora_corte_optima":       hora_corte,
        # Suelo
        "humedad_suelo_pct":       hum_pct,
        "humedad_suelo_estado":    hum_est,
        "humedad_subsuperficial_pct": hum_sub_pct,
        "suelo_tipo":              suelo,
        "suelo_clay_pct":          clay_pct,
        "suelo_sand_pct":          sand_pct,
        "suelo_whc_mm":            int(whc) if whc else 42,
        # Crecimiento / GDD
        "gdd_acumulado_7d":        gdd_7d,
        "gdd_rate_diario":         gdd_rate,
        "dias_proximo_corte":      dias_corte,
        # GNDVI
        "gndvi_por_cancha":        gndvi_data,
        # Fungal risk
        "riesgo_fungosis":         riesgo_fung,
        # Satellite metadata
        "ecostress":               ecostress,
        "smap":                    smap,
        # CONAE SAR
        "sar_disponible":          conae.get("disponible", False),
        "sar_mensaje":             conae.get("mensaje", "Sin datos SAR"),
        # Water cost
        "costo_agua_ars":          costo_agua,
    }


_pool = ThreadPoolExecutor(max_workers=8)

async def _arun(fn):
    """Run a sync fetch function in the shared thread pool (non-blocking)."""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(_pool, fn)

async def _fetch_all_live_async() -> dict:
    """asyncio.gather over all 6 live sources with return_exceptions=True."""
    keys = ["nasa", "soil", "hourly", "conae", "ecostress", "smap"]
    fns  = [_fetch_nasa_power, _fetch_soilgrids, _fetch_open_meteo_hourly,
            _fetch_conae, _fetch_ecostress_cmr, _fetch_smap_cmr]
    results = await asyncio.gather(*[_arun(fn) for fn in fns], return_exceptions=True)
    out = {}
    for key, result in zip(keys, results):
        if isinstance(result, Exception):
            log.warning("  FAIL %s: %s", key, result)
            out[key] = {}
        else:
            log.info("  OK %s", key)
            out[key] = result or {}
    return out

def _fetch_all_live() -> dict:
    """Synchronous entry point — runs asyncio.gather internally."""
    return asyncio.run(_fetch_all_live_async())


# ── BUILD ─────────────────────────────────────────────────────────────────────

def build_velez_data(fecha: str = None, live: bool = True) -> dict:
    f = fecha or FECHA_EMISION
    usuarios = copy.deepcopy(USUARIO_CONFIG)
    usuarios["roger"]["tareas_semana"] = _build_roger_calendar()

    weather_live = {}
    if live:
        log.info("Fetching live data sources (asyncio.gather)...")
        try:
            res = _fetch_all_live()
            weather_live = _compute_weather_live(
                res["nasa"], res["soil"], res["hourly"], res["conae"],
                res["ecostress"], res["smap"],
            )
            rf = weather_live.get("riesgo_fungosis", {})
            log.info("weather_live: ET0=%.2f deficit=%.1f GDD=%.1f fungosis=%s",
                     weather_live.get("et0_mm_dia", 0),
                     weather_live.get("deficit_hidrico_mm", 0),
                     weather_live.get("gdd_acumulado_7d", 0),
                     rf.get("nivel", "?"))

            # ── Inject water cost into intendente budget ──────────────────────
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

            # ── Inject fungal risk authorization item if ALTO ─────────────────
            if rf.get("nivel") == "alto":
                fung_monto = 35000
                fung_item  = {
                    "concepto": f"Fungicida preventivo — riesgo {rf.get('enfermedad','hongos')} ALTO",
                    "monto": fung_monto,
                    "sem": "rojo",
                }
                fung_auth  = {
                    "sector":   "Villa Olímpica",
                    "accion":   rf.get("accion_recomendada", "Aplicar fungicida preventivo"),
                    "monto":    fung_monto,
                    "urgencia": "HOY",
                }
                for slug in ("juan", "pait"):
                    u = usuarios[slug]
                    u["presupuesto_urgente"]["items"].append(fung_item)
                    u["presupuesto_urgente"]["total"] += fung_monto
                    u["sectores_autorizacion"].append(fung_auth)

            # ── Add fungal risk KPI to exec users ────────────────────────────
            nivel_fung = rf.get("nivel", "bajo")
            sem_fung   = {"alto": "rojo", "medio": "amarillo", "bajo": "verde"}[nivel_fung]
            fung_kpi   = {
                "label": "Riesgo Fungosis",
                "value": nivel_fung.upper(),
                "unit":  "",
                "sub":   rf.get("descripcion", "")[:50],
                "sem":   sem_fung,
            }
            for slug in ("banchero", "berlanga", "nelson", "aveleyra", "admin"):
                usuarios[slug]["kpis"].append(fung_kpi)

            # ── Inject GNDVI into cancha objects ────────────────────────────
            gndvi_canchas = weather_live.get("gndvi_por_cancha", {}).get("canchas", {})
            for cancha in SECTOR_DATA.get("canchero", {}).get("canchas", []):
                cid = cancha.get("id")
                if cid in gndvi_canchas:
                    cancha["gndvi"]    = gndvi_canchas[cid]["gndvi"]
                    cancha["n_status"] = gndvi_canchas[cid]["n_status"]
                    cancha["n_rec"]    = gndvi_canchas[cid]["n_rec"]

        except Exception as e:
            log.error("live data pipeline error: %s", e)

    return {
        "meta": {
            "version": "4.0",
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
