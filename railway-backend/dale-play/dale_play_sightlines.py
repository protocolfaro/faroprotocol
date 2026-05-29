"""
dale_play_sightlines.py — C-value ISO/FIFA por sector de tribuna.

Fórmula de análisis (no recurrente):
    C_n = H_n - H_{n-1} × (d_n / d_{n-1})

donde d_n = distancia fila n → PoF (campo nivel 0) y H_n = altura ojo sobre campo.
PoF = nivel del campo (z=0). Si se puede ver el campo, se puede ver el escenario.
Positivo = el ojo de la fila n supera la línea de visión desde PoF a través del ojo n-1.

Umbrales FIFA: adecuado ≥ 60mm · bueno ≥ 90mm · excelente ≥ 120mm.
"""
from __future__ import annotations
import json, logging, math, pathlib
import numpy as np

log = logging.getLogger(__name__)

EYE_HEIGHT  = 1.2    # m sobre nivel del asiento
ROW_DEPTH   = 0.80   # m profundidad típica de asiento + legroom
C_ADEQUATE  = 0.060
C_GOOD      = 0.090
C_EXCELLENT = 0.120

# Amalfitani — alturas del primer asiento sobre el campo (m)
# Norte/Sur: tribuna principal, primer asiento prácticamente a nivel campo
# Este/Oeste: platea baja, ligero escalón
CONCOURSE_H = {"norte": 0.5, "sur": 0.5, "este": 0.3, "oeste": 0.3}

# Ángulos de rake reales (grados) — medidos en planta del estadio
RAKE_DEG = {"norte": 28.0, "sur": 26.0, "este": 24.0, "oeste": 24.0}


def _row_heights(nombre: str, n_filas: int) -> list[float]:
    """
    Alturas del OJO de cada fila sobre el nivel del campo.
    h_i = concourse_h + i * ROW_DEPTH * tan(rake) + EYE_HEIGHT
    """
    h0   = CONCOURSE_H[nombre]
    rake = math.radians(RAKE_DEG[nombre])
    rise = ROW_DEPTH * math.tan(rake)          # m de altura ganada por fila
    return [h0 + i * rise + EYE_HEIGHT for i in range(n_filas)]


def _row_distances_norte(cx: float, cy: float, n_filas: int, stage_utm: dict) -> list[float]:
    """Distancias horizontales Norte → frente del escenario."""
    stage_y = stage_utm["y"]
    # Primera fila Norte: cy + PITCH_L/2 + TRACK_W
    y0 = cy + 52.5 + 8.0        # primera fila (60.5m del centro)
    return [abs(y0 + i * ROW_DEPTH - stage_y) for i in range(n_filas)]


def _row_distances_sur(cx: float, cy: float, n_filas: int, stage_utm: dict) -> list[float]:
    """Sur está detrás del escenario — distancias desde filas Sur al PoF sur."""
    stage_y = stage_utm["y"]
    y0 = cy - 52.5 - 8.0        # primera fila Sur
    return [abs(y0 - i * ROW_DEPTH - stage_y) for i in range(n_filas)]


def _row_distances_lateral(cx: float, cy: float, nombre: str,
                            n_filas: int, stage_utm: dict) -> list[float]:
    """Este/Oeste — distancia lateral desde la tribuna al escenario (centro)."""
    stage_x = stage_utm["x"]
    if nombre == "este":
        x0 = cx + 34.0 + 8.0    # primera fila Este (cx + PITCH_W/2 + TRACK_W)
        return [abs(x0 + i * ROW_DEPTH - stage_x) for i in range(n_filas)]
    else:
        x0 = cx - 34.0 - 8.0
        return [abs(x0 - i * ROW_DEPTH - stage_x) for i in range(n_filas)]


def _c_values(d_rows: list[float], h_rows: list[float]) -> list[float]:
    """
    C-value de análisis (no recurrente): cuánto supera el ojo de la fila n
    la línea de visión desde el PoF a través del ojo de la fila n-1.
    C_n = H_n - H_{n-1} × (d_n / d_{n-1})
    """
    c = [0.0]
    for n in range(1, len(d_rows)):
        cn = h_rows[n] - h_rows[n-1] * (d_rows[n] / d_rows[n-1])
        c.append(cn)
    return c


def _stats(c_vals: list[float]) -> dict:
    n = len(c_vals)
    if n == 0:
        return {}
    return {
        "adecuada_pct":  round(sum(c >= C_ADEQUATE  for c in c_vals) / n * 100, 1),
        "buena_pct":     round(sum(c >= C_GOOD       for c in c_vals) / n * 100, 1),
        "excelente_pct": round(sum(c >= C_EXCELLENT  for c in c_vals) / n * 100, 1),
        "c_min_m":       round(min(c_vals), 4),
        "c_max_m":       round(max(c_vals), 4),
        "c_prom_m":      round(float(np.mean(c_vals)), 4),
        "n_filas":       n,
    }


def analyze_sightlines(model: dict, stage_override: dict | None = None) -> dict:
    stage = dict(model["escenario"])
    if stage_override:
        if "altura_m" in stage_override:
            stage["altura_m"] = stage_override["altura_m"]
        if "despl_norte_m" in stage_override:
            stage["utm"] = {
                "x": stage["utm"]["x"],
                "y": stage["utm"]["y"] + stage_override["despl_norte_m"],
            }

    cx   = model["utm_centro"]["x"]
    cy   = model["utm_centro"]["y"]
    sut  = stage["utm"]
    n_tr = {k: v["n_filas"] for k, v in model["tribunas"].items()}

    resultados = {}
    heatmaps   = {}

    for nombre in ("norte", "sur", "este", "oeste"):
        nf = n_tr[nombre]
        h_rows = _row_heights(nombre, nf)

        if nombre == "norte":
            d_rows = _row_distances_norte(cx, cy, nf, sut)
            obstr  = False
        elif nombre == "sur":
            d_rows = _row_distances_sur(cx, cy, nf, sut)
            # Sur está detrás del escenario — sightlines comprometidas
            obstr  = True
        else:
            d_rows = _row_distances_lateral(cx, cy, nombre, nf, sut)
            obstr  = False

        # Distancias mínimas de 1m para evitar división por cero
        d_rows = [max(d, 1.0) for d in d_rows]
        c_vals = _c_values(d_rows, h_rows)

        vis = _stats(c_vals)
        if obstr:
            vis["nota"] = "Tribuna Sur detrás del escenario — sightline al performer obstruida"

        resultados[nombre] = {
            "visibilidad":              vis,
            "obstruida_por_escenario":  obstr,
            "rake_deg":                 RAKE_DEG[nombre],
            "h_primera_fila_m":         round(h_rows[0], 3),
            "h_ultima_fila_m":          round(h_rows[-1], 3),
            "d_primera_fila_m":         round(d_rows[0], 1),
            "d_ultima_fila_m":          round(d_rows[-1], 1),
        }
        # Heatmap: C-value por fila normalizado [0,1] para visualización
        c_arr = np.array(c_vals)
        c_norm = np.clip((c_arr - C_ADEQUATE) / (C_EXCELLENT - C_ADEQUATE), 0, 1)
        heatmaps[nombre] = {
            "c_values_m":    [round(c, 4) for c in c_vals],
            "c_norm_0_1":    [round(float(x), 3) for x in c_norm],
        }

    # Resumen — Norte/Este/Oeste (Sur tiene sightline comprometida en concierto)
    frontales = ["norte", "este", "oeste"]
    resumen = {
        "visibilidad_global_adecuada_pct": round(float(np.mean(
            [resultados[t]["visibilidad"]["adecuada_pct"] for t in frontales])), 1),
        "visibilidad_global_buena_pct": round(float(np.mean(
            [resultados[t]["visibilidad"]["buena_pct"] for t in frontales])), 1),
        "visibilidad_global_excelente_pct": round(float(np.mean(
            [resultados[t]["visibilidad"]["excelente_pct"] for t in frontales])), 1),
        "escenario_altura_m":  stage["altura_m"],
        "escenario_utm_y":     sut["y"],
        "nota":                "Sur excluido del promedio (detrás del escenario).",
    }

    return {
        "tribunas":   resultados,
        "heatmaps":   heatmaps,
        "resumen":    resumen,
        "parametros": {
            "eye_height_m":  EYE_HEIGHT,
            "row_depth_m":   ROW_DEPTH,
            "pof":           "Nivel del campo (z=0) — visible implica ver escenario",
            "c_adequate_m":  C_ADEQUATE,
            "c_good_m":      C_GOOD,
            "c_excellent_m": C_EXCELLENT,
        },
    }


def run_and_save(model: dict, stage_override: dict | None = None,
                 filename: str = "sightlines_airbag.json") -> dict:
    result = analyze_sightlines(model, stage_override)
    out = pathlib.Path(__file__).parent / "models" / filename
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    log.info("Sightlines saved → %s", out)
    return result
