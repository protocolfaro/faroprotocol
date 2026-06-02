# SOURCE_LOG — Faro Protocol

Registro de auditoría automático. Actualizado en cada ejecución del pipeline y job semanal.

| Timestamp (UTC) | Módulo | Fuente Usada | Fuente Intentadas | Motivo Fallback | Confianza | Venue Cubierto |
|---|---|---|---|---|---|---|
| 2026-06-02 20:00 UTC | **INIT** | `dale_play_sar.py` | UMBRA_X → S1_RTC_PC → CAPELLA_X → ALOS2_META → EGMS_PROXY | Arquitectura cascada aviación — nunca null | — | — |
| 2026-06-02 22:32 UTC | **SCOUT_SEMANAL** | `SAOCOM-1A/1B (ESA/CONAE PUMAS)` | — | SAR · rec=adoptar: L-band polarimétrico nativo argentino; penetra vegetación y suelo húmedo mejor q | — | 🔍 |
| 2026-06-02 22:32 UTC | **SCOUT_SEMANAL** | `SAOCOM-2A (CONAE)` | — | SAR · rec=investigar: Sucesor SAOCOM-1A, lanzamiento previsto 2025-2026; reducirá revisita a ~4 días j | — | 🔍 |
| 2026-06-02 22:32 UTC | **SCOUT_SEMANAL** | `COMET LiCSAR + LiCSBAS` | — | InSAR · rec=adoptar: Interferogramas Sentinel-1 procesados automáticamente y series temporales de def | — | 🔍 |
| 2026-06-02 22:32 UTC | **SCOUT_SEMANAL** | `NASA ARIA (ARIA-S1 Gunw)` | — | InSAR · rec=adoptar: Interferogramas normalizados (GUNW NetCDF) Sentinel-1 sobre América del Sur disp | — | 🔍 |
| 2026-06-02 22:32 UTC | **SCOUT_SEMANAL** | `NASA SMAP L3 Soil Moisture` | — | soil · rec=adoptar: Humedad de suelo activa/pasiva diaria; API EARTHDATA + STAC disponible; corrige  | — | 🔍 |
| 2026-06-02 22:32 UTC | **SCOUT_SEMANAL** | `GRACE-FO Mascon (JPL RL06)` | — | soil · rec=investigar: Anomalías de agua terrestre subterránea mensual sobre cuenca del Plata; proxy de | — | 🔍 |
| 2026-06-02 22:32 UTC | **SCOUT_SEMANAL** | `Landsat Next / Landsat 10` | — | optical · rec=investigar: Constelación 3 satélites, 26 bandas superespectrales, resolución 10-30m, revisit | — | 🔍 |
| 2026-06-02 22:32 UTC | **SCOUT_SEMANAL** | `Planet NICFI Basemaps (Norway NICFI)` | — | optical · rec=investigar: Mosaicos PlanetScope 5m mensuales y bianuales gratuitos para América del Sur (la | — | 🔍 |
| 2026-06-02 22:54 UTC | Satellite_NDVI | `S2` | — | — | **0.90** | ✅ |
| 2026-06-02 22:57 UTC | SAR | `UMBRA_X` | UMBRA_X=ok_proxy → S1_RTC_PC=sin_pixeles → CAPELLA_X=excepcion → ALOS2_META=excepcion | — | 0.25 | ❌ |
| 2026-06-02 22:57 UTC | SAR | `UMBRA_X` | UMBRA_X=ok_proxy → S1_RTC_PC=sin_pixeles → CAPELLA_X=excepcion → ALOS2_META=excepcion | — | 0.25 | ❌ |
| 2026-06-02 23:01 UTC | **SCOUT_SEMANAL** | `SAOCOM-1A/1B (ESA/CONAE PUMAS)` | — | SAR · rec=adoptar: L-band nativo argentino: penetra vegetación y suelo húmedo mejor que C-band Sent | — | 🔍 |
| 2026-06-02 23:01 UTC | **SCOUT_SEMANAL** | `SAOCOM-2A / SAOCOM-2B` | — | SAR · rec=investigar: Sucesores de SAOCOM-1; lanzamiento planificado 2025 (2A) y 2026 (2B); con los 4  | — | 🔍 |
| 2026-06-02 23:01 UTC | **SCOUT_SEMANAL** | `COMET LiCSAR / LiCSBAS (Sentinel-1 InSAR portal)` | — | InSAR · rec=adoptar: Interferogramas y series de tiempo de subsidencia/deformación ya procesados auto | — | 🔍 |
| 2026-06-02 23:01 UTC | **SCOUT_SEMANAL** | `HLS v2.x con Sentinel-2C (NASA LP DAAC)` | — | optical · rec=adoptar: HLS ya en stack actual a 30m; novedad 2024-2025: Sentinel-2C incorporado, revisi | — | 🔍 |
| 2026-06-02 23:01 UTC | **SCOUT_SEMANAL** | `SMAP L4 Soil Moisture (NASA NSIDC)` | — | soil · rec=adoptar: Humedad de suelo superficial y de raíz cada 3h a 9km; clave para correlacionar s | — | 🔍 |
| 2026-06-02 23:01 UTC | **SCOUT_SEMANAL** | `GRACE-FO Mascon (JPL RL06.1)` | — | soil · rec=investigar: Variaciones mensuales de agua total (acuífero Puelche bajo BsAs); combina con SM | — | 🔍 |
| 2026-06-02 23:01 UTC | **SCOUT_SEMANAL** | `Landsat Next (NASA/USGS)` | — | optical · rec=descartar: 26 bandas espectrales vs 11 actuales; resolución 10m (vs 30m Landsat-9); revisit | — | 🔍 |
| 2026-06-02 23:01 UTC | **SCOUT_SEMANAL** | `ARIA S1 GUNW InSAR (JPL/NASA ASIPS)` | — | InSAR · rec=adoptar: Interferogramas Sentinel-1 en formato netCDF listos para análisis de deformación | — | 🔍 |
