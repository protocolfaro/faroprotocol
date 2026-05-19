"""
app.py — Flask backend: Vélez IPOS + heatmap pipeline
Faro Protocol · Railway-ready
"""
from __future__ import annotations
import hashlib, logging, os, traceback
from datetime import datetime, timezone

from flask import Flask, jsonify, request
from flask_cors import CORS
import ipos as ipos_mod
import heatmap_gen
import github_push

app = Flask(__name__)
CORS(app)
logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

_PIN_HASH = os.environ.get("VELEZ_PIN_HASH","")
_DEV = os.environ.get("FLASK_ENV","production") == "development"

def _ok_pin(pin):
    if not _PIN_HASH:
        if _DEV:
            log.warning("PIN check skipped (dev mode)")
            return True
        return False
    if not pin: return False
    return hashlib.sha256(str(pin).encode()).hexdigest() == _PIN_HASH

@app.route("/health")
def health():
    return jsonify({"status":"ok","service":"velez-ipos"})

@app.route("/velez/horarios", methods=["POST"])
def horarios():
    body = request.get_json(silent=True)
    if not body:
        return jsonify({"status":"error","error":"JSON body required"}), 400

    if not _ok_pin(body.get("pin")):
        return jsonify({"status":"error","error":"PIN inválido"}), 401

    semana   = body.get("semana", {})
    sessions = body.get("sessions", [])
    sem_label = str(semana.get("label","?"))

    if not sessions:
        return jsonify({"status":"error","error":"sessions vacío"}), 400

    log.info(f"Request: semana={sem_label}, sessions={len(sessions)}")

    try:
        ipos_results = ipos_mod.compute_ipos(sessions)
    except Exception as e:
        log.error(traceback.format_exc())
        return jsonify({"status":"error","error":f"IPOS: {e}"}), 500

    try:
        png_bytes, verify_hashes = heatmap_gen.generate_all(ipos_results, sem_label)
    except Exception as e:
        log.error(traceback.format_exc())
        return jsonify({"status":"error","error":f"Heatmaps: {e}"}), 500

    hm_urls = {}
    cfg_url = ""
    try:
        hm_urls = github_push.push_heatmaps(png_bytes, sem_label, ipos_results)
    except EnvironmentError as e:
        log.warning(str(e)); hm_urls = {k:"" for k in png_bytes}
    except Exception as e:
        log.error(traceback.format_exc()); hm_urls = {k:f"ERROR:{e}" for k in png_bytes}

    try:
        cfg_url = github_push.push_config(
            ipos_results, sem_label, semana, verify_hashes, sessions
        )
    except EnvironmentError:
        pass
    except Exception as e:
        log.error(traceback.format_exc()); cfg_url = f"ERROR:{e}"

    return jsonify({
        "status": "ok",
        "semana": semana,
        "ipos": ipos_results,
        "heatmaps": hm_urls,
        "config_commit": cfg_url,
        "generado_en": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    })

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT",5000)))
