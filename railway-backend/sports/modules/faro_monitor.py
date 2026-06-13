"""
sports/modules/faro_monitor.py — Agente de monitoreo autónomo compartido.

Job cada 6h: revisa clima/NDVI/SAR para todos los venues activos.
Genera alertas SOLO cuando hay anomalía real — no falsas alarmas estacionales.
Usa field_timeseries baseline para saber si el NDVI es normal para esa semana del año.

Uso:
    from sports.modules.faro_monitor import run_monitoring_check
    alerts = run_monitoring_check(venue_id='amalfitani', config=VELEZ_CONFIG)

Clientes activos: Vélez Sarsfield, Dale Play/Amalfitani
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../../events/clients/dale-play'))
from dale_play_monitor import run_monitoring_check, _check_venue, _threshold_ndvi

# Importar baseline fenológico del core
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../../core/satellite'))
try:
    from field_timeseries import detect_anomaly, get_baseline
    HAS_BASELINE = True
except ImportError:
    HAS_BASELINE = False

def run_velez_monitor(venue_id: str = 'amalfitani') -> dict:
    """
    Monitor específico para Vélez con corrección estacional.
    Usa el baseline fenológico de 2020-2026 para distinguir
    anomalías reales de variación estacional normal.
    """
    import datetime as _dt
    month = _dt.date.today().month
    winter = month in (5, 6, 7, 8)
    
    result = run_monitoring_check(venue_id=venue_id, config={
        'lat': -34.636, 'lon': -58.519,
        'ndvi_threshold_winter': 0.04 if winter else 0.25,
        'ndvi_threshold_normal': 0.25,
        'use_baseline': HAS_BASELINE,
        'winter_mode': winter,
    })
    return result

__all__ = ['run_monitoring_check', 'run_velez_monitor', 'HAS_BASELINE']
