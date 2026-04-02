"""
FARO PROTOCOL — Gestión de áreas geográficas
Carga configuraciones desde faro_areas/<nombre>.json
"""

import json
from pathlib import Path

AREAS_DIR = Path(__file__).parent / 'faro_areas'


def load_area(name: str) -> dict:
    """
    Carga la configuración de un área desde faro_areas/<name>.json

    Ejemplo:
        area = load_area('cordoba')
        area = load_area('vaca_muerta')
    """
    path = AREAS_DIR / f"{name}.json"
    if not path.exists():
        available = list_areas()
        raise FileNotFoundError(
            f"Área '{name}' no encontrada.\n"
            f"Áreas disponibles: {', '.join(available) or 'ninguna'}\n"
            f"Para agregar un área nueva creá: {path}"
        )
    with open(path, encoding='utf-8') as f:
        return json.load(f)


def list_areas() -> list:
    """Retorna los nombres de todas las áreas configuradas."""
    return sorted(f.stem for f in AREAS_DIR.glob('*.json') if f.stem != 'areas')
