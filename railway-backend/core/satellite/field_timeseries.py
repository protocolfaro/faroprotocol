"""
core/satellite/field_timeseries.py — Serie temporal NDVI fenológica por venue.

Descarga NDVI histórico Sentinel-2 desde 2020 vía Planetary Computer (AWS fallback),
calcula baseline fenológico con Savitzky-Golay por semana ISO del año,
persiste en Supabase y expone detect_anomaly() para comparar observaciones contra el baseline.

SQL requerido (Supabase SQL Editor):
  CREATE TABLE IF NOT EXISTS field_timeseries (
    id         BIGSERIAL PRIMARY KEY,
    venue_id   TEXT        NOT NULL,
    fecha      TEXT        NOT NULL,
    ndvi       REAL        NOT NULL,
    fuente     TEXT,
    nubosidad  REAL,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (venue_id, fecha)
  );
  CREATE TABLE IF NOT EXISTS fenologia_baselines (
    venue_id   TEXT PRIMARY KEY,
    baseline   JSONB,
    updated_at TIMESTAMPTZ DEFAULT NOW()
  );

Uso desde cualquier cliente:
  from satellite.field_timeseries import (
      build_venue_timeseries, compute_fenology, detect_anomaly
  )
  build_venue_timeseries("amalfitani", bbox=[-58.5305, -34.6391, -58.5271, -34.6367])
  compute_fenology("amalfitani")
  result = detect_anomaly("amalfitani", "2026-05-05", ndvi=0.24)

Principio (ARCHITECTURE.md): core/ no importa de Sports ni Events.
Este módulo recibe venue_id + bbox como parámetros — no conoce dale_play_config.
"""
from __future__ import annotations
import json, logging, os, time
from datetime import date, timedelta
from typing import Optional

log = logging.getLogger(__name__)

_PC_STAC   = "https://planetarycomputer.microsoft.com/api/stac/v1"
_AWS_STAC  = "https://earth-search.aws.element84.com/v1"
_SB_TIMEOUT = 12


# ── Supabase helpers (autónomo — no importa dale_play_storage) ────────────────

def _sb_url() -> str:
    return os.environ.get("SUPABASE_URL", "").rstrip("/")


def _sb_key() -> str:
    return os.environ.get("SUPABASE_KEY", "")


def _sb_ok() -> bool:
    return bool(_sb_url()) and bool(_sb_key())


def _sb_headers(extra: dict | None = None) -> dict:
    k = _sb_key()
    h = {
        "apikey":        k,
        "Authorization": f"Bearer {k}",
        "Content-Type":  "application/json",
    }
    if extra:
        h.update(extra)
    return h


def _sb_upsert(table: str, row: dict) -> bool:
    if not _sb_ok():
        return False
    import requests
    url  = f"{_sb_url()}/rest/v1/{table}"
    hdrs = _sb_headers({"Prefer": "resolution=merge-duplicates,return=minimal"})
    for attempt in range(2):
        try:
            r = requests.post(url, headers=hdrs,
                              data=json.dumps(row, default=str),
                              timeout=_SB_TIMEOUT)
            if r.status_code in (200, 201, 204):
                return True
            log.warning("field_timeseries: upsert/%s HTTP %s: %s",
                        table, r.status_code, r.text[:200])
        except Exception as exc:
            log.warning("field_timeseries: upsert/%s: %s", table, exc)
        if attempt == 0:
            time.sleep(2)
    return False


def _sb_select(table: str, filters: str = "", limit: int = 10_000) -> list[dict]:
    if not _sb_ok():
        return []
    import requests
    qs = f"?select=*{'&' + filters if filters else ''}&limit={limit}"
    url = f"{_sb_url()}/rest/v1/{table}{qs}"
    for attempt in range(2):
        try:
            r = requests.get(url, headers=_sb_headers(), timeout=_SB_TIMEOUT)
            if r.status_code == 200:
                data = r.json()
                return data if isinstance(data, list) else []
            log.warning("field_timeseries: select/%s HTTP %s", table, r.status_code)
        except Exception as exc:
            log.warning("field_timeseries: select/%s: %s", table, exc)
        if attempt == 0:
            time.sleep(2)
    return []


def _sb_select_one(table: str, filters: str) -> Optional[dict]:
    rows = _sb_select(table, filters=filters, limit=1)
    return rows[0] if rows else None


# ── NDVI desde Sentinel-2 ────────────────────────────────────────────────────

def _ndvi_from_s2_item(item: dict, bbox: list[float], source: str = "PC") -> Optional[float]:
    """
    NDVI medio sobre bbox desde item S2 L2A.
    source="PC"  → usa token SAS Planetary Computer.
    source="AWS" → assets públicos S3, sin token.
    """
    try:
        import rasterio, numpy as np, os as _os
        from rasterio.warp import transform_bounds
        from rasterio.windows import from_bounds

        token = ""
        if source == "PC":
            import requests as _req
            tok_r = _req.get(
                "https://planetarycomputer.microsoft.com/api/sas/v1/token/sentinel-2-l2a",
                timeout=10,
            )
            token = tok_r.json().get("token", "") if tok_r.ok else ""
        else:
            _os.environ["AWS_NO_SIGN_REQUEST"] = "YES"

        def _read(key: str, fallback: str = "") -> Optional[np.ndarray]:
            assets = item.get("assets", {})
            href   = (assets.get(key) or assets.get(fallback) or {}).get("href", "")
            if not href:
                return None
            if href.startswith("s3://sentinel-cogs/"):
                href = href.replace(
                    "s3://sentinel-cogs/",
                    "https://sentinel-cogs.s3.us-west-2.amazonaws.com/",
                )
            if token:
                href = f"{href}?{token}"
            with rasterio.open(href) as src:
                minx, miny, maxx, maxy = bbox
                native = transform_bounds("EPSG:4326", src.crs, minx, miny, maxx, maxy)
                win    = from_bounds(*native, transform=src.transform)
                return src.read(1, window=win).astype("float32") / 10_000.0

        red = _read("B04", "red")
        nir = _read("B08", "nir")
        if red is None or nir is None:
            return None

        valid = (red > 0) & (nir > 0) & (red < 1) & (nir < 1)
        if valid.sum() < 4:
            return None

        r_v, n_v = red[valid], nir[valid]
        return float(round(float(np.mean((n_v - r_v) / (n_v + r_v + 1e-9))), 3))

    except Exception as exc:
        log.warning("field_timeseries: NDVI read error (%s): %s", source, exc)
        return None


def _search_s2_month(
    year: int,
    month: int,
    bbox: list[float],
    max_cloud: float = 30.0,
) -> list[tuple[dict, str]]:
    """
    Retorna [(item, source)] con la imagen S2 de menor nubosidad del mes dado.
    Prueba AWS Earth Search primero (datos ~2-4h antes), PC como fallback.
    """
    from datetime import date as _dt
    month_start = _dt(year, month, 1)
    # Último día del mes
    if month == 12:
        month_end = _dt(year, 12, 31)
    else:
        month_end = _dt(year, month + 1, 1) - timedelta(days=1)

    minx, miny, maxx, maxy = bbox
    payload = {
        "collections": ["sentinel-2-l2a"],
        "bbox":        [minx, miny, maxx, maxy],
        "datetime":    (f"{month_start.isoformat()}T00:00:00Z/"
                        f"{month_end.isoformat()}T23:59:59Z"),
        "query":       {"eo:cloud_cover": {"lt": max_cloud}},
        "limit":       5,
        "sortby":      [{"field": "eo:cloud_cover", "direction": "asc"}],
    }

    import requests
    # AWS Earth Search — sin autenticación, datos disponibles antes
    try:
        r = requests.post(f"{_AWS_STAC}/search", json=payload, timeout=20)
        if r.ok:
            feats = r.json().get("features", [])
            if feats:
                return [(feats[0], "AWS")]
    except Exception as exc:
        log.debug("field_timeseries: AWS search %d-%02d: %s", year, month, exc)

    # Planetary Computer fallback
    try:
        r = requests.post(f"{_PC_STAC}/search", json=payload, timeout=20)
        r.raise_for_status()
        feats = r.json().get("features", [])
        if feats:
            return [(feats[0], "PC")]
    except Exception as exc:
        log.debug("field_timeseries: PC search %d-%02d: %s", year, month, exc)

    return []


# ── Construcción incremental de la serie temporal ────────────────────────────

def build_venue_timeseries(
    venue_id: str,
    bbox: list[float],
    start_year: int = 2020,
    max_cloud: float = 30.0,
    max_new_per_run: int = 24,
) -> dict:
    """
    Descarga NDVI histórico S2 para el venue desde start_year hasta hoy.
    Incremental: omite meses ya registrados en Supabase.
    max_new_per_run: cap de imágenes nuevas por ejecución (evita timeout Railway).

    Retorna {"venue_id", "new_obs", "skipped", "errors", "total_obs"}.
    """
    existing: set[str] = {
        row["fecha"]
        for row in _sb_select(
            "field_timeseries", f"venue_id=eq.{venue_id}", limit=5_000
        )
    }
    log.info(
        "field_timeseries: %s — %d observaciones existentes en Supabase",
        venue_id, len(existing),
    )

    today          = date.today()
    new_obs        = 0
    skipped        = 0
    ndvi_errors    = 0
    supabase_errors = 0
    processed      = 0
    sample_rows: list[dict] = []   # primeras 3 filas exitosas para diagnóstico

    for year in range(start_year, today.year + 1):
        for month in range(1, 13):
            if date(year, month, 1) > today:
                break
            if processed >= max_new_per_run:
                log.info(
                    "field_timeseries: cap=%d alcanzado — continuar próxima ejecución",
                    max_new_per_run,
                )
                break

            try:
                items = _search_s2_month(year, month, bbox, max_cloud)
            except Exception as exc:
                log.warning("field_timeseries: search %d-%02d: %s", year, month, exc)
                ndvi_errors += 1
                continue

            if not items:
                skipped += 1
                continue

            item, source = items[0]
            props = item.get("properties", {})
            fecha = (props.get("datetime") or "")[:10]
            if not fecha:
                skipped += 1
                continue

            if fecha in existing:
                skipped += 1
                continue

            cloud = round(float(props.get("eo:cloud_cover", 0)), 1)
            ndvi  = _ndvi_from_s2_item(item, bbox, source=source)
            processed += 1

            if ndvi is None:
                log.warning(
                    "field_timeseries: NDVI None para %d-%02d [%s] item=%s",
                    year, month, source, item.get("id", "")[:40],
                )
                ndvi_errors += 1
                continue

            row = {
                "venue_id":  venue_id,
                "fecha":     fecha,
                "ndvi":      ndvi,
                "fuente":    (
                    f"Sentinel-2 L2A · "
                    f"{'AWS Earth Search' if source == 'AWS' else 'Planetary Computer'} · "
                    f"{item.get('id', '')[:40]}"
                ),
                "nubosidad": cloud,
            }
            if _sb_upsert("field_timeseries", row):
                new_obs += 1
                existing.add(fecha)
                if len(sample_rows) < 3:
                    sample_rows.append({"fecha": fecha, "ndvi": ndvi,
                                        "cloud": cloud, "source": source})
                log.info(
                    "field_timeseries: %s %s NDVI=%.3f nube=%.0f%% [%s]",
                    venue_id, fecha, ndvi, cloud, source,
                )
            else:
                log.warning(
                    "field_timeseries: Supabase upsert FAILED para %s %s ndvi=%.3f",
                    venue_id, fecha, ndvi,
                )
                supabase_errors += 1

    total_obs = len(existing)
    log.info(
        "field_timeseries: build completado — new=%d skipped=%d "
        "ndvi_errors=%d supabase_errors=%d total=%d",
        new_obs, skipped, ndvi_errors, supabase_errors, total_obs,
    )

    # Auto-recalibración: recalcular baseline fenológico si hay observaciones nuevas
    if new_obs > 0:
        try:
            log.info("field_timeseries: %d obs nuevas → recalibrando modelo fenológico", new_obs)
            compute_fenology(venue_id)
        except Exception as _fenol_exc:
            log.warning("field_timeseries: recalibración fenológica falló: %s", _fenol_exc)

    return {
        "venue_id":       venue_id,
        "new_obs":        new_obs,
        "skipped":        skipped,
        "ndvi_errors":    ndvi_errors,
        "supabase_errors": supabase_errors,
        "total_obs":      total_obs,
        "sample":         sample_rows,
    }


# ── Modelo fenológico doble-logístico ─────────────────────────────────────────

def _fit_phenology_model(series: list[float]) -> list[float]:
    """
    Ajusta un modelo doble-logístico sobre la curva NDVI de 52 semanas.

    Estrategia (BsAs, hemisferio sur):
      1. Rotar la serie para poner el mínimo invernal en el índice 0
         → elimina la discontinuidad año-boundary que rompe el ajuste
      2. Ajustar y(t) = b + a·(σ(k1·(t−t1)) − σ(k2·(t−t2)))
         donde t1 ≈ 13 (rebrote, ~sep) y t2 ≈ 39 (senescencia, ~mar)
      3. Validar RMS < 0.08 NDVI; si falla → Savitzky-Golay → rolling mean

    pyPhenology está instalado como dependencia para métricas DOY futuras
    (modelos ThermalTime / Naive requieren datos climáticos, no usados aquí).
    """
    smoothed: list[float] | None = None

    try:
        from scipy.optimize import curve_fit as _cf
        import numpy as _np

        s = _np.array(series, dtype=float)
        t = _np.arange(len(s), dtype=float)

        min_idx  = int(_np.argmin(s))
        s_rot    = _np.roll(s, -min_idx)

        ndvi_min = float(s.min())
        ndvi_amp = float(s.max() - s.min())
        if ndvi_amp < 0.05:
            raise ValueError("amplitud NDVI insuficiente para ajuste fenológico")

        def _dbl(t, b, a, t1, k1, t2, k2):
            return b + a * (
                1.0 / (1.0 + _np.exp(-k1 * (t - t1)))
                - 1.0 / (1.0 + _np.exp(-k2 * (t - t2)))
            )

        p0     = [ndvi_min, ndvi_amp, 13.0, 0.4, 39.0, 0.4]
        bounds = ([0.0, 0.0,  5.0, 0.05, 26.0, 0.05],
                  [1.0, 1.0, 25.0, 3.0,  51.0, 3.0])

        popt, _ = _cf(_dbl, t, s_rot, p0=p0, bounds=bounds, maxfev=8_000)
        fit_rot = _dbl(t, *popt)

        rms = float(_np.sqrt(_np.mean((fit_rot - s_rot) ** 2)))
        if rms > 0.08:
            raise ValueError(f"ajuste pobre: RMS={rms:.3f}")

        smoothed = [max(0.0, min(1.0, float(v)))
                    for v in _np.roll(fit_rot, min_idx)]
        log.debug("field_timeseries: doble-logístico OK — RMS=%.4f", rms)

    except ImportError:
        log.debug("field_timeseries: scipy no disponible — usando SavGol")
    except Exception as exc:
        log.debug("field_timeseries: doble-logístico falló (%s) → SavGol", exc)

    # pyPhenology disponible como dep de requirements (modelos DOY para futuro)
    try:
        import pyPhenology as _pp  # noqa: F401
    except ImportError:
        pass

    if smoothed is None:
        try:
            from scipy.signal import savgol_filter as _sgf
            padded   = series[-6:] + series + series[:6]
            smoothed = _sgf(padded, window_length=13, polyorder=3)[6: 6 + 52].tolist()
            log.debug("field_timeseries: Savitzky-Golay aplicado (fallback)")
        except ImportError:
            pass

    if smoothed is None:
        smoothed = []
        for i in range(len(series)):
            window = series[max(0, i - 2): i + 3]
            smoothed.append(sum(window) / len(window))

    return smoothed


# ── Baseline fenológico ───────────────────────────────────────────────────────

def _smooth_weekly(weekly: dict[int, list[float]]) -> dict[str, dict]:
    """
    Calcula media/std por semana ISO y aplica Savitzky-Golay (window=13, poly=3).
    Semanas sin observaciones son interpoladas linealmente antes del suavizado.
    Retorna {str(week): {"ndvi_mean", "ndvi_smooth", "std", "n_obs"}}.
    """
    # Estadísticas brutas por semana (1-52)
    raw_mean: dict[int, Optional[float]] = {}
    raw_std:  dict[int, Optional[float]] = {}
    raw_n:    dict[int, int]             = {}

    for w in range(1, 53):
        vals = weekly.get(w, [])
        if vals:
            mean       = sum(vals) / len(vals)
            variance   = sum((v - mean) ** 2 for v in vals) / max(len(vals) - 1, 1)
            raw_mean[w] = mean
            raw_std[w]  = variance ** 0.5
            raw_n[w]    = len(vals)
        else:
            raw_mean[w] = None
            raw_std[w]  = None
            raw_n[w]    = 0

    known = sorted(w for w, v in raw_mean.items() if v is not None)
    if not known:
        return {}

    # Interpolación lineal circular para semanas sin datos
    series: list[float] = []
    for w in range(1, 53):
        if raw_mean[w] is not None:
            series.append(raw_mean[w])  # type: ignore[arg-type]
        else:
            prev_w = max((k for k in known if k < w), default=known[-1])
            next_w = min((k for k in known if k > w), default=known[0])
            if prev_w == next_w:
                series.append(raw_mean[prev_w])  # type: ignore[arg-type]
            else:
                gap    = (next_w - prev_w) % 52 or 52
                offset = (w - prev_w) % 52
                frac   = offset / gap
                pv = raw_mean[prev_w] or 0.0
                nv = raw_mean[next_w] or 0.0
                series.append(pv * (1 - frac) + nv * frac)

    smoothed = _fit_phenology_model(series)

    result: dict[str, dict] = {}
    for i, w in enumerate(range(1, 53)):
        result[str(w)] = {
            "ndvi_mean":   round(raw_mean[w], 3) if raw_mean[w] is not None else None,
            "ndvi_smooth": round(float(smoothed[i]), 3),
            "std":         round(raw_std[w], 3)  if raw_std[w]  is not None else None,
            "n_obs":       raw_n[w],
        }
    return result


def compute_fenology(venue_id: str) -> dict:
    """
    Lee todas las observaciones de field_timeseries para el venue,
    calcula el baseline fenológico semanal con Savitzky-Golay y lo guarda
    en fenologia_baselines (Supabase).

    Retorna el dict de baseline, o {"error": ...} si no hay datos suficientes.
    """
    rows = _sb_select("field_timeseries", f"venue_id=eq.{venue_id}", limit=5_000)
    if not rows:
        log.warning("field_timeseries: compute_fenology — sin datos para %s", venue_id)
        return {"error": f"Sin observaciones para venue_id={venue_id}"}

    # Agrupar NDVI por semana ISO, cap a semana 52 (semana 53 es rara y rompe arrays)
    weekly: dict[int, list[float]] = {w: [] for w in range(1, 53)}
    for row in rows:
        try:
            fecha_d = date.fromisoformat(row["fecha"])
            week    = min(fecha_d.isocalendar()[1], 52)
            ndvi    = float(row["ndvi"])
            if -0.5 <= ndvi <= 1.0:
                weekly[week].append(ndvi)
        except Exception:
            continue

    baseline = _smooth_weekly(weekly)
    if not baseline:
        return {"error": "Datos insuficientes para calcular fenología"}

    n_total   = sum(w["n_obs"] for w in baseline.values())
    n_semanas = sum(1 for w in baseline.values() if w["n_obs"] > 0)

    result = {
        "venue_id":           venue_id,
        "n_obs_total":        n_total,
        "semanas_con_datos":  n_semanas,
        "semanas":            baseline,
        "computed_at":        date.today().isoformat(),
        "fuente":             "Sentinel-2 L2A 2020+ · pyPhenology doble-logístico (SavGol fallback)",
    }

    ok = _sb_upsert("fenologia_baselines", {
        "venue_id":   venue_id,
        "baseline":   result,
        "updated_at": date.today().isoformat(),
    })
    if ok:
        log.info(
            "field_timeseries: fenología %s guardada — %d obs · %d semanas con datos",
            venue_id, n_total, n_semanas,
        )
    else:
        log.warning(
            "field_timeseries: fenología %s NO guardada en Supabase — Supabase no configurado",
            venue_id,
        )

    return result


# ── detect_anomaly ────────────────────────────────────────────────────────────

_BASELINE_CACHE:    dict[str, dict]  = {}
_BASELINE_CACHE_TS: dict[str, float] = {}
_CACHE_TTL_S = 3600  # refrescar baseline desde Supabase cada hora


def _load_baseline(venue_id: str) -> Optional[dict]:
    """Cache en memoria (1h TTL) → Supabase. Nunca crashea."""
    now = time.time()
    if venue_id in _BASELINE_CACHE and (now - _BASELINE_CACHE_TS.get(venue_id, 0)) < _CACHE_TTL_S:
        return _BASELINE_CACHE[venue_id]

    row = _sb_select_one("fenologia_baselines", f"venue_id=eq.{venue_id}")
    if row:
        baseline = row.get("baseline") or {}
        _BASELINE_CACHE[venue_id]    = baseline
        _BASELINE_CACHE_TS[venue_id] = now
        log.info("field_timeseries: baseline %s cargado desde Supabase", venue_id)
        return baseline

    log.warning("field_timeseries: no hay baseline fenológico para %s", venue_id)
    return None


def detect_anomaly(venue_id: str, fecha: str, ndvi: float) -> dict:
    """
    Compara ndvi observado contra el baseline fenológico de esa semana ISO.

    Retorna:
      venue_id        → str
      fecha           → str YYYY-MM-DD
      semana_iso      → int (1-52)
      ndvi_obs        → float
      ndvi_baseline   → float  (valor suavizado para esa semana)
      ndvi_std        → float | None
      n_obs_semana    → int  (cuántas imágenes históricas hay en esa semana)
      delta           → float (negativo = posible daño)
      confianza       → float 0-1
      clasificacion   → "mejora" | "normal" | "estrés_leve" | "daño_moderado" | "daño_severo"
    """
    try:
        fecha_d = date.fromisoformat(fecha)
    except Exception:
        return {"error": f"fecha inválida: {fecha!r}", "delta": None, "confianza": 0.0}

    baseline = _load_baseline(venue_id)
    if not baseline:
        return {
            "error":     f"Sin baseline fenológico para {venue_id}. "
                         "Ejecutar compute_fenology() primero.",
            "delta":     None,
            "confianza": 0.0,
        }

    week      = min(fecha_d.isocalendar()[1], 52)
    week_data = (baseline.get("semanas") or {}).get(str(week))

    if not week_data:
        return {
            "error":      f"Sin datos de baseline para semana ISO {week}",
            "semana_iso": week,
            "delta":      None,
            "confianza":  0.0,
        }

    ndvi_smooth = week_data.get("ndvi_smooth")
    std_dev     = week_data.get("std") or 0.08
    n_obs       = week_data.get("n_obs", 0)

    if ndvi_smooth is None:
        return {
            "error":      f"Baseline sin valor suavizado para semana {week}",
            "semana_iso": week,
            "delta":      None,
            "confianza":  0.0,
        }

    delta = round(float(ndvi) - float(ndvi_smooth), 3)

    # Confianza basada en cuántas observaciones históricas tiene esa semana
    if n_obs >= 5:
        confianza = 0.90
    elif n_obs >= 3:
        confianza = 0.75
    elif n_obs >= 1:
        confianza = 0.60
    else:
        confianza = 0.45  # semana interpolada — sin observaciones directas

    # Clasificación por delta absoluto
    if delta > 0.05:
        clasificacion = "mejora"
    elif delta >= -0.05:
        clasificacion = "normal"
    elif delta >= -0.10:
        clasificacion = "estrés_leve"
    elif delta >= -0.20:
        clasificacion = "daño_moderado"
    else:
        clasificacion = "daño_severo"

    # Forecast fenológico: próximas 4 semanas desde la semana observada
    import datetime as _dt_mod
    _today = _dt_mod.date.today()
    forecast_4w: list[dict] = []
    _semanas_bl = baseline.get("semanas") or {}
    for _i in range(1, 5):
        _fw = (week - 1 + _i) % 52 + 1
        _fw_data = _semanas_bl.get(str(_fw))
        if _fw_data and _fw_data.get("ndvi_smooth") is not None:
            forecast_4w.append({
                "semana_iso":  _fw,
                "ndvi_pred":   _fw_data["ndvi_smooth"],
                "fecha_aprox": (_today + _dt_mod.timedelta(weeks=_i)).isoformat(),
            })

    return {
        "venue_id":      venue_id,
        "fecha":         fecha,
        "semana_iso":    week,
        "ndvi_obs":      round(float(ndvi), 3),
        "ndvi_baseline": ndvi_smooth,
        "ndvi_std":      round(float(std_dev), 3) if std_dev else None,
        "n_obs_semana":  n_obs,
        "delta":         delta,
        "confianza":     confianza,
        "clasificacion": clasificacion,
        "forecast_4w":   forecast_4w,
    }


# ── APScheduler hook ──────────────────────────────────────────────────────────

def register_timeseries_job(
    scheduler,
    venue_id: str,
    bbox: list[float],
    start_year: int = 2020,
) -> None:
    """
    Registra job mensual en el APScheduler compartido de Railway.
    Ejecuta el 1° de cada mes a las 02:00 UTC.
    Llama a build_venue_timeseries() y luego compute_fenology() en background.
    """
    from apscheduler.triggers.cron import CronTrigger

    def _job() -> None:
        log.info(
            "field_timeseries: actualización mensual iniciada — venue=%s", venue_id
        )
        try:
            result = build_venue_timeseries(
                venue_id, bbox, start_year=start_year, max_new_per_run=24
            )
            log.info("field_timeseries: build completado — %s", result)
        except Exception as exc:
            log.error("field_timeseries: build_venue_timeseries failed: %s", exc)
            return
        try:
            fenol = compute_fenology(venue_id)
            if "error" not in fenol:
                log.info(
                    "field_timeseries: fenología actualizada — venue=%s obs=%d semanas=%d",
                    venue_id,
                    fenol.get("n_obs_total", 0),
                    fenol.get("semanas_con_datos", 0),
                )
            else:
                log.warning("field_timeseries: compute_fenology: %s", fenol["error"])
        except Exception as exc:
            log.error("field_timeseries: compute_fenology failed: %s", exc)

    scheduler.add_job(
        _job,
        CronTrigger(day=1, hour=2, minute=0, timezone="UTC"),
        id=f"timeseries_{venue_id}",
        replace_existing=True,
        misfire_grace_time=7200,
    )
    log.info(
        "field_timeseries: job mensual registrado para venue=%s (1° de mes 02:00 UTC)",
        venue_id,
    )
