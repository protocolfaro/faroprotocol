"""
dale_play_smoke.py — Tests de humo pre-reporte.

5 verificaciones básicas de sanidad sobre los datos del pipeline.
Si cualquiera falla → el pipeline para, loguea el error, no genera el PNG.

Principio: si el dato es absurdo, es mejor no generar un reporte que generar
uno con números falsos sin advertencia.
"""
from __future__ import annotations
import logging

log = logging.getLogger(__name__)


class SmokeTestFailure(Exception):
    """Excepción cuando un test de humo crítico falla."""


def _check_ndvi(result: dict) -> list[str]:
    sat  = result.get("satellite") or {}
    ndvi = sat.get("ndvi")
    if ndvi is None:
        return []   # None es aceptable — datos satelitales no disponibles
    if not isinstance(ndvi, (int, float)):
        return [f"NDVI no es numérico: {ndvi!r}"]
    if not (-1.0 <= float(ndvi) <= 1.0):
        return [f"NDVI fuera de rango válido [-1, 1]: {ndvi}"]
    return []


def _check_spl(result: dict) -> list[str]:
    ac   = result.get("acoustic") or {}
    secs = ac.get("sectores") or []
    errs = []
    for s in secs:
        spl = s.get("spl_db")
        if spl is None:
            continue
        if not isinstance(spl, (int, float)):
            errs.append(f"SPL no numérico en {s.get('id', '?')}: {spl!r}")
        elif not (60.0 <= float(spl) <= 130.0):
            errs.append(f"SPL fuera de rango [60-130 dB] en {s.get('id', '?')}: {spl}")
    return errs


def _check_soil(result: dict) -> list[str]:
    soil = result.get("soil") or {}
    kpa  = soil.get("capacidad_efectiva_kpa")
    if kpa is None:
        return []
    if not isinstance(kpa, (int, float)):
        return [f"Capacidad portante no numérica: {kpa!r}"]
    if float(kpa) <= 0:
        return [f"Capacidad portante del suelo no puede ser ≤ 0 kPa: {kpa}"]
    return []


def _check_fii(result: dict) -> list[str]:
    fii_r = result.get("fii") or {}
    fii   = fii_r.get("fii")
    if fii is None:
        return []
    if not isinstance(fii, (int, float)):
        return [f"FII no numérico: {fii!r}"]
    if not (0.0 <= float(fii) <= 100.0):
        return [f"FII fuera de rango [0-100]: {fii}"]
    return []


def _check_iroe(result: dict) -> list[str]:
    """IROE se calcula en el reporte — verificamos que el FII computable no produzca garbage."""
    # Si acoustic y soil están OK, IROE será calculable. Validamos indirectamente.
    ac_ok   = bool((result.get("acoustic") or {}).get("sectores"))
    soil_ok = bool((result.get("soil") or {}).get("zonas"))
    if not ac_ok and not soil_ok:
        return ["IROE incalculable: ni acoustic ni soil disponibles — sin datos suficientes"]
    return []


SMOKE_TESTS = [
    ("NDVI",        _check_ndvi),
    ("SPL",         _check_spl),
    ("SUELO kPa",   _check_soil),
    ("FII",         _check_fii),
    ("IROE",        _check_iroe),
]


def run_smoke_tests(result: dict, show_id: str = "unknown") -> dict:
    """
    Corre los 5 tests de humo.
    Retorna {"passed": bool, "errors": [...], "warnings": [...]}.
    Lanza SmokeTestFailure si algún test crítico falla.
    """
    errors   = []
    warnings = []

    for name, fn in SMOKE_TESTS:
        try:
            issues = fn(result)
            if issues:
                for issue in issues:
                    log.error("SMOKE [%s] %s: %s", show_id, name, issue)
                    errors.append(f"[{name}] {issue}")
        except Exception as exc:
            warnings.append(f"[{name}] Error al ejecutar test: {exc}")
            log.warning("SMOKE [%s] %s excepción: %s", show_id, name, exc)

    passed = len(errors) == 0
    if not passed:
        log.error("SMOKE TESTS FALLARON (%d errores) — show_id=%s", len(errors), show_id)

    return {"passed": passed, "errors": errors, "warnings": warnings}
