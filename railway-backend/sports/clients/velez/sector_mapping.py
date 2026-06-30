"""
sector_mapping.py — Mapeo cancha_id → sector_id para ERA5-Land.

Fuente única: sector_definitions.json (validado con Google Earth).
Usado por faro_prescription.py y faro_era5_land_sectorial.py.
"""
from __future__ import annotations
import json
import os
from typing import Optional

_DEF_PATH = os.path.join(os.path.dirname(__file__), "sector_definitions.json")

with open(_DEF_PATH, encoding="utf-8") as _f:
    _CFG = json.load(_f)

SECTORS:          dict[str, dict] = _CFG["sectors"]
CANCHA_TO_SECTOR: dict[str, str]  = _CFG["cancha_to_sector_mapping"]


def get_sector_id(cancha_id: str) -> Optional[str]:
    """Retorna sector_id para la cancha, o None si no está mapeada."""
    return CANCHA_TO_SECTOR.get((cancha_id or "").lower())


def get_sector_def(sector_id: str) -> Optional[dict]:
    """Retorna la definición completa del sector (bbox, nombre, etc.)."""
    return SECTORS.get(sector_id)


def get_canchas_in_sector(sector_id: str) -> list[str]:
    """Lista de cancha_ids en un sector dado."""
    return [c for c, s in CANCHA_TO_SECTOR.items() if s == sector_id]


def all_sector_ids() -> list[str]:
    return list(SECTORS.keys())


# Sanity check en import — falla rápido si el JSON está roto
assert len(CANCHA_TO_SECTOR) == 13, (
    f"sector_definitions.json debe mapear exactamente 13 canchas, tiene {len(CANCHA_TO_SECTOR)}"
)
assert all(s in SECTORS for s in CANCHA_TO_SECTOR.values()), (
    "Todos los sector_id en cancha_to_sector_mapping deben existir en sectors"
)
