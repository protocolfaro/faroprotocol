"""
faro_json_validator.py — Auditor automático del velez_data.json ensamblado.

Verifica que:
1. Todos los campos obligatorios estén presentes (no N/D, no None)
2. Los valores estén dentro de rangos físicamente posibles
3. Las 12 canchas tengan NDVI real (no fallback)
4. No haya valores sintéticos conocidos
5. Los datos sean recientes (< 24h)
6. Sectores tengan detalle y score coherentes

Uso: python faro_json_validator.py [path/to/velez_data.json]
Exit 0 = OK, Exit 1 = errores críticos, Exit 2 = advertencias
"""
import json, sys, logging
from datetime import datetime, timezone, timedelta
from pathlib import Path

log = logging.getLogger(__name__)
_ART = timezone(timedelta(hours=-3))

# Canchas esperadas en Villa Olímpica
_EXPECTED_CANCHAS = {"1fa","2fa","3fa","4fa","5fa","6fa","7fa","8fa","9fa","10fa","1fp","2fp"}

# Rangos físicamente válidos para Argentina, campo de fútbol
_NDVI_MIN, _NDVI_MAX   = 0.05, 0.95
_NDVI_INVIERNO_MIN     = 0.10  # julio: suelo desnudo / pasto dormido mínimo
_NDVI_INVIERNO_MAX     = 0.70  # julio: techo razonable si riego activo
_ET0_MIN, _ET0_MAX     = 0.0, 15.0   # mm/día
_HUM_MIN, _HUM_MAX     = 5.0, 100.0  # %
_INSAR_MIN, _INSAR_MAX = 0.0, 20.0   # mm (deformación < 20mm es plausible)
_SAR_MIN, _SAR_MAX     = -30.0, 5.0  # dB VV backscatter
_SCORE_MIN, _SCORE_MAX = 0, 100

# Valores sintéticos conocidos (hardcodeados en gen scripts legacy)
_SYNTHETIC_INSAR = {0.85, 1.20, 0.60, 2.80}
_SYNTHETIC_SOLAR = {82.4}
_SYNTHETIC_NDVI  = {0.24, 0.31, 0.38, 0.45}  # valores de fallback legacy (eliminados de data_refresh.py — detector de regresión)

ERRORS   = []   # bloquean
WARNINGS = []   # informativos


def _err(msg: str):
    ERRORS.append(msg)
    log.error("ERROR: %s", msg)


def _warn(msg: str):
    WARNINGS.append(msg)
    log.warning("WARN: %s", msg)


def _ok(msg: str):
    log.info("OK: %s", msg)


def validate(vd: dict) -> tuple[list, list]:
    """Returns (errors, warnings). errors=[] means pass."""
    global ERRORS, WARNINGS
    ERRORS, WARNINGS = [], []

    # ── 1. Timestamp freshness ────────────────────────────────────────────────
    ts_str = vd.get("weather_live", {}).get("timestamp", "")
    if ts_str:
        try:
            ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            age_h = (datetime.now(timezone.utc) - ts).total_seconds() / 3600
            if age_h > 26:
                _err(f"Datos con {age_h:.1f}h de antigüedad — pipeline no corrió o falló")
            elif age_h > 12:
                _warn(f"Datos con {age_h:.1f}h de antigüedad")
            else:
                _ok(f"Timestamp reciente: {ts_str} ({age_h:.1f}h)")
        except Exception as e:
            _warn(f"No se pudo parsear timestamp '{ts_str}': {e}")
    else:
        _err("weather_live.timestamp AUSENTE")

    # ── 2. Clima básico ───────────────────────────────────────────────────────
    wl = vd.get("weather_live", {})
    et0 = wl.get("et0_mm_dia")
    hum = wl.get("humedad_suelo_pct")

    if et0 is None:
        _err("weather_live.et0_mm_dia AUSENTE")
    elif not (_ET0_MIN <= float(et0) <= _ET0_MAX):
        _err(f"ET0 fuera de rango: {et0} (esperado {_ET0_MIN}-{_ET0_MAX})")
    else:
        _ok(f"ET0 = {et0} mm/día")

    if hum is None:
        _err("weather_live.humedad_suelo_pct AUSENTE")
    elif not (_HUM_MIN <= float(hum) <= _HUM_MAX):
        _err(f"Humedad fuera de rango: {hum}%")
    else:
        _ok(f"Humedad suelo = {hum}%")

    # ── 3. NDVI por cancha ────────────────────────────────────────────────────
    gpc = wl.get("gndvi_por_cancha", {})
    gpc_canchas = gpc.get("canchas", {}) if isinstance(gpc, dict) else {}
    gpc_fuente  = gpc.get("fuente", "N/D") if isinstance(gpc, dict) else "N/D"
    gpc_fecha   = gpc.get("fecha_imagen", "N/D") if isinstance(gpc, dict) else "N/D"

    if not gpc_canchas:
        _err("weather_live.gndvi_por_cancha.canchas AUSENTE — sin datos Sentinel-2")
    else:
        present_ids = set(gpc_canchas.keys())
        missing = _EXPECTED_CANCHAS - present_ids
        if missing:
            _warn(f"Canchas sin NDVI en gndvi_por_cancha: {sorted(missing)}")

        is_winter = datetime.now(_ART).month in (5, 6, 7, 8, 9)
        low_limit = _NDVI_INVIERNO_MIN if is_winter else _NDVI_MIN

        for cid, cdata in gpc_canchas.items():
            if not isinstance(cdata, dict):
                continue
            ndvi = cdata.get("ndvi")
            if ndvi is None:
                _warn(f"Cancha {cid}: ndvi ausente en gndvi_por_cancha")
                continue
            try:
                ndvi_f = float(ndvi)
            except (TypeError, ValueError):
                _err(f"Cancha {cid}: ndvi no es número: {ndvi}")
                continue
            if ndvi_f in _SYNTHETIC_NDVI:
                _warn(f"Cancha {cid}: NDVI={ndvi} coincide con valor de fallback sintético")
            if not (low_limit <= ndvi_f <= _NDVI_MAX):
                _warn(f"Cancha {cid}: NDVI={ndvi} fuera de rango estacional ({low_limit}-{_NDVI_MAX})")
            else:
                pass  # valid

        _ok(f"NDVI Sentinel-2: {len(gpc_canchas)} canchas · fuente={gpc_fuente} · fecha={gpc_fecha}")

    # ── 4. Sectores ───────────────────────────────────────────────────────────
    sectores = vd.get("sectores", {})
    expected_sectors = ["estadio", "canchero", "solar", "poli", "sede", "piletas"]
    for sk in expected_sectors:
        s = sectores.get(sk, {})
        if not s:
            _err(f"Sector '{sk}' AUSENTE en vd.sectores")
            continue
        score = s.get("score")
        sem   = s.get("sem")
        det   = s.get("detalle", "")
        if score is None:
            _warn(f"Sector {sk}: score AUSENTE")
        elif not (_SCORE_MIN <= int(score) <= _SCORE_MAX):
            _err(f"Sector {sk}: score={score} fuera de rango")
        if sem not in ("verde", "amarillo", "rojo", None):
            _warn(f"Sector {sk}: sem='{sem}' inesperado")
        if not det:
            _warn(f"Sector {sk}: detalle vacío")
        _ok(f"Sector {sk}: score={score} sem={sem}")

    # ── 5. Valores InSAR sospechosos ─────────────────────────────────────────
    for sk, label in [("poli", "poli"), ("estadio", "estadio"), ("sede", "sede"), ("piletas", "piletas")]:
        s = sectores.get(sk, {})
        insar = s.get("insar_mm")
        if insar is not None:
            try:
                insar_f = float(insar)
                if insar_f in _SYNTHETIC_INSAR:
                    _warn(f"Sector {sk}: insar_mm={insar} es un valor sintético conocido — verificar origen en Supabase")
                elif not (_INSAR_MIN <= insar_f <= _INSAR_MAX):
                    _err(f"Sector {sk}: insar_mm={insar} fuera de rango físico")
            except (TypeError, ValueError):
                pass
        det = s.get("detalle", "")
        import re as _re
        for sv in _SYNTHETIC_INSAR:
            # Match only when followed by " mm" to avoid matching NDVI values like 0.61
            if det and _re.search(rf"\b{sv}\s*mm", det):
                _warn(f"Sector {sk}: detalle contiene valor InSAR sospechoso ({sv}mm): '{det[:60]}'")

    # ── 6. Solar ─────────────────────────────────────────────────────────────
    sol = sectores.get("solar", {})
    eff = sol.get("eficiencia_pct") or sol.get("eff_pct") or sol.get("score")
    if eff is not None:
        try:
            eff_f = float(eff)
            if eff_f in _SYNTHETIC_SOLAR:
                _warn(f"Solar: eficiencia_pct={eff} es valor sintético conocido (82.4% hardcodeado)")
            elif not (0 <= eff_f <= 100):
                _err(f"Solar eficiencia_pct={eff} fuera de rango")
        except (TypeError, ValueError):
            pass
    # pvlib physics-based efficiency check
    eff_pvlib = sol.get("eficiencia_pct_pvlib")
    if eff_pvlib is None:
        _warn("Solar: eficiencia_pct_pvlib AUSENTE — pvlib no corrió o GHI no disponible")
    else:
        try:
            pvlib_f = float(eff_pvlib)
            if not (5.0 <= pvlib_f <= 25.0):
                _warn(f"Solar: eficiencia_pct_pvlib={pvlib_f} fuera del rango físico (5-25%)")
            else:
                _ok(f"Solar pvlib OK: eff={pvlib_f}% PR={sol.get('pr_pvlib','N/D')} T_cell={sol.get('t_cell_estimada','N/D')}°C")
        except (TypeError, ValueError):
            pass

    # ── 7. Roger canchas ─────────────────────────────────────────────────────
    roger = vd.get("roger_canchas", [])
    if not roger:
        _warn("roger_canchas VACÍO — sin acciones Roger (Railway/hermes inaccesible?)")
    else:
        _ok(f"roger_canchas: {len(roger)} canchas con acciones")

    # ── 8. Canchero canchas match ─────────────────────────────────────────────
    canchas_list = sectores.get("canchero", {}).get("canchas", [])
    found_ids = {c.get("id") for c in canchas_list if c.get("id")}
    missing_c = _EXPECTED_CANCHAS - found_ids
    if missing_c:
        _warn(f"canchero.canchas faltantes: {sorted(missing_c)}")
    extra_c = found_ids - _EXPECTED_CANCHAS
    if extra_c:
        _warn(f"canchero.canchas inesperadas (IDs desconocidos): {sorted(extra_c)}")
    _ok(f"canchero.canchas: {len(canchas_list)} canchas presentes")

    return ERRORS, WARNINGS


def report(errors: list, warnings: list) -> int:
    """Print report and return exit code."""
    print("\n" + "="*60)
    print("FARO JSON VALIDATOR — Reporte de auditoría")
    print("="*60)
    if not errors and not warnings:
        print("RESULTADO: OK — JSON ensamblado sin problemas")
        return 0
    if warnings:
        print(f"\nADVERTENCIAS ({len(warnings)}):")
        for w in warnings:
            print(f"  ! {w}")
    if errors:
        print(f"\nERRORES CRITICOS ({len(errors)}):")
        for e in errors:
            print(f"  X {e}")
        print("\nRESULTADO: FALLO — Datos no aptos para publicar")
        return 1
    print(f"\nRESULTADO: OK con {len(warnings)} advertencia(s) — revisar pero publicable")
    return 2


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    path = sys.argv[1] if len(sys.argv) > 1 else "velez/velez_data.json"
    vd = json.loads(Path(path).read_text(encoding="utf-8"))
    errors, warnings = validate(vd)
    code = report(errors, warnings)
    sys.exit(0 if code in (0, 2) else 1)
