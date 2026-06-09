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
    return jsonify({"status": "ok", "service": "velez-ipos"})


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
        if med.get("tipo", "").lower() == "clegg":
            commit_url = github_push.push_clegg_medicion(med)
        else:
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
            import satellite_pipeline, base64 as _b64, json as _json
            import requests as _rq

            # Re-use NDVI data already in weather_live.gndvi_por_cancha (avoids fresh
            # Sentinel-2 fetch — pystac/rasterio may not be installed on Railway).
            ndvi_data = None
            token = os.environ.get("GITHUB_TOKEN", "")
            if token:
                r = _rq.get(
                    "https://api.github.com/repos/protocolfaro/faroprotocol"
                    "/contents/velez/velez_data.json",
                    headers={"Authorization": f"Bearer {token}",
                             "Accept": "application/vnd.github+json",
                             "X-GitHub-Api-Version": "2022-11-28"},
                    params={"ref": "main"}, timeout=15,
                )
                if r.status_code == 200:
                    vd = _json.loads(_b64.b64decode(r.json()["content"]).decode())
                    ndvi_data = vd.get("weather_live", {}).get("gndvi_por_cancha")
                    if ndvi_data:
                        log.info("satellite_force: usando NDVI del %s (%d canchas)",
                                 ndvi_data.get("fecha_imagen"), len(ndvi_data.get("canchas", {})))

            result = satellite_pipeline.run_satellite_cycle(ndvi_data, force=True)
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
        "msg":     "Pipeline iniciado con force=True — usando NDVI de weather_live.gndvi_por_cancha.",
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


@app.route("/velez/log-intervencion", methods=["POST"])
def log_intervencion():
    """
    Log a field operation (riego, corte, fertilizante, etc.) from Roger's panel.
    Body: { pin, cancha_id, tipo, detalle?, horas_uso?, foto_url? }
    Stores in Supabase velez_intervenciones. Hermes uses this for contextual validation.
    """
    body = request.get_json(silent=True) or {}
    if not _ok_pin(body.get("pin")):
        return jsonify({"status": "error", "error": "PIN inválido"}), 401

    cancha_id = (body.get("cancha_id") or "").strip()
    tipo      = (body.get("tipo") or "").strip().lower()
    detalle   = (body.get("detalle") or "").strip()
    horas_uso = body.get("horas_uso")
    foto_url  = (body.get("foto_url") or "").strip() or None

    _TIPOS_VALIDOS = {"riego", "corte", "fertilizante", "fungicida", "resiembra", "aireacion"}
    if not cancha_id:
        return jsonify({"status": "error", "error": "cancha_id requerido"}), 400
    if tipo not in _TIPOS_VALIDOS:
        return jsonify({
            "status": "error",
            "error": f"tipo inválido — debe ser uno de: {', '.join(sorted(_TIPOS_VALIDOS))}",
        }), 400

    if horas_uso is not None:
        try:
            horas_uso = float(horas_uso)
        except (TypeError, ValueError):
            return jsonify({"status": "error", "error": "horas_uso debe ser número"}), 400

    try:
        from velez_supabase import insert_intervencion
        ok = insert_intervencion(
            cancha_id=cancha_id,
            tipo=tipo,
            detalle=detalle,
            horas_uso=horas_uso,
            foto_url=foto_url,
        )
        if ok:
            log.info("log-intervencion: %s en %s guardada", tipo, cancha_id)
            return jsonify({"status": "ok", "cancha_id": cancha_id, "tipo": tipo}), 201
        return jsonify({"status": "error", "error": "Supabase no disponible o no configurado"}), 503
    except Exception as exc:
        log.error(traceback.format_exc())
        return jsonify({"status": "error", "error": str(exc)}), 500


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
    try:
        from sqlalchemy import create_engine, text
        engine = create_engine(db_url, connect_args={"connect_timeout": 10})
        sql = open(migration_path, encoding="utf-8").read()
        stmts = [s.strip() for s in sql.split(";") if s.strip() and not s.strip().startswith("--")]
        with engine.begin() as conn:
            for stmt in stmts:
                conn.execute(text(stmt))
        log.info("startup migrations: OK — data lake schema actualizado")
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
    log.info("Schedulers registered: daily weather + weekly reports + dale_play sources + dale_play post-show")
except Exception as _sched_err:
    _scheduler = None
    log.error("Scheduler failed to start (non-fatal): %s", _sched_err)

# ── Scheduler routes ──────────────────────────────────────────────────────────
app.add_url_rule("/velez/run_now",        "velez_run_now",        velez_scheduler.route_run_now,        methods=["POST"])
app.add_url_rule("/velez/weekly_status",  "velez_weekly_status",  velez_scheduler.route_weekly_status,  methods=["GET"])
app.add_url_rule("/velez/test_whatsapp",  "velez_test_whatsapp",  velez_scheduler.route_test_whatsapp,  methods=["POST"])
app.add_url_rule("/velez/test_email",     "velez_test_email",     velez_scheduler.route_test_email,     methods=["POST"])
app.add_url_rule("/velez/smtp_diag",      "velez_smtp_diag",      velez_scheduler.route_smtp_diag,      methods=["GET"])


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


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
