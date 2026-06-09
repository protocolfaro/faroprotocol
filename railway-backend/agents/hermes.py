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
import sys
from datetime import date, datetime, timedelta, timezone

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


# ── hermes_consolidate ────────────────────────────────────────────────────────

def hermes_consolidate(venue_id: str, cancha_id: str | None = None) -> dict:
    """
    Lee las 4 tablas del data lake y consolida una vista agronómica unificada.

    Ponderación temporal óptica:
      dias_antiguedad < 7   → peso 1.0
      7 ≤ dias ≤ 15         → peso 0.4
      dias > 15             → excluido

    Corrección turca (soil moisture drift):
      humedad_hoy = theta_soil - ET0_acumulada_desde_SAR / root_zone_mm
      root_zone = 300 mm (césped)

    Returns dict: soil, vegetation, climate, intervenciones_48h,
                  humedad_estimada, confianza_consolidada, fuentes_activas, alertas
    """
    _ts = datetime.now(timezone.utc).isoformat()
    out: dict = {
        "venue_id":              venue_id,
        "cancha_id":             cancha_id,
        "soil":                  None,
        "vegetation":            None,
        "climate":               None,
        "intervenciones_48h":    [],
        "humedad_estimada":      None,
        "confianza_consolidada": 0.10,
        "fuentes_activas":       [],
        "alertas":               [],
        "ts":                    _ts,
    }

    # ── import velez_supabase ─────────────────────────────────────────────
    try:
        _here = os.path.dirname(os.path.abspath(__file__))
        _vs_path = os.path.join(_here, "..", "sports", "clients", "velez")
        if _vs_path not in sys.path:
            sys.path.insert(0, _vs_path)
        import velez_supabase as _vs
        if not _vs._ok():
            log.warning("hermes_consolidate: Supabase no disponible — retornando vacío")
            return out
    except Exception as _imp:
        log.warning("hermes_consolidate: import error: %s", _imp)
        return out

    conf = 0.10

    # ── 1. soil_metrics (SAR) ─────────────────────────────────────────────
    soil_rows = _vs.get_soil_metrics_latest(venue_id, cancha_id, dias=14)
    if soil_rows:
        soil = soil_rows[0]
        out["soil"] = soil
        out["fuentes_activas"].append("SAR")
        try:
            soil_age = (date.today() - date.fromisoformat(str(soil["fecha_imagen"]))).days
        except Exception:
            soil_age = 99
        conf += 0.30 if soil_age < 7 else 0.15

    # ── 2. vegetation_metrics (óptico) — ponderación por margen Kalman o antigüedad ──
    veg_rows = _vs.get_vegetation_metrics_latest(venue_id, cancha_id, dias=15)
    if veg_rows:
        _fields = ("ndvi", "gndvi", "evi2", "bsi", "ndwi")
        acc: dict[str, float] = {f: 0.0 for f in _fields}
        total_w = 0.0
        conf_sum, conf_n = 0.0, 0
        _uses_kalman = False
        for row in veg_rows:
            margen = row.get("margen_error_kalman")
            if margen is not None:
                # Kalman disponible — reemplaza lógica de días
                _uses_kalman = True
                m = float(margen)
                if m < 0.05:
                    w = 1.0       # confianza 100%
                elif m <= 0.15:
                    w = 0.6       # confianza 60%
                else:
                    continue      # confianza 0% — excluir fila, activar física
            else:
                # Sin Kalman — lógica días de antigüedad original
                d = row.get("dias_antiguedad")
                if d is None:
                    try:
                        d = (date.today() - date.fromisoformat(str(row["fecha_imagen"]))).days
                    except Exception:
                        d = 999
                if d > 15:
                    continue
                w = 1.0 if d < 7 else 0.4
            for f in _fields:
                v = row.get(f)
                if v is not None:
                    acc[f] += float(v) * w
            total_w += w
            cp = row.get("confianza_pct")
            if cp is not None:
                conf_sum += float(cp)
                conf_n += 1
        if total_w > 0:
            veg_out = {f: round(acc[f] / total_w, 3) for f in _fields if acc[f] != 0.0}
            if conf_n > 0:
                veg_out["confianza_pct"] = round(conf_sum / conf_n)
            veg_out["optical_weight"] = round(min(total_w / max(len(veg_rows), 1), 1.0), 2)
            veg_out["usa_kalman"] = _uses_kalman
            out["vegetation"] = veg_out
            out["fuentes_activas"].append("Kalman/LSTM" if _uses_kalman else "Óptico/S2")
            conf += 0.30 * veg_out["optical_weight"]
        elif any(row.get("margen_error_kalman", 0) or 0 > 0.15 for row in veg_rows):
            # Todas las filas con margen > 0.15 → activar física de suelos como señal
            out["alertas"].append("kalman_incertidumbre_alta: margen>0.15 — usando física de suelos")

    # ── 3. climate_metrics ────────────────────────────────────────────────
    clim_rows = _vs.get_climate_metrics_latest(venue_id, dias=7)
    if clim_rows:
        out["climate"] = clim_rows[0]
        out["fuentes_activas"].append("Clima/NASA")
        try:
            clim_age = (
                date.today()
                - date.fromisoformat(str(clim_rows[0].get("fecha") or
                                        clim_rows[0].get("created_at", ""))[:10])
            ).days
            conf += 0.20 if clim_age < 2 else 0.10
        except Exception:
            conf += 0.10

    # ── 4. intervenciones 48h ─────────────────────────────────────────────
    try:
        _cid_int = cancha_id or venue_id
        int_rows = _vs.get_intervenciones_recientes(_cid_int, dias=2)
        out["intervenciones_48h"] = int_rows
        if int_rows:
            out["fuentes_activas"].append("Intervenciones")
    except Exception as _ie:
        log.debug("hermes_consolidate: intervenciones (non-fatal): %s", _ie)

    # ── 5. Corrección turca — theta ajustado por ET0 acumulada ───────────
    _soil = out["soil"]
    if _soil and _soil.get("theta_soil") is not None:
        theta = float(_soil["theta_soil"])
        humedad = theta
        if clim_rows and _soil.get("fecha_imagen"):
            try:
                sar_date = date.fromisoformat(str(_soil["fecha_imagen"]))
                et0_acc = 0.0
                for cr in clim_rows:
                    cr_date_str = str(cr.get("fecha") or cr.get("created_at", ""))[:10]
                    cr_date = date.fromisoformat(cr_date_str)
                    if cr_date >= sar_date:
                        et0_acc += float(cr.get("et0_mm_dia") or 0.0)
                # 1 mm ET0 over 300 mm root zone → 0.00333 m³/m³ depletion
                depletion = et0_acc / 300.0
                humedad = max(0.046, round(theta - depletion, 4))
                out["_et0_acumulada_mm"] = round(et0_acc, 1)
                out["_theta_sar"] = theta
            except Exception as _te:
                log.debug("hermes_consolidate: corrección turca: %s", _te)
        out["humedad_estimada"] = humedad

    # ── 6. Alertas automáticas ────────────────────────────────────────────
    _hum = out["humedad_estimada"]
    if _hum is not None and _hum < 0.15:
        out["alertas"].append(f"riesgo_sequia: humedad_estimada {_hum:.3f} m³/m³ < 0.15")

    _clim = out["climate"] or {}
    _sk = _clim.get("smith_kerns_pct")
    if _sk is not None and float(_sk) > 50.0:
        out["alertas"].append(f"riesgo_dollar_spot: Smith-Kerns {float(_sk):.1f}%")

    _veg = out["vegetation"] or {}
    _ndvi = _veg.get("ndvi")
    if _ndvi is not None:
        _mes = datetime.now().month
        if _mes in (6, 7, 8) and _ndvi > 0.60:
            out["alertas"].append(
                f"ndvi_invierno_alto: NDVI {_ndvi:.3f} > 0.60 en invierno austral"
            )
        if _mes in (12, 1, 2) and _ndvi < 0.25:
            out["alertas"].append(
                f"ndvi_verano_bajo: NDVI {_ndvi:.3f} < 0.25 en verano"
            )

    # Compactación: BSI alto sin aireación en 14d
    if _veg.get("bsi") is not None and float(_veg["bsi"]) > 0.30:
        try:
            _all_int = _vs.get_intervenciones_recientes(cancha_id or venue_id, dias=14)
            if "aireacion" not in {iv.get("tipo") for iv in _all_int}:
                out["alertas"].append(
                    f"compactacion: BSI {_veg['bsi']:.3f} > 0.30 sin aireación en 14d"
                )
        except Exception:
            pass

    out["confianza_consolidada"] = round(min(conf, 1.0), 3)

    log.info(
        "hermes_consolidate [%s/%s] conf=%.2f fuentes=%s alertas=%d",
        venue_id, cancha_id, out["confianza_consolidada"],
        out["fuentes_activas"], len(out["alertas"]),
    )
    return out
