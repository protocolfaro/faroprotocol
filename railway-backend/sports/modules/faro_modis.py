"""
sports/modules/faro_modis.py — Motor MODIS para todos los clientes Sports y Events.

Fuente: MODIS MOD09GQ 250m diario via earthaccess (NASA Earthdata).
Solución al problema de nubes: MODIS pasa TODOS los días, cualquier clima.
Resolución 250m — suficiente para tracking de tendencia por venue.

Uso:
    from sports.modules.faro_modis import fetch_modis_ndvi
    result = fetch_modis_ndvi(lat=-34.64, lon=-58.52, days_back=7)

Clientes activos: Vélez Sarsfield, Dale Play/Amalfitani
"""
# Re-exporta la implementación de Dale Play que ya está probada en producción
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../../events/clients/dale-play'))
from dale_play_modis import fetch_modis_ndvi, _earthaccess_login, _search_granule

__all__ = ['fetch_modis_ndvi', '_earthaccess_login', '_search_granule']
