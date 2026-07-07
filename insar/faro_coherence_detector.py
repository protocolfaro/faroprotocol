import os
import shutil
import zipfile
import numpy as np
import rasterio
from rasterio.warp import transform_bounds
from typing import Dict, Any, List

# Coordenadas geográficas exactas de la alfombra híbrida de juego del Amalfitani (WGS84)
VELEZ_BBOX = {
    "lat_max": -34.6351,
    "lat_min": -34.6361,
    "lon_min": -58.5244,
    "lon_max": -58.5232
}

def extract_coherence_from_hyp3(zip_path: str) -> np.ndarray:
    """
    Busca y extrae la matriz de coherencia interferométrica (_corr.tif)
    generada por el procesamiento SLC de HyP3 de la ASF.

    Evita procesar fase cruda en Railway delegando el cálculo pesado a los servidores de Alaska.
    """
    if not os.path.exists(zip_path):
        raise FileNotFoundError(f"Error: No se encontró el entregable de HyP3 en {zip_path}")

    temp_extract_dir = "./temp_hyp3"
    os.makedirs(temp_extract_dir, exist_ok=True)

    with zipfile.ZipFile(zip_path, 'r') as zip_ref:
        for file in zip_ref.namelist():
            if file.endswith("_corr.tif") or file.endswith("_coh.tif"):
                zip_ref.extract(file, temp_extract_dir)
                target_tif = os.path.join(temp_extract_dir, file)
                break
        else:
            raise FileNotFoundError("Error crítico: El paquete HyP3 no contiene la capa de coherencia (_corr.tif)")

    with rasterio.open(target_tif) as src:
        # fix: comparar EPSG numérico, no string contra objeto rasterio.CRS
        if src.crs.to_epsg() != 4326:
            left, bottom, right, top = transform_bounds(
                "EPSG:4326", src.crs,
                VELEZ_BBOX["lon_min"], VELEZ_BBOX["lat_min"],
                VELEZ_BBOX["lon_max"], VELEZ_BBOX["lat_max"]
            )
        else:
            left, bottom, right, top = (
                VELEZ_BBOX["lon_min"], VELEZ_BBOX["lat_min"],
                VELEZ_BBOX["lon_max"], VELEZ_BBOX["lat_max"]
            )

        window = src.window(left, bottom, right, top)
        coherence_matrix = src.read(1, window=window).astype(np.float32)

    shutil.rmtree(temp_extract_dir, ignore_errors=True)

    # La coherencia oscila estrictamente entre 0.0 (fase caótica) y 1.0 (fase estable)
    return np.clip(coherence_matrix, 0.0, 1.0)

def audit_field_operations(
    coherence_grid: np.ndarray,
    rain_24h: float
) -> List[Dict[str, Any]]:
    """
    Motor de Confianza Cruzada (Faro v3)
    Cruza la pérdida de coherencia de fase (SLC) con el balance hídrico atmosférico (Open-Meteo).

    Fórmula de Decisión Calibrada para Césped Deportivo Corto:
    - Alta coherencia (coh >= 0.50) + No lluvia -> Corte de Césped Confirmado.
    - Caída de coherencia (coh <= 0.15) + No lluvia -> Partido / Pisoteo Extremo.
    - Caída de coherencia (coh <= 0.15) + Lluvia -> Evento Climático.
    """
    rows, cols = coherence_grid.shape
    r_step = max(1, rows // 3)
    c_step = max(1, cols // 3)

    sectores_auditados = []

    for i in range(3):
        for j in range(3):
            sub_matrix = coherence_grid[i*r_step:(i+1)*r_step, j*c_step:(j+1)*c_step]
            mean_coherence = float(np.nanmean(sub_matrix)) if sub_matrix.size > 0 else 0.35

            if mean_coherence >= 0.50:
                status_fase = "ESTABLE (CORTE RECIENTE DETECTADO)"
                urgencia = "ESTABLE"
                recomendacion = "Estado de dosel óptimo y peinado uniforme. Corte confirmado automáticamente sin reporte manual."
            elif mean_coherence <= 0.15:
                if rain_24h > 1.5:
                    status_fase = "DESCORRELACIONADO POR LLUVIA"
                    urgencia = "ALTA"
                    recomendacion = "Suelo mojado por precipitación. Riesgo de deformación plástica. Prohibir ingreso de tractores Toro."
                else:
                    status_fase = "DESCORRELACIONADO POR DESGASTE / HUMEDAD"
                    urgencia = "CRITICA"
                    recomendacion = "Evidencia de pisoteo intenso post-partido o riego manual pesado. Programar descompactación con Toro ProCore 648."
            else:
                status_fase = "INERCIA NORMAL"
                urgencia = "ESTABLE"
                recomendacion = "Inercia foliar típica dentro de los parámetros de competencia. Monitoreo pasivo activo."

            sectores_auditados.append({
                "id": f"{i+1},{j+1}",
                "coherencia_gamma": round(mean_coherence, 2),
                "estado_fase": status_fase,
                "urgencia_operacion": urgencia,
                "accion_prescripta": recomendacion
            })

    return sectores_auditados

if __name__ == "__main__":
    print("--- Faro v3: Verificación de Ingesta Coherencia (Césped Deportivo) ---")

    mock_coherence = np.array([
        [0.55, 0.52, 0.48],
        [0.38, 0.11, 0.42],
        [0.51, 0.49, 0.53]
    ])

    mock_rain = 0.0

    auditoria = audit_field_operations(mock_coherence, mock_rain)

    for sec in auditoria:
        print(f"Sector {sec['id']} | Coherencia: {sec['coherencia_gamma']} | Fase: {sec['estado_fase']} | Urgencia: {sec['urgencia_operacion']}")
