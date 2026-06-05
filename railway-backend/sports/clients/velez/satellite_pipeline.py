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
import base64, json, logging
from datetime import date, datetime, timezone

import requests

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

    # 2. Sanity check: rechazar imagen con NDVI mediano < 0.12 (artefacto probable)
    # Valores 0.03–0.10 son suelo desnudo, no pasto — indican niebla/sombra no detectada.
    _ndvi_vals = sorted(
        v["ndvi"] for v in ndvi_data.get("canchas", {}).values()
        if isinstance(v.get("ndvi"), (int, float))
    )
    _median_ndvi = _ndvi_vals[len(_ndvi_vals) // 2] if len(_ndvi_vals) >= 8 else None

    if _median_ndvi is not None and _median_ndvi < 0.12:
        log.warning(
            "satellite_pipeline: imagen %s RECHAZADA — NDVI mediano %.3f < 0.12 "
            "(artefacto probable: niebla/sombra de nube no detectada) — "
            "manteniendo último ciclo válido",
            img_date, _median_ndvi,
        )
        _record_run(_run_ts, img_date, _median_ndvi, False, skipped_reason="ndvi_anomaly")
        return {
            "ok": True, "skipped": True, "reason": "ndvi_anomaly",
            "fecha_imagen": img_date, "median_ndvi": _median_ndvi,
        }

    # 3. Verificar si esta imagen ya fue procesada (usar >= para no re-procesar misma fecha)
    if not force:
        last = _last_processed_date()
        if last and last >= img_date:
            log.info("satellite_pipeline: imagen %s ya procesada (última: %s) — omitido", img_date, last)
            _record_run(_run_ts, img_date, _median_ndvi, False, skipped_reason="already_processed")
            return {"ok": True, "skipped": True, "reason": "already_processed", "fecha_imagen": img_date}

    log.info("=== satellite_pipeline START — imagen %s ===", img_date)

    # 4. Construir ndvi_map y ipos_results
    ndvi_map = {cid: v["ndvi"] for cid, v in ndvi_data.get("canchas", {}).items()
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
    try:
        import github_push
        hm_urls = github_push.push_heatmaps(png_bytes, img_date, ipos_results)
        log.info("satellite_pipeline: %d PNGs subidos", len(hm_urls))
    except EnvironmentError as e:
        log.warning("satellite_pipeline: GITHUB_TOKEN no configurado — PNGs no subidos")
    except Exception as e:
        log.error("satellite_pipeline: push_heatmaps falló: %s", e)

    # 6. Actualizar velez_data.json (NDVI real + semana)
    commit_ndvi = ""
    try:
        commit_ndvi = _push_heatmap_ndvi_update(ndvi_data)
        log.info("satellite_pipeline: velez_data.json actualizado")
    except Exception as e:
        log.error("satellite_pipeline: push_heatmap_ndvi_update falló: %s", e)

    # 6b. Propagar NDVI real a sectores.canchero.canchas[] y recalcular scores
    try:
        import github_push as _gp
        _ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        _gp.push_velez_data(ipos_results, _ts)
        log.info("satellite_pipeline: sector scores actualizados con NDVI real")
    except Exception as e:
        log.error("satellite_pipeline: push_velez_data falló: %s", e)

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

    log.info("=== satellite_pipeline OK — %s · %d canchas · %d PNGs ===",
             img_date, len(ndvi_map), len(png_bytes))
    _record_run(_run_ts, img_date, _median_ndvi, True, canchas=len(ndvi_map))
    return {
        "ok":              True,
        "fecha_imagen":    img_date,
        "nubosidad_pct":   ndvi_data.get("nubosidad_pct"),
        "canchas_ndvi":    len(ndvi_map),
        "pngs_generados":  len(png_bytes),
        "pngs_subidos":    len(hm_urls),
        "commit_ndvi":     commit_ndvi,
        "historial":       hist_result,
    }
