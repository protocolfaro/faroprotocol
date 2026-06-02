"""faro_kalman.py — Extended Kalman Filter for grass state estimation
Faro Protocol · numpy only

State vector x = [compactacion, traccion, ndvi_offset]
  compactacion : soil compaction index (0–100, higher = more compact)
  traccion     : traction index (0–100, higher = better grip)
  ndvi_offset  : latent NDVI bias from Sentinel-1 SAR (−0.3..+0.3)

Sensors (two independent update paths):
  1. Manual field measurements: Clegg hammer (kg) + torque wrench (Nm)
     maps to compaction/traction space via empirical calibration curves
  2. Sentinel-1 SAR: VV/VH ratio (dB) → ndvi_offset via linear model

Process model: identity (state persists between updates, with Gaussian noise)
"""
from __future__ import annotations
import numpy as np
from typing import Optional


# ── Sensor calibration curves ──────────────────────────────────────────────
# Clegg Impact Value (CIV) in kg · calibration → compactacion 0-100
# CIV < 20 → loose; CIV 40-60 → match; CIV > 80 → hard
def _civ_to_compactacion(civ_kg: float) -> float:
    return float(np.clip((civ_kg / 100.0) * 100.0, 0, 100))

# Torque (Nm) → traccion 0-100
# <2 Nm → poor grip; 5-8 Nm → adequate; >12 Nm → excellent
def _torque_to_traccion(torque_nm: float) -> float:
    return float(np.clip((torque_nm / 15.0) * 100.0, 0, 100))

# SAR VV/VH ratio (dB) → ndvi_offset
# Empirical: ratio range ≈ 5–20 dB for turf; centred at 12 dB → offset 0
def _sar_to_ndvi_offset(vv_vh_db: float) -> float:
    return float(np.clip((vv_vh_db - 12.0) * 0.018, -0.3, 0.3))


# ── Process noise ──────────────────────────────────────────────────────────
_Q = np.diag([0.5, 0.5, 0.005])    # small drift per time step

# ── Measurement noise ──────────────────────────────────────────────────────
_R_MANUAL = np.diag([4.0, 4.0])    # compactacion + traccion noise (variance)
_R_SAR    = np.array([[0.002]])    # ndvi_offset noise (variance)

# ── Observation matrices ───────────────────────────────────────────────────
# Manual measures state[0] and state[1]
_H_MANUAL = np.array([[1, 0, 0],
                       [0, 1, 0]], dtype=float)
# SAR measures state[2]
_H_SAR    = np.array([[0, 0, 1]], dtype=float)


class FaroKalman:
    """Extended Kalman Filter for single-cancha grass state estimation."""

    def __init__(
        self,
        x0: Optional[np.ndarray] = None,
        P0: Optional[np.ndarray] = None,
    ) -> None:
        self.x = x0 if x0 is not None else np.array([50.0, 50.0, 0.0])
        self.P = P0 if P0 is not None else np.eye(3) * 100.0
        self._steps = 0

    # ── Predict ───────────────────────────────────────────────────────────
    def predict(self) -> None:
        """Advance state by one time step (identity process model)."""
        # x_{k|k-1} = F x_{k-1}  with F = I
        self.P = self.P + _Q
        self._steps += 1

    # ── Update: manual measurement ────────────────────────────────────────
    def update_manual(self, clegg_kg: float, torque_nm: float) -> dict:
        """Incorporate Clegg hammer + torque wrench readings."""
        z = np.array([
            _civ_to_compactacion(clegg_kg),
            _torque_to_traccion(torque_nm),
        ])
        H, R = _H_MANUAL, _R_MANUAL
        y = z - H @ self.x                          # innovation
        S = H @ self.P @ H.T + R
        K = self.P @ H.T @ np.linalg.inv(S)        # Kalman gain
        self.x = self.x + K @ y
        self.P = (np.eye(3) - K @ H) @ self.P
        return self.state_dict()

    # ── Update: SAR ───────────────────────────────────────────────────────
    def update_sar(self, vv_vh_db: float) -> dict:
        """Incorporate Sentinel-1 VV/VH ratio (dB)."""
        z = np.array([_sar_to_ndvi_offset(vv_vh_db)])
        H, R = _H_SAR, _R_SAR
        y = z - H @ self.x
        S = H @ self.P @ H.T + R
        K = self.P @ H.T @ np.linalg.inv(S)
        self.x = self.x + K @ y
        self.P = (np.eye(3) - K @ H) @ self.P
        return self.state_dict()

    # ── State accessor ────────────────────────────────────────────────────
    def state_dict(self) -> dict:
        return {
            "compactacion":  round(float(self.x[0]), 2),
            "traccion":      round(float(self.x[1]), 2),
            "ndvi_offset":   round(float(self.x[2]), 4),
            "uncertainty":   [round(float(self.P[i, i]), 4) for i in range(3)],
            "steps":         self._steps,
        }

    def to_dict(self) -> dict:
        return {
            "x": self.x.tolist(),
            "P": self.P.tolist(),
            "steps": self._steps,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "FaroKalman":
        kf = cls(
            x0=np.array(d["x"]),
            P0=np.array(d["P"]),
        )
        kf._steps = d.get("steps", 0)
        return kf


# ── Convenience: run one full cycle ───────────────────────────────────────
def run_update(
    state_dict: Optional[dict],
    clegg_kg:   Optional[float] = None,
    torque_nm:  Optional[float] = None,
    vv_vh_db:   Optional[float] = None,
) -> dict:
    """Load existing state (or init fresh), predict, apply available measurements.

    Returns updated state_dict to persist in JSON.
    """
    kf = FaroKalman.from_dict(state_dict) if state_dict else FaroKalman()
    kf.predict()
    if clegg_kg is not None and torque_nm is not None:
        kf.update_manual(clegg_kg, torque_nm)
    if vv_vh_db is not None:
        kf.update_sar(vv_vh_db)
    return kf.to_dict()


if __name__ == "__main__":
    kf = FaroKalman()
    print("Init:  ", kf.state_dict())
    kf.predict()
    print("After predict:", kf.state_dict())
    kf.update_manual(clegg_kg=55.0, torque_nm=7.5)
    print("After Clegg+Torque:", kf.state_dict())
    kf.predict()
    kf.update_sar(vv_vh_db=14.2)
    print("After SAR:", kf.state_dict())
