"""
dale_play_soil.py — Mapa de carga del suelo para estructuras de evento.
Capacidad portante real via SoilGrids REST API v2.0 + fórmula de Terzaghi (FS=3).
Fallback: Franco-arcilloso típico Liniers [ESTIMADO] si la API no responde.
Guarda: models/soil_real_{show_id}.json
"""
from __future__ import annotations
import json, logging, math, pathlib
from datetime import datetime
from typing import Optional

try:
    import requests as _requests
    _HAS_REQUESTS = True
except ImportError:
    _HAS_REQUESTS = False

from dale_play_config import (
    STAGE_ZONES, SOIL_PROFILE, VENUE_LAT, VENUE_LON,
    SOIL_SAFE_KPA, SOIL_CAUTION_KPA, SOIL_CRITICAL_KPA,
)

log = logging.getLogger(__name__)

MODELS_DIR = pathlib.Path(__file__).parent / "models"

_SOILGRIDS_URL     = "https://rest.isric.org/soilgrids/v2.0/properties/query"
_SOILGRIDS_TIMEOUT = 15  # seconds


# ── Helpers internos ──────────────────────────────────────────────────────────

def _presion_kpa(carga_ton: float, area_m2: float) -> float:
    if area_m2 <= 0:
        return 0.0
    kn = carga_ton * 9.81
    return round(kn / area_m2, 1)


def _classify(presion: float, capacidad: float) -> dict:
    ratio = presion / capacidad if capacidad else 1.0
    if ratio >= 1.0 or presion >= SOIL_CRITICAL_KPA:
        return {"clase": "exclusion",  "label": "Zona de exclusión — no colocar estructura",
                "color": "#e74c3c"}
    if ratio >= 0.75 or presion >= SOIL_CAUTION_KPA:
        return {"clase": "precaucion", "label": "Precaución — distribuir con durmientes",
                "color": "#f0b429"}
    return     {"clase": "segura",     "label": "Zona segura para estructura",
                "color": "#27ae60"}


# ── SoilGrids REST API ────────────────────────────────────────────────────────

def _fetch_soilgrids(lat: float = VENUE_LAT, lon: float = VENUE_LON) -> Optional[dict]:
    """
    Fetches bulk density, clay, sand, silt, pH from SoilGrids v2.0.
    Returns None on any failure (API down, timeout, parse error).
    WebDAV fallback (files.isric.org/soilgrids/latest/data/) requires
    Homolosine reprojection — not implemented here; falls through to [ESTIMADO].
    """
    if not _HAS_REQUESTS:
        log.warning("requests not installed — SoilGrids unavailable")
        return None

    params = {
        "lon":      lon,
        "lat":      lat,
        "property": ["bdod", "clay", "sand", "silt", "phh2o"],
        "depth":    "0-5cm",
        "value":    "mean",
    }
    try:
        resp = _requests.get(_SOILGRIDS_URL, params=params, timeout=_SOILGRIDS_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        log.warning("SoilGrids API unavailable: %s — fallback a [ESTIMADO]", exc)
        return None

    try:
        layers = data["properties"]["layers"]
        raw: dict = {}
        for layer in layers:
            name = layer["name"]
            for depth_info in layer.get("depths", []):
                if depth_info.get("label") == "0-5cm":
                    val = depth_info["values"].get("mean")
                    if val is not None:
                        raw[name] = val
                    break

        if not raw:
            log.warning("SoilGrids: respuesta vacía para (%.4f, %.4f)", lat, lon)
            return None

        # d_factor conversions per SoilGrids v2.0 spec
        bdod_g_cm3 = raw.get("bdod", 145) / 100.0   # cg/cm³  → g/cm³
        clay_raw   = raw.get("clay", 350) / 10.0    # g/kg    → %
        sand_raw   = raw.get("sand", 250) / 10.0    # g/kg    → %
        silt_raw   = raw.get("silt", 400) / 10.0    # g/kg    → %
        ph         = raw.get("phh2o", 65)  / 10.0   # pH×10   → pH

        # Normalize texture fractions to 100 %
        txt_sum = clay_raw + sand_raw + silt_raw
        if txt_sum > 0:
            clay_pct = clay_raw / txt_sum * 100
            sand_pct = sand_raw / txt_sum * 100
            silt_pct = silt_raw / txt_sum * 100
        else:
            clay_pct, sand_pct, silt_pct = 35.0, 25.0, 40.0

        log.info(
            "SoilGrids OK — bdod=%.3f g/cm³  clay=%.1f%%  sand=%.1f%%  silt=%.1f%%  pH=%.1f",
            bdod_g_cm3, clay_pct, sand_pct, silt_pct, ph,
        )
        return {
            "bdod_g_cm3": round(bdod_g_cm3, 3),
            "clay_pct":   round(clay_pct, 1),
            "sand_pct":   round(sand_pct, 1),
            "silt_pct":   round(silt_pct, 1),
            "ph":         round(ph, 1),
            "raw":        raw,
        }
    except (KeyError, TypeError, ZeroDivisionError) as exc:
        log.warning("SoilGrids parse error: %s", exc)
        return None


# ── Fórmula de Terzaghi ───────────────────────────────────────────────────────

def _terzaghi_kpa(bdod_g_cm3: float, clay_pct: float,
                  sand_pct: float, silt_pct: float) -> tuple[float, dict]:
    """
    Capacidad portante admisible via Terzaghi (cimentación corrida superficial).
    FS = 3 aplicado a q_u para obtener q_a.
    Retorna (q_a kPa, detalle_dict).
    """
    # Peso unitario saturado γ (kN/m³) — bdod en g/cm³
    gamma = bdod_g_cm3 * 9.81  # ≈ kN/m³ para suelo saturado

    # Ángulo de fricción interna φ (Holtz & Kovacs simplificado)
    phi_deg = float(max(5.0, min(38.0, 22.0 + 0.15 * sand_pct - 0.10 * clay_pct)))
    phi_rad = math.radians(phi_deg)
    tan_phi = math.tan(phi_rad)

    # Cohesión c (Skempton 1957): c ≈ 0.022 × clay% × γ
    c = max(0.0, 0.022 * clay_pct * gamma)

    # Factores de capacidad portante de Terzaghi (falla general de corte)
    N_q = math.exp(math.pi * tan_phi) * math.tan(math.radians(45 + phi_deg / 2)) ** 2
    N_c = (N_q - 1) / tan_phi if tan_phi > 0.001 else 5.14
    N_g = 2.0 * (N_q + 1) * tan_phi

    # Presión de sobrecarga q (Df = 0.5 m)
    D_f = 0.5
    q   = gamma * D_f

    # q_u — cimentación corrida, B = 1 m
    B   = 1.0
    q_u = c * N_c + q * N_q + 0.5 * gamma * B * N_g
    q_a = q_u / 3.0  # FS = 3

    detail = {
        "phi_deg":    round(phi_deg, 1),
        "c_kpa":      round(c, 1),
        "gamma_kn_m3": round(gamma, 2),
        "N_c": round(N_c, 2), "N_q": round(N_q, 2), "N_g": round(N_g, 2),
        "q_u_kpa":    round(q_u, 1),
        "FS":         3,
        "q_a_kpa":    round(q_a, 1),
    }
    log.info(
        "Terzaghi: φ=%.1f° c=%.1f kPa → qu=%.0f kPa → qa=%.0f kPa",
        phi_deg, c, q_u, q_a,
    )
    return round(q_a, 1), detail


# ── API pública — datos reales ────────────────────────────────────────────────

def fetch_real_soil_capacity(show_id: str = "unknown",
                              lat: float = VENUE_LAT,
                              lon: float = VENUE_LON) -> dict:
    """
    Obtiene capacidad portante real de SoilGrids + Terzaghi.
    Guarda models/soil_real_{show_id}.json.
    Retorna dict con capacidad_portante_kpa y metadatos.
    """
    MODELS_DIR.mkdir(exist_ok=True)

    sg = _fetch_soilgrids(lat, lon)

    if sg:
        q_a, terzaghi_detail = _terzaghi_kpa(
            sg["bdod_g_cm3"], sg["clay_pct"], sg["sand_pct"], sg["silt_pct"]
        )
        fuente   = "SoilGrids REST API v2.0 (ISRIC) · Fórmula de Terzaghi (FS=3)"
        estimado = False
    else:
        # Perfil estimado Franco-arcilloso típico Liniers (BHCP baseline)
        q_a = float(SOIL_PROFILE["capacidad_portante_kpa"])
        terzaghi_detail = {"nota": "Valor estimado — SoilGrids no disponible"}
        sg = {
            "bdod_g_cm3": 1.45, "clay_pct": 35.0,
            "sand_pct": 25.0,   "silt_pct": 40.0,
            "ph": 6.5,          "raw": {},
        }
        fuente   = "[ESTIMADO] Perfil edafológico BHCP · Franco-arcilloso típico Liniers"
        estimado = True

    result = {
        "show_id":                show_id,
        "lat":                    lat,
        "lon":                    lon,
        "fecha_analisis":         datetime.now().strftime("%Y-%m-%d"),
        "estimado":               estimado,
        "fuente":                 fuente,
        "soilgrids": {
            "bulk_density_g_cm3": sg["bdod_g_cm3"],
            "clay_pct":           sg["clay_pct"],
            "sand_pct":           sg["sand_pct"],
            "silt_pct":           sg["silt_pct"],
            "ph":                 sg["ph"],
        },
        "terzaghi":               terzaghi_detail,
        "capacidad_portante_kpa": q_a,
    }

    out = MODELS_DIR / f"soil_real_{show_id}.json"
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    log.info("Soil real → %s  (%.0f kPa%s)", out, q_a, "  [ESTIMADO]" if estimado else "")
    return result


# ── Análisis de carga ─────────────────────────────────────────────────────────

def analyze_soil_load(rider: dict, lluvia_48h_mm: float = 0.0,
                      show_id: str = "unknown") -> dict:
    """
    Analiza la carga del suelo para las estructuras declaradas en el rider.
    Usa capacidad portante real de SoilGrids/Terzaghi; fallback a estimado BHCP.

    rider: {"estructuras": [{"nombre": str, "carga_ton": float, "area_m2": float}]}
    lluvia_48h_mm: lluvia acumulada 48h antes del show.
    show_id: identificador del show (para soil_real_{show_id}.json).
    """
    # Intentar cargar JSON ya guardado; si no, fetch en demanda
    real_json = MODELS_DIR / f"soil_real_{show_id}.json"
    estimado  = True

    if real_json.exists():
        try:
            data      = json.loads(real_json.read_text(encoding="utf-8"))
            cap_seco  = float(data["capacidad_portante_kpa"])
            fuente_b  = data["fuente"]
            estimado  = data.get("estimado", True)
        except Exception:
            cap_seco  = float(SOIL_PROFILE["capacidad_portante_kpa"])
            fuente_b  = "[ESTIMADO] Perfil edafológico BHCP"
    else:
        try:
            data      = fetch_real_soil_capacity(show_id, VENUE_LAT, VENUE_LON)
            cap_seco  = float(data["capacidad_portante_kpa"])
            fuente_b  = data["fuente"]
            estimado  = data.get("estimado", True)
        except Exception as exc:
            log.warning("fetch_real_soil_capacity error: %s", exc)
            cap_seco  = float(SOIL_PROFILE["capacidad_portante_kpa"])
            fuente_b  = "[ESTIMADO] Perfil edafológico BHCP"

    cap_sat = float(SOIL_PROFILE["capacidad_saturada_kpa"])

    if lluvia_48h_mm >= 30:
        capacidad = cap_sat
        condicion = "saturado"
    elif lluvia_48h_mm >= 10:
        capacidad = round(cap_seco * 0.80)
        condicion = "húmedo"
    else:
        capacidad = cap_seco
        condicion = "normal"

    estructuras = rider.get("estructuras") or [
        {
            "nombre":    z["name"],
            "carga_ton": z["carga_ton"],
            "area_m2":   z.get("w_m", 10) * z.get("h_m", 10),
        }
        for z in STAGE_ZONES
    ]

    alertas: list[str] = []
    zonas:   list[dict] = []

    for est in estructuras:
        carga = float(est.get("carga_ton", 0))
        area  = float(est.get("area_m2", 1))
        p_kpa = _presion_kpa(carga, area)
        clase = _classify(p_kpa, capacidad)

        zona = {
            "nombre":      est.get("nombre", "Estructura"),
            "carga_ton":   carga,
            "area_m2":     area,
            "presion_kpa": p_kpa,
            **clase,
        }
        zonas.append(zona)

        if clase["clase"] == "exclusion":
            alertas.append(
                f"⚠ EXCLUSIÓN: {zona['nombre']} — {p_kpa} kPa supera capacidad "
                f"({capacidad} kPa). Redistribuir o mover zona."
            )
        elif clase["clase"] == "precaucion":
            alertas.append(
                f"Precaución: {zona['nombre']} — {p_kpa} kPa. "
                "Usar durmientes (mínimo 4 m²/punto de apoyo)."
            )

    if condicion == "saturado":
        alertas.insert(0,
            f"Suelo saturado ({lluvia_48h_mm:.0f} mm en 48h) — "
            f"capacidad portante reducida a {capacidad} kPa"
        )
    elif condicion == "húmedo":
        alertas.insert(0,
            f"Suelo húmedo ({lluvia_48h_mm:.0f} mm en 48h) — "
            f"capacidad reducida a {capacidad} kPa"
        )

    excl = [z for z in zonas if z["clase"] == "exclusion"]

    return {
        "condicion_suelo":        condicion,
        "lluvia_48h_mm":          lluvia_48h_mm,
        "capacidad_efectiva_kpa": capacidad,
        "suelo_tipo":             SOIL_PROFILE["tipo"],
        "zonas":                  zonas,
        "alertas":                alertas,
        "hay_exclusiones":        len(excl) > 0,
        "n_exclusiones":          len(excl),
        "fuente":                 fuente_b + " · Modelo carga Faro Protocol",
        "soil_real_json":         str(MODELS_DIR / f"soil_real_{show_id}.json"),
    }
