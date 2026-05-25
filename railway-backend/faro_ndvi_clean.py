"""faro_ndvi_clean.py — Sub-pixel spectral unmixing and NDVI cleaning
Faro Protocol · numpy only

Cleans a raw NDVI raster from:
  - Saturated pixels (NIR > 9000 or RED > 9000 DN)
  - Cloud-contaminated pixels (NDVI < 0.0 in vegetated context)
  - Mixed pixels — linear spectral unmixing into three endmembers:
      vegetation (EV), bare soil (ES), cloud/shadow (EC)

Each pixel spectral signature S(NIR,RED) is decomposed as:
    S = f_v * EV + f_s * ES + f_c * EC,  f_v+f_s+f_c = 1, each ≥ 0

Then corrected NDVI uses vegetation-fraction-weighted endmember NDVI:
    NDVI_clean = f_v * NDVI_EV + f_s * NDVI_ES
(cloud fraction is discarded; result renormalised to [f_v+f_s])
"""
from __future__ import annotations
import numpy as np

# Band reflectance endmembers (NIR, RED) — normalised to 0-1 scale
# Tuned for Sentinel-2 BOA bands B08 (NIR) / B04 (RED), DN / 10000
EV_NIR, EV_RED = 0.78, 0.06  # dense vegetation
ES_NIR, ES_RED = 0.24, 0.22  # bare soil / dry grass
EC_NIR, EC_RED = 0.90, 0.88  # cloud / snow / saturation

NDVI_EV = (EV_NIR - EV_RED) / (EV_NIR + EV_RED)   # ≈ 0.854
NDVI_ES = (ES_NIR - ES_RED) / (ES_NIR + ES_RED)   # ≈ 0.043

# Saturation thresholds (DN, Sentinel-2 L2A, 0-10000 scale)
SAT_THRESH = 9000

# Endmember matrix A: columns = [EV, ES, EC] for bands [NIR, RED]
_A = np.array([
    [EV_NIR, ES_NIR, EC_NIR],
    [EV_RED, ES_RED, EC_RED],
], dtype=np.float64)


def _ndvi(nir: np.ndarray, red: np.ndarray) -> np.ndarray:
    denom = nir + red
    with np.errstate(invalid="ignore", divide="ignore"):
        return np.where(denom > 0, (nir - red) / denom, 0.0)


def saturation_mask(nir_dn: np.ndarray, red_dn: np.ndarray) -> np.ndarray:
    """Boolean mask: True where pixel is saturated."""
    return (nir_dn > SAT_THRESH) | (red_dn > SAT_THRESH)


def cloud_mask(ndvi: np.ndarray, threshold: float = 0.0) -> np.ndarray:
    """Boolean mask: True where NDVI < threshold (likely cloud/shadow over veg)."""
    return ndvi < threshold


def unmix_pixel(nir: float, red: float) -> tuple[float, float, float]:
    """Linear unmixing of one pixel into (f_veg, f_soil, f_cloud) fractions.

    Uses non-negative least-squares (NNLS) via hand-rolled projected gradient.
    Returns fractions summing to 1.
    """
    b = np.array([nir, red], dtype=np.float64)
    # Unconstrained least-squares solution
    AtA = _A @ _A.T  # 2×2 is invertible
    # NNLS via projected gradient (≤10 iterations is sufficient for 3 endmembers)
    f = np.ones(3) / 3.0
    for _ in range(40):
        grad = _A.T @ (_A @ f - b)
        f = np.clip(f - 0.05 * grad, 0, None)
        s = f.sum()
        if s > 0:
            f /= s
    return float(f[0]), float(f[1]), float(f[2])


def unmix_array(
    nir_norm: np.ndarray,
    red_norm: np.ndarray,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Vectorised unmixing (slower than per-pixel but avoids scipy dependency).

    Returns (f_veg, f_soil, f_cloud) arrays of same shape as inputs.
    """
    orig_shape = nir_norm.shape
    N = nir_norm.size
    nir_f = nir_norm.ravel().astype(np.float64)
    red_f = red_norm.ravel().astype(np.float64)

    fv = np.empty(N)
    fs = np.empty(N)
    fc = np.empty(N)
    for i in range(N):
        fv[i], fs[i], fc[i] = unmix_pixel(nir_f[i], red_f[i])
    return fv.reshape(orig_shape), fs.reshape(orig_shape), fc.reshape(orig_shape)


def clean_ndvi(
    nir_dn: np.ndarray,
    red_dn: np.ndarray,
    *,
    do_unmix: bool = True,
) -> dict:
    """Full NDVI cleaning pipeline.

    Parameters
    ----------
    nir_dn, red_dn : DN arrays (0-10000 scale, Sentinel-2 L2A)
    do_unmix       : run sub-pixel unmixing (slower; set False for quick pass)

    Returns
    -------
    dict with keys:
      ndvi_raw      : raw NDVI
      ndvi_clean    : cleaned NDVI (NaN where unrecoverable)
      mask_sat      : saturation mask
      mask_cloud    : cloud mask
      frac_veg      : vegetation fraction (if do_unmix)
      frac_soil     : soil fraction (if do_unmix)
      frac_cloud    : cloud fraction (if do_unmix)
      mean_ndvi     : scalar mean of valid clean pixels
      coverage_pct  : % of valid pixels
    """
    nir_norm = nir_dn.astype(np.float64) / 10000.0
    red_norm = red_dn.astype(np.float64) / 10000.0

    ndvi_raw = _ndvi(nir_norm, red_norm)
    mask_sat   = saturation_mask(nir_dn, red_dn)
    mask_cloud = cloud_mask(ndvi_raw)
    bad = mask_sat | mask_cloud

    ndvi_clean = ndvi_raw.copy()
    ndvi_clean[bad] = np.nan

    result: dict = {
        "ndvi_raw":   ndvi_raw,
        "ndvi_clean": ndvi_clean,
        "mask_sat":   mask_sat,
        "mask_cloud": mask_cloud,
    }

    if do_unmix and bad.any():
        fv, fs, fc = unmix_array(nir_norm, red_norm)
        # Recover pixels where we have meaningful vegetation fraction
        recoverable = bad & (fv > 0.15)
        total = fv + fs
        with np.errstate(invalid="ignore", divide="ignore"):
            ndvi_recovered = np.where(
                total > 0,
                (fv * NDVI_EV + fs * NDVI_ES) / total,
                np.nan,
            )
        ndvi_clean[recoverable] = ndvi_recovered[recoverable]
        result["frac_veg"]   = fv
        result["frac_soil"]  = fs
        result["frac_cloud"] = fc

    valid = ~np.isnan(ndvi_clean)
    result["mean_ndvi"]    = float(np.nanmean(ndvi_clean)) if valid.any() else 0.0
    result["coverage_pct"] = float(100.0 * valid.sum() / ndvi_clean.size)
    return result


if __name__ == "__main__":
    rng = np.random.default_rng(42)
    nir = rng.integers(2000, 8000, (20, 20), dtype=np.int32)
    red = rng.integers(500,  3000, (20, 20), dtype=np.int32)
    # Inject some saturated pixels
    nir[5, 5] = 9500; red[5, 5] = 9200
    # Inject cloud pixel
    nir[10, 10] = 3000; red[10, 10] = 4000

    r = clean_ndvi(nir, red, do_unmix=True)
    print(f"Mean NDVI clean:  {r['mean_ndvi']:.4f}")
    print(f"Coverage valid:   {r['coverage_pct']:.1f}%")
    print(f"Saturated pixels: {r['mask_sat'].sum()}")
    print(f"Cloud pixels:     {r['mask_cloud'].sum()}")
