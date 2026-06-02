"""
dale_play_insar.py — Detección de vibración estructural post-show via InSAR diferencial.
Importa insar_hyp3 desde railway-backend sin modificarlo.
Cuatro tribunas del Amalfitani: Norte, Sur, Este, Oeste.
Mejorado: cruce EGMS histórico (mm/año) vs InSAR semanal HyP3.
  delta = medición_actual_normalizada − velocidad_histórica
  Si |delta| > 0.5 mm → alerta estructural.

Adicionado: fetch_sar_compaction() — Sentinel-1 GRD backscatter delta pre/post show.
  Delta VV > 1.5 dB → alerta de compactación/humectación anómala del suelo.
  No requiere NASA Earthdata. Usa Planetary Computer STAC directamente.
  Revisita real ~3-4 días sobre 34°S / tile 21HUB.
"""
from __future__ import annotations
import json, logging, math, os, pathlib, sys as _sys
from typing import Optional

from dale_play_config import (
    VENUE_LAT, VENUE_BBOX, TRIBUNAS,
    INSAR_OK_MM, INSAR_CAUTION_MM, INSAR_CRITICAL_MM,
)

# Asegurar que core/ esté en sys.path para imports transversales
_CORE_DIR = str(pathlib.Path(__file__).parents[3] / "core")
if _CORE_DIR not in _sys.path:
    _sys.path.insert(0, _CORE_DIR)
from utils import coords_to_bbox

log = logging.getLogger(__name__)

_MODELS_DIR = pathlib.Path(__file__).parent / "models"
_EGMS_JSON  = _MODELS_DIR / "egms_amalfitani.json"

_COS_INC = math.cos(math.radians(38.0))

# Umbral de anomalía EGMS vs InSAR (mm desplazamiento)
_DELTA_ALERTA_MM = 0.5

# Tribunas Amalfitani: 60 m ancho × 40 m alto por tribuna
TRIBUNA_BBOXES = {
    t["id"]: tuple(coords_to_bbox(t["lat"], t["lon"], h_m=40, w_m=60))
    for t in TRIBUNAS
}


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

    # HyP3 requiere credenciales NASA Earthdata para autenticar el job.
    # Sin ellas el job fallará silenciosamente tras ~30 min de espera.
    if not os.environ.get("NASA_EARTHDATA_USER") or not os.environ.get("NASA_EARTHDATA_PASS"):
        log.warning("dale_play_insar: NASA_EARTHDATA no configurado — usando EGMS histórico como fallback")
        return {
            "error":          "NASA_EARTHDATA no configurado",
            "fallback_usado": True,
            "dato":           "EGMS histórico",
            "fuente":         "EGMS L3 Copernicus 2015-2022 [fallback — sin creds NASA Earthdata]",
            "nota": (
                "Configurar NASA_EARTHDATA_USER y NASA_EARTHDATA_PASS en Railway "
                "para activar InSAR diferencial HyP3 en tiempo real."
            ),
        }

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


# ─────────────────────────────────────────────────────────────────────────────
# Sentinel-1 GRD backscatter delta — compactación post-show
# ─────────────────────────────────────────────────────────────────────────────

_PC_STAC             = "https://planetarycomputer.microsoft.com/api/stac/v1"
_S1_COLLECTION       = "sentinel-1-grd"
_DELTA_COMPACTION_DB = 1.5  # dB — umbral de alerta de compactación/humectación


def fetch_sar_compaction(
    show_date: str = "",
    days_pre: int  = 10,
    days_post: int = 5,
) -> dict:
    """
    Compara backscatter Sentinel-1 VV pre vs post show sobre el campo.
    No requiere NASA Earthdata — usa Planetary Computer STAC.
    Revisita real ~3-4 días a 34°S.

    Delta VV > +1.5 dB → posible anegamiento/humectación anómala.
    Delta VV < -1.5 dB → posible compactación/secado post-show.

    Output:
      disponible, pre_fecha, post_fecha, pre_vv_db, post_vv_db,
      delta_vv_db, alerta_compactacion, umbral_db, interpretacion, fuente
    """
    import requests
    from datetime import date as _date, timedelta

    _fallback = {"disponible": False, "fuente": f"Sentinel-1 GRD · Planetary Computer · 20m"}

    try:
        if show_date:
            try:
                show_dt = _date.fromisoformat(show_date)
            except Exception:
                show_dt = _date.today() - timedelta(days=5)
        else:
            show_dt = _date.today() - timedelta(days=5)

        pre_end   = show_dt - timedelta(days=1)
        pre_start = pre_end  - timedelta(days=days_pre)
        post_start = show_dt
        post_end   = show_dt + timedelta(days=days_post)

        minx, miny, maxx, maxy = VENUE_BBOX

        def _search_s1(start: _date, end: _date) -> Optional[dict]:
            payload = {
                "collections": [_S1_COLLECTION],
                "bbox":        [minx, miny, maxx, maxy],
                "datetime":    f"{start.isoformat()}T00:00:00Z/{end.isoformat()}T23:59:59Z",
                "limit":       5,
                "sortby":      [{"field": "datetime", "direction": "desc"}],
            }
            r = requests.post(f"{_PC_STAC}/search", json=payload, timeout=20)
            if r.status_code in (400, 404):
                return None
            r.raise_for_status()
            features = r.json().get("features", [])
            return features[0] if features else None

        pre_item  = _search_s1(pre_start, pre_end)
        post_item = _search_s1(post_start, post_end)

        if not pre_item and not post_item:
            return {**_fallback, "motivo": "Sin escenas S1 disponibles en el período"}

        def _mean_vv_db(item: dict) -> Optional[float]:
            """
            Backscatter VV medio sobre VENUE_BBOX en dB (amplitud relativa).
            Sentinel-1 GRD en PC no tiene CRS embebido → se mapea la ventana
            usando el bbox geográfico del item STAC (interpolación bilineal).
            Valor en dB es relativo (no sigma0 calibrado), válido para delta pre/post.
            """
            try:
                import rasterio, numpy as np
                from rasterio.windows import Window

                tok_r = requests.get(
                    f"https://planetarycomputer.microsoft.com/api/sas/v1/token/{_S1_COLLECTION}",
                    timeout=10
                )
                token = tok_r.json().get("token", "") if tok_r.ok else ""

                # Asset key es minúsculas "vv" en el catálogo PC sentinel-1-grd
                vv_href = item.get("assets", {}).get("vv", {}).get("href", "")
                if not vv_href:
                    return None
                if token:
                    vv_href = f"{vv_href}?{token}"

                # Bbox geográfico del item para mapear pixels (GRD no tiene CRS)
                i_lon0, i_lat0, i_lon1, i_lat1 = item.get("bbox", [0, 0, 1, 1])

                with rasterio.open(vv_href) as src:
                    H, W = src.shape
                    # Fracción de longitud/latitud del venue dentro del item
                    fx0 = (minx - i_lon0) / (i_lon1 - i_lon0)
                    fx1 = (maxx - i_lon0) / (i_lon1 - i_lon0)
                    # Latitud: fila 0 = lat máxima → invertir eje Y
                    fy0 = (i_lat1 - maxy) / (i_lat1 - i_lat0)
                    fy1 = (i_lat1 - miny) / (i_lat1 - i_lat0)

                    col_off = max(0, int(fx0 * W))
                    col_end = min(W, max(col_off + 20, int(fx1 * W)))
                    row_off = max(0, int(fy0 * H))
                    row_end = min(H, max(row_off + 20, int(fy1 * H)))

                    win  = Window(col_off, row_off, col_end - col_off, row_end - row_off)
                    data = src.read(1, window=win).astype("float32")

                valid = (data > 0) & np.isfinite(data)
                if valid.sum() < 4:
                    return None
                # dB relativo (amplitud): válido para comparación pre/post
                return float(round(10.0 * float(np.log10(float(np.mean(data[valid])))), 2))
            except Exception as exc:
                log.debug("dale_play_insar: S1 VV read: %s", exc)
                return None

        pre_vv  = _mean_vv_db(pre_item)  if pre_item  else None
        post_vv = _mean_vv_db(post_item) if post_item else None

        delta_vv            = round(post_vv - pre_vv, 2) if (pre_vv is not None and post_vv is not None) else None
        alerta_compactacion = abs(delta_vv) >= _DELTA_COMPACTION_DB if delta_vv is not None else False

        pre_fecha  = (pre_item.get("properties",  {}).get("datetime") or "")[:10] if pre_item  else None
        post_fecha = (post_item.get("properties", {}).get("datetime") or "")[:10] if post_item else None

        if alerta_compactacion and delta_vv is not None:
            interpretacion = (
                f"Delta VV {delta_vv:+.1f} dB post-show → "
                f"{'humectación anómala (posible anegamiento)' if delta_vv > 0 else 'compactación/secado anómalo'}. "
                "Inspección de campo recomendada."
            )
        elif delta_vv is not None:
            interpretacion = f"Delta VV {delta_vv:+.1f} dB — dentro del rango normal post-show."
        else:
            interpretacion = "Sin datos comparables pre/post para calcular delta."

        resultado = {
            "disponible":           True,
            "pre_fecha":            pre_fecha,
            "post_fecha":           post_fecha,
            "pre_vv_db":            pre_vv,
            "post_vv_db":           post_vv,
            "delta_vv_db":          delta_vv,
            "alerta_compactacion":  alerta_compactacion,
            "umbral_db":            _DELTA_COMPACTION_DB,
            "revisita_dias":        3.5,
            "interpretacion":       interpretacion,
            "fuente":               "Sentinel-1 GRD · Planetary Computer · 20m · VV",
        }

        log.info(
            "dale_play_insar: SAR compaction — pre=%s post=%s delta_vv=%s dB alerta=%s",
            pre_fecha, post_fecha, delta_vv, alerta_compactacion
        )
        return resultado

    except Exception as e:
        log.warning("dale_play_insar: SAR compaction failed: %s", e)
        return {**_fallback, "error": str(e)}
