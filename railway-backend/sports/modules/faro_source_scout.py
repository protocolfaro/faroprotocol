"""
sports/modules/faro_source_scout.py — Auto-descubrimiento de fuentes satelitales.

Job semanal que usa Claude + web_search para descubrir nuevas fuentes de datos
satelitales gratuitas, las evalúa y las registra en SOURCE_LOG.md.
El sistema mejora solo — sin intervención manual.

Uso:
    from sports.modules.faro_source_scout import run_source_scout
    result = run_source_scout()  # corre el ciclo completo
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../../events/clients/dale-play'))
from dale_play_source_scout import run_source_scout, weekly_source_scout_job

__all__ = ['run_source_scout', 'weekly_source_scout_job']
