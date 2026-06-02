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
