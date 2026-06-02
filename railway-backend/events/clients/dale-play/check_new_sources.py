"""
check_new_sources.py — Buscador automático de fuentes de datos nuevas.

Cada 3 días (APScheduler) evalúa fuentes candidatas contra el pipeline existente.
Si encuentra algo mejor:
  1. Evalúa: gratuita, resolución, frecuencia, mejora concreta
  2. Intenta integrarla como DalePlayModule (genera código, carga en runtime)
  3. Agrega al MANUAL.md via append_to_manual() — NUNCA sobreescribe
  4. Manda email a protocolfaro@gmail.com con resumen + diff del MANUAL

Configuración via variables de entorno:
  EMAIL_APP_PASS  — Gmail app password (16 chars) para protocolfaro@gmail.com
  ANTHROPIC_API_KEY — para análisis de fuentes via Claude (opcional)
"""
from __future__ import annotations
import importlib
import logging
import os
import pathlib
import smtplib
import sys
import textwrap
from datetime import datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Callable

log = logging.getLogger(__name__)

DALE_DIR   = pathlib.Path(__file__).parent
sys.path.insert(0, str(DALE_DIR))

NOTIFY_EMAIL = "protocolfaro@gmail.com"
SMTP_HOST    = "smtp.gmail.com"
SMTP_PORT    = 587


# ═══════════════════════════════════════════════════════════════════════════════
# Catálogo de fuentes candidatas
# ═══════════════════════════════════════════════════════════════════════════════

# Cada entrada define: qué mide, calidad vs lo existente, y cómo integrarlo.
# integration_fn(venue_lat, venue_lon) → dict con "code" (str, código Python) o "error"

def _check_viirs_availability(lat: float, lon: float) -> dict:
    """VIIRS VNP09GA — NDVI diario 500m via NASA Earthdata (sin credenciales para CMR)."""
    import requests
    try:
        params = {
            "short_name":   "VNP09GA",
            "version":      "002",
            "temporal":     f"{datetime.now(timezone.utc).date().isoformat()}T00:00:00Z,"
                            f"{datetime.now(timezone.utc).date().isoformat()}T23:59:59Z",
            "bounding_box": f"{lon-0.1},{lat-0.1},{lon+0.1},{lat+0.1}",
            "page_size":    5,
        }
        r = requests.get(
            "https://cmr.earthdata.nasa.gov/search/granules.json",
            params=params, timeout=15
        )
        granules = r.json().get("feed", {}).get("entry", [])
        return {
            "disponible":    len(granules) > 0,
            "n_granules":    len(granules),
            "sample_id":     granules[0].get("id", "") if granules else "",
        }
    except Exception as e:
        return {"disponible": False, "error": str(e)}


def _check_gsmap_availability(lat: float, lon: float) -> dict:
    """GSMaP JAXA — precipitación horaria 0.1° (~11km). API pública."""
    import requests
    try:
        # GSMaP NRT via JAXA
        url = "https://sharaku.eorc.jaxa.jp/GSMaP/api/v1/"
        r   = requests.get(url, timeout=10)
        return {"disponible": r.status_code < 500, "status": r.status_code}
    except Exception as e:
        return {"disponible": False, "error": str(e)}


def _check_era5_availability(lat: float, lon: float) -> dict:
    """ERA5 — reanalysis meteorológico ECMWF. CDS API pública con registro gratuito."""
    import requests
    try:
        r = requests.get(
            "https://cds.climate.copernicus.eu/api/v2",
            timeout=10
        )
        return {
            "disponible": r.status_code in (200, 301, 302, 401),
            "status":     r.status_code,
        }
    except Exception as e:
        return {"disponible": False, "error": str(e)}


def _check_openelevation(lat: float, lon: float) -> dict:
    """Open-Elevation — DEM 30m gratuito (SRTM). Para el modelo estructural."""
    import requests
    try:
        r = requests.get(
            f"https://api.open-elevation.com/api/v1/lookup?locations={lat},{lon}",
            timeout=10
        )
        if r.ok:
            elev = r.json().get("results", [{}])[0].get("elevation")
            return {"disponible": True, "elevation_m": elev}
        return {"disponible": False, "status": r.status_code}
    except Exception as e:
        return {"disponible": False, "error": str(e)}


def _check_openaq(lat: float, lon: float) -> dict:
    """OpenAQ — calidad del aire (PM2.5, O3) gratuito, relevante para eventos."""
    import requests
    try:
        r = requests.get(
            f"https://api.openaq.org/v2/latest?coordinates={lat},{lon}&radius=10000&limit=5",
            headers={"Accept": "application/json"},
            timeout=10
        )
        if r.ok:
            results = r.json().get("results", [])
            return {"disponible": len(results) > 0, "n_stations": len(results),
                    "sample": results[0] if results else {}}
        return {"disponible": False, "status": r.status_code}
    except Exception as e:
        return {"disponible": False, "error": str(e)}


def _check_nominatim(lat: float, lon: float) -> dict:
    """Nominatim reverse geocoding — para enrichment de venue info."""
    import requests
    try:
        r = requests.get(
            f"https://nominatim.openstreetmap.org/reverse?lat={lat}&lon={lon}&format=json",
            headers={"User-Agent": "FaroProtocol/1.1 (protocolfaro@gmail.com)"},
            timeout=10
        )
        if r.ok:
            data = r.json()
            return {"disponible": True, "address": data.get("display_name", "")}
        return {"disponible": False, "status": r.status_code}
    except Exception as e:
        return {"disponible": False, "error": str(e)}


def _check_overpass_stadium(lat: float, lon: float) -> dict:
    """Overpass API (OSM) — geometría real del estadio (nivel, capacidad, dimensiones)."""
    import requests
    try:
        query = f"""
        [out:json][timeout:10];
        (
          way["leisure"="stadium"](around:500,{lat},{lon});
          relation["leisure"="stadium"](around:500,{lat},{lon});
        );
        out body;
        """
        r = requests.post(
            "https://overpass-api.de/api/interpreter",
            data={"data": query}, timeout=15
        )
        if r.ok:
            elements = r.json().get("elements", [])
            return {"disponible": len(elements) > 0, "n_elements": len(elements),
                    "sample_tags": elements[0].get("tags", {}) if elements else {}}
        return {"disponible": False, "status": r.status_code}
    except Exception as e:
        return {"disponible": False, "error": str(e)}


def _check_copernicus_land(lat: float, lon: float) -> dict:
    """Copernicus Land Service — cobertura terrestre y vegetación."""
    import requests
    try:
        r = requests.get(
            "https://land.copernicus.eu/api",
            timeout=10
        )
        return {"disponible": r.status_code < 500}
    except Exception as e:
        return {"disponible": False, "error": str(e)}


# ── Catálogo completo ─────────────────────────────────────────────────────────

SOURCE_CATALOG: list[dict] = [
    {
        "id":               "viirs_ndvi",
        "nombre":           "VIIRS VNP09GA v002 — NDVI Diario 500m (NASA)",
        "parametro":        "NDVI / reflectancia superficial",
        "resolucion_m":     500,
        "frecuencia_dias":  1.0,
        "gratuito":         True,
        "requiere_cred":    False,   # CMR metadatos gratis, píxeles requieren Earthdata
        "mejora_modulo":    "satellite",
        "url":              "https://cmr.earthdata.nasa.gov — VNP09GA",
        "motivo":           "Revisita diaria vs 5 días de S2. Útil como alerta temprana "
                            "de estrés hídrico entre dos escenas S2 limpias.",
        "check_fn":         _check_viirs_availability,
        "threshold_mejora": lambda info, existing: (
            info.get("disponible") and
            existing.get("revisita_dias", 5) > 1.0
        ),
        "module_code":      lambda: textwrap.dedent("""
            class VIIRSAlertModule(DalePlayModule):
                RESULT_KEY      = "viirs_alert"
                REQUIRED_FIELDS = ["disponible", "n_granules", "fecha_reciente"]
                MODES           = ("full", "post_show")
                CRITICAL        = False
                TIMEOUT_S       = 15
                DATA_SOURCE     = "VIIRS VNP09GA v002 · NASA CMR · 500m · diario"

                def run(self, rider: dict, **kwargs) -> dict:
                    try:
                        import requests
                        from datetime import date, timedelta
                        from dale_play_config import VENUE_BBOX, VENUE_LAT, VENUE_LON
                        end_dt   = date.today()
                        start_dt = end_dt - timedelta(days=kwargs.get("days_back", 3))
                        minx, miny, maxx, maxy = VENUE_BBOX
                        params = {
                            "short_name":   "VNP09GA",
                            "version":      "002",
                            "temporal":     f"{start_dt.isoformat()}T00:00:00Z,"
                                            f"{end_dt.isoformat()}T23:59:59Z",
                            "bounding_box": f"{minx},{miny},{maxx},{maxy}",
                            "page_size":    10,
                            "sort_key":     "-start_date",
                        }
                        r = requests.get(
                            "https://cmr.earthdata.nasa.gov/search/granules.json",
                            params=params, timeout=15
                        )
                        granules = r.json().get("feed", {}).get("entry", [])
                        n        = len(granules)
                        fecha    = (granules[0].get("time_start") or "")[:10] if granules else ""
                        return {
                            "disponible":     n > 0,
                            "n_granules":     n,
                            "fecha_reciente": fecha,
                            "fuente":         self.DATA_SOURCE,
                            "fallback_usado": False,
                        }
                    except Exception as exc:
                        return {"error": str(exc), "fuente": self.DATA_SOURCE,
                                "fallback_usado": True, "disponible": False, "n_granules": 0,
                                "fecha_reciente": ""}
        """),
    },
    {
        "id":               "openaq_air",
        "nombre":           "OpenAQ — Calidad del Aire (PM2.5, O3, NO2)",
        "parametro":        "PM2.5, O3, NO2, CO",
        "resolucion_m":     None,   # puntual por estación
        "frecuencia_dias":  0.04,   # ~1 hora
        "gratuito":         True,
        "requiere_cred":    False,
        "mejora_modulo":    "nuevo — AirQualityModule",
        "url":              "https://api.openaq.org/v2",
        "motivo":           "Calidad del aire en el venue. Relevante para eventos masivos "
                            "(densidad de personas + tráfico de producción). "
                            "No existe módulo de calidad del aire en el pipeline.",
        "check_fn":         _check_openaq,
        "threshold_mejora": lambda info, existing: (
            info.get("disponible") and info.get("n_stations", 0) > 0
        ),
        "module_code":      lambda: textwrap.dedent("""
            class AirQualityModule(DalePlayModule):
                RESULT_KEY      = "air_quality"
                REQUIRED_FIELDS = ["pm25_ug_m3", "estado_aire"]
                MODES           = ("full", "post_show")
                CRITICAL        = False
                TIMEOUT_S       = 10
                DATA_SOURCE     = "OpenAQ API v2 · PM2.5, O3, NO2 · gratuito"

                def run(self, rider: dict, **kwargs) -> dict:
                    try:
                        import requests
                        from dale_play_config import VENUE_LAT, VENUE_LON
                        r = requests.get(
                            f"https://api.openaq.org/v2/latest"
                            f"?coordinates={VENUE_LAT},{VENUE_LON}"
                            f"&radius=10000&limit=5",
                            headers={"Accept": "application/json"},
                            timeout=10
                        )
                        results = r.json().get("results", []) if r.ok else []
                        pm25 = None
                        for station in results:
                            for meas in station.get("measurements", []):
                                if meas.get("parameter") == "pm25":
                                    pm25 = meas.get("value")
                                    break
                            if pm25 is not None:
                                break
                        estado = ("malo" if (pm25 or 0) > 35
                                  else "moderado" if (pm25 or 0) > 12
                                  else "bueno") if pm25 is not None else "sin_datos"
                        return {
                            "pm25_ug_m3":   pm25,
                            "estado_aire":  estado,
                            "n_estaciones": len(results),
                            "fuente":       self.DATA_SOURCE,
                            "fallback_usado": pm25 is None,
                        }
                    except Exception as exc:
                        return {"error": str(exc), "fuente": self.DATA_SOURCE,
                                "fallback_usado": True, "pm25_ug_m3": None,
                                "estado_aire": "sin_datos"}
        """),
    },
    {
        "id":               "overpass_stadium",
        "nombre":           "OpenStreetMap Overpass — Geometría real del estadio",
        "parametro":        "footprint, capacidad, nombre, tags OSM",
        "resolucion_m":     1,
        "frecuencia_dias":  7,
        "gratuito":         True,
        "requiere_cred":    False,
        "mejora_modulo":    "structural",
        "url":              "https://overpass-api.de/api/interpreter",
        "motivo":           "Geometría del estadio en OSM puede enriquecer el digital twin "
                            "estructural con dimensiones más precisas que las hardcodeadas.",
        "check_fn":         _check_overpass_stadium,
        "threshold_mejora": lambda info, existing: (
            info.get("disponible") and info.get("n_elements", 0) > 0
        ),
        "module_code":      None,   # Complementa structural — no crea módulo propio
    },
    {
        "id":               "open_elevation",
        "nombre":           "Open-Elevation SRTM 30m — DEM gratuito",
        "parametro":        "elevación (m sobre nivel del mar)",
        "resolucion_m":     30,
        "frecuencia_dias":  None,   # estático
        "gratuito":         True,
        "requiere_cred":    False,
        "mejora_modulo":    "structural",
        "url":              "https://api.open-elevation.com/api/v1/lookup",
        "motivo":           "Elevación real del venue para corrección de presión atmosférica "
                            "y cálculo de velocidad de viento por cota.",
        "check_fn":         _check_openelevation,
        "threshold_mejora": lambda info, existing: (
            info.get("disponible") and info.get("elevation_m") is not None
        ),
        "module_code":      None,   # Dato auxiliar, no módulo propio
    },
    {
        "id":               "gsmap_rain",
        "nombre":           "GSMaP JAXA — Precipitación horaria 0.1° (~11km)",
        "parametro":        "precipitación mm/h",
        "resolucion_m":     11000,
        "frecuencia_dias":  0.04,   # horario
        "gratuito":         True,
        "requiere_cred":    False,
        "mejora_modulo":    "weather",
        "url":              "https://sharaku.eorc.jaxa.jp/GSMaP",
        "motivo":           "Precipitación horaria para alertas en tiempo real el día del show. "
                            "Complementa Open-Meteo (pronóstico) con observación real.",
        "check_fn":         _check_gsmap_availability,
        "threshold_mejora": lambda info, existing: info.get("disponible"),
        "module_code":      None,   # Mejora WeatherModule, no crea módulo nuevo
    },
]

# ═══════════════════════════════════════════════════════════════════════════════
# Estado actual del pipeline (para comparar)
# ═══════════════════════════════════════════════════════════════════════════════

def _get_existing_pipeline_state() -> dict:
    """Snapshot del estado actual del pipeline para comparación."""
    return {
        "revisita_dias":       5.0,     # S2 L2A
        "ndvi_resolucion_m":   10,      # S2 10m
        "weather_frecuencia":  "72h pronóstico",
        "modulos_activos":     [
            "satellite", "weather", "soil_real", "soil", "drainage",
            "acoustic", "egms", "spl_compliance", "structural", "rider_compliance"
        ],
        "modulos_sin_aire":    True,    # sin AirQualityModule
        "modulos_sin_viirs":   True,    # sin alerta diaria NDVI
    }


# ═══════════════════════════════════════════════════════════════════════════════
# Evaluación de fuentes
# ═══════════════════════════════════════════════════════════════════════════════

def _evaluate_source(source: dict, lat: float, lon: float,
                     existing: dict) -> dict:
    """
    Evalúa una fuente candidata.
    Retorna dict con: disponible, sirve, motivo_evaluacion, info_raw.
    """
    source_id = source["id"]
    log.info("check_new_sources: evaluando %s", source_id)
    result = {
        "id":            source_id,
        "nombre":        source["nombre"],
        "gratuito":      source["gratuito"],
        "requiere_cred": source["requiere_cred"],
        "resolucion_m":  source.get("resolucion_m"),
        "frecuencia_dias": source.get("frecuencia_dias"),
        "mejora_modulo": source["mejora_modulo"],
        "url":           source["url"],
        "motivo":        source["motivo"],
    }

    try:
        info = source["check_fn"](lat, lon)
        result["info_raw"]   = info
        result["disponible"] = info.get("disponible", False)

        threshold_fn = source.get("threshold_mejora")
        sirve = (
            source["gratuito"] and
            result["disponible"] and
            (threshold_fn(info, existing) if threshold_fn else False)
        )
        result["sirve"]           = sirve
        result["motivo_evaluacion"] = (
            "Disponible y mejor que pipeline actual"
            if sirve else
            f"No disponible ({info.get('error', info.get('status', 'no_data'))})"
            if not result["disponible"] else
            "Disponible pero no supera umbral de mejora"
        )
    except Exception as e:
        result["info_raw"]          = {"error": str(e)}
        result["disponible"]        = False
        result["sirve"]             = False
        result["motivo_evaluacion"] = f"Error en check: {e}"

    log.info("check_new_sources: %s → sirve=%s  (%s)",
             source_id, result["sirve"], result["motivo_evaluacion"])
    return result


# ═══════════════════════════════════════════════════════════════════════════════
# Integración al pipeline
# ═══════════════════════════════════════════════════════════════════════════════

def _try_integrate(source: dict, eval_result: dict) -> dict:
    """
    Intenta integrar la fuente al pipeline como DalePlayModule.
    Si tiene `module_code`, genera el código y lo registra.
    Retorna {"integrado": bool, "resultado": str, "codigo": str|None}.
    """
    code_fn = source.get("module_code")
    if code_fn is None:
        return {
            "integrado": False,
            "resultado": "Fuente complementaria — no genera módulo independiente. "
                         "Enriquece módulo existente: " + source["mejora_modulo"],
            "codigo":    None,
        }

    try:
        code = code_fn()
        # Nombre de la clase: primer token "class XxxModule" en el código
        import re
        m = re.search(r"class\s+(\w+Module)", code)
        class_name   = m.group(1) if m else f"{source['id'].title()}Module"
        result_key   = re.search(r'RESULT_KEY\s*=\s*["\'](\w+)["\']', code)
        rkey         = result_key.group(1) if result_key else source["id"]

        # Escribir módulo a dale_play_{id}.py si no existe
        module_file = DALE_DIR / f"dale_play_{source['id']}.py"
        if not module_file.exists():
            module_file.write_text(
                f'"""\ndale_play_{source["id"]}.py — auto-generado por check_new_sources.py\n"""\n'
                f'# Módulo: {source["nombre"]}\n'
                f'# Fuente: {source["url"]}\n\n'
                f'# La lógica está embebida en {class_name} dentro de dale_play_modules.py\n',
                encoding="utf-8"
            )

        # Intentar agregar la clase a dale_play_modules.py
        modules_path = DALE_DIR / "dale_play_modules.py"
        modules_raw  = modules_path.read_text(encoding="utf-8")

        if class_name in modules_raw:
            return {
                "integrado": False,
                "resultado": f"{class_name} ya existe en dale_play_modules.py",
                "codigo":    code,
            }

        # Insertar clase antes del comentario "── Registro global ──"
        marker = "# ── Registro global ──"
        if marker not in modules_raw:
            marker = "MODULES: list[DalePlayModule] ="
        if marker not in modules_raw:
            return {
                "integrado": False,
                "resultado": "No se encontró el punto de inserción en dale_play_modules.py",
                "codigo":    code,
            }

        # Indent del código de la clase
        class_block = "\n\n" + code.strip() + "\n"
        modules_new = modules_raw.replace(marker, class_block + "\n\n" + marker, 1)

        # Agregar instancia al final de MODULES list
        modules_new = modules_new.replace(
            "]\n\nMODULES_BY_KEY",
            f"    {class_name}(),\n]\n\nMODULES_BY_KEY"
        )

        modules_path.write_text(modules_new, encoding="utf-8")

        # Verificar que el módulo carga correctamente
        try:
            spec   = importlib.util.spec_from_file_location("dale_play_modules", str(modules_path))
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            mods   = [type(m).__name__ for m in module.MODULES]
            if class_name not in mods:
                raise ImportError(f"{class_name} no aparece en MODULES tras la inserción")
        except Exception as ve:
            # Revertir
            modules_path.write_text(modules_raw, encoding="utf-8")
            return {
                "integrado": False,
                "resultado": f"Verificación fallida — revertido: {ve}",
                "codigo":    code,
            }

        return {
            "integrado": True,
            "resultado": f"{class_name} integrado en dale_play_modules.py. "
                         f"RESULT_KEY='{rkey}'",
            "codigo":    code,
        }

    except Exception as e:
        log.error("_try_integrate %s: %s", source["id"], e, exc_info=True)
        return {
            "integrado": False,
            "resultado": f"Error en integración: {e}",
            "codigo":    None,
        }


# ═══════════════════════════════════════════════════════════════════════════════
# Email
# ═══════════════════════════════════════════════════════════════════════════════

def _send_email(subject: str, html_body: str, text_body: str) -> bool:
    """
    Envía email a protocolfaro@gmail.com via Gmail SMTP.
    Requiere env var EMAIL_APP_PASS (Gmail app password, 16 chars).
    """
    password = os.environ.get("EMAIL_APP_PASS", "")
    if not password:
        log.warning("check_new_sources: EMAIL_APP_PASS no configurado — email no enviado")
        return False

    msg              = MIMEMultipart("alternative")
    msg["Subject"]   = subject
    msg["From"]      = NOTIFY_EMAIL
    msg["To"]        = NOTIFY_EMAIL
    msg.attach(MIMEText(text_body, "plain", "utf-8"))
    msg.attach(MIMEText(html_body, "html",  "utf-8"))

    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
            server.ehlo()
            server.starttls()
            server.login(NOTIFY_EMAIL, password)
            server.sendmail(NOTIFY_EMAIL, [NOTIFY_EMAIL], msg.as_string())
        log.info("check_new_sources: email enviado → %s", NOTIFY_EMAIL)
        return True
    except Exception as e:
        log.error("check_new_sources: email error: %s", e)
        return False


def _build_email(fuentes_nuevas: list[dict]) -> tuple[str, str]:
    """Construye subject + cuerpo HTML/text del email de notificación."""
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    n     = len(fuentes_nuevas)
    subject = f"Faro Protocol · {n} fuente{'s' if n > 1 else ''} nueva{'s' if n > 1 else ''} detectada{'s' if n > 1 else ''} · {stamp[:10]}"

    # Plain text
    lines = [f"FARO PROTOCOL — CHECK NEW SOURCES\n{stamp}\n{'='*50}\n"]
    for f in fuentes_nuevas:
        ev   = f["eval"]
        intg = f["integration"]
        lines.append(f"\nFUENTE: {ev['nombre']}")
        lines.append(f"  Módulo mejorado: {ev['mejora_modulo']}")
        lines.append(f"  Por qué sirve:   {ev['motivo']}")
        lines.append(f"  Gratuita:        {'Sí' if ev['gratuito'] else 'No'}")
        if ev.get("resolucion_m"):
            lines.append(f"  Resolución:      {ev['resolucion_m']}m")
        if ev.get("frecuencia_dias"):
            lines.append(f"  Frecuencia:      {ev['frecuencia_dias']} días")
        lines.append(f"  URL:             {ev['url']}")
        lines.append(f"  Integración:     {intg['resultado']}")
        if f.get("manual_diff"):
            lines.append(f"\n  MANUAL.md diff:\n{f['manual_diff']}")

    text_body = "\n".join(lines)

    # HTML
    rows = ""
    for f in fuentes_nuevas:
        ev   = f["eval"]
        intg = f["integration"]
        integrado_badge = (
            '<span style="color:#27ae60;font-weight:bold">✓ INTEGRADO</span>'
            if intg.get("integrado") else
            '<span style="color:#f0b429">→ COMPLEMENTARIO</span>'
        )
        rows += f"""
        <tr>
          <td style="padding:8px;border-bottom:1px solid #2a2e38">
            <strong style="color:#c9a84c">{ev['nombre']}</strong><br>
            <span style="color:#8a9099;font-size:0.85em">{ev['url']}</span>
          </td>
          <td style="padding:8px;border-bottom:1px solid #2a2e38;color:#f2ede4">{ev['mejora_modulo']}</td>
          <td style="padding:8px;border-bottom:1px solid #2a2e38;color:#f2ede4">{ev.get('resolucion_m','—')}m</td>
          <td style="padding:8px;border-bottom:1px solid #2a2e38;color:#f2ede4">{ev.get('frecuencia_dias','—')} días</td>
          <td style="padding:8px;border-bottom:1px solid #2a2e38">{integrado_badge}</td>
        </tr>
        <tr>
          <td colspan="5" style="padding:6px 8px 12px 8px;color:#8a9099;font-size:0.85em;border-bottom:2px solid #c9a84c">
            <em>{ev['motivo']}</em><br>
            Integración: {intg['resultado']}
          </td>
        </tr>
        """

    html_body = f"""
    <!DOCTYPE html>
    <html>
    <head><meta charset="utf-8"></head>
    <body style="background:#0d1117;color:#f2ede4;font-family:monospace;padding:24px">
      <h2 style="color:#c9a84c;border-bottom:1px solid #c9a84c;padding-bottom:8px">
        FARO PROTOCOL · CHECK NEW SOURCES
      </h2>
      <p style="color:#8a9099">{stamp} · {n} fuente(s) nueva(s) detectada(s)</p>
      <table width="100%" style="border-collapse:collapse;margin-top:16px">
        <thead>
          <tr style="background:#111318">
            <th style="padding:8px;text-align:left;color:#c9a84c">Fuente</th>
            <th style="padding:8px;text-align:left;color:#c9a84c">Módulo</th>
            <th style="padding:8px;text-align:left;color:#c9a84c">Resolución</th>
            <th style="padding:8px;text-align:left;color:#c9a84c">Frecuencia</th>
            <th style="padding:8px;text-align:left;color:#c9a84c">Estado</th>
          </tr>
        </thead>
        <tbody>{rows}</tbody>
      </table>
      <p style="color:#8a9099;margin-top:24px;font-size:0.85em">
        MANUAL.md actualizado via <code>append_to_manual()</code> — nunca sobreescribe.<br>
        Próximo check automático: en 3 días.
      </p>
    </body>
    </html>
    """
    return subject, (html_body, text_body)


# ═══════════════════════════════════════════════════════════════════════════════
# Función principal
# ═══════════════════════════════════════════════════════════════════════════════

def check_all_sources() -> list[dict]:
    """
    Evalúa todas las fuentes del catálogo. Para las que sirven:
    - Intenta integrar al pipeline
    - Agrega al MANUAL.md via append_to_manual()
    - Manda email de notificación

    Retorna lista de resultados completos.
    """
    from dale_play_config import VENUE_LAT, VENUE_LON
    from dale_play_manual import append_new_source, append_module_added, get_section_content

    log.info("check_new_sources: iniciando evaluación de %d fuentes", len(SOURCE_CATALOG))
    existing   = _get_existing_pipeline_state()
    resultados = []
    nuevas     = []

    for source in SOURCE_CATALOG:
        ev   = _evaluate_source(source, VENUE_LAT, VENUE_LON, existing)
        intg = {"integrado": False, "resultado": "no evaluado", "codigo": None}

        if ev["sirve"]:
            intg = _try_integrate(source, ev)

            # MANUAL.md — append (nunca sobreescribe)
            manual_before = get_section_content("15. ESTADO ACTUAL") or ""
            append_new_source(
                source_name   = source["nombre"],
                modulo        = source["mejora_modulo"],
                descripcion   = source["motivo"],
                resolucion    = f"{source.get('resolucion_m','?')}m",
                frecuencia    = f"{source.get('frecuencia_dias','?')} días",
                credenciales  = "No requiere" if not source["requiere_cred"] else "Requiere (ver url)",
                mejora        = ev["motivo_evaluacion"],
            )

            if intg.get("integrado"):
                append_module_added(
                    result_key  = source["id"],
                    archivo     = f"dale_play_{source['id']}.py",
                    data_source = source["url"],
                    descripcion = source["motivo"],
                )

            manual_after = get_section_content("15. ESTADO ACTUAL") or ""
            diff_lines   = _simple_diff(manual_before, manual_after)

            nuevas.append({
                "source":      source,
                "eval":        ev,
                "integration": intg,
                "manual_diff": diff_lines,
            })

        resultados.append({
            "source":      source["id"],
            "eval":        ev,
            "integration": intg,
        })

    # Email solo si hay novedades
    if nuevas:
        try:
            subj, (html_body, text_body) = _build_email(nuevas)
            _send_email(subj, html_body, text_body)
        except Exception as e:
            log.error("check_new_sources: email build error: %s", e)

    log.info("check_new_sources: fin — %d fuentes evaluadas, %d sirven",
             len(resultados), len(nuevas))
    return resultados


def _simple_diff(before: str, after: str) -> str:
    """Genera un diff simple de líneas agregadas (sin librerías externas)."""
    before_lines = set(before.splitlines())
    after_lines  = after.splitlines()
    added = [l for l in after_lines if l not in before_lines]
    if not added:
        return "(sin cambios)"
    return "\n".join(f"+ {l}" for l in added[:30])


# ═══════════════════════════════════════════════════════════════════════════════
# Registro en APScheduler
# ═══════════════════════════════════════════════════════════════════════════════

def register_scheduler(scheduler) -> None:
    """
    Registra el job de check_new_sources en el scheduler de APScheduler.
    Llamar desde app.py pasando el scheduler ya inicializado.

    Frecuencia: cada 3 días, a las 04:00 UTC (baja carga de APIs externas).
    """
    from apscheduler.triggers.interval import IntervalTrigger
    from datetime import timedelta

    job_id = "dale_play_check_new_sources"

    # Evitar duplicados si se llama más de una vez
    existing_job = scheduler.get_job(job_id)
    if existing_job:
        log.info("register_scheduler: job '%s' ya registrado — skip", job_id)
        return

    scheduler.add_job(
        func              = check_all_sources,
        trigger           = IntervalTrigger(days=3, jitter=3600),   # jitter ±1h
        id                = job_id,
        name              = "Dale Play · Check New Sources (cada 3 días)",
        replace_existing  = True,
        misfire_grace_time= 3600,
    )
    log.info("register_scheduler: job '%s' registrado — cada 3 días", job_id)


# ═══════════════════════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import json
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    log.info("check_new_sources: corriendo manualmente...")
    results = check_all_sources()
    print("\n" + "=" * 55)
    print("CHECK NEW SOURCES - RESULTADOS")
    print("=" * 55)
    for r in results:
        ev    = r["eval"]
        sirve = ev.get("sirve", False)
        marca = "[SIRVE]" if sirve else "[skip] "
        nombre = ev["nombre"].encode("ascii", "replace").decode()[:55]
        motivo = ev["motivo_evaluacion"].encode("ascii", "replace").decode()
        print(f"  {marca}  {nombre}")
        print(f"           -> {motivo}")
        if sirve:
            intg = r["integration"]["resultado"].encode("ascii", "replace").decode()
            print(f"           -> integracion: {intg}")
    print("=" * 55)
