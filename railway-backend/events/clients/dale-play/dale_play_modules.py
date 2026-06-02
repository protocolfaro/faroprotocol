"""
dale_play_modules.py — Registro centralizado de módulos Dale Play.

Cada módulo declara: RESULT_KEY, REQUIRED_FIELDS, CRITICAL, TIMEOUT_S, DATA_SOURCE.
Para agregar un módulo nuevo: crear clase, agregarla a MODULES. Nada más.
"""
from __future__ import annotations
from dale_play_module import DalePlayModule


class SatelliteModule(DalePlayModule):
    RESULT_KEY      = "satellite"
    REQUIRED_FIELDS = ["ndvi", "ndvi_fecha"]
    MODES           = ("full", "post_show")
    CRITICAL        = True
    TIMEOUT_S       = 60
    DATA_SOURCE     = "HLS / Sentinel-2 L2A / Landsat C2L2 · Planetary Computer + NASA CMR MODIS"

    def run(self, rider: dict, **kwargs) -> dict:
        try:
            from dale_play_satellite import fetch_satellite_baseline
            mode      = kwargs.get("mode", "full")
            show_date = kwargs.get("show_date", "")
            show_id   = kwargs.get("show_id", "")
            out = fetch_satellite_baseline(mode=mode, show_date=show_date, show_id=show_id)
            out.setdefault("fuente", self.DATA_SOURCE)
            out.setdefault("fallback_usado", out.get("ndvi") is None)

            try:
                from dale_play_source_log import log_source_event
                fuente_tipo = out.get("fuente_tipo", "sin_dato")
                fuente_ndvi = out.get("fuente_ndvi", fuente_tipo)
                confianza   = 0.90 if fuente_tipo in ("HLS", "S2") else 0.60 if fuente_tipo == "S2_openEO" else 0.10
                log_source_event(
                    modulo="Satellite_NDVI",
                    fuente_usada=fuente_tipo,
                    confianza=confianza,
                    venue_cubierto=out.get("ndvi") is not None,
                    motivo_fallback="" if not out.get("fallback_usado") else f"NDVI={out.get('ndvi')} via {fuente_ndvi}",
                )
            except Exception:
                pass

            return out
        except Exception as exc:
            return {"error": str(exc), "fuente": self.DATA_SOURCE, "fallback_usado": False}


class WeatherModule(DalePlayModule):
    RESULT_KEY      = "weather"
    REQUIRED_FIELDS = ["show_day", "riesgo_global"]
    MODES           = ("full", "post_show", "weather_only")
    CRITICAL        = True
    TIMEOUT_S       = 20
    DATA_SOURCE     = "Open-Meteo API"

    def run(self, rider: dict, **kwargs) -> dict:
        try:
            from dale_play_weather import fetch_72h_forecast
            out = fetch_72h_forecast(show_date=kwargs.get("show_date", ""))
            out.setdefault("fuente", self.DATA_SOURCE)
            out["fallback_usado"] = bool(out.get("error"))
            return out
        except Exception as exc:
            return {"error": str(exc), "fuente": self.DATA_SOURCE,
                    "fallback_usado": False, "riesgo_global": "sin_datos"}


class SoilRealModule(DalePlayModule):
    RESULT_KEY      = "soil_real"
    REQUIRED_FIELDS = ["capacidad_portante_kpa"]
    MODES           = ("full", "post_show")
    CRITICAL        = False
    TIMEOUT_S       = 30
    DATA_SOURCE     = "SoilGrids REST API v2 (ISRIC)"

    def run(self, rider: dict, **kwargs) -> dict:
        try:
            from dale_play_soil import fetch_real_soil_capacity
            from dale_play_config import VENUE_LAT, VENUE_LON
            out = fetch_real_soil_capacity(
                kwargs.get("show_id", "unknown"),
                kwargs.get("lat", VENUE_LAT),
                kwargs.get("lon", VENUE_LON),
            )
            out.setdefault("fuente", self.DATA_SOURCE)
            return out
        except Exception as exc:
            return {"error": str(exc), "fuente": self.DATA_SOURCE,
                    "fallback_usado": True,
                    "capacidad_portante_kpa": 120,
                    "estimado": True}


class SoilModule(DalePlayModule):
    RESULT_KEY      = "soil"
    REQUIRED_FIELDS = ["zonas", "n_exclusiones", "capacidad_efectiva_kpa"]
    MODES           = ("full", "post_show")
    CRITICAL        = True
    TIMEOUT_S       = 30
    DATA_SOURCE     = "SoilGrids + Terzaghi (Faro Protocol)"

    def run(self, rider: dict, **kwargs) -> dict:
        try:
            from dale_play_soil import analyze_soil_load
            result = kwargs.get("result", {})
            lluvia = float(
                (result.get("weather") or {}).get("show_day", {}).get("lluvia_mm") or 0
            )
            out = analyze_soil_load(
                rider, lluvia_48h_mm=lluvia,
                show_id=kwargs.get("show_id", "unknown"),
            )
            out.setdefault("fuente", self.DATA_SOURCE)
            out.setdefault("fallback_usado", out.get("fuente", "").startswith("[ESTIMADO]"))
            return out
        except Exception as exc:
            return {"error": str(exc), "fuente": self.DATA_SOURCE, "fallback_usado": False}


class DrainageModule(DalePlayModule):
    RESULT_KEY      = "drainage"
    REQUIRED_FIELDS = ["zonas", "resumen"]
    MODES           = ("full", "post_show")
    CRITICAL        = False
    TIMEOUT_S       = 30
    DATA_SOURCE     = "Planos C&G Amalfitani · 14 líneas DRENAFLEX AWACOR 118mm + 2 colectores AWACOR 218mm + 4 cámaras + guardaganado 380ml/15cm · Manning"

    def run(self, rider: dict, **kwargs) -> dict:
        try:
            from dale_play_drainage import analyze_drainage
            from dale_play_config import VENUE_LAT, VENUE_LON, VENUE_NAME
            result = kwargs.get("result", {})
            lluvia = float(
                (result.get("weather") or {}).get("show_day", {}).get("lluvia_mm") or 0
            )
            out = analyze_drainage(
                kwargs.get("lat", VENUE_LAT),
                kwargs.get("lon", VENUE_LON),
                VENUE_NAME,
                lluvia_48h_mm=lluvia,
            )
            out.setdefault("fuente", self.DATA_SOURCE)
            out["fallback_usado"] = False  # datos reales de infraestructura, nunca fallback
            return out
        except Exception as exc:
            return {"error": str(exc), "fuente": self.DATA_SOURCE, "fallback_usado": True}


class AcousticModule(DalePlayModule):
    RESULT_KEY      = "acoustic"
    REQUIRED_FIELDS = ["sectores", "spl_promedio_db", "rt60_s"]
    MODES           = ("full", "post_show")
    CRITICAL        = True
    TIMEOUT_S       = 60
    DATA_SOURCE     = "Claude API claude-sonnet-4-6 + modelo geométrico Sabine-Eyring"

    def run(self, rider: dict, **kwargs) -> dict:
        try:
            from dale_play_acoustic import analyze_acoustic_sightlines
            result_ctx = kwargs.get("result") or {}
            rider_enriched = {
                **rider,
                "artist":             result_ctx.get("artist", rider.get("artist", "Artista")),
                "show_date":          kwargs.get("show_date", rider.get("show_date", "")),
                "capacidad_estimada": rider.get("capacidad_estimada", 0),
            }
            out = analyze_acoustic_sightlines(
                rider_enriched, show_id=kwargs.get("show_id", "unknown")
            )
            out.setdefault("fuente", self.DATA_SOURCE)
            modelo = out.get("modelo_acustico", "")
            out["fallback_usado"] = "Claude" not in modelo
            return out
        except Exception as exc:
            return {"error": str(exc), "fuente": self.DATA_SOURCE, "fallback_usado": False}


class EGMSModule(DalePlayModule):
    RESULT_KEY      = "egms"
    REQUIRED_FIELDS = ["sectores", "sector_critico", "vel_max_abs_mm_yr"]
    MODES           = ("full", "post_show")
    CRITICAL        = False
    TIMEOUT_S       = 15
    DATA_SOURCE     = "EGMS Copernicus 2015-2022 [ESTIMADO — datos históricos]"

    def run(self, rider: dict, **kwargs) -> dict:
        try:
            from dale_play_egms import fetch_egms_amalfitani
            out = fetch_egms_amalfitani()
            # EGMS data is entirely from 2015-2022 historical database, not real-time
            out["fuente"]        = self.DATA_SOURCE
            out["fallback_usado"] = True
            out["estimado"]      = True
            return out
        except Exception as exc:
            return {"error": str(exc), "fuente": self.DATA_SOURCE,
                    "fallback_usado": True, "estimado": True}


class SPLComplianceModule(DalePlayModule):
    RESULT_KEY      = "spl_compliance"
    REQUIRED_FIELDS = ["estado_global", "limites_aplicados", "sectores"]
    MODES           = ("full", "post_show")
    CRITICAL        = False
    TIMEOUT_S       = 10
    DATA_SOURCE     = "Ordenanza GCBA 11.554 + Res. 5613 (límites SPL eventos)"

    def run(self, rider: dict, **kwargs) -> dict:
        try:
            from dale_play_spl_compliance import analyze_spl_compliance
            result = kwargs.get("result", {})
            acoustic = result.get("acoustic") or {}
            return analyze_spl_compliance(acoustic, rider)
        except Exception as exc:
            return {"error": str(exc), "fuente": self.DATA_SOURCE, "fallback_usado": False}


class StructuralTwinModule(DalePlayModule):
    RESULT_KEY      = "structural"
    REQUIRED_FIELDS = ["margen_seguridad_pct", "estado_global", "factores"]
    MODES           = ("full", "post_show")
    CRITICAL        = False
    TIMEOUT_S       = 15
    DATA_SOURCE     = "Open-Meteo viento + EGMS + Terzaghi + Faro Protocol"

    def run(self, rider: dict, **kwargs) -> dict:
        try:
            from dale_play_structural import analyze_structural_twin
            result = kwargs.get("result", {})
            return analyze_structural_twin(
                rider,
                weather=result.get("weather") or {},
                egms=result.get("egms") or {},
                soil=result.get("soil") or {},
            )
        except Exception as exc:
            return {"error": str(exc), "fuente": self.DATA_SOURCE, "fallback_usado": False}


class RiderComplianceModule(DalePlayModule):
    RESULT_KEY      = "rider_compliance"
    REQUIRED_FIELDS = ["estado_global", "incumplimientos", "aprobaciones"]
    MODES           = ("full", "post_show")
    CRITICAL        = False
    TIMEOUT_S       = 10
    DATA_SOURCE     = "Rider técnico + datos acústicos/suelo/drenaje"

    def run(self, rider: dict, **kwargs) -> dict:
        try:
            from dale_play_rider_compliance import analyze_rider_compliance
            result = kwargs.get("result", {})
            return analyze_rider_compliance(
                rider,
                acoustic=result.get("acoustic") or {},
                soil=result.get("soil") or {},
                drainage=result.get("drainage") or {},
            )
        except Exception as exc:
            return {"error": str(exc), "fuente": self.DATA_SOURCE, "fallback_usado": False}


class SARModule(DalePlayModule):
    """
    Módulo SAR con cascada de aviación: Umbra → S1 RTC → Capella → ALOS-2 meta → EGMS proxy.
    Nunca retorna null. Siempre incluye sar_nivel_confianza y sar_cascade_log.
    Mantiene RESULT_KEY = "umbra_sar" para compatibilidad con report/certification.
    """
    RESULT_KEY      = "umbra_sar"
    REQUIRED_FIELDS = ["umbra_cobre_venue", "escena_datetime", "sar_nivel_confianza"]
    MODES           = ("full", "post_show")
    CRITICAL        = False
    TIMEOUT_S       = 200
    DATA_SOURCE     = (
        "Cascada SAR: Umbra X-band 0.26m CC-BY-4.0 "
        "→ Sentinel-1 RTC C-band 20m PC "
        "→ Capella X-band 0.5m CC-BY-4.0 "
        "→ ALOS-2 L-band meta (ASF) "
        "→ EGMS proxy Copernicus 2015-2022"
    )

    def run(self, rider: dict, **kwargs) -> dict:
        try:
            from dale_play_sar import fetch_sar
            from dale_play_config import VENUE_LAT, VENUE_LON
            out = fetch_sar(
                venue_lat=kwargs.get("lat", VENUE_LAT),
                venue_lon=kwargs.get("lon", VENUE_LON),
                show_id=kwargs.get("show_id", "unknown"),
                window_m=500,
            )
            out.setdefault("fuente", self.DATA_SOURCE)

            # Registrar en SOURCE_LOG.md
            try:
                from dale_play_source_log import log_source_event
                log_source_event(
                    modulo="SAR",
                    fuente_usada=out.get("sar_fuente_usada", "UNKNOWN"),
                    confianza=out.get("sar_nivel_confianza", 0.0),
                    venue_cubierto=out.get("venue_cubierto"),
                    cascade_log=out.get("sar_cascade_log"),
                )
            except Exception:
                pass

            return out

        except Exception as exc:
            try:
                from dale_play_source_log import log_source_event
                log_source_event(
                    modulo="SAR",
                    fuente_usada="EXCEPCION",
                    confianza=0.0,
                    motivo_fallback=str(exc)[:100],
                )
            except Exception:
                pass
            return {
                "error":               str(exc),
                "fuente":              self.DATA_SOURCE,
                "fallback_usado":      True,
                "umbra_cobre_venue":   False,
                "escena_datetime":     None,
                "sar_fuente_usada":    "EXCEPCION",
                "sar_nivel_confianza": 0.0,
                "sar_cascade_log":     [{"fuente": "EXCEPCION", "razon": str(exc)[:200]}],
            }


# ── Registro global ───────────────────────────────────────────────────────────
# Para agregar un módulo: crear clase arriba, agregar instancia aquí. Nada más.
MODULES: list[DalePlayModule] = [
    SatelliteModule(),
    WeatherModule(),
    SoilRealModule(),
    SoilModule(),
    DrainageModule(),
    AcousticModule(),
    EGMSModule(),
    SPLComplianceModule(),
    StructuralTwinModule(),
    RiderComplianceModule(),
    SARModule(),
]

MODULES_BY_KEY: dict[str, DalePlayModule] = {m.RESULT_KEY: m for m in MODULES}
