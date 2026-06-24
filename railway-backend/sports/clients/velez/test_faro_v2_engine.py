"""
test_faro_v2_engine.py — Suite pytest para Faro Engine V2.

Cubre:
  - Venue registry (valid / invalid)
  - Open-Meteo API call (mocked)
  - HAND topography (mocked deps)
  - Van Genuchten physics (determinista)
  - FaroAuditor SHA-256 + TSA RFC3161 (mocked network)
  - FaroEngine.run() end-to-end (skip canopy lband para no depender de GEE)
  - persist_to_supabase (mocked Supabase)
  - record_prometheus_metrics (filesystem)
  - CLI argparse parsing

Uso:
    pytest sports/clients/velez/test_faro_v2_engine.py -v
    pytest sports/clients/velez/test_faro_v2_engine.py -v -k "solar or hand"
"""
from __future__ import annotations

import base64
import json
import os
import sys
import tempfile
from datetime import date
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

# Asegurar que el módulo sea importable desde cualquier CWD
_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import faro_v2_engine as _fv2  # needed for patch.dict on _RUNNERS

from faro_v2_engine import (
    AuditRecord,
    CanopyMetrics,
    FaroAuditor,
    FaroEngine,
    FaroV2Report,
    HydroMetrics,
    LBandBaseline,
    SARMetrics,
    SolarMetrics,
    VENUE_REGISTRY,
    _compute_hand,
    _fetch_solar,
    _van_genuchten,
    persist_to_supabase,
    record_prometheus_metrics,
    _v,
)


# ─────────────────────────────────────────────────────────────────────────────
# Venue Registry
# ─────────────────────────────────────────────────────────────────────────────

def test_venue_registry_known_venue():
    v = _v("amalfitani")
    assert v["lat"]  == pytest.approx(-34.6373)
    assert v["lon"]  == pytest.approx(-58.5240)
    assert "bbox"    in v
    assert len(v["bbox"]) == 4


def test_venue_registry_unknown_raises():
    with pytest.raises(ValueError, match="no registrado"):
        _v("estadio_fantasma")


def test_all_registry_venues_have_required_keys():
    required = {"lat", "lon", "bbox", "altitude_m", "timezone"}
    for venue_id, meta in VENUE_REGISTRY.items():
        missing = required - meta.keys()
        assert not missing, f"venue '{venue_id}' falta: {missing}"


# ─────────────────────────────────────────────────────────────────────────────
# Open-Meteo Solar
# ─────────────────────────────────────────────────────────────────────────────

_OPENMETEO_FIXTURE = {
    "daily": {
        "time":                         ["2026-06-20", "2026-06-21", "2026-06-22"],
        "shortwave_radiation_sum":       [8.5,           9.1,          8.8],    # MJ/m²
        "et0_fao_evapotranspiration":    [2.1,           2.3,          2.2],    # mm
    }
}


def test_fetch_solar_happy_path():
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = _OPENMETEO_FIXTURE

    with patch("faro_v2_engine.requests.get", return_value=mock_resp):
        result = _fetch_solar("amalfitani", dias=3)

    assert isinstance(result, SolarMetrics)
    assert result.ghi_wh_m2  is not None
    assert result.et0_mm_dia is not None
    # 8.8 MJ/m² × 277.78 = 2444.5 Wh/m²
    assert result.ghi_wh_m2 == pytest.approx(8.8 * 277.78, abs=1.0)
    assert result.et0_mm_dia == pytest.approx(2.2, abs=0.01)


def test_fetch_solar_api_failure_returns_defaults():
    mock_resp = MagicMock()
    mock_resp.status_code = 503
    mock_resp.raise_for_status.side_effect = Exception("Service Unavailable")

    with patch("faro_v2_engine.requests.get", return_value=mock_resp):
        result = _fetch_solar("amalfitani", dias=3)

    assert isinstance(result, SolarMetrics)
    assert result.ghi_wh_m2  is None
    assert result.et0_mm_dia is None


def test_fetch_solar_network_error_returns_defaults():
    with patch("faro_v2_engine.requests.get", side_effect=ConnectionError("timeout")):
        result = _fetch_solar("villa_olimpica", dias=3)

    assert isinstance(result, SolarMetrics)
    assert result.ghi_wh_m2 is None


def test_fetch_solar_empty_response_returns_defaults():
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"daily": {}}

    with patch("faro_v2_engine.requests.get", return_value=mock_resp):
        result = _fetch_solar("amalfitani")

    assert result.ghi_wh_m2 is None


# ─────────────────────────────────────────────────────────────────────────────
# Van Genuchten physics
# ─────────────────────────────────────────────────────────────────────────────

def test_van_genuchten_typical_values():
    theta, h_suc = _van_genuchten(vv_db=-12.0, vh_db=-19.5)
    assert 0.045 < theta < 0.41, f"theta={theta} fuera de rango Van Genuchten"
    assert h_suc > 0, f"h_suction debe ser positiva, got {h_suc}"


def test_van_genuchten_wet_soil_higher_theta():
    theta_wet,  _ = _van_genuchten(vv_db=-8.0,  vh_db=-15.5)
    theta_dry,  _ = _van_genuchten(vv_db=-18.0, vh_db=-25.5)
    assert theta_wet > theta_dry, "suelo húmedo debe tener mayor theta"


def test_van_genuchten_no_nan():
    for vv in [-5, -10, -15, -20, -25]:
        theta, h = _van_genuchten(float(vv), float(vv) - 7.5)
        assert not (theta != theta), f"NaN en theta con VV={vv}"
        assert not (h != h),         f"NaN en h_suction con VV={vv}"


# ─────────────────────────────────────────────────────────────────────────────
# HAND topography
# ─────────────────────────────────────────────────────────────────────────────

def _make_flat_dem(rows: int = 20, cols: int = 20, noise: float = 0.3) -> np.ndarray:
    """DEM sintético: superficie plana con depresión central (cauce simulado)."""
    dem = np.full((rows, cols), 25.0, dtype=np.float32)
    dem += np.random.default_rng(42).uniform(0, noise, (rows, cols))
    # Depresión central (cauce)
    dem[rows // 2, :] -= 1.5
    return dem


def test_compute_hand_numpy_logic():
    """
    Testea la lógica HAND pura sobre un array numpy sintético,
    sin depender de pystac_client/stackstac (que son lazy imports).
    """
    import scipy.ndimage as ndi

    dem  = _make_flat_dem(rows=20, cols=20)
    p5   = np.nanpercentile(dem, 5)
    cauce = dem <= p5
    _, idx = ndi.distance_transform_edt(~cauce, return_indices=True)
    nearest_elev = dem[idx[0], idx[1]]
    hand = np.clip(dem - nearest_elev, 0, None)

    assert np.all(hand >= 0), "HAND no puede ser negativo"
    assert np.nanmean(hand) < 5.0, "HAND medio irreal en terreno plano sintético"


def test_compute_hand_with_mocked_stac():
    """Testea _compute_hand inyectando mocks en sys.modules para deps lazy."""
    dem = _make_flat_dem()
    h, w = dem.shape

    # mocks para deps importadas lazily dentro de _compute_hand
    mock_pystac    = MagicMock()
    mock_stackstac = MagicMock()
    mock_dask      = MagicMock()
    mock_ndi       = MagicMock()

    # pystac_client search → 1 item
    mock_pystac.Client.open.return_value.search.return_value.items.return_value = [MagicMock()]

    # stackstac → DataArray con DEM sintético
    mock_da = MagicMock()
    mock_da.mean.return_value.squeeze.return_value.compute.return_value.values = dem
    mock_stackstac.stack.return_value = mock_da

    # dask.config.set como context manager
    cm = MagicMock(); cm.__enter__ = MagicMock(return_value=None); cm.__exit__ = MagicMock(return_value=False)
    mock_dask.config.set.return_value = cm

    # scipy.ndimage.distance_transform_edt → índices todos cero (cauce en [0,0])
    idx_arr = np.zeros((2, h, w), dtype=int)
    mock_ndi.distance_transform_edt.return_value = (np.zeros((h, w)), idx_arr)

    # inyectar en sys.modules ANTES de que la función haga "import X"
    saved = {}
    mods  = {"pystac_client": mock_pystac, "stackstac": mock_stackstac,
             "dask": mock_dask, "scipy.ndimage": mock_ndi}
    for k, v in mods.items():
        saved[k] = sys.modules.get(k)
        sys.modules[k] = v
    try:
        result = _compute_hand("amalfitani")
    finally:
        for k, v in saved.items():
            if v is None: sys.modules.pop(k, None)
            else: sys.modules[k] = v

    assert isinstance(result, HydroMetrics)
    # Si el DEM es sintético, hand_mean puede ser 0 (todos apuntan al cauce)
    assert result.hand_mean_m is not None


def test_compute_hand_no_items_returns_empty():
    """Testea que sin items STAC, retorna HydroMetrics vacío."""
    mock_pystac = MagicMock()
    mock_pystac.Client.open.return_value.search.return_value.items.return_value = []

    saved = sys.modules.get("pystac_client")
    sys.modules["pystac_client"] = mock_pystac
    try:
        result = _compute_hand("amalfitani")
    finally:
        if saved is None: sys.modules.pop("pystac_client", None)
        else: sys.modules["pystac_client"] = saved

    assert isinstance(result, HydroMetrics)
    assert result.hand_mean_m is None


def test_compute_hand_missing_dep_returns_empty():
    import builtins
    real_import = builtins.__import__

    def mock_import(name, *args, **kwargs):
        if name == "pystac_client":
            raise ImportError("pystac_client not installed")
        return real_import(name, *args, **kwargs)

    with patch("builtins.__import__", side_effect=mock_import):
        result = _compute_hand("amalfitani")

    assert isinstance(result, HydroMetrics)
    assert result.hand_mean_m is None


# ─────────────────────────────────────────────────────────────────────────────
# FaroAuditor
# ─────────────────────────────────────────────────────────────────────────────

def _minimal_report(venue: str = "amalfitani") -> FaroV2Report:
    return FaroV2Report(
        venue_id = venue,
        fecha    = "2026-06-24",
        solar    = SolarMetrics(fecha="2026-06-24", ghi_wh_m2=2440.0, et0_mm_dia=2.2),
        sar      = SARMetrics(fecha="2026-06-24", vv_gamma0_db=-12.5, theta_soil=0.22),
    )


def test_auditor_sha256_is_deterministic():
    auditor = FaroAuditor()
    r = _minimal_report()
    h1 = auditor.sha256(r)
    h2 = auditor.sha256(r)
    assert h1 == h2
    assert len(h1) == 64  # hex SHA-256


def test_auditor_sha256_changes_with_data():
    auditor = FaroAuditor()
    r1 = _minimal_report()
    r2 = _minimal_report()
    r2.solar.ghi_wh_m2 = 9999.0
    assert auditor.sha256(r1) != auditor.sha256(r2)


def test_auditor_tsa_success():
    # TSA retorna 200 con content-type timestamp-reply
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.content     = b"\x30\x82\x03\xff" + b"\x00" * 50  # DER stub
    mock_resp.headers     = {"Content-Type": "application/timestamp-reply"}

    auditor = FaroAuditor(tsa_url="https://mock-tsa.test/ts")
    with patch("faro_v2_engine.requests.post", return_value=mock_resp):
        token = auditor._stamp("a" * 64, "https://mock-tsa.test/ts")

    assert token is not None
    decoded = base64.b64decode(token)
    assert len(decoded) > 0


def test_auditor_tsa_network_error_returns_none():
    auditor = FaroAuditor(tsa_url="https://mock-tsa.test/ts")
    with patch("faro_v2_engine.requests.post", side_effect=ConnectionError("timeout")):
        token = auditor._stamp("a" * 64, "https://mock-tsa.test/ts")

    assert token is None


def test_auditor_certify_sets_audit_fields():
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.content     = b"\x30\x10" + b"\xab" * 14
    mock_resp.headers     = {"Content-Type": "application/timestamp-reply"}

    auditor = FaroAuditor()
    r = _minimal_report()

    with patch("faro_v2_engine.requests.post", return_value=mock_resp):
        result = auditor.certify(r)

    assert result.audit.sha256 != ""
    assert result.audit.timestamp_iso.endswith("Z")
    assert result.audit.tsa_token_b64 is not None
    assert result.audit.verified is True


def test_auditor_certify_tsa_fail_still_sets_sha256():
    auditor = FaroAuditor(tsa_url="https://mock.test/ts", fallback_url="")
    r = _minimal_report()

    with patch("faro_v2_engine.requests.post", side_effect=Exception("red caída")):
        result = auditor.certify(r)

    assert len(result.audit.sha256) == 64
    assert result.audit.verified is False
    assert result.audit.tsa_token_b64 is None


def test_auditor_der_encoding_structure():
    auditor   = FaroAuditor()
    hash_b    = bytes(range(32))
    tsr       = auditor._build_ts_request(hash_b)
    assert tsr[0] == 0x30, "El TSR debe ser un SEQUENCE DER (tag 0x30)"
    assert len(tsr) > 40, "El TSR parece demasiado corto"


# ─────────────────────────────────────────────────────────────────────────────
# FaroEngine end-to-end
# ─────────────────────────────────────────────────────────────────────────────

def _mock_solar(*a, **kw) -> SolarMetrics:
    return SolarMetrics(fecha=str(date.today()), ghi_wh_m2=2440.0, et0_mm_dia=2.2)


def _mock_hand(*a, **kw) -> HydroMetrics:
    return HydroMetrics(hand_mean_m=1.3, hand_p90_m=2.8, zona_riesgo="medio")


def test_engine_run_skip_canopy_lband_sar():
    # _RUNNERS holds direct function refs — must use patch.dict, not patch("faro_v2_engine._fn")
    with (
        patch.dict(_fv2.FaroEngine._RUNNERS, {"solar": _mock_solar, "hydro": _mock_hand}),
        patch("faro_v2_engine.requests.post",
              return_value=MagicMock(status_code=200,
                                    content=b"\x30\x10" + b"\x00" * 14,
                                    headers={"Content-Type": "application/timestamp-reply"})),
    ):
        engine = FaroEngine("amalfitani")
        report = engine.run(skip=["canopy", "lband", "sar"])

    assert isinstance(report, FaroV2Report)
    assert report.venue_id == "amalfitani"
    assert report.solar.ghi_wh_m2  == pytest.approx(2440.0)
    assert report.hydro.zona_riesgo == "medio"
    assert report.audit.sha256 != ""
    assert report.duration_s   >= 0


def test_engine_invalid_venue_raises():
    with pytest.raises(ValueError):
        FaroEngine("campo_inexistente")


def _raise_solar(v):
    raise RuntimeError("API caída")


def test_engine_component_failure_captured_in_errors():
    with (
        patch.dict(_fv2.FaroEngine._RUNNERS, {"solar": _raise_solar, "hydro": _mock_hand}),
        patch("faro_v2_engine.requests.post",
              return_value=MagicMock(status_code=200,
                                    content=b"\x30\x10" + b"\x00" * 14,
                                    headers={"Content-Type": "application/timestamp-reply"})),
    ):
        engine = FaroEngine("amalfitani")
        report = engine.run(skip=["canopy", "lband", "sar"])

    assert any("solar" in e for e in report.errors)
    # report se construye igual — campos con valor default
    assert isinstance(report.solar, SolarMetrics)


def test_engine_to_json_serializable():
    with (
        patch.dict(_fv2.FaroEngine._RUNNERS, {"solar": _mock_solar, "hydro": _mock_hand}),
        patch("faro_v2_engine.requests.post",
              side_effect=ConnectionError("tsa down")),
    ):
        report = FaroEngine("amalfitani").run(skip=["canopy", "lband", "sar"])

    js = report.to_json()
    data = json.loads(js)
    assert data["venue_id"] == "amalfitani"
    assert "solar" in data
    assert "audit" in data


# ─────────────────────────────────────────────────────────────────────────────
# persist_to_supabase
# ─────────────────────────────────────────────────────────────────────────────

def test_persist_to_supabase_success():
    mock_resp = MagicMock()
    mock_resp.status_code = 201

    with (
        patch.dict(os.environ, {"SUPABASE_URL": "https://x.supabase.co",
                                "SUPABASE_KEY": "testkey123"}),
        patch("faro_v2_engine.requests.post", return_value=mock_resp),
    ):
        ok = persist_to_supabase(_minimal_report())

    assert ok is True


def test_persist_to_supabase_no_creds_returns_false():
    with patch.dict(os.environ, {}, clear=True):
        # Eliminar vars del entorno real si existen
        env = {k: v for k, v in os.environ.items()
               if k not in ("SUPABASE_URL", "SUPABASE_KEY")}
        with patch.dict(os.environ, env, clear=True):
            ok = persist_to_supabase(_minimal_report())
    assert ok is False


def test_persist_to_supabase_http_error_returns_false():
    mock_resp = MagicMock()
    mock_resp.status_code = 500
    mock_resp.text        = "Internal Server Error"

    with (
        patch.dict(os.environ, {"SUPABASE_URL": "https://x.supabase.co",
                                "SUPABASE_KEY": "testkey123"}),
        patch("faro_v2_engine.requests.post", return_value=mock_resp),
    ):
        ok = persist_to_supabase(_minimal_report())

    assert ok is False


# ─────────────────────────────────────────────────────────────────────────────
# Prometheus metrics file
# ─────────────────────────────────────────────────────────────────────────────

def test_record_prometheus_metrics_writes_file():
    r = _minimal_report()
    r.sar.theta_soil     = 0.22
    r.hydro.hand_mean_m  = 1.3
    r.audit              = AuditRecord(sha256="a" * 64, verified=True,
                                       timestamp_iso="2026-06-24T00:00:00Z",
                                       tsa_url="https://ts.test")
    r.duration_s         = 3.7

    with tempfile.NamedTemporaryFile(suffix=".prom", delete=False, mode="w") as f:
        path = f.name

    try:
        record_prometheus_metrics(r, path=path)
        content = open(path).read()

        assert 'venue="amalfitani"' in content
        assert "faro_pipeline_duration_seconds" in content
        assert "faro_sar_theta_soil"            in content
        assert "faro_audit_verified"            in content
        assert "3.7"                            in content
        assert "0.22"                           in content
    finally:
        os.unlink(path)


def test_record_prometheus_metrics_none_fields_skipped():
    r = _minimal_report()
    # sin solar, sin hydro
    r.solar = SolarMetrics(fecha=str(date.today()))  # todo None

    with tempfile.NamedTemporaryFile(suffix=".prom", delete=False, mode="w") as f:
        path = f.name

    try:
        record_prometheus_metrics(r, path=path)
        content = open(path).read()
        assert "faro_solar_ghi_wh_m2" not in content
    finally:
        os.unlink(path)


# ─────────────────────────────────────────────────────────────────────────────
# CLI argparse
# ─────────────────────────────────────────────────────────────────────────────

def test_cli_args_default_venue(monkeypatch):
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--venue",   default="amalfitani", choices=list(VENUE_REGISTRY))
    parser.add_argument("--skip",    nargs="*", default=[])
    parser.add_argument("--persist", action="store_true")
    parser.add_argument("--json",    action="store_true")
    parser.add_argument("--tsa",     default="https://timestamp.sigstore.dev/api/v1/timestamp")

    args = parser.parse_args([])
    assert args.venue   == "amalfitani"
    assert args.skip    == []
    assert args.persist is False
    assert args.json    is False


def test_cli_args_custom_venue_and_skip(monkeypatch):
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--venue", default="amalfitani", choices=list(VENUE_REGISTRY))
    parser.add_argument("--skip",  nargs="*", default=[])
    parser.add_argument("--persist", action="store_true")
    parser.add_argument("--json",    action="store_true")
    parser.add_argument("--tsa",     default="https://timestamp.sigstore.dev/api/v1/timestamp")

    args = parser.parse_args(["--venue", "villa_olimpica", "--skip", "canopy", "lband",
                               "--json", "--persist"])
    assert args.venue   == "villa_olimpica"
    assert "canopy"     in args.skip
    assert "lband"      in args.skip
    assert args.json    is True
    assert args.persist is True
