"""
FARO PROTOCOL — Faro Stress Index (FSI)
=========================================

Detecta estres hidrico, estructural y operacional por sector.
Cada vertical tiene su propio modelo calibrado — comparar NDVI
de un desierto petrolero con una pradera agricola es un error
categorico que este modulo evita.

Modelos por sector:
  agro         NDVI + SAR + estado del engine + brecha optico/radar
  deforestacion NDVI relativo a bosque tropical esperado (ref. 0.70)
  maritimo     Actividad SAR en zona portuaria (retorno < -12 dB = baja actividad)
  energia/oil_gas  Indice de actividad + SAR (proxy de actividad industrial)
  mineria      Actividad + variacion de produccion relativa

Integracion Landsat 9 TIRS (futura):
  Con LST disponible se agrega estrés termico confirmado:
    if ndvi > 0.45 and lst_celsius > umbral_termico[cultivo]:
        fsi += 20
  API: USGS EarthExplorer (earthexplorer.usgs.gov) — credenciales separadas.

Score FSI: 0-100
  0-20   Sin estres detectable
  21-40  Estres leve — monitorear
  41-60  Estres moderado — accion preventiva recomendada
  61-80  Estres alto — accion correctiva urgente
  81-100 Estres severo — alerta critica

Uso:
    from faro_stress_index import FaroStressIndex
    r = FaroStressIndex.evaluate('balcarce')
    print(r.score, r.nivel, r.certificacion)

    python faro_stress_index.py                  # todas las areas
    python faro_stress_index.py balcarce permian # areas especificas
"""

from __future__ import annotations
from dataclasses import dataclass, field
from pathlib import Path
import json
import sys

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

ROOT      = Path(__file__).parent
DATA_JSON = ROOT / 'data.json'

# ── Referencia NDVI por sector (cobertura esperada en condicion normal) ───────
NDVI_REF = {
    'agro':           0.55,   # cultivo en pleno vigor (soja/maiz/trigo)
    'deforestacion':  0.70,   # bosque tropical denso (Rouse et al. 1973)
    'maritimo':       0.05,   # agua y zonas portuarias (casi cero es normal)
    'shipping':       0.05,
    'energia':        0.15,   # zona industrial/semideserto
    'oil_gas':        0.15,
    'mineria':        0.10,   # zona de extraccion / terreno descubierto
    'mining_iron':    0.10,
}

# ── SAR esperado por sector (dB, VV polarizacion) ────────────────────────────
# Refs: Ulaby et al. 1986; McNairn & Brisco 2004; ESA Sentinel-1 Handbook
SAR_REF = {
    'agro':           -8.0,   # campo cultivado humedo: -5 a -12 dB
    'deforestacion':  -6.0,   # dosel forestal denso: -4 a -10 dB
    'maritimo':       -10.0,  # agua calma: -15 a -5 dB con buques
    'shipping':       -10.0,
    'energia':        -8.0,   # zona industrial mixta
    'oil_gas':        -8.0,
    'mineria':        -5.0,   # superficie mineral expuesta: alta reflectividad
    'mining_iron':    -5.0,
}


@dataclass
class StressResult:
    area:          str
    sector:        str
    score:         float
    nivel:         str
    certificacion: str
    detalle:       list[str]  = field(default_factory=list)
    ndvi:          float | None = None
    sar_db:        float | None = None
    fusion:        float | None = None
    activ:         float | None = None
    lst_celsius:   float | None = None   # None hasta Landsat 9
    fuente_lst:    str = 'Pendiente — USGS EarthExplorer (Landsat 9 TIRS)'


def _nivel(s: float) -> str:
    if s <= 20: return 'Sin estres'
    if s <= 40: return 'Leve'
    if s <= 60: return 'Moderado'
    if s <= 80: return 'Alto'
    return 'Severo'


def _cert(nivel: str, sector: str) -> str:
    context = {
        'agro':         'hidrico/estructural en cultivo',
        'deforestacion':'cobertura forestal',
        'maritimo':     'actividad logistica portuaria',
        'shipping':     'actividad logistica portuaria',
        'energia':      'actividad operacional O&G',
        'oil_gas':      'actividad operacional O&G',
        'mineria':      'produccion en zona minera',
        'mining_iron':  'produccion en zona minera',
    }.get(sector, 'parametros operacionales')

    if nivel == 'Sin estres':
        return f'SIN ESTRES DETECTABLE — {context.title()} dentro de parametros'
    elif nivel in ('Leve', 'Moderado'):
        return f'ESTRES {nivel.upper()} EN {context.upper()} — Monitoreo preventivo'
    else:
        return f'ESTRES {nivel.upper()} EN {context.upper()} — Accion recomendada'


def _load_metrics(area_name: str) -> dict:
    if not DATA_JSON.exists():
        return {}
    with open(DATA_JSON, encoding='utf-8') as f:
        d = json.load(f)
    return {**d.get('pipeline', {}).get(area_name, {}),
            **d.get('insights', {}).get(area_name, {})}


def _load_sector(area_name: str) -> str:
    cfg = ROOT / 'faro_areas' / f'{area_name}.json'
    if not cfg.exists():
        return 'desconocido'
    with open(cfg, encoding='utf-8') as f:
        d = json.load(f)
    return d.get('sector', d.get('vertical', 'desconocido'))


# ── Modelos de estres por sector ──────────────────────────────────────────────

def _stress_agro(ndvi, sar, fusion, estado, pct_anom) -> tuple[float, list[str]]:
    """Modelo agro: NDVI + SAR + estado del engine."""
    score   = 0.0
    detalle = []
    ref_n   = NDVI_REF['agro']
    ref_s   = SAR_REF['agro']

    # Motor ya certifico ALERTA (rinde muy por debajo del historico)
    if estado == 'ALERTA':
        score += 40
        detalle.append('Engine faro certifico ALERTA — rinde muy por debajo del historico')

    # NDVI por debajo de referencia de cultivo vigoroso
    if ndvi is not None:
        deficit = max(0, ref_n - ndvi) / ref_n   # 0.0 – 1.0
        pts = round(deficit * 30)
        if pts > 5:
            score += pts
            detalle.append(
                f'NDVI {ndvi:.3f} — {deficit*100:.0f}% por debajo del '
                f'valor de referencia agro ({ref_n})'
            )

    # SAR por debajo de referencia (suelo seco, raices debiles)
    if sar is not None and sar < ref_s - 4:
        pts = min(20, (ref_s - 4 - sar) * 2)
        score += pts
        detalle.append(
            f'SAR {sar:.1f} dB — retorno bajo para campo cultivado '
            f'(ref. {ref_s} dB): posible sequia o compactacion'
        )

    # Brecha NDVI vs Fusion (optico dice verde, radar dice otra cosa)
    if ndvi is not None and fusion is not None and ndvi > 0.30:
        expected_fusion = 0.6 * ndvi + 0.15   # estimacion lineal simple
        gap = expected_fusion - fusion
        if gap > 0.12:
            pts = min(25, round(gap * 80))
            score += pts
            detalle.append(
                f'Brecha optico/radar: Fusion {fusion:.3f} '
                f'(esperado ~{expected_fusion:.3f}) — SAR arrastra el indice'
            )

    # Anomalias de vision computacional
    if pct_anom and pct_anom > 5:
        score += min(15, pct_anom)
        detalle.append(f'Anomalias detectadas: {pct_anom:.1f}% del area')

    return min(100, score), detalle


def _stress_forest(ndvi, sar, pct_anom) -> tuple[float, list[str]]:
    """Modelo forestal: NDVI relativo a bosque tropical esperado."""
    score   = 0.0
    detalle = []
    ref_n   = NDVI_REF['deforestacion']

    if ndvi is not None:
        deficit = max(0, ref_n - ndvi) / ref_n
        pts     = round(deficit * 60)
        score  += pts
        if pts > 5:
            detalle.append(
                f'NDVI {ndvi:.3f} — {deficit*100:.0f}% por debajo del '
                f'bosque tropical esperado (ref. {ref_n})'
            )
        else:
            detalle.append(f'Cobertura forestal NDVI {ndvi:.3f} — dentro del rango esperado')

    if pct_anom and pct_anom > 3:
        score += min(20, pct_anom * 2)
        detalle.append(f'Anomalias detectadas por vision: {pct_anom:.1f}% — posibles claros')

    return min(100, score), detalle


def _stress_maritime(ndvi, sar, fusion) -> tuple[float, list[str]]:
    """Modelo maritimo: actividad SAR en zona portuaria."""
    score   = 0.0
    detalle = []
    ref_s   = SAR_REF['maritimo']

    # NDVI negativo o nulo es NORMAL en agua — no penalizar
    # El stress maritimo es en la actividad SAR (buques, estructuras)
    if sar is not None:
        if sar < ref_s - 5:   # actividad muy baja para zona portuaria
            deficit = (ref_s - 5 - sar)
            pts     = min(40, round(deficit * 5))
            score  += pts
            detalle.append(
                f'SAR {sar:.1f} dB — retorno bajo en zona portuaria '
                f'(ref. {ref_s} dB): posible baja actividad o condicion atmosferica'
            )
        elif sar >= -5:        # retorno muy alto: congestion o estructura densa
            score += 10
            detalle.append(
                f'SAR {sar:.1f} dB — retorno alto: posible congestion '
                f'o anclaje prolongado de flota'
            )
        else:
            detalle.append(f'Actividad SAR {sar:.1f} dB — dentro del rango portuario normal')

    if fusion is not None and fusion < 0.35:
        score += 15
        detalle.append(f'Indice Fusion {fusion:.3f} — convergencia SAR+optico baja')

    return min(100, score), detalle


def _stress_energy(ndvi, sar, activ, fusion) -> tuple[float, list[str]]:
    """Modelo energia (O&G / mineria): actividad industrial SAR."""
    score   = 0.0
    detalle = []

    # Indice de actividad es el KPI primario para energia
    if activ is not None:
        if activ < 40:
            pts = round((60 - activ) * 0.8)
            score += min(40, pts)
            detalle.append(
                f'Indice Actividad {activ:.1f}/100 — por debajo del umbral '
                f'operacional (40): posible paralisis o baja produccion'
            )
        elif activ < 60:
            score += 15
            detalle.append(f'Actividad {activ:.1f}/100 — moderada, monitorear tendencia')
        else:
            detalle.append(f'Actividad {activ:.1f}/100 — nivel operacional normal')

    if sar is not None:
        ref_s = SAR_REF.get('energia', -8.0)
        if sar < ref_s - 6:
            score += 20
            detalle.append(
                f'SAR {sar:.1f} dB — retorno bajo para zona industrial '
                f'(ref. {ref_s} dB): reduccion de actividad de superficie'
            )

    if not detalle:
        detalle.append(f'SAR {sar:.1f if sar else "N/A"} dB — sin anomalias detectadas')

    return min(100, score), detalle


# ── Evaluacion principal ──────────────────────────────────────────────────────

class FaroStressIndex:

    @staticmethod
    def evaluate(area_name: str) -> StressResult:
        m      = _load_metrics(area_name)
        sector = _load_sector(area_name)
        ndvi   = m.get('ndvi_medio')
        sar    = m.get('sar_medio_db')
        fusion = m.get('indice_fusion_medio')
        activ  = m.get('indice_actividad')
        estado = m.get('estado', 'SIN_DATOS')
        pct_an = m.get('pct_anomalia', 0) or 0

        if estado == 'SIN_DATOS' or (ndvi is None and sar is None and activ is None):
            return StressResult(
                area=area_name, sector=sector, score=50.0,
                nivel='Moderado',
                certificacion='DATOS INSUFICIENTES — Evaluacion pendiente de datos satelitales',
                detalle=['Sin datos satelitales procesados para esta area'],
                ndvi=ndvi, sar_db=sar, fusion=fusion, activ=activ,
            )

        # Despacho por sector
        if sector in ('agro',):
            score, detalle = _stress_agro(ndvi, sar, fusion, estado, pct_an)
        elif sector in ('deforestacion',):
            score, detalle = _stress_forest(ndvi, sar, pct_an)
        elif sector in ('maritimo', 'shipping'):
            score, detalle = _stress_maritime(ndvi, sar, fusion)
        elif sector in ('energia', 'oil_gas', 'mineria', 'mining_iron'):
            score, detalle = _stress_energy(ndvi, sar, activ, fusion)
        else:
            # Fallback generico
            score   = 0.0
            detalle = [f'NDVI {ndvi} | SAR {sar} dB | Fusion {fusion} — sector sin modelo especifico']

        if not detalle:
            detalle = ['Parametros dentro del rango esperado para el sector']

        nivel = _nivel(score)
        return StressResult(
            area=area_name, sector=sector,
            score=round(score, 1), nivel=nivel,
            certificacion=_cert(nivel, sector),
            detalle=detalle,
            ndvi=ndvi, sar_db=sar, fusion=fusion, activ=activ,
        )

    @staticmethod
    def batch(area_names: list[str]) -> dict[str, StressResult]:
        return {a: FaroStressIndex.evaluate(a) for a in area_names}


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == '__main__':
    from faro_areas import list_areas
    areas = sys.argv[1:] or list_areas()

    print('=' * 65)
    print('  FARO PROTOCOL — Faro Stress Index')
    print('=' * 65)
    for area in areas:
        r = FaroStressIndex.evaluate(area)
        bar = '#' * int(r.score / 5) + '.' * (20 - int(r.score / 5))
        print(f'\n  {area.upper():<15} [{r.sector}]')
        print(f'  FSI {r.score:>5.1f}/100  [{bar}]  {r.nivel}')
        for d in r.detalle:
            print(f'    * {d}')
        print(f'  > {r.certificacion}')
    print()
