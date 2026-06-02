"""
dale_play_config.py — Config del venue para el pipeline Dale Play.

Carga desde venues/{venue_id}.json. Si no existe, usa defaults Amalfitani.
Principio (ARCHITECTURE.md): nunca hardcodear datos — todo viene de config.

Uso:
    from dale_play_config import get_venue_config
    vc = get_venue_config("amalfitani")   # o el venue_id del show

Backwards-compat: las constantes módulo-nivel (VENUE_LAT, etc.) siguen
exportadas con los valores de Amalfitani para no romper imports existentes.
"""
from __future__ import annotations
import json, logging, pathlib
from functools import lru_cache

log = logging.getLogger(__name__)

_VENUES_DIR       = pathlib.Path(__file__).parent / "venues"
_DEFAULT_VENUE_ID = "amalfitani"


@lru_cache(maxsize=8)
def get_venue_config(venue_id: str | None = None) -> dict:
    """
    Carga la config del venue desde venues/{venue_id}.json.
    Cachea en memoria para el lifetime del proceso (Railway).
    """
    vid  = (venue_id or _DEFAULT_VENUE_ID).lower().strip()
    path = _VENUES_DIR / f"{vid}.json"
    if not path.exists():
        log.warning("venue config no encontrada: %s — usando amalfitani", vid)
        path = _VENUES_DIR / f"{_DEFAULT_VENUE_ID}.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        log.error("error cargando venue config %s: %s — usando defaults", path, exc)
        return _AMALFITANI_DEFAULTS


# ── Defaults hardcoded como último recurso (si venues/ no existe en Railway) ─
_AMALFITANI_DEFAULTS: dict = {
    "venue_id": "amalfitani",
    "nombre":   "Estadio José Amalfitani",
    "lat": -34.6379, "lon": -58.5288,
    "bbox": [-58.5305, -34.6391, -58.5271, -34.6367],
    "capacidad": 49540,
    "campo": {"largo_m": 105, "ancho_m": 68},
    "tribunas": [
        {"id": "norte", "nombre": "Tribuna Norte",  "lat": -34.6370, "lon": -58.5288},
        {"id": "sur",   "nombre": "Tribuna Sur",    "lat": -34.6388, "lon": -58.5288},
        {"id": "este",  "nombre": "Tribuna Este",   "lat": -34.6379, "lon": -58.5277},
        {"id": "oeste", "nombre": "Tribuna Oeste",  "lat": -34.6379, "lon": -58.5299},
    ],
    "suelo": {"tipo": "Franco-arcilloso", "capacidad_portante_kpa": 120, "capacidad_saturada_kpa": 65},
    "modis_tile": "h13v12",
}


# ── Constantes módulo-nivel (backwards-compat) ────────────────────────────────
# Todos los módulos que hacen `from dale_play_config import VENUE_LAT` siguen
# funcionando. Nuevos módulos deben llamar a get_venue_config(venue_id).
_vc = get_venue_config(_DEFAULT_VENUE_ID)

VENUE_LAT  = _vc["lat"]
VENUE_LON  = _vc["lon"]
VENUE_NAME = _vc["nombre"]
VENUE_ADDR = _vc.get("direccion", "Juan B. Justo 9200, Liniers, Buenos Aires")
VENUE_CAP  = _vc["capacidad"]
VENUE_BBOX = tuple(_vc["bbox"])

CAMPO_LARGO_M = _vc["campo"]["largo_m"]   # 105 m
CAMPO_ANCHO_M = _vc["campo"]["ancho_m"]   # 68 m

# GitHub — histórico shows
GH_OWNER         = "protocolfaro"
GH_REPO          = "faroprotocol"
GH_BRANCH        = "main"
GH_SHOWS_PATH    = "dale-play/shows"
GH_REPORTES_PATH = "dale-play/reportes"

# Tribunas
TRIBUNAS = _vc.get("tribunas", _AMALFITANI_DEFAULTS["tribunas"])

# Suelo baseline
_suelo = _vc.get("suelo", _AMALFITANI_DEFAULTS["suelo"])
SOIL_PROFILE = {
    "tipo":                   _suelo.get("tipo", "Franco-arcilloso"),
    "capacidad_portante_kpa": _suelo.get("capacidad_portante_kpa", 120),
    "capacidad_saturada_kpa": _suelo.get("capacidad_saturada_kpa", 65),
}

# Thresholds suelo (kPa)
SOIL_SAFE_KPA     = 80
SOIL_CAUTION_KPA  = 100
SOIL_CRITICAL_KPA = 120

# Thresholds InSAR (mm desplazamiento LOS → vertical)
INSAR_OK_MM       = 1.0
INSAR_CAUTION_MM  = 2.0
INSAR_CRITICAL_MM = 3.5

# Thresholds NDVI
NDVI_BUENO     = 0.55
NDVI_DEGRADADO = 0.35

# Thresholds clima (km/h / mm)
WIND_CAUTION_KMH  = 40
WIND_CRITICAL_KMH = 65
RAIN_CAUTION_MM   = 10
TEMP_MIN_SAFE     = 4
TEMP_MAX_SAFE     = 38

# MODIS tile
MODIS_TILE = _vc.get("modis_tile", "h13v12")

# Stage zones default (override con rider)
STAGE_ZONES = [
    {"id": "escenario_principal", "name": "Escenario Principal",
     "lat": -34.6379, "lon": -58.5295, "w_m": 50, "h_m": 20, "carga_ton": 80},
    {"id": "torres_sonido",       "name": "Torres de Sonido",
     "lat": -34.6375, "lon": -58.5291, "w_m": 4,  "h_m": 4,  "carga_ton": 8},
    {"id": "torres_iluminacion",  "name": "Torres de Iluminación",
     "lat": -34.6383, "lon": -58.5291, "w_m": 4,  "h_m": 4,  "carga_ton": 6},
    {"id": "mix_central",         "name": "Consola FOH",
     "lat": -34.6379, "lon": -58.5285, "w_m": 6,  "h_m": 6,  "carga_ton": 2},
    {"id": "barricada",           "name": "Barricada de Seguridad",
     "lat": -34.6379, "lon": -58.5291, "w_m": 40, "h_m": 3,  "carga_ton": 5},
]
