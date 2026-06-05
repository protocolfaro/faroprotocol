"""
dale_play_monitor.py — Agente de monitoreo autónomo.

Job cada 6h: revisa clima / NDVI / SAR para todos los venues activos.
Usa Paperclip (Claude API) para decisión contextual; fallback a umbrales fijos.
Si detecta anomalía → email automático a Roger y Juan.

Variables de entorno:
  VELEZ_EMAIL_CANCHERO    — email de Roger (ya configurado en Railway para Vélez)
  VELEZ_EMAIL_INTENDENTE  — email de Juan  (ya configurado en Railway para Vélez)
  GMAIL_USER     — remitente SMTP
  EMAIL_APP_PASS — App Password Gmail (16 chars)
  ANTHROPIC_API_KEY — para Paperclip (opcional, fallback a umbrales si falta)
  SUPABASE_URL / SUPABASE_KEY — para consultar show_memory y show_baselines
"""
from __future__ import annotations

import logging
import os
import sys
import smtplib
import time
from datetime import datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from typing import Optional

import requests

log = logging.getLogger(__name__)

# Agregar agents/ al path para importar Paperclip
_AGENTS_DIR = str(Path(__file__).parent.parent.parent.parent / "agents")
if _AGENTS_DIR not in sys.path:
    sys.path.insert(0, _AGENTS_DIR)

# ── Venues activos para monitorear ───────────────────────────────────────────
_ACTIVE_VENUES = [
    {
        "venue_id": "amalfitani",
        "nombre":   "Estadio Amalfitani",
        "lat":      -34.6379,
        "lon":      -58.5288,
    },
]

# ── Umbrales de anomalía ─────────────────────────────────────────────────────
_RAIN_MM_24H    = 20.0   # mm lluvia acumulada en 24h
_WIND_KMH       = 70.0   # km/h viento máximo
_NDVI_DROP      = 0.10   # caída de NDVI desde baseline
_SAR_CONF_MIN   = 0.25   # confianza mínima SAR; debajo = alerta


# ─────────────────────────────────────────────────────────────────────────────
# Entry point del job
# ─────────────────────────────────────────────────────────────────────────────

def run_monitoring_check() -> dict:
    """
    Revisa todos los venues activos.
    Retorna: {timestamp, venues, alertas, detalle, duracion_s}
    """
    t0        = time.time()
    timestamp = datetime.now(timezone.utc).isoformat() + "Z"
    alertas: list[dict] = []

    for venue in _ACTIVE_VENUES:
        vid = venue["venue_id"]
        log.info("dale_play_monitor: verificando %s", vid)
        alertas.extend(_check_venue(venue))

    duracion_s = round(time.time() - t0, 1)

    if alertas:
        log.warning(
            "dale_play_monitor: %d anomalía(s) — enviando email (%.1fs)",
            len(alertas), duracion_s,
        )
        _send_alert_email(alertas, timestamp)
    else:
        log.info("dale_play_monitor: sin anomalías (%.1fs)", duracion_s)

    return {
        "timestamp":  timestamp,
        "venues":     len(_ACTIVE_VENUES),
        "alertas":    len(alertas),
        "detalle":    alertas,
        "duracion_s": duracion_s,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Chequeos por venue
# ─────────────────────────────────────────────────────────────────────────────

def _check_venue(venue: dict) -> list[dict]:
    """Evalúa venue con Paperclip; fallback a umbrales fijos si Claude no disponible."""
    # Colectar datos de todas las fuentes
    weather_ctx = _collect_weather(venue)
    ndvi_ctx    = _collect_ndvi(venue)
    sar_ctx     = _collect_sar(venue)

    # Intentar Paperclip
    try:
        from paperclip import analyze_venue
        decision = analyze_venue(
            venue=venue,
            weather_ctx=weather_ctx,
            ndvi_ctx=ndvi_ctx,
            sar_ctx=sar_ctx,
        )
        if not decision.get("fallback"):
            if decision.get("alertar"):
                nivel = decision.get("nivel", "medium")
                return [{
                    "venue_id":  venue["venue_id"],
                    "nombre":    venue["nombre"],
                    "tipo":      "paperclip_alerta",
                    "mensaje":   decision.get("motivo", ""),
                    "severidad": "alta" if nivel == "high" else "media",
                    "nivel":     nivel,
                    "recomendacion": decision.get("recomendacion", ""),
                    "confianza": decision.get("confianza", 0.7),
                    "factores":  decision.get("factores", []),
                }]
            else:
                log.info(
                    "paperclip [%s] sin alerta: %s",
                    venue["venue_id"], decision.get("motivo", ""),
                )
                return []
    except Exception as exc:
        log.warning("paperclip: error — fallback a umbrales fijos [%s]: %s",
                    venue["venue_id"], exc)

    # Fallback: umbrales fijos
    alertas: list[dict] = []
    alertas.extend(_threshold_weather(venue, weather_ctx))
    alertas.extend(_threshold_ndvi(venue, ndvi_ctx))
    alertas.extend(_threshold_sar(venue, sar_ctx))
    return alertas


def _collect_weather(venue: dict) -> dict:
    """Colecta datos climáticos de Open-Meteo. Retorna dict con datos brutos."""
    lat, lon = venue["lat"], venue["lon"]
    try:
        resp = requests.get(
            "https://api.open-meteo.com/v1/forecast",
            params={
                "latitude":      lat,
                "longitude":     lon,
                "hourly":        "precipitation,windspeed_10m",
                "forecast_days": 2,
                "timezone":      "America/Argentina/Buenos_Aires",
            },
            timeout=15,
        )
        if resp.status_code != 200:
            log.warning("dale_play_monitor: weather HTTP %s", resp.status_code)
            return {}
        hourly = resp.json().get("hourly", {})
        return {
            "precipitation_24h": sum(
                p for p in hourly.get("precipitation", [])[:24] if p is not None
            ),
            "wind_max": max(
                (w for w in hourly.get("windspeed_10m", [])[:24] if w is not None),
                default=0,
            ),
        }
    except Exception as exc:
        log.warning("dale_play_monitor: weather collect %s: %s", venue["venue_id"], exc)
        return {}


def _collect_ndvi(venue: dict) -> dict:
    """Colecta datos de NDVI desde Supabase. Retorna dict con datos brutos."""
    vid = venue["venue_id"]
    try:
        baseline = _query_supabase("show_baselines", {"order": "date.desc", "limit": "1"})
        if not baseline:
            return {}
        baseline_ndvi = baseline[0].get("ndvi")
        if baseline_ndvi is None:
            return {}

        recent = _query_supabase(
            "show_memory",
            {"venue_id": f"eq.{vid}", "order": "created_at.desc", "limit": "3"},
        )
        if not recent:
            return {"baseline_ndvi": float(baseline_ndvi)}

        recent_ndvis = [r["ndvi_value"] for r in recent if r.get("ndvi_value") is not None]
        if not recent_ndvis:
            return {"baseline_ndvi": float(baseline_ndvi)}

        current_ndvi = sum(recent_ndvis) / len(recent_ndvis)
        drop = float(baseline_ndvi) - current_ndvi
        log.info(
            "dale_play_monitor: %s NDVI baseline=%.3f reciente=%.3f drop=%.3f",
            vid, baseline_ndvi, current_ndvi, drop,
        )
        return {
            "baseline_ndvi": round(float(baseline_ndvi), 3),
            "current_ndvi":  round(current_ndvi, 3),
            "drop":          round(drop, 3),
            "n_muestras":    len(recent_ndvis),
        }
    except Exception as exc:
        log.warning("dale_play_monitor: NDVI collect %s: %s", vid, exc)
        return {}


def _collect_sar(venue: dict) -> dict:
    """Colecta datos SAR desde Supabase. Retorna dict con datos brutos."""
    vid = venue["venue_id"]
    try:
        recent = _query_supabase(
            "show_memory",
            {"venue_id": f"eq.{vid}", "order": "created_at.desc", "limit": "1",
             "sar_fuente": "neq."},
        )
        if not recent:
            return {}
        row        = recent[0]
        sar_fuente = row.get("sar_fuente", "")
        return {
            "sar_fuente":    sar_fuente,
            "is_egms_proxy": "EGMS" in (sar_fuente or "").upper(),
            "modules_ok":    row.get("modules_ok") or [],
            "show_id":       row.get("show_id", ""),
        }
    except Exception as exc:
        log.warning("dale_play_monitor: SAR collect %s: %s", vid, exc)
        return {}


# ── Umbrales fijos (fallback cuando Paperclip no disponible) ─────────────────

def _threshold_weather(venue: dict, ctx: dict) -> list[dict]:
    """Aplica umbrales fijos a datos climáticos."""
    alertas: list[dict] = []
    if not ctx:
        return alertas
    vid    = venue["venue_id"]
    nombre = venue["nombre"]
    precip_24h = ctx.get("precipitation_24h", 0)
    wind_max   = ctx.get("wind_max", 0)

    if precip_24h >= _RAIN_MM_24H:
        alertas.append({
            "venue_id":  vid, "nombre": nombre, "tipo": "clima_lluvia",
            "mensaje":   f"Lluvia acumulada próx 24h: {precip_24h:.1f}mm (umbral {_RAIN_MM_24H}mm)",
            "valor":     round(precip_24h, 1), "umbral": _RAIN_MM_24H,
            "severidad": "alta" if precip_24h >= _RAIN_MM_24H * 2 else "media",
        })
    if wind_max >= _WIND_KMH:
        alertas.append({
            "venue_id":  vid, "nombre": nombre, "tipo": "clima_viento",
            "mensaje":   f"Viento máx próx 24h: {wind_max:.0f} km/h (umbral {_WIND_KMH}km/h)",
            "valor":     round(wind_max, 0), "umbral": _WIND_KMH,
            "severidad": "alta" if wind_max >= _WIND_KMH * 1.3 else "media",
        })
    return alertas


def _threshold_ndvi(venue: dict, ctx: dict) -> list[dict]:
    """Aplica umbral fijo de caída NDVI."""
    if not ctx or ctx.get("drop") is None:
        return []
    drop = ctx["drop"]
    if drop < _NDVI_DROP:
        return []
    vid    = venue["venue_id"]
    nombre = venue["nombre"]
    b      = ctx.get("baseline_ndvi", 0)
    c      = ctx.get("current_ndvi", 0)
    return [{
        "venue_id":   vid, "nombre": nombre, "tipo": "ndvi_caida",
        "mensaje":    f"NDVI cayó {drop:.3f} desde baseline (baseline={b:.3f} → reciente={c:.3f})",
        "valor":      round(c, 3), "baseline": round(b, 3),
        "drop":       round(drop, 3), "umbral": _NDVI_DROP,
        "severidad":  "alta" if drop >= _NDVI_DROP * 2 else "media",
        "n_muestras": ctx.get("n_muestras", 0),
    }]


def _threshold_sar(venue: dict, ctx: dict) -> list[dict]:
    """Aplica umbral de confianza SAR (alerta si fuente es EGMS proxy)."""
    if not ctx or not ctx.get("is_egms_proxy"):
        return []
    vid    = venue["venue_id"]
    nombre = venue["nombre"]
    sar_f  = ctx.get("sar_fuente", "")
    return [{
        "venue_id":  vid, "nombre": nombre, "tipo": "sar_baja_conf",
        "mensaje":   f"SAR usando proxy EGMS (sin datos reales) — fuente: {sar_f}",
        "fuente":    sar_f, "umbral": _SAR_CONF_MIN,
        "severidad": "media", "show_id": ctx.get("show_id", ""),
    }]


# ─────────────────────────────────────────────────────────────────────────────
# Supabase helper
# ─────────────────────────────────────────────────────────────────────────────

def _query_supabase(table: str, params: dict) -> list[dict]:
    supa_url = os.environ.get("SUPABASE_URL", "")
    supa_key = os.environ.get("SUPABASE_KEY", "")
    if not supa_url or not supa_key:
        return []
    try:
        resp = requests.get(
            f"{supa_url}/rest/v1/{table}",
            params=params,
            headers={
                "apikey":        supa_key,
                "Authorization": f"Bearer {supa_key}",
                "Accept":        "application/json",
            },
            timeout=10,
        )
        if resp.status_code == 200:
            return resp.json()
        log.warning("dale_play_monitor: Supabase %s HTTP %s", table, resp.status_code)
        return []
    except Exception as exc:
        log.warning("dale_play_monitor: Supabase query %s: %s", table, exc)
        return []


# ─────────────────────────────────────────────────────────────────────────────
# Email de alerta
# ─────────────────────────────────────────────────────────────────────────────

def _send_alert_email(alertas: list[dict], timestamp: str) -> None:
    gmail_user = os.environ.get("GMAIL_USER", "protocolfaro@gmail.com")
    app_pass   = os.environ.get("EMAIL_APP_PASS", "")
    if not app_pass:
        log.info("dale_play_monitor: EMAIL_APP_PASS no configurado — email omitido")
        return

    roger = os.environ.get("VELEZ_EMAIL_CANCHERO", "")
    juan  = os.environ.get("VELEZ_EMAIL_INTENDENTE", "")
    recipients = [e for e in [roger, juan] if e]
    if not recipients:
        log.warning("dale_play_monitor: VELEZ_EMAIL_CANCHERO y VELEZ_EMAIL_INTENDENTE no configurados")
        return

    ts_str  = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    subject = f"[Faro Protocol] {len(alertas)} anomalía(s) detectada(s) — {ts_str}"

    rows_html = ""
    for a in alertas:
        color = "#c0392b" if a.get("severidad") == "alta" else "#e67e22"
        tipo  = a.get("tipo", "").replace("_", " ").upper()
        rows_html += (
            f"<tr>"
            f"<td style='padding:8px 12px;border-bottom:1px solid #1a2a1e'>{a.get('nombre','')}</td>"
            f"<td style='padding:8px 12px;border-bottom:1px solid #1a2a1e;color:{color}'><b>{tipo}</b></td>"
            f"<td style='padding:8px 12px;border-bottom:1px solid #1a2a1e'>{a.get('mensaje','')}</td>"
            f"<td style='padding:8px 12px;border-bottom:1px solid #1a2a1e;color:{color}'>{(a.get('severidad') or '').upper()}</td>"
            f"</tr>"
        )

    html = f"""<html><body style="font-family:Arial,sans-serif;background:#07110a;color:#f2ede4;padding:24px">
<h2 style="color:#e74c3c">Anomalías detectadas — Faro Protocol</h2>
<p style="color:#9aa0a8">Chequeo automático cada 6h — {ts_str}</p>
<table style="border-collapse:collapse;width:100%;margin:16px 0;background:#0d1f12">
  <tr style="background:#1a3020;color:#9aa0a8;font-size:12px">
    <th style="padding:8px 12px;text-align:left">Venue</th>
    <th style="padding:8px 12px;text-align:left">Tipo</th>
    <th style="padding:8px 12px;text-align:left">Detalle</th>
    <th style="padding:8px 12px;text-align:left">Severidad</th>
  </tr>
  {rows_html}
</table>
<hr style="border-color:#c9a84c44;margin:24px 0">
<p style="color:#9aa0a8;font-size:11px">
  Faro Protocol · Monitoreo autónomo cada 6 horas<br>
  <a href="mailto:protocolfaro@gmail.com" style="color:#c9a84c">protocolfaro@gmail.com</a>
</p>
</body></html>"""

    try:
        for to_email in recipients:
            msg = MIMEMultipart("alternative")
            msg["Subject"] = subject
            msg["From"]    = gmail_user
            msg["To"]      = to_email
            msg.attach(MIMEText(html, "html"))
            with smtplib.SMTP_SSL("smtp.gmail.com", 465) as srv:
                srv.login(gmail_user, app_pass)
                srv.sendmail(gmail_user, to_email, msg.as_string())
        log.info("dale_play_monitor: email enviado a %s", recipients)
    except Exception as exc:
        log.warning("dale_play_monitor: email failed: %s", exc)


# ─────────────────────────────────────────────────────────────────────────────
# Registro en APScheduler
# ─────────────────────────────────────────────────────────────────────────────

def register_monitoring_job(scheduler) -> None:
    """
    Registra el job de monitoreo cada 6h.
    Llamar desde app.py junto con init_scheduler().
    """
    try:
        scheduler.add_job(
            _run_monitoring_job,
            "interval",
            hours=6,
            id="dp_monitoring_6h",
            replace_existing=True,
            max_instances=1,
            misfire_grace_time=1800,
        )
        log.info("dale_play_monitor: job de monitoreo cada 6h registrado")
    except Exception as exc:
        log.warning("dale_play_monitor: register_monitoring_job: %s", exc)


def _run_monitoring_job() -> None:
    """Wrapper APScheduler — captura excepciones."""
    log.info("dale_play_monitor: === JOB MONITOREO INICIADO ===")
    try:
        result = run_monitoring_check()
        log.info(
            "dale_play_monitor: === JOB COMPLETADO === alertas=%d venues=%d (%.1fs)",
            result.get("alertas", 0), result.get("venues", 0), result.get("duracion_s", 0),
        )
    except Exception as exc:
        log.error("dale_play_monitor: job error: %s", exc, exc_info=True)


def get_monitor_status() -> dict:
    """Estado público para endpoint /dale-play/monitor-status."""
    return {
        "venues_activos":  [v["venue_id"] for v in _ACTIVE_VENUES],
        "umbrales": {
            "lluvia_mm_24h": _RAIN_MM_24H,
            "viento_kmh":    _WIND_KMH,
            "ndvi_drop":     _NDVI_DROP,
            "sar_conf_min":  _SAR_CONF_MIN,
        },
        "intervalo_h": 6,
    }
