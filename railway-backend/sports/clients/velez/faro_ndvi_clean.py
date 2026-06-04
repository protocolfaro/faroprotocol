"""faro_ndvi_clean.py — CLOSDI shadow masking + spectral unmixing + NDVI cleaning
Faro Protocol · numpy only

Cleaning pipeline (applied in order):
  1. Saturation mask  : NIR > 9000 or RED > 9000 DN  → discard
  2. CLOSDI shadow    : 250*(NIR+RED)/(NIR+2.4*RED+1) < 34.0  → discard dark shadows
                        Closed-form from NIR+RED only (Cal 2025, doi:10.1016/j.rsase.2026.101990)
  3. Cloud mask       : NDVI < 0.0  → discard bright cloud / water / ice
  4. External mask    : optional pre-computed bad-pixel mask (e.g. SCL classes 0,1,3,8,9,10)
  5. Spectral unmixing: optionally recover borderline pixels via EV/ES/EC endmembers
"""
from __future__ import annotations
import numpy as np

# Band reflectance endmembers (NIR, RED) — normalised 0-1, Sentinel-2 BOA B08/B04
EV_NIR, EV_RED = 0.78, 0.06   # dense vegetation
ES_NIR, ES_RED = 0.24, 0.22   # bare soil / dry grass
EC_NIR, EC_RED = 0.90, 0.88   # bright cloud / snow / saturation

NDVI_EV = (EV_NIR - EV_RED) / (EV_NIR + EV_RED)   # ≈ 0.854
NDVI_ES = (ES_NIR - ES_RED) / (ES_NIR + ES_RED)   # ≈ 0.043

SAT_THRESH = 9000          # DN saturation threshold (Sentinel-2 L2A 0-10000)
CLOSDI_THRESH = 34.0       # Cal 2025 — shadow when CLOSDI < 34.0

_A = np.array([
    [EV_NIR, ES_NIR, EC_NIR],
    [EV_RED, ES_RED, EC_RED],
], dtype=np.float64)


def _ndvi(nir: np.ndarray, red: np.ndarray) -> np.ndarray:
    denom = nir + red
    with np.errstate(invalid="ignore", divide="ignore"):
        return np.where(denom > 0, (nir - red) / denom, 0.0)


def _evi2(nir: np.ndarray, red: np.ndarray) -> np.ndarray:
    """EVI2 = 2.5*(NIR-RED)/(NIR+2.4*RED+1) — no blue band required."""
    with np.errstate(invalid="ignore", divide="ignore"):
        return np.clip(2.5 * (nir - red) / (nir + 2.4 * red + 1.0), -1.0, 1.0)


def saturation_mask(nir_dn: np.ndarray, red_dn: np.ndarray) -> np.ndarray:
    """True where pixel is saturated (DN > 9000)."""
    return (nir_dn > SAT_THRESH) | (red_dn > SAT_THRESH)


def cloud_mask(ndvi: np.ndarray, threshold: float = 0.0) -> np.ndarray:
    """True where NDVI < threshold — bright cloud, water, ice."""
    return ndvi < threshold


def closdi_shadow_mask(nir: np.ndarray, red: np.ndarray) -> np.ndarray:
    """CLOSDI pixel-level cloud shadow mask (Cal 2025).

    CLOSDI = 250 * (NIR + RED) / (NIR + 2.4*RED + 1)
           = 100 * EVI2 / NDVI  (closed-form, NIR+RED only)

    Derived from differential sensitivity of NDVI vs EVI2 to shadow conditions.
    Shadow pixels have low NIR → both indices drop, but EVI2 drops proportionally
    more (background-correction denominator), so CLOSDI < 34.0 reliably flags them.

    Threshold 34.0 maximises median IoU on the CloudSEN12 dataset.
    """
    with np.errstate(invalid="ignore", divide="ignore"):
        closdi = 250.0 * (nir + red) / (nir + 2.4 * red + 1.0)
    valid = (nir + red) > 0.02   # skip no-data / fill pixels
    return valid & (closdi < CLOSDI_THRESH)


def unmix_pixel(nir: float, red: float) -> tuple[float, float, float]:
    """Linear unmixing into (f_veg, f_soil, f_cloud) fractions via NNLS."""
    b = np.array([nir, red], dtype=np.float64)
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
    """Vectorised unmixing. Returns (f_veg, f_soil, f_cloud) same shape as inputs."""
    orig_shape = nir_norm.shape
    N = nir_norm.size
    nir_f = nir_norm.ravel().astype(np.float64)
    red_f = red_norm.ravel().astype(np.float64)
    fv = np.empty(N); fs = np.empty(N); fc = np.empty(N)
    for i in range(N):
        fv[i], fs[i], fc[i] = unmix_pixel(nir_f[i], red_f[i])
    return fv.reshape(orig_shape), fs.reshape(orig_shape), fc.reshape(orig_shape)


def clean_ndvi(
    nir_dn: np.ndarray,
    red_dn: np.ndarray,
    *,
    do_unmix: bool = True,
    external_mask: np.ndarray | None = None,
) -> dict:
    """Full NDVI cleaning pipeline.

    Parameters
    ----------
    nir_dn, red_dn  : raw DN arrays (0-10000 scale, Sentinel-2 L2A)
    do_unmix        : run sub-pixel unmixing to recover borderline pixels
    external_mask   : additional bad-pixel boolean mask (e.g. from SCL band).
                      True = bad pixel, will be discarded before mean NDVI.

    Returns
    -------
    dict with: ndvi_raw, ndvi_clean, mask_sat, mask_cloud, mask_shadow,
               mean_ndvi, coverage_pct, [frac_veg/soil/cloud if do_unmix]
    """
    nir_norm = nir_dn.astype(np.float64) / 10000.0
    red_norm = red_dn.astype(np.float64) / 10000.0

    ndvi_raw    = _ndvi(nir_norm, red_norm)
    mask_sat    = saturation_mask(nir_dn, red_dn)
    mask_shadow = closdi_shadow_mask(nir_norm, red_norm)   # dark cloud shadows
    mask_cloud  = cloud_mask(ndvi_raw)                     # bright cloud / water

    bad = mask_sat | mask_shadow | mask_cloud
    if external_mask is not None:
        bad |= external_mask.astype(bool)

    ndvi_clean = ndvi_raw.copy()
    ndvi_clean[bad] = np.nan

    result: dict = {
        "ndvi_raw":    ndvi_raw,
        "ndvi_clean":  ndvi_clean,
        "mask_sat":    mask_sat,
        "mask_shadow": mask_shadow,
        "mask_cloud":  mask_cloud,
    }

    if do_unmix and bad.any():
        fv, fs, fc = unmix_array(nir_norm, red_norm)
        # Recover pixels where vegetation fraction is meaningful
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
    # Saturated pixel
    nir[5, 5] = 9500; red[5, 5] = 9200
    # Cloud shadow: NIR=500, RED=400 → CLOSDI = 250*0.09/1.146 = 19.6 < 34 → shadow
    nir[10, 10] = 500; red[10, 10] = 400
    # Bright cloud: NIR=8000, RED=8500 → NDVI < 0 → cloud
    nir[15, 15] = 8000; red[15, 15] = 8500

    r = clean_ndvi(nir, red, do_unmix=True)
    print(f"Mean NDVI clean:  {r['mean_ndvi']:.4f}")
    print(f"Coverage valid:   {r['coverage_pct']:.1f}%")
    print(f"Saturated pixels: {r['mask_sat'].sum()}")
    print(f"Shadow pixels:    {r['mask_shadow'].sum()}")
    print(f"Cloud pixels:     {r['mask_cloud'].sum()}")
