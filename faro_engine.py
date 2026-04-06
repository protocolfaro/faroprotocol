"""
FARO PROTOCOL — Motor centralizado (faro_engine.py)

Punto de entrada principal del sistema. Orquesta el pipeline técnico
(fusion SAR+NDVI, vision computacional) y aplica el modelo económico
para convertir datos satelitales en insights verificables.

Protocolo de Cero Footprint
----------------------------
Los datos crudos (arrays de píxeles, coordenadas exactas, algoritmos)
NUNCA salen del servidor. Lo que ve el cliente es el insight económico
agregado: rinde estimado, score Faro, confianza, alerta.

data.json tiene tres secciones:
  _interno  → datos técnicos para el sistema (no exponer en web pública)
  insights  → lo que ve el cliente (rinde, score, estado)
  resumen   → métricas globales del sistema

Modelo de estimación de rinde (sin historial de cliente)
---------------------------------------------------------
Vertical AGRO:
  Base: promedios nacionales INDEC / Bolsa de Cereales Argentina
    soja  3.2 t/ha · maiz 8.8 t/ha · trigo 3.4 t/ha
  Ajuste NDVI: vigor vegetativo -> proxy de biomasa -> rinde potencial
  Ajuste SAR:  backscatter en dB -> humedad de suelo + biomasa adicional
    referencia C-band cultivo establecido: sigma_0 ~ -12 dB
  Formula: rinde = base * factor_ndvi * factor_sar

Vertical ENERGIA:
  Metrica: Indice de Actividad Industrial (0-100)
  Derivado del backscatter SAR: mas estructuras metalicas = mayor sigma_0

Uso:
    python faro_engine.py --area cordoba
    python faro_engine.py --area vaca_muerta
    python faro_engine.py --area cordoba --skip-vision
    python faro_engine.py --area cordoba --sar-file ruta/archivo.tiff

Como modulo:
    from faro_engine import FaroEngine
    engine = FaroEngine()
    insight = engine.analizar_area('cordoba')
"""

import argparse
import hashlib
import json
import math
import sys
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Optional

try:
    import numpy as np
    _NUMPY_OK = True
except ImportError:
    _NUMPY_OK = False

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')
if hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(encoding='utf-8')

from faro_areas import load_area, list_areas

DATA_JSON = Path(__file__).parent / 'data.json'


def _sha256(path: str) -> str:
    """SHA-256 de un archivo. Utilidad interna del motor."""
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for bloque in iter(lambda: f.read(65536), b''):
            h.update(bloque)
    return h.hexdigest()


# ─── Parámetros del modelo ──────────────────────────────────────────────────────

# Factor de conversión biomasa aérea → grano (Harvest Index).
# Constante agronómica de referencia para estimación de rinde en cereales/oleaginosas:
#   soja  HI ≈ 0.44–0.47  (Spaeth et al. 1987; USDA-ARS)
#   maíz  HI ≈ 0.47–0.50
#   trigo HI ≈ 0.42–0.46
# Se usa 0.44 como valor conservador global, aplicado explícitamente en
# _estimar_rinde_agro() para transformar biomasa vegetativa en biomasa de cosecha.
FACTOR_BIOMASA_A_GRANO: float = 0.44

# Rendimientos de referencia → fallback hardcodeado si no hay baseline.
# Con datos MAGyP en datos/baseline_nacional.json el engine usa get_rinde_ref()
# y estos valores solo se aplican si el archivo no existe.
# Fuente grano: INDEC / Bolsa de Cereales Argentina, campaña 2024/25.
RINDE_REF_BIOMASA = {
    "soja":  round(3.20 / FACTOR_BIOMASA_A_GRANO, 3),   # 7.273 t/ha
    "maiz":  round(8.80 / FACTOR_BIOMASA_A_GRANO, 3),   # 20.0  t/ha
    "trigo": round(3.40 / FACTOR_BIOMASA_A_GRANO, 3),   # 7.727 t/ha
    "agro":  round(3.20 / FACTOR_BIOMASA_A_GRANO, 3),   # fallback → soja
}
RINDE_REF = {k: round(v * FACTOR_BIOMASA_A_GRANO, 2) for k, v in RINDE_REF_BIOMASA.items()}


def _get_rinde_ref_zona(area: dict) -> tuple[float, float, str]:
    """
    Retorna (rinde_grano_ref_tn_ha, biomasa_ref_tn_ha, fuente) para el área.
    Jerarquía: departamento > provincia > nacional > fallback hardcodeado.
    """
    cultivo     = area.get('cultivo', 'soja')
    provincia   = area.get('provincia')
    departamento = area.get('departamento')
    try:
        from faro_rinde_import import get_rinde_ref
        ref = get_rinde_ref(cultivo, provincia=provincia, departamento=departamento)
        rinde_grano = ref['media_tn_ha']
        fuente      = ref['fuente']
    except Exception:
        rinde_grano = RINDE_REF.get(cultivo, RINDE_REF['agro'])
        fuente      = 'fallback_hardcodeado'
    biomasa = round(rinde_grano / FACTOR_BIOMASA_A_GRANO, 3)
    return rinde_grano, biomasa, fuente


# ─── Utilidades NumPy para procesamiento de arrays ──────────────────────────────

class FaroArray:
    """
    Utilidades estáticas para procesamiento de arrays SAR y NDVI.
    Requiere NumPy. Si no está disponible, los métodos levantan RuntimeError.

    Uso:
        import numpy as np
        dn_array = np.array([...], dtype=np.float32)
        sigma0_db = FaroArray.sar_dn_to_sigma0(dn_array)

        ndvi_int16 = np.array([4444, 2000, -500], dtype=np.int16)
        ndvi_real  = FaroArray.ndvi_int16_normalize(ndvi_int16.astype(float))
    """

    @staticmethod
    def _require_numpy() -> None:
        if not _NUMPY_OK:
            raise RuntimeError("NumPy no disponible. Instalar con: pip install numpy")

    @staticmethod
    def sar_dn_to_sigma0(
        array: "np.ndarray",
        nodata: Optional[float] = None,
        clip_db: tuple[float, float] = (-35.0, 5.0),
    ) -> "np.ndarray":
        """
        Convierte array SAR de DN (Digital Numbers) a Sigma-0 en dB.

        Fórmula estándar Sentinel-1 GRD IW:
            sigma0_linear = DN²   (si viene de GRD procesado)
            sigma0_db     = 10 × log10(DN² + ε)   donde ε=1e-10 evita log(0)

        Para arrays ya lineales (float 0–1):
            sigma0_db = 10 × log10(value + ε)

        Parámetros
        ----------
        array   : ndarray float32/float64 con valores DN o lineales
        nodata  : valor a tratar como NaN (ej. 0.0 o -9999)
        clip_db : rango válido en dB para clamp final (excluye outliers)

        Retorna
        -------
        ndarray float32 en dB, con NaN donde nodata o fuera de rango
        """
        FaroArray._require_numpy()
        out = array.astype(np.float64).copy()

        if nodata is not None:
            out[out == nodata] = np.nan

        # Si el rango sugiere DN² (valores > 1), tratar como GRD DN
        finite = out[np.isfinite(out)]
        if len(finite) > 0 and np.nanmax(finite) > 2.0:
            # DN grandes: sigma0 = 10*log10(DN² + ε) = 20*log10(DN + ε)
            out = 20.0 * np.log10(np.abs(out) + 1e-10)
        else:
            # Ya lineales [0,1]: sigma0 = 10*log10(val + ε)
            out = 10.0 * np.log10(out + 1e-10)

        # Clamp al rango válido (excluye ruido térmico y artefactos)
        lo, hi = clip_db
        out = np.where(np.isfinite(out), np.clip(out, lo, hi), np.nan)

        return out.astype(np.float32)

    @staticmethod
    def ndvi_int16_normalize(
        array: "np.ndarray",
        nodata: Optional[float] = None,
        scale: float = 10_000.0,
    ) -> "np.ndarray":
        """
        Normaliza NDVI exportado por Google Earth Engine en escala int16.

        GEE exporta NDVI como: ndvi_real × 10000 → int16.
        Esta función detecta automáticamente si el array está en escala
        int16 (valores fuera de [-2, 2]) y divide por `scale`.

        Parámetros
        ----------
        array   : ndarray con valores NDVI (int16 o ya normalizados)
        nodata  : valor nodata a reemplazar con NaN
        scale   : factor de escala de GEE (default 10000)

        Retorna
        -------
        ndarray float32 con NDVI en [-1.0, 1.0], NaN donde nodata
        """
        FaroArray._require_numpy()
        out = array.astype(np.float64).copy()

        if nodata is not None:
            out[out == nodata] = np.nan

        finite = out[np.isfinite(out)]
        if len(finite) > 0 and (np.nanmax(finite) > 2.0 or np.nanmin(finite) < -2.0):
            out = out / scale

        # Clamp al rango físico válido del NDVI
        out = np.where(np.isfinite(out), np.clip(out, -1.0, 1.0), np.nan)

        return out.astype(np.float32)

    @staticmethod
    def fusion_index(
        ndvi_arr: "np.ndarray",
        sar_db_arr: "np.ndarray",
        w_ndvi: float = 0.60,
        w_sar:  float = 0.40,
        sar_ref_db: float = -12.0,
        sar_range_db: float = 15.0,
    ) -> "np.ndarray":
        """
        Calcula índice de fusión SAR+NDVI pixel a pixel.

        Normaliza cada fuente a [0,1] y combina con pesos.
        NDVI ya está en [-1,1] → normalización a [0,1]: (ndvi + 1) / 2
        SAR en dB → normalización: (sar - (sar_ref - range/2)) / range

        Parámetros
        ----------
        ndvi_arr   : ndarray float32 NDVI normalizado [-1, 1]
        sar_db_arr : ndarray float32 SAR en dB
        w_ndvi     : peso del componente óptico (default 0.60)
        w_sar      : peso del componente radar  (default 0.40)
        sar_ref_db : centro del rango SAR esperado en dB
        sar_range_db: ancho del rango válido en dB

        Retorna
        -------
        ndarray float32 índice fusión [0, 1], NaN donde falta alguna fuente
        """
        FaroArray._require_numpy()
        ndvi_norm = (ndvi_arr.astype(np.float64) + 1.0) / 2.0
        ndvi_norm = np.clip(ndvi_norm, 0.0, 1.0)

        sar_lo = sar_ref_db - sar_range_db / 2.0
        sar_norm = (sar_db_arr.astype(np.float64) - sar_lo) / sar_range_db
        sar_norm = np.clip(sar_norm, 0.0, 1.0)

        fusion = w_ndvi * ndvi_norm + w_sar * sar_norm
        valid  = np.isfinite(ndvi_arr) & np.isfinite(sar_db_arr)
        fusion[~valid] = np.nan

        return fusion.astype(np.float32)

    @staticmethod
    def stats(array: "np.ndarray") -> dict:
        """Estadísticas básicas de un array ignorando NaN."""
        FaroArray._require_numpy()
        valid = array[np.isfinite(array)]
        if len(valid) == 0:
            return {"n": 0, "media": None, "std": None, "min": None, "max": None}
        return {
            "n":     int(len(valid)),
            "media": float(round(np.nanmean(valid), 6)),
            "std":   float(round(np.nanstd(valid), 6)),
            "min":   float(round(np.nanmin(valid), 6)),
            "max":   float(round(np.nanmax(valid), 6)),
        }

# Rango SAR C-band Sentinel-1 para cultivos (IW GRD, sigma_0 en dB)
SAR_REF_DB      = -12.0   # referencia cultivo establecido, humedad normal
SAR_SENSIBILIDAD = 0.20   # 20% de variación por cada 4 dB de desvío

# Umbrales NDVI para escala de factor de rinde
NDVI_UMBRAL_CERO  = 0.20   # por debajo → sin cultivo → rinde 0
NDVI_UMBRAL_MIN   = 0.30   # inicio del rango productivo → 40% del rinde
NDVI_UMBRAL_OPT   = 0.65   # óptimo → 100% del rinde de referencia
NDVI_UMBRAL_MAX   = 0.80   # máximo → 125% del rinde de referencia

# Umbrales para score y confianza
SCORE_ALERTA_ANOMALIA  = 10.0   # %: si pct_anomalia > este valor → alerta
SCORE_ALERTA_RINDE     = 0.70   # fracción: si rinde < 70% del ref → alerta
CONFIANZA_UMBRAL_ALTA  = 5.0    # pct_anomalia < 5% y ambas fuentes disponibles
CONFIANZA_UMBRAL_MEDIA = 15.0   # pct_anomalia < 15%


# ─── Estructuras de datos (Protocolo Cero Footprint) ──────────────────────────

@dataclass
class DatosCrudos:
    """
    CAPA INTERNA — nunca abandona el servidor ni va al cliente.

    Contiene todo lo técnico: arrays raster, estadísticas pixel-level,
    rutas a archivos, SHA-256 completo del reporte.
    """
    area_name:            str
    sar_medio_db:         Optional[float]
    ndvi_medio:           Optional[float]
    indice_fusion_medio:  Optional[float]
    pixeles_analizados:   Optional[int]
    pct_anomalia:         Optional[float]
    reporte_png:          Optional[str]
    sha256_completo:      Optional[str]
    tiene_sar:            bool = False
    tiene_ndvi:           bool = False
    tiene_vision:         bool = False


@dataclass
class InsightEconomico:
    """
    CAPA CLIENTE — lo que ve el dashboard y el cliente final.

    Sin datos crudos: no hay coordenadas exactas, no hay arrays,
    no hay información de otros clientes. Solo el insight económico.
    """
    area:               str
    zona:               str
    vertical:           str
    fecha:              str
    # Estimación económica
    rinde_estimado_tha: Optional[float]   # t/ha (agro) o None (energía)
    indice_actividad:   Optional[float]   # 0–100 (energía) o None (agro)
    score_faro:         float             # 0–100 siempre
    confianza:          str               # "Alta / Media / Baja"
    # Estado
    estado:             str               # "OK / ALERTA / SIN_DATOS"
    alerta:             Optional[str]
    pct_anomalia:       float
    # Verificación
    sha256_reporte:     str               # primeros 16 chars + "..."


# ─── Motor ─────────────────────────────────────────────────────────────────────

class FaroEngine:
    """Motor centralizado de Faro Protocol."""

    def analizar_area(
        self,
        area_name: str,
        sar_file:    Optional[str] = None,
        skip_georef: bool          = False,
        skip_vision: bool          = False,
    ) -> InsightEconomico:
        """
        Analiza un área completa y retorna el insight económico listo para
        publicar. Actualiza data.json automáticamente al terminar.

        Parámetros
        ----------
        area_name   : nombre del área (ej. 'cordoba', 'vaca_muerta')
        sar_file    : ruta al .tiff SAR crudo; None si ya está georreferenciado
        skip_georef : True si sar_<area>_georef.tif ya existe
        skip_vision : True para saltar clasificación NDVI (Fase 6)
        """
        area = load_area(area_name)
        print("\n" + "=" * 60)
        print(f"  FARO ENGINE — Analisis integral")
        print(f"  Area    : {area['label']}")
        print(f"  Vertical: {area['vertical'].upper()}")
        print(f"  Fecha   : {datetime.now().strftime('%Y-%m-%d %H:%M')}")
        print("=" * 60)

        crudos = DatosCrudos(area_name=area_name,
                             sar_medio_db=None, ndvi_medio=None,
                             indice_fusion_medio=None, pixeles_analizados=None,
                             pct_anomalia=0.0, reporte_png=None,
                             sha256_completo=None)

        # ── Paso 1: Georreferenciación SAR ──────────────────────────
        if sar_file and not skip_georef:
            print("\n[1/4] Georreferenciando SAR...")
            import faro_sar_georef
            faro_sar_georef.main(area=area, sar_input=sar_file)
            crudos.tiene_sar = True
        else:
            sar_path = Path(area.get('sar_output', ''))
            crudos.tiene_sar = sar_path.exists()
            estado_sar = "encontrado" if crudos.tiene_sar else "no disponible"
            print(f"\n[1/4] SAR georreferenciado: {estado_sar}")

        # ── Paso 2: Fusión SAR + NDVI ────────────────────────────────
        print("\n[2/4] Fusionando SAR + NDVI...")
        import faro_fusion
        resultado_fusion = faro_fusion.main(area=area)
        reporte_png      = resultado_fusion['output_png']
        stats_fusion     = resultado_fusion['stats']

        crudos.sar_medio_db        = stats_fusion.get('sar_medio_db')
        crudos.ndvi_medio          = stats_fusion.get('ndvi_medio')
        crudos.indice_fusion_medio = stats_fusion.get('indice_fusion_medio')
        crudos.pixeles_analizados  = stats_fusion.get('pixeles_analizados')
        crudos.reporte_png         = reporte_png
        crudos.tiene_ndvi          = crudos.ndvi_medio is not None

        # ── Paso 3: Visión computacional ─────────────────────────────
        if not skip_vision:
            ndvi_path = area.get('ndvi_tif', '')
            if ndvi_path and Path(ndvi_path).exists():
                print("\n[3/4] Vision computacional (Fase 6)...")
                import faro_vision
                vstats = faro_vision.run(area_name)
                crudos.pct_anomalia  = vstats.get('pct_anomalia', 0.0)
                crudos.tiene_vision  = True
                # NDVI más preciso viene de vision (escala int16 ya corregida)
                crudos.ndvi_medio    = vstats.get('ndvi_global_medio', crudos.ndvi_medio)
            else:
                print("\n[3/4] Vision omitida: NDVI no disponible para esta area")
        else:
            print("\n[3/4] Vision omitida (--skip-vision)")

        # ── Paso 4: SHA-256 del reporte ──────────────────────────────
        print("\n[4/4] Sellando reporte (SHA-256)...")
        digest             = _sha256(reporte_png)
        crudos.sha256_completo = digest
        Path(reporte_png).with_suffix('.sha256').write_text(
            f"{digest}  {Path(reporte_png).name}\n", encoding='utf-8'
        )
        print(f"  SHA-256: {digest[:32]}...")

        # ── Modelo económico ─────────────────────────────────────────
        insight = self._generar_insight(area, crudos)

        # ── Publicar data.json ───────────────────────────────────────
        self._publicar(crudos, insight)

        print("\n" + "=" * 60)
        print(f"  Score Faro:      {insight.score_faro:.1f} / 100")
        if insight.rinde_estimado_tha is not None:
            print(f"  Rinde estimado:  {insight.rinde_estimado_tha:.2f} t/ha  "
                  f"({insight.confianza} confianza)")
        if insight.indice_actividad is not None:
            print(f"  Actividad ind.:  {insight.indice_actividad:.1f} / 100  "
                  f"({insight.confianza} confianza)")
        if insight.alerta:
            print(f"  [!] ALERTA: {insight.alerta}")
        print(f"  Estado:          {insight.estado}")
        print(f"  data.json:       {DATA_JSON}")
        print("=" * 60 + "\n")

        return insight

    # ── Modelo económico ────────────────────────────────────────────────────────

    def _generar_insight(self, area: dict, crudos: DatosCrudos) -> InsightEconomico:
        vertical  = area.get('vertical', 'agro')
        fecha     = datetime.now().strftime("%Y-%m-%d %H:%M")
        ndvi      = crudos.ndvi_medio
        sar_db    = crudos.sar_medio_db
        fusion    = crudos.indice_fusion_medio
        anom_pct  = crudos.pct_anomalia or 0.0

        if vertical == 'agro':
            rinde, indice_act = self._estimar_rinde_agro(area, ndvi, sar_db), None
        else:
            rinde, indice_act = None, self._estimar_actividad_energia(sar_db)

        score     = self._calcular_score_faro(fusion, anom_pct, vertical,
                                               rinde, indice_act, area)
        confianza = self._evaluar_confianza(crudos)
        estado, alerta = self._evaluar_estado(vertical, rinde, indice_act,
                                               anom_pct, fusion, area)
        sha_corto = (crudos.sha256_completo[:16] + "...") if crudos.sha256_completo else "—"

        return InsightEconomico(
            area               = area['name'],
            zona               = area['label'],
            vertical           = vertical,
            fecha              = fecha,
            rinde_estimado_tha = rinde,
            indice_actividad   = indice_act,
            score_faro         = score,
            confianza          = confianza,
            estado             = estado,
            alerta             = alerta,
            pct_anomalia       = round(anom_pct, 2),
            sha256_reporte     = sha_corto,
        )

    def _estimar_rinde_agro(self, area: dict, ndvi: Optional[float],
                             sar_db: Optional[float]) -> Optional[float]:
        """
        Estima rinde de grano en t/ha a partir de NDVI y SAR.

        Pipeline explícito (crop science + teledetección):
          1. NDVI → factor de vigor vegetativo relativo   [0 – 1.25]
          2. SAR  → factor de ajuste por humedad y biomasa húmeda  [0.70 – 1.30]
          3. biomasa_aérea = RINDE_REF_BIOMASA × factor_ndvi × factor_sar
          4. rinde_grano   = biomasa_aérea × FACTOR_BIOMASA_A_GRANO (0.44)

        El FACTOR_BIOMASA_A_GRANO (Harvest Index = 0.44) transforma la
        biomasa vegetativa total estimada por satélite en rendimiento de
        cosecha real. Es constante agronómica validada para soja/maíz/trigo.

        Con baseline MAGyP disponible: usa rinde histórico por departamento/provincia.
        Sin baseline: usa promedios nacionales INDEC / Bolsa de Cereales Argentina.
        """
        _, biomasa_ref, fuente_ref = _get_rinde_ref_zona(area)
        if fuente_ref != 'fallback_hardcodeado':
            print(f"  [engine] Rinde ref: {round(biomasa_ref * FACTOR_BIOMASA_A_GRANO, 3)} t/ha ({fuente_ref})")

        if ndvi is None and sar_db is None:
            return None

        # ── Factor NDVI: proxy de biomasa foliar ──────────────────────
        if ndvi is not None:
            if ndvi < NDVI_UMBRAL_CERO:
                factor_ndvi = 0.0
            elif ndvi < NDVI_UMBRAL_MIN:
                t = (ndvi - NDVI_UMBRAL_CERO) / (NDVI_UMBRAL_MIN - NDVI_UMBRAL_CERO)
                factor_ndvi = 0.40 * t
            elif ndvi <= NDVI_UMBRAL_OPT:
                t = (ndvi - NDVI_UMBRAL_MIN) / (NDVI_UMBRAL_OPT - NDVI_UMBRAL_MIN)
                factor_ndvi = 0.40 + 0.60 * t
            else:
                t = min((ndvi - NDVI_UMBRAL_OPT) / (NDVI_UMBRAL_MAX - NDVI_UMBRAL_OPT), 1.0)
                factor_ndvi = 1.00 + 0.25 * t
        else:
            factor_ndvi = 1.0

        # ── Factor SAR: ajuste humedad de suelo + biomasa húmeda ──────
        if sar_db is not None:
            desvio     = sar_db - SAR_REF_DB
            factor_sar = 1.0 + SAR_SENSIBILIDAD * (desvio / 4.0)
            factor_sar = max(0.70, min(1.30, factor_sar))
        else:
            factor_sar = 1.0

        # ── Conversión biomasa → grano (Harvest Index explícito) ──────
        biomasa_estimada = biomasa_ref * factor_ndvi * factor_sar
        rinde_grano      = biomasa_estimada * FACTOR_BIOMASA_A_GRANO

        return round(max(0.0, rinde_grano), 2)

    def _estimar_actividad_energia(self, sar_db: Optional[float]) -> Optional[float]:
        """
        Índice de actividad industrial 0–100 para vertical energía.

        Mayor backscatter SAR en C-band → más estructuras metálicas
        (torres, tuberías, maquinaria) → mayor actividad.
        Rango de referencia Vaca Muerta: -18 dB (campo vacío) a -3 dB (alta actividad).
        """
        if sar_db is None:
            return None
        SAR_MIN, SAR_MAX = -18.0, -3.0
        indice = (sar_db - SAR_MIN) / (SAR_MAX - SAR_MIN) * 100
        return round(max(0.0, min(100.0, indice)), 1)

    def _calcular_score_faro(self, fusion: Optional[float], anom_pct: float,
                              vertical: str, rinde: Optional[float],
                              indice_act: Optional[float], area: dict) -> float:
        """
        Score Faro (0–100): síntesis del estado del área.

        Para agro: pesa fusión (60%) + rinde relativo (30%) + penalización anomalías (10%)
        Para energía: pesa fusión (40%) + actividad industrial (50%) + penalización (10%)
        """
        fusion_score = (fusion or 0.0) * 100

        if vertical == 'agro':
            if rinde is not None:
                ref, _, _ = _get_rinde_ref_zona(area)
                rinde_rel = min(rinde / ref, 1.3) / 1.3  # normalizado sobre max esperable
                score     = 0.60 * fusion_score + 0.30 * rinde_rel * 100
            else:
                score = 0.90 * fusion_score
        else:
            act   = indice_act or 0.0
            score = 0.40 * fusion_score + 0.50 * act

        # Penalización por anomalías: hasta -10 puntos si pct_anomalia = 100%
        penalizacion = min(anom_pct / 100, 1.0) * 10
        score        = max(0.0, min(100.0, score - penalizacion))

        return round(score, 1)

    def _evaluar_confianza(self, crudos: DatosCrudos) -> str:
        fuentes = int(crudos.tiene_sar) + int(crudos.tiene_ndvi)
        anom    = crudos.pct_anomalia or 0.0

        if fuentes == 2 and anom < CONFIANZA_UMBRAL_ALTA:
            return "Alta"
        if fuentes >= 1 and anom < CONFIANZA_UMBRAL_MEDIA:
            return "Media"
        return "Baja"

    def _evaluar_estado(self, vertical: str, rinde: Optional[float],
                         indice_act: Optional[float], anom_pct: float,
                         fusion: Optional[float], area: dict):
        alertas = []

        if anom_pct > SCORE_ALERTA_ANOMALIA:
            alertas.append(
                f"{anom_pct:.1f}% de superficie con anomalias detectadas "
                "(posibles malezas o estres)"
            )

        if vertical == 'agro' and rinde is not None:
            ref, _, fuente_ref = _get_rinde_ref_zona(area)
            if rinde < ref * SCORE_ALERTA_RINDE:
                brecha_pct = round((1 - rinde / ref) * 100, 1)
                cultivo    = area.get('cultivo', 'cultivo')
                alertas.append(
                    f"Rinde estimado {rinde:.2f} t/ha — {brecha_pct}% por debajo "
                    f"del promedio historico regional {ref:.2f} t/ha ({fuente_ref}). "
                    f"Recomendacion: verificar condicion hidrica y sanidad del cultivo "
                    f"de {cultivo} en campo antes de proyectar cosecha."
                )

        if fusion is not None and fusion < 0.30:
            alertas.append("Indice de fusion bajo — posible estres generalizado")

        if alertas:
            return "ALERTA", " | ".join(alertas)
        if fusion is None and rinde is None and indice_act is None:
            return "SIN_DATOS", None
        return "OK", None

    # ── Inicialización de nodos globales ────────────────────────────────────────

    def init_nodos_globales(self) -> dict:
        """
        Escribe en data.json todos los nodos conocidos con estado inicial
        PENDIENTE_DATOS para los que aún no hay telemetría satelital.

        Preserva los datos reales existentes (p. ej. Córdoba) sin sobreescribirlos.
        Usado para que faro_website.html muestre la cobertura global del sistema
        aunque los archivos raster de esas áreas aún no existan.

        Retorna dict con conteo de nodos inicializados vs. ya procesados.
        """
        data: dict = {}
        if DATA_JSON.exists():
            try:
                with open(DATA_JSON, encoding='utf-8') as f:
                    data = json.load(f)
            except json.JSONDecodeError:
                data = {}

        ahora = datetime.now().strftime("%Y-%m-%d %H:%M")

        data.setdefault('_meta', {}).update({
            "generado_en":  ahora,
            "generado_por": "faro_engine.init_nodos_globales",
            "protocolo":    "Cero Footprint v1",
        })
        data.setdefault('insights',  {})
        data.setdefault('_interno',  {})
        data.setdefault('pipeline',  {})

        nodos_inicializados = []
        nodos_preservados   = []

        for area_name in list_areas():
            # No sobreescribir áreas que ya tienen datos reales
            existing = data['insights'].get(area_name, {})
            if existing.get('estado') in ('OK', 'ALERTA'):
                nodos_preservados.append(area_name)
                continue

            try:
                area = load_area(area_name)
            except Exception:
                continue

            vertical = area.get('vertical', 'agro')
            data['insights'][area_name] = {
                "zona":               area.get('label', area_name),
                "vertical":           vertical,
                "fecha":              ahora,
                "rinde_estimado_tha": None,
                "indice_actividad":   None,
                "score_faro":         None,
                "confianza":          "—",
                "estado":             "PENDIENTE_DATOS",
                "alerta":             None,
                "pct_anomalia":       None,
                "sha256_reporte":     "—",
                "center":             area.get('center'),
                "bounds":             area.get('bounds'),
            }
            data['pipeline'].setdefault(area_name, {}).update({
                "estado":   "PENDIENTE_DATOS",
                "ultimo_run": ahora,
            })
            nodos_inicializados.append(area_name)

        # Resumen global (solo áreas con datos reales)
        scores_validos = [
            v['score_faro'] for v in data['insights'].values()
            if v.get('score_faro') is not None
        ]
        areas_alerta = [
            k for k, v in data['insights'].items()
            if v.get('estado') == 'ALERTA'
        ]
        data['resumen'] = {
            "ultimo_run":      ahora,
            "areas_activas":   len(list_areas()),
            "areas_con_datos": len(nodos_preservados),
            "score_promedio":  round(sum(scores_validos) / len(scores_validos), 1)
                               if scores_validos else None,
            "estado_global":   "ALERTA" if areas_alerta else "OK",
            "areas_en_alerta": areas_alerta,
        }

        with open(DATA_JSON, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

        print(f"  Nodos globales escritos en {DATA_JSON}")
        print(f"  Inicializados   : {nodos_inicializados}")
        print(f"  Preservados     : {nodos_preservados}")

        return {
            "inicializados": nodos_inicializados,
            "preservados":   nodos_preservados,
            "total":         len(list_areas()),
        }

    # ── Publicación data.json (Protocolo Cero Footprint) ───────────────────────

    def _sello_datos(self, area_name: str, fecha: str,
                     ndvi: Optional[float], rinde: Optional[float]) -> str:
        """
        SHA-256 determinístico sobre campos clave del análisis.
        Independiente del PNG — verificable sin necesidad del archivo.
        Formato: SHA256("area_name|YYYY-MM-DD HH:MM|ndvi:.4f|rinde:.2f")
        """
        ndvi_s   = f"{ndvi:.4f}"   if ndvi   is not None else "null"
        rinde_s  = f"{rinde:.2f}"  if rinde  is not None else "null"
        cadena   = f"{area_name}|{fecha}|{ndvi_s}|{rinde_s}"
        return hashlib.sha256(cadena.encode('utf-8')).hexdigest()

    def _audit_log(self, area_name: str, fecha: str, insight: InsightEconomico,
                   sello_datos: str):
        """Append a audit_log.jsonl — Proof of Work histórico."""
        log_path = Path(__file__).parent / 'audit_log.jsonl'
        entrada  = {
            "timestamp":   fecha,
            "area":        area_name,
            "score":       insight.score_faro,
            "rinde_tha":   insight.rinde_estimado_tha,
            "estado":      insight.estado,
            "sello_datos": sello_datos,
            "hash_png":    insight.sha256_reporte,
        }
        with open(log_path, 'a', encoding='utf-8') as f:
            f.write(json.dumps(entrada, ensure_ascii=False) + '\n')

    def _publicar(self, crudos: DatosCrudos, insight: InsightEconomico):
        """
        Escribe data.json con tres secciones separadas:
          _interno  → datos técnicos (no exponer en web pública)
          insights  → lo que ve el cliente
          resumen   → métricas globales del sistema
        Solo escribe claves generadas por el pipeline — nunca preserva
        campos demo/legacy (accuracy_pct, readings_verified, etc.).
        """
        # Leer JSON existente para preservar otras áreas
        data = {}
        if DATA_JSON.exists():
            try:
                with open(DATA_JSON, encoding='utf-8') as f:
                    data = json.load(f)
            except json.JSONDecodeError:
                data = {}

        # Eliminar campos demo/legacy que no deben estar en producción
        CAMPOS_LEGACY = {'accuracy_pct', 'readings_verified', 'sla_pct',
                         'vaca_muerta', 'lithium_puna', 'faro_arg_score'}
        for campo in CAMPOS_LEGACY:
            data.pop(campo, None)

        ahora = datetime.now().strftime("%Y-%m-%d %H:%M")

        # ── _meta ─────────────────────────────────────────────────
        data['_meta'] = {
            "nota":         "Generado automaticamente por faro_engine.py",
            "generado_en":  ahora,
            "generado_por": "faro_engine.py",
            "protocolo":    "Cero Footprint v1",
        }

        # ── _interno ──────────────────────────────────────────────
        data.setdefault('_interno', {})
        data['_interno'][crudos.area_name] = {
            "sar_medio_db":        crudos.sar_medio_db,
            "ndvi_medio":          crudos.ndvi_medio,
            "indice_fusion_medio": crudos.indice_fusion_medio,
            "pixeles_analizados":  crudos.pixeles_analizados,
            "pct_anomalia":        crudos.pct_anomalia,
            "reporte_png":         crudos.reporte_png,
            "sha256_completo":     crudos.sha256_completo,
            "tiene_sar":           crudos.tiene_sar,
            "tiene_ndvi":          crudos.tiene_ndvi,
            "tiene_vision":        crudos.tiene_vision,
            "ultimo_run":          ahora,
        }

        # ── Sello de datos + audit log ────────────────────────────
        sello = self._sello_datos(
            crudos.area_name, ahora,
            crudos.ndvi_medio, insight.rinde_estimado_tha
        )
        self._audit_log(crudos.area_name, ahora, insight, sello)

        # ── insights (CAPA CLIENTE) ───────────────────────────────
        data.setdefault('insights', {})
        data['insights'][insight.area] = {
            "zona":               insight.zona,
            "vertical":           insight.vertical,
            "fecha":              insight.fecha,
            "rinde_estimado_tha": insight.rinde_estimado_tha,
            "indice_actividad":   insight.indice_actividad,
            "score_faro":         insight.score_faro,
            "confianza":          insight.confianza,
            "estado":             insight.estado,
            "alerta":             insight.alerta,
            "pct_anomalia":       insight.pct_anomalia,
            "sha256_reporte":     insight.sha256_reporte,
            "sello_datos":        sello,
        }

        # ── resumen global ────────────────────────────────────────
        todos_insights = data.get('insights', {})
        scores_validos = [v['score_faro'] for v in todos_insights.values()
                          if v.get('score_faro') is not None]
        areas_alerta   = [k for k, v in todos_insights.items()
                          if v.get('estado') == 'ALERTA']

        data['resumen'] = {
            "ultimo_run":      ahora,
            "areas_activas":   len(todos_insights),
            "score_promedio":  round(sum(scores_validos) / len(scores_validos), 1)
                               if scores_validos else None,
            "estado_global":   "ALERTA" if areas_alerta else "OK",
            "areas_en_alerta": areas_alerta,
        }

        # ── pipeline (compatibilidad hacia atrás con faro_visor.html) ──
        data.setdefault('pipeline', {})
        data['pipeline']['ultimo_run'] = ahora
        data['pipeline'].setdefault(crudos.area_name, {}).update({
            "estado":              insight.estado,
            "ultimo_run":          ahora,
            "ndvi_medio":          crudos.ndvi_medio,
            "sar_medio_db":        crudos.sar_medio_db,
            "indice_fusion_medio": crudos.indice_fusion_medio,
            "pixeles_analizados":  crudos.pixeles_analizados,
            "sha256":              insight.sha256_reporte,
            "score_faro":          insight.score_faro,
            "rinde_estimado_tha":  insight.rinde_estimado_tha,
            "indice_actividad":    insight.indice_actividad,
            "pct_anomalia":        insight.pct_anomalia,
        })

        with open(DATA_JSON, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

        print(f"  data.json actualizado [{crudos.area_name}] "
              f"— Score: {insight.score_faro} | Estado: {insight.estado}")


# ─── CLI ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description='FARO ENGINE — Motor centralizado Faro Protocol',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Areas disponibles: " + ', '.join(list_areas()) + "\n\n"
            "El engine es el punto de entrada recomendado. Produce:\n"
            "  - Reporte PNG (fusion SAR + optico)\n"
            "  - Clasificacion PNG (vigor + anomalias)\n"
            "  - SHA-256 del reporte\n"
            "  - data.json actualizado (Protocolo Cero Footprint)\n"
        )
    )
    parser.add_argument(
        '--area', required=False, default=None,
        help='Nombre del area (ej: cordoba, vaca_muerta, balcarce)'
    )
    parser.add_argument(
        '--sar-file',
        help='Path al SAR .tiff crudo (si el georef.tif ya existe, omitir)'
    )
    parser.add_argument(
        '--skip-georef', action='store_true',
        help='Omitir georreferenciacion SAR (el .tif ya existe)'
    )
    parser.add_argument(
        '--skip-vision', action='store_true',
        help='Omitir vision computacional (Fase 6)'
    )
    parser.add_argument(
        '--init-nodos', action='store_true',
        help='Inicializar data.json con todos los nodos globales (sin procesar raster)'
    )
    args = parser.parse_args()

    engine = FaroEngine()

    if args.init_nodos:
        print("\n" + "=" * 60)
        print("  FARO ENGINE — Inicializando nodos globales")
        print(f"  Fecha: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
        print("=" * 60)
        resultado = engine.init_nodos_globales()
        print(f"\n  Total nodos : {resultado['total']}")
        print("=" * 60)
        return

    if not args.area:
        print("ERROR: --area es requerido cuando no se usa --init-nodos")
        parser.print_help()
        sys.exit(1)

    engine.analizar_area(
        area_name   = args.area,
        sar_file    = args.sar_file,
        skip_georef = args.skip_georef,
        skip_vision = args.skip_vision,
    )


if __name__ == '__main__':
    main()


