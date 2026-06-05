"""
paperclip.py — Monitor inteligente de venues Faro Protocol.

Reemplaza los umbrales fijos del job de monitoreo cada 6h.
Claude recibe el contexto completo (clima, NDVI, SAR, época, historial)
y decide si alertar, eliminando falsos positivos por falta de contexto.

Comportamiento:
  - Decide alertar o no + nivel + motivo
  - Persiste todas las decisiones en paperclip_decisions (append-only)
  - Fail-open: si Claude no disponible, retorna fallback=True

Costo: ~400–600 tokens/venue con prompt caching activo
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone

log = logging.getLogger(__name__)

_SUPABASE_TABLE = "paperclip_decisions"

# ── System prompt cacheado ────────────────────────────────────────────────────
_DOMAIN_KNOWLEDGE = """\
Sos Paperclip, monitor inteligente de venues deportivos del sistema Faro Protocol.

Tu tarea: evaluar el contexto completo de un estadio y decidir si se debe enviar
una alerta. NO uses umbrales fijos — razoná sobre si el dato es realmente preocupante
dado el contexto completo.

## Venue: Estadio Amalfitani (Vélez Sársfield), Buenos Aires, Argentina
- Campo de juego: césped natural, 105m × 68m, suelo arcilloso-limoso
- Zona climática: pampeana, precipitaciones medias ~1000mm/año
- Temporada: partidos todo el año, con recesos de mantenimiento en verano
- Uso no deportivo: conciertos eventuales (impacto físico en césped)

## Hemisferio Sur — referencia estacional
Verano   (Dic–Feb): calor, alta evapotranspiración, riego intensivo
Otoño    (Mar–May): lluvias, recuperación post-verano
Invierno (Jun–Ago): frío seco, césped latente, NDVI bajo esperado
Primavera(Sep–Nov): crecimiento activo, NDVI en ascenso

## Señales y su contexto

### Lluvia
- < 20mm/24h próximas: normal, no alertar salvo que ya haya saturación previa
- 20–40mm/24h: posible problema si hay evento programado en < 72h
- > 40mm/24h: preocupante para integridad del campo
- Lluvia acumulada post-concierto: ESPERAR recuperación natural 7–10 días antes de alertar

### Viento
- > 70 km/h: real preocupación para estructuras efímeras (techos, backlines)
- < 70 km/h: viento normal en BA en invierno, no alertar

### NDVI
- Caída post-evento (concierto, lluvia intensa): esperada, NO alertar si < 3 semanas
- Caída sin causa conocida > 0.15: SÍ alertar
- NDVI bajo en invierno (Jun–Ago): NORMAL para pasto templado, NO alertar
- NDVI bajo persistente en verano (Dic–Feb): SÍ alertar (pasto muerto o sin riego)

### SAR confidence
- Fuente EGMS (proxy): menor confianza, útil como indicador tendencial
- Sin datos SAR: situación transitoria, no alertar si es primera vez

## Principio clave
Alertar solo cuando el dato representa un RIESGO REAL para la operación del estadio
o para la integridad del campo. Un NDVI de 0.22 en julio NO es riesgo — es invierno.
Lluvia de 25mm cuando el próximo partido es en 5 días SÍ puede ser relevante.
"""

# Niveles de alerta
_NIVELES = ("none", "low", "medium", "high")


def analyze_venue(
    venue: dict,
    weather_ctx: dict,
    ndvi_ctx: dict,
    sar_ctx: dict,
    historial: list | None = None,
) -> dict:
    """
    Analiza el contexto completo de un venue y decide si alertar.

    Args:
        venue:       dict con venue_id, nombre, lat, lon
        weather_ctx: dict con precipitation_24h, wind_max, horas, etc.
        ndvi_ctx:    dict con baseline_ndvi, current_ndvi, drop, n_muestras, etc.
        sar_ctx:     dict con sar_fuente, is_egms_proxy, modules_ok, etc.
        historial:   lista de eventos recientes (opcional)

    Returns:
        {
          "alertar": bool, "nivel": str, "motivo": str,
          "confianza": float, "recomendacion": str,
          "fallback": bool  ← solo presente si Claude no disponible
        }
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        log.warning("paperclip: ANTHROPIC_API_KEY no configurada — fail-open [%s]",
                    venue.get("venue_id", "?"))
        return {"alertar": False, "nivel": "none", "motivo": "paperclip_unavailable",
                "confianza": 0.0, "fallback": True}

    try:
        import anthropic
    except ImportError:
        log.warning("paperclip: anthropic no instalado — fail-open [%s]",
                    venue.get("venue_id", "?"))
        return {"alertar": False, "nivel": "none", "motivo": "paperclip_import_error",
                "confianza": 0.0, "fallback": True}

    mes = datetime.now().month
    _MESES = {
        1: "enero", 2: "febrero", 3: "marzo", 4: "abril",
        5: "mayo",  6: "junio",   7: "julio",  8: "agosto",
        9: "septiembre", 10: "octubre", 11: "noviembre", 12: "diciembre",
    }
    _ESTACIONES = {
        12: "verano", 1: "verano", 2: "verano",
        3: "otoño",   4: "otoño",  5: "otoño",
        6: "invierno",7: "invierno",8: "invierno",
        9: "primavera",10:"primavera",11:"primavera",
    }

    # Construir mensaje de usuario con datos en formato legible
    precip = weather_ctx.get("precipitation_24h")
    wind   = weather_ctx.get("wind_max")
    b_ndvi = ndvi_ctx.get("baseline_ndvi")
    c_ndvi = ndvi_ctx.get("current_ndvi")
    drop   = ndvi_ctx.get("drop")
    sar_f  = sar_ctx.get("sar_fuente", "no_data")
    egms   = sar_ctx.get("is_egms_proxy", False)

    ndvi_str = (
        f"baseline={b_ndvi:.3f} | actual={c_ndvi:.3f} | caída={drop:.3f}"
        if (b_ndvi is not None and c_ndvi is not None and drop is not None)
        else "sin datos NDVI"
    )

    historial_str = ""
    if historial:
        historial_str = "\nHistorial reciente (últimos eventos):\n"
        for h in historial[:5]:
            historial_str += f"  - {h}\n"

    user_msg = (
        f"Revisá el estado actual del venue y decidí si alertar:\n\n"
        f"Venue: {venue.get('nombre', venue.get('venue_id', '?'))}\n"
        f"Fecha: {_MESES.get(mes, '?')} (mes {mes}) — {_ESTACIONES.get(mes, '?')}\n\n"
        f"CLIMA (próximas 24h):\n"
        f"  Lluvia acumulada: {precip:.1f} mm\n"
        f"  Viento máximo:    {wind:.0f} km/h\n\n"
        f"NDVI:\n"
        f"  {ndvi_str}\n\n"
        f"SAR:\n"
        f"  Fuente:     {sar_f}\n"
        f"  Proxy EGMS: {'sí' if egms else 'no'}\n"
        f"{historial_str}"
        f"\nUsá la herramienta paperclip_decision para dar tu veredicto."
    )

    paperclip_tool = {
        "name": "paperclip_decision",
        "description": "Decisión de alerta contextual del monitor inteligente Paperclip",
        "input_schema": {
            "type": "object",
            "properties": {
                "alertar": {
                    "type": "boolean",
                    "description": "True si se debe enviar alerta, False si todo es normal",
                },
                "nivel": {
                    "type": "string",
                    "enum": ["none", "low", "medium", "high"],
                    "description": "none=sin alerta, low=observar, medium=precaución, high=acción requerida",
                },
                "motivo": {
                    "type": "string",
                    "description": "Explicación en 1–2 oraciones de la decisión",
                },
                "factores": {
                    "type": "array",
                    "description": "Factores relevantes que influyeron en la decisión",
                    "items": {
                        "type": "object",
                        "properties": {
                            "tipo":       {"type": "string"},
                            "valor":      {"type": "string"},
                            "relevancia": {"type": "string", "enum": ["alta", "media", "baja"]},
                        },
                        "required": ["tipo", "valor", "relevancia"],
                    },
                },
                "confianza": {
                    "type": "number",
                    "description": "Confianza en la decisión: 0.0–1.0",
                },
                "recomendacion": {
                    "type": "string",
                    "description": "Acción sugerida en 1 oración (solo si alertar=True)",
                },
            },
            "required": ["alertar", "nivel", "motivo", "factores", "confianza"],
        },
    }

    try:
        client = anthropic.Anthropic(api_key=api_key)
        resp = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=512,
            system=[
                {
                    "type": "text",
                    "text": _DOMAIN_KNOWLEDGE,
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            tools=[paperclip_tool],
            tool_choice={"type": "tool", "name": "paperclip_decision"},
            messages=[{"role": "user", "content": user_msg}],
        )

        tool_result: dict | None = None
        for block in resp.content:
            if block.type == "tool_use" and block.name == "paperclip_decision":
                tool_result = block.input
                break

        if not tool_result:
            log.warning("paperclip: sin tool_use en respuesta — fail-open [%s]",
                        venue.get("venue_id"))
            return {"alertar": False, "nivel": "none", "motivo": "paperclip_no_tool_output",
                    "confianza": 0.0, "fallback": True}

        nivel = tool_result.get("nivel", "none")
        if nivel not in _NIVELES:
            nivel = "none"

        result = {
            "alertar":       bool(tool_result.get("alertar", False)),
            "nivel":         nivel,
            "motivo":        str(tool_result.get("motivo", "")),
            "factores":      tool_result.get("factores", []),
            "confianza":     float(tool_result.get("confianza", 0.7)),
            "recomendacion": str(tool_result.get("recomendacion", "")),
        }

        usage = resp.usage
        log.info(
            "paperclip [%s] alertar=%s nivel=%s | %s | tokens in=%d out=%d cache_read=%d",
            venue.get("venue_id"), result["alertar"], result["nivel"], result["motivo"],
            usage.input_tokens, usage.output_tokens,
            getattr(usage, "cache_read_input_tokens", 0),
        )

        # Persistir decisión en Supabase (siempre, no solo si alerta)
        _save_decision(
            venue_id=venue.get("venue_id", "unknown"),
            result=result,
            contexto={
                "weather": weather_ctx,
                "ndvi":    ndvi_ctx,
                "sar":     sar_ctx,
                "mes":     mes,
            },
        )

        return result

    except Exception as exc:
        log.warning("paperclip: error Claude API — fail-open [%s]: %s",
                    venue.get("venue_id"), exc)
        return {"alertar": False, "nivel": "none", "motivo": f"paperclip_error:{exc}",
                "confianza": 0.0, "fallback": True}


# ── Persistencia ──────────────────────────────────────────────────────────────

def _save_decision(venue_id: str, result: dict, contexto: dict) -> None:
    """Guarda decisión en paperclip_decisions (append-only). No bloquea si falla."""
    supa_url = os.environ.get("SUPABASE_URL", "")
    supa_key = os.environ.get("SUPABASE_KEY", "")
    if not supa_url or not supa_key:
        log.info("paperclip: Supabase no configurado — decisión no persistida [%s]", venue_id)
        return

    try:
        import requests
        row = {
            "venue_id":      venue_id,
            "alertar":       result.get("alertar", False),
            "nivel":         result.get("nivel", "none"),
            "motivo":        result.get("motivo", ""),
            "confianza":     result.get("confianza", 0.0),
            "recomendacion": result.get("recomendacion", ""),
            "contexto":      contexto,
            "created_at":    datetime.now(timezone.utc).isoformat(),
        }
        url  = f"{supa_url}/rest/v1/{_SUPABASE_TABLE}"
        hdrs = {
            "apikey":        supa_key,
            "Authorization": f"Bearer {supa_key}",
            "Content-Type":  "application/json",
            "Prefer":        "return=minimal",
        }
        r = requests.post(url, headers=hdrs, data=json.dumps(row, default=str), timeout=10)
        if r.status_code not in (200, 201, 204):
            log.warning(
                "paperclip: Supabase paperclip_decisions HTTP %s: %s",
                r.status_code, r.text[:200],
            )
    except Exception as exc:
        log.warning("paperclip: no se pudo guardar decisión: %s", exc)
