"""
dale_play_pipeline.py — Orquestador del pipeline de auditoría Dale Play.
Corre los módulos en orden y produce show_data completo.

Modos:
  full       — satélite + clima + acústica + suelo + reporte + GitHub
  weather_only — solo pronóstico climático
  post_show  — full + InSAR diferencial post-show
"""
from __future__ import annotations
import logging
from datetime import datetime, timezone

log = logging.getLogger(__name__)


def run_show_audit(show_config: dict, mode: str = "full") -> dict:
    """
    show_config: contenido de dale-play/shows/{show_id}.json
    Retorna show_data dict con todos los resultados y metadatos.
    """
    show_id   = show_config.get("show_id",   "unknown")
    show_date = show_config.get("show_date", "")
    artist    = show_config.get("artist",    "Artista")
    rider     = show_config.get("rider",     {})

    log.info("=== dale_play_pipeline START — %s · %s · mode=%s ===",
             artist, show_date, mode)

    result = {
        "show_id":   show_id,
        "show_date": show_date,
        "artist":    artist,
        "venue":     "Estadio José Amalfitani",
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "mode":      mode,
    }

    # ── 1. Baseline satelital ────────────────────────────────────────────────
    if mode in ("full", "post_show"):
        try:
            from dale_play_satellite import fetch_satellite_baseline
            result["satellite"] = fetch_satellite_baseline()
            log.info("dale_play_pipeline: satellite OK — NDVI %s",
                     result["satellite"].get("ndvi"))
        except Exception as e:
            log.warning("dale_play_pipeline: satellite failed: %s", e)
            result["satellite"] = {"error": str(e)}

    # ── 2. Pronóstico climático (siempre) ────────────────────────────────────
    try:
        from dale_play_weather import fetch_72h_forecast
        result["weather"] = fetch_72h_forecast(show_date=show_date)
        log.info("dale_play_pipeline: weather OK — riesgo_global=%s",
                 result["weather"].get("riesgo_global"))
    except Exception as e:
        log.warning("dale_play_pipeline: weather failed: %s", e)
        result["weather"] = {"error": str(e), "riesgo_global": "sin_datos"}

    if mode == "weather_only":
        return result

    # ── 3. Análisis acústico + sightlines ────────────────────────────────────
    try:
        from dale_play_acoustic import analyze_acoustic_sightlines
        result["acoustic"] = analyze_acoustic_sightlines(rider)
        log.info("dale_play_pipeline: acoustic OK — cobertura_optima=%s%%",
                 result["acoustic"].get("cobertura_optima_pct"))
    except Exception as e:
        log.warning("dale_play_pipeline: acoustic failed: %s", e)
        result["acoustic"] = {"error": str(e)}

    # ── 4. Carga del suelo ───────────────────────────────────────────────────
    try:
        from dale_play_soil import analyze_soil_load
        lluvia = float(
            (result.get("weather") or {}).get("show_day", {}).get("lluvia_mm") or 0
        )
        result["soil"] = analyze_soil_load(rider, lluvia_48h_mm=lluvia)
        log.info("dale_play_pipeline: soil OK — exclusiones=%d",
                 result["soil"].get("n_exclusiones", 0))
    except Exception as e:
        log.warning("dale_play_pipeline: soil failed: %s", e)
        result["soil"] = {"error": str(e)}

    # ── 5. InSAR post-show (solo en modo post_show) ──────────────────────────
    if mode == "post_show":
        try:
            from dale_play_insar import fetch_post_show_vibration
            result["insar"] = fetch_post_show_vibration()
            log.info("dale_play_pipeline: insar OK")
        except Exception as e:
            log.warning("dale_play_pipeline: insar failed: %s", e)
            result["insar"] = {"error": str(e)}

    # ── 6. Reporte PNG ───────────────────────────────────────────────────────
    try:
        from dale_play_report import generate_report
        result["report_png"] = generate_report(result, show_config)
        log.info("dale_play_pipeline: report PNG → %s", result["report_png"])
    except Exception as e:
        log.warning("dale_play_pipeline: report failed: %s", e)
        result["report_png"] = None

    # ── 7. Histórico GitHub ──────────────────────────────────────────────────
    try:
        from dale_play_github import push_show_snapshot
        result["github"] = push_show_snapshot(show_id, result)
        log.info("dale_play_pipeline: github %s", result["github"].get("action"))
    except Exception as e:
        log.warning("dale_play_pipeline: github failed: %s", e)
        result["github"] = {"error": str(e)}

    log.info("=== dale_play_pipeline OK — %s · %s ===", artist, show_date)
    return result
