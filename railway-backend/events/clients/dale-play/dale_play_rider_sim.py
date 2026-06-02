"""
dale_play_rider_sim.py — Simulación de variables del rider para Dale Play.

Métricas por configuración:
  - Ángulo de elevación al performer (stage_h + 1.8m) por fila
  - Score de visión óptima: % filas con ángulo ∈ [-5°, +15°] (sweet spot de concierto)
  - Score LED: % filas dentro del cono de ±30° del normal de la pantalla
  - Distancia promedio al frente del escenario (Norte/Este/Oeste)
"""
from __future__ import annotations
import math
import numpy as np

PERFORMER_H = 1.8   # m — altura del performer sobre la plataforma del escenario
EYE_HEIGHT  = 1.2
ROW_DEPTH   = 0.80
CONCOURSE_H = {"norte": 0.5, "sur": 0.5, "este": 0.3, "oeste": 0.3}
RAKE_DEG    = {"norte": 28.0, "sur": 26.0, "este": 24.0, "oeste": 24.0}
STAND_ROWS  = {"norte": 32, "sur": 28, "este": 22, "oeste": 22}

PITCH_L = 105.0
PITCH_W = 68.0
TRACK_W = 8.0


def _eye_heights(nombre: str) -> list[float]:
    h0   = CONCOURSE_H[nombre]
    rake = math.radians(RAKE_DEG[nombre])
    rise = ROW_DEPTH * math.tan(rake)
    nf   = STAND_ROWS[nombre]
    return [h0 + i * rise + EYE_HEIGHT for i in range(nf)]


def _distances_norte(cx, cy, stage_y, nf):
    y0 = cy + PITCH_L/2 + TRACK_W
    return [abs(y0 + i * ROW_DEPTH - stage_y) for i in range(nf)]


def _distances_este(cx, cy, stage_x, nf):
    x0 = cx + PITCH_W/2 + TRACK_W
    return [abs(x0 + i * ROW_DEPTH - stage_x) for i in range(nf)]


def _distances_oeste(cx, cy, stage_x, nf):
    x0 = cx - PITCH_W/2 - TRACK_W
    return [abs(x0 - i * ROW_DEPTH - stage_x) for i in range(nf)]


def simulate_variant(model: dict, stage_h: float, despl_norte: float,
                     led_tilt_deg: float) -> dict:
    """
    Simula una configuración del rider y retorna métricas de calidad.

    stage_h       — altura total del escenario sobre el campo (m)
    despl_norte   — desplazamiento del escenario hacia Norte (m positivo = más al centro/Norte)
    led_tilt_deg  — ángulo de inclinación del LED hacia la audiencia (0=vertical, 15=inclinado)
    """
    cx  = model["utm_centro"]["x"]
    cy  = model["utm_centro"]["y"]
    sx  = model["escenario"]["utm"]["x"]
    sy  = model["escenario"]["utm"]["y"] + despl_norte

    performer_elev = stage_h + PERFORMER_H  # altura del performer sobre el campo (m)

    # LED normal direction: tilt from vertical (0=vertical screen)
    # led_tilt_deg = 0 → screen faces straight up (horizontal viewer at 90° from normal)
    # led_tilt_deg = 15 → screen tilted 15° toward audience
    # A spectator sees the screen optimally when their line-of-sight aligns with screen normal
    # Screen normal angle from horizontal = 90° - led_tilt_deg
    led_normal_elev_deg = 90.0 - led_tilt_deg  # elevation of the normal vector

    sectors = {}
    for nombre in ("norte", "este", "oeste"):
        nf      = STAND_ROWS[nombre]
        h_eyes  = _eye_heights(nombre)

        if nombre == "norte":
            d_rows = _distances_norte(cx, cy, sy, nf)
        elif nombre == "este":
            d_rows = _distances_este(cx, cy, sx, nf)
        else:
            d_rows = _distances_oeste(cx, cy, sx, nf)

        d_rows = [max(d, 1.0) for d in d_rows]

        elev_angles = []
        led_scores  = []
        for h_eye, d in zip(h_eyes, d_rows):
            # Ángulo de elevación al performer
            delta_h = performer_elev - h_eye
            ang = math.degrees(math.atan2(delta_h, d))
            elev_angles.append(ang)

            # Calidad LED: ángulo del espectador respecto al normal de la pantalla
            # spectator looks at screen at elevation = elev_angle_to_screen_center
            # screen normal points at led_normal_elev_deg above horizontal toward audience
            # best viewing when spectator elevation to screen ≈ led_normal_elev_deg - 90°
            # simplificado: diferencia entre ángulo del espectador y ángulo óptimo del LED
            # ángulo óptimo del espectador = -(90° - led_tilt_deg) = led_tilt_deg - 90°...
            # más simple: el LED tilt_deg define el down-tilt; espectadores en ángulo de visión
            # vertical_to_screen = angle from spectator eye to screen center
            # The LED is at stage position, height = stage_h + 4m (LED bottom of screen)
            screen_h = stage_h + 4.0          # LED panel a 4m sobre el escenario
            screen_delta_h = screen_h - h_eye
            screen_ang = math.degrees(math.atan2(screen_delta_h, d))

            # El normal de la pantalla apunta a led_tilt_deg debajo de horizontal
            # Un espectador en ángulo screen_ang ve la pantalla con incidencia:
            # viewing_incidence = |screen_ang - (-led_tilt_deg)|
            viewing_incidence = abs(screen_ang - (-led_tilt_deg))
            # LED calidad: cos del ángulo de incidencia (1=perfecto, 0=de canto)
            led_quality = max(0.0, math.cos(math.radians(viewing_incidence)))
            led_scores.append(led_quality)

        arr      = np.array(elev_angles)
        in_sweet = float(np.mean((arr >= -5.0) & (arr <= 15.0))) * 100
        mean_ang = float(np.mean(arr))
        mean_d   = float(np.mean(d_rows))
        led_pct  = float(np.mean([s > 0.5 for s in led_scores])) * 100

        sectors[nombre] = {
            "sweet_spot_pct":  round(in_sweet, 1),
            "elev_ang_prom":   round(mean_ang, 2),
            "dist_prom_m":     round(mean_d, 1),
            "led_score_pct":   round(led_pct, 1),
            "elev_min":        round(float(np.min(arr)), 2),
            "elev_max":        round(float(np.max(arr)), 2),
        }

    # Score global
    sweet_global = round(np.mean([s["sweet_spot_pct"] for s in sectors.values()]), 1)
    led_global   = round(np.mean([s["led_score_pct"]  for s in sectors.values()]), 1)
    dist_global  = round(np.mean([s["dist_prom_m"]    for s in sectors.values()]), 1)

    return {
        "stage_h_m":       stage_h,
        "despl_norte_m":   despl_norte,
        "led_tilt_deg":    led_tilt_deg,
        "sweet_spot_global_pct": sweet_global,
        "led_global_pct":  led_global,
        "dist_global_m":   dist_global,
        "sectores":        sectors,
    }


def run_full_simulation(model: dict) -> list[dict]:
    """Corre todas las combinaciones de variables del rider."""
    results = []
    for stage_dh in [0.0, 0.5, 1.0, 1.5, 2.0]:
        for despl in [0, 5, 10]:
            for led in [0, 5, 10, 15]:
                stage_h = model["escenario"]["altura_m"] + stage_dh
                r = simulate_variant(model, stage_h, despl, led)
                results.append(r)
    return results
