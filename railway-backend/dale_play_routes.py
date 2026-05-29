"""
dale_play_routes.py — Flask Blueprint con endpoints /dale-play/.
Se registra en app.py con 2 líneas sin modificar los endpoints de Vélez.
"""
from __future__ import annotations
import json, logging, os, sys, threading

from flask import Blueprint, jsonify, request

# dale-play/ está un nivel arriba de railway-backend/
_DP_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), "dale-play"))
if _DP_PATH not in sys.path:
    sys.path.insert(0, _DP_PATH)

log = logging.getLogger(__name__)

dale_play_bp = Blueprint("dale_play", __name__, url_prefix="/dale-play")


@dale_play_bp.route("/health", methods=["GET"])
def dp_health():
    return jsonify({
        "service":          "dale-play",
        "status":           "ok",
        "github_token":     bool(os.environ.get("GITHUB_TOKEN")),
        "insar_configured": bool(os.environ.get("NASA_EARTHDATA_USER")),
    })


@dale_play_bp.route("/shows", methods=["GET"])
def dp_shows():
    """Lista los show configs disponibles en dale-play/shows/."""
    shows_dir = os.path.join(_DP_PATH, "shows")
    try:
        files = sorted(
            f[:-5] for f in os.listdir(shows_dir)
            if f.endswith(".json") and not f.startswith(".")
        )
        return jsonify({"shows": files})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@dale_play_bp.route("/weather", methods=["GET"])
def dp_weather():
    """GET /dale-play/weather?show_date=YYYY-MM-DD — pronóstico rápido."""
    show_date = request.args.get("show_date")
    try:
        from dale_play_weather import fetch_72h_forecast
        return jsonify(fetch_72h_forecast(show_date=show_date))
    except Exception as e:
        log.error("dale_play /weather: %s", e)
        return jsonify({"error": str(e)}), 500


@dale_play_bp.route("/report", methods=["GET"])
def dp_report():
    """
    GET /dale-play/report?show_id=airbag_2026-05-31&mode=full
    Corre el pipeline completo y retorna show_data JSON.
    """
    show_id = request.args.get("show_id", "")
    mode    = request.args.get("mode", "full")
    if not show_id:
        return jsonify({"error": "show_id required"}), 400

    show_config = _load_show(show_id)
    if show_config is None:
        return jsonify({"error": f"Show config not found: {show_id}"}), 404

    try:
        from dale_play_pipeline import run_show_audit
        return jsonify(run_show_audit(show_config, mode=mode))
    except Exception as e:
        log.error("dale_play /report: %s", e)
        return jsonify({"error": str(e)}), 500


@dale_play_bp.route("/run", methods=["POST"])
def dp_run():
    """
    POST /dale-play/run
    Body: {"show_id": str, "mode": "full|weather_only|post_show"}
    Lanza el pipeline en background y retorna inmediatamente.
    """
    body    = request.get_json(force=True, silent=True) or {}
    show_id = body.get("show_id", "")
    mode    = body.get("mode", "full")

    if not show_id:
        return jsonify({"error": "show_id required"}), 400

    show_config = _load_show(show_id)
    if show_config is None:
        return jsonify({"error": f"Show config not found: {show_id}"}), 404

    def _bg():
        try:
            from dale_play_pipeline import run_show_audit
            run_show_audit(show_config, mode=mode)
        except Exception as exc:
            log.error("dale_play /run background: %s", exc)

    threading.Thread(target=_bg, daemon=True).start()

    return jsonify({
        "status":  "started",
        "show_id": show_id,
        "mode":    mode,
        "message": f"Pipeline iniciado en background para {show_id}",
    })


# ── helper ────────────────────────────────────────────────────────────────────

def _load_show(show_id: str) -> dict | None:
    path = os.path.join(_DP_PATH, "shows", f"{show_id}.json")
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        log.error("dale_play _load_show %s: %s", show_id, e)
        return None
