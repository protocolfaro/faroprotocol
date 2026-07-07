"""
sar_ndvi_calibration.py — Site-specific SAR→NDVI Ridge regression for Velez facilities.

Trains from historical Supabase pairs (soil_metrics VV/VH × vegetation_metrics NDVI)
matched within ±5 days by cancha_id. Model cached in-memory per process.

Features: [VV_dB, VH_dB, RVI4S1, sin(2π·m/12), cos(2π·m/12)]
Uses Ridge(α=1.0) + StandardScaler.  Typical RMSE on Argentine ryegrass: 0.03–0.06.

API:
    rvi4s1(vv_db, vh_db)                         → float
    build_and_save(min_pairs=10)                  → dict
    predict_ndvi(vv_db, vh_db, month, model=None) → float
"""
from __future__ import annotations
import logging, math, os, sys
from datetime import date
from typing import Optional

log = logging.getLogger(__name__)

# In-memory model cache — populated lazily on first predict_ndvi call.
_MODEL_CACHE: dict | None = None

# All canchas share venue_id="amalfitani" in Supabase (set by satellite_pipeline).
_VENUE_ID = "amalfitani"


# ── Public helpers ────────────────────────────────────────────────────────────

def rvi4s1(vv_db: float, vh_db: float) -> float:
    """
    Radar Vegetation Index for dual-pol Sentinel-1.
    RVI4S1 = 4·VH_lin / (VV_lin + VH_lin)  ∈ [0, 1]
    Dense live vegetation → ~1; bare soil → ~0.
    """
    try:
        vv_lin = 10.0 ** (float(vv_db) / 10.0)
        vh_lin = 10.0 ** (float(vh_db) / 10.0)
        denom = vv_lin + vh_lin
        if denom < 1e-12:
            return 0.0
        return round(min(1.0, max(0.0, 4.0 * vh_lin / denom)), 4)
    except Exception:
        return 0.0


def _sar_features(vv_db: float, vh_db: float, month: int) -> list[float]:
    """5-feature vector: [VV, VH, RVI4S1, sin(2π·m/12), cos(2π·m/12)]."""
    rvi   = rvi4s1(vv_db, vh_db)
    angle = 2.0 * math.pi * month / 12.0
    return [float(vv_db), float(vh_db), rvi, math.sin(angle), math.cos(angle)]


# ── Training ──────────────────────────────────────────────────────────────────

def _fetch_training_pairs() -> list[dict]:
    """
    Pull historical SAR×NDVI matched pairs from Supabase.
    Matches soil_metrics (VV/VH) ↔ vegetation_metrics (NDVI) by cancha_id + date ±5 days.
    Returns list of {vv_db, vh_db, ndvi, month}.
    """
    try:
        _here = os.path.dirname(os.path.abspath(__file__))
        if _here not in sys.path:
            sys.path.insert(0, _here)
        import velez_supabase as vs
    except Exception as exc:
        log.warning("sar_calibration: import failed: %s", exc)
        return []

    try:
        soil_rows = vs.get_soil_metrics_latest(_VENUE_ID, cancha_id=None, dias=365)
        veg_rows  = vs.get_vegetation_metrics_latest(_VENUE_ID, cancha_id=None, dias=365)
    except Exception as exc:
        log.warning("sar_calibration: Supabase query failed: %s", exc)
        return []

    if not soil_rows or not veg_rows:
        log.info("sar_calibration: no historical data in Supabase (soil=%d veg=%d)",
                 len(soil_rows or []), len(veg_rows or []))
        return []

    # Index NDVI by (cancha_id, fecha_imagen)
    ndvi_idx: dict[tuple[str, str], float] = {}
    for row in veg_rows:
        cid = row.get("cancha_id") or ""
        fi  = (row.get("fecha_imagen") or "")[:10]
        nd  = row.get("ndvi")
        if cid and fi and nd is not None:
            ndvi_idx[(cid, fi)] = float(nd)

    pairs: list[dict] = []
    for srow in soil_rows:
        vv  = srow.get("sar_vv_db")
        vh  = srow.get("sar_vh_db")
        cid = srow.get("cancha_id") or ""
        fi  = (srow.get("fecha_imagen") or "")[:10]
        if vv is None or vh is None or not cid or not fi:
            continue
        try:
            soil_dt = date.fromisoformat(fi)
        except ValueError:
            continue

        best_ndvi: Optional[float] = None
        best_delta = 6
        for (v_cid, v_date_str), ndvi_val in ndvi_idx.items():
            if v_cid != cid:
                continue
            try:
                delta = abs((soil_dt - date.fromisoformat(v_date_str)).days)
                if delta <= 5 and delta < best_delta:
                    best_delta = delta
                    best_ndvi = ndvi_val
            except ValueError:
                continue

        if best_ndvi is not None:
            pairs.append({
                "vv_db": float(vv), "vh_db": float(vh),
                "ndvi": best_ndvi, "month": soil_dt.month,
            })

    log.info("sar_calibration: %d training pairs collected (cancha×date)", len(pairs))
    return pairs


def build_and_save(min_pairs: int = 10) -> dict:
    """
    Train Ridge regression from Supabase history and populate in-memory cache.
    Falls back gracefully when not enough pairs are available.
    """
    global _MODEL_CACHE
    pairs = _fetch_training_pairs()
    if len(pairs) < min_pairs:
        log.info("sar_calibration: %d pairs < %d threshold — RVI4S1 seasonal proxy active",
                 len(pairs), min_pairs)
        return {"ok": False, "n_pairs": len(pairs), "reason": f"need ≥{min_pairs} pairs"}

    try:
        import numpy as np
        from sklearn.linear_model import Ridge
        from sklearn.preprocessing import StandardScaler

        X = np.array([_sar_features(p["vv_db"], p["vh_db"], p["month"]) for p in pairs])
        y = np.array([p["ndvi"] for p in pairs])

        scaler = StandardScaler()
        X_s = scaler.fit_transform(X)

        mdl = Ridge(alpha=1.0)
        mdl.fit(X_s, y)
        rmse = float(np.sqrt(np.mean((y - mdl.predict(X_s)) ** 2)))

        _MODEL_CACHE = {
            "coef":      mdl.coef_.tolist(),
            "intercept": float(mdl.intercept_),
            "mean":      scaler.mean_.tolist(),
            "scale":     scaler.scale_.tolist(),
            "rmse":      round(rmse, 5),
            "n_pairs":   len(pairs),
        }
        log.info("sar_calibration: Ridge trained — %d pairs, RMSE=%.4f", len(pairs), rmse)
        return {"ok": True, **_MODEL_CACHE}

    except Exception as exc:
        log.warning("sar_calibration: training failed: %s", exc)
        return {"ok": False, "error": str(exc)[:200]}


# ── Inference ─────────────────────────────────────────────────────────────────

def predict_ndvi(
    vv_db: Optional[float],
    vh_db: Optional[float],
    month: int,
    model: dict | None = None,
) -> float:
    """
    Predict NDVI from SAR backscatter (dB, S1A-equivalent after radiometric correction).

    Priority:
      1. Ridge regression (site-specific, trained from Supabase history)
      2. RVI4S1 × seasonal factor (0.40 winter / 0.70 summer) — universal proxy

    Args:
        vv_db:  VV sigma0 in dB
        vh_db:  VH sigma0 in dB
        month:  acquisition month 1-12 (for seasonal correction)
        model:  pre-loaded model dict; uses _MODEL_CACHE if None
    """
    winter = month in (5, 6, 7, 8)

    if vv_db is None or vh_db is None:
        return round(0.08 if winter else 0.35, 4)

    m = model or _MODEL_CACHE
    if m is None:
        build_and_save()
        m = _MODEL_CACHE

    if m is None:
        return round(rvi4s1(vv_db, vh_db) * (0.40 if winter else 0.70), 4)

    try:
        import numpy as np
        feats   = np.array([_sar_features(vv_db, vh_db, month)])
        feats_s = (feats - np.array(m["mean"])) / np.array(m["scale"])
        pred    = float(np.dot(feats_s, m["coef"]) + m["intercept"])
        return round(max(0.02, min(0.95, pred)), 4)
    except Exception as exc:
        log.debug("predict_ndvi Ridge failed: %s — RVI fallback", exc)
        return round(rvi4s1(vv_db, vh_db) * (0.40 if winter else 0.70), 4)


def get_model_status() -> dict:
    """Returns current cache status for monitoring endpoints."""
    if _MODEL_CACHE is None:
        return {"trained": False}
    return {
        "trained":  True,
        "n_pairs":  _MODEL_CACHE.get("n_pairs"),
        "rmse":     _MODEL_CACHE.get("rmse"),
    }
