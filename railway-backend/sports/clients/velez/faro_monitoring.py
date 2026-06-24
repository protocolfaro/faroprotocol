"""
faro_monitoring.py — Observabilidad y calidad de datos para Faro Engine V2.

Tres responsabilidades:
  1. check_data_quality()  — 5 pilares: freshness, completeness, bounds, schema, lineage
  2. alert_to_slack()      — webhook Slack cuando health < verde
  3. Flask /metrics        — endpoint Prometheus (lee /tmp/faro_metrics.prom)

Puede correrse standalone como servidor de métricas:
    python faro_monitoring.py --port 9090

O importarse para quality checks:
    from faro_monitoring import check_data_quality, run_and_alert

Env vars:
  SLACK_WEBHOOK_URL           webhook Slack para alertas
  SUPABASE_URL + SUPABASE_KEY lectura de reportes para quality checks
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

_METRICS_PATH    = os.environ.get("PROMETHEUS_METRICS_PATH", "/tmp/faro_metrics.prom")
_SLACK_WEBHOOK   = lambda: os.environ.get("SLACK_WEBHOOK_URL", "")
_SUPA_URL        = lambda: os.environ.get("SUPABASE_URL", "")
_SUPA_KEY        = lambda: os.environ.get("SUPABASE_KEY", "")

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
    """
    Pillar 5 — Lineage.
    Verifica que exista audit trail: SHA-256 válido + TSA verificado.
    """
    audit = report_data.get("audit") or {}
    sha   = audit.get("sha256", "")
    tsa   = audit.get("tsa_token_b64")
    ts    = audit.get("timestamp_iso", "")
    vrf   = audit.get("verified", False)

    issues: list[str] = []
    if not sha or len(sha) != 64:
        issues.append("sha256 inválido o ausente")
    if not ts:
        issues.append("timestamp_iso ausente")
    if not vrf:
        issues.append("TSA no verificado")
    if not tsa:
        issues.append("sin token RFC 3161")

    score = max(0.0, 1.0 - len(issues) * 0.25)
    # SHA-256 solo ya es lineage parcial
    if sha and len(sha) == 64 and not vrf:
        score = max(score, 0.5)

    status = "green" if score >= 0.9 else ("yellow" if score >= 0.5 else "red")
    detail = "SHA-256 + TSA OK" if not issues else "; ".join(issues)
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


def check_latest_from_supabase(venue_id: str) -> Optional[DataQualityReport]:
    """
    Fetches el reporte más reciente de Supabase y corre check_data_quality.
    Retorna None si no hay conectividad o no existe el reporte.
    """
    url = _SUPA_URL()
    key = _SUPA_KEY()
    if not url or not key:
        log.warning("quality_check: SUPABASE_URL/KEY no configurados")
        return None
    try:
        r = requests.get(
            f"{url}/rest/v1/faro_v2_reports",
            headers = {"apikey": key, "Authorization": f"Bearer {key}"},
            params  = {"venue_id": f"eq.{venue_id}", "order": "created_at.desc", "limit": "1"},
            timeout = 10,
        )
        if r.status_code != 200 or not r.json():
            log.warning("quality_check: sin reporte para %s (HTTP %s)", venue_id, r.status_code)
            return None
        return check_data_quality(r.json()[0])
    except Exception as e:
        log.warning("quality_check Supabase (non-fatal): %s", e)
        return None


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


def run_and_alert(venue_id: str) -> Optional[DataQualityReport]:
    """Shortcut: check_latest_from_supabase + alert_to_slack si no verde."""
    qr = check_latest_from_supabase(venue_id)
    if qr:
        alert_to_slack(qr)
    return qr


# ── Prometheus metrics Flask endpoint ────────────────────────────────────────

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

    @app.route("/quality/<venue_id>")
    def quality(venue_id: str):
        if venue_id not in _valid_venues():
            return Response(json.dumps({"error": "venue no registrado"}),
                            mimetype="application/json", status=404)
        qr = check_latest_from_supabase(venue_id)
        if not qr:
            return Response(
                json.dumps({"error": "sin reporte en Supabase para " + venue_id}),
                mimetype="application/json", status=503,
            )
        return Response(json.dumps({**qr.to_dict(), "summary": qr.summary()}),
                        mimetype="application/json")

    return app


def _valid_venues() -> list[str]:
    try:
        from faro_v2_engine import VENUE_REGISTRY
        return list(VENUE_REGISTRY.keys())
    except ImportError:
        return ["amalfitani", "villa_olimpica"]


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse

    logging.basicConfig(
        level   = logging.INFO,
        format  = "%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt = "%H:%M:%S",
    )

    parser = argparse.ArgumentParser(description="Faro Monitoring — /metrics + quality checks")
    sub = parser.add_subparsers(dest="cmd")

    srv = sub.add_parser("serve", help="Iniciar servidor /metrics Flask")
    srv.add_argument("--port",  type=int, default=9090)
    srv.add_argument("--host",  default="0.0.0.0")
    srv.add_argument("--debug", action="store_true")

    chk = sub.add_parser("check", help="Correr quality check de un venue")
    chk.add_argument("--venue", default="amalfitani")
    chk.add_argument("--alert", action="store_true",
                     help="Enviar alerta a Slack si no verde")

    args = parser.parse_args()

    if args.cmd == "serve":
        app = _build_flask_app()
        if not app:
            print("ERROR: Flask no instalado — pip install flask")
            sys.exit(1)
        print(f"Faro Monitoring — http://{args.host}:{args.port}/metrics")
        app.run(host=args.host, port=args.port, debug=args.debug)

    elif args.cmd == "check":
        qr = check_latest_from_supabase(args.venue)
        if not qr:
            print(f"Sin datos en Supabase para venue={args.venue}")
            sys.exit(1)
        print(qr.summary())
        if args.alert:
            sent = alert_to_slack(qr)
            if sent:
                print("Alerta enviada a Slack.")
        sys.exit(0 if qr.overall_status == "green" else 1)

    else:
        parser.print_help()
