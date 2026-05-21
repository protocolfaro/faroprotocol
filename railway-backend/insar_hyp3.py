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


def _search_slc_granules(days_back: int = 26) -> list[dict]:
    """Search ASF Vertex for recent S1 IW SLC granules over Vélez complex."""
    import requests as _req
    end_dt   = date.today()
    start_dt = end_dt - timedelta(days=days_back)
    params = {
        "platform":        "SA,SB",
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
        orbit = g.get("pathNumber") or g.get("relativeOrbit")
        if orbit is not None:
            by_orbit[int(orbit)].append(g)

    best_pair = None
    best_date = None

    for orbit_granules in by_orbit.values():
        sorted_g = sorted(orbit_granules, key=_parse_date)
        for i in range(len(sorted_g) - 1):
            for j in range(i + 1, len(sorted_g)):
                d1 = _parse_date(sorted_g[i])
                d2 = _parse_date(sorted_g[j])
                delta = (d2 - d1).days
                if 10 <= delta <= 14:
                    if best_date is None or d2 > best_date:
                        best_pair = (sorted_g[i], sorted_g[j])
                        best_date = d2

    return best_pair


def _submit_and_wait(granule1: dict, granule2: dict, timeout_s: int = 7200) -> object:
    """Submit HyP3 D-InSAR job and block until SUCCEEDED (up to timeout_s). Returns job."""
    import hyp3_sdk
    username = os.environ.get("EARTHDATA_USERNAME", "")
    password = os.environ.get("EARTHDATA_PASSWORD", "")
    if not username or not password:
        raise EnvironmentError("EARTHDATA_USERNAME / EARTHDATA_PASSWORD not set")

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


def _download_displacement_tif(job) -> str:
    """Download and extract *_los_displacement.tif from HyP3 job zip. Returns local path."""
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

    tmp_dir = tempfile.mkdtemp(prefix="insar_")
    with zipfile.ZipFile(io.BytesIO(r.content)) as zf:
        candidates = [n for n in zf.namelist() if "los_displacement" in n and n.endswith(".tif")]
        if not candidates:
            candidates = [n for n in zf.namelist() if "displacement" in n and n.endswith(".tif")]
        if not candidates:
            raise RuntimeError(f"No displacement GeoTIFF in zip: {zf.namelist()}")
        tif_name = candidates[0]
        tif_path = os.path.join(tmp_dir, os.path.basename(tif_name))
        with open(tif_path, "wb") as out:
            out.write(zf.read(tif_name))

    log.info("insar_hyp3: extracted %s → %s", tif_name, tif_path)
    return tif_path


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

    tif_path = None
    try:
        tif_path    = _download_displacement_tif(job)
        sector_mm   = _read_sector_displacement(tif_path)
    except Exception as exc:
        log.warning("insar_hyp3: read failed: %s", exc)
        return None
    finally:
        if tif_path:
            try:
                os.remove(tif_path)
                os.rmdir(os.path.dirname(tif_path))
            except Exception:
                pass

    if not sector_mm:
        log.warning("insar_hyp3: ⚠️  No valid sector readings from displacement map")
        return None

    log.info(
        "✅ InSAR — par %s/%s · %d sectores · %s",
        date1, date2, len(sector_mm),
        ", ".join(f"{k}:{v:+.2f}mm" for k, v in sector_mm.items()),
    )
    return {
        "fuente":         f"Sentinel-1 InSAR · ASF HyP3 · par {date1}/{date2}",
        "fecha_ref":      date1,
        "fecha_sec":      date2,
        "incidencia_deg": _INCIDENCE_DEG,
        "sectores":       sector_mm,
    }
