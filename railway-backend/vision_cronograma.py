"""
vision_cronograma.py — Parse weekly schedule image using Claude Vision API
Faro Protocol · Vélez Sarsfield
"""
from __future__ import annotations
import base64, json, logging, os
from datetime import date, timedelta

log = logging.getLogger(__name__)

_DIA_OFFSET = {
    "lunes": 0, "martes": 1, "miercoles": 2, "jueves": 3,
    "viernes": 4, "sabado": 5, "domingo": 6,
}
_DIA_LABEL = {0:"Lun",1:"Mar",2:"Mié",3:"Jue",4:"Vie",5:"Sáb",6:"Dom"}

SYSTEM_PROMPT = """Sos un asistente especializado en leer grillas de cronogramas deportivos.
Recibís una imagen de un cronograma semanal de canchas del Club Atlético Vélez Sarsfield.
Extraé los datos de uso de canchas y devolvé ÚNICAMENTE JSON válido sin markdown.

Convenciones del cronograma:
- Días: Lunes a Sábado (a veces Domingo)
- Franjas horarias → turnos:
    * "mañana" / AM / antes del mediodía → "manana"
    * "tarde" / infantiles / 12-17hs       → "infantiles"
    * "noche" / femenino / desde 17:30hs  → "femenino"
- Canchas: números "1"–"10" son Amalfitani (ids: "1fa"–"10fa"), "1FP"/"2FP" son Villa Olímpica (ids: "1fp"/"2fp")
- Cuando una cancha aparece en una franja = está OCUPADA ese turno

Formato de respuesta (sin comentarios, sin markdown):
{
  "dias_semana": ["lunes", "martes", "miercoles", "jueves", "viernes", "sabado"],
  "sessions": [
    {"dia": "lunes",   "turno": "manana",     "canchas": ["1fa", "2fa"]},
    {"dia": "lunes",   "turno": "infantiles",  "canchas": ["3fa", "4fa"]},
    {"dia": "martes",  "turno": "femenino",    "canchas": ["1fp"]}
  ]
}

Reglas estrictas:
- "turno" solo puede ser: "manana", "infantiles" o "femenino"
- "dia" en sessions debe coincidir exactamente con los valores en "dias_semana"
- Grafías exactas sin acento: "lunes", "martes", "miercoles", "jueves", "viernes", "sabado", "domingo"
- "canchas" en formato lowercase: "1fa"–"10fa", "1fp", "2fp"
- Solo incluir sessions donde hay canchas asignadas (uso real)
- Si ves campus, estadio u otros espacios no numerados, ignorarlos
"""


def _current_week_monday() -> date:
    today = date.today()
    return today - timedelta(days=today.weekday())


def _build_dias_array(dias_semana: list, reference_monday: date | None = None) -> list:
    monday = reference_monday or _current_week_monday()
    result = []
    for dia in dias_semana:
        dia_norm = dia.lower().strip()
        # Strip accents for lookup
        dia_key = (dia_norm
                   .replace("é", "e").replace("á", "a")
                   .replace("ó", "o").replace("ú", "u").replace("í", "i"))
        offset = _DIA_OFFSET.get(dia_key) if dia_key in _DIA_OFFSET else _DIA_OFFSET.get(dia_norm)
        if offset is None:
            log.warning("build_dias: unrecognized day '%s'", dia)
            continue
        d = monday + timedelta(days=offset)
        result.append({
            "id":    dia_key,
            "label": _DIA_LABEL.get(offset, dia[:3].capitalize()),
            "fecha": d.isoformat(),
        })
    return result


def _normalize_cancha(raw: str) -> str:
    """'1' → '1fa', '1fp' → '1fp', '10' → '10fa', etc."""
    s = raw.lower().strip()
    if s.endswith("fa") or s.endswith("fp"):
        return s
    if s.isdigit():
        return s + "fa"
    return s


def parse_cronograma(image_bytes: bytes, mime_type: str = "image/jpeg") -> dict:
    """
    Pass image to Claude Vision API, return structured cronograma dict.
    Returns: {sessions: [...], dias: [...], semana_label: str}
    Raises EnvironmentError if ANTHROPIC_API_KEY is missing.
    Raises RuntimeError on API or parse failure.
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        raise EnvironmentError("ANTHROPIC_API_KEY no configurado en Railway")

    try:
        import anthropic
    except ImportError:
        raise RuntimeError("anthropic SDK no instalado")

    client = anthropic.Anthropic(api_key=api_key, timeout=45.0)
    b64 = base64.standard_b64encode(image_bytes).decode("utf-8")

    log.info("Sending cronograma image to Claude Vision (%d KB, %s)", len(image_bytes) // 1024, mime_type)

    try:
        message = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=2048,
            system=SYSTEM_PROMPT,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": mime_type,
                                "data": b64,
                            },
                        },
                        {
                            "type": "text",
                            "text": "Analizá este cronograma semanal de Vélez y devolvé el JSON estructurado con los días y franjas de uso de canchas.",
                        },
                    ],
                }
            ],
        )
    except anthropic.APITimeoutError as e:
        log.warning("Claude Vision API timeout after 45s: %s", e)
        raise TimeoutError("Claude Vision API no respondió en 45 segundos")

    raw = message.content[0].text.strip()
    log.info("Vision API response (first 400 chars): %s", raw[:400])

    # Strip markdown code fences if present
    if raw.startswith("```"):
        lines = raw.split("\n")
        raw = "\n".join(lines[1:] if not lines[-1].strip("`") else lines[1:-1])

    try:
        parsed = json.loads(raw)
    except Exception as e:
        log.error("Vision API JSON parse error: %s — raw: %s", e, raw[:500])
        raise RuntimeError(f"Vision API devolvió JSON inválido: {e}")

    sessions_raw = parsed.get("sessions", [])
    dias_semana  = parsed.get("dias_semana", [])

    # Default to Mon-Sat if Claude omitted dias_semana
    if not dias_semana:
        dias_semana = ["lunes", "martes", "miercoles", "jueves", "viernes", "sabado"]

    # Normalize sessions
    clean_sessions = []
    valid_dias = {d.lower().strip() for d in dias_semana}
    for s in sessions_raw:
        dia   = (s.get("dia") or "").lower().strip()
        turno = (s.get("turno") or "").lower().strip()
        if turno not in ("manana", "infantiles", "femenino"):
            log.warning("Skipping session: unknown turno '%s'", turno)
            continue
        canchas = [_normalize_cancha(c) for c in (s.get("canchas") or []) if c]
        if not dia or not canchas:
            continue
        clean_sessions.append({"dia": dia, "turno": turno, "canchas": canchas})

    dias = _build_dias_array(dias_semana)

    monday = _current_week_monday()
    iso_cal = monday.isocalendar()
    semana_label = f"{iso_cal[0]}-W{iso_cal[1]:02d}"

    result = {
        "sessions":     clean_sessions,
        "dias":         dias,
        "semana_label": semana_label,
    }
    log.info("Cronograma parsed OK: %d sessions, %d dias, semana=%s",
             len(clean_sessions), len(dias), semana_label)
    return result
