"""
Tests para el filtro bbox_legacy — verifica que filas contaminadas se excluyen.

El patrón está implementado en:
  faro_eopatch.py  (build_eopatch, fallback path y section 2)
  field_timeseries.py  (compute_fenology)

Testeamos la lógica del filtro directamente sin I/O para mantenerlo puro.
"""


def _is_bbox_legacy(row: dict) -> bool:
    return str(row.get("fuente") or "").startswith("bbox_legacy")


def test_contaminated_rows_excluded():
    rows = [
        {"fuente": "bbox_legacy::kalman-seasonal · test-fixed", "ndvi": 0.107},
        {"fuente": "sentinel2_pc", "ndvi": 0.512},
        {"fuente": None, "ndvi": 0.430},
        {"fuente": "bbox_legacy::", "ndvi": 0.034},
        {"fuente": "hls_nasa", "ndvi": 0.580},
    ]
    clean = [r for r in rows if not _is_bbox_legacy(r)]
    ndvi_vals = [r["ndvi"] for r in clean]

    assert 0.107 not in ndvi_vals, "fila bbox_legacy::kalman-seasonal no fue filtrada"
    assert 0.034 not in ndvi_vals, "fila bbox_legacy:: no fue filtrada"
    assert 0.512 in ndvi_vals
    assert 0.430 in ndvi_vals
    assert 0.580 in ndvi_vals


def test_none_fuente_passes():
    assert not _is_bbox_legacy({"fuente": None, "ndvi": 0.43})


def test_empty_fuente_passes():
    assert not _is_bbox_legacy({"fuente": "", "ndvi": 0.43})


def test_partial_prefix_blocked():
    assert _is_bbox_legacy({"fuente": "bbox_legacy", "ndvi": 0.1})
    assert _is_bbox_legacy({"fuente": "bbox_legacy::anything", "ndvi": 0.1})


def test_clean_fuentes_pass():
    for fuente in ["sentinel2_pc", "hls_nasa", "landsat_e84", "kalman-seasonal"]:
        assert not _is_bbox_legacy({"fuente": fuente}), \
            f"fuente legítima '{fuente}' fue bloqueada incorrectamente"
