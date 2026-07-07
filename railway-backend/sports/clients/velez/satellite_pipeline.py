"""
satellite_pipeline.py — Pipeline satelital autónomo · Faro Protocol · Vélez Sarsfield

Flujo completo sin intervención manual:
  1. ndvi_real.fetch_ndvi()         → NDVI real Sentinel-2 para TODAS las canchas
  2. _load_last_ipos()              → últimos datos de uso desde config_velez.json
  3. heatmap_gen.generate_all()     → PNGs con NDVI real
  4. github_push.push_heatmaps()   → sube PNGs a velez/heatmaps/
  5. _push_heatmap_ndvi_update()   → actualiza velez_data.json (NDVI + semana)
  6. push_historial_snapshot()      → guarda snapshot semanal en historial/YYYY-MM-DD.json
  7. historico.write_weekly_snapshot() → fila CSV histórico local

Se llama desde:
  - data_refresh.run_refresh()     → diario 09:00 UTC si hay nueva imagen
  - velez_scheduler.run_weekly_job() → lunes 10:00 UTC como fallback garantizado
"""
from __future__ import annotations
import base64, json, logging, sys, os
from datetime import date, datetime, timezone

import requests

# ── Motor compartido Faro Sports ──────────────────────────────────────────
# Importar desde sports/modules/ — mejoras acá se propagan a todos los clientes
_SPORTS_MODULES = os.path.join(os.path.dirname(__file__), '../../..')
if _SPORTS_MODULES not in sys.path:
    sys.path.insert(0, _SPORTS_MODULES)

try:
    from sports.modules.faro_modis       import fetch_modis_ndvi
    from sports.modules.faro_starfm      import compute_starfm_ndvi
    from sports.modules.faro_monitor     import run_velez_monitor
    from sports.modules.faro_autocorrect import record_module_result, get_degraded_modules
    from sports.modules.faro_source_scout import run_source_scout
    _MOTOR_OK = True
    logging.getLogger(__name__).info("motor sports/modules: OK")
except ImportError as _e:
    _MOTOR_OK = False
    logging.getLogger(__name__).warning("motor sports/modules: %s — modo standalone", _e)
    # Fallbacks no-op para que el pipeline siga corriendo
    def fetch_modis_ndvi(*a, **k): return None
    def compute_starfm_ndvi(*a, **k): return None
    def run_velez_monitor(*a, **k): return {}
    def record_module_result(*a, **k): pass
    def get_degraded_modules(*a, **k): return []
    def run_source_scout(*a, **k): return {}

# ── baseline fenológico del core ──────────────────────────────────────────
try:
    _CORE = os.path.join(os.path.dirname(__file__), '../../../core/satellite')
    if _CORE not in sys.path:
        sys.path.insert(0, _CORE)
    from field_timeseries import detect_anomaly, get_baseline
    _BASELINE_OK = True
    logging.getLogger(__name__).info("core/field_timeseries: OK")
except ImportError as _e2:
    _BASELINE_OK = False
    logging.getLogger(__name__).warning("core/field_timeseries: %s", _e2)
    def detect_anomaly(*a, **k): return None
    def get_baseline(*a, **k): return None

log = logging.getLogger(__name__)

_OWNER  = "protocolfaro"

# ── Supabase observability ────────────────────────────────────────────────────

def _record_run(
    ts_utc: str,
    fecha_imagen: str | None,
    ndvi_median: float | None,
    accepted: bool,
    canchas: int | None = None,
    skipped_reason: str | None = None,
    error: str | None = None,
) -> None:
    """Insert one row into pipeline_runs. Non-blocking, silent on failure."""
    try:
        import velez_supabase as _vs
        _vs.insert_pipeline_run(
            timestamp_utc=ts_utc,
            fecha_imagen=fecha_imagen,
            ndvi_median=ndvi_median,
            accepted=accepted,
            canchas_procesadas=canchas,
            skipped_reason=skipped_reason,
            error=error,
        )
    except Exception as exc:
        log.debug("pipeline_runs record failed (non-fatal): %s", exc)

_REPO   = "faroprotocol"
_BRANCH = "main"
_VD_PATH = "velez/velez_data.json"
_CFG_PATH = "velez/config_velez.json"


def _gh_hdrs():
    import os
    token = os.environ.get("GITHUB_TOKEN", "")
    if not token:
        raise EnvironmentError("GITHUB_TOKEN not set")
    return {"Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28"}


def _gh_get(path: str) -> tuple[str | None, dict]:
    r = requests.get(f"https://api.github.com/repos/{_OWNER}/{_REPO}/contents/{path}",
                     headers=_gh_hdrs(), params={"ref": _BRANCH}, timeout=15)
    if r.status_code == 200:
        d = r.json()
        return d["sha"], json.loads(base64.b64decode(d["content"]).decode())
    return None, {}


def _load_last_ipos() -> dict:
    """Carga el último resultado IPOS desde config_velez.json (canchas VO)."""
    try:
        _, cfg = _gh_get(_CFG_PATH)
        canchas = cfg.get("ipos_semana", {}).get("canchas", {})
        if canchas:
            log.info("satellite_pipeline: IPOS cargado — %d canchas", len(canchas))
            return canchas
    except Exception as e:
        log.warning("satellite_pipeline: _load_last_ipos: %s", e)
    return {}


def _neutral_ipos(cid: str, detalle: str = "") -> dict:
    return {"score": 0, "semaforo": "verde", "icono": "🟢",
            "texto": "Sin actividad registrada", "personas": 0, "horas": 0, "detalle": detalle}


def _build_ipos_results(last_ipos: dict) -> dict:
    """Combina último IPOS de canchas VO con entradas estáticas para Amalf y Poli."""
    result = dict(last_ipos)
    statics = {
        "amalfitani": _neutral_ipos("amalfitani", "Amalfitani — Campo Principal"),
        "poli_f11":   _neutral_ipos("poli_f11",   "Fútbol 11 — Polideportivo Feijóo"),
        "poli_f8a":   _neutral_ipos("poli_f8a",   "Fútbol 8A — Polideportivo Feijóo"),
        "poli_f8b":   _neutral_ipos("poli_f8b",   "Fútbol 8B — Polideportivo Feijóo"),
        "poli_hockey":_neutral_ipos("poli_hockey", "Hockey — Polideportivo Feijóo"),
    }
    for k, v in statics.items():
        result.setdefault(k, v)
    # Asegurar que canchas VO sin IPOS esta semana tengan entrada neutral
    from heatmap_gen import DIMS
    for cid in DIMS:
        result.setdefault(cid, _neutral_ipos(cid))
    return result


def _push_heatmap_ndvi_update(ndvi_data: dict) -> str:
    """
    Writes cancha NDVI to Supabase velez_canchas (primary).
    Always updates heatmaps_meta (semana/fuente) in GitHub velez_data.json so that
    _last_processed_date() keeps working. Falls back to full GitHub write if Supabase
    not configured (no SHA conflict risk since only satellite_pipeline calls this).
    """
    ts       = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    img_date = ndvi_data.get("fecha_imagen", date.today().isoformat())
    fuente   = ndvi_data.get("fuente", "sentinel-2-l2a · Planetary Computer")
    canchas  = ndvi_data.get("canchas", {})

    # Primary: Supabase UPSERT for per-cancha spectral data
    supabase_ok = False
    try:
        import velez_supabase as _vs
        if _vs._ok():
            supabase_ok = _vs.upsert_canchas(canchas, fuente=fuente)
            if supabase_ok:
                log.info("_push_heatmap_ndvi_update: %d canchas → Supabase OK", len(canchas))
    except Exception as _se:
        log.warning("_push_heatmap_ndvi_update Supabase (non-fatal): %s", _se)

    sha, vd = _gh_get(_VD_PATH)
    if not sha:
        raise RuntimeError("No se pudo leer velez_data.json")

    roger = vd.setdefault("usuarios", {}).setdefault("roger", {})

    if not supabase_ok:
        # Supabase not configured — also write per-cancha NDVI to GitHub heatmaps section
        hm = roger.setdefault("heatmaps", {})
        for cid, cdata in canchas.items():
            ndvi = cdata.get("ndvi")
            if ndvi is None:
                continue
            if cid in hm:
                hm[cid]["ndvi"] = ndvi
                if cdata.get("gndvi") is not None:
                    hm[cid]["gndvi"]    = cdata["gndvi"]
                    hm[cid]["n_status"] = cdata.get("n_status", "")
                    hm[cid]["n_rec"]    = cdata.get("n_rec", "")
                if cdata.get("bsi") is not None:
                    hm[cid]["bsi"] = cdata["bsi"]
                if cdata.get("ndwi") is not None:
                    hm[cid]["ndwi"] = cdata["ndwi"]
            else:
                entry: dict = {"archivo": f"heatmaps/heatmap_{cid}.png",
                               "ndvi": ndvi, "detalle": ""}
                if cdata.get("gndvi") is not None:
                    entry["gndvi"]    = cdata["gndvi"]
                    entry["n_status"] = cdata.get("n_status", "")
                    entry["n_rec"]    = cdata.get("n_rec", "")
                if cdata.get("bsi") is not None:
                    entry["bsi"] = cdata["bsi"]
                if cdata.get("ndwi") is not None:
                    entry["ndwi"] = cdata["ndwi"]
                hm[cid] = entry

    # Always write NDVI + derived Nivel-2 metrics to GitHub heatmaps regardless of Supabase.
    # push_velez_data() reads ndvi from the GitHub JSON, so it must be current here.
    _hm2 = roger.setdefault("heatmaps", {})
    for _cid2, _cd2 in canchas.items():
        _e2 = _hm2.setdefault(_cid2, {})
        if _cd2.get("ndvi") is not None:
            _e2["ndvi"] = round(float(_cd2["ndvi"]), 4)
        if _cd2.get("gndvi") is not None:
            _e2["gndvi"] = round(float(_cd2["gndvi"]), 4)
        if _cd2.get("ndre") is not None:
            _e2["ndre"] = round(float(_cd2["ndre"]), 3)
        if _cd2.get("ccci") is not None:
            _e2["ccci"] = round(float(_cd2["ccci"]), 4)
        _ndvi2d = _cd2.get("ndvi_2d") or []
        if _ndvi2d:
            _flat = [v for row in _ndvi2d for v in row if v > 0.10]
            _pct  = round(sum(1 for v in _flat if v < 0.30) / len(_flat) * 100) if _flat else 0
            _e2["pct_baja_ndvi"] = _pct
            _low_px = sorted(
                (v, r_i, c_i)
                for r_i, row in enumerate(_ndvi2d)
                for c_i, v in enumerate(row)
                if 0.10 < v < 0.30
            )
            _nrows = len(_ndvi2d)
            _ncols = len(_ndvi2d[0]) if _ndvi2d else 1
            _e2["focos_reales"] = [
                {"x": round((c_i + .5) / _ncols, 2),
                 "y": round(1.0 - (r_i + .5) / _nrows, 2),
                 "ndvi": round(v, 3)}
                for v, r_i, c_i in _low_px[:3]
            ]
            # Persiste la matriz 2D completa para el panel heatmap de gen_velez_canchero.py.
            # Ya viene como lista-de-listas float16 normalizada [0,1] desde ndvi_real.py.
            _e2["ndvi_2d"] = _ndvi2d

    # Always write heatmaps_meta (image date + source) to GitHub for _last_processed_date()
    roger.setdefault("heatmaps_meta", {}).update({
        "semana":     img_date,
        "fuente":     fuente,
        "updated_at": ts,
    })
    vd["updated_at"] = ts

    payload = {
        "message": f"satellite: NDVI {img_date} · {len(canchas)} canchas · Sentinel-2 [{ts}]",
        "content": base64.b64encode(
            json.dumps(vd, ensure_ascii=False, separators=(",", ":")).encode()
        ).decode(),
        "branch": _BRANCH,
        "sha": sha,
    }
    r = requests.put(
        f"https://api.github.com/repos/{_OWNER}/{_REPO}/contents/{_VD_PATH}",
        headers=_gh_hdrs(), json=payload, timeout=35
    )
    r.raise_for_status()
    return r.json().get("commit", {}).get("html_url", "")


def _last_processed_date() -> str:
    """Lee la fecha de la última imagen Sentinel-2 procesada desde velez_data.json."""
    try:
        _, vd = _gh_get(_VD_PATH)
        return vd.get("usuarios", {}).get("roger", {}).get("heatmaps_meta", {}).get("semana", "")
    except Exception:
        return ""


def run_satellite_cycle(ndvi_data: dict = None, force: bool = False) -> dict:
    """
    Pipeline satelital autónomo completo.

    Args:
        ndvi_data: resultado ya obtenido de ndvi_real.fetch_ndvi() (evita doble fetch).
                   Si None, se llama fetch_ndvi() internamente.
        force:     si True, ejecuta aunque la fecha de imagen sea igual a la última procesada.

    Returns: dict con resultado del ciclo.
    """
    _run_ts = datetime.now(timezone.utc).isoformat()

    # 1. Obtener NDVI real si no fue pasado
    if ndvi_data is None:
        try:
            import ndvi_real
            ndvi_data = ndvi_real.fetch_ndvi()
        except Exception as e:
            log.warning("satellite_pipeline: fetch_ndvi falló: %s", e)
            _record_run(_run_ts, None, None, False, error=str(e))
            return {"ok": False, "error": str(e)}

    if not ndvi_data:
        log.info("satellite_pipeline: sin imagen limpia disponible — ciclo omitido")
        _record_run(_run_ts, None, None, False, skipped_reason="no_new_image")
        return {"ok": True, "skipped": True, "reason": "no_new_image"}

    img_date = ndvi_data.get("fecha_imagen", "?")

    # 2. Sanity check: rechazar imágenes con NDVI mediano anómalamente bajo.
    # El umbral es ESTACIONAL — en invierno (Jun-Ago) la bermuda/kikuyo entra en dormancia
    # y NDVI 0.04-0.10 es FISIOLÓGICAMENTE NORMAL, no un artefacto.
    # Las fuentes sintéticas (Kalman) ya aplican el modelo estacional — no se rechazan.
    _ndvi_vals = sorted(
        v["ndvi"] for v in ndvi_data.get("canchas", {}).values()
        if isinstance(v.get("ndvi"), (int, float))
    )
    _median_ndvi  = _ndvi_vals[len(_ndvi_vals) // 2] if len(_ndvi_vals) >= 8 else None
    _is_synthetic = "kalman" in str(ndvi_data.get("fuente", "")).lower()
    _m            = date.today().month
    _ndvi_floor   = (0.04 if _m in (6, 7, 8) else 0.05 if _m in (5, 9) else 0.10)

    if not _is_synthetic and _median_ndvi is not None and _median_ndvi < _ndvi_floor:
        log.warning(
            "satellite_pipeline: imagen %s RECHAZADA — NDVI mediano %.3f < %.2f "
            "(mes %d, artefacto probable: niebla/sombra no detectada) — "
            "manteniendo último ciclo válido",
            img_date, _median_ndvi, _ndvi_floor, _m,
        )
        _record_run(_run_ts, img_date, _median_ndvi, False, skipped_reason="ndvi_anomaly")
        return {
            "ok": True, "skipped": True, "reason": "ndvi_anomaly",
            "fecha_imagen": img_date, "median_ndvi": _median_ndvi,
        }

    # 3. Verificar si esta imagen ya fue procesada (usar >= para no re-procesar misma fecha).
    # Las fuentes sintéticas (Kalman) generan un valor nuevo cada día — siempre procesar.
    if not force and not _is_synthetic:
        last = _last_processed_date()
        if last and last >= img_date:
            log.info("satellite_pipeline: imagen %s ya procesada (última: %s) — omitido", img_date, last)
            _record_run(_run_ts, img_date, _median_ndvi, False, skipped_reason="already_processed")
            return {"ok": True, "skipped": True, "reason": "already_processed", "fecha_imagen": img_date}

    log.info("=== satellite_pipeline START — imagen %s ===", img_date)

    # 4. Construir ndvi_map y ipos_results
    ndvi_map = {cid: v for cid, v in ndvi_data.get("canchas", {}).items()
                if v.get("ndvi") is not None}
    last_ipos   = _load_last_ipos()
    ipos_results = _build_ipos_results(last_ipos)

    # 4. Generar PNGs con NDVI real
    try:
        import heatmap_gen
        png_bytes, verify_hashes = heatmap_gen.generate_all(ipos_results, img_date, ndvi_map=ndvi_map)
        log.info("satellite_pipeline: %d PNGs generados", len(png_bytes))
    except Exception as e:
        log.error("satellite_pipeline: heatmap_gen falló: %s", e)
        return {"ok": False, "error": f"heatmap_gen: {e}"}

    # 5. Subir PNGs a GitHub
    hm_urls = {}
    _push_error = None
    try:
        import github_push
        hm_urls = github_push.push_heatmaps(png_bytes, img_date, ipos_results)
        log.info("satellite_pipeline: %d PNGs subidos", len(hm_urls))
    except EnvironmentError as e:
        _push_error = "GITHUB_TOKEN_not_set"
        log.warning("satellite_pipeline: GITHUB_TOKEN no configurado — PNGs no subidos")
    except Exception as e:
        _push_error = str(e)[:200]
        log.error("satellite_pipeline: push_heatmaps falló: %s", e)

    # 5b. Hermes — solo cuando hay datos SAR reales (pipeline NDVI-only lo omite)
    # En Railway el ciclo óptico diario no produce SAR → Hermes no aplica aquí.

    # 6. Actualizar velez_data.json (NDVI real + semana)
    commit_ndvi = ""
    try:
        commit_ndvi = _push_heatmap_ndvi_update(ndvi_data)
        log.info("satellite_pipeline: velez_data.json actualizado")
    except Exception as e:
        log.error("satellite_pipeline: push_heatmap_ndvi_update falló: %s", e)

    # 6b. vegetation_metrics: persistir índices ópticos por cancha en Supabase
    try:
        import velez_supabase as _vs_vm
        _fuente_vm = ndvi_data.get("fuente", "sentinel-2-l2a")
        for _cid, _cd in ndvi_data.get("canchas", {}).items():
            if _cd.get("ndvi") is None:
                continue
            _conf_pct = None
            _pc = _cd.get("prithvi_confidence")
            if _pc is not None:
                _conf_pct = int(round(_pc * 100))
            _metodo = _cd.get("metodo_generacion") or ndvi_data.get("metodo_generacion", "SENTINEL_DIRECTO")
            _vs_vm.insert_vegetation_metrics(
                venue_id="amalfitani",
                cancha_id=_cid,
                fecha_imagen=img_date,
                ndvi=_cd.get("ndvi"),
                gndvi=_cd.get("gndvi"),
                evi2=_cd.get("evi2"),
                bsi=_cd.get("bsi"),
                ndwi=_cd.get("ndwi"),
                ndre=_cd.get("ndre"),
                ccci=_cd.get("ccci"),
                confianza_pct=_conf_pct,
                ndvi_sintetico=_cd.get("ndvi") if _metodo != "SENTINEL_DIRECTO" else None,
                margen_error_kalman=_cd.get("margen_error_kalman"),
                metodo_generacion=_metodo,
                fuente=_fuente_vm,
            )
        log.info("satellite_pipeline: vegetation_metrics insertados (%d canchas)", len(ndvi_map))
    except Exception as _vm_exc:
        log.warning("satellite_pipeline: vegetation_metrics write (non-fatal): %s", _vm_exc)

    # 6c. Propagar NDVI real a sectores.canchero.canchas[] y recalcular scores
    try:
        import github_push as _gp
        _ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        _gp.push_velez_data(ipos_results, _ts)
        log.info("satellite_pipeline: sector scores actualizados con NDVI real")
    except Exception as e:
        log.error("satellite_pipeline: push_velez_data falló: %s", e)

    # 6d. Regenerar PNGs de reportes con datos frescos y subirlos a velez/reportes/
    try:
        from pathlib import Path as _Path
        import render_reports as _rr
        _out_dir = _Path(__file__).parents[4] / "reportes_velez"
        _out_dir.mkdir(exist_ok=True)
        _, _vd_rpt = _gh_get(_VD_PATH)
        if _vd_rpt:
            _rendered = _rr.render_all(_vd_rpt, _out_dir)
            _ts_rpt   = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            _pushed_rpt = 0
            for _rp in _rendered:
                try:
                    _rpt_gh = f"velez/reportes/{_rp.name}"
                    _rsha_r = requests.get(
                        f"https://api.github.com/repos/{_OWNER}/{_REPO}/contents/{_rpt_gh}",
                        headers=_gh_hdrs(), params={"ref": _BRANCH}, timeout=8,
                    )
                    _rsha = _rsha_r.json().get("sha") if _rsha_r.status_code == 200 else None
                    _pl = {
                        "message": f"reports: {_rp.name} · {img_date} [{_ts_rpt}]",
                        "content": base64.b64encode(_rp.read_bytes()).decode(),
                        "branch":  _BRANCH,
                    }
                    if _rsha:
                        _pl["sha"] = _rsha
                    _rr_resp = requests.put(
                        f"https://api.github.com/repos/{_OWNER}/{_REPO}/contents/{_rpt_gh}",
                        headers=_gh_hdrs(), json=_pl, timeout=35,
                    )
                    if _rr_resp.status_code in (200, 201):
                        _pushed_rpt += 1
                except Exception as _push_rpt_exc:
                    log.debug("satellite_pipeline: push report %s: %s", _rp.name, _push_rpt_exc)
            log.info(
                "satellite_pipeline: %d/%d report PNGs regenerados → velez/reportes/",
                _pushed_rpt, len(_rendered),
            )
    except Exception as _rnd_exc:
        log.warning("satellite_pipeline: render_all (non-fatal): %s", _rnd_exc)

    # 7. Snapshot histórico en GitHub (historial/YYYY-MM-DD.json)
    hist_result = {}
    try:
        import github_push
        hist_result = github_push.push_historial_snapshot(img_date)
        log.info("satellite_pipeline: historial/%s.json %s", img_date, hist_result.get("action", "?"))
    except Exception as e:
        log.warning("satellite_pipeline: historial snapshot falló: %s", e)

    # 8. CSV histórico local
    try:
        import historico
        _, vd_fresh = _gh_get(_VD_PATH)
        if vd_fresh:
            historico.write_weekly_snapshot(vd_fresh)
    except Exception as e:
        log.warning("satellite_pipeline: historico CSV falló: %s", e)

    # 9. FaroEngine V2 — SAR soil moisture + HAND + solar para ambos venues
    _faro_v2: dict = {}
    try:
        _here_fp = os.path.dirname(os.path.abspath(__file__))
        if _here_fp not in sys.path:
            sys.path.insert(0, _here_fp)
        from faro_v2_engine import FaroEngine
        for _venue in ["amalfitani", "villa_olimpica"]:
            try:
                _rpt = FaroEngine(_venue).run_and_persist(skip=["canopy", "lband"])
                _faro_v2[_venue] = {"ok": True, "sha256": _rpt.audit.sha256[:16],
                                    "errors": len(_rpt.errors)}
                log.info("FaroEngine V2 %s OK — SHA256=%s… errors=%d",
                         _venue, _rpt.audit.sha256[:16], len(_rpt.errors))
            except Exception as _ve:
                log.warning("FaroEngine V2 %s (non-fatal): %s", _venue, _ve)
                _faro_v2[_venue] = {"ok": False, "error": str(_ve)[:120]}
    except Exception as _fe_err:
        log.warning("FaroEngine V2 (non-fatal): %s", _fe_err)

    log.info("=== satellite_pipeline OK — %s · %d canchas · %d PNGs · push_ok=%s ===",
             img_date, len(ndvi_map), len(png_bytes), not _push_error)
    _record_run(_run_ts, img_date, _median_ndvi, True, canchas=len(ndvi_map))
    return {
        "ok":              True,
        "fecha_imagen":    img_date,
        "nubosidad_pct":   ndvi_data.get("nubosidad_pct"),
        "canchas_ndvi":    len(ndvi_map),
        "pngs_generados":  len(png_bytes),
        "pngs_subidos":    len(hm_urls),
        "push_ok":         not bool(_push_error),
        "push_error":      _push_error,
        "commit_ndvi":     commit_ndvi,
        "historial":       hist_result,
        "faro_v2":         _faro_v2,
    }


def run_sar_kalman_cycle() -> dict | None:
    """
    Ciclo SAR+Kalman cuando no hay imagen óptica disponible.
    Genera NDVI estimado para cada cancha usando:
    1. Kalman LSTM (serie histórica 2020-2026 + ET0 + GDD actuales)
    2. SAR RVI como proxy de salud vegetal (Sentinel-1, penetra nubes)
    3. Marca todos los valores como ESTIMADO con badge en el JSON

    Retorna estructura compatible con gndvi_por_cancha para el assembler.
    """
    import datetime as _dt
    import os as _os
    today = _dt.date.today().isoformat()
    month = _dt.date.today().month
    winter = month in (5, 6, 7, 8)

    result = {
        "fuente": "SAR+Kalman · sin imagen óptica disponible",
        "fecha_imagen": today,
        "metodo_generacion": "KALMAN_LSTM+SAR",
        "nubosidad_pct": 100.0,
        "estimado": True,
        "canchas": {}
    }

    # Intentar Kalman gapfill del core
    try:
        import sys as _sys
        _core = _os.path.join(_os.path.dirname(__file__), '../../../core/satellite')
        if _core not in _sys.path: _sys.path.insert(0, _core)
        from faro_kalman_gapfill import run_kalman_gapfill
        kalman = run_kalman_gapfill(venue_id="amalfitani")
        if kalman and kalman.get("ndvi_estimado") is not None:
            result["canchas"]["amalfitani"] = {
                "ndvi": round(kalman["ndvi_estimado"], 4),
                "margen_error_kalman": round(kalman.get("margen_error", 0.04), 4),
                "metodo_generacion": "KALMAN_LSTM",
                "estimado": True,
            }
            log.info("run_sar_kalman_cycle: Kalman OK — NDVI estimado=%.3f", kalman["ndvi_estimado"])
    except Exception as _ke:
        log.debug("run_sar_kalman_cycle: Kalman (non-fatal): %s", _ke)

    # SAR→NDVI calibrado para las 12 canchas VO (Sentinel-1 penetra nubes)
    try:
        from faro_sar_polimetria import get_sar_by_cancha
        from sar_ndvi_calibration import predict_ndvi as _pred_ndvi
        sar_data = get_sar_by_cancha()
        if sar_data:
            for cid, sar in sar_data.items():
                vv  = sar.get("vv_db")
                vh  = sar.get("vh_db")
                rvi = sar.get("rvi", 0.0)
                ndvi_proxy = _pred_ndvi(vv, vh, month)
                result["canchas"][cid] = {
                    "ndvi":               ndvi_proxy,
                    "rvi":                round(rvi, 4),
                    "sar_vv_db":          vv,
                    "sar_vh_db":          vh,
                    "metodo_generacion":  "SAR_RIDGE",
                    "estimado":           True,
                }
            log.info("run_sar_kalman_cycle: SAR→NDVI Ridge OK — %d canchas", len(sar_data))
    except Exception as _se:
        log.debug("run_sar_kalman_cycle: SAR NDVI (non-fatal): %s", _se)

    # Si no hay datos de ninguna fuente, generar estimados basales con GDD
    if not result["canchas"]:
        log.info("run_sar_kalman_cycle: sin SAR ni Kalman — estimado basal por GDD")
        # NDVI basal invernal para Ryegrass en BsAs (histórico Kalman 2020-2026)
        ndvi_basal = 0.08 if winter else 0.35
        canchas_ids = ["amalfitani","1fa","2fa","3fa","4fa","5fa","6fa","7fa","8fa","9fa","10fa","1fp","2fp"]
        for cid in canchas_ids:
            result["canchas"][cid] = {
                "ndvi": ndvi_basal,
                "metodo_generacion": "BASAL_GDD",
                "estimado": True,
            }

    return result if result["canchas"] else None
