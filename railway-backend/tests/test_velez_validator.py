"""Tests for faro_json_validator — covers None-detalle crash fix (line 177)."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from sports.clients.velez.faro_json_validator import validate

_BASE_VD = {
    "weather_live": {
        "timestamp": "2099-01-01T00:00:00Z",
        "et0_mm_dia": 3.0,
        "humedad_suelo_pct": 55.0,
    },
}


def _sector_block(detalle=None):
    """Minimal sector block with controllable detalle."""
    return {
        "score": 70,
        "insar_mm": 0.5,
        "detalle": detalle,
    }


def _full_sectores(**overrides):
    """All required sectors with sane defaults; overrides replace individual sector dicts."""
    base = {sk: {"score": 70, "sem": "verde", "detalle": "ok", "insar_mm": 0.5}
            for sk in ("estadio", "canchero", "solar", "poli", "sede", "piletas")}
    base.update(overrides)
    return base


def test_detalle_none_does_not_crash():
    """Regression: TypeError when detalle is None (fixed at line 177)."""
    vd = dict(_BASE_VD)
    vd["sectores"] = _full_sectores(estadio=_sector_block(detalle=None))
    errors, warnings = validate(vd)
    assert isinstance(errors, list)
    assert isinstance(warnings, list)


def test_detalle_empty_string_does_not_crash():
    vd = dict(_BASE_VD)
    vd["sectores"] = _full_sectores(estadio=_sector_block(detalle=""))
    errors, warnings = validate(vd)
    assert isinstance(errors, list)


def test_detalle_with_synthetic_insar_warns():
    vd = dict(_BASE_VD)
    vd["sectores"] = _full_sectores(
        estadio=_sector_block(detalle="InSAR: 0.85 mm de deformacion")
    )
    errors, warnings = validate(vd)
    assert any("0.85" in w for w in warnings), f"Expected synthetic InSAR warning, got: {warnings}"


def test_detalle_normal_value_no_warn():
    vd = dict(_BASE_VD)
    vd["sectores"] = _full_sectores(estadio=_sector_block(detalle="Deformacion estable 0.3 mm"))
    errors, warnings = validate(vd)
    synth_warns = [w for w in warnings if "sospechoso" in w]
    assert not synth_warns, f"Unexpected synthetic warning: {synth_warns}"
