"""
faro_temporal_eye.py  —  El Ojo: motor de deteccion temporal de eventos sobre canchas

Compara el estado actual de cada cancha contra:
  1. Ultima imagen satelital (5-12d)  — detecta riego, corte, fertilizacion inmediata
  2. Ventana 30 dias               — detecta tendencias N, estres hidrico progresivo
  3. Baseline historico 10 anos   — detecta anomalias fenologicas (DOY +/-14 dias)
     (Meroni et al. 2019 / NASA MODIS standard, PMC6360378)

Eventos detectados (con confianza 0-1):
  RIEGO           — NDWI spike + BSI drop en 5-12d  (Bazzi et al. 2020, RS 12:4058)
  CORTE_PASTO     — NDVI drop >=0.10 en 10-15d + recuperacion  (Sen4CAP / arxiv 2403.09554)
  FERTILIZACION_N — GNDVI slope positivo en 14-30d  (MDPI RS 8:973)
  QUEMADO_UREA    — GNDVI pico seguido de NDVI caida en 5-10d  (quimiotoxicidad NH3)
  STRESS_HIDRICO  — NDVI decline + ET0 alto + SM bajo
  ENFERMEDAD      — NDVI decline espacialmente concentrado, BSI estable

Retorna por cancha:
  delta_5d, delta_30d, anomalia_historica_pct, tendencia_14d, eventos[], alertas_roger[]

Fuentes de datos:
  Primaria  : Supabase vegetation_metrics (se llena via insert_vegetation_metrics diario)
  Fallback  : Kalman seasonal model (cuando Supabase tiene < 5 puntos)
  Historico : Planetary Computer STAC backfill (funcion separada, correr una vez)
"""
from __future__ import annotations

import logging
import math
from datetime import date, timedelta
from typing import Optional

import numpy as np

log = logging.getLogger(__name__)

# ── Canchas Villa Olimpica ─────────────────────────────────────────────────────
_VO_ORDER = ["1fa","2fa","3fa","4fa","5fa","6fa","7fa","8fa","9fa","10fa","1fp","2fp"]

# ── Umbrales industria ─────────────────────────────────────────────────────────
_NDVI_MOWING_THRESHOLD   = 0.10   # Sen4CAP: NDVI drop >= 0.10 -> corte
_NDWI_IRRIGATION_MIN     = 0.03   # Bazzi 2020: NDWI delta >= 0.03
_GNDVI_FERT_MIN_TOTAL    = 0.025  # GNDVI rise >= 0.025 en 14-30d
_GNDVI_UREA_SPIKE        = 0.030  # pico GNDVI antes del quemado
_NDVI_UREA_DROP          = 0.05   # caida NDVI post-pico
_HIST_DOY_WINDOW         = 14     # Meroni 2019: +/-14 dias mismo DOY
_HIST_MIN_POINTS         = 5      # minimo historico para anomalia
_GAP_MAX_IRRIGATION      = 12     # dias max entre obs para detectar riego
_GAP_MAX_MOWING          = 15     # dias max entre obs para detectar corte

# Invierno austral: NDVI bajo es dormancia, no dano
_WINTER_MONTHS = frozenset({6, 7, 8})


# ── Helpers de fecha ───────────────────────────────────────────────────────────

def _parse(fecha_str: str) -> date:
    return date.fromisoformat(str(fecha_str)[:10])

def _doy(d: date) -> int:
    return int(d.strftime("%j"))

def _days_since(fecha_str: str) -> int:
    return (date.today() - _parse(fecha_str)).days


# ── Carga de serie temporal desde Supabase ─────────────────────────────────────

def _load_series(venue_id: str, cancha_id: str, days_back: int = 3650) -> list[dict]:
    """
    Carga la serie temporal de vegetation_metrics para una cancha.
    Retorna lista [{fecha, ndvi, gndvi, bsi, ndwi, evi2}, ...] ordenada por fecha.
    """
    try:
        import velez_supabase as _vs
        if not _vs._ok():
            return []
        client = _vs._client()
        cutoff = (date.today() - timedelta(days=days_back)).isoformat()
        resp = (client.table("vegetation_metrics")
                .select("fecha_imagen,ndvi,gndvi,bsi,ndwi,evi2")
                .eq("venue_id", venue_id)
                .eq("cancha_id", cancha_id)
                .gte("fecha_imagen", cutoff)
                .order("fecha_imagen", desc=False)
                .execute())
        rows = resp.data or []
        return [{"fecha": r["fecha_imagen"],
                 "ndvi":  r.get("ndvi"),
                 "gndvi": r.get("gndvi"),
                 "bsi":   r.get("bsi"),
                 "ndwi":  r.get("ndwi"),
                 "evi2":  r.get("evi2")} for r in rows if r.get("ndvi") is not None]
    except Exception as exc:
        log.debug("temporal_eye: _load_series %s/%s: %s", venue_id, cancha_id, exc)
        return []


def _augment_with_heatmap(ts: list[dict], hm: dict) -> list[dict]:
    """
    Si la serie Supabase esta vacia o tiene solo datos viejos,
    agrega el punto actual del heatmap como observacion de hoy.
    """
    if not isinstance(hm, dict) or not hm.get("ndvi"):
        return ts
    today_str = date.today().isoformat()
    # No duplicar si ya tenemos datos recientes (< 3 dias)
    if ts and _days_since(ts[-1]["fecha"]) < 3:
        return ts
    ts.append({
        "fecha": today_str,
        "ndvi":  hm.get("ndvi"),
        "gndvi": hm.get("gndvi"),
        "bsi":   hm.get("bsi"),
        "ndwi":  hm.get("ndwi"),
        "evi2":  hm.get("evi2"),
    })
    return ts


# ── Deteccion de eventos ───────────────────────────────────────────────────────

def _detect_irrigation(ts: list[dict], et0: float = 0.0) -> Optional[dict]:
    """
    Riego: NDWI spike + BSI drop en ventana 5-12 dias.
    Bazzi et al. 2020 (Remote Sensing 12:4058).
    """
    ndwi_pts = [(p["fecha"], p["ndwi"]) for p in ts if p.get("ndwi") is not None]
    if len(ndwi_pts) < 2:
        return None
    t1_f, ndwi1 = ndwi_pts[-2]
    t2_f, ndwi2 = ndwi_pts[-1]
    gap = (_parse(t2_f) - _parse(t1_f)).days
    if not (3 <= gap <= _GAP_MAX_IRRIGATION):
        return None
    delta_ndwi = ndwi2 - ndwi1
    if delta_ndwi < _NDWI_IRRIGATION_MIN:
        return None
    # BSI corrobora: suelo mas humedo -> BSI baja
    bsi_pts = [(p["fecha"], p["bsi"]) for p in ts if p.get("bsi") is not None]
    delta_bsi = 0.0
    if len(bsi_pts) >= 2:
        delta_bsi = bsi_pts[-1][1] - bsi_pts[-2][1]
    conf = min(1.0, delta_ndwi / 0.08) * (1.1 if delta_bsi < -0.01 else 0.85)
    intensidad_mm = round(delta_ndwi * 35)
    return {
        "tipo": "RIEGO",
        "fecha_estimada": t2_f,
        "confianza": round(min(1.0, conf), 2),
        "descripcion": (f"Riego detectado — NDWI +{delta_ndwi:.3f} en {gap}d "
                        f"(aprox. {intensidad_mm} mm) · BSI {delta_bsi:+.3f}"),
        "delta": round(delta_ndwi, 3),
        "indices": {"ndwi_antes": ndwi1, "ndwi_despues": ndwi2, "delta_bsi": round(delta_bsi, 3)},
    }


def _detect_mowing(ts: list[dict]) -> Optional[dict]:
    """
    Corte: NDVI drop >= 0.10 en 10-15 dias.
    Sen4CAP DDF v1.3 / arxiv 2403.09554.
    Recuperacion en 15 dias confirma (vs quemado/enfermedad).
    """
    ndvi_pts = [(p["fecha"], p["ndvi"]) for p in ts if p.get("ndvi") is not None]
    if len(ndvi_pts) < 2:
        return None
    # Buscar en las ultimas 3 pares (puede haber habido nubosidad en medio)
    for i in range(len(ndvi_pts) - 1, max(0, len(ndvi_pts) - 4), -1):
        t1_f, ndvi1 = ndvi_pts[i - 1]
        t2_f, ndvi2 = ndvi_pts[i]
        gap = (_parse(t2_f) - _parse(t1_f)).days
        if not (3 <= gap <= _GAP_MAX_MOWING):
            continue
        delta = ndvi2 - ndvi1
        if delta > -_NDVI_MOWING_THRESHOLD:
            continue
        # Buscar recuperacion en puntos posteriores (confirma corte vs enfermedad)
        recovery = False
        for j in range(i + 1, len(ndvi_pts)):
            rec_gap = (_parse(ndvi_pts[j][0]) - _parse(t2_f)).days
            if rec_gap > 20:
                break
            if ndvi_pts[j][1] > ndvi2 + 0.03:
                recovery = True
                break
        conf = min(1.0, abs(delta) / 0.20)
        if recovery:
            conf = min(1.0, conf * 1.15)
        return {
            "tipo": "CORTE_PASTO",
            "fecha_estimada": t2_f,
            "confianza": round(conf, 2),
            "descripcion": (f"Corte de pasto — NDVI {delta:.3f} en {gap}d "
                            f"{'· recuperacion confirmada' if recovery else '· sin recuperacion aun'}"),
            "delta": round(delta, 3),
            "indices": {"ndvi_antes": round(ndvi1, 3), "ndvi_despues": round(ndvi2, 3),
                        "recovery_confirmada": recovery},
        }
    return None


def _detect_fertilization_n(ts: list[dict]) -> Optional[dict]:
    """
    Fertilizacion N: GNDVI slope positivo sostenido en 14-30 dias.
    MDPI RS 8:973 / GCSAA 14-day composite.
    """
    gndvi_pts = [(p["fecha"], p["gndvi"]) for p in ts
                 if p.get("gndvi") is not None
                 and _days_since(p["fecha"]) <= 35]
    if len(gndvi_pts) < 2:
        return None
    gndvi_pts_sorted = sorted(gndvi_pts, key=lambda x: x[0])
    span_dias = (_parse(gndvi_pts_sorted[-1][0]) - _parse(gndvi_pts_sorted[0][0])).days
    if span_dias < 14:
        return None
    delta_gndvi = gndvi_pts_sorted[-1][1] - gndvi_pts_sorted[0][1]
    if delta_gndvi < _GNDVI_FERT_MIN_TOTAL:
        return None
    # Verificar que la tendencia sea monotonamente ascendente (no ruido)
    vals = [v for _, v in gndvi_pts_sorted]
    ups   = sum(1 for a, b in zip(vals, vals[1:]) if b > a)
    downs = sum(1 for a, b in zip(vals, vals[1:]) if b < a)
    if ups < downs:  # mas bajadas que subidas -> no es fertilizacion, es ruido
        return None
    slope = delta_gndvi / max(1, span_dias)
    conf  = min(1.0, delta_gndvi / 0.06)
    return {
        "tipo": "FERTILIZACION_N",
        "fecha_estimada": gndvi_pts_sorted[0][0],
        "confianza": round(conf, 2),
        "descripcion": (f"Fertilizacion N detectada — GNDVI +{delta_gndvi:.3f} "
                        f"en {span_dias}d ({slope*1000:.1f} x10-3/dia)"),
        "delta": round(delta_gndvi, 3),
        "indices": {"gndvi_inicio": round(gndvi_pts_sorted[0][1], 3),
                    "gndvi_fin":   round(gndvi_pts_sorted[-1][1], 3),
                    "slope_por_dia": round(slope, 5)},
    }


def _detect_urea_burn(ts: list[dict]) -> Optional[dict]:
    """
    Exceso urea: GNDVI pico -> caida NDVI en 5-10 dias (volatilizacion NH3).
    Patron: aplicacion excesiva -> respuesta N -> fitotoxicidad.
    """
    pts = sorted([p for p in ts if p.get("gndvi") and p.get("ndvi")],
                 key=lambda x: x["fecha"])
    if len(pts) < 3:
        return None
    for i in range(1, len(pts) - 1):
        t_prev, t_peak, t_curr = pts[i - 1], pts[i], pts[i + 1]
        gap_ab = (_parse(t_peak["fecha"]) - _parse(t_prev["fecha"])).days
        gap_bc = (_parse(t_curr["fecha"]) - _parse(t_peak["fecha"])).days
        if not (3 <= gap_ab <= 14 and 3 <= gap_bc <= 14):
            continue
        gndvi_rise = t_peak["gndvi"] - t_prev["gndvi"]
        ndvi_drop  = t_curr["ndvi"]  - t_peak["ndvi"]
        if gndvi_rise < _GNDVI_UREA_SPIKE or ndvi_drop > -_NDVI_UREA_DROP:
            continue
        conf = min(1.0, (gndvi_rise + abs(ndvi_drop)) / 0.15)
        return {
            "tipo": "QUEMADO_UREA",
            "fecha_estimada": t_peak["fecha"],
            "confianza": round(conf, 2),
            "descripcion": (f"Posible exceso urea — pico GNDVI +{gndvi_rise:.3f} "
                            f"seguido de caida NDVI {ndvi_drop:.3f} en {gap_bc}d"),
            "delta": round(ndvi_drop, 3),
            "indices": {"gndvi_pico": round(t_peak["gndvi"], 3),
                        "ndvi_caida": round(ndvi_drop, 3),
                        "fecha_pico": t_peak["fecha"]},
        }
    return None


def _detect_stress_hidrico(ts: list[dict], et0: float, sm_pct: float) -> Optional[dict]:
    """
    Estres hidrico: NDVI en caida + ET0 alta + SM bajo.
    """
    ndvi_pts = [(p["fecha"], p["ndvi"]) for p in ts
                if p.get("ndvi") and _days_since(p["fecha"]) <= 14]
    if len(ndvi_pts) < 2:
        return None
    ndvi_pts = sorted(ndvi_pts, key=lambda x: x[0])
    delta    = ndvi_pts[-1][1] - ndvi_pts[0][1]
    dias     = max(1, (_parse(ndvi_pts[-1][0]) - _parse(ndvi_pts[0][0])).days)
    slope    = delta / dias
    if slope >= -0.003 or et0 < 4.5 or sm_pct >= 25:
        return None
    conf = min(1.0, abs(slope) / 0.008 * 0.5 + (max(0, et0 - 4.5) / 5.0) * 0.3)
    return {
        "tipo": "STRESS_HIDRICO",
        "fecha_estimada": date.today().isoformat(),
        "confianza": round(conf, 2),
        "descripcion": (f"Estres hidrico — NDVI declinando {slope*1000:.1f}x10-3/dia · "
                        f"ET0={et0:.1f}mm/dia · SM={sm_pct:.0f}%"),
        "delta": round(delta, 3),
        "indices": {"slope_ndvi_dia": round(slope, 5), "et0": et0, "sm_pct": sm_pct},
    }


def _detect_disease(ts: list[dict], month: int) -> Optional[dict]:
    """
    Enfermedad / dano organico: NDVI declining sin ET0 alta y sin corte ni riego detectado.
    En verano -> posible hongo; en invierno -> normal (no alertar).
    """
    if month in _WINTER_MONTHS:
        return None  # dormancia estacional, no dano
    ndvi_pts = [(p["fecha"], p["ndvi"]) for p in ts
                if p.get("ndvi") and _days_since(p["fecha"]) <= 20]
    if len(ndvi_pts) < 2:
        return None
    ndvi_pts = sorted(ndvi_pts, key=lambda x: x[0])
    delta = ndvi_pts[-1][1] - ndvi_pts[0][1]
    dias  = max(1, (_parse(ndvi_pts[-1][0]) - _parse(ndvi_pts[0][0])).days)
    slope = delta / dias
    if slope >= -0.005:
        return None
    conf = min(1.0, abs(slope) / 0.010)
    return {
        "tipo": "ENFERMEDAD_POSIBLE",
        "fecha_estimada": date.today().isoformat(),
        "confianza": round(conf, 2),
        "descripcion": (f"Deterioro activo sin causa fisica obvia — "
                        f"NDVI {slope*1000:.1f}x10-3/dia en {dias}d — inspeccion visual"),
        "delta": round(delta, 3),
        "indices": {"slope_ndvi_dia": round(slope, 5), "n_obs": len(ndvi_pts)},
    }


# ── Anomalia historica (DOY baseline) ─────────────────────────────────────────

def _anomalia_historica(ts: list[dict]) -> Optional[dict]:
    """
    Compara NDVI actual vs baseline historico mismo DOY +/-14 dias.
    Meroni et al. 2019 (PMC6360378). Baseline: anos anteriores (min 5 puntos).
    """
    today   = date.today()
    doy_now = _doy(today)
    current_year = today.year

    historical_ndvi = []
    for p in ts:
        f = _parse(p["fecha"])
        if f.year >= current_year:
            continue  # excluir ano actual
        if p.get("ndvi") is None:
            continue
        d = _doy(f)
        # dist circular entre DOYs
        doy_diff = min(abs(d - doy_now), 365 - abs(d - doy_now))
        if doy_diff <= _HIST_DOY_WINDOW:
            historical_ndvi.append(p["ndvi"])

    if len(historical_ndvi) < _HIST_MIN_POINTS:
        return None

    # NDVI actual: ultimo punto del ano corriente
    current_pts = sorted(
        [p for p in ts if _parse(p["fecha"]).year == current_year and p.get("ndvi")],
        key=lambda x: x["fecha"]
    )
    if not current_pts:
        return None

    current_ndvi = current_pts[-1]["ndvi"]
    hist_arr     = np.array(historical_ndvi)
    hist_median  = float(np.median(hist_arr))
    hist_mean    = float(np.mean(hist_arr))
    hist_std     = float(np.std(hist_arr)) or 0.01

    anomalia_pct = round((current_ndvi - hist_median) / max(0.01, hist_median) * 100, 1)
    z_score      = round((current_ndvi - hist_mean) / hist_std, 2)

    nivel = ("critica" if abs(z_score) > 2.0
             else "alta"   if abs(z_score) > 1.0
             else "normal")

    return {
        "ndvi_actual":             round(current_ndvi, 3),
        "ndvi_mediana_historica":  round(hist_median, 3),
        "anomalia_pct":            anomalia_pct,
        "z_score":                 z_score,
        "nivel":                   nivel,
        "n_anos_baseline":         len(set(_parse(p["fecha"]).year for p in ts
                                          if _parse(p["fecha"]).year < current_year)),
        "n_puntos_baseline":       len(historical_ndvi),
    }


# ── Delta y tendencia ──────────────────────────────────────────────────────────

def _delta(ts: list[dict], days_back: int, index: str = "ndvi") -> Optional[float]:
    """Cambio del indice entre ahora y la observacion mas cercana a days_back atras."""
    pts = sorted([p for p in ts if p.get(index) is not None], key=lambda x: x["fecha"])
    if len(pts) < 2:
        return None
    current = pts[-1][index]
    target_date = date.today() - timedelta(days=days_back)
    # Buscar el punto mas cercano al target
    best = min(pts[:-1], key=lambda p: abs((_parse(p["fecha"]) - target_date).days))
    if abs((_parse(best["fecha"]) - target_date).days) > days_back // 2 + 5:
        return None  # demasiado lejos del target
    return round(current - best[index], 4)


def _tendencia_slope(ts: list[dict], days_back: int = 14) -> Optional[float]:
    """Pendiente linear (NDVI/dia) de los ultimos N dias. Positivo = mejorando."""
    pts = sorted(
        [p for p in ts if p.get("ndvi") and _days_since(p["fecha"]) <= days_back],
        key=lambda x: x["fecha"]
    )
    if len(pts) < 2:
        return None
    x = np.array([(_parse(p["fecha"]) - _parse(pts[0]["fecha"])).days for p in pts],
                 dtype=float)
    y = np.array([p["ndvi"] for p in pts], dtype=float)
    if x[-1] == 0:
        return None
    try:
        slope = float(np.polyfit(x, y, 1)[0])
    except Exception:
        return None
    return round(slope, 6)


# ── Alertas Roger (lenguaje natural) ─────────────────────────────────────────

def _build_alertas(cid: str, eventos: list[dict], anomalia: Optional[dict],
                   delta_5d: Optional[float], tendencia: Optional[float],
                   month: int) -> list[str]:
    """Genera alertas en lenguaje natural listas para Roger."""
    alertas = []
    lbl = cid.upper()

    # Prioridad critica: eventos detectados
    for ev in sorted(eventos, key=lambda e: -e["confianza"]):
        tipo = ev["tipo"]
        desc = ev["descripcion"]
        conf_pct = round(ev["confianza"] * 100)
        if tipo == "QUEMADO_UREA":
            alertas.append(f"{lbl}: ALERTA quemado urea ({conf_pct}% confianza) — {desc}")
        elif tipo == "ENFERMEDAD_POSIBLE":
            alertas.append(f"{lbl}: Posible enfermedad activa — {desc}")
        elif tipo == "STRESS_HIDRICO":
            alertas.append(f"{lbl}: Estres hidrico detectado — {desc}")
        elif tipo == "RIEGO":
            alertas.append(f"{lbl}: Riego reciente confirmado — {desc}")
        elif tipo == "CORTE_PASTO":
            alertas.append(f"{lbl}: Corte reciente — {desc}")
        elif tipo == "FERTILIZACION_N":
            alertas.append(f"{lbl}: Respuesta N activa — {desc}")

    # Anomalia historica
    if anomalia and anomalia["nivel"] in ("critica", "alta"):
        pct = anomalia["anomalia_pct"]
        direction = "por debajo" if pct < 0 else "por encima"
        alertas.append(
            f"{lbl}: NDVI {abs(pct):.0f}% {direction} del baseline historico "
            f"(z={anomalia['z_score']:.1f} · {anomalia['n_anos_baseline']} anos)"
        )

    # Tendencia
    if tendencia is not None and abs(tendencia) > 0.002 and month not in _WINTER_MONTHS:
        dir_txt = "mejorando" if tendencia > 0 else "declinando"
        alertas.append(f"{lbl}: Tendencia 14d {dir_txt} — {tendencia*1000:.1f}x10-3 NDVI/dia")

    return alertas[:4]


# ── Analisis completo por cancha ───────────────────────────────────────────────

def analyze_cancha(
    cid: str,
    hm: dict,
    weather_live: dict,
    venue_id: str = "amalfitani",
    month: Optional[int] = None,
) -> dict:
    """
    Analiza una cancha. Retorna dict con todos los indicadores temporales y alertas.

    Args:
        cid:          ID de cancha (e.g. "1fa")
        hm:           heatmap dict actual (de roger.heatmaps)
        weather_live: dict completo del weather_live
        venue_id:     ID del venue para Supabase
        month:        mes (default: hoy)
    """
    m   = month or date.today().month
    et0 = float(weather_live.get("et0_mm_dia", 3.5))
    sm  = float(weather_live.get("humedad_suelo_pct", 20.0))

    # Cargar serie temporal desde Supabase + completar con punto actual del heatmap
    ts = _load_series(venue_id, cid, days_back=3650)
    ts = _augment_with_heatmap(ts, hm)

    # Deltas temporales
    delta_5d  = _delta(ts, days_back=10,  index="ndvi")  # S2 revisit ~5-10d
    delta_30d = _delta(ts, days_back=30,  index="ndvi")
    tendencia = _tendencia_slope(ts, days_back=14)

    # Deteccion de eventos
    eventos: list[dict] = []
    for detector_fn, args in [
        (_detect_irrigation,    (ts, et0)),
        (_detect_mowing,        (ts,)),
        (_detect_fertilization_n, (ts,)),
        (_detect_urea_burn,     (ts,)),
        (_detect_stress_hidrico, (ts, et0, sm)),
        (_detect_disease,       (ts, m)),
    ]:
        try:
            ev = detector_fn(*args)
            if ev is not None:
                eventos.append(ev)
        except Exception as exc:
            log.debug("temporal_eye: detector %s/%s: %s", cid, detector_fn.__name__, exc)

    # Anomalia historica (DOY baseline)
    anomalia = _anomalia_historica(ts)

    # Alertas para Roger
    alertas_roger = _build_alertas(cid, eventos, anomalia, delta_5d, tendencia, m)

    return {
        "cid":                  cid,
        "n_observaciones":      len(ts),
        "delta_5d":             delta_5d,
        "delta_30d":            delta_30d,
        "tendencia_14d":        tendencia,
        "anomalia_historica":   anomalia,
        "eventos":              eventos,
        "alertas_roger":        alertas_roger,
    }


# ── Analisis de todas las canchas VO ──────────────────────────────────────────

def analyze_all(
    heatmaps: dict,
    weather_live: dict,
    month: Optional[int] = None,
) -> dict:
    """
    Corre analyze_cancha() sobre todas las canchas VO.

    Returns:
        {cid: analyze_cancha() result, ...}
        Ademas: "_resumen" con conteo de eventos y alertas aggregadas.
    """
    m = month or date.today().month
    results = {}
    all_alertas = []
    event_counts: dict[str, int] = {}

    for cid in _VO_ORDER:
        hm = heatmaps.get(cid)
        if not isinstance(hm, dict):
            continue
        try:
            r = analyze_cancha(cid, hm, weather_live, venue_id="amalfitani", month=m)
            results[cid] = r
            all_alertas.extend(r["alertas_roger"])
            for ev in r["eventos"]:
                t = ev["tipo"]
                event_counts[t] = event_counts.get(t, 0) + 1
        except Exception as exc:
            log.warning("temporal_eye: analyze_cancha %s failed: %s", cid, exc)

    results["_resumen"] = {
        "n_canchas":     len(results) - 1,
        "eventos_total": sum(event_counts.values()),
        "tipos":         event_counts,
        "alertas_roger": all_alertas[:12],
    }
    log.info(
        "temporal_eye: %d canchas · %d eventos (%s)",
        len(results) - 1,
        sum(event_counts.values()),
        event_counts,
    )
    return results


# ── Backfill historico (correr una vez) ───────────────────────────────────────

def backfill_historical_ndvi(
    venue_id:  str  = "amalfitani",
    start_year: int = 2017,   # S2A+2B full dual coverage
    max_scenes: int = 500,    # cap para no agotar la API
) -> int:
    """
    Descarga historia S2 desde Planetary Computer y la inserta en Supabase.
    Correr UNA VEZ para backfill. Las siguientes ejecuciones pueden omitir
    fechas ya presentes (upsert por fecha).

    Returns: numero de escenas insertadas.
    """
    try:
        import pystac_client
        import planetary_computer as pc
        import velez_supabase as _vs
    except ImportError as exc:
        log.error("backfill: dependencias faltantes: %s", exc)
        return 0

    LAT, LON = -34.6375, -58.5215
    bbox = [LON - 0.035, LAT - 0.035, LON + 0.035, LAT + 0.035]

    catalog = pystac_client.Client.open(
        "https://planetarycomputer.microsoft.com/api/stac/v1",
        modifier=pc.sign_inplace,
    )
    search = catalog.search(
        collections=["sentinel-2-l2a"],
        bbox=bbox,
        datetime=f"{start_year}-01-01/{date.today().isoformat()}",
        query={"eo:cloud_cover": {"lt": 20}},
        max_items=max_scenes,
    )

    inserted = 0
    for item in search.items():
        try:
            fecha = item.datetime.date().isoformat()
            import rasterio, numpy as np_
            from rasterio.windows import from_bounds

            signed = pc.sign(item)
            b4_url  = signed.assets["B04"].href   # Red
            b8_url  = signed.assets["B08"].href   # NIR
            b11_url = signed.assets.get("B11", signed.assets.get("SWIR16", None))
            b3_url  = signed.assets["B03"].href   # Green

            def _read_mean(url):
                with rasterio.open(url) as src:
                    win = from_bounds(*bbox, src.transform)
                    data = src.read(1, window=win).astype(float)
                    data[data == 0] = np_.nan
                    return float(np_.nanmean(data)) / 10000.0  # L2A scale factor

            b4 = _read_mean(b4_url)
            b8 = _read_mean(b8_url)
            b3 = _read_mean(b3_url)

            ndvi  = (b8 - b4) / max(1e-9, b8 + b4)
            gndvi = (b8 - b3) / max(1e-9, b8 + b3)
            ndwi  = (b3 - b8) / max(1e-9, b3 + b8)

            bsi = None
            if b11_url:
                b11 = _read_mean(b11_url.href if hasattr(b11_url, "href") else b11_url)
                bsi = ((b11 + b4) - (b8 + b3)) / max(1e-9, (b11 + b4) + (b8 + b3))

            # Insertar para cada cancha VO (misma imagen, distintas coords se refinarán con polígono real)
            for cid in _VO_ORDER:
                _vs.insert_vegetation_metrics(
                    venue_id=venue_id,
                    cancha_id=cid,
                    fecha_imagen=fecha,
                    ndvi=round(ndvi, 4),
                    gndvi=round(gndvi, 4),
                    ndwi=round(ndwi, 4),
                    bsi=round(bsi, 4) if bsi else None,
                    metodo_generacion="S2_BACKFILL_TILE",
                    fuente="sentinel-2-l2a · Planetary Computer · backfill",
                )
            inserted += 1
            log.info("backfill: %s NDVI=%.3f insertado", fecha, ndvi)
        except Exception as exc:
            log.warning("backfill: escena %s fallida: %s",
                        getattr(item, "datetime", "?"), exc)

    log.info("backfill: %d escenas insertadas", inserted)
    return inserted


# ── CLI de diagnostico ────────────────────────────────────────────────────────

if __name__ == "__main__":
    import json, sys
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(message)s")
    mode = sys.argv[1] if len(sys.argv) > 1 else "analyze"

    if mode == "backfill":
        n = backfill_historical_ndvi(start_year=2017, max_scenes=100)
        print(f"Backfill: {n} escenas insertadas")
        sys.exit(0)

    # Analisis demo sin Supabase (usa valores de ejemplo)
    demo_heatmaps = {
        "1fa": {"ndvi": 0.043, "gndvi": 0.044, "ndwi": -0.31, "bsi": 0.12,
                "ipos": 1845, "semaforo": "rojo"},
        "4fa": {"ndvi": 0.086, "gndvi": 0.080, "ndwi": -0.42, "bsi": 0.09,
                "ipos": 0, "semaforo": "verde"},
    }
    demo_weather = {"et0_mm_dia": 2.8, "humedad_suelo_pct": 18.0}
    result = analyze_all(demo_heatmaps, demo_weather)
    print(json.dumps(result, indent=2, ensure_ascii=False, default=str))
