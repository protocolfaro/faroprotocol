"""
faro_landsat_thermal.py — Temperatura superficial del suelo via Landsat 9 TIRS (ST_B10).

Producto: LANDSAT_OT_C2_L2 (Collection 2 Level-2) — Path 228, Row 085 (Buenos Aires).
Banda: ST_B10 (Surface Temperature, 30 m resolución).
Conversión: ST_B10_DN × 0.00341802 + 149.0 = Kelvin  →  − 273.15 = °C

Guardado: soil_metrics.temp_superficie_c

Alertas:
  temp_superficie_c > 35°C con theta_soil < 0.15 → estrés hídrico severo
  temp_superficie_c > 40°C → calentamiento extremo del suelo (riesgo quemadura raíces)
"""
from __future__ import annotations
import logging, os, sys, tempfile
from datetime import date, timedelta

log = logging.getLogger(__name__)

_LAT, _LON = -34.6373, -58.5240
_BBOX = (-58.535, -34.645, -58.515, -34.625)

# Landsat Collection 2 Level-2 ST scale/offset (USGS)
_ST_SCALE  = 0.00341802
_ST_OFFSET = 149.0
_KELVIN    = 273.15


def _get_vs():
    _here = os.path.dirname(os.path.abspath(__file__))
    if _here not in sys.path:
        sys.path.insert(0, _here)
    import velez_supabase as _vs
    return _vs


def fetch_landsat_thermal(days_back: int = 20) -> float | None:
    """
    Busca LANDSAT_OT_C2_L2, descarga el tile más reciente, extrae temperatura
    superficial media sobre Amalfitani en °C. Retorna float o None.
    """
    try:
        import earthaccess
        user = os.environ.get("NASA_EARTHDATA_USER") or os.environ.get("EARTHDATA_USERNAME", "")
        pwd  = os.environ.get("NASA_EARTHDATA_PASS")  or os.environ.get("EARTHDATA_PASSWORD", "")
        if not user or not pwd:
            log.warning("landsat_thermal: credenciales NASA no configuradas — saltando")
            return None
        os.environ["EARTHDATA_USERNAME"] = user
        os.environ["EARTHDATA_PASSWORD"] = pwd
        earthaccess.login(strategy="environment")

        today  = date.today()
        start  = (today - timedelta(days=days_back)).strftime("%Y-%m-%d")
        end    = today.strftime("%Y-%m-%d")

        results = earthaccess.search_data(
            short_name  = "LANDSAT_OT_C2_L2",
            temporal    = (start, end),
            bounding_box= _BBOX,
        )
        if not results:
            # Fallback: buscar Landsat 8 también (mismo sensor familia)
            results = earthaccess.search_data(
                short_name  = "LANDSAT_ETM_C2_L2",
                temporal    = (start, end),
                bounding_box= _BBOX,
            )
        if not results:
            log.info("landsat_thermal: sin granulos en últimos %dd para Amalfitani", days_back)
            return None

        log.info("landsat_thermal: %d granulos encontrados", len(results))
        with tempfile.TemporaryDirectory(prefix="lst_") as tmpdir:
            # Sólo descargar la banda ST_B10 — filtrar links
            granule = results[0]
            st_links = [
                l for l in (granule.data_links() if hasattr(granule, "data_links") else [])
                if "ST_B10" in str(l).upper() or "st_b10" in str(l)
            ]
            if st_links:
                files = earthaccess.download([granule], local_path=tmpdir,
                                              provider="USGS_EROS")
            else:
                files = earthaccess.download(results[:1], local_path=tmpdir)

            if not files:
                return None

            # Buscar ST_B10.TIF en los archivos descargados
            import glob as _glob, os as _os
            tif_files = (_glob.glob(_os.path.join(tmpdir, "*ST_B10.TIF")) +
                         _glob.glob(_os.path.join(tmpdir, "**", "*ST_B10.TIF"), recursive=True))
            if not tif_files:
                # Si descargó tar.gz, descomprimir
                tar_files = _glob.glob(_os.path.join(tmpdir, "*.tar*"))
                if tar_files:
                    import tarfile
                    with tarfile.open(tar_files[0]) as tf:
                        tf.extractall(tmpdir)
                    tif_files = (_glob.glob(_os.path.join(tmpdir, "*ST_B10.TIF")) +
                                 _glob.glob(_os.path.join(tmpdir, "**", "*ST_B10.TIF"), recursive=True))
            if not tif_files:
                log.warning("landsat_thermal: ST_B10.TIF no encontrado en descarga")
                return None

            return _extract_temp_celsius(tif_files[0])

    except ImportError:
        log.warning("landsat_thermal: earthaccess no instalado — saltando")
        return None
    except Exception as exc:
        log.warning("landsat_thermal: fetch_landsat_thermal (non-fatal): %s", exc)
        return None


def _extract_temp_celsius(tif_path: str) -> float | None:
    """Extrae temperatura superficial media sobre Amalfitani desde ST_B10.TIF."""
    try:
        import rasterio, numpy as np
        from rasterio.warp import transform_bounds
        from rasterio.windows import from_bounds

        # Ventana ~500m alrededor de Amalfitani
        margin = 0.005  # ~550 m en lat/lon
        bbox_4326 = (
            _LON - margin, _LAT - margin,
            _LON + margin, _LAT + margin,
        )
        with rasterio.open(tif_path) as src:
            crs    = src.crs
            nodata = src.nodata or 0
            native = transform_bounds("EPSG:4326", crs, *bbox_4326)
            win    = from_bounds(*native, transform=src.transform)
            data   = src.read(1, window=win).astype("float32")

        # USGS C2 L2 Surface Temperature: apply scale + offset
        mask = (data != nodata) & (data > 0) & np.isfinite(data)
        if mask.sum() == 0:
            log.warning("landsat_thermal: ningún pixel válido en bbox Amalfitani")
            return None

        temp_k  = data[mask] * _ST_SCALE + _ST_OFFSET
        temp_c  = float(np.nanmedian(temp_k)) - _KELVIN
        temp_c  = round(temp_c, 2)
        log.info("landsat_thermal: temp_superficie=%.1f°C en Amalfitani", temp_c)
        return temp_c

    except ImportError:
        log.warning("landsat_thermal: rasterio no instalado — saltando")
        return None
    except Exception as exc:
        log.warning("landsat_thermal: _extract_temp_celsius: %s", exc)
        return None


# ── Ciclo principal ───────────────────────────────────────────────────────────

def run_landsat_cycle(venue_id: str = "amalfitani") -> dict:
    """
    Descarga temperatura superficial, parchea soil_metrics, emite alertas QA.
    Nunca lanza excepción.
    """
    out: dict = {
        "venue_id":           venue_id,
        "temp_superficie_c":  None,
        "alerta_calor":       False,
        "error":              None,
    }
    try:
        _vs = _get_vs()
        temp_c = fetch_landsat_thermal()
        if temp_c is None:
            return out

        out["temp_superficie_c"] = temp_c
        soil_row = _vs.get_latest_soil_row(venue_id)
        if soil_row and soil_row.get("id"):
            _vs.patch_row("soil_metrics", soil_row["id"], {"temp_superficie_c": temp_c})

            # Alerta estrés hídrico severo
            theta = float(soil_row.get("theta_soil") or 1.0)
            if temp_c > 35.0 and theta < 0.15:
                out["alerta_calor"] = True
                _emit_alert(
                    f"Estrés hídrico severo: temp_superficie={temp_c:.1f}°C > 35°C "
                    f"con θ_soil={theta:.3f} < 0.15 m³/m³",
                    "landsat_thermal", venue_id,
                )
            elif temp_c > 40.0:
                out["alerta_calor"] = True
                _emit_alert(
                    f"Calentamiento extremo del suelo: {temp_c:.1f}°C > 40°C — "
                    f"riesgo quemadura de raíces",
                    "landsat_thermal", venue_id,
                )

    except Exception as exc:
        log.warning("landsat_cycle (non-fatal): %s", exc)
        out["error"] = str(exc)
    return out


def _emit_alert(mensaje: str, modulo: str, venue_id: str) -> None:
    try:
        _here = os.path.dirname(os.path.abspath(__file__))
        if _here not in sys.path:
            sys.path.insert(0, _here)
        from faro_qa_watchdog import _insert_alert
        _insert_alert(nivel="WARNING", modulo=modulo, mensaje=mensaje, venue_id=venue_id)
    except Exception:
        pass
