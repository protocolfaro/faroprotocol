"""
faro_monitoring.py — Observabilidad standalone para Faro Engine V2.

Responsabilidades:
  1. record_prometheus_metrics() — escribe /tmp/faro_metrics.prom desde un FaroV2Report
  2. check_data_quality()        — 5 pilares: freshness, completeness, bounds, schema, lineage
  3. alert_to_slack()            — webhook Slack cuando health < verde
  4. Flask /metrics + /health    — servidor Prometheus para scraping local

Uso standalone (servidor de métricas):
    python faro_monitoring.py serve --port 9090

Diseño: no importa faro_v2_engine en producción (evita imports circulares).
Acepta dicts planos (asdict(FaroV2Report(...))) como entrada a check_data_quality.

Env vars:
  SLACK_WEBHOOK_URL           webhook Slack para alertas
  PROMETHEUS_METRICS_PATH     path del archivo .prom (default /tmp/faro_metrics.prom)
"""
from __future__ import annotations

import json
import logging
import os
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Optional

import requests

log = logging.getLogger(__name__)

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

_METRICS_PATH  = os.environ.get("PROMETHEUS_METRICS_PATH", "/tmp/faro_metrics.prom")
_SLACK_WEBHOOK = lambda: os.environ.get("SLACK_WEBHOOK_URL", "")

# Umbrales físicos para pillar "bounds"
_BOUNDS: dict[str, tuple[float, float]] = {
    "solar.ghi_wh_m2":      (0,     9000),   # Wh/m²/día — máx teórico ~11 kWh/m² en Ecuador
    "solar.et0_mm_dia":     (0,     20),      # mm/día
    "sar.vv_gamma0_db":     (-30,   5),       # dB — rango SAR C-band superficie
    "sar.vh_gamma0_db":     (-35,   0),
    "sar.theta_soil":       (0.045, 0.41),    # Van Genuchten Franco-Arenoso Deportivo
    "sar.h_suction_cm":     (0,     15000),
    "hydro.hand_mean_m":    (0,     500),
    "canopy.altura_media_m":(0,     80),
    "lband.hh_mean_db":     (-30,   10),
    "lband.hv_mean_db":     (-35,   5),
}

# Campos que DEBEN estar presentes y no-None para schema válido
_REQUIRED_FIELDS = [
    "venue_id", "fecha", "audit.sha256", "audit.timestamp_iso",
]


# ── Data Structures ───────────────────────────────────────────────────────────

@dataclass
class PillarResult:
    name:    str
    score:   float          # 0.0 – 1.0
    status:  str            # "green" | "yellow" | "red"
    detail:  str = ""

    @property
    def emoji(self) -> str:
        return {"green": "🟢", "yellow": "🟡", "red": "🔴"}.get(self.status, "⚪")


@dataclass
class DataQualityReport:
    venue_id:     str
    checked_at:   str
    freshness:    PillarResult = field(default_factory=lambda: PillarResult("freshness",  0, "red"))
    completeness: PillarResult = field(default_factory=lambda: PillarResult("completeness", 0, "red"))
    bounds:       PillarResult = field(default_factory=lambda: PillarResult("bounds",     0, "red"))
    schema:       PillarResult = field(default_factory=lambda: PillarResult("schema",     0, "red"))
    lineage:      PillarResult = field(default_factory=lambda: PillarResult("lineage",    0, "red"))

    @property
    def overall_score(self) -> float:
        pillars = [self.freshness, self.completeness, self.bounds, self.schema, self.lineage]
        return round(sum(p.score for p in pillars) / len(pillars), 3)

    @property
    def overall_status(self) -> str:
        score = self.overall_score
        if score >= 0.85:
            return "green"
        if score >= 0.60:
            return "yellow"
        return "red"

    @property
    def overall_emoji(self) -> str:
        return {"green": "🟢", "yellow": "🟡", "red": "🔴"}.get(self.overall_status, "⚪")

    def to_dict(self) -> dict:
        return asdict(self)

    def summary(self) -> str:
        lines = [
            f"Faro Quality Report — {self.venue_id} [{self.checked_at}]",
            f"Overall: {self.overall_emoji} {self.overall_status.upper()} "
            f"(score={self.overall_score:.2f})",
            "",
        ]
        for p in [self.freshness, self.completeness, self.bounds, self.schema, self.lineage]:
            lines.append(f"  {p.emoji} {p.name:<14} score={p.score:.2f}  {p.detail}")
        return "\n".join(lines)


# ── 5 Pilares de Calidad ──────────────────────────────────────────────────────

def _pillar_freshness(report_data: dict) -> PillarResult:
    """
    Pillar 1 — Freshness.
    Mide cuántos días han pasado desde la fecha del reporte vs hoy.
    Score = 1 - (días / 7). Umbral rojo: > 7 días.
    """
    fecha_str = report_data.get("fecha") or report_data.get("created_at", "")[:10]
    if not fecha_str:
        return PillarResult("freshness", 0.0, "red", "sin campo 'fecha'")
    try:
        fecha = datetime.strptime(fecha_str[:10], "%Y-%m-%d")
        dias  = (datetime.utcnow() - fecha).days
        score = max(0.0, 1.0 - dias / 7.0)
        status = "green" if dias <= 1 else ("yellow" if dias <= 3 else "red")
        return PillarResult("freshness", round(score, 3), status,
                            f"{dias}d desde última imagen")
    except Exception as e:
        return PillarResult("freshness", 0.0, "red", f"parse error: {e}")


def _pillar_completeness(report_data: dict) -> PillarResult:
    """
    Pillar 2 — Completeness.
    Cuenta campos non-None sobre total esperado en las secciones clave.
    """
    sections = {
        "solar":  ["ghi_wh_m2",  "et0_mm_dia"],
        "sar":    ["vv_gamma0_db", "theta_soil"],
        "hydro":  ["hand_mean_m", "zona_riesgo"],
        "canopy": ["altura_media_m"],
        "lband":  ["hh_mean_db"],
    }
    total = filled = 0
    missing: list[str] = []
    for section, fields in sections.items():
        sect_data = report_data.get(section) or {}
        for f in fields:
            total += 1
            if sect_data.get(f) is not None:
                filled += 1
            else:
                missing.append(f"{section}.{f}")

    score  = filled / total if total else 0.0
    status = "green" if score >= 0.8 else ("yellow" if score >= 0.5 else "red")
    detail = f"{filled}/{total} campos" + (f" — ausentes: {', '.join(missing[:3])}" if missing else "")
    return PillarResult("completeness", round(score, 3), status, detail)


def _pillar_bounds(report_data: dict) -> PillarResult:
    """
    Pillar 3 — Bounds.
    Verifica que los valores estén dentro de rangos físicos esperados.
    """
    violations: list[str] = []
    checked = 0

    def _flat(d: dict, prefix: str = "") -> dict:
        out = {}
        for k, v in d.items():
            full = f"{prefix}.{k}" if prefix else k
            if isinstance(v, dict):
                out.update(_flat(v, full))
            else:
                out[full] = v
        return out

    flat = _flat(report_data)
    for key, (lo, hi) in _BOUNDS.items():
        val = flat.get(key)
        if val is None:
            continue
        checked += 1
        try:
            fval = float(val)
            if not (lo <= fval <= hi):
                violations.append(f"{key}={fval:.3f} ∉ [{lo},{hi}]")
        except (TypeError, ValueError):
            pass

    score  = max(0.0, 1.0 - len(violations) / max(checked, 1))
    status = "green" if not violations else ("yellow" if len(violations) <= 2 else "red")
    detail = (f"{checked} campos verificados" if not violations
              else f"{len(violations)} violaciones: {'; '.join(violations[:2])}")
    return PillarResult("bounds", round(score, 3), status, detail)


def _pillar_schema(report_data: dict) -> PillarResult:
    """
    Pillar 4 — Schema.
    Verifica presencia y tipo de campos obligatorios.
    """
    missing: list[str] = []

    def _get_nested(d: dict, dotted: str):
        parts = dotted.split(".")
        cur   = d
        for p in parts:
            if not isinstance(cur, dict):
                return None
            cur = cur.get(p)
        return cur

    for field_path in _REQUIRED_FIELDS:
        val = _get_nested(report_data, field_path)
        if val is None or val == "":
            missing.append(field_path)

    # Verificar tipos básicos
    type_errors: list[str] = []
    if not isinstance(report_data.get("venue_id"), str):
        type_errors.append("venue_id debe ser str")
    fecha = report_data.get("fecha")
    if fecha and not isinstance(fecha, str):
        type_errors.append("fecha debe ser str ISO")

    issues = missing + type_errors
    score  = 1.0 if not issues else (0.5 if len(issues) <= 2 else 0.0)
    status = "green" if not issues else ("yellow" if len(issues) <= 2 else "red")
    detail = "OK" if not issues else f"faltantes/inválidos: {', '.join(issues[:3])}"
    return PillarResult("schema", score, status, detail)


def _pillar_lineage(report_data: dict) -> PillarResult:
    """Pillar 5 — Lineage. Chequea: audit.sha256 válido, timestamp_iso presente, errors vacío."""
    audit  = report_data.get("audit") or {}
    sha    = audit.get("sha256", "")
    ts     = audit.get("timestamp_iso", "")
    errors = report_data.get("errors") or []

    issues: list[str] = []
    if not sha or len(sha) != 64:
        issues.append("sha256 inválido o ausente")
    if not ts:
        issues.append("timestamp_iso ausente")
    if errors:
        issues.append(f"{len(errors)} errores en pipeline")

    score  = max(0.0, 1.0 - len(issues) / 3)
    status = "green" if not issues else ("yellow" if len(issues) == 1 else "red")
    detail = "SHA-256 + timestamp + sin errores" if not issues else "; ".join(issues)
    return PillarResult("lineage", round(score, 3), status, detail)


# ── check_data_quality ────────────────────────────────────────────────────────

def check_data_quality(report_data: dict) -> DataQualityReport:
    """
    Evalúa los 5 pilares de calidad sobre un reporte FaroV2 (como dict).

    Acepta tanto dicts crudos de Supabase como `asdict(FaroV2Report(...))`.
    Retorna DataQualityReport con scores y status por pilar.
    """
    venue_id = report_data.get("venue_id", "unknown")
    now      = datetime.utcnow().isoformat()[:19] + "Z"

    return DataQualityReport(
        venue_id     = venue_id,
        checked_at   = now,
        freshness    = _pillar_freshness(report_data),
        completeness = _pillar_completeness(report_data),
        bounds       = _pillar_bounds(report_data),
        schema       = _pillar_schema(report_data),
        lineage      = _pillar_lineage(report_data),
    )


# ── Prometheus metrics writer ─────────────────────────────────────────────────

def record_prometheus_metrics(report_data,
                               path: str = "/tmp/faro_metrics.prom") -> None:
    """
    Escribe métricas en formato Prometheus text exposition.
    Acepta FaroV2Report (dataclass) o dict equivalente. Sin deps externas.
    """
    if hasattr(report_data, "__dataclass_fields__"):
        from dataclasses import asdict as _asdict
        report_data = _asdict(report_data)
    v   = report_data.get("venue_id", "unknown")
    now = datetime.utcnow().timestamp()

    def _g(name: str, value, help_text: str = "") -> list[str]:
        if value is None:
            return []
        try:
            fval = float(value)
        except (TypeError, ValueError):
            return []
        lines = []
        if help_text:
            lines.append(f"# HELP {name} {help_text}")
        lines.append(f'# TYPE {name} gauge')
        lines.append(f'{name}{{venue="{v}"}} {fval}')
        return lines

    solar  = report_data.get("solar")  or {}
    sar    = report_data.get("sar")    or {}
    hydro  = report_data.get("hydro")  or {}
    canopy = report_data.get("canopy") or {}
    audit  = report_data.get("audit")  or {}

    metrics: list[str] = [
        f"# Faro Engine V2 metrics — generated {datetime.utcnow().isoformat()}Z",
        *_g("faro_pipeline_duration_seconds", report_data.get("duration_s"),
            "Duración del pipeline en segundos"),
        *_g("faro_pipeline_errors_total",     float(len(report_data.get("errors") or [])),
            "Cantidad de componentes con error"),
        *_g("faro_audit_verified",            float(bool(audit.get("verified"))),
            "1=TSA RFC3161 verificado, 0=sin sello"),
        *_g("faro_solar_ghi_wh_m2",           solar.get("ghi_wh_m2"),
            "Irradiación global horizontal Wh/m²/día"),
        *_g("faro_solar_et0_mm_dia",          solar.get("et0_mm_dia"),
            "Evapotranspiración referencia FAO-56 mm/día"),
        *_g("faro_sar_vv_db",                 sar.get("vv_gamma0_db"),
            "SAR OPERA RTC VV gamma0 dB"),
        *_g("faro_sar_theta_soil",            sar.get("theta_soil"),
            "Humedad suelo volumétrica Van Genuchten m³/m³"),
        *_g("faro_sar_h_suction_cm",          sar.get("h_suction_cm"),
            "Tensión mátrica Van Genuchten cm"),
        *_g("faro_hydro_hand_mean_m",         hydro.get("hand_mean_m"),
            "HAND medio sobre AOI metros"),
        *_g("faro_canopy_height_mean_m",      canopy.get("altura_media_m"),
            "ETH canopy height media metros"),
        *_g("faro_run_timestamp",             now, "UNIX timestamp del último run"),
        "",
    ]

    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(metrics))
        log.info("metrics: escrito a %s", path)
    except Exception as e:
        log.warning("metrics write (non-fatal): %s", e)


# ── Slack alerting ────────────────────────────────────────────────────────────

def alert_to_slack(qr: DataQualityReport, webhook_url: str = "") -> bool:
    """
    Envía alerta a Slack cuando el overall_status != "green".
    Retorna True si el mensaje fue enviado con éxito.
    """
    url = webhook_url or _SLACK_WEBHOOK()
    if not url:
        log.debug("alert_to_slack: SLACK_WEBHOOK_URL no configurado — skipping")
        return False

    if qr.overall_status == "green":
        log.debug("alert_to_slack: status green — sin alerta")
        return False

    emoji_map = {"yellow": ":large_yellow_circle:", "red": ":red_circle:"}
    head_emoji = emoji_map.get(qr.overall_status, ":white_circle:")

    pillar_lines = []
    for p in [qr.freshness, qr.completeness, qr.bounds, qr.schema, qr.lineage]:
        if p.status != "green":
            pillar_lines.append(f"  {p.emoji} *{p.name}* (score={p.score:.2f}): {p.detail}")

    payload = {
        "text": (
            f"{head_emoji} *Faro Engine V2 — Quality Alert*\n"
            f"*Venue:* `{qr.venue_id}` | *Score:* {qr.overall_score:.2f} "
            f"| *Status:* {qr.overall_status.upper()}\n"
            + "\n".join(pillar_lines)
        )
    }

    try:
        r = requests.post(url, json=payload, timeout=10)
        if r.status_code == 200:
            log.info("alert_to_slack: mensaje enviado (venue=%s score=%.2f)",
                     qr.venue_id, qr.overall_score)
            return True
        log.warning("alert_to_slack: HTTP %s — %s", r.status_code, r.text[:100])
    except Exception as e:
        log.warning("alert_to_slack (non-fatal): %s", e)
    return False


# ── Flask /metrics + /health ─────────────────────────────────────────────────

def _build_flask_app():
    """Construye la Flask app con /metrics y /health."""
    try:
        from flask import Flask, Response
    except ImportError:
        return None

    app = Flask(__name__)

    @app.route("/metrics")
    def metrics():
        path = _METRICS_PATH
        if not os.path.exists(path):
            return Response(
                "# faro_metrics.prom no encontrado — ejecutar FaroEngine primero\n",
                mimetype="text/plain; version=0.0.4",
                status=200,
            )
        try:
            content = open(path, encoding="utf-8").read()
            age_s   = time.time() - os.path.getmtime(path)
            content += f'\n# Last updated {age_s:.0f}s ago\n'
            return Response(content, mimetype="text/plain; version=0.0.4")
        except Exception as e:
            return Response(f"# Error leyendo métricas: {e}\n",
                            mimetype="text/plain", status=500)

    @app.route("/health")
    def health():
        path     = _METRICS_PATH
        exists   = os.path.exists(path)
        age_s    = (time.time() - os.path.getmtime(path)) if exists else None
        stale    = age_s is not None and age_s > 86400  # > 24h
        status   = "ok" if exists and not stale else "degraded"
        return Response(
            json.dumps({"status": status,
                        "metrics_file": exists,
                        "age_seconds":  age_s}),
            mimetype="application/json",
            status=200 if status == "ok" else 503,
        )

    return app


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse

    logging.basicConfig(
        level   = logging.INFO,
        format  = "%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt = "%H:%M:%S",
    )

    parser = argparse.ArgumentParser(description="Faro Monitoring — servidor /metrics Prometheus")
    parser.add_argument("--port",  type=int, default=9090)
    parser.add_argument("--host",  default="0.0.0.0")
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args()

    app = _build_flask_app()
    if not app:
        print("ERROR: Flask no instalado — pip install flask")
        sys.exit(1)
    print(f"Faro Monitoring — http://{args.host}:{args.port}/metrics")
    app.run(host=args.host, port=args.port, debug=args.debug)
