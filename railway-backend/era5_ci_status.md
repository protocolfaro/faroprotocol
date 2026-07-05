# ERA5-Land CI — Estado, fixes y validación

**Fecha:** 2026-07-05  
**Estado:** ESTABLE — 8/8 sanity checks pass, 8 resilience tests escritos

---

## Qué se arregló

### 1. Workflow GitHub Actions (`era5_land_daily.yml`)

| Problema anterior | Fix |
|---|---|
| `ubuntu-20.04` deprecated | `ubuntu-latest` |
| Sin cache pip → 4-5 min de install | `cache: pip` en `setup-python@v5` |
| Instalaba `requirements.txt` completo (todos los paquetes satelitales) | Solo instala ERA5 deps: `cdsapi xarray netCDF4 tenacity requests numpy scipy` |
| Sin validación de `CDS_API_KEY` → crash tardío | Pre-flight step: valida presencia + formato `UID:APIkey` antes de descargar |
| Sin validación de imports Python | Pre-flight verifica netCDF4, xarray, cdsapi, tenacity |
| Sin verificación de `sector_definitions.json` | Pre-flight verifica archivo existe + cuenta sectores |
| Sin retry a nivel CI | 3 intentos con 90s/180s backoff (rate limit CDS) |
| Sin verificación post-run | Step "Verify Supabase write" consulta `climate_metrics_sectorial` |
| Sin alerta de fallo | Email via GMAIL_APP_PASS cuando los 3 intentos fallan |
| Sin `workflow_dispatch` inputs | Se puede disparar manualmente con `--date YYYY-MM-DD --sector nombre` |

### 2. Módulo Python (`faro_era5_land_sectorial.py`)

| Vulnerabilidad anterior | Fix |
|---|---|
| `with open(sector_definitions.json)` sin try/except → crash en import | `try/except` con `SECTORS = {}` fallback + warning |
| `import cdsapi` sin fallback → `ImportError` si no instalado | `_HAS_CDS` flag, todas las rutas lo comprueban antes de usar |
| `import xarray` sin fallback | `_HAS_XR` flag, ídem |
| Sin `preflight_check()` — primer error aparece al intentar descargar | `preflight_check() -> list[str]` valida todo antes de empezar |
| Sin retry en descarga CDS | `_download_era5_land()` usa tenacity: 3 intentos, espera exponencial 60-300s |
| Sin retry en upsert Supabase | `_upsert_sector_row()` usa tenacity: 3 intentos, espera fija 8s |
| Un sector falla → todo el proceso falla (fail-fast) | `process_all_sectors()` continúa con los demás sectores, status=`partial` |
| `process_all_sectors()` sin early-exit de preflight | Llama `preflight_check()` primero, retorna `status=failed` si hay errores |
| Sin validación de valores de salida | `_validate_output()` detecta anomalías (ET0>15mm, RH<10%, SM>0.60) y emite warnings |
| Sin `--sector` CLI arg | `argparse` acepta `--date` y `--sector` |
| cdsapi Client sin timeouts configurados | `retry_max=3, sleep_max=120, timeout=300` |

### 3. Requirements (`requirements.txt`)

- Añadido `xarray>=2024.1.0` (era dependencia transitiva vía rioxarray, ahora explícita)
- `tenacity>=8.2.0` ya existía; `cdsapi>=0.6.1`, `netCDF4>=1.6.5` ya existían

---

## Cómo validar localmente

### Sanity check rápido (sin CDS ni Supabase reales)
```bash
cd railway-backend
python _check_era5.py
# Resultado esperado: 8/8 passed
```

### Tests de resiliencia (pytest)
```bash
cd railway-backend
pip install pytest
pytest tests/test_era5_resilience.py -v
# 8 tests, 5 clases
```

### Verificar preflight con key real
```bash
export CDS_API_KEY="TU_UID:TU_API_KEY"
export SUPABASE_URL="https://xljxpzudgwhbzcnrvylo.supabase.co"
export SUPABASE_KEY="TU_KEY"
cd railway-backend
python -c "
import sys; sys.path.insert(0, 'sports/clients/velez')
import faro_era5_land_sectorial as e
print(e.preflight_check())  # debe retornar []
"
```

### Run manual con fecha específica
```bash
cd railway-backend
python sports/clients/velez/faro_era5_land_sectorial.py --date 2026-06-28 --sector norte
```

---

## Secrets necesarios en GitHub Actions

| Secret | Dónde obtener | Estado |
|---|---|---|
| `CDS_API_KEY` | https://cds.climate.copernicus.eu → perfil → API key (formato `UID:APIkey`) | **PENDIENTE** |
| `SUPABASE_URL` | Supabase dashboard → Settings → API | OK (asumido presente) |
| `SUPABASE_KEY` | Supabase dashboard → Settings → API → service_role | OK (asumido presente) |
| `GMAIL_APP_PASS` | Gmail → Seguridad → Contraseñas de aplicaciones | Opcional (sin esto no llega email de alerta) |

Para agregar: `github.com/tu-org/tu-repo/settings/secrets/actions`

---

## Arquitectura del retry

```
process_all_sectors()
  └── preflight_check()           ← falla rápido si configuración inválida
      ├── CDS_API_KEY presente y formato UID:APIkey
      ├── SUPABASE_URL / SUPABASE_KEY presentes
      ├── cdsapi instalado (_HAS_CDS)
      ├── xarray instalado (_HAS_XR)
      └── SECTORS no vacío

  └── por cada sector:
      └── _download_era5_land()   ← tenacity: 3 intentos, 60-300s exponencial
          └── _download_era5_land_raw()  ← una sola descarga CDS
      └── _aggregate()            ← procesa NetCDF → dict
      └── _validate_output()      ← warnings si valores fuera de rango
      └── _upsert_sector_row()    ← tenacity: 3 intentos, 8s fijo
          └── _upsert_sector_row_raw()  ← POST Supabase REST
      ← continúa con siguiente sector aunque este falle
```

**Resultado final:**
- Todos OK → `{"status": "ok", "sectors_ok": N, "sectors_failed": 0}`
- Algún fallo → `{"status": "partial", "sectors_ok": M, "sectors_failed": K}`
- Preflight falla → `{"status": "failed", "preflight_errors": [...]}`

---

## Tests de resiliencia — cobertura

| Test | Escenario | Verifica |
|---|---|---|
| `test_missing_key_returns_preflight_error` | Sin CDS_API_KEY | preflight detecta ausencia |
| `test_wrong_format_key_returns_preflight_error` | Key sin `:` | preflight detecta formato |
| `test_process_returns_failed_on_missing_key` | process con key vacía | status=failed, preflight_errors presente |
| `test_supabase_retries_on_oserror` | Supabase falla 2x, ok 3ra | 3 intentos, retorna True |
| `test_supabase_fails_after_3_attempts` | Supabase falla siempre | retorna False sin crash |
| `test_failed_sector_does_not_block_others` | sector_a falla | sector_b procesado, status=partial |
| `test_cds_rate_limit_retried_then_fails_gracefully` | 429 × 3 | retorna None sin crash |
| `test_cds_success_on_second_attempt` | CDS falla 1x, ok 2da | retorna path no-None |
| `test_missing_cdsapi_caught_in_preflight` | _HAS_CDS=False | preflight error de cdsapi |
| `test_missing_xarray_caught_in_preflight` | _HAS_XR=False | preflight error de xarray |
| `test_download_returns_none_without_cdsapi` | _HAS_CDS=False | _download retorna None |
| `test_no_cds_calls_when_preflight_fails` | preflight falla | _download nunca se llama |
