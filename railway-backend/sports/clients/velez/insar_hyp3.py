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
import json, logging, math, os, tempfile
from datetime import date, datetime, timedelta
from typing import Optional

log = logging.getLogger(__name__)

_DEG_PER_M_LAT = 1 / 111_139
_DEG_PER_M_LON = 1 / 91_300   # at lat ≈ -34.6 (Argentina)

_INCIDENCE_DEG = 38.0
_COS_INC       = math.cos(math.radians(_INCIDENCE_DEG))

_ASF_SEARCH_URL   = "https://api.daac.asf.alaska.edu/services/search/param"
_PENDING_JOB_PATH = "/tmp/faro_pending_hyp3.json"   # survives within a Railway process


def _bbox(lat: float, lon: float, w_m: float = 80, h_m: float = 60, buf_m: float = 15) -> tuple:
    dlat = (h_m / 2 + buf_m) * _DEG_PER_M_LAT
    dlon = (w_m / 2 + buf_m) * _DEG_PER_M_LON
    return (lon - dlon, lat - dlat, lon + dlon, lat + dlat)


# ── Sector bboxes — rigid-surface infrastructure targets ──────────────────────
SECTOR_BBOXES: dict[str, tuple] = {
    # ── Estadio Amalfitani — global + 4 tribunas individuales ────────────────
    # Tribuna bboxes aíslan la señal InSAR por stand para detectar asentamiento diferencial.
    # Cada stand es ~150m×28m (norte/sur) o ~28m×120m (este/oeste), resolución 20m Sentinel-1.
    "estadio":                _bbox(-34.6373, -58.5240, w_m=200, h_m=150),
    "estadio_tribuna_norte":  _bbox(-34.6353, -58.5237, w_m=150, h_m=28, buf_m=5),
    "estadio_tribuna_sur":    _bbox(-34.6397, -58.5237, w_m=150, h_m=28, buf_m=5),
    "estadio_tribuna_este":   _bbox(-34.6373, -58.5209, w_m=28,  h_m=120, buf_m=5),
    "estadio_tribuna_oeste":  _bbox(-34.6373, -58.5265, w_m=28,  h_m=120, buf_m=5),
    # ── Polideportivo Feijóo ──────────────────────────────────────────────────
    "poli_basquet":           _bbox(-34.6390, -58.5195, w_m=60,  h_m=50),
    "poli_playon_norte":      _bbox(-34.6385, -58.5188, w_m=90,  h_m=70),
    # ── Sede + Piletas ────────────────────────────────────────────────────────
    "sede_anexo_norte":       _bbox(-34.6360, -58.5228, w_m=50,  h_m=40),
    "piletas":                _bbox(-34.6395, -58.5210, w_m=70,  h_m=55),
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

_SECTOR_TO_VENUE: dict[str, str | None] = {
    "estadio":           "amalfitani",
    "poli_basquet":      "villa_olimpica",
    "poli_playon_norte": "villa_olimpica",
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
        venue_id  = _SECTOR_TO_VENUE.get(sector_id)
        if venue_id is None:
            continue
        try:
            vh_db = round(vv_db - 7.5, 2)  # typical C-band VV/VH cross-pol ratio for grass
            theta, h_suc = _van_genuchten_from_sar(vv_db, vh_db)
            insert_soil_metrics(
                venue_id      = venue_id,
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


# ── Async job state — persiste dentro del proceso Railway ────────────────────

def _save_pending_job(job_id: str, job_name: str, g1: dict, g2: dict,
                      date1: str, date2: str) -> None:
    state = {
        "job_id":       job_id,
        "job_name":     job_name,
        "date1":        date1,
        "date2":        date2,
        "submitted_at": datetime.utcnow().isoformat(),
    }
    try:
        with open(_PENDING_JOB_PATH, "w") as _f:
            json.dump(state, _f)
        log.info("insar_hyp3: job pendiente guardado — %s (%s→%s)", job_id, date1, date2)
    except Exception as _e:
        log.debug("insar_hyp3: save pending job: %s", _e)


def _load_pending_job() -> dict | None:
    try:
        if os.path.exists(_PENDING_JOB_PATH):
            with open(_PENDING_JOB_PATH) as _f:
                return json.load(_f)
    except Exception:
        pass
    return None


def _clear_pending_job() -> None:
    try:
        if os.path.exists(_PENDING_JOB_PATH):
            os.unlink(_PENDING_JOB_PATH)
    except Exception:
        pass


def _hyp3_client():
    import hyp3_sdk
    username = (os.environ.get("NASA_EARTHDATA_USER")
                or os.environ.get("EARTHDATA_USERNAME", ""))
    password = (os.environ.get("NASA_EARTHDATA_PASS")
                or os.environ.get("EARTHDATA_PASSWORD", ""))
    if not username or not password:
        raise EnvironmentError(
            "NASA_EARTHDATA_USER + NASA_EARTHDATA_PASS not set in Railway env vars"
        )
    return hyp3_sdk.HyP3(username=username, password=password)


def _submit_job_nowait(granule1: dict, granule2: dict,
                       date1: str, date2: str) -> tuple[str, str]:
    """Submit D-InSAR job and return (job_id, job_name) immediately — no polling."""
    hyp3      = _hyp3_client()
    name1     = granule1.get("granuleName") or granule1.get("sceneName", "")
    name2     = granule2.get("granuleName") or granule2.get("sceneName", "")
    job_label = f"faro_{date1}_{date2}"
    log.info("insar_hyp3: submitting job %s + %s (async)", name1[:50], name2[:50])
    job = hyp3.submit_insar_job(name1, name2, name=job_label,
                                looks="20x4", include_displacement_maps=True)
    return job.job_id, job_label


def _check_and_collect(state: dict) -> "dict | str | None":
    """
    Poll pending HyP3 job once.
    Returns: result dict (SUCCEEDED), "running" (PENDING/RUNNING), None (FAILED/expired/not found).
    """
    job_id   = state["job_id"]
    job_name = state.get("job_name", "")
    date1    = state.get("date1", "?")
    date2    = state.get("date2", "?")

    # Expire jobs older than 3 hours (typical max HyP3 processing time)
    try:
        elapsed_s = (datetime.utcnow() -
                     datetime.fromisoformat(state["submitted_at"])).total_seconds()
    except Exception:
        elapsed_s = 0
    if elapsed_s > 10800:
        log.warning("insar_hyp3: job %s expirado (>3h) — descartando", job_id)
        return None

    try:
        hyp3 = _hyp3_client()
        # Search by job_name (unique per date pair) → fast, small result set
        batch = hyp3.get_jobs(name=job_name) if job_name else hyp3.get_jobs()
        job   = next((j for j in batch if j.job_id == job_id), None)
    except Exception as exc:
        log.warning("insar_hyp3: status check falló: %s — reintentando próximo ciclo", exc)
        return "running"

    if job is None:
        log.warning("insar_hyp3: job %s no encontrado en HyP3 — descartando", job_id)
        return None

    log.info("insar_hyp3: job %s status=%s (elapsed %.0fmin)",
             job_id, job.status_code, elapsed_s / 60)

    if job.status_code in ("PENDING", "RUNNING"):
        return "running"

    if job.status_code == "FAILED":
        log.warning("insar_hyp3: job %s FAILED", job_id)
        return None

    if job.status_code != "SUCCEEDED":
        return None

    # ── Descargar y procesar resultado ────────────────────────────────────
    log.info("insar_hyp3: SUCCEEDED — descargando par %s→%s", date1, date2)
    disp_path = amp_path = None
    try:
        disp_path, amp_path = _download_insar_files(job)
        sector_mm           = _read_sector_displacement(disp_path)
    except Exception as exc:
        log.warning("insar_hyp3: descarga/lectura falló: %s", exc)
        return None
    finally:
        for _p in filter(None, [disp_path, amp_path]):
            try: os.remove(_p)
            except Exception: pass
        if disp_path:
            try: os.rmdir(os.path.dirname(disp_path))
            except Exception: pass

    if not sector_mm:
        return None

    fuente_str = f"Sentinel-1 InSAR · ASF HyP3 · par {date1}/{date2}"
    if amp_path:
        try:
            sector_bs = _read_sector_backscatter(amp_path)
            if sector_bs:
                _write_soil_metrics(sector_bs, fuente=fuente_str, fecha_imagen=date2)
        except Exception as _be:
            log.debug("insar_hyp3: backscatter (non-fatal): %s", _be)

    log.info("✅ InSAR async — par %s/%s · %d sectores · %s",
             date1, date2, len(sector_mm),
             ", ".join(f"{k}:{v:+.2f}mm" for k, v in sector_mm.items()))
    return {
        "fuente":         fuente_str,
        "fecha_ref":      date1,
        "fecha_sec":      date2,
        "incidencia_deg": _INCIDENCE_DEG,
        "sectores":       sector_mm,
    }


def fetch_insar() -> Optional[dict]:
    """
    Two-phase async InSAR pipeline.

    Fase 2 (check): si hay un job pendiente en /tmp, consulta su estado.
      - SUCCEEDED → descarga y retorna resultados
      - RUNNING   → retorna None (volver a intentar próximo ciclo)
      - FAILED    → limpia y cae a Fase 1

    Fase 1 (submit): busca granules S1, encuentra par 12d, submite job sin bloquear.
      Retorna None — los resultados llegan en el siguiente ciclo (~30-60 min).

    El ciclo típico: ciclo 1 submite, ciclo 2 (30-60 min después) descarga.
    """
    try:
        import hyp3_sdk   # noqa — validate imports early
        import rasterio   # noqa
    except ImportError as exc:
        log.warning("insar_hyp3: missing dependency %s — skipping", exc)
        return None

    # ── Fase 2: verificar job pendiente ──────────────────────────────────
    pending = _load_pending_job()
    if pending:
        result = _check_and_collect(pending)
        if result == "running":
            log.info("insar_hyp3: job %s aún procesando — esperando próximo ciclo",
                     pending["job_id"])
            return None
        _clear_pending_job()
        if isinstance(result, dict):
            return result   # ✅ resultados listos
        log.info("insar_hyp3: job previo falló/expiró — submiteando nuevo job")

    # ── Fase 1: buscar par y submitear ────────────────────────────────────
    try:
        granules = _search_slc_granules(days_back=26)
    except Exception as exc:
        log.warning("insar_hyp3: ASF search failed: %s", exc)
        return None

    if not granules:
        log.warning("insar_hyp3: sin granules S1 SLC en los últimos 26 días")
        return None

    pair = _find_pair(granules)
    if not pair:
        log.warning("insar_hyp3: sin par coherente de 12 días")
        return None

    g1, g2 = pair
    date1  = (g1.get("startTime") or "")[:10]
    date2  = (g2.get("startTime") or "")[:10]
    log.info("insar_hyp3: par seleccionado %s → %s", date1, date2)

    try:
        job_id, job_name = _submit_job_nowait(g1, g2, date1, date2)
        _save_pending_job(job_id, job_name, g1, g2, date1, date2)
        log.info("insar_hyp3: job submiteado %s — resultados en próximo ciclo", job_id)
    except Exception as exc:
        log.warning("insar_hyp3: submit falló: %s", exc)

    return None  # resultados disponibles en el próximo ciclo
