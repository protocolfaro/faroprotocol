"""
dale_play_insar_cdse.py — Interferometría SAR Sentinel-1 via openEO CDSE.

El procesamiento InSAR corre íntegramente en servidores de Copernicus Data Space
Ecosystem (CDSE). Railway solo:
  1. Obtiene token OIDC con CDSE_USER / CDSE_PASS (protocolfaro@gmail.com)
  2. Envía un process graph openEO (sentinel1_sar_interferogram) vía REST
  3. Espera resultado — max 8 minutos para AOI 2.5km × 1.5km (Amalfitani)
  4. Descarga GeoTIFF resultado (~100 KB a 20m resolución)
  5. Samplea píxeles en centroides de sector → mm de deformación / sector
  6. Convierte a velocidad mm/año y cachea en Supabase insar_cdse_results (TTL 30d)

Física:
  λ (S1 C-band) = 55.6 mm; d_LOS = (λ/4π) × φ_unwrapped
  θ_inc ≈ 38° (descending, lat −34°S) → d_vert = d_LOS / cos(38°)
  K = 55.6 / (4π × 0.788) ≈ 5.61 mm/radian

Salida: misma estructura que fetch_egms_amalfitani() para compatibilidad directa
con StructuralTwinModule — sectores {campo, tribuna_norte/sur/este/oeste} con
vel_mm_yr, coherencia, nivel, tendencia.

SQL (ejecutar en Supabase SQL Editor):
  CREATE TABLE IF NOT EXISTS insar_cdse_results (
    venue_id          TEXT PRIMARY KEY,
    job_id            TEXT,
    ref_date          TEXT,
    sec_date          TEXT,
    temporal_baseline_days INTEGER,
    sectores          JSONB,
    vel_max_abs_mm_yr REAL,
    coherencia_media  REAL,
    computed_at       TIMESTAMPTZ DEFAULT NOW()
  );

Credenciales Railway: CDSE_USER=protocolfaro@gmail.com, CDSE_PASS=<contraseña CDSE>
Fallback: si CDSE no responde o credenciales no configuradas → None (egms usa datos 2022).
"""
from __future__ import annotations

import logging
import math
import os
import time
from datetime import date, timedelta
from typing import Optional

import requests

log = logging.getLogger(__name__)

# ── CDSE openEO endpoints ──────────────────────────────────────────────────────
_CDSE_TOKEN_URL  = "https://identity.dataspace.copernicus.eu/auth/realms/CDSE/protocol/openid-connect/token"
_CDSE_OPENEO_URL = "https://openeo.dataspace.copernicus.eu/openeo/1.1"

# ── AOI: Estadio José Amalfitani (2.5km × 1.5km, incluye todas las tribunas) ──
_BBOX = {"west": -58.540, "south": -34.645, "east": -58.515, "north": -34.630}

# Centroides de sector para sampleo del GeoTIFF (lon, lat)
_SECTOR_CENTROIDS = {
    "campo":         (-58.528, -34.638),
    "tribuna_norte": (-58.528, -34.633),
    "tribuna_sur":   (-58.528, -34.643),
    "tribuna_este":  (-58.522, -34.638),
    "tribuna_oeste": (-58.534, -34.638),
}

_SECTOR_NOMBRES = {
    "campo":         "Campo de juego",
    "tribuna_norte": "Tribuna Norte",
    "tribuna_sur":   "Tribuna Sur",
    "tribuna_este":  "Tribuna Este",
    "tribuna_oeste": "Tribuna Oeste (Principal)",
}

# ── Parámetros S1 ──────────────────────────────────────────────────────────────
_S1_WAVELENGTH_MM = 55.6           # C-band λ = 5.56 cm
_INCIDENCE_DEG    = 38.0           # θ_inc descending, lat −34°S
_PHASE_TO_DISP_MM = _S1_WAVELENGTH_MM / (4 * math.pi * math.cos(math.radians(_INCIDENCE_DEG)))

_COHERENCE_MIN    = 0.30           # umbral mínimo para pixel confiable
_POLL_INTERVAL_S  = 20
_POLL_MAX_S       = 480            # 8 minutos — AOI pequeño, CDSE rápido
_CACHE_TTL_DAYS   = 30
_SUPA_TIMEOUT     = 12


# ── Auth ───────────────────────────────────────────────────────────────────────

def _is_configured() -> bool:
    return bool(os.environ.get("CDSE_USER")) and bool(os.environ.get("CDSE_PASS"))


def _cdse_token() -> Optional[str]:
    """Obtiene access token OIDC via password grant (CDSE_USER + CDSE_PASS)."""
    user = os.environ.get("CDSE_USER", "")
    pw   = os.environ.get("CDSE_PASS", "")
    if not user or not pw:
        return None
    try:
        r = requests.post(
            _CDSE_TOKEN_URL,
            data={
                "grant_type": "password",
                "client_id":  "cdse-public",
                "username":   user,
                "password":   pw,
            },
            timeout=30,
        )
        r.raise_for_status()
        token = r.json().get("access_token")
        log.info("insar_cdse: token CDSE OK (user=%s)", user)
        return token
    except Exception as exc:
        log.warning("insar_cdse: auth falló: %s", exc)
        return None


def _headers(token: str) -> dict:
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type":  "application/json",
    }


# ── Selección de fechas S1 ─────────────────────────────────────────────────────

def _select_dates() -> tuple[str, str]:
    """
    Elige par de fechas de referencia/secundaria para el interferograma.
    Baseline temporal óptimo: 24-48 días (2-4 ciclos de 12d S1).
    Usa ventana de 6 meses atrás para asegurar disponibilidad.
    """
    today    = date.today()
    sec_end  = today - timedelta(days=10)      # imagen secundaria: hasta hace 10 días
    sec_start = sec_end - timedelta(days=18)   # ventana de 18d para encontrar al menos 1 pase
    ref_end  = sec_start - timedelta(days=12)  # separación mínima 12d (un ciclo S1)
    ref_start = ref_end - timedelta(days=18)

    return ref_start.isoformat(), sec_end.isoformat()


# ── Process graph openEO ───────────────────────────────────────────────────────

def _build_process_graph(ref_start: str, sec_end: str) -> dict:
    """
    Process graph openEO para sentinel1_sar_interferogram sobre el Amalfitani.

    load_collection SENTINEL1_SLC con ventana temporal que abarca ref+sec →
    CDSE selecciona el par de acquisitions óptimo → sentinel1_sar_interferogram
    computa coherencia + fase → save_result GTiff 20m.
    """
    return {
        "title":       "InSAR Amalfitani — Faro Protocol",
        "description": "Sentinel-1 SLC interferogram — Estadio Amalfitani, BsAs",
        "process": {
            "process_graph": {
                "load_s1": {
                    "process_id": "load_collection",
                    "arguments": {
                        "id": "SENTINEL1_SLC",
                        "spatial_extent": _BBOX,
                        "temporal_extent": [ref_start, sec_end],
                        "bands": ["VV"],
                        "properties": {
                            "sat:orbit_state": {
                                "process_id": "eq",
                                "arguments": {
                                    "x":  {"from_parameter": "value"},
                                    "y":  "DESCENDING",
                                },
                            }
                        },
                    },
                },
                "interferogram": {
                    "process_id": "sentinel1_sar_interferogram",
                    "arguments": {
                        "data":               {"from_node": "load_s1"},
                        "remove_phase_ramp":  True,
                        "coherence_window":   4,
                        "flatten_temporal":   True,
                    },
                },
                "save": {
                    "process_id": "save_result",
                    "arguments": {
                        "data":   {"from_node": "interferogram"},
                        "format": "GTiff",
                        "options": {"dtype": "float32"},
                    },
                    "result": True,
                },
            }
        },
    }


# ── Job lifecycle ──────────────────────────────────────────────────────────────

def _submit_job(token: str, process: dict) -> Optional[str]:
    """Crea un batch job en CDSE openEO. Retorna job_id o None."""
    try:
        r = requests.post(
            f"{_CDSE_OPENEO_URL}/jobs",
            headers=_headers(token),
            json=process,
            timeout=30,
        )
        r.raise_for_status()
        job_id = r.json().get("id") or r.headers.get("OpenEO-Identifier")
        log.info("insar_cdse: job creado — id=%s", job_id)
        return job_id
    except Exception as exc:
        log.warning("insar_cdse: submit_job falló: %s", exc)
        return None


def _start_job(token: str, job_id: str) -> bool:
    """Inicia el procesamiento del batch job."""
    try:
        r = requests.post(
            f"{_CDSE_OPENEO_URL}/jobs/{job_id}/results",
            headers=_headers(token),
            timeout=20,
        )
        ok = r.status_code in (200, 202, 204)
        log.info("insar_cdse: job %s iniciado — HTTP %d", job_id, r.status_code)
        return ok
    except Exception as exc:
        log.warning("insar_cdse: start_job falló: %s", exc)
        return False


def _poll_job(token: str, job_id: str) -> str:
    """
    Espera hasta que el job termine (finished/error/canceled).
    Retorna el status final. Máx _POLL_MAX_S segundos.
    """
    deadline = time.time() + _POLL_MAX_S
    while time.time() < deadline:
        try:
            r = requests.get(
                f"{_CDSE_OPENEO_URL}/jobs/{job_id}",
                headers=_headers(token),
                timeout=20,
            )
            r.raise_for_status()
            status = r.json().get("status", "unknown")
            log.debug("insar_cdse: job %s → %s", job_id, status)
            if status in ("finished", "error", "canceled"):
                return status
        except Exception as exc:
            log.debug("insar_cdse: poll error: %s", exc)
        time.sleep(_POLL_INTERVAL_S)
    return "timeout"


def _get_result_url(token: str, job_id: str) -> Optional[str]:
    """Obtiene URL del primer asset GeoTIFF del job terminado."""
    try:
        r = requests.get(
            f"{_CDSE_OPENEO_URL}/jobs/{job_id}/results",
            headers=_headers(token),
            timeout=20,
        )
        r.raise_for_status()
        assets = r.json().get("assets") or {}
        for key, asset in assets.items():
            atype = asset.get("type", "")
            if "tiff" in atype.lower() or key.endswith(".tif"):
                return asset.get("href")
        # Fallback: primer asset
        if assets:
            return next(iter(assets.values())).get("href")
        return None
    except Exception as exc:
        log.warning("insar_cdse: get_result_url falló: %s", exc)
        return None


def _download_tiff(token: str, url: str) -> Optional[bytes]:
    """Descarga el GeoTIFF resultado (~100KB para AOI Amalfitani)."""
    try:
        r = requests.get(
            url,
            headers={"Authorization": f"Bearer {token}"},
            timeout=60,
        )
        r.raise_for_status()
        log.info("insar_cdse: GeoTIFF descargado — %d KB", len(r.content) // 1024)
        return r.content
    except Exception as exc:
        log.warning("insar_cdse: download_tiff falló: %s", exc)
        return None


# ── Extracción de deformación por sector ──────────────────────────────────────

def _extract_sectors(tiff_bytes: bytes, temporal_baseline_days: int) -> dict:
    """
    Samplea el GeoTIFF en los centroides de sector y convierte fase → mm/año.

    Bandas esperadas del proceso sentinel1_sar_interferogram:
      Band 1: coherence [0, 1]
      Band 2: wrapped_phase [−π, π] radians  (o unwrapped si disponible)
      Band 3 (opcional): unwrapped_phase radians

    Conversión:
      d_LOS    = (λ/4π) × φ_unwrapped
      d_vert   = d_LOS / cos(θ_inc)   [negativo = subsidencia]
      vel_mm_yr = d_vert / (baseline_days / 365.25)
    """
    import io
    import numpy as np
    import rasterio
    from rasterio.transform import rowcol

    sectors: dict = {}

    with rasterio.open(io.BytesIO(tiff_bytes)) as src:
        crs       = src.crs
        transform = src.transform
        n_bands   = src.count

        # Detectar bandas (algunas implementaciones usan orden distinto)
        coh_band   = 1
        phase_band = 2 if n_bands >= 2 else 1

        log.info("insar_cdse: GeoTIFF %dx%d, %d bandas, CRS=%s",
                 src.width, src.height, n_bands, crs)

        from rasterio.warp import transform as warp_transform

        for sid, (lon, lat) in _SECTOR_CENTROIDS.items():
            try:
                # Transformar centroide a CRS del raster
                xs, ys = warp_transform("EPSG:4326", crs, [lon], [lat])
                col, row = ~transform * (xs[0], ys[0])
                col, row = int(col), int(row)

                # Ventana 5×5 píxeles (100m × 100m a 20m resolución)
                r0  = max(0, row - 2)
                r1  = min(src.height, row + 3)
                c0  = max(0, col - 2)
                c1  = min(src.width, col + 3)
                win = rasterio.windows.Window(c0, r0, c1 - c0, r1 - r0)

                coh_data   = src.read(coh_band,   window=win).astype("float32")
                phase_data = src.read(phase_band, window=win).astype("float32")

                # Usar banda 3 (unwrapped) si existe y tiene datos no nulos
                if n_bands >= 3:
                    uw_data = src.read(3, window=win).astype("float32")
                    uw_valid = np.isfinite(uw_data) & (uw_data != 0)
                    if uw_valid.sum() > 0:
                        phase_data = uw_data

                # Filtrar por coherencia
                valid_mask = (
                    np.isfinite(coh_data) &
                    np.isfinite(phase_data) &
                    (coh_data >= _COHERENCE_MIN)
                )
                n_valid = int(valid_mask.sum())

                if n_valid == 0:
                    sectors[sid] = {
                        "nombre":      _SECTOR_NOMBRES[sid],
                        "vel_mm_yr":   0.0,
                        "std_mm_yr":   None,
                        "coherencia":  float(np.nanmean(coh_data)),
                        "n_pixeles":   0,
                        "nota":        "Coherencia insuficiente — sin medición confiable",
                        "nivel":       "sin_datos",
                        "tendencia":   "desconocida",
                        "critico":     False,
                    }
                    continue

                coh_mean   = float(np.mean(coh_data[valid_mask]))
                phase_mean = float(np.mean(phase_data[valid_mask]))
                phase_std  = float(np.std(phase_data[valid_mask]))

                # Conversión fase → desplazamiento vertical mm
                d_vert_mm = _PHASE_TO_DISP_MM * phase_mean
                # Velocidad anual (negativo = subsidencia)
                vel_mm_yr = d_vert_mm / (temporal_baseline_days / 365.25)
                std_mm_yr = (_PHASE_TO_DISP_MM * phase_std /
                             (temporal_baseline_days / 365.25))

                nivel    = "ok" if abs(vel_mm_yr) < 2.5 else (
                           "atencion" if abs(vel_mm_yr) < 3.5 else "critico")
                tendencia = "estable" if abs(vel_mm_yr) < 3.0 else (
                            "moderada" if abs(vel_mm_yr) < 3.5 else "acelerada")

                sectors[sid] = {
                    "nombre":      _SECTOR_NOMBRES[sid],
                    "vel_mm_yr":   round(vel_mm_yr, 2),
                    "std_mm_yr":   round(std_mm_yr, 2),
                    "coherencia":  round(coh_mean, 3),
                    "n_pixeles":   n_valid,
                    "nota":        f"S1 InSAR CDSE — baseline {temporal_baseline_days}d",
                    "nivel":       nivel,
                    "tendencia":   tendencia,
                    "critico":     nivel == "critico",
                }
                log.info(
                    "insar_cdse: %s vel=%.2f mm/yr coh=%.2f px=%d",
                    sid, vel_mm_yr, coh_mean, n_valid,
                )

            except Exception as exc:
                log.warning("insar_cdse: sector %s error: %s", sid, exc)
                sectors[sid] = {
                    "nombre":    _SECTOR_NOMBRES.get(sid, sid),
                    "vel_mm_yr": 0.0,
                    "coherencia": None,
                    "nota":      f"Error extrayendo sector: {exc!s:.80}",
                    "nivel":     "sin_datos",
                    "tendencia": "desconocida",
                    "critico":   False,
                }

    return sectors


# ── Supabase cache ─────────────────────────────────────────────────────────────

def _supa() -> tuple[str, str]:
    return os.environ.get("SUPABASE_URL", ""), os.environ.get("SUPABASE_KEY", "")


def _save_cache(result: dict) -> None:
    supa_url, supa_key = _supa()
    if not supa_url or not supa_key:
        return
    try:
        requests.post(
            f"{supa_url}/rest/v1/insar_cdse_results",
            headers={
                "apikey":        supa_key,
                "Authorization": f"Bearer {supa_key}",
                "Content-Type":  "application/json",
                "Prefer":        "resolution=merge-duplicates",
            },
            json=result,
            timeout=_SUPA_TIMEOUT,
        )
        log.info("insar_cdse: resultado cacheado en Supabase")
    except Exception as exc:
        log.debug("insar_cdse: cache save error: %s", exc)


def _load_cache(venue_id: str = "amalfitani") -> Optional[dict]:
    """Lee el resultado cacheado si existe y tiene menos de _CACHE_TTL_DAYS días."""
    supa_url, supa_key = _supa()
    if not supa_url or not supa_key:
        return None
    try:
        r = requests.get(
            f"{supa_url}/rest/v1/insar_cdse_results",
            headers={
                "apikey":        supa_key,
                "Authorization": f"Bearer {supa_key}",
            },
            params={"venue_id": f"eq.{venue_id}", "select": "*"},
            timeout=_SUPA_TIMEOUT,
        )
        if not r.ok or not r.json():
            return None
        row = r.json()[0]

        # Verificar TTL
        computed_at = row.get("computed_at", "")
        if computed_at:
            from datetime import datetime, timezone
            ts  = datetime.fromisoformat(computed_at.replace("Z", "+00:00"))
            age = (datetime.now(timezone.utc) - ts).days
            if age > _CACHE_TTL_DAYS:
                log.info("insar_cdse: cache expirado (%d días)", age)
                return None

        log.info("insar_cdse: cache CDSE cargado desde Supabase")
        return row
    except Exception as exc:
        log.debug("insar_cdse: cache load error: %s", exc)
        return None


# ── Entry point ────────────────────────────────────────────────────────────────

def fetch_insar_cdse(venue_id: str = "amalfitani") -> Optional[dict]:
    """
    Ejecuta el pipeline InSAR CDSE y retorna estructura compatible con
    fetch_egms_amalfitani():
      {venue, sectores, sector_critico, vel_max_abs_mm_yr, fuente, ...}

    1. Intenta cache Supabase (TTL 30 días) → retorno inmediato
    2. Si no hay cache o expiró: autentica CDSE → envía job → espera (máx 8min)
    3. Descarga GeoTIFF → samplea sectores → convierte fase→mm → cachea
    4. Retorna None si CDSE no está configurado o falla (egms usa fallback 2022)

    Nunca lanza excepciones.
    """
    # ── 1. Cache ──────────────────────────────────────────────────────────────
    cached = _load_cache(venue_id)
    if cached:
        log.info("insar_cdse: usando cache (ref=%s → sec=%s)",
                 cached.get("ref_date"), cached.get("sec_date"))
        return _cached_to_egms_format(cached)

    # ── 2. CDSE configurado? ──────────────────────────────────────────────────
    if not _is_configured():
        log.debug("insar_cdse: CDSE_USER/PASS no configurados — sin datos CDSE")
        return None

    try:
        # ── 3. Auth + fechas ──────────────────────────────────────────────────
        token = _cdse_token()
        if not token:
            return None

        ref_start, sec_end = _select_dates()
        log.info("insar_cdse: ventana temporal %s → %s", ref_start, sec_end)

        # Baseline temporal aproximado (distancia al centro de cada ventana)
        ref_center = date.fromisoformat(ref_start) + timedelta(days=9)
        sec_center = date.fromisoformat(sec_end) - timedelta(days=9)
        baseline_days = max(12, (sec_center - ref_center).days)

        # ── 4. Submit + start job ─────────────────────────────────────────────
        pg     = _build_process_graph(ref_start, sec_end)
        job_id = _submit_job(token, pg)
        if not job_id:
            return None

        if not _start_job(token, job_id):
            return None

        # ── 5. Poll ───────────────────────────────────────────────────────────
        status = _poll_job(token, job_id)
        if status != "finished":
            log.warning("insar_cdse: job %s terminó con status=%s", job_id, status)
            return None

        # ── 6. Download + extract ─────────────────────────────────────────────
        result_url = _get_result_url(token, job_id)
        if not result_url:
            return None

        tiff_bytes = _download_tiff(token, result_url)
        if not tiff_bytes:
            return None

        sectors = _extract_sectors(tiff_bytes, baseline_days)
        if not sectors:
            return None

        # ── 7. Armar resultado + cachear ──────────────────────────────────────
        max_vel    = max((abs(s.get("vel_mm_yr") or 0) for s in sectors.values()), default=0)
        coh_vals   = [s["coherencia"] for s in sectors.values() if s.get("coherencia") is not None]
        coh_media  = round(sum(coh_vals) / len(coh_vals), 3) if coh_vals else None
        critico_id = max(sectors, key=lambda k: abs(sectors[k].get("vel_mm_yr") or 0),
                         default=None)

        cache_row = {
            "venue_id":               venue_id,
            "job_id":                 job_id,
            "ref_date":               ref_start,
            "sec_date":               sec_end,
            "temporal_baseline_days": baseline_days,
            "sectores":               sectors,
            "vel_max_abs_mm_yr":      round(max_vel, 2),
            "coherencia_media":       coh_media,
        }
        _save_cache(cache_row)

        return _build_egms_format(cache_row, critico_id)

    except Exception as exc:
        log.warning("insar_cdse: error inesperado: %s", exc)
        return None


# ── Conversión de formato ──────────────────────────────────────────────────────

def _build_egms_format(row: dict, critico_id: Optional[str]) -> dict:
    """Convierte resultado CDSE al formato de salida de fetch_egms_amalfitani()."""
    from datetime import datetime
    sectors    = row.get("sectores") or {}
    max_vel    = row.get("vel_max_abs_mm_yr", 0)
    coh        = row.get("coherencia_media")
    ref_date   = row.get("ref_date", "")
    sec_date   = row.get("sec_date", "")
    baseline_d = row.get("temporal_baseline_days", 0)

    # Proyecciones 5yr para compatibilidad con StructuralTwin
    sectores_out: dict = {}
    for sid, s in sectors.items():
        vel = s.get("vel_mm_yr", 0.0)
        sectores_out[sid] = {
            **s,
            "acumulado_2022_mm":   None,
            "proyeccion_2027_mm":  round(vel * 5.0, 1),
            "serie_temporal":      [],
        }

    resumen_sectores = list(sectors.values())
    return {
        "venue":              "Estadio José Amalfitani",
        "lat":                -34.6379,
        "lon":                -58.5288,
        "fecha_analisis":     datetime.now().strftime("%Y-%m-%d"),
        "periodo":            f"{ref_date} / {sec_date}",
        "satelite":           "Sentinel-1 SLC C-band · órbita descendente",
        "producto":           "openEO CDSE sentinel1_sar_interferogram",
        "referencia":         f"Baseline temporal: {baseline_d}d · protocolfaro@gmail.com",
        "sectores":           sectores_out,
        "sector_critico":     critico_id,
        "vel_max_abs_mm_yr":  round(max_vel, 2),
        "coherencia_media":   coh,
        "alerta": (
            f"Sector crítico: {critico_id or 'ninguno'} ({max_vel:.1f} mm/año). "
            "Datos InSAR LIVE — Copernicus CDSE."
        ) if max_vel >= 3.5 else "",
        "resumen": {
            "vel_media_mm_yr":   round(
                sum(s.get("vel_mm_yr", 0) for s in resumen_sectores) /
                max(len(resumen_sectores), 1), 2),
            "sectores_criticos": sum(1 for s in resumen_sectores if s.get("nivel") == "critico"),
            "sectores_atencion": sum(1 for s in resumen_sectores if s.get("nivel") == "atencion"),
            "sectores_ok":       sum(1 for s in resumen_sectores if s.get("nivel") == "ok"),
        },
        "fallback_usado": False,
        "estimado":       False,
        "fuente":         f"S1 InSAR CDSE openEO · baseline {baseline_d}d · {sec_date}",
    }


def _cached_to_egms_format(cached: dict) -> dict:
    """Reconstruye formato EGMS desde la fila cacheada en Supabase."""
    sectors    = cached.get("sectores") or {}
    critico_id = max(sectors, key=lambda k: abs(sectors[k].get("vel_mm_yr") or 0),
                     default=None) if sectors else None
    return _build_egms_format(cached, critico_id)
