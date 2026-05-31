"""
dale_play_insar.py — Detección de vibración estructural post-show via InSAR diferencial.
Importa insar_hyp3 desde railway-backend sin modificarlo.
Cuatro tribunas del Amalfitani: Norte, Sur, Este, Oeste.
Mejorado: cruce EGMS histórico (mm/año) vs InSAR semanal HyP3.
  delta = medición_actual_normalizada − velocidad_histórica
  Si |delta| > 0.5 mm → alerta estructural.
"""
from __future__ import annotations
import json, logging, math, os, pathlib
from typing import Optional

from dale_play_config import (
    VENUE_LAT, TRIBUNAS,
    INSAR_OK_MM, INSAR_CAUTION_MM, INSAR_CRITICAL_MM,
)

log = logging.getLogger(__name__)

_MODELS_DIR = pathlib.Path(__file__).parent / "models"
_EGMS_JSON  = _MODELS_DIR / "egms_amalfitani.json"

_DEG_LAT = 1 / 111_139
_DEG_LON = 1 / (111_139 * math.cos(math.radians(VENUE_LAT)))
_COS_INC = math.cos(math.radians(38.0))

# Umbral de anomalía EGMS vs InSAR (mm desplazamiento)
_DELTA_ALERTA_MM = 0.5


def _tribuna_bbox(lat: float, lon: float, w_m: float = 60, h_m: float = 40) -> tuple:
    dlat = (h_m / 2) * _DEG_LAT
    dlon = (w_m / 2) * _DEG_LON
    return (lon - dlon, lat - dlat, lon + dlon, lat + dlat)


TRIBUNA_BBOXES = {t["id"]: _tribuna_bbox(t["lat"], t["lon"]) for t in TRIBUNAS}


def _classify(los_mm: float) -> dict:
    a = abs(los_mm)
    if a >= INSAR_CRITICAL_MM:
        return {"nivel": "critico",  "label": "Deformación crítica — inspección inmediata"}
    if a >= INSAR_CAUTION_MM:
        return {"nivel": "atencion", "label": "Deformación elevada — monitorear"}
    if a >= INSAR_OK_MM:
        return {"nivel": "leve",     "label": "Deformación leve — dentro del rango normal"}
    return     {"nivel": "ok",       "label": "Sin deformación significativa"}


def _load_egms_velocities() -> dict[str, float]:
    """
    Carga velocidades EGMS históricas (mm/año) de egms_amalfitani.json.
    Retorna {tribuna_id: vel_mm_yr} con fallback a ceros si no existe.
    """
    if not _EGMS_JSON.exists():
        # Intentar generar el JSON si el módulo está disponible
        try:
            from dale_play_egms import fetch_egms_amalfitani
            fetch_egms_amalfitani()
        except Exception:
            pass

    if _EGMS_JSON.exists():
        try:
            data = json.loads(_EGMS_JSON.read_text(encoding="utf-8"))
            sectors = data.get("sectores", {})
            # Mapear IDs EGMS a IDs de tribunas InSAR
            _id_map = {
                "norte":  "tribuna_norte",
                "sur":    "tribuna_sur",
                "este":   "tribuna_este",
                "oeste":  "tribuna_oeste",
            }
            result: dict[str, float] = {}
            for t in TRIBUNAS:
                tid      = t["id"]  # norte, sur, este, oeste
                egms_key = f"tribuna_{tid}"
                s        = sectors.get(egms_key) or sectors.get(tid) or {}
                result[tid] = float(s.get("vel_mm_yr", 0.0))
            return result
        except Exception as exc:
            log.warning("EGMS load error: %s", exc)

    return {t["id"]: 0.0 for t in TRIBUNAS}


def _egms_expected_mm(vel_mm_yr: float, period_days: int) -> float:
    """Desplazamiento esperado según tendencia EGMS para un período dado (mm)."""
    return vel_mm_yr * period_days / 365.0


def _compute_delta(measured_mm: float, expected_mm: float) -> dict:
    """
    Compara medición InSAR actual con tendencia EGMS.
    delta > 0: más subsidencia que la histórica.
    delta < 0: menor subsidencia (recuperación).
    |delta| > 0.5 mm → anomalía estructural.
    """
    delta = measured_mm - expected_mm
    anomalia = abs(delta) > _DELTA_ALERTA_MM
    if anomalia:
        if delta > 0:
            estado = "aceleración"
            alerta = (f"⚠ Subsidencia {abs(delta):.2f} mm sobre tendencia EGMS — "
                      "posible respuesta estructural al evento")
        else:
            estado = "recuperación"
            alerta = (f"Info: Menor subsidencia {abs(delta):.2f} mm que tendencia "
                      "EGMS — suelo en fase de recuperación")
    else:
        estado = "normal"
        alerta = None

    return {
        "delta_mm":     round(delta, 2),
        "anomalia":     anomalia,
        "estado_delta": estado,
        "alerta":       alerta,
    }


def fetch_post_show_vibration() -> Optional[dict]:
    """
    Corre el pipeline HyP3 D-InSAR para el Amalfitani.
    Retorna desplazamiento LOS→vertical por tribuna (mm) + cruce EGMS histórico.

    Output mejorado incluye tabla EGMS:
    {velocidad_historica_mm_yr, medicion_actual_mm, periodo_dias,
     delta_mm, anomalia, estado_delta, nivel}
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

    # Calcular período en días entre las escenas
    try:
        from datetime import date
        _d1 = date.fromisoformat(d1)
        _d2 = date.fromisoformat(d2)
        period_days = abs((_d2 - _d1).days) or 12
    except Exception:
        period_days = 12

    try:
        job = insar_hyp3._submit_and_wait(g1, g2)
    except Exception as e:
        log.warning("dale_play_insar: HyP3 job failed: %s", e)
        return None

    # Cargar velocidades EGMS históricas
    egms_vels = _load_egms_velocities()

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
            for t in TRIBUNAS:
                t_id = t["id"]
                bbox = TRIBUNA_BBOXES[t_id]
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

                    los_m   = float(data[mask].mean())
                    vert_mm = round(los_m / _COS_INC * 1000, 2)
                    cls     = _classify(vert_mm)

                    # Cruce EGMS
                    vel_hist = egms_vels.get(t_id, 0.0)
                    expected = _egms_expected_mm(vel_hist, period_days)
                    delta_r  = _compute_delta(vert_mm, expected)

                    tribunas_result[t_id] = {
                        # InSAR actual
                        "los_mm":             vert_mm,
                        **cls,
                        # EGMS histórico
                        "egms": {
                            "velocidad_historica_mm_yr": round(vel_hist, 2),
                            "periodo_dias":              period_days,
                            "medicion_actual_mm":        vert_mm,
                            "esperado_tendencia_mm":     round(expected, 2),
                            **delta_r,
                        },
                    }

                    if delta_r["anomalia"]:
                        log.warning(
                            "dale_play_insar: %s — anomalía EGMS: delta=%.2f mm (%s)",
                            t_id, delta_r["delta_mm"], delta_r["estado_delta"],
                        )

                except Exception as ex:
                    log.warning("dale_play_insar: %s: %s", t_id, ex)

        if not tribunas_result:
            log.warning("dale_play_insar: no valid tribuna readings")
            return None

        # Resumen de anomalías
        anomalias = [
            {"tribuna": tid, **tr["egms"]}
            for tid, tr in tribunas_result.items()
            if tr.get("egms", {}).get("anomalia")
        ]

        log.info(
            "✅ dale_play_insar — par %s/%s · %s · anomalías_EGMS=%d",
            d1, d2,
            " ".join(f"{k}:{v['los_mm']:+.2f}mm" for k, v in tribunas_result.items()),
            len(anomalias),
        )

        return {
            "fecha_pre":       d1,
            "fecha_post":      d2,
            "periodo_dias":    period_days,
            "tribunas":        tribunas_result,
            "anomalias_egms":  anomalias,
            "n_anomalias":     len(anomalias),
            "fuente":          f"Sentinel-1 InSAR · ASF HyP3 · par {d1}/{d2}",
            "fuente_egms":     "EGMS L3 Copernicus 2015-2022 · egms_amalfitani.json",
            "delta_umbral_mm": _DELTA_ALERTA_MM,
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
