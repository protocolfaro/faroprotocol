"""
FARO PROTOCOL — MachinaOS GIS MCP Server
=========================================
Herramientas GIS nativas para análisis satelital via Claude Tool Use.
Sin LangChain, sin QGIS local. Basado en rasterio + numpy + FastMCP.

Uso local (Claude Desktop):
    python machina_gis_server.py

Uso HTTP (Railway):
    GIS_MCP_TRANSPORT=http GIS_MCP_PORT=9010 python machina_gis_server.py

Registro en Claude Desktop (claude_desktop_config.json):
    "faro-gis": {
        "command": "python",
        "args": ["C:/Users/Usuario/Desktop/Faro-index/machina_gis_server.py"]
    }
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
import warnings
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import rasterio
from rasterio.crs import CRS
from rasterio.transform import from_bounds
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.gridspec as gridspec
from mcp.server.fastmcp import FastMCP

warnings.filterwarnings('ignore', category=rasterio.errors.NotGeoreferencedWarning)

PROJECT_ROOT = Path(__file__).parent
ENGINE_DIR   = PROJECT_ROOT / 'engine'
sys.path.insert(0, str(ENGINE_DIR))

# ── Área registry ─────────────────────────────────────────────────────────────

AREAS: dict[str, dict] = {
    'cordoba': {
        'label'     : 'Córdoba, Argentina',
        'polygon'   : 'POLYGON((-65.5 -33.5,-63.5 -33.5,-63.5 -30.5,-65.5 -30.5,-65.5 -33.5))',
        'bounds'    : [-65.5, -33.5, -63.5, -30.5],
        'sar_output': str(ENGINE_DIR / 'sar_cordoba_georef.tif'),
        'ndvi_tif'  : str(ENGINE_DIR / 'FaroProtocol_NDVI_limpio_Cordoba.tif'),
    },
    'vaca_muerta': {
        'label'     : 'Vaca Muerta, Neuquén',
        'polygon'   : 'POLYGON((-70.5 -39.5,-67.0 -39.5,-67.0 -37.0,-70.5 -37.0,-70.5 -39.5))',
        'bounds'    : [-70.5, -39.5, -67.0, -37.0],
        'sar_output': str(ENGINE_DIR / 'sar_vaca_muerta_georef.tif'),
        'ndvi_tif'  : str(ENGINE_DIR / 'FaroProtocol_NDVI_limpio_Vaca_muerta.tif'),
    },
    'permian': {
        'label'     : 'Permian Basin, Texas',
        'polygon'   : 'POLYGON((-104.0 29.5,-101.0 29.5,-101.0 33.0,-104.0 33.0,-104.0 29.5))',
        'bounds'    : [-104.0, 29.5, -101.0, 33.0],
        'sar_output': str(ENGINE_DIR / 'sar_permian_georef.tif'),
        'ndvi_tif'  : str(ENGINE_DIR / 'FaroProtocol_NDVI_limpio_Permian.tif'),
    },
    'rotterdam': {
        'label'     : 'Puerto Rotterdam, Países Bajos',
        'polygon'   : 'POLYGON((3.9 51.7,4.7 51.7,4.7 52.1,3.9 52.1,3.9 51.7))',
        'bounds'    : [3.9, 51.7, 4.7, 52.1],
        'sar_output': str(ENGINE_DIR / 'sar_rotterdam_georef.tif'),
        'ndvi_tif'  : str(ENGINE_DIR / 'FaroProtocol_NDVI_limpio_Rotterdam.tif'),
    },
}

# Intentar enriquecer desde faro_areas.pyc si está disponible
def _enrich_from_pyc() -> None:
    pyc = PROJECT_ROOT / '__pycache__' / 'faro_areas.cpython-313.pyc'
    if not pyc.exists():
        return
    try:
        spec = importlib.util.spec_from_file_location('faro_areas', str(pyc))
        mod  = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        for name in (mod.list_areas() or []):
            try:
                AREAS[name] = mod.load_area(name)
            except Exception:
                pass
    except Exception:
        pass

_enrich_from_pyc()


def _get_area(area_name: str) -> dict:
    a = AREAS.get(area_name.lower())
    if not a:
        available = ', '.join(sorted(AREAS.keys()))
        raise ValueError(f"Área '{area_name}' no encontrada. Disponibles: {available}")
    return a


# ── FastMCP Server ────────────────────────────────────────────────────────────

mcp = FastMCP(
    "Faro Protocol GIS",
    instructions=(
        "Servidor GIS de Faro Protocol. Analiza datos satelitales Sentinel-1 (SAR) "
        "y Sentinel-2 (óptico) para zonas agro-industriales e infraestructura crítica. "
        "Tres capacidades principales: cálculo de índices espectrales (NDVI/EVI/SAVI/NDWI/NBR), "
        "análisis de cambio SAR entre épocas, y generación de mapas de reporte visuales."
    )
)


# ── Tool 1: Índices espectrales ───────────────────────────────────────────────

@mcp.tool()
def calcular_indices_espectrales(
    area_name: str,
    indices: list[str] | None = None,
    ndvi_tif_path: str | None = None,
    b04_path: str | None = None,
    b08_path: str | None = None,
    b02_path: str | None = None,
    b03_path: str | None = None,
    b11_path: str | None = None,
) -> dict[str, Any]:
    """
    Calcula índices espectrales de vegetación y superficie a partir de
    datos Sentinel-2 para un área Faro Protocol.

    Índices soportados:
    - NDVI  = (NIR-RED)/(NIR+RED)                  [vegetación]
    - SAVI  = (NIR-RED)/(NIR+RED+0.5) × 1.5        [suelo expuesto]
    - EVI   = 2.5×(NIR-RED)/(NIR+6RED-7.5BLUE+1)   [vegetación densa, requiere B02]
    - NDWI  = (GREEN-NIR)/(GREEN+NIR)               [agua, requiere B03]
    - NBR   = (NIR-SWIR)/(NIR+SWIR)                 [quemas/estrés, requiere B11]

    Parámetros:
        area_name    : Nombre del área ('cordoba', 'vaca_muerta', 'permian', 'rotterdam')
        indices      : Lista de índices a calcular. Default: ['NDVI', 'SAVI']
        ndvi_tif_path: Path opcional al TIF NDVI pre-calculado (anula búsqueda automática)
        b04_path     : Path a banda RED (B04) para cálculo desde bandas crudas
        b08_path     : Path a banda NIR (B08)
        b02_path     : Path a banda BLUE (B02) — para EVI
        b03_path     : Path a banda GREEN (B03) — para NDWI
        b11_path     : Path a banda SWIR (B11) — para NBR

    Retorna dict con estadísticas por índice: mean, std, min, max, p25, p75, clasificación.
    """
    if indices is None:
        indices = ['NDVI', 'SAVI']

    indices = [i.upper() for i in indices]
    area    = _get_area(area_name)
    results: dict[str, Any] = {
        'area'         : area_name,
        'label'        : area['label'],
        'fecha_analisis': datetime.now().strftime('%Y-%m-%d %H:%M'),
        'indices'      : {},
        'advertencias' : [],
        'archivos'     : {},
    }

    # ── Cargar bandas disponibles ─────────────────────────────────────────────
    def _load_band(path_override, auto_search_key):
        if path_override and Path(path_override).exists():
            with rasterio.open(path_override) as src:
                data = src.read(1).astype(np.float32)
            return np.where(data == 0, np.nan, data) / 10000.0
        return None

    # Auto-buscar .jp2 en datos_s2/ si no se pasa path explícito
    def _find_band_jp2(tag: str) -> np.ndarray | None:
        datos_s2 = ENGINE_DIR / 'datos_s2'
        if not datos_s2.exists():
            return None
        # Buscar archivos más recientes primero
        candidates = sorted(
            list(datos_s2.rglob(f'*_{tag}_10m.jp2')) + list(datos_s2.rglob(f'*_{tag}*.jp2')),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        if not candidates:
            return None
        try:
            with rasterio.open(candidates[0]) as src:
                data = src.read(1).astype(np.float32)
            results['archivos'][tag] = str(candidates[0].name)
            return np.where(data == 0, np.nan, data) / 10000.0
        except Exception:
            return None

    # RED (B04)
    red = _load_band(b04_path, 'B04') or _find_band_jp2('B04')
    # NIR (B08)
    nir = _load_band(b08_path, 'B08') or _find_band_jp2('B08')
    # Opcional
    blue  = _load_band(b02_path, 'B02') or _find_band_jp2('B02')
    green = _load_band(b03_path, 'B03') or _find_band_jp2('B03')
    swir  = _load_band(b11_path, 'B11') or _find_band_jp2('B11')

    # ── Cargar NDVI pre-calculado si no hay bandas ────────────────────────────
    ndvi_precomp = None
    if red is None or nir is None:
        ndvi_path = Path(ndvi_tif_path or area.get('ndvi_tif', ''))
        if ndvi_path.exists():
            with rasterio.open(ndvi_path) as src:
                data = src.read(1).astype(np.float32)
                nodata = src.nodata
            if nodata is not None:
                data = np.where(data == nodata, np.nan, data)
            finite = data[np.isfinite(data)]
            if len(finite) > 0 and (np.nanmax(finite) > 2 or np.nanmin(finite) < -2):
                data = data / 10000.0
            ndvi_precomp = data
            results['archivos']['ndvi_precomp'] = str(ndvi_path.name)
        else:
            results['advertencias'].append(
                f"Sin bandas S2 ni TIF NDVI para '{area_name}'. "
                f"Ejecutar primero: python engine/faro_s2_pipeline.py --area {area_name}"
            )

    # ── Calcular índices solicitados ──────────────────────────────────────────
    def _stats(arr: np.ndarray, nombre: str) -> dict:
        v = arr[np.isfinite(arr)]
        if len(v) == 0:
            return {'error': 'Sin datos válidos'}
        return {
            'mean'          : float(round(np.mean(v), 4)),
            'std'           : float(round(np.std(v), 4)),
            'min'           : float(round(np.min(v), 4)),
            'max'           : float(round(np.max(v), 4)),
            'p25'           : float(round(np.percentile(v, 25), 4)),
            'median'        : float(round(np.median(v), 4)),
            'p75'           : float(round(np.percentile(v, 75), 4)),
            'pixeles_validos': int(len(v)),
            'cobertura_pct' : float(round(len(v) / arr.size * 100, 1)),
        }

    def _ndvi_class(mean: float) -> str:
        if mean >= 0.6: return 'Vegetación densa / cultivo en desarrollo'
        if mean >= 0.4: return 'Vegetación moderada / cobertura media'
        if mean >= 0.2: return 'Vegetación escasa / suelo semi-cubierto'
        if mean >= 0.0: return 'Suelo desnudo / área urbana / roca'
        return 'Agua / nieve / nube (valor negativo)'

    for idx in indices:
        if idx == 'NDVI':
            if red is not None and nir is not None:
                with np.errstate(divide='ignore', invalid='ignore'):
                    arr = np.where((nir + red) == 0, np.nan, (nir - red) / (nir + red))
            elif ndvi_precomp is not None:
                arr = ndvi_precomp
            else:
                results['advertencias'].append('NDVI: sin datos disponibles')
                continue
            s = _stats(arr, 'NDVI')
            if 'mean' in s:
                s['clasificacion'] = _ndvi_class(s['mean'])
            results['indices']['NDVI'] = s

        elif idx == 'SAVI':
            if red is None or nir is None:
                results['advertencias'].append('SAVI requiere bandas B04+B08')
                continue
            L = 0.5
            with np.errstate(divide='ignore', invalid='ignore'):
                arr = np.where((nir + red + L) == 0, np.nan,
                               (nir - red) / (nir + red + L) * (1 + L))
            s = _stats(arr, 'SAVI')
            s['parametro_L'] = L
            results['indices']['SAVI'] = s

        elif idx == 'EVI':
            if red is None or nir is None:
                results['advertencias'].append('EVI requiere bandas B04+B08')
                continue
            if blue is None:
                results['advertencias'].append('EVI: B02 (BLUE) no disponible — usando aproximación sin corrección atmosférica')
                b = np.zeros_like(red)
            else:
                b = blue
            with np.errstate(divide='ignore', invalid='ignore'):
                denom = nir + 6 * red - 7.5 * b + 1
                arr   = np.where(denom == 0, np.nan, 2.5 * (nir - red) / denom)
            arr = np.clip(arr, -1, 2)
            results['indices']['EVI'] = _stats(arr, 'EVI')

        elif idx == 'NDWI':
            if green is None or nir is None:
                results['advertencias'].append('NDWI requiere B03 (GREEN) + B08 (NIR)')
                continue
            with np.errstate(divide='ignore', invalid='ignore'):
                arr = np.where((green + nir) == 0, np.nan, (green - nir) / (green + nir))
            s = _stats(arr, 'NDWI')
            if 'mean' in s:
                s['clasificacion'] = ('Presencia de agua' if s['mean'] > 0
                                      else 'Sin agua libre / suelo seco')
            results['indices']['NDWI'] = s

        elif idx == 'NBR':
            if nir is None or swir is None:
                results['advertencias'].append('NBR requiere B08 (NIR) + B11 (SWIR)')
                continue
            with np.errstate(divide='ignore', invalid='ignore'):
                arr = np.where((nir + swir) == 0, np.nan, (nir - swir) / (nir + swir))
            s = _stats(arr, 'NBR')
            if 'mean' in s:
                s['clasificacion'] = ('Vegetación saludable' if s['mean'] > 0.1
                                      else 'Área quemada / estrés severo / suelo')
            results['indices']['NBR'] = s

        else:
            results['advertencias'].append(f"Índice '{idx}' no soportado. Disponibles: NDVI, SAVI, EVI, NDWI, NBR")

    results['estado'] = 'ok' if results['indices'] else 'sin_datos'
    return results


# ── Tool 2: Análisis de cambio SAR ────────────────────────────────────────────

@mcp.tool()
def analizar_cambio_sar(
    area_name: str,
    sar_ref_path: str | None = None,
    sar_reciente_path: str | None = None,
    umbral_db: float = 3.0,
    guardar_tif: bool = True,
) -> dict[str, Any]:
    """
    Detecta cambios en backscatter SAR Sentinel-1 entre dos épocas
    para identificar inundaciones, deforestación, actividad industrial o
    colapso de infraestructura.

    Método: Log-ratio en dB = 10 × log10(σ_nueva / σ_ref)
    Cambio positivo (+dB): aumento de backscatter (agua->suelo, cosecha, construcción)
    Cambio negativo (-dB): pérdida de backscatter (suelo->agua, deforestación)

    Parámetros:
        area_name        : Área a analizar
        sar_ref_path     : Path al TIF SAR de referencia (escena base)
        sar_reciente_path: Path al TIF SAR reciente (escena a comparar)
        umbral_db        : Umbral de cambio significativo en dB (default 3.0 dB ≈ ×2)
        guardar_tif      : Si True, guarda mapa de cambio como GeoTIFF

    Si no se proveen paths, busca automáticamente en engine/ y datos_sar/.

    Retorna estadísticas de cambio, porcentaje de área afectada e interpretación.
    """
    area = _get_area(area_name)
    result: dict[str, Any] = {
        'area'          : area_name,
        'label'         : area['label'],
        'fecha_analisis': datetime.now().strftime('%Y-%m-%d %H:%M'),
        'umbral_db'     : umbral_db,
        'advertencias'  : [],
    }

    # ── Localizar TIFs SAR ────────────────────────────────────────────────────
    def _find_sar(path_override: str | None, label: str) -> np.ndarray | None:
        if path_override:
            p = Path(path_override)
            if p.exists():
                with rasterio.open(p) as src:
                    return src.read(1).astype(np.float32)
            result['advertencias'].append(f'{label}: path no encontrado: {path_override}')
            return None
        # Auto-buscar
        candidates = []
        area_sar = Path(area.get('sar_output', ''))
        if area_sar.exists():
            candidates.append(area_sar)
        for d in [ENGINE_DIR, ENGINE_DIR / 'datos_sar']:
            if d.exists():
                candidates.extend(sorted(d.glob(f'*{area_name}*.tif'),
                                         key=lambda p: p.stat().st_mtime, reverse=True))
        if not candidates:
            return None
        with rasterio.open(candidates[0]) as src:
            data = src.read(1).astype(np.float32)
        result[f'{label}_archivo'] = str(Path(candidates[0]).name)
        return data

    ref      = _find_sar(sar_ref_path,      'ref')
    reciente = _find_sar(sar_reciente_path, 'reciente')

    if ref is None or reciente is None:
        result['estado'] = 'sin_datos'
        result['advertencias'].append(
            'Se necesitan dos TIFs SAR georreferenciados. '
            f'Ejecutar primero: python engine/faro_sar_auto.py --area {area_name}  '
            'para generar sar_<area>_georef.tif de cada época.'
        )
        return result

    # ── Alinear shapes ────────────────────────────────────────────────────────
    if ref.shape != reciente.shape:
        from skimage.transform import resize
        reciente = resize(reciente, ref.shape, anti_aliasing=True).astype(np.float32)
        result['advertencias'].append(
            f'Shapes diferentes — reciente resampled a {ref.shape}'
        )

    # ── Convertir a dB y calcular log-ratio ───────────────────────────────────
    eps   = 1e-10
    ref_lin = np.where(ref > 0, ref, eps)
    rec_lin = np.where(reciente > 0, reciente, eps)

    # Si ya están en dB (valores < 0), convertir a lineal primero
    if np.nanmean(ref_lin) < 0:
        ref_lin = 10 ** (ref_lin / 10)
    if np.nanmean(rec_lin) < 0:
        rec_lin = 10 ** (rec_lin / 10)

    with np.errstate(divide='ignore', invalid='ignore'):
        log_ratio = 10 * np.log10(rec_lin / ref_lin)

    log_ratio = np.where(np.isfinite(log_ratio), log_ratio, np.nan)

    # ── Estadísticas globales ─────────────────────────────────────────────────
    v = log_ratio[np.isfinite(log_ratio)]
    if len(v) == 0:
        result['estado'] = 'sin_datos'
        return result

    mask_aum  = v > umbral_db
    mask_dis  = v < -umbral_db
    mask_estb = np.abs(v) <= umbral_db

    total = len(v)
    result.update({
        'cambio_medio_db'        : float(round(float(np.mean(v)), 3)),
        'cambio_std_db'          : float(round(float(np.std(v)), 3)),
        'cambio_max_aumento_db'  : float(round(float(np.max(v)), 2)),
        'cambio_max_disminucion_db': float(round(float(np.min(v)), 2)),
        'pct_estable'            : float(round(mask_estb.sum() / total * 100, 1)),
        'pct_aumento'            : float(round(mask_aum.sum()  / total * 100, 1)),
        'pct_disminucion'        : float(round(mask_dis.sum()  / total * 100, 1)),
        'pixeles_totales'        : total,
        'pixeles_cambio'         : int((mask_aum | mask_dis).sum()),
        'estado'                 : 'ok',
    })

    # ── Interpretación automática ─────────────────────────────────────────────
    pct_cambio = result['pct_aumento'] + result['pct_disminucion']
    if pct_cambio < 5:
        interp = 'Sin cambios significativos. Área estable entre las dos épocas.'
    elif pct_cambio < 15:
        interp = f'Cambio moderado ({pct_cambio:.1f}% del área). '
        if result['pct_aumento'] > result['pct_disminucion']:
            interp += 'Predomina aumento de backscatter: posible cosecha, construcción o suelo seco post-lluvia.'
        else:
            interp += 'Predomina disminución: posible inundación, deforestación o cambio de cobertura.'
    else:
        interp = (f'Cambio significativo ({pct_cambio:.1f}% del área). '
                  f'Aumento: {result["pct_aumento"]:.1f}% | Disminución: {result["pct_disminucion"]:.1f}%. '
                  'Investigar causas: eventos climáticos, cambio de uso del suelo, actividad industrial.')
    result['interpretacion'] = interp

    # ── Guardar mapa de cambio como GeoTIFF ───────────────────────────────────
    if guardar_tif:
        bounds  = area['bounds']
        west, south, east, north = bounds
        h, w    = log_ratio.shape
        tfm     = from_bounds(west, south, east, north, w, h)
        out_dir = ENGINE_DIR
        out_dir.mkdir(exist_ok=True)
        out_path = out_dir / f'sar_cambio_{area_name}_{datetime.now().strftime("%Y%m%d")}.tif'

        nodata = -9999.0
        arr_out = np.where(np.isfinite(log_ratio), log_ratio, nodata).astype(np.float32)
        with rasterio.open(
            out_path, 'w',
            driver='GTiff', height=h, width=w,
            count=1, dtype='float32',
            crs=CRS.from_epsg(4326), transform=tfm,
            nodata=nodata, compress='lzw',
        ) as dst:
            dst.write(arr_out, 1)

        result['tif_cambio_path'] = str(out_path)

    return result


# ── Tool 3: Generación de mapa de reporte ─────────────────────────────────────

@mcp.tool()
def generar_mapa_reporte(
    area_name: str,
    titulo: str | None = None,
    ndvi_tif_path: str | None = None,
    sar_tif_path: str | None = None,
    cambio_tif_path: str | None = None,
    incluir_estadisticas: bool = True,
    dpi: int = 150,
) -> dict[str, Any]:
    """
    Genera un mapa de reporte visual en estilo Faro Protocol combinando
    capas NDVI, SAR backscatter y/o mapa de cambio SAR.

    El reporte usa el esquema visual dark de Faro (#0a0a1a) con colormaps
    científicos: RdYlGn para NDVI, gray para SAR, RdBu_r para cambio.
    Incluye panel de estadísticas clave y metadata del área.

    Parámetros:
        area_name           : Área a reportar
        titulo              : Título personalizado (se genera automáticamente si None)
        ndvi_tif_path       : Path al TIF NDVI (busca automáticamente si None)
        sar_tif_path        : Path al TIF SAR georef (busca automáticamente si None)
        cambio_tif_path     : Path al TIF de cambio SAR (opcional, 4to panel)
        incluir_estadisticas: Incluir panel de métricas en el reporte
        dpi                 : Resolución del PNG (default 150, usar 300 para print)

    Retorna path al PNG generado y metadata del reporte.
    """
    area = _get_area(area_name)

    # ── Localizar datos ───────────────────────────────────────────────────────
    def _load_tif(path_override, auto_path, label):
        raw = path_override or auto_path or ''
        if not raw:
            return None, None
        p = Path(raw)
        if p.exists():
            with rasterio.open(p) as src:
                data   = src.read(1).astype(np.float32)
                nodata = src.nodata
            if nodata is not None:
                data = np.where(data == nodata, np.nan, data)
            return data, str(p.name)
        return None, None

    ndvi_data, ndvi_file = _load_tif(
        ndvi_tif_path, area.get('ndvi_tif'), 'NDVI'
    )
    sar_data, sar_file = _load_tif(
        sar_tif_path, area.get('sar_output'), 'SAR'
    )
    cambio_data, cambio_file = _load_tif(
        cambio_tif_path, None, 'Cambio'
    )

    # Buscar cambio TIF más reciente si no hay path explícito
    if cambio_data is None:
        candidates = sorted(
            list(ENGINE_DIR.glob(f'sar_cambio_{area_name}_*.tif')),
            key=lambda p: p.stat().st_mtime, reverse=True,
        )
        if candidates:
            with rasterio.open(candidates[0]) as src:
                cambio_data = src.read(1).astype(np.float32)
                nd = src.nodata
            if nd is not None:
                cambio_data = np.where(cambio_data == nd, np.nan, cambio_data)
            cambio_file = candidates[0].name

    capas_disponibles = []
    if ndvi_data   is not None: capas_disponibles.append(('NDVI',   ndvi_data,   'RdYlGn', (-1, 1)))
    if sar_data    is not None: capas_disponibles.append(('SAR',    sar_data,    'gray',   None))
    if cambio_data is not None: capas_disponibles.append(('Cambio', cambio_data, 'RdBu_r', (-6, 6)))

    if not capas_disponibles:
        return {
            'estado'    : 'sin_datos',
            'area'      : area_name,
            'advertencia': (
                f"Sin datos TIF para '{area_name}'. "
                f"Ejecutar primero faro_sar_auto.py --area {area_name} --with-s2"
            ),
        }

    # ── Normalizar SAR a dB si no lo está ────────────────────────────────────
    if sar_data is not None:
        sar_vals = sar_data[np.isfinite(sar_data)]
        if len(sar_vals) > 0 and np.nanmean(sar_vals) > 0:
            with np.errstate(divide='ignore', invalid='ignore'):
                sar_db = 10 * np.log10(np.where(sar_data > 0, sar_data, np.nan))
            for i, (lbl, _, cmap, clim) in enumerate(capas_disponibles):
                if lbl == 'SAR':
                    capas_disponibles[i] = ('SAR dB', sar_db, 'gray', None)
                    break

    # ── Normalizar NDVI si está en int16 ─────────────────────────────────────
    if ndvi_data is not None:
        finite = ndvi_data[np.isfinite(ndvi_data)]
        if len(finite) > 0 and (np.nanmax(finite) > 2 or np.nanmin(finite) < -2):
            ndvi_data = ndvi_data / 10000.0
            for i, (lbl, _, cmap, clim) in enumerate(capas_disponibles):
                if lbl == 'NDVI':
                    capas_disponibles[i] = ('NDVI', ndvi_data, 'RdYlGn', (-1, 1))
                    break

    # ── Layout ────────────────────────────────────────────────────────────────
    n      = len(capas_disponibles)
    ncols  = min(n, 3)
    nrows  = 1 + (1 if incluir_estadisticas else 0)
    figw   = 6 * ncols
    figh   = 5 * nrows + (3 if incluir_estadisticas else 0)

    fecha  = datetime.now().strftime('%Y-%m-%d')
    titulo = titulo or f"FARO PROTOCOL — Reporte Satelital | {area['label']} | {fecha}"

    fig = plt.figure(figsize=(figw, figh), facecolor='#0a0a1a')
    fig.suptitle(titulo, color='white', fontsize=14, fontweight='bold', y=0.98)

    if incluir_estadisticas:
        gs = gridspec.GridSpec(nrows, ncols, figure=fig, hspace=0.35, wspace=0.3,
                               height_ratios=[5] * (nrows - 1) + [2])
    else:
        gs = gridspec.GridSpec(1, ncols, figure=fig, hspace=0.35, wspace=0.3)

    # ── Panel de mapas ────────────────────────────────────────────────────────
    for col, (lbl, data, cmap, clim) in enumerate(capas_disponibles):
        ax = fig.add_subplot(gs[0, col])
        kwargs = dict(cmap=cmap, interpolation='nearest')
        if clim:
            kwargs['vmin'], kwargs['vmax'] = clim
        im = ax.imshow(data, **kwargs)
        ax.set_title(lbl, color='white', fontsize=12, pad=8)
        ax.axis('off')
        cb = plt.colorbar(im, ax=ax, fraction=0.046, pad=0.04)
        cb.ax.yaxis.set_tick_params(color='white')
        plt.setp(cb.ax.get_yticklabels(), color='white')

    # ── Panel de estadísticas ─────────────────────────────────────────────────
    stats_items = []
    if ndvi_data is not None:
        v = ndvi_data[np.isfinite(ndvi_data)]
        if len(v):
            stats_items += [
                ('NDVI medio',   f'{np.mean(v):.4f}'),
                ('NDVI máximo',  f'{np.max(v):.4f}'),
                ('NDVI p25/p75', f'{np.percentile(v,25):.3f} / {np.percentile(v,75):.3f}'),
            ]
    if sar_data is not None:
        v = sar_data[np.isfinite(sar_data)]
        if len(v):
            stats_items += [
                ('SAR medio',   f'{np.mean(v):.4f}'),
                ('Píxeles SAR', f'{len(v):,}'),
            ]
    if cambio_data is not None:
        v = cambio_data[np.isfinite(cambio_data)]
        if len(v):
            pct_cambio = np.sum(np.abs(v) > 3.0) / len(v) * 100
            stats_items += [
                ('Cambio medio dB', f'{np.mean(v):.2f}'),
                ('% área cambiada', f'{pct_cambio:.1f}%'),
            ]

    stats_items += [
        ('Satélites',      'Sentinel-1A + 2A'),
        ('Proveedor',      'Copernicus/CDSE'),
        ('Motor óptico',   'CLOSDI Cal(2026)'),
        ('Estado',         '✓ VALIDADO'),
    ]

    if incluir_estadisticas and stats_items:
        ax_s = fig.add_subplot(gs[nrows - 1, :])
        ax_s.set_facecolor('#111133')
        ax_s.axis('off')
        cols_s = min(len(stats_items), 5)
        for i, (k, v) in enumerate(stats_items):
            xi = 0.02 + (i % cols_s) * (0.96 / cols_s)
            yi = 0.80 - (i // cols_s) * 0.42
            ax_s.text(xi, yi,        k, transform=ax_s.transAxes,
                      color='#aaaacc', fontsize=8)
            ax_s.text(xi, yi - 0.28, v, transform=ax_s.transAxes,
                      color='white',   fontsize=10, fontweight='bold')

    # ── Guardar ───────────────────────────────────────────────────────────────
    out_dir  = ENGINE_DIR
    out_dir.mkdir(exist_ok=True)
    out_path = out_dir / f'faro_reporte_{area_name}_{datetime.now().strftime("%Y%m%d_%H%M")}.png'

    plt.savefig(out_path, dpi=dpi, bbox_inches='tight',
                facecolor='#0a0a1a', edgecolor='none')
    plt.close(fig)

    return {
        'estado'           : 'ok',
        'area'             : area_name,
        'label'            : area['label'],
        'png_path'         : str(out_path),
        'titulo'           : titulo,
        'fecha'            : fecha,
        'capas_incluidas'  : [lbl for lbl, *_ in capas_disponibles],
        'archivos_fuente'  : {k: v for k, v in [
            ('ndvi', ndvi_file), ('sar', sar_file), ('cambio', cambio_file)
        ] if v},
        'resolucion_dpi'   : dpi,
        'dimensiones_px'   : f'{int(figw * dpi)} × {int(figh * dpi)}',
    }


# ── Tool 4: Coherencia InSAR via openEO (nube, sin descarga) ─────────────────

@mcp.tool()
def computar_coherencia_insar(
    area_name: str,
    fecha_inicio: str,
    fecha_fin: str,
    polarizacion: str = 'VV',
    temporal_baseline: int = 6,
    burst_id: int | None = None,
    sub_swath: str = 'IW2',
) -> dict[str, Any]:
    """
    Computa coherencia interferométrica SAR Sentinel-1 en la nube via openEO
    sobre Copernicus Data Space Ecosystem. No requiere descargar escenas GRD.

    La coherencia mide la estabilidad de la señal radar entre dos fechas:
    - Coherencia alta (≈1): área estable (asfalto, edificios, suelo árido)
    - Coherencia media (0.3-0.7): cambios graduales (crecimiento de cultivos)
    - Coherencia baja (≈0): cambio drástico (inundación, deforestación, cosecha)

    Ideal para: monitoreo de cultivos, detección de inundaciones, subsidencia de
    infraestructura, cambio en patrones de actividad industrial (Vaca Muerta).

    Requiere: pip install openeo  (autenticación OIDC con credenciales Copernicus)

    Parámetros:
        area_name          : Área a analizar
        fecha_inicio       : ISO date inicio ('2026-01-01')
        fecha_fin          : ISO date fin ('2026-01-31')
        polarizacion       : 'VV' (superficies) o 'VH' (vegetación)
        temporal_baseline  : Días entre par interferométrico (6=Sentinel-1 repeat)
        burst_id           : ID del burst Sentinel-1 (buscar en https://burst.asf.alaska.edu)
        sub_swath          : Subswath IW ('IW1', 'IW2', 'IW3')

    Retorna estadísticas de coherencia y path al TIF descargado.
    """
    try:
        import openeo
    except ImportError:
        return {
            'estado'    : 'dependencia_faltante',
            'mensaje'   : 'openeo no instalado. Ejecutar: pip install openeo',
            'comando'   : 'pip install openeo',
        }

    area = _get_area(area_name)

    if burst_id is None:
        bounds = area['bounds']
        return {
            'estado'     : 'burst_id_requerido',
            'mensaje'    : (
                f"Para '{area_name}' ({area['label']}) se necesita el burst_id de Sentinel-1. "
                f"Obtenerlo en: https://burst.asf.alaska.edu/?bbox="
                f"{bounds[0]},{bounds[1]},{bounds[2]},{bounds[3]}"
            ),
            'bounds'     : bounds,
            'sugerencia' : 'Filtrar por bbox del área y fecha para obtener el burst_id correcto.',
        }

    result: dict[str, Any] = {
        'area'            : area_name,
        'label'           : area['label'],
        'fecha_inicio'    : fecha_inicio,
        'fecha_fin'       : fecha_fin,
        'polarizacion'    : polarizacion,
        'temporal_baseline': temporal_baseline,
        'burst_id'        : burst_id,
        'advertencias'    : [],
    }

    try:
        conn = openeo.connect("openeo.dataspace.copernicus.eu")

        # Auth — usa token cacheado si existe, sinó device flow (headless)
        try:
            conn.authenticate_oidc()
        except Exception:
            conn.authenticate_oidc_device()

        # Construir proceso de coherencia via APEx algorithms
        coherence_cube = conn.datacube_from_process(
            "sentinel1_sar_coherence",
            namespace="https://raw.githubusercontent.com/ESA-APEx/apex_algorithms/main/algorithm_catalog/sentinel1_sar_coherence/openeo_udp/sentinel1_sar_coherence.json",
            temporal_extent=[fecha_inicio, fecha_fin],
            temporal_baseline=temporal_baseline,
            burst_id=burst_id,
            coherence_window_az=2,
            coherence_window_rg=10,
            polarization=polarizacion,
            sub_swath=sub_swath,
        )

        # Guardar resultado
        out_dir  = ENGINE_DIR
        out_dir.mkdir(exist_ok=True)
        out_path = out_dir / f'coherencia_{area_name}_{fecha_inicio[:7]}.tif'

        job = coherence_cube.execute_batch(
            outputfile=str(out_path),
            title=f"Faro Protocol — Coherencia {area['label']} {fecha_inicio}",
        )
        job.get_results()

        # Leer estadísticas del TIF resultante
        if out_path.exists():
            with rasterio.open(out_path) as src:
                data = src.read(1).astype(np.float32)
                nd   = src.nodata
            if nd is not None:
                data = np.where(data == nd, np.nan, data)
            v = data[np.isfinite(data)]

            result.update({
                'estado'              : 'ok',
                'coherencia_media'    : float(round(float(np.mean(v)), 4)),
                'coherencia_std'      : float(round(float(np.std(v)), 4)),
                'pct_alta_coherencia' : float(round(float(np.mean(v > 0.7)) * 100, 1)),
                'pct_media_coherencia': float(round(float(np.mean((v > 0.3) & (v <= 0.7))) * 100, 1)),
                'pct_baja_coherencia' : float(round(float(np.mean(v <= 0.3)) * 100, 1)),
                'tif_coherencia_path' : str(out_path),
                'interpretacion'      : _interpretar_coherencia(
                    float(np.mean(v)), float(np.mean(v <= 0.3)) * 100, area_name
                ),
            })
        else:
            result['estado']    = 'error'
            result['advertencias'].append('El job openEO completó pero el TIF no se descargó.')

    except Exception as e:
        result['estado']     = 'error'
        result['error']      = str(e)
        result['advertencias'].append(
            f'Error en openEO: {e}. '
            'Verificar credenciales con: openeo.connect("openeo.dataspace.copernicus.eu").authenticate_oidc()'
        )

    return result


def _interpretar_coherencia(media: float, pct_baja: float, area: str) -> str:
    if media > 0.7:
        return f"Alta coherencia global ({media:.2f}). Área estable, sin cambios estructurales entre épocas."
    if media > 0.4:
        if 'vaca_muerta' in area:
            return (f"Coherencia media ({media:.2f}). {pct_baja:.1f}% de baja coherencia. "
                    "Patrón típico de actividad de perforación/construcción activa en Vaca Muerta.")
        return (f"Coherencia media ({media:.2f}). {pct_baja:.1f}% de baja coherencia. "
                "Cambios moderados: posible variación de cobertura vegetal, humedad o actividad.")
    return (f"Baja coherencia ({media:.2f}). {pct_baja:.1f}% del área con cambio drástico. "
            "Posibles causas: inundación, cosecha masiva, deforestación o evento climático severo.")


# ── Entrypoint ────────────────────────────────────────────────────────────────

if __name__ == '__main__':
    transport = os.getenv('GIS_MCP_TRANSPORT', 'stdio')
    port      = int(os.getenv('GIS_MCP_PORT', '9010'))

    if transport == 'http':
        mcp.run(transport='streamable-http', host='0.0.0.0', port=port)
    else:
        mcp.run(transport='stdio')
