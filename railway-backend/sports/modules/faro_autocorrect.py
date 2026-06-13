"""
sports/modules/faro_autocorrect.py — Autocorrección de módulos compartida.

Registra éxito/fallo de cada módulo. Si un módulo falla 3 veces → se marca
como degraded y el pipeline lo skipea hasta que se recupere.
El sistema aprende qué fuentes están disponibles y cuáles no.

Uso:
    from sports.modules.faro_autocorrect import record_module_result, get_degraded_modules
    record_module_result('faro_clms_lst', success=True, venue_id='amalfitani')
    degraded = get_degraded_modules()  # ['modulo_roto', ...]
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../../events/clients/dale-play'))
from dale_play_autocorrect import record_module_result, get_module_health, get_degraded_modules

__all__ = ['record_module_result', 'get_module_health', 'get_degraded_modules']
