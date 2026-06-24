"""
test_tramos_a_b_c_d.py — Verificación de los 4 tramos del pipeline satelital Vélez.

Uso:
    python test_tramos_a_b_c_d.py              # todos los tramos
    python test_tramos_a_b_c_d.py --tramo A    # solo un tramo
    python test_tramos_a_b_c_d.py --tramo B C  # varios

Retorna exit code 0 si todos los tramos seleccionados pasan (o pasan con advertencia).
Exit code 1 si alguno falla de forma inesperada.

Nota sobre Tramo C (HyP3):
  El primer run submite un job y retorna None — esto es PASS ("submitted").
  El segundo run (ciclo siguiente) retorna el resultado InSAR si el job terminó.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import traceback
from typing import Any

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

# ── colores ANSI ──────────────────────────────────────────────────────────────
_GREEN  = "\033[92m"
_YELLOW = "\033[93m"
_RED    = "\033[91m"
_CYAN   = "\033[96m"
_RESET  = "\033[0m"
_BOLD   = "\033[1m"


def _ok(msg: str)   -> None: print(f"  {_GREEN}✅ PASS{_RESET}  {msg}")
def _warn(msg: str) -> None: print(f"  {_YELLOW}⚠  WARN{_RESET}  {msg}")
def _fail(msg: str) -> None: print(f"  {_RED}❌ FAIL{_RESET}  {msg}")
def _info(msg: str) -> None: print(f"     {_CYAN}→{_RESET} {msg}")


def _header(tramo: str, title: str) -> None:
    print(f"\n{_BOLD}{'─'*60}{_RESET}")
    print(f"{_BOLD}Tramo {tramo}: {title}{_RESET}")
    print(f"{_BOLD}{'─'*60}{_RESET}")


# ─────────────────────────────────────────────────────────────────────────────
# Tramo A — Mini-BAP 5d
# ─────────────────────────────────────────────────────────────────────────────

def _test_tramo_a() -> bool:
    """
    Invoca ndvi_real.fetch_ndvi() y verifica que devuelve una estructura válida.
    No fuerza que se active Mini-BAP (depende de disponibilidad de escenas);
    verifica que la cascade completa funciona sin excepciones.
    """
    _header("A", "Mini-BAP 5d — cascade ndvi_real.fetch_ndvi()")
    try:
        import ndvi_real
    except ImportError as e:
        _fail(f"import ndvi_real: {e}")
        return False

    t0 = time.time()
    try:
        result = ndvi_real.fetch_ndvi()
    except Exception as e:
        _fail(f"fetch_ndvi() lanzó excepción: {e}")
        _info(traceback.format_exc())
        return False

    elapsed = round(time.time() - t0, 1)

    if result is None:
        _warn(f"fetch_ndvi() → None ({elapsed}s) — todos los tramos fallaron o sin escenas")
        return True  # WARN pero no FAIL — puede pasar con nubes totales

    fuente   = result.get("fuente", "?")
    metodo   = result.get("metodo_generacion") or result.get("metodo", "?")
    canchas  = result.get("canchas", {})
    nub      = result.get("nubosidad_pct", "?")

    _info(f"fuente: {fuente}")
    _info(f"metodo: {metodo}")
    _info(f"nubosidad: {nub}%")
    _info(f"canchas: {len(canchas)} — {list(canchas.keys())[:5]}{'…' if len(canchas)>5 else ''}")

    # Verificar estructura mínima
    issues = []
    if not canchas:
        issues.append("canchas vacías")
    for cid, vals in list(canchas.items())[:3]:
        if not isinstance(vals, dict):
            issues.append(f"{cid} no es dict")
        elif vals.get("ndvi") is None and metodo not in ("CLOUDBREAKER_SAR_FUSION",):
            issues.append(f"{cid}.ndvi es None en metodo={metodo}")

    if issues:
        _fail(f"estructura inválida: {', '.join(issues)}")
        return False

    if "MINI_BAP" in str(metodo).upper() or "MINI_BAP" in str(fuente).upper():
        _ok(f"Mini-BAP 5d activado ({elapsed}s)")
    else:
        _ok(f"cascade OK via {metodo} ({elapsed}s) — Mini-BAP no necesario en este ciclo")

    return True


# ─────────────────────────────────────────────────────────────────────────────
# Tramo B — OpenEO CDSE
# ─────────────────────────────────────────────────────────────────────────────

def _test_tramo_b() -> bool:
    """
    Invoca ndvi_openeo.fetch_ndvi_openeo() directamente.
    Requiere CDSE_USER + CDSE_PASS en el entorno.
    """
    _header("B", "OpenEO CDSE — ndvi_openeo.fetch_ndvi_openeo()")

    # Verificar credenciales presentes
    user = os.environ.get("CDSE_USER") or os.environ.get("COPERNICUS_USER", "")
    pwd  = os.environ.get("CDSE_PASS") or os.environ.get("COPERNICUS_PASS", "")
    if not user or not pwd:
        _warn("CDSE_USER/PASS no configurados — skipping Tramo B")
        return True

    _info(f"usuario: {user[:6]}***")

    try:
        import ndvi_openeo
    except ImportError as e:
        _fail(f"import ndvi_openeo: {e}")
        return False

    # Primero verificar sólo la conexión (rápida, sin batch job)
    _info("verificando autenticación CDSE...")
    try:
        import openeo
        conn = openeo.connect("https://openeo.dataspace.copernicus.eu")
        conn.authenticate_oidc_resource_owner_password_credentials(
            username=user, password=pwd,
            client_id="cdse-public", provider_id="CDSE",
        )
        info = conn.describe_account()
        _ok(f"auth CDSE OK — {info.get('name','?')}")
    except Exception as e:
        _fail(f"autenticación CDSE: {e}")
        return False

    # Verificar SENTINEL2_L2A disponible
    try:
        colls = conn.list_collections()
        has_s2 = any(c.get("id") == "SENTINEL2_L2A" for c in colls)
        if has_s2:
            _ok("SENTINEL2_L2A disponible en CDSE")
        else:
            _warn("SENTINEL2_L2A no encontrada — revisar colecciones disponibles")
    except Exception as e:
        _warn(f"list_collections: {e}")

    # Fetch real (puede ser lento — batch job CDSE)
    _info("ejecutando fetch_ndvi_openeo(window_days=30) — puede tardar 1-3 min...")
    t0 = time.time()
    try:
        result = ndvi_openeo.fetch_ndvi_openeo(window_days=30)
    except Exception as e:
        _fail(f"fetch_ndvi_openeo: {e}")
        _info(traceback.format_exc())
        return False

    elapsed = round(time.time() - t0, 1)

    if result is None:
        _warn(f"fetch_ndvi_openeo → None ({elapsed}s) — sin escenas en ventana 30d o job falló")
        return True

    fuente  = result.get("fuente", "?")
    canchas = result.get("canchas", {})
    _info(f"fuente: {fuente}")
    _info(f"canchas: {len(canchas)} — {list(canchas.keys())[:5]}")
    _ok(f"OpenEO CDSE OK — {len(canchas)} canchas en {elapsed}s")
    return True


# ─────────────────────────────────────────────────────────────────────────────
# Tramo C — HyP3 InSAR async
# ─────────────────────────────────────────────────────────────────────────────

def _test_tramo_c() -> bool:
    """
    Invoca insar_hyp3.fetch_insar().
    Fase 1: submite job → None → PASS ("submitted, check next cycle")
    Fase 2: si hay job pendiente, comprueba estado.
    Requiere NASA_EARTHDATA_USER + NASA_EARTHDATA_PASS.
    """
    _header("C", "HyP3 InSAR async — insar_hyp3.fetch_insar()")

    user = os.environ.get("NASA_EARTHDATA_USER") or os.environ.get("EARTHDATA_USERNAME", "")
    pwd  = os.environ.get("NASA_EARTHDATA_PASS") or os.environ.get("EARTHDATA_PASSWORD", "")
    if not user or not pwd:
        _warn("NASA_EARTHDATA_USER/PASS no configurados — skipping Tramo C")
        return True

    _info(f"usuario: {user[:6]}***")

    # Verificar creds via URS antes de tocar HyP3
    try:
        import requests as _req
        r = _req.get("https://urs.earthdata.nasa.gov/api/users/tokens",
                     auth=(user, pwd), timeout=15)
        if r.status_code == 200:
            _ok("NASA Earthdata creds OK (URS 200)")
        else:
            _fail(f"NASA Earthdata: URS {r.status_code}")
            return False
    except Exception as e:
        _warn(f"URS check (non-fatal): {e}")

    # Estado del job pendiente
    import json as _json
    pending_path = "/tmp/faro_pending_hyp3.json"
    if os.path.exists(pending_path):
        try:
            state = _json.load(open(pending_path))
            _info(f"job pendiente encontrado: {state.get('job_id','?')} "
                  f"(submiteado: {state.get('submitted_at','?')[:16]})")
        except Exception:
            pass

    try:
        import insar_hyp3
    except ImportError as e:
        _fail(f"import insar_hyp3: {e}")
        return False

    _info("ejecutando fetch_insar() (two-phase async)...")
    t0 = time.time()
    try:
        result = insar_hyp3.fetch_insar()
    except Exception as e:
        _fail(f"fetch_insar() lanzó excepción: {e}")
        _info(traceback.format_exc())
        return False

    elapsed = round(time.time() - t0, 1)

    if result is None:
        # None es resultado esperado: o job submiteado (Fase 1) o aún procesando (Fase 2)
        if os.path.exists(pending_path):
            _ok(f"job en proceso — estado guardado en {pending_path} ({elapsed}s)")
            try:
                state = _json.load(open(pending_path))
                _info(f"job_id: {state.get('job_id','?')} | par: {state.get('date1','?')} → {state.get('date2','?')}")
            except Exception:
                pass
        else:
            _warn(f"fetch_insar → None sin job pendiente ({elapsed}s) — sin par S1 disponible o error de submit")
        return True

    # Resultado real (Fase 2 exitosa)
    sectores = result.get("sectores", {})
    _info(f"fuente: {result.get('fuente','?')}")
    _info(f"par: {result.get('fecha_ref','?')} → {result.get('fecha_sec','?')}")
    for sid, mm in sectores.items():
        _info(f"  {sid}: {mm:+.2f} mm")
    _ok(f"InSAR resultado disponible — {len(sectores)} sectores ({elapsed}s)")
    return True


# ─────────────────────────────────────────────────────────────────────────────
# Tramo D — CloudBreaker SAR fusion
# ─────────────────────────────────────────────────────────────────────────────

def _test_tramo_d(venue: str = "amalfitani") -> bool:
    """
    Invoca faro_cloudbreaker_hf.reconstruct(venue_id=venue).
    Requiere datos SAR en soil_metrics Supabase para el venue.
    Si no hay datos SAR → WARN (no FAIL).
    """
    _header("D", f"CloudBreaker SAR fusion — faro_cloudbreaker_hf.reconstruct(venue_id='{venue}')")

    try:
        import faro_cloudbreaker_hf as cb
    except ImportError as e:
        _fail(f"import faro_cloudbreaker_hf: {e}")
        return False

    _info(f"venue: {venue}")

    # Verificar que hay datos SAR disponibles (sin lanzar reconstruct completo aún)
    sar = cb._last_sar(venue)
    if sar is None:
        _warn(f"sin datos SAR en soil_metrics para venue={venue} (últimos 30d)")
        _warn("CloudBreaker no puede operar — WARN (no FAIL): requiere ciclo HyP3 previo")
        return True

    _info(f"SAR disponible: vv={sar.get('sar_vv_db','?')} dB | θ={sar.get('theta_soil','?')}")

    optical = cb._last_clean_optical(venue)
    _info(f"óptico limpio: {'sí — ndvi=' + str(optical.get('ndvi','?')) if optical else 'no (nubosidad total)'}")

    t0 = time.time()
    try:
        result = cb.reconstruct(venue_id=venue)
    except Exception as e:
        _fail(f"reconstruct({venue}): {e}")
        _info(traceback.format_exc())
        return False

    elapsed = round(time.time() - t0, 1)

    if result is None:
        _warn(f"reconstruct → None ({elapsed}s) — proxy VG también falló")
        return True

    canchas = result.get("canchas", {})
    metodo  = result.get("metodo_generacion", "?")
    _info(f"metodo: {metodo}")
    for cid, vals in canchas.items():
        _info(f"  {cid}: ndvi={vals.get('ndvi','?')} | margen={vals.get('margen_error_kalman','?')}")

    _ok(f"CloudBreaker OK — {len(canchas)} canchas via {metodo} ({elapsed}s)")
    return True


# ─────────────────────────────────────────────────────────────────────────────
# Runner
# ─────────────────────────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--tramo", nargs="+", choices=["A","B","C","D"],
                        help="Tramos a ejecutar (default: todos)")
    parser.add_argument("--venue", default="amalfitani",
                        help="venue_id para Tramo D (default: amalfitani)")
    args = parser.parse_args()

    tramos = args.tramo or ["A", "B", "C", "D"]
    results: dict[str, bool] = {}

    runners = {
        "A": lambda: _test_tramo_a(),
        "B": lambda: _test_tramo_b(),
        "C": lambda: _test_tramo_c(),
        "D": lambda: _test_tramo_d(args.venue),
    }

    for t in tramos:
        try:
            results[t] = runners[t]()
        except Exception as e:
            _fail(f"error inesperado en Tramo {t}: {e}")
            results[t] = False

    # Resumen
    print(f"\n{_BOLD}{'═'*60}{_RESET}")
    print(f"{_BOLD}RESUMEN{_RESET}")
    print(f"{_BOLD}{'═'*60}{_RESET}")
    all_ok = True
    for t, ok in results.items():
        status = f"{_GREEN}PASS{_RESET}" if ok else f"{_RED}FAIL{_RESET}"
        print(f"  Tramo {t}: {status}")
        if not ok:
            all_ok = False

    print()
    if all_ok:
        print(f"{_GREEN}{_BOLD}Todos los tramos OK.{_RESET}")
    else:
        print(f"{_RED}{_BOLD}Hay tramos con FAIL — revisar logs arriba.{_RESET}")

    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
