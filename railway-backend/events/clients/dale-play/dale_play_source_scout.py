"""
dale_play_source_scout.py — Auto-mejora semanal: descubrimiento de nuevas fuentes satelitales.

Job semanal que:
1. Llama a Claude API con web_search para buscar fuentes SAR/ópticas para Argentina/BsAs.
2. Evalúa cada fuente vs las actuales (cobertura, resolución, costo, integración).
3. Guarda resultados en Supabase source_discoveries + SOURCE_LOG.md.
4. Genera resumen en logs para revisión humana.

Variables de entorno requeridas:
  ANTHROPIC_API_KEY — Claude API key (ya configurada en Railway para acoustic module)

Modelo: claude-sonnet-4-6 con web_search_20250305.
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
from datetime import datetime, timezone
from typing import Optional

log = logging.getLogger(__name__)

# ── Fuentes actuales del sistema (contexto para el scout) ─────────────────────
_CURRENT_SOURCES = {
    "SAR": [
        {"id": "UMBRA_X",    "desc": "Umbra Open Data X-band 0.26m CC-BY-4.0",           "confianza_max": 0.95, "costo": "gratuito"},
        {"id": "S1_RTC_PC",  "desc": "Sentinel-1 RTC C-band 20m Planetary Computer",     "confianza_max": 0.80, "costo": "gratuito"},
        {"id": "CAPELLA_X",  "desc": "Capella Open Data X-band 0.5m CC-BY-4.0",          "confianza_max": 0.90, "costo": "gratuito"},
        {"id": "ALOS2_META", "desc": "ALOS-2 PALSAR-2 L-band 25m ASF (metadata only)",  "confianza_max": 0.15, "costo": "gratuito"},
    ],
    "optical_ndvi": [
        {"id": "S2_PC",   "desc": "Sentinel-2 L2A 10m Planetary Computer SAS token",     "confianza_max": 0.90, "costo": "gratuito"},
        {"id": "S2_E84",  "desc": "Sentinel-2 L2A 10m Element84 Earth Search",           "confianza_max": 0.90, "costo": "gratuito"},
        {"id": "HLS_PC",  "desc": "HLS Landsat+S2 30m Planetary Computer",               "confianza_max": 0.85, "costo": "gratuito"},
        {"id": "S2_OPEN", "desc": "Sentinel-2 Element84 COG directo (fallback openeo)",  "confianza_max": 0.80, "costo": "gratuito"},
    ],
    "thermal": [
        {"id": "TIRS_PC", "desc": "Landsat-9 C2L2 TIRS ST_B10 100m Planetary Computer", "confianza_max": 0.75, "costo": "gratuito"},
    ],
    "InSAR": [
        {"id": "EGMS",    "desc": "EGMS Copernicus 2015-2022 histórico",                  "confianza_max": 0.40, "costo": "gratuito"},
    ],
    "weather": [
        {"id": "OPEN_METEO", "desc": "Open-Meteo API pronóstico 72h",                    "confianza_max": 0.90, "costo": "gratuito"},
    ],
}

_SCOUT_PROMPT = """Sistema: Faro Protocol monitorea Estadio Amalfitani, Buenos Aires (-34.638,-58.529).
Fuentes SAR actuales: Umbra X-band, Sentinel-1 RTC C-band, Capella X-band, ALOS-2 L-band.
Fuentes ópticas actuales: Sentinel-2 10m, HLS 30m, Landsat-9 100m TIRS.

Busca en la web (máx 3 búsquedas) novedades 2024-2026 sobre fuentes gratuitas para Argentina:
- SAR open data (ICEYE, SAOCOM, NovaSAR, Sentinel-1 nuevas colecciones)
- Óptico alta resolución gratuito (Maxar Open Data, Planet NICFI, Landsat Next)
- InSAR tiempo real Buenos Aires (COMET LiCSAR, ARIA)
- APIs suelo/humedad (SMAP, GRACE-FO)

Para cada fuente que supere lo actual, incluirla en el JSON.

IMPORTANTE: Responde ÚNICAMENTE con el JSON. Ningún texto antes ni después. Empieza directamente con {{
{{
  "fuentes_nuevas": [
    {{
      "nombre": "str",
      "url": "str",
      "tipo": "SAR|optical|InSAR|weather|soil",
      "cobertura": "global|Argentina|BsAs",
      "resolucion": "Xm",
      "frecuencia_revisita": "X dias",
      "costo": "gratuito|freemium|pago",
      "ventaja_vs_actual": "str corto",
      "recomendacion": "adoptar|investigar|descartar",
      "url_stac_o_api": "str"
    }}
  ],
  "resumen_ejecutivo": "str 2 oraciones max",
  "prioridad": ["nombre1", "nombre2"]
}}"""


# ─────────────────────────────────────────────────────────────────────────────
# Scout principal
# ─────────────────────────────────────────────────────────────────────────────

def run_source_scout() -> dict:
    """
    Ejecuta el scout semanal de nuevas fuentes via Claude API + web_search.

    Returns:
        {
          "fuentes_nuevas": [...],
          "resumen_ejecutivo": str,
          "prioridad": [...],
          "timestamp": str,
          "modelo": str,
          "error": str | None,
        }
    """
    t0 = time.time()
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        log.warning("dale_play_source_scout: ANTHROPIC_API_KEY no configurado")
        return {
            "error":     "ANTHROPIC_API_KEY no configurado en Railway",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "fuentes_nuevas": [],
        }

    try:
        import anthropic

        client = anthropic.Anthropic(api_key=api_key)
        prompt = _SCOUT_PROMPT.format(
            current_sources=json.dumps(_CURRENT_SOURCES, indent=2, ensure_ascii=False)
        )

        log.info("dale_play_source_scout: iniciando scout via Claude API + web_search")

        response = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=8192,
            tools=[{"type": "web_search_20250305", "name": "web_search"}],
            messages=[{"role": "user", "content": prompt}],
        )

        # Extraer texto (pueden haber tool_use blocks antes del text final)
        text_parts = []
        for block in response.content:
            if hasattr(block, "text"):
                text_parts.append(block.text)
        full_text = "\n".join(text_parts).strip()

        # Parsear JSON
        result = _extract_json(full_text)

        result["timestamp"] = datetime.now(timezone.utc).isoformat() + "Z"
        result["modelo"]    = "claude-sonnet-4-6 + web_search_20250305"
        result["duracion_s"] = round(time.time() - t0, 1)
        result.pop("error", None)

        n_fuentes = len(result.get("fuentes_nuevas", []))
        log.info(
            "dale_play_source_scout: completado en %.1fs — %d fuentes nuevas encontradas",
            time.time() - t0, n_fuentes,
        )

        return result

    except Exception as exc:
        log.warning("dale_play_source_scout: error: %s", exc)
        return {
            "error":          str(exc),
            "timestamp":      datetime.now(timezone.utc).isoformat() + "Z",
            "fuentes_nuevas": [],
            "duracion_s":     round(time.time() - t0, 1),
        }


def _extract_json(text: str) -> dict:
    """
    Extrae JSON válido del texto de respuesta de Claude.
    Maneja: JSON directo, con prefijo de texto, dentro de ```json...```,
    y respuestas truncadas por max_tokens.
    """
    # 1. Parsear directo (mejor caso: prefill funcionó)
    try:
        return json.loads(text)
    except Exception:
        pass

    # 2. Bloque ```json...``` con cierre — regex greedy para capturar más contenido
    match = re.search(r"```json\s*([\s\S]+?)\s*```", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1))
        except Exception:
            pass

    # 3. Bloque ```json sin cierre (respuesta truncada) — tomar todo desde ahí
    match = re.search(r"```json\s*([\s\S]+)$", text, re.DOTALL)
    if match:
        candidate = match.group(1).strip()
        parsed = _try_recover_json(candidate)
        if parsed:
            parsed["_truncated"] = True
            return parsed

    # 4. Primer { hasta el final — respuesta sin code block
    match = re.search(r"\{[\s\S]+", text, re.DOTALL)
    if match:
        candidate = match.group().strip()
        parsed = _try_recover_json(candidate)
        if parsed:
            return parsed

    log.warning("dale_play_source_scout: no se pudo parsear JSON (len=%d)", len(text))
    return {
        "fuentes_nuevas":    [],
        "resumen_ejecutivo": "Error parseando respuesta — ver raw_response",
        "prioridad":         [],
        "raw_response":      text[:2000],
        "error":             "JSON parse failed",
    }


def _try_recover_json(text: str) -> dict | None:
    """
    Intenta parsear JSON posiblemente truncado.
    Estrategia: intentar tal cual, luego cerrar llaves/corchetes faltantes.
    """
    # Intento directo
    try:
        return json.loads(text)
    except Exception:
        pass

    # Contar llaves y corchetes para cerrar lo que falta
    stack = []
    in_str = False
    escape = False
    last_good = 0

    for i, ch in enumerate(text):
        if escape:
            escape = False
            continue
        if ch == "\\" and in_str:
            escape = True
            continue
        if ch == '"':
            in_str = not in_str
            continue
        if in_str:
            continue
        if ch in ("{", "["):
            stack.append("}" if ch == "{" else "]")
        elif ch in ("}", "]"):
            if stack and stack[-1] == ch:
                stack.pop()
                if not stack:
                    last_good = i + 1

    # Remover trailing comas antes de cerrar
    partial = text[:last_good if last_good else len(text)].rstrip().rstrip(",")
    closing = "".join(reversed(stack))
    candidate = partial + closing

    try:
        return json.loads(candidate)
    except Exception:
        return None


# ─────────────────────────────────────────────────────────────────────────────
# Persistencia y notificación
# ─────────────────────────────────────────────────────────────────────────────

def save_and_notify(scout_result: dict) -> dict:
    """
    Guarda resultados del scout en Supabase + SOURCE_LOG.md + GitHub.

    Returns:
        {"saved": int, "errors": int, "log_url": str | None}
    """
    saved  = 0
    errors = 0

    # ── Guardar cada fuente en Supabase ───────────────────────────────────────
    for fuente in scout_result.get("fuentes_nuevas", []):
        try:
            from dale_play_storage import save_source_discovery
            ok = save_source_discovery({
                "fuente_nombre": fuente.get("nombre", ""),
                "fuente_url":    fuente.get("url", ""),
                "fuente_tipo":   fuente.get("tipo", ""),
                "cobertura":     fuente.get("cobertura", ""),
                "resolucion":    fuente.get("resolucion", ""),
                "frecuencia":    fuente.get("frecuencia_revisita", ""),
                "costo":         fuente.get("costo", ""),
                "evaluacion":    fuente.get("ventaja_vs_actual", ""),
                "recomendacion": fuente.get("recomendacion", ""),
                "datos_raw":     fuente,
            })
            if ok:
                saved += 1
            else:
                errors += 1
        except Exception as exc:
            log.warning("dale_play_source_scout: save_source_discovery: %s", exc)
            errors += 1

    # ── Guardar reporte completo en Supabase ──────────────────────────────────
    try:
        from dale_play_storage import save_scout_report
        save_scout_report(scout_result)
    except Exception as exc:
        log.warning("dale_play_source_scout: save_scout_report: %s", exc)

    # ── Escribir en SOURCE_LOG.md ─────────────────────────────────────────────
    log_url = None
    try:
        from dale_play_source_log import log_discovery_event, push_log_to_github

        for fuente in scout_result.get("fuentes_nuevas", []):
            log_discovery_event(
                fuente_nombre = fuente.get("nombre", ""),
                fuente_tipo   = fuente.get("tipo", ""),
                recomendacion = fuente.get("recomendacion", ""),
                evaluacion    = fuente.get("ventaja_vs_actual", ""),
            )

        log_url = push_log_to_github("source_scout_semanal")
    except Exception as exc:
        log.warning("dale_play_source_scout: source_log: %s", exc)

    log.info(
        "dale_play_source_scout: persistencia completa — saved=%d errors=%d log_url=%s",
        saved, errors, log_url,
    )
    return {"saved": saved, "errors": errors, "log_url": log_url}


# ─────────────────────────────────────────────────────────────────────────────
# Entry point del job semanal
# ─────────────────────────────────────────────────────────────────────────────

def weekly_source_scout_job() -> None:
    """
    Función que ejecuta APScheduler cada semana.
    1. Corre el scout.
    2. Guarda resultados.
    3. Loguea resumen.
    """
    log.info("dale_play_source_scout: === JOB SEMANAL INICIADO ===")
    try:
        result  = run_source_scout()
        if result.get("error"):
            log.warning("dale_play_source_scout: scout con error: %s", result["error"])
            return

        persist = save_and_notify(result)

        n_adopt   = sum(1 for f in result.get("fuentes_nuevas", []) if f.get("recomendacion") == "adoptar")
        n_invest  = sum(1 for f in result.get("fuentes_nuevas", []) if f.get("recomendacion") == "investigar")
        n_discard = sum(1 for f in result.get("fuentes_nuevas", []) if f.get("recomendacion") == "descartar")

        log.info(
            "dale_play_source_scout: === JOB SEMANAL COMPLETADO === "
            "fuentes=%d adoptar=%d investigar=%d descartar=%d "
            "guardadas=%d errores=%d",
            len(result.get("fuentes_nuevas", [])),
            n_adopt, n_invest, n_discard,
            persist.get("saved", 0),
            persist.get("errors", 0),
        )

        if n_adopt > 0:
            prioridades = result.get("prioridad", [])
            log.info(
                "dale_play_source_scout: ACCION REQUERIDA — %d fuentes recomendadas para adoptar: %s",
                n_adopt, prioridades,
            )

        resumen = result.get("resumen_ejecutivo", "")
        if resumen:
            log.info("dale_play_source_scout: RESUMEN — %s", resumen)

    except Exception as exc:
        log.error("dale_play_source_scout: job_semanal error: %s", exc, exc_info=True)
