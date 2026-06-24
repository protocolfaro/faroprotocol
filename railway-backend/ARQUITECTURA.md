# Reglas Arquitectónicas — Faro Protocol Backend

## Regla 1: Assembler como única fuente de datos

**Ningún renderer, ningún `_body_*()`, ningún gen script importa `hermes`,
`velez_supabase`, `faro_ecostress` ni ninguna fuente de datos directamente.**

Todo dato pasa por `faro_assembler.assemble_report()`.  
Esta regla no tiene excepciones.

### Flujo de datos obligatorio

```
velez_data.json (estático)
velez_supabase (overlay)        →  faro_assembler.assemble_report()  →  VelezReport (dict)
hermes_consolidate (ET₀/humedad)                                           │
soil_metrics / vegetation_metrics (científico)                             ▼
                                                                   gen scripts (FARO_VD_PATH)
                                                                   _body_*() renderers
```

### Por qué existe esta regla

Antes del assembler cada módulo nuevo requería tocar los renderers.
`_body_roger()` creció a 274 líneas con imports inline de Hermes.
Los gen scripts originales fueron reemplazados por `render_reports.py` genérico.
Resultado: layouts rotos, datos pisados, reportes inconsistentes.

### Cómo agregar un módulo nuevo

1. Calcular y escribir el dato en su tabla Supabase (`soil_metrics`, `vegetation_metrics`, `climate_metrics`).
2. Agregar el campo como `Optional` en `faro_schema.py` (contrato canónico).
3. Leer el campo en `faro_assembler.py` dentro del paso correspondiente.
4. Los renderers y gen scripts lo leen desde el `VelezReport` ensamblado — sin tocar imports.

### Archivos de contrato

| Archivo | Rol |
|---|---|
| `sports/clients/velez/faro_schema.py` | TypedDict canónico — único lugar de definición de campos |
| `sports/clients/velez/faro_assembler.py` | Única función que lee fuentes de datos |
| `sports/clients/velez/velez_scheduler.py` | `_get_velez_data()` delega a `assemble_report()` |

## Regla 2: Un HTML por cliente, sin subpáginas

Cada cliente tiene exactamente un archivo HTML. No se crean subpáginas ni rutas adicionales.
`admin-roger` no existe — es un error arquitectónico.

## Regla 3: Gen scripts intocables

La estructura visual de los gen scripts (`gen_velez_*.py`) es fija.
Solo cambian datos vía `FARO_VD_PATH`. Nunca simplificar ni reemplazar con layouts genéricos.

## Regla 4: Sin constantes hardcodeadas de datos de campo

**Ningún gen script puede contener constantes hardcodeadas de datos de campo.**
Esto incluye temperaturas, índices espectrales, KPIs acuáticos, valores InSAR, conteos de paneles,
o cualquier número que represente estado físico del predio.

Todo dato viene del assembler (`assemble_report()` → `FARO_VD_PATH`).
Si no hay dato real → el campo es `None`/`null` → el renderer muestra `'SIN DATO'`.

Nunca un número inventado.

---

## Pipeline Satelital — 4 Tramos (Vélez)

El pipeline satelital produce NDVI, NDRE, índices de suelo e InSAR para Amalfitani
y Villa Olímpica. Se ejecuta en Railway (cron diario 09:00 UTC, InSAR semanal).

### Cascade de imágenes ópticas (`ndvi_real.fetch_ndvi()`)

La cascade intenta fuentes en orden de frescura/costo. Retorna al primer éxito.

```
Ronda 0   S2 + Landsat  cloud≤25%  ventana  7d   imagen única
    ↓
Tramo A   Mini-BAP 5d   cloud≤80%  ventana  5d   composite multi-sensor
          S2 override Landsat por resolución (10m > 30m)
    ↓
Ronda 2   S2 + Landsat  cloud≤45%  ventana 14d   imagen única
Ronda 3   S2 + Landsat  cloud≤65%  ventana 30d   imagen única
Ronda 4   S2 + Landsat  cloud≤80%  ventana 45d   imagen única (invierno)
    ↓
BAP 30d   S2            cloud≤95%  ventana 30d   composite 8 escenas
    ↓
Tramo B   OpenEO CDSE   cloud SCL  ventana 45d   composite server-side CDSE
          Requiere: CDSE_USER + CDSE_PASS (Railway)
    ↓
Kalman    gap-fill temporal — proyecta desde últimos valores conocidos
    ↓
Tramo D   CloudBreaker  SAR→S2 fusion via HF Inference API
          Fallback: proxy Van Genuchten (SAR θ → NDVI estimado)
          Venues configurables: CB_VENUES env var (default: amalfitani,villa_olimpica)
```

### Tramo A — Mini-BAP 5d (`ndvi_real._composite_multisource`)

- Activa cuando ninguna escena individual pasa cloud≤25% en los últimos 7 días
- Descarga Landsat 8/9 (30m) y Sentinel-2 (10m) de los últimos 5 días
- `_composite_multisource()`: merge por orden Landsat→S2 (S2 sobreescribe)
- `metodo_generacion`: `COMPOSITE_MINI_BAP_5D`
- Frescura efectiva: ≤5d (era 8-14d antes)

### Tramo B — OpenEO CDSE (`ndvi_openeo.fetch_ndvi_openeo`)

- Composite server-side: SCL mask en CDSE, nunca descarga píxeles nublados
- Colección: `SENTINEL2_L2A`, reducción temporal por mediana
- Auth: `CDSE_USER` + `CDSE_PASS` (fallback: `COPERNICUS_USER` + `COPERNICUS_PASS`)
- Latencia: 2-3h desde el último pase S2 (batch job CDSE)
- Archivo: `sports/clients/velez/ndvi_openeo.py`

### Tramo C — HyP3 InSAR async (`insar_hyp3.fetch_insar`)

- D-InSAR semanal: par Sentinel-1 de 12 días sobre el complejo Vélez
- **Two-phase async** — no bloquea el proceso Railway:
  - Fase 1: busca par S1 en ASF → submite job → guarda estado en `/tmp/faro_pending_hyp3.json`
  - Fase 2 (ciclo siguiente, ~30-60 min): verifica estado → descarga si SUCCEEDED
- Auth: `NASA_EARTHDATA_USER` + `NASA_EARTHDATA_PASS` (Railway)
- Output: desplazamiento LOS→vertical por sector + backscatter → `soil_metrics` Supabase
- Sectores: `estadio` (amalfitani), `poli_basquet` + `poli_playon_norte` (villa_olimpica)
- Archivo: `sports/clients/velez/insar_hyp3.py`

### Tramo D — CloudBreaker SAR fusion (`faro_cloudbreaker_hf.reconstruct`)

- SAR→NDVI cuando no hay imagen óptica disponible (nubosidad persistente)
- Fuente SAR: último `soil_metrics` con `sar_vv_db` / `sar_vh_db` de Supabase
- Intento 1: HF Inference API (modelo `ibm-nasa-geospatial/Prithvi-EO-2.0-300M`)
- Intento 2: HF Space REST endpoint (`HF_CLOUDBREAKER_SPACE` env var)
- Intento 3: proxy Van Genuchten — NDVI ≈ 0.35 + 0.80·(θ − 0.10)
- `metodo_generacion`: `CLOUDBREAKER_SAR_FUSION`
- Venues: controlados por `CB_VENUES` env var (default: `amalfitani,villa_olimpica`)
- Archivo: `sports/clients/velez/faro_cloudbreaker_hf.py`

### Mapeo venue_id por sector/fuente

| Fuente | Cancha/sector | venue_id |
|--------|--------------|----------|
| ndvi_real (canchas FA) | 1fa…10fa | `villa_olimpica` |
| ndvi_real (canchas FP) | 1fp, 2fp | `villa_olimpica` |
| ndvi_real (Amalfitani) | amalfitani | `amalfitani` |
| insar_hyp3 | estadio | `amalfitani` |
| insar_hyp3 | poli_basquet, poli_playon_norte | `villa_olimpica` |
| insar_hyp3 | sede_anexo_norte, piletas | — (skip, no grass) |
| climate_metrics | — | `amalfitani` + `villa_olimpica` (ambos) |

### Variables de entorno requeridas

| Var | Tramo | Estado |
|-----|-------|--------|
| `CDSE_USER` + `CDSE_PASS` | B (OpenEO) | ✅ en Railway |
| `NASA_EARTHDATA_USER` + `NASA_EARTHDATA_PASS` | C (HyP3) | ✅ en Railway |
| `HF_API_TOKEN` | D (CloudBreaker HF) | opcional — free tier sin token |
| `CB_VENUES` | D (CloudBreaker venues) | opcional — default amalfitani,villa_olimpica |
| `HF_CLOUDBREAKER_SPACE` | D (CloudBreaker Space) | opcional — alternativa al modelo |
