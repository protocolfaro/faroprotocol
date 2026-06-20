from ndvi_real import CANCHA_BBOXES

_VO_CANCHAS = ["1fa","2fa","3fa","4fa","5fa","6fa","7fa","8fa","9fa","10fa","1fp","2fp"]

# Ituzaingó bbox: lon ≈ -58.70 a -58.74
_LON_ITUZA_MIN = -58.75
_LON_ITUZA_MAX = -58.70

# Liniers / Amalfitani zona: lon ≈ -58.50 a -58.55
_LON_LINIERS_EAST = -58.60


def test_vo_lon_ituzaingo():
    for cid in _VO_CANCHAS:
        bbox = CANCHA_BBOXES[cid]
        lon_c = (bbox[0] + bbox[2]) / 2
        assert _LON_ITUZA_MIN <= lon_c <= _LON_ITUZA_MAX, (
            f"{cid}: lon_centro={lon_c:.4f} fuera de rango Ituzaingó "
            f"[{_LON_ITUZA_MIN}, {_LON_ITUZA_MAX}]"
        )


def test_vo_not_liniers():
    for cid in _VO_CANCHAS:
        bbox = CANCHA_BBOXES[cid]
        lon_c = (bbox[0] + bbox[2]) / 2
        assert lon_c < _LON_LINIERS_EAST, (
            f"{cid}: lon_centro={lon_c:.4f} — parece zona Liniers/Amalfitani (contaminado)"
        )


def test_amalfitani_distinct_from_vo():
    amal = CANCHA_BBOXES["amalfitani"]
    lon_a = (amal[0] + amal[2]) / 2
    for cid in _VO_CANCHAS:
        bbox = CANCHA_BBOXES[cid]
        lon_c = (bbox[0] + bbox[2]) / 2
        assert abs(lon_c - lon_a) > 0.10, (
            f"{cid}: lon_centro={lon_c:.4f} demasiado cerca de amalfitani={lon_a:.4f}"
        )
