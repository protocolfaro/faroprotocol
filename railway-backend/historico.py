"""
historico.py — Weekly data registry for Vélez Sarsfield.
Appends one row per Monday run to historico_velez_protocol.csv.
Call write_weekly_snapshot(vd) from velez_scheduler before send_all_reports().
"""
from __future__ import annotations
import csv, logging
from datetime import date, datetime
from pathlib import Path

log = logging.getLogger(__name__)

HIST_PATH = Path(__file__).parent.parent / "historico_velez_protocol.csv"

FIELDS = [
    "timestamp_utc", "semana_iso", "semana_epi", "fecha_local",
    # NDVI per cancha (Sentinel-2 sub-pixel cleaned)
    "ndvi_1fa", "ndvi_2fa", "ndvi_3fa", "ndvi_4fa",
    "ndvi_5fa", "ndvi_6fa", "ndvi_7fa", "ndvi_8fa", "ndvi_9fa", "ndvi_10fa",
    "ndvi_1fp", "ndvi_2fp",
    # GNDVI (nitrogen proxy) per cancha
    "gndvi_1fa", "gndvi_2fa", "gndvi_3fa", "gndvi_4fa",
    "gndvi_5fa", "gndvi_6fa", "gndvi_7fa", "gndvi_8fa", "gndvi_9fa", "gndvi_10fa",
    "gndvi_1fp", "gndvi_2fp",
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


def write_weekly_snapshot(vd: dict) -> Path:
    """
    Extract metrics from velez_data.json dict and append one row to the CSV.
    Returns the path of the CSV file written.
    """
    today    = date.today()
    iso_cal  = today.isocalendar()
    wl       = vd.get("weather_live", {})
    gndvi_d  = wl.get("gndvi_por_cancha", {})
    canchas  = gndvi_d.get("canchas", {})
    sectores = vd.get("sectores", {})
    roger    = vd.get("usuarios", {}).get("roger", {})

    # Clegg: from roger.mediciones.clegg or roger.kpis proxies
    clegg_raw  = roger.get("mediciones", {}).get("clegg", [])
    clegg_map  = {m.get("zona", "").lower(): m.get("valor_cg") for m in clegg_raw}

    # Aspersores: if synced from Railway into vd
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

    # SAR
    sar = wl.get("sar", {})

    row: dict = {
        "timestamp_utc":           datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "semana_iso":              f"{iso_cal[0]}-W{iso_cal[1]:02d}",
        "semana_epi":              iso_cal[1],
        "fecha_local":             today.isoformat(),
        # NDVI
        **{f"ndvi_{cid}":  canchas.get(cid, {}).get("ndvi",  "") for cid in [
            "1fa","2fa","3fa","4fa","5fa","6fa","7fa","8fa","9fa","10fa","1fp","2fp"]},
        # GNDVI
        **{f"gndvi_{cid}": canchas.get(cid, {}).get("gndvi", "") for cid in [
            "1fa","2fa","3fa","4fa","5fa","6fa","7fa","8fa","9fa","10fa","1fp","2fp"]},
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

    file_exists = HIST_PATH.exists()
    with open(HIST_PATH, "a", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=FIELDS, extrasaction="ignore")
        if not file_exists:
            writer.writeheader()
        writer.writerow(row)

    log.info("historico: appended row %s to %s", row["semana_iso"], HIST_PATH)
    return HIST_PATH
