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
        # NDVI staleness: flag gndvi_por_cancha if fecha_imagen > 5 days old
        _now_utc = datetime.now(timezone.utc)
        _gndvi = vd.get("weather_live", {}).get("gndvi_por_cancha", {})
        _fi = _gndvi.get("fecha_imagen")
        if _fi:
            try:
                _fi_dt = datetime.fromisoformat(str(_fi)[:10])
                _ndvi_age = (_now_utc.date() - _fi_dt.date()).days
                _gndvi["ndvi_age_days"] = _ndvi_age
                if _ndvi_age > 5:
                    _gndvi["ndvi_stale"] = True
                    log.warning("assembler: NDVI STALE — imagen del %s (%dd atrás)", _fi, _ndvi_age)
            except Exception:
                pass
        # sectores
        for sid, s in overlay.get("sectores", {}).items():
            vd.setdefault("sectores", {}).setdefault(sid, {}).update(
                {k: v for k, v in s.items() if k not in ("sector_id", "updated_at")}
            )
            # Staleness detection: mark InSAR as stale if updated_at > 14 days
            _upd = s.get("updated_at")
            if _upd and s.get("insar_mm") is not None:
                try:
                    _upd_dt = datetime.fromisoformat(_upd.replace("Z", "+00:00"))
                    _age_days = (_now_utc - _upd_dt).days
                    if _age_days > 14:
                        vd["sectores"][sid]["insar_stale"] = True
                        vd["sectores"][sid]["insar_age_days"] = _age_days
                        log.warning("assembler: InSAR %s STALE (%d días)", sid, _age_days)
                except Exception:
                    pass
        # Tribunas InSAR — sectores estadio_tribuna_* → estadio.tribunas_insar dict
        tribunas_insar: dict = {}
        for sid, s in overlay.get("sectores", {}).items():
            if sid.startswith("estadio_tribuna_"):
                trib_key = sid.replace("estadio_tribuna_", "")   # "norte","sur","este","oeste"
                mm = s.get("insar_mm")
                if mm is not None:
                    tribunas_insar[trib_key] = mm
        if tribunas_insar:
            est = vd.setdefault("sectores", {}).setdefault("estadio", {})
            est["tribunas_insar"] = tribunas_insar
            # Derive global insar_mm from tribuna mean if not already set by direct measurement
            if est.get("insar_mm") is None:
                est["insar_mm"] = round(sum(tribunas_insar.values()) / len(tribunas_insar), 2)
            log.info("assembler: tribunas_insar OK — %s", tribunas_insar)

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
    """3. hermes_consolidate → vd['hermes'] + campos por cancha en weather_live.    Corre una vez a nivel venue + una vez por cancha de VO para alertas individuales."""
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
        # Hermes por cancha individual — alertas específicas por 1FA-10FA, 1FP, 2FP
        _hermes_por_cancha: dict = {}
        _canchas_list = vd.get("sectores", {}).get("canchero", {}).get("canchas", [])
        for _c in _canchas_list:
            _cid = _c.get("id", "")
            if not _cid:
                continue
            try:
                _hc = hermes_consolidate(venue_id, _cid)
                _hermes_por_cancha[_cid] = {
                    "humedad_estimada":      _hc.get("humedad_estimada"),
                    "confianza":             _hc.get("confianza_consolidada"),
                    "alertas":               _hc.get("alertas", []),
                    "fuentes_activas":       _hc.get("fuentes_activas", []),
                }
            except Exception as _he:
                log.debug("assembler: hermes cancha %s (non-fatal): %s", _cid, _he)
        if _hermes_por_cancha:
            vd["hermes"]["por_cancha"] = _hermes_por_cancha
            log.info("assembler: hermes por cancha OK (%d canchas)", len(_hermes_por_cancha))
    except Exception as e:
        log.warning("assembler: hermes (non-fatal): %s", e)
        vd.setdefault("hermes", {})


def _apply_poli_overlay(vd: dict) -> None:
    """Populates sectores.poli with real techo KPIs + canchas[] from vegetation_metrics."""
    try:
        _here = os.path.dirname(os.path.abspath(__file__))
        if _here not in sys.path:
            sys.path.insert(0, _here)
        import velez_supabase as _vs
        veg_rows = _vs.get_vegetation_metrics_latest("amalfitani", None, dias=15)
        veg_by: dict = {}
        for r in veg_rows:
            cid = r.get("cancha_id", "")
            if cid.startswith("poli_") and cid not in veg_by:
                veg_by[cid] = r
        poli = vd.setdefault("sectores", {}).setdefault("poli", {})
        # Alias insar_mm → techo_insar (already populated by _apply_supabase_overlay)
        if poli.get("insar_mm") is not None and "techo_insar" not in poli:
            poli["techo_insar"] = poli["insar_mm"]
        # Alias thermal_temp → techo_temp
        thermal = poli.get("thermal_temp") or poli.get("temp_superficie")
        if thermal is not None and "techo_temp" not in poli:
            poli["techo_temp"] = thermal
        # Build ordered canchas list matching CANCHAS order in gen_velez_poli.py
        _CANCHA_ORDER = [
            "poli_tenis1", "poli_tenis2", "poli_hockey",
            "poli_basquet", "poli_f11", "poli_f8a",
        ]
        canchas_out = []
        for cid in _CANCHA_ORDER:
            r = veg_by.get(cid, {})
            entry: dict = {}
            if r.get("ndvi") is not None:
                entry["ndvi"] = round(float(r["ndvi"]), 3)
            if r.get("sem"):
                entry["sem"] = r["sem"]
            canchas_out.append(entry)
        poli["canchas"] = canchas_out
        log.info("assembler: poli_overlay OK — %d/%d canchas con ndvi",
                 sum(1 for c in canchas_out if c.get("ndvi") is not None), len(canchas_out))
    except Exception as e:
        log.warning("assembler: poli_overlay (non-fatal): %s", e)


def _apply_sede_overlay(vd: dict) -> None:
    """Aliases velez_sectores sede fields for gen_velez_sede.py."""
    try:
        sede = vd.setdefault("sectores", {}).setdefault("sede", {})
        if sede.get("insar_mm") is not None and "insar_deformacion" not in sede:
            sede["insar_deformacion"] = sede["insar_mm"]
        thermal = sede.get("thermal_temp") or sede.get("temp_superficie")
        if thermal is not None and "thermal" not in sede:
            sede["thermal"] = thermal
        log.info("assembler: sede_overlay OK — insar_deformacion=%s thermal=%s",
                 sede.get("insar_deformacion"), sede.get("thermal"))
    except Exception as e:
        log.warning("assembler: sede_overlay (non-fatal): %s", e)


def _apply_instituto_overlay(vd: dict) -> None:
    """Aliases velez_sectores instituto fields for gen_velez_instituto.py."""
    try:
        inst = vd.setdefault("sectores", {}).setdefault("instituto", {})
        if inst.get("insar_mm") is not None and "insar_deformacion" not in inst:
            inst["insar_deformacion"] = inst["insar_mm"]
        thermal = inst.get("thermal_temp") or inst.get("temp_superficie")
        if thermal is not None and "thermal" not in inst:
            inst["thermal"] = thermal
        log.info("assembler: instituto_overlay OK — insar_deformacion=%s thermal=%s",
                 inst.get("insar_deformacion"), inst.get("thermal"))
    except Exception as e:
        log.warning("assembler: instituto_overlay (non-fatal): %s", e)


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
        # Compute avg efficiency so gen_velez_solar_v2 shows Panel Ejecutivo
        effs = [p["eficiencia_pct"] for p in by_panel.values()
                if isinstance(p.get("eficiencia_pct"), (int, float))]
        if effs:
            solar.setdefault("eficiencia_pct", round(sum(effs) / len(effs), 1))
        log.info("assembler: solar overlay OK (%d paneles, eff=%s%%)",
                 len(by_panel), solar.get("eficiencia_pct"))
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


def _build_roger_canchas(vd: dict) -> list:
    """
    Vista unificada Roger: Amalfitani + 12 canchas VO en una sola lista.
    Cada entrada tiene todos los campos científicos del schema CanchaReport.
    Amalfitani viene de sectores.estadio, VO de sectores.canchero.canchas.
    """
    roger_canchas: list = []

    # Amalfitani como primera cancha
    estadio = vd.get("sectores", {}).get("estadio", {})
    hermes_pc = vd.get("hermes", {}).get("por_cancha", {})
    amalf_hermes = hermes_pc.get("amalfitani", {})
    roger_canchas.append({
        "id":                    "amalfitani",
        "nombre":                "Estadio Amalfitani",
        "venue":                 "liniers",
        "score":                 estadio.get("score"),
        "score_prev":            estadio.get("score_prev"),
        "sem":                   estadio.get("sem", "amarillo"),
        "detalle":               estadio.get("detalle", ""),
        "tipo_cesped":           "hibrido",
        # Científicos del assembler (si existen en la lista canchero o en enriquecimiento)
        **{k: v for k, v in (
            vd.get("usuarios", {}).get("roger", {}).get("heatmaps", {}).get("amalfitani") or {}
        ).items() if k not in ("archivo", "detalle", "texto")},
        # Hermes individual
        "humedad_estimada":      amalf_hermes.get("humedad_estimada"),
        "hermes_alertas":        amalf_hermes.get("alertas", []),
        "hermes_confianza":      amalf_hermes.get("confianza"),
        "heatmap_archivo":       "heatmap_amalfitani.png",
    })

    # 12 canchas Villa Olímpica
    for c in vd.get("sectores", {}).get("canchero", {}).get("canchas", []):
        cid = c.get("id", "")
        c_hermes = hermes_pc.get(cid, {})
        entry = dict(c)
        entry["venue"] = "villa_olimpica"
        entry["humedad_estimada"] = c_hermes.get("humedad_estimada")
        entry["hermes_alertas"]   = c_hermes.get("alertas", [])
        entry["hermes_confianza"] = c_hermes.get("confianza")
        entry["heatmap_archivo"]  = f"heatmap_{cid}.png"
        roger_canchas.append(entry)

    return roger_canchas


def _apply_faro_v2_overlay(vd: dict, venue_id: str) -> None:
    """
    7. faro_v2_reports → overlay SAR, solar GHI, HAND sobre vd.
    Conecta FaroEngine V2 (sar/solar/hydro) con el assembler —
    sin esto los gen scripts usaban defaults hardcodeados.
    """
    try:
        _here = os.path.dirname(os.path.abspath(__file__))
        if _here not in sys.path:
            sys.path.insert(0, _here)
        import velez_supabase as _vs
        row = _vs.get_faro_v2_latest(venue_id, dias=14)
        if not row:
            log.info("assembler: faro_v2_overlay — sin datos recientes en faro_v2_reports")
            return

        wl  = vd.setdefault("weather_live", {})
        sec = vd.setdefault("sectores", {})

        # ── SAR C-band (OPERA RTC-S1): backscatter + θ_soil ──────────────────
        # gen_velez_canchero usa weather_live.sar_vv_db para el modelo de compactación
        sar = row.get("sar") or {}
        if sar.get("vv_gamma0_db") is not None and wl.get("sar_vv_db") is None:
            wl["sar_vv_db"] = sar["vv_gamma0_db"]
        if sar.get("vh_gamma0_db") is not None and wl.get("sar_vh_db") is None:
            wl["sar_vh_db"] = sar["vh_gamma0_db"]
        # θ_soil SAR → humedad_suelo_pct si hermes no lo proveyó
        if sar.get("theta_soil") is not None and wl.get("humedad_suelo_pct") is None:
            wl["humedad_suelo_pct"] = round(float(sar["theta_soil"]) * 100, 1)

        # ── Solar GHI + ET₀ ──────────────────────────────────────────────────
        # gen_velez_solar_v2 lee sectores.solar.ghi_kwh_m2 para el panel ejecutivo
        sol = row.get("solar") or {}
        if sol.get("ghi_wh_m2") is not None:
            sec.setdefault("solar", {})["ghi_kwh_m2"] = round(float(sol["ghi_wh_m2"]) / 1000, 3)
        if sol.get("et0_mm_dia") is not None and wl.get("et0_mm_dia") is None:
            wl["et0_mm_dia"] = sol["et0_mm_dia"]

        # ── HAND — drenaje gravitacional (Copernicus DEM 30m) ─────────────────
        # gen scripts pueden leer sectores.estadio.hand_mean_m para info de drenaje
        hydro = row.get("hydro") or {}
        if hydro.get("hand_mean_m") is not None:
            est = sec.setdefault("estadio", {})
            est["hand_mean_m"] = hydro["hand_mean_m"]
            est["hand_zona"]   = hydro.get("zona_riesgo")

        # ── Per-cancha SAR (VO) — subsets procesados del mismo granule OPERA ────
        # FaroEngine almacena por_cancha dentro del JSONB sar: [{id, vv_db, vh_db, theta_soil}]
        por_cancha = sar.get("por_cancha") or []
        if por_cancha:
            cancha_sar: dict = {c["id"]: c for c in por_cancha if c.get("id")}
            cancha_list = (vd.get("sectores", {})
                             .get("canchero", {})
                             .get("canchas", []))
            updated = 0
            for c in cancha_list:
                cid = c.get("id", "")
                csar = cancha_sar.get(cid)
                if not csar:
                    continue
                if csar.get("vv_db") is not None:
                    c["sar_vv_db"] = csar["vv_db"]
                    c["sar_vh_db"] = csar.get("vh_db")
                if csar.get("theta_soil") is not None:
                    c["theta_soil"] = csar["theta_soil"]
                    c["humedad_suelo_pct"] = round(float(csar["theta_soil"]) * 100, 1)
                updated += 1
            if updated:
                log.info("assembler: faro_v2 por_cancha OK — %d canchas con SAR individual", updated)

        log.info(
            "assembler: faro_v2_overlay OK — fecha=%s vv_db=%s ghi_kwh=%.3f hand=%.2fm por_cancha=%d",
            row.get("fecha", "?"),
            sar.get("vv_gamma0_db"),
            float(sol.get("ghi_wh_m2") or 0) / 1000,
            float(hydro.get("hand_mean_m") or 0),
            len(por_cancha),
        )
    except Exception as e:
        log.warning("assembler: faro_v2_overlay (non-fatal): %s", e)


def _parse_detalle_fields(vd: dict) -> None:
    """
    Extrae campos numéricos de los strings 'detalle' de sectores estáticos.
    El JSON estático solo tiene texto; los gen scripts necesitan floats.
    """
    import re
    secs = vd.get("sectores", {})

    # estadio: "NDVI cubierta: 0.61 · InSAR: 0.22 mm"
    est = secs.get("estadio", {})
    det = est.get("detalle", "")
    if est.get("ndvi") is None and det:
        m = re.search(r'NDVI[^:]*:\s*([0-9.]+)', det, re.IGNORECASE)
        if m:
            est["ndvi"] = float(m.group(1))
    if est.get("insar_mm") is None and det:
        m = re.search(r'InSAR[:\s]+([0-9.]+)\s*mm', det, re.IGNORECASE)
        if m:
            est["insar_mm"] = float(m.group(1))

    # poli: "Básquet InSAR: 0.85 mm · Playón: 0.72 mm"
    poli = secs.get("poli", {})
    det_p = poli.get("detalle", "")
    if poli.get("techo_insar") is None and det_p:
        m = re.search(r'InSAR[:\s]+([0-9.]+)\s*mm', det_p, re.IGNORECASE)
        if m:
            poli["techo_insar"] = float(m.group(1))
    if poli.get("techo_sem") is None:
        poli["techo_sem"] = poli.get("sem", "amarillo")

    # solar: extraer eficiencia del detalle dinámico (pipeline la actualiza cada día)
    sol = secs.get("solar", {})
    det_s = sol.get("detalle", "")
    if sol.get("eficiencia_pct") is None and det_s:
        m = re.search(r'Eficiencia[:\s]+([0-9]+)%', det_s, re.IGNORECASE)
        if m:
            sol["eficiencia_pct"] = float(m.group(1))

    log.info("assembler: detalle fields parsed — estadio ndvi=%s insar_mm=%s solar eff=%s",
             est.get("ndvi"), est.get("insar_mm"), sol.get("eficiencia_pct"))


def _apply_solar_pvlib(vd: dict) -> None:
    """Physics-based solar efficiency via NOCT model + NASA POWER GHI.

    Reads GHI from sectores.solar.ghi_kwh_m2 (set by _apply_faro_v2_overlay).
    Computes PR (Performance Ratio) and effective panel efficiency without
    relying on Supabase values that may be synthetic.

    Stores:
      sectores.solar.eficiencia_pct_pvlib  — physics-based efficiency %
      sectores.solar.pr_pvlib              — Performance Ratio (0-1)
      sectores.solar.t_cell_estimada       — estimated cell temperature °C
    """
    try:
        solar = vd.setdefault("sectores", {}).setdefault("solar", {})
        ghi_kwh = solar.get("ghi_kwh_m2")
        if ghi_kwh is None:
            # Fallback: try faro_v2_reports path directly
            wl = vd.get("weather_live", {})
            ghi_kwh = wl.get("ghi_kwh_m2")
        if ghi_kwh is None:
            log.info("assembler: pvlib solar — GHI no disponible, saltando")
            return

        ghi_kwh_f = float(ghi_kwh)
        # Peak irradiance proxy: 6 peak-sun-hours for Buenos Aires (-34°)
        irr_w_m2 = (ghi_kwh_f * 1000) / 6.0

        # Ambient temperature from hermes/weather data
        wl = vd.get("weather_live", {})
        t_amb = float(wl.get("temperatura") or wl.get("temp_media") or 15.0)

        # NOCT cell temperature model (IEC 61215)
        # T_cell = T_amb + (NOCT - 20°C) / 800 W/m² × G
        NOCT = 47.0   # typical monocrystalline panel
        t_cell = t_amb + (NOCT - 20.0) / 800.0 * irr_w_m2

        # Panel + system loss factors (industry standard values for Argentina)
        EFF_STC   = 0.200   # rated module efficiency @ STC (20% monocrystalline)
        TEMP_COEF = -0.0040  # power temperature coefficient (%/°C)
        ETA_INV   = 0.960   # inverter efficiency
        F_SOIL    = 0.970   # soiling (dust, pampas)
        F_SHADE   = 0.985   # partial shading from stadium structure
        F_MISMATCH= 0.980   # module mismatch
        F_WIRING  = 0.980   # DC wiring losses

        # Temperature-corrected module efficiency
        temp_factor = 1.0 + TEMP_COEF * (t_cell - 25.0)
        eff_module = EFF_STC * max(0.70, temp_factor)

        # Overall system Performance Ratio
        pr = ETA_INV * F_SOIL * F_SHADE * F_MISMATCH * F_WIRING

        # Effective DC-to-AC system efficiency
        eff_system_pct = round(eff_module * pr * 100.0, 1)
        pr_total = round(eff_module / EFF_STC * pr, 3)

        solar["eficiencia_pct_pvlib"] = eff_system_pct
        solar["pr_pvlib"]             = pr_total
        solar["t_cell_estimada"]      = round(t_cell, 1)
        solar["ghi_kwh_m2_real"]      = round(ghi_kwh_f, 3)

        # Override eficiencia_pct only if it's synthetic (82.4) or missing
        SYNTHETIC_EFF = {82.4, 71.0}
        current_eff = solar.get("eficiencia_pct")
        if current_eff is None or float(current_eff) in SYNTHETIC_EFF:
            solar["eficiencia_pct"] = eff_system_pct
            log.info("assembler: pvlib solar — eficiencia_pct sobreescrita (%s→%.1f%%)",
                     current_eff, eff_system_pct)

        log.info(
            "assembler: pvlib solar OK — GHI=%.2f kWh/m² G_peak=%.0fW/m² "
            "T_cell=%.1f°C PR=%.3f eff=%.1f%%",
            ghi_kwh_f, irr_w_m2, t_cell, pr_total, eff_system_pct,
        )
    except Exception as e:
        log.warning("assembler: pvlib solar (non-fatal): %s", e)


def assemble_report(venue_id: str = "amalfitani") -> dict:
    """
    Ensambla el VelezReport canónico.
    Nunca lanza excepción — retorna dict vacío en el peor caso.
    Llamar una sola vez por ciclo semanal y pasar el resultado a todos los consumidores.
    """
    vd = _load_static_json()
    _apply_supabase_overlay(vd)
    _apply_hermes(vd, venue_id)
    _apply_poli_overlay(vd)
    _apply_sede_overlay(vd)
    _apply_instituto_overlay(vd)
    _apply_surface_rules(vd)
    _apply_solar_overlay(vd)
    _apply_piletas_overlay(vd)
    _apply_faro_v2_overlay(vd, venue_id)   # SAR vv/vh, GHI solar, HAND hydro desde faro_v2_reports
    _apply_solar_pvlib(vd)                 # physics-based efficiency: NOCT model + NASA POWER GHI
    _parse_detalle_fields(vd)              # extrae floats de detalle strings (fallback si v2 no tiene datos)
    # Vista unificada Roger: Amalfitani + 12 canchas VO con todos los campos científicos
    vd["roger_canchas"] = _build_roger_canchas(vd)
    for _c in vd.get("sectores", {}).get("canchero", {}).get("canchas", []):
        _cid = _c.get("id", "")
        if _cid:
            _c.setdefault("heatmap_archivo", f"heatmap_{_cid}.png")
    _est = vd.get("sectores", {}).get("estadio")
    if isinstance(_est, dict):
        _est.setdefault("heatmap_archivo", "heatmap_amalfitani.png")
    vd["_assembled_at"] = datetime.now(timezone.utc).isoformat()
    vd["_venue_id"]     = venue_id
    log.info("assembler: reporte ensamblado para %s — %d roger_canchas [%s]",
             venue_id, len(vd.get("roger_canchas", [])), vd.get("_assembled_at", ""))
    return vd
