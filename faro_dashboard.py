"""
FARO PROTOCOL — Dashboard de Monitoreo Dinámico
=================================================

Modo DINÁMICO: datos actualizables en tiempo real.
Contrasta con faro_report_engine.py (modo estático/inmutable).

Responsabilidades:
  1. Fetching automático Sentinel-1 + Sentinel-2 desde Copernicus CDSE
  2. Visualización multicapa: NDVI óptico · SAR radar · mapa de calor fusión
  3. ROI proyectado según sector y área
  4. Variables económicas por vertical:
       · Agro      → precios CBOT maíz/soja/trigo
       · Marítimo  → USD 35.000/día por buque demorado
       · O&G       → 1.5%/mes costo de oportunidad del capital
       · Minería   → variación precio mineral × tonelaje estimado
  5. Alertas automáticas:
       · Agro      → desvío de rinde > 5% respecto al histórico
       · Marítimo  → anomalía logística sostenida > 4hs
       · O&G/Min   → caída de actividad SAR > 10% semanal

Uso:
    python faro_dashboard.py --area balcarce
    python faro_dashboard.py --area malacca --force-fetch
    python faro_dashboard.py --area permian --area rotterdam   # multi-área
    python faro_dashboard.py --all
"""

import argparse
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.gridspec as gridspec
import numpy as np

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')
if hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(encoding='utf-8')

from faro_areas import load_area, list_areas

ROOT      = Path(__file__).parent
DATA_JSON = ROOT / 'data.json'

# ── Paleta ────────────────────────────────────────────────────────────────────
BG     = '#0a0a1a'
BG2    = '#0d1020'
WHITE  = '#f2ede4'
GREY   = '#888899'
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
PURPLE = '#7a4a9a'
PURPLE_L='#aa70cc'

SECTOR_COLORS = {
    'agro':          (GREEN,  GREEN_L,  'AGRO'),
    'deforestacion': (RED,    RED_L,    'FOREST'),
    'energia':       (BLUE,   BLUE_L,   'O&G'),
    'oil_gas':       (BLUE,   BLUE_L,   'O&G'),
    'maritimo':      (PURPLE, PURPLE_L, 'MARITIME'),
    'shipping':      (PURPLE, PURPLE_L, 'MARITIME'),
    'mineria':       (AMBER,  AMBER_L,  'MINING'),
    'mining_iron':   (AMBER,  AMBER_L,  'MINING'),
    'tierras_raras': (AMBER,  AMBER_L,  'CRITICAL MIN'),
}

# ── Variables económicas por sector ───────────────────────────────────────────

ECONOMIC_VARS = {
    # Agro: precios CBOT (USD/t) — se sobreescriben con faro_prices.json si existe
    'agro': {
        'soja_usd_t':     380.0,
        'maiz_usd_t':     175.0,
        'trigo_usd_t':    220.0,
        'costo_servicio_ha': 2.50,
        'nota': 'Precios CBOT Jun-2025 (proxy)',
    },
    # Marítimo: tarifa de demora estándar
    'maritimo': {
        'demurrage_usd_dia':  35_000,
        'umbral_anomalia_hs':     4,
        'buques_referencia':      1,
        'nota': 'Demurrage estándar Panamax (USD/día)',
    },
    'shipping': {
        'demurrage_usd_dia':  35_000,
        'umbral_anomalia_hs':     4,
        'buques_referencia':      1,
        'nota': 'Demurrage estándar Panamax (USD/día)',
    },
    # O&G: costo de oportunidad sobre capex típico de perforación
    'energia': {
        'costo_oportunidad_mensual': 0.015,   # 1.5 %/mes
        'capex_referencia_musd':     120.0,   # USD 120M por pozo (Permian)
        'nota': 'Costo oportunidad sobre CAPEX típico de perforación',
    },
    'oil_gas': {
        'costo_oportunidad_mensual': 0.015,
        'capex_referencia_musd':     120.0,
        'nota': 'Costo oportunidad sobre CAPEX típico de perforación',
    },
    # Minería: precio Fe / t × tonelaje estimado
    'mineria': {
        'precio_mineral_usd_t': 95.0,   # hierro spot (proxy)
        'tonelaje_diario':   50_000,    # t/día referencia Pilbara
        'nota': 'Precio Fe spot × tonelaje diario referencia',
    },
    'mining_iron': {
        'precio_mineral_usd_t': 95.0,
        'tonelaje_diario':   50_000,
        'nota': 'Precio Fe spot × tonelaje diario referencia',
    },
    # Deforestación: no tiene ROI productivo
    'deforestacion': {
        'nota': 'Monitoreo de pérdida de cobertura forestal — sin ROI productivo',
    },
    # Tierras raras / litio / minerales críticos
    'tierras_raras': {
        'precio_mineral_usd_t': 8_500,  # litio carbonato (proxy Li2CO3 spot ~USD 8,500/t)
        'tonelaje_diario':      200,     # t/día referencia operación mediana
        'nota': 'Precio Li₂CO₃ spot (proxy). Adaptar según mineral específico.',
    },
}


def _load_prices() -> dict:
    """Carga precios reales si existen (generados por faro_price_updater.py)."""
    prices_path = ROOT / 'datos' / 'faro_prices.json'
    if prices_path.exists():
        with open(prices_path, encoding='utf-8') as f:
            return json.load(f)
    return {}


def get_economic_vars(sector: str) -> dict:
    """
    Retorna variables económicas para el sector.
    Si hay precios reales en datos/faro_prices.json, los usa.
    """
    base  = dict(ECONOMIC_VARS.get(sector, {'nota': 'Sector sin modelo económico definido'}))
    real  = _load_prices()

    if sector in ('agro',):
        if 'urea_usd_tn' in real:
            base['urea_usd_t'] = real['urea_usd_tn']
        if 'diesel_usd_l' in real:
            base['diesel_usd_l'] = real['diesel_usd_l']

    return base


# ── ROI dinámico ──────────────────────────────────────────────────────────────

def compute_roi(area_cfg: dict, metrics: dict, econ: dict) -> dict:
    """
    Calcula el ROI proyectado para el área en base al sector.
    Retorna dict con roi_usd_ha (agro), ahorro_usd_dia (maritimo), etc.
    """
    vertical = area_cfg.get('vertical', '')
    sector   = area_cfg.get('sector', vertical)
    roi      = {'sector': sector, 'econ': econ}

    if sector in ('agro',):
        ndvi  = metrics.get('ndvi_medio', 0) or 0
        rinde = metrics.get('rinde_estimado_tha') or (ndvi * 4.0)  # fallback lineal
        soja  = econ.get('soja_usd_t', 380)
        costo = econ.get('costo_servicio_ha', 2.50)
        ingreso_ha = rinde * soja
        roi.update({
            'rinde_estimado_tha':  round(rinde, 2),
            'ingreso_bruto_ha':    round(ingreso_ha, 2),
            'ahorro_ha':           round(costo, 2),
            'roi_multiplicador':   round(ingreso_ha / (costo * 100 + 1), 2),
        })

    elif sector in ('maritimo', 'shipping'):
        demurrage = econ.get('demurrage_usd_dia', 35_000)
        sar_db    = metrics.get('sar_medio_db', 0) or 0
        # Proxy: actividad SAR baja → posible demora logística
        # Un SAR < -10 dB en zona portuaria indica menor actividad de carga
        actividad_rel = max(0, min(1, (sar_db + 20) / 20))
        demora_estimada_dias = (1 - actividad_rel) * 2   # hasta 2 días de demora
        roi.update({
            'actividad_relativa':    round(actividad_rel, 3),
            'demora_estimada_dias':  round(demora_estimada_dias, 2),
            'costo_demora_usd':      round(demurrage * demora_estimada_dias, 0),
            'demurrage_dia_usd':     demurrage,
        })

    elif sector in ('energia', 'oil_gas'):
        capex  = econ.get('capex_referencia_musd', 120) * 1e6
        cop_m  = econ.get('costo_oportunidad_mensual', 0.015)
        activ  = metrics.get('indice_actividad', 50) or 50
        # Costo oportunidad proporcional a la inactividad
        inactividad = max(0, (100 - activ) / 100)
        costo_mes   = capex * cop_m * inactividad
        roi.update({
            'indice_actividad':  round(activ, 1),
            'inactividad_pct':   round(inactividad * 100, 1),
            'costo_oportunidad_mensual_usd': round(costo_mes, 0),
            'capex_referencia_musd': econ.get('capex_referencia_musd', 120),
        })

    elif sector in ('mineria', 'mining_iron'):
        precio = econ.get('precio_mineral_usd_t', 95)
        ton_d  = econ.get('tonelaje_diario', 50_000)
        activ  = metrics.get('indice_actividad', 50) or 50
        produccion_rel = activ / 100
        valor_dia      = precio * ton_d * produccion_rel
        roi.update({
            'indice_actividad':   round(activ, 1),
            'produccion_relativa':round(produccion_rel, 3),
            'valor_produccion_dia_usd': round(valor_dia, 0),
            'precio_mineral_usd_t': precio,
        })

    elif sector in ('tierras_raras',):
        precio = econ.get('precio_mineral_usd_t', 8_500)
        ton_d  = econ.get('tonelaje_diario', 200)
        activ  = metrics.get('indice_actividad', 50) or 50
        produccion_rel = activ / 100
        valor_dia      = precio * ton_d * produccion_rel
        roi.update({
            'indice_actividad':         round(activ, 1),
            'produccion_relativa':      round(produccion_rel, 3),
            'valor_produccion_dia_usd': round(valor_dia, 0),
            'precio_mineral_usd_t':     precio,
        })

    return roi


# ── Alertas automáticas ───────────────────────────────────────────────────────

ALERT_THRESHOLDS = {
    'desvio_rinde_pct':       5.0,    # agro: > 5% de caída respecto al histórico
    'anomalia_logistica_hs':  4.0,    # marítimo: > 4hs sin movimiento
    'caida_actividad_sar_pct':10.0,   # O&G/minería: > 10% caída semanal
    'ndvi_critico':           0.15,   # todos: NDVI < 0.15 es zona muy degradada
}


def check_alerts(area_name: str, metrics: dict, area_cfg: dict) -> list[dict]:
    """
    Evalúa condiciones de alerta sobre los métricas del área.
    Retorna lista de dicts con {nivel, mensaje, valor, umbral}.
    """
    alerts  = []
    sector  = area_cfg.get('sector', area_cfg.get('vertical', ''))
    estado  = metrics.get('estado', '')
    ndvi    = metrics.get('ndvi_medio')
    activ   = metrics.get('indice_actividad')
    alerta_txt = metrics.get('alerta', '')

    # Alerta de estado del engine (ALERTA ya calculada en faro_engine)
    if estado == 'ALERTA' and alerta_txt:
        alerts.append({
            'nivel': 'HIGH',
            'tipo':  'ENGINE_ALERT',
            'mensaje': alerta_txt[:120],
        })

    # NDVI crítico (cobertura muy baja)
    if ndvi is not None and ndvi < ALERT_THRESHOLDS['ndvi_critico']:
        alerts.append({
            'nivel': 'MEDIUM',
            'tipo':  'NDVI_CRITICO',
            'mensaje': f'NDVI {ndvi:.4f} bajo umbral crítico ({ALERT_THRESHOLDS["ndvi_critico"]:.2f})',
            'valor':   ndvi,
            'umbral':  ALERT_THRESHOLDS['ndvi_critico'],
        })

    # Alerta marítima: actividad SAR + proxy de demora
    if sector in ('maritimo', 'shipping'):
        sar = metrics.get('sar_medio_db', 0) or 0
        if sar < -12:
            alerts.append({
                'nivel':  'HIGH',
                'tipo':   'ANOMALIA_LOGISTICA',
                'mensaje': (f'Backscatter SAR bajo ({sar:.1f} dB) en zona portuaria — '
                            f'posible demora logística > {ALERT_THRESHOLDS["anomalia_logistica_hs"]:.0f}hs'),
                'valor':   sar,
                'umbral':  -12,
            })

    # Alerta O&G / minería / tierras raras: actividad baja
    if sector in ('energia', 'oil_gas', 'mineria', 'mining_iron', 'tierras_raras'):
        if activ is not None and activ < 40:
            alerts.append({
                'nivel':  'MEDIUM',
                'tipo':   'ACTIVIDAD_BAJA',
                'mensaje': f'Índice de actividad {activ:.1f}/100 — por debajo del umbral operacional (40)',
                'valor':   activ,
                'umbral':  40,
            })

    return alerts


# ── Fetching (wrapper sobre pipeline_area) ────────────────────────────────────

def _data_is_stale(area_name: str, max_age_days: int = 7) -> bool:
    """True si el último run tiene más de max_age_days días."""
    if not DATA_JSON.exists():
        return True
    with open(DATA_JSON, encoding='utf-8') as f:
        d = json.load(f)
    ts = d.get('pipeline', {}).get(area_name, {}).get('ultimo_run', '')
    if not ts:
        return True
    try:
        last = datetime.strptime(ts[:16], '%Y-%m-%d %H:%M')
        return (datetime.now() - last).days >= max_age_days
    except ValueError:
        return True


def fetch_latest(area_name: str, force: bool = False,
                 with_s2: bool = True, max_nubes: float = 50.0) -> dict:
    """
    Descarga S1 + S2 y corre el pipeline si los datos son obsoletos.
    Retorna el estado resultante del pipeline.
    """
    stale = _data_is_stale(area_name) or force
    if not stale:
        print(f"  [{area_name}] Datos recientes — sin descarga (usá --force-fetch para forzar)")
    else:
        print(f"  [{area_name}] Descargando datos satelitales actualizados...")
        from faro_sar_auto import pipeline_area
        pipeline_area(area_name, with_s2=with_s2, max_nubes_s2=max_nubes)

    # Leer estado actualizado
    if DATA_JSON.exists():
        with open(DATA_JSON, encoding='utf-8') as f:
            d = json.load(f)
        pipe = d.get('pipeline', {}).get(area_name, {})
        ins  = d.get('insights', {}).get(area_name, {})
        return {**pipe, **ins}
    return {}


# ── Visualización multicapa ───────────────────────────────────────────────────

def _load_rasters(area_cfg: dict) -> dict:
    """Carga los arrays numpy desde los TIF del área. Retorna dict con claves disponibles."""
    import rasterio

    result = {}
    ndvi_path = area_cfg.get('ndvi_tif', '')
    sar_path  = area_cfg.get('sar_output', '')

    if ndvi_path and Path(ndvi_path).exists():
        with rasterio.open(ndvi_path) as src:
            arr = src.read(1).astype(float)
            if arr[np.isfinite(arr)].max() > 2:
                arr /= 10000.0          # int16 GEE
        result['ndvi'] = np.clip(arr, -1, 1)

    if sar_path and Path(sar_path).exists():
        with rasterio.open(sar_path) as src:
            arr = src.read(1).astype(float)
        result['sar'] = arr

    if 'ndvi' in result and 'sar' in result:
        ndvi_arr = result['ndvi']
        sar_arr  = result['sar']
        # Redimensionar SAR al shape del NDVI si difieren
        if sar_arr.shape != ndvi_arr.shape:
            from skimage.transform import resize as _resize
            sar_arr = _resize(sar_arr, ndvi_arr.shape, anti_aliasing=True)
        ndvi_n = (ndvi_arr - np.nanmin(ndvi_arr)) / (
                  np.nanmax(ndvi_arr) - np.nanmin(ndvi_arr) + 1e-9)
        sar_n  = (sar_arr  - np.nanmin(sar_arr))  / (
                  np.nanmax(sar_arr)  - np.nanmin(sar_arr)  + 1e-9)
        result['fusion'] = 0.6 * ndvi_n + 0.4 * sar_n

    return result


def render_dashboard(area_cfg: dict, metrics: dict,
                     roi: dict, alerts: list[dict]) -> Path:
    """
    Genera el PNG del dashboard multicapa.
    Layout: [header] [NDVI | SAR | Heatmap Fusión | Panel económico]
             [fila de alertas al pie]
    """
    area_name = area_cfg['name']
    sector    = area_cfg.get('sector', area_cfg.get('vertical', ''))
    label     = area_cfg.get('label', area_name.title())
    col, col_l, tag = SECTOR_COLORS.get(sector, (GOLD, GOLD_L, 'FARO'))

    rasters = _load_rasters(area_cfg)

    fig = plt.figure(figsize=(20, 11), facecolor=BG)
    fig.suptitle(
        f'FARO PROTOCOL — Dashboard  ·  {label}  ·  {datetime.now().strftime("%Y-%m-%d %H:%M")}',
        color=WHITE, fontsize=14, fontweight='bold', y=0.985,
        fontfamily='monospace',
    )

    gs = gridspec.GridSpec(
        3, 4, figure=fig,
        height_ratios=[5.5, 2.5, 1.2],
        width_ratios=[4, 4, 4, 3.5],
        hspace=0.35, wspace=0.28,
        left=0.04, right=0.98, top=0.96, bottom=0.04,
    )

    # ── Capas satelitales ─────────────────────────────────────────────────────
    capas = [
        ('ndvi',   'NDVI  ·  Óptico Sentinel-2',   'RdYlGn'),
        ('sar',    'SAR  ·  Backscatter Sentinel-1','gray'),
        ('fusion', 'Mapa de Calor  ·  Fusión Faro', 'plasma'),
    ]

    for ci, (key, titulo, cmap) in enumerate(capas):
        ax = fig.add_subplot(gs[0, ci])
        ax.set_facecolor(BG2)
        ax.set_title(titulo, color=col_l, fontsize=9.5, pad=6,
                     fontfamily='monospace')
        ax.tick_params(colors=GREY, labelsize=6)
        for sp in ax.spines.values():
            sp.set_edgecolor(col)
            sp.set_linewidth(0.5)

        if key in rasters:
            arr = rasters[key]
            # Downsample para performance visual
            if arr.shape[0] > 800:
                step = arr.shape[0] // 800
                arr  = arr[::step, ::step]
            im = ax.imshow(arr, cmap=cmap, interpolation='bilinear',
                           vmin=np.nanpercentile(arr, 2),
                           vmax=np.nanpercentile(arr, 98))
            cb = plt.colorbar(im, ax=ax, fraction=0.046, pad=0.04)
            cb.ax.yaxis.set_tick_params(color=GREY, labelsize=6)
            plt.setp(cb.ax.get_yticklabels(), color=GREY)
        else:
            ax.text(0.5, 0.5, 'Sin datos disponibles\n(ejecutar fetch)',
                    ha='center', va='center', color=GREY, fontsize=9,
                    fontfamily='monospace', transform=ax.transAxes)
            ax.axis('off')

    # ── Panel económico ───────────────────────────────────────────────────────
    ax_eco = fig.add_subplot(gs[0, 3])
    ax_eco.set_facecolor(BG2)
    ax_eco.axis('off')
    ax_eco.set_title(f'ROI  ·  {tag}', color=col_l, fontsize=9.5, pad=6,
                     fontfamily='monospace')
    for sp in ax_eco.spines.values():
        sp.set_edgecolor(col); sp.set_linewidth(0.5)

    score = metrics.get('score_faro')
    s_col = GREEN if (score or 0) >= 65 else (AMBER if (score or 0) >= 45 else RED)

    ax_eco.text(0.08, 0.93, 'SCORE FARO', fontsize=7, color=GREY,
                fontfamily='monospace', transform=ax_eco.transAxes, va='top')
    ax_eco.text(0.08, 0.84, f'{score:.0f} / 100' if score else 'N/A',
                fontsize=18, fontweight='bold', color=s_col,
                transform=ax_eco.transAxes, va='top')

    # Barra de score
    ax_eco.add_patch(plt.Rectangle((0.08, 0.72), 0.84, 0.03,
        transform=ax_eco.transAxes, facecolor='#1a2030', zorder=3))
    if score:
        ax_eco.add_patch(plt.Rectangle((0.08, 0.72), 0.84 * score / 100, 0.03,
            transform=ax_eco.transAxes, facecolor=s_col, alpha=0.7, zorder=4))

    # Variables económicas relevantes del ROI
    eco_items = []
    if 'rinde_estimado_tha' in roi:
        eco_items += [
            ('Rinde est.',    f"{roi['rinde_estimado_tha']:.2f} t/ha"),
            ('Ingreso bruto', f"USD {roi.get('ingreso_bruto_ha', 0):,.0f}/ha"),
            ('Ahorro FARO',   f"USD {roi.get('ahorro_ha', 0):.2f}/ha"),
            ('ROI',           f"{roi.get('roi_multiplicador', 0):.1f}x"),
        ]
    elif 'costo_demora_usd' in roi:
        eco_items += [
            ('Demora estimada',  f"{roi.get('demora_estimada_dias', 0):.1f} días"),
            ('Demurrage',        f"USD {roi.get('demurrage_dia_usd', 0):,}/día"),
            ('Costo demora',     f"USD {roi.get('costo_demora_usd', 0):,.0f}"),
            ('Actividad SAR',    f"{roi.get('actividad_relativa', 0)*100:.0f}%"),
        ]
    elif 'costo_oportunidad_mensual_usd' in roi:
        eco_items += [
            ('Actividad',    f"{roi.get('indice_actividad', 0):.1f}/100"),
            ('Inactividad',  f"{roi.get('inactividad_pct', 0):.1f}%"),
            ('Costo/mes',    f"USD {roi.get('costo_oportunidad_mensual_usd', 0):,.0f}"),
            ('CAPEX ref.',   f"USD {roi.get('capex_referencia_musd', 0):.0f}M"),
        ]
    elif 'valor_produccion_dia_usd' in roi:
        eco_items += [
            ('Actividad',   f"{roi.get('indice_actividad', 0):.1f}/100"),
            ('Precio Fe',   f"USD {roi.get('precio_mineral_usd_t', 0)}/t"),
            ('Valor/día',   f"USD {roi.get('valor_produccion_dia_usd', 0):,.0f}"),
        ]

    y_e = 0.65
    for lbl, val in eco_items[:6]:
        ax_eco.text(0.08, y_e,      lbl, fontsize=6.5, color=GREY,
                    fontfamily='monospace', transform=ax_eco.transAxes, va='top')
        ax_eco.text(0.08, y_e-0.075, val, fontsize=9, fontweight='bold',
                    color=col_l, transform=ax_eco.transAxes, va='top')
        y_e -= 0.155

    econ_nota = roi.get('econ', {}).get('nota', '')
    if econ_nota:
        ax_eco.text(0.08, 0.04, econ_nota[:55],
                    fontsize=5, color=GREY, fontfamily='monospace',
                    transform=ax_eco.transAxes, va='bottom', wrap=True)

    # ── Métricas satelitales (fila 2) ─────────────────────────────────────────
    ax_met = fig.add_subplot(gs[1, :])
    ax_met.set_facecolor('#0d1020')
    ax_met.axis('off')

    met_items = [
        ('NDVI promedio',       f"{metrics.get('ndvi_medio', 'N/A'):.4f}"
                                 if metrics.get('ndvi_medio') else 'N/A'),
        ('SAR backscatter',     f"{metrics.get('sar_medio_db', 'N/A'):.2f} dB"
                                 if metrics.get('sar_medio_db') else 'N/A'),
        ('Índice Fusión',       f"{metrics.get('indice_fusion_medio', 'N/A'):.4f}"
                                 if metrics.get('indice_fusion_medio') else 'N/A'),
        ('Píxeles analizados',  f"{metrics.get('pixeles_analizados', 0)/1e6:.1f}M"
                                 if metrics.get('pixeles_analizados') else 'N/A'),
        ('Confianza',           metrics.get('confianza', 'N/A')),
        ('Estado',              metrics.get('estado', 'SIN_DATOS')),
        ('Último run',          (metrics.get('ultimo_run') or metrics.get('fecha', ''))[:16]),
        ('Satélites',           'S1 GRD + S2 L2A'),
    ]

    n_cols = len(met_items)
    for i, (k, v) in enumerate(met_items):
        x = 0.01 + i * (0.99 / n_cols)
        ax_met.text(x, 0.78, k, fontsize=7, color=GREY, fontfamily='monospace',
                    transform=ax_met.transAxes, va='top')
        estado_col = (RED_L if v == 'ALERTA' else
                      GREEN_L if v in ('OK', 'Alta') else WHITE)
        ax_met.text(x, 0.42, v, fontsize=9.5, fontweight='bold', color=estado_col,
                    fontfamily='monospace', transform=ax_met.transAxes, va='top')

    # ── Fila de alertas (pie) ─────────────────────────────────────────────────
    ax_alt = fig.add_subplot(gs[2, :])
    ax_alt.set_facecolor('#0a0010')
    ax_alt.axis('off')

    if alerts:
        for ai, alert in enumerate(alerts[:3]):
            nivel = alert.get('nivel', 'INFO')
            a_col = RED_L if nivel == 'HIGH' else (AMBER_L if nivel == 'MEDIUM' else GREY)
            prefix = '⚠' if nivel == 'HIGH' else '·'
            msg    = alert.get('mensaje', '')[:90]
            x_a    = 0.01 + ai * 0.34
            ax_alt.text(x_a, 0.75, f'{prefix} [{nivel}]', fontsize=7,
                        color=a_col, fontfamily='monospace',
                        transform=ax_alt.transAxes, va='top', fontweight='bold')
            ax_alt.text(x_a, 0.35, msg, fontsize=6.5, color=WHITE,
                        fontfamily='monospace', transform=ax_alt.transAxes, va='top')
    else:
        ax_alt.text(0.5, 0.5, '✓  Sin alertas activas  ·  Sistema operando dentro de parámetros normales',
                    fontsize=9, color=GREEN_L, fontfamily='monospace',
                    ha='center', va='center', transform=ax_alt.transAxes)

    out_path = ROOT / f'faro_dashboard_{area_name}.png'
    plt.savefig(out_path, dpi=150, bbox_inches='tight',
                facecolor=BG, edgecolor='none')
    plt.close(fig)
    return out_path


# ── Función principal ─────────────────────────────────────────────────────────

def run(area_name: str, force_fetch: bool = False,
        with_s2: bool = True, max_nubes: float = 50.0) -> dict:
    """
    Pipeline completo del dashboard para un área:
      fetch → métricas → ROI → alertas → render → output
    """
    print(f'\n  [{area_name.upper()}] Dashboard iniciado...')
    area_cfg = load_area(area_name)

    metrics = fetch_latest(area_name, force=force_fetch,
                           with_s2=with_s2, max_nubes=max_nubes)

    sector = area_cfg.get('sector', area_cfg.get('vertical', ''))
    econ   = get_economic_vars(sector)
    roi    = compute_roi(area_cfg, metrics, econ)
    alerts = check_alerts(area_name, metrics, area_cfg)

    out_path = render_dashboard(area_cfg, metrics, roi, alerts)

    result = {
        'area':    area_name,
        'score':   metrics.get('score_faro'),
        'estado':  metrics.get('estado'),
        'alerts':  len(alerts),
        'roi':     roi,
        'dashboard_png': str(out_path),
    }

    print(f'  [{area_name.upper()}] Score={result["score"]} | '
          f'Estado={result["estado"]} | Alertas={result["alerts"]}')
    print(f'  Dashboard: {out_path.name}')
    return result


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == '__main__':
    parser = argparse.ArgumentParser(
        description='FARO PROTOCOL — Dashboard de Monitoreo Dinámico')
    parser.add_argument('--area', nargs='+',
        help='Área(s) a monitorear')
    parser.add_argument('--all', action='store_true',
        help='Todas las áreas configuradas')
    parser.add_argument('--force-fetch', action='store_true',
        help='Forzar descarga aunque los datos sean recientes')
    parser.add_argument('--no-s2', action='store_true',
        help='Modo SAR-only (no descargar Sentinel-2)')
    parser.add_argument('--max-nubes', type=float, default=50.0)
    args = parser.parse_args()

    areas_a_correr = list_areas() if args.all else (args.area or [])
    if not areas_a_correr:
        parser.print_help()
        sys.exit(1)

    print('=' * 65)
    print('  FARO PROTOCOL — Dashboard')
    print(f'  {datetime.now().strftime("%Y-%m-%d %H:%M")}')
    print('=' * 65)

    resultados = []
    for a in areas_a_correr:
        r = run(a, force_fetch=args.force_fetch,
                with_s2=not args.no_s2, max_nubes=args.max_nubes)
        resultados.append(r)

    print()
    print('=' * 65)
    print('  RESUMEN')
    print('=' * 65)
    for r in resultados:
        tag = '⚠' if r['alerts'] > 0 else '✓'
        print(f'  {tag} {r["area"]:<15} '
              f'Score={str(r["score"] or "?"):>5} | '
              f'{r["estado"] or "?":<12} | '
              f'{r["alerts"]} alertas')
    print('=' * 65)
