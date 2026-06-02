"""faro_recovery.py — Gompertz / Richards grass recovery curves
Faro Protocol · scipy (scipy.integrate.solve_ivp + scipy.optimize.dual_annealing)

ODE: Richards generalized logistic
    dN/dt = r · N · (1 - (N/K)^v)

    v → 1  : standard logistic (sigmoid)
    v → 0  : Gompertz (asymmetric, fast early growth)
    v > 1  : convex S-curve (slow start, fast finish)

State N(t) ∈ [0, 1] represents relative grass health (NDVI normalised to K=1).
"""
from __future__ import annotations
from typing import Optional
import numpy as np
from scipy.integrate import solve_ivp
from scipy.optimize import dual_annealing


# ── Pre-defined recovery scenarios ────────────────────────────────────────
SCENARIOS = {
    "basico": {
        "label": "Básico (r=0.10)",
        "r": 0.10, "v": 0.5, "K": 1.0,
        "desc": "Recuperación lenta — campo muy exigido, sin período de descanso",
    },
    "intermedio": {
        "label": "Intermedio (r=0.20)",
        "r": 0.20, "v": 0.5, "K": 1.0,
        "desc": "Recuperación normal — 2-3 días de descanso entre sesiones",
    },
    "intensivo": {
        "label": "Intensivo (r=0.35)",
        "r": 0.35, "v": 0.5, "K": 1.0,
        "desc": "Recuperación rápida — campo fertilizado + riego óptimo",
    },
}

_T_SPAN = (0.0, 30.0)   # 0-30 days simulation window
_T_EVAL = np.linspace(0, 30, 121)  # every 0.25 days


# ── ODE definition ────────────────────────────────────────────────────────
def _richards_ode(t: float, y: list[float], r: float, K: float, v: float) -> list[float]:
    N = max(y[0], 1e-6)
    return [r * N * (1.0 - (N / K) ** v)]


def simulate(
    N0: float,
    r: float = 0.20,
    K: float = 1.0,
    v: float = 0.5,
    t_days: int = 30,
) -> dict:
    """Simulate Richards recovery from initial health N0 ∈ [0,1].

    Returns dict with 't' and 'N' arrays (lists for JSON serialisation).
    """
    t_eval = np.linspace(0, t_days, t_days * 4 + 1)
    sol = solve_ivp(
        _richards_ode,
        (0.0, float(t_days)),
        [float(max(0.0, min(N0, K)))],
        args=(r, K, v),
        t_eval=t_eval,
        method="RK45",
        dense_output=False,
        rtol=1e-4, atol=1e-6,
    )
    return {
        "t":       sol.t.tolist(),
        "N":       sol.y[0].tolist(),
        "r": r, "K": K, "v": v, "N0": N0,
        "days_to_90pct": _days_to_target(sol.t, sol.y[0], 0.90 * K),
        "days_to_75pct": _days_to_target(sol.t, sol.y[0], 0.75 * K),
    }


def _days_to_target(t: np.ndarray, N: np.ndarray, target: float) -> Optional[float]:
    idx = np.searchsorted(N, target)
    if idx >= len(t):
        return None
    if idx == 0:
        return 0.0
    # Linear interpolation between adjacent points
    t0, t1 = t[idx - 1], t[idx]
    n0, n1 = N[idx - 1], N[idx]
    if n1 <= n0:
        return None
    frac = (target - n0) / (n1 - n0)
    return round(float(t0 + frac * (t1 - t0)), 2)


def simulate_all_scenarios(N0: float, t_days: int = 30) -> dict:
    """Run all three pre-defined scenarios from the same initial condition."""
    return {
        key: {**simulate(N0, sc["r"], sc["K"], sc["v"], t_days), "meta": sc}
        for key, sc in SCENARIOS.items()
    }


# ── Rate fitting via simulated annealing ──────────────────────────────────
def fit_recovery_rate(
    ndvi_history: list[tuple[float, float]],
    K: float = 1.0,
    v: float = 0.5,
    seed: int = 42,
) -> dict:
    """Fit recovery rate r and initial condition N0 from historical NDVI data.

    Parameters
    ----------
    ndvi_history : list of (day, ndvi_norm) tuples — ndvi normalised to [0, K]
    K, v         : Richards parameters (fixed during fit)
    seed         : for reproducibility

    Returns dict with 'r', 'N0', 'rmse', 'scenario_match'.
    """
    t_obs = np.array([p[0] for p in ndvi_history])
    N_obs = np.array([p[1] for p in ndvi_history])

    def residuals(params: np.ndarray) -> float:
        r_try, N0_try = params
        if r_try <= 0 or N0_try <= 0 or N0_try > K:
            return 1e6
        try:
            sol = solve_ivp(
                _richards_ode,
                (0.0, float(t_obs.max())),
                [float(N0_try)],
                args=(r_try, K, v),
                t_eval=t_obs,
                method="RK45",
                rtol=1e-3, atol=1e-5,
            )
            N_pred = sol.y[0]
            if len(N_pred) != len(N_obs):
                return 1e6
            return float(np.sqrt(np.mean((N_pred - N_obs) ** 2)))
        except Exception:
            return 1e6

    result = dual_annealing(
        residuals,
        bounds=[(0.01, 1.0), (0.01, K)],
        seed=seed,
        maxiter=500,
        minimizer_kwargs={"method": "Nelder-Mead"},
    )

    r_fit, N0_fit = float(result.x[0]), float(result.x[1])
    rmse = float(result.fun)

    # Match closest scenario
    scenario_match = min(
        SCENARIOS.keys(),
        key=lambda k: abs(SCENARIOS[k]["r"] - r_fit),
    )

    return {
        "r":               round(r_fit, 4),
        "N0":              round(N0_fit, 4),
        "rmse":            round(rmse, 6),
        "scenario_match":  scenario_match,
        "scenario_label":  SCENARIOS[scenario_match]["label"],
    }


if __name__ == "__main__":
    import json
    print("=== Simulate 3 scenarios from N0=0.35 ===")
    out = simulate_all_scenarios(N0=0.35, t_days=30)
    for sc, data in out.items():
        print(f"{sc:12s}: 75% in {data['days_to_75pct']} days, "
              f"90% in {data['days_to_90pct']} days")

    print("\n=== Fit recovery rate from synthetic history ===")
    # Generate synthetic data from 'intermedio' scenario + noise
    rng = np.random.default_rng(0)
    sol = simulate(N0=0.40, r=0.20, t_days=20)
    t_arr = np.array(sol["t"])
    N_arr = np.array(sol["N"]) + rng.normal(0, 0.02, len(sol["t"]))
    # Sample every 5 days
    sample_idx = np.arange(0, len(t_arr), 20)
    history = [(float(t_arr[i]), float(N_arr[i])) for i in sample_idx]
    fit = fit_recovery_rate(history, K=1.0, v=0.5, seed=42)
    print(json.dumps(fit, indent=2))
