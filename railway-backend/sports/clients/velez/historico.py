"""
historico.py — Weekly data registry for Vélez Sarsfield.
Appends one row per satellite cycle to historico_velez_protocol.csv.
Primary store: GitHub historial/csv/historico_velez_protocol.csv (persistent across Railway deploys).
Secondary store: local filesystem (dev only).
"""
from __future__ import annotations
import csv, io, logging, os
from datetime import date, datetime
from pathlib import Path

log = logging.getLogger(__name__)

HIST_PATH    = Path(__file__).parents[4] / "historico_velez_protocol.csv"
GITHUB_CSV_PATH = "historial/csv/historico_velez_protocol.csv"

# VO canchas
_VO_CIDS = ["1fa","2fa","3fa","4fa","5fa","6fa","7fa","8fa","9fa","10fa","1fp","2fp"]
# Canchas nuevas con cobertura satelital
_NEW_CIDS = ["amalfitani","poli_f11","poli_f8a","poli_f8b","poli_hockey"]

FIELDS = [
    "timestamp_utc", "semana_iso", "semana_epi", "fecha_local",
    # NDVI per cancha (Sentinel-2 sub-pixel cleaned) — VO
    "ndvi_1fa", "ndvi_2fa", "ndvi_3fa", "ndvi_4fa",
    "ndvi_5fa", "ndvi_6fa", "ndvi_7fa", "ndvi_8fa", "ndvi_9fa", "ndvi_10fa",
    "ndvi_1fp", "ndvi_2fp",
    # NDVI — Amalfitani + Polideportivo
    "ndvi_amalfitani", "ndvi_poli_f11", "ndvi_poli_f8a", "ndvi_poli_f8b", "ndvi_poli_hockey",
    # GNDVI (nitrogen proxy) per cancha — VO
    "gndvi_1fa", "gndvi_2fa", "gndvi_3fa", "gndvi_4fa",
    "gndvi_5fa", "gndvi_6fa", "gndvi_7fa", "gndvi_8fa", "gndvi_9fa", "gndvi_10fa",
    "gndvi_1fp", "gndvi_2fp",
    # GNDVI — Amalfitani + Polideportivo (from heatmaps pipeline)
    "gndvi_amalfitani", "gndvi_poli_f11", "gndvi_poli_f8a", "gndvi_poli_f8b", "gndvi_poli_hockey",
    # SAR Sentinel-1 (VV/VH backscatter dB)
    "sar_vv_db", "sar_vh_db", "sar_ratio_vv_vh", "sar_fecha",
    # Clegg Hammer compaction (CG units) from Roger's field measurements
    "clegg_1fa", "clegg_2fa", "clegg_3fa", "clegg_4fa",
    "clegg_1fp", "clegg_2fp",
    # Aspersores network state breakdown (%)
    "asp_cubierto_pct", "asp_solapado_pct", "asp_sin_cobertura_pct", "asp_total_count",
    # Sector scores
    "score_canchero", "score_estadio", "score_poli",
    "score_solar", "score_piletas", "score_sede",
    # Weather / hydric balance
    "et0_mm_dia", "precipitacion_semana_mm", "deficit_hidrico_mm",
    "humedad_suelo_pct", "humedad_suelo_estado",
    # Satellite metadata
    "sentinel2_fecha_imagen", "sentinel2_nubosidad_pct", "sentinel2_fuente",
]


def _build_row(vd: dict) -> dict:
    """Extract all metrics from a velez_data.json dict into a CSV row dict."""
    today    = date.today()
    iso_cal  = today.isocalendar()
    wl       = vd.get("weather_live", {})
    gndvi_d  = wl.get("gndvi_por_cancha", {})
    # VO NDVI/GNDVI come from weather_live.gndvi_por_cancha.canchas
    vo_canchas = gndvi_d.get("canchas", {})
    sectores = vd.get("sectores", {})
    roger    = vd.get("usuarios", {}).get("roger", {})
    # New canchas NDVI comes from usuarios.roger.heatmaps.{cid}.ndvi (satellite pipeline)
    hm_data  = roger.get("heatmaps", {})

    clegg_raw  = roger.get("mediciones", {}).get("clegg", [])
    clegg_map  = {m.get("zona", "").lower(): m.get("valor_cg") for m in clegg_raw}

    asp_all = vd.get("aspersores", {})
    if asp_all:
        asp_list = [a for v in asp_all.values() if isinstance(v, list) for a in v]
        total  = len(asp_list)
        cub    = sum(1 for a in asp_list if a.get("estado") == "cubierto")
        sol    = sum(1 for a in asp_list if a.get("estado") == "solapado")
        sin_c  = total - cub - sol
        asp_cub = round(cub / total * 100, 1) if total else ""
        asp_sol = round(sol / total * 100, 1) if total else ""
        asp_sin = round(sin_c / total * 100, 1) if total else ""
    else:
        total = asp_cub = asp_sol = asp_sin = ""

    sar = wl.get("sar", {})

    row: dict = {
        "timestamp_utc":           datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "semana_iso":              f"{iso_cal[0]}-W{iso_cal[1]:02d}",
        "semana_epi":              iso_cal[1],
        "fecha_local":             today.isoformat(),
        # NDVI VO canchas
        **{f"ndvi_{cid}":  vo_canchas.get(cid, {}).get("ndvi",  "") for cid in _VO_CIDS},
        # NDVI nuevas canchas (de heatmaps, escritas por satellite_pipeline)
        **{f"ndvi_{cid}":  (hm_data.get(cid) or {}).get("ndvi", "") for cid in _NEW_CIDS},
        # GNDVI VO (from weather_live.gndvi_por_cancha)
        **{f"gndvi_{cid}": vo_canchas.get(cid, {}).get("gndvi", "") for cid in _VO_CIDS},
        # GNDVI nuevas canchas (from heatmaps, written by satellite_pipeline)
        **{f"gndvi_{cid}": (hm_data.get(cid) or {}).get("gndvi", "") for cid in _NEW_CIDS},
        # SAR
        "sar_vv_db":               sar.get("vv_db", ""),
        "sar_vh_db":               sar.get("vh_db", ""),
        "sar_ratio_vv_vh":         sar.get("ratio_vv_vh", ""),
        "sar_fecha":               sar.get("fecha", ""),
        # Clegg
        "clegg_1fa":  clegg_map.get("1fa", ""),
        "clegg_2fa":  clegg_map.get("2fa", ""),
        "clegg_3fa":  clegg_map.get("3fa", ""),
        "clegg_4fa":  clegg_map.get("4fa", ""),
        "clegg_1fp":  clegg_map.get("1fp", ""),
        "clegg_2fp":  clegg_map.get("2fp", ""),
        # Aspersores
        "asp_cubierto_pct":       asp_cub,
        "asp_solapado_pct":       asp_sol,
        "asp_sin_cobertura_pct":  asp_sin,
        "asp_total_count":        total,
        # Sector scores
        "score_canchero":  sectores.get("canchero",  {}).get("score", ""),
        "score_estadio":   sectores.get("estadio",   {}).get("score", ""),
        "score_poli":      sectores.get("poli",      {}).get("score", ""),
        "score_solar":     sectores.get("solar",     {}).get("score", ""),
        "score_piletas":   sectores.get("piletas",   {}).get("score", ""),
        "score_sede":      sectores.get("sede",      {}).get("score", ""),
        # Weather
        "et0_mm_dia":              wl.get("et0_mm_dia", ""),
        "precipitacion_semana_mm": wl.get("precipitacion_semana_mm", ""),
        "deficit_hidrico_mm":      wl.get("deficit_hidrico_mm", ""),
        "humedad_suelo_pct":       wl.get("humedad_suelo_pct", ""),
        "humedad_suelo_estado":    wl.get("humedad_suelo_estado", ""),
        # Sentinel-2
        "sentinel2_fecha_imagen":  gndvi_d.get("fecha_imagen", ""),
        "sentinel2_nubosidad_pct": gndvi_d.get("nubosidad_pct", ""),
        "sentinel2_fuente":        gndvi_d.get("fuente", ""),
    }
    return row


def _push_csv_to_github(row: dict) -> str:
    """Append row to historial/csv/historico_velez_protocol.csv on GitHub.
    Reads existing CSV, appends new row, pushes full file back.
    Returns commit URL or empty string on failure.
    """
    import base64, requests as _req
    token = os.environ.get("GITHUB_TOKEN", "")
    if not token:
        return ""
    owner, repo, branch = "protocolfaro", "faroprotocol", "main"
    api_url = f"https://api.github.com/repos/{owner}/{repo}/contents/{GITHUB_CSV_PATH}"
    hdrs = {"Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28"}

    existing_sha = None
    existing_content = ""
    r = _req.get(api_url, headers=hdrs, params={"ref": branch}, timeout=15)
    if r.status_code == 200:
        d = r.json()
        existing_sha = d["sha"]
        existing_content = base64.b64decode(d["content"]).decode("utf-8")

    # Build new CSV content
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=FIELDS, extrasaction="ignore", lineterminator="\n")
    if not existing_content:
        writer.writeheader()
        writer.writerow(row)
    else:
        fecha_to_check = row.get("fecha_local", "")
        if fecha_to_check:
            reader = csv.DictReader(io.StringIO(existing_content))
            if any(r.get("fecha_local") == fecha_to_check for r in reader):
                log.info("historico: fecha_local %s ya existe en GitHub CSV — duplicado omitido", fecha_to_check)
                return ""
        buf.write(existing_content.rstrip("\n") + "\n")
        writer.writerow(row)

    ts = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    payload = {
        "message": f"historico: snapshot {row['semana_iso']} [{ts}]",
        "content": base64.b64encode(buf.getvalue().encode("utf-8")).decode(),
        "branch":  branch,
    }
    if existing_sha:
        payload["sha"] = existing_sha

    resp = _req.put(api_url, headers=hdrs, json=payload, timeout=35)
    resp.raise_for_status()
    return resp.json().get("commit", {}).get("html_url", "")


def write_weekly_snapshot(vd: dict) -> Path:
    """
    Extract metrics from velez_data.json and append one row to the historical CSV.
    Primary: pushes to GitHub (persistent across Railway deploys).
    Secondary: writes local CSV as dev fallback.
    Returns local HIST_PATH.
    """
    row = _build_row(vd)

    # Primary: GitHub CSV (survives Railway deploys)
    try:
        commit = _push_csv_to_github(row)
        if commit:
            log.info("historico: row %s pushed to GitHub CSV", row["semana_iso"])
        else:
            log.info("historico: GITHUB_TOKEN not set — GitHub CSV skipped")
    except Exception as e:
        log.warning("historico: GitHub CSV push falló (non-fatal): %s", e)

    # Secondary: local filesystem (dev / CI)
    file_exists = HIST_PATH.exists()
    try:
        fecha_to_check = row.get("fecha_local", "")
        if file_exists and fecha_to_check:
            with open(HIST_PATH, newline="", encoding="utf-8") as fh:
                if any(r.get("fecha_local") == fecha_to_check for r in csv.DictReader(fh)):
                    log.info("historico: local fecha_local %s ya existe — duplicado omitido", fecha_to_check)
                    return HIST_PATH
        with open(HIST_PATH, "a", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=FIELDS, extrasaction="ignore")
            if not file_exists:
                writer.writeheader()
            writer.writerow(row)
        log.info("historico: row %s appended to local %s", row["semana_iso"], HIST_PATH)
    except Exception as e:
        log.warning("historico: local CSV write falló (non-fatal): %s", e)

    return HIST_PATH
