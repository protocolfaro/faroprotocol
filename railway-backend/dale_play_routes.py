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


@dale_play_bp.route("/scheduler-status", methods=["GET"])
def dp_scheduler_status():
    """GET /dale-play/scheduler-status — monitores post-show activos."""
    try:
        from dale_play_scheduler import get_active_monitors
        return jsonify({"monitors": get_active_monitors()})
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

    except Exception as exc:
        out["ok"]    = False
        out["error"] = str(exc)
        out["tb"]    = _tb.format_exc()[-1200:]

    return jsonify(out)


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
