"""
app.py — Flask backend: Vélez IPOS + heatmap pipeline + daily weather refresh
Faro Protocol · Railway-ready · v2026-05-20
"""
from __future__ import annotations
import hashlib, logging, os, threading, traceback
from datetime import datetime, timezone

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from flask import Flask, jsonify, request
from flask_cors import CORS
import ipos as ipos_mod
import heatmap_gen
import github_push
import data_refresh
import velez_scheduler

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 10 * 1024 * 1024  # 10 MB — native mobile camera photos
CORS(app, resources={
    r"/velez/cronograma/*": {
        "origins": ["https://protocolfaro.github.io"],
        "methods": ["POST", "OPTIONS"],
        "allow_headers": ["Content-Type", "Accept"],
        "max_age": 600,
    },
    r"/*": {"origins": "*"},
})
logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

_PIN_HASH = os.environ.get("VELEZ_PIN_HASH", "")
_DEV = os.environ.get("FLASK_ENV", "production") == "development"

def _ok_pin(pin):
    if not _PIN_HASH:
        return bool(_DEV)
    if not pin:
        return False
    return hashlib.sha256(str(pin).encode()).hexdigest() == _PIN_HASH


@app.errorhandler(413)
def _too_large(e):
    return jsonify({"status": "error", "error": "Imagen demasiado grande (máx 10 MB)"}), 413


# ── Routes ────────────────────────────────────────────────────────────────────

@app.route("/health")
def health():
    return jsonify({"status": "ok", "service": "velez-ipos"})


@app.route("/velez/horarios", methods=["POST"])
def horarios():
    body = request.get_json(silent=True)
    if not body:
        return jsonify({"status": "error", "error": "JSON body required"}), 400
    if not _ok_pin(body.get("pin")):
        return jsonify({"status": "error", "error": "PIN inválido"}), 401

    semana    = body.get("semana", {})
    sessions  = body.get("sessions", [])
    sem_label = str(semana.get("label", "?"))

    if not sessions:
        return jsonify({"status": "error", "error": "sessions vacío"}), 400

    log.info("Request: semana=%s, sessions=%d", sem_label, len(sessions))

    try:
        ipos_results = ipos_mod.compute_ipos(sessions)
    except Exception as e:
        log.error(traceback.format_exc())
        return jsonify({"status": "error", "error": f"IPOS: {e}"}), 500

    try:
        png_bytes, verify_hashes = heatmap_gen.generate_all(ipos_results, sem_label)
    except Exception as e:
        log.error(traceback.format_exc())
        return jsonify({"status": "error", "error": f"Heatmaps: {e}"}), 500

    hm_urls = {}
    try:
        hm_urls = github_push.push_heatmaps(png_bytes, sem_label, ipos_results)
    except EnvironmentError as e:
        log.warning(str(e)); hm_urls = {k: "" for k in png_bytes}
    except Exception as e:
        log.error(traceback.format_exc()); hm_urls = {k: f"ERROR:{e}" for k in png_bytes}

    cfg_url = ""
    try:
        cfg_url = github_push.push_config(
            ipos_results, sem_label, semana, verify_hashes, sessions
        )
    except EnvironmentError:
        pass
    except Exception as e:
        log.error(traceback.format_exc()); cfg_url = f"ERROR:{e}"

    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    vd_url = ""
    try:
        vd_url = github_push.push_velez_data(ipos_results, ts)
        log.info("velez_data.json updated: %s", vd_url)
    except EnvironmentError:
        pass
    except Exception as e:
        log.error("push_velez_data failed: %s", traceback.format_exc())
        vd_url = f"ERROR:{e}"

    return jsonify({
        "status": "ok",
        "semana": semana,
        "ipos": ipos_results,
        "heatmaps": hm_urls,
        "config_commit": cfg_url,
        "velez_data_commit": vd_url,
        "generado_en": ts,
    })


@app.route("/velez/mediciones", methods=["POST", "DELETE"])
def mediciones():
    body = request.get_json(silent=True)
    if not body:
        return jsonify({"status": "error", "error": "JSON body required"}), 400
    if not _ok_pin(body.get("pin")):
        return jsonify({"status": "error", "error": "PIN inválido"}), 401

    if request.method == "DELETE":
        rec_id = body.get("id", "")
        if not rec_id:
            return jsonify({"status": "error", "error": "id requerido"}), 400
        try:
            result = github_push.delete_medicion(rec_id)
            return jsonify({"status": "ok", "result": result})
        except EnvironmentError as e:
            return jsonify({"status": "error", "error": str(e)}), 503
        except Exception as e:
            log.error(traceback.format_exc())
            return jsonify({"status": "error", "error": str(e)}), 500

    med = body.get("medicion")
    if not med or not med.get("tipo") or not med.get("cancha"):
        return jsonify({"status": "error", "error": "medicion.tipo y medicion.cancha requeridos"}), 400
    try:
        commit_url = github_push.push_medicion(med)
        return jsonify({"status": "ok", "commit": commit_url})
    except EnvironmentError as e:
        return jsonify({"status": "error", "error": str(e)}), 503
    except Exception as e:
        log.error(traceback.format_exc())
        return jsonify({"status": "error", "error": str(e)}), 500


@app.route("/velez/aspersores", methods=["GET", "POST"])
def aspersores():
    if request.method == "GET":
        try:
            data = github_push.get_aspersores()
            return jsonify({"status": "ok", "aspersores_por_cancha": data})
        except EnvironmentError as e:
            return jsonify({"status": "error", "error": str(e)}), 503
        except Exception as e:
            log.error(traceback.format_exc())
            return jsonify({"status": "error", "error": str(e)}), 500

    body = request.get_json(silent=True)
    if not body:
        return jsonify({"status": "error", "error": "JSON body required"}), 400
    if not _ok_pin(body.get("pin")):
        return jsonify({"status": "error", "error": "PIN inválido"}), 401
    cid = body.get("cancha", "")
    asp = body.get("aspersores")
    if not cid or not isinstance(asp, list):
        return jsonify({"status": "error", "error": "cancha y aspersores[] requeridos"}), 400
    try:
        commit_url = github_push.push_aspersores(cid, asp)
        log.info("Aspersores %s guardados: %d puntos → %s", cid.upper(), len(asp), commit_url)
        return jsonify({"status": "ok", "cancha": cid, "n": len(asp), "commit": commit_url})
    except EnvironmentError as e:
        return jsonify({"status": "error", "error": str(e)}), 503
    except Exception as e:
        log.error(traceback.format_exc())
        return jsonify({"status": "error", "error": str(e)}), 500


@app.route("/velez/aspersores/<cancha>", methods=["DELETE"])
def delete_aspersores_route(cancha):
    body = request.get_json(silent=True)
    if not body:
        return jsonify({"status": "error", "error": "JSON body required"}), 400
    if not _ok_pin(body.get("pin")):
        return jsonify({"status": "error", "error": "PIN inválido"}), 401
    try:
        result = github_push.delete_aspersores(cancha)
        log.info("Aspersores %s borrados — %s", cancha.upper(), result)
        return jsonify({"status": "ok", "cancha": cancha, "commit": result})
    except EnvironmentError as e:
        return jsonify({"status": "error", "error": str(e)}), 503
    except Exception as e:
        log.error(traceback.format_exc())
        return jsonify({"status": "error", "error": str(e)}), 500


@app.route("/velez/cronograma/upload", methods=["POST"])
def cronograma_upload():
    """Parse a weekly schedule image via Claude Vision API and push to GitHub."""
    pin = request.form.get("pin") or (request.get_json(force=True, silent=True) or {}).get("pin")
    if not _ok_pin(pin):
        return jsonify({"status": "error", "error": "PIN inválido"}), 401

    if "file" not in request.files:
        return jsonify({"status": "error", "error": "No se recibió archivo (campo 'file')"}), 400

    f = request.files["file"]
    mime = (f.content_type or "image/jpeg").split(";")[0].strip()
    ALLOWED = {"image/jpeg", "image/png", "image/webp", "image/heic", "image/heif"}
    if mime not in ALLOWED:
        return jsonify({"status": "error",
                        "error": f"Tipo no soportado: {mime}. Usar JPEG, PNG o WebP"}), 415

    image_bytes = f.read()
    if len(image_bytes) > 10 * 1024 * 1024:
        return jsonify({"status": "error", "error": "Imagen demasiado grande (máx 10 MB)"}), 413
    if not image_bytes:
        return jsonify({"status": "error", "error": "Archivo vacío"}), 400

    try:
        import vision_cronograma
        parsed = vision_cronograma.parse_cronograma(image_bytes, mime)
    except EnvironmentError as e:
        return jsonify({"status": "error", "error": str(e)}), 503
    except TimeoutError:
        return jsonify({
            "status":  "procesando",
            "message": "La IA tardó más de lo esperado analizando la imagen. "
                       "Esperá 30 segundos y volvé a intentarlo.",
        }), 202
    except Exception as e:
        log.error(traceback.format_exc())
        return jsonify({"status": "error", "error": f"Vision API: {e}"}), 500

    try:
        commit_url = github_push.push_cronograma(parsed)
        log.info("Cronograma OCR pushed: %d sessions → %s",
                 len(parsed.get("sessions", [])), commit_url)
    except EnvironmentError as e:
        return jsonify({"status": "error", "error": str(e)}), 503
    except Exception as e:
        log.error(traceback.format_exc())
        return jsonify({"status": "error", "error": f"GitHub push: {e}"}), 500

    return jsonify({
        "status":   "ok",
        "semana":   parsed.get("semana_label"),
        "sessions": len(parsed.get("sessions", [])),
        "dias":     len(parsed.get("dias", [])),
        "commit":   commit_url,
    })


@app.route("/velez/refresh", methods=["POST"])
def manual_refresh():
    """Trigger an immediate weather refresh (admin use, no PIN required from Railway)."""
    result = data_refresh.run_refresh()
    status = "ok" if result.get("ok") else "error"
    code   = 200 if result.get("ok") else 500
    return jsonify({"status": status, **result}), code


_insar_running = False


@app.route("/velez/refresh_insar", methods=["POST"])
def manual_insar_refresh():
    """
    Trigger InSAR refresh asynchronously.
    Returns 202 immediately — HyP3 processing takes 30-120 min.
    Poll /velez/refresh_status to see result.
    """
    global _insar_running
    if _insar_running:
        return jsonify({
            "status": "running",
            "msg": "InSAR job ya en progreso — revisá /velez/refresh_status en ~60 min",
        }), 409

    def _run():
        global _insar_running, _last_insar
        _insar_running = True
        try:
            result = data_refresh.run_insar_refresh()
            _last_insar = {**result, "ran_at": datetime.now(timezone.utc).isoformat()}
            if result.get("ok"):
                log.info("=== InSAR manual: OK — %s ===", result.get("sectores"))
            else:
                log.error("=== InSAR manual: FAILED — %s ===", result.get("error"))
        except Exception as _e:
            log.error("=== InSAR manual: EXCEPTION — %s ===", _e)
            _last_insar = {"ok": False, "error": str(_e),
                           "ran_at": datetime.now(timezone.utc).isoformat()}
        finally:
            _insar_running = False

    threading.Thread(target=_run, daemon=True).start()
    return jsonify({
        "status": "accepted",
        "msg": "InSAR job iniciado en background (HyP3 tarda 30-120 min). "
               "Revisá /velez/refresh_status para ver resultado.",
        "check": "/velez/refresh_status",
    }), 202


@app.route("/velez/commit_historial", methods=["POST"])
def commit_historial():
    """Guarda snapshot semanal en historial/YYYY-MM-DD.json en GitHub."""
    try:
        result = github_push.push_historial_snapshot()
        return jsonify(result), 200
    except EnvironmentError as e:
        return jsonify({"ok": False, "error": str(e)}), 503
    except Exception as e:
        log.error(traceback.format_exc())
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/velez/refresh_status", methods=["GET"])
def refresh_status():
    return jsonify({
        "service": "velez-ipos",
        "weather": {
            "last": _last_refresh,
            "next": "09:00 UTC diario (06:00 ART)",
        },
        "insar": {
            "running": _insar_running,
            "last": _last_insar,
            "schedule": "Lunes 10:00 UTC (07:00 ART) · Sentinel-1 12-day repeat",
        },
    })


# ── Daily weather cron ────────────────────────────────────────────────────────

_last_refresh: dict = {}
_last_insar:   dict = {}


def _daily_refresh():
    global _last_refresh
    log.info("=== Cron: daily weather refresh starting ===")
    result = data_refresh.run_refresh()
    _last_refresh = {**result, "ran_at": datetime.now(timezone.utc).isoformat()}
    if result.get("ok"):
        log.info("=== Cron: daily weather refresh OK ===")
    else:
        log.error("=== Cron: daily weather refresh FAILED: %s ===", result.get("error"))


def _weekly_insar_refresh():
    global _last_insar
    log.info("=== Cron: weekly InSAR refresh starting ===")
    result = data_refresh.run_insar_refresh()
    _last_insar = {**result, "ran_at": datetime.now(timezone.utc).isoformat()}
    if result.get("ok"):
        log.info("=== Cron: weekly InSAR refresh OK ===")
    else:
        log.error("=== Cron: weekly InSAR refresh FAILED: %s ===", result.get("error"))


def _start_scheduler():
    scheduler = BackgroundScheduler(timezone="UTC")
    # 06:00 ART = 09:00 UTC (ART is UTC-3, no DST in Argentina)
    scheduler.add_job(
        _daily_refresh,
        CronTrigger(hour=9, minute=0, timezone="UTC"),
        id="daily_weather_refresh",
        replace_existing=True,
        misfire_grace_time=3600,
    )
    # Monday 10:00 UTC = Monday 07:00 ART — S1 12-day repeat, post-acquisition processing window
    scheduler.add_job(
        _weekly_insar_refresh,
        CronTrigger(day_of_week="mon", hour=10, minute=0, timezone="UTC"),
        id="weekly_insar_refresh",
        replace_existing=True,
        misfire_grace_time=7200,
    )
    scheduler.start()
    log.info("APScheduler started — daily refresh at 09:00 UTC · InSAR Mondays at 10:00 UTC")
    return scheduler


try:
    _scheduler = _start_scheduler()
    velez_scheduler.register_jobs(_scheduler)
    log.info("Schedulers registered: daily weather + weekly reports")
except Exception as _sched_err:
    _scheduler = None
    log.error("Scheduler failed to start (non-fatal): %s", _sched_err)

# ── Scheduler routes ──────────────────────────────────────────────────────────
app.add_url_rule("/velez/run_now",        "velez_run_now",        velez_scheduler.route_run_now,        methods=["POST"])
app.add_url_rule("/velez/weekly_status",  "velez_weekly_status",  velez_scheduler.route_weekly_status,  methods=["GET"])
app.add_url_rule("/velez/test_whatsapp",  "velez_test_whatsapp",  velez_scheduler.route_test_whatsapp,  methods=["POST"])
app.add_url_rule("/velez/test_email",     "velez_test_email",     velez_scheduler.route_test_email,     methods=["POST"])
app.add_url_rule("/velez/smtp_diag",      "velez_smtp_diag",      velez_scheduler.route_smtp_diag,      methods=["GET"])


# ── Grass recovery projection ─────────────────────────────────────────────────

@app.route("/velez/recovery", methods=["POST"])
def velez_recovery():
    """
    Simulate Richards grass recovery from current NDVI state.
    Body: { "cancha": "1fa", "n0": 0.31, "days": 30 }
    Returns three scenarios: basico / intermedio / intensivo.
    """
    body   = request.get_json(silent=True) or {}
    cancha = body.get("cancha", "")
    n0_raw = body.get("n0")
    days   = min(int(body.get("days", 30)), 90)

    # If n0 not provided, read from velez_data.json
    if n0_raw is None and cancha:
        try:
            from urllib.request import urlopen, Request as UReq
            import json as _json
            req = UReq(
                "https://raw.githubusercontent.com/protocolfaro/faro-paneles/main/velez/velez_data.json",
                headers={"User-Agent": "FaroProtocol/4.0"},
            )
            with urlopen(req, timeout=10) as r:
                vd = _json.loads(r.read())
            canchas = vd.get("sectores", {}).get("canchero", {}).get("canchas", [])
            for c in canchas:
                if c.get("id", "").lower() == cancha.lower():
                    n0_raw = c.get("ndvi", 0.4)
                    break
        except Exception as _e:
            log.warning("recovery: could not read ndvi for %s: %s", cancha, _e)

    n0 = float(n0_raw) if n0_raw is not None else 0.4
    n0 = max(0.01, min(0.99, n0))

    try:
        import faro_recovery as _fr
        results = _fr.simulate_all_scenarios(n0, t_days=days)
        # Identify when each scenario reaches 75% of K
        target = 0.75
        for sc in results.values():
            t_arr = sc["t"]
            N_arr = sc["N"]
            days_to = next((t_arr[i] for i, n in enumerate(N_arr) if n >= target), None)
            sc["days_to_75pct"] = round(days_to, 1) if days_to else None
        return jsonify({
            "status": "ok",
            "cancha": cancha,
            "n0": n0,
            "scenarios": results,
        })
    except ImportError:
        return jsonify({"status": "error", "error": "faro_recovery not available (scipy missing)"}), 503
    except Exception as e:
        log.error(traceback.format_exc())
        return jsonify({"status": "error", "error": str(e)}), 500


# ── Pasaporte Climático ───────────────────────────────────────────────────────

@app.route("/pasaporte/request", methods=["POST"])
def pasaporte_request():
    """
    Receive a Pasaporte Climático order after PayPal payment.
    Body: { "email": str, "lote_name": str, "tier": str, "coords_geojson": [...] }
    Stores the request in GitHub and sends confirmation emails.
    """
    body = request.get_json(silent=True) or {}
    email      = body.get("email", "").strip()
    lote_name  = body.get("lote_name", "").strip() or "Sin nombre"
    tier       = body.get("tier", "").strip()
    coords     = body.get("coords_geojson")

    if not email or "@" not in email:
        return jsonify({"status": "error", "error": "email requerido"}), 400

    from datetime import datetime, timezone
    ts      = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    order_id = f"PP-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}"

    # 1. Store request in GitHub
    try:
        import base64, json as _json, requests as _req
        token = os.environ.get("GITHUB_TOKEN", "")
        if token:
            record = {
                "order_id": order_id,
                "email": email,
                "lote_name": lote_name,
                "tier": tier,
                "coords_geojson": coords,
                "submitted_at": ts,
                "status": "pending",
            }
            path    = f"pasaporte/requests/{order_id}.json"
            content = base64.b64encode(
                _json.dumps(record, ensure_ascii=False, indent=2).encode()
            ).decode()
            _req.put(
                f"https://api.github.com/repos/protocolfaro/faroprotocol/contents/{path}",
                headers={"Authorization": f"Bearer {token}",
                         "Accept": "application/vnd.github+json",
                         "X-GitHub-Api-Version": "2022-11-28"},
                json={"message": f"pasaporte request {order_id}", "content": content,
                      "branch": "main"},
                timeout=20,
            )
            log.info("Pasaporte request stored: %s", order_id)
    except Exception as _ge:
        log.warning("pasaporte: github store failed (non-fatal): %s", _ge)

    # 2. Send confirmation email to buyer
    try:
        html_buyer = f"""
<html><body style="font-family:Arial,sans-serif;background:#07110a;color:#f2ede4;padding:24px">
<h2 style="color:#c9a84c">Pasaporte Climático Faro — Solicitud recibida</h2>
<p>Hola, recibimos tu solicitud para el lote <b>{lote_name}</b>.</p>
<p><b>ID de pedido:</b> {order_id}</p>
<p><b>Tier:</b> {tier}</p>
<p>Procesaremos el pago y generaremos tu reporte satelital. Recibirás el PDF en este mail
en aproximadamente <b>3 minutos</b> después de la confirmación del pago.</p>
<p style="color:#9aa0a8;font-size:12px">Si tenés dudas escribinos a
<a href="mailto:protocolfaro@gmail.com" style="color:#c9a84c">protocolfaro@gmail.com</a></p>
<hr style="border-color:#c9a84c44">
<p style="color:#9aa0a8;font-size:11px">Faro Protocol · Satellite Intelligence · {ts}</p>
</body></html>"""
        velez_scheduler.send_email(email, f"Pasaporte Climático — Pedido {order_id}", html_buyer)
    except Exception as _me:
        log.warning("pasaporte: buyer email failed: %s", _me)

    # 3. Notify admin
    try:
        import json as _json
        coords_str = _json.dumps(coords, ensure_ascii=False)[:500] if coords else "No especificado"
        html_admin = f"""
<html><body style="font-family:Arial,sans-serif;background:#07110a;color:#f2ede4;padding:24px">
<h2 style="color:#c9a84c">Nuevo Pedido Pasaporte Climático</h2>
<p><b>Order ID:</b> {order_id}</p>
<p><b>Email comprador:</b> {email}</p>
<p><b>Lote:</b> {lote_name}</p>
<p><b>Tier:</b> {tier}</p>
<p><b>Coords (extracto):</b><br><code style="font-size:11px;color:#9aa0a8">{coords_str}</code></p>
<p style="color:#9aa0a8;font-size:11px">{ts}</p>
</body></html>"""
        admin_email = os.environ.get("GMAIL_USER", "protocolfaro@gmail.com")
        velez_scheduler.send_email(admin_email,
                                   f"[Pasaporte] Nuevo pedido {order_id} — {email}", html_admin)
    except Exception as _ae:
        log.warning("pasaporte: admin email failed: %s", _ae)

    return jsonify({
        "status":   "ok",
        "order_id": order_id,
        "message":  "Solicitud recibida. Revisá tu mail en los próximos minutos.",
    })


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
