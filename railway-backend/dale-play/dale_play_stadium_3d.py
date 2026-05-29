"""
dale_play_stadium_3d.py — Modelo 3D del Estadio José Amalfitani.
Descarga la huella OSM via Overpass API + elevación base SRTM/Open-Elevation.
Genera amalfitani_3d.json con geometría de campo, tribunas y escenario.
"""
from __future__ import annotations
import json, logging, math, pathlib, time
import requests
import numpy as np
from shapely.geometry import Polygon, mapping
from pyproj import Transformer

log = logging.getLogger(__name__)

# ── Constantes del estadio ────────────────────────────────────────────────────
VENUE_LAT  = -34.6379
VENUE_LON  = -58.5288
OSM_QUERY  = """
[out:json][timeout:25];
(
  way["leisure"="stadium"]["name"~"Amalfitani",i](around:500,-34.6379,-58.5288);
  relation["leisure"="stadium"]["name"~"Amalfitani",i](around:500,-34.6379,-58.5288);
  way["name"~"Amalfitani",i](around:500,-34.6379,-58.5288);
);
out body geom;
"""

# Dimensiones reales conocidas del Amalfitani (fuente: planos CONMEBOL/AFA)
PITCH_L   = 105.0   # m largo
PITCH_W   = 68.0    # m ancho
TRACK_W   = 8.0     # m pista atletismo perimetral
STAND_D   = 14.0    # m profundidad promedio tribuna
STAND_ROWS = {
    "norte":  {"n_filas": 32, "altura_max": 18.0, "angulo_rake": 28.0},
    "sur":    {"n_filas": 28, "altura_max": 16.0, "angulo_rake": 26.0},
    "este":   {"n_filas": 22, "altura_max": 12.0, "angulo_rake": 24.0},
    "oeste":  {"n_filas": 22, "altura_max": 12.0, "angulo_rake": 24.0},
}
BASE_ELEV  = 25.0   # m ASL aprox Liniers, CABA


# ── Helpers de proyección ──────────────────────────────────────────────────────
def _proj():
    """UTM zona 21S para Buenos Aires."""
    return Transformer.from_crs("EPSG:4326", "EPSG:32721", always_xy=True)


def _latlon_to_utm(lat, lon):
    t = _proj()
    return t.transform(lon, lat)


def _utm_to_latlon(x, y):
    t = Transformer.from_crs("EPSG:32721", "EPSG:4326", always_xy=True)
    lon, lat = t.transform(x, y)
    return lat, lon


# ── 1. Huella OSM via Overpass ────────────────────────────────────────────────
def fetch_osm_footprint() -> dict | None:
    """Retorna {'coords': [(lat,lon)...], 'source': str} o None."""
    try:
        r = requests.post(
            "https://overpass-api.de/api/interpreter",
            data={"data": OSM_QUERY}, timeout=20,
        )
        r.raise_for_status()
        data = r.json()
        for el in data.get("elements", []):
            if el.get("type") == "way" and el.get("geometry"):
                coords = [(n["lat"], n["lon"]) for n in el["geometry"]]
                log.info("OSM footprint: %d nodes (way %s)", len(coords), el["id"])
                return {"coords": coords, "osm_id": el["id"], "source": "OpenStreetMap/Overpass"}
    except Exception as e:
        log.warning("Overpass fallback: %s", e)
    return None


# ── 2. Elevación base Open-Elevation ──────────────────────────────────────────
def fetch_base_elevation() -> float:
    try:
        r = requests.get(
            "https://api.open-elevation.com/api/v1/lookup",
            params={"locations": f"{VENUE_LAT},{VENUE_LON}"}, timeout=10,
        )
        r.raise_for_status()
        elev = r.json()["results"][0]["elevation"]
        log.info("Open-Elevation: %.1f m ASL", elev)
        return float(elev)
    except Exception as e:
        log.warning("Open-Elevation fallback (%.1f m): %s", BASE_ELEV, e)
        return BASE_ELEV


# ── 3. Geometría 3D de las tribunas ──────────────────────────────────────────
def _build_tribune(name: str, cx: float, cy: float,
                   direction: str, base_elev: float) -> dict:
    """
    Genera perfiles de fila para una tribuna.
    direction: 'N','S','E','W' — lado del estadio.
    cx, cy: centro del campo en UTM.
    """
    spec   = STAND_ROWS[name]
    nf     = spec["n_filas"]
    h_max  = spec["altura_max"]
    rake_r = math.radians(spec["angulo_rake"])

    # Distancia horizontal del primer asiento al borde del campo
    d_field = PITCH_L/2 + TRACK_W if direction in ("N","S") else PITCH_W/2 + TRACK_W
    row_d   = STAND_D / nf          # profundidad por fila

    rows = []
    for i in range(nf):
        d_row   = d_field + i * row_d
        h_row   = (i / (nf - 1)) * h_max if nf > 1 else h_max
        # Coordenada horizontal (offset desde centro campo)
        if direction == "N":   rx, ry =  0,  d_row
        elif direction == "S": rx, ry =  0, -d_row
        elif direction == "E": rx, ry =  d_row, 0
        else:                  rx, ry = -d_row, 0
        lat, lon = _utm_to_latlon(cx + rx, cy + ry)
        rows.append({
            "fila": i + 1,
            "dist_campo_m": round(d_row, 2),
            "altura_m":     round(h_row, 2),
            "elev_abs_m":   round(base_elev + h_row, 2),
            "lat": round(lat, 7),
            "lon": round(lon, 7),
        })
    return {
        "nombre":      name,
        "direccion":   direction,
        "n_filas":     nf,
        "altura_max_m": h_max,
        "angulo_rake_deg": spec["angulo_rake"],
        "filas":       rows,
    }


# ── 4. Escenario desde rider ──────────────────────────────────────────────────
def _build_stage(cx: float, cy: float, base_elev: float, rider: dict) -> dict:
    stage_spec = rider.get("stage", {})
    h_stage    = 1.6   # altura estándar escenario concierto (m)
    w_stage    = 50.0
    d_stage    = 20.0
    # El escenario se ubica en el extremo sur del campo
    sy = cy - PITCH_L/2 + d_stage/2
    lat_c, lon_c = _utm_to_latlon(cx, sy)
    return {
        "posicion":     {"lat": round(lat_c, 7), "lon": round(lon_c, 7)},
        "utm":          {"x": round(cx, 1), "y": round(sy, 1)},
        "ancho_m":      w_stage,
        "profundidad_m": d_stage,
        "altura_m":     h_stage,
        "elev_abs_m":   round(base_elev + h_stage, 2),
        "lw_db":        stage_spec.get("lw_db", 134),
        "throws":       stage_spec.get("throws", []),
    }


# ── 5. Orquestador ────────────────────────────────────────────────────────────
def build_3d_model(rider: dict | None = None) -> dict:
    rider = rider or {}
    base_elev = fetch_base_elevation()
    footprint = fetch_osm_footprint()

    # Centro en UTM
    cx, cy = _latlon_to_utm(VENUE_LAT, VENUE_LON)

    # Huella del estadio en coords UTM
    if footprint:
        fp_utm = [_latlon_to_utm(lat, lon) for lat, lon in footprint["coords"]]
        fp_area = Polygon(fp_utm).area
        fp_source = footprint["source"]
        fp_osm_id = footprint.get("osm_id")
    else:
        # Fallback: rectángulo aproximado
        half_l = (PITCH_L/2 + TRACK_W + STAND_D) * 1.05
        half_w = (PITCH_W/2 + TRACK_W + STAND_D) * 1.05
        fp_utm = [
            (cx - half_w, cy - half_l), (cx + half_w, cy - half_l),
            (cx + half_w, cy + half_l), (cx - half_w, cy + half_l),
        ]
        fp_area   = Polygon(fp_utm).area
        fp_source = "aproximación geométrica (Overpass no disponible)"
        fp_osm_id = None

    # Campo
    pitch = {
        "largo_m": PITCH_L, "ancho_m": PITCH_W,
        "corners_utm": [
            {"x": round(cx - PITCH_W/2, 1), "y": round(cy - PITCH_L/2, 1)},
            {"x": round(cx + PITCH_W/2, 1), "y": round(cy - PITCH_L/2, 1)},
            {"x": round(cx + PITCH_W/2, 1), "y": round(cy + PITCH_L/2, 1)},
            {"x": round(cx - PITCH_W/2, 1), "y": round(cy + PITCH_L/2, 1)},
        ],
        "elev_abs_m": round(base_elev, 2),
    }

    tribunas = {
        "norte": _build_tribune("norte", cx, cy, "N", base_elev),
        "sur":   _build_tribune("sur",   cx, cy, "S", base_elev),
        "este":  _build_tribune("este",  cx, cy, "E", base_elev),
        "oeste": _build_tribune("oeste", cx, cy, "W", base_elev),
    }

    stage = _build_stage(cx, cy, base_elev, rider)

    model = {
        "venue":         "Estadio José Amalfitani",
        "lat_centro":    VENUE_LAT,
        "lon_centro":    VENUE_LON,
        "utm_centro":    {"x": round(cx, 1), "y": round(cy, 1)},
        "elev_base_m":   round(base_elev, 2),
        "elev_fuente":   "Open-Elevation API (SRTM30)" if base_elev != BASE_ELEV else "fallback 25m ASL",
        "footprint": {
            "source":  fp_source,
            "osm_id":  fp_osm_id,
            "area_m2": round(fp_area, 1),
            "nodes":   len(fp_utm),
        },
        "campo":    pitch,
        "tribunas": tribunas,
        "escenario": stage,
    }

    out = pathlib.Path(__file__).parent / "models" / "amalfitani_3d.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(model, ensure_ascii=False, indent=2), encoding="utf-8")
    log.info("3D model saved → %s", out)
    return model


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    m = build_3d_model()
    print(json.dumps({
        "venue":       m["venue"],
        "elev_base_m": m["elev_base_m"],
        "footprint":   m["footprint"],
        "n_tribunas":  len(m["tribunas"]),
        "escenario_utm": m["escenario"]["utm"],
    }, indent=2))
