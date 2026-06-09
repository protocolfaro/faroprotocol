"""
faro_ecostress.py — ET termodinámica ECOSTRESS + validación cruzada SMAP.

Módulo 1 — ECOSTRESS (ECO3ETPTJPL):
  NASA LP DAAC, ~70 m resolución, instante de overpass ISS.
  ET instantánea (W/m²) → diaria (mm/día) via factor climatológico (Buenos Aires, verano).
  Guardado en climate_metrics.et_ecostress_mm.
  Alerta en qa_alerts si difiere > 30% del ET0 Penman-Monteith calculado por data_refresh.

Módulo 2 — SMAP Enhanced L3 (SPL3SMP_E):
  NASA NSIDC, ~9 km resolución, pasivo L-band, diario.
  Humedad volumétrica (m³/m³). Guardado en soil_metrics.theta_smap.
  Alerta en qa_alerts si difiere > 25% del theta_soil Van Genuchten del SAR.

Auth: NASA_EARTHDATA_USER + NASA_EARTHDATA_PASS (ya en Railway env vars para HyP3).
"""
from __future__ import annotations
import logging, os, sys, tempfile
from datetime import date, timedelta

log = logging.getLogger(__name__)

# Amalfitani — bbox para búsqueda satelital
_LAT, _LON = -34.6373, -58.5240
_BBOX = (-58.535, -34.645, -58.515, -34.625)   # (lon_min, lat_min, lon_max, lat_max)

# Conversión instantánea ECOSTRESS W/m² → mm/día
# λ_evap ≈ 2.45 MJ/kg, escala temporal de overpass ~15min → full-day factor ≈ 0.035
_LE_TO_MM_DAY = 0.035


def _get_vs():
    _here = os.path.dirname(os.path.abspath(__file__))
    if _here not in sys.path:
        sys.path.insert(0, _here)
    import velez_supabase as _vs
    return _vs


def _auth_earthaccess():
    """Configura env vars para earthaccess y devuelve el módulo autenticado."""
    import earthaccess
    user = os.environ.get("NASA_EARTHDATA_USER") or os.environ.get("EARTHDATA_USERNAME", "")
    pwd  = os.environ.get("NASA_EARTHDATA_PASS")  or os.environ.get("EARTHDATA_PASSWORD", "")
    if not user or not pwd:
        raise EnvironmentError("NASA_EARTHDATA_USER / NASA_EARTHDATA_PASS no configurados")
    os.environ["EARTHDATA_USERNAME"] = user
    os.environ["EARTHDATA_PASSWORD"] = pwd
    earthaccess.login(strategy="environment")
    return earthaccess


# ── ECOSTRESS ECO3ETPTJPL ────────────────────────────────────────────────────

def fetch_ecostress_et(days_back: int = 8) -> float | None:
    """
    Busca granulos ECO3ETPTJPL, descarga el más reciente, extrae ET media sobre Amalfitani.
    Retorna ET en mm/día o None en cualquier fallo.
    """
    try:
        ea = _auth_earthaccess()
        today  = date.today()
        start  = (today - timedelta(days=days_back)).strftime("%Y-%m-%d")
        end    = today.strftime("%Y-%m-%d")

        results = ea.search_data(
            short_name  = "ECO3ETPTJPL",
            temporal    = (start, end),
            bounding_box= _BBOX,
        )
        if not results:
            log.info("ecostress: sin granulos ECO3ETPTJPL en últimos %dd para Amalfitani", days_back)
            return None

        log.info("ecostress: %d granulos encontrados, descargando el más reciente", len(results))
        with tempfile.TemporaryDirectory(prefix="eco_") as tmpdir:
            files = ea.download(results[:1], local_path=tmpdir)
            if not files:
                log.warning("ecostress: descarga sin archivos")
                return None
            et_mm = _parse_ecostress_h5(files[0])
            return et_mm

    except ImportError:
        log.warning("ecostress: earthaccess no instalado — saltando")
        return None
    except EnvironmentError as env_err:
        log.warning("ecostress: %s — saltando", env_err)
        return None
    except Exception as exc:
        log.warning("ecostress: fetch_ecostress_et (non-fatal): %s", exc)
        return None


def _parse_ecostress_h5(path: str) -> float | None:
    """Extrae ET media W/m² desde ECO3ETPTJPL HDF5, convierte a mm/día."""
    import numpy as np

    # Intento 1: h5py (nativo)
    try:
        import h5py
        with h5py.File(path, "r") as f:
            # ECO3ETPTJPL v001: /L3T_ET_PTJPL/ETinst
            group_key = next((k for k in f.keys() if "ET" in k.upper()), None)
            if group_key is None:
                return None
            group = f[group_key]
            ds_key = next((k for k in group.keys() if "etinst" in k.lower() or k == "ETinst"), None)
            if ds_key is None:
                return None
            data   = group[ds_key][:].astype("float32")
            fill   = float(group[ds_key].attrs.get("_FillValue", 9999))
            scale  = float(group[ds_key].attrs.get("scale_factor", 0.001))
            data[data >= fill * 0.9] = np.nan
            data   *= scale
            valid  = data[np.isfinite(data) & (data > 0) & (data < 1000)]
            if valid.size == 0:
                return None
            et_wm2 = float(np.nanmedian(valid))
            et_mm  = round(et_wm2 * _LE_TO_MM_DAY, 3)
            log.info("ecostress: ETinst=%.3f W/m² → %.3f mm/día", et_wm2, et_mm)
            return et_mm
    except ImportError:
        pass
    except Exception as exc:
        log.debug("ecostress h5py parse: %s", exc)

    # Intento 2: rasterio GDAL HDF5 driver
    try:
        import rasterio
        for sds_suffix in (
            "L3T_ET_PTJPL/ETinst",
            "ECO_L3T_ET_PTJPL/ETinst",
            "ETJPL/ETinst",
        ):
            try:
                with rasterio.open(f'HDF5:"{path}"://{sds_suffix}') as src:
                    data  = src.read(1).astype("float32")
                    scale = src.scales[0] if src.scales else 1.0
                    data *= float(scale)
                    mask  = np.isfinite(data) & (data > 0) & (data < 9000)
                    if mask.sum() == 0:
                        continue
                    et_wm2 = float(np.nanmedian(data[mask]))
                    return round(et_wm2 * _LE_TO_MM_DAY, 3)
            except Exception:
                continue
    except ImportError:
        pass

    log.warning("ecostress: no se pudo parsear %s", path)
    return None


# ── SMAP SPL3SMP_E ────────────────────────────────────────────────────────────

def fetch_smap_theta(days_back: int = 5) -> float | None:
    """
    Busca SPL3SMP_E, descarga más reciente, extrae theta volumétrico en Amalfitani.
    Retorna theta (m³/m³) o None.
    """
    try:
        ea = _auth_earthaccess()
        today = date.today()
        start = (today - timedelta(days=days_back)).strftime("%Y-%m-%d")
        end   = today.strftime("%Y-%m-%d")

        results = ea.search_data(
            short_name  = "SPL3SMP_E",
            temporal    = (start, end),
            bounding_box= _BBOX,
        )
        if not results:
            log.info("smap: sin granulos SPL3SMP_E en últimos %dd", days_back)
            return None

        with tempfile.TemporaryDirectory(prefix="smap_") as tmpdir:
            files = ea.download(results[:1], local_path=tmpdir)
            if not files:
                return None
            return _parse_smap_h5(files[0])

    except ImportError:
        log.warning("smap: earthaccess no instalado — saltando")
        return None
    except EnvironmentError as env_err:
        log.warning("smap: %s — saltando", env_err)
        return None
    except Exception as exc:
        log.warning("smap: fetch_smap_theta (non-fatal): %s", exc)
        return None


def _parse_smap_h5(path: str) -> float | None:
    """Extrae soil moisture en Amalfitani desde SPL3SMP_E HDF5."""
    try:
        import h5py, numpy as np
        with h5py.File(path, "r") as f:
            # Intentar AM primero, luego PM
            for group_name in ("Soil_Moisture_Retrieval_Data_AM",
                               "Soil_Moisture_Retrieval_Data_PM",
                               "Soil_Moisture_Retrieval_Data"):
                group = f.get(group_name)
                if group is None:
                    continue
                sm_ds = group.get("soil_moisture")
                if sm_ds is None:
                    continue
                sm = sm_ds[:].astype("float32")
                fill = float(sm_ds.attrs.get("_FillValue", -9999.0))
                sm[sm == fill] = np.nan
                sm[sm < 0]     = np.nan
                sm[sm > 1]     = np.nan

                # SPL3SMP_E Enhanced grid: 0.0625° (~9km), 1624×3856 global
                # lat origin: 85.0448 N, lon origin: -179.9688 W, resolution: 0.0625°
                lat_orig, lon_orig = 85.0448, -179.9688
                lat_res, lon_res   = -0.0625,  0.0625
                row_idx = int((_LAT - lat_orig) / lat_res)
                col_idx = int((_LON - lon_orig) / lon_res)
                r0, r1 = max(0, row_idx - 1), min(sm.shape[0], row_idx + 2)
                c0, c1 = max(0, col_idx - 1), min(sm.shape[1], col_idx + 2)
                crop   = sm[r0:r1, c0:c1]
                valid  = crop[np.isfinite(crop)]
                if valid.size == 0:
                    continue
                theta_smap = round(float(np.nanmean(valid)), 4)
                log.info("smap: θ_SMAP=%.4f m³/m³ en Amalfitani [%s]", theta_smap, group_name)
                return theta_smap
    except ImportError:
        log.warning("smap: h5py no instalado — saltando")
    except Exception as exc:
        log.warning("smap: _parse_smap_h5: %s", exc)
    return None


# ── Ciclo principal ───────────────────────────────────────────────────────────

def run_ecostress_cycle(venue_id: str = "amalfitani") -> dict:
    """
    Ciclo completo: ECOSTRESS ET + SMAP theta. Parchea tablas Supabase.
    Emite alertas QA si divergencias superan umbrales.
    Nunca lanza excepción.
    """
    out: dict = {
        "venue_id":        venue_id,
        "et_ecostress_mm": None,
        "theta_smap":      None,
        "alerta_et":       False,
        "alerta_smap":     False,
        "error":           None,
    }
    try:
        _vs = _get_vs()

        # ── ECOSTRESS ET ──────────────────────────────────────────────────────
        et_eco = fetch_ecostress_et()
        if et_eco is not None:
            out["et_ecostress_mm"] = et_eco
            clim_row = _vs.get_latest_climate_row(venue_id)
            if clim_row and clim_row.get("id"):
                _vs.patch_row("climate_metrics", clim_row["id"], {"et_ecostress_mm": et_eco})
                et0_pm = float(clim_row.get("et0_mm_dia") or 0)
                if et0_pm > 0:
                    diff_pct = abs(et_eco - et0_pm) / et0_pm * 100
                    if diff_pct > 30:
                        out["alerta_et"] = True
                        log.warning(
                            "ecostress: divergencia ET %.0f%% — ECOSTRESS=%.2f vs PM=%.2f mm/día",
                            diff_pct, et_eco, et0_pm,
                        )
                        _emit_alert(
                            f"Divergencia ET termodinámica: "
                            f"ECOSTRESS={et_eco:.2f}mm vs Penman-Monteith={et0_pm:.2f}mm "
                            f"({diff_pct:.0f}%>30%)",
                            "ecostress", venue_id,
                        )

        # ── SMAP theta ────────────────────────────────────────────────────────
        theta_smap = fetch_smap_theta()
        if theta_smap is not None:
            out["theta_smap"] = theta_smap
            soil_row = _vs.get_latest_soil_row(venue_id)
            if soil_row and soil_row.get("id"):
                _vs.patch_row("soil_metrics", soil_row["id"], {"theta_smap": theta_smap})
                theta_sar = float(soil_row.get("theta_soil") or 0)
                if theta_sar > 0:
                    diff_pct = abs(theta_smap - theta_sar) / theta_sar * 100
                    if diff_pct > 25:
                        out["alerta_smap"] = True
                        log.warning(
                            "smap: divergencia θ %.0f%% — SMAP=%.3f vs SAR-VG=%.3f m³/m³",
                            diff_pct, theta_smap, theta_sar,
                        )
                        _emit_alert(
                            f"Divergencia humedad SAR vs SMAP: "
                            f"θ_SAR={theta_sar:.3f} vs θ_SMAP={theta_smap:.3f} "
                            f"({diff_pct:.0f}%>25%)",
                            "smap_validacion", venue_id,
                        )

    except Exception as exc:
        log.warning("ecostress_cycle (non-fatal): %s", exc)
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
