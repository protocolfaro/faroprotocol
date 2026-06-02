"""
core/utils/__init__.py
Funciones transversales compartidas por Sports, Events y Protocol.
Regla: nunca duplicar estas funciones en verticales — importar desde aquí.
"""
from __future__ import annotations
import math, sys, logging
from datetime import datetime, timezone

log = logging.getLogger(__name__)


def format_date(date) -> str:
    """
    Convierte date (str ISO, datetime, o date) a formato DD/MM/YYYY.
    Retorna 'N/D' si el valor es None o no parseable.
    """
    if date is None:
        return "N/D"
    if isinstance(date, datetime):
        return date.strftime("%d/%m/%Y")
    if hasattr(date, "strftime"):          # datetime.date
        return date.strftime("%d/%m/%Y")
    s = str(date).strip()
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%Y/%m/%d", "%d-%m-%Y"):
        try:
            return datetime.strptime(s, fmt).strftime("%d/%m/%Y")
        except ValueError:
            continue
    return "N/D"


def coords_to_bbox(lat: float, lon: float, radius_m: float = 0, *,
                   h_m: float | None = None, w_m: float | None = None) -> list[float]:
    """
    Convierte coordenadas centrales a bbox [W, S, E, N] (orden GeoJSON/STAC).
    Modos:
      - radius_m         → bbox cuadrado (mitad de lado = radius_m)
      - h_m=..., w_m=... → bbox rectangular (alto × ancho en metros)
    Usa aproximación plana válida para distancias < 500 km.
    """
    lat_deg_per_m = 1.0 / 111_320.0
    lon_deg_per_m = 1.0 / (111_320.0 * math.cos(math.radians(lat)))
    dlat = (h_m / 2 if h_m is not None else radius_m) * lat_deg_per_m
    dlon = (w_m / 2 if w_m is not None else radius_m) * lon_deg_per_m
    return [
        round(lon - dlon, 6),
        round(lat - dlat, 6),
        round(lon + dlon, 6),
        round(lat + dlat, 6),
    ]


def log_error(module: str, error: Exception | str, show_id: str = "") -> None:
    """
    Loguea un error en stderr con timestamp ISO, módulo y show_id.
    No lanza excepción — solo loguea.
    """
    ts  = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    msg = f"[{ts}] ERROR module={module} show_id={show_id!r} — {error}"
    print(msg, file=sys.stderr)
    log.error(msg)
