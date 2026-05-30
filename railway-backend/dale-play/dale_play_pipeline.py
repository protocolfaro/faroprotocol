"""
dale_play_pipeline.py — Orquestador de producción del pipeline Dale Play.

Principio: el sistema nunca miente. Errores documentados, timeouts manejados,
módulos críticos con circuit breaker, log inmutable de auditoría.

Modos:
  full         — todos los módulos
  weather_only — solo pronóstico climático
  post_show    — full + InSAR + certificación
"""
from __future__ import annotations
import json, logging, pathlib, time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout
from datetime import datetime, timezone

log = logging.getLogger(__name__)

_SHOWS_DIR  = pathlib.Path(__file__).parent / "shows"
_MODELS_DIR = pathlib.Path(__file__).parent / "models"


# ── Runner con timeout ────────────────────────────────────────────────────────

def _run_with_timeout(module, rider: dict, ctx: dict) -> dict:
    """Ejecuta module.run() con timeout. Retorna error dict si supera el límite."""
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(module.run, rider, **ctx)
        try:
            return future.result(timeout=module.TIMEOUT_S)
        except FuturesTimeout:
            return {
                "error":          f"Timeout {module.TIMEOUT_S}s superado",
                "fuente":         module.DATA_SOURCE,
                "fallback_usado": False,
            }
        except Exception as exc:
            return {
                "error":          str(exc),
                "fuente":         module.DATA_SOURCE,
                "fallback_usado": False,
            }


# ── Pipeline principal ────────────────────────────────────────────────────────

def run_show_audit(show_config: dict, mode: str = "full") -> dict:
    """
    show_config: contenido de dale-play/shows/{show_id}.json
    Retorna show_data dict completo con todos los resultados y metadatos.
    """
    from dale_play_config import VENUE_LAT, VENUE_LON
    from dale_play_modules import MODULES

    show_id   = show_config.get("show_id",   "unknown")
    show_date = show_config.get("show_date", "")
    artist    = show_config.get("artist",    "Artista")
    rider     = show_config.get("rider",     {})

    log.info("=== dale_play_pipeline START — %s · %s · mode=%s ===",
             artist, show_date, mode)

    ts_inicio = time.time()
    result = {
        "show_id":   show_id,
        "show_date": show_date,
        "artist":    artist,
        "venue":     "Estadio José Amalfitani",
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "mode":      mode,
    }

    # Acumuladores para audit_log
    _log_ejecutados: list[dict] = []
    _log_fallidos:   list[dict] = []
    _fuentes:        dict       = {}

    # ── 0. Layout ────────────────────────────────────────────────────────────
    try:
        from dale_play_layout import load_layout
        layout = load_layout(show_id)
        result["layout"] = layout
        if layout:
            log.info("dale_play_pipeline: layout real cargado (%s)", layout.get("filename"))
        else:
            log.info("dale_play_pipeline: sin layout subido — rider JSON como fallback")
    except Exception as e:
        log.warning("dale_play_pipeline: layout load error: %s", e)
        result["layout"] = None

    # ── 1–N. Loop de módulos con circuit breaker ──────────────────────────────
    ctx = dict(
        show_id   = show_id,
        show_date = show_date,
        lat       = VENUE_LAT,
        lon       = VENUE_LON,
        result    = result,
    )

    for module in MODULES:
        if mode not in module.MODES:
            log.info("dale_play_pipeline: %s — skip (mode=%s)", module.RESULT_KEY, mode)
            continue

        t0     = time.time()
        output = _run_with_timeout(module, rider, ctx)
        dur_s  = round(time.time() - t0, 2)

        # Validar output
        errors = module.validate(output)
        for e in errors:
            log.warning("dale_play_pipeline: %s", e)

        # Registrar confianza
        confianza = module.confianza(output)

        if output.get("error"):
            _log_fallidos.append({
                "modulo":    module.RESULT_KEY,
                "error":     output["error"],
                "duracion_s": dur_s,
                "critico":   module.CRITICAL,
            })
            log.warning("dale_play_pipeline: %s FALLÓ (%ss) — %s",
                        module.RESULT_KEY, dur_s, output["error"])

            # Circuit breaker: módulo crítico falla → pipeline para
            if module.CRITICAL:
                result[module.RESULT_KEY] = output
                result["_pipeline_error"] = (
                    f"Módulo crítico '{module.RESULT_KEY}' falló: {output['error']}"
                )
                log.error("dale_play_pipeline: CIRCUIT BREAKER — %s", result["_pipeline_error"])
                _guardar_run_log(show_id, result, _log_ejecutados, _log_fallidos,
                                 _fuentes, ts_inicio, smoke=None)
                return result
        else:
            _log_ejecutados.append({
                "modulo":     module.RESULT_KEY,
                "confianza":  confianza,
                "duracion_s": dur_s,
                "fuente":     output.get("fuente", module.DATA_SOURCE),
            })
            _fuentes[module.RESULT_KEY] = output.get("fuente", module.DATA_SOURCE)
            log.info("dale_play_pipeline: %s OK (%ss) [%s]",
                     module.RESULT_KEY, dur_s, confianza.upper())

        result[module.RESULT_KEY] = output

    # ── Post-satellite: persistir baseline ───────────────────────────────────
    if result.get("satellite") and not result["satellite"].get("error"):
        try:
            from dale_play_storage import save_show_baseline
            sat = result["satellite"]
            save_show_baseline(
                show_id       = show_id,
                ndvi          = sat.get("ndvi"),
                date          = sat.get("ndvi_fecha", show_date),
                satellite_data= sat,
            )
        except Exception as _se:
            log.warning("dale_play_pipeline: storage baseline: %s", _se)

    if mode == "weather_only":
        _guardar_run_log(show_id, result, _log_ejecutados, _log_fallidos,
                         _fuentes, ts_inicio, smoke=None)
        return result

    # ── Comparativa layout ───────────────────────────────────────────────────
    try:
        from dale_play_vision import analyze_comparativa
        result["comparativa"] = analyze_comparativa(show_id=show_id)
        log.info("dale_play_pipeline: comparativa OK — score=%d",
                 result["comparativa"].get("score", {}).get("dale_play", 0))
    except Exception as e:
        log.warning("dale_play_pipeline: comparativa failed: %s", e)
        result["comparativa"] = {"error": str(e)}

    # ── InSAR post-show ──────────────────────────────────────────────────────
    if mode == "post_show":
        try:
            from dale_play_insar import fetch_post_show_vibration
            result["insar"] = fetch_post_show_vibration()
            log.info("dale_play_pipeline: insar OK")
        except Exception as e:
            log.warning("dale_play_pipeline: insar failed: %s", e)
            result["insar"] = {"error": str(e)}

    # ── FII ──────────────────────────────────────────────────────────────────
    try:
        from dale_play_fii import compute_fii
        _egms_j = _MODELS_DIR / "egms_amalfitani.json"
        _dr_j   = _MODELS_DIR / "drainage_amalfitani.json"
        _egms_d = json.loads(_egms_j.read_text(encoding="utf-8")) if _egms_j.exists() else None
        _dr_d   = json.loads(_dr_j.read_text(encoding="utf-8"))   if _dr_j.exists() else None
        sat = result.get("satellite") or {}
        result["fii"] = compute_fii(sat.get("ndvi"), _egms_d, result.get("layout"), _dr_d)
        log.info("dale_play_pipeline: FII=%s", result["fii"].get("fii"))
    except Exception as e:
        log.warning("dale_play_pipeline: fii failed: %s", e)
        result["fii"] = {"error": str(e)}

    # ── Smoke tests ──────────────────────────────────────────────────────────
    smoke_result = None
    try:
        from dale_play_smoke import run_smoke_tests
        smoke_result = run_smoke_tests(result, show_id=show_id)
        if not smoke_result["passed"]:
            log.error("dale_play_pipeline: SMOKE TESTS FALLARON: %s",
                      smoke_result["errors"])
            result["_smoke_errors"] = smoke_result["errors"]
            # Smoke failures bloquean PNG — guardar log y retornar
            _guardar_run_log(show_id, result, _log_ejecutados, _log_fallidos,
                             _fuentes, ts_inicio, smoke=smoke_result)
            result["report_png"]     = None
            result["report_png_url"] = None
            return result
        log.info("dale_play_pipeline: smoke tests OK")
    except Exception as e:
        log.warning("dale_play_pipeline: smoke tests error: %s", e)

    # ── Reporte PNG ──────────────────────────────────────────────────────────
    try:
        from dale_play_report import generate_report
        result["report_png"] = generate_report(result, show_config)
        log.info("dale_play_pipeline: report PNG → %s", result["report_png"])
    except Exception as e:
        log.warning("dale_play_pipeline: report failed: %s", e)
        result["report_png"] = None

    # ── Push PNG a GitHub ────────────────────────────────────────────────────
    result["report_png_url"] = None
    if result.get("report_png"):
        try:
            from dale_play_github import push_png_to_github
            result["report_png_url"] = push_png_to_github(show_id, result["report_png"])
            log.info("dale_play_pipeline: PNG → GitHub %s", result["report_png_url"])
        except Exception as e:
            log.warning("dale_play_pipeline: PNG github push failed: %s", e)

    # ── Histórico GitHub ─────────────────────────────────────────────────────
    try:
        from dale_play_github import push_show_snapshot
        result["github"] = push_show_snapshot(show_id, result)
        log.info("dale_play_pipeline: github %s", result["github"].get("action"))
    except Exception as e:
        log.warning("dale_play_pipeline: github failed: %s", e)
        result["github"] = {"error": str(e)}

    # ── Certificación post-show ──────────────────────────────────────────────
    if mode == "post_show":
        try:
            from dale_play_certification import run_certification
            result["certificado"] = run_certification(show_id, mode="post_show")
            log.info("dale_play_pipeline: certificado → %s",
                     result["certificado"].get("pdf_path"))
        except Exception as e:
            log.warning("dale_play_pipeline: certificacion failed: %s", e)
            result["certificado"] = {"error": str(e)}

    # ── Run log + Audit log inmutable ────────────────────────────────────────
    _guardar_run_log(show_id, result, _log_ejecutados, _log_fallidos,
                     _fuentes, ts_inicio, smoke=smoke_result)

    log.info("=== dale_play_pipeline OK — %s · %s ===", artist, show_date)
    return result


# ── Persistencia del run log ──────────────────────────────────────────────────

def _guardar_run_log(show_id: str, result: dict,
                     ejecutados: list, fallidos: list,
                     fuentes: dict, ts_inicio: float,
                     smoke: dict | None) -> None:
    """Guarda run_log_{show_id}.json + audit_log en Supabase."""
    dur_total = round(time.time() - ts_inicio, 2)

    # Confianza general: roja si hay críticos fallidos, amarilla si hay fallback
    n_fallidos = len(fallidos)
    n_estimados = sum(1 for e in ejecutados if e.get("confianza") in ("amarillo", "rojo"))
    if n_fallidos > 0:
        confianza_general = "rojo" if any(f["critico"] for f in fallidos) else "amarillo"
    elif n_estimados > len(ejecutados) * 0.5:
        confianza_general = "amarillo"
    else:
        confianza_general = "verde"

    run_log = {
        "show_id":            show_id,
        "timestamp":          datetime.now(timezone.utc).isoformat(),
        "duracion_total_s":   dur_total,
        "modulos_ejecutados": ejecutados,
        "modulos_fallidos":   fallidos,
        "fuentes_usadas":     fuentes,
        "confianza_general":  confianza_general,
        "smoke_tests":        smoke,
        "pipeline_error":     result.get("_pipeline_error"),
    }

    # Guardar JSON local (models/run_log_{show_id}.json)
    try:
        out_path = _MODELS_DIR / f"run_log_{show_id}.json"
        out_path.write_text(
            json.dumps(run_log, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        log.info("dale_play_pipeline: run_log → %s", out_path)
    except Exception as e:
        log.warning("dale_play_pipeline: run_log write failed: %s", e)

    # Audit log inmutable en Supabase
    try:
        from dale_play_storage import save_audit_log
        save_audit_log(
            show_id            = show_id,
            modulos_ejecutados = ejecutados,
            modulos_fallidos   = fallidos,
            fuentes_usadas     = fuentes,
            confianza_general  = confianza_general,
            smoke_tests        = smoke,
        )
    except Exception as e:
        log.warning("dale_play_pipeline: audit_log failed: %s", e)
