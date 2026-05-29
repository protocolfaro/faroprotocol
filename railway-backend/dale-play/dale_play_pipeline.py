"""
dale_play_pipeline.py — Orquestador del pipeline de auditoría Dale Play.
Corre los módulos en orden y produce show_data completo.

Modos:
  full         — layout → satélite → clima → acústica → suelo → drenaje → EGMS → comparativa → reporte PNG
  weather_only — solo pronóstico climático
  post_show    — full + InSAR diferencial + certificación automática
"""
from __future__ import annotations
import logging, pathlib
from datetime import datetime, timezone

log = logging.getLogger(__name__)

_SHOWS_DIR = pathlib.Path(__file__).parent / "shows"


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

    # ── 0. Layout real (si fue subido) ──────────────────────────────────────────
    try:
        from dale_play_layout import load_layout
        layout = load_layout(show_id)
        if layout is not None:
            result["layout"] = layout
            log.info("dale_play_pipeline: layout real cargado (%s)", layout.get("filename"))
        else:
            result["layout"] = None
            log.info("dale_play_pipeline: sin layout subido — usando rider JSON como fallback")
    except Exception as e:
        log.warning("dale_play_pipeline: layout load error: %s", e)
        result["layout"] = None

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

    # ── 5a. Drenaje del campo ────────────────────────────────────────────────
    try:
        from dale_play_drainage import analyze_drainage
        result["drainage"] = analyze_drainage()
        log.info("dale_play_pipeline: drainage OK")
    except Exception as e:
        log.warning("dale_play_pipeline: drainage failed: %s", e)
        result["drainage"] = {"error": str(e)}

    # ── 5b. EGMS histórico ───────────────────────────────────────────────────
    try:
        from dale_play_egms import fetch_egms_amalfitani
        result["egms"] = fetch_egms_amalfitani()
        log.info("dale_play_pipeline: egms OK — sector_critico=%s",
                 result["egms"].get("sector_critico"))
    except Exception as e:
        log.warning("dale_play_pipeline: egms failed: %s", e)
        result["egms"] = {"error": str(e)}

    # ── 5c. Comparativa layout real vs Faro Protocol ─────────────────────────
    try:
        from dale_play_vision import analyze_comparativa
        result["comparativa"] = analyze_comparativa(show_id=show_id)
        log.info("dale_play_pipeline: comparativa OK — score=%d",
                 result["comparativa"].get("score", {}).get("dale_play", 0))
    except Exception as e:
        log.warning("dale_play_pipeline: comparativa failed: %s", e)
        result["comparativa"] = {"error": str(e)}

    # ── 6. InSAR post-show (solo en modo post_show) ──────────────────────────
    if mode == "post_show":
        try:
            from dale_play_insar import fetch_post_show_vibration
            result["insar"] = fetch_post_show_vibration()
            log.info("dale_play_pipeline: insar OK")
        except Exception as e:
            log.warning("dale_play_pipeline: insar failed: %s", e)
            result["insar"] = {"error": str(e)}

    # ── 7. Reporte PNG (incluyendo superposición layout/drenaje si hay layout) ─
    try:
        from dale_play_report import generate_report
        result["report_png"] = generate_report(result, show_config)
        log.info("dale_play_pipeline: report PNG → %s", result["report_png"])
    except Exception as e:
        log.warning("dale_play_pipeline: report failed: %s", e)
        result["report_png"] = None

    # ── 7b. Push PNG a GitHub (persistencia) ─────────────────────────────────
    result["report_png_url"] = None
    if result.get("report_png"):
        try:
            from dale_play_github import push_png_to_github
            result["report_png_url"] = push_png_to_github(show_id, result["report_png"])
            log.info("dale_play_pipeline: PNG → GitHub %s", result["report_png_url"])
        except Exception as e:
            log.warning("dale_play_pipeline: PNG github push failed: %s", e)

    # ── 8. Histórico GitHub ──────────────────────────────────────────────────
    try:
        from dale_play_github import push_show_snapshot
        result["github"] = push_show_snapshot(show_id, result)
        log.info("dale_play_pipeline: github %s", result["github"].get("action"))
    except Exception as e:
        log.warning("dale_play_pipeline: github failed: %s", e)
        result["github"] = {"error": str(e)}

    # ── 9. Certificación automática (solo en modo post_show) ─────────────────
    if mode == "post_show":
        try:
            from dale_play_certification import run_certification
            result["certificado"] = run_certification(show_id, mode="post_show")
            log.info("dale_play_pipeline: certificado → %s",
                     result["certificado"].get("pdf_path"))
        except Exception as e:
            log.warning("dale_play_pipeline: certificacion failed: %s", e)
            result["certificado"] = {"error": str(e)}

    log.info("=== dale_play_pipeline OK — %s · %s ===", artist, show_date)
    return result
