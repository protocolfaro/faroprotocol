"""
faro_qa_watchdog.py — QA post-ciclo · 3 capas · Faro Protocol

Arquitectura de validación:
  Capa 1 — Pandera  : validación física determinista (rangos físicamente posibles)
  Capa 2 — Evidently: drift del modelo Kalman via KS-test sobre NDVI histórico vs reciente
  Capa 3 — Claude   : diagnóstico agronómico en español (solo cuando Capa 1/2 levantan bandera)

Checks adicionales:
  • 4 tablas del data lake con datos frescos (climate hoy, veg 8d, soil 30d)
  • velez_data.json actualizado hoy (GitHub)
  • /velez/health responde "ok"
  • satellite.age_days ≤ 14 (WARN si > 14)
  • hermes_consolidate() confianza > 0.30
  • pipeline_runs tiene fila aceptada hoy
  • Si es lunes: weekly job corrió
  • Inconsistencias cruzadas (lluvia > 15mm pero suelo seco)

Cron: 09:05 UTC diariamente (5 min post data_refresh).
"""
from __future__ import annotations

import json
import logging
import os
import sys
from datetime import date, datetime, timedelta, timezone
from typing import Any

import requests as _req

log = logging.getLogger(__name__)

_HERE = os.path.dirname(os.path.abspath(__file__))


# ── Supabase helpers ──────────────────────────────────────────────────────────

def _sb_base() -> str:
    return os.environ.get("SUPABASE_URL", "").rstrip("/")


def _sb_key() -> str:
    return os.environ.get("SUPABASE_KEY", "")


def _sb_ok() -> bool:
    return bool(_sb_base()) and bool(_sb_key())


def _sb_hdrs(extra: dict | None = None) -> dict:
    k = _sb_key()
    h = {"apikey": k, "Authorization": f"Bearer {k}", "Content-Type": "application/json"}
    if extra:
        h.update(extra)
    return h


def _sb_get(table: str, qs: str, limit: int = 200) -> list[dict]:
    if not _sb_ok():
        return []
    try:
        r = _req.get(
            f"{_sb_base()}/rest/v1/{table}?{qs}&limit={limit}",
            headers=_sb_hdrs(), timeout=12,
        )
        if r.status_code == 200:
            return r.json() or []
        log.warning("qa_watchdog: %s HTTP %s", table, r.status_code)
    except Exception as exc:
        log.warning("qa_watchdog: %s: %s", table, exc)
    return []


def _insert_alert(
    nivel: str,
    modulo: str,
    mensaje: str,
    diagnostico_claude: str | None = None,
    venue_id: str = "velez",
) -> bool:
    """Inserta una alerta en qa_alerts. Nivel: ERROR | WARNING | INFO."""
    if not _sb_ok():
        return False
    row = {
        "nivel":              nivel,
        "modulo":             modulo,
        "mensaje":            mensaje[:2000],
        "diagnostico_claude": diagnostico_claude,
        "venue_id":           venue_id,
        "resuelto":           False,
    }
    try:
        r = _req.post(
            f"{_sb_base()}/rest/v1/qa_alerts",
            headers=_sb_hdrs(), json=row, timeout=8,
        )
        if r.status_code in (200, 201, 204):
            return True
        log.warning("qa_watchdog: insert qa_alerts HTTP %s — %s", r.status_code, r.text[:150])
    except Exception as exc:
        log.warning("qa_watchdog: insert qa_alerts: %s", exc)
    return False


# ── CAPA 1: Pandera — validación física determinista ─────────────────────────

def _check_pandera(venue_id: str) -> list[dict]:
    """
    Valida que los últimos datos de las 3 tablas estén dentro de rangos físicos.
    Retorna lista de dicts {nivel, modulo, mensaje} para cada falla.
    """
    alerts: list[dict] = []
    try:
        import pandas as pd
        import pandera as pa

        soil_schema = pa.DataFrameSchema(
            {
                "theta_soil":   pa.Column(float, pa.Check.in_range(0.0, 0.60),    nullable=True),
                "h_suction_cm": pa.Column(float, pa.Check.in_range(0.0, 1500.0), nullable=True),
                "sar_vv_db":    pa.Column(float, pa.Check.in_range(-35.0, 5.0),  nullable=True),
            },
            coerce=True,
        )
        vegetation_schema = pa.DataFrameSchema(
            {
                "ndvi":               pa.Column(float, pa.Check.in_range(-1.0, 1.0), nullable=True),
                "bsi":                pa.Column(float, pa.Check.in_range(-1.0, 1.0), nullable=True),
                "margen_error_kalman": pa.Column(float, pa.Check.in_range(0.0, 1.0),  nullable=True),
            },
            coerce=True,
        )
        climate_schema = pa.DataFrameSchema(
            {
                "et0_mm_dia":      pa.Column(float, pa.Check.in_range(0.0, 12.0),  nullable=True),
                "smith_kerns_pct": pa.Column(float, pa.Check.in_range(0.0, 100.0), nullable=True),
                "gdd_acumulado_7d": pa.Column(float, pa.Check.in_range(0.0, 150.0), nullable=True),
            },
            coerce=True,
        )

        cutoff = (date.today() - timedelta(days=7)).isoformat()
        checks = [
            (
                "soil_metrics", soil_schema,
                f"venue_id=eq.{venue_id}&created_at=gte.{cutoff}T00:00:00&order=created_at.desc",
                ["theta_soil", "h_suction_cm", "sar_vv_db"],
            ),
            (
                "vegetation_metrics", vegetation_schema,
                f"venue_id=eq.{venue_id}&created_at=gte.{cutoff}T00:00:00&order=created_at.desc",
                ["ndvi", "bsi", "margen_error_kalman"],
            ),
            (
                "climate_metrics", climate_schema,
                f"venue_id=eq.{venue_id}&created_at=gte.{cutoff}T00:00:00&order=created_at.desc",
                ["et0_mm_dia", "smith_kerns_pct", "gdd_acumulado_7d"],
            ),
        ]

        for table, schema, qs, cols in checks:
            rows = _sb_get(table, qs, limit=50)
            if not rows:
                continue
            try:
                df = pd.DataFrame(rows)
                for col in cols:
                    if col not in df.columns:
                        df[col] = None
                    df[col] = pd.to_numeric(df[col], errors="coerce")
                schema.validate(df[cols])
                log.info("qa_watchdog: Pandera %s OK (%d filas)", table, len(df))
            except pa.errors.SchemaError as e:
                msg = f"Validación física {table}: {str(e)[:400]}"
                log.error("qa_watchdog: Pandera ERROR — %s", msg)
                alerts.append({"nivel": "ERROR", "modulo": f"pandera.{table}", "mensaje": msg})
            except Exception as exc:
                log.warning("qa_watchdog: Pandera %s check error: %s", table, exc)

    except ImportError:
        log.warning("qa_watchdog: pandera no instalado — Capa 1 omitida")
    except Exception as exc:
        log.warning("qa_watchdog: Capa 1 error general: %s", exc)

    return alerts


# ── CAPA 2: Evidently — KS-test drift del modelo Kalman ──────────────────────

def _check_kalman_drift(venue_id: str) -> dict:
    """
    Compara distribución NDVI histórico (field_timeseries, 76 obs) vs reciente
    (vegetation_metrics, últimas 2 semanas). KS-test: p < 0.05 → drift detectado.
    """
    ref_rows = _sb_get(
        "field_timeseries",
        f"venue_id=eq.{venue_id}&order=fecha.asc",
        limit=76,
    )
    cutoff14 = (date.today() - timedelta(days=14)).isoformat()
    cur_rows = _sb_get(
        "vegetation_metrics",
        f"venue_id=eq.{venue_id}&created_at=gte.{cutoff14}T00:00:00&order=created_at.desc",
        limit=100,
    )

    ref_ndvi = [float(r["ndvi"]) for r in ref_rows if r.get("ndvi") is not None]
    cur_ndvi = [float(r["ndvi"]) for r in cur_rows if r.get("ndvi") is not None]

    if len(ref_ndvi) < 5:
        return {"skip": True, "reason": f"field_timeseries insuficiente ({len(ref_ndvi)} obs)"}
    if len(cur_ndvi) < 2:
        return {"skip": True, "reason": f"vegetation_metrics reciente insuficiente ({len(cur_ndvi)} obs)"}

    p_value: float | None = None
    drift_detected: bool = False
    method: str = ""

    # Primero: Evidently ColumnDriftMetric (KS-test nativo)
    try:
        import pandas as pd
        from evidently.report import Report
        from evidently.metrics import ColumnDriftMetric

        ref_df = pd.DataFrame({"ndvi": ref_ndvi})
        cur_df = pd.DataFrame({"ndvi": cur_ndvi})
        report = Report(metrics=[ColumnDriftMetric(column_name="ndvi")])
        report.run(reference_data=ref_df, current_data=cur_df)
        m_result = report.as_dict()["metrics"][0]["result"]
        p_value = float(m_result.get("p_value") or m_result.get("drift_score") or 1.0)
        drift_detected = bool(m_result.get("drift_detected", p_value < 0.05))
        method = f"evidently · {m_result.get('stattest_name', 'KS')}"
        log.info("qa_watchdog: Evidently KS p=%.4f drift=%s", p_value, drift_detected)
    except ImportError:
        log.debug("qa_watchdog: evidently no instalado — fallback scipy KS-test")
    except Exception as ev_exc:
        log.debug("qa_watchdog: Evidently error — fallback scipy: %s", ev_exc)

    # Fallback: scipy KS-test
    if p_value is None:
        try:
            from scipy.stats import ks_2samp
            stat, p_value = ks_2samp(ref_ndvi, cur_ndvi)
            p_value = round(float(p_value), 4)
            drift_detected = p_value < 0.05
            method = "scipy.ks_2samp"
            log.info("qa_watchdog: scipy KS p=%.4f drift=%s", p_value, drift_detected)
        except Exception as sp_exc:
            log.warning("qa_watchdog: scipy KS error: %s", sp_exc)
            return {"skip": True, "reason": f"KS-test falló: {sp_exc}"}

    return {
        "p_value":       p_value,
        "drift_detected": drift_detected,
        "method":        method,
        "n_ref":         len(ref_ndvi),
        "n_cur":         len(cur_ndvi),
    }


# ── CAPA 3: Claude — diagnóstico agronómico ───────────────────────────────────

def _call_claude_diagnostic(context: dict, venue_id: str = "velez") -> str:
    """
    Llama a Claude API con el contexto de errores/warnings para generar
    un diagnóstico agronómico en español para Roger. Usa claude-haiku-4-5.
    Solo se llama cuando Capa 1 o Capa 2 levantan bandera.
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        log.warning("qa_watchdog: ANTHROPIC_API_KEY no configurada — Capa 3 omitida")
        return ""

    prompt = (
        "Sos el sistema de diagnóstico agronómico Faro Protocol para el Estadio Amalfitani "
        "(Club Atlético Vélez Sarsfield, Buenos Aires). "
        "Detecté las siguientes alertas en el pipeline de datos satelital:\n\n"
        f"{json.dumps(context, ensure_ascii=False, indent=2)}\n\n"
        "Generá un diagnóstico operacional conciso en español para Roger Sosa (encargado de campo):\n"
        "1. Qué falló exactamente y por qué en términos agronómicos concretos\n"
        "2. Si los datos son físicamente plausibles o hay un error de instrumentación/pipeline\n"
        "3. Qué acción concreta debe tomar Roger hoy (si hay alguna)\n\n"
        "Máximo 150 palabras. Párrafo directo, sin headers ni bullets. Español rioplatense."
    )

    try:
        r = _req.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key":         api_key,
                "anthropic-version": "2023-06-01",
                "content-type":      "application/json",
            },
            json={
                "model":      "claude-haiku-4-5-20251001",
                "max_tokens": 512,
                "messages":   [{"role": "user", "content": prompt}],
            },
            timeout=30,
        )
        r.raise_for_status()
        text = r.json()["content"][0]["text"]
        log.info("qa_watchdog: Capa 3 Claude diagnóstico generado (%d chars)", len(text))
        return text
    except Exception as exc:
        log.warning("qa_watchdog: Capa 3 Claude error: %s", exc)
        return f"[Claude API error: {exc}]"


# ── Checks básicos ────────────────────────────────────────────────────────────

def _check_data_freshness(venue_id: str) -> dict:
    """Verifica que las 4 tablas tienen datos dentro de ventanas aceptables."""
    today = date.today().isoformat()
    cutoff_8d  = (date.today() - timedelta(days=8)).isoformat()
    cutoff_30d = (date.today() - timedelta(days=30)).isoformat()
    results: dict = {}

    # climate_metrics: debe tener fila HOY
    cm = _sb_get("climate_metrics", f"venue_id=eq.{venue_id}&created_at=gte.{today}T00:00:00")
    results["climate_metrics"] = {"count_today": len(cm), "ok": len(cm) > 0}

    # velez_weather_live: row 'current' actualizado hoy
    try:
        r = _req.get(
            f"{_sb_base()}/rest/v1/velez_weather_live?id=eq.current&select=updated_at",
            headers=_sb_hdrs(), timeout=8,
        )
        wl_updated = ""
        if r.status_code == 200 and r.json():
            wl_updated = str(r.json()[0].get("updated_at") or "")[:10]
        results["weather_live"] = {"updated_at": wl_updated, "ok": wl_updated >= today}
    except Exception as exc:
        results["weather_live"] = {"ok": None, "error": str(exc)}

    # vegetation_metrics: al menos 1 fila en últimos 8 días
    veg = _sb_get("vegetation_metrics", f"venue_id=eq.{venue_id}&created_at=gte.{cutoff_8d}T00:00:00")
    results["vegetation_metrics"] = {"count_8d": len(veg), "ok": len(veg) > 0}

    # soil_metrics: al menos 1 fila en últimos 30 días
    soil = _sb_get("soil_metrics", f"venue_id=eq.{venue_id}&created_at=gte.{cutoff_30d}T00:00:00")
    results["soil_metrics"] = {"count_30d": len(soil), "ok": len(soil) > 0}

    return results


def _check_velez_data_json() -> dict:
    """Verifica que velez_data.json en GitHub tiene fecha de hoy."""
    token = os.environ.get("GITHUB_TOKEN", "")
    if not token:
        return {"ok": None, "skip": True, "reason": "GITHUB_TOKEN no configurado"}
    try:
        import base64
        r = _req.get(
            "https://api.github.com/repos/protocolfaro/faroprotocol/contents/velez/velez_data.json",
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            params={"ref": "main"}, timeout=12,
        )
        if r.status_code == 200:
            vd = json.loads(base64.b64decode(r.json()["content"]).decode())
            updated = str(vd.get("updated_at", ""))[:10]
            today = date.today().isoformat()
            return {"ok": updated >= today, "updated_at": updated, "today": today}
        return {"ok": False, "status_code": r.status_code}
    except Exception as exc:
        return {"ok": None, "error": str(exc)}


def _check_health_endpoint() -> dict:
    """Llama a /velez/health del propio Railway y verifica status == ok."""
    railway_url = os.environ.get("RAILWAY_URL", "").rstrip("/")
    if not railway_url:
        return {"ok": None, "skip": True, "reason": "RAILWAY_URL no configurado"}
    try:
        r = _req.get(f"{railway_url}/velez/health", timeout=12)
        if r.status_code == 200:
            data = r.json()
            return {
                "ok":            data.get("status") == "ok",
                "status":        data.get("status"),
                "age_h":         data.get("weather", {}).get("age_h"),
                "satellite_age": data.get("satellite", {}).get("age_days"),
            }
        return {"ok": False, "status_code": r.status_code}
    except Exception as exc:
        return {"ok": None, "error": str(exc)}


def _check_satellite_age() -> dict:
    """Verifica antigüedad del último heatmap satelital. WARN si > 14 días."""
    token = os.environ.get("GITHUB_TOKEN", "")
    if not token:
        return {"ok": None, "skip": True}
    try:
        import base64
        r = _req.get(
            "https://api.github.com/repos/protocolfaro/faroprotocol/contents/velez/velez_data.json",
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            params={"ref": "main"}, timeout=12,
        )
        if r.status_code == 200:
            vd = json.loads(base64.b64decode(r.json()["content"]).decode())
            semana = vd.get("usuarios", {}).get("roger", {}).get("heatmaps_meta", {}).get("semana", "")
            if semana:
                age_days = (date.today() - date.fromisoformat(semana)).days
                return {"ok": age_days <= 14, "age_days": age_days, "semana": semana, "warn": age_days > 14}
        return {"ok": None, "skip": True}
    except Exception as exc:
        return {"ok": None, "error": str(exc)}


def _check_hermes(venue_id: str) -> dict:
    """Corre hermes_consolidate() y verifica que confianza_consolidada > 0.30."""
    try:
        _agents = os.path.join(_HERE, "..", "..", "..", "agents")
        if _agents not in sys.path:
            sys.path.insert(0, _agents)
        from hermes import hermes_consolidate  # type: ignore[import]
        result = hermes_consolidate(venue_id=venue_id, cancha_id="amalfitani")
        confianza = float(result.get("confianza_consolidada", 0.0))
        fuentes   = result.get("fuentes_activas", [])
        alertas   = result.get("alertas", [])
        return {
            "ok":        confianza > 0.30,
            "confianza": round(confianza, 3),
            "fuentes":   len(fuentes),
            "alertas":   alertas,
        }
    except Exception as exc:
        return {"ok": None, "error": str(exc)}


def _check_pipeline_runs_today() -> dict:
    """Verifica que pipeline_runs tiene al menos una fila aceptada hoy."""
    today = date.today().isoformat()
    rows = _sb_get(
        "pipeline_runs",
        f"timestamp_utc=gte.{today}T00:00:00&order=timestamp_utc.desc",
        limit=10,
    )
    if not rows:
        return {"ok": False, "count": 0, "accepted": 0}
    accepted = [r for r in rows if r.get("accepted")]
    latest = rows[0].get("timestamp_utc", "")[:19]
    return {"ok": len(accepted) > 0, "count": len(rows), "accepted": len(accepted), "latest": latest}


def _check_monday_weekly_job() -> dict | None:
    """Si hoy es lunes, verifica que el job semanal corrió (pipeline_runs con accepted=True)."""
    if date.today().weekday() != 0:  # 0 = lunes
        return None
    result = _check_pipeline_runs_today()
    if result.get("accepted", 0) == 0:
        return {
            "ok": False,
            "msg": "Lunes: weekly job no dejó pipeline_runs aceptado. Verificar velez_scheduler.",
        }
    return {"ok": True, "msg": f"Lunes: weekly job OK — {result.get('accepted')} runs aceptados"}


def _check_cross_inconsistencies(venue_id: str) -> list[dict]:
    """
    Inconsistencias cruzadas:
      - climate_metrics: if deficit_hidrico_mm == 0 → precip_proxy >= et0 (posible lluvia fuerte)
        AND soil_metrics: theta_soil < 0.15 → suelo seco contradice la lluvia
      - vegetation_metrics: NDVI jump > 0.25 en 24h (físicamente imposible en pasto)
    """
    alerts: list[dict] = []
    today = date.today().isoformat()

    # Cross-check 1: lluvia implícita vs suelo seco
    cm_rows = _sb_get(
        "climate_metrics",
        f"venue_id=eq.{venue_id}&fecha=eq.{today}",
        limit=1,
    )
    soil_rows = _sb_get(
        "soil_metrics",
        f"venue_id=eq.{venue_id}&created_at=gte.{today}T00:00:00&order=created_at.desc",
        limit=1,
    )
    if cm_rows and soil_rows:
        et0    = float(cm_rows[0].get("et0_mm_dia") or 0)
        deficit = float(cm_rows[0].get("deficit_hidrico_mm") or 0)
        precip_proxy = max(0.0, et0 - deficit) * 7  # semana
        theta = float(soil_rows[0].get("theta_soil") or 1.0)
        if precip_proxy > 15.0 and theta < 0.15:
            msg = (
                f"Inconsistencia cruzada: climate_metrics indica precipitación proxy "
                f"{precip_proxy:.1f}mm/semana pero soil_metrics.theta_soil={theta:.3f} "
                f"(suelo seco < 0.15). Posible error en Van Genuchten proxy o en ET0."
            )
            log.warning("qa_watchdog: CROSS — %s", msg)
            alerts.append({
                "nivel":   "WARNING",
                "modulo":  "cross.climate_vs_soil",
                "mensaje": msg,
            })

    # Cross-check 2: NDVI jump > 0.25 en últimas 2 observaciones
    veg_rows = _sb_get(
        "vegetation_metrics",
        f"venue_id=eq.{venue_id}&cancha_id=eq.amalfitani&order=created_at.desc",
        limit=2,
    )
    if len(veg_rows) == 2:
        ndvi_new = float(veg_rows[0].get("ndvi") or 0)
        ndvi_old = float(veg_rows[1].get("ndvi") or 0)
        jump = abs(ndvi_new - ndvi_old)
        if jump > 0.25:
            msg = (
                f"NDVI jump físicamente imposible: {ndvi_old:.3f} → {ndvi_new:.3f} "
                f"(Δ={jump:.3f} > 0.25). Posible artefacto de nube o error en pipeline STAC."
            )
            log.warning("qa_watchdog: CROSS — %s", msg)
            alerts.append({
                "nivel":   "ERROR",
                "modulo":  "cross.ndvi_jump",
                "mensaje": msg,
            })

    return alerts


# ── Entry point principal ─────────────────────────────────────────────────────

def run_qa_checks(venue_id: str = "amalfitani") -> dict:
    """
    Ejecuta todos los checks del QA watchdog.
    Retorna {"ok": bool, "alerts_insertados": int, "checks": dict, "alertas": list[str]}.
    """
    if not _sb_ok():
        log.warning("qa_watchdog: Supabase no configurado — checks omitidos")
        return {"ok": False, "error": "Supabase no configurado", "checks": {}, "alertas": []}

    checks:           dict       = {}
    pending_alerts:   list[dict] = []  # acumular antes de insertar en Supabase
    needs_claude:     bool       = False

    # ── Capa 1: Pandera ───────────────────────────────────────────────────────
    pandera_alerts = _check_pandera(venue_id)
    checks["pandera"] = {
        "ok": len(pandera_alerts) == 0,
        "errors": len(pandera_alerts),
    }
    if pandera_alerts:
        pending_alerts.extend(pandera_alerts)
        needs_claude = True

    # ── Capa 2: Evidently KS-test ─────────────────────────────────────────────
    drift_result = _check_kalman_drift(venue_id)
    checks["kalman_drift"] = drift_result
    if not drift_result.get("skip") and drift_result.get("drift_detected"):
        msg = (
            f"Kalman Data Drift Detected — modelo requiere re-entrenamiento. "
            f"KS p={drift_result.get('p_value'):.4f} < 0.05 "
            f"(ref={drift_result.get('n_ref')} obs, cur={drift_result.get('n_cur')} obs, "
            f"método={drift_result.get('method')})"
        )
        log.warning("qa_watchdog: Capa 2 — %s", msg)
        pending_alerts.append({
            "nivel": "WARNING", "modulo": "evidently.kalman_drift", "mensaje": msg,
        })
        needs_claude = True

    # ── Capa 3: Claude diagnóstico (solo si hay alertas Capa 1/2) ─────────────
    claude_diag = ""
    if needs_claude and pending_alerts:
        # Enriquecer contexto con últimas 48h de logs y climate_metrics
        context: dict[str, Any] = {
            "alertas":        pending_alerts,
            "venue_id":       venue_id,
            "timestamp_utc":  datetime.now(timezone.utc).isoformat(),
        }
        try:
            cm = _sb_get(
                "climate_metrics",
                f"venue_id=eq.{venue_id}&order=created_at.desc",
                limit=3,
            )
            context["climate_metrics_recientes"] = cm[:3]
        except Exception:
            pass
        claude_diag = _call_claude_diagnostic(context, venue_id)
        checks["claude_diagnostico"] = bool(claude_diag)

    # ── Checks básicos ────────────────────────────────────────────────────────
    freshness = _check_data_freshness(venue_id)
    checks["freshness"] = freshness

    if not freshness.get("climate_metrics", {}).get("ok"):
        today = date.today().isoformat()
        pending_alerts.append({
            "nivel":   "ERROR",
            "modulo":  "freshness.climate_metrics",
            "mensaje": f"climate_metrics: 0 filas hoy ({today}) para {venue_id}. "
                       "Verificar data_refresh.run_refresh().",
        })

    if freshness.get("weather_live", {}).get("ok") is False:
        pending_alerts.append({
            "nivel":   "ERROR",
            "modulo":  "freshness.weather_live",
            "mensaje": f"velez_weather_live: última actualización "
                       f"{freshness['weather_live'].get('updated_at')} — esperado hoy.",
        })

    if not freshness.get("vegetation_metrics", {}).get("ok"):
        pending_alerts.append({
            "nivel":   "WARNING",
            "modulo":  "freshness.vegetation_metrics",
            "mensaje": f"vegetation_metrics: 0 filas en últimos 8 días para {venue_id}. "
                       "Sentinel-2 gap prolongado o pipeline satelital no corrió.",
        })

    if not freshness.get("soil_metrics", {}).get("ok"):
        pending_alerts.append({
            "nivel":   "WARNING",
            "modulo":  "freshness.soil_metrics",
            "mensaje": f"soil_metrics: 0 filas en últimos 30 días para {venue_id}. "
                       "Van Genuchten proxy no está insertando.",
        })

    # velez_data.json date
    vd_check = _check_velez_data_json()
    checks["velez_data_json"] = vd_check
    if vd_check.get("ok") is False and not vd_check.get("skip"):
        pending_alerts.append({
            "nivel":   "WARNING",
            "modulo":  "freshness.velez_data_json",
            "mensaje": f"velez_data.json último update: {vd_check.get('updated_at')} — esperado hoy.",
        })

    # /velez/health
    health_check = _check_health_endpoint()
    checks["health_endpoint"] = health_check
    if health_check.get("ok") is False and not health_check.get("skip"):
        pending_alerts.append({
            "nivel":   "ERROR",
            "modulo":  "health.endpoint",
            "mensaje": f"/velez/health retornó status != ok: {health_check}",
        })

    # Satellite age
    sat_check = _check_satellite_age()
    checks["satellite_age"] = sat_check
    if sat_check.get("warn"):
        pending_alerts.append({
            "nivel":   "WARNING",
            "modulo":  "satellite.age",
            "mensaje": f"Última imagen satelital: {sat_check.get('age_days')} días. "
                       f"Semana procesada: {sat_check.get('semana')}. "
                       "Nubosidad persistente o pipeline satelital inactivo.",
        })

    # Hermes confianza
    hermes_check = _check_hermes(venue_id)
    checks["hermes"] = hermes_check
    if hermes_check.get("ok") is False and hermes_check.get("error") is None:
        pending_alerts.append({
            "nivel":   "WARNING",
            "modulo":  "hermes.confianza",
            "mensaje": f"hermes_consolidate confianza_consolidada={hermes_check.get('confianza', 0):.3f} ≤ 0.30. "
                       "Datos insuficientes o las 4 tablas del data lake vacías.",
        })

    # pipeline_runs hoy
    pr_check = _check_pipeline_runs_today()
    checks["pipeline_runs"] = pr_check
    if not pr_check.get("ok"):
        pending_alerts.append({
            "nivel":   "WARNING",
            "modulo":  "pipeline_runs.today",
            "mensaje": f"pipeline_runs: 0 filas aceptadas hoy. "
                       f"Total encontradas: {pr_check.get('count', 0)}.",
        })

    # Lunes: weekly job
    monday_check = _check_monday_weekly_job()
    if monday_check is not None:
        checks["monday_weekly_job"] = monday_check
        if not monday_check.get("ok"):
            pending_alerts.append({
                "nivel":   "WARNING",
                "modulo":  "scheduler.weekly_job",
                "mensaje": monday_check.get("msg", "Lunes: weekly job no corrió."),
            })

    # Inconsistencias cruzadas
    cross_alerts = _check_cross_inconsistencies(venue_id)
    if cross_alerts:
        pending_alerts.extend(cross_alerts)

    # ── Insertar todas las alertas en Supabase ────────────────────────────────
    alerts_insertados = 0
    for al in pending_alerts:
        diag = claude_diag if (needs_claude and al.get("nivel") in ("ERROR", "WARNING")) else None
        ok = _insert_alert(
            nivel=al.get("nivel", "INFO"),
            modulo=al.get("modulo", "unknown"),
            mensaje=al.get("mensaje", ""),
            diagnostico_claude=diag,
            venue_id=venue_id,
        )
        if ok:
            alerts_insertados += 1

    all_ok = not any(a.get("nivel") == "ERROR" for a in pending_alerts)

    log.info(
        "qa_watchdog: %s — %d alertas (%d insertadas en qa_alerts) | venue=%s",
        "OK" if all_ok else "FAIL/WARN",
        len(pending_alerts),
        alerts_insertados,
        venue_id,
    )

    return {
        "ok":                all_ok,
        "alerts_insertados": alerts_insertados,
        "n_alerts":          len(pending_alerts),
        "checks":            checks,
        "alertas":           [a.get("modulo", "") for a in pending_alerts],
        "claude_diagnostico": bool(claude_diag),
    }
