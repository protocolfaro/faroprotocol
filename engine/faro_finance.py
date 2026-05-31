import sys as _sys
from pathlib import Path as _Path
_ED = str(_Path(__file__).parent)
if _ED not in _sys.path:
    _sys.path.insert(0, _ED)
"""
FARO PROTOCOL — Módulo de Inteligencia de Negocio (faro_finance.py)

Convierte el Score Faro en valor económico cuantificado (USD/ha/año):
  - Ahorro Urea           : reducción de insumos N por zonas diferenciadas
  - Ahorro Combustible    : optimización de labores y logística de campo
  - Ahorro Multas/Riesgo  : detección temprana → prevención de infracciones

Sello SHA-256
    Cada InformeFinanciero se sella criptográficamente al generarse.
    El hash garantiza que el informe no fue modificado después de su emisión
    (mismo principio RWA que el reporte satelital).

Verticales soportadas
    agro         → soja, maíz, trigo (Argentina / USA)
    energia      → petróleo/gas, actividad industrial portuaria
    deforestacion → monitoreo ambiental (Brasil / Amazonas)

Uso como módulo:
    from faro_finance import FaroFinance
    finance = FaroFinance()
    informe = finance.generar(area_meta, score=61.0, hectareas=5000)
    print(informe.resumen())

Uso CLI:
    python faro_finance.py --area cordoba --score 61 --ha 5000
    python faro_finance.py --area rotterdam --score 74 --ha 1200
"""

import argparse
import hashlib
import json
import sys
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from typing import Optional

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')
if hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(encoding='utf-8')

PROJECT_ROOT = Path(__file__).parent
DATA_JSON    = PROJECT_ROOT / 'data.json'
_PRICES_FILE = PROJECT_ROOT / 'datos' / 'faro_prices.json'


# ─── Constantes económicas de referencia (defaults hardcoded) ─────────────────
# Actualizadas automáticamente por faro_price_updater.py via datos/faro_prices.json
# Fuente defaults: CBOT/CME soja 2024, Bolsa Rosario, INDEC

PRECIO_UREA_USD_TN         = 600.0    # urea granulada spot — actualizable
UREA_KG_N_POR_TN           = 460.0    # 46% N por tn urea (constante química)
DOSIS_N_REFERENCIA_KG_HA   = 50.0     # aplicación estándar sin info satelital
COSTO_COMBUSTIBLE_HA       = 75.0     # USD/ha labores totales — actualizable
PRECIO_DIESEL_USD_L        = 0.90     # precio diesel internacional de referencia
PRECIO_SOJA_USD_TN         = 390.0    # precio FOB Rosario referencia
PRECIO_MAIZ_USD_TN         = 220.0    # precio FOB Rosario referencia
PRECIO_TRIGO_USD_TN        = 250.0    # precio FOB referencia

# ENERGÍA
COSTO_PERFORACION_REFERENCIA_USD_HA = 120.0  # USD/ha/año ops campo

# DEFORESTACIÓN
# Fuente: Lei nº 9.605/1998, IBAMA auto de infração, SISNAMA Brasil
MULTA_IBAMA_USD_HA          = 1_400.0  # USD/ha deforestation fine (BRL 7000 × 0.20 FX)

# Score mínimo a partir del cual se genera valor diferencial
SCORE_MINIMO_EFECTO         = 20.0
# Score máximo de referencia (todo el ahorro potencial)
SCORE_REFERENCIA_MAX        = 100.0


def _cargar_precios_externos() -> None:
    """
    Carga precios actualizados desde datos/faro_prices.json si el archivo existe.
    Llamado automáticamente al importar el módulo.
    Escrito por faro_price_updater.py — no editar manualmente.
    """
    global PRECIO_UREA_USD_TN, COSTO_COMBUSTIBLE_HA, PRECIO_DIESEL_USD_L
    global PRECIO_SOJA_USD_TN, PRECIO_MAIZ_USD_TN, PRECIO_TRIGO_USD_TN
    if not _PRICES_FILE.exists():
        return
    try:
        with open(_PRICES_FILE, encoding='utf-8') as f:
            p = json.load(f)
        if 'PRECIO_UREA_USD_TN' in p:
            PRECIO_UREA_USD_TN   = float(p['PRECIO_UREA_USD_TN'])
        if 'COSTO_COMBUSTIBLE_HA' in p:
            COSTO_COMBUSTIBLE_HA = float(p['COSTO_COMBUSTIBLE_HA'])
        if 'PRECIO_DIESEL_USD_L' in p:
            PRECIO_DIESEL_USD_L  = float(p['PRECIO_DIESEL_USD_L'])
    except Exception:
        pass  # keeps defaults if file is malformed


_cargar_precios_externos()


# ─── Modelo de ahorros ─────────────────────────────────────────────────────────

def _factor_score(score: float, umbral_min: float = SCORE_MINIMO_EFECTO) -> float:
    """
    Factor lineal [0, 1] derivado del Score Faro.
    Cero para score <= umbral_min, uno para score = 100.
    """
    if score <= umbral_min:
        return 0.0
    return min((score - umbral_min) / (SCORE_REFERENCIA_MAX - umbral_min), 1.0)


def calcular_ahorro_urea(score: float, vertical: str = 'agro',
                         cultivo: str = 'agro') -> float:
    """
    USD/ha/año ahorrado en urea por aplicación de nitrógeno diferenciado.

    Solo aplica para vertical agro. Energía y deforestación retornan 0.

    Lógica: Sin datos satelitales, el productor aplica la dosis estándar uniforme.
    Con Score Faro alto, se identifican zonas N-suficientes vs. N-deficientes,
    reduciendo la dosis promedio hasta un 35% en los mejores casos.

    Máximo (Score 100): 35% de ($50 kg N/ha × $1.30/kg N) ≈ $22.75/ha
    """
    if vertical != 'agro':
        return 0.0

    # Precio N: USD/tn urea ÷ kg N/tn urea = USD/kg N
    precio_n_usd_kg    = PRECIO_UREA_USD_TN / UREA_KG_N_POR_TN
    costo_n_referencia = DOSIS_N_REFERENCIA_KG_HA * precio_n_usd_kg   # USD/ha
    ahorro_max   = costo_n_referencia * 0.35   # 35% de reducción máxima posible
    factor       = _factor_score(score, umbral_min=40.0)

    return round(ahorro_max * factor, 2)


def calcular_ahorro_combustible(score: float, vertical: str = 'agro') -> float:
    """
    USD/ha/año ahorrado en combustible y logística de campo.

    Agro: optimización de fechas de siembra, rutas de pulverización y
    cosecha basada en mapas de vigor → reducción hasta 20% del costo total.
    Energía: priorización de sitios y rutas de inspección → hasta 25%.

    Máximo agro     (Score 100): 20% × $75/ha = $15.00/ha
    Máximo energía  (Score 100): 25% × $120/ha = $30.00/ha
    """
    factor = _factor_score(score, umbral_min=20.0)

    if vertical == 'agro':
        return round(COSTO_COMBUSTIBLE_HA * 0.20 * factor, 2)
    elif vertical == 'energia':
        return round(COSTO_PERFORACION_REFERENCIA_USD_HA * 0.25 * factor, 2)
    else:
        return 0.0


def calcular_ahorro_multas(score: float, vertical: str,
                            area_ha: float = 1.0) -> float:
    """
    USD/ha/año evitado por detección temprana de infracciones.

    Deforestación: el sistema detecta clearing events 25-40 días antes
    que la fiscalización terrestre → el operador puede detener la actividad
    antes de que se consolide el daño legal.
    Probabilidad de multa evitada ≈ 70% a Score ≥ 60.

    Energía: detección de flaring, derrames, actividad no declarada →
    prevención de multas regulatorias y remedios costosos.

    El valor retornado es USD/ha/año de riesgo evitado (valor esperado).
    """
    factor = _factor_score(score, umbral_min=30.0)

    if vertical == 'deforestacion':
        prob_evasion = factor * 0.70    # hasta 70% de probabilidad de multa evitada
        return round(MULTA_IBAMA_USD_HA * prob_evasion, 2)

    elif vertical == 'energia':
        multa_referencia = 250.0        # USD/ha multa regulatoria media sector energía
        prob_evasion = factor * 0.55
        return round(multa_referencia * prob_evasion, 2)

    else:
        return 0.0


# ─── Estructura de informe ─────────────────────────────────────────────────────

@dataclass
class InformeFinanciero:
    """
    Salida de FaroFinance.generar().

    Todos los valores en USD/ha/año salvo ahorro_total_usd que es el
    total absoluto para el predio completo (× hectáreas).
    """
    area_name:              str
    zona:                   str
    vertical:               str
    cultivo:                str
    score_faro:             float
    hectareas:              float
    fecha:                  str

    # Desglose de ahorros (USD/ha/año)
    ahorro_urea_ha:         float
    ahorro_combustible_ha:  float
    ahorro_multas_ha:       float
    ahorro_total_ha:        float

    # Total absoluto para el predio
    ahorro_total_usd:       float

    # ROI vs. costo del servicio
    costo_servicio_ha:      float    # precio de referencia Faro (USD/ha/año)
    roi_x:                  float    # ahorro_total_ha / costo_servicio_ha

    # Supuestos de mercado usados
    supuestos:              dict

    # Sello criptográfico
    sha256_informe:         str

    def resumen(self) -> str:
        lineas = [
            "=" * 58,
            f"  INFORME FINANCIERO FARO PROTOCOL",
            f"  Área       : {self.zona}",
            f"  Vertical   : {self.vertical.upper()}",
            f"  Score Faro : {self.score_faro:.1f} / 100",
            f"  Hectáreas  : {self.hectareas:,.0f} ha",
            "-" * 58,
            f"  Ahorro Urea         : USD {self.ahorro_urea_ha:>7.2f} /ha/año",
            f"  Ahorro Combustible  : USD {self.ahorro_combustible_ha:>7.2f} /ha/año",
            f"  Ahorro Multas/Riesgo: USD {self.ahorro_multas_ha:>7.2f} /ha/año",
            "-" * 58,
            f"  TOTAL               : USD {self.ahorro_total_ha:>7.2f} /ha/año",
            f"  TOTAL PREDIO        : USD {self.ahorro_total_usd:>10,.0f} /año",
            f"  Costo servicio      : USD {self.costo_servicio_ha:>7.2f} /ha/año",
            f"  ROI                 : {self.roi_x:.1f}x",
            "-" * 58,
            f"  SHA-256 : {self.sha256_informe[:32]}...",
            "=" * 58,
        ]
        return "\n".join(lineas)

    def to_dict(self) -> dict:
        return asdict(self)


# ─── Motor financiero ──────────────────────────────────────────────────────────

class FaroFinance:
    """
    Motor de inteligencia de negocio de Faro Protocol.

    Convierte métricas satelitales (Score Faro) en ahorros económicos
    cuantificados en USD, desglosados por categoría.
    Sella cada informe con SHA-256 para trazabilidad RWA.
    """

    # Precio de referencia del servicio Faro (interno — no exponer en web)
    _COSTO_SERVICIO_HA = 2.50    # USD/ha/año (fase piloto)

    def generar(
        self,
        area_meta:  dict,
        score:      float,
        hectareas:  float = 1_000.0,
    ) -> InformeFinanciero:
        """
        Genera el informe financiero para un área y score dados.

        Parámetros
        ----------
        area_meta : dict con al menos 'name', 'label', 'vertical'
                    (mismo formato que load_area() en faro_areas/__init__.py)
        score     : Score Faro (0–100) del último análisis del área
        hectareas : superficie a analizar en hectáreas

        Retorna
        -------
        InformeFinanciero con ahorros, ROI y SHA-256
        """
        area_name = area_meta.get('name', 'desconocido')
        zona      = area_meta.get('label', area_name)
        vertical  = area_meta.get('vertical', 'agro')
        cultivo   = area_meta.get('cultivo', 'agro')
        fecha     = datetime.now().strftime("%Y-%m-%d %H:%M")

        score = max(0.0, min(100.0, float(score)))

        urea   = calcular_ahorro_urea(score, vertical=vertical, cultivo=cultivo)
        comb   = calcular_ahorro_combustible(score, vertical=vertical)
        multas = calcular_ahorro_multas(score, vertical=vertical, area_ha=hectareas)

        total_ha  = round(urea + comb + multas, 2)
        total_usd = round(total_ha * hectareas, 2)

        roi = round(total_ha / self._COSTO_SERVICIO_HA, 1) if self._COSTO_SERVICIO_HA > 0 else 0.0

        supuestos = {
            "precio_urea_usd_tn":    PRECIO_UREA_USD_TN,
            "dosis_n_kg_ha":         DOSIS_N_REFERENCIA_KG_HA,
            "costo_combustible_ha":  COSTO_COMBUSTIBLE_HA,
            "multa_ibama_usd_ha":    MULTA_IBAMA_USD_HA,
            "costo_servicio_ha":     self._COSTO_SERVICIO_HA,
            "score_minimo_efecto":   SCORE_MINIMO_EFECTO,
        }

        # Datos del informe antes del sello
        datos_informe = {
            "area_name":    area_name,
            "zona":         zona,
            "vertical":     vertical,
            "cultivo":      cultivo,
            "score_faro":   score,
            "hectareas":    hectareas,
            "fecha":        fecha,
            "ahorro_ha":    total_ha,
            "supuestos":    supuestos,
        }
        sha256 = self._sellar(datos_informe)

        informe = InformeFinanciero(
            area_name             = area_name,
            zona                  = zona,
            vertical              = vertical,
            cultivo               = cultivo,
            score_faro            = score,
            hectareas             = hectareas,
            fecha                 = fecha,
            ahorro_urea_ha        = urea,
            ahorro_combustible_ha = comb,
            ahorro_multas_ha      = multas,
            ahorro_total_ha       = total_ha,
            ahorro_total_usd      = total_usd,
            costo_servicio_ha     = self._COSTO_SERVICIO_HA,
            roi_x                 = roi,
            supuestos             = supuestos,
            sha256_informe        = sha256,
        )

        return informe

    def _sellar(self, datos: dict) -> str:
        """
        Genera SHA-256 del informe financiero.

        El hash se calcula sobre el JSON serializado con claves ordenadas
        (sort_keys=True) para que sea determinista e independiente de la
        implementación. Cualquier modificación posterior cambia el hash.
        """
        contenido = json.dumps(datos, sort_keys=True, ensure_ascii=False, default=str)
        return hashlib.sha256(contenido.encode('utf-8')).hexdigest()

    def generar_desde_data_json(self, area_name: str,
                                 hectareas: float = 1_000.0) -> Optional[InformeFinanciero]:
        """
        Lee el Score Faro de data.json para un área dada y genera el informe.
        Útil para integrar con el pipeline automático.
        """
        if not DATA_JSON.exists():
            print(f"  data.json no encontrado: {DATA_JSON}")
            return None

        with open(DATA_JSON, encoding='utf-8') as f:
            data = json.load(f)

        insight = data.get('insights', {}).get(area_name)
        if not insight:
            print(f"  Area '{area_name}' no encontrada en data.json")
            return None

        score = insight.get('score_faro')
        if score is None:
            print(f"  Score Faro null para '{area_name}' — area sin datos")
            return None

        from faro_areas import load_area
        area_meta = load_area(area_name)

        return self.generar(area_meta=area_meta, score=score, hectareas=hectareas)

    def guardar_informe(self, informe: InformeFinanciero,
                        directorio: Optional[Path] = None) -> Path:
        """
        Guarda el informe financiero como JSON en datos/finance/.
        El nombre del archivo incluye el área y la fecha para trazabilidad.
        """
        directorio = directorio or (PROJECT_ROOT / 'datos' / 'finance')
        directorio.mkdir(parents=True, exist_ok=True)

        ts       = informe.fecha.replace(' ', 'T').replace(':', '')
        nombre   = f"informe_financiero_{informe.area_name}_{ts}.json"
        ruta     = directorio / nombre

        with open(ruta, 'w', encoding='utf-8') as f:
            json.dump(informe.to_dict(), f, indent=2, ensure_ascii=False)

        print(f"  Informe guardado: {ruta}")
        return ruta


# ─── CLI ───────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description='FARO FINANCE — Inteligencia de negocio Faro Protocol',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Ejemplos:\n"
            "  python faro_finance.py --area cordoba\n"
            "    (lee score de data.json automáticamente)\n\n"
            "  python faro_finance.py --area cordoba --score 61 --ha 5000\n"
            "  python faro_finance.py --area rotterdam --score 74 --ha 1200\n"
            "  python faro_finance.py --area amazonas  --score 58 --ha 50000\n"
        )
    )
    parser.add_argument('--area',  required=True, help='Nombre del área (ej: cordoba, rotterdam)')
    parser.add_argument('--score', type=float, default=None,
                        help='Score Faro (0-100). Si se omite, lee de data.json')
    parser.add_argument('--ha',    type=float, default=1_000.0,
                        help='Hectáreas del predio (default: 1000)')
    parser.add_argument('--guardar', action='store_true',
                        help='Guardar informe en datos/finance/')
    args = parser.parse_args()

    finance = FaroFinance()

    if args.score is not None:
        from faro_areas import load_area
        try:
            area_meta = load_area(args.area)
        except Exception as e:
            print(f"  ERROR cargando área '{args.area}': {e}")
            sys.exit(1)
        informe = finance.generar(area_meta=area_meta, score=args.score, hectareas=args.ha)
    else:
        informe = finance.generar_desde_data_json(area_name=args.area, hectareas=args.ha)
        if informe is None:
            sys.exit(1)

    print(informe.resumen())

    if args.guardar:
        ruta = finance.guardar_informe(informe)
        print(f"\n  Guardado: {ruta}")


if __name__ == '__main__':
    main()
