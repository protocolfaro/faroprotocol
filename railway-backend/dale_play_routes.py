"""
dale_play_routes.py — Flask Blueprint con endpoints /dale-play/.
Se registra en app.py con 2 líneas sin modificar los endpoints de Vélez.
"""
from __future__ import annotations
import json, logging, os, sys, threading

from flask import Blueprint, jsonify, request, send_file, render_template_string

# dale-play movido a events/clients/dale-play/ (ARCHITECTURE.md)
_DP_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), "events", "clients", "dale-play"))
if _DP_PATH not in sys.path:
    sys.path.insert(0, _DP_PATH)

log = logging.getLogger(__name__)

dale_play_bp = Blueprint("dale_play", __name__, url_prefix="/dale-play")


@dale_play_bp.route("/health", methods=["GET"])
def dp_health():
    from dale_play_storage import check_supabase_config, ping as _sb_ping
    _sb_configured = check_supabase_config()
    _sb_connected, _sb_error = _sb_ping()
    return jsonify({
        "service":          "dale-play",
        "status":           "ok",
        "github_token":     bool(os.environ.get("GITHUB_TOKEN")),
        "insar_configured": bool(os.environ.get("NASA_EARTHDATA_USER")),
        "supabase": {
            "configured": _sb_configured,
            "connected":  _sb_connected,
            "error":      _sb_error,
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
    """GET /dale-play/certificado/{show_id} — descarga el PDF del certificado.

    Jerarquía:
      1. Filesystem local (Railway efímero — solo disponible en la sesión que lo generó)
      2. Supabase certifications.data.pdf_base64 (persistente entre restarts)
    """
    import io as _io
    import base64 as _b64
    import os as _os

    # 1. Intentar local
    pdf_path = _os.path.join(_DP_PATH, "certificados", f"{show_id}_certificado.pdf")
    if _os.path.exists(pdf_path):
        return send_file(pdf_path, mimetype="application/pdf",
                         download_name=f"{show_id}_certificado.pdf")

    # 2. Fallback: Supabase certifications.data.pdf_base64
    try:
        from dale_play_storage import get_certification
        cert = get_certification(show_id)
        if cert is None:
            return jsonify({"error": f"Certificado no encontrado: {show_id}"}), 404
        pdf_b64 = (cert.get("data") or {}).get("pdf_base64")
        if not pdf_b64:
            return jsonify({"error": f"PDF no disponible para {show_id} — regenerar con /certify"}), 404
        pdf_bytes = _b64.b64decode(pdf_b64)
        return send_file(
            _io.BytesIO(pdf_bytes),
            mimetype="application/pdf",
            download_name=f"{show_id}_certificado.pdf",
        )
    except Exception as _e:
        log.error("dp_certificado fallback Supabase error: %s", _e)
        return jsonify({"error": f"Error recuperando certificado: {_e}"}), 500


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


@dale_play_bp.route("/scheduler-status", methods=["GET"])
def dp_scheduler_status():
    """GET /dale-play/scheduler-status — monitores post-show activos."""
    try:
        from dale_play_scheduler import get_active_monitors
        return jsonify({"monitors": get_active_monitors()})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@dale_play_bp.route("/memory", methods=["GET"])
def dp_memory():
    """
    GET /dale-play/memory — Memoria del sistema: últimos N runs del pipeline.

    Query params:
      venue_id  — filtrar por venue (default: todos)
      limit     — número de registros (default: 20, max: 100)
    """
    try:
        from dale_play_memory import memory_summary
        venue_id = request.args.get("venue_id")
        limit    = min(int(request.args.get("limit", 20)), 100)
        return jsonify(memory_summary(venue_id=venue_id, last_n=limit))
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@dale_play_bp.route("/monitor-status", methods=["GET"])
def dp_monitor_status():
    """GET /dale-play/monitor-status — Configuración del agente de monitoreo."""
    try:
        from dale_play_monitor import get_monitor_status
        return jsonify(get_monitor_status())
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@dale_play_bp.route("/monitor-run", methods=["POST"])
def dp_monitor_run():
    """POST /dale-play/monitor-run — Dispara el chequeo de monitoreo manualmente."""
    try:
        from dale_play_monitor import run_monitoring_check
        result = run_monitoring_check()
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@dale_play_bp.route("/health", methods=["GET"])
def dp_health_modules():
    """
    GET /dale-play/health — Salud de módulos (auto-corrección).
    Muestra consecutive_fails, status, degraded_since por módulo.
    """
    try:
        from dale_play_autocorrect import get_module_health
        return jsonify(get_module_health())
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@dale_play_bp.route("/fii-weights", methods=["GET"])
def dp_fii_weights():
    """GET /dale-play/fii-weights — Pesos FII actuales y historial de calibración."""
    try:
        from dale_play_fii_calibration import get_fii_weights
        import os, requests as _req
        supa_url = os.environ.get("SUPABASE_URL", "")
        supa_key = os.environ.get("SUPABASE_KEY", "")
        current  = get_fii_weights()
        history  = []
        if supa_url and supa_key:
            r = _req.get(
                f"{supa_url}/rest/v1/fii_weights",
                params={"order": "calibrado_at.desc", "limit": "10"},
                headers={"apikey": supa_key, "Authorization": f"Bearer {supa_key}", "Accept": "application/json"},
                timeout=5,
            )
            if r.status_code == 200:
                history = r.json()
        return jsonify({"current": current, "history": history})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@dale_play_bp.route("/dashboard/<show_id>", methods=["GET"])
def dp_dashboard(show_id: str):
    """GET /dale-play/dashboard/{show_id} — Panel de validación interactivo."""
    tpl_path = os.path.join(_DP_PATH, "templates", "dale_play_dashboard.html")
    if not os.path.exists(tpl_path):
        return jsonify({"error": "Dashboard template not found"}), 404
    with open(tpl_path, encoding="utf-8") as f:
        html = f.read()
    return html, 200, {"Content-Type": "text/html; charset=utf-8"}


# ── Time series fenológica — trigger manual ───────────────────────────────────

_ts_state: dict = {"status": "idle", "started_at": None, "result": None, "error": None}
_ts_lock = threading.Lock()


@dale_play_bp.route("/admin/timeseries-build", methods=["POST"])
def dp_timeseries_build():
    """
    POST /dale-play/admin/timeseries-build
    Body (opcional): {"venue_id": "amalfitani", "start_year": 2020, "max_new": 24}
    Lanza build_venue_timeseries + compute_fenology en background.
    Retorna 202 inmediatamente. Pollear /admin/timeseries-status para resultados.
    """
    with _ts_lock:
        if _ts_state["status"] == "running":
            return jsonify({"status": "running",
                            "msg": "Build ya en progreso — pollear /admin/timeseries-status"}), 409
        _ts_state.update({"status": "running", "started_at": __import__("datetime")
                          .datetime.utcnow().isoformat() + "Z",
                          "result": None, "error": None})

    body      = request.get_json(force=True, silent=True) or {}
    venue_id  = body.get("venue_id", "amalfitani")
    start_yr  = int(body.get("start_year", 2020))
    max_new   = int(body.get("max_new", 24))
    bbox      = body.get("bbox") or [-58.5305, -34.6391, -58.5271, -34.6367]

    def _run():
        try:
            from satellite.field_timeseries import build_venue_timeseries, compute_fenology
            build_result = build_venue_timeseries(
                venue_id, bbox, start_year=start_yr, max_new_per_run=max_new
            )
            fenol_result = compute_fenology(venue_id)
            with _ts_lock:
                _ts_state.update({
                    "status":   "done",
                    "result":   {"build": build_result, "fenologia": fenol_result},
                    "error":    None,
                })
            log.info("dp_timeseries_build: done — %s", build_result)
        except Exception as exc:
            with _ts_lock:
                _ts_state.update({"status": "error", "error": str(exc)})
            log.error("dp_timeseries_build: %s", exc)

    threading.Thread(target=_run, daemon=True).start()
    return jsonify({
        "status":   "started",
        "venue_id": venue_id,
        "msg":      f"Build iniciado — {max_new} imágenes máx. Pollear /dale-play/admin/timeseries-status",
    }), 202


@dale_play_bp.route("/admin/timeseries-status", methods=["GET"])
def dp_timeseries_status():
    """GET /dale-play/admin/timeseries-status — estado actual del build de time series."""
    with _ts_lock:
        return jsonify(dict(_ts_state))


@dale_play_bp.route("/admin/timeseries-diag", methods=["GET"])
def dp_timeseries_diag():
    """
    GET /dale-play/admin/timeseries-diag
    Diagnóstico completo: busca una imagen S2 y muestra por qué falla el NDVI.
    Propaga la excepción real sin swallowing.
    """
    import traceback as _tb
    bbox = [-58.5305, -34.6391, -58.5271, -34.6367]
    out: dict = {}

    # 1. STAC search
    try:
        from satellite.field_timeseries import _search_s2_month
        items = _search_s2_month(2025, 3, bbox, max_cloud=50.0)
        if not items:
            return jsonify({"error": "Sin items S2 en STAC para 2025-03"}), 200
        item, source = items[0]
        props = item.get("properties", {})
        assets_keys = list((item.get("assets") or {}).keys())
        out["stac_ok"]       = True
        out["source"]        = source
        out["item_id"]       = item.get("id", "")[:60]
        out["datetime"]      = props.get("datetime", "")[:10]
        out["cloud_cover"]   = props.get("eo:cloud_cover")
        out["assets_keys"]   = assets_keys
    except Exception as exc:
        return jsonify({"error": f"STAC search falló: {exc}", "tb": _tb.format_exc()}), 500

    # 2. Token SAS (si PC)
    if source == "PC":
        try:
            import requests as _rq
            tok_r = _rq.get(
                "https://planetarycomputer.microsoft.com/api/sas/v1/token/sentinel-2-l2a",
                timeout=10,
            )
            out["token_ok"]     = tok_r.ok
            out["token_status"] = tok_r.status_code
            token = tok_r.json().get("token", "") if tok_r.ok else ""
        except Exception as exc:
            out["token_error"] = str(exc)
            token = ""
    else:
        import os as _os
        _os.environ["AWS_NO_SIGN_REQUEST"] = "YES"
        token = ""
        out["token_ok"] = "N/A (AWS)"

    # 3. Intentar abrir la banda B04/red
    assets = item.get("assets", {})
    red_href = (assets.get("B04") or assets.get("red") or {}).get("href", "")
    out["red_href_raw"] = red_href[:100] if red_href else "(vacío)"
    if red_href.startswith("s3://sentinel-cogs/"):
        red_href = red_href.replace("s3://sentinel-cogs/",
                                    "https://sentinel-cogs.s3.us-west-2.amazonaws.com/")
    if token:
        red_href_signed = f"{red_href}?{token[:20]}..."
    else:
        red_href_signed = red_href
    out["red_href_used"] = red_href_signed[:100]

    try:
        import rasterio
        out["rasterio_version"] = rasterio.__version__
        from rasterio.warp import transform_bounds
        from rasterio.windows import from_bounds
        import numpy as np
        minx, miny, maxx, maxy = bbox
        with rasterio.open(red_href if not token else f"{red_href}?{token}") as src:
            out["src_crs"]    = str(src.crs)
            out["src_shape"]  = list(src.shape)
            native = transform_bounds("EPSG:4326", src.crs, minx, miny, maxx, maxy)
            out["native_bounds"] = list(native)
            win    = from_bounds(*native, transform=src.transform)
            out["window"] = {"col_off": win.col_off, "row_off": win.row_off,
                             "width": win.width, "height": win.height}
            data = src.read(1, window=win).astype("float32") / 10_000.0
            out["data_shape"]  = list(data.shape)
            out["data_min"]    = float(data.min())
            out["data_max"]    = float(data.max())
            valid = (data > 0) & (data < 1)
            out["valid_px"]    = int(valid.sum())
            out["ndvi_possible"] = int(valid.sum()) >= 4
    except Exception as exc:
        out["rasterio_error"] = str(exc)
        out["rasterio_tb"]    = _tb.format_exc()[-800:]

    return jsonify(out)


@dale_play_bp.route("/admin/timeseries-test-ndvi", methods=["GET"])
def dp_timeseries_test_ndvi():
    """
    GET /dale-play/admin/timeseries-test-ndvi?year=2020&month=3
    Llama _ndvi_from_s2_item SIN try/except para ver el error real.
    """
    import traceback as _tb
    bbox  = [-58.5305, -34.6391, -58.5271, -34.6367]
    year  = int(request.args.get("year",  2020))
    month = int(request.args.get("month", 3))
    out: dict = {"year": year, "month": month}

    try:
        from satellite.field_timeseries import _search_s2_month
        import rasterio, numpy as np, os as _os
        from rasterio.warp import transform_bounds
        from rasterio.windows import from_bounds
        import requests as _req

        items = _search_s2_month(year, month, bbox, max_cloud=50.0)
        if not items:
            return jsonify({"error": f"Sin items STAC para {year}-{month:02d}"}), 200

        item, source = items[0]
        props = item.get("properties", {})
        assets = item.get("assets", {})
        out["source"]       = source
        out["item_id"]      = item.get("id", "")[:60]
        out["datetime"]     = props.get("datetime", "")[:10]
        out["cloud_cover"]  = props.get("eo:cloud_cover")
        out["assets_keys"]  = list(assets.keys())

        # Resolver HREFs
        def _resolve_href(key, fallback=""):
            a = assets.get(key) or assets.get(fallback) or {}
            return a.get("href", "")

        red_href = _resolve_href("B04", "red")
        nir_href = _resolve_href("B08", "nir")
        out["red_key_found"] = "B04" if assets.get("B04") else ("red" if assets.get("red") else "NONE")
        out["nir_key_found"] = "B08" if assets.get("B08") else ("nir" if assets.get("nir") else "NONE")
        out["red_href"] = red_href[:80] if red_href else "(vacío)"
        out["nir_href"] = nir_href[:80] if nir_href else "(vacío)"

        if not red_href or not nir_href:
            return jsonify({**out, "error": "Href vacío — asset key no encontrado"}), 200

        # Token
        if source == "PC":
            tok_r = _req.get(
                "https://planetarycomputer.microsoft.com/api/sas/v1/token/sentinel-2-l2a",
                timeout=10,
            )
            token = tok_r.json().get("token", "") if tok_r.ok else ""
        else:
            _os.environ["AWS_NO_SIGN_REQUEST"] = "YES"
            token = ""

        def _open_band(href: str) -> np.ndarray:
            if href.startswith("s3://sentinel-cogs/"):
                href = href.replace("s3://sentinel-cogs/",
                                    "https://sentinel-cogs.s3.us-west-2.amazonaws.com/")
            if token:
                href = f"{href}?{token}"
            minx, miny, maxx, maxy = bbox
            with rasterio.open(href) as src:
                native = transform_bounds("EPSG:4326", src.crs, minx, miny, maxx, maxy)
                win    = from_bounds(*native, transform=src.transform)
                return src.read(1, window=win).astype("float32") / 10_000.0

        # Leer RED — sin try/except para ver el error real
        red = _open_band(red_href)
        out["red_shape"]     = list(red.shape)
        out["red_min"]       = float(red.min())
        out["red_max"]       = float(red.max())

        # Leer NIR
        nir = _open_band(nir_href)
        out["nir_shape"]     = list(nir.shape)
        out["nir_min"]       = float(nir.min())
        out["nir_max"]       = float(nir.max())

        valid = (red > 0) & (nir > 0) & (red < 1) & (nir < 1)
        out["valid_px"] = int(valid.sum())
        if valid.sum() >= 4:
            r_v, n_v = red[valid], nir[valid]
            ndvi = float(round(float(np.mean((n_v - r_v) / (n_v + r_v + 1e-9))), 3))
            out["ndvi"] = ndvi
            out["ok"] = True
        else:
            out["ndvi"] = None
            out["ok"] = False
            out["error"] = f"Solo {int(valid.sum())} pixels válidos"

        # Comparación: llamar _ndvi_from_s2_item directamente con el mismo item
        try:
            from satellite.field_timeseries import _ndvi_from_s2_item
            ndvi_fn = _ndvi_from_s2_item(item, bbox, source)
            out["ndvi_via_fn"] = ndvi_fn
            out["fn_matches"]  = (ndvi_fn == out.get("ndvi"))
        except Exception as exc2:
            out["fn_error"] = str(exc2)
            out["fn_tb"]    = _tb.format_exc()[-600:]

    except Exception as exc:
        out["ok"]    = False
        out["error"] = str(exc)
        out["tb"]    = _tb.format_exc()[-1200:]

    return jsonify(out)


# ── Lali Espósito 2025 — análisis comparativo Sentinel-2 ─────────────────────

_lali_state: dict = {"status": "idle", "started_at": None, "result": None, "error": None}
_lali_lock = threading.Lock()


@dale_play_bp.route("/lali-analysis", methods=["GET"])
def dp_lali_analysis():
    """
    GET /dale-play/lali-analysis — Análisis comparativo NDVI 5 shows Lali Espósito 2025.

    Query params:
      force=1      — ignora caché y re-corre el análisis
      summary=1    — retorna versión compacta (sin listas de escenas completas)

    Primera llamada puede tardar 2-5 min (descarga de imágenes S2).
    Resultado cacheado 6h automáticamente.
    """
    try:
        from dale_play_lali_analysis import run_lali_analysis, get_lali_summary
        force   = request.args.get("force", "0") == "1"
        summary = request.args.get("summary", "0") == "1"
        if summary:
            return jsonify(get_lali_summary())
        return jsonify(run_lali_analysis(force=force))
    except Exception as e:
        log.error("dale_play /lali-analysis: %s", e)
        return jsonify({"error": str(e)}), 500


@dale_play_bp.route("/lali-analysis/run", methods=["POST"])
def dp_lali_run():
    """
    POST /dale-play/lali-analysis/run
    Lanza el análisis en background y retorna inmediatamente.
    Pollear GET /dale-play/lali-analysis?summary=1 para resultados.
    """
    with _lali_lock:
        if _lali_state["status"] == "running":
            return jsonify({"status": "running",
                            "msg": "Análisis ya en progreso — pollear /dale-play/lali-analysis"}), 409
        _lali_state.update({
            "status":     "running",
            "started_at": __import__("datetime").datetime.utcnow().isoformat() + "Z",
            "result":     None,
            "error":      None,
        })

    def _run():
        try:
            from dale_play_lali_analysis import run_lali_analysis
            result = run_lali_analysis(force=True)
            with _lali_lock:
                _lali_state.update({"status": "done", "result": result, "error": None})
            log.info("dp_lali_run: done — %d eventos con datos", result.get("n_eventos_con_datos", 0))
        except Exception as exc:
            with _lali_lock:
                _lali_state.update({"status": "error", "error": str(exc)})
            log.error("dp_lali_run: %s", exc)

    threading.Thread(target=_run, daemon=True).start()
    return jsonify({
        "status":  "started",
        "msg":     "Análisis Lali 2025 iniciado en background. ~2-5 min. Pollear GET /dale-play/lali-analysis?summary=1",
    }), 202


@dale_play_bp.route("/lali-analysis/status", methods=["GET"])
def dp_lali_status():
    """GET /dale-play/lali-analysis/status — estado del análisis en background."""
    with _lali_lock:
        state = dict(_lali_state)
    # Retornar solo el resumen en el status (sin listas largas)
    if state.get("result"):
        r = state["result"]
        state["result"] = {
            "n_eventos_con_datos": r.get("n_eventos_con_datos"),
            "delta_ndvi_promedio": r.get("delta_ndvi_promedio"),
            "evento_mayor_impacto": r.get("evento_mayor_impacto"),
            "escenas_totales_2025": r.get("escenas_totales_2025"),
        }
    return jsonify(state)


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


@dale_play_bp.route("/admin/supabase-diag", methods=["GET"])
def dp_supabase_diag():
    """GET /dale-play/admin/supabase-diag — últimas filas de certifications y paperclip_decisions."""
    import os, requests as _req
    supa_url = os.environ.get("SUPABASE_URL", "").rstrip("/")
    supa_key = os.environ.get("SUPABASE_KEY", "")
    if not supa_url or not supa_key:
        return jsonify({"error": "SUPABASE_URL/KEY no configurados"}), 503
    hdrs = {"apikey": supa_key, "Authorization": f"Bearer {supa_key}", "Accept": "application/json"}
    out = {}
    for table, fields in [
        ("certifications",     "show_id,cert_hash,nivel_dano,created_at"),
        ("paperclip_decisions","venue_id,alertar,nivel,motivo,created_at"),
        ("pipeline_runs",      "timestamp_utc,ndvi_median,accepted,skipped_reason,error"),
    ]:
        try:
            r = _req.get(f"{supa_url}/rest/v1/{table}",
                         params={"select": fields, "order": "created_at.desc", "limit": "5"},
                         headers=hdrs, timeout=8)
            out[table] = r.json() if r.ok else {"error": r.status_code, "detail": r.text[:200]}
        except Exception as exc:
            out[table] = {"error": str(exc)}
    return jsonify(out)


# ── verify-visual — QR interactivo del certificado ────────────────────────────

_VERIFY_VISUAL_HTML = r"""<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0">
<title>Certificado · Faro Protocol</title>
<script src="https://unpkg.com/maplibre-gl@4.7.1/dist/maplibre-gl.js"></script>
<link href="https://unpkg.com/maplibre-gl@4.7.1/dist/maplibre-gl.css" rel="stylesheet">
<style>
  :root{--gold:#c9a84c;--bg:#0a0a0a;--panel:rgba(17,17,17,.96);--border:#2a2a2a;--red:#e05050;--text:#e8e8e8;--muted:#777;--r:8px}
  *{box-sizing:border-box;margin:0;padding:0}
  body{background:var(--bg);color:var(--text);font-family:'Inter',system-ui,sans-serif;height:100dvh;display:flex;flex-direction:column;overflow:hidden}
  #map{flex:1;width:100%}
  #cert-overlay{position:absolute;top:12px;left:12px;max-width:300px;background:var(--panel);border:1px solid var(--gold);border-radius:var(--r);padding:16px;backdrop-filter:blur(12px);z-index:5}
  .co-logo{font-size:11px;font-weight:700;letter-spacing:.1em;color:var(--gold);text-transform:uppercase;margin-bottom:10px}
  .co-title{font-size:14px;font-weight:700;margin-bottom:6px}
  .co-sub{font-size:11px;color:var(--muted);margin-bottom:12px}
  .co-row{display:flex;justify-content:space-between;padding:5px 0;border-bottom:1px solid var(--border);font-size:12px}
  .co-row:last-of-type{border-bottom:none}
  .co-key{color:var(--muted)}
  .co-val{font-weight:600}
  .co-valid{margin-top:12px;padding:8px 12px;background:rgba(76,175,125,.15);border:1px solid rgba(76,175,125,.3);border-radius:6px;font-size:11px;text-align:center;color:#4caf7d}
  .co-invalid{background:rgba(224,80,80,.15);border-color:rgba(224,80,80,.3);color:var(--red)}
  #slider-bar{position:absolute;bottom:0;left:0;right:0;background:var(--panel);border-top:1px solid var(--border);padding:12px 16px;z-index:5}
  .sb-label{font-size:10px;color:var(--muted);text-transform:uppercase;letter-spacing:.07em;display:flex;justify-content:space-between;margin-bottom:8px}
  .sb-label strong{color:var(--gold)}
  #timeline-slider{width:100%;-webkit-appearance:none;appearance:none;height:3px;background:var(--border);border-radius:2px;outline:none;cursor:pointer}
  #timeline-slider::-webkit-slider-thumb{-webkit-appearance:none;width:14px;height:14px;border-radius:50%;background:var(--gold);cursor:pointer}
  #tl-date{text-align:center;font-size:11px;color:var(--text);margin-top:8px}
  .sar-legend{position:absolute;bottom:80px;right:12px;background:var(--panel);border:1px solid var(--border);border-radius:6px;padding:8px 12px;font-size:10px;z-index:5}
  .sar-legend .leg-row{display:flex;align-items:center;gap:6px;margin-bottom:4px}
  .sar-legend .leg-row:last-child{margin-bottom:0}
  .leg-dot{width:10px;height:10px;border-radius:2px;flex-shrink:0}
  @media(max-width:520px){#cert-overlay{max-width:none;left:8px;right:8px;top:8px}}
</style>
</head>
<body>
<div id="map"></div>

<div id="cert-overlay">
  <div class="co-logo">Faro Protocol · Verificación</div>
  <div class="co-title" id="cert-show-id">Cargando…</div>
  <div class="co-sub" id="cert-venue">—</div>
  <div class="co-row"><span class="co-key">Fecha show</span><span class="co-val" id="cert-fecha">—</span></div>
  <div class="co-row"><span class="co-key">NDVI pre-show</span><span class="co-val" id="cert-ndvi-pre">—</span></div>
  <div class="co-row"><span class="co-key">NDVI post-show</span><span class="co-val" id="cert-ndvi-post">—</span></div>
  <div class="co-row"><span class="co-key">Δ NDVI</span><span class="co-val" id="cert-delta">—</span></div>
  <div class="co-row"><span class="co-key">Nivel impacto</span><span class="co-val" id="cert-nivel">—</span></div>
  <div class="co-row"><span class="co-key">Hash SHA-256</span><span class="co-val" id="cert-hash" style="font-size:9px;word-break:break-all">—</span></div>
  <div class="co-valid co-invalid" id="cert-status">Verificando…</div>
</div>

<div class="sar-legend">
  <div style="font-size:9px;color:var(--muted);margin-bottom:4px;text-transform:uppercase;letter-spacing:.05em">SAR Backscatter</div>
  <div class="leg-row"><div class="leg-dot" style="background:#900"></div><span>Compactación alta</span></div>
  <div class="leg-row"><div class="leg-dot" style="background:#e05050"></div><span>Compactación media</span></div>
  <div class="leg-row"><div class="leg-dot" style="background:#888"></div><span>Sin cambio</span></div>
</div>

<div id="slider-bar">
  <div class="sb-label">
    <span>Timeline pre / post show</span>
    <strong id="tl-label">Pre-show</strong>
  </div>
  <input type="range" id="timeline-slider" min="0" max="1" value="0" step="1">
  <div id="tl-date">—</div>
</div>

<script>
'use strict';
const BACKEND  = window.FARO_BACKEND || 'BACKEND_URL_PLACEHOLDER';
const CERT_HASH = '__CERT_HASH__';
const CERT_DATA = __CERT_JSON__;

// Venue coords
const VENUES = {
  amalfitani: { center: [-58.5288, -34.6379], zoom: 15.5 },
  default:    { center: [-58.5288, -34.6379], zoom: 14 },
};

const venueKey  = (CERT_DATA.show_id || '').includes('airbag') ? 'amalfitani' : 'default';
const venueConf = VENUES[venueKey] || VENUES.default;

// Poblar overlay
const d = CERT_DATA;
document.getElementById('cert-show-id').textContent  = d.show_id    || CERT_HASH.slice(0,16) + '…';
document.getElementById('cert-venue').textContent     = d.venue      || 'Estadio Amalfitani';
document.getElementById('cert-fecha').textContent     = d.fecha      || '—';
document.getElementById('cert-ndvi-pre').textContent  = d.ndvi_pre  != null ? (+d.ndvi_pre).toFixed(4)  : '—';
document.getElementById('cert-ndvi-post').textContent = d.ndvi_post != null ? (+d.ndvi_post).toFixed(4) : '—';
document.getElementById('cert-delta').textContent     = d.delta_ndvi != null ? (d.delta_ndvi >= 0 ? '+' : '') + (+d.delta_ndvi).toFixed(4) : '—';
document.getElementById('cert-nivel').textContent     = d.nivel_dano  || '—';
document.getElementById('cert-hash').textContent      = CERT_HASH;

const statusEl = document.getElementById('cert-status');
if (d.valido) {
  statusEl.textContent = 'Certificado válido · verificado en blockchain Faro';
  statusEl.classList.remove('co-invalid');
} else {
  statusEl.textContent = d.error || 'Certificado no encontrado';
}

// MapLibre
const map = new maplibregl.Map({
  container: 'map',
  style: {
    version: 8,
    sources: { osm: { type:'raster', tiles:['https://tile.openstreetmap.org/{z}/{x}/{y}.png'], tileSize:256, attribution:'© OpenStreetMap' } },
    layers:  [{ id:'osm', type:'raster', source:'osm' }],
  },
  center:  venueConf.center,
  zoom:    venueConf.zoom,
});
map.addControl(new maplibregl.NavigationControl(), 'bottom-right');

let sarMode = 'pre'; // pre | post

map.on('load', () => {
  // SAR pre-show
  map.addSource('sar-pre', {
    type:     'raster',
    tiles:    [`${BACKEND}/tiles/sar/{z}/{x}/{y}?venue=amalfitani&opacity=180`],
    tileSize: 256,
  });
  map.addLayer({ id:'sar-pre-layer', type:'raster', source:'sar-pre',
    paint: { 'raster-opacity':0.6, 'raster-hue-rotate':0 } });

  // SAR post-show (mismo source con distinto color via CSS/opacity — en producción
  // usar scene_url distinto para la fecha post)
  map.addSource('sar-post', {
    type:     'raster',
    tiles:    [`${BACKEND}/tiles/sar/{z}/{x}/{y}?venue=amalfitani&opacity=200`],
    tileSize: 256,
  });
  map.addLayer({ id:'sar-post-layer', type:'raster', source:'sar-post',
    layout: { visibility:'none' },
    paint:  { 'raster-opacity':0.6, 'raster-color-range':[0,1] } });

  // Marker del venue
  new maplibregl.Marker({ color:'#c9a84c' })
    .setLngLat(venueConf.center)
    .setPopup(new maplibregl.Popup().setHTML(
      `<strong>${d.show_id || 'Show'}</strong><br>Impacto SAR verificado`))
    .addTo(map);
});

// Timeline slider (0=pre, 1=post)
const tlSlider = document.getElementById('timeline-slider');
const tlLabel  = document.getElementById('tl-label');
const tlDate   = document.getElementById('tl-date');

const DATES = {
  pre:  d.fecha_pre  || (d.fecha ? shiftDate(d.fecha, -7) : '—'),
  post: d.fecha_post || d.fecha || '—',
};

function shiftDate(iso, days) {
  try {
    const dt = new Date(iso);
    dt.setDate(dt.getDate() + days);
    return dt.toISOString().slice(0,10);
  } catch { return iso; }
}

tlDate.textContent = DATES.pre;

tlSlider.addEventListener('input', () => {
  const v = +tlSlider.value;
  sarMode = v === 0 ? 'pre' : 'post';
  tlLabel.textContent = v === 0 ? 'Pre-show' : 'Post-show';
  tlDate.textContent  = v === 0 ? DATES.pre : DATES.post;

  if (map.getLayer('sar-pre-layer'))  map.setLayoutProperty('sar-pre-layer',  'visibility', v === 0 ? 'visible' : 'none');
  if (map.getLayer('sar-post-layer')) map.setLayoutProperty('sar-post-layer', 'visibility', v === 1 ? 'visible' : 'none');
});
</script>
</body>
</html>
"""


@dale_play_bp.route("/verify-visual/<cert_hash>", methods=["GET"])
def dp_verify_visual(cert_hash: str):
    """
    GET /dale-play/verify-visual/{hash}
    Página HTML interactiva: MapLibre + SAR pre/post + datos del certificado.
    Verificable sin login. Apta para QR público.
    """
    import os as _os
    import json as _json

    # Buscar datos del certificado
    cert_data: dict = {"valido": False, "error": "Certificado no encontrado"}
    try:
        from dale_play_storage import get_certification_by_hash
        cert = get_certification_by_hash(cert_hash.upper())
        if cert:
            data = cert.get("data") or {}
            cert_data = {
                "valido":      True,
                "show_id":     cert.get("show_id", ""),
                "venue":       data.get("venue", "Estadio Amalfitani"),
                "fecha":       data.get("fecha_show", data.get("fecha_emision", "")),
                "fecha_pre":   data.get("fecha_pre", ""),
                "fecha_post":  data.get("fecha_post", ""),
                "ndvi_pre":    cert.get("ndvi_pre"),
                "ndvi_post":   cert.get("ndvi_post"),
                "delta_ndvi":  cert.get("delta_ndvi"),
                "nivel_dano":  cert.get("nivel_dano"),
            }
    except Exception as _e:
        log.warning("dp_verify_visual hash=%s: %s", cert_hash[:16], _e)

    backend_url = _os.environ.get("BACKEND_URL", "https://faroprotocol-production.up.railway.app")
    cert_json   = _json.dumps(cert_data)

    html = _VERIFY_VISUAL_HTML \
        .replace("__CERT_HASH__", cert_hash[:64]) \
        .replace("__CERT_JSON__", cert_json) \
        .replace("BACKEND_URL_PLACEHOLDER", backend_url)

    return render_template_string(html)
