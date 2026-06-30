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

def _aeration_actions(ipos_score: float, zone_factor: float, wl: dict | None = None) -> list[dict]:
    effective  = ipos_score * zone_factor
    sar_change = float((wl or {}).get("sar_vv_change_6d") or 0)
    # SAR change < -1.5 dB in 6d signals significant drying → compaction risk elevated
    sar_flag   = sar_change < -1.5
    if effective > 300 or (effective > 200 and sar_flag):
        return [{"type": "AEREAR", "urgency": "HOY",
                 "detail": f"Carga efectiva {effective:.0f} — compactación severa. Airear con púas hoy.",
                 "metric": f"IPOS×{zone_factor:.1f}={effective:.0f}",
                 "razon":  f"Carga efectiva {effective:.0f} pts · SAR cambio {sar_change:+.1f} dB en 6d"}]
    if effective > 200:
        return [{"type": "AEREAR", "urgency": "ESTA_SEMANA",
                 "detail": f"Carga acumulada alta ({effective:.0f}). Aireación esta semana.",
                 "metric": f"IPOS×{zone_factor:.1f}={effective:.0f}",
                 "razon":  f"Carga efectiva {effective:.0f} pts supera umbral 200"}]
    if effective > 140:
        return [{"type": "AEREAR", "urgency": "PROXIMA_SEMANA",
                 "detail": f"Tráfico moderado ({effective:.0f}). Planificar aireación preventiva.",
                 "metric": f"IPOS×{zone_factor:.1f}={effective:.0f}",
                 "razon":  f"Carga efectiva {effective:.0f} pts (moderada)"}]
    return []


def _irrigation_actions(wl: dict) -> list[dict]:
    deficit    = float(wl.get("deficit_hidrico_mm") or 0)
    hum_est    = wl.get("humedad_suelo_estado") or "normal"
    hum_pct    = float(wl.get("humedad_suelo_pct") or 18)
    lluvia     = float(wl.get("lluvia_48h_mm") or 0)
    riego_min  = int(wl.get("riego_min_sector") or 0)
    hora       = wl.get("hora_riego_optima") or "06:00"
    et0_sem    = float(wl.get("et0_semana_mm") or wl.get("et0_acumulado_7d") or 0)
    sar_change = float(wl.get("sar_vv_change_6d") or 0)

    if lluvia > 20:
        return []

    mins = f" — {riego_min} min/sector" if riego_min else ""
    # SAR drying signal amplifies urgency: change < -1.5 dB = measurable drying
    sar_dry = sar_change < -1.5
    razon_base = (f"ET₀ acum. {et0_sem:.0f}mm · déficit {deficit:.1f}mm"
                  + (f" · SAR {sar_change:+.1f} dB" if sar_change != 0 else ""))
    if deficit > 10 and hum_est in ("seco", "bajo"):
        return [{"type": "REGAR", "urgency": "HOY",
                 "detail": f"Déficit {deficit:.1f} mm · suelo {hum_est} ({hum_pct:.0f}%). Regar hoy {hora}{mins}.",
                 "metric": f"déficit={deficit:.1f}mm · ET₀ sem={et0_sem:.0f}mm",
                 "razon":  razon_base + f" · suelo {hum_est}",
                 "mm_recommended": deficit}]
    if deficit > 5 or hum_est == "seco" or (sar_dry and deficit > 3):
        return [{"type": "REGAR", "urgency": "ESTA_SEMANA",
                 "detail": f"Déficit {deficit:.1f} mm · suelo {hum_est}{mins}.",
                 "metric": f"déficit={deficit:.1f}mm · ET₀ sem={et0_sem:.0f}mm",
                 "razon":  razon_base,
                 "mm_recommended": deficit}]
    if deficit > 2 or hum_est == "bajo":
        return [{"type": "REGAR", "urgency": "PROXIMA_SEMANA",
                 "detail": f"Déficit leve {deficit:.1f} mm — monitorear humedad.",
                 "metric": f"déficit={deficit:.1f}mm",
                 "razon":  f"Déficit leve {deficit:.1f}mm · ET₀ diaria {wl.get('et0_mm_dia') or 0:.1f}mm",
                 "mm_recommended": deficit}]
    return []


def _fungal_actions(wl: dict) -> list[dict]:
    riesgo  = wl.get("riesgo_fungosis") or {}
    nivel   = riesgo.get("nivel") or "bajo"
    horas   = int(riesgo.get("horas_favorables_48h") or 0)
    enf     = riesgo.get("enfermedad") or "hongos"
    accion  = riesgo.get("accion_recomendada") or ""
    sk_pct  = float(wl.get("smith_kerns_pct") or 0)
    razon   = f"Smith-Kerns {sk_pct:.0f}% · {horas}h T°/HR favorables"

    if nivel == "alto":
        return [{"type": "FUNGICIDA", "urgency": "HOY",
                 "detail": accion or f"Riesgo ALTO {enf} — {horas}h favorables. Aplicar fungicida hoy.",
                 "metric": f"Smith-Kerns={sk_pct:.0f}% · {horas}h favorables",
                 "razon":  razon}]
    if nivel == "medio":
        return [{"type": "FUNGICIDA", "urgency": "ESTA_SEMANA",
                 "detail": accion or f"Riesgo MEDIO {enf} — {horas}h favorables. Monitorear.",
                 "metric": f"Smith-Kerns={sk_pct:.0f}% · {horas}h favorables",
                 "razon":  razon}]
    return []


def _fertilizer_actions(wl: dict) -> list[dict]:
    gdd_rate = float(wl.get("gdd_rate_diario") or 0)
    gndvi_d  = wl.get("gndvi_por_cancha") or {}
    canchas  = gndvi_d.get("canchas") or {}

    vals = [c.get("gndvi") for c in canchas.values() if c.get("gndvi") is not None]
    avg_gndvi = sum(vals) / len(vals) if vals else 0.42

    if avg_gndvi < 0.30 and gdd_rate > 0.8:
        return [{"type": "FERTILIZAR", "urgency": "HOY",
                 "detail": f"GNDVI {avg_gndvi:.2f} — deficiencia N grave. Fertilizar urgente.",
                 "metric": f"GNDVI={avg_gndvi:.2f} · GDD/día={gdd_rate:.1f}",
                 "razon":  f"GNDVI {avg_gndvi:.2f} < 0.30 con crecimiento activo ({gdd_rate:.1f}°C/d)"}]
    if avg_gndvi < 0.38 and gdd_rate > 0.8:
        return [{"type": "FERTILIZAR", "urgency": "ESTA_SEMANA",
                 "detail": f"GNDVI {avg_gndvi:.2f} — déficit N moderado. Fertilizar esta semana.",
                 "metric": f"GNDVI={avg_gndvi:.2f} · GDD/día={gdd_rate:.1f}",
                 "razon":  f"GNDVI {avg_gndvi:.2f} bajo umbral 0.38 · GDD {gdd_rate:.1f}°C/d"}]
    if avg_gndvi < 0.45:
        return [{"type": "FERTILIZAR", "urgency": "MONITOREAR",
                 "detail": f"GNDVI {avg_gndvi:.2f} — N en límite. Monitorear crecimiento.",
                 "metric": f"GNDVI={avg_gndvi:.2f}",
                 "razon":  f"GNDVI {avg_gndvi:.2f} próximo al umbral mínimo 0.45"}]
    return []


def _drainage_actions(wl: dict) -> list[dict]:
    hum_pct    = float(wl.get("humedad_suelo_pct") or 18)
    lluvia     = float(wl.get("lluvia_48h_mm") or 0)
    sar_change = float(wl.get("sar_vv_change_6d") or 0)
    # SAR VV increase > +1.5 dB after rain = surface wetness anomaly → drainage check
    sar_wet    = sar_change > 1.5

    if lluvia > 35 and hum_pct > 35:
        return [{"type": "DRENAR", "urgency": "HOY",
                 "detail": f"Lluvia {lluvia:.0f}mm en 48h · suelo saturado ({hum_pct:.0f}%). Verificar drenaje.",
                 "metric": f"lluvia={lluvia:.0f}mm · humedad={hum_pct:.0f}%",
                 "razon":  f"Lluvia {lluvia:.0f}mm + suelo saturado {hum_pct:.0f}% + SAR {sar_change:+.1f} dB"}]
    if lluvia > 20 and hum_pct > 28:
        return [{"type": "DRENAR", "urgency": "ESTA_SEMANA",
                 "detail": f"Lluvia {lluvia:.0f}mm · humedad elevada ({hum_pct:.0f}%). Inspeccionar drenaje.",
                 "metric": f"lluvia={lluvia:.0f}mm · humedad={hum_pct:.0f}%",
                 "razon":  f"Lluvia {lluvia:.0f}mm + humedad {hum_pct:.0f}%"}]
    if sar_wet and lluvia > 10:
        return [{"type": "DRENAR", "urgency": "MONITOREAR",
                 "detail": f"SAR detecta humedad elevada post-lluvia ({lluvia:.0f}mm). Monitorear drenaje.",
                 "metric": f"SAR cambio={sar_change:+.1f} dB · lluvia={lluvia:.0f}mm",
                 "razon":  f"SAR VV aumentó {sar_change:+.1f} dB post-lluvia {lluvia:.0f}mm"}]
    return []


# ── Zone + cancha computation ─────────────────────────────────────────────────

def compute_zone(zone_id: str, ipos_score: float, wl: dict) -> dict:
    factor   = ZONE_FACTORS.get(zone_id, 1.0)
    label    = ZONE_LABELS.get(zone_id, zone_id)
    svg_rect = ZONE_SVG.get(zone_id, {})

    actions: list[dict] = []
    actions.extend(_aeration_actions(ipos_score, factor, wl))
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
        "et0_acumulado_7d":    wl.get("et0_semana_mm") or wl.get("et0_acumulado_7d"),
        "deficit_hidrico_mm":  wl.get("deficit_hidrico_mm"),
        "deficit_mm":          wl.get("deficit_hidrico_mm"),   # alias v3
        "humedad_suelo_pct":   wl.get("humedad_suelo_pct"),
        "humedad_estado":      wl.get("humedad_suelo_estado"),
        "lluvia_48h_mm":       wl.get("lluvia_48h_mm"),
        "precip_acumulada_7d": wl.get("precipitacion_semana_mm"),
        "gdd_acumulado_7d":    wl.get("gdd_acumulado_7d"),
        "gdd_rate_diario":     wl.get("gdd_rate_diario"),
        "dias_proximo_corte":  wl.get("dias_proximo_corte"),
        "hora_riego_optima":   wl.get("hora_riego_optima"),
        "hora_riego_alt":      wl.get("hora_riego_alternativa"),
        "fungal_nivel":        riesgo.get("nivel"),
        "fungal_desc":         riesgo.get("descripcion"),
        "fungal_accion":       riesgo.get("accion_recomendada"),
        "fungal_horas_48h":    riesgo.get("horas_favorables_48h"),
        "smith_kerns_pct":     wl.get("smith_kerns_pct"),
        "temp_suelo_0cm":      wl.get("temp_suelo_0cm"),
        "timestamp":           wl.get("timestamp"),
        # SAR C-band — Sentinel-1 backscatter + computed change fields
        "sar_vv_db":           wl.get("sar_vv_db"),
        "sar_vh_db":           wl.get("sar_vh_db"),
        "sar_vv_change_6d":    wl.get("sar_vv_change_6d"),
        "vh_vv_ratio":         wl.get("vh_vv_ratio"),
        "vh_vv_change_1d":     wl.get("vh_vv_change_1d"),
        "sar_humedad_relativa": wl.get("sar_humedad_relativa"),
        "insar_mm":            wl.get("insar_mm"),
        "sar_fecha":           wl.get("sar_fecha"),
        # ERA5-Land soil moisture layers (m³/m³ — multiply ×100 for %)
        "era5_sm_0_7cm":       wl.get("era5_sm_0_7cm"),
        "era5_sm_7_28cm":      wl.get("era5_sm_7_28cm"),
        "era5_sm_28_100cm":    wl.get("era5_sm_28_100cm"),
        "era5_sm_100_289cm":   wl.get("era5_sm_100_289cm"),
    }

    return {
        "generated_at":   datetime.now(timezone.utc).isoformat(),
        "weather_summary": summary,
        "canchas":         canchas_out,
        "zone_colors":     URGENCY_COLORS,
        "zone_svg":        ZONE_SVG,
        "zone_labels":     ZONE_LABELS,
    }
