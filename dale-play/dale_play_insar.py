"""
dale_play_insar.py — Detección de vibración estructural post-show via InSAR diferencial.
Importa insar_hyp3 desde railway-backend sin modificarlo.
Cuatro tribunas del Amalfitani: Norte, Sur, Este, Oeste.
"""
from __future__ import annotations
import logging, math, os, tempfile
from typing import Optional

from dale_play_config import (
    VENUE_LAT, TRIBUNAS,
    INSAR_OK_MM, INSAR_CAUTION_MM, INSAR_CRITICAL_MM,
)

log = logging.getLogger(__name__)

_DEG_LAT = 1 / 111_139
_DEG_LON = 1 / (111_139 * math.cos(math.radians(VENUE_LAT)))
_COS_INC = math.cos(math.radians(38.0))


def _tribuna_bbox(lat: float, lon: float, w_m: float = 60, h_m: float = 40) -> tuple:
    dlat = (h_m / 2) * _DEG_LAT
    dlon = (w_m / 2) * _DEG_LON
    return (lon - dlon, lat - dlat, lon + dlon, lat + dlat)


TRIBUNA_BBOXES = {t["id"]: _tribuna_bbox(t["lat"], t["lon"]) for t in TRIBUNAS}


def _classify(los_mm: float) -> dict:
    a = abs(los_mm)
    if a >= INSAR_CRITICAL_MM:
        return {"nivel": "critico",     "label": "Deformación crítica — inspección inmediata"}
    if a >= INSAR_CAUTION_MM:
        return {"nivel": "atencion",    "label": "Deformación elevada — monitorear"}
    if a >= INSAR_OK_MM:
        return {"nivel": "leve",        "label": "Deformación leve — dentro del rango normal"}
    return     {"nivel": "ok",          "label": "Sin deformación significativa"}


def fetch_post_show_vibration() -> Optional[dict]:
    """
    Corre el pipeline HyP3 D-InSAR para el Amalfitani.
    Usa insar_hyp3._search_slc_granules + _find_pair + _submit_and_wait.
    Retorna desplazamiento LOS→vertical por tribuna (mm).
    """
    try:
        import insar_hyp3
    except ImportError as e:
        log.warning("dale_play_insar: insar_hyp3 not importable: %s", e)
        return None

    try:
        granules = insar_hyp3._search_slc_granules(days_back=26)
    except Exception as e:
        log.warning("dale_play_insar: ASF search failed: %s", e)
        return None

    if not granules:
        log.warning("dale_play_insar: no SLC granules found")
        return None

    pair = insar_hyp3._find_pair(granules)
    if not pair:
        log.warning("dale_play_insar: no 12-day pair found")
        return None

    g1, g2 = pair
    d1 = (g1.get("startTime") or "")[:10]
    d2 = (g2.get("startTime") or "")[:10]
    log.info("dale_play_insar: pair %s → %s", d1, d2)

    try:
        job = insar_hyp3._submit_and_wait(g1, g2)
    except Exception as e:
        log.warning("dale_play_insar: HyP3 job failed: %s", e)
        return None

    tif_path = None
    try:
        import rasterio, numpy as np
        from rasterio.warp import transform_bounds
        from rasterio.windows import from_bounds

        tif_path = insar_hyp3._download_displacement_tif(job)
        tribunas_result: dict = {}

        with rasterio.open(tif_path) as src:
            crs    = src.crs
            nodata = src.nodata
            for t_id, bbox in TRIBUNA_BBOXES.items():
                try:
                    native = transform_bounds("EPSG:4326", crs, *bbox)
                    win    = from_bounds(*native, transform=src.transform)
                    data   = src.read(1, window=win).astype("float32")
                    mask   = np.isfinite(data)
                    if nodata is not None:
                        mask &= (data != float(nodata))
                    mask &= (np.abs(data) < 1.0)
                    if mask.sum() < 2:
                        log.warning("dale_play_insar: %s — fewer than 2 valid pixels", t_id)
                        continue
                    los_m  = float(data[mask].mean())
                    vert_mm = round(los_m / _COS_INC * 1000, 2)
                    tribunas_result[t_id] = {"los_mm": vert_mm, **_classify(vert_mm)}
                except Exception as ex:
                    log.warning("dale_play_insar: %s: %s", t_id, ex)

        if not tribunas_result:
            log.warning("dale_play_insar: no valid tribuna readings")
            return None

        log.info("✅ dale_play_insar — par %s/%s · %s",
                 d1, d2,
                 " ".join(f"{k}:{v['los_mm']:+.2f}mm" for k, v in tribunas_result.items()))
        return {
            "fecha_pre":  d1,
            "fecha_post": d2,
            "tribunas":   tribunas_result,
            "fuente":     f"Sentinel-1 InSAR · ASF HyP3 · par {d1}/{d2}",
        }

    except Exception as e:
        log.warning("dale_play_insar: read failed: %s", e)
        return None
    finally:
        if tif_path:
            try:
                os.remove(tif_path)
                os.rmdir(os.path.dirname(tif_path))
            except Exception:
                pass
