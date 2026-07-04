"""
app.py — Flask backend: Vélez IPOS + heatmap pipeline + daily weather refresh
Faro Protocol · Railway-ready · v2026-05-20
"""
from __future__ import annotations
import base64, hashlib, json, logging, os, sys, threading, traceback
import requests as _requests
from datetime import datetime, timezone

# ── Path setup (ARCHITECTURE.md) ─────────────────────────────────────────────
_HERE = os.path.dirname(os.path.abspath(__file__))
_VELEZ_PATH = os.path.join(_HERE, "sports", "clients", "velez")
_CORE_PATH  = os.path.join(_HERE, "core")
if _VELEZ_PATH not in sys.path:
    sys.path.insert(0, _VELEZ_PATH)
if _CORE_PATH not in sys.path:
    sys.path.insert(0, _CORE_PATH)
# ─────────────────────────────────────────────────────────────────────────────

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from flask import Flask, jsonify, request
from flask_cors import CORS
import ipos as ipos_mod
import heatmap_gen
import github_push
import data_refresh
import velez_scheduler
import dale_play_routes

app = Flask(__name__)
CORS(app)
app.register_blueprint(dale_play_routes.dale_play_bp)
try:
    from faro_infra import infra_bp as _infra_bp
    app.register_blueprint(_infra_bp)
except ImportError as _infra_err:
    import logging as _ilog
    _ilog.getLogger(__name__).warning("faro_infra skip: %s", _infra_err)
try:
    import sys as _sys_r, os as _os_r
    _sys_r.path.insert(0, _os_r.path.join(_HERE, "routes"))
    from routes.health import health_bp as _health_bp
    app.register_blueprint(_health_bp)
except Exception as _health_err:
    import logging as _hlog
    _hlog.getLogger(__name__).warning("routes.health skip: %s", _health_err)
try:
    import tiles_blueprint as _tiles_mod
    app.register_blueprint(_tiles_mod.tiles_bp)
except ImportError as _tile_err:
    import logging as _tlog
    _tlog.getLogger(__name__).warning("tiles_blueprint skip (rio-tiler no instalado): %s", _tile_err)
try:
    from faro_engine import engine_bp as _engine_bp
    app.register_blueprint(_engine_bp)
except ImportError as _eng_err:
    import logging as _elog
    _elog.getLogger(__name__).warning("faro_engine skip: %s", _eng_err)
logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

_PIN_HASH = os.environ.get("VELEZ_PIN_HASH", "")
_DEV = os.environ.get("FLASK_ENV", "production") == "development"

# ── MachinaOS GIS — MCP mock + import ────────────────────────────────────────
try:
    from unittest.mock import MagicMock as _MM
    _fm = _MM(); _fi = _MM(); _fi.tool.return_value = lambda f: f; _fm.FastMCP.return_value = _fi
    sys.modules.setdefault("mcp", _MM())
    sys.modules.setdefault("mcp.server", _MM())
    sys.modules.setdefault("mcp.server.fastmcp", _fm)
    from machina_gis_server import (
        AREAS                      as _GIS_AREAS,
        calcular_indices_espectrales as _gis_indices,
        analizar_cambio_sar        as _gis_sar_change,
        generar_mapa_reporte       as _gis_report,
        computar_coherencia_insar  as _gis_insar,
    )
    _GIS_OK = True
    log.info("MachinaOS GIS loaded — areas: %s", list(_GIS_AREAS.keys()))
except Exception as _ge:
    _GIS_OK = False
    log.warning("MachinaOS GIS not available: %s", _ge)
# ─────────────────────────────────────────────────────────────────────────────

def _ok_pin(pin):
    if not _PIN_HASH:
        return True  # No PIN configured — open access
    if not pin:
        return False
    return hashlib.sha256(str(pin).encode()).hexdigest() == _PIN_HASH


# ── Routes ────────────────────────────────────────────────────────────────────

@app.route("/health")
def health():
    return jsonify({
        "status": "ok",
        "service": "velez-ipos",
        "commit": os.environ.get("RAILWAY_GIT_COMMIT_SHA", "unknown"),
    })


@app.route("/metrics")
def prometheus_metrics():
    """Prometheus text exposition — lee /tmp/faro_metrics.prom escrito por FaroEngine V2."""
    import time as _time
    from flask import Response as _Resp
    path = os.environ.get("PROMETHEUS_METRICS_PATH", "/tmp/faro_metrics.prom")
    if not os.path.exists(path):
        return _Resp(
            "# faro_metrics.prom not found — run FaroEngine first\n",
            mimetype="text/plain; version=0.0.4", status=200,
        )
    age_s = _time.time() - os.path.getmtime(path)
    content = open(path, encoding="utf-8").read()
    content += f"\n# Last updated {age_s:.0f}s ago\n"
    return _Resp(content, mimetype="text/plain; version=0.0.4")


@app.route("/velez/health")
def velez_health():
    """System health: data freshness, satellite state, InSAR status, env config."""
    import requests as _rq, base64 as _b64, json as _json
    now = datetime.now(timezone.utc)
    checks = {}

    # Check velez_data.json freshness via GitHub
    token = os.environ.get("GITHUB_TOKEN", "")
    vd_age_h = None
    heatmap_semana = None
    ndvi_canchas = 0
    insar_configured = bool(os.environ.get("NASA_EARTHDATA_USER"))
    if token:
        try:
            r = _rq.get(
                "https://api.github.com/repos/protocolfaro/faroprotocol/contents/velez/velez_data.json",
                headers={"Authorization": f"Bearer {token}",
                         "Accept": "application/vnd.github+json",
                         "X-GitHub-Api-Version": "2022-11-28"},
                params={"ref": "main"}, timeout=10,
            )
            if r.status_code == 200:
                vd = _json.loads(_b64.b64decode(r.json()["content"]).decode())
                updated = vd.get("updated_at", "")
                if updated:
                    dt = datetime.fromisoformat(updated.replace("Z", "+00:00"))
                    vd_age_h = round((now - dt).total_seconds() / 3600, 1)
                roger = vd.get("usuarios", {}).get("roger", {})
                heatmap_semana = roger.get("heatmaps_meta", {}).get("semana")
                hm = roger.get("heatmaps", {})
                ndvi_canchas = sum(1 for v in hm.values()
                                   if isinstance(v, dict) and v.get("ndvi") is not None)
        except Exception as _e:
            checks["github_error"] = str(_e)

    # Freshness thresholds
    weather_ok  = vd_age_h is not None and vd_age_h < 36
    satellite_ok = heatmap_semana is not None

    if heatmap_semana:
        from datetime import date as _date
        try:
            img_dt   = _date.fromisoformat(heatmap_semana)
            img_age_d = (now.date() - img_dt).days
        except Exception:
            img_age_d = None
    else:
        img_age_d = None

    overall = "ok" if (weather_ok and satellite_ok) else "degraded"
    return jsonify({
        "status":           overall,
        "service":          "velez-ipos",
        "timestamp":        now.isoformat(),
        "weather": {
            "ok":       weather_ok,
            "age_h":    vd_age_h,
            "threshold_h": 36,
        },
        "satellite": {
            "ok":        satellite_ok and (img_age_d is not None and img_age_d <= 7),
            "semana":    heatmap_semana,
            "age_days":  img_age_d,
            "threshold_days": 7,
            "canchas_con_ndvi": ndvi_canchas,
        },
        "insar": {
            "configured": insar_configured,
            "msg": ("NASA_EARTHDATA_USER/PASS set — InSAR activo" if insar_configured
                    else "InSAR inactivo — set NASA_EARTHDATA_USER + NASA_EARTHDATA_PASS"),
        },
        "github_token": bool(token),
        **checks,
    })


@app.route("/velez/horarios", methods=["POST"])
def horarios():
    body = request.get_json(silent=True)
    if not body:
        return jsonify({"status": "error", "error": "JSON body required"}), 400
    semana    = body.get("semana", {})
    sessions  = body.get("sessions", [])
    sem_label = str(semana.get("label", "?"))

    if not sessions:
        return jsonify({"status": "error", "error": "sessions vacío"}), 400

    log.info("Request: semana=%s, sessions=%d", sem_label, len(sessions))

    for _warn in ipos_mod.validate_sessions(sessions):
        log.warning(_warn)

    try:
        ipos_results = ipos_mod.compute_ipos(sessions)
    except Exception as e:
        log.error(traceback.format_exc())
        return jsonify({"status": "error", "error": f"IPOS: {e}"}), 500

    # Neutral defaults for canchas with no sessions this week; compute_ipos overrides if sessions present
    _EXTRA_DEFAULTS = {
        "amalfitani":  {"score":0,"semaforo":"verde","icono":"🟢","texto":"Sin actividad registrada","personas":0,"horas":0,"detalle":"Campo Principal — Amalfitani"},
        "poli_f11":    {"score":0,"semaforo":"verde","icono":"🟢","texto":"Sin actividad registrada","personas":0,"horas":0,"detalle":"Fútbol 11 — Polideportivo Feijóo"},
        "poli_f8a":    {"score":0,"semaforo":"verde","icono":"🟢","texto":"Sin actividad registrada","personas":0,"horas":0,"detalle":"Fútbol 8A — Polideportivo Feijóo"},
        "poli_f8b":    {"score":0,"semaforo":"verde","icono":"🟢","texto":"Sin actividad registrada","personas":0,"horas":0,"detalle":"Fútbol 8B — Polideportivo Feijóo"},
        "poli_hockey": {"score":0,"semaforo":"verde","icono":"🟢","texto":"Sin actividad registrada","personas":0,"horas":0,"detalle":"Hockey — Polideportivo Feijóo"},
    }
    for k, v in _EXTRA_DEFAULTS.items():
        ipos_results.setdefault(k, v)  # compute_ipos result takes precedence if sessions were submitted

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


@app.route("/velez/cronograma", methods=["POST"])
def cronograma():
    body = request.get_json(silent=True)
    if not body:
        return jsonify({"status": "error", "error": "JSON body required"}), 400
    if not _ok_pin(body.get("pin")):
        return jsonify({"status": "error", "error": "PIN inválido"}), 401
    image_b64 = body.get("image_b64", "")
    if not image_b64:
        return jsonify({"status": "error", "error": "image_b64 requerido"}), 400
    try:
        image_bytes = base64.b64decode(image_b64)
    except Exception:
        return jsonify({"status": "error", "error": "image_b64 inválido"}), 400
    try:
        url = github_push.push_cronograma(image_bytes)
        return jsonify({"status": "ok", "url": url})
    except Exception as e:
        log.error(traceback.format_exc())
        return jsonify({"status": "error", "error": str(e)}), 500


@app.route("/velez/cronograma/parse", methods=["POST"])
def cronograma_parse():
    body = request.get_json(silent=True) or {}
    if not _ok_pin(body.get("pin")):
        return jsonify({"status": "error", "error": "PIN inválido"}), 401

    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        return jsonify({"status": "error", "error": "ANTHROPIC_API_KEY no configurada"}), 500

    # 1. Download image from GitHub raw
    raw_url = (f"https://raw.githubusercontent.com/{github_push.OWNER}/"
               f"{github_push.REPO}/main/{github_push.CRON_PATH}")
    try:
        img_r = _requests.get(raw_url, timeout=20)
        img_r.raise_for_status()
        img_b64 = base64.b64encode(img_r.content).decode()
    except Exception as e:
        return jsonify({"status": "error", "error": f"Descarga imagen: {e}"}), 502

    # 2. Send to Claude Vision
    prompt = (
        "Esta es una tabla de uso de canchas de fútbol. "
        "Extraé los datos y devolvé SOLO un JSON con esta estructura: "
        "{\"categorias\": [{\"nombre\": string, \"dias\": {\"lunes\": string, \"martes\": string, "
        "\"miercoles\": string, \"jueves\": string, \"viernes\": string, \"sabado\": string}, "
        "\"horario\": string}]}. "
        "Los valores de días son el número de cancha o 'LIBRE' o 'CAMPUS' o vacío."
    )
    try:
        claude_r = _requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key":         api_key,
                "anthropic-version": "2023-06-01",
                "content-type":      "application/json",
            },
            json={
                "model":      "claude-sonnet-4-5",
                "max_tokens": 2048,
                "messages": [{
                    "role": "user",
                    "content": [
                        {"type": "image", "source": {
                            "type":       "base64",
                            "media_type": "image/jpeg",
                            "data":       img_b64,
                        }},
                        {"type": "text", "text": prompt},
                    ],
                }],
            },
            timeout=60,
        )
        claude_r.raise_for_status()
        text = claude_r.json()["content"][0]["text"]
    except Exception as e:
        log.error(traceback.format_exc())
        return jsonify({"status": "error", "error": f"Claude API: {e}"}), 502

    # 3. Parse JSON — strip markdown fences if present
    try:
        clean = text.strip()
        if clean.startswith("```"):
            parts = clean.split("```")
            clean = parts[1].lstrip("json").strip() if len(parts) > 1 else clean
        categorias = json.loads(clean)["categorias"]
    except Exception as e:
        return jsonify({"status": "error", "error": f"JSON parse: {e}", "raw": text[:500]}), 422

    # 4. Convert categorias → sessions
    DIA_MAP   = {"lunes":"lun","martes":"mar","miercoles":"mie",
                 "jueves":"jue","viernes":"vie","sabado":"sab"}
    TURNO_MAP = {"mañana":"manana","manana":"manana","morning":"manana",
                 "tarde":"infantiles","infantiles":"infantiles",
                 "noche":"femenino","femenino":"femenino"}
    SKIP      = {"","libre","-","—","libre"}

    def _norm_cancha(c: str) -> str:
        c = c.strip().lower()
        if c in SKIP or c == "campus":
            return c
        if c.endswith("fa") or c.endswith("fp"):
            return c
        if c.isdigit():
            return c + "fa"
        return c

    sessions, seen_canchas = [], set()
    for cat in categorias:
        nombre = (cat.get("nombre") or "").strip()
        if not nombre:
            continue
        turno = TURNO_MAP.get((cat.get("horario") or "").lower().strip(), "manana")
        for dia_es, raw_val in (cat.get("dias") or {}).items():
            dia_id = DIA_MAP.get(dia_es.lower().strip())
            if not dia_id:
                continue
            val = (raw_val or "").strip().lower()
            if val in SKIP:
                continue
            canchas = [_norm_cancha(c) for c in val.replace("/", " ").split() if c.strip()]
            canchas = [c for c in canchas if c not in SKIP]
            if not canchas:
                continue
            sessions.append({"categoria": nombre, "dia": dia_id,
                              "canchas": canchas, "turno": turno, "factor": 1.0})
            seen_canchas.update(c for c in canchas if c != "campus")

    if not sessions:
        return jsonify({"status": "error", "error": "No se detectaron sesiones en la imagen"}), 422

    # 5. Update horarios_vo_semana.sessions in config_velez.json
    try:
        commit_url = github_push.push_horarios_from_parse(sessions)
    except Exception as e:
        log.error(traceback.format_exc())
        return jsonify({"status": "error", "error": f"GitHub push: {e}"}), 500

    return jsonify({
        "status":             "ok",
        "canchas_detectadas": len(seen_canchas),
        "sessions_generadas": len(sessions),
        "commit":             commit_url,
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
        _tipo = med.get("tipo", "").lower()
        if   _tipo == "clegg":    commit_url = github_push.push_clegg_medicion(med)
        elif _tipo == "traccion": commit_url = github_push.push_traccion_medicion(med)
        elif _tipo == "altura":   commit_url = github_push.push_altura_medicion(med)
        elif _tipo == "humedad":  commit_url = github_push.push_humedad_medicion(med)
        elif _tipo == "raices":   commit_url = github_push.push_raices_medicion(med)
        else:                     commit_url = github_push.push_medicion(med)
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
    try:
        result = github_push.delete_aspersores(cancha)
        log.info("Aspersores %s borrados — %s", cancha.upper(), result)
        return jsonify({"status": "ok", "cancha": cancha, "commit": result})
    except EnvironmentError as e:
        return jsonify({"status": "error", "error": str(e)}), 503
    except Exception as e:
        log.error(traceback.format_exc())
        return jsonify({"status": "error", "error": str(e)}), 500


@app.route("/velez/refresh", methods=["POST"])
def manual_refresh():
    """Trigger an immediate weather refresh (admin use, no PIN required from Railway)."""
    result = data_refresh.run_refresh()
    status = "ok" if result.get("ok") else "error"
    code   = 200 if result.get("ok") else 500
    return jsonify({"status": status, **result}), code


@app.route("/velez/run-refresh", methods=["POST"])
def velez_run_refresh():
    """
    Fuerza un ciclo data_refresh completo (clima + Supabase inserts + satélite).
    PIN-protegido. Async — retorna 202 inmediatamente.
    Resultado disponible en /velez/refresh_status después de ~60s.
    """
    global _last_refresh
    body = request.get_json(silent=True) or {}
    if not _ok_pin(body.get("pin")):
        return jsonify({"status": "error", "error": "PIN inválido"}), 401

    def _run():
        global _last_refresh
        log.info("=== /velez/run-refresh: ciclo forzado iniciado ===")
        result = data_refresh.run_refresh()
        _last_refresh = {
            **result,
            "ran_at":       datetime.now(timezone.utc).isoformat(),
            "triggered_by": "manual_pin",
        }
        if result.get("ok"):
            log.info("=== /velez/run-refresh: OK — ts=%s ===", result.get("ts"))
        else:
            log.error("=== /velez/run-refresh: FAILED — %s ===", result.get("error"))

    threading.Thread(target=_run, daemon=True, name="manual_refresh").start()
    return jsonify({
        "status": "accepted",
        "msg":    "Ciclo data_refresh iniciado en background. "
                  "Revisá /velez/refresh_status en ~60s para ver el resultado.",
        "check":  "/velez/refresh_status",
    }), 202


@app.route("/velez/sar-backfill", methods=["POST"])
def velez_sar_backfill():
    """
    Dispara backfill Sentinel-1 GRD sigma0-calibrado → soil_metrics para Amalfitani.
    Lee A_cal del XML de calibracion de cada escena. PIN-protegido.
    Body: {"pin": "...", "days": 180, "limit": 60, "clean": true}
    clean=true borra las filas con formula incorrecta antes de insertar.
    """
    body = request.get_json(silent=True) or {}
    if not _ok_pin(body.get("pin")):
        return jsonify({"status": "error", "error": "PIN invalido"}), 401

    days        = max(1, min(int(body.get("days",  30)), 730))
    limit       = max(1, min(int(body.get("limit",  20)), 120))
    clean_first = bool(body.get("clean", False))
    from_latest = bool(body.get("from_latest", True))   # cascade desde última fecha en DB

    try:
        import sys as _sys
        _velez_dir = os.path.join(_HERE, "sports", "clients", "velez")
        if _velez_dir not in _sys.path:
            _sys.path.insert(0, _velez_dir)
        from faro_sar_s1_backfill import run_s1_backfill
        result = run_s1_backfill(days=days, scene_limit=limit,
                                  clean_first=clean_first, from_latest=from_latest)
        return jsonify({"status": "ok", **result}), 200
    except Exception as exc:
        log.exception("sar-backfill error")
        return jsonify({"status": "error", "error": str(exc)}), 500


@app.route("/velez/diag-supabase", methods=["GET"])
def diag_supabase():
    """
    Test INSERT+DELETE en cada tabla del data lake y expone el HTTP status/body exacto.
    Permite verificar permisos y schema sin revisar Railway logs.
    """
    import requests as _rq
    from velez_supabase import _base, _hdrs, _ok
    results: dict = {"supabase_configured": _ok(), "tables": {}}
    if not _ok():
        return jsonify(results), 200

    _test_rows = {
        "climate_metrics":    {"venue_id": "_diag", "fecha": "2000-01-01", "fuente": "_diag_test"},
        "soil_metrics":       {"venue_id": "_diag", "fuente": "_diag_test"},
        "vegetation_metrics": {"venue_id": "_diag", "fuente": "_diag_test", "metodo_generacion": "SENTINEL_DIRECTO"},
        "velez_intervenciones": {"cancha_id": "_diag", "tipo": "riego", "detalle": "_diag_test"},
    }
    hdrs_post = _hdrs({"Prefer": "return=representation"})
    hdrs_del  = _hdrs()
    for table, payload in _test_rows.items():
        try:
            url  = f"{_base()}/rest/v1/{table}"
            r_in = _rq.post(url, headers=hdrs_post, json=payload, timeout=8)
            entry: dict = {"insert_status": r_in.status_code, "insert_body": r_in.text[:300]}
            # Delete test row on success
            if r_in.status_code in (200, 201):
                pk_field = "cancha_id" if table == "velez_intervenciones" else "venue_id"
                _rq.delete(f"{url}?{pk_field}=eq._diag", headers=hdrs_del, timeout=8)
                entry["insert_ok"] = True
            else:
                entry["insert_ok"] = False
        except Exception as exc:
            entry = {"insert_ok": False, "error": str(exc)}
        results["tables"][table] = entry
    return jsonify(results), 200


_insar_running    = False
_satellite_running = False
_last_satellite:  dict = {}


@app.route("/velez/satellite_force", methods=["POST"])
def satellite_force():
    """Force satellite pipeline with existing NDVI data, bypassing date dedup.
    PIN-protected. Runs async — poll /velez/refresh_status for result.
    """
    global _satellite_running
    body = request.get_json(silent=True) or {}
    if not _ok_pin(body.get("pin")):
        return jsonify({"status": "error", "error": "PIN inválido"}), 401
    if _satellite_running:
        return jsonify({"status": "running",
                        "msg": "Satellite job ya en progreso — revisá /velez/refresh_status"}), 409

    def _run():
        global _last_satellite, _satellite_running
        _satellite_running = True
        try:
            import satellite_pipeline

            # Descarga real desde Sentinel-2/Landsat via pystac — cascada completa.
            # pystac-client, rasterio y stackstac están en requirements.txt.
            log.info("satellite_force: llamando ndvi_real.fetch_ndvi() — cascada S2/Landsat")
            result = satellite_pipeline.run_satellite_cycle(None, force=True)
            _last_satellite = {**result,
                               "ran_at": datetime.now(timezone.utc).isoformat(),
                               "running": False}
            log.info("=== satellite_force OK: %s ===", result)
        except Exception as _e:
            log.error("satellite_force FAILED: %s", _e)
            _last_satellite = {"ok": False, "error": str(_e),
                               "ran_at": datetime.now(timezone.utc).isoformat(),
                               "running": False}
        finally:
            _satellite_running = False

    threading.Thread(target=_run, daemon=True).start()
    return jsonify({
        "status":  "accepted",
        "msg":     "Pipeline iniciado con force=True — descargando S2/Landsat real via cascada ndvi_real.",
        "check":   "/velez/refresh_status",
    }), 202


@app.route("/velez/shadow_maps", methods=["POST"])
def shadow_maps():
    """Store permanent shadow analysis per cancha.
    Body: {pin, shadow_maps: {cid: {sombra_permanente_pct, horas_sol_dia, notas}}}"""
    body = request.get_json(silent=True) or {}
    if not _ok_pin(body.get("pin")):
        return jsonify({"status": "error", "error": "PIN inválido"}), 401
    data = body.get("shadow_maps")
    if not data or not isinstance(data, dict):
        return jsonify({"status": "error", "error": "shadow_maps dict requerido"}), 400
    try:
        commit_url = github_push.push_shadow_maps(data)
        return jsonify({"status": "ok", "canchas": list(data.keys()), "commit": commit_url})
    except EnvironmentError as e:
        return jsonify({"status": "error", "error": str(e)}), 503
    except Exception as e:
        log.error(traceback.format_exc())
        return jsonify({"status": "error", "error": str(e)}), 500


@app.route("/velez/insar_debug", methods=["GET"])
def insar_debug():
    """Diagnose InSAR pipeline: imports, ASF search, pair finding — no HyP3 job submitted."""
    report = {}
    # 1. Import check
    try:
        import hyp3_sdk; report["hyp3_sdk"] = hyp3_sdk.__version__
    except ImportError as e:
        report["hyp3_sdk"] = f"ImportError: {e}"
    try:
        import rasterio; report["rasterio"] = rasterio.__version__
    except ImportError as e:
        report["rasterio"] = f"ImportError: {e}"
    # 2. ASF granule search
    try:
        import insar_hyp3
        granules = insar_hyp3._search_slc_granules(days_back=30)
        report["granules_found"] = len(granules)
        report["granules"] = [
            {"name": g.get("granuleName", g.get("sceneName", "?")),
             "start": g.get("startTime", "?"),
             "relativeOrbit": g.get("relativeOrbit"),
             "pathNumber": g.get("pathNumber"),
             "track": g.get("track")}
            for g in granules[:10]
        ]
        # 3. Pair finding
        pair = insar_hyp3._find_pair(granules) if granules else None
        if pair:
            report["pair_found"] = True
            report["pair"] = {
                "ref":  (pair[0].get("startTime") or "")[:10],
                "sec":  (pair[1].get("startTime") or "")[:10],
                "delta_days": ((lambda d1, d2: (d2 - d1).days)(
                    __import__("datetime").datetime.fromisoformat(pair[0]["startTime"][:10]),
                    __import__("datetime").datetime.fromisoformat(pair[1]["startTime"][:10])
                )),
            }
        else:
            report["pair_found"] = False
    except Exception as e:
        report["error"] = str(e)
    return jsonify(report)


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


@app.route("/velez/pipeline_history", methods=["GET"])
def velez_pipeline_history():
    """Last N days of satellite pipeline run history from Supabase pipeline_runs table."""
    days = min(int(request.args.get("days", 14)), 90)
    try:
        from velez_supabase import query_pipeline_runs
        runs = query_pipeline_runs(days=days)
        return jsonify({"ok": True, "days": days, "count": len(runs), "runs": runs}), 200
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


# /velez/log-intervencion eliminado en Panel Roger v3 — pipeline 100% automático


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
    # Enrich satellite.last with Supabase pipeline_runs when in-memory state is empty
    satellite_last = _last_satellite
    if not satellite_last:
        try:
            from velez_supabase import query_pipeline_runs
            runs = query_pipeline_runs(days=30)
            if runs:
                r = runs[0]
                satellite_last = {
                    "ok":           r.get("accepted"),
                    "fecha_imagen": r.get("fecha_imagen"),
                    "ndvi_median":  r.get("ndvi_median"),
                    "ran_at":       r.get("timestamp_utc"),
                    "source":       "supabase_pipeline_runs",
                }
        except Exception:
            pass

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
        "satellite": {
            "running": _satellite_running,
            "last": satellite_last,
        },
    })


# ── Panel Roger — canonical endpoints ────────────────────────────────────────

@app.route("/velez/panel-roger-canonical", methods=["GET"])
def velez_panel_roger_canonical():
    """Single endpoint for #roger panel: calls assemble_report() and returns JSON."""
    try:
        if _VELEZ_PATH not in sys.path:
            sys.path.insert(0, _VELEZ_PATH)
        from faro_assembler import assemble_report
        data = assemble_report("amalfitani")
        resp = jsonify(data)
        resp.headers["Access-Control-Allow-Origin"] = "*"
        resp.headers["Cache-Control"] = "no-store"
        return resp
    except Exception as e:
        log.warning("/velez/panel-roger-canonical error: %s", e)
        return jsonify({"error": str(e), "_assembled_at": None}), 500


def _sar_grid_3x3(vd: dict) -> dict:
    """
    Build a 3×3 SAR sector grid for Amalfitani from field-level assembled data.

    Rows N→S: NORTE (GK+penalty N), CENTRO (midfield), SUR (GK+penalty S).
    Cols L→R: IZQ, CEN, DER.

    VV delta reflects FIFA wear research: GK zones compact 1–1.5 dB more than midfield.
    Theta_soil is inverse: compacted zones retain ~3% less moisture.
    """
    wl  = vd.get("weather_live", {})
    est = vd.get("sectores", {}).get("estadio", {})

    vv_base     = float(wl.get("sar_vv_db")        or -8.5)
    vh_base     = float(wl.get("sar_vh_db")        or -15.0)
    theta_base  = float(wl.get("humedad_suelo_pct") or 20.0) / 100.0
    insar_base  = float(est.get("insar_mm")         or 0.0)
    deficit     = float(wl.get("deficit_hidrico_mm") or 5.0)
    et0         = float(wl.get("et0_mm_dia")         or 2.5)

    gndvi_c    = wl.get("gndvi_por_cancha", {}).get("canchas", {})
    amalf_gndvi = float((gndvi_c.get("amalfitani") or {}).get("gndvi") or 0.42)
    n_base = 50.0 if amalf_gndvi < 0.30 else 30.0 if amalf_gndvi < 0.38 else 15.0 if amalf_gndvi < 0.45 else 0.0

    # Sector modifiers — row × col (3×3)
    _VV_DELTA    = [[+0.8, +1.2, +0.8], [+0.0, +0.3, +0.0], [+0.8, +1.2, +0.8]]
    _TH_DELTA    = [[-0.03,-0.04,-0.03], [+0.01,+0.00,+0.01], [-0.03,-0.04,-0.03]]
    _RIEGO_F     = [[1.2, 1.3, 1.2],    [0.9,  1.0,  0.9],   [1.2, 1.3, 1.2]]
    _N_F         = [[1.3, 1.4, 1.3],    [1.0,  1.1,  1.0],   [1.3, 1.4, 1.3]]
    _INSAR_DELTA = [[+0.3,+0.5,+0.3],   [+0.0,+0.1,+0.0],   [+0.3,+0.5,+0.3]]
    _ROW_NAMES   = ["NORTE", "CENTRO", "SUR"]
    _COL_NAMES   = ["IZQ",   "CEN",    "DER"]

    sectores = []
    for r in range(3):
        for c in range(3):
            vv       = round(vv_base + _VV_DELTA[r][c], 2)
            theta    = round(max(0.05, min(0.45, theta_base + _TH_DELTA[r][c])), 3)
            insar    = round(insar_base + _INSAR_DELTA[r][c], 2)
            def_mm   = round(deficit * _RIEGO_F[r][c], 1)
            riego_mm = round(max(0, def_mm * 1.1 + et0), 1)
            n_kg     = int(round(n_base * _N_F[r][c]))
            compact_alert = vv > (vv_base + 0.6)
            riego_alert   = def_mm > 8
            urgencia = ("CRÍTICA" if compact_alert and riego_alert else
                        "ALTA"    if compact_alert or  riego_alert else "ESTABLE")
            accion   = ("Aireación + riego urgente" if compact_alert and riego_alert else
                        "Aireación en 5-7 días"     if compact_alert else
                        "Riego prioritario hoy"     if riego_alert   else
                        "Monitoreo rutinario")
            sectores.append({
                "id":                     f"{r+1},{c+1}",
                "fila":                   _ROW_NAMES[r],
                "columna":                _COL_NAMES[c],
                "sar_vv_db":              vv,
                "sar_vh_db":              round(vh_base - (vv - vv_base) * 0.8, 2),
                "theta_soil":             theta,
                "humedad_suelo_pct":      round(theta * 100, 1),
                "insar_mm":               insar,
                "deficit_riego_mm":       def_mm,
                "recomendacion_riego_mm": riego_mm,
                "nitrogen_kg_ha":         n_kg,
                "accion_proxima":         accion,
                "accion_urgencia":        urgencia,
            })

    return {
        "format":          "3x3",
        "campo":           "amalfitani",
        "fecha":           (vd.get("_assembled_at") or "")[:10],
        "baseline_vv_db":  vv_base,
        "baseline_theta":  round(theta_base * 100, 1),
        "baseline_insar":  insar_base,
        "gndvi":           amalf_gndvi,
        "sectores":        sectores,
    }


def _sar_timeseries() -> dict:
    """
    Time series of SAR VV/VH + theta_soil from soil_metrics (last 180 days).
    Also computes sar_vv_change_6d (last - prev pass) and vh_vv_ratio_last.
    Physical range for C-band VV over grass/soil: -4 to -30 dB.
    """
    try:
        if _VELEZ_PATH not in sys.path:
            import sys as _sys; _sys.path.insert(0, _VELEZ_PATH)
        import velez_supabase as _vs
        rows = _vs.get_soil_metrics_latest("amalfitani", dias=180)
        seen: dict[str, tuple] = {}
        for row in rows:
            fecha = (row.get("fecha_imagen") or "")[:10]
            if not fecha or row.get("sar_vv_db") is None:
                continue
            vv = float(row["sar_vv_db"])
            if vv > -4.0 or vv < -30.0:
                continue
            theta = row.get("theta_soil")
            vh    = row.get("sar_vh_db")
            if fecha not in seen:
                seen[fecha] = (vv, theta, vh)
        sorted_items = sorted(seen.items())
        dates   = [d for d, _ in sorted_items]
        vv_s    = [round(v, 2)  for _, (v, _, _) in sorted_items]
        theta_s = [round(float(t) * 100, 1) if t is not None else None
                   for _, (_, t, _) in sorted_items]
        vh_s    = [round(float(h), 2) if h is not None else None
                   for _, (_, _, h) in sorted_items]
        # sar_vv_change_6d: difference between last two valid VV passes (≈6-day revisit)
        sar_vv_change_6d = None
        if len(vv_s) >= 2:
            sar_vv_change_6d = round(vv_s[-1] - vv_s[-2], 2)
        # vh_vv_ratio_last: VH − VV in dB (negative = typical; less negative = wetter/denser)
        vh_vv_ratio_last = None
        if vh_s and vh_s[-1] is not None and vv_s:
            vh_vv_ratio_last = round(vh_s[-1] - vv_s[-1], 2)
        # vh_vv_change_1d: change in VH/VV ratio between last two passes
        vh_vv_change_1d = None
        if len(vh_s) >= 2 and vh_s[-1] is not None and vh_s[-2] is not None:
            prev_ratio = vh_s[-2] - vv_s[-2]
            curr_ratio = vh_s[-1] - vv_s[-1]
            vh_vv_change_1d = round(curr_ratio - prev_ratio, 2)
        return {
            "timeseries":         True,
            "campo":              "amalfitani",
            "n_puntos":           len(dates),
            "sar_dates":          dates,
            "sar_vv_series":      vv_s,
            "sar_vh_series":      vh_s,
            "theta_soil_series":  theta_s,
            "sar_vv_change_6d":   sar_vv_change_6d,
            "vh_vv_ratio_last":   vh_vv_ratio_last,
            "vh_vv_change_1d":    vh_vv_change_1d,
        }
    except Exception as exc:
        log.warning("_sar_timeseries: %s", exc)
        return {"error": str(exc), "sar_dates": [], "sar_vv_series": [], "theta_soil_series": []}


@app.route("/velez/panel-data", methods=["GET"])
def velez_panel_data():
    """
    Panel data endpoint — CORS-open, no-cache.

    Query params:
      ?format=3x3       → 3×3 SAR sector grid for Amalfitani (9 sectors)
      ?timeseries=true  → SAR VV time series from soil_metrics (last 30 days)
      (none)            → full assembled report (original behavior)
    """
    try:
        fmt = request.args.get("format", "")
        ts  = request.args.get("timeseries", "").lower() == "true"

        if ts:
            payload = _sar_timeseries()
            resp = jsonify(payload)
            resp.headers["Access-Control-Allow-Origin"] = "*"
            resp.headers["Cache-Control"] = "no-store"
            return resp

        if _VELEZ_PATH not in sys.path:
            sys.path.insert(0, _VELEZ_PATH)
        from faro_assembler import assemble_report
        vd = assemble_report("amalfitani")

        if fmt == "3x3":
            payload = _sar_grid_3x3(vd)
            resp = jsonify(payload)
            resp.headers["Access-Control-Allow-Origin"] = "*"
            resp.headers["Cache-Control"] = "no-store"
            return resp

        resp = jsonify(vd)
        resp.headers["Access-Control-Allow-Origin"] = "*"
        resp.headers["Cache-Control"] = "no-store"
        return resp
    except Exception as e:
        log.warning("/velez/panel-data error: %s", e)
        return jsonify({"error": str(e)}), 500


@app.route("/velez/panel-roger", methods=["GET"])
def velez_panel_roger():
    """
    Endpoint optimizado para el panel Roger — solo datos relevantes para el canchero.
    Retorna: roger_canchas (Amalfitani + 12 VO unificadas), weather_live, hermes,
             tareas_semana, heatmaps_meta, qa_alerts recientes.
    Más liviano que /panel-roger-canonical — solo los campos que necesita el panel.
    """
    try:
        if _VELEZ_PATH not in sys.path:
            sys.path.insert(0, _VELEZ_PATH)
        from faro_assembler import assemble_report
        vd = assemble_report("amalfitani")

        roger = vd.get("usuarios", {}).get("roger", {})

        # QA alerts recientes de Supabase (últimas 48h)
        qa_alerts = []
        try:
            import velez_supabase as _vs
            from datetime import datetime as _dt, timezone as _tz, timedelta as _td
            import requests as _rq
            _since = (_dt.now(_tz.utc) - _td(hours=48)).isoformat()
            _url = (f"{_vs._base()}/rest/v1/qa_alerts"
                    f"?created_at=gte.{_since}&order=created_at.desc&limit=20")
            _r = _rq.get(_url, headers=_vs._hdrs(), timeout=6)
            if _r.status_code == 200:
                qa_alerts = _r.json()
        except Exception as _qe:
            log.debug("panel-roger: qa_alerts (non-fatal): %s", _qe)

        payload = {
            # Vista unificada Amalfitani + 12 canchas VO con todos los campos científicos
            "roger_canchas":  vd.get("roger_canchas", []),
            # Clima
            "weather_live":   vd.get("weather_live", {}),
            # Hermes — consolidado venue + por cancha
            "hermes":         vd.get("hermes", {}),
            # Tareas semanales de Roger
            "tareas_semana":  roger.get("tareas_semana", []),
            # Metadatos de los heatmaps
            "heatmaps_meta":  roger.get("heatmaps_meta", {}),
            "heatmaps":       roger.get("heatmaps", {}),
            # Alertas QA del watchdog
            "qa_alerts":      qa_alerts,
            # Meta
            "_assembled_at":  vd.get("_assembled_at"),
            "meta":           vd.get("meta", {}),
        }
        resp = jsonify(payload)
        resp.headers["Access-Control-Allow-Origin"] = "*"
        resp.headers["Cache-Control"] = "no-store"
        return resp
    except Exception as e:
        log.warning("/velez/panel-roger error: %s", e)
        return jsonify({"error": str(e), "_assembled_at": None}), 500


@app.route("/velez/prescriptions", methods=["GET"])
def velez_prescriptions():
    """
    Surgical prescription map — 6 zones per cancha with urgency-ranked actions.
    Uses IPOS traffic load + weather_live (ET₀, soil, fungal risk, GDD) to compute
    per-zone interventions: AEREAR / REGAR / FUNGICIDA / FERTILIZAR / DRENAR.
    No PIN required — read-only computed output.
    """
    try:
        if _VELEZ_PATH not in sys.path:
            sys.path.insert(0, _VELEZ_PATH)
        from faro_assembler import assemble_report
        import faro_prescription as fp

        vd = assemble_report("amalfitani")
        roger_canchas = vd.get("roger_canchas", [])
        weather_live  = vd.get("weather_live", {})

        # Fallback: build minimal cancha list from older sectores format
        if not roger_canchas:
            estadio = vd.get("sectores", {}).get("estadio")
            vo_list = vd.get("sectores", {}).get("canchero", {}).get("canchas", [])
            if estadio:
                roger_canchas = [{"id": "amalfitani", **estadio}] + vo_list
            else:
                roger_canchas = vo_list

        # Inject heatmap_archivo from roger heatmaps if missing
        hm_dict = vd.get("usuarios", {}).get("roger", {}).get("heatmaps", {})
        for c in roger_canchas:
            cid = c.get("id") or c.get("cancha_id", "")
            if cid and not c.get("heatmap_archivo"):
                c["heatmap_archivo"] = hm_dict.get(cid, {}).get("heatmap_archivo")

        # Inject insar_mm from sectores.estadio into weather_live
        est = vd.get("sectores", {}).get("estadio", {})
        if est.get("insar_mm") is not None and weather_live.get("insar_mm") is None:
            weather_live["insar_mm"] = est["insar_mm"]

        # Inject SAR fields from soil_metrics timeseries
        try:
            _ts = _sar_timeseries()
            _vv_list = _ts.get("sar_vv_series", [])
            _dt_list = _ts.get("sar_dates", [])
            _valid = [(dt, vv) for dt, vv in zip(_dt_list, _vv_list) if vv is not None]
            if _valid:
                _last_dt, _last_vv = _valid[-1]
                if weather_live.get("sar_vv_db") is None:
                    weather_live["sar_vv_db"] = _last_vv
                    weather_live["sar_fecha"]  = _last_dt
                    log.info("prescriptions: sar_vv_db=%.2f fecha=%s", _last_vv, _last_dt)
            # New v3 fields — computed from timeseries
            if _ts.get("sar_vv_change_6d") is not None:
                weather_live.setdefault("sar_vv_change_6d", _ts["sar_vv_change_6d"])
            if _ts.get("vh_vv_ratio_last") is not None:
                weather_live.setdefault("vh_vv_ratio", _ts["vh_vv_ratio_last"])
            if _ts.get("vh_vv_change_1d") is not None:
                weather_live.setdefault("vh_vv_change_1d", _ts["vh_vv_change_1d"])
            # Honest relative humidity state (SAR change + ERA5 reference)
            _vv_now  = weather_live.get("sar_vv_db")
            _chg     = _ts.get("sar_vv_change_6d")
            _vv_prev = (round(_vv_now - _chg, 2)
                        if _vv_now is not None and _chg is not None else None)
            try:
                from sports.clients.velez.faro_humedad_relativa import get_humidity_relative as _ghr
            except ImportError:
                from faro_humedad_relativa import get_humidity_relative as _ghr  # type: ignore
            weather_live.setdefault("sar_humedad_relativa", _ghr(
                sar_vv_today=_vv_now,
                sar_vv_6d_ago=_vv_prev,
                era5_sm_7_28cm=weather_live.get("era5_sm_7_28cm"),
                era5_sm_0_7cm=weather_live.get("era5_sm_0_7cm"),
            ))
        except Exception as _e:
            log.debug("prescriptions sar inject: %s", _e)

        payload = fp.generate_prescriptions(roger_canchas, weather_live)
        resp = jsonify(payload)
        resp.headers["Access-Control-Allow-Origin"] = "*"
        resp.headers["Cache-Control"] = "no-store"
        return resp
    except Exception as e:
        log.warning("/velez/prescriptions error: %s", e)
        return jsonify({"error": str(e)}), 500


@app.route("/velez/amalfitani-geojson", methods=["GET"])
def velez_amalfitani_geojson():
    """GeoJSON FeatureCollection — ALL system canchas with correct coordinates.

    Amalfitani main field (id='amalfitani'):
      Center derived from tiles_blueprint._BBOXES["amalfitani"] — the authoritative
      bbox already used by the satellite pipeline for Sentinel-2 and OPERA RTC-S1.
      Generates a 2.5×2.5 m quadrant grid (42 cols × 27 rows = 1 134 polygons).

    Villa Olímpica (1fa–10fa, 1fp, 2fp) and Polideportivo (poli_*):
      Centers from the system coordinate registry (_SYSTEM_COORDS below).
      Each rendered as a single ~90×60 m (training) or ~105×68 m (professional)
      rectangular polygon.

    Properties on every feature come from faro_assembler (live assembler data).
    quad_id / quad_row / quad_col added on Amalfitani quads for future per-quad
    enrichment once Supabase has quadrant-level satellite rows.
    """
    import math

    _D2R = math.pi / 180.0

    # ── Amalfitani center from tiles_blueprint authoritative bbox ─────────
    # tiles_blueprint._BBOXES["amalfitani"] = [-58.5305, -34.6391, -58.5271, -34.6367]
    # This bbox is used by the satellite ingest pipeline — it IS the system truth.
    try:
        from tiles_blueprint import _BBOXES as _TB
        _ab = _TB.get("amalfitani", (-58.5305, -34.6391, -58.5271, -34.6367))
    except ImportError:
        _ab = (-58.5305, -34.6391, -58.5271, -34.6367)

    AMALF_LNG = (_ab[0] + _ab[2]) / 2.0   # -58.5288
    AMALF_LAT = (_ab[1] + _ab[3]) / 2.0   # -34.6379

    _M_LAT = lambda lat: 111_132.0 * (1.0 - 0.00335 * math.sin(2.0 * lat * _D2R) ** 2)
    _M_LNG = lambda lat: 111_320.0 * math.cos(lat * _D2R)

    A_MLAT = _M_LAT(AMALF_LAT)
    A_MLNG = _M_LNG(AMALF_LAT)

    FIELD_W, FIELD_H, QUAD_M = 105.0, 68.0, 2.5
    COLS = int(FIELD_W / QUAD_M)   # 42
    ROWS = int(FIELD_H / QUAD_M)   # 27
    step_lng   = QUAD_M / A_MLNG
    step_lat   = QUAD_M / A_MLAT
    lng_origin = AMALF_LNG - (FIELD_W / 2.0) / A_MLNG   # west edge
    lat_origin = AMALF_LAT + (FIELD_H / 2.0) / A_MLAT   # north edge

    # ── System coordinate registry ────────────────────────────────────────
    # Source: Club Atlético Vélez Sarsfield numeración de canchas 2024 +
    # OSM relation 2567701.  (lng, lat, half_w_m, half_h_m)
    _VO_MLNG = _M_LNG(-34.620)
    _VO_MLAT = _M_LAT(-34.620)
    _PO_MLNG = _M_LNG(-34.633)
    _PO_MLAT = _M_LAT(-34.633)

    def _hl(m, mlng): return m / mlng
    def _hh(m, mlat): return m / mlat

    _SYSTEM_COORDS = {
        # Villa Olímpica — training fields ~90×60 m
        "1fa":         (-58.7208, -34.6195, _hl(45, _VO_MLNG), _hh(30, _VO_MLAT)),
        "2fa":         (-58.7205, -34.6205, _hl(45, _VO_MLNG), _hh(30, _VO_MLAT)),
        "3fa":         (-58.7196, -34.6232, _hl(45, _VO_MLNG), _hh(30, _VO_MLAT)),
        "4fa":         (-58.7196, -34.6225, _hl(45, _VO_MLNG), _hh(30, _VO_MLAT)),
        "5fa":         (-58.7193, -34.6242, _hl(45, _VO_MLNG), _hh(30, _VO_MLAT)),
        "6fa":         (-58.7192, -34.6216, _hl(45, _VO_MLNG), _hh(30, _VO_MLAT)),
        "7fa":         (-58.7188, -34.6208, _hl(45, _VO_MLNG), _hh(30, _VO_MLAT)),
        "8fa":         (-58.7183, -34.6195, _hl(45, _VO_MLNG), _hh(30, _VO_MLAT)),
        "9fa":         (-58.7186, -34.6210, _hl(45, _VO_MLNG), _hh(30, _VO_MLAT)),
        "10fa":        (-58.7178, -34.6187, _hl(45, _VO_MLNG), _hh(30, _VO_MLAT)),
        # Villa Olímpica — professional training ~105×68 m
        "1fp":         (-58.7208, -34.6255, _hl(52, _VO_MLNG), _hh(34, _VO_MLAT)),
        "2fp":         (-58.7200, -34.6255, _hl(52, _VO_MLNG), _hh(34, _VO_MLAT)),
        # Polideportivo Feijóo
        "poli_f11":    (-58.5152, -34.6345, _hl(52, _PO_MLNG), _hh(34, _PO_MLAT)),
        "poli_f8a":    (-58.5143, -34.6325, _hl(40, _PO_MLNG), _hh(25, _PO_MLAT)),
        "poli_f8b":    (-58.5118, -34.6338, _hl(40, _PO_MLNG), _hh(25, _PO_MLAT)),
        "poli_hockey": (-58.5122, -34.6320, _hl(45, _PO_MLNG), _hh(27, _PO_MLAT)),
        "poli_tenis1": (-58.5130, -34.6330, _hl(12, _PO_MLNG), _hh(6,  _PO_MLAT)),
        "poli_tenis2": (-58.5125, -34.6328, _hl(12, _PO_MLNG), _hh(6,  _PO_MLAT)),
        "poli_basquet":(-58.5138, -34.6334, _hl(14, _PO_MLNG), _hh(8,  _PO_MLAT)),
    }

    _PROP_KEYS = (
        "id", "nombre", "score", "sem", "ndvi", "gndvi", "bsi", "ndwi",
        "entropia_h", "angulo_alpha", "compactacion_index_ml",
        "temp_superficie_c", "theta_5cm", "theta_10cm", "theta_20cm", "theta_smap",
        "ndvi_2_5m", "n_status", "n_rec", "detalle",
    )

    # ── Assembler data ────────────────────────────────────────────────────
    try:
        if _VELEZ_PATH not in sys.path:
            sys.path.insert(0, _VELEZ_PATH)
        from faro_assembler import assemble_report
        data = assemble_report("amalfitani")
    except Exception as e:
        log.warning("/velez/amalfitani-geojson assembler error: %s", e)
        data = {}

    canchas = (data.get("sectores") or {}).get("canchero", {}).get("canchas", [])
    cancha_idx = {c.get("id"): c for c in canchas if c.get("id")}
    amalf      = cancha_idx.get("amalfitani", {})

    features = []

    # ── 1. Amalfitani 2.5×2.5 m quadrant grid ────────────────────────────
    base = {k: amalf.get(k) for k in _PROP_KEYS}
    for row in range(ROWS):
        lat_n = lat_origin - row * step_lat
        lat_s = lat_n - step_lat
        for col in range(COLS):
            lng_w = lng_origin + col * step_lng
            lng_e = lng_w + step_lng
            props = dict(base)
            props["quad_id"]  = f"r{row:02d}_c{col:02d}"
            props["quad_row"] = row
            props["quad_col"] = col
            features.append({
                "type": "Feature",
                "geometry": {"type": "Polygon", "coordinates": [[
                    [lng_w, lat_n], [lng_e, lat_n],
                    [lng_e, lat_s], [lng_w, lat_s],
                    [lng_w, lat_n],
                ]]},
                "properties": props,
            })

    # ── 2. All other system canchas — one polygon each ────────────────────
    for cid, (lng0, lat0, hl, hh) in _SYSTEM_COORDS.items():
        c     = cancha_idx.get(cid, {"id": cid})
        props = {k: c.get(k) for k in _PROP_KEYS}
        features.append({
            "type": "Feature",
            "geometry": {"type": "Polygon", "coordinates": [[
                [lng0 - hl, lat0 + hh], [lng0 + hl, lat0 + hh],
                [lng0 + hl, lat0 - hh], [lng0 - hl, lat0 - hh],
                [lng0 - hl, lat0 + hh],
            ]]},
            "properties": props,
        })

    resp = jsonify({"type": "FeatureCollection", "features": features})
    resp.headers["Access-Control-Allow-Origin"] = "*"
    resp.headers["Cache-Control"] = "no-store"
    return resp


@app.route("/velez/villa-olimpica-geojson", methods=["GET"])
def velez_villa_olimpica_geojson():
    """Alias — redirects to /velez/amalfitani-geojson which now serves all venues."""
    from flask import redirect
    return redirect("/velez/amalfitani-geojson", code=302)


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


def _daily_qa_check():
    """Cron 09:05 UTC — QA post-ciclo: verifica las 4 tablas del data lake."""
    log.info("=== Cron: QA watchdog starting ===")
    try:
        from faro_qa_watchdog import run_qa_checks
        result = run_qa_checks(venue_id="amalfitani")
        if result.get("ok"):
            log.info("=== Cron: QA OK — todos los checks pasaron ===")
        else:
            log.warning(
                "=== Cron: QA WARN/FAIL — alertas: %s ===",
                result.get("alerts", []),
            )
    except Exception as exc:
        log.error("=== Cron: QA watchdog FAILED: %s ===", exc)


def _daily_enrichment():
    """
    Cron 09:15 UTC — Stack científico completo post-ciclo.
    Corre después del refresh (09:00) y QA watchdog (09:05).
    Cada módulo tiene fallback silencioso; ninguno puede romper el pipeline.
    """
    log.info("=== Cron: enrichment cycle starting ===")
    _here = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         "sports", "clients", "velez")
    import sys as _sys
    if _here not in _sys.path:
        _sys.path.insert(0, _here)

    modules = [
        ("faro_sar_polimetria",  "run_polimetria",        {"venue_id": "amalfitani"}),
        ("faro_ecostress",       "run_ecostress_cycle",   {"venue_id": "amalfitani"}),
        ("faro_landsat_thermal", "run_landsat_cycle",     {"venue_id": "amalfitani"}),
        ("faro_clms_lst",        "run_clms_lst_cycle",    {"venue_id": "amalfitani"}),  # NUEVO: CLMS LST 3km
        ("faro_saocom",          "run_saocom_cycle",      {"venue_id": "amalfitani"}),  # NUEVO: SAOCOM L-band
        ("faro_richards_profile","run_richards_profile",  {"venue_id": "amalfitani"}),
        ("faro_superres",        "run_superres_cycle",    {"venue_id": "amalfitani"}),
        ("faro_compactacion_ml", "run_compactacion_cycle",{"venue_id": "amalfitani"}),
    ]
    results = {}
    for mod_name, fn_name, kwargs in modules:
        try:
            import importlib
            mod = importlib.import_module(mod_name)
            fn  = getattr(mod, fn_name)
            result = fn(**kwargs)
            results[mod_name] = result
            log.info("enrichment: %s.%s OK — %s", mod_name, fn_name, result)
        except Exception as exc:
            results[mod_name] = {"error": str(exc)}
            log.warning("enrichment: %s (non-fatal): %s", mod_name, exc)
    log.info("=== Cron: enrichment cycle done — %d módulos ===", len(modules))
    return results


def _daily_sar_cascade():
    """
    Cron 09:30 UTC — Cascade SAR: busca desde la última fecha en DB hacia hoy.
    Fuente primaria CDSE (lag ~6h), fallback PC (lag ~3-6d).
    """
    log.info("=== Cron: SAR cascade starting ===")
    try:
        import sys as _sys
        _velez_dir = os.path.join(_HERE, "sports", "clients", "velez")
        if _velez_dir not in _sys.path:
            _sys.path.insert(0, _velez_dir)
        from faro_sar_s1_backfill import run_s1_backfill
        result = run_s1_backfill(days=30, scene_limit=20, from_latest=True)
        nuevas = result.get("escenas_nuevas", 0)
        log.info("=== Cron: SAR cascade OK — %d escenas nuevas · %s ===",
                 nuevas, result.get("vv_range_db", ""))
    except Exception as exc:
        log.error("=== Cron: SAR cascade FAILED: %s ===", exc)


def _weekly_insar_refresh():
    global _last_insar
    if not os.environ.get("NASA_EARTHDATA_USER") or not os.environ.get("NASA_EARTHDATA_PASS"):
        msg = ("InSAR skipped — set NASA_EARTHDATA_USER + NASA_EARTHDATA_PASS "
               "in Railway env vars to activate Sentinel-1 displacement monitoring")
        log.warning("=== Cron: %s ===", msg)
        _last_insar = {"ok": False, "error": msg, "ran_at": datetime.now(timezone.utc).isoformat()}
        return
    log.info("=== Cron: weekly InSAR refresh starting ===")
    result = data_refresh.run_insar_refresh()
    _last_insar = {**result, "ran_at": datetime.now(timezone.utc).isoformat()}
    if result.get("ok"):
        log.info("=== Cron: weekly InSAR refresh OK ===")
    else:
        log.error("=== Cron: weekly InSAR refresh FAILED: %s ===", result.get("error"))


def _make_jobstores() -> dict:
    """
    Intenta configurar SQLAlchemy jobstore apuntando a Supabase Postgres.
    Jobs persisten en la tabla apscheduler_jobs — sobreviven reinicios Railway.
    Fail-open: retorna {} (in-memory) si SUPABASE_DB_URL no está o falla.
    """
    db_url = os.environ.get("SUPABASE_DB_URL", "")
    if not db_url:
        log.info("APScheduler: SUPABASE_DB_URL no configurada — jobstore in-memory")
        return {}
    # SQLAlchemy 2.x requiere postgresql:// no postgres://
    if db_url.startswith("postgres://"):
        db_url = db_url.replace("postgres://", "postgresql://", 1)
    try:
        from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
        jobstore = SQLAlchemyJobStore(
            url=db_url,
            tablename="apscheduler_jobs",
        )
        # Probe connection — falla rápido si la URL es inválida
        jobstore.get_all_jobs()
        log.info("APScheduler: SQLAlchemy jobstore OK (Supabase Postgres)")
        return {"default": jobstore}
    except Exception as exc:
        log.warning("APScheduler: jobstore Supabase falló — in-memory: %s", exc)
        return {}


def _run_startup_migrations():
    """
    Aplica migrations/fix_data_lake_schema.sql al inicio del proceso Railway.
    Requiere SUPABASE_DB_URL (postgres connection string). No bloquea si falla.
    """
    db_url = os.environ.get("SUPABASE_DB_URL", "")
    if not db_url:
        log.warning(
            "SUPABASE_DB_URL no configurada — data lake schema NO se migró automáticamente. "
            "Ejecutar migrations/fix_data_lake_schema.sql en Supabase SQL Editor manualmente."
        )
        return
    if db_url.startswith("postgres://"):
        db_url = db_url.replace("postgres://", "postgresql://", 1)
    migration_path = os.path.join(_HERE, "migrations", "fix_data_lake_schema.sql")
    migration_june2026 = os.path.join(_HERE, "migrations", "add_june2026_columns.sql")
    migration_fecha_imagen = os.path.join(_HERE, "migrations", "add_fecha_imagen.sql")
    try:
        from sqlalchemy import create_engine, text
        engine = create_engine(db_url, connect_args={"connect_timeout": 10})
        for mpath in [migration_path, migration_june2026, migration_fecha_imagen]:
            if not os.path.exists(mpath):
                continue
            sql = open(mpath, encoding="utf-8").read()
            stmts = [s.strip() for s in sql.split(";") if s.strip() and not s.strip().startswith("--")]
            with engine.begin() as conn:
                for stmt in stmts:
                    try:
                        conn.execute(text(stmt))
                    except Exception as _se:
                        log.debug("migration stmt skip (non-fatal): %s", _se)
        log.info("startup migrations: OK — data lake schema actualizado (incluyendo jun2026)")
    except Exception as exc:
        log.warning("startup migrations (non-fatal): %s — ejecutar fix_data_lake_schema.sql manualmente", exc)


def _start_scheduler():
    jobstores  = _make_jobstores()
    persistent = bool(jobstores)
    scheduler  = BackgroundScheduler(
        jobstores=jobstores,
        timezone="UTC",
        job_defaults={
            "coalesce":            True,   # ejecuta solo una vez si acumula disparos
            "max_instances":       1,
            "misfire_grace_time":  3600,
        },
    )
    # 06:00 ART = 09:00 UTC
    scheduler.add_job(
        _daily_refresh,
        CronTrigger(hour=9, minute=0, timezone="UTC"),
        id="daily_weather_refresh",
        replace_existing=True,
    )
    # 06:05 ART = 09:05 UTC — QA post-ciclo: verifica las 4 tablas del data lake
    scheduler.add_job(
        _daily_qa_check,
        CronTrigger(hour=9, minute=5, timezone="UTC"),
        id="daily_qa_watchdog",
        replace_existing=True,
    )
    # 06:15 ART = 09:15 UTC — enrichment científico post-ciclo
    scheduler.add_job(
        _daily_enrichment,
        CronTrigger(hour=9, minute=15, timezone="UTC"),
        id="daily_scientific_enrichment",
        replace_existing=True,
    )
    # 06:30 ART = 09:30 UTC — cascade SAR Sentinel-1 (CDSE primario → PC fallback)
    scheduler.add_job(
        _daily_sar_cascade,
        CronTrigger(hour=9, minute=30, timezone="UTC"),
        id="daily_sar_cascade",
        replace_existing=True,
    )
    # Lunes 10:00 UTC = 07:00 ART
    scheduler.add_job(
        _weekly_insar_refresh,
        CronTrigger(day_of_week="mon", hour=10, minute=0, timezone="UTC"),
        id="weekly_insar_refresh",
        replace_existing=True,
    )
    scheduler.start()
    log.info(
        "APScheduler started — jobstore=%s · daily 09:00 UTC · InSAR lunes 10:00 UTC",
        "supabase_postgres" if persistent else "in-memory",
    )
    return scheduler


_run_startup_migrations()

try:
    _scheduler = _start_scheduler()
    velez_scheduler.register_jobs(_scheduler)
    # Vélez — monitor autónomo cada 6h desde sports/modules
    try:
        import sys as _vsys, os as _vos
        _sp = _vos.path.join(_vos.path.dirname(__file__), 'sports', 'modules')
        if _sp not in _vsys.path: _vsys.path.insert(0, _vos.path.dirname(__file__))
        from sports.modules.faro_monitor import run_velez_monitor
        def _velez_monitor_job():
            try:
                result = run_velez_monitor(venue_id="amalfitani")
                log.info("Vélez monitor: %d alertas", len(result.get("alerts", [])))
            except Exception as _vme:
                log.debug("Vélez monitor job (non-fatal): %s", _vme)
        _scheduler.add_job(_velez_monitor_job, "cron", hour="6,12,18,0", minute=45,
                           id="velez_monitor", replace_existing=True)
        log.info("Vélez monitor autónomo registrado (cada 6h)")
    except Exception as _vm_err:
        log.warning("Vélez monitor scheduler: %s", _vm_err)
    # Vélez — source scout semanal (auto-descubre nuevas fuentes satelitales)
    try:
        from sports.modules.faro_source_scout import run_source_scout
        def _velez_scout_job():
            try:
                result = run_source_scout()
                log.info("source scout: %d fuentes evaluadas", len(result.get("sources", [])))
            except Exception as _vse:
                log.debug("source scout job (non-fatal): %s", _vse)
        _scheduler.add_job(_velez_scout_job, "cron", day_of_week="sun", hour=8, minute=0,
                           id="velez_source_scout", replace_existing=True)
        log.info("Vélez source scout registrado (domingos 08:00 UTC)")
    except Exception as _vs_err:
        log.warning("Vélez source scout scheduler: %s", _vs_err)
    # Dale Play — check fuentes nuevas cada 3 días
    try:
        import sys as _sys, pathlib as _pathlib
        _sys.path.insert(0, str(_pathlib.Path(__file__).parent / "events" / "clients" / "dale-play"))
        from check_new_sources import register_scheduler as _dp_register
        _dp_register(_scheduler)
        log.info("Dale Play check_new_sources registrado en APScheduler (cada 3 días)")
    except Exception as _dp_sched_err:
        log.warning("Dale Play check_new_sources scheduler: %s", _dp_sched_err)
    # Dale Play — scheduler post-show (auto-actualización cada 6h)
    try:
        from dale_play_scheduler import init_scheduler as _dp_sched_init, schedule_source_scout as _dp_scout
        _dp_sched_init(_scheduler)
        _dp_scout(_scheduler)   # source scout semanal: lunes 11:00 UTC
        log.info("Dale Play post-show scheduler + source scout semanal inicializados")
    except Exception as _dp_sched2_err:
        log.warning("Dale Play post-show scheduler: %s", _dp_sched2_err)
    # Dale Play — recuperar monitors activos de Supabase después de restart Railway
    try:
        from dale_play_scheduler import recover_monitors_on_startup as _dp_recover
        threading.Thread(
            target=lambda: _dp_recover(_scheduler), daemon=True, name="dp_monitor_recovery"
        ).start()
        log.info("Dale Play monitor recovery iniciado (background thread)")
    except Exception as _dp_rec_err:
        log.warning("Dale Play monitor recovery: %s", _dp_rec_err)
    # Dale Play — agente de monitoreo (chequeo cada 6h: clima/NDVI/SAR)
    try:
        from dale_play_monitor import register_monitoring_job as _dp_monitor_reg
        _dp_monitor_reg(_scheduler)
    except Exception as _dp_mon_err:
        log.warning("Dale Play monitor scheduler: %s", _dp_mon_err)
    # Dale Play — time series fenológica Amalfitani (actualización mensual)
    try:
        from satellite.field_timeseries import register_timeseries_job as _ft_register
        _ft_register(
            _scheduler,
            venue_id="amalfitani",
            bbox=[-58.5305, -34.6391, -58.5271, -34.6367],
            start_year=2020,
        )
    except Exception as _ft_err:
        log.warning("field_timeseries scheduler: %s", _ft_err)
    # Dale Play — sync local storage → Supabase (archivos generados offline o durante outage)
    try:
        def _dp_sync():
            from dale_play_storage import sync_pending_to_supabase
            result = sync_pending_to_supabase()
            log.info("Dale Play sync_pending_to_supabase: %s", result)
        threading.Thread(target=_dp_sync, daemon=True).start()
    except Exception as _dp_sync_err:
        log.warning("Dale Play sync_pending: %s", _dp_sync_err)
    # Faro Cerebro — monitor autónomo cada hora (JSON + health + PNGs)
    try:
        from agents.faro_cerebro import register_cerebro_job as _cerebro_reg
        _cerebro_reg(_scheduler)
    except Exception as _cerebro_err:
        log.warning("Faro Cerebro scheduler: %s", _cerebro_err)
    # Startup PNG generation — Railway filesystem es stateless; los PNGs se pierden en cada restart.
    # Regenerar en background para que el primer ciclo del cerebro no los encuentre faltantes.
    try:
        def _startup_png_gen():
            import time as _t
            _t.sleep(15)  # esperar a que Flask esté up y scheduler arrancado
            try:
                velez_scheduler.run_refresh_only()
                log.info("Startup PNG generation: OK")
            except Exception as _spe:
                log.warning("Startup PNG generation (non-fatal): %s", _spe)
        threading.Thread(target=_startup_png_gen, daemon=True, name="startup_png_gen").start()
        log.info("Startup PNG generation thread iniciado (15s delay)")
    except Exception as _startup_png_err:
        log.warning("Startup PNG generation thread: %s", _startup_png_err)
    log.info("Schedulers registered: daily weather + weekly reports + dale_play sources + dale_play post-show")
except Exception as _sched_err:
    _scheduler = None
    log.error("Scheduler failed to start (non-fatal): %s", _sched_err)

# ── Infra: middleware + scheduled health snapshots + alert evaluation ──────────
try:
    from faro_infra import init_infra as _init_infra
    _init_infra(app, _scheduler)
except Exception as _infra_init_err:
    log.warning("faro_infra init (non-fatal): %s", _infra_init_err)

# ── Alert system: ERA5 lag, DpRVIc lag, Kalman, SAR — every 30 min ────────────
try:
    from alerts.system import register_alert_scheduler as _reg_alerts
    _reg_alerts(_scheduler)
except Exception as _alert_err:
    log.warning("alerts.system scheduler (non-fatal): %s", _alert_err)

def _route_velez_test_report():
    """POST /velez/test_report — genera PNGs _TEST con datos live y manda mail."""
    import threading
    import velez_test_report as _vtr
    def _bg():
        try:
            result = _vtr.run_test_report()
            log.info("velez_test_report result: %s", result)
        except Exception as _e:
            log.error("velez_test_report bg error: %s", _e)
    threading.Thread(target=_bg, daemon=True, name="velez_test_report").start()
    return jsonify({"status": "started", "msg": "Generando PNGs _TEST y enviando mail — puede tardar ~3min"})


# ── Scheduler routes ──────────────────────────────────────────────────────────
app.add_url_rule("/velez/run_now",        "velez_run_now",        velez_scheduler.route_run_now,        methods=["POST"])
app.add_url_rule("/velez/weekly_status",  "velez_weekly_status",  velez_scheduler.route_weekly_status,  methods=["GET"])
app.add_url_rule("/velez/test_whatsapp",  "velez_test_whatsapp",  velez_scheduler.route_test_whatsapp,  methods=["POST"])
app.add_url_rule("/velez/test_email",          "velez_test_email",          velez_scheduler.route_test_email,          methods=["POST"])
app.add_url_rule("/velez/test-email",         "velez_test_email_dash",     velez_scheduler.route_test_email,          methods=["POST"])
app.add_url_rule("/velez/smtp_diag",          "velez_smtp_diag",           velez_scheduler.route_smtp_diag,           methods=["GET"])
app.add_url_rule("/velez/preview_email",      "velez_preview_email",       velez_scheduler.route_preview_email,       methods=["GET"])
app.add_url_rule("/velez/send_preview_email", "velez_send_preview_email",  velez_scheduler.route_send_preview_email,  methods=["POST"])
app.add_url_rule("/velez/force_reprocess",    "velez_force_reprocess",     velez_scheduler.route_force_reprocess,     methods=["POST"])
app.add_url_rule("/velez/test_report",        "velez_test_report",         _route_velez_test_report,                  methods=["POST"])
app.add_url_rule("/velez/debug_vd",           "velez_debug_vd",            velez_scheduler.route_debug_vd,            methods=["GET"])


@app.route("/velez/check-pngs", methods=["GET"])
def velez_check_pngs():
    """Verifica qué PNGs existen en reportes_velez/. Usado por faro_cerebro."""
    import os as _os
    png_dir = _os.path.join(_os.path.dirname(_os.path.dirname(__file__)), "reportes_velez")
    expected = [
        "faro_reporte_velez.png",
        "faro_reporte_velez_canchero.png",
        "faro_reporte_velez_agro_FINAL.png",
        "faro_reporte_velez_solar_v2.png",
        "faro_reporte_velez_poli.png",
        "faro_reporte_velez_sede.png",
        "faro_reporte_velez_piletas.png",
        "faro_reporte_velez_instituto.png",
    ]
    missing  = [p for p in expected if not _os.path.exists(_os.path.join(png_dir, p))]
    present  = [p for p in expected if _os.path.exists(_os.path.join(png_dir, p))]
    return jsonify({"ok": len(missing) == 0, "present": present, "missing": missing})


@app.route("/scheduler/status", methods=["GET"])
def scheduler_status():
    """Estado del APScheduler: jobstore activo, jobs registrados."""
    if not _scheduler:
        return jsonify({"ok": False, "error": "scheduler no iniciado"}), 503
    try:
        jobs = _scheduler.get_jobs()
        jobstores = list(_scheduler._jobstores.keys())
        persistent = any(
            "SQLAlchemy" in type(js).__name__
            for js in _scheduler._jobstores.values()
        )
        return jsonify({
            "ok":         True,
            "persistent": persistent,
            "jobstore":   "supabase_postgres" if persistent else "in-memory",
            "jobs_n":     len(jobs),
            "jobs": [
                {
                    "id":       j.id,
                    "next_run": j.next_run_time.isoformat() if j.next_run_time else None,
                    "trigger":  str(j.trigger),
                }
                for j in jobs
            ],
        })
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


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


# ── MachinaOS GIS routes (/gis/*) ─────────────────────────────────────────────

@app.route("/gis/health")
def gis_health():
    return jsonify({
        "status":  "ok" if _GIS_OK else "unavailable",
        "service": "machina-gis",
        "areas":   list(_GIS_AREAS.keys()) if _GIS_OK else [],
    })


@app.route("/gis/areas")
def gis_areas():
    if not _GIS_OK:
        return jsonify({"error": "GIS service unavailable"}), 503
    return jsonify({
        name: {"label": a["label"], "bounds": a["bounds"]}
        for name, a in _GIS_AREAS.items()
    })


@app.route("/gis/indices", methods=["POST"])
def gis_indices():
    if not _GIS_OK:
        return jsonify({"error": "GIS service unavailable"}), 503
    body = request.get_json(force=True) or {}
    try:
        return jsonify(_gis_indices(**body))
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/gis/sar-change", methods=["POST"])
def gis_sar_change():
    if not _GIS_OK:
        return jsonify({"error": "GIS service unavailable"}), 503
    body = request.get_json(force=True) or {}
    body.setdefault("guardar_tif", False)
    try:
        return jsonify(_gis_sar_change(**body))
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/gis/report", methods=["POST"])
def gis_report():
    if not _GIS_OK:
        return jsonify({"error": "GIS service unavailable"}), 503
    body = request.get_json(force=True) or {}
    try:
        return jsonify(_gis_report(**body))
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/gis/insar", methods=["POST"])
def gis_insar():
    if not _GIS_OK:
        return jsonify({"error": "GIS service unavailable"}), 503
    body = request.get_json(force=True) or {}
    try:
        return jsonify(_gis_insar(**body))
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/v1/field-analysis/cutting-window")
def cutting_window():
    """Magnus-Tetens + GDD cutting window. Reads live weather from query params or velez_data."""
    try:
        from faro_analytics_physics import compute_faro_cutting_core
    except ImportError as e:
        return jsonify({"error": f"faro_analytics_physics unavailable: {e}"}), 503
    try:
        temp    = float(request.args.get("temp", 18.0))
        rh      = float(request.args.get("rh", 75.0))
        gdd     = float(request.args.get("gdd", 0.0))
        cab     = float(request.args.get("cab", 35.0))
        h_suc   = float(request.args.get("h_suction", 300.0))
        result  = compute_faro_cutting_core(temp, rh, gdd, cab, h_suc)
        return jsonify(result)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/api/v1/field-analysis/hydro")
def hydro_analysis():
    """WCM + Van Genuchten soil moisture from SAR VV/VH inputs."""
    try:
        from faro_analytics_physics import compute_faro_hydro_core
    except ImportError as e:
        return jsonify({"error": f"faro_analytics_physics unavailable: {e}"}), 503
    try:
        sigma_vv  = float(request.args.get("sigma_vv", -12.0))
        sigma_vh  = float(request.args.get("sigma_vh", -18.0))
        lai       = float(request.args.get("lai", 2.5))
        evapo     = float(request.args.get("evapo", 4.0))
        result    = compute_faro_hydro_core(sigma_vv, sigma_vh, lai, evapo)
        return jsonify(result)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/velez/prescripciones-operativas", methods=["GET"])
def velez_prescripciones_operativas():
    """
    Returns auto-generated prescripciones_operativas for all canchas.
    Read-only — generated by faro_temporal_eye + acciones_engine in daily cycle.
    No manual input required.
    """
    try:
        _, vd = data_refresh._gh_get_sha_and_content(data_refresh._VD_PATH)
        heatmaps = vd.get("usuarios", {}).get("roger", {}).get("heatmaps", {})
        prescs = {
            cid: hm.get("prescripcion_operativa")
            for cid, hm in heatmaps.items()
            if hm.get("prescripcion_operativa")
        }
        return jsonify({
            "ok":            True,
            "prescripciones": prescs,
            "n_canchas":     len(prescs),
            "updated_at":    vd.get("updated_at", ""),
            "nota":          "generado automaticamente — sin input manual requerido",
        })
    except Exception as exc:
        log.error("prescripciones-operativas: %s", exc)
        return jsonify({"ok": False, "error": str(exc)}), 500


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
