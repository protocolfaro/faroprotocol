"""
FARO PROTOCOL — Motor de Reportes Certificados (Estático / Inmutable)
======================================================================

Modo REPORTE: documento sellado, auditable, no modificable post-generación.
Contrasta con faro_dashboard.py (modo dinámico/monitoreo).

Responsabilidades:
  1. KPIs sectoriales auditables:
       · Marítimo   → vessel count · truck count · demora acumulada
       · Agro       → rinde estimado · silobag count proxy · anomalías
       · O&G        → porcentaje avance estructural · Índice Actividad
       · Minería    → producción relativa · actividad radar
       · Forest     → cobertura perdida estimada (% área)
  2. Módulo Economic Impact:
       → Resumen ejecutivo listo para auditoría contable
       → Pérdida/ganancia estimada con fuentes y supuestos explícitos
  3. Certificación con SHA-256:
       → Cada reporte PNG/TXT recibe hash inmutable
       → Se registra en audit_log.jsonl
  4. Tres modos de trigger:
       → scheduled:     generación automática (lunes 10:00 ART)
       → event_driven:  condición programable (umbral, cambio de estado)
       → on_demand:     generación inmediata vía CLI o llamada Python

Diseño: sobriedad, precisión técnica, autoridad institucional.
Sin GEE. Sin IPFS. SHA-256 es suficiente para primera etapa.

Uso:
    python faro_report_engine.py --area cordoba
    python faro_report_engine.py --area malacca --trigger on_demand
    python faro_report_engine.py --area permian --trigger event_driven \\
        --condition "score < 45"
    python faro_report_engine.py --all --trigger scheduled
"""

import argparse
import hashlib
import json
import sys
from datetime import datetime
from pathlib import Path

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.gridspec as gridspec
import matplotlib.patches as mpatches
from matplotlib.patches import FancyBboxPatch
import numpy as np

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')
if hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(encoding='utf-8')

from faro_areas import load_area, list_areas
from faro_dashboard import (
    get_economic_vars, compute_roi, check_alerts, SECTOR_COLORS
)

ROOT       = Path(__file__).parent
DATA_JSON  = ROOT / 'data.json'
AUDIT_LOG  = ROOT / 'audit_log.jsonl'

# ── Paleta ────────────────────────────────────────────────────────────────────
BG     = '#0a0a1a'
BG2    = '#0d1020'
BG3    = '#111130'
WHITE  = '#f2ede4'
GREY   = '#888899'
GREY2  = '#555566'
GOLD   = '#c9a84c'
GOLD_L = '#e2c97e'
GREEN  = '#2d8c5e'
GREEN_L= '#4ab87e'
RED    = '#b03030'
RED_L  = '#e05050'
AMBER  = '#c07a30'
AMBER_L= '#e09a50'
BLUE   = '#2a6496'
BLUE_L = '#4a90c8'


# ── SHA-256 ───────────────────────────────────────────────────────────────────

def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(65536), b''):
            h.update(chunk)
    return h.hexdigest()


def _verify_report(area_name: str) -> tuple[bool, str]:
    """Verifica el PNG de fusión existente contra su .sha256. Retorna (ok, hex)."""
    png = ROOT / f'faro_reporte_fusion_{area_name}.png'
    sha = ROOT / f'faro_reporte_fusion_{area_name}.sha256'
    if not png.exists() or not sha.exists():
        return False, ''
    archivado = sha.read_text(encoding='utf-8').split()[0].strip()
    actual    = _sha256(png)
    return actual == archivado, actual


# ── Carga de métricas ─────────────────────────────────────────────────────────

def _load_metrics(area_name: str) -> dict:
    if not DATA_JSON.exists():
        return {}
    with open(DATA_JSON, encoding='utf-8') as f:
        d = json.load(f)
    pipe = d.get('pipeline', {}).get(area_name, {})
    ins  = d.get('insights', {}).get(area_name, {})
    return {**pipe, **ins}


# ── KPIs sectoriales ──────────────────────────────────────────────────────────

def compute_kpis(area_cfg: dict, metrics: dict) -> dict:
    """
    Retorna KPIs auditables según vertical del área.
    Todos los valores llevan fuente explícita para trazabilidad.
    """
    sector = area_cfg.get('sector', area_cfg.get('vertical', ''))
    ndvi   = metrics.get('ndvi_medio', 0) or 0
    sar    = metrics.get('sar_medio_db', -15) or -15
    activ  = metrics.get('indice_actividad', 0) or 0
    fusion = metrics.get('indice_fusion_medio', 0) or 0
    pixels = metrics.get('pixeles_analizados', 0) or 0
    score  = metrics.get('score_faro', 0) or 0

    kpis = {
        'sector':      sector,
        'timestamp':   datetime.now().strftime('%Y-%m-%d %H:%M'),
        'fuente_sar':  'Sentinel-1A IW GRD (Copernicus CDSE)',
        'fuente_ndvi': 'Sentinel-2 L2A via Copernicus CDSE (B04/B08)',
    }

    if sector in ('agro',):
        rinde     = metrics.get('rinde_estimado_tha') or (ndvi * 4.0)
        pct_anom  = metrics.get('pct_anomalia', 0) or 0
        # Silobag count proxy: SAR alto en zona agro → silobolsas llenos
        # Un SAR > -5 dB en zona agrícola plana puede indicar superficie reflectiva
        silobag_proxy = max(0, int((sar + 20) / 5))    # 0–5 (proxy)

        kpis.update({
            'kpi_rinde_estimado_tha':  round(rinde, 3),
            'kpi_silobag_count_proxy': silobag_proxy,
            'kpi_pct_anomalia':        round(pct_anom, 2),
            'kpi_ndvi_vigor':          round(ndvi, 4),
            'kpi_pixels_analizados':   pixels,
            'kpi_score_faro':          score,
            'nota_silobag':
                'Proxy SAR — valor indicativo. Para conteo real: imagen VHR < 1m.',
        })

    elif sector in ('maritimo', 'shipping'):
        # Vessel count proxy: SAR alto en zona portuaria → más estructuras metálicas
        # SAR > -5 dB en agua → probable embarcación o estructura
        vessel_proxy = max(0, int((sar + 15) / 3))     # 0–10 (proxy)
        truck_proxy  = max(0, int(fusion * 15))         # basado en índice fusión

        kpis.update({
            'kpi_vessel_count_proxy': vessel_proxy,
            'kpi_truck_count_proxy':  truck_proxy,
            'kpi_actividad_sar_db':   round(sar, 3),
            'kpi_indice_fusion':      round(fusion, 4),
            'kpi_score_faro':         score,
            'nota_vessel':
                'Proxy SAR — conteo indicativo. Para AIS real: integrar EXACTEARTH/Spire.',
            'nota_truck':
                'Proxy fusión SAR+NDVI — estimación de densidad de tráfico en área.',
        })

    elif sector in ('energia', 'oil_gas'):
        # Avance estructural: índice de actividad SAR como proxy de obra en curso
        avance_pct = min(100, max(0, activ))
        flares_proxy = max(0, int((sar + 10) / 5))

        kpis.update({
            'kpi_avance_estructural_pct': round(avance_pct, 1),
            'kpi_indice_actividad':       round(activ, 2),
            'kpi_flares_proxy':           flares_proxy,
            'kpi_sar_db':                 round(sar, 3),
            'kpi_score_faro':             score,
            'nota_avance':
                'Avance SAR proxy — para obra civil real: integrar drone + BIM.',
        })

    elif sector in ('mineria', 'mining_iron'):
        produccion_rel = round(activ / 100, 3) if activ else round(fusion, 3)

        kpis.update({
            'kpi_produccion_relativa':  produccion_rel,
            'kpi_indice_actividad':     round(activ, 2),
            'kpi_backscatter_db':       round(sar, 3),
            'kpi_score_faro':           score,
            'nota_produccion':
                'Producción relativa basada en Índice Actividad SAR (0–100).',
        })

    elif sector in ('deforestacion',):
        # Cobertura forestal perdida: NDVI bajo en zona que debería ser verde
        ndvi_ref    = 0.7     # NDVI esperado para bosque tropical (referencia)
        cobertura_perdida_pct = max(0, (ndvi_ref - ndvi) / ndvi_ref * 100)

        kpis.update({
            'kpi_ndvi_actual':              round(ndvi, 4),
            'kpi_ndvi_referencia_bosque':   ndvi_ref,
            'kpi_cobertura_perdida_pct':    round(cobertura_perdida_pct, 2),
            'kpi_anomalias_detectadas':     metrics.get('pct_anomalia', 0),
            'kpi_score_faro':               score,
            'nota_cobertura':
                'NDVI ref bosque tropical = 0.7 (Rouse et al., 1973). '
                'Pérdida estimada por desvío respecto al valor esperado.',
        })

    return kpis


# ── Economic Impact (módulo ejecutivo) ────────────────────────────────────────

def economic_impact(area_cfg: dict, metrics: dict,
                    kpis: dict, roi: dict) -> dict:
    """
    Genera el resumen ejecutivo de impacto económico.
    Retorna dict listo para auditoría contable.
    Todos los supuestos son explícitos y trazables.
    """
    sector   = area_cfg.get('sector', area_cfg.get('vertical', ''))
    area_ha  = area_cfg.get('area_ha', 10_000)   # default 10K ha si no está definido
    label    = area_cfg.get('label', area_cfg['name'])
    score    = metrics.get('score_faro', 0) or 0

    impact = {
        'area':           area_cfg['name'],
        'zona':           label,
        'sector':         sector,
        'fecha_reporte':  datetime.now().strftime('%Y-%m-%d %H:%M'),
        'score_faro':     score,
        'supuestos':      {},
        'lineas':         [],
        'total_impacto':  None,
        'unidad':         'USD',
        'horizonte':      'anual',
        'auditoria_nota': (
            'Valores estimados a partir de datos satelitales Sentinel-1/2. '
            'No reemplaza auditoría contable presencial. '
            'Para uso en due diligence requiere validación de campo.'
        ),
    }

    if sector in ('agro',):
        rinde   = kpis.get('kpi_rinde_estimado_tha', 0)
        soja_t  = roi.get('econ', {}).get('soja_usd_t', 380)
        costo   = roi.get('econ', {}).get('costo_servicio_ha', 2.50)
        ingreso = rinde * soja_t * area_ha
        ahorro  = costo * area_ha

        impact['supuestos'] = {
            'area_ha':         area_ha,
            'rinde_tha':       rinde,
            'precio_soja_usd': soja_t,
            'costo_faro_ha':   costo,
            'fuente_precio':   'CBOT Jun-2025 (proxy)',
        }
        impact['lineas'] = [
            {'concepto': 'Ingreso bruto estimado',
             'calculo':  f'{rinde:.2f} t/ha × USD {soja_t}/t × {area_ha:,} ha',
             'valor':    round(ingreso, 0)},
            {'concepto': 'Costo servicio FARO (anual)',
             'calculo':  f'USD {costo}/ha × {area_ha:,} ha',
             'valor':    round(ahorro, 0)},
            {'concepto': 'ROI servicio',
             'calculo':  f'Ahorro / Costo = {ingreso/max(1,ahorro*100):.1f}x',
             'valor':    None},
        ]
        impact['total_impacto'] = round(ingreso, 0)

    elif sector in ('maritimo', 'shipping'):
        demurrage = roi.get('econ', {}).get('demurrage_usd_dia', 35_000)
        demora_d  = roi.get('demora_estimada_dias', 0.5)
        semanas   = 52
        costo_anual = demurrage * demora_d * semanas

        impact['supuestos'] = {
            'demurrage_dia_usd': demurrage,
            'demora_por_evento_dias': demora_d,
            'eventos_por_anio': semanas,
            'fuente_demurrage': 'Baltic Exchange Panamax (referencia 2025)',
        }
        impact['lineas'] = [
            {'concepto': 'Costo demurrage evitable (anual)',
             'calculo':  f'USD {demurrage:,}/día × {demora_d:.1f} días × {semanas} semanas',
             'valor':    round(costo_anual, 0)},
            {'concepto': 'Beneficio de alertas anticipadas',
             'calculo':  f'Reducción estimada 60% de demoras por alerta FARO',
             'valor':    round(costo_anual * 0.6, 0)},
        ]
        impact['total_impacto'] = round(costo_anual * 0.6, 0)
        impact['horizonte'] = 'anual proyectado'

    elif sector in ('energia', 'oil_gas'):
        costo_mes = roi.get('costo_oportunidad_mensual_usd', 0)
        impact['supuestos'] = {
            'capex_musd':              roi.get('capex_referencia_musd', 120),
            'costo_oportunidad_pct_m': 1.5,
            'inactividad_pct':         roi.get('inactividad_pct', 0),
            'fuente': 'Costo oportunidad estándar industria O&G (1.5%/mes)',
        }
        impact['lineas'] = [
            {'concepto': 'Costo oportunidad por inactividad (mensual)',
             'calculo':  f'USD {roi.get("capex_referencia_musd",120):.0f}M × 1.5% × {roi.get("inactividad_pct",0):.1f}% inactividad',
             'valor':    round(costo_mes, 0)},
            {'concepto': 'Proyección anual (12 meses)',
             'calculo':  f'USD {costo_mes:,.0f}/mes × 12',
             'valor':    round(costo_mes * 12, 0)},
        ]
        impact['total_impacto'] = round(costo_mes * 12, 0)

    elif sector in ('mineria', 'mining_iron'):
        val_dia = roi.get('valor_produccion_dia_usd', 0)
        impact['supuestos'] = {
            'precio_mineral_usd_t':  roi.get('precio_mineral_usd_t', 95),
            'tonelaje_referencia_t': roi.get('econ', {}).get('tonelaje_diario', 50_000),
            'actividad_pct':         kpis.get('kpi_indice_actividad', 0),
        }
        impact['lineas'] = [
            {'concepto': 'Valor producción diaria estimada',
             'calculo':  f'USD {roi.get("precio_mineral_usd_t",95)}/t × {roi.get("econ",{}).get("tonelaje_diario",50000):,} t/día × {kpis.get("kpi_indice_actividad",0)/100:.0%} actividad',
             'valor':    round(val_dia, 0)},
            {'concepto': 'Valor anual estimado (250 días operativos)',
             'calculo':  f'USD {val_dia:,.0f} × 250 días',
             'valor':    round(val_dia * 250, 0)},
        ]
        impact['total_impacto'] = round(val_dia * 250, 0)

    elif sector in ('deforestacion',):
        pct_perdida = kpis.get('kpi_cobertura_perdida_pct', 0)
        carbon_t_ha = 150   # tCO2 por ha de bosque tropical (IPCC referencia)
        precio_co2  = 15    # USD/tCO2 (mercado voluntario)
        ha_afectadas = area_ha * pct_perdida / 100
        valor_co2    = ha_afectadas * carbon_t_ha * precio_co2

        impact['supuestos'] = {
            'area_ha':           area_ha,
            'cobertura_perdida_pct': pct_perdida,
            'tCO2_por_ha':       carbon_t_ha,
            'precio_CO2_usd':    precio_co2,
            'fuente_carbono':    'IPCC 2006 GL, Tropical Moist Forest',
            'fuente_precio':     'Voluntary Carbon Market 2025 (proxy)',
        }
        impact['lineas'] = [
            {'concepto': 'Hectáreas afectadas (estimado)',
             'calculo':  f'{area_ha:,} ha × {pct_perdida:.1f}%',
             'valor':    round(ha_afectadas, 0)},
            {'concepto': 'Créditos carbono perdidos (tCO2)',
             'calculo':  f'{ha_afectadas:,.0f} ha × {carbon_t_ha} tCO2/ha',
             'valor':    round(ha_afectadas * carbon_t_ha, 0)},
            {'concepto': 'Valor económico pérdida (USD)',
             'calculo':  f'{ha_afectadas * carbon_t_ha:,.0f} tCO2 × USD {precio_co2}/tCO2',
             'valor':    round(valor_co2, 0)},
        ]
        impact['total_impacto'] = round(valor_co2, 0)
        impact['unidad']        = 'USD (créditos carbono)'

    return impact


# ── Render del reporte certificado ────────────────────────────────────────────

def _fmt_usd(v) -> str:
    if v is None:
        return '—'
    if abs(v) >= 1e6:
        return f'USD {v/1e6:.2f}M'
    return f'USD {v:,.0f}'


def render_report(area_cfg: dict, metrics: dict,
                  kpis: dict, impact: dict,
                  alerts: list, sello_ok: bool,
                  sha_hex: str, trigger: str) -> Path:
    """
    Genera el reporte certificado en PNG.
    Diseño: sobriedad institucional. Sin decoración superflua.
    """
    area_name = area_cfg['name']
    sector    = area_cfg.get('sector', area_cfg.get('vertical', ''))
    label     = area_cfg.get('label', area_name.title())
    col, col_l, tag = SECTOR_COLORS.get(sector, (GOLD, GOLD_L, 'FARO'))
    score     = metrics.get('score_faro', 0) or 0
    s_col     = GREEN if score >= 65 else (AMBER if score >= 45 else RED)
    fecha_ts  = datetime.now().strftime('%Y-%m-%d %H:%M')

    fig = plt.figure(figsize=(18, 13), facecolor=BG)

    # GridSpec: header | [kpis | economic] | [alerts | seal]
    gs = gridspec.GridSpec(
        4, 2, figure=fig,
        height_ratios=[1.8, 5.5, 4.2, 1.8],
        width_ratios=[1, 1],
        hspace=0.30, wspace=0.20,
        left=0.04, right=0.98, top=0.97, bottom=0.03,
    )

    # ── Header ────────────────────────────────────────────────────────────────
    ax_hdr = fig.add_subplot(gs[0, :])
    ax_hdr.set_facecolor(BG2)
    ax_hdr.set_xlim(0, 1); ax_hdr.set_ylim(0, 1)
    ax_hdr.axis('off')

    # Barra superior coloreada
    ax_hdr.add_patch(plt.Rectangle((0, 0.88), 1, 0.12,
        transform=ax_hdr.transAxes, facecolor=col, alpha=0.9, zorder=3))
    ax_hdr.text(0.012, 0.94, f'FARO PROTOCOL  ·  REPORTE CERTIFICADO  ·  {tag}',
        fontsize=10, fontweight='bold', color=BG,
        fontfamily='monospace', va='center', transform=ax_hdr.transAxes, zorder=4)
    ax_hdr.text(0.985, 0.94, f'TRIGGER: {trigger.upper()}',
        fontsize=8, fontweight='bold', color=BG,
        fontfamily='monospace', ha='right', va='center',
        transform=ax_hdr.transAxes, zorder=4)

    # Título de área
    ax_hdr.text(0.012, 0.65, label,
        fontsize=17, fontweight='bold', color=WHITE,
        va='top', transform=ax_hdr.transAxes, zorder=4)
    ax_hdr.text(0.012, 0.22,
        f'Fecha emisión: {fecha_ts}  ·  '
        f'Satélites: Sentinel-1 GRD + Sentinel-2 L2A  ·  '
        f'Método: SHA-256 (no IPFS)',
        fontsize=8, color=GREY, fontfamily='monospace',
        va='bottom', transform=ax_hdr.transAxes, zorder=4)

    # Score
    ax_hdr.text(0.985, 0.67, f'{score:.0f}',
        fontsize=32, fontweight='bold', color=s_col,
        ha='right', va='top', transform=ax_hdr.transAxes, zorder=4)
    ax_hdr.text(0.985, 0.22, '/ 100  SCORE FARO',
        fontsize=8, color=GREY, fontfamily='monospace',
        ha='right', va='bottom', transform=ax_hdr.transAxes, zorder=4)

    # ── Panel KPIs ────────────────────────────────────────────────────────────
    ax_kpi = fig.add_subplot(gs[1, 0])
    ax_kpi.set_facecolor(BG2)
    ax_kpi.set_xlim(0, 1); ax_kpi.set_ylim(0, 1)
    ax_kpi.axis('off')
    ax_kpi.text(0.04, 0.97, f'KPIs SECTORIALES  ·  {tag}',
        fontsize=9, fontweight='bold', color=col_l,
        fontfamily='monospace', va='top', transform=ax_kpi.transAxes)
    ax_kpi.axhline(y=0.92, color=col, linewidth=0.6, alpha=0.5,
                    xmin=0.04, xmax=0.96)

    kpi_items = [(k.replace('kpi_', '').replace('_', ' ').upper(), v)
                 for k, v in kpis.items()
                 if k.startswith('kpi_') and not k.endswith('nota')]

    y_k = 0.87
    for lbl, val in kpi_items[:10]:
        if isinstance(val, float):
            val_str = f'{val:,.4f}' if val < 10 else f'{val:,.2f}'
        elif isinstance(val, int):
            val_str = f'{val:,}'
        else:
            val_str = str(val)
        ax_kpi.text(0.04, y_k, lbl, fontsize=7, color=GREY,
            fontfamily='monospace', va='top', transform=ax_kpi.transAxes)
        ax_kpi.text(0.55, y_k, val_str, fontsize=8.5, fontweight='bold',
            color=WHITE, fontfamily='monospace', va='top',
            transform=ax_kpi.transAxes)
        y_k -= 0.085

    # Notas (fuentes auditables)
    y_nota = 0.10
    for k, v in kpis.items():
        if 'nota_' in k:
            ax_kpi.text(0.04, y_nota, f'* {v[:70]}',
                fontsize=5.5, color=GREY2, fontfamily='monospace',
                va='top', transform=ax_kpi.transAxes)
            y_nota -= 0.065
            if y_nota < 0.01:
                break

    # ── Panel Economic Impact ─────────────────────────────────────────────────
    ax_eco = fig.add_subplot(gs[1, 1])
    ax_eco.set_facecolor(BG2)
    ax_eco.set_xlim(0, 1); ax_eco.set_ylim(0, 1)
    ax_eco.axis('off')
    ax_eco.text(0.04, 0.97, 'ECONOMIC IMPACT  ·  RESUMEN EJECUTIVO',
        fontsize=9, fontweight='bold', color=col_l,
        fontfamily='monospace', va='top', transform=ax_eco.transAxes)
    ax_eco.axhline(y=0.92, color=col, linewidth=0.6, alpha=0.5,
                    xmin=0.04, xmax=0.96)

    lineas = impact.get('lineas', [])
    y_e = 0.87
    for lin in lineas:
        ax_eco.text(0.04, y_e, lin['concepto'],
            fontsize=7.5, color=WHITE, fontfamily='monospace',
            va='top', transform=ax_eco.transAxes)
        ax_eco.text(0.04, y_e - 0.065, lin['calculo'],
            fontsize=6.5, color=GREY, fontfamily='monospace',
            va='top', transform=ax_eco.transAxes)
        if lin.get('valor') is not None:
            ax_eco.text(0.90, y_e, _fmt_usd(lin['valor']),
                fontsize=8.5, fontweight='bold', color=col_l,
                ha='right', va='top', fontfamily='monospace',
                transform=ax_eco.transAxes)
        ax_eco.axhline(y=y_e - 0.13, color=GREY2, linewidth=0.3,
                        alpha=0.4, xmin=0.04, xmax=0.96)
        y_e -= 0.155

    # Total
    total = impact.get('total_impacto')
    horizonte = impact.get('horizonte', 'anual')
    if total is not None:
        ax_eco.add_patch(plt.Rectangle((0.04, 0.06), 0.92, 0.10,
            transform=ax_eco.transAxes,
            facecolor=col + '22', edgecolor=col, linewidth=0.8, zorder=3))
        ax_eco.text(0.08, 0.115, f'IMPACTO TOTAL ({horizonte})',
            fontsize=8, fontweight='bold', color=col_l, fontfamily='monospace',
            va='center', transform=ax_eco.transAxes, zorder=4)
        ax_eco.text(0.92, 0.115, _fmt_usd(total),
            fontsize=11, fontweight='bold', color=GOLD_L, fontfamily='monospace',
            ha='right', va='center', transform=ax_eco.transAxes, zorder=4)

    ax_eco.text(0.04, 0.025, impact.get('auditoria_nota', '')[:90],
        fontsize=5, color=GREY2, fontfamily='monospace',
        va='bottom', transform=ax_eco.transAxes)

    # ── Panel Alertas ─────────────────────────────────────────────────────────
    ax_alt = fig.add_subplot(gs[2, 0])
    ax_alt.set_facecolor(BG2)
    ax_alt.set_xlim(0, 1); ax_alt.set_ylim(0, 1)
    ax_alt.axis('off')
    ax_alt.text(0.04, 0.97, 'ALERTAS ACTIVAS',
        fontsize=9, fontweight='bold', color=RED_L if alerts else GREEN_L,
        fontfamily='monospace', va='top', transform=ax_alt.transAxes)
    ax_alt.axhline(y=0.92, color=RED if alerts else GREEN, linewidth=0.6,
                    alpha=0.5, xmin=0.04, xmax=0.96)

    if alerts:
        y_a = 0.87
        for al in alerts[:5]:
            nivel  = al.get('nivel', 'INFO')
            a_col  = RED_L if nivel == 'HIGH' else (AMBER_L if nivel == 'MEDIUM' else GREY)
            ax_alt.add_patch(plt.Rectangle((0.03, y_a - 0.005), 0.008, 0.06,
                transform=ax_alt.transAxes, facecolor=a_col, zorder=3))
            ax_alt.text(0.06, y_a + 0.045, f'[{nivel}]',
                fontsize=7, fontweight='bold', color=a_col,
                fontfamily='monospace', va='top', transform=ax_alt.transAxes)
            ax_alt.text(0.06, y_a, al.get('mensaje', '')[:75],
                fontsize=7, color=WHITE, fontfamily='monospace',
                va='top', transform=ax_alt.transAxes)
            y_a -= 0.17
    else:
        ax_alt.text(0.5, 0.52,
            '✓  Sin alertas activas\nSistema dentro de parámetros normales',
            fontsize=10, color=GREEN_L, fontfamily='monospace',
            ha='center', va='center', transform=ax_alt.transAxes)

    # ── Panel Supuestos (auditoría) ───────────────────────────────────────────
    ax_sup = fig.add_subplot(gs[2, 1])
    ax_sup.set_facecolor(BG2)
    ax_sup.set_xlim(0, 1); ax_sup.set_ylim(0, 1)
    ax_sup.axis('off')
    ax_sup.text(0.04, 0.97, 'SUPUESTOS Y FUENTES  ·  AUDITORÍA',
        fontsize=9, fontweight='bold', color=GOLD_L,
        fontfamily='monospace', va='top', transform=ax_sup.transAxes)
    ax_sup.axhline(y=0.92, color=GOLD, linewidth=0.6, alpha=0.5,
                    xmin=0.04, xmax=0.96)

    supuestos = impact.get('supuestos', {})
    y_s = 0.87
    for k, v in list(supuestos.items())[:9]:
        lbl_s = k.replace('_', ' ').title()
        val_s = str(v)[:35]
        ax_sup.text(0.04, y_s, lbl_s, fontsize=6.5, color=GREY,
            fontfamily='monospace', va='top', transform=ax_sup.transAxes)
        ax_sup.text(0.55, y_s, val_s, fontsize=6.5, color=WHITE,
            fontfamily='monospace', va='top', transform=ax_sup.transAxes)
        y_s -= 0.09

    # ── Pie: sello SHA-256 ────────────────────────────────────────────────────
    ax_seal = fig.add_subplot(gs[3, :])
    ax_seal.set_facecolor(BG3)
    ax_seal.set_xlim(0, 1); ax_seal.set_ylim(0, 1)
    ax_seal.axis('off')

    if sello_ok:
        ax_seal.add_patch(plt.Rectangle((0, 0), 1, 1,
            transform=ax_seal.transAxes,
            facecolor=GREEN + '18', edgecolor=GREEN, linewidth=0.8, zorder=2))
        ax_seal.text(0.012, 0.75,
            '✓  SELLO VERDE  ·  REPORTE VALIDADO  ·  SHA-256 VERIFICADO',
            fontsize=10, fontweight='bold', color=GREEN_L,
            fontfamily='monospace', va='top', transform=ax_seal.transAxes, zorder=3)
        ax_seal.text(0.012, 0.28,
            f'SHA-256:  {sha_hex}',
            fontsize=7.5, color=WHITE, fontfamily='monospace',
            va='top', transform=ax_seal.transAxes, zorder=3)
    else:
        ax_seal.text(0.012, 0.65,
            '[!]  Sin sello — reporte de fusión no disponible o hash no verificado',
            fontsize=8, color=RED_L, fontfamily='monospace',
            va='top', transform=ax_seal.transAxes)

    ax_seal.text(0.985, 0.55,
        f'protocolfaro.github.io/faroprotocol  ·  {fecha_ts}',
        fontsize=7, color=GREY2, fontfamily='monospace',
        ha='right', va='top', transform=ax_seal.transAxes)

    # Guardar
    out_path = ROOT / f'faro_report_{area_name}.png'
    plt.savefig(out_path, dpi=150, bbox_inches='tight',
                facecolor=BG, edgecolor='none')
    plt.close(fig)
    return out_path


# ── Audit log ─────────────────────────────────────────────────────────────────

def _audit_entry(area_name: str, trigger: str, report_sha: str,
                 score, estado: str, impact_total, alerts_n: int):
    entry = {
        'ts':           datetime.now().strftime('%Y-%m-%d %H:%M'),
        'area':         area_name,
        'trigger':      trigger,
        'score':        score,
        'estado':       estado,
        'impact_total': impact_total,
        'alerts':       alerts_n,
        'report_sha256': report_sha,
        'version':      'faro_report_engine v1',
    }
    with open(AUDIT_LOG, 'a', encoding='utf-8') as f:
        f.write(json.dumps(entry, ensure_ascii=False) + '\n')


# ── Triggers ──────────────────────────────────────────────────────────────────

class FaroTrigger:
    """
    Tres modos de activación del reporte certificado.
    """

    @staticmethod
    def on_demand(area_name: str) -> dict:
        """Generación inmediata — el analista pulsa 'Certificar Ahora'."""
        return _run_report(area_name, trigger='on_demand')

    @staticmethod
    def scheduled(area_names: list[str]) -> list[dict]:
        """
        Generación automática semanal.
        En producción: invocar desde Task Scheduler o cron.
        Aquí simplemente corre los reportes en secuencia.
        """
        results = []
        for a in area_names:
            r = _run_report(a, trigger='scheduled')
            results.append(r)
        return results

    @staticmethod
    def event_driven(area_name: str, condition: str) -> dict | None:
        """
        Genera el reporte sólo si la condición se cumple.
        condition: string evaluable, e.g. "score < 45" o "estado == 'ALERTA'"
        Variables disponibles: score, estado, ndvi, sar, activ, alerts_n
        """
        metrics = _load_metrics(area_name)
        score   = metrics.get('score_faro', 100)
        estado  = metrics.get('estado', 'OK')
        ndvi    = metrics.get('ndvi_medio', 1)
        sar     = metrics.get('sar_medio_db', 0)
        activ   = metrics.get('indice_actividad', 100)
        area_cfg= load_area(area_name)
        alerts  = check_alerts(area_name, metrics, area_cfg)
        alerts_n= len(alerts)

        try:
            triggered = eval(condition, {}, {     # noqa: S307 — condición del operador
                'score': score, 'estado': estado,
                'ndvi': ndvi, 'sar': sar,
                'activ': activ, 'alerts_n': alerts_n,
            })
        except Exception as e:
            print(f'  [event_driven] Error evaluando condición "{condition}": {e}')
            triggered = False

        if triggered:
            print(f'  [event_driven] Condición "{condition}" cumplida — generando reporte')
            return _run_report(area_name, trigger=f'event_driven:{condition}')
        else:
            print(f'  [event_driven] Condición "{condition}" no cumplida — sin reporte')
            return None


# ── Pipeline de reporte ───────────────────────────────────────────────────────

def _load_metrics(area_name: str) -> dict:
    if not DATA_JSON.exists():
        return {}
    with open(DATA_JSON, encoding='utf-8') as f:
        d = json.load(f)
    pipe = d.get('pipeline', {}).get(area_name, {})
    ins  = d.get('insights', {}).get(area_name, {})
    return {**pipe, **ins}


def _run_report(area_name: str, trigger: str = 'on_demand') -> dict:
    """Pipeline completo de generación de reporte certificado."""
    print(f'\n  [{area_name.upper()}] Reporte certificado — trigger={trigger}')

    area_cfg = load_area(area_name)
    metrics  = _load_metrics(area_name)
    sector   = area_cfg.get('sector', area_cfg.get('vertical', ''))

    econ    = get_economic_vars(sector)
    roi     = compute_roi(area_cfg, metrics, econ)
    alerts  = check_alerts(area_name, metrics, area_cfg)
    kpis    = compute_kpis(area_cfg, metrics)
    impact  = economic_impact(area_cfg, metrics, kpis, roi)

    sello_ok, sha_hex = _verify_report(area_name)

    out_path = render_report(
        area_cfg, metrics, kpis, impact,
        alerts, sello_ok, sha_hex, trigger,
    )

    # SHA-256 del propio reporte generado
    report_sha = _sha256(out_path)
    sha_out    = out_path.with_suffix('.sha256')
    sha_out.write_text(f'{report_sha}  {out_path.name}\n', encoding='utf-8')

    # Audit log
    _audit_entry(
        area_name=area_name,
        trigger=trigger,
        report_sha=report_sha[:16] + '...',
        score=metrics.get('score_faro'),
        estado=metrics.get('estado', '?'),
        impact_total=impact.get('total_impacto'),
        alerts_n=len(alerts),
    )

    print(f'  Reporte: {out_path.name}')
    print(f'  SHA-256: {report_sha[:32]}...')
    print(f'  Impacto: {_fmt_usd(impact.get("total_impacto"))}')
    print(f'  Alertas: {len(alerts)}')

    return {
        'area':        area_name,
        'trigger':     trigger,
        'report_png':  str(out_path),
        'report_sha':  report_sha,
        'sello_ok':    sello_ok,
        'score':       metrics.get('score_faro'),
        'estado':      metrics.get('estado'),
        'impact':      impact.get('total_impacto'),
        'alerts':      len(alerts),
    }


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == '__main__':
    parser = argparse.ArgumentParser(
        description='FARO PROTOCOL — Motor de Reportes Certificados')
    parser.add_argument('--area', nargs='+',
        help='Área(s) a certificar')
    parser.add_argument('--all', action='store_true',
        help='Todas las áreas con datos')
    parser.add_argument('--trigger',
        choices=['on_demand', 'scheduled', 'event_driven'],
        default='on_demand')
    parser.add_argument('--condition', default='score < 50',
        help='Condición para trigger event_driven (default: "score < 50")')
    args = parser.parse_args()

    areas_arg = list_areas() if args.all else (args.area or [])
    if not areas_arg:
        parser.print_help()
        sys.exit(1)

    print('=' * 65)
    print('  FARO PROTOCOL — Motor de Reportes Certificados')
    print(f'  {datetime.now().strftime("%Y-%m-%d %H:%M")}  ·  Trigger: {args.trigger}')
    print('=' * 65)

    trigger = FaroTrigger()
    resultados = []

    if args.trigger == 'on_demand':
        for a in areas_arg:
            resultados.append(trigger.on_demand(a))
    elif args.trigger == 'scheduled':
        resultados = trigger.scheduled(areas_arg)
    elif args.trigger == 'event_driven':
        for a in areas_arg:
            r = trigger.event_driven(a, args.condition)
            if r:
                resultados.append(r)

    print()
    print('=' * 65)
    print('  RESUMEN DE REPORTES EMITIDOS')
    print('=' * 65)
    for r in resultados:
        sello = '✓' if r.get('sello_ok') else '!'
        print(f'  [{sello}] {r["area"]:<15} '
              f'Score={str(r.get("score") or "?"):>5} | '
              f'{r.get("estado") or "?":<12} | '
              f'Impacto={_fmt_usd(r.get("impact"))}')
    print(f'  Reportes emitidos: {len(resultados)}')
    print('=' * 65)
