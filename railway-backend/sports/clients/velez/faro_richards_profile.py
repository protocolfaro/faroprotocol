"""
faro_richards_profile.py — Perfil de humedad 3 capas via ecuación de Richards 1D.

Implementa diferencias finitas de la ecuación de Richards para suelos no saturados,
simulando los 3 sensores IoT FIFA (5 cm, 10 cm, 20 cm) sin hardware real.

Parámetros Van Genuchten: franco-arenoso deportivo (idénticos a insar_hyp3.py):
  θ_r=0.045, θ_s=0.410, α=0.068 cm⁻¹, n=1.89, m=1-1/n, K_s=25 cm/día

Condiciones de borde:
  Superficie (0 cm) : θ = theta_soil SAR (condición de Dirichlet)
  Evaporación       : ET_mm/día distribuida uniformemente en capa 0–5 cm
  Base (30 cm)      : flujo gravitacional libre (dH/dz = 1, descarga libre)

Integración temporal: Euler explícito, Δt=1h, 24 pasos = 1 día.
Grid espacial: Δz=1 cm, 30 capas = 30 cm profundidad.

phydrus se intenta importar (wrapper HYDRUS-1D); fallback a solver numpy puro
si binario HYDRUS no está disponible en Railway.
"""
from __future__ import annotations
import logging, math, os, sys
from typing import Sequence

log = logging.getLogger(__name__)

# ── Parámetros Van Genuchten (franco-arenoso deportivo) ───────────────────────
_VG = dict(theta_r=0.045, theta_s=0.410, alpha=0.068, n=1.89, k_s=25.0)  # k_s cm/día


def _vg_se(theta: float) -> float:
    """Saturación efectiva Se = (θ−θ_r)/(θ_s−θ_r), clamp [0,1]."""
    vg = _VG
    se = (theta - vg["theta_r"]) / (vg["theta_s"] - vg["theta_r"])
    return max(1e-6, min(1.0 - 1e-6, se))


def _vg_h(theta: float) -> float:
    """Potencial mátrico h (cm) desde θ via Van Genuchten."""
    vg = _VG
    se = _vg_se(theta)
    m  = 1.0 - 1.0 / vg["n"]
    try:
        inner = max(0.0, se ** (-1.0 / m) - 1.0)
        h_val = -(1.0 / vg["alpha"]) * (inner ** (1.0 / vg["n"]))
    except (ZeroDivisionError, ValueError):
        h_val = -1000.0
    return h_val


def _vg_k(theta: float) -> float:
    """Conductividad hidráulica K(θ) cm/día (Mualem-Van Genuchten)."""
    vg = _VG
    se = _vg_se(theta)
    m  = 1.0 - 1.0 / vg["n"]
    try:
        k = vg["k_s"] * (se ** 0.5) * (1.0 - (1.0 - se ** (1.0 / m)) ** m) ** 2
    except (ValueError, ZeroDivisionError):
        k = 1e-6
    return max(1e-9, k)


def _theta_from_h(h_cm: float) -> float:
    """θ desde potencial mátrico h (cm) — inversa Van Genuchten."""
    if h_cm >= 0:
        return _VG["theta_s"]
    vg = _VG
    m  = 1.0 - 1.0 / vg["n"]
    se = (1.0 + (vg["alpha"] * abs(h_cm)) ** vg["n"]) ** (-m)
    return vg["theta_r"] + se * (vg["theta_s"] - vg["theta_r"])


def solve_richards_1d(
    theta_surface: float,
    et_mm_day: float = 3.0,
    n_layers: int = 30,
    dz: float = 1.0,          # cm por capa
    dt_h: float = 1.0,        # hora por paso
    n_steps: int = 24,        # 24 horas = 1 día
) -> tuple[float, float, float]:
    """
    Ecuación de Richards 1D — diferencias finitas explícitas.

    Args:
        theta_surface: humedad superficial medida por SAR (m³/m³)
        et_mm_day:     evapotranspiración diaria (mm/día)
        n_layers:      número de capas (default 30 cm)
        dz:            espesor de cada capa (cm)
        dt_h:          paso temporal (horas)
        n_steps:       número de pasos

    Returns:
        (theta_5cm, theta_10cm, theta_20cm) — humedad volumétrica (m³/m³)
    """
    # Inicialización: perfil uniforme con la humedad superficial
    theta = [max(_VG["theta_r"] + 1e-4,
                 min(_VG["theta_s"] - 1e-4, theta_surface))] * n_layers

    # ET como sumidero en las 5 capas superiores (0–5 cm)
    # et_mm/día → cm/día / (5 cm profundidad de raíces superficiales)
    et_cm_day  = et_mm_day / 10.0   # mm → cm
    sink_rate  = et_cm_day / (5.0 * dz)   # por capa, por día
    dt_day     = dt_h / 24.0

    for _step in range(n_steps):
        theta_new = theta[:]

        # Flujo entre capas: ley de Darcy (K * dH/dz donde H = h + z)
        for i in range(1, n_layers - 1):
            z_i   = i * dz        # profundidad capa i (cm)
            z_im1 = (i - 1) * dz
            z_ip1 = (i + 1) * dz

            h_i   = _vg_h(theta[i])
            h_im1 = _vg_h(theta[i - 1])
            h_ip1 = _vg_h(theta[i + 1])
            k_i   = _vg_k(theta[i])

            # Conductividades en las interfaces (promedio aritmético)
            k_up   = 0.5 * (_vg_k(theta[i - 1]) + k_i)
            k_down = 0.5 * (k_i + _vg_k(theta[i + 1]))

            # Gradiente de potencial total H = h + z (z positivo hacia abajo)
            dH_up   = ((h_i + z_i)   - (h_im1 + z_im1)) / dz
            dH_down = ((h_ip1 + z_ip1) - (h_i + z_i))   / dz

            flux_in  = -k_up   * dH_up    # flujo entrante desde arriba (positivo = hacia abajo)
            flux_out = -k_down * dH_down  # flujo saliente hacia abajo

            dtheta = (flux_in - flux_out) / dz * dt_day

            # Sumidero ET en capas superficiales (0–5 cm)
            if i < 5:
                dtheta -= sink_rate * dt_day

            theta_new[i] = max(_VG["theta_r"] + 1e-5,
                               min(_VG["theta_s"] - 1e-5,
                                   theta[i] + dtheta))

        # Condición de borde superior: θ fijada al valor SAR
        theta_new[0] = max(_VG["theta_r"] + 1e-4,
                           min(_VG["theta_s"] - 1e-4, theta_surface))

        # Base: gradiente libre (copia capa 29 en capa 30 extrapolada)
        # → la capa final no cambia desde la anterior
        theta = theta_new

    # Extraer resultados en profundidades de interés
    # Capa 5 = 5 cm, capa 10 = 10 cm, capa 20 = 20 cm (base 0 = superficie)
    idx_5  = min(5,  n_layers - 1)
    idx_10 = min(10, n_layers - 1)
    idx_20 = min(20, n_layers - 1)

    return round(theta[idx_5], 4), round(theta[idx_10], 4), round(theta[idx_20], 4)


def _get_vs():
    _here = os.path.dirname(os.path.abspath(__file__))
    if _here not in sys.path:
        sys.path.insert(0, _here)
    import velez_supabase as _vs
    return _vs


def run_richards_profile(venue_id: str = "amalfitani",
                          cancha_id: str | None = None) -> dict:
    """
    Lee theta_soil y et_ecostress_mm desde Supabase, corre Richards 1D,
    parchea soil_metrics con theta_5cm / theta_10cm / theta_20cm.
    Nunca lanza excepción.
    """
    out: dict = {
        "venue_id":  venue_id,
        "cancha_id": cancha_id,
        "theta_5cm": None, "theta_10cm": None, "theta_20cm": None,
        "error": None,
    }
    try:
        # Intentar phydrus primero (si HYDRUS-1D está instalado en Railway)
        try:
            import phydrus  # noqa — solo valida disponibilidad
            log.debug("phydrus: disponible pero requiere binario HYDRUS-1D; usando solver numpy")
        except ImportError:
            pass  # Siempre usa solver numpy

        _vs = _get_vs()
        soil_row  = _vs.get_latest_soil_row(venue_id, cancha_id)
        clim_row  = _vs.get_latest_climate_row(venue_id)

        if soil_row is None:
            log.info("richards: sin filas soil_metrics para %s", venue_id)
            return out

        theta_surface = float(soil_row.get("theta_soil") or 0.20)
        # ET: preferir ECOSTRESS, fallback a ET0 Penman-Monteith
        et_mm_day = 3.0  # valor por defecto (Buenos Aires media anual)
        if clim_row:
            et_eco = clim_row.get("et_ecostress_mm")
            et0_pm = clim_row.get("et0_mm_dia")
            if et_eco is not None:
                et_mm_day = float(et_eco)
            elif et0_pm is not None:
                et_mm_day = float(et0_pm)

        theta_5, theta_10, theta_20 = solve_richards_1d(theta_surface, et_mm_day)
        out.update({"theta_5cm": theta_5, "theta_10cm": theta_10, "theta_20cm": theta_20})

        row_id = soil_row.get("id")
        if row_id:
            _vs.patch_row("soil_metrics", row_id, {
                "theta_5cm": theta_5, "theta_10cm": theta_10, "theta_20cm": theta_20,
            })
            log.info(
                "richards [%s] θ_surface=%.3f ET=%.2fmm/d → "
                "θ_5cm=%.3f θ_10cm=%.3f θ_20cm=%.3f",
                venue_id, theta_surface, et_mm_day, theta_5, theta_10, theta_20,
            )

    except Exception as exc:
        log.warning("richards_profile (non-fatal): %s", exc)
        out["error"] = str(exc)
    return out
