# SOURCE_LOG — Faro Protocol

Registro de auditoría automático. Actualizado en cada ejecución del pipeline y job semanal.

| Timestamp (UTC) | Módulo | Fuente Usada | Fuente Intentadas | Motivo Fallback | Confianza | Venue Cubierto |
|---|---|---|---|---|---|---|
| 2026-06-02 20:00 UTC | **INIT** | `dale_play_sar.py` | UMBRA_X → S1_RTC_PC → CAPELLA_X → ALOS2_META → EGMS_PROXY | Arquitectura cascada aviación — nunca null | — | — |

---

## Changelog de implementación

| Fecha (ART) | Módulo | Cambio | Motivo | Confianza |
|---|---|---|---|---|
| 2026-06-04 14:45 | `satellite_pipeline.py` / `velez_scheduler.py` | Bug fix: tendencia NDVI corregida + dormancia invernal — elimina falsos positivos post-resembrado | Alertas incorrectas en invierno austral por umbral sin corrección estacional | ✅ Alta |
| 2026-06-04 14:57 | `satellite_pipeline.py` | Bug fix: sanity check NDVI (0–1), skip duplicado de imágenes ya procesadas, aviso antigüedad datos | Imágenes procesadas dos veces; NDVI inválido pasaba sin rechazo | ✅ Alta |
| 2026-06-04 15:12 | `ndvi_real.py` / `velez_storage.py` | Bug fix: ndvi_prev tracking, deduplicación CSV historial, purga imagen contaminada 22/05/2026 | Imagen 22/05 era outlier inválido que sesgaba baseline histórico | ✅ Alta |
| 2026-06-04 15:21 | `velez_scheduler.py` / `dale_play_storage.py` | Bug fix: 6 correcciones — skip condition, ndvi_prev, CSV dedup, tendencia alertas, hora ART en emails | Errores acumulados detectados en revisión de pipeline | ✅ Alta |
| 2026-06-04 16:12 | `faro_ndvi_clean.py` / `ndvi_real.py` | CLOSDI shadow masking + BSI + NDWI + SCL pixel-level filtering — bug raíz imagen 22/05 | CLOSDI (`250*(NIR+RED)/(NIR+2.4*RED+1) < 34`) no estaba aplicado correctamente en ruta rasterio | ✅ Alta |
| 2026-06-04 16:43 | `ndvi_real.py` / `dale_play_satellite.py` / `velez_scheduler.py` | BSI + NDWI + GNDVI calculados por cancha, propagados a heatmaps y email Roger con label `[DATO REAL]` | Índices disponibles en S2 pero no explotados; BSI detecta suelo expuesto post-show | ✅ Alta |
| 2026-06-04 16:43 | `dale_play_certification.py` / `dale_play_opentimestamps.py` | OpenTimestamps — SHA-256 del certificado anclado a Bitcoin via OTS calendar REST (alice/bob/finney) | Inmutabilidad verificable on-chain; sin dependencia de librería OTS | ✅ Alta |
| 2026-06-04 20:22 | `ndvi_real.py` / `velez_supabase.py` / `satellite_pipeline.py` | stackstac — reemplaza loop rasterio per-cancha por un único `da.compute()` sobre cluster bbox completo | 16 canchas × 5 bandas = 80 HTTP requests → 1 llamada Dask; fallback rasterio si ImportError | ✅ Alta |
| 2026-06-04 20:22 | `velez_supabase.py` / `satellite_pipeline.py` / `app.py` | `pipeline_runs` Supabase — registra cada ciclo satelital (timestamp, ndvi_median, accepted, skipped_reason) | Sin observabilidad del pipeline; imposible auditar rechazos de imagen | ✅ Alta |
| 2026-06-04 20:22 | `dale_play_storage.py` / `dale_play_scheduler.py` / `app.py` | `show_monitors` Supabase + `recover_monitors_on_startup()` — persiste jobs APScheduler entre restarts Railway | Jobs de monitoreo post-show se perdían en cada deploy/crash de Railway | ✅ Alta |
| 2026-06-04 20:40 | `field_timeseries.py` / `dale_play_timeseries_baseline.py` | pyPhenology — modelo doble-logístico reemplaza Savitzky-Golay; auto-recalibración con nuevas obs; `forecast_4w` en `detect_anomaly()` | SavGol no es un modelo fenológico real; sin capacidad de forecast | ✅ Alta |
| 2026-06-04 20:45 | `dale_play_cloudbreaker.py` / `dale_play_satellite.py` | CloudBreaker — NDVI sintético SAR (step 2.6 en `fetch_satellite_baseline`) cuando CLOSDI descarta imagen o catálogo vacío | Sin NDVI óptico, certificado queda ciego; SAR provee estimación con confianza 0.30–0.65 | ⚠️ Media |
| 2026-06-04 23:56 | `dale_play_insar_cdse.py` / `dale_play_egms.py` / `dale_play_structural.py` | openEO InSAR CDSE — `sentinel1_sar_interferogram` sobre SENTINEL1_SLC; reemplaza EGMS 2022 estático con deformación milimétrica por tribuna (cache Supabase 30d) | EGMS datos de 2022; deformación real post-shows requiere medición reciente | ⚠️ Media |
| 2026-06-05 | Supabase | Tabla `insar_cdse_results` creada (venue_id PK, job_id, ref/sec_date, sectores JSONB, vel_max_abs_mm_yr, coherencia_media) | Persistencia de resultados InSAR CDSE entre requests Railway | ✅ Alta |
| 2026-06-05 | `agents/hermes.py` (NUEVO) | Agente Hermes — valida plausibilidad física SAR + NDVI estacional antes de emitir SHA-256; bloquea certificado si anomalía detectada; log a `hermes_flags` Supabase | Certificados sin validación de anomalías: NDVI 0.88 en invierno austral pasaba sin freno | ✅ Alta |
| 2026-06-05 | `agents/paperclip.py` (NUEVO) | Agente Paperclip — reemplaza umbrales fijos en job de monitoreo 6h con razonamiento contextual Claude; decide alertar según clima + NDVI + SAR + época + historial; persiste todas las decisiones en `paperclip_decisions` | Falsos positivos por NDVI bajo en invierno / lluvia previa sin contexto | ✅ Alta |
| 2026-06-05 | `agents/hermes.py` + MCP GIS | MachinaOS GIS MCP Server — 4 herramientas: `get_spectral_indices`, `get_sar_change`, `generate_field_report`, `get_insar_coherence`; accesibles via Claude Desktop | Análisis GIS sin protocolo; cada consulta requería Python manual | ✅ Alta |
| 2026-06-05 | Supabase | Tablas `hermes_flags` + `paperclip_decisions` creadas + índices + vistas `hermes_flags_recientes` / `paperclip_alertas_activas` | Persistencia de decisiones de agentes Claude para auditoría y trazabilidad | ✅ Alta |
| 2026-06-05 | `dale_play_certification.py` | Fix 1: hook Hermes integrado antes de `generar_certificado()` — `validate_certificate()` llama Claude API; `CertificateBlocked` cancela emisión | Hermes existía pero no estaba conectado al pipeline real de certificación Dale Play | ✅ Alta |
| 2026-06-05 | `dale_play_certification.py` / `dale_play_routes.py` | Fix 2: PDF persistido como base64 en `certifications.data.pdf_base64`; `/certificado/{show_id}` usa Supabase como fallback cuando filesystem Railway es efímero | PDFs perdidos en cada redeploy Railway; endpoint devolvía 404 en producción | ✅ Alta |
| 2026-06-05 | `migrations/fix3_pipeline_audit_schema.sql` | Fix 3: migración SQL — crea `audit_log` (inmutable, trazabilidad pipeline Dale Play) y asegura `pipeline_runs` con columna `created_at`; idempotente | `audit_log` nunca existía en Supabase; `pipeline_runs` con schema mismatch | ✅ Alta |
| 2026-06-05 | `dale_play_monitor.py` | Fix 4: `get_monitor_status()` reporta motor activo (`paperclip` vs `umbrales_fijos`) detectando ANTHROPIC_API_KEY + anthropic instalado | Endpoint mostraba umbrales hardcodeados sin indicar que Paperclip estaba activo | ✅ Alta |
| 2026-06-05 | `app.py` | Fix 5: `/velez/refresh_status` enriquece `satellite.last` desde Supabase `pipeline_runs` cuando estado en memoria es vacío (post-redeploy) | `satellite.last: {}` tras cada Railway deploy — imposible saber cuándo corrió el pipeline por última vez | ✅ Alta |
