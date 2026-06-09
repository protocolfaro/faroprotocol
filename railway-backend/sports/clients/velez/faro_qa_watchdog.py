"""
faro_qa_watchdog.py — QA post-ciclo para el data lake de Vélez.

Corre a las 09:05 UTC diariamente (5 min después del cron data_refresh 09:00 UTC).
Verifica invariantes del pipeline y loguea alertas en Supabase qa_alerts si algo falla.

Checks:
  1. climate_metrics tiene ≥1 fila con created_at de hoy
  2. velez_weather_live row 'current' actualizado hoy
  3. vegetation_metrics tiene ≥1 fila en los últimos 8 días
     (Sentinel-2 revisit = 5 días; aceptamos hasta 8d por nubosidad)
  4. soil_metrics tiene ≥1 fila en los últimos 30 días
     (Van Genuchten proxy diario; SAR real semanal)
"""
from __future__ import annotations
import logging
import os
from datetime import date, datetime, timedelta, timezone

import requests as _req

log = logging.getLogger(__name__)


def _sb_base() -> str:
    return os.environ.get("SUPABASE_URL", "").rstrip("/")


def _sb_key() -> str:
    return os.environ.get("SUPABASE_KEY", "")


def _sb_ok() -> bool:
    return bool(_sb_base()) and bool(_sb_key())


def _sb_hdrs() -> dict:
    k = _sb_key()
    return {
        "apikey": k,
        "Authorization": f"Bearer {k}",
        "Content-Type": "application/json",
    }


def _count_since(table: str, cutoff_iso: str, extra_filter: str = "") -> int:
    """Return row count in table with created_at >= cutoff_iso. -1 on error."""
    if not _sb_ok():
        return -1
    url = (
        f"{_sb_base()}/rest/v1/{table}"
        f"?created_at=gte.{cutoff_iso}T00:00:00&select=id"
    )
    if extra_filter:
        url += f"&{extra_filter}"
    try:
        r = _req.get(url, headers=_sb_hdrs(), timeout=8)
        if r.status_code == 200:
            return len(r.json())
        log.warning("qa_watchdog: %s HTTP %s — %s", table, r.status_code, r.text[:100])
    except Exception as exc:
        log.warning("qa_watchdog: %s query error: %s", table, exc)
    return -1


def _insert_alert(table_name: str, check_name: str, status: str, detail: str) -> None:
    """Insert one row into qa_alerts. Best-effort, never raises."""
    if not _sb_ok():
        return
    row = {
        "table_name": table_name,
        "check_name": check_name,
        "status":     status,
        "detail":     detail,
        "checked_at": datetime.now(timezone.utc).isoformat(),
    }
    url = f"{_sb_base()}/rest/v1/qa_alerts"
    try:
        r = _req.post(url, headers=_sb_hdrs(), json=row, timeout=8)
        if r.status_code not in (200, 201, 204):
            log.warning("qa_watchdog: insert qa_alerts HTTP %s — %s", r.status_code, r.text[:100])
    except Exception as exc:
        log.warning("qa_watchdog: insert qa_alerts error: %s", exc)


def run_qa_checks(venue_id: str = "amalfitani") -> dict:
    """
    Ejecuta todos los checks del data lake.
    Retorna {"ok": bool, "checks": dict, "alerts": list[str]}.
    """
    if not _sb_ok():
        log.warning("qa_watchdog: Supabase no configurado — checks omitidos")
        return {"ok": False, "error": "Supabase no configurado", "checks": {}, "alerts": []}

    today = date.today().isoformat()
    checks: dict = {}
    alerts: list[str] = []

    # ── 1. climate_metrics hoy ────────────────────────────────────────────────
    cm_count = _count_since("climate_metrics", today, f"venue_id=eq.{venue_id}")
    checks["climate_metrics_hoy"] = cm_count
    if cm_count == 0:
        msg = (
            f"climate_metrics: 0 filas con created_at de hoy ({today}) "
            f"para venue_id={venue_id}. Verificar data_refresh.run_refresh()."
        )
        log.error("qa_watchdog: FAIL — %s", msg)
        _insert_alert("climate_metrics", "climate_metrics_diario", "FAIL", msg)
        alerts.append("climate_metrics")
    elif cm_count > 0:
        log.info("qa_watchdog: climate_metrics OK — %d filas hoy", cm_count)

    # ── 2. velez_weather_live actualizado hoy ─────────────────────────────────
    wl_updated = None
    try:
        r = _req.get(
            f"{_sb_base()}/rest/v1/velez_weather_live?id=eq.current&select=updated_at",
            headers=_sb_hdrs(), timeout=8,
        )
        if r.status_code == 200 and r.json():
            wl_updated = str(r.json()[0].get("updated_at") or "")[:10]
    except Exception as exc:
        log.warning("qa_watchdog: weather_live query: %s", exc)

    checks["weather_live_updated"] = wl_updated
    if wl_updated is None:
        log.warning("qa_watchdog: weather_live row 'current' no encontrada")
    elif wl_updated < today:
        msg = (
            f"velez_weather_live: última actualización {wl_updated}, "
            f"esperado {today}. El cron 09:00 UTC no corrió o falló."
        )
        log.error("qa_watchdog: FAIL — %s", msg)
        _insert_alert("velez_weather_live", "weather_live_diario", "FAIL", msg)
        alerts.append("weather_live")
    else:
        log.info("qa_watchdog: weather_live OK — actualizado %s", wl_updated)

    # ── 3. vegetation_metrics reciente (8 días) ───────────────────────────────
    cutoff_veg = (date.today() - timedelta(days=8)).isoformat()
    veg_count = _count_since("vegetation_metrics", cutoff_veg, f"venue_id=eq.{venue_id}")
    checks["vegetation_metrics_8d"] = veg_count
    if veg_count == 0:
        msg = (
            f"vegetation_metrics: 0 filas en los últimos 8 días para {venue_id}. "
            "Sentinel-2 tiene gap de nubosidad prolongado o el pipeline satelital no corrió."
        )
        log.warning("qa_watchdog: WARN — %s", msg)
        _insert_alert("vegetation_metrics", "vegetation_metrics_8d", "WARN", msg)
        alerts.append("vegetation_metrics")
    elif veg_count > 0:
        log.info("qa_watchdog: vegetation_metrics OK — %d filas últimos 8d", veg_count)

    # ── 4. soil_metrics reciente (30 días) ────────────────────────────────────
    cutoff_soil = (date.today() - timedelta(days=30)).isoformat()
    soil_count = _count_since("soil_metrics", cutoff_soil, f"venue_id=eq.{venue_id}")
    checks["soil_metrics_30d"] = soil_count
    if soil_count == 0:
        msg = (
            f"soil_metrics: 0 filas en los últimos 30 días para {venue_id}. "
            "Van Genuchten proxy no está insertando. Verificar data_refresh.py."
        )
        log.warning("qa_watchdog: WARN — %s", msg)
        _insert_alert("soil_metrics", "soil_metrics_30d", "WARN", msg)
        alerts.append("soil_metrics")
    elif soil_count > 0:
        log.info("qa_watchdog: soil_metrics OK — %d filas últimos 30d", soil_count)

    all_ok = len(alerts) == 0
    log.info(
        "qa_watchdog: %s completados — %s | alertas: %s",
        "todos los checks" if all_ok else "checks con fallas",
        "OK" if all_ok else "FAIL",
        alerts or "ninguna",
    )
    return {"ok": all_ok, "checks": checks, "alerts": alerts}
