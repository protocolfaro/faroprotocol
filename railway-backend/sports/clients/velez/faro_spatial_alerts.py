"""
faro_spatial_alerts.py
======================
Módulo de alertas espaciales quirúrgicas para Vélez Sarsfield.

Combina:
  - focos_reales (posición de píxeles NDVI bajos desde satellite_pipeline)
  - CUSUM temporal change detection en SAR VV time series (últimos 30 días)
  - ERA5 soil moisture como fuente de confianza adicional

Output: spatial_alerts list por cancha, compatible con panel Roger.
"""
import os
import logging
from datetime import date, timedelta

try:
    import requests as _requests
except ImportError:
    _requests = None

log = logging.getLogger(__name__)

SUPABASE_URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "")


# ── Colores por tipo ──────────────────────────────────────────────────────────

_COLORS = {
    "NDVI_BAJO":     "#ef4444",   # rojo
    "COMPACTACION":  "#f59e0b",   # ámbar
    "STRESS_COMBO":  "#a855f7",   # violeta
    "STRESS_HIDRICO":"#3b82f6",   # azul
}

_URGENCY_ORDER = ["HOY", "ESTA_SEMANA", "PROXIMA_SEMANA", "OK"]


# ── CUSUM change detection ────────────────────────────────────────────────────

def _cusum_changepoint(series: list) -> dict:
    """
    CUSUM two-sided change detection en serie temporal VV dB.
    Detecta el primer punto donde la serie se desvía significativamente de la baseline.
    """
    if len(series) < 4:
        return {"change_detected": False}
    n = len(series)
    half = max(2, n // 2)
    baseline = series[:half]
    mu = sum(baseline) / len(baseline)
    sigma = max(0.3, (sum((x - mu) ** 2 for x in baseline) / len(baseline)) ** 0.5)
    k = 0.5 * sigma
    h = 3.0 * sigma
    cusum_pos = cusum_neg = 0.0
    change_idx = direction = None
    for i in range(half, n):
        cusum_pos = max(0.0, cusum_pos + (series[i] - mu) - k)
        cusum_neg = max(0.0, cusum_neg - (series[i] - mu) - k)
        if cusum_pos > h and direction is None:
            change_idx = i
            direction = "up"   # VV aumenta → drying/compactación
            break
        if cusum_neg > h and direction is None:
            change_idx = i
            direction = "down"  # VV baja → wet/saturado
            break
    if change_idx is None:
        return {"change_detected": False}
    return {
        "change_detected": True,
        "dias_activo": n - change_idx,
        "direction": direction,
        "delta_db": round(series[-1] - mu, 2),
        "baseline_db": round(mu, 2),
    }


# ── Query SAR VV time series desde Supabase REST ─────────────────────────────

def _fetch_sar_series(venue_id: str, cancha_id: str | None) -> list:
    """
    Fetch SAR VV time series de soil_metrics para los últimos 30 días.
    Devuelve lista de floats ordenada oldest-first (deduplicada por fecha, último valor del día).
    """
    if not SUPABASE_URL or not SUPABASE_KEY or _requests is None:
        return []
    since = (date.today() - timedelta(days=30)).isoformat()
    if cancha_id == "amalfitani" or cancha_id is None:
        cid_filter = "cancha_id=is.null"
        vid_filter = f"venue_id=eq.amalfitani"
    else:
        cid_filter = f"cancha_id=eq.{cancha_id}"
        vid_filter = f"venue_id=eq.{venue_id}"
    url = (
        f"{SUPABASE_URL}/rest/v1/soil_metrics"
        f"?{vid_filter}&{cid_filter}"
        f"&created_at=gte.{since}"
        f"&order=created_at.asc"
        f"&select=cancha_id,sar_vv_db,fecha_imagen"
        f"&limit=50"
    )
    try:
        resp = _requests.get(
            url,
            headers={
                "apikey": SUPABASE_KEY,
                "Authorization": f"Bearer {SUPABASE_KEY}",
            },
            timeout=5,
        )
        if not resp.ok:
            log.debug("spatial_alerts: soil_metrics query HTTP %s", resp.status_code)
            return []
        rows = resp.json()
        # Deduplicar por fecha (fecha_imagen o fecha de created_at), último valor del día
        by_date: dict = {}
        for r in rows:
            vv = r.get("sar_vv_db")
            if vv is None:
                continue
            fecha = (r.get("fecha_imagen") or r.get("created_at", ""))[:10]
            if not fecha:
                continue
            by_date[fecha] = float(vv)
        # Retornar ordenado oldest-first
        return [by_date[k] for k in sorted(by_date.keys())]
    except Exception as e:
        log.debug("spatial_alerts: _fetch_sar_series error: %s", e)
        return []


# ── Clasificación y construcción de alertas ──────────────────────────────────

def _urgency_from_ndvi(ndvi: float) -> str:
    if ndvi < 0.25:
        return "HOY"
    if ndvi < 0.40:
        return "ESTA_SEMANA"
    return "OK"


def _urgency_from_cusum(delta_db: float) -> str:
    if abs(delta_db) > 2.0:
        return "HOY"
    if abs(delta_db) > 1.0:
        return "ESTA_SEMANA"
    return "PROXIMA_SEMANA"


def _worst_urgency(a: str, b: str) -> str:
    ia = _URGENCY_ORDER.index(a) if a in _URGENCY_ORDER else 3
    ib = _URGENCY_ORDER.index(b) if b in _URGENCY_ORDER else 3
    return _URGENCY_ORDER[min(ia, ib)]


def _build_ndvi_alerts(focos: list) -> list:
    """Construye alertas tipo NDVI_BAJO desde focos_reales."""
    alerts = []
    for f in focos:
        ndvi = f.get("ndvi")
        if not isinstance(ndvi, (int, float)):
            continue
        ndvi = float(ndvi)
        urgencia = _urgency_from_ndvi(ndvi)
        if urgencia == "OK":
            continue
        alerts.append({
            "x_frac":       float(f.get("x", 0.5)),
            "y_frac":       float(f.get("y", 0.5)),
            "radius_frac":  0.05,
            "tipo":         "NDVI_BAJO",
            "label":        f"NDVI {ndvi:.2f}",
            "sub_label":    f"NDVI {ndvi:.2f}",
            "urgencia":     urgencia,
            "color":        _COLORS["NDVI_BAJO"],
            "confianza_pct": 55,
            "fuentes":      ["ndvi"],
            "dias_activo":  0,
            "_ndvi":        ndvi,
        })
    return alerts


def _build_cusum_alert(cusum: dict) -> dict | None:
    """Construye alerta COMPACTACION desde resultado CUSUM (direction='up')."""
    if not cusum.get("change_detected") or cusum.get("direction") != "up":
        return None
    delta_db = cusum["delta_db"]
    if delta_db <= 0:
        return None
    urgencia = _urgency_from_cusum(delta_db)
    return {
        "x_frac":       0.5,
        "y_frac":       0.5,
        "radius_frac":  0.05,
        "tipo":         "COMPACTACION",
        "label":        "COMPACT. SAR",
        "sub_label":    f"SAR +{delta_db:.1f}dB",
        "urgencia":     urgencia,
        "color":        _COLORS["COMPACTACION"],
        "confianza_pct": 45,
        "fuentes":      ["sar"],
        "dias_activo":  cusum.get("dias_activo", 0),
        "_delta_db":    delta_db,
    }


def _merge_combo_alerts(ndvi_alerts: list, cusum_alert: dict | None, has_era5: bool) -> list:
    """
    Fusiona alertas NDVI + SAR en STRESS_COMBO cuando ambas existen en la misma cancha.
    Si solo hay una fuente, devuelve la alerta individual con confianza actualizada.
    """
    result = []

    if ndvi_alerts and cusum_alert:
        # Seleccionar el foco NDVI más crítico (NDVI más bajo)
        worst_foco = min(ndvi_alerts, key=lambda a: a["_ndvi"])
        base_conf = 76
        if has_era5:
            base_conf = min(91, base_conf + 15)
        combo = {
            "x_frac":       worst_foco["x_frac"],
            "y_frac":       worst_foco["y_frac"],
            "radius_frac":  0.06,
            "tipo":         "STRESS_COMBO",
            "label":        f"NDVI {worst_foco['_ndvi']:.2f}",
            "sub_label":    cusum_alert["sub_label"],
            "urgencia":     _worst_urgency(worst_foco["urgencia"], cusum_alert["urgencia"]),
            "color":        _COLORS["STRESS_COMBO"],
            "confianza_pct": base_conf,
            "fuentes":      ["ndvi", "sar"] + (["era5"] if has_era5 else []),
            "dias_activo":  cusum_alert["dias_activo"],
        }
        result.append(combo)
        # Agregar focos NDVI adicionales (excluir el peor ya incluido en combo)
        for a in ndvi_alerts:
            if a is not worst_foco:
                a_out = {k: v for k, v in a.items() if not k.startswith("_")}
                if has_era5:
                    a_out["confianza_pct"] = min(91, a_out["confianza_pct"] + 15)
                    if "era5" not in a_out["fuentes"]:
                        a_out["fuentes"] = a_out["fuentes"] + ["era5"]
                result.append(a_out)
    else:
        # Solo NDVI
        for a in ndvi_alerts:
            a_out = {k: v for k, v in a.items() if not k.startswith("_")}
            if has_era5:
                a_out["confianza_pct"] = min(91, a_out["confianza_pct"] + 15)
                if "era5" not in a_out["fuentes"]:
                    a_out["fuentes"] = a_out["fuentes"] + ["era5"]
            result.append(a_out)
        # Solo SAR
        if cusum_alert:
            a_out = {k: v for k, v in cusum_alert.items() if not k.startswith("_")}
            if has_era5:
                a_out["confianza_pct"] = min(91, a_out["confianza_pct"] + 15)
                if "era5" not in a_out["fuentes"]:
                    a_out["fuentes"] = a_out["fuentes"] + ["era5"]
            result.append(a_out)

    return result


# ── Función principal ─────────────────────────────────────────────────────────

def compute_spatial_alerts(vd: dict) -> dict:
    """
    Para cada cancha en vd['roger_canchas'], computa spatial_alerts.
    Retorna dict: { cid: [spatial_alert, ...] }
    """
    result: dict = {}
    heatmaps = vd.get("usuarios", {}).get("roger", {}).get("heatmaps", {})
    roger_canchas = vd.get("roger_canchas", [])

    for cancha in roger_canchas:
        cid = cancha.get("id") or cancha.get("cancha_id")
        if not cid:
            continue
        try:
            # Determinar venue_id para query
            venue = cancha.get("venue", "villa_olimpica")
            if cid == "amalfitani":
                venue_id_q = "amalfitani"
                cancha_id_q = None
            else:
                venue_id_q = "villa_olimpica"
                cancha_id_q = cid

            # 1. focos_reales desde heatmaps
            hm = heatmaps.get(cid, {})
            focos = cancha.get("focos_reales") or hm.get("focos_reales") or []
            if not isinstance(focos, list):
                focos = []

            # 2. SAR VV time series + CUSUM
            sar_series = _fetch_sar_series(venue_id_q, cancha_id_q)
            cusum = _cusum_changepoint(sar_series)

            # 3. ERA5 disponible?
            has_era5 = cancha.get("era5_sm_0_7cm") is not None

            # 4. Clasificar
            ndvi_alerts = _build_ndvi_alerts(focos)
            cusum_alert = _build_cusum_alert(cusum)

            alerts = _merge_combo_alerts(ndvi_alerts, cusum_alert, has_era5)
            result[cid] = alerts

        except Exception as e:
            log.warning("spatial_alerts: compute error cid=%s: %s", cid, e)
            result[cid] = []

    return result


# ── SAR next date ─────────────────────────────────────────────────────────────

def compute_next_sar_date(vd: dict) -> str | None:
    """
    Calcula la próxima fecha de paso SAR (ciclo Sentinel-1 = 12 días).
    Lee weather_live.sar_fecha y suma 12 días.
    """
    try:
        wl = vd.get("weather_live", {})
        sar_fecha = wl.get("sar_fecha")
        if not sar_fecha:
            return None
        last_date = date.fromisoformat(str(sar_fecha)[:10])
        next_date = last_date + timedelta(days=12)
        return next_date.isoformat()
    except Exception as e:
        log.debug("spatial_alerts: compute_next_sar_date error: %s", e)
        return None


# ── Punto de entrada público ──────────────────────────────────────────────────

def apply_spatial_alerts(vd: dict) -> None:
    """
    Agrega spatial_alerts y focos_reales a cada cancha en vd['roger_canchas'].
    Agrega sar_next_fecha y sar_dias_hasta_siguiente a vd['weather_live'].
    No lanza excepciones.
    """
    try:
        alerts_by_cid = compute_spatial_alerts(vd)
        heatmaps = vd.get("usuarios", {}).get("roger", {}).get("heatmaps", {})

        for cancha in vd.get("roger_canchas", []):
            cid = cancha.get("id") or cancha.get("cancha_id")
            if not cid:
                continue
            cancha["spatial_alerts"] = alerts_by_cid.get(cid, [])
            # Propagar focos_reales desde heatmaps si no está ya en el cancha dict
            if not cancha.get("focos_reales"):
                hm = heatmaps.get(cid, {})
                if hm.get("focos_reales"):
                    cancha["focos_reales"] = hm["focos_reales"]
    except Exception as e:
        log.warning("spatial_alerts: apply_spatial_alerts (non-fatal): %s", e)

    try:
        next_fecha = compute_next_sar_date(vd)
        wl = vd.setdefault("weather_live", {})
        wl["sar_next_fecha"] = next_fecha
        if next_fecha:
            try:
                dias = (date.fromisoformat(next_fecha) - date.today()).days
                wl["sar_dias_hasta_siguiente"] = dias
            except Exception:
                wl["sar_dias_hasta_siguiente"] = None
        else:
            wl["sar_dias_hasta_siguiente"] = None
    except Exception as e:
        log.warning("spatial_alerts: sar_next_fecha (non-fatal): %s", e)
