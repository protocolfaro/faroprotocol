"""
faro_analytics_physics.py — Water Cloud Model + Van Genuchten + Magnus-Tetens.
Faro Protocol — sin bucles Python, todo numpy vectorizado.
"""
from __future__ import annotations
import numpy as np


def compute_faro_hydro_core(sigma_db_vv, sigma_db_vh, lai_prosail, evapo_clima_mm):
    """
    WCM + Van Genuchten Franco-Arenoso Deportivo.
    Inputs may be scalars or numpy arrays.
    Returns dict with rvi, soil_moisture_volumetric, matric_potential_cm, irrigation_prescription.
    """
    sig_vv = 10.0 ** (np.asarray(sigma_db_vv, dtype=np.float64) / 10.0)
    sig_vh = 10.0 ** (np.asarray(sigma_db_vh, dtype=np.float64) / 10.0)

    ratio_vh_vv = np.where(sig_vv > 0.0, sig_vh / sig_vv, 0.0)
    sig_vv_c = np.where(ratio_vh_vv < 0.05, sig_vh / 0.11, sig_vv)

    denom_rvi = sig_vv_c + sig_vh
    rvi = np.where(denom_rvi > 0.0, (4.0 * sig_vh) / denom_rvi, 0.0)

    theta_rad = np.radians(38.5)
    cos_theta = np.cos(theta_rad)
    A, B = 0.0012, 0.08
    lai = np.asarray(lai_prosail, dtype=np.float64)
    tau_2 = np.exp(-2.0 * B * lai / cos_theta)
    sigma_0_veg = A * lai * (1.0 - tau_2) * cos_theta
    sigma_0_soil = np.maximum((sig_vv_c - sigma_0_veg) / (tau_2 + 1e-6), 1e-4)
    theta_soil = (sigma_0_soil * 0.28) + 0.12

    theta_r, theta_s, alpha, n = 0.045, 0.410, 0.068, 1.89
    m = 1.0 - (1.0 / n)
    theta_c = np.clip(theta_soil, theta_r + 1e-4, theta_s - 1e-4)
    se = (theta_c - theta_r) / (theta_s - theta_r)
    term_inner = np.maximum((se ** (-1.0 / m)) - 1.0, 0.0)
    h_suction = (1.0 / alpha) * (term_inner ** (1.0 / n))

    evapo = np.asarray(evapo_clima_mm, dtype=np.float64)
    deficit_suelo = (theta_s - theta_c) * 150.0
    deficit_total = np.where(h_suction > 350.0, deficit_suelo + evapo, 0.0)
    tiempo_riego = (deficit_total / 12.5) * 60.0

    return {
        "rvi_structural_density":   round(float(np.mean(rvi)), 4),
        "soil_moisture_volumetric": round(float(np.mean(theta_c)), 4),
        "matric_potential_cm":      round(float(np.mean(h_suction)), 2),
        "irrigation_prescription": {
            "deficit_hydric_mm":     round(float(np.mean(deficit_total)), 2),
            "aspersor_time_minutes": int(np.ceil(np.mean(tiempo_riego))),
        },
    }


def compute_faro_cutting_core(temp_air_24h, rh_pct, last_cut_gdd, cab_nitrogen, h_suction_cm):
    """
    Magnus-Tetens + GDD → ventana de corte helicoidal.
    Inputs may be scalars or numpy arrays.
    Returns dict with metabolic_growth_pct, elongation_mm, dew_point_c, cutting_prescription.
    """
    temp = np.asarray(temp_air_24h, dtype=np.float64)
    rh   = np.asarray(rh_pct, dtype=np.float64)
    h_s  = np.asarray(h_suction_cm, dtype=np.float64)
    cab  = np.asarray(cab_nitrogen, dtype=np.float64)

    gp_thermal   = np.exp(-0.5 * ((temp - 20.0) / 5.5) ** 2)
    alpha_stress = np.where(cab >= 40.0, 1.0, cab / 40.0)
    gdd_today    = np.maximum(temp - 5.0, 0.0)
    crec         = gdd_today * 0.45 * alpha_stress * gp_thermal

    a_m, b_m = 17.625, 243.04
    alpha_magnus = ((a_m * temp) / (b_m + temp)) + np.log(np.clip(rh / 100.0, 1e-4, 1.0))
    temp_dew     = (b_m * alpha_magnus) / (a_m - alpha_magnus)
    t_delta      = temp - temp_dew

    ajuste_turg = np.where(h_s < 80.0, 2.0, np.where(h_s > 600.0, 4.0, 0.0))
    altura_mm   = 24.0 + ajuste_turg

    t_d   = float(np.mean(t_delta))
    h_s_m = float(np.mean(h_s))
    alt   = int(np.mean(altura_mm))

    if h_s_m > 800.0:
        status = "BLOQUEO ABSOLUTO"
        accion = "Células estomáticas colapsadas. Aplicar riego de mitigación urgente."
        rpm    = 0
    elif t_d < 2.5:
        status = "VENTANA RESTRINGIDA"
        accion = "Retrasar corte hasta evaporación del rocío. Molinete 950 RPM, evitar succión de esporas."
        rpm    = 950
    else:
        status = "VENTANA ÓPTIMA"
        accion = "Corte helicoidal alta velocidad. Ajustar contracuchilla 0.05 mm luz."
        rpm    = 1200

    return {
        "metabolic_growth_pct":    round(float(np.mean(gp_thermal)) * 100, 1),
        "elongation_predicted_mm": round(float(np.mean(crec)), 2),
        "dew_point_c":             round(float(np.mean(temp_dew)), 2),
        "cutting_prescription": {
            "status_window":        status,
            "prescribed_height_mm": alt,
            "mechanical_action":    accion,
            "reel_speed_rpm":       rpm,
        },
    }
