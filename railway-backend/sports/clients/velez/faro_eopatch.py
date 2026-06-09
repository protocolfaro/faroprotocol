"""
faro_eopatch.py — Construye un EOPatch con el histórico del Amalfitani.

Fuente: Supabase field_timeseries + vegetation_metrics + soil_metrics.
Hasta 76 observaciones ordenadas cronológicamente.

Compatible con eo-learn-core si está instalado; fallback a FaroPatch (dict).
FaroPatch es el tipo canónico interno — ambas rutas producen la misma interfaz.
"""
from __future__ import annotations

import logging
import os
import sys
from datetime import date, datetime, timezone
from typing import Optional

import numpy as np

log = logging.getLogger(__name__)

# ── path setup ────────────────────────────────────────────────────────────────
_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)


class FaroPatch:
    """Lightweight EOPatch-compatible container (no eo-learn dependency)."""

    def __init__(self) -> None:
        self.timestamps: list[date] = []
        self.data: dict[str, np.ndarray] = {}   # name → (T,) float32
        self.mask: dict[str, np.ndarray] = {}   # name → (T,) bool (True = gap)
        self.meta: dict = {}

    def __len__(self) -> int:
        return len(self.timestamps)

    def to_dict(self) -> dict:
        return {
            "timestamps": [str(t) for t in self.timestamps],
            "data":  {k: v.tolist() for k, v in self.data.items()},
            "mask":  {k: v.tolist() for k, v in self.mask.items()},
            "meta":  self.meta,
        }


def _sb_get(table: str, qs: str) -> list[dict]:
    """Minimal Supabase REST GET — reuses env vars from velez_supabase."""
    supa_url = os.environ.get("SUPABASE_URL", "")
    supa_key = os.environ.get("SUPABASE_KEY", "")
    if not supa_url or not supa_key:
        return []
    import requests as _req
    hdrs = {"apikey": supa_key, "Authorization": f"Bearer {supa_key}"}
    try:
        r = _req.get(f"{supa_url}/rest/v1/{table}?{qs}", headers=hdrs, timeout=15)
        if r.status_code == 200:
            return r.json() or []
        log.warning("faro_eopatch: %s HTTP %s", table, r.status_code)
    except Exception as exc:
        log.warning("faro_eopatch: %s fetch: %s", table, exc)
    return []


def build_eopatch(venue_id: str = "amalfitani",
                  cancha_id: str | None = None,
                  limit: int = 76) -> FaroPatch:
    """
    Construye un FaroPatch con datos históricos del venue/cancha.

    Combina:
      - field_timeseries → NDVI, nubosidad
      - vegetation_metrics → BSI, NDWI, EVI2, GNDVI (por cancha)
      - soil_metrics → SAR_VV

    Retorna FaroPatch con series temporales alineadas por fecha.
    Gaps (fechas sin dato) → mask[feature][i] = True.
    """
    patch = FaroPatch()
    patch.meta = {"venue_id": venue_id, "cancha_id": cancha_id,
                  "built_at": datetime.now(timezone.utc).isoformat()}

    # ── 1. field_timeseries — backbone temporal ───────────────────────────
    ts_rows = _sb_get(
        "field_timeseries",
        f"venue_id=eq.{venue_id}&order=fecha.asc&limit={limit}",
    )
    if not ts_rows:
        log.warning(
            "faro_eopatch: sin datos en field_timeseries para %s — "
            "fallback a vegetation_metrics", venue_id,
        )
        # Bootstrap desde vegetation_metrics cuando field_timeseries no tiene datos.
        # Da pocos puntos pero permite que el Kalman arranque con datos reales en lugar
        # de usar siempre los defaults de BsAs.
        _cid_qs_fb = f"&cancha_id=eq.{cancha_id}" if cancha_id else ""
        veg_fb = _sb_get(
            "vegetation_metrics",
            f"venue_id=eq.{venue_id}{_cid_qs_fb}&order=fecha_imagen.asc&limit={limit}",
        )
        _seen_fb: dict[str, dict] = {}
        for _row in veg_fb:
            _fd = str(_row.get("fecha_imagen") or "")[:10]
            if _fd and _row.get("ndvi") is not None:
                _seen_fb[_fd] = _row
        if _seen_fb:
            ts_rows = [
                {
                    "fecha":    _fd,
                    "ndvi":     _row["ndvi"],
                    "nubosidad": _row.get("confianza_pct") or 0,
                }
                for _fd, _row in sorted(_seen_fb.items())
            ]
            log.info(
                "faro_eopatch: fallback vegetation_metrics — %d obs para %s",
                len(ts_rows), venue_id,
            )
        else:
            return patch

    # Build date index
    date_index: dict[str, int] = {}
    timestamps: list[date] = []
    ndvi_arr: list[float | None] = []
    nub_arr:  list[float | None] = []

    for row in ts_rows:
        try:
            d = date.fromisoformat(str(row["fecha"])[:10])
        except Exception:
            continue
        idx = len(timestamps)
        date_index[str(d)] = idx
        timestamps.append(d)
        ndvi_arr.append(float(row["ndvi"]) if row.get("ndvi") is not None else None)
        nub_arr.append(float(row.get("nubosidad") or 0.0))

    T = len(timestamps)
    if T == 0:
        return patch

    patch.timestamps = timestamps

    def _fill(arr: list[float | None]) -> tuple[np.ndarray, np.ndarray]:
        vals = np.full(T, np.nan, dtype=np.float32)
        mask = np.ones(T, dtype=bool)
        for i, v in enumerate(arr):
            if v is not None and not np.isnan(v):
                vals[i] = v
                mask[i] = False
        return vals, mask

    patch.data["NDVI"],       patch.mask["NDVI"]       = _fill(ndvi_arr)
    patch.data["NUBOSIDAD"],  patch.mask["NUBOSIDAD"]  = _fill(nub_arr)

    # ── 2. vegetation_metrics — BSI, NDWI, EVI2, GNDVI ───────────────────
    _cid_qs = f"&cancha_id=eq.{cancha_id}" if cancha_id else ""
    veg_rows = _sb_get(
        "vegetation_metrics",
        f"venue_id=eq.{venue_id}{_cid_qs}&order=fecha_imagen.asc&limit=200",
    )
    bsi_arr  = [None] * T
    ndwi_arr = [None] * T
    evi2_arr = [None] * T
    gndvi_arr= [None] * T

    for row in veg_rows:
        fd = str(row.get("fecha_imagen") or "")[:10]
        if fd not in date_index:
            continue
        i = date_index[fd]
        if row.get("bsi")   is not None: bsi_arr[i]   = float(row["bsi"])
        if row.get("ndwi")  is not None: ndwi_arr[i]  = float(row["ndwi"])
        if row.get("evi2")  is not None: evi2_arr[i]  = float(row["evi2"])
        if row.get("gndvi") is not None: gndvi_arr[i] = float(row["gndvi"])

    patch.data["BSI"],   patch.mask["BSI"]   = _fill(bsi_arr)
    patch.data["NDWI"],  patch.mask["NDWI"]  = _fill(ndwi_arr)
    patch.data["EVI2"],  patch.mask["EVI2"]  = _fill(evi2_arr)
    patch.data["GNDVI"], patch.mask["GNDVI"] = _fill(gndvi_arr)

    # ── 3. soil_metrics — SAR VV ──────────────────────────────────────────
    soil_rows = _sb_get(
        "soil_metrics",
        f"venue_id=eq.{venue_id}&order=created_at.asc&limit=200",
    )
    sar_arr = [None] * T

    for row in soil_rows:
        fd = str(row.get("fecha_imagen") or row.get("created_at", ""))[:10]
        if fd not in date_index:
            continue
        if row.get("sar_vv_db") is not None:
            sar_arr[date_index[fd]] = float(row["sar_vv_db"])

    patch.data["SAR_VV"], patch.mask["SAR_VV"] = _fill(sar_arr)

    patch.meta["n_obs_ndvi"]   = int((~patch.mask["NDVI"]).sum())
    patch.meta["n_obs_sar"]    = int((~patch.mask["SAR_VV"]).sum())
    patch.meta["n_gaps_ndvi"]  = int(patch.mask["NDVI"].sum())

    log.info(
        "faro_eopatch: %s/%s — T=%d obs_ndvi=%d gaps=%d obs_sar=%d",
        venue_id, cancha_id, T,
        patch.meta["n_obs_ndvi"], patch.meta["n_gaps_ndvi"], patch.meta["n_obs_sar"],
    )
    return patch


def try_eolearn_wrap(patch: FaroPatch):
    """
    Opcional: envuelve FaroPatch en un eo-learn EOPatch real si está disponible.
    Retorna EOPatch o el FaroPatch original si eo-learn no está instalado.
    """
    try:
        from eolearn.core import EOPatch, FeatureType
        eo = EOPatch()
        eo.timestamp = [datetime.combine(d, datetime.min.time()) for d in patch.timestamps]
        for name, arr in patch.data.items():
            eo[FeatureType.SCALAR, name] = arr.reshape(-1, 1)
        return eo
    except ImportError:
        return patch
