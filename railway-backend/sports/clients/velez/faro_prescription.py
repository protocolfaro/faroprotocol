"""
faro_prescription.py — Surgical prescription engine for Vélez turf management.
Per-zone (6 zones/cancha) agronomic prescriptions from real-time data:
ET₀, soil moisture, weather, IPOS traffic load, fungal disease risk, GDD.
"""
from __future__ import annotations
from datetime import datetime, timezone
import logging

log = logging.getLogger(__name__)

# FIFA wear research — GK areas take 2× peak load vs. center
ZONE_FACTORS: dict[str, float] = {
    "GK_N":   2.0,
    "AREA_N": 1.5,
    "MID_N":  1.0,
    "MID_S":  1.0,
    "AREA_S": 1.5,
    "GK_S":   2.0,
}

ZONE_LABELS: dict[str, str] = {
    "GK_N":   "Portería Norte",
    "AREA_N": "Área Penal Norte",
    "MID_N":  "Mediocampo Norte",
    "MID_S":  "Mediocampo Sur",
    "AREA_S": "Área Penal Sur",
    "GK_S":   "Portería Sur",
}

# SVG coordinate rects covering the field without gaps (viewBox="0 0 68 105")
# Field rectangle: x=3..65, y=3..102
ZONE_SVG: dict[str, dict] = {
    "GK_N":   {"x": 3.0,  "y": 3.0,   "w": 62.0, "h": 16.5},
    "AREA_N": {"x": 3.0,  "y": 19.5,  "w": 62.0, "h": 13.5},
    "MID_N":  {"x": 3.0,  "y": 33.0,  "w": 62.0, "h": 19.5},
    "MID_S":  {"x": 3.0,  "y": 52.5,  "w": 62.0, "h": 19.5},
    "AREA_S": {"x": 3.0,  "y": 72.0,  "w": 62.0, "h": 13.5},
    "GK_S":   {"x": 3.0,  "y": 85.5,  "w": 62.0, "h": 16.5},
}

URGENCY_ORDER: dict[str, int] = {
    "HOY":            0,
    "ESTA_SEMANA":    1,
    "PROXIMA_SEMANA": 2,
    "MONITOREAR":     3,
    "OK":             4,
}

URGENCY_COLORS: dict[str, str] = {
    "HOY":            "#EF4444",
    "ESTA_SEMANA":    "#F59E0B",
    "PROXIMA_SEMANA": "#3B82F6",
    "MONITOREAR":     "#6B7280",
    "OK":             "#22C55E",
}

ACTION_ICONS: dict[str, str] = {
    "AEREAR":    "⬤",
    "REGAR":     "◆",
    "FUNGICIDA": "▲",
    "FERTILIZAR":"■",
    "DRENAR":    "◈",
    "OK":        "✓",
}


# ── Action calculators ────────────────────────────────────────────────────────

def _aeration_actions(ipos_score: float, zone_factor: float) -> list[dict]:
    effective = ipos_score * zone_factor
    if effective > 300:
        return [{"type": "AEREAR", "urgency": "HOY",
                 "detail": f"Carga efectiva {effective:.0f} — compactación severa. Airear con púas hoy.",
                 "metric": f"IPOS×{zone_factor:.1f}={effective:.0f}"}]
    if effective > 200:
        return [{"type": "AEREAR", "urgency": "ESTA_SEMANA",
                 "detail": f"Carga acumulada alta ({effective:.0f}). Aireación esta semana.",
                 "metric": f"IPOS×{zone_factor:.1f}={effective:.0f}"}]
    if effective > 140:
        return [{"type": "AEREAR", "urgency": "PROXIMA_SEMANA",
                 "detail": f"Tráfico moderado ({effective:.0f}). Planificar aireación preventiva.",
                 "metric": f"IPOS×{zone_factor:.1f}={effective:.0f}"}]
    return []


def _irrigation_actions(wl: dict) -> list[dict]:
    deficit   = float(wl.get("deficit_hidrico_mm") or 0)
    hum_est   = wl.get("humedad_suelo_estado") or "normal"
    hum_pct   = float(wl.get("humedad_suelo_pct") or 18)
    lluvia    = float(wl.get("lluvia_48h_mm") or 0)
    riego_min = int(wl.get("riego_min_sector") or 0)
    hora      = wl.get("hora_riego_optima") or "06:00"

    if lluvia > 20:
        return []

    mins = f" — {riego_min} min/sector" if riego_min else ""
    if deficit > 10 and hum_est in ("seco", "bajo"):
        return [{"type": "REGAR", "urgency": "HOY",
                 "detail": f"Déficit {deficit:.1f} mm · suelo {hum_est} ({hum_pct:.0f}%). Regar hoy {hora}{mins}.",
                 "metric": f"déficit={deficit:.1f}mm · humedad={hum_pct:.0f}%",
                 "mm_recommended": deficit}]
    if deficit > 5 or hum_est == "seco":
        return [{"type": "REGAR", "urgency": "ESTA_SEMANA",
                 "detail": f"Déficit {deficit:.1f} mm · suelo {hum_est}{mins}.",
                 "metric": f"déficit={deficit:.1f}mm · humedad={hum_pct:.0f}%",
                 "mm_recommended": deficit}]
    if deficit > 2 or hum_est == "bajo":
        return [{"type": "REGAR", "urgency": "PROXIMA_SEMANA",
                 "detail": f"Déficit leve {deficit:.1f} mm — monitorear humedad.",
                 "metric": f"déficit={deficit:.1f}mm",
                 "mm_recommended": deficit}]
    return []


def _fungal_actions(wl: dict) -> list[dict]:
    riesgo = wl.get("riesgo_fungosis") or {}
    nivel  = riesgo.get("nivel") or "bajo"
    horas  = int(riesgo.get("horas_favorables_48h") or 0)
    enf    = riesgo.get("enfermedad") or "hongos"
    accion = riesgo.get("accion_recomendada") or ""

    if nivel == "alto":
        return [{"type": "FUNGICIDA", "urgency": "HOY",
                 "detail": accion or f"Riesgo ALTO {enf} — {horas}h favorables. Aplicar fungicida hoy.",
                 "metric": f"riesgo={nivel} · {horas}h favorables"}]
    if nivel == "medio":
        return [{"type": "FUNGICIDA", "urgency": "ESTA_SEMANA",
                 "detail": accion or f"Riesgo MEDIO {enf} — {horas}h favorables. Monitorear.",
                 "metric": f"riesgo={nivel} · {horas}h favorables"}]
    return []


def _fertilizer_actions(wl: dict) -> list[dict]:
    gdd_rate = float(wl.get("gdd_rate_diario") or 0)
    gndvi_d  = wl.get("gndvi_por_cancha") or {}
    canchas  = gndvi_d.get("canchas") or {}

    vals = [c.get("gndvi") for c in canchas.values() if c.get("gndvi") is not None]
    avg_gndvi = sum(vals) / len(vals) if vals else 0.42

    # Fertilize only during active growth (ryegrass base 5°C; BsAs winter still grows slowly)
    if avg_gndvi < 0.30 and gdd_rate > 0.8:
        return [{"type": "FERTILIZAR", "urgency": "HOY",
                 "detail": f"GNDVI {avg_gndvi:.2f} — deficiencia N grave. Fertilizar urgente.",
                 "metric": f"GNDVI={avg_gndvi:.2f} · GDD/día={gdd_rate:.1f}"}]
    if avg_gndvi < 0.38 and gdd_rate > 0.8:
        return [{"type": "FERTILIZAR", "urgency": "ESTA_SEMANA",
                 "detail": f"GNDVI {avg_gndvi:.2f} — déficit N moderado. Fertilizar esta semana.",
                 "metric": f"GNDVI={avg_gndvi:.2f} · GDD/día={gdd_rate:.1f}"}]
    if avg_gndvi < 0.45:
        return [{"type": "FERTILIZAR", "urgency": "MONITOREAR",
                 "detail": f"GNDVI {avg_gndvi:.2f} — N en límite. Monitorear crecimiento.",
                 "metric": f"GNDVI={avg_gndvi:.2f}"}]
    return []


def _drainage_actions(wl: dict) -> list[dict]:
    hum_pct = float(wl.get("humedad_suelo_pct") or 18)
    lluvia  = float(wl.get("lluvia_48h_mm") or 0)

    if lluvia > 35 and hum_pct > 35:
        return [{"type": "DRENAR", "urgency": "HOY",
                 "detail": f"Lluvia {lluvia:.0f}mm en 48h · suelo saturado ({hum_pct:.0f}%). Verificar drenaje.",
                 "metric": f"lluvia={lluvia:.0f}mm · humedad={hum_pct:.0f}%"}]
    if lluvia > 20 and hum_pct > 28:
        return [{"type": "DRENAR", "urgency": "ESTA_SEMANA",
                 "detail": f"Lluvia {lluvia:.0f}mm · humedad elevada ({hum_pct:.0f}%). Inspeccionar drenaje.",
                 "metric": f"lluvia={lluvia:.0f}mm · humedad={hum_pct:.0f}%"}]
    return []


# ── Zone + cancha computation ─────────────────────────────────────────────────

def compute_zone(zone_id: str, ipos_score: float, wl: dict) -> dict:
    factor   = ZONE_FACTORS.get(zone_id, 1.0)
    label    = ZONE_LABELS.get(zone_id, zone_id)
    svg_rect = ZONE_SVG.get(zone_id, {})

    actions: list[dict] = []
    actions.extend(_aeration_actions(ipos_score, factor))
    actions.extend(_irrigation_actions(wl))
    actions.extend(_fungal_actions(wl))
    actions.extend(_fertilizer_actions(wl))
    actions.extend(_drainage_actions(wl))

    # Sort by urgency — most urgent action drives zone color
    actions.sort(key=lambda a: URGENCY_ORDER.get(a.get("urgency", "OK"), 4))

    primary = actions[0] if actions else {
        "type": "OK", "urgency": "OK",
        "detail": "Sin intervención requerida.", "metric": "",
    }
    urgency = primary["urgency"]
    color   = URGENCY_COLORS.get(urgency, "#22C55E")

    return {
        "zone_id":  zone_id,
        "label":    label,
        "svg_rect": svg_rect,
        "color":    color,
        "urgency":  urgency,
        "icon":     ACTION_ICONS.get(primary["type"], "✓"),
        "actions":  actions,
        "primary":  primary,
    }


def compute_cancha(cancha_id: str, ipos_score: float, ipos_semaforo: str,
                   wl: dict, ndvi: float | None, heatmap_archivo: str | None) -> dict:
    zones = {zid: compute_zone(zid, ipos_score, wl) for zid in ZONE_FACTORS}

    worst_idx     = min(URGENCY_ORDER.get(z["urgency"], 4) for z in zones.values())
    worst_urgency = next(u for u, i in URGENCY_ORDER.items() if i == worst_idx)

    # Fungal risk elevates zones that match the risk cancha list
    fungal_nivel = (wl.get("riesgo_fungosis") or {}).get("nivel", "bajo")

    return {
        "id":              cancha_id,
        "ipos_score":      ipos_score,
        "ipos_semaforo":   ipos_semaforo,
        "ndvi":            ndvi,
        "heatmap_archivo": heatmap_archivo,
        "worst_urgency":   worst_urgency,
        "worst_color":     URGENCY_COLORS.get(worst_urgency, "#22C55E"),
        "fungal_nivel":    fungal_nivel,
        "zones":           zones,
    }


# ── Public entry point ────────────────────────────────────────────────────────

def generate_prescriptions(roger_canchas: list[dict], weather_live: dict) -> dict:
    """
    roger_canchas: list from assemble_report()['roger_canchas']
    weather_live:  dict from assemble_report()['weather_live']
    Returns complete prescriptions payload ready for JSON serialization.
    """
    canchas_out: dict[str, dict] = {}

    for cancha in roger_canchas:
        cid  = cancha.get("id") or cancha.get("cancha_id")
        if not cid:
            continue
        ipos_score   = float(cancha.get("ipos_score") or cancha.get("score") or 0)
        ipos_semaforo = cancha.get("semaforo") or "verde"
        ndvi          = cancha.get("ndvi")
        hm_archivo    = cancha.get("heatmap_archivo")

        canchas_out[cid] = compute_cancha(
            cid, ipos_score, ipos_semaforo, weather_live, ndvi, hm_archivo
        )

    wl = weather_live
    riesgo = wl.get("riesgo_fungosis") or {}
    summary = {
        "et0_mm_dia":          wl.get("et0_mm_dia"),
        "deficit_hidrico_mm":  wl.get("deficit_hidrico_mm"),
        "humedad_suelo_pct":   wl.get("humedad_suelo_pct"),
        "humedad_estado":      wl.get("humedad_suelo_estado"),
        "lluvia_48h_mm":       wl.get("lluvia_48h_mm"),
        "gdd_acumulado_7d":    wl.get("gdd_acumulado_7d"),
        "gdd_rate_diario":     wl.get("gdd_rate_diario"),
        "dias_proximo_corte":  wl.get("dias_proximo_corte"),
        "hora_riego_optima":   wl.get("hora_riego_optima"),
        "hora_riego_alt":      wl.get("hora_riego_alternativa"),
        "fungal_nivel":        riesgo.get("nivel"),
        "fungal_desc":         riesgo.get("descripcion"),
        "fungal_accion":       riesgo.get("accion_recomendada"),
        "fungal_horas_48h":    riesgo.get("horas_favorables_48h"),
        "temp_suelo_0cm":      wl.get("temp_suelo_0cm"),
        "timestamp":           wl.get("timestamp"),
    }

    return {
        "generated_at":   datetime.now(timezone.utc).isoformat(),
        "weather_summary": summary,
        "canchas":         canchas_out,
        "zone_colors":     URGENCY_COLORS,
        "zone_svg":        ZONE_SVG,
        "zone_labels":     ZONE_LABELS,
    }
