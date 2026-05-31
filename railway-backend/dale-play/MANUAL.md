# FARO PROTOCOL — DALE PLAY · MANUAL COMPLETO DEL SISTEMA

> **LEER ANTES DE TOCAR UNA SOLA LÍNEA DE CÓDIGO.**
> Este archivo es la fuente de verdad del sistema. Si algo no está aquí, no existe o está mal.
> Fecha de última actualización: TEST-2026-05-31. Versión: 1.1.

---

## 1. QUÉ ES ESTE SISTEMA

**Dale Play** es una productora de eventos masivos. **Faro Protocol** le provee una auditoría técnica automatizada completa antes (y después) de cada show, que cubre el estado del campo, el riesgo climático, la capacidad del suelo, la cobertura acústica, el compliance regulatorio y la integridad estructural.

| Atributo | Valor |
|---|---|
| Producto | Auditoría operativa de eventos masivos en estadios |
| Cliente | Dale Play (productora) |
| Precio | u$s 8.000 por show |
| Venue piloto | Estadio José Amalfitani · Vélez Sarsfield |
| Coordenadas | lat -34.6379, lon -58.5288 |
| Bounding box | (-58.5305, -34.6391, -58.5271, -34.6367) |
| Capacidad | 49.540 espectadores |
| Dirección | Juan B. Justo 9200, Liniers, Buenos Aires |
| Stack | Python 3.11 · Flask · Railway · GitHub Pages · Supabase |
| Base de datos | PostgreSQL en Supabase (REST API directa, sin supabase-py) |
| Almacenamiento PNG | GitHub repo `protocolfaro/faroprotocol`, branch `main` |

**Principio rector:** El sistema nunca miente. Datos faltantes se declaran explícitamente con `fallback_usado: true` o `estimado: true`. Un reporte con datos falsos es peor que ningún reporte.

### 1.1 VENUE — ZONAS DE CAMPO Y SISTEMA DE PROTECCIÓN

El campo del Amalfitani tiene tres zonas con superficies distintas que determinan el tipo de protección requerida para eventos masivos:

| Zona | Superficie | Sistema de protección | Observaciones |
|---|---|---|---|
| Campo central | Césped natural híbrido | **Terraplas 2×2m** | Zona de mayor riesgo de daño |
| Perimetral | Césped sintético | Terraplas (menor riesgo) | Más resistente, menor prioridad |
| Accesos | Concreto / asfalto | Arena Panel aluminio (eps.net) | Para carga pesada (vehículos, camiones) |

**Sistema Terraplas (eps.net) — especificaciones técnicas:**

| Parámetro | Valor |
|---|---|
| Dimensiones panel | 2m × 2m × 6cm (4 elementos de 1×1m ensamblables) |
| Peso por unidad | 50 kg |
| Material | Polietileno HDPE UV-estabilizado |
| Permeabilidad | Permeable a luz, agua y aire |
| Protección máxima | Hasta 7 días sin daño al césped subyacente |
| Superficie campo central | 7.140 m² |
| Paneles necesarios (campo completo) | **1.785 paneles** (= 7.140 / 4 m² por panel) |
| Fuente | eps.net/en/products/pedestrian-event-flooring/terraplas |

**Implicancias para el pipeline:**
- La presencia o ausencia de Terraplas en el rider **modifica la capacidad portante efectiva** del suelo: con Terraplas, la distribución de carga reduce la presión por punto al doblar el área efectiva.
- El módulo de suelo (`SoilModule`) y el de drenaje (`DrainageModule`) deben conocer si el rider declara uso de Terraplas para ajustar las presiones calculadas.
- El Terraplas NO elimina las zonas de exclusión hídrica (Canal N-S, Colector Sur) — solo protege el césped del tráfico peatonal. Las restricciones de drenaje aplican con o sin Terraplas.
- Campo sin Terraplas + show de 35.000 personas → daño severo al césped garantizado (evidencia de múltiples shows argentinos 2019-2024).

---

## 2. ARQUITECTURA

```
Dale Play sube rider JSON
        ↓
shows/{show_id}.json
        ↓
dale_play_pipeline.py → run_show_audit()
        ↓ (loop en orden)
[SatelliteModule] → dale_play_satellite.py
[WeatherModule]   → dale_play_weather.py
[SoilRealModule]  → dale_play_soil.py
[SoilModule]      → dale_play_soil.py
[DrainageModule]  → dale_play_drainage.py
[AcousticModule]  → dale_play_acoustic.py
[EGMSModule]      → dale_play_egms.py
[SPLComplianceModule] → dale_play_spl_compliance.py
[StructuralTwinModule] → dale_play_structural.py
[RiderComplianceModule] → dale_play_rider_compliance.py
        ↓
[FII]        → dale_play_fii.py
[Comparativa] → dale_play_vision.py
[Smoke tests] → dale_play_smoke.py  ← si falla, no se genera PNG
        ↓
[Reporte PNG] → dale_play_report.py → reportes/reporte_{show_id}.png
[Push GitHub] → dale_play_github.py
[Run log]     → models/run_log_{show_id}.json
[Audit log]   → Supabase.audit_log (append-only, inmutable)
```

### 2.1 Clase base: DalePlayModule (dale_play_module.py)

Todo módulo hereda de `DalePlayModule` (ABC). Contrato obligatorio:

```python
class DalePlayModule(ABC):
    RESULT_KEY:      str          # clave en el dict result del pipeline
    REQUIRED_FIELDS: list[str]    # campos obligatorios en el output
    MODES:           tuple        # ("full", "post_show", "weather_only")
    CRITICAL:        bool         # True → pipeline para si falla (circuit breaker)
    TIMEOUT_S:       int          # segundos antes de timeout (default 30)
    DATA_SOURCE:     str          # descripción de la fuente real

    def run(self, rider: dict, **kwargs) -> dict:   # ABSTRACTO
    def validate(self, output: dict) -> list[str]:  # valida REQUIRED_FIELDS
    def confianza(self, output: dict) -> str:        # 'verde'|'amarillo'|'rojo'
    def extract(self, result: dict) -> dict:         # accessor seguro
```

**Contrato de run():**
- Éxito: `{"campo1": ..., "fuente": str, "fallback_usado": bool}`
- Fallo: `{"error": str, "fuente": str, "fallback_usado": bool}`
- **NUNCA lanza excepciones al caller.** Todo error va en el dict de retorno.

**Semáforo de confianza:**
- `verde`: dato real, fuente verificada, `fallback_usado=False`, `estimado=False`
- `amarillo`: fallback usado, estimado, o fuente contiene `[ESTIMADO]`
- `rojo`: módulo falló (`error` presente en output)

### 2.2 Pipeline (dale_play_pipeline.py)

`run_show_audit(show_config, mode)` corre los módulos en el orden de `MODULES` con estas garantías:

1. **Timeout**: cada módulo corre en `ThreadPoolExecutor` con `TIMEOUT_S`. Si supera el límite, retorna `{"error": "Timeout Xs superado", ...}`.
2. **Circuit breaker**: si un módulo `CRITICAL=True` falla, el pipeline para inmediatamente, guarda el run_log y retorna. No genera PNG.
3. **Módulos no críticos**: si fallan, se registra en `_log_fallidos` pero el pipeline continúa con `fallback_usado=True`.
4. **Smoke tests**: se corren antes de generar el PNG. Si fallan, no se genera PNG y se retorna el error.

**Modos disponibles:**
| Modo | Descripción | Módulos |
|---|---|---|
| `full` | Auditoría completa pre-show | Todos los módulos |
| `weather_only` | Solo pronóstico climático | WeatherModule |
| `post_show` | Full + InSAR + certificación | Todos + InSAR + PDF cert |

### 2.3 Reporte PNG (dale_play_report.py)

- Lee los outputs del pipeline desde el dict `result`. **Nunca hardcodea datos.**
- GridSpec: 19 filas, `figsize=(14, 68)`. Cada sección ocupa una o más filas.
- Todo dato que no viene del pipeline se muestra como "N/D" o "Sin datos".
- Si el output de un módulo tiene `error`, la sección muestra el error visible.

### 2.4 Persistencia (dale_play_storage.py)

Tres capas de persistencia, en orden de prioridad:
1. **Cache local** (`dale-play/cache/`): se escribe antes de intentar Supabase.
2. **Supabase REST API**: upsert con retry (2 intentos, backoff 2s).
3. **Fallback local** (`dale-play/models/storage/`): si Supabase no disponible.

El sistema nunca crashea por falta de Supabase. Todo error se loguea en stderr.

**Tabla `audit_log`**: append-only. Solo INSERT, nunca UPDATE. Es el registro inmutable de auditoría.

---

## 3. MÓDULOS — FUENTES REALES Y CONTRATOS

### 3.1 SatelliteModule

| Atributo | Valor |
|---|---|
| Archivo | `dale_play_satellite.py` |
| RESULT_KEY | `satellite` |
| CRITICAL | `True` |
| TIMEOUT_S | 60 |
| DATA_SOURCE | HLS / Sentinel-2 L2A / Landsat C2L2 · Planetary Computer + NASA CMR MODIS |
| MODES | full, post_show |

**Jerarquía de fuentes NDVI (por prioridad en modo post_show):**

| Fuente | Resolución | Revisita | Modo | Requiere credenciales |
|---|---|---|---|---|
| HLS (Harmonized Landsat+S2) | 30m | ~1.4 días | post_show (primaria) | No |
| Sentinel-2 L2A | 10m | ~5 días | full (primaria) / post_show (fallback) | No |
| Landsat-9 TIRS | 100m | ~16 días | complementario | No |
| MODIS MOD09GQ | 250m | ~1 día | post_show (alerta temprana) | No (solo metadatos) |
| Sen2SR (ESA) | 2.5m | — | post_show + S2 limpia | No (pip install sen2sr) |

**Sentinel-1 GRD SAR (compactación post-show):**
Implementado en `dale_play_insar.py` como `fetch_sar_compaction()`. Detecta cambios en backscatter VV pre/post show. Delta > 1.5 dB → alerta. No requiere HyP3 ni NASA Earthdata.

**Fuente de datos:**
- NDVI: HLS o Sentinel-2 B04/B08 via Planetary Computer STAC (token SAS automático)
- TIRS: Landsat-9 C2L2 ST_B10 via Planetary Computer
- MODIS alerta: NASA CMR granule count (sin descarga de píxeles)
- Sen2SR: librería ESA (opcional, degradación graceful si no instalada)

**Output esperado:**
```json
{
  "ndvi": 0.099,
  "ndvi_fecha": "2026-05-29",
  "ndvi_cloud_pct": 8.1,
  "ndvi_status": {"semaforo": "amarillo", "label": "Dormancia estacional (Bermuda — invierno BsAs)"},
  "fuente_tipo": "HLS",
  "revisita_dias": 1.4,
  "fuente_ndvi": "HLS · Harmonized Landsat+Sentinel-2 · Planetary Computer",
  "fuente_s2": "Sentinel-2 L2A · Planetary Computer",
  "tirs_celsius": 14.2,
  "tirs_fecha": "2026-04-28",
  "fuente_tirs": "Landsat-9 C2L2 ST_B10 · Planetary Computer",
  "modis_alerta": {
    "disponible": true,
    "n_granules": 8,
    "fecha_mas_reciente": "2026-05-30",
    "tile": "h13v12",
    "producto": "MOD09GQ v061 · 250m · diario",
    "fuente": "NASA CMR · MODIS Terra MOD09GQ"
  },
  "sen2sr_aplicado": false,
  "sen2sr_estado": "no_instalado — pip install sen2sr",
  "fuente": "HLS / Sentinel-2 L2A / Landsat C2L2 · Planetary Computer + NASA CMR MODIS",
  "fallback_usado": false
}
```

**REQUIRED_FIELDS:** `["ndvi", "ndvi_fecha"]`

**Thresholds NDVI:**
```python
NDVI_BUENO     = 0.55   # verde — césped en buen estado
NDVI_DEGRADADO = 0.35   # amarillo — estrés moderado
# < 0.35              → rojo — daño severo
# Dormancia Bermuda: 0.08-0.25 en meses 4-8 → amarillo (NO rojo)
```

**Corrección de dormancia (BUG #1 corregido):**
La bermuda grass (césped del Amalfitani) entra en dormancia otoño/invierno en Buenos Aires (meses abril-agosto). NDVI 0.08-0.25 en ese período **no es daño**, es dormancia normal. Sin esta corrección el sistema reportaba ROJO incorrecto.

```python
def _classify_ndvi(ndvi, month=None):
    if month is not None and 4 <= month <= 8 and 0.08 <= ndvi <= 0.25:
        return {"semaforo": "amarillo", "label": "Dormancia estacional (Bermuda — invierno BsAs)"}
    ...
```

**Sen2SR — instalación:**
```bash
pip install sen2sr
```
Si no está instalado, el módulo retorna `sen2sr_estado: "no_instalado"` sin error. No bloquea el pipeline.

**Sentinel-1 SAR compactación — uso standalone:**
```python
from dale_play_insar import fetch_sar_compaction
result = fetch_sar_compaction(show_date="2026-05-31", days_pre=10, days_post=5)
# → {disponible, pre_vv_db, post_vv_db, delta_vv_db, alerta_compactacion, interpretacion}
```

<!-- append:2026-05-31 v1.1. -->
#### Umbra Space SAR X-band · integrado 2026-05-31
- **RESULT_KEY:** `umbra_sar`
- **Archivo:** `dale_play_umbra.py`
- **Fuente:** Umbra Open Data Catalog (CC-BY-4.0, sin autenticación)
- **Sensor:** X-band (9.8 GHz) · VV · Modo SPOTLIGHT
- **Resolución:** ~0.18m EW × 0.20m NS (~25cm nominal)
- **Formato:** COG uint8 amplitud EPSG:4326 · lectura via HTTP range (sin descarga completa)
- **Cobertura Buenos Aires:** escena 2024-07-15 UMBRA-05, bbox (-58.418, -34.633, -58.346, -34.574)
- **Nota cobertura:** Amalfitani (lon=-58.529) queda 9.4 km al oeste del bbox disponible
- **Fallback:** cuando no hay escena que cubra el venue, reporta escena BA con `umbra_cobre_venue: False`
- **Detección de cambio:** `compare_umbra_scenes()` — umbral compactación 1.5 dB, estructural 3.0 dB
- **STAC:** https://s3.us-west-2.amazonaws.com/umbra-open-data-catalog/stac/catalog.json

---

### 3.2 WeatherModule

| Atributo | Valor |
|---|---|
| Archivo | `dale_play_weather.py` |
| RESULT_KEY | `weather` |
| CRITICAL | `True` |
| TIMEOUT_S | 20 |
| DATA_SOURCE | Open-Meteo API |
| MODES | full, post_show, weather_only |

**Fuente de datos:** Open-Meteo API pública (sin API key). Pronóstico 72h para el venue.

**URL:** `https://api.open-meteo.com/v1/forecast?latitude=-34.6379&longitude=-58.5288&daily=...`

**Output esperado:**
```json
{
  "show_date": "2026-05-31",
  "dias": [...],
  "show_day": {
    "fecha": "2026-05-31",
    "lluvia_mm": 0.0,
    "viento_max_kmh": 17.0,
    "rachas_max_kmh": 25.0,
    "temp_max": 18.0,
    "temp_min": 11.0,
    "riesgo_viento": "ok",
    "riesgo_lluvia": "ok",
    "riesgo_temp": "ok"
  },
  "alertas": [],
  "riesgo_global": "ok",
  "fuente": "Open-Meteo API"
}
```

**REQUIRED_FIELDS:** `["show_day", "riesgo_global"]`

**Thresholds:**
```python
WIND_CAUTION_KMH  = 40   # km/h — atencion
WIND_CRITICAL_KMH = 65   # km/h — critico (estructura en riesgo)
RAIN_CAUTION_MM   = 10   # mm — atencion
TEMP_MIN_SAFE     = 4    # °C
TEMP_MAX_SAFE     = 38   # °C
```

---

### 3.3 SoilRealModule

| Atributo | Valor |
|---|---|
| Archivo | `dale_play_soil.py` |
| RESULT_KEY | `soil_real` |
| CRITICAL | `False` |
| TIMEOUT_S | 30 |
| DATA_SOURCE | SoilGrids REST API v2 (ISRIC) |
| MODES | full, post_show |

**Fuente de datos:** SoilGrids REST API (`https://rest.isric.org/soilgrids/v2.0/properties/query`) para capacidad portante real en el venue. Sin credenciales. Fallback: 120 kPa (franco-arcilloso típico Liniers).

**Output esperado:**
```json
{
  "capacidad_portante_kpa": 120,
  "fuente": "SoilGrids REST API v2 (ISRIC)",
  "estimado": false,
  "fallback_usado": false
}
```

**REQUIRED_FIELDS:** `["capacidad_portante_kpa"]`

---

### 3.4 SoilModule

| Atributo | Valor |
|---|---|
| Archivo | `dale_play_soil.py` |
| RESULT_KEY | `soil` |
| CRITICAL | `True` |
| TIMEOUT_S | 30 |
| DATA_SOURCE | SoilGrids + Terzaghi (Faro Protocol) |
| MODES | full, post_show |

**Fuente de datos:** SoilGrids para textura + modelo Terzaghi para capacidad portante. Considera lluvia de las últimas 48h (saturación reduce capacidad).

**Output esperado:**
```json
{
  "zonas": [...],
  "n_exclusiones": 0,
  "capacidad_efectiva_kpa": 98,
  "fuente": "SoilGrids + Terzaghi (Faro Protocol)",
  "fallback_usado": false
}
```

**REQUIRED_FIELDS:** `["zonas", "n_exclusiones", "capacidad_efectiva_kpa"]`

**Thresholds:**
```python
SOIL_SAFE_KPA     = 80    # capacidad portante segura (kPa)
SOIL_CAUTION_KPA  = 100   # precaución
SOIL_CRITICAL_KPA = 120   # crítico — zona de exclusión
```

---

### 3.5 DrainageModule

| Atributo | Valor |
|---|---|
| Archivo | `dale_play_drainage.py` |
| RESULT_KEY | `drainage` |
| CRITICAL | `False` |
| TIMEOUT_S | 30 |
| DATA_SOURCE | SoilGrids Ksat + modelo hidráulico Faro Protocol |
| MODES | full, post_show |

**Fuente de datos:** SoilGrids REST API para textura → Ksat (conductividad hidráulica) via pedotransfer Rawls & Brakensiek (1985), equivalente a HiHydroSoil v2.0. Los datos de infraestructura de drenaje son de 2015 (documentación Amalfitani).

**Zonas del campo:**
| ID | Nombre | Tipo | Capacidad (kPa) |
|---|---|---|---|
| canal_central | Canal N-S | exclusion | 32 |
| colector_sur | Colector Sur | exclusion | 28 |
| lateral_este | Lateral E | precaucion | 58 |
| lateral_oeste | Lateral O | precaucion | 58 |
| area_norte | Área Norte | seguro | 118 |
| cuadrante_este | Centro E | seguro | 105 |
| cuadrante_oeste | Centro O | seguro | 105 |

**Output esperado:**
```json
{
  "zonas": [...],
  "exclusiones": [
    "Escenario: tarimas distribuidoras (mín. 2.5 m²/punto de carga) sobre toda la zona Colector Sur",
    "Torres L/R: verificar que pie de torre no caiga sobre Canal N-S — desplazar 0.5 m si es necesario"
  ],
  "resumen": {
    "zonas_exclusion": 2, "zonas_precaucion": 2, "zonas_seguras": 3,
    "ksat_mm_h": 1.2, "riesgo_global": "alto"
  },
  "fuente": "SoilGrids Ksat + modelo hidráulico Faro Protocol"
}
```

**REQUIRED_FIELDS:** `["zonas", "resumen"]`

**BUG #6 corregido:** La lista `exclusiones` contenía "Consola FOH recomendada en Área Norte — confirmado, sin restricciones" que es una **aprobación**, no una exclusión. Estaba propagándose como incumplimiento crítico en RiderComplianceModule. Eliminado.

---

### 3.6 AcousticModule

| Atributo | Valor |
|---|---|
| Archivo | `dale_play_acoustic.py` |
| RESULT_KEY | `acoustic` |
| CRITICAL | `True` |
| TIMEOUT_S | 60 |
| DATA_SOURCE | Claude API claude-sonnet-4-6 + modelo geométrico Sabine-Eyring |
| MODES | full, post_show |

**Modelo primario:** Claude API (`claude-sonnet-4-6`) calcula SPL por sector, C-value ISO/FIFA y sightlines usando geometría real del estadio.

**Modelo fallback (sin ANTHROPIC_API_KEY):** Sabine-Eyring geométrico. SPL = Lw - 20·log10(d_eff) - 11 + contribución reverberante.

**Sectores del Amalfitani:**
| ID | Nombre | Distancia | Capacidad | Ángulo | Altura |
|---|---|---|---|---|---|
| campo_central | Campo Central | 80m | 25.000 | 0° | 0m |
| tribuna_norte | Tribuna Norte | 120m | 8.000 | 30° | 8m |
| tribuna_sur | Tribuna Sur | 140m | 8.000 | 330° | 8m |
| tribuna_este | Tribuna Este | 130m | 4.000 | 0° | 8m |
| platea_alta_norte | Platea Alta Norte | 150m | 5.000 | 25° | 15m |
| platea_alta_sur | Platea Alta Sur | 170m | 5.000 | 335° | 15m |

Tribuna Oeste = zona de escenario. No se analiza como sector receptor.

**Output esperado:**
```json
{
  "artista": "Airbag",
  "show_date": "2026-05-31",
  "sectores": [
    {"id": "campo_central", "spl_db": 97.1, "sightline": "optima", "cobertura": "buena", "dist_m": 80, "height_m": 0, "alertas": []}
  ],
  "spl_promedio_db": 95.2,
  "cobertura_optima_pct": 33.3,
  "rt60_s": 2.30,
  "modelo_acustico": "Sabine-Eyring geométrico",
  "fuente": "Modelo acústico Sabine-Eyring · Faro Protocol · Dale Play",
  "fallback_usado": true
}
```

**REQUIRED_FIELDS:** `["sectores", "spl_promedio_db", "rt60_s"]`

**Bugs corregidos en este módulo:**

**BUG #2 (modules.py):** `AcousticModule.run()` no enriquecía el rider con `artist`, `show_date` y `capacidad_estimada` antes de pasarlo a `analyze_acoustic_sightlines()`. Resultado: artista = "Artista" hardcodeado, show_date = "".
```python
# FIX en dale_play_modules.py — AcousticModule.run():
rider_enriched = {
    **rider,
    "artist":             result_ctx.get("artist", rider.get("artist", "Artista")),
    "show_date":          kwargs.get("show_date", rider.get("show_date", "")),
    "capacidad_estimada": rider.get("capacidad_estimada", 0),
}
```

**BUG #3:** `campo_central` (height=0, frontal directo al escenario) aparecía como "obstruida" porque el SPL ~97 dB era menor al threshold 99 dB de la lógica original. Un sector en el campo a 80m frente al escenario tiene la mejor visibilidad posible. Fix: `campo_central` siempre `"optima"`.

**BUG #4:** `cobertura_optima_pct` = 0% siempre porque el threshold era `spl >= 103 dB` (NIOSH). En el Amalfitani sin delay towers el SPL promedio es ~97 dB. Fix: umbral `>= 98 dB` (mínimo estándar de rider).

**BUG #5:** `cap_est = int(rider.get("capacidad_estimada", VENUE_CAP))` — si el rider no especifica capacidad estimada, defaulteaba a 49.540 (capacidad total) y disparaba la alerta "49.540 supera el 85% de 49.540". Fix: default 0, y la alerta solo se dispara si `cap_est > 0`.

---

### 3.7 EGMSModule

| Atributo | Valor |
|---|---|
| Archivo | `dale_play_egms.py` |
| RESULT_KEY | `egms` |
| CRITICAL | `False` |
| TIMEOUT_S | 15 |
| DATA_SOURCE | EGMS Copernicus 2015-2022 [ESTIMADO — datos históricos] |
| MODES | full, post_show |

**Fuente de datos:** EGMS L3 (European Ground Motion Service) Copernicus Land Monitoring Service. Datos de velocidad de deformación mm/año (Sentinel-1 C-band InSAR, 2015-2022) para el Área Metropolitana de Buenos Aires. **No hay API pública en tiempo real** — los datos están hardcodeados del producto QuickLook + literatura científica (Bejar-Pizarro et al. 2023; Liotta et al. 2022).

**SIEMPRE** retorna `fallback_usado=True` y `estimado=True`.

**Velocidades por sector (2015-2022):**
| Sector | Velocidad (mm/año) | Nivel |
|---|---|---|
| campo | -2.5 | atencion |
| tribuna_norte | -2.8 | atencion |
| tribuna_sur | -3.2 | atencion |
| tribuna_este | -2.1 | ok |
| tribuna_oeste | -3.8 | **critico** ← sector crítico |

Tribuna Oeste supera -3.5 mm/año → alerta siempre activa.

**Output esperado:**
```json
{
  "sectores": {...},
  "sector_critico": "tribuna_oeste",
  "vel_max_abs_mm_yr": 3.8,
  "resumen": {"sectores_criticos": 1, "sectores_atencion": 3, "sectores_ok": 1},
  "fuente": "EGMS Copernicus 2015-2022 [ESTIMADO — datos históricos]",
  "fallback_usado": true,
  "estimado": true
}
```

**REQUIRED_FIELDS:** `["sectores", "sector_critico", "vel_max_abs_mm_yr"]`

**Thresholds InSAR:**
```python
INSAR_OK_MM       = 1.0   # mm/año — ok
INSAR_CAUTION_MM  = 2.0   # mm/año — atencion
INSAR_CRITICAL_MM = 3.5   # mm/año — critico
```

---

### 3.8 SPLComplianceModule

| Atributo | Valor |
|---|---|
| Archivo | `dale_play_spl_compliance.py` |
| RESULT_KEY | `spl_compliance` |
| CRITICAL | `False` |
| TIMEOUT_S | 10 |
| DATA_SOURCE | Ordenanza GCBA 11.554/1994 + Res. 5613/2016 + OMS + NIOSH |
| MODES | full, post_show |

**Normativa aplicada:**
- Ordenanza GCBA 11.554/1994 — ruido en espacios públicos (Buenos Aires)
- Resolución GCBA 5613/2016 — eventos masivos (modificatoria)
- ISO 1996-1 — evaluación de ruido ambiente
- OMS 2018 — límite interior eventos: 100 dB(A)
- NIOSH 1998 — daño auditivo 4h: 103 dB(A)

**Límites prediales (Amalfitani — zona mixta Liniers):**
```
DIURNO  (8h-22h):  65 dB(A) en límite predial
NOCTURNO (22h-8h): 55 dB(A) en límite predial
Distancia predial estimada: 180m
Atenuación estructura estadio: -12 dB
```

**BUG #7 corregido:** `hora_inicio` defaulteaba a 21h y el threshold de nocturno era `>= 22`. Un show iniciando a 21h terminaba a las 23h+, pero el sistema usaba límites diurnos (65 dB). Fix: default `hora_inicio = 20`, nocturno si `hora_inicio >= 20`.

**Output esperado:**
```json
{
  "estado_global": "CONFORME",
  "periodo": "NOCTURNO (22h-8h)",
  "spl_max_interior_db": 97.1,
  "spl_predial_db": 51.3,
  "limite_exterior_dba": 55.0,
  "margen_exterior_db": 3.7,
  "excede_exterior": false,
  "sectores": [...],
  "limites_aplicados": {...},
  "fallback_usado": false,
  "estimado": true
}
```

**REQUIRED_FIELDS:** `["estado_global", "limites_aplicados", "sectores"]`

---

### 3.9 StructuralTwinModule

| Atributo | Valor |
|---|---|
| Archivo | `dale_play_structural.py` |
| RESULT_KEY | `structural` |
| CRITICAL | `False` |
| TIMEOUT_S | 15 |
| DATA_SOURCE | Open-Meteo viento + EGMS + Terzaghi + Faro Protocol |
| MODES | full, post_show |

**Normativa:** CIRSOC 102-2005 (cargas de viento Argentina) + IRAM 11603 (estructuras temporales).

**Constantes físicas (no modificar):**
```python
_CD_ESCENARIO = 1.3      # coeficiente arrastre — CIRSOC 102-2005 Tabla 8.1
_RHO_AIRE     = 1.225    # kg/m³ — densidad aire 15°C ISO 2533
_FS_MIN       = 2.5      # factor de seguridad mínimo — IRAM 11603
_STAGE_H_M    = 8.0      # altura referencia escenario
_STAGE_W_M    = 50.0     # ancho referencia escenario
```

**Factores integrados:**
1. **Viento:** F = Cd × q × A, q = 0.5 × ρ × v². Momento volcador vs estabilizador.
2. **EGMS:** Proyección 5 años de subsidencia acumulada.
3. **Suelo:** FS = capacidad_efectiva_kpa / presion_max_kpa (mínimo 3.0).

**FS global = mínimo de los tres.** Margen% = ((FS-1)/FS) × 100.

**Output esperado:**
```json
{
  "estado_global": "OK",
  "margen_seguridad_pct": 87.7,
  "vel_viento_kmh": 17.0,
  "factores": {
    "viento": {"fuerza_lateral_kn": 7.4, "factor_seguridad": 833.6, "estado": "OK"},
    "egms":   {"vel_max_mm_yr": 3.8, "acumulado_5yr_mm": 19.0, "estado": "ATENCIÓN"},
    "suelo":  {"capacidad_kpa": 98.0, "presion_max_kpa": 0.0, "factor_seguridad": 980.0, "estado": "OK"}
  },
  "restricciones": [],
  "normativa": "CIRSOC 102-2005 + IRAM 11603 + Faro Protocol v1.0"
}
```

**REQUIRED_FIELDS:** `["margen_seguridad_pct", "estado_global", "factores"]`

**Nota:** El FS de viento puede ser muy alto (>100) con vientos bajos de show-day. Esto es matemáticamente correcto. Para diseño estructural del escenario se debe usar velocidad de diseño CIRSOC (≥ 100 km/h para CABA), no la del pronóstico.

---

### 3.10 RiderComplianceModule

| Atributo | Valor |
|---|---|
| Archivo | `dale_play_rider_compliance.py` |
| RESULT_KEY | `rider_compliance` |
| CRITICAL | `False` |
| TIMEOUT_S | 10 |
| DATA_SOURCE | Rider técnico + datos acústicos/suelo/drenaje |
| MODES | full, post_show |

**Principio:** NUNCA genera un incumplimiento sin dato real para comparar. Si falta un módulo, esa categoría queda como `INDETERMINADO`.

**Checks realizados:**

1. **Acústico:** SPL real vs SPL mínimo por sector.
   ```python
   SPL_MINIMO_CAMPO_DB    = 98.0 dB
   SPL_MINIMO_TRIBUNA_DB  = 96.0 dB
   SPL_MINIMO_PLATEA_DB   = 94.0 dB
   ```

2. **Suelo:** zonas marcadas como `exclusion` o `precaucion` = incumplimiento crítico o advertencia.

3. **Drenaje:** `n_exclusiones > 0` en el resumen = incumplimiento crítico con las exclusiones listadas.

**Output esperado:**
```json
{
  "estado_global": "CONFORME",
  "incumplimientos": [],
  "indeterminados": [],
  "aprobaciones": [...],
  "n_criticos": 0,
  "n_advertencias": 0,
  "n_conformes": 5,
  "fallback_usado": false,
  "estimado": false
}
```

**REQUIRED_FIELDS:** `["estado_global", "incumplimientos", "aprobaciones"]`

---

## 4. REGLAS QUE NUNCA SE ROMPEN

1. **El sistema nunca miente.** Si falta un dato, dice "N/D" o "sin_datos". Nunca inventa un número. Nunca pone un 0 donde corresponde un null.

2. **El reporte PNG nunca hardcodea datos.** Todo lo que se muestra en el PNG viene del dict `result` del pipeline. Si el PNG muestra un dato que no está en el pipeline, es un bug.

3. **Los módulos críticos tienen circuit breaker.** Si `satellite`, `weather`, `soil` o `acoustic` fallan → pipeline para, sin PNG, con error explícito en el JSON. No hay reporte parcial de un show sin estos datos.

4. **Los smoke tests bloquean el PNG.** Cinco validaciones de sanidad corren antes del PNG. NDVI fuera de [-1, 1], SPL fuera de [60, 130], suelo ≤ 0 kPa, FII fuera de [0, 100], IROE incalculable → no se genera el PNG. Un reporte con datos absurdos es peor que ningún reporte.

5. **El audit_log es inmutable.** Solo INSERT, nunca UPDATE. Es el registro legal de cada auditoría. Si el pipeline corre, queda en Supabase. Si Supabase falla, queda en `models/storage/`.

6. **Cada módulo se ejecuta con timeout.** Ningún módulo puede bloquear el pipeline indefinidamente. Si supera `TIMEOUT_S`, retorna error de timeout.

7. **El fallback siempre es visible.** `fallback_usado=True` o `estimado=True` en el output → semáforo amarillo en el reporte. Nunca se muestra verde si el dato es estimado.

---

## 5. BUGS CORREGIDOS (2026-05-30)

Diagnóstico realizado via análisis completo del output del pipeline para `airbag_2026-05-31`. Los 8 bugs a continuación fueron identificados con causa raíz y corregidos en la misma sesión.

### Bug #1 — Satellite: clasificación NDVI sin estacionalidad
- **Archivo:** `dale_play_satellite.py` → `_classify_ndvi()`
- **Causa:** La función no tenía parámetro `month`. NDVI 0.099 en mayo → "rojo/Daño severo".
- **Realidad:** NDVI 0.08-0.25 en meses 4-8 (BsAs otoño/invierno) = dormancia Bermuda grass. Normal. No es daño.
- **Fix:** Agregar `month` parameter. Si `4 <= month <= 8 and 0.08 <= ndvi <= 0.25` → `"amarillo"/"Dormancia estacional"`.
- **Impacto:** Satellite pasó de ROJO a VERDE. FII subió de 28.2 a 43.

### Bug #2 — Modules: AcousticModule no enriquecía rider
- **Archivo:** `dale_play_modules.py` → `AcousticModule.run()`
- **Causa:** Pasaba `rider` directo a `analyze_acoustic_sightlines()`. El subdict `rider` del show config no tiene `artist` ni `show_date` (son campos del nivel superior del show config, no del rider).
- **Fix:** Crear `rider_enriched` con `artist` tomado de `result_ctx` (el show-level result dict), `show_date` de kwargs.
- **Impacto:** artista y fecha del show ahora aparecen correctamente en el reporte acústico.

### Bug #3 — Acoustic: campo_central marcado como "obstruida"
- **Archivo:** `dale_play_acoustic.py` → lógica de sightline
- **Causa:** Lógica `if spl >= 99: optima else: obstruida`. campo_central (80m frontal directo, height=0) da SPL ~97 dB → "obstruida". Absurdo físico.
- **Fix:** `campo_central` → siempre `"optima"`. Platea alta → `"atencion"`. Resto: `>= 95 dB` → `"optima"`, `>= 90` → `"buena"`, menor → `"obstruida"`.

### Bug #4 — Acoustic: cobertura_optima_pct siempre 0%
- **Archivo:** `dale_play_acoustic.py`
- **Causa:** `if spl >= 103: cobertura = "optima"`. En el Amalfitani sin delay towers el SPL promedio es ~97 dB. El threshold de 103 dB (NIOSH) nunca se alcanza en condiciones normales.
- **Fix:** Threshold `>= 98 dB` (mínimo estándar de rider Faro Protocol v1.0).

### Bug #5 — Acoustic: alerta capacidad compara número consigo mismo
- **Archivo:** `dale_play_acoustic.py`
- **Causa:** `cap_est = int(rider.get("capacidad_estimada", VENUE_CAP))`. Si el rider no especifica `capacidad_estimada`, toma 49.540 (total del estadio) y dispara la alerta "49.540 supera 85% de 49.540".
- **Fix:** Default `0`. Alerta solo si `cap_est > 0 and cap_est > VENUE_CAP * 0.85`.

### Bug #6 — Drainage: Consola FOH listada como exclusión
- **Archivo:** `dale_play_drainage.py` → lista `exclusiones`
- **Causa:** Tercer elemento de la lista era "Consola FOH recomendada en Área Norte — confirmado, sin restricciones". Es una **aprobación**. RiderComplianceModule lee `exclusiones` y la propagaba como incumplimiento crítico.
- **Fix:** Eliminar ese elemento de la lista.

### Bug #7 — SPL Compliance: período incorrecto para shows nocturnos
- **Archivo:** `dale_play_spl_compliance.py`
- **Causa:** `hora_inicio = rider.get("hora_inicio", 21)` + `es_nocturno = int(hora_inicio) >= 22`. Un show iniciando a 21h (pero que termina a las 23h+) usaba límites DIURNOS (65 dB) en vez de NOCTURNOS (55 dB).
- **Fix:** Default `hora_inicio = 20`. `es_nocturno = int(hora_inicio) >= 20`. Shows de 20h+ siempre aplican límite nocturno (conservador, correcto).

### Bug #8 — FII: NDVI score = 0 en dormancia → FII "RIESGO OPERATIVO"
- **Archivo:** `dale_play_fii.py` → `compute_fii()`
- **Causa:** NDVI 0.099 < 0.10 → `ndvi_score = 0.0` ("Crítico") → FII = 28.2 "RIESGO OPERATIVO". El campo estaba dormante, no dañado.
- **Fix:** Agregar rama `_dormancia` antes de los thresholds normales. NDVI 0.08-0.25 en meses 4-8 → `ndvi_score = 35.0 + (ndvi - 0.08) / 0.17 * 15.0` (rango 35-50). FII pasó a 43 "REQUIERE INTERVENCIÓN".

---

## 6. FII — ÍNDICE FARO DE INTEGRIDAD

**Definición:** FII = 40% NDVI + 35% EGMS + 25% Layout. Escala 0-100.

```
FII >= 65 → "CERTIFICADO FARO PROTOCOL" (semáforo verde)
40 <= FII < 65 → "REQUIERE INTERVENCIÓN" (semáforo atencion)
FII < 40 → "RIESGO OPERATIVO" (semáforo critico)
```

**Componente NDVI (40%):**
```
NDVI >= 0.45       → score 100 (Excelente)
NDVI 0.30-0.45     → score 70-100 (Bueno)
NDVI 0.20-0.30     → score 40-70 (Regular)
NDVI 0.10-0.20     → score 10-40 (Malo)
NDVI < 0.10        → score 0 (Crítico)
NDVI 0.08-0.25 en meses 4-8 → score 35-50 (Dormancia estacional) ← IMPORTANTE
```

**Componente EGMS (35%):**
```
Base 100. -25 por sector crítico (>3.5 mm/año). -10 por sector atención (2.5-3.5).
Sin datos → 50 (neutral).
```

**Componente Layout (25%):**
```
Sin layout → 50 (neutral). -40 por colisión con zona ROJA. -15 por zona AMARILLA.
```

---

## 7. SMOKE TESTS

Cinco validaciones que bloquean el PNG si fallan:

| Test | Validación |
|---|---|
| NDVI | `-1.0 <= ndvi <= 1.0` (None = OK) |
| SPL | `60.0 <= spl_db <= 130.0` por sector (None = OK) |
| SUELO kPa | `capacidad_efectiva_kpa > 0` (None = OK) |
| FII | `0.0 <= fii <= 100.0` (None = OK) |
| IROE | Acoustic o soil disponibles (al menos uno) |

Si cualquier smoke test falla: `result["_smoke_errors"]` recibe la lista de errores, `report_png = None`, el pipeline retorna sin generar imagen.

---

## 8. FLUJO COMPLETO DE UN SHOW

### Pre-show (D-7 a D-1)

1. **Dale Play crea el show config** en `dale-play/shows/{show_id}.json`:
   ```json
   {
     "show_id": "artista_2026-MM-DD",
     "artist": "Nombre del artista",
     "show_date": "2026-MM-DD",
     "venue": "Estadio José Amalfitani",
     "promotor": "Dale Play",
     "capacidad_estimada": 35000,
     "rider": {
       "stage": {"lw_db": 134, "throws": ["main", "delay", "front_fill"], ...},
       "estructuras": [...],
       "produccion": {...}
     }
   }
   ```

2. **(Opcional) Dale Play sube el plano de producción** via `POST /dale-play/upload-layout` (PDF o DXF). El sistema parsea las coordenadas de estructuras con pdfplumber/ezdxf + Claude Vision.

3. **Trigger de auditoría** via `POST /dale-play/run` con `{"show_id": "...", "mode": "full"}`. El pipeline corre en background (~15-60s).

4. **El pipeline genera:**
   - `models/run_log_{show_id}.json` — log de ejecución
   - `reportes/reporte_{show_id}.png` — reporte visual
   - Push a GitHub repo `protocolfaro/faroprotocol/dale-play/reportes/`
   - Audit log en Supabase

5. **Dale Play revisa el reporte** en `GET /dale-play/report-png/{show_id}` o via GitHub Pages.

6. **Si hay incumplimientos críticos**, Dale Play recibe las correcciones requeridas antes de la producción.

### Show day (D-1)

7. Correr `GET /dale-play/weather?show_date=YYYY-MM-DD` para pronóstico actualizado.

### Post-show (D+3 a D+7)

8. **Certificación** via `POST /dale-play/certify` con `{"show_id": "...", "mode": "post_show"}`. El sistema:
   - Descarga NDVI post-show (Sentinel-2)
   - Calcula delta NDVI pre/post
   - Clasifica nivel de daño al césped
   - Genera PDF certificado con hash SHA-256
   - Guarda en Supabase tabla `certifications`

9. **Verificación del certificado** por terceros via `GET /dale-play/verify/{cert_hash}`.

---

## 9. VARIABLES DE ENTORNO REQUERIDAS

Configurar en Railway → Variables de entorno del servicio.

| Variable | Requerida | Módulo | Descripción |
|---|---|---|---|
| `ANTHROPIC_API_KEY` | Para modelo primario acústico | AcousticModule, Certification | Claude API. Sin ella, fallback a Sabine-Eyring. |
| `GITHUB_TOKEN` | Para push PNG e histórico | dale_play_github.py | Token con permisos `repo` en `protocolfaro/faroprotocol`. |
| `SUPABASE_URL` | Para persistencia | dale_play_storage.py | URL del proyecto Supabase. Ej: `https://xxx.supabase.co` |
| `SUPABASE_KEY` | Para persistencia | dale_play_storage.py | `anon` o `service_role` key del proyecto. |
| `NASA_EARTHDATA_USER` | Para InSAR (post_show) | dale_play_insar.py | Cuenta NASA Earthdata. Gratis en urs.earthdata.nasa.gov |
| `NASA_EARTHDATA_PASS` | Para InSAR (post_show) | dale_play_insar.py | Password NASA Earthdata. |
| `COPERNICUS_USER` | Para EGMS real-time (futuro) | dale_play_egms.py | Cuenta Copernicus Data Space. |
| `COPERNICUS_PASS` | Para EGMS real-time (futuro) | dale_play_egms.py | Password Copernicus Data Space. |

**Sin `ANTHROPIC_API_KEY`:** El módulo acústico usa Sabine-Eyring geométrico (fallback). El reporte PNG se genera igualmente, con semáforo amarillo en acoustic.

**Sin `GITHUB_TOKEN`:** El PNG se genera localmente pero no se pushea. `report_png_url = None`.

**Sin `SUPABASE_URL`/`SUPABASE_KEY`:** Los datos se guardan en `models/storage/` local. No hay pérdida de datos en Railway (el directorio persiste en disco Railway siempre que el volumen esté configurado).

---

## 10. ENDPOINTS DISPONIBLES

Base URL Railway: `https://[tu-servicio].railway.app`

### GET /dale-play/health
Estado del servicio y conectividad.

**Response:**
```json
{
  "service": "dale-play", "status": "ok",
  "github_token": true,
  "insar_configured": false,
  "supabase": {"configured": true, "connected": true, "error": null}
}
```

---

### GET /dale-play/shows
Lista los show configs disponibles.

**Response:** `{"shows": ["airbag_2026-05-31", ...]}`

---

### GET /dale-play/weather?show_date=YYYY-MM-DD
Pronóstico climático rápido sin correr el pipeline completo.

**Params:** `show_date` (opcional, default mañana)

**Response:** Output de `WeatherModule` (ver sección 3.2).

---

### GET /dale-play/report?show_id=...&mode=full
Corre el pipeline completo y retorna el JSON de todos los módulos. **Síncrono** — puede tardar hasta 60s.

**Params:**
- `show_id` (requerido) — ej: `airbag_2026-05-31`
- `mode` (opcional) — `full` | `weather_only` | `post_show`

**Response:** Dict completo del pipeline (todos los módulos + PNG path + FII + run_log).

---

### POST /dale-play/run
Lanza el pipeline en **background** y retorna inmediatamente.

**Body:** `{"show_id": "airbag_2026-05-31", "mode": "full"}`

**Response:** `{"status": "started", "show_id": "...", "mode": "full", "message": "..."}`

---

### POST /dale-play/upload-layout
Sube un plano de producción (PDF o DXF/DWG) para parseo automático.

**Form (multipart):**
- `show_id`: string
- `file`: PDF o DXF/DWG

**Response:** Layout dict con estructuras parseadas (escenario, torres, consola, etc.).

---

### GET /dale-play/layout/{show_id}
Retorna el layout JSON guardado si existe.

**Response:** `{"escenario": {...}, "torres_lr": [...], ...}` o 404.

---

### POST /dale-play/certify
Genera certificado post-evento (solo modo `post_show`).

**Body:** `{"show_id": "airbag_2026-05-31", "mode": "post_show"}`

**Response:**
```json
{
  "show_id": "airbag_2026-05-31",
  "cert_hash": "SHA256...",
  "pdf_path": "certificados/airbag_2026-05-31_certificado.pdf",
  "ndvi_pre": 0.099,
  "ndvi_post": 0.092,
  "nivel_dano": "leve"
}
```

---

### GET /dale-play/certificado/{show_id}
Descarga el PDF del certificado.

**Response:** PDF file (`application/pdf`).

---

### GET /dale-play/report-png/{show_id}
Sirve el PNG del reporte generado.

**Response:** PNG image (`image/png`) o 404.

---

### GET /dale-play/verify/{cert_hash}
Verifica un certificado por hash SHA-256 (para terceros).

**Response:**
```json
{
  "valido": true,
  "show_id": "airbag_2026-05-31",
  "fecha": "2026-06-05",
  "ndvi_pre": 0.099,
  "ndvi_post": 0.092,
  "delta_ndvi": -0.007,
  "nivel_dano": "leve",
  "cert_hash": "SHA256...",
  "interpretacion": "Daño menor compatible con tráfico normal de producción"
}
```

---

### GET /dale-play/dashboard/{show_id}
Panel de validación interactivo HTML.

**Response:** HTML template (`dale-play/templates/dale_play_dashboard.html`).

---

## 11. CÓMO AGREGAR UN MÓDULO NUEVO

**Seguir exactamente estos pasos, en este orden:**

### Paso 1: Crear el archivo del módulo
Crear `dale-play/dale_play_{nombre}.py` con la función principal:
```python
def analyze_{nombre}(rider: dict, **kwargs) -> dict:
    """Retorna dict con datos o {"error": str, "fuente": str, "fallback_usado": bool}."""
    ...
```

### Paso 2: Crear la clase en dale_play_modules.py
```python
class {Nombre}Module(DalePlayModule):
    RESULT_KEY      = "{nombre}"
    REQUIRED_FIELDS = ["campo1", "campo2"]
    MODES           = ("full", "post_show")
    CRITICAL        = False  # True solo si sin este dato el show no puede continuar
    TIMEOUT_S       = 30
    DATA_SOURCE     = "Fuente de datos real (URL o nombre del servicio)"

    def run(self, rider: dict, **kwargs) -> dict:
        try:
            from dale_play_{nombre} import analyze_{nombre}
            result_ctx = kwargs.get("result") or {}
            out = analyze_{nombre}(rider, **kwargs)
            out.setdefault("fuente", self.DATA_SOURCE)
            out.setdefault("fallback_usado", False)
            return out
        except Exception as exc:
            return {"error": str(exc), "fuente": self.DATA_SOURCE, "fallback_usado": False}
```

### Paso 3: Registrar en MODULES (al final de dale_play_modules.py)
```python
MODULES: list[DalePlayModule] = [
    ...
    {Nombre}Module(),   # ← agregar aquí, en el orden de ejecución deseado
]
```

### Paso 4: Agregar la sección al reporte PNG (dale_play_report.py)
- Expandir `height_ratios` en el GridSpec (agregar una fila con el height apropiado)
- Incrementar el `figsize` en proporción
- Añadir `ax = fig.add_subplot(gs[N])` en la posición correcta
- La sección siempre empieza con `_ax_base()`, `_section_title()`, `_confianza_tag()`
- Leer datos via `result.get("{nombre}") or {}`

### Paso 5: Agregar al certificado PDF (dale_play_certification.py)
Agregar la sección correspondiente en la función que genera el contenido del PDF.

### Paso 6: Agregar al MANUAL (este archivo)
Documentar: archivo, RESULT_KEY, CRITICAL, TIMEOUT_S, DATA_SOURCE, fuente de datos, credenciales necesarias, output esperado, REQUIRED_FIELDS, bugs conocidos.

---

## 12. CÓMO AGREGAR UN VENUE NUEVO

Para auditar un estadio diferente al Amalfitani:

### Paso 1: Crear dale_play_config_{venue}.py
Duplicar `dale_play_config.py` y ajustar:
```python
VENUE_LAT  = -34.XXXX
VENUE_LON  = -58.XXXX
VENUE_NAME = "Nombre del Estadio"
VENUE_ADDR = "Dirección"
VENUE_CAP  = 50000
VENUE_BBOX = (...)
```

### Paso 2: Actualizar dale_play_config.py
Si el sistema solo maneja un venue a la vez, reemplazar las constantes. Si maneja múltiples venues, agregar un mecanismo de selección por `venue_id` en el show config.

### Paso 3: Levantar el modelo 3D del venue
Crear sectores en `dale_play_acoustic.py` (`_SECTORES`) con distancias, ángulos y alturas reales del nuevo estadio.

### Paso 4: Levantar datos EGMS del venue
Obtener velocidades Sentinel-1 InSAR del área del nuevo estadio (Copernicus Data Space o literatura) y actualizar `dale_play_egms.py`.

### Paso 5: Levantar infraestructura de drenaje
Obtener planos de drenaje del nuevo estadio y actualizar zonas en `dale_play_drainage.py`.

### Paso 6: Verificar normativa local
Confirmar los límites de ruido locales (equivalentes a Ordenanza GCBA 11.554 para BsAs) y actualizar `dale_play_spl_compliance.py`.

### Paso 7: Agregar show config de prueba
`shows/{venue}_{fecha}.json` y correr `run_test.py` adaptado.

---

## 13. QUÉ HACER SI ALGO FALLA

### El pipeline para con `_pipeline_error`
Un módulo `CRITICAL=True` falló. El JSON tiene `"_pipeline_error": "Módulo crítico 'X' falló: ..."`.

**Acción:** Revisar el log del módulo fallado. Verificar credenciales (ANTHROPIC_API_KEY para acoustic, acceso a internet para satellite/weather/soil). Si el módulo real no está disponible, considerar si aplica un fallback manual.

### Smoke tests fallan (`_smoke_errors`)
El pipeline corrió pero los datos no pasan validación básica.

**Acción:** Revisar los errores específicos. Si es NDVI fuera de rango, verificar la imagen Sentinel-2 (nube total puede dar valores espurios). Si es SPL fuera de rango, verificar el `lw_db` en el rider.

### PNG no se genera pero pipeline no falló
Revisar `result.get("report_png")`. Si es `None` con `_smoke_errors` presente → smoke tests bloquearon. Si es `None` sin smoke errors → excepción en `dale_play_report.py`. Revisar logs de Railway.

### Módulo retorna `estimado: true`
Normal. El módulo usó un fallback (datos locales, modelo simplificado). El reporte lo muestra con semáforo amarillo. No bloquea el pipeline ni el PNG.

### Supabase no disponible
El sistema fallará silenciosamente en la escritura a Supabase y usará el fallback local (`models/storage/`). Los datos no se pierden mientras el volumen de Railway esté persistente. Verificar `SUPABASE_URL` y `SUPABASE_KEY` en las variables de entorno de Railway.

### GitHub push falla
El PNG se genera localmente pero no se sube a GitHub. `report_png_url = None`. El PNG está disponible en `GET /dale-play/report-png/{show_id}` directamente desde Railway. Verificar `GITHUB_TOKEN`.

### Claude API no disponible (acoustic fallback)
El módulo acústico cae al modelo Sabine-Eyring geométrico. El reporte se genera con `fallback_usado=True` y semáforo amarillo. La calidad del análisis acústico es menor (sin C-value ISO, sin sightlines angulares precisas), pero los datos son físicamente correctos.

---

## 14. ESTRUCTURA DE DIRECTORIOS

```
railway-backend/
├── app.py                          ← Flask app principal (registra dale_play_bp)
├── dale_play_routes.py             ← Blueprint /dale-play/* (endpoints)
└── dale-play/
    ├── MANUAL.md                   ← ESTE ARCHIVO
    ├── dale_play_module.py         ← ABC base de todos los módulos
    ├── dale_play_modules.py        ← Registro MODULES + MODULES_BY_KEY
    ├── dale_play_pipeline.py       ← Orquestador principal
    ├── dale_play_config.py         ← Constantes del venue
    ├── dale_play_report.py         ← Generador PNG (matplotlib)
    ├── dale_play_storage.py        ← Persistencia Supabase + local
    ├── dale_play_github.py         ← Push PNG e histórico a GitHub
    ├── dale_play_smoke.py          ← 5 smoke tests pre-PNG
    ├── dale_play_fii.py            ← FII (Índice Faro de Integridad)
    ├── dale_play_certification.py  ← Certificado PDF post-show
    ├── dale_play_vision.py         ← Comparativa layout (Claude Vision)
    ├── dale_play_layout.py         ← Parser de planos (PDF/DXF)
    ├── dale_play_insar.py          ← InSAR post-show (NASA Earthdata)
    │
    ├── dale_play_satellite.py      ← Módulo: Sentinel-2 NDVI + Landsat TIRS
    ├── dale_play_weather.py        ← Módulo: Open-Meteo pronóstico 72h
    ├── dale_play_soil.py           ← Módulo: SoilGrids + Terzaghi
    ├── dale_play_drainage.py       ← Módulo: Ksat + drenaje hidráulico
    ├── dale_play_acoustic.py       ← Módulo: Claude API + Sabine-Eyring
    ├── dale_play_egms.py           ← Módulo: EGMS Copernicus 2015-2022
    ├── dale_play_spl_compliance.py ← Módulo: Ordenanza GCBA 11.554
    ├── dale_play_structural.py     ← Módulo: Digital twin estructural
    ├── dale_play_rider_compliance.py ← Módulo: Compliance rider técnico
    │
    ├── shows/                      ← Show configs por show_id
    │   └── airbag_2026-05-31.json
    ├── reportes/                   ← PNGs generados
    │   └── reporte_airbag_2026-05-31.png
    ├── certificados/               ← PDFs de certificación
    ├── models/                     ← JSONs de outputs de módulos
    │   ├── run_log_{show_id}.json
    │   ├── acoustic_real_{show_id}.json
    │   ├── egms_amalfitani.json
    │   ├── drainage_amalfitani.json
    │   └── storage/                ← Fallback local de Supabase
    ├── cache/                      ← Cache pre-Supabase
    └── templates/
        └── dale_play_dashboard.html
```

---

## 15. ESTADO ACTUAL DEL SISTEMA

**Versión:** 1.0  
**Fecha:** 2026-05-30  
**Venue activo:** Estadio José Amalfitani (único)  

### Módulos activos (10)

| # | RESULT_KEY | CRITICAL | Fuente de datos | Confianza típica |
|---|---|---|---|---|
| 1 | satellite | Sí | HLS / S2 / Landsat / MODIS (Planetary Computer + NASA CMR) | Verde (modo full: S2; post_show: HLS) |
| 2 | weather | Sí | Open-Meteo API | Verde |
| 3 | soil_real | No | SoilGrids REST v2 | Amarillo (SoilGrids responde errático) |
| 4 | soil | Sí | SoilGrids + Terzaghi | Amarillo |
| 5 | drainage | No | SoilGrids Ksat + infra 2015 | Verde |
| 6 | acoustic | Sí | Claude API / Sabine-Eyring fallback | Amarillo (sin ANTHROPIC_API_KEY en local) |
| 7 | egms | No | EGMS Copernicus 2015-2022 [hardcoded] | Amarillo (siempre estimado) |
| 8 | spl_compliance | No | Ord. GCBA 11.554 + OMS + NIOSH | Amarillo (SPL exterior es estimación) |
| 9 | structural | No | Open-Meteo + EGMS + Terzaghi | Amarillo |
| 10 | rider_compliance | No | Rider + módulos acústico/suelo/drenaje | Verde |

### Funciones satelitales post_show (nuevo — 2026-05-31)

| Función | Archivo | Qué hace | Requiere |
|---|---|---|---|
| `_search_hls()` | `dale_play_satellite.py` | Busca HLS en PC STAC (30m, ~1.4d) | Nada |
| `_ndvi_from_hls_item()` | `dale_play_satellite.py` | NDVI desde HLS L30/S30 | rasterio |
| `_check_modis_alert()` | `dale_play_satellite.py` | Cuenta granules MODIS post-show | Nada |
| `_apply_sen2sr()` | `dale_play_satellite.py` | Super-resolución 10m→2.5m | pip install sen2sr |
| `fetch_sar_compaction()` | `dale_play_insar.py` | Delta VV Sentinel-1 pre/post show | rasterio, PC STAC |

### Módulos pendientes / futuros

| Módulo | Descripción | Bloqueado por |
|---|---|---|
| EGMS real-time | Datos InSAR actualizados (no hardcodeados) | Credenciales Copernicus Data Space + API compleja |
| InSAR post-show HyP3 | Detección de deformación post-evento | NASA Earthdata (`dale_play_insar.py` existe) |
| MODIS NDVI real | Descarga píxeles MODIS MOD09GQ | NASA Earthdata credentials |
| Crowd density | Densidad de público real-time via cámara | Hardware de cámaras en Amalfitani |
| Multi-venue | Soporte para más de un estadio | Refactoring config por venue |

### Último show auditado
- **Show:** Airbag · 2026-05-31 · Estadio José Amalfitani
- **FII:** 43/100 "REQUIERE INTERVENCIÓN" (campo en dormancia estacional Bermuda, invierno)
- **Pipeline:** 14.7s · 0 módulos fallidos · 0 smoke errors
- **PNG:** `reportes/reporte_airbag_2026-05-31.png`
- **Confianza general:** amarillo (EGMS estimado + suelo fallback + acoustic sin Claude API en local)

### Próximo paso comercial
One-pager para Nelson (contacto en Dale Play) mostrando el reporte Airbag como caso piloto.

---

## 16. SUPABASE — ESQUEMA DE TABLAS REQUERIDO

Si Supabase está vacío o se recrea, ejecutar en el SQL Editor:

```sql
-- Baselines satelitales pre-show
CREATE TABLE IF NOT EXISTS show_baselines (
  show_id    TEXT PRIMARY KEY,
  ndvi       REAL,
  date       TEXT,
  satellite  JSONB,
  created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Certificaciones post-show
CREATE TABLE IF NOT EXISTS certifications (
  show_id    TEXT PRIMARY KEY,
  cert_hash  TEXT UNIQUE,
  pdf_path   TEXT,
  ndvi_pre   REAL,
  ndvi_post  REAL,
  delta_ndvi REAL,
  nivel_dano TEXT,
  data       JSONB,
  created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Audit log inmutable (solo INSERT, nunca UPDATE)
CREATE TABLE IF NOT EXISTS audit_log (
  id                 BIGSERIAL PRIMARY KEY,
  show_id            TEXT        NOT NULL,
  timestamp          TIMESTAMPTZ DEFAULT NOW(),
  modulos_ejecutados JSONB,
  modulos_fallidos   JSONB,
  fuentes_usadas     JSONB,
  confianza_general  TEXT,
  smoke_tests        JSONB
);
```

---

## 17. GITHUB — ESTRUCTURA DEL REPO

Repo: `protocolfaro/faroprotocol` · Branch: `main`

```
dale-play/
├── shows/         ← Show configs (push automático)
└── reportes/      ← PNGs del reporte (push automático post-pipeline)
```

El token `GITHUB_TOKEN` necesita permiso `repo` (lectura y escritura) sobre `protocolfaro/faroprotocol`.

---

*Fin del manual. Cualquier modificación al sistema debe reflejarse aquí antes de hacer commit.*

## Fuentes nuevas detectadas automáticamente

<!-- append:2026-05-31 v1.1. -->
#### Open-Elevation SRTM 30m — DEM gratuito · detectada 2026-05-31
- **Módulo mejorado:** `structural`
- **Descripción:** Elevación real del venue para corrección de presión atmosférica y cálculo de velocidad de viento por cota.
- **Resolución:** 30m  ·  **Frecuencia:** None días
- **Credenciales:** No requiere
- **Mejora:** Disponible y mejor que pipeline actual

<!-- append:2026-05-31 v1.1. -->
#### GSMaP JAXA — Precipitación horaria 0.1° (~11km) · detectada 2026-05-31
- **Módulo mejorado:** `weather`
- **Descripción:** Precipitación horaria para alertas en tiempo real el día del show. Complementa Open-Meteo (pronóstico) con observación real.
- **Resolución:** 11000m  ·  **Frecuencia:** 0.04 días
- **Credenciales:** No requiere
- **Mejora:** Disponible y mejor que pipeline actual

<!-- append:2026-05-31 v1.1. -->
#### Open-Elevation SRTM 30m — DEM gratuito · detectada 2026-05-31
- **Módulo mejorado:** `structural`
- **Descripción:** Elevación real del venue para corrección de presión atmosférica y cálculo de velocidad de viento por cota.
- **Resolución:** 30m  ·  **Frecuencia:** None días
- **Credenciales:** No requiere
- **Mejora:** Disponible y mejor que pipeline actual

<!-- append:2026-05-31 v1.1. -->
#### GSMaP JAXA — Precipitación horaria 0.1° (~11km) · detectada 2026-05-31
- **Módulo mejorado:** `weather`
- **Descripción:** Precipitación horaria para alertas en tiempo real el día del show. Complementa Open-Meteo (pronóstico) con observación real.
- **Resolución:** 11000m  ·  **Frecuencia:** 0.04 días
- **Credenciales:** No requiere
- **Mejora:** Disponible y mejor que pipeline actual

---
