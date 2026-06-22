"""
Tests de integridad del reporte canchero (gen_velez_canchero.py).

test_zones_ndvi_none_when_no_satellite:
    Verifica que zone['ndvi'] y zone['ndre'] sean null cuando el JSON no tiene
    datos de imagen óptica. Detecta regresiones del tipo "fallback numérico
    silencioso" (ej: NDVI 0.68, NDRE 0.42) que aparecen como datos reales.

test_smoke_png_extreme_data:
    Verifica que el PNG se genere sin crash ni error con datos satélite all-None
    y que el archivo tenga un tamaño razonable (> 500 KB). Detecta paneles rotos
    o renders parciales silenciosos.
"""
import copy, json, os, subprocess, sys
from pathlib import Path
import pytest

_GEN = Path(__file__).parent.parent / 'sports/clients/velez/gen_velez_canchero.py'
_VD  = Path(__file__).parent.parent.parent / 'velez/velez_data.json'

_SAT_FIELDS = ['ndvi', 'ndre', 'ccci', 'pct_baja_ndvi', 'focos_reales', 'ndvi_2d']


def _make_all_none_vd(base_vd: dict) -> dict:
    """Elimina todos los campos satélite del VD para simular ciclo sin imagen óptica."""
    vd = copy.deepcopy(base_vd)
    estadio = vd.get('sectores', {}).get('estadio', {})
    for k in _SAT_FIELDS:
        estadio.pop(k, None)
    for c in vd.get('sectores', {}).get('canchero', {}).get('canchas', []):
        for k in _SAT_FIELDS:
            c[k] = None
    for hm in vd.get('usuarios', {}).get('roger', {}).get('heatmaps', {}).values():
        for k in ['ndvi', 'ndre', 'ccci', 'ndvi_2d']:
            hm.pop(k, None)
    return vd


@pytest.fixture(scope='module')
def all_none_vd_path(tmp_path_factory):
    if not _VD.exists():
        pytest.skip('velez_data.json no encontrado')
    with open(_VD, encoding='utf-8') as f:
        base = json.load(f)
    p = tmp_path_factory.mktemp('vd') / 'vd_none.json'
    p.write_text(json.dumps(_make_all_none_vd(base)), encoding='utf-8')
    return p


def test_zones_ndvi_none_when_no_satellite(all_none_vd_path):
    """NDVI y NDRE son null en el zone dict cuando no hay imagen óptica."""
    result = subprocess.run(
        [sys.executable, str(_GEN)],
        env={**os.environ, 'FARO_VD_PATH': str(all_none_vd_path), 'FARO_DUMP_ZONES': '1'},
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, f'FARO_DUMP_ZONES falló:\n{result.stderr[:500]}'
    zones = json.loads(result.stdout)
    assert len(zones) == 13, f'Se esperaban 13 zonas, hay {len(zones)}'
    for z in zones:
        assert z['ndvi'] is None, (
            f"{z['name']}: ndvi={z['ndvi']} — fallback numérico silencioso detectado"
        )
        assert z['ndre'] is None, (
            f"{z['name']}: ndre={z['ndre']} — fallback numérico silencioso detectado"
        )


def test_smoke_png_extreme_data(all_none_vd_path, tmp_path):
    """El PNG se genera sin crash con datos satélite all-None y tiene tamaño razonable."""
    out = tmp_path / 'test_canchero_smoke.png'
    result = subprocess.run(
        [sys.executable, str(_GEN)],
        env={**os.environ,
             'FARO_VD_PATH': str(all_none_vd_path),
             'FARO_OUT_PATH': str(out)},
        capture_output=True, text=True, timeout=120,
    )
    assert result.returncode == 0, (
        f'gen_velez_canchero.py terminó con error:\n{result.stderr[:500]}'
    )
    assert out.exists(), 'PNG no fue creado'
    size = out.stat().st_size
    assert size > 500_000, (
        f'PNG demasiado chico ({size:,} bytes) — posible render roto o truncado'
    )
