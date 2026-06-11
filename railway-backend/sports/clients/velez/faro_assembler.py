"""
faro_assembler.py — Único ensamblador del reporte canónico VelezReport.

REGLA ARQUITECTÓNICA:
  Este es el ÚNICO módulo que lee de velez_data.json, Supabase y hermes_consolidate.
  Ningún renderer, ningún _body_*(), ningún gen script importa esas fuentes directamente.
  Todo dato pasa por assemble_report(). Esta regla no tiene excepciones.

Fuentes en orden:
  1. velez_data.json (local o GitHub) — base estática
  2. Supabase overlay (velez_canchas, velez_sectores, velez_weather_live) — datos vivos
  3. hermes_consolidate() — humedad corregida por ET₀ + alertas físicas
  4. soil_metrics / vegetation_metrics por cancha — enriquecimiento científico

Retorna un dict backward-compatible con el formato velez_data.json.
Nunca lanza excepción — cada paso falla silenciosamente con log.warning.
"""
from __future__ import annotations
import json, logging, os, sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import urlopen, Request as UReq

log = logging.getLogger(__name__)

_VD_RAW_URL = "https://raw.githubusercontent.com/protocolfaro/faro-paneles/main/velez/velez_data.json"

_SOIL_SCIENTIFIC = (
    "entropia_h", "angulo_alpha", "compactacion_index_ml",
    "temp_superficie_c", "theta_5cm", "theta_10cm", "theta_20cm", "theta_smap",
)
_VEG_SCIENTIFIC = ("ndvi_2_5m", "bsi_2_5m", "ndwi_2_5m")


def _load_static_json() -> dict:
    """1. velez_data.json local → GitHub fallback."""
    local = Path(__file__).parents[4] / "velez" / "velez_data.json"
    if local.exists():
        try:
            return json.loads(local.read_text(encoding="utf-8"))
        except Exception as e:
            log.warning("assembler: JSON local: %s", e)
    try:
        req = UReq(_VD_RAW_URL, headers={"User-Agent": "FaroProtocol/5.0"})
        with urlopen(req, timeout=12) as r:
            return json.loads(r.read())
    except Exception as e:
        log.warning("assembler: JSON remoto: %s", e)
    return {}


def _apply_supabase_overlay(vd: dict) -> None:
    """2. Overlay de Supabase en-place sobre vd."""
    try:
        _here = os.path.dirname(os.path.abspath(__file__))
        if _here not in sys.path:
            sys.path.insert(0, _here)
        import velez_supabase as _vs
        overlay = _vs.get_live_overlay()
        if not overlay:
            return
        # weather_live
        if overlay.get("weather_live"):
            vd["weather_live"] = overlay["weather_live"]
        # sectores
        for sid, s in overlay.get("sectores", {}).items():
            vd.setdefault("sectores", {}).setdefault(sid, {}).update(
                {k: v for k, v in s.items() if k not in ("sector_id", "updated_at")}
            )
        # canchas en sectores.canchero.canchas + weather_live + usuarios.roger.heatmaps
        canchas_ov = overlay.get("canchas", {})
        if canchas_ov:
            cancha_list = vd.get("sectores", {}).get("canchero", {}).get("canchas", [])
            for entry in cancha_list:
                cid = entry.get("id", "")
                if cid in canchas_ov:
                    cd = canchas_ov[cid]
                    for f in ("ndvi", "gndvi", "bsi", "ndwi", "score", "sem",
                              "detalle", "n_status", "n_rec", "tipo_cesped"):
                        if cd.get(f) is not None:
                            entry[f] = cd[f]
            gn_c = (vd.setdefault("weather_live", {})
                      .setdefault("gndvi_por_cancha", {})
                      .setdefault("canchas", {}))
            for cid, cd in canchas_ov.items():
                gn_c.setdefault(cid, {}).update(
                    {k: v for k, v in cd.items()
                     if k not in ("cancha_id", "updated_at", "fuente")})
            roger_hm = (vd.setdefault("usuarios", {})
                          .setdefault("roger", {})
                          .setdefault("heatmaps", {}))
            for cid, cd in canchas_ov.items():
                roger_hm.setdefault(cid, {}).update(
                    {k: v for k, v in cd.items()
                     if k in ("ndvi", "gndvi", "bsi", "ndwi", "n_status",
                              "n_rec", "tipo_cesped") and cd.get(k) is not None})
        log.info("assembler: overlay Supabase OK")
    except Exception as e:
        log.warning("assembler: Supabase overlay (non-fatal): %s", e)


def _apply_hermes(vd: dict, venue_id: str) -> None:
    """3. hermes_consolidate → vd['hermes'] + campos clave en weather_live."""
    try:
        _here    = os.path.dirname(os.path.abspath(__file__))
        _agents  = os.path.normpath(os.path.join(_here, "..", "..", "..", "agents"))
        for p in (_here, _agents):
            if p not in sys.path:
                sys.path.insert(0, p)
        from hermes import hermes_consolidate
        hc = hermes_consolidate(venue_id)
        vd["hermes"] = hc
        # Surfacear campos de climate_metrics a weather_live para gen scripts
        clim = hc.get("climate") or {}
        wl   = vd.setdefault("weather_live", {})
        for field, wl_key in (
            ("smith_kerns_pct",    "smith_kerns_pct"),
            ("et0_mm_dia",         "et0_mm_dia"),
            ("deficit_hidrico_mm", "deficit_hidrico_mm"),
            ("gdd_acumulado_7d",   "gdd_acumulado_7d"),
            ("ventana_corte",      "ventana_corte"),
            ("altura_corte_mm",    "altura_corte_mm"),
            ("et_ecostress_mm",    "et_ecostress_mm"),
        ):
            if clim.get(field) is not None and wl.get(wl_key) is None:
                wl[wl_key] = clim[field]
        # riego_min → riego_min_sector (alias para gen scripts)
        if clim.get("riego_min") and not wl.get("riego_min_sector"):
            wl["riego_min_sector"] = clim["riego_min"]
        log.info("assembler: hermes OK (conf=%.2f fuentes=%s)",
                 hc.get("confianza_consolidada", 0), hc.get("fuentes_activas", []))
    except Exception as e:
        log.warning("assembler: hermes (non-fatal): %s", e)
        vd.setdefault("hermes", {})


def _enrich_canchas_scientific(vd: dict, venue_id: str) -> None:
    """4. Enriquecer cada cancha con campos científicos de soil_metrics / vegetation_metrics."""
    cancha_list = vd.get("sectores", {}).get("canchero", {}).get("canchas", [])
    if not cancha_list:
        return
    try:
        _here = os.path.dirname(os.path.abspath(__file__))
        if _here not in sys.path:
            sys.path.insert(0, _here)
        import velez_supabase as _vs
        soil_rows = _vs.get_soil_metrics_latest(venue_id, None, dias=14)
        veg_rows  = _vs.get_vegetation_metrics_latest(venue_id, None, dias=15)
        # Indexar por cancha_id (tomar el más reciente = primera fila)
        soil_by: dict = {}
        for r in soil_rows:
            cid = r.get("cancha_id")
            if cid and cid not in soil_by:
                soil_by[cid] = r
        veg_by: dict = {}
        for r in veg_rows:
            cid = r.get("cancha_id")
            if cid and cid not in veg_by:
                veg_by[cid] = r
        for c in cancha_list:
            cid = c.get("id", "")
            if not cid:
                continue
            soil = soil_by.get(cid)
            veg  = veg_by.get(cid)
            if soil:
                for f in _SOIL_SCIENTIFIC:
                    if soil.get(f) is not None:
                        c[f] = soil[f]
            if veg:
                for f in _VEG_SCIENTIFIC:
                    if veg.get(f) is not None:
                        c[f] = veg[f]
        log.info("assembler: enriquecimiento científico OK (%d canchas)", len(cancha_list))
    except Exception as e:
        log.warning("assembler: enriquecimiento científico (non-fatal): %s", e)


def _apply_surface_rules(vd: dict) -> None:
    """7. Aplicar reglas de capa por tipo de superficie.

    Para superficies no biológicas (sintético, polvo de ladrillo, indoor):
      - NDVI / GNDVI / BSI / NDWI / n_status / n_rec → None
      - Excluir de canchas_en_riesgo de fungosis
      - Agregar campo tipo_superficie a cada heatmap

    Para indoor: adicionalmente marca sar_aplica=False.
    """
    try:
        _here = os.path.dirname(os.path.abspath(__file__))
        if _here not in sys.path:
            sys.path.insert(0, _here)
        import velez_supabase as _vs
        tipo_cesped  = _vs._TIPO_CESPED
        tipos_no_bio = _vs._TIPOS_NO_BIO
        tipos_indoor = _vs._TIPOS_INDOOR
    except Exception as e:
        log.warning("assembler: surface_rules — no pudo importar tipos: %s", e)
        return

    _NDVI_FIELDS = ("ndvi", "gndvi", "bsi", "ndwi", "n_status", "n_rec")

    # ── heatmaps de Roger ──────────────────────────────────────────────────
    heatmaps = (vd.get("usuarios", {})
                   .get("roger", {})
                   .get("heatmaps", {}))
    nulled = 0
    for cid, hm in heatmaps.items():
        t = tipo_cesped.get(cid, "natural")
        hm["tipo_superficie"] = t
        if t in tipos_no_bio:
            for f in _NDVI_FIELDS:
                if hm.get(f) is not None:
                    hm[f] = None
                    nulled += 1
            if t in tipos_indoor:
                hm["sar_aplica"] = False

    # ── sectores.canchero.canchas ──────────────────────────────────────────
    for c in (vd.get("sectores", {})
                 .get("canchero", {})
                 .get("canchas", []) or []):
        cid = c.get("id", "")
        t = tipo_cesped.get(cid, "natural")
        c["tipo_superficie"] = t
        if t in tipos_no_bio:
            for f in _NDVI_FIELDS:
                c[f] = None

    # ── excluir no-biológicos de canchas_en_riesgo fungosis ───────────────
    riesgo = vd.get("weather_live", {}).get("riesgo_fungosis", {})
    cr = riesgo.get("canchas_en_riesgo")
    if isinstance(cr, list):
        antes = len(cr)
        riesgo["canchas_en_riesgo"] = [
            c for c in cr
            if tipo_cesped.get(c, "natural") not in tipos_no_bio
        ]
        excluidas = antes - len(riesgo["canchas_en_riesgo"])
        if excluidas:
            log.info("assembler: surface_rules — %d canchas no-biológicas excluidas "
                     "de fungosis: %s",
                     excluidas,
                     [c for c in cr if tipo_cesped.get(c, "natural") in tipos_no_bio])

    log.info("assembler: surface_rules OK — %d campos NDVI nulleados en superficies no-bio",
             nulled)


def _apply_solar_overlay(vd: dict) -> None:
    """5. velez_solar → vd['sectores']['solar']['panel_data'] por panel_id."""
    try:
        _here = os.path.dirname(os.path.abspath(__file__))
        if _here not in sys.path:
            sys.path.insert(0, _here)
        import velez_supabase as _vs
        rows = _vs.get_solar_latest(dias=7)
        if not rows:
            return
        by_panel: dict = {}
        for r in rows:
            pid = r.get("panel_id")
            if pid and pid not in by_panel:
                by_panel[pid] = {k: v for k, v in r.items()
                                 if k not in ("id", "created_at")}
        solar = vd.setdefault("sectores", {}).setdefault("solar", {})
        solar["panel_data"] = by_panel
        log.info("assembler: solar overlay OK (%d paneles)", len(by_panel))
    except Exception as e:
        log.warning("assembler: solar overlay (non-fatal): %s", e)


def _apply_piletas_overlay(vd: dict) -> None:
    """6. velez_piletas → vd['sectores']['piletas']['kpis_por_pileta'] por pileta_id."""
    try:
        _here = os.path.dirname(os.path.abspath(__file__))
        if _here not in sys.path:
            sys.path.insert(0, _here)
        import velez_supabase as _vs
        rows = _vs.get_piletas_latest(dias=7)
        if not rows:
            return
        by_pileta: dict = {}
        for r in rows:
            pid = r.get("pileta_id")
            if pid and pid not in by_pileta:
                by_pileta[pid] = {k: v for k, v in r.items()
                                  if k not in ("id", "created_at")}
        piletas = vd.setdefault("sectores", {}).setdefault("piletas", {})
        piletas["kpis_por_pileta"] = by_pileta
        log.info("assembler: piletas overlay OK (%d piletas)", len(by_pileta))
    except Exception as e:
        log.warning("assembler: piletas overlay (non-fatal): %s", e)


def assemble_report(venue_id: str = "amalfitani") -> dict:
    """
    Ensambla el VelezReport canónico.
    Nunca lanza excepción — retorna dict vacío en el peor caso.
    Llamar una sola vez por ciclo semanal y pasar el resultado a todos los consumidores.
    """
    vd = _load_static_json()
    _apply_supabase_overlay(vd)
    _apply_hermes(vd, venue_id)
    _enrich_canchas_scientific(vd, venue_id)
    _apply_surface_rules(vd)
    _apply_solar_overlay(vd)
    _apply_piletas_overlay(vd)
    vd["_assembled_at"] = datetime.now(timezone.utc).isoformat()
    vd["_venue_id"]     = venue_id
    log.info("assembler: reporte ensamblado para %s [%s]",
             venue_id, vd.get("_assembled_at", ""))
    return vd
