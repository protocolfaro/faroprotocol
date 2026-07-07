import os
import sys
import subprocess
import logging
import time
from datetime import datetime, timezone

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] Railway-Cron-Runner: %(message)s'
)

CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
ORCHESTRATOR_PATH = os.path.join(CURRENT_DIR, "faro_orchestrator.py")

def verify_environment() -> bool:
    logging.info("Iniciando Pre-Flight Check de la infraestructura...")
    if not os.path.exists(ORCHESTRATOR_PATH):
        logging.critical(f"Ruta crítica inaccesible: Falta {ORCHESTRATOR_PATH}")
        return False
    return True

def execute_pipeline() -> bool:
    """
    Ejecuta el orquestador principal con timeout de 120s y backoff escalonado
    para absorber micro-cortes de red sin colgar el contenedor de Railway.
    """
    max_retries = 3

    for attempt in range(1, max_retries + 1):
        logging.info(f"Lanzando Pipeline Autónomo de Faro (Intento {attempt}/{max_retries})...")

        try:
            result = subprocess.run(
                [sys.executable, ORCHESTRATOR_PATH],
                capture_output=True,
                text=True,
                check=True,
                timeout=120
            )
            logging.info("Faro Cerebro completó su ejecución de fase con éxito.")
            print(result.stdout)
            return True

        except subprocess.TimeoutExpired:
            logging.error(f"Intento {attempt} abortado por Timeout (límite 120s excedido).")
            if attempt < max_retries:
                wait_time = attempt * 15
                logging.warning(f"Reintentando en {wait_time}s...")
                time.sleep(wait_time)

        except subprocess.CalledProcessError as err:
            logging.error(f"Falla en ejecución. Código de salida: {err.returncode}")
            logging.error(f"Stderr:\n{err.stderr}")
            if attempt < max_retries:
                wait_time = attempt * 15
                logging.info(f"Aplicando ventana de mitigación. Reintentando en {wait_time}s...")
                time.sleep(wait_time)
            else:
                logging.error("Se agotaron los intentos de contingencia.")

    return False

if __name__ == "__main__":
    logging.info("=== ORQUESTADOR DE INTEGRIDAD AUTOMÁTICA SENTINEL-1 INICIADO ===")
    logging.info(f"Ejecución disparada: {datetime.now(timezone.utc).isoformat()}Z")

    if not verify_environment():
        sys.exit(1)

    if execute_pipeline():
        logging.info("=== PROCESAMIENTO FINALIZADO CORRECTAMENTE ===")
        sys.exit(0)
    else:
        logging.critical("=== PROCESAMIENTO COMPROMETIDO — REVISAR LOGS ===")
        sys.exit(1)
