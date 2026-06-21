"""
bonillo_semaforo.py — UEFA/STRI pitch quality semáforo (Bonillo 2024)
Faro Protocol · Vélez Sarsfield — Pure functions, no I/O.

Source: Informe Final Prof. Bonillo — Calidad Césped
        (Roger Matias Bernal, Ing. Agrónomo — UEFA/STRI standard)

Thresholds per metric:
  Compactación CG : 70-90 verde, 60-100 amarillo, else rojo
  Tracción Nm     : ≥30 verde,   ≥20 amarillo,    else rojo
  Altura mm       : 24-28 verde, 20-30 amarillo,   else rojo
  Humedad %       : 20-30 verde, 18-32 amarillo,   else rojo
  Raíces mm       : >85 verde,   ≥60 amarillo,     else rojo
"""
from __future__ import annotations
from typing import Optional


# ── Threshold functions ───────────────────────────────────────────────────────

def sem_compactacion(cg: float) -> str:
    """Clegg Impact Value in Gravities (CG). UEFA/STRI optimal: 70-90 CG."""
    if 70 <= cg <= 90:  return "verde"
    if 60 <= cg <= 100: return "amarillo"
    return "rojo"

def sem_traccion(nm: float) -> str:
    """Torque wrench in Nm. ≥30 Nm = match standard."""
    if nm >= 30: return "verde"
    if nm >= 20: return "amarillo"
    return "rojo"

def sem_altura(mm: float) -> str:
    """Grass height in mm. 24-28 mm = UEFA match range."""
    if 24 <= mm <= 28: return "verde"
    if 20 <= mm <= 30: return "amarillo"
    return "rojo"

def sem_humedad(pct: float) -> str:
    """Soil moisture %. 20-30% = optimal."""
    if 20 <= pct <= 30: return "verde"
    if 18 <= pct <= 32: return "amarillo"
    return "rojo"

def sem_raices(mm: float) -> str:
    """Root depth in mm. >85 mm = healthy rooting."""
    if mm > 85:  return "verde"
    if mm >= 60: return "amarillo"
    return "rojo"


# ── Metric descriptors ────────────────────────────────────────────────────────
# (key, col_header, unit, semaforo_fn, verde_range_str)
METRICS: list[tuple] = [
    ("compactacion", "Compact.", "CG", sem_compactacion, "70-90"),
    ("traccion",     "Traccion", "Nm", sem_traccion,     ">=30"),
    ("altura",       "Altura",   "mm", sem_altura,       "24-28"),
    ("humedad",      "Humedad",  "%",  sem_humedad,      "20-30"),
    ("raices",       "Raices",   "mm", sem_raices,       ">85"),
]

_FN_MAP = {m[0]: m[3] for m in METRICS}


def classify(key: str, val: float) -> str:
    """Return semaforo color string for a single metric+value pair."""
    fn = _FN_MAP.get(key)
    return fn(val) if fn else "amarillo"


# ── VD extraction ─────────────────────────────────────────────────────────────

def extract_from_vd(vd: dict) -> dict:
    """
    Read physical field measurements from velez_data.json.
    Returns {cancha_id_lower: {metric_key: float|None}}.

    Only canchas with >=1 measurement appear in the result.
    Currently wired: 'compactacion' via roger.mediciones.clegg (valor_cg in CG).
    Other metrics (traccion, altura, humedad, raices) return None until their
    collection endpoints are created.

    Clegg list is newest-first (push_clegg_medicion prepends). We take the
    first (most recent) entry per cancha.
    """
    roger = vd.get("usuarios", {}).get("roger", {})
    meds  = roger.get("mediciones", {})

    clegg_map: dict[str, Optional[float]] = {}
    for m in meds.get("clegg", []):
        zona = (m.get("zona") or "").lower()
        if zona and zona not in clegg_map:
            val = m.get("valor_cg")
            clegg_map[zona] = float(val) if val is not None else None

    if not clegg_map:
        return {}

    return {
        cid: {
            "compactacion": clegg_map[cid],
            "traccion": None,
            "altura":   None,
            "humedad":  None,
            "raices":   None,
        }
        for cid in clegg_map
    }
