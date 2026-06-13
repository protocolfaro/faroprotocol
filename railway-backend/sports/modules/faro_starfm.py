"""
sports/modules/faro_starfm.py — Motor ESTARFM para todos los clientes Sports y Events.

ESTARFM: Sentinel-2 baseline + MODIS diario → NDVI cloud-free hoy.
Es exactamente lo que usan Geosys, EarthDaily y Farmonaut para resolver las nubes.
Costo: $0. Fuentes: NASA MODIS + ESA Sentinel-2 (ambas gratuitas).

Uso:
    from sports.modules.faro_starfm import compute_starfm_ndvi
    result = compute_starfm_ndvi(lat=-34.64, lon=-58.52, show_date='2026-06-13', days_back=30)

Clientes activos: Vélez Sarsfield, Dale Play/Amalfitani
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../../events/clients/dale-play'))
from dale_play_starfm import compute_starfm_ndvi, _estarfm_simplified

__all__ = ['compute_starfm_ndvi', '_estarfm_simplified']
