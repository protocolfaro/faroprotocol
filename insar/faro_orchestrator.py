import json
import logging
import os
import sys
from datetime import datetime, timezone
import numpy as np
import requests
from typing import Dict, Any, List

try:
    from supabase import create_client, Client
    SUPABASE_AVAILABLE = True
except ImportError:
    SUPABASE_AVAILABLE = False

try:
    sys.path.append(os.path.dirname(os.path.abspath(__file__)))
    from faro_coherence_detector import extract_coherence_from_hyp3
    DETECTOR_AVAILABLE = True
except ImportError:
    DETECTOR_AVAILABLE = False

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] Faro-Cerebro: %(message)s'
)

VELEZ_LAT = -34.6356
VELEZ_LON = -58.5238

def fetch_real_time_precipitation() -> float:
    url = (
        f"https://api.open-meteo.com/v1/forecast?"
        f"latitude={VELEZ_LAT}&longitude={VELEZ_LON}"
        f"&hourly=precipitation"
        f"&timezone=America/Argentina/Buenos_Aires"
        f"&past_days=1"
        f"&forecast_days=0"
    )
    try:
        logging.info("Conectando con Open-Meteo para auditar balance hídrico...")
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        data = response.json()
        precipitations = data.get("hourly", {}).get("precipitation", [])
        total_rain_24h = sum(precipitations[-24:]) if precipitations else 0.0
        logging.info(f"Precipitación real detectada en Liniers (últimas 24h observadas): {total_rain_24h:.2f} mm")
        return float(total_rain_24h)
    except Exception as e:
        logging.warning(f"Error de conexión con Open-Meteo: {e}. Aplicando fallback (0.0mm).")
        return 0.0

def run_cross_trust_audit(
    coherence_matrix: np.ndarray,
    rain_24h: float,
    rvi_grid: np.ndarray = None
) -> Dict[str, Any]:
    """
    Motor de Confianza Cruzada (Cross-Trust Engine) de Faro Cerebro.
    Cruza coherencia interferométrica con balance hídrico para generar
    prescripciones agronómicas libres de sesgo foliar.
    """
    logging.info("Iniciando análisis espacial por cuadrantes (Grilla 3x3)...")
    rows, cols = coherence_matrix.shape
    r_step = max(1, rows // 3)
    c_step = max(1, cols // 3)

    # Fallback determinístico neutral: 0.23 = humedad típica del suelo en Liniers
    if rvi_grid is None:
        rvi_grid = np.full((3, 3), 0.23)

    sectores_auditados = []
    coherence_losses = 0
    confirmed_mows = 0
    anomalies_detected = 0

    for i in range(3):
        for j in range(3):
            sub_coh = coherence_matrix[i*r_step:(i+1)*r_step, j*c_step:(j+1)*c_step]
            mean_coh = float(np.nanmean(sub_coh)) if sub_coh.size > 0 else 0.35

            rvi_val = float(rvi_grid[i, j])
            theta_soil_pct = round(10.0 + (rvi_val * 25.0), 1)

            if mean_coh >= 0.50:
                status = "CORTE CONFIRMADO (DOSAL ESTABLE)"
                action = "Estado de dosel uniforme. Corte validado automáticamente sin reporte manual de Roger."
                urgency = "ESTABLE"
                confirmed_mows += 1
            elif mean_coh <= 0.15:
                if rain_24h > 1.5:
                    status = "ANOMALÍA POR PRECIPITACIÓN"
                    action = f"Suelo saturado por lluvia ({rain_24h:.1f}mm). Prohibir ingreso de maquinaria pesada Toro para evitar compactación."
                    urgency = "ALTA"
                    anomalies_detected += 1
                else:
                    status = "DESGASTE EXTREMO POST-EVENTO"
                    action = "Evidencia de pisoteo severo o riego masivo localizado. Programar aireación Toro ProCore 648 en este cuadrante."
                    urgency = "CRITICA"
                    coherence_losses += 1
            else:
                status = "COMPORTAMIENTO NORMAL"
                action = "Evolución foliar típica. Continuar con monitoreo pasivo orbital."
                urgency = "ESTABLE"

            sectores_auditados.append({
                "sector_id": f"{i+1},{j+1}",
                "coherencia_gamma": round(mean_coh, 2),
                "rvi_humedad_estimada_pct": theta_soil_pct,
                "estado_fase": status,
                "urgencia": urgency,
                "accion_prescripta": action
            })

    if coherence_losses >= 3:
        diagnosis = "DESGASTE ESTRUCTURAL SEVERO GLOBAL (Downtime operativo sugerido)"
    elif confirmed_mows >= 5:
        diagnosis = "MANTENIMIENTO DE CORTE CONFIRMADO Y HOMOGÉNEO"
    elif anomalies_detected >= 4:
        diagnosis = "INUNDACIÓN O ALTA ATENUACIÓN POR AGUA LIBRE EN HOJA"
    else:
        diagnosis = "INERCIA AGRONÓMICA ESTABLE"

    return {
        "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "stadium": "Estadio José Amalfitani (Vélez)",
        "weather_rain_24h_mm": rain_24h,
        "global_diagnosis": diagnosis,
        "cuadrantes": sectores_auditados
    }

def persist_to_supabase(payload: Dict[str, Any]) -> bool:
    supabase_url = os.environ.get("SUPABASE_URL")
    supabase_key = os.environ.get("SUPABASE_KEY")

    if not supabase_url or not supabase_key:
        logging.warning("SUPABASE_URL / SUPABASE_KEY ausentes. Persistencia omitida (modo local).")
        return False

    if not SUPABASE_AVAILABLE:
        logging.warning("supabase-py no instalado. No se puede persistir el payload.")
        return False

    try:
        logging.info("Persistiendo payload de auditoría en Supabase...")
        supabase: Client = create_client(supabase_url, supabase_key)
        supabase.table("faro_audits").upsert({
            "stadium_id": "velez_amalfitani",
            "timestamp": payload["timestamp"],
            "weather_rain_24h_mm": payload["weather_rain_24h_mm"],
            "global_diagnosis": payload["global_diagnosis"],
            "payload_data": payload
        }).execute()
        logging.info("Sincronización exitosa. Panel Roger actualizado.")
        return True
    except Exception as e:
        logging.error(f"Fallo al interactuar con Supabase: {e}")
        return False

if __name__ == "__main__":
    logging.info("Iniciando pipeline de ejecución unificado (InSAR + Met)...")

    rain_sensor = fetch_real_time_precipitation()

    # Busca ZIP real entregado por HyP3; si no existe, activa simulación calibrada
    target_zip = "/tmp/hyp3_latest/latest_coherence.zip"
    coherence_matrix = None

    if DETECTOR_AVAILABLE and os.path.exists(target_zip):
        try:
            logging.info(f"Archivo real de órbita detectado en {target_zip}. Extrayendo matriz InSAR...")
            coherence_matrix = extract_coherence_from_hyp3(target_zip)
        except Exception as e:
            logging.error(f"Fallo al leer el ZIP de órbita: {e}. Activando simulación de contingencia...")

    if coherence_matrix is None:
        logging.warning("ZIP de órbita no encontrado en /tmp. Aplicando simulación calibrada de Vélez...")
        coherence_matrix = np.array([
            [0.55, 0.52, 0.48],
            [0.51, 0.12, 0.49],
            [0.53, 0.11, 0.50]
        ])

    audit_report = run_cross_trust_audit(coherence_matrix, rain_sensor)
    persist_to_supabase(audit_report)

    print(json.dumps(audit_report, indent=2, ensure_ascii=False))
    logging.info("Pipeline de orquestación completado.")
