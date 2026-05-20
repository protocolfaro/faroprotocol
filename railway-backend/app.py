"""
app.py — Flask backend: Vélez IPOS + heatmap pipeline + daily weather refresh
Faro Protocol · Railway-ready · v2026-05-20
"""
from __future__ import annotations
import hashlib, logging, os, traceback
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
CORS(app)
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


@app.route("/velez/refresh", methods=["POST"])
def manual_refresh():
    """Trigger an immediate weather refresh (admin use, no PIN required from Railway)."""
    result = data_refresh.run_refresh()
    status = "ok" if result.get("ok") else "error"
    code   = 200 if result.get("ok") else 500
    return jsonify({"status": status, **result}), code


@app.route("/velez/refresh_status", methods=["GET"])
def refresh_status():
    return jsonify({
        "last_refresh": _last_refresh,
        "next_refresh": "06:00 ART daily (09:00 UTC)",
        "service": "velez-ipos",
    })


# ── Daily weather cron ────────────────────────────────────────────────────────

_last_refresh: dict = {}

def _daily_refresh():
    global _last_refresh
    log.info("=== Cron: daily weather refresh starting ===")
    result = data_refresh.run_refresh()
    _last_refresh = {**result, "ran_at": datetime.now(timezone.utc).isoformat()}
    if result.get("ok"):
        log.info("=== Cron: daily weather refresh OK ===")
    else:
        log.error("=== Cron: daily weather refresh FAILED: %s ===", result.get("error"))


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
    scheduler.start()
    log.info("APScheduler started — daily refresh at 06:00 ART (09:00 UTC)")
    return scheduler


try:
    _scheduler = _start_scheduler()
    velez_scheduler.register_jobs(_scheduler)
    log.info("Schedulers registered: daily weather + weekly reports")
except Exception as _sched_err:
    _scheduler = None
    log.error("Scheduler failed to start (non-fatal): %s", _sched_err)

# ── Scheduler routes ──────────────────────────────────────────────────────────
app.add_url_rule("/velez/run_now",       "velez_run_now",       velez_scheduler.route_run_now,       methods=["POST"])
app.add_url_rule("/velez/weekly_status", "velez_weekly_status", velez_scheduler.route_weekly_status, methods=["GET"])
app.add_url_rule("/velez/test_whatsapp", "velez_test_whatsapp", velez_scheduler.route_test_whatsapp, methods=["POST"])
app.add_url_rule("/velez/test_email",    "velez_test_email",    velez_scheduler.route_test_email,    methods=["POST"])


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
