from faro_saocom import _van_genuchten_lband

_THETA_R = 0.040
_THETA_S = 0.420


def test_theta_physical_range():
    for db in [-22, -18, -14, -10, -6]:
        theta, h = _van_genuchten_lband(db)
        assert _THETA_R <= theta <= _THETA_S, (
            f"theta={theta:.4f} fuera de rango físico [{_THETA_R}, {_THETA_S}] "
            f"para sigma={db}dB"
        )


def test_h_suction_positive():
    for db in [-22, -15, -8, -5]:
        _, h = _van_genuchten_lband(db)
        assert h >= 0, f"h_suction={h} negativo para sigma={db}dB"


def test_theta_monotonic_with_backscatter():
    sigmas = [-22, -18, -14, -10, -6]
    thetas = [_van_genuchten_lband(db)[0] for db in sigmas]
    assert thetas == sorted(thetas), (
        f"theta debe crecer con backscatter más alto. Obtenido: {thetas}"
    )


def test_dry_end_low_theta():
    theta_dry, _ = _van_genuchten_lband(-22)
    assert theta_dry < (_THETA_R + _THETA_S) / 2, (
        f"Backscatter mínimo debería dar theta bajo, obtenido={theta_dry:.4f}"
    )
