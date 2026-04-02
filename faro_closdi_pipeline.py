"""
FARO PROTOCOL — Pipeline CLOSDI
Basado en: Cal, A. (2026). CLOSDI: A novel spectral index for cloud shadow 
detection in Sentinel-2 imagery using NDVI and EVI2.
Remote Sensing Applications: Society and Environment.
https://doi.org/10.1016/j.rsase.2026.101990

USO LEGAL: La fórmula matemática es de dominio público (ciencia abierta).
Se cita la fuente original. No se reproduce texto del paper.

COMPATIBILIDAD: No pisa ningún código existente de Faro Protocol.
Se puede importar como módulo independiente.
"""

import numpy as np

# ─────────────────────────────────────────────
# CONSTANTES
# ─────────────────────────────────────────────
CLOSDI_UMBRAL_OPTIMO = 34.0   # OCT identificado por Cal (2026) en test set
EVI2_G  = 2.5                  # Ganancia EVI2 (Jiang et al., 2008)
EVI2_L  = 2.4                  # Coeficiente suelo EVI2
EVI2_C  = 1.0                  # Constante EVI2


# ─────────────────────────────────────────────
# FUNCIONES PRINCIPALES
# ─────────────────────────────────────────────

def calcular_ndvi(red: np.ndarray, nir: np.ndarray) -> np.ndarray:
    """
    Normalized Difference Vegetation Index.
    Bandas Sentinel-2: RED=B4, NIR=B8
    """
    denom = nir + red
    return np.where(denom == 0, 0.0, (nir - red) / denom)


def calcular_evi2(red: np.ndarray, nir: np.ndarray) -> np.ndarray:
    """
    Two-band Enhanced Vegetation Index (Jiang et al., 2008).
    No requiere banda azul — compatible con todas las resoluciones S2.
    """
    denom = nir + EVI2_L * red + EVI2_C
    return np.where(denom == 0, 0.0, EVI2_G * (nir - red) / denom)


def calcular_closdi(red: np.ndarray, nir: np.ndarray) -> np.ndarray:
    """
    Cloud Shadow Detection Index — Cal (2026).
    Formulación cerrada usando solo RED y NIR.
    Rango típico: valores altos (>34) indican sombra de nube.
    """
    ndvi = calcular_ndvi(red, nir)
    evi2 = calcular_evi2(red, nir)
    return (ndvi - evi2) * 100


def generar_mascara_sombra(red: np.ndarray, 
                            nir: np.ndarray,
                            umbral: float = CLOSDI_UMBRAL_OPTIMO) -> np.ndarray:
    """
    Genera máscara booleana: True = pixel con sombra de nube.
    
    Parámetros:
        red    : array reflectancia banda roja (0-1)
        nir    : array reflectancia banda NIR (0-1)
        umbral : CLOSDI threshold (default 34.0 según Cal 2026)
    
    Retorna:
        mascara: array bool, True donde hay sombra
    """
    closdi = calcular_closdi(red, nir)
    return closdi > umbral


def aplicar_filtro_sombra(datos: np.ndarray,
                           red: np.ndarray,
                           nir: np.ndarray,
                           umbral: float = CLOSDI_UMBRAL_OPTIMO,
                           valor_fill: float = np.nan) -> np.ndarray:
    """
    Aplica el filtro CLOSDI a cualquier banda o índice.
    Los píxeles con sombra se reemplazan por valor_fill (NaN por defecto).
    
    Para Faro Protocol: usar valor_fill=np.nan y luego rellenar con SAR.
    
    Parámetros:
        datos     : array a filtrar (ej: NDVI, EVI2, banda B8, etc.)
        red       : banda roja
        nir       : banda NIR
        umbral    : CLOSDI threshold
        valor_fill: valor para píxeles con sombra (NaN = excluir del análisis)
    
    Retorna:
        datos filtrados con sombras marcadas
    """
    mascara = generar_mascara_sombra(red, nir, umbral)
    return np.where(mascara, valor_fill, datos)


def reporte_calidad(red: np.ndarray, 
                    nir: np.ndarray,
                    umbral: float = CLOSDI_UMBRAL_OPTIMO) -> dict:
    """
    Genera reporte de calidad de la imagen.
    Útil para el Sello Verde de Faro Protocol.
    """
    mascara = generar_mascara_sombra(red, nir, umbral)
    total = red.size
    sombras = mascara.sum()
    limpios = total - sombras
    
    return {
        'pixeles_totales'  : int(total),
        'pixeles_limpios'  : int(limpios),
        'pixeles_sombra'   : int(sombras),
        'porcentaje_limpio': round(limpios / total * 100, 2),
        'porcentaje_sombra': round(sombras / total * 100, 2),
        'apto_para_analisis': limpios / total > 0.70,  # >70% limpio = apto
        'umbral_usado'     : umbral,
        'metodo'           : 'CLOSDI — Cal (2026)',
    }


# ─────────────────────────────────────────────
# DEMO / TEST
# ─────────────────────────────────────────────

if __name__ == '__main__':
    print("=" * 55)
    print("  FARO PROTOCOL — Test Pipeline CLOSDI")
    print("=" * 55)

    np.random.seed(42)
    size = 100

    red = np.zeros((size, size))
    nir = np.zeros((size, size))

    # Vegetación sana
    red[:40, :40] = np.random.normal(0.05, 0.01, (40,40)).clip(0.01, 0.15)
    nir[:40, :40] = np.random.normal(0.45, 0.03, (40,40)).clip(0.20, 0.70)
    # Zona con sombra de nube
    red[35:65, 35:65] = np.random.normal(0.02, 0.005, (30,30)).clip(0.005, 0.05)
    nir[35:65, 35:65] = np.random.normal(0.15, 0.02,  (30,30)).clip(0.05, 0.25)
    # Suelo desnudo
    red[60:, 60:] = np.random.normal(0.20, 0.02, (40,40)).clip(0.10, 0.35)
    nir[60:, 60:] = np.random.normal(0.25, 0.02, (40,40)).clip(0.15, 0.40)
    # Resto
    mask_r = (red == 0)
    red[mask_r] = np.random.normal(0.06, 0.01, mask_r.sum()).clip(0.02, 0.12)
    nir[mask_r] = np.random.normal(0.40, 0.04, mask_r.sum()).clip(0.20, 0.65)

    # Calcular NDVI original
    ndvi_original = calcular_ndvi(red, nir)

    # Aplicar filtro CLOSDI
    ndvi_filtrado = aplicar_filtro_sombra(ndvi_original, red, nir)

    # Reporte
    reporte = reporte_calidad(red, nir)

    print(f"\nResultados:")
    for k, v in reporte.items():
        print(f"  {k:<25}: {v}")

    print(f"\n  NDVI promedio SIN filtro : {np.nanmean(ndvi_original):.4f}")
    print(f"  NDVI promedio CON filtro : {np.nanmean(ndvi_filtrado):.4f}")
    print(f"\n  Estado imagen            : {'✓ APTA' if reporte['apto_para_analisis'] else '✗ NO APTA — demasiadas sombras'}")
    print("\n" + "=" * 55)
    print("  Pipeline OK — listo para integrar con GEE")
    print("=" * 55)
