"""
dale_play_config.py — Constantes del venue y parámetros globales para Dale Play.
Estadio José Amalfitani · Vélez Sarsfield · Liniers, Buenos Aires.
"""
from __future__ import annotations

VENUE_LAT  = -34.6379
VENUE_LON  = -58.5288
VENUE_NAME = "Estadio José Amalfitani"
VENUE_ADDR = "Juan B. Justo 9200, Liniers, Buenos Aires"
VENUE_CAP  = 49_540

# Bounding box del estadio (~200 m × 150 m buffer)
VENUE_BBOX = (-58.5305, -34.6391, -58.5271, -34.6367)  # (minlon, minlat, maxlon, maxlat)

# GitHub — histórico shows
GH_OWNER      = "protocolfaro"
GH_REPO       = "faroprotocol"
GH_BRANCH     = "main"
GH_SHOWS_PATH = "dale-play/shows"

# Tribunas (orden: Norte, Sur, Este, Oeste)
TRIBUNAS = [
    {"id": "norte",  "name": "Tribuna Norte",  "lat": -34.6370, "lon": -58.5288},
    {"id": "sur",    "name": "Tribuna Sur",    "lat": -34.6388, "lon": -58.5288},
    {"id": "este",   "name": "Tribuna Este",   "lat": -34.6379, "lon": -58.5277},
    {"id": "oeste",  "name": "Tribuna Oeste",  "lat": -34.6379, "lon": -58.5299},
]

# Zonas de escenario por defecto (ajustables por rider)
STAGE_ZONES = [
    {"id": "escenario_principal", "name": "Escenario Principal",
     "lat": -34.6379, "lon": -58.5295, "w_m": 50, "h_m": 20, "carga_ton": 80},
    {"id": "mix_central",        "name": "Consola FOH",
     "lat": -34.6379, "lon": -58.5285, "w_m": 6,  "h_m": 6,  "carga_ton": 2},
    {"id": "barricada",          "name": "Barricada de Seguridad",
     "lat": -34.6379, "lon": -58.5291, "w_m": 40, "h_m": 3,  "carga_ton": 5},
]

# Suelo baseline Amalfitani (Franco-arcilloso, típico CABA/Liniers)
SOIL_PROFILE = {
    "tipo":                    "Franco-arcilloso",
    "capacidad_portante_kpa":  120,
    "capacidad_saturada_kpa":  65,
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

# Thresholds clima (Open-Meteo km/h / mm)
WIND_CAUTION_KMH  = 40
WIND_CRITICAL_KMH = 65
RAIN_CAUTION_MM   = 10
TEMP_MIN_SAFE     = 4
TEMP_MAX_SAFE     = 38
