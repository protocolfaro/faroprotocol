"""
dale_play_routes.py — Flask Blueprint con endpoints /dale-play/.
Se registra en app.py con 2 líneas sin modificar los endpoints de Vélez.
"""
from __future__ import annotations
import json, logging, os, sys, threading

from flask import Blueprint, jsonify, request, send_file, render_template_string

# dale-play/ está un nivel arriba de railway-backend/
_DP_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), "dale-play"))
if _DP_PATH not in sys.path:
    sys.path.insert(0, _DP_PATH)

log = logging.getLogger(__name__)

dale_play_bp = Blueprint("dale_play", __name__, url_prefix="/dale-play")


@dale_play_bp.route("/health", methods=["GET"])
def dp_health():
    from dale_play_storage import check_supabase_config, _client
    _sb_configured = check_supabase_config()
    _sb_connected  = False
    _sb_error      = None
    if _sb_configured:
        try:
            from supabase import create_client as _sbc
            _sb_url = os.environ.get("SUPABASE_URL", "")
            _sb_key = os.environ.get("SUPABASE_KEY", "")
            _c = _sbc(_sb_url, _sb_key)
            _c.table("show_baselines").select("show_id").limit(1).execute()
            _sb_connected = True
        except Exception as _e:
            _sb_error = str(_e)
            log.warning("health: supabase ping failed: %s", _e)
    return jsonify({
        "service":          "dale-play",
        "status":           "ok",
        "github_token":     bool(os.environ.get("GITHUB_TOKEN")),
        "insar_configured": bool(os.environ.get("NASA_EARTHDATA_USER")),
        "supabase": {
            "configured":  _sb_configured,
            "connected":   _sb_connected,
            "error":       _sb_error,
            "url_preview": (os.environ.get("SUPABASE_URL","")[:30] + "…")
                           if os.environ.get("SUPABASE_URL") else None,
        },
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


@dale_play_bp.route("/upload-layout", methods=["POST"])
def dp_upload_layout():
    """
    POST /dale-play/upload-layout
    Form: show_id (str), file (PDF o DXF/DWG)
    Parsea el layout con pdfplumber/ezdxf + Claude Vision.
    Guarda shows/{show_id}_layout.json y retorna el dict.
    """
    show_id = request.form.get("show_id", "").strip()
    if not show_id:
        return jsonify({"error": "show_id required"}), 400
    if "file" not in request.files:
        return jsonify({"error": "file required (multipart field 'file')"}), 400

    f = request.files["file"]
    if not f.filename:
        return jsonify({"error": "empty filename"}), 400

    try:
        from dale_play_layout import parse_layout_file
        result = parse_layout_file(f.read(), f.filename, show_id)
        return jsonify(result)
    except ValueError as ve:
        return jsonify({"error": str(ve)}), 400
    except Exception as e:
        log.error("dale_play /upload-layout: %s", e)
        return jsonify({"error": str(e)}), 500


@dale_play_bp.route("/layout/<show_id>", methods=["GET"])
def dp_layout(show_id: str):
    """GET /dale-play/layout/{show_id} — retorna layout JSON si existe."""
    try:
        from dale_play_layout import load_layout
        layout = load_layout(show_id)
        if layout is None:
            return jsonify({"error": f"No layout found for {show_id}"}), 404
        return jsonify(layout)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@dale_play_bp.route("/certify", methods=["POST"])
def dp_certify():
    """
    POST /dale-play/certify
    Body: {"show_id": str, "mode": "post_show"}
    Genera certificado PDF post-evento con NDVI pre/post, hash SHA-256.
    """
    body    = request.get_json(force=True, silent=True) or {}
    show_id = body.get("show_id", "").strip()
    mode    = body.get("mode", "post_show")

    if not show_id:
        return jsonify({"error": "show_id required"}), 400
    if mode != "post_show":
        return jsonify({"error": "mode debe ser 'post_show'"}), 400

    try:
        from dale_play_certification import run_certification
        result = run_certification(show_id, mode=mode)
        return jsonify(result)
    except FileNotFoundError as e:
        return jsonify({"error": str(e)}), 404
    except Exception as e:
        log.error("dale_play /certify: %s", e)
        return jsonify({"error": str(e)}), 500


@dale_play_bp.route("/certificado/<show_id>", methods=["GET"])
def dp_certificado(show_id: str):
    """GET /dale-play/certificado/{show_id} — descarga el PDF del certificado."""
    import os as _os
    pdf_path = _os.path.join(_DP_PATH, "certificados", f"{show_id}_certificado.pdf")
    if not _os.path.exists(pdf_path):
        return jsonify({"error": f"Certificado no encontrado: {show_id}"}), 404
    return send_file(pdf_path, mimetype="application/pdf",
                     download_name=f"{show_id}_certificado.pdf")


@dale_play_bp.route("/report-png/<show_id>", methods=["GET"])
def dp_report_png(show_id: str):
    """GET /dale-play/report-png/airbag_2026-05-31 — sirve el PNG generado."""
    png_path = os.path.join(_DP_PATH, "reportes", f"reporte_{show_id}.png")
    if not os.path.exists(png_path):
        return jsonify({"error": f"PNG not found: {png_path}"}), 404
    return send_file(png_path, mimetype="image/png")


@dale_play_bp.route("/verify/<cert_hash>", methods=["GET"])
def dp_verify(cert_hash: str):
    """
    GET /dale-play/verify/{hash}
    Verifica un certificado por hash SHA-256.
    Retorna: {valido, show_id, fecha, ndvi_pre, ndvi_post, nivel_dano}
    """
    try:
        from dale_play_storage import get_certification_by_hash
        cert = get_certification_by_hash(cert_hash.upper())
        if cert is None:
            return jsonify({"valido": False, "error": "Certificado no encontrado"}), 404
        damage = cert.get("data", {}).get("damage") or {}
        return jsonify({
            "valido":      True,
            "show_id":     cert.get("show_id"),
            "fecha":       (cert.get("data") or {}).get("fecha_emision"),
            "ndvi_pre":    cert.get("ndvi_pre"),
            "ndvi_post":   cert.get("ndvi_post"),
            "delta_ndvi":  cert.get("delta_ndvi"),
            "nivel_dano":  cert.get("nivel_dano"),
            "cert_hash":   cert.get("cert_hash"),
            "interpretacion": damage.get("interpretacion"),
        })
    except Exception as e:
        log.error("dale_play /verify: %s", e)
        return jsonify({"valido": False, "error": str(e)}), 500


@dale_play_bp.route("/dashboard/<show_id>", methods=["GET"])
def dp_dashboard(show_id: str):
    """GET /dale-play/dashboard/{show_id} — Panel de validación interactivo."""
    tpl_path = os.path.join(_DP_PATH, "templates", "dale_play_dashboard.html")
    if not os.path.exists(tpl_path):
        return jsonify({"error": "Dashboard template not found"}), 404
    with open(tpl_path, encoding="utf-8") as f:
        html = f.read()
    return html, 200, {"Content-Type": "text/html; charset=utf-8"}


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
