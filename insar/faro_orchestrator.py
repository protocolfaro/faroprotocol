import json
import logging
import os
import sys
from datetime import datetime, timezone
import numpy as np
import requests
from typing import Dict, Any, Optional

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] Faro-Cerebro: %(message)s'
)

# ── Imports dinámicos desde railway-backend ───────────────────────────────────
_velez_path = os.path.abspath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)),
                 '..', 'railway-backend', 'sports', 'clients', 'velez')
)
if _velez_path not in sys.path:
    sys.path.insert(0, _velez_path)

try:
    from data_refresh import run_insar_refresh
    REFRESH_AVAILABLE = True
except ImportError as e:
    logging.warning(f"data_refresh no importable: {e}")
    REFRESH_AVAILABLE = False

try:
    import velez_supabase as _vs
    SUPA_AVAILABLE = True
except ImportError:
    SUPA_AVAILABLE = False

VELEZ_LAT = -34.6356
VELEZ_LON = -58.5238

# Escala desplazamiento LOS → coherencia proxy: exp(-|mm| / 3mm)
_DISP_SCALE_MM = 3.0

# Orden de sectores HyP3 → grilla 3×3 Cross-Trust
_SECTOR_GRID = [
    ["estadio_tribuna_norte", "estadio",         "estadio_tribuna_sur"],
    ["estadio_tribuna_este",  "poli_basquet",     "estadio_tribuna_oeste"],
    ["poli_playon_norte",     "sede_anexo_norte", "piletas"],
]


def fetch_real_time_precipitation() -> float:
    url = (
        f"https://api.open-meteo.com/v1/forecast?"
        f"latitude={VELEZ_LAT}&longitude={VELEZ_LON}"
        f"&hourly=precipitation"
        f"&timezone=America/Argentina/Buenos_Aires"
        f"&past_days=1&forecast_days=0"
    )
    try:
        logging.info("Consultando Open-Meteo (últimas 24h reales)...")
        r = requests.get(url, timeout=10)
        r.raise_for_status()
        precip = r.json().get("hourly", {}).get("precipitation", [])
        total = sum(precip[-24:]) if precip else 0.0
        logging.info(f"Precipitación Liniers 24h: {total:.2f} mm")
        return float(total)
    except Exception as e:
        logging.warning(f"Open-Meteo error: {e} → fallback 0.0mm")
        return 0.0


def _build_coherence_from_sectors(sector_mm: dict) -> Optional[np.ndarray]:
    """Convierte desplazamiento LOS (mm) → coherencia proxy 0-1 para Cross-Trust."""
    if not sector_mm:
        return None
    grid = np.zeros((3, 3), dtype=np.float32)
    for i, row in enumerate(_SECTOR_GRID):
        for j, sid in enumerate(row):
            mm = abs(sector_mm.get(sid, 0.0))
            grid[i, j] = float(np.exp(-mm / _DISP_SCALE_MM))
    return grid


def run_cross_trust_audit(
    coherence_matrix: np.ndarray,
    rain_24h: float,
    insar_fuente: str = "simulación calibrada"
) -> Dict[str, Any]:
    """Motor Cross-Trust: coherencia × lluvia → prescripciones por cuadrante."""
    rows, cols = coherence_matrix.shape
    r_step = max(1, rows // 3)
    c_step = max(1, cols // 3)

    sectores_auditados = []
    coherence_losses = confirmed_mows = anomalies_detected = 0

    for i in range(3):
        for j in range(3):
            sub = coherence_matrix[i*r_step:(i+1)*r_step, j*c_step:(j+1)*c_step]
            coh = float(np.nanmean(sub)) if sub.size > 0 else 0.35

            if coh >= 0.50:
                status  = "CORTE CONFIRMADO (DOSAL ESTABLE)"
                action  = "Dosel uniforme. Corte validado sin reporte manual."
                urgency = "ESTABLE"
                confirmed_mows += 1
            elif coh <= 0.15:
                if rain_24h > 1.5:
                    status  = "ANOMALÍA POR PRECIPITACIÓN"
                    action  = f"Suelo saturado ({rain_24h:.1f}mm). Prohibir ingreso de tractores Toro."
                    urgency = "ALTA"
                    anomalies_detected += 1
                else:
                    status  = "DESGASTE EXTREMO POST-EVENTO"
                    action  = "Pisoteo severo o riego masivo. Programar Toro ProCore 648."
                    urgency = "CRITICA"
                    coherence_losses += 1
            else:
                status  = "INERCIA NORMAL"
                action  = "Evolución foliar típica. Monitoreo pasivo activo."
                urgency = "ESTABLE"

            sectores_auditados.append({
                "sector_id": f"{i+1},{j+1}",
                "coherencia_gamma": round(coh, 2),
                "estado_fase": status,
                "urgencia": urgency,
                "accion_prescripta": action,
            })

    if coherence_losses >= 3:
        diagnosis = "DESGASTE ESTRUCTURAL SEVERO GLOBAL"
    elif confirmed_mows >= 5:
        diagnosis = "MANTENIMIENTO DE CORTE CONFIRMADO Y HOMOGÉNEO"
    elif anomalies_detected >= 4:
        diagnosis = "INUNDACIÓN O ALTA ATENUACIÓN POR AGUA LIBRE"
    else:
        diagnosis = "INERCIA AGRONÓMICA ESTABLE"

    return {
        "timestamp":         datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "stadium":           "Estadio José Amalfitani (Vélez)",
        "insar_fuente":      insar_fuente,
        "weather_rain_24h_mm": rain_24h,
        "global_diagnosis":  diagnosis,
        "cuadrantes":        sectores_auditados,
    }


def _push_cross_trust_to_supa(audit: Dict[str, Any]) -> bool:
    """Persiste el diagnóstico Cross-Trust en velez_sectores.estadio.cross_trust."""
    if not SUPA_AVAILABLE:
        return False
    try:
        ok = _vs.upsert_sectores({
            "estadio": {"cross_trust": json.dumps(audit, ensure_ascii=False)}
        })
        if ok:
            logging.info("Cross-Trust persistido en velez_sectores.estadio")
        return ok
    except Exception as e:
        logging.warning(f"Cross-Trust Supabase push (non-fatal): {e}")
        return False


if __name__ == "__main__":
    logging.info("=== FARO CEREBRO v3 — PIPELINE UNIFICADO ===")

    rain = fetch_real_time_precipitation()

    # 1. InSAR real: delega TODO a run_insar_refresh() de data_refresh.py
    #    → fetch_insar() → push_insar_update() → velez_sectores + velez_data.json
    sector_mm   = {}
    insar_fuente = "simulación calibrada"

    if REFRESH_AVAILABLE:
        try:
            logging.info("Ejecutando run_insar_refresh() → HyP3 + VD update...")
            result = run_insar_refresh()
            if result.get("ok"):
                sector_mm    = result.get("sectores", {})
                insar_fuente = "Sentinel-1 InSAR · ASF HyP3"
                logging.info(f"InSAR OK: {len(sector_mm)} sectores actualizados en VD")
            else:
                logging.info(f"InSAR pendiente: {result.get('error','')}")
        except Exception as e:
            logging.warning(f"run_insar_refresh error: {e}")
    else:
        logging.warning("data_refresh no disponible. Usando simulación.")

    # 2. Construir grilla de coherencia (de datos reales o simulación de calibración)
    coherence_matrix = _build_coherence_from_sectors(sector_mm)
    if coherence_matrix is None:
        logging.warning("Sin sectores reales → simulación calibrada Amalfitani")
        coherence_matrix = np.array([
            [0.55, 0.52, 0.48],
            [0.51, 0.12, 0.49],
            [0.53, 0.11, 0.50]
        ])

    # 3. Motor Cross-Trust: diagnóstico por cuadrante
    audit = run_cross_trust_audit(coherence_matrix, rain, insar_fuente=insar_fuente)

    # 4. Persistir Cross-Trust en velez_sectores.estadio.cross_trust
    _push_cross_trust_to_supa(audit)

    # 5. Log de salida para Railway
    print(json.dumps(audit, indent=2, ensure_ascii=False))
    logging.info("=== PIPELINE COMPLETADO ===")
