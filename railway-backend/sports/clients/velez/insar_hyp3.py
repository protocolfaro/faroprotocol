"""
insar_hyp3.py — Weekly Sentinel-1 InSAR displacement monitoring via ASF HyP3.
Submits D-InSAR jobs for infrastructure sectors at Vélez Sarsfield complex.
Follows the same pattern as ndvi_real.py. Called from data_refresh.run_insar_refresh().

Sectors monitored (rigid-surface targets with stable InSAR coherence):
  estadio, poli_basquet, poli_playon_norte, sede_anexo_norte, piletas

Incidence angle: 38° (Sentinel-1 IW typical). LOS → vertical: v = LOS / cos(38°).
Auth: EARTHDATA_USERNAME + EARTHDATA_PASSWORD env vars (NASA Earthdata account).
"""
from __future__ import annotations
import logging, math, os, tempfile
from datetime import date, timedelta
from typing import Optional

log = logging.getLogger(__name__)

_DEG_PER_M_LAT = 1 / 111_139
_DEG_PER_M_LON = 1 / 91_300   # at lat ≈ -34.6 (Argentina)

_INCIDENCE_DEG = 38.0
_COS_INC       = math.cos(math.radians(_INCIDENCE_DEG))

_ASF_SEARCH_URL = "https://api.daac.asf.alaska.edu/services/search/param"


def _bbox(lat: float, lon: float, w_m: float = 80, h_m: float = 60, buf_m: float = 15) -> tuple:
    dlat = (h_m / 2 + buf_m) * _DEG_PER_M_LAT
    dlon = (w_m / 2 + buf_m) * _DEG_PER_M_LON
    return (lon - dlon, lat - dlat, lon + dlon, lat + dlat)


# ── Sector bboxes — rigid-surface infrastructure targets ──────────────────────
SECTOR_BBOXES: dict[str, tuple] = {
    "estadio":           _bbox(-34.6373, -58.5240, w_m=200, h_m=150),
    "poli_basquet":      _bbox(-34.6390, -58.5195, w_m=60,  h_m=50),
    "poli_playon_norte": _bbox(-34.6385, -58.5188, w_m=90,  h_m=70),
    "sede_anexo_norte":  _bbox(-34.6360, -58.5228, w_m=50,  h_m=40),
    "piletas":           _bbox(-34.6395, -58.5210, w_m=70,  h_m=55),
}


def _search_slc_granules(days_back: int = 30) -> list[dict]:
    """Search ASF Vertex for recent S1 IW SLC granules over Vélez complex."""
    import requests as _req
    end_dt   = date.today()
    start_dt = end_dt - timedelta(days=days_back)
    params = {
        "platform":        "SA,SB,SC,SD",  # 1A+1B+1C+1D — constelacion completa jun 2026
        "processingLevel": "SLC",
        "beamMode":        "IW",
        "intersectsWith":  "POINT(-58.5215 -34.6375)",
        "start":           start_dt.isoformat(),
        "end":             end_dt.isoformat(),
        "output":          "json",
        "maxResults":      "50",
    }
    r = _req.get(_ASF_SEARCH_URL, params=params, timeout=20)
    r.raise_for_status()
    data = r.json()
    # Response is [[granule, ...]] — outer list wraps results
    if isinstance(data, list) and data and isinstance(data[0], list):
        return data[0]
    return []


def _find_pair(granules: list[dict]) -> tuple[dict, dict] | None:
    """
    Group by relative orbit. Return the most recent 12-day pair (±2 days).
    Prefers the pair with the latest secondary date.
    """
    from collections import defaultdict
    from datetime import datetime as _dt

    def _parse_date(g: dict) -> _dt:
        t = g.get("startTime", "")
        try:
            return _dt.fromisoformat(t[:10])
        except Exception:
            return _dt.min

    by_orbit: dict[int, list] = defaultdict(list)
    for g in granules:
        orbit = (g.get("pathNumber") or g.get("relativeOrbit")
                 or g.get("track") or g.get("orbitNumber"))
        if orbit is not None:
            try:
                by_orbit[int(orbit)].append(g)
            except (ValueError, TypeError):
                pass
        else:
            # ASF API sometimes omits pathNumber — group by start-hour as proxy for relative orbit
            start = g.get("startTime", "")
            hour_key = int(start[11:13]) if len(start) >= 13 else 0
            by_orbit[hour_key].append(g)

    best_pair = None
    best_date = None

    for orbit_granules in by_orbit.values():
        sorted_g = sorted(orbit_granules, key=_parse_date)
        for i in range(len(sorted_g) - 1):
            for j in range(i + 1, len(sorted_g)):
                d1 = _parse_date(sorted_g[i])
                d2 = _parse_date(sorted_g[j])
                delta = (d2 - d1).days
                if 4 <= delta <= 14:  # acepta pares 6-day (1C+1D) y 12-day (1A,1C)
                    if best_date is None or d2 > best_date:
                        best_pair = (sorted_g[i], sorted_g[j])
                        best_date = d2

    return best_pair


def _submit_and_wait(granule1: dict, granule2: dict, timeout_s: int = 7200) -> object:
    """Submit HyP3 D-InSAR job and block until SUCCEEDED (up to timeout_s). Returns job."""
    import hyp3_sdk
    # Accept both naming conventions present in the project
    username = (os.environ.get("NASA_EARTHDATA_USER")
                or os.environ.get("EARTHDATA_USERNAME", ""))
    password = (os.environ.get("NASA_EARTHDATA_PASS")
                or os.environ.get("EARTHDATA_PASSWORD", ""))
    if not username or not password:
        raise EnvironmentError(
            "NASA Earthdata credentials not set — "
            "add NASA_EARTHDATA_USER + NASA_EARTHDATA_PASS to Railway env vars"
        )

    hyp3 = hyp3_sdk.HyP3(username=username, password=password)

    name1 = granule1.get("granuleName") or granule1.get("sceneName", "")
    name2 = granule2.get("granuleName") or granule2.get("sceneName", "")
    log.info("insar_hyp3: submitting job %s + %s", name1[:50], name2[:50])

    job = hyp3.submit_insar_job(name1, name2, looks="20x4", include_displacement_maps=True)
    batch = hyp3.watch([job], timeout=timeout_s, interval=300)

    completed = [j for j in batch if j.status_code == "SUCCEEDED"]
    if not completed:
        failed = [j for j in batch if j.status_code == "FAILED"]
        status = failed[0].status_code if failed else "TIMEOUT/RUNNING"
        raise RuntimeError(f"InSAR job did not succeed: {status}")

    return completed[0]


def _download_insar_files(job) -> tuple[str, str | None]:
    """
    Download HyP3 job zip. Extract *_los_displacement.tif and *_amp.tif (if present).
    Returns (disp_tif_path, amp_tif_path_or_None).
    """
    import io, zipfile
    import requests as _req

    files = job.files if hasattr(job, "files") else []
    zip_url = None
    for f in files:
        url = f.get("url", "") if isinstance(f, dict) else getattr(f, "url", "")
        if url.endswith(".zip"):
            zip_url = url
            break

    if not zip_url:
        raise RuntimeError("No .zip product in HyP3 job files")

    log.info("insar_hyp3: downloading %s", zip_url[-70:])
    r = _req.get(zip_url, timeout=180, stream=True)
    r.raise_for_status()

    tmp_dir  = tempfile.mkdtemp(prefix="insar_")
    disp_path: str | None = None
    amp_path:  str | None = None

    with zipfile.ZipFile(io.BytesIO(r.content)) as zf:
        namelist = zf.namelist()
        # Displacement TIF
        disps = [n for n in namelist if "los_displacement" in n and n.endswith(".tif")]
        if not disps:
            disps = [n for n in namelist if "displacement" in n and n.endswith(".tif")]
        if not disps:
            raise RuntimeError(f"No displacement GeoTIFF in zip: {namelist}")
        disp_path = os.path.join(tmp_dir, os.path.basename(disps[0]))
        with open(disp_path, "wb") as out:
            out.write(zf.read(disps[0]))
        # Amplitude TIF (HyP3 InSAR products include *_amp.tif from reference+secondary)
        amps = [n for n in namelist if n.endswith("_amp.tif")]
        if amps:
            amp_path = os.path.join(tmp_dir, os.path.basename(amps[0]))
            with open(amp_path, "wb") as out:
                out.write(zf.read(amps[0]))
            log.info("insar_hyp3: extracted amp TIF → %s", amp_path)

    log.info("insar_hyp3: extracted disp → %s, amp → %s", disp_path, amp_path or "none")
    return disp_path, amp_path


# keep old name for any external callers
def _download_displacement_tif(job) -> str:
    return _download_insar_files(job)[0]


def _read_sector_displacement(tif_path: str) -> dict[str, float]:
    """
    Windowed rasterio read over each sector bbox.
    LOS displacement (meters) → vertical (mm): vertical = LOS / cos(38°) × 1000
    Returns {sector_id: vertical_mm}.
    """
    import numpy as np
    import rasterio
    from rasterio.warp import transform_bounds
    from rasterio.windows import from_bounds

    results: dict[str, float] = {}
    with rasterio.open(tif_path) as src:
        crs    = src.crs
        nodata = src.nodata
        for sector_id, (minx, miny, maxx, maxy) in SECTOR_BBOXES.items():
            try:
                native = transform_bounds("EPSG:4326", crs, minx, miny, maxx, maxy)
                win    = from_bounds(*native, transform=src.transform)
                data   = src.read(1, window=win).astype("float32")

                mask = np.isfinite(data)
                if nodata is not None:
                    mask &= (data != float(nodata))
                mask &= (np.abs(data) < 1.0)  # discard values > 1m as outliers

                if mask.sum() < 2:
                    log.warning("insar_hyp3: %s — fewer than 2 valid pixels", sector_id)
                    continue

                los_mean_m  = float(data[mask].mean())
                vertical_mm = round(los_mean_m / _COS_INC * 1000, 2)
                results[sector_id] = vertical_mm
            except Exception as exc:
                log.warning("insar_hyp3: %s: %s", sector_id, exc)

    return results


def _read_sector_backscatter(amp_tif_path: str) -> dict[str, float]:
    """
    Per-sector SAR VV backscatter from HyP3 amplitude TIF.
    HyP3 amp.tif: float32 power units → dB = 10*log10(power).
    Returns {sector_id: sar_vv_db}.
    """
    import numpy as np
    import rasterio
    from rasterio.warp import transform_bounds
    from rasterio.windows import from_bounds

    results: dict[str, float] = {}
    with rasterio.open(amp_tif_path) as src:
        crs    = src.crs
        nodata = src.nodata
        for sector_id, (minx, miny, maxx, maxy) in SECTOR_BBOXES.items():
            try:
                native = transform_bounds("EPSG:4326", crs, minx, miny, maxx, maxy)
                win    = from_bounds(*native, transform=src.transform)
                data   = src.read(1, window=win).astype("float32")
                mask   = np.isfinite(data) & (data > 0)
                if nodata is not None:
                    mask &= (data != float(nodata))
                if mask.sum() < 2:
                    continue
                power_mean = float(data[mask].mean())
                results[sector_id] = round(10.0 * math.log10(max(power_mean, 1e-10)), 2)
            except Exception as exc:
                log.debug("insar_hyp3 backscatter %s: %s", sector_id, exc)
    return results


def _van_genuchten_from_sar(sar_vv_db: float, sar_vh_db: float) -> tuple[float, float]:
    """
    Water Cloud Model → Van Genuchten (Franco-Arenoso Deportivo).
    Returns (theta_soil, h_suction_cm).
    Mirrors compute_faro_hydro_core() from faro_analytics_physics but scalar-only.
    """
    import math as _m
    sig_vv = 10.0 ** (sar_vv_db / 10.0)
    sig_vh = 10.0 ** (sar_vh_db / 10.0)
    ratio  = sig_vh / sig_vv if sig_vv > 0 else 0.0
    sig_c  = sig_vh / 0.11 if ratio < 0.05 else sig_vv
    denom  = sig_c + sig_vh
    # WCM vegetation attenuation (LAI=2.5, θ=38.5°)
    lai   = 2.5
    cos_t = _m.cos(_m.radians(38.5))
    tau2  = _m.exp(-2.0 * 0.08 * lai / cos_t)
    s_veg = 0.0012 * lai * (1.0 - tau2) * cos_t
    s_soil = max((sig_c - s_veg) / (tau2 + 1e-6), 1e-4)
    theta  = (s_soil * 0.28) + 0.12
    # Van Genuchten
    theta_r, theta_s, alpha, n = 0.045, 0.410, 0.068, 1.89
    m = 1.0 - 1.0 / n
    theta_c = max(theta_r + 1e-4, min(theta_s - 1e-4, theta))
    se      = (theta_c - theta_r) / (theta_s - theta_r)
    inner   = max((se ** (-1.0 / m)) - 1.0, 0.0)
    h_suc   = (1.0 / alpha) * (inner ** (1.0 / n))
    return round(theta_c, 4), round(h_suc, 2)


# InSAR sector → cancha mapping for soil_metrics (only sectors with grass)
_SECTOR_TO_CANCHA: dict[str, str | None] = {
    "estadio":           "amalfitani",
    "poli_basquet":      "poli_f11",
    "poli_playon_norte": "poli_f11",
    "sede_anexo_norte":  None,
    "piletas":           None,
}


def _write_soil_metrics(sector_backscatter: dict[str, float], fuente: str,
                        fecha_imagen: str) -> None:
    """Non-blocking: write SAR soil_metrics to Supabase for each sector. Silent on failure."""
    try:
        import sys as _sys, os as _os
        _here = _os.path.dirname(_os.path.abspath(__file__))
        if _here not in _sys.path:
            _sys.path.insert(0, _here)
        from velez_supabase import insert_soil_metrics
    except Exception as _imp:
        log.debug("insar soil_metrics import (non-fatal): %s", _imp)
        return

    for sector_id, vv_db in sector_backscatter.items():
        cancha_id = _SECTOR_TO_CANCHA.get(sector_id)
        try:
            vh_db = round(vv_db - 7.5, 2)  # typical C-band VV/VH cross-pol ratio for grass
            theta, h_suc = _van_genuchten_from_sar(vv_db, vh_db)
            insert_soil_metrics(
                venue_id      = "amalfitani",  # FIX: era "velez", rompia todos los modulos cientificos
                cancha_id     = cancha_id or sector_id,
                sar_vv_db     = vv_db,
                sar_vh_db     = vh_db,
                theta_soil    = theta,
                h_suction_cm  = h_suc,
                fuente        = fuente,
                fecha_imagen  = fecha_imagen,
            )
        except Exception as _ie:
            log.debug("insar soil_metrics insert %s (non-fatal): %s", sector_id, _ie)


def fetch_insar() -> Optional[dict]:
    """
    Full S1 InSAR pipeline: search → pair → submit → wait → download → read.
    Returns dict with sector displacements + metadata, or None on any failure.
    """
    try:
        import hyp3_sdk   # noqa — validate imports early
        import rasterio   # noqa
    except ImportError as exc:
        log.warning("insar_hyp3: missing dependency %s — skipping", exc)
        return None

    try:
        granules = _search_slc_granules(days_back=26)
    except Exception as exc:
        log.warning("insar_hyp3: ASF search failed: %s", exc)
        return None

    if not granules:
        log.warning("insar_hyp3: ⚠️  No S1 SLC granules found in last 26 days")
        return None

    pair = _find_pair(granules)
    if not pair:
        log.warning("insar_hyp3: ⚠️  No coherent 12-day pair found")
        return None

    g1, g2   = pair
    date1    = (g1.get("startTime") or "")[:10]
    date2    = (g2.get("startTime") or "")[:10]
    log.info("insar_hyp3: selected pair %s → %s", date1, date2)

    try:
        job = _submit_and_wait(g1, g2)
    except Exception as exc:
        log.warning("insar_hyp3: HyP3 job failed: %s", exc)
        return None

    disp_path = amp_path = None
    try:
        disp_path, amp_path = _download_insar_files(job)
        sector_mm           = _read_sector_displacement(disp_path)
    except Exception as exc:
        log.warning("insar_hyp3: read failed: %s", exc)
        return None
    finally:
        for p in (disp_path, amp_path):
            if p:
                try:
                    os.remove(p)
                except Exception:
                    pass
        if disp_path:
            try:
                os.rmdir(os.path.dirname(disp_path))
            except Exception:
                pass

    if not sector_mm:
        log.warning("insar_hyp3: ⚠️  No valid sector readings from displacement map")
        return None

    fuente_str = f"Sentinel-1 InSAR · ASF HyP3 · par {date1}/{date2}"

    # SAR backscatter from amplitude TIF → soil_metrics (non-blocking)
    if amp_path:
        try:
            sector_bs = _read_sector_backscatter(amp_path)
            if sector_bs:
                _write_soil_metrics(sector_bs, fuente=fuente_str, fecha_imagen=date2)
            else:
                log.debug("insar_hyp3: amp TIF returned no backscatter — soil_metrics skipped")
        except Exception as _be:
            log.debug("insar_hyp3: backscatter read (non-fatal): %s", _be)
    else:
        log.debug("insar_hyp3: no amp TIF in zip — soil_metrics skipped (displacement only)")

    log.info(
        "✅ InSAR — par %s/%s · %d sectores · %s",
        date1, date2, len(sector_mm),
        ", ".join(f"{k}:{v:+.2f}mm" for k, v in sector_mm.items()),
    )
    return {
        "fuente":         fuente_str,
        "fecha_ref":      date1,
        "fecha_sec":      date2,
        "incidencia_deg": _INCIDENCE_DEG,
        "sectores":       sector_mm,
    }
