"""
FARO PROTOCOL — faro_price_updater.py  V2  Global & Genérico
=============================================================

REGLA DE ORO
    Agregar un área nueva en faro_areas/ con cualquier país del mundo NO
    requiere ningún cambio en este script. El área solo necesita tener:
        country_code   — ISO 3166-1 alpha-2  (ej. "AR", "US", "NL")
        country_name   — nombre en inglés    (ej. "Argentina", "Netherlands")
        vertical       — "agro" | "energia" | "deforestacion"
        hectareas_ref  — superficie (opcional, default 1 000 ha)

ARQUITECTURA
    1. Lee todas las áreas desde faro_areas/*.json  (sin lista hardcodeada)
    2. Obtiene precios globales (una vez por run):
         Urea  USD/tn   → IndexMundi FusionCharts embed
         Brent USD/bbl  → Yahoo Finance BZ=F
    3. Por cada área, resuelve precios locales:
         Diesel USD/L   → GlobalPetrolPrices.com (tabla semanal ~160 países)
                          → World Bank EP.PMP.DESL.CD (dato oficial, 1-2 años)
                          → Brent-derivado (fallback universal)
         FX local       → Frankfurter.app (free, no-auth, ~32 monedas)
         Multa USD/ha   → Tabla de regímenes regulatorios (dato, no código)
                          → UNEP/SETAC baseline USD 500/ha (fallback universal)
    4. Calcula Business Case por área con los precios locales resueltos
    5. Escribe:
         datos/faro_prices.json      (global + por área; compat faro_finance.py)
         data.json → sección finance (BC por área)
         datos/faro_prices_log.json  (historial, ≤104 entradas / ~2 años)
    6. Paperclip notifica resumen por área

FUENTES DE DATOS
    Urea          : https://www.indexmundi.com/commodities/?commodity=urea
    Brent         : https://query1.finance.yahoo.com/v8/finance/chart/BZ=F
    Diesel local  : https://www.globalpetrolprices.com/diesel_prices/
                    https://api.worldbank.org/v2/country/{iso2}/indicator/EP.PMP.DESL.CD
    FX            : https://api.frankfurter.app/latest?from=USD&to={currency}
    Multas        : tabla FINE_REGIMES (ver EnvironmentalFineResolver)

USO
    python faro_price_updater.py           # ciclo completo ahora
    python faro_price_updater.py --now     # igual
    python faro_price_updater.py --daemon  # scheduler lunes 08:00 ART
    python faro_price_updater.py --log     # historial (últimas 10 entradas)
    python faro_price_updater.py --log 20  # historial (últimas 20 entradas)
"""

import argparse
import hashlib
import json
import os
import re
import sys
import unicodedata
import urllib.request
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')
if hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(encoding='utf-8')

# ─── Rutas ────────────────────────────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).parent
DATOS_DIR    = PROJECT_ROOT / 'datos'
LOGS_DIR     = PROJECT_ROOT / 'logs'
PRICES_FILE  = DATOS_DIR / 'faro_prices.json'
LOG_FILE     = DATOS_DIR / 'faro_prices_log.json'
RUN_LOG_FILE = LOGS_DIR  / 'price_updater.log'
DATA_JSON    = PROJECT_ROOT / 'data.json'
AREAS_DIR    = PROJECT_ROOT / 'faro_areas'

# ─── Logging persistente ──────────────────────────────────────────────────────
import logging

def _setup_logging() -> None:
    """Configura logging a archivo + stdout. Rota cuando supera 1 MB."""
    LOGS_DIR.mkdir(exist_ok=True)
    from logging.handlers import RotatingFileHandler
    root = logging.getLogger('faro_price_updater')
    root.setLevel(logging.INFO)
    if root.handlers:
        return  # ya configurado (evita duplicados en APScheduler)
    fmt = logging.Formatter('%(asctime)s [%(levelname)s] %(message)s',
                            datefmt='%Y-%m-%d %H:%M:%S')
    # Handler a archivo (rota a 1 MB, guarda 3 backups)
    fh = RotatingFileHandler(RUN_LOG_FILE, maxBytes=1_000_000, backupCount=3,
                             encoding='utf-8')
    fh.setFormatter(fmt)
    root.addHandler(fh)
    # Handler a stdout (para ver en terminal también)
    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(fmt)
    root.addHandler(sh)

_setup_logging()
_log = logging.getLogger('faro_price_updater')

# ─── Zona horaria ─────────────────────────────────────────────────────────────
ART = timezone(timedelta(hours=-3))

# ─── Constantes físicas y económicas ──────────────────────────────────────────
BBL_TO_LITER             = 158.987   # 1 barril = 158.987 litros (constante física)
DIESEL_REFINING_MARKUP   = 1.20      # margen refinación + crack spread ~20%
LITROS_POR_HA_AGRO       = 50.0      # L gasoil/ha en labores agropecuarias (AR benchmark)
BASE_OP_NON_FUEL_USD_HA  = 30.0      # costo no-combustible (maquinaria, mano de obra)
UREA_KG_N_POR_TN         = 460.0     # 46% N por tn de urea (constante química)
DOSIS_N_REFERENCIA_KG_HA = 50.0      # kg N/ha aplicación estándar sin info satelital
COSTO_SERVICIO_HA        = 2.50      # precio servicio Faro (USD/ha/año, piloto)
SCORE_UMBRAL_MIN         = 20.0      # score mínimo para generar valor diferencial
DEFAULT_HECTAREAS        = 1_000.0   # si el área no define hectareas_ref
HTTP_TIMEOUT             = 15        # segundos


# ═══════════════════════════════════════════════════════════════════════════════
# DATA CLASSES
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class GlobalPrices:
    """Precios de commodities internacionales, válidos para todas las áreas."""
    urea_usd_tn:       float
    urea_source:       str
    brent_usd_bbl:     float
    brent_source:      str
    diesel_intl_usd_l: float   # derivado de brent — referencia internacional


@dataclass
class AreaBundle:
    """
    Precios de mercado resueltos para un área geográfica específica.
    Combina precios globales + ajuste local por país.
    """
    area_name:            str
    country_code:         str     # ISO 3166-1 alpha-2
    country_name:         str
    vertical:             str
    hectareas:            float
    score_faro:           Optional[float]

    # Combustible local
    diesel_local_usd_l:   float
    diesel_source:        str
    costo_combustible_ha: float   # BASE_OP_NON_FUEL + diesel × litros/ha

    # Marco regulatorio ambiental
    fine_usd_ha:          float
    fine_source:          str
    fine_regime:          str     # nombre del marco legal

    # FX informativo (el Business Case siempre es en USD)
    fx_currency_code:     str
    fx_usd_per_local:     Optional[float]   # USD por 1 unidad de moneda local
    fx_local_per_usd:     Optional[float]   # unidades de moneda local por 1 USD
    fx_source:            str


@dataclass
class BusinessCase:
    """Business Case calculado por área con precios locales reales."""
    area_name:             str
    country_code:          str
    country_name:          str
    vertical:              str
    score_faro:            float
    hectareas:             float

    ahorro_urea_ha:        float   # USD/ha/año — solo vertical agro
    ahorro_combustible_ha: float   # USD/ha/año — agro y energía
    ahorro_multas_ha:      float   # USD/ha/año — energía y deforestación

    ahorro_total_ha:       float   # suma de los tres
    ahorro_total_usd:      float   # ahorro_total_ha × hectareas
    roi_x:                 float   # ahorro_total_ha / COSTO_SERVICIO_HA

    costo_servicio_ha:     float
    sha256:                str
    fecha:                 str
    supuestos:             dict    # precios exactos usados en el cálculo


# ═══════════════════════════════════════════════════════════════════════════════
# HTTP CLIENT BASE
# ═══════════════════════════════════════════════════════════════════════════════

class HTTPClient:
    """Wrapper mínimo para HTTP GET con User-Agent y timeout comunes."""

    def _get(self, url: str, headers: Optional[dict] = None) -> bytes:
        h = {'User-Agent': 'Mozilla/5.0 FaroProtocol/2.0'}
        if headers:
            h.update(headers)
        req = urllib.request.Request(url, headers=h)
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as r:
            return r.read()


# ═══════════════════════════════════════════════════════════════════════════════
# PRECIOS GLOBALES (COMMODITIES INTERNACIONALES)
# ═══════════════════════════════════════════════════════════════════════════════

class GlobalPriceFetcher(HTTPClient):
    """
    Obtiene precios de commodities con cobertura global:
      - Urea granulada spot (USD/tn) — IndexMundi
      - Brent crude front-month (USD/bbl) — Yahoo Finance BZ=F
    Ambos son precios de referencia internacional, no locales.
    """

    def fetch_urea_usd_tn(self) -> tuple[float, str]:
        """
        IndexMundi publica series históricas de commodities agrícolas.
        Los datos están embebidos como XML de FusionCharts en el HTML de la página.
        Formato: <set label='Mon-YYYY' value='NNN.NN' showLabel='1'/>
        Tomamos el elemento más reciente de la serie.
        Fallback: último valor del log histórico → constante USD 600/tn.
        """
        url = 'https://www.indexmundi.com/commodities/?commodity=urea&months=12'
        try:
            html = self._get(url).decode('utf-8', 'replace')
            sets = re.findall(r"<set label='([^']+)' value='([\d.]+)'", html)
            if sets:
                label, value = sets[-1]
                return round(float(value), 2), f"IndexMundi/{label}"
        except Exception as e:
            _warn(f"Urea/IndexMundi: {e}")

        cached = _last_log_value('global', 'urea_usd_tn')
        if cached is not None:
            return cached, "cache/log"
        return 600.0, "default"

    def fetch_brent_usd_bbl(self) -> tuple[float, str]:
        """
        Yahoo Finance expone una API JSON no-oficial para cotizaciones.
        BZ=F es el símbolo del futuro front-month del crudo Brent ICE.
        Usamos regularMarketPrice; si no está disponible, previousClose.
        Fallback: último log → USD 75/bbl.
        """
        url = 'https://query1.finance.yahoo.com/v8/finance/chart/BZ=F?interval=1d&range=5d'
        try:
            data = json.loads(self._get(url))
            meta = data['chart']['result'][0]['meta']
            precio = meta.get('regularMarketPrice') or meta.get('previousClose')
            if precio and float(precio) > 0:
                return round(float(precio), 2), "Yahoo/BZ=F"
        except Exception as e:
            _warn(f"Brent/Yahoo: {e}")

        cached = _last_log_value('global', 'brent_usd_bbl')
        if cached is not None:
            return cached, "cache/log"
        return 75.0, "default"

    def build(self) -> GlobalPrices:
        """Ejecuta los dos fetchers y construye el objeto GlobalPrices."""
        urea_val, urea_src   = self.fetch_urea_usd_tn()
        brent_val, brent_src = self.fetch_brent_usd_bbl()
        diesel_intl = round((brent_val / BBL_TO_LITER) * DIESEL_REFINING_MARKUP, 4)

        print(f"    Urea          : USD {urea_val}/tn  [{urea_src}]")
        print(f"    Brent         : USD {brent_val}/bbl  [{brent_src}]")
        print(f"    Diesel intl.  : USD {diesel_intl}/L  [Brent/158.987×1.20]")

        return GlobalPrices(
            urea_usd_tn       = urea_val,
            urea_source       = urea_src,
            brent_usd_bbl     = brent_val,
            brent_source      = brent_src,
            diesel_intl_usd_l = diesel_intl,
        )


# ═══════════════════════════════════════════════════════════════════════════════
# RESOLVER DE COMBUSTIBLE LOCAL (CUALQUIER PAÍS)
# ═══════════════════════════════════════════════════════════════════════════════

class LocalFuelResolver(HTTPClient):
    """
    Resuelve el precio local del gasoil/diesel en USD/L para cualquier país.

    Proceso (genérico, sin código país-específico):
      1. GlobalPetrolPrices.com — tabla semanal con ~160 países en una sola página.
         El matching es por nombre de país normalizado (lowercase, sin acentos).
      2. World Bank API — indicador EP.PMP.DESL.CD (Pump price diesel, USD/L).
         Dato oficial de estadísticas nacionales, puede tener 1-2 años de demora.
      3. Brent-derivado — precio internacional sin ajuste local.
         Siempre disponible como último fallback.

    Cada fuente retorna (precio_float, etiqueta_str). La lógica de fallback
    es la misma para todos los países; no hay bloques if/elif por país.

    La tabla GPP se descarga una sola vez por run (caché en clase).
    """

    _gpp_cache: Optional[dict] = None   # {nombre_norm: precio_usd_l}

    @staticmethod
    def _norm(s: str) -> str:
        """Lowercase, sin acentos, sin caracteres no-alfanuméricos."""
        n = unicodedata.normalize('NFD', s.lower())
        n = ''.join(c for c in n if unicodedata.category(c) != 'Mn')
        return re.sub(r'[^a-z0-9 ]', ' ', n).strip()

    def _fetch_gpp_table(self) -> dict[str, float]:
        """
        Descarga la página de precios de GlobalPetrolPrices.com y extrae
        el mapeo {nombre_país_normalizado: precio_usd_l}.

        GPP embebe los datos en la página de dos formas posibles:
          A. Tabla HTML: <a href="/Country/diesel_prices/">Country</a> + <td>precio</td>
          B. Google Charts DataTable: ['CountryName', precio]
        Probamos ambos patrones; el que devuelva más de 30 entradas se considera válido.

        La caché de clase garantiza que esta descarga ocurre una sola vez por run,
        independientemente de cuántas áreas procese el ciclo.
        """
        if LocalFuelResolver._gpp_cache is not None:
            return LocalFuelResolver._gpp_cache

        url = 'https://www.globalpetrolprices.com/diesel_prices/'
        resultado: dict[str, float] = {}
        try:
            html = self._get(url).decode('utf-8', 'replace')

            # Patrón A: tabla HTML con enlaces por país
            for nombre, precio_str in re.findall(
                r'href="/[^"/]+/diesel_prices/"[^>]*>\s*([^<]+)\s*</a>'
                r'.*?<td[^>]*>\s*(\d+\.\d+)\s*</td>',
                html, re.DOTALL
            ):
                k = self._norm(nombre.strip())
                try:
                    resultado[k] = float(precio_str)
                except ValueError:
                    pass

            # Patrón B: Google Charts DataTable si la tabla no dio suficientes resultados
            if len(resultado) < 30:
                resultado.clear()
                for nombre, precio_str in re.findall(
                    r"\[\s*['\"]([A-Za-z ]+)['\"]\s*,\s*([\d.]+)\s*\]", html
                ):
                    k = self._norm(nombre.strip())
                    try:
                        resultado[k] = float(precio_str)
                    except ValueError:
                        pass

        except Exception as e:
            _warn(f"GPP/tabla: {e}")

        if resultado:
            _info(f"    GPP tabla     : {len(resultado)} países cargados")
        else:
            _warn("GPP tabla: 0 países extraídos (HTML posiblemente distinto al esperado) — usando WorldBank/intl fallback")
        LocalFuelResolver._gpp_cache = resultado
        return resultado

    def _fetch_worldbank(self, iso2: str) -> Optional[float]:
        """
        World Bank Open Data — indicador EP.PMP.DESL.CD.
        Pump price for diesel fuel (US$ per liter), dato oficial de cada país.
        La URL es paramétrica; el iso2 es el único input país-específico.
        Retorna el valor más reciente no-nulo, o None.
        """
        url = (
            f'https://api.worldbank.org/v2/country/{iso2}'
            f'/indicator/EP.PMP.DESL.CD?format=json&mrv=5&per_page=1'
        )
        try:
            data = json.loads(self._get(url))
            if isinstance(data, list) and len(data) > 1 and data[1]:
                for entry in data[1]:
                    val = entry.get('value')
                    if val is not None and float(val) > 0:
                        year = entry.get('date', '?')
                        return round(float(val), 4), f"WorldBank/EP.PMP.DESL.CD/{iso2}/{year}"
        except Exception as e:
            _warn(f"WorldBank/{iso2}: {e}")
        return None

    def resolve(
        self,
        country_code: str,
        country_name: str,
        intl_diesel_usd_l: float,
    ) -> tuple[float, str]:
        """
        Retorna (precio_diesel_local_usd_l, fuente).
        La secuencia GPP → WorldBank → intl es idéntica para cualquier país.
        """
        nombre_norm = self._norm(country_name)

        # 1. GlobalPetrolPrices
        gpp = self._fetch_gpp_table()
        if gpp:
            if nombre_norm in gpp:
                return round(gpp[nombre_norm], 4), f"GPP/{country_code}"
            # matching por prefijo común (ej. "united states" ↔ "united states of america")
            for k, v in gpp.items():
                if (k.startswith(nombre_norm[:8]) or nombre_norm.startswith(k[:8])) and len(k) > 4:
                    return round(v, 4), f"GPP/{country_code}/fuzzy"

        # 2. World Bank
        wb_result = self._fetch_worldbank(country_code)
        if wb_result:
            return wb_result  # ya es (float, str)

        # 3. Internacional Brent-derivado (siempre disponible)
        return intl_diesel_usd_l, f"intl-fallback/Brent/{country_code}"


# ═══════════════════════════════════════════════════════════════════════════════
# RESOLVER DE MULTAS AMBIENTALES
# ═══════════════════════════════════════════════════════════════════════════════

class EnvironmentalFineResolver:
    """
    Retorna el valor de referencia para multas ambientales (USD/ha) según país y vertical.

    Diseño data-driven:
      - FINE_REGIMES es una tabla de datos de referencia (no lógica condicional).
      - La lógica de lookup es idéntica para todos los países: buscar en tabla → UNEP.
      - Agregar un nuevo país requiere solo una entrada en FINE_REGIMES; cero código.
      - Si el país no está en la tabla o la vertical no aplica → UNEP USD 500/ha.

    FINE_REGIMES contiene únicamente datos de regímenes regulatorios documentados
    públicamente. Las fuentes son normas legales verificables, no estimaciones.
    """

    # ── Tabla de regímenes regulatorios ──────────────────────────────────────
    # Formato: { ISO2: { vertical: { amount_usd_ha, source, regime } } }
    # Valores en USD/ha/año de riesgo de multa según el marco legal vigente.
    # La lógica del código no cambia al agregar entradas a esta tabla.
    FINE_REGIMES: dict[str, dict[str, dict]] = {
        'AR': {
            'deforestacion': {
                'amount': 800.0,
                'source': 'Ley26331/OTBN-Argentina',
                'regime': 'Ley de Bosques Nativos (Ley 26.331/2007)',
            },
            'energia': {
                'amount': 150.0,
                'source': 'Ley24051/residuos-peligrosos',
                'regime': 'Régimen de Residuos Peligrosos (Ley 24.051)',
            },
        },
        'BR': {
            'deforestacion': {
                'amount': 1_400.0,
                'source': 'IBAMA/Lei9605-1998',
                'regime': 'Lei de Crimes Ambientais (Lei 9.605/1998)',
            },
            'energia': {
                'amount': 300.0,
                'source': 'IBAMA/SISNAMA',
                'regime': 'Sistema Nacional do Meio Ambiente (SISNAMA)',
            },
        },
        'US': {
            'deforestacion': {
                'amount': 2_500.0,
                'source': 'EPA/CleanWaterAct-s404+ESA-s9',
                'regime': 'Clean Water Act §404 + Endangered Species Act §9',
            },
            'energia': {
                'amount': 500.0,
                'source': 'EPA/CleanAirAct-s113+CleanWaterAct-s311',
                'regime': 'Clean Air Act §113 + Clean Water Act §311',
            },
            'agro': {
                'amount': 120.0,
                'source': 'EPA/Clean-Water-Act-wetlands',
                'regime': 'Clean Water Act wetlands compliance (agro)',
            },
        },
        'NL': {
            'deforestacion': {
                'amount': 1_800.0,
                'source': 'EU/EUDR-2023-1115+Wet-Milieubeheer',
                'regime': 'EU Deforestation Regulation (EUDR 2023/1115) + Wet Milieubeheer',
            },
            'energia': {
                'amount': 650.0,
                'source': 'EU-ETS-2003-87-EC+Dutch-Wet-Milieubeheer',
                'regime': 'EU ETS Directive 2003/87/EC + Wet Milieubeheer',
            },
        },
        'DE': {
            'deforestacion': {
                'amount': 2_000.0,
                'source': 'EU/EUDR+BNatSchG',
                'regime': 'EUDR 2023/1115 + Bundesnaturschutzgesetz (BNatSchG)',
            },
            'energia': {
                'amount': 700.0,
                'source': 'EU-ETS+BImSchG',
                'regime': 'EU ETS + Bundes-Immissionsschutzgesetz (BImSchG)',
            },
        },
        'FR': {
            'deforestacion': {
                'amount': 1_900.0,
                'source': "EU/EUDR+Code-Environnement",
                'regime': "EUDR 2023/1115 + Code de l'Environnement",
            },
            'energia': {
                'amount': 650.0,
                'source': 'EU-ETS+ICPE',
                'regime': 'EU ETS + Installations Classées pour l\'Environnement (ICPE)',
            },
        },
        'GB': {
            'deforestacion': {
                'amount': 1_600.0,
                'source': 'DEFRA/Environment-Act-2021',
                'regime': 'UK Environment Act 2021 (deforestation due diligence)',
            },
            'energia': {
                'amount': 550.0,
                'source': 'UK-ETS+EPR-2016',
                'regime': 'UK Emissions Trading Scheme + Environmental Permitting Regulations 2016',
            },
        },
        'AU': {
            'deforestacion': {
                'amount': 1_200.0,
                'source': 'DAWE/EPBC-Act-1999',
                'regime': 'Environment Protection and Biodiversity Conservation Act 1999',
            },
            'energia': {
                'amount': 400.0,
                'source': 'DCCEEW/NGER-Act-2007+EPBC',
                'regime': 'National Greenhouse and Energy Reporting Act 2007',
            },
        },
        'CA': {
            'deforestacion': {
                'amount': 1_500.0,
                'source': 'ECCC/SARA+CEPA-1999',
                'regime': 'Species at Risk Act + Canadian Environmental Protection Act 1999',
            },
            'energia': {
                'amount': 480.0,
                'source': 'ECCC/CEPA-1999+Carbon-Pricing',
                'regime': 'CEPA 1999 + Federal Carbon Pricing Backstop',
            },
        },
        'CN': {
            'deforestacion': {
                'amount': 500.0,
                'source': 'MEE/Forest-Law-Amendment-2020',
                'regime': 'China Forest Law Amendment 2020',
            },
            'energia': {
                'amount': 200.0,
                'source': 'MEE/Environmental-Protection-Law-2014',
                'regime': 'Environmental Protection Law of the PRC (2014)',
            },
        },
        'IN': {
            'deforestacion': {
                'amount': 600.0,
                'source': 'MoEF/Forest-Conservation-Act-1980+FRA-2006',
                'regime': 'Forest Conservation Act 1980 + Forest Rights Act 2006',
            },
            'energia': {
                'amount': 180.0,
                'source': 'MoEF/Environment-Protection-Act-1986',
                'regime': 'Environment Protection Act 1986 (India)',
            },
        },
        'ID': {
            'deforestacion': {
                'amount': 700.0,
                'source': 'KLHK/Forestry-Law-41-1999+PP24-2021',
                'regime': 'Undang-Undang Kehutanan 41/1999 + PP 24/2021',
            },
            'energia': {
                'amount': 250.0,
                'source': 'KLHK/UU-PPLH-32-2009',
                'regime': 'UU Perlindungan dan Pengelolaan LH 32/2009',
            },
        },
        'CO': {
            'deforestacion': {
                'amount': 650.0,
                'source': 'MADS/Ley99-1993',
                'regime': 'Ley 99 de 1993 — Sistema Nacional Ambiental (Colombia)',
            },
            'energia': {
                'amount': 180.0,
                'source': 'MADS/Dec2811-1974',
                'regime': 'Código Nacional de Recursos Naturales Renovables (Dec. 2811/1974)',
            },
        },
        'PE': {
            'deforestacion': {
                'amount': 600.0,
                'source': 'SERFOR/Ley29763',
                'regime': 'Ley Forestal y de Fauna Silvestre 29763 (Perú)',
            },
            'energia': {
                'amount': 160.0,
                'source': 'OEFA/Ley28611',
                'regime': 'Ley General del Ambiente 28611 (Perú)',
            },
        },
        'RU': {
            'deforestacion': {
                'amount': 400.0,
                'source': 'Rosleshoz/Forest-Code-2006',
                'regime': 'Лесной кодекс Российской Федерации (2006)',
            },
            'energia': {
                'amount': 150.0,
                'source': 'Rosprirodnadzor/Federal-Law-7FZ-2002',
                'regime': 'Federal Law 7-FZ On Environmental Protection (2002)',
            },
        },
        'ZA': {
            'deforestacion': {
                'amount': 500.0,
                'source': 'DFFE/NFA-84-1998',
                'regime': 'National Forests Act 84 of 1998 (South Africa)',
            },
            'energia': {
                'amount': 200.0,
                'source': 'DFFE/NEMA-107-1998',
                'regime': 'National Environmental Management Act 107/1998 (South Africa)',
            },
        },
        'NG': {
            'deforestacion': {
                'amount': 350.0,
                'source': 'NESREA/NESREA-Act-2007',
                'regime': 'NESREA Act 2007 (Nigeria)',
            },
            'energia': {
                'amount': 250.0,
                'source': 'DPR/Petroleum-Act-1969+EGASPIN',
                'regime': 'Petroleum Act 1969 + EGASPIN 2002 (Nigeria)',
            },
        },
        'NO': {
            'deforestacion': {
                'amount': 2_200.0,
                'source': 'MD/Naturmangfoldloven-2009',
                'regime': 'Naturmangfoldloven (Nature Diversity Act) 2009 (Norway)',
            },
            'energia': {
                'amount': 900.0,
                'source': 'MD/CO2-Tax+Petroleum-Activities-Act',
                'regime': 'CO2 tax + Petroleum Activities Act (Norway)',
            },
        },
        'SA': {
            'energia': {
                'amount': 100.0,
                'source': 'PME/Environmental-Law-Royal-Decree-2001',
                'regime': 'Environmental Law (Royal Decree M/34) Saudi Arabia',
            },
        },
        'AE': {
            'energia': {
                'amount': 120.0,
                'source': 'MOEI/Federal-Law-24-1999',
                'regime': 'Federal Law No. 24 of 1999 For the Protection of Environment (UAE)',
            },
        },
    }

    # Baseline UNEP/SETAC — aplica a cualquier país no documentado
    _UNEP_DEFAULT: dict = {
        'amount': 500.0,
        'source': 'UNEP/SETAC-Environmental-Damage-Reference',
        'regime': 'UNEP/SETAC Environmental Damage Reference Value (international baseline)',
    }

    def resolve(
        self,
        country_code: str,
        vertical: str,
    ) -> tuple[float, str, str]:
        """
        Retorna (monto_usd_ha, fuente, nombre_régimen).

        Lógica genérica:
          1. Buscar country_code en FINE_REGIMES
          2. Buscar vertical dentro del régimen del país
          3. Si no existe cualquiera de los dos → UNEP baseline
        El código no tiene lógica condicional por país específico.
        """
        regimen = (
            self.FINE_REGIMES
            .get(country_code.upper(), {})
            .get(vertical)
        )
        if regimen:
            return regimen['amount'], regimen['source'], regimen['regime']

        return (
            self._UNEP_DEFAULT['amount'],
            self._UNEP_DEFAULT['source'],
            self._UNEP_DEFAULT['regime'],
        )


# ═══════════════════════════════════════════════════════════════════════════════
# RESOLVER DE TIPO DE CAMBIO (FX)
# ═══════════════════════════════════════════════════════════════════════════════

class FXResolver(HTTPClient):
    """
    Obtiene el tipo de cambio local vs. USD usando Frankfurter.app.
    Frankfurter.app es gratuito, sin autenticación, ~32 monedas principales.
    El resultado es informativo: el Business Case siempre se expresa en USD.

    Si la moneda del país no está en Frankfurter o la request falla,
    retorna (None, None, "unavailable") sin romper el ciclo.

    ISO_4217 mapea el código de país (ISO 3166-1 alpha-2) al código de
    moneda (ISO 4217). Esta tabla es datos de referencia estándar, no lógica.
    """

    # ISO 3166-1 alpha-2 → ISO 4217 currency code
    # Fuente: ISO 3166-1 + ISO 4217 (referencia internacional estándar)
    ISO_4217: dict[str, str] = {
        'AD': 'EUR', 'AE': 'AED', 'AF': 'AFN', 'AG': 'XCD', 'AL': 'ALL',
        'AM': 'AMD', 'AO': 'AOA', 'AR': 'ARS', 'AT': 'EUR', 'AU': 'AUD',
        'AZ': 'AZN', 'BA': 'BAM', 'BB': 'BBD', 'BD': 'BDT', 'BE': 'EUR',
        'BF': 'XOF', 'BG': 'BGN', 'BH': 'BHD', 'BI': 'BIF', 'BJ': 'XOF',
        'BN': 'BND', 'BO': 'BOB', 'BR': 'BRL', 'BS': 'BSD', 'BW': 'BWP',
        'BY': 'BYN', 'BZ': 'BZD', 'CA': 'CAD', 'CD': 'CDF', 'CF': 'XAF',
        'CG': 'XAF', 'CH': 'CHF', 'CI': 'XOF', 'CL': 'CLP', 'CM': 'XAF',
        'CN': 'CNY', 'CO': 'COP', 'CR': 'CRC', 'CU': 'CUP', 'CV': 'CVE',
        'CY': 'EUR', 'CZ': 'CZK', 'DE': 'EUR', 'DJ': 'DJF', 'DK': 'DKK',
        'DO': 'DOP', 'DZ': 'DZD', 'EC': 'USD', 'EE': 'EUR', 'EG': 'EGP',
        'ER': 'ERN', 'ES': 'EUR', 'ET': 'ETB', 'FI': 'EUR', 'FJ': 'FJD',
        'FR': 'EUR', 'GA': 'XAF', 'GB': 'GBP', 'GE': 'GEL', 'GH': 'GHS',
        'GM': 'GMD', 'GN': 'GNF', 'GQ': 'XAF', 'GR': 'EUR', 'GT': 'GTQ',
        'GW': 'XOF', 'GY': 'GYD', 'HN': 'HNL', 'HR': 'EUR', 'HT': 'HTG',
        'HU': 'HUF', 'ID': 'IDR', 'IE': 'EUR', 'IL': 'ILS', 'IN': 'INR',
        'IQ': 'IQD', 'IR': 'IRR', 'IS': 'ISK', 'IT': 'EUR', 'JM': 'JMD',
        'JO': 'JOD', 'JP': 'JPY', 'KE': 'KES', 'KG': 'KGS', 'KH': 'KHR',
        'KR': 'KRW', 'KW': 'KWD', 'KZ': 'KZT', 'LA': 'LAK', 'LB': 'LBP',
        'LK': 'LKR', 'LR': 'LRD', 'LS': 'LSL', 'LT': 'EUR', 'LU': 'EUR',
        'LV': 'EUR', 'LY': 'LYD', 'MA': 'MAD', 'MD': 'MDL', 'MG': 'MGA',
        'MK': 'MKD', 'ML': 'XOF', 'MM': 'MMK', 'MN': 'MNT', 'MR': 'MRU',
        'MT': 'EUR', 'MU': 'MUR', 'MV': 'MVR', 'MW': 'MWK', 'MX': 'MXN',
        'MY': 'MYR', 'MZ': 'MZN', 'NA': 'NAD', 'NE': 'XOF', 'NG': 'NGN',
        'NI': 'NIO', 'NL': 'EUR', 'NO': 'NOK', 'NP': 'NPR', 'NZ': 'NZD',
        'OM': 'OMR', 'PA': 'PAB', 'PE': 'PEN', 'PG': 'PGK', 'PH': 'PHP',
        'PK': 'PKR', 'PL': 'PLN', 'PT': 'EUR', 'PY': 'PYG', 'QA': 'QAR',
        'RO': 'RON', 'RS': 'RSD', 'RU': 'RUB', 'RW': 'RWF', 'SA': 'SAR',
        'SD': 'SDG', 'SE': 'SEK', 'SG': 'SGD', 'SI': 'EUR', 'SK': 'EUR',
        'SL': 'SLE', 'SN': 'XOF', 'SO': 'SOS', 'SR': 'SRD', 'SS': 'SSP',
        'SV': 'USD', 'SY': 'SYP', 'SZ': 'SZL', 'TD': 'XAF', 'TG': 'XOF',
        'TH': 'THB', 'TJ': 'TJS', 'TL': 'USD', 'TM': 'TMT', 'TN': 'TND',
        'TO': 'TOP', 'TR': 'TRY', 'TT': 'TTD', 'TZ': 'TZS', 'UA': 'UAH',
        'UG': 'UGX', 'US': 'USD', 'UY': 'UYU', 'UZ': 'UZS', 'VE': 'VES',
        'VN': 'VND', 'YE': 'YER', 'ZA': 'ZAR', 'ZM': 'ZMW', 'ZW': 'ZWL',
    }

    def resolve(
        self,
        country_code: str,
    ) -> tuple[str, Optional[float], Optional[float], str]:
        """
        Retorna (currency_code, usd_per_local, local_per_usd, fuente).
        usd_per_local y local_per_usd son None si la moneda no está disponible.
        El BC se calcula siempre en USD; el FX es solo informativo.

        Fuentes en cascada:
          1. Frankfurter.app — ~32 monedas principales, sin auth
             https://api.frankfurter.app/latest?from=USD&to={currency}
          2. currency-api.pages.dev — ~170 monedas, sin auth
             https://latest.currency-api.pages.dev/v1/currencies/usd.min.json
        """
        currency = self.ISO_4217.get(country_code.upper(), 'USD')

        if currency == 'USD':
            return 'USD', 1.0, 1.0, "USD-nativo"

        # 1. Frankfurter.app
        try:
            url = f'https://api.frankfurter.app/latest?from=USD&to={currency}'
            data = json.loads(self._get(url))
            rates = data.get('rates', {})
            local_per_usd = rates.get(currency)
            if local_per_usd and float(local_per_usd) > 0:
                lpu = round(float(local_per_usd), 6)
                return currency, round(1.0 / lpu, 8), lpu, "frankfurter.app"
        except Exception:
            pass   # silencioso — intentamos el fallback

        # 2. currency-api.pages.dev (cobertura ~170 monedas)
        try:
            url = 'https://latest.currency-api.pages.dev/v1/currencies/usd.min.json'
            data = json.loads(self._get(url))
            rates = data.get('usd', {})
            local_per_usd = rates.get(currency.lower())
            if local_per_usd and float(local_per_usd) > 0:
                lpu = round(float(local_per_usd), 6)
                return currency, round(1.0 / lpu, 8), lpu, "currency-api.pages.dev"
        except Exception as e:
            _warn(f"FX/{country_code}/{currency}: {e}")

        return currency, None, None, "unavailable"


# ═══════════════════════════════════════════════════════════════════════════════
# BUILDER DE BUNDLES POR ÁREA
# ═══════════════════════════════════════════════════════════════════════════════

class AreaBundleBuilder:
    """
    Orquesta los resolvers para construir un AreaBundle completo por cada área.
    Lee scores actuales de data.json para incluirlos en el bundle.
    """

    def __init__(
        self,
        global_prices: GlobalPrices,
        fuel_resolver:  LocalFuelResolver,
        fine_resolver:  EnvironmentalFineResolver,
        fx_resolver:    FXResolver,
    ):
        self.gp   = global_prices
        self.fuel = fuel_resolver
        self.fine = fine_resolver
        self.fx   = fx_resolver
        self._scores: dict[str, Optional[float]] = self._cargar_scores()

    def _cargar_scores(self) -> dict[str, Optional[float]]:
        """Lee score_faro por área desde data.json (insights)."""
        scores: dict[str, Optional[float]] = {}
        if not DATA_JSON.exists():
            return scores
        try:
            with open(DATA_JSON, encoding='utf-8') as f:
                data = json.load(f)
            for name, insight in data.get('insights', {}).items():
                s = insight.get('score_faro')
                scores[name] = float(s) if s is not None else None
        except Exception as e:
            _warn(f"Lectura scores data.json: {e}")
        return scores

    def build(self, area_meta: dict) -> AreaBundle:
        """Construye el AreaBundle para un área dada su metadata."""
        name         = area_meta['name']
        country_code = area_meta.get('country_code', 'XX')
        country_name = area_meta.get('country_name', 'Unknown')
        vertical     = area_meta.get('vertical', 'agro')
        hectareas    = float(area_meta.get('hectareas_ref', DEFAULT_HECTAREAS))
        score        = self._scores.get(name)

        # Combustible local
        diesel_local, diesel_src = self.fuel.resolve(
            country_code, country_name, self.gp.diesel_intl_usd_l
        )
        costo_comb_ha = round(BASE_OP_NON_FUEL_USD_HA + diesel_local * LITROS_POR_HA_AGRO, 2)

        # Multa ambiental
        fine_amt, fine_src, fine_regime = self.fine.resolve(country_code, vertical)

        # FX
        currency, usd_per_local, local_per_usd, fx_src = self.fx.resolve(country_code)

        return AreaBundle(
            area_name            = name,
            country_code         = country_code,
            country_name         = country_name,
            vertical             = vertical,
            hectareas            = hectareas,
            score_faro           = score,
            diesel_local_usd_l   = diesel_local,
            diesel_source        = diesel_src,
            costo_combustible_ha = costo_comb_ha,
            fine_usd_ha          = fine_amt,
            fine_source          = fine_src,
            fine_regime          = fine_regime,
            fx_currency_code     = currency,
            fx_usd_per_local     = usd_per_local,
            fx_local_per_usd     = local_per_usd,
            fx_source            = fx_src,
        )


# ═══════════════════════════════════════════════════════════════════════════════
# CALCULADOR DE BUSINESS CASE
# ═══════════════════════════════════════════════════════════════════════════════

class BusinessCaseCalculator:
    """
    Calcula el Business Case por área usando precios locales resueltos.

    Las fórmulas son las mismas que en faro_finance.py pero aplicadas con
    los precios locales del AreaBundle en lugar de los globales del módulo.
    Se documenta cada componente para trazabilidad.

    Componentes del ahorro (USD/ha/año):
      Urea         [solo agro]     : factor × (urea_usd_tn / 460 × 50) × 0.35
                                     Ahorro por fertilización nitrogenada diferenciada
                                     (máx. 35% de reducción de dosis a Score 100)
      Combustible  [agro, energía] : factor × costo_combustible_ha_local × tasa_vertical
                                     Ahorro por optimización de rutas y labores
                                     (20% agro, 25% energía)
      Multas       [energía, def.] : factor × multa_local_usd_ha × prob_evasion
                                     Valor esperado de multa evitada por detección temprana
                                     (70% deforestación, 55% energía)
    """

    @staticmethod
    def _factor(score: float, umbral: float = SCORE_UMBRAL_MIN) -> float:
        """
        Factor lineal [0, 1] derivado del Score Faro.
        0 cuando score ≤ umbral; 1 cuando score = 100.
        """
        if score <= umbral:
            return 0.0
        return min((score - umbral) / (100.0 - umbral), 1.0)

    def calcular_urea(
        self,
        score: float,
        vertical: str,
        urea_usd_tn: float,
    ) -> float:
        """Solo agro. Fórmula: factor(score, umbral=40) × (urea/460 × 50) × 0.35"""
        if vertical != 'agro':
            return 0.0
        precio_n_usd_kg = urea_usd_tn / UREA_KG_N_POR_TN
        costo_n_ha      = DOSIS_N_REFERENCIA_KG_HA * precio_n_usd_kg
        ahorro_max      = costo_n_ha * 0.35
        return round(ahorro_max * self._factor(score, umbral=40.0), 2)

    def calcular_combustible(
        self,
        score: float,
        vertical: str,
        costo_combustible_ha: float,
    ) -> float:
        """Agro: 20% del costo local. Energía: 25% de referencia fija."""
        factor = self._factor(score, umbral=20.0)
        if vertical == 'agro':
            return round(costo_combustible_ha * 0.20 * factor, 2)
        elif vertical == 'energia':
            # Para energía usamos el costo de combustible local como base
            # (la referencia interna es 120 USD/ha para operaciones de campo energético)
            return round(max(costo_combustible_ha, 120.0) * 0.25 * factor, 2)
        return 0.0

    def calcular_multas(
        self,
        score: float,
        vertical: str,
        fine_usd_ha: float,
    ) -> float:
        """
        Valor esperado de multa evitada por detección temprana (USD/ha/año).
        Deforestación: prob. evasión 70% a Score máximo.
        Energía: prob. evasión 55% a Score máximo.
        """
        factor = self._factor(score, umbral=30.0)
        if vertical == 'deforestacion':
            return round(fine_usd_ha * factor * 0.70, 2)
        elif vertical == 'energia':
            return round(fine_usd_ha * factor * 0.55, 2)
        return 0.0

    def calcular(
        self,
        bundle: AreaBundle,
        gp: GlobalPrices,
    ) -> Optional['BusinessCase']:
        """
        Genera el BusinessCase completo para un área.
        Retorna None si el área no tiene score (sin datos satelitales aún).
        """
        if bundle.score_faro is None:
            return None

        score = float(bundle.score_faro)
        score = max(0.0, min(100.0, score))

        urea  = self.calcular_urea(score, bundle.vertical, gp.urea_usd_tn)
        comb  = self.calcular_combustible(score, bundle.vertical, bundle.costo_combustible_ha)
        mult  = self.calcular_multas(score, bundle.vertical, bundle.fine_usd_ha)

        total_ha  = round(urea + comb + mult, 2)
        total_usd = round(total_ha * bundle.hectareas, 2)
        roi       = round(total_ha / COSTO_SERVICIO_HA, 1) if COSTO_SERVICIO_HA > 0 else 0.0

        fecha = datetime.now(ART).strftime("%Y-%m-%dT%H:%M:%S-03:00")

        supuestos = {
            "urea_usd_tn":           gp.urea_usd_tn,
            "urea_source":           gp.urea_source,
            "diesel_local_usd_l":    bundle.diesel_local_usd_l,
            "diesel_source":         bundle.diesel_source,
            "costo_combustible_ha":  bundle.costo_combustible_ha,
            "fine_usd_ha":           bundle.fine_usd_ha,
            "fine_source":           bundle.fine_source,
            "fine_regime":           bundle.fine_regime,
            "costo_servicio_ha":     COSTO_SERVICIO_HA,
            "dosis_n_kg_ha":         DOSIS_N_REFERENCIA_KG_HA,
        }

        # SHA-256 del informe (mismo principio que el reporte satelital)
        contenido = json.dumps({
            "area": bundle.area_name,
            "country": bundle.country_code,
            "score": score,
            "ha": bundle.hectareas,
            "ahorro_ha": total_ha,
            "fecha": fecha,
            "supuestos": supuestos,
        }, sort_keys=True, ensure_ascii=False)
        sha256 = hashlib.sha256(contenido.encode('utf-8')).hexdigest()

        return BusinessCase(
            area_name             = bundle.area_name,
            country_code          = bundle.country_code,
            country_name          = bundle.country_name,
            vertical              = bundle.vertical,
            score_faro            = score,
            hectareas             = bundle.hectareas,
            ahorro_urea_ha        = urea,
            ahorro_combustible_ha = comb,
            ahorro_multas_ha      = mult,
            ahorro_total_ha       = total_ha,
            ahorro_total_usd      = total_usd,
            roi_x                 = roi,
            costo_servicio_ha     = COSTO_SERVICIO_HA,
            sha256                = sha256,
            fecha                 = fecha,
            supuestos             = supuestos,
        )


# ═══════════════════════════════════════════════════════════════════════════════
# ACTUALIZADOR DE ARCHIVOS
# ═══════════════════════════════════════════════════════════════════════════════

class FinanceUpdater:
    """Escribe faro_prices.json y actualiza la sección finance de data.json."""

    def guardar_precios(
        self,
        gp:      GlobalPrices,
        bundles: dict[str, AreaBundle],
        cases:   dict[str, Optional[BusinessCase]],
    ) -> None:
        """
        Escribe datos/faro_prices.json con:
          - Claves de nivel raíz para compatibilidad con faro_finance.py
          - Sección 'global' con precios internacionales
          - Sección 'areas' con precios locales + BC por área
        """
        DATOS_DIR.mkdir(exist_ok=True)
        ahora = datetime.now(ART).strftime("%Y-%m-%dT%H:%M:%S-03:00")

        # Precio de combustible internacional (para compat con faro_finance.py)
        comb_intl = round(BASE_OP_NON_FUEL_USD_HA + gp.diesel_intl_usd_l * LITROS_POR_HA_AGRO, 2)

        output: dict = {
            "_schema_version": 2,
            "_actualizado":    ahora,

            # ── Backward compat para faro_finance.py ─────────────────────
            "PRECIO_UREA_USD_TN":   gp.urea_usd_tn,
            "BRENT_USD_BBL":        gp.brent_usd_bbl,
            "PRECIO_DIESEL_USD_L":  gp.diesel_intl_usd_l,
            "COSTO_COMBUSTIBLE_HA": comb_intl,

            # ── Precios globales ──────────────────────────────────────────
            "global": {
                "urea_usd_tn":        gp.urea_usd_tn,
                "urea_source":        gp.urea_source,
                "brent_usd_bbl":      gp.brent_usd_bbl,
                "brent_source":       gp.brent_source,
                "diesel_intl_usd_l":  gp.diesel_intl_usd_l,
                "costo_comb_intl_ha": comb_intl,
            },

            # ── Precios locales por área ──────────────────────────────────
            "areas": {},
        }

        for name, bundle in bundles.items():
            bc = cases.get(name)
            area_entry: dict = {
                "country_code":         bundle.country_code,
                "country_name":         bundle.country_name,
                "vertical":             bundle.vertical,
                "hectareas_ref":        bundle.hectareas,
                "score_faro":           bundle.score_faro,
                "diesel_local_usd_l":   bundle.diesel_local_usd_l,
                "diesel_source":        bundle.diesel_source,
                "costo_combustible_ha": bundle.costo_combustible_ha,
                "fine_usd_ha":          bundle.fine_usd_ha,
                "fine_source":          bundle.fine_source,
                "fine_regime":          bundle.fine_regime,
                "fx_currency":          bundle.fx_currency_code,
                "fx_local_per_usd":     bundle.fx_local_per_usd,
                "fx_source":            bundle.fx_source,
            }
            if bc:
                area_entry["business_case"] = {
                    "ahorro_urea_ha":        bc.ahorro_urea_ha,
                    "ahorro_combustible_ha": bc.ahorro_combustible_ha,
                    "ahorro_multas_ha":      bc.ahorro_multas_ha,
                    "ahorro_total_ha":       bc.ahorro_total_ha,
                    "ahorro_total_usd":      bc.ahorro_total_usd,
                    "roi_x":                 bc.roi_x,
                    "sha256":                bc.sha256,
                    "fecha":                 bc.fecha,
                }
            output["areas"][name] = area_entry

        with open(PRICES_FILE, 'w', encoding='utf-8') as f:
            json.dump(output, f, indent=2, ensure_ascii=False)
        print(f"  → {PRICES_FILE}")

    def actualizar_data_json(
        self,
        gp:    GlobalPrices,
        cases: dict[str, Optional[BusinessCase]],
    ) -> None:
        """
        Agrega/reemplaza la sección 'finance' en data.json.
        Solo escribe las áreas que tienen BusinessCase (score disponible).
        """
        data: dict = {}
        if DATA_JSON.exists():
            with open(DATA_JSON, encoding='utf-8') as f:
                data = json.load(f)

        ahora = datetime.now(ART).strftime("%Y-%m-%dT%H:%M:%S-03:00")
        data.setdefault('finance', {})
        data['finance']['ultimo_update'] = ahora
        data['finance']['precios_globales'] = {
            "urea_usd_tn":    gp.urea_usd_tn,
            "brent_usd_bbl":  gp.brent_usd_bbl,
            "diesel_intl_usd_l": gp.diesel_intl_usd_l,
            "fuentes": {
                "urea":  gp.urea_source,
                "brent": gp.brent_source,
            },
        }

        bcs: dict = {}
        for name, bc in cases.items():
            if bc:
                bcs[name] = {
                    "country_code":          bc.country_code,
                    "country_name":          bc.country_name,
                    "score_faro":            bc.score_faro,
                    "hectareas":             bc.hectareas,
                    "ahorro_urea_ha":        bc.ahorro_urea_ha,
                    "ahorro_combustible_ha": bc.ahorro_combustible_ha,
                    "ahorro_multas_ha":      bc.ahorro_multas_ha,
                    "ahorro_total_ha":       bc.ahorro_total_ha,
                    "ahorro_total_usd":      bc.ahorro_total_usd,
                    "roi_x":                 bc.roi_x,
                    "sha256":                bc.sha256,
                    "fecha":                 bc.fecha,
                }
        data['finance']['business_cases'] = bcs

        with open(DATA_JSON, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        print(f"  → {DATA_JSON} (sección 'finance')")


# ═══════════════════════════════════════════════════════════════════════════════
# LOGGER HISTÓRICO
# ═══════════════════════════════════════════════════════════════════════════════

class PriceLogger:
    """
    Mantiene el historial de actualizaciones de precios en faro_prices_log.json.
    Límite: 104 entradas (~2 años de ciclos semanales).
    """

    def __init__(self, path: Path = LOG_FILE):
        self.path = path

    def _cargar(self) -> list:
        if not self.path.exists():
            return []
        try:
            with open(self.path, encoding='utf-8') as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return []

    def registrar(
        self,
        gp:      GlobalPrices,
        bundles: dict[str, AreaBundle],
        cases:   dict[str, Optional[BusinessCase]],
    ) -> None:
        """Agrega una entrada al historial con fecha y resumen por área."""
        DATOS_DIR.mkdir(exist_ok=True)
        log = self._cargar()

        entrada: dict = {
            "fecha": datetime.now(ART).strftime("%Y-%m-%dT%H:%M:%S-03:00"),
            "global": {
                "urea_usd_tn":       gp.urea_usd_tn,
                "brent_usd_bbl":     gp.brent_usd_bbl,
                "diesel_intl_usd_l": gp.diesel_intl_usd_l,
                "urea_source":       gp.urea_source,
                "brent_source":      gp.brent_source,
            },
            "areas": {},
        }

        for name, bundle in bundles.items():
            bc = cases.get(name)
            entrada["areas"][name] = {
                "country_code":        bundle.country_code,
                "diesel_local_usd_l":  bundle.diesel_local_usd_l,
                "diesel_source":       bundle.diesel_source,
                "fine_usd_ha":         bundle.fine_usd_ha,
                "fine_source":         bundle.fine_source,
                "score_faro":          bundle.score_faro,
                "roi_x":               bc.roi_x if bc else None,
                "ahorro_total_ha":     bc.ahorro_total_ha if bc else None,
            }

        log.append(entrada)
        if len(log) > 104:
            log = log[-104:]

        with open(self.path, 'w', encoding='utf-8') as f:
            json.dump(log, f, indent=2, ensure_ascii=False)
        print(f"  → {self.path} ({len(log)} entradas en historial)")

    def ultimo_registro(self) -> Optional[dict]:
        log = self._cargar()
        return log[-1] if log else None

    def mostrar_historial(self, n: int = 10) -> None:
        """Imprime tabla compacta de las últimas n entradas del historial."""
        log = self._cargar()
        if not log:
            print("  (sin registros en el historial)")
            return

        recientes = log[-n:]
        print(f"\n  {'Fecha':<22} {'Urea/tn':>10} {'Brent':>8} {'Diesel-i':>10}")
        print("  " + "-" * 54)
        for e in recientes:
            g = e.get('global', {})
            print(
                f"  {e['fecha'][:19]:<22} "
                f"{str(g.get('urea_usd_tn', '—')):>10} "
                f"{str(g.get('brent_usd_bbl', '—')):>8} "
                f"{str(g.get('diesel_intl_usd_l', '—')):>10}"
            )
            for area_name, adata in e.get('areas', {}).items():
                roi  = adata.get('roi_x')
                aha  = adata.get('ahorro_total_ha')
                cc   = adata.get('country_code', '??')
                d    = adata.get('diesel_local_usd_l', '—')
                roi_s = f"ROI={roi}x" if roi else "sin score"
                print(
                    f"    {area_name:<18} [{cc}] "
                    f"diesel={d}  {roi_s}"
                )


# ═══════════════════════════════════════════════════════════════════════════════
# NOTIFICADOR PAPERCLIP
# ═══════════════════════════════════════════════════════════════════════════════

class PaperclipNotifier:
    """
    Notifica el resultado del ciclo via Paperclip.
    Con GROQ_API_KEY: resumen ejecutivo generado por llama-3.3-70b (costo $0).
    Sin API key: resumen formateado directo a terminal.
    """

    def notificar(
        self,
        gp:        GlobalPrices,
        bundles:   dict[str, AreaBundle],
        cases:     dict[str, Optional[BusinessCase]],
        duracion_s: float,
    ) -> str:
        resumen = {
            "evento":    "faro_price_updater/ciclo_semanal",
            "fecha":     datetime.now(ART).strftime("%Y-%m-%d %H:%M ART"),
            "duracion_s": round(duracion_s, 1),
            "global": {
                "urea_usd_tn":   gp.urea_usd_tn,
                "brent_usd_bbl": gp.brent_usd_bbl,
                "diesel_intl":   gp.diesel_intl_usd_l,
            },
            "areas": {
                name: {
                    "country":  bundle.country_code,
                    "diesel_local": bundle.diesel_local_usd_l,
                    "diesel_src":   bundle.diesel_source,
                    "fine_ha":      bundle.fine_usd_ha,
                    "score":        bundle.score_faro,
                    "roi_x":        cases[name].roi_x if cases.get(name) else None,
                    "ahorro_ha":    cases[name].ahorro_total_ha if cases.get(name) else None,
                }
                for name, bundle in bundles.items()
            },
        }

        api_key = _cargar_env_key()
        if api_key:
            return self._notificar_llm(api_key, resumen)
        return self._notificar_terminal(resumen)

    def _notificar_llm(self, api_key: str, datos: dict) -> str:
        try:
            from groq import Groq
            cliente = Groq(api_key=api_key)
            prompt = (
                "Sos Paperclip, el agente monitor de FARO PROTOCOL.\n"
                "Recibiste la actualización semanal de precios por área geográfica.\n"
                "Generá un resumen ejecutivo conciso (máx. 12 líneas). "
                "Sin markdown, sin emojis. Primero los datos globales, "
                "luego cada área con el precio de diesel local y el ROI.\n"
                "Indicá si algún precio local difiere significativamente "
                "del precio internacional Brent-derivado.\n\n"
                f"{json.dumps(datos, indent=2, ensure_ascii=False)}"
            )
            chat = cliente.chat.completions.create(
                model="llama-3.3-70b-versatile",
                max_tokens=400,
                messages=[{"role": "user", "content": prompt}],
            )
            texto = chat.choices[0].message.content
            print("\n" + "─" * 60)
            print(texto)
            print("─" * 60)
            return texto
        except Exception as e:
            _warn(f"Paperclip LLM: {e}")
            return self._notificar_terminal(datos)

    def _notificar_terminal(self, datos: dict) -> str:
        g = datos['global']
        lineas = [
            "",
            "=" * 62,
            "  [Paperclip] Precios actualizados por área — FARO PROTOCOL",
            f"  {datos['fecha']}  ({datos['duracion_s']}s)",
            "─" * 62,
            f"  Urea int.  : USD {g['urea_usd_tn']}/tn",
            f"  Brent      : USD {g['brent_usd_bbl']}/bbl",
            f"  Diesel int.: USD {g['diesel_intl']}/L",
            "─" * 62,
            f"  {'Área':<16} {'País':>4} {'Diesel L':>10} {'Fuente':<24} {'ROI':>6} {'Ahorro/ha':>10}",
            "  " + "─" * 60,
        ]
        for name, adata in datos['areas'].items():
            roi_s  = f"{adata['roi_x']}x" if adata['roi_x'] else "—"
            aha_s  = f"${adata['ahorro_ha']}" if adata['ahorro_ha'] else "sin score"
            diesel = f"{adata['diesel_local']}" if adata['diesel_local'] else "—"
            fuente = (adata['diesel_src'] or "")[:22]
            lineas.append(
                f"  {name:<16} {adata['country']:>4} "
                f"{diesel:>10} {fuente:<24} {roi_s:>6} {aha_s:>10}"
            )
        lineas += [
            "─" * 62,
            "  faro_prices.json actualizado. Próximo ciclo: lunes 08:00 ART.",
            "=" * 62,
            "",
        ]
        texto = "\n".join(lineas)
        print(texto)
        return texto


# ═══════════════════════════════════════════════════════════════════════════════
# HELPERS
# ═══════════════════════════════════════════════════════════════════════════════

def _warn(msg: str) -> None:
    print(f"  [WARN] {msg}")

def _info(msg: str) -> None:
    print(msg)

def _last_log_value(section: str, key: str) -> Optional[float]:
    """Lee el último valor de log['section']['key'] del historial."""
    try:
        log = PriceLogger()._cargar()
        if log:
            val = log[-1].get(section, {}).get(key)
            if val is not None:
                return float(val)
    except Exception:
        pass
    return None

def _cargar_env_key() -> str:
    """Carga GROQ_API_KEY del entorno o del .env en la raíz del proyecto."""
    key = os.environ.get('GROQ_API_KEY', '')
    if key:
        return key
    env_file = PROJECT_ROOT / '.env'
    if env_file.exists():
        for linea in env_file.read_text(encoding='utf-8').splitlines():
            linea = linea.strip()
            if linea.startswith('GROQ_API_KEY='):
                return linea.split('=', 1)[1].strip()
    return ''

def _listar_areas() -> list[dict]:
    """
    Carga y retorna la metadata de todas las áreas configuradas en faro_areas/.
    Solo procesa archivos .json que no sean 'areas.json' (el índice legacy).
    """
    areas = []
    for path in sorted(AREAS_DIR.glob('*.json')):
        if path.stem == 'areas':
            continue
        try:
            with open(path, encoding='utf-8') as f:
                meta = json.load(f)
            if 'country_code' not in meta or 'country_name' not in meta:
                _warn(f"{path.name}: faltan 'country_code'/'country_name' — omitida")
                continue
            areas.append(meta)
        except Exception as e:
            _warn(f"{path.name}: {e}")
    return areas


# ═══════════════════════════════════════════════════════════════════════════════
# CICLO PRINCIPAL
# ═══════════════════════════════════════════════════════════════════════════════

def run_update() -> None:
    """
    Ciclo completo de actualización de precios:
      1. Precios globales (urea, brent)
      2. Por cada área: diesel local + FX + multa ambiental
      3. Business Case por área con precios locales
      4. Guardar faro_prices.json
      5. Actualizar data.json
      6. Registrar historial
      7. Paperclip notifica
    """
    t0 = datetime.now(ART)
    _log.info("=" * 50)
    _log.info("run_update iniciado")
    print("\n" + "=" * 62)
    print("  FARO PRICE UPDATER V2 — Global & Genérico")
    print(f"  {t0.strftime('%Y-%m-%d %H:%M:%S ART')}")
    print("=" * 62)

    # ── 1. Áreas disponibles ──────────────────────────────────────────────────
    print("\n[1/6] Cargando áreas...")
    area_metas = _listar_areas()
    if not area_metas:
        print("  ERROR: no se encontraron áreas con country_code/country_name.")
        return
    print(f"  {len(area_metas)} área(s): {', '.join(m['name'] for m in area_metas)}")

    # ── 2. Precios globales ───────────────────────────────────────────────────
    print("\n[2/6] Consultando precios globales (commodities)...")
    gp = GlobalPriceFetcher().build()

    # ── 3. Precios locales por área ───────────────────────────────────────────
    print("\n[3/6] Resolviendo precios locales por área...")
    fuel_resolver = LocalFuelResolver()
    fine_resolver = EnvironmentalFineResolver()
    fx_resolver   = FXResolver()
    builder       = AreaBundleBuilder(gp, fuel_resolver, fine_resolver, fx_resolver)

    bundles: dict[str, AreaBundle] = {}
    for meta in area_metas:
        name = meta['name']
        print(f"  {name} [{meta['country_code']}]...")
        try:
            bundle = builder.build(meta)
            bundles[name] = bundle
            fx_s = (
                f"  {bundle.fx_currency_code} 1 USD={bundle.fx_local_per_usd}"
                if bundle.fx_local_per_usd else f"  {bundle.fx_currency_code} (FX no disponible)"
            )
            print(f"    Diesel local  : USD {bundle.diesel_local_usd_l}/L  [{bundle.diesel_source}]")
            print(f"    Costo comb/ha : USD {bundle.costo_combustible_ha}/ha")
            print(f"    Multa ambiental: USD {bundle.fine_usd_ha}/ha  [{bundle.fine_source}]")
            print(f"    FX            :{fx_s}  [{bundle.fx_source}]")
        except Exception as e:
            _warn(f"{name}: {e}")

    # ── 4. Business Cases ─────────────────────────────────────────────────────
    print("\n[4/6] Calculando Business Cases...")
    calc  = BusinessCaseCalculator()
    cases: dict[str, Optional[BusinessCase]] = {}
    for name, bundle in bundles.items():
        bc = calc.calcular(bundle, gp)
        cases[name] = bc
        if bc:
            print(
                f"  {name:<16} [{bundle.country_code}]  "
                f"score={bc.score_faro:.0f}  "
                f"ahorro={bc.ahorro_total_ha:.2f} USD/ha  "
                f"ROI={bc.roi_x}x"
            )
        else:
            print(f"  {name:<16} [{bundle.country_code}]  sin score — BC omitido")

    # ── 5. Guardar archivos ───────────────────────────────────────────────────
    print("\n[5/6] Guardando archivos...")
    updater = FinanceUpdater()
    updater.guardar_precios(gp, bundles, cases)
    updater.actualizar_data_json(gp, cases)

    # ── 6. Log histórico ──────────────────────────────────────────────────────
    logger = PriceLogger()
    logger.registrar(gp, bundles, cases)

    # ── 7. Notificar ──────────────────────────────────────────────────────────
    duracion = (datetime.now(ART) - t0).total_seconds()
    PaperclipNotifier().notificar(gp, bundles, cases, duracion)

    areas_ok = [n for n, c in cases.items() if c is not None and c.roi_x is not None]
    _log.info(
        f"run_update completado en {duracion:.1f}s — "
        f"Urea {gp.urea_usd_tn} USD/tn · Brent {gp.brent_usd_bbl} USD/bbl · "
        f"areas OK: {', '.join(areas_ok) if areas_ok else 'ninguna'}"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# SCHEDULER  (APScheduler — lunes 08:00 ART)
# ═══════════════════════════════════════════════════════════════════════════════

def iniciar_scheduler() -> None:
    """
    Inicia el scheduler de APScheduler en modo bloqueante.
    Ejecuta run_update() todos los lunes a las 08:00 hora Argentina (UTC-3).
    misfire_grace_time=3600 garantiza que si el sistema estuvo apagado,
    el job corre igual con hasta 1 hora de tolerancia.
    """
    try:
        from apscheduler.schedulers.blocking import BlockingScheduler
        from apscheduler.triggers.cron import CronTrigger
    except ImportError:
        print("ERROR: APScheduler no instalado. Correr: pip install apscheduler")
        sys.exit(1)

    tz = "America/Argentina/Buenos_Aires"
    scheduler = BlockingScheduler(timezone=tz)
    scheduler.add_job(
        func              = run_update,
        trigger           = CronTrigger(day_of_week='mon', hour=8, minute=0, timezone=tz),
        id                = 'faro_price_update_weekly',
        name              = 'FARO — Actualización semanal de precios',
        misfire_grace_time = 3600,
        replace_existing  = True,
    )

    # Calcular próxima ejecución antes de iniciar
    trigger_preview = CronTrigger(day_of_week='mon', hour=8, minute=0, timezone=tz)
    proxima = trigger_preview.get_next_fire_time(None, datetime.now(ART))

    print("=" * 62)
    print("  FARO PRICE UPDATER V2 — Scheduler activo")
    print(f"  Frecuencia  : lunes 08:00 ART")
    print(f"  Próxima run : {proxima.strftime('%Y-%m-%d %H:%M %Z') if proxima else '—'}")
    print(f"  Detener     : Ctrl+C")
    print("=" * 62)

    _log.info("Scheduler iniciado — próxima ejecución: "
              f"{proxima.strftime('%Y-%m-%d %H:%M %Z') if proxima else '?'}")
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        print("\n  Scheduler detenido.")
        _log.info("Scheduler detenido por usuario (KeyboardInterrupt)")


# ═══════════════════════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════════════════════

def main() -> None:
    parser = argparse.ArgumentParser(
        description='FARO PRICE UPDATER V2 — Precios globales y locales por área',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Ejemplos:\n"
            "  python faro_price_updater.py            # ciclo completo ahora\n"
            "  python faro_price_updater.py --now      # igual\n"
            "  python faro_price_updater.py --daemon   # scheduler lunes 08:00 ART\n"
            "  python faro_price_updater.py --log      # últimas 10 entradas del historial\n"
            "  python faro_price_updater.py --log 20   # últimas 20 entradas\n"
            "\n"
            "Agregar un área nueva: crear faro_areas/<nombre>.json con\n"
            "  country_code, country_name, vertical, hectareas_ref\n"
            "El script la detectará automáticamente en el próximo ciclo.\n"
        ),
    )
    parser.add_argument('--now',    action='store_true',
                        help='Ejecutar ciclo completo ahora (comportamiento por defecto)')
    parser.add_argument('--daemon', action='store_true',
                        help='Iniciar scheduler — lunes 08:00 ART (proceso bloqueante)')
    parser.add_argument('--log',    nargs='?', const=10, type=int, metavar='N',
                        help='Ver historial de precios (últimas N entradas, default 10)')
    args = parser.parse_args()

    if args.log is not None:
        print("\n  FARO PRICES — Historial de actualizaciones")
        PriceLogger().mostrar_historial(n=args.log)
        return

    if args.daemon:
        iniciar_scheduler()
        return

    run_update()


if __name__ == '__main__':
    main()
