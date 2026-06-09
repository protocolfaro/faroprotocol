"""
hermes.py — Validador de certificados Faro Protocol.

Antes de emitir el SHA-256, Claude revisa coherencia físico-estacional:
  - SAR backscatter dentro del rango físico para el tipo de cobertura
  - NDVI consistente con la época del año (hemisferio sur)
  - FII plausible dado el modo de datos (SAR-only vs SAR+óptico)

Comportamiento:
  - Aprobado  → retorna dict, pipeline continúa
  - Bloqueado → lanza CertificateBlocked, loguea en hermes_flags
  - Sin API   → fail-open (aprueba y loguea advertencia)

Costo: ~300–500 tokens/cert con prompt caching activo (90 % reducción)
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone

log = logging.getLogger(__name__)

_SUPABASE_TABLE = "hermes_flags"

# ── System prompt cacheado (SAR + NDVI + FII domain knowledge) ───────────────
_DOMAIN_KNOWLEDGE = """\
Sos Hermes, validador físico-estacional de certificados satelitales Faro Protocol.
Tu tarea: revisar si los índices son físicamente plausibles antes de emitir SHA-256.

## Física SAR — Sentinel-1 VV, superficies típicas
Pasto/césped sano:     −18 a  −8 dB
Suelo desnudo/seco:    −14 a  −5 dB
Estructuras/urbano:     −5 a  +5 dB
Sin datos / sombra:   < −32 dB  ← error de procesamiento
Double-bounce severo: >  +6 dB  ← calibración incorrecta
Rango aceptable para estadios y campos: −22 a 0 dB

## NDVI estacional — Hemisferio Sur, césped / campo de juego
Verano   (Dic–Feb): 0.55 – 0.85  ← máximo vigor vegetal
Otoño    (Mar–May): 0.35 – 0.65
Invierno (Jun–Ago): 0.10 – 0.40  ← estado latente
Primavera(Sep–Nov): 0.35 – 0.70
NDVI > 0.90 → saturación de sensor o zona boscosa densa (sospechoso en estadio)
NDVI < −0.05 → artefacto negativo, error de banda
NDVI = None en modo SAR-only → completamente normal, no penalizar

## FII (Fusion Index) — rango 0–100
SAR-only (ndvi_disponible=False): 20–80 esperado
SAR + NDVI óptico: 40–95 esperado
FII > 97 con ndvi_disponible=False → sin base óptica no puede llegar tan alto

## Score Faro — rango 0–100
Score 100 con ndvi_disponible=False → sospechoso (máximo sin validación óptica real)
Score < 5 → degradación severa, posible error de pipeline

## Reglas de bloqueo (cualquiera es suficiente para bloquear)
R1: sar_medio_db > 5   (double-bounce o calibración rota)
R2: sar_medio_db < −35 (sin datos / sombra severa)
R3: ndvi_medio > 0.90  (saturación extrema)
R4: ndvi_medio < −0.05 con ndvi_disponible=True
R5: ndvi_medio > 0.60  en meses Jun/Jul/Ago (invierno — inconsistencia estacional)
R6: ndvi_medio < 0.10  en meses Dic/Ene/Feb con ndvi_disponible=True (pasto muerto en verano)
R7: score_faro = 100   con ndvi_disponible=False
R8: indice_fusion_medio > 97 con ndvi_disponible=False

## Intervenciones recientes (contexto operacional)
Si se proveen intervenciones del cuaderno de Roger, usalas para contextualizar anomalías.
Ejemplos de contextualización válida (NO bloquear si hay intervención que lo explique):
- NDVI bajo + resiembra reciente  → plausible, en regeneración
- Backscatter atípico + riego intensivo (≥ 30 mm) → suelo saturado esperado
- Score bajo + aireación hace < 5 días → remoción de tapiz, normal
Aún así, aplicar R1 y R2 sin excepción (son errores instrumentales, no agronómicos).

Si ninguna regla se activa → APROBADO.
"""


class CertificateBlocked(Exception):
    """El certificado fue bloqueado por Hermes por anomalía detectada."""

    def __init__(self, motivo: str, anomalias: list, confianza: float) -> None:
        super().__init__(motivo)
        self.motivo    = motivo
        self.anomalias = anomalias
        self.confianza = confianza


def validate_certificate(
    area_name: str,
    stats: dict,
    insight_score: float | int,
    insight_estado: str,
    digest: str,
    mes: int | None = None,
    intervenciones: list | None = None,
) -> dict:
    """
    Valida coherencia físico-estacional antes de emitir el certificado SHA-256.

    Args:
        area_name:      nombre del área analizada
        stats:          dict con keys: sar_medio_db, ndvi_medio,
                        indice_fusion_medio, ndvi_disponible
        insight_score:  score_faro (0–100)
        insight_estado: estado textual ('ÓPTIMO', 'DEGRADADO', etc.)
        digest:         SHA-256 hex del PNG de reporte
        mes:            mes actual 1–12 (None = detectar automáticamente)
        intervenciones: lista de ops recientes de velez_intervenciones (opcional)

    Returns:
        {"aprobado": True, "motivo": ..., "confianza": ...}

    Raises:
        CertificateBlocked: si Claude detecta anomalía bloqueante
    """
    if mes is None:
        mes = datetime.now().month

    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        log.warning("hermes: ANTHROPIC_API_KEY no configurada — fail-open [%s]", area_name)
        return {"aprobado": True, "motivo": "hermes_unavailable", "fallback": True}

    try:
        import anthropic
    except ImportError:
        log.warning("hermes: anthropic no instalado — fail-open [%s]", area_name)
        return {"aprobado": True, "motivo": "hermes_import_error", "fallback": True}

    _MESES = {
        1: "enero", 2: "febrero", 3: "marzo", 4: "abril",
        5: "mayo",  6: "junio",   7: "julio",  8: "agosto",
        9: "septiembre", 10: "octubre", 11: "noviembre", 12: "diciembre",
    }
    ndvi_disponible = bool(stats.get("ndvi_disponible", False))
    sar_db = stats.get("sar_medio_db")
    ndvi   = stats.get("ndvi_medio")
    fii    = stats.get("indice_fusion_medio")

    _interv_section = ""
    if intervenciones:
        lines = []
        for iv in intervenciones[:10]:  # max 10 para no saturar
            ts   = str(iv.get("created_at", ""))[:10]
            tipo = iv.get("tipo", "?")
            det  = iv.get("detalle", "")
            hu   = iv.get("horas_uso")
            line = f"  [{ts}] {tipo}"
            if hu is not None:
                line += f" {hu}h"
            if det:
                line += f" — {det}"
            lines.append(line)
        _interv_section = "\n\nIntervenciones recientes (últimos 14 días):\n" + "\n".join(lines)

    user_msg = (
        f"Validá el resultado del análisis satelital Faro Protocol:\n\n"
        f"Área:  {area_name}\n"
        f"Mes:   {_MESES.get(mes, str(mes))} (mes {mes})\n"
        f"Modo:  {'SAR + NDVI óptico' if ndvi_disponible else 'SAR-only (sin NDVI real)'}\n\n"
        f"Métricas:\n"
        f"  sar_medio_db        = {sar_db}\n"
        f"  ndvi_medio          = {ndvi if ndvi is not None else 'None (SAR-only)'}\n"
        f"  indice_fusion_medio = {fii}\n"
        f"  score_faro          = {insight_score}\n"
        f"  estado              = {insight_estado}\n"
        f"  sha256_prefix       = {digest[:20]}..."
        f"{_interv_section}\n\n"
        f"Aplicá las reglas de bloqueo y usá la herramienta hermes_decision."
    )

    hermes_tool = {
        "name": "hermes_decision",
        "description": "Veredicto de validación físico-estacional del certificado Faro Protocol",
        "input_schema": {
            "type": "object",
            "properties": {
                "aprobado": {
                    "type": "boolean",
                    "description": "True si todos los valores son plausibles; False si hay anomalía bloqueante",
                },
                "anomalias": {
                    "type": "array",
                    "description": "Anomalías detectadas. Lista vacía si aprobado=True",
                    "items": {
                        "type": "object",
                        "properties": {
                            "campo":  {"type": "string", "description": "Campo afectado, ej: ndvi_medio"},
                            "valor":  {"type": "string", "description": "Valor observado como string"},
                            "regla":  {"type": "string", "description": "Regla violada, ej: R5"},
                            "motivo": {"type": "string", "description": "Explicación breve"},
                        },
                        "required": ["campo", "valor", "regla", "motivo"],
                    },
                },
                "motivo": {
                    "type": "string",
                    "description": "Resumen en una oración de la decisión",
                },
                "confianza": {
                    "type": "number",
                    "description": "Confianza en la decisión: 0.0–1.0",
                },
            },
            "required": ["aprobado", "anomalias", "motivo", "confianza"],
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
            tools=[hermes_tool],
            tool_choice={"type": "tool", "name": "hermes_decision"},
            messages=[{"role": "user", "content": user_msg}],
        )

        tool_result: dict | None = None
        for block in resp.content:
            if block.type == "tool_use" and block.name == "hermes_decision":
                tool_result = block.input
                break

        if not tool_result:
            log.warning("hermes: sin tool_use en respuesta — fail-open [%s]", area_name)
            return {"aprobado": True, "motivo": "hermes_no_tool_output", "fallback": True}

        result = {
            "aprobado":  bool(tool_result.get("aprobado", True)),
            "anomalias": tool_result.get("anomalias", []),
            "motivo":    str(tool_result.get("motivo", "")),
            "confianza": float(tool_result.get("confianza", 0.8)),
        }

        usage = resp.usage
        log.info(
            "hermes [%s] aprobado=%s | %s | tokens in=%d out=%d cache_read=%d",
            area_name, result["aprobado"], result["motivo"],
            usage.input_tokens, usage.output_tokens,
            getattr(usage, "cache_read_input_tokens", 0),
        )

        if not result["aprobado"]:
            _log_flag(area_name=area_name, digest=digest, mes=mes, result=result)
            raise CertificateBlocked(
                motivo=result["motivo"],
                anomalias=result["anomalias"],
                confianza=result["confianza"],
            )

        return result

    except CertificateBlocked:
        raise
    except Exception as exc:
        log.warning("hermes: error Claude API — fail-open [%s]: %s", area_name, exc)
        return {"aprobado": True, "motivo": f"hermes_error:{exc}", "fallback": True}


# ── Persistencia ──────────────────────────────────────────────────────────────

def _log_flag(area_name: str, digest: str, mes: int, result: dict) -> None:
    """Inserta bloqueo en hermes_flags vía Supabase REST. No bloquea si falla."""
    supa_url = os.environ.get("SUPABASE_URL", "")
    supa_key = os.environ.get("SUPABASE_KEY", "")
    if not supa_url or not supa_key:
        log.warning("hermes: Supabase no configurado — flag no persistido [%s]", area_name)
        return

    try:
        import requests
        row = {
            "area_name": area_name,
            "sha256":    digest,
            "mes":       mes,
            "anomalias": result.get("anomalias", []),
            "motivo":    result.get("motivo", ""),
            "confianza": result.get("confianza", 0.0),
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        url  = f"{supa_url}/rest/v1/{_SUPABASE_TABLE}"
        hdrs = {
            "apikey":        supa_key,
            "Authorization": f"Bearer {supa_key}",
            "Content-Type":  "application/json",
            "Prefer":        "return=minimal",
        }
        r = requests.post(url, headers=hdrs, data=json.dumps(row, default=str), timeout=10)
        if r.status_code in (200, 201, 204):
            log.info("hermes: flag guardado — área=%s sha=%s...", area_name, digest[:16])
        else:
            log.warning(
                "hermes: Supabase hermes_flags HTTP %s: %s",
                r.status_code, r.text[:200],
            )
    except Exception as exc:
        log.warning("hermes: no se pudo guardar flag: %s", exc)
