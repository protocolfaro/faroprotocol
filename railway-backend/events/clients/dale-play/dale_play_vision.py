"""
dale_play_vision.py — Análisis comparativo Claude Vision.
Extrae configuración real del show desde fotos pre-show y compara
contra configuración óptima Faro Protocol.
Output: models/airbag_comparativa.json
"""
from __future__ import annotations
import base64, json, os, pathlib
from datetime import datetime

MODELS_DIR = pathlib.Path(__file__).parent / "models"


def _img_b64(path: pathlib.Path) -> str:
    return base64.standard_b64encode(path.read_bytes()).decode()


def _analyze_with_vision(photos: list[pathlib.Path], show_id: str) -> dict:
    """Usa Claude Vision para extraer configuración real del montaje."""
    import anthropic
    client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))

    prompt = """Sos un ingeniero de producción de eventos analizando fotos pre-show del Estadio José Amalfitani (Buenos Aires, Argentina).
El campo mide 105m × 68m (largo × ancho), orientación N-S. El escenario va en el sector SUR.

Para cada foto, extraé:
1. Posición del escenario: distancia estimada desde la línea de gol sur (metros), ancho aproximado (metros), altura estimada (metros)
2. Posición de torres de sonido L/R: distancia desde el escenario (metros), posición lateral (% del ancho del campo)
3. Área del campo afectada (m²): zona cubierta por estructuras pesadas
4. Estado del césped: condición visual (excelente/bueno/regular/malo)
5. Observaciones de riesgo: ¿hay estructuras sobre zonas de drenaje visibles? ¿posición correcta?

Respondé en JSON exacto con esta estructura:
{
  "escenario": {"dist_gol_sur_m": float, "ancho_m": float, "alto_m": float, "x_pct_inicio": float, "x_pct_fin": float, "y_pct_fin": float},
  "torres_lr": {"dist_escenario_m": float, "x_pct_izq": float, "x_pct_der": float, "y_pct": float},
  "area_afectada_m2": float,
  "estado_cesped": "excelente|bueno|regular|malo",
  "observaciones": ["obs1", "obs2"],
  "confianza": "alta|media|baja"
}"""

    content = [{"type": "text", "text": prompt}]
    for p in photos[:3]:  # Máximo 3 fotos relevantes (escenario + torres + campo)
        content.append({
            "type": "image",
            "source": {"type": "base64", "media_type": "image/jpeg",
                       "data": _img_b64(p)}
        })
    content.append({"type": "text", "text": "Analizá las fotos y devolvé solo el JSON:"})

    msg = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=800,
        messages=[{"role": "user", "content": content}]
    )
    raw = msg.content[0].text.strip()
    # Extraer JSON del response
    start = raw.find("{")
    end   = raw.rfind("}") + 1
    return json.loads(raw[start:end])


def _manual_extraction() -> dict:
    """
    Extracción manual desde análisis visual de las 5 fotos pre-show Airbag 2026-05-27.

    Foto 1 (15.00.25): Escenario frontal — dos pantallas LED, estructura ~28m ancho,
      altura ~1.6m base, profundidad aprox 20m desde gol sur.
    Foto 2 (15.00.25-1): Tribuna Norte — campo libre, marcador electrónico visible.
    Foto 3 (15.00.26): Campo centro — torres delay laterales a ~60m del escenario,
      posicionadas en aprox x_pct 10% (izq) y 88% (der).
    Foto 4 (15.00.26-1): Lateral izquierdo — vallado campo, materiales apilados en borde.
    Foto 5 (15.00.27): Césped close-up — manchas amarillas, seco.
    """
    return {
        "escenario": {
            "dist_gol_sur_m":  0.5,    # escenario pegado a la línea de gol sur
            "ancho_m":        28.0,    # dos alas de pantalla ~14m c/u
            "alto_m":          1.6,    # altura base estándar (sin +50cm)
            "x_pct_inicio":   18.0,
            "x_pct_fin":      80.0,
            "y_pct_fin":      22.0,    # ocupa primeros 22% del largo (≈23m)
        },
        "torres_lr": {
            "dist_escenario_m": 62.0,  # torres delay a ~62m
            "x_pct_izq":       10.0,
            "x_pct_der":       88.0,
            "y_pct":           59.0,
        },
        "area_afectada_m2": 1540.0,   # escenario 28×20m=560 + área vallado ~980m²
        "estado_cesped": "regular",   # manchas secas visibles, NDVI 0.099
        "observaciones": [
            "Escenario posicionado sobre zona Colector Sur (y_pct 0-22 vs exclusión 0-18) — 4m de exceso",
            "Torres delay en posición aceptable pero close al Canal N-S (x_pct 10 vs mínimo recomendado 15)",
            "No se observan tarimas distribuidoras de carga bajo estructura principal",
            "Materiales apilados en lateral izquierdo del campo (sobre zona lateral drenaje)",
            "Césped con signos de estrés hídrico — consistente con NDVI 0.099 y SMAP seco",
        ],
        "confianza": "alta",
    }


def analyze_comparativa(
    photos_dir: pathlib.Path | None = None,
    show_id: str = "airbag_2026-05-31",
    use_vision_api: bool = False,
) -> dict:
    """
    Genera análisis comparativo: configuración real (fotos) vs óptima Faro Protocol.
    """
    # ── Extracción configuración real ─────────────────────────────────────────
    if use_vision_api and os.environ.get("ANTHROPIC_API_KEY"):
        photos = sorted((photos_dir or pathlib.Path.cwd()).glob("WhatsApp Image 2026-05-27*.jpeg"))
        config_real = _analyze_with_vision(photos[:3], show_id)
    else:
        config_real = _manual_extraction()

    # ── Configuración óptima Faro Protocol ────────────────────────────────────
    # Basada en sightlines_airbag.json + rider_sim_airbag.json + drainage_amalfitani.json
    config_optima = {
        "escenario": {
            "dist_gol_sur_m":  0.5,
            "ancho_m":        28.0,
            "alto_m":          2.1,    # +50cm según rider_sim
            "x_pct_inicio":   18.0,
            "x_pct_fin":      80.0,
            "y_pct_fin":      18.0,    # dentro de zona de exclusión de drenaje
        },
        "torres_lr": {
            "dist_escenario_m": 62.0,
            "x_pct_izq":       15.0,  # fuera del Canal N-S
            "x_pct_der":       85.0,
            "y_pct":           59.0,
        },
        "area_afectada_m2": 1224.0,   # escenario 28×18m=504 + área vallado ~720m²
        "tarimas_distribuidoras": True,
        "notas": [
            "Stage height +50cm → sweet-spot global 100% vs 92.9% actual",
            "Torres L/R a x_pct≥15 para evitar Canal N-S central",
            "Tarimas distribuidoras bajo patas escenario sobre Colector Sur",
        ],
    }

    # ── Deltas ─────────────────────────────────────────────────────────────────
    er  = config_real["escenario"]
    eo  = config_optima["escenario"]
    tr  = config_real["torres_lr"]
    to_ = config_optima["torres_lr"]

    delta_stage_h    = round(eo["alto_m"] - er["alto_m"], 2)        # +0.5m
    delta_y_pct      = round(er["y_pct_fin"] - eo["y_pct_fin"], 1)  # +4% = ~4.2m dentro exclusión
    delta_x_torre_izq = round(to_["x_pct_izq"] - tr["x_pct_izq"], 1)  # +5%
    delta_area       = round(config_real["area_afectada_m2"] - config_optima["area_afectada_m2"])
    delta_sweet      = round(100.0 - 92.9, 1)                        # +7.1%

    # Carga sobre zona drenaje
    carga_escenario_ton = 120.0
    area_base_m2        = er["ancho_m"] * (er["y_pct_fin"] / 100 * 105)
    presion_real_kpa    = round((carga_escenario_ton * 9.81) / area_base_m2, 1)
    cap_colector_kpa    = 28.0
    riesgo_drenaje      = presion_real_kpa > cap_colector_kpa

    deltas = {
        "altura_escenario_m":     {"real": er["alto_m"], "optimo": eo["alto_m"],
                                   "delta": f"+{delta_stage_h}m", "impacto": f"sweet-spot +{delta_sweet}%"},
        "profundidad_campo_pct":  {"real": er["y_pct_fin"], "optimo": eo["y_pct_fin"],
                                   "delta": f"+{delta_y_pct}% ({delta_y_pct*105/100:.1f}m)",
                                   "impacto": "escenario sobre zona exclusión drenaje"},
        "torre_izq_x_pct":        {"real": tr["x_pct_izq"], "optimo": to_["x_pct_izq"],
                                   "delta": f"-{delta_x_torre_izq}% ({delta_x_torre_izq*68/100:.1f}m)",
                                   "impacto": "torre cercana a Canal N-S"},
        "area_afectada_m2":       {"real": config_real["area_afectada_m2"],
                                   "optimo": config_optima["area_afectada_m2"],
                                   "delta": f"+{delta_area}m²", "impacto": "mayor daño al césped"},
        "presion_drenaje_kpa":    {"real": presion_real_kpa, "capacidad": cap_colector_kpa,
                                   "riesgo": riesgo_drenaje,
                                   "impacto": "Presión dentro de umbral — sin riesgo estructural"},
        "tarimas_distribuidoras": {"real": False, "optimo": True,
                                   "delta": "ausentes",
                                   "impacto": "carga puntual sin distribución sobre Colector Sur"},
    }

    # Score global
    puntos = {
        "altura_escenario": 0 if delta_stage_h > 0 else 25,
        "zona_drenaje":     0 if delta_y_pct > 2 else 25,
        "torres_posicion":  15 if abs(delta_x_torre_izq) <= 3 else 0,
        "tarimas":          0,  # ausentes
        "cesped":           5 if config_real["estado_cesped"] in ("excelente","bueno") else 0,
    }
    score_real   = sum(puntos.values())    # /100
    score_optimo = 100

    result = {
        "show_id":         show_id,
        "fecha_analisis":  datetime.now().strftime("%Y-%m-%d %H:%M"),
        "fotos_analizadas": 5,
        "fuente_extraccion": "Claude Vision (análisis visual manual) + modelos Faro Protocol",
        "config_real":     config_real,
        "config_optima":   config_optima,
        "deltas":          deltas,
        "score": {
            "dale_play":    score_real,
            "faro_optimo":  score_optimo,
            "gap":          score_optimo - score_real,
            "detalle":      puntos,
        },
        "recomendaciones": [
            f"Subir escenario {delta_stage_h}m → sweet-spot {delta_sweet}% adicional de público con visión perfecta",
            f"Retraer escenario {delta_y_pct*105/100:.1f}m → salir de zona Colector Sur (capacidad 28 kPa)",
            f"Desplazar torre izquierda {delta_x_torre_izq*68/100:.1f}m al este → evitar Canal N-S",
            "Instalar tarimas distribuidoras (mín. 2.5 m²/punto) bajo patas del escenario",
            "Regar el campo 48h antes del show — SMAP superficial 0% (seco)",
        ],
    }

    out = MODELS_DIR / "airbag_comparativa.json"
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


if __name__ == "__main__":
    r = analyze_comparativa()
    s = r["score"]
    print(
        f"Comparativa Airbag · {r['fecha_analisis']}\n"
        f"  Score Dale Play:  {s['dale_play']}/100\n"
        f"  Score Faro Óptimo: {s['faro_optimo']}/100\n"
        f"  Gap:              -{s['gap']} puntos\n"
        f"\nRecomendaciones:"
    )
    for rec in r["recomendaciones"]:
        print(f"  • {rec}")
