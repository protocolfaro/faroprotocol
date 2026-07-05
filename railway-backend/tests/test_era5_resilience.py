"""
test_era5_resilience.py — Tests de resiliencia para faro_era5_land_sectorial.py

Cubre los 5 escenarios de fallo críticos:
  1. CDS_API_KEY inválida o ausente → pre-flight error claro
  2. Supabase timeout → retry 3x + eventual success
  3. Descarga de sector falla → continúa con otros sectores (no fail-fast)
  4. Rate limit 429 / CDS error → retry con backoff, no crash
  5. netCDF4 / xarray ausente → fallo informativo antes de descargar

Todos los tests son sin llamadas reales a CDS ni Supabase (mock completo).
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch, call

import pytest

# Asegura que el módulo es importable desde el path de test
_MODULE_DIR = os.path.join(
    os.path.dirname(__file__), "..", "sports", "clients", "velez"
)
sys.path.insert(0, os.path.abspath(_MODULE_DIR))


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture()
def valid_env(monkeypatch):
    """Setea env vars válidas para todos los tests."""
    monkeypatch.setenv("CDS_API_KEY",   "99999:abcdef0123456789")
    monkeypatch.setenv("SUPABASE_URL",  "https://fake.supabase.co")
    monkeypatch.setenv("SUPABASE_KEY",  "fake-key-for-testing")


@pytest.fixture()
def sector_definitions_file(tmp_path):
    """Crea un sector_definitions.json mínimo en un directorio temporal."""
    defs = {
        "sectors": {
            "sector_a": {
                "bbox": {
                    "latitud_min":  -34.65,
                    "latitud_max":  -34.63,
                    "longitud_min": -58.53,
                    "longitud_max": -58.51,
                }
            },
            "sector_b": {
                "bbox": {
                    "latitud_min":  -34.66,
                    "latitud_max":  -34.64,
                    "longitud_min": -58.54,
                    "longitud_max": -58.52,
                }
            },
        }
    }
    p = tmp_path / "sector_definitions.json"
    p.write_text(json.dumps(defs), encoding="utf-8")
    return str(tmp_path)


# ── Test 1: CDS_API_KEY inválida / ausente ────────────────────────────────────

class TestInvalidCDSKey:
    """
    Si CDS_API_KEY está ausente o mal formateada, preflight_check() debe
    retornar un error claro y process_all_sectors() debe retornar status='failed'
    sin intentar descargar nada.
    """

    def test_missing_key_returns_preflight_error(self, monkeypatch):
        monkeypatch.setenv("SUPABASE_URL", "https://fake.supabase.co")
        monkeypatch.setenv("SUPABASE_KEY", "fake-key")
        monkeypatch.delenv("CDS_API_KEY", raising=False)

        import importlib
        import faro_era5_land_sectorial as era5
        importlib.reload(era5)  # recarga con env vars nuevas

        errors = era5.preflight_check()
        assert any("CDS_API_KEY" in e for e in errors), (
            f"Se esperaba error de CDS_API_KEY en preflight, got: {errors}"
        )

    def test_wrong_format_key_returns_preflight_error(self, monkeypatch):
        monkeypatch.setenv("CDS_API_KEY",   "sin_dos_puntos_invalida")
        monkeypatch.setenv("SUPABASE_URL",  "https://fake.supabase.co")
        monkeypatch.setenv("SUPABASE_KEY",  "fake-key")

        import importlib
        import faro_era5_land_sectorial as era5
        importlib.reload(era5)

        errors = era5.preflight_check()
        assert any("formato" in e.lower() or "UID:APIkey" in e for e in errors), (
            f"Se esperaba error de formato en preflight, got: {errors}"
        )

    def test_process_returns_failed_on_missing_key(self, monkeypatch, sector_definitions_file):
        monkeypatch.setenv("SUPABASE_URL", "https://fake.supabase.co")
        monkeypatch.setenv("SUPABASE_KEY", "fake-key")
        monkeypatch.delenv("CDS_API_KEY", raising=False)

        import importlib
        import faro_era5_land_sectorial as era5
        # Patch sector path to use our fixture
        with patch.object(era5, "_DEF_PATH",
                          str(os.path.join(sector_definitions_file, "sector_definitions.json"))):
            with patch.object(era5, "SECTORS",
                              json.loads(open(os.path.join(
                                  sector_definitions_file, "sector_definitions.json")).read())["sectors"]):
                with patch.object(era5, "_CDS_KEY", ""):
                    result = era5.process_all_sectors()

        assert result["status"] == "failed", (
            f"Expected status='failed' on missing CDS_API_KEY, got: {result['status']}"
        )
        assert "preflight_errors" in result


# ── Test 2: Supabase timeout → retry 3x ──────────────────────────────────────

class TestSupabaseRetry:
    """
    Si Supabase falla las primeras 2 veces y funciona en la 3ra,
    _upsert_sector_row debe retornar True y no propagar la excepción.
    """

    def test_supabase_retries_on_oserror(self, valid_env):
        import importlib
        import faro_era5_land_sectorial as era5
        importlib.reload(era5)

        call_count = {"n": 0}

        def flaky_post(*args, **kwargs):
            call_count["n"] += 1
            if call_count["n"] < 3:
                raise OSError(f"Connection refused (attempt {call_count['n']})")
            mock_resp = MagicMock()
            mock_resp.status_code = 201
            return mock_resp

        record = {
            "sector_id": "sector_a", "fecha": "2026-07-01",
            "et0_mm_dia": 2.5, "temp_2m_c": 12.3, "rh_pct": 75.0,
            "fuente": "ERA5-Land-hourly",
        }

        with patch("requests.post", side_effect=flaky_post):
            result = era5._upsert_sector_row(record)

        assert result is True, f"Expected True after retry, got {result}"
        assert call_count["n"] == 3, f"Expected 3 attempts, got {call_count['n']}"

    def test_supabase_fails_after_3_attempts(self, valid_env):
        import importlib
        import faro_era5_land_sectorial as era5
        importlib.reload(era5)

        record = {"sector_id": "sector_a", "fecha": "2026-07-01", "et0_mm_dia": 2.5}

        with patch("requests.post", side_effect=OSError("Persistent failure")):
            result = era5._upsert_sector_row(record)

        assert result is False, f"Expected False after 3 failed attempts, got {result}"


# ── Test 3: Descarga falla en sector X → continúa con sector Y ───────────────

class TestSectorFailContinue:
    """
    Si la descarga de sector_a falla, el pipeline debe continuar con sector_b
    y sector_b debe procesarse correctamente.
    """

    def test_failed_sector_does_not_block_others(self, valid_env, tmp_path):
        import importlib
        import faro_era5_land_sectorial as era5
        importlib.reload(era5)

        two_sectors = {
            "sector_a": {"bbox": {"latitud_min": -34.65, "latitud_max": -34.63,
                                  "longitud_min": -58.53, "longitud_max": -58.51}},
            "sector_b": {"bbox": {"latitud_min": -34.66, "latitud_max": -34.64,
                                  "longitud_min": -58.54, "longitud_max": -58.52}},
        }
        mock_agg = {
            "sector_id": "sector_b",
            "et0_mm_dia": 2.1, "temp_2m_c": 11.0, "rh_pct": 80.0,
            "sm_0_7cm_m3m3": 0.25, "sm_0_7cm_pct": 25.0,
        }

        download_calls = []

        def fake_download(sector_id, bbox, date, out_dir):
            download_calls.append(sector_id)
            if sector_id == "sector_a":
                return None   # simula fallo de descarga
            # sector_b: crear archivo .nc vacío ficticio
            nc_path = os.path.join(out_dir, f"era5_{sector_id}_20260701.nc")
            open(nc_path, "wb").write(b"\x00" * 2048)  # archivo mínimo > 1KB
            return nc_path

        with patch.object(era5, "SECTORS", two_sectors), \
             patch.object(era5, "_download_era5_land", side_effect=fake_download), \
             patch.object(era5, "_aggregate", return_value=mock_agg), \
             patch.object(era5, "_fetch_latest_sector", return_value=None), \
             patch.object(era5, "_upsert_sector_row", return_value=True), \
             patch.object(era5, "_CDS_KEY", "99999:abc"):
            result = era5.process_all_sectors()

        assert "sector_a" in download_calls, "sector_a debería haberse intentado"
        assert "sector_b" in download_calls, "sector_b debería haberse intentado"
        assert result["sectors_ok"] == 1, f"Expected 1 ok, got {result['sectors_ok']}"
        assert result["sectors_failed"] == 1, f"Expected 1 failed, got {result['sectors_failed']}"
        assert result["status"] == "partial", f"Expected 'partial', got {result['status']}"


# ── Test 4: Rate limit 429 / CDS error → retry + no crash ────────────────────

class TestCDSRateLimit:
    """
    Si el CDS client lanza RuntimeError (e.g., por 429 o queue error),
    _download_era5_land debe reintentar y eventualmente retornar None sin crashear.
    """

    def test_cds_rate_limit_retried_then_fails_gracefully(self, valid_env, tmp_path):
        import importlib
        import faro_era5_land_sectorial as era5
        importlib.reload(era5)

        attempt_count = {"n": 0}

        def rate_limited_raw(*args, **kwargs):
            attempt_count["n"] += 1
            raise RuntimeError(f"429 Too Many Requests (attempt {attempt_count['n']})")

        bbox = {"latitud_min": -34.65, "latitud_max": -34.63,
                "longitud_min": -58.53, "longitud_max": -58.51}
        date  = datetime(2026, 7, 1, tzinfo=timezone.utc)

        with patch.object(era5, "_download_era5_land_raw", side_effect=rate_limited_raw), \
             patch.object(era5, "_CDS_KEY", "99999:abc"), \
             patch.object(era5, "_HAS_CDS", True), \
             patch.object(era5, "_HAS_XR",  True):
            result = era5._download_era5_land("sector_a", bbox, date, str(tmp_path))

        assert result is None, f"Expected None after exhausted retries, got {result}"
        # Should have tried 3 times (stop_after_attempt(3))
        assert attempt_count["n"] == 3, (
            f"Expected 3 retry attempts for 429, got {attempt_count['n']}"
        )

    def test_cds_success_on_second_attempt(self, valid_env, tmp_path):
        import importlib
        import faro_era5_land_sectorial as era5
        importlib.reload(era5)

        attempt_count = {"n": 0}
        nc_file = str(tmp_path / "era5_sector_a_20260701.nc")

        def flaky_raw(*args, **kwargs):
            attempt_count["n"] += 1
            if attempt_count["n"] < 2:
                raise RuntimeError("CDS queue error — retry")
            # Simular archivo descargado
            open(nc_file, "wb").write(b"\x00" * 2048)
            return True

        bbox = {"latitud_min": -34.65, "latitud_max": -34.63,
                "longitud_min": -58.53, "longitud_max": -58.51}
        date  = datetime(2026, 7, 1, tzinfo=timezone.utc)

        with patch.object(era5, "_download_era5_land_raw", side_effect=flaky_raw), \
             patch.object(era5, "_CDS_KEY", "99999:abc"), \
             patch.object(era5, "_HAS_CDS", True), \
             patch.object(era5, "_HAS_XR",  True):
            result = era5._download_era5_land("sector_a", bbox, date, str(tmp_path))

        assert result is not None, "Expected non-None on second attempt success"
        assert attempt_count["n"] == 2, f"Expected 2 attempts, got {attempt_count['n']}"


# ── Test 5: netCDF4 / xarray ausente → pre-flight falla antes de descargar ───

class TestMissingDependencies:
    """
    Si _HAS_CDS o _HAS_XR son False, preflight_check() lo detecta
    y process_all_sectors() falla antes de tocar CDS.
    """

    def test_missing_cdsapi_caught_in_preflight(self, valid_env, monkeypatch):
        import importlib
        import faro_era5_land_sectorial as era5
        importlib.reload(era5)

        with patch.object(era5, "_HAS_CDS", False), \
             patch.object(era5, "_HAS_XR",  True):
            errors = era5.preflight_check()

        cds_error = [e for e in errors if "cdsapi" in e.lower()]
        assert cds_error, (
            f"Se esperaba error de cdsapi en preflight, got: {errors}"
        )

    def test_missing_xarray_caught_in_preflight(self, valid_env, monkeypatch):
        import importlib
        import faro_era5_land_sectorial as era5
        importlib.reload(era5)

        with patch.object(era5, "_HAS_CDS", True), \
             patch.object(era5, "_HAS_XR",  False):
            errors = era5.preflight_check()

        xr_error = [e for e in errors if "xarray" in e.lower() or "netcdf" in e.lower()]
        assert xr_error, (
            f"Se esperaba error de xarray en preflight, got: {errors}"
        )

    def test_download_returns_none_without_cdsapi(self, valid_env, tmp_path):
        import importlib
        import faro_era5_land_sectorial as era5
        importlib.reload(era5)

        bbox = {"latitud_min": -34.65, "latitud_max": -34.63,
                "longitud_min": -58.53, "longitud_max": -58.51}
        date  = datetime(2026, 7, 1, tzinfo=timezone.utc)

        with patch.object(era5, "_HAS_CDS", False):
            result = era5._download_era5_land("sector_a", bbox, date, str(tmp_path))

        assert result is None, f"Expected None when cdsapi missing, got {result}"

    def test_no_cds_calls_when_preflight_fails(self, valid_env, monkeypatch):
        """Cuando preflight falla, no se hace ninguna llamada al CDS API."""
        import importlib
        import faro_era5_land_sectorial as era5
        importlib.reload(era5)

        download_called = []

        with patch.object(era5, "_HAS_CDS", False), \
             patch.object(era5, "_download_era5_land",
                          side_effect=lambda *a, **k: download_called.append(1) or None):
            result = era5.process_all_sectors()

        assert result["status"] == "failed"
        assert len(download_called) == 0, (
            f"_download_era5_land no debe llamarse cuando preflight falla. "
            f"Se llamó {len(download_called)} veces."
        )
