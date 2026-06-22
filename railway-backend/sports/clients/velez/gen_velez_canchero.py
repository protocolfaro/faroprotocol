"""
Faro Protocol — Reporte Canchero Vélez Sarsfield
Planos de fútbol reales con marcadores de trabajo para el canchero.
Sin términos técnicos en los mapas.
"""
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.gridspec as gridspec
import matplotlib.patches as mpatches
from matplotlib.patches import FancyBboxPatch, Circle, Arc
import numpy as np
import hashlib, os
from datetime import datetime, timedelta

# ─── PALETA ──────────────────────────────────────────────────────────────────
BG    = '#06080b'
BG2   = '#0d1117'
BG3   = '#141c24'
GOLD  = '#c9a84c'
GOLDL = '#e2c97e'
WHITE = '#f2ede4'
WDIM  = '#9aa0a8'
RED   = '#c0392b'
REDL  = '#e74c3c'
REDXL = '#ff6b6b'
YEL   = '#d4a017'
YELL  = '#f0b429'
GRN   = '#1e8449'
GRNL  = '#27ae60'
GRNXL = '#58d68d'
FIELD = '#2d5a1b'      # green field
FIELD2= '#275218'      # darker stripe
LINE  = '#ffffff'
LINE_A= '#ffffffcc'

SEM_COLOR = {'verde': GRNL, 'amarillo': YELL, 'rojo': REDL}
SEM_EMOJI = {'verde': '●', 'amarillo': '●', 'rojo': '●'}

ZONES = [
    {
        'name': 'Amalfitani',
        'sem': 'verde',
        'estado': 'ÓPTIMO',
        'accion': 'Aerificar porterías\nSemana 3',
        'ndvi': 0.68, 'ndre': 0.42,
        'n_kg': 0, 'ccci': 'sin hist', 'riego': 12, 'ndvi_2d': None,
        'resiembra': 'No', 'hongos': 'No',
        'compact': 'Media', 'drenaje': 'OK',
        'timeline': 'Semana 3', 'fusion': 87.9, 'score_prev': 85.2,
        'map_accion': 'AERIFICAR PORTERÍAS\nSEMANA 3',
        'comparativa': '→',
        'focos': [
            {'x': 0.5, 'y': 0.88, 'color': YELL, 'label': 'AERIFICAR\nACÁ', 'size': 0.09},
            {'x': 0.5, 'y': 0.12, 'color': YELL, 'label': 'AERIFICAR\nACÁ', 'size': 0.09},
            {'x': 0.5, 'y': 0.50, 'color': YELL, 'label': 'COMPACTACIÓN', 'size': 0.07},
        ],
    },
    {
        'name': '1FA',
        'sem': 'amarillo',
        'estado': 'ATENCIÓN',
        'accion': 'Fungicida preventivo\nEsta semana',
        'ndvi': 0.52, 'ndre': 0.33,
        'n_kg': 15, 'ccci': 'sin hist', 'riego': 18, 'ndvi_2d': None,
        'resiembra': 'No', 'hongos': 'Preventivo',
        'compact': 'Alta', 'drenaje': 'Regular',
        'timeline': 'Esta semana', 'fusion': 65.0, 'score_prev': 68.0,
        'map_accion': 'FUNGICIDA PREVENTIVO\nESTA SEMANA',
        'comparativa': '↓',
        'focos': [
            {'x': 0.25, 'y': 0.75, 'color': YELL, 'label': 'ATENCIÓN\nACÁ', 'size': 0.09},
            {'x': 0.75, 'y': 0.25, 'color': YELL, 'label': 'REGAR\nACÁ', 'size': 0.08},
        ],
    },
    {
        'name': '2FA',
        'sem': 'amarillo',
        'estado': 'ATENCIÓN',
        'accion': 'Fertilizar 15kg N/ha\nEsta semana',
        'ndvi': 0.48, 'ndre': 0.31,
        'n_kg': 15, 'ccci': 'sin hist', 'riego': 18, 'ndvi_2d': None,
        'resiembra': 'Parcial', 'hongos': 'Preventivo',
        'compact': 'Alta', 'drenaje': 'Regular',
        'timeline': 'Esta semana', 'fusion': 58.0, 'score_prev': 55.0,
        'map_accion': 'FERTILIZAR 15kg N/ha\nESTA SEMANA',
        'comparativa': '↑',
        'focos': [
            {'x': 0.5, 'y': 0.50, 'color': YELL, 'label': 'ATENCIÓN\nACÁ', 'size': 0.09},
            {'x': 0.75, 'y': 0.75, 'color': YELL, 'label': 'REGAR\nACÁ', 'size': 0.07},
        ],
    },
    {
        'name': '3FA',
        'sem': 'amarillo',
        'estado': 'ATENCIÓN',
        'accion': 'Aerificar + riego\nEsta semana',
        'ndvi': 0.51, 'ndre': 0.34,
        'n_kg': 15, 'ccci': 'sin hist', 'riego': 18, 'ndvi_2d': None,
        'resiembra': 'No', 'hongos': 'Preventivo',
        'compact': 'Alta', 'drenaje': 'Regular',
        'timeline': 'Esta semana', 'fusion': 72.0, 'score_prev': 70.0,
        'map_accion': 'AERIFICAR + RIEGO\nESTA SEMANA',
        'comparativa': '→',
        'focos': [
            {'x': 0.5, 'y': 0.70, 'color': YELL, 'label': 'ATENCIÓN\nACÁ', 'size': 0.09},
            {'x': 0.5, 'y': 0.30, 'color': YELL, 'label': 'REGAR\nACÁ', 'size': 0.08},
        ],
    },
    {
        'name': '4FA',
        'sem': 'amarillo',
        'estado': 'ATENCIÓN',
        'accion': 'Fungicida + resembrar\nEsta semana',
        'ndvi': 0.39, 'ndre': 0.26,
        'n_kg': 15, 'ccci': 'sin hist', 'riego': 22, 'ndvi_2d': None,
        'resiembra': 'Parcial', 'hongos': 'Preventivo',
        'compact': 'Alta', 'drenaje': 'Regular',
        'timeline': 'Esta semana', 'fusion': 45.0, 'score_prev': 51.0,
        'map_accion': 'FUNGICIDA + RESEMBRAR\nESTA SEMANA',
        'comparativa': '↓',
        'focos': [
            {'x': 0.3, 'y': 0.70, 'color': YELL, 'label': 'ATENCIÓN\nACÁ', 'size': 0.09},
            {'x': 0.7, 'y': 0.30, 'color': YELL, 'label': 'RESEMBRAR\nACÁ', 'size': 0.08},
        ],
    },
    {
        'name': '5FA',
        'sem': 'amarillo',
        'estado': 'ATENCIÓN',
        'accion': 'Riego + monitoreo\nEsta semana',
        'ndvi': 0.50, 'ndre': 0.33,
        'n_kg': 15, 'ccci': 'sin hist', 'riego': 18, 'ndvi_2d': None,
        'resiembra': 'No', 'hongos': 'Preventivo',
        'compact': 'Alta', 'drenaje': 'Regular',
        'timeline': 'Esta semana', 'fusion': 60.0, 'score_prev': 58.0,
        'map_accion': 'RIEGO + MONITOREO\nESTA SEMANA',
        'comparativa': '↑',
        'focos': [
            {'x': 0.5, 'y': 0.60, 'color': YELL, 'label': 'ATENCIÓN\nACÁ', 'size': 0.09},
        ],
    },
    {
        'name': '6FA',
        'sem': 'amarillo',
        'estado': 'ATENCIÓN',
        'accion': 'Fertilizar + aerificar\nEsta semana',
        'ndvi': 0.45, 'ndre': 0.30,
        'n_kg': 15, 'ccci': 'sin hist', 'riego': 20, 'ndvi_2d': None,
        'resiembra': 'No', 'hongos': 'Preventivo',
        'compact': 'Alta', 'drenaje': 'Regular',
        'timeline': 'Esta semana', 'fusion': 55.0, 'score_prev': 60.0,
        'map_accion': 'FERTILIZAR + AERIFICAR\nESTA SEMANA',
        'comparativa': '↓',
        'focos': [
            {'x': 0.4, 'y': 0.65, 'color': YELL, 'label': 'ATENCIÓN\nACÁ', 'size': 0.09},
            {'x': 0.6, 'y': 0.35, 'color': YELL, 'label': 'REGAR\nACÁ', 'size': 0.08},
        ],
    },
    {
        'name': '7FA',
        'sem': 'amarillo',
        'estado': 'ATENCIÓN',
        'accion': 'Riego + fungicida\nEsta semana',
        'ndvi': 0.53, 'ndre': 0.35,
        'n_kg': 15, 'ccci': 'sin hist', 'riego': 18, 'ndvi_2d': None,
        'resiembra': 'No', 'hongos': 'Preventivo',
        'compact': 'Alta', 'drenaje': 'Regular',
        'timeline': 'Esta semana', 'fusion': 68.0, 'score_prev': 65.0,
        'map_accion': 'RIEGO + FUNGICIDA\nESTA SEMANA',
        'comparativa': '↑',
        'focos': [
            {'x': 0.5, 'y': 0.75, 'color': YELL, 'label': 'ATENCIÓN\nACÁ', 'size': 0.09},
            {'x': 0.5, 'y': 0.25, 'color': YELL, 'label': 'REGAR\nACÁ', 'size': 0.08},
        ],
    },
    {
        'name': '8FA',
        'sem': 'amarillo',
        'estado': 'ATENCIÓN',
        'accion': 'Aerificar + monitoreo\nEsta semana',
        'ndvi': 0.56, 'ndre': 0.37,
        'n_kg': 15, 'ccci': 'sin hist', 'riego': 16, 'ndvi_2d': None,
        'resiembra': 'No', 'hongos': 'Preventivo',
        'compact': 'Media', 'drenaje': 'Regular',
        'timeline': 'Esta semana', 'fusion': 74.0, 'score_prev': 72.0,
        'map_accion': 'AERIFICAR + MONITOREO\nESTA SEMANA',
        'comparativa': '→',
        'focos': [
            {'x': 0.5, 'y': 0.88, 'color': YELL, 'label': 'AERIFICAR\nACÁ', 'size': 0.09},
            {'x': 0.5, 'y': 0.12, 'color': YELL, 'label': 'AERIFICAR\nACÁ', 'size': 0.09},
        ],
    },
    {
        'name': '9FA',
        'sem': 'verde',
        'estado': 'ÓPTIMO',
        'accion': 'Monitoreo rutinario\nSemana 3',
        'ndvi': 0.65, 'ndre': 0.41,
        'n_kg': 0, 'ccci': 'sin hist', 'riego': 12, 'ndvi_2d': None,
        'resiembra': 'No', 'hongos': 'No',
        'compact': 'Media', 'drenaje': 'OK',
        'timeline': 'Semana 3', 'fusion': 82.0, 'score_prev': 80.0,
        'map_accion': 'MONITOREO RUTINARIO\nSEMANA 3',
        'comparativa': '→',
        'focos': [
            {'x': 0.5, 'y': 0.88, 'color': YELL, 'label': 'AERIFICAR\nACÁ', 'size': 0.09},
            {'x': 0.5, 'y': 0.12, 'color': YELL, 'label': 'AERIFICAR\nACÁ', 'size': 0.09},
        ],
    },
    {
        'name': '10FA',
        'sem': 'verde',
        'estado': 'ÓPTIMO',
        'accion': 'Aerificar bordes\nSemana 3',
        'ndvi': 0.63, 'ndre': 0.40,
        'n_kg': 0, 'ccci': 'sin hist', 'riego': 12, 'ndvi_2d': None,
        'resiembra': 'No', 'hongos': 'No',
        'compact': 'Media', 'drenaje': 'OK',
        'timeline': 'Semana 3', 'fusion': 78.0, 'score_prev': 75.0,
        'map_accion': 'AERIFICAR BORDES\nSEMANA 3',
        'comparativa': '↑',
        'focos': [
            {'x': 0.5, 'y': 0.88, 'color': YELL, 'label': 'AERIFICAR\nACÁ', 'size': 0.09},
            {'x': 0.5, 'y': 0.12, 'color': YELL, 'label': 'AERIFICAR\nACÁ', 'size': 0.09},
        ],
    },
    {
        'name': '1FP',
        'sem': 'amarillo',
        'estado': 'ATENCIÓN',
        'accion': 'Riego + resembrar\nEsta semana',
        'ndvi': 0.44, 'ndre': 0.29,
        'n_kg': 15, 'ccci': 'sin hist', 'riego': 20, 'ndvi_2d': None,
        'resiembra': 'Parcial', 'hongos': 'Preventivo',
        'compact': 'Alta', 'drenaje': 'Regular',
        'timeline': 'Esta semana', 'fusion': 50.0, 'score_prev': 55.0,
        'map_accion': 'RIEGO + RESEMBRAR\nESTA SEMANA',
        'comparativa': '↓',
        'focos': [
            {'x': 0.5, 'y': 0.65, 'color': YELL, 'label': 'ATENCIÓN\nACÁ', 'size': 0.09},
            {'x': 0.5, 'y': 0.35, 'color': YELL, 'label': 'RESEMBRAR\nACÁ', 'size': 0.08},
        ],
    },
    {
        'name': '2FP',
        'sem': 'verde',
        'estado': 'ÓPTIMO',
        'accion': 'Monitoreo rutinario\nSemana 3',
        'ndvi': 0.70, 'ndre': 0.44,
        'n_kg': 0, 'ccci': 'sin hist', 'riego': 12, 'ndvi_2d': None,
        'resiembra': 'No', 'hongos': 'No',
        'compact': 'Media', 'drenaje': 'OK',
        'timeline': 'Semana 3', 'fusion': 85.0, 'score_prev': 83.0,
        'map_accion': 'MONITOREO RUTINARIO\nSEMANA 3',
        'comparativa': '↑',
        'focos': [
            {'x': 0.5, 'y': 0.88, 'color': YELL, 'label': 'AERIFICAR\nACÁ', 'size': 0.09},
            {'x': 0.5, 'y': 0.12, 'color': YELL, 'label': 'AERIFICAR\nACÁ', 'size': 0.09},
        ],
    },
]

now = datetime.now()
SCAN_DATE = now.strftime('%d de %B de %Y  ·  %H:%M UTC−3')
SHA = hashlib.sha256(f"FARO-VELEZ-CANCHERO-{now.isoformat()}".encode()).hexdigest()

# ─── REAL DATA OVERRIDE ───────────────────────────────────────────────────────
def _build_zones_from_vd(vd):
    _sem_estado   = {'verde': 'ÓPTIMO',    'amarillo': 'ATENCIÓN', 'rojo': 'CRÍTICA'}
    _sem_n        = {'verde': 0,           'amarillo': 15,         'rojo': 40}
    _sem_riego    = {'verde': 12,          'amarillo': 18,         'rojo': 30}
    _sem_res      = {'verde': 'No',        'amarillo': 'Parcial',  'rojo': 'Total'}
    _sem_hon      = {'verde': 'No',        'amarillo': 'Preventivo', 'rojo': 'Activos'}
    _sem_comp     = {'verde': 'Media',     'amarillo': 'Alta',     'rojo': 'Crítica'}
    _sem_dren     = {'verde': 'OK',        'amarillo': 'Regular',  'rojo': 'Crítico'}
    _sem_timeline = {'verde': 'Semana 3',  'amarillo': 'Esta semana', 'rojo': 'HOY — URGENTE'}
    _focos_verde  = [{'x': 0.5, 'y': 0.88, 'color': YELL, 'label': 'AERIFICAR\nACÁ', 'size': 0.09},
                     {'x': 0.5, 'y': 0.12, 'color': YELL, 'label': 'AERIFICAR\nACÁ', 'size': 0.09}]
    _focos_amari  = [{'x': 0.5, 'y': 0.50, 'color': YELL, 'label': 'ATENCIÓN',       'size': 0.09}]
    _focos_rojo   = [{'x': 0.3, 'y': 0.70, 'color': REDL, 'label': 'URGENTE\nACÁ',   'size': 0.13},
                     {'x': 0.7, 'y': 0.30, 'color': REDL, 'label': 'REPARAR',         'size': 0.11}]
    _focos_map    = {'verde': _focos_verde, 'amarillo': _focos_amari, 'rojo': _focos_rojo}
    _CANCHA_NAMES = {
        'amalfitani': 'Amalfitani',
        '1fa': '1FA', '2fa': '2FA', '3fa': '3FA', '4fa': '4FA',
        '5fa': '5FA', '6fa': '6FA', '7fa': '7FA', '8fa': '8FA',
        '9fa': '9FA', '10fa': '10FA', '1fp': '1FP', '2fp': '2FP',
    }
    canchas = vd.get('sectores', {}).get('canchero', {}).get('canchas', [])
    if not canchas:
        return None

    # ── 1.1 Riego real — déficit hídrico semanal via ET₀ (NASA POWER + Penman-Monteith) ──
    # Umbral semáforo: <5mm → verde, 5-15mm → amarillo, >15mm → rojo
    _wl      = vd.get('weather_live', {})
    _deficit = _wl.get('deficit_hidrico_mm')   # mm/semana, None si no hay datos

    # ── 1.2 Hongos real — Dollar Spot pressure via Smith-Kerns (ventana 5d T/RH) ──
    # Umbral: <30% → verde "No", 30-60% → amarillo "Preventivo", >60% → rojo "Activos"
    _sk_pct = _wl.get('riesgo_dollar_spot_pct')

    def _hongos_from_sk(pct):
        if pct < 30:  return f'No ({pct:.0f}%)'
        if pct < 60:  return f'Prev·{pct:.0f}%'
        return             f'Activos·{pct:.0f}%'

    # ── 1.5 Compactación real — score_compactacion_fisica (θ_soil + SAR VV C-band) ──
    # Umbral: <25 → Baja, 25-50 → Media, 50-70 → Alta, >70 → Crítica
    _hm_vd  = vd.get('usuarios', {}).get('roger', {}).get('heatmaps', {})
    _theta_g = max(0.01, min(0.50, float(_wl.get('humedad_suelo_pct') or 20.0) / 100.0))
    _vv_g    = float(_wl.get('sar_vv_db') or -14.0)
    _sc_fn   = None
    try:
        from faro_compactacion_ml import score_compactacion_fisica as _sc_fn
    except ImportError:
        pass

    def _compact_label(s):
        if s < 25:  return f'Baja·{s:.0f}'
        if s < 50:  return f'Media·{s:.0f}'
        if s < 70:  return f'Alta·{s:.0f}'
        return          f'Crítica·{s:.0f}'

    # ── 2.4 Drenaje Amalfitani — DrainageModule + lluvia_max_1h vs R_crítica ─
    # R_crítica = Q_efectivo / área_campo = 68.98 L/s / 7140 m² × 3600 = 34.78 mm/h
    # Comparamos intensidad horaria máxima REAL (Open-Meteo past_days=2) contra
    # la capacidad hidráulica real del sistema C&G — sin proxy acumulado.
    _R_CRITICA_AMF = 34.78   # mm/h — derivado de Q_efectivo planos C&G
    _lluvia_max_1h  = float(_wl.get('lluvia_max_1h_mm', 0.0) or 0.0)
    _lluvia_48h     = float(_wl.get('lluvia_48h_mm',    0.0) or 0.0)

    _analyze_drainage = None
    try:
        import pathlib as _pl_dp, sys as _sys_dp
        _dp_dir = str(
            _pl_dp.Path(__file__).resolve()
                .parent.parent.parent.parent   # → railway-backend/
            / 'events' / 'clients' / 'dale-play'
        )
        if _dp_dir not in _sys_dp.path:
            _sys_dp.path.insert(0, _dp_dir)
        from dale_play_drainage import analyze_drainage as _analyze_drainage
    except Exception:
        pass

    _a_dren = _sem_dren.get('verde', 'OK')   # fallback si DrainageModule no disponible
    if _analyze_drainage is not None:
        try:
            _analyze_drainage(lluvia_48h_mm=_lluvia_48h)  # actualiza models/drainage_amalfitani.json
            # Criterio físico: intensidad horaria máxima real vs capacidad del sistema
            _a_dren = ('Crítico·C&G' if _lluvia_max_1h > _R_CRITICA_AMF          else
                       'Regular·C&G' if _lluvia_max_1h > _R_CRITICA_AMF * 0.5    else
                       'OK·C&G')
        except Exception:
            pass

    # ── 2.4 Drenaje VO — theta_soil → Van Genuchten (umbrales aprobados) ─────
    # θ < 0.26 → OK, 0.26-0.33 → Regular, ≥0.33 → Crítico
    # Fuente: SAR si sar_vv_db está en heatmaps por cancha, proxy si no.
    _THETA_TAU2 = 0.5998   # exp(-2 × 0.08 × 2.5 / cos(38.5°)) — constante WCM césped
    _THETA_SVEG = 0.000940  # σ_veg WCM: 0.0012 × LAI × (1-τ²) × cos(θ)

    def _theta_from_vv(vv_db: float) -> float:
        sig_vv = 10.0 ** (vv_db / 10.0)
        s_soil = max((sig_vv - _THETA_SVEG) / (_THETA_TAU2 + 1e-6), 1e-4)
        return max(0.046, min(0.409, (s_soil * 0.28) + 0.12))

    def _drenaje_vo(theta: float, fuente: str) -> str:
        if theta < 0.26:   lbl = 'OK'
        elif theta < 0.33: lbl = 'Regular'
        else:              lbl = 'Crítico'
        return f'{lbl}·{fuente}'

    zones = []

    # Amalfitani desde sectores.estadio
    _estadio = vd.get('sectores', {}).get('estadio', {})
    if _estadio:
        _a_sem   = _estadio.get('sem', 'verde')
        _a_score = float(_estadio.get('score', 80.0))
        _a_sprev = float(_estadio.get('score_prev', _a_score))
        _a_det   = _estadio.get('detalle', '')
        _a_ndre  = _estadio.get('ndre')   # None si sin imagen óptica — no usar proxy
        _a_ndvi  = float(_estadio.get('ndvi') or 0.68)
        _a_ccci  = _estadio.get('ccci')
        _a_ccci_label = f'CCCI {_a_ccci:.3f}·sin hist' if isinstance(_a_ccci, float) else 'Sin histórico'
        _a_accion = (_a_det.split('·')[1].strip() if '·' in _a_det else _a_det) or 'Monitoreo rutinario'
        _a_riego  = round(_deficit) if _deficit is not None else _sem_riego.get(_a_sem, 12)
        _a_hk     = _hongos_from_sk(_sk_pct) if _sk_pct is not None else _sem_hon.get(_a_sem, 'No')
        _a_comp   = _compact_label(_sc_fn({'theta_soil': _theta_g, 'sar_vv_db': _vv_g})) if _sc_fn else _sem_comp.get(_a_sem, 'Media')
        _a_pct_baja = _estadio.get('pct_baja_ndvi')
        _a_res = ('No' if not isinstance(_a_pct_baja, float) or _a_pct_baja < 15
                  else f'Parcial·{round(_a_pct_baja)}%' if _a_pct_baja < 35
                  else f'Total·{round(_a_pct_baja)}%')
        _a_ndvi2d = _hm_vd.get('amalfitani', {}).get('ndvi_2d')
        _a_focos_raw = _estadio.get('focos_reales')
        _a_focos = ([
            {'x': f['x'], 'y': f['y'], 'ndvi': f['ndvi'],
             'color': REDL if f['ndvi'] < 0.20 else YELL,
             'label': f'NDVI\n{f["ndvi"]:.2f}', 'size': 0.09}
            for f in _a_focos_raw
        ] if _a_focos_raw else _focos_map.get(_a_sem, _focos_verde))
        zones.append({
            'name': 'Amalfitani', 'sem': _a_sem,
            'estado': _sem_estado.get(_a_sem, 'ÓPTIMO'),
            'accion': _a_accion,
            'ndvi': _a_ndvi, 'ndre': _a_ndre, 'n_kg': _sem_n.get(_a_sem, 0),
            'ccci': _a_ccci_label, 'riego': _a_riego,
            'resiembra': _a_res, 'hongos': _a_hk, 'compact': _a_comp,
            'drenaje': _a_dren,
            'timeline': _sem_timeline.get(_a_sem, 'Semana 3'),
            'fusion': _a_score, 'score_prev': _a_sprev,
            'map_accion': _a_accion.upper(),
            'comparativa': '↑' if _a_score > _a_sprev else ('→' if _a_score == _a_sprev else '↓'),
            'focos': _a_focos, 'ndvi_2d': _a_ndvi2d,
        })

    for c in canchas[:12]:
        sem    = c.get('sem', 'amarillo')
        ndvi   = c.get('ndvi')   # may be None if cloud-covered; handle below
        ndvi_f = float(ndvi) if isinstance(ndvi, (int, float)) else 0.0
        score  = c.get('score', 60)
        s_prev = c.get('score_prev', score)
        detalle = c.get('detalle', '')
        cid     = c.get('id', '')
        nombre  = _CANCHA_NAMES.get(cid, c.get('nombre', cid.upper()))
        comp_sym = '↑' if score > s_prev else ('→' if score == s_prev else '↓')
        accion_short = (detalle.split('\xb7')[1].strip() if '\xb7' in detalle
                        else detalle.split('·')[1].strip() if '·' in detalle
                        else detalle)

        # 1.1 riego: déficit semanal mm (real) o fallback semáforo
        _riego_val = (round(_deficit) if _deficit is not None
                      else _sem_riego.get(sem, 18))

        # 1.2 hongos: Smith-Kerns (real) o fallback semáforo
        _hongos_val = (_hongos_from_sk(_sk_pct) if _sk_pct is not None
                       else _sem_hon.get(sem, 'No'))

        # 1.5 compact: score físico (real) o fallback semáforo
        if _sc_fn is not None:
            _vv_c = float(_hm_vd.get(cid, {}).get('sar_vv_db') or _vv_g)
            _cs   = _sc_fn({'theta_soil': _theta_g, 'sar_vv_db': _vv_c})
            _compact_val = _compact_label(_cs)
        else:
            _compact_val = _sem_comp.get(sem, 'Media')

        # ── 2.1 NDRE real — B05 RedEdge (Sentinel-2) via satellite pipeline ──
        # Umbral N: <0.15 → deficiencia severa, 0.15-0.25 → moderada, >0.25 → OK
        _ndre_raw = c.get('ndre')
        _ndre_val = round(float(_ndre_raw), 3) if isinstance(_ndre_raw, (int, float)) else None

        # ── 2.2 CCCI — Canopy Chlorophyll Content Index (Barnes et al. 2000) ──
        # CCCI = NDRE / NDVI. Muestra desvío relativo vs. baseline histórico estacional.
        # Baseline: p80 de observaciones limpias (≥10 por estación) acumuladas en Supabase.
        # Sin suficiente histórico → muestra CCCI crudo + aviso explícito.
        # TODO 2.4 PENDIENTE: calibrar umbral de corte con muestras foliares reales de Vélez.
        _MIN_OBS_CCCI = 10   # observaciones limpias por estación para baseline confiable
        _ccci_raw = c.get('ccci')
        _ccci_label = 'Sin histórico'
        if isinstance(_ccci_raw, (int, float)):
            # Try seasonal baseline from Supabase
            _ccci_bl = None
            try:
                import velez_supabase as _vs_ccci
                if _vs_ccci._ok():
                    _month_now = __import__('datetime').date.today().month
                    _season = ('invierno'  if _month_now in (5,6,7,8) else
                               'primavera' if _month_now in (9,10)    else
                               'verano'    if _month_now in (11,12,1,2) else 'otono')
                    _bl = _vs_ccci.get_ccci_seasonal_baseline(
                        'amalfitani', cid, _season, min_obs=_MIN_OBS_CCCI)
                    if _bl['baseline'] is not None:
                        _ccci_bl = _bl['baseline']
                        _n_obs   = _bl['n_obs']
            except Exception:
                pass
            if _ccci_bl is not None:
                _dev = round((_ccci_raw - _ccci_bl) / _ccci_bl * 100)
                _sign = '+' if _dev >= 0 else ''
                _ccci_label = f'{_sign}{_dev}% vs hist·{_season[:3]}'
            else:
                _ccci_label = f'CCCI {_ccci_raw:.3f}·sin hist'
        # n_kg falls back to semáforo lookup until CCCI baseline is established
        _n_kg_val = _sem_n.get(sem, 0)

        # ── 2.3 Resiembra real — % píxeles NDVI<0.30 dentro del bbox ──
        # Umbral: <15% → No, 15-35% → Parcial, >35% → Total
        _pct_baja = c.get('pct_baja_ndvi')
        if isinstance(_pct_baja, (int, float)):
            if _pct_baja < 15:    _res_val = 'No'
            elif _pct_baja < 35:  _res_val = f'Parcial·{round(_pct_baja)}%'
            else:                  _res_val = f'Total·{round(_pct_baja)}%'
        else:
            _res_val = _sem_res.get(sem, 'No')

        # ── 2.5 Focos reales — posición de píxeles dañados dentro del bbox ──
        _focos_raw = c.get('focos_reales')
        if _focos_raw:
            _focos_val = [
                {'x': f['x'], 'y': f['y'],
                 'ndvi': f['ndvi'],      # conservado para overlay en heatmap panel
                 'color': REDL if f['ndvi'] < 0.20 else YELL,
                 'label': f'NDVI\n{f["ndvi"]:.2f}', 'size': 0.09}
                for f in _focos_raw
            ]
        else:
            _focos_val = _focos_map.get(sem, _focos_amari)

        # ── 4.2 ndvi_2d — matriz satelital 2D para panel heatmap ──────────────
        # Lista-de-listas float16 normalizada [0,1] (vmin=0.10, vmax=0.65).
        # None si la cancha no tuvo imagen limpia en el ciclo actual.
        _ndvi2d_val = _hm_vd.get(cid, {}).get('ndvi_2d')

        # ── 2.4 Drenaje VO — theta_soil Van Genuchten (aprobado 2026-06-21) ──
        _vv_per_cancha = _hm_vd.get(cid, {}).get('sar_vv_db')
        if _vv_per_cancha is not None:
            _drenaje_val = _drenaje_vo(_theta_from_vv(float(_vv_per_cancha)), 'SAR')
        else:
            _drenaje_val = _drenaje_vo(_theta_g, 'proxy')

        zones.append({
            'name':       nombre,
            'sem':        sem,
            'estado':     _sem_estado.get(sem, 'ATENCIÓN'),
            'accion':     accion_short,
            'ndvi':       ndvi_f,
            'ndre':       _ndre_val,      # 2.1: real B05, no proxy
            'n_kg':       _n_kg_val,      # fallback semáforo (CCCI baseline aún no acumulado)
            'ccci':       _ccci_label,    # 2.2: CCCI desvío vs histórico estacional
            'riego':      _riego_val,
            'resiembra':  _res_val,       # 2.3: real pct_baja_ndvi
            'hongos':     _hongos_val,
            'compact':    _compact_val,
            # 2.4 Drenaje VO — theta_soil Van Genuchten (aprobado 2026-06-21)
            'drenaje':    _drenaje_val,
            'timeline':   _sem_timeline.get(sem, 'Esta semana'),
            'fusion':     float(score),
            'score_prev': float(s_prev),
            'map_accion': accion_short.upper(),
            'comparativa': comp_sym,
            'focos':      _focos_val,     # 2.5: real pixel positions
            'ndvi_2d':    _ndvi2d_val,    # 4.2: matriz 2D para heatmap panel; None = sin imagen
        })
    return zones

import os as _os, json as _json
try:
    import bonillo_semaforo as _bs
except ImportError:
    _bs = None
_bonillo_data: dict = {}
_vd_path = _os.environ.get("FARO_VD_PATH")
_insar_sectors: dict | None = None   # 1.4: sector InSAR mm values from JSON
if _vd_path:
    try:
        with open(_vd_path, encoding="utf-8") as _f:
            _vd_data = _json.load(_f)
        _zones_real = _build_zones_from_vd(_vd_data)
        if _zones_real:
            ZONES = _zones_real
        # 1.4: extract InSAR sector displacements (pushed by run_insar_refresh)
        _secs = _vd_data.get("sectores", {})
        _ins_check = {k: _secs.get(k, {}).get("insar_mm")
                      for k in ("estadio", "poli", "sede", "piletas")}
        if any(v is not None for v in _ins_check.values()):
            _insar_sectors = _ins_check
        # 8: calidad superficie — mediciones físicas de campo desde roger.mediciones
        if _bs:
            _bonillo_data = _bs.extract_from_vd(_vd_data)
    except Exception as _e:
        print(f"FARO_VD_PATH canchero: {_e} — usando datos hardcodeados")
_out_path = _os.environ.get("FARO_OUT_PATH")

# ─── SOCCER FIELD DRAWING FUNCTION ───────────────────────────────────────────
def draw_soccer_field(ax, x0, y0, w, h, focos=None, is_critical=False, title='',
                       sem='verde', accion='', numero=None):
    """
    Draw a full soccer field plan at the given position in axis coordinates (0-10).
    x0, y0 = bottom-left corner
    w, h   = width and height
    focos  = list of {'x':0-1, 'y':0-1, 'color':..., 'label':..., 'size':...}
    """
    # Stripe background
    n_stripes = 8
    sw = w / n_stripes
    for i in range(n_stripes):
        fc = FIELD if i % 2 == 0 else FIELD2
        ax.add_patch(mpatches.Rectangle(
            (x0 + i * sw, y0), sw, h,
            facecolor=fc, edgecolor='none', zorder=1))

    # Critical overlay
    if is_critical:
        ax.add_patch(mpatches.Rectangle(
            (x0, y0), w, h,
            facecolor=RED, alpha=0.22, zorder=2))

    # Outer boundary
    lw_border = 2.5 if is_critical else 1.5
    ec_border = REDL if is_critical else SEM_COLOR[sem]
    ax.add_patch(mpatches.Rectangle(
        (x0, y0), w, h,
        facecolor='none', edgecolor=ec_border,
        linewidth=lw_border, zorder=6))

    # Field lines (white)
    def line(x1, y1, x2, y2, lw=1.0, alpha=0.9):
        ax.plot([x1, x2], [y1, y2], color=LINE, linewidth=lw,
                alpha=alpha, zorder=5)

    def frac(val, total, base): return base + val * total

    # Outer touchlines (already done by rectangle)
    # Halfway line
    mid_y = y0 + h / 2
    line(x0, mid_y, x0 + w, mid_y, lw=1.0)

    # Centre circle  (r = ~9.15m → scale)
    r_circle = min(w, h) * 0.16
    cx_, cy_ = x0 + w/2, y0 + h/2
    ax.add_patch(mpatches.Circle((cx_, cy_), r_circle,
        fill=False, edgecolor=LINE, linewidth=1.0, alpha=0.9, zorder=5))
    # Centre dot
    ax.plot(cx_, cy_, 'o', ms=3, color=LINE, zorder=5)

    # ---- Penalty areas (both ends) ----
    # Standard pitch: penalty area = 40.32m wide × 16.5m deep
    # Scale relative to field
    pa_w = w * 0.62     # ~40/64 of pitch width
    pa_h = h * 0.17     # ~16.5/100 of pitch length
    pa_x = x0 + (w - pa_w) / 2

    # North (top) penalty area
    pa_top_y = y0 + h - pa_h
    ax.add_patch(mpatches.Rectangle(
        (pa_x, pa_top_y), pa_w, pa_h,
        fill=False, edgecolor=LINE, linewidth=0.9, alpha=0.9, zorder=5))

    # South (bottom) penalty area
    pa_bot_y = y0
    ax.add_patch(mpatches.Rectangle(
        (pa_x, pa_bot_y), pa_w, pa_h,
        fill=False, edgecolor=LINE, linewidth=0.9, alpha=0.9, zorder=5))

    # ---- Goal areas (6-yard boxes) ----
    ga_w = w * 0.30
    ga_h = h * 0.065
    ga_x = x0 + (w - ga_w) / 2

    # Top goal area
    ax.add_patch(mpatches.Rectangle(
        (ga_x, y0 + h - ga_h), ga_w, ga_h,
        fill=False, edgecolor=LINE, linewidth=0.7, alpha=0.85, zorder=5))

    # Bottom goal area
    ax.add_patch(mpatches.Rectangle(
        (ga_x, y0), ga_w, ga_h,
        fill=False, edgecolor=LINE, linewidth=0.7, alpha=0.85, zorder=5))

    # ---- Goals (posts) ----
    goal_w = w * 0.12
    goal_d = h * 0.025
    goal_x = x0 + (w - goal_w) / 2

    # Top goal
    ax.add_patch(mpatches.Rectangle(
        (goal_x, y0 + h), goal_w, goal_d,
        facecolor='none', edgecolor=LINE, linewidth=1.4, alpha=0.95, zorder=5))

    # Bottom goal
    ax.add_patch(mpatches.Rectangle(
        (goal_x, y0 - goal_d), goal_w, goal_d,
        facecolor='none', edgecolor=LINE, linewidth=1.4, alpha=0.95, zorder=5))

    # ---- Penalty spots ----
    pen_x = cx_
    pen_top = y0 + h - h * 0.115
    pen_bot = y0 + h * 0.115
    ax.plot(pen_x, pen_top, 'o', ms=2.5, color=LINE, zorder=5)
    ax.plot(pen_x, pen_bot, 'o', ms=2.5, color=LINE, zorder=5)

    # ---- Penalty arcs ----
    arc_r = r_circle * 1.1
    arc_top = Arc((pen_x, pen_top), arc_r*2, arc_r*2,
                  angle=0, theta1=200, theta2=340,
                  color=LINE, linewidth=0.8, alpha=0.8, zorder=5)
    arc_bot = Arc((pen_x, pen_bot), arc_r*2, arc_r*2,
                  angle=0, theta1=20, theta2=160,
                  color=LINE, linewidth=0.8, alpha=0.8, zorder=5)
    ax.add_patch(arc_top)
    ax.add_patch(arc_bot)

    # ---- Zone name badge ----
    sc = SEM_COLOR[sem]
    ax.add_patch(FancyBboxPatch(
        (x0 + 0.05, y0 + h - 0.30), w - 0.1, 0.26,
        boxstyle='round,pad=0.02',
        facecolor='#000000CC', edgecolor=sc, linewidth=0.8, zorder=8))
    title_clean = title.replace('\n', ' ')
    ax.text(x0 + w/2, y0 + h - 0.17, title_clean,
        color=WHITE, fontsize=6.5, fontweight='bold',
        ha='center', va='center', zorder=9,
        fontfamily='sans-serif')

    # ---- Semáforo dot ----
    ax.plot(x0 + 0.18, y0 + h - 0.18, 'o', ms=7, color=sc, zorder=9)

    # ---- Critical badge ----
    if is_critical:
        ax.text(x0 + w/2, y0 + h/2 - 0.05, '⚠ CRÍTICA',
            color=REDXL, fontsize=10, fontweight='bold',
            ha='center', va='center', zorder=10,
            fontfamily='monospace',
            bbox=dict(facecolor='#1a0000', edgecolor=REDL,
                      linewidth=1.5, pad=4, boxstyle='round,pad=0.3'))

    # ---- Foco markers ----
    if focos:
        for foco in focos:
            fx = x0 + foco['x'] * w
            fy = y0 + foco['y'] * h
            fr = foco['size'] * min(w, h)
            fc = foco['color']
            label = foco['label']

            # Outer glow
            ax.add_patch(mpatches.Circle((fx, fy), fr * 1.35,
                facecolor=fc, alpha=0.18, zorder=7))
            # Main circle
            ax.add_patch(mpatches.Circle((fx, fy), fr,
                facecolor=fc, alpha=0.80, edgecolor='white',
                linewidth=1.2, zorder=8))
            # Label inside circle
            lines = label.split('\n')
            if len(lines) == 2:
                ax.text(fx, fy + fr*0.18, lines[0],
                    color='black', fontsize=5.5, fontweight='bold',
                    ha='center', va='center', zorder=9,
                    fontfamily='monospace')
                ax.text(fx, fy - fr*0.28, lines[1],
                    color='black', fontsize=5.5, fontweight='bold',
                    ha='center', va='center', zorder=9,
                    fontfamily='monospace')
            else:
                ax.text(fx, fy, label,
                    color='black', fontsize=5.5, fontweight='bold',
                    ha='center', va='center', zorder=9,
                    fontfamily='monospace')

    # ---- Accion text bottom ----
    if accion:
        accion_clean = accion.replace('\n', ' · ')
        sc = SEM_COLOR[sem]
        ax.text(x0 + w/2, y0 - 0.22, accion_clean,
            color=sc, fontsize=6.0, fontweight='bold',
            ha='center', va='top', zorder=9,
            fontfamily='monospace',
            bbox=dict(facecolor=BG2+'DD', edgecolor=sc+'88',
                      linewidth=0.6, pad=2, boxstyle='round,pad=0.2'))


# ─── SETUP FIGURE ────────────────────────────────────────────────────────────
DPI = 200
FW, FH = 13.5, 34.5   # extendido para 13 zonas + panel calidad superficie
fig = plt.figure(figsize=(FW, FH), dpi=DPI, facecolor=BG)
fig.subplots_adjust(left=0.03, right=0.97, top=0.99, bottom=0.01, hspace=0.0)

gs = gridspec.GridSpec(
    10, 1,
    figure=fig,
    hspace=0.06,
    height_ratios=[1.6, 1.8, 4.5, 4.5, 2.0, 2.2, 7.0, 1.5, 2.0, 0.85],
)

# ══════════════════════════════════════════════════════════════════════════════
# 0 · HEADER
# ══════════════════════════════════════════════════════════════════════════════
ax_hdr = fig.add_subplot(gs[0])
ax_hdr.set_facecolor(BG)
ax_hdr.set_xlim(0, 1); ax_hdr.set_ylim(0, 1)
ax_hdr.axis('off')

ax_hdr.add_patch(mpatches.Rectangle((0, 0.93), 1, 0.07,
    transform=ax_hdr.transAxes, color=GOLD, zorder=2))

ax_hdr.text(0.5, 0.88, 'FARO PROTOCOL — FORTÍN INTELIGENTE',
    transform=ax_hdr.transAxes, color=GOLD,
    fontsize=17, fontweight='bold', ha='center', va='top',
    fontfamily='monospace')

ax_hdr.text(0.5, 0.70, 'VÉLEZ SARSFIELD · MAPA DE TRABAJO PARA EL CANCHERO',
    transform=ax_hdr.transAxes, color=WHITE,
    fontsize=12.5, fontweight='bold', ha='center', va='top',
    fontfamily='sans-serif')

ax_hdr.text(0.5, 0.52, f'Escaneo satelital: {SCAN_DATE}',
    transform=ax_hdr.transAxes, color=WDIM,
    fontsize=9, ha='center', va='top', fontfamily='monospace')

ax_hdr.text(0.5, 0.36, 'Lat −34.6379  ·  Lon −58.5288  ·  WGS-84',
    transform=ax_hdr.transAxes, color=WDIM,
    fontsize=8.5, ha='center', va='top', fontfamily='monospace')

ax_hdr.add_patch(FancyBboxPatch(
    (0.28, 0.02), 0.44, 0.22,
    boxstyle='round,pad=0.01',
    transform=ax_hdr.transAxes,
    facecolor=BG3, edgecolor=GOLD, linewidth=1.2, zorder=3))
ax_hdr.text(0.5, 0.13, '  MAPA SIMPLIFICADO · USO OPERATIVO · MAYO 2026  ',
    transform=ax_hdr.transAxes, color=GOLD,
    fontsize=8.5, fontweight='bold', ha='center', va='bottom',
    fontfamily='monospace', zorder=4)

for lx, ltxt in [(0.06, 'ESA'), (0.14, 'COP'), (0.22, 'NASA')]:
    ax_hdr.add_patch(FancyBboxPatch(
        (lx - 0.025, 0.03), 0.065, 0.22,
        boxstyle='round,pad=0.01', transform=ax_hdr.transAxes,
        facecolor=BG3, edgecolor=GOLD+'88', linewidth=0.8, zorder=3))
    ax_hdr.text(lx + 0.008, 0.14, ltxt,
        transform=ax_hdr.transAxes, color=GOLD,
        fontsize=7, fontweight='bold', ha='center', va='center',
        fontfamily='monospace', zorder=4)
for lx, ltxt in [(0.78, 'ESA'), (0.86, 'COP'), (0.94, 'NASA')]:
    ax_hdr.add_patch(FancyBboxPatch(
        (lx - 0.025, 0.03), 0.065, 0.22,
        boxstyle='round,pad=0.01', transform=ax_hdr.transAxes,
        facecolor=BG3, edgecolor=GOLD+'88', linewidth=0.8, zorder=3))
    ax_hdr.text(lx + 0.008, 0.14, ltxt,
        transform=ax_hdr.transAxes, color=GOLD,
        fontsize=7, fontweight='bold', ha='center', va='center',
        fontfamily='monospace', zorder=4)

ax_hdr.axhline(0.0, color=GOLD, linewidth=1.5, xmin=0, xmax=1)


# ══════════════════════════════════════════════════════════════════════════════
# 1 · SEMÁFORO EJECUTIVO
# ══════════════════════════════════════════════════════════════════════════════
ax_sem = fig.add_subplot(gs[1])
ax_sem.set_facecolor(BG2)
ax_sem.set_xlim(0, 1); ax_sem.set_ylim(0, 1)
ax_sem.axis('off')

ax_sem.text(0.5, 0.98, 'PANEL 0 · SEMÁFORO EJECUTIVO — LECTURA EN 3 SEGUNDOS',
    transform=ax_sem.transAxes, color=GOLD,
    fontsize=8.5, fontweight='bold', ha='center', va='top',
    fontfamily='monospace')

ax_sem.axhline(0.92, color=GOLD+'44', linewidth=0.5)

_n_sem = len(ZONES)
xs = np.linspace(0.05, 0.95, _n_sem)
bw = max(0.055, 0.85 / _n_sem - 0.008)
_sem_name_fs  = max(5.0, 7.5 - max(0, _n_sem - 5) * 0.25)
_sem_est_fs   = max(6.0, 9.0 - max(0, _n_sem - 5) * 0.3)
_sem_acc_fs   = max(4.5, 7.0 - max(0, _n_sem - 5) * 0.25)
_sem_dot_ms   = max(12, 22 - max(0, _n_sem - 5) * 1.4)
_sem_glow_ms  = int(_sem_dot_ms * 1.35)

for i, z in enumerate(ZONES):
    cx = xs[i]
    sc = SEM_COLOR[z['sem']]
    bx = cx - bw/2 + 0.005
    by = 0.06
    bh = 0.84
    border_c = sc + 'AA' if z['sem'] == 'rojo' else sc + '55'
    lw = 2.0 if z['sem'] == 'rojo' else 0.9
    ax_sem.add_patch(FancyBboxPatch(
        (bx, by), bw - 0.01, bh,
        boxstyle='round,pad=0.008',
        transform=ax_sem.transAxes,
        facecolor=BG3 if z['sem'] != 'rojo' else '#1a0505',
        edgecolor=sc, linewidth=lw, zorder=2))

    ax_sem.text(cx + 0.005, by + bh - 0.04, z['name'],
        transform=ax_sem.transAxes,
        color=WHITE, fontsize=_sem_name_fs, fontweight='bold',
        ha='center', va='top', fontfamily='sans-serif', zorder=3)

    dot_y = by + bh - 0.32
    ax_sem.plot(cx + 0.005, dot_y, 'o', ms=_sem_dot_ms, color=sc,
        transform=ax_sem.transAxes, zorder=4, clip_on=False)
    ax_sem.plot(cx + 0.005, dot_y, 'o', ms=_sem_glow_ms, color=sc, alpha=0.12,
        transform=ax_sem.transAxes, zorder=3, clip_on=False)

    ax_sem.text(cx + 0.005, dot_y - 0.145, z['estado'],
        transform=ax_sem.transAxes,
        color=sc, fontsize=_sem_est_fs, fontweight='bold',
        ha='center', va='top', fontfamily='monospace', zorder=3)

    ax_sem.text(cx + 0.005, by + 0.18, z['accion'],
        transform=ax_sem.transAxes,
        color=WHITE if z['sem'] != 'rojo' else REDXL,
        fontsize=_sem_acc_fs, ha='center', va='bottom',
        fontfamily='monospace', zorder=3,
        linespacing=1.35)

ax_sem.axhline(0.0, color=GOLD+'44', linewidth=0.5)


# ══════════════════════════════════════════════════════════════════════════════
# 2 · PANEL 1 — MAPA DE PRESCRIPCIÓN (planos reales)
# ══════════════════════════════════════════════════════════════════════════════
ax_map1 = fig.add_subplot(gs[2])
ax_map1.set_facecolor(BG2)
ax_map1.set_xlim(0, 10); ax_map1.set_ylim(0, 10)
ax_map1.axis('off')

ax_map1.text(5, 9.85, 'PANEL 1 · MAPA DE PRESCRIPCIÓN — DÓNDE TRABAJAR ESTA SEMANA',
    color=GOLD, fontsize=8.5, fontweight='bold',
    ha='center', va='top', fontfamily='monospace')
ax_map1.text(5, 9.65, '[ROJO] = trabajar HOY      [AMARILLO] = trabajar esta semana',
    color=WHITE, fontsize=8, ha='center', va='top', fontfamily='monospace')

# Amalfitani — grande izquierda
draw_soccer_field(ax_map1,
    x0=0.15, y0=0.35, w=2.6, h=9.0,
    focos=ZONES[0]['focos'],
    is_critical=ZONES[0]['sem'] == 'rojo',
    title=ZONES[0]['name'],
    sem=ZONES[0]['sem'],
    accion=ZONES[0]['map_accion'])

# 12 VO canchas en grilla 3×4
_p1_cols = [3.1, 5.3, 7.5]   # x0 de cada columna
_p1_rows = [7.2, 5.05, 2.9, 0.75]  # y0 de cada fila (top → bottom)
_p1_fw, _p1_fh = 1.95, 1.95  # tamaño de cada campo pequeño

for _idx, _z in enumerate(ZONES[1:13]):
    _col = _idx % 3
    _row = _idx // 3
    _fx0 = _p1_cols[_col]
    _fy0 = _p1_rows[_row]
    draw_soccer_field(ax_map1,
        x0=_fx0, y0=_fy0, w=_p1_fw, h=_p1_fh,
        focos=_z['focos'],
        is_critical=_z['sem'] == 'rojo',
        title=f'{_z["name"]} ⚠' if _z['sem'] == 'rojo' else _z['name'],
        sem=_z['sem'],
        accion=_z['map_accion'])

# Leyenda Panel 1 — una línea de texto abajo
ax_map1.text(5.0, 0.10,
    '●VERDE=OK·trabajar Semana 3   ●AMARILLO=trabajar esta semana   ●ROJO=intervención HOY',
    color=WHITE, fontsize=6.0, ha='center', va='top', fontfamily='monospace')


# ══════════════════════════════════════════════════════════════════════════════
# 3 · PANEL 2 — MAPA DE ALERTAS (planos reales con focos numerados)
# ══════════════════════════════════════════════════════════════════════════════
ax_map2 = fig.add_subplot(gs[3])
ax_map2.set_facecolor(BG2)
ax_map2.set_xlim(0, 10); ax_map2.set_ylim(0, 10)
ax_map2.axis('off')

ax_map2.text(5, 9.85, 'PANEL 2 · MAPA DE ALERTAS — FOCOS EXACTOS POR CANCHA',
    color=GOLD, fontsize=8.5, fontweight='bold',
    ha='center', va='top', fontfamily='monospace')
ax_map2.text(5, 9.65,
    '1=HONGO  2=AGUA/DRENAJE  3=MALEZA  4=RESEMBRAR  — cada número = lugar exacto',
    color=WHITE, fontsize=7.5, ha='center', va='top', fontfamily='monospace')

# alert_focos: para zonas sin datos reales, focos genéricos por semáforo
def _default_alert_focos(z):
    sem = z['sem']
    if sem == 'verde':
        return [{'x': 0.5, 'y': 0.88, 'num': '1', 'color': YELL, 'type': 'AERIF.'},
                {'x': 0.5, 'y': 0.12, 'num': '2', 'color': YELL, 'type': 'AERIF.'}]
    elif sem == 'amarillo':
        return [{'x': 0.5, 'y': 0.70, 'num': '1', 'color': YELL, 'type': 'ATENC.'},
                {'x': 0.5, 'y': 0.30, 'num': '2', 'color': YELL, 'type': 'REGAR'}]
    else:
        return [{'x': 0.30, 'y': 0.75, 'num': '1', 'color': REDL, 'type': 'URGENTE'},
                {'x': 0.70, 'y': 0.40, 'num': '2', 'color': REDL, 'type': 'REPARAR'},
                {'x': 0.50, 'y': 0.55, 'num': '3', 'color': YELL, 'type': 'MALEZA'}]

def draw_numbered_focos(ax, x0, y0, w, h, focos_num):
    for f in focos_num:
        fx = x0 + f['x'] * w
        fy = y0 + f['y'] * h
        fc = f['color']
        num = f['num']
        typ = f['type']
        r = min(w, h) * 0.09

        ax.add_patch(mpatches.Circle((fx, fy), r * 1.4,
            facecolor=fc, alpha=0.20, zorder=7))
        ax.add_patch(mpatches.Circle((fx, fy), r,
            facecolor=fc, alpha=0.85, edgecolor='white',
            linewidth=1.2, zorder=8))
        ax.text(fx, fy + r * 0.15, num,
            color='black', fontsize=7.5, fontweight='bold',
            ha='center', va='center', zorder=9,
            fontfamily='monospace')
        ax.text(fx, fy - r * 1.8, typ,
            color=fc, fontsize=5.5, fontweight='bold',
            ha='center', va='center', zorder=9,
            fontfamily='monospace',
            bbox=dict(facecolor=BG+'CC', edgecolor=fc+'88',
                      linewidth=0.5, pad=1.5, boxstyle='round,pad=0.15'))

# Amalfitani
draw_soccer_field(ax_map2,
    x0=0.15, y0=0.35, w=2.6, h=9.0,
    focos=None, is_critical=ZONES[0]['sem'] == 'rojo',
    title=ZONES[0]['name'], sem=ZONES[0]['sem'], accion='')
draw_numbered_focos(ax_map2, 0.15, 0.35, 2.6, 9.0, _default_alert_focos(ZONES[0]))

# 12 VO canchas
_p2_cols = [3.1, 5.3, 7.5]
_p2_rows = [7.2, 5.05, 2.9, 0.75]
_p2_fw, _p2_fh = 1.95, 1.95

for _idx, _z in enumerate(ZONES[1:13]):
    _col = _idx % 3
    _row = _idx // 3
    _fx0 = _p2_cols[_col]
    _fy0 = _p2_rows[_row]
    draw_soccer_field(ax_map2,
        x0=_fx0, y0=_fy0, w=_p2_fw, h=_p2_fh,
        focos=None,
        is_critical=_z['sem'] == 'rojo',
        title=f'{_z["name"]} ⚠' if _z['sem'] == 'rojo' else _z['name'],
        sem=_z['sem'], accion='')
    draw_numbered_focos(ax_map2, _fx0, _fy0, _p2_fw, _p2_fh, _default_alert_focos(_z))

# Leyenda Panel 2 — una línea de texto abajo
ax_map2.text(5.0, 0.10,
    '●VERDE=OK·trabajar Semana 3   ●AMARILLO=trabajar esta semana   ●ROJO=intervención HOY',
    color=WHITE, fontsize=6.0, ha='center', va='top', fontfamily='monospace')


# ══════════════════════════════════════════════════════════════════════════════
# 4 · ÍNDICE FUSIÓN FARO (idéntico al FINAL)
# ══════════════════════════════════════════════════════════════════════════════
from matplotlib.colors import LinearSegmentedColormap as LSC

def ndvi_colormap():
    colors = [(0.55, 0.0, 0.0), (0.85, 0.35, 0.0),
              (0.95, 0.75, 0.1), (0.4, 0.75, 0.2),
              (0.05, 0.45, 0.05)]
    return LSC.from_list('ndvi', colors)

ax_fus = fig.add_subplot(gs[4])
ax_fus.set_facecolor(BG2)
ax_fus.set_xlim(0, 1); ax_fus.set_ylim(0, 1)
ax_fus.axis('off')

ax_fus.text(0.5, 0.98, 'PANEL 3 · ÍNDICE FUSIÓN FARO — PUNTUACIÓN INTEGRADA POR ZONA',
    transform=ax_fus.transAxes,
    color=GOLD, fontsize=8.5, fontweight='bold',
    ha='center', va='top', fontfamily='monospace')
ax_fus.axhline(0.92, color=GOLD+"44", linewidth=0.5)

scale_y = 0.10
ax_fus.add_patch(mpatches.Rectangle(
    (0.07, scale_y), 0.86, 0.06,
    transform=ax_fus.transAxes,
    facecolor='none', edgecolor=GOLD+'44', linewidth=0.6))
grad_arr = np.linspace(0, 1, 200)[np.newaxis, :]
ax_fus_img = ax_fus.inset_axes([0.07, scale_y, 0.86, 0.06])
ax_fus_img.imshow(grad_arr, cmap=ndvi_colormap(), aspect='auto', vmin=0, vmax=1)
ax_fus_img.axis('off')
for val, lbl in [(0, '0\nCrítico'), (50, '50\nNormal'), (100, '100\nÓptimo')]:
    sx = 0.07 + val/100 * 0.86
    ax_fus.plot([sx, sx], [scale_y - 0.01, scale_y + 0.07],
        color=WHITE, linewidth=0.8, transform=ax_fus.transAxes, zorder=5)
    ax_fus.text(sx, scale_y - 0.03, lbl,
        transform=ax_fus.transAxes,
        color=WDIM, fontsize=7, ha='center', va='top',
        fontfamily='monospace', linespacing=1.1)

# Sort zones by fusion score so arrows never cross (lowest score leftmost)
_zones_p3 = sorted(range(len(ZONES)), key=lambda i: ZONES[i]['fusion'])
xs5 = np.linspace(0.12, 0.88, len(ZONES))
_n_fus = len(ZONES)
_fus_score_fs = max(9, 22 - max(0, _n_fus - 5) * 1.6)
_fus_name_fs  = max(5.0, 8.0 - max(0, _n_fus - 5) * 0.25)
_fus_dot_ms   = max(9, 14 - max(0, _n_fus - 5) * 0.6)
for pos_i, z_i in enumerate(_zones_p3):
    z  = ZONES[z_i]
    cx = xs5[pos_i]
    sc = SEM_COLOR[z['sem']]
    fv = z['fusion']

    ax_fus.text(cx, 0.88, z['name'],
        transform=ax_fus.transAxes,
        color=WHITE, fontsize=_fus_name_fs, fontweight='bold',
        ha='center', va='top', fontfamily='sans-serif')

    ax_fus.text(cx, 0.63, f'{fv}',
        transform=ax_fus.transAxes,
        color=sc, fontsize=_fus_score_fs, fontweight='bold',
        ha='center', va='center', fontfamily='monospace')

    ax_fus.plot(cx, 0.42, 'o', ms=_fus_dot_ms, color=sc,
        transform=ax_fus.transAxes, zorder=4)
    ax_fus.plot(cx, 0.42, 'o', ms=int(_fus_dot_ms*1.57), color=sc, alpha=0.14,
        transform=ax_fus.transAxes, zorder=3)

    ptr_x = 0.07 + fv/100 * 0.86
    ax_fus.annotate('',
        xy=(ptr_x, scale_y + 0.06),
        xytext=(cx, 0.30),
        xycoords='axes fraction', textcoords='axes fraction',
        arrowprops=dict(arrowstyle='->', color=sc+'99', lw=0.8))


# ══════════════════════════════════════════════════════════════════════════════
# 5 · InSAR (idéntico al FINAL)
# ══════════════════════════════════════════════════════════════════════════════
ax_ins = fig.add_subplot(gs[5])
ax_ins.set_facecolor(BG3)
for sp in ax_ins.spines.values():
    sp.set_edgecolor(GOLD+'44'); sp.set_linewidth(0.6)

ax_ins.text(0.5, 1.02, 'PANEL InSAR · DEFORMACIÓN ESTRUCTURAL — TRIBUNAS',
    transform=ax_ins.transAxes,
    color=GOLD, fontsize=8.5, fontweight='bold',
    ha='center', va='bottom', fontfamily='monospace')

# 1.4: use real InSAR sector values if available; else hardcoded example
if _insar_sectors is not None:
    ax_ins.text(0.98, 1.02, '● DATOS REALES',
        transform=ax_ins.transAxes, color=GRNL, fontsize=6.5,
        fontweight='bold', ha='right', va='bottom', fontfamily='monospace')
else:
    ax_ins.text(0.98, 1.02, '● DATOS DE EJEMPLO — sin InSAR reciente',
        transform=ax_ins.transAxes, color=YELL, fontsize=6.5,
        fontweight='bold', ha='right', va='bottom', fontfamily='monospace')
if _insar_sectors is not None:
    _ins_pairs = [('Estadio', _insar_sectors.get('estadio')),
                  ('Poli',    _insar_sectors.get('poli')),
                  ('Sede',    _insar_sectors.get('sede')),
                  ('Piletas', _insar_sectors.get('piletas'))]
    _ins_valid = [(lbl, v) for lbl, v in _ins_pairs if v is not None]
    labels_ins = [lbl for lbl, v in _ins_valid] if _ins_valid else ['Estadio', 'Poli', 'Sede', 'Piletas']
    vals_ins   = [v   for lbl, v in _ins_valid] if _ins_valid else [0.0, 0.0, 0.0, 0.0]
else:
    labels_ins = ['Norte', 'Sur', 'Este', 'Oeste']
    vals_ins   = [0.85, 1.20, 0.60, 2.80]
colors_ins = [REDL if v >= 2.0 else (YELL if v >= 1.0 else GRNL) for v in vals_ins]
x_pos_ins  = np.arange(len(labels_ins))

bars = ax_ins.bar(x_pos_ins, vals_ins, color=colors_ins, edgecolor=BG,
                  linewidth=0.8, zorder=3, width=0.55)

ax_ins.axhline(2.0, color=REDL, linewidth=1.8, linestyle='--', zorder=4)
_n_ins   = len(vals_ins)
ax_ins.text(_n_ins - 0.45, 2.08, '← umbral 2mm', color=REDL,
    fontsize=8.5, fontweight='bold', va='bottom', fontfamily='monospace')

for bar_, val in zip(bars, vals_ins):
    clr = REDXL if val >= 2.0 else WHITE
    ax_ins.text(bar_.get_x() + bar_.get_width()/2, val + 0.05,
        f'{val:.2f}mm', color=clr, fontsize=9, fontweight='bold',
        ha='center', va='bottom', fontfamily='monospace')

_max_ins_val = max(vals_ins) if vals_ins else 0.0
_max_ins_idx = int(np.argmax(vals_ins)) if vals_ins else 0
if _max_ins_val >= 2.0:
    _ann_x = float(x_pos_ins[_max_ins_idx])
    ax_ins.annotate('⚠ INSPECCIÓN\nESTRUCTURAL\nRECOMENDADA',
        xy=(_ann_x, _max_ins_val),
        xytext=(max(0.2, _ann_x - 1.2), _max_ins_val + 0.7),
        color=REDXL, fontsize=8, fontweight='bold', fontfamily='monospace',
        arrowprops=dict(arrowstyle='->', color=REDXL, lw=1.5),
        bbox=dict(facecolor='#1a0000', edgecolor=REDL, linewidth=1.2,
                  pad=4, boxstyle='round,pad=0.3'))

_ylim_top = max(4.8, _max_ins_val * 1.5 + 0.5)
ax_ins.set_xlim(-0.6, _n_ins - 0.2); ax_ins.set_ylim(0, _ylim_top)
ax_ins.set_xticks(x_pos_ins)
ax_ins.set_xticklabels(labels_ins, color=WHITE, fontsize=10, fontfamily='monospace')
ax_ins.set_ylabel('Desplazamiento (mm)', color=WDIM, fontsize=8, fontfamily='monospace')
ax_ins.tick_params(colors=WDIM, labelsize=8)
ax_ins.yaxis.label.set_color(WDIM)
ax_ins.set_facecolor(BG3)
ax_ins.yaxis.set_tick_params(labelcolor=WDIM)
for spine in ax_ins.spines.values():
    spine.set_edgecolor(GOLD+'33')
ax_ins.text(-0.58, 0.2, '0mm\nEstable', color=GRNL,
    fontsize=7, fontfamily='monospace', va='bottom', linespacing=1.2)
ax_ins.text(-0.58, 2.05, '2mm\nUmbral', color=YELL,
    fontsize=7, fontfamily='monospace', va='bottom', linespacing=1.2)
ax_ins.text(-0.58, 4.4, '5mm\nCrítico', color=REDL,
    fontsize=7, fontfamily='monospace', va='top', linespacing=1.2)


# ══════════════════════════════════════════════════════════════════════════════
# 6 · PANEL 4 — MAPA SATELITAL NDVI POR CANCHA
#   Reemplaza tabla de texto: heatmap pixel-a-pixel (Sentinel-2 10 m/px, crudo)
#   + fila compacta de métricas reales que el mapa no muestra.
#   Decisiones de diseño:
#     · interpolation='none' — píxeles reales sin suavizado
#     · 'SIN IMAGEN' explícito cuando ndvi_2d es None (no relleno silencioso)
#     · Drenaje marcado 'PENDIENTE' hasta dato InSAR real (ver línea 294)
# ══════════════════════════════════════════════════════════════════════════════
ax_sat = fig.add_subplot(gs[6])
ax_sat.set_facecolor(BG2)
ax_sat.set_xlim(0, 1); ax_sat.set_ylim(0, 1)
ax_sat.axis('off')

ax_sat.text(0.5, 0.995,
    'PANEL 4 · MAPA SATELITAL NDVI — PÍXELES REALES SENTINEL-2',
    transform=ax_sat.transAxes,
    color=GOLD, fontsize=8.5, fontweight='bold',
    ha='center', va='top', fontfamily='monospace')
ax_sat.text(0.5, 0.970,
    '10 m/pixel · crudo sin suavizado · círculos = focos exactos del satélite'
    '  |  rojo=0.10  amarillo=0.35  verde=0.65+',
    transform=ax_sat.transAxes,
    color=WDIM, fontsize=6.0,
    ha='center', va='top', fontfamily='monospace')
ax_sat.plot([0.01, 0.99], [0.950, 0.950], color=GOLD + '33', linewidth=0.5,
    transform=ax_sat.transAxes)

# ── 5 heatmaps de canchas ─────────────────────────────────────────────────
_n_hm   = len(ZONES)
_hm_gap = 0.014
_hm_mx  = 0.018   # margen izquierdo/derecho
_hm_w   = (1.0 - 2 * _hm_mx - (_n_hm - 1) * _hm_gap) / _n_hm
_hm_y0  = 0.550   # base del área de heatmaps (arriba queda la fila compacta)
_hm_y1  = 0.945   # tope del área de heatmaps

import numpy as _np_sat

for _hi, _z in enumerate(ZONES):
    _hx0 = _hm_mx + _hi * (_hm_w + _hm_gap)
    _ax_hm = ax_sat.inset_axes([_hx0, _hm_y0, _hm_w, _hm_y1 - _hm_y0])
    _ax_hm.set_facecolor(BG3)

    _sem     = _z['sem']
    _sc      = SEM_COLOR[_sem]
    _ndvi2d  = _z.get('ndvi_2d')   # None → sin imagen; lista-de-listas → dato real

    if _ndvi2d is None or len(_ndvi2d) == 0:
        # ── SIN IMAGEN — nublado / sin dato ──────────────────────────────
        _ax_hm.set_xlim(0, 1); _ax_hm.set_ylim(0, 1)
        _ax_hm.set_xticks([]); _ax_hm.set_yticks([])
        _ax_hm.add_patch(mpatches.Rectangle(
            (0, 0), 1, 1,
            facecolor=BG3, edgecolor='none',
            hatch='////', zorder=1,
            transform=_ax_hm.transAxes))
        _ax_hm.text(0.5, 0.62, 'SIN IMAGEN',
            transform=_ax_hm.transAxes,
            color=WDIM, fontsize=7.5, fontweight='bold',
            ha='center', va='center', fontfamily='monospace', zorder=5)
        _ax_hm.text(0.5, 0.44, 'nublado / sin dato',
            transform=_ax_hm.transAxes,
            color=WDIM, fontsize=6.0,
            ha='center', va='center', fontfamily='monospace', zorder=5)
        _last_ndvi = _z.get('ndvi')
        if isinstance(_last_ndvi, (int, float)):
            _ax_hm.text(0.5, 0.27, f'ult. NDVI: {_last_ndvi:.2f}',
                transform=_ax_hm.transAxes,
                color=WDIM + '99', fontsize=6.0,
                ha='center', va='center', fontfamily='monospace', zorder=5)
    else:
        # ── Heatmap píxeles reales, CRUDO ────────────────────────────────
        _arr  = _np_sat.array(_ndvi2d, dtype=float)
        _rows, _cols = _arr.shape

        _ax_hm.imshow(
            _arr, cmap=ndvi_colormap(), vmin=0, vmax=1,
            origin='upper',
            extent=[0, _cols, 0, _rows],
            aspect='auto',
            interpolation='none',   # píxeles crudos, sin suavizado
            zorder=1,
        )

        # Field lines overlay — semitransparentes para no tapar el heatmap
        _ax_hm.axhline(_rows / 2.0, color=LINE, linewidth=0.8, alpha=0.40, zorder=3)
        _cx_f, _cy_f = _cols / 2.0, _rows / 2.0
        _ax_hm.add_patch(plt.Circle(
            (_cx_f, _cy_f), min(_cols, _rows) * 0.18,
            fill=False, edgecolor=LINE, linewidth=0.75, alpha=0.40, zorder=3))
        _ax_hm.plot(_cx_f, _cy_f, 'o', ms=1.5, color=LINE, alpha=0.45, zorder=3)
        _pa_w_f = _cols * 0.62; _pa_h_f = _rows * 0.17
        _pa_x_f = (_cols - _pa_w_f) / 2.0
        _ax_hm.add_patch(mpatches.Rectangle(
            (_pa_x_f, 0), _pa_w_f, _pa_h_f,
            fill=False, edgecolor=LINE, linewidth=0.6, alpha=0.35, zorder=3))
        _ax_hm.add_patch(mpatches.Rectangle(
            (_pa_x_f, _rows - _pa_h_f), _pa_w_f, _pa_h_f,
            fill=False, edgecolor=LINE, linewidth=0.6, alpha=0.35, zorder=3))

        # Focos reales sobre el heatmap
        for _f in (_z.get('focos') or []):
            _fx_p = _f['x'] * _cols
            _fy_p = (1.0 - _f['y']) * _rows   # origin='upper' → invertir Y
            _ndvi_f = _f.get('ndvi')
            _fc_p   = REDL if (isinstance(_ndvi_f, float) and _ndvi_f < 0.20) else YELL
            _r_p    = min(_cols, _rows) * 0.085
            _ax_hm.add_patch(plt.Circle((_fx_p, _fy_p), _r_p * 1.4,
                facecolor=_fc_p, alpha=0.18, zorder=4))
            _ax_hm.add_patch(plt.Circle((_fx_p, _fy_p), _r_p,
                facecolor=_fc_p, alpha=0.88, edgecolor='white',
                linewidth=0.9, zorder=5))
            if isinstance(_ndvi_f, float):
                _ax_hm.text(_fx_p, _fy_p, f'{_ndvi_f:.2f}',
                    color='black', fontsize=4.5, fontweight='bold',
                    ha='center', va='center', zorder=6,
                    fontfamily='monospace')

        _ax_hm.set_xlim(0, _cols); _ax_hm.set_ylim(0, _rows)
        _ax_hm.set_xticks([]); _ax_hm.set_yticks([])

    # Borde color semáforo (misma convención que los planos de campo)
    for _sp in _ax_hm.spines.values():
        _sp.set_edgecolor(_sc)
        _sp.set_linewidth(2.0 if _sem == 'rojo' else 1.0)

    # Nombre de cancha encima del heatmap
    ax_sat.text(
        _hx0 + _hm_w / 2, _hm_y1 + 0.006,
        _z['name'].replace('\n', ' '),
        transform=ax_sat.transAxes,
        color=WHITE, fontsize=5.5, fontweight='bold',
        ha='center', va='bottom', fontfamily='monospace')

    # NDVI + CCCI debajo del heatmap
    _ndvi_disp = _z.get('ndvi', 0.0)
    _ndvi_c    = (REDL if _ndvi_disp < 0.30 else
                  YELL if _ndvi_disp < 0.50 else GRNL)
    ax_sat.text(
        _hx0 + _hm_w / 2, _hm_y0 - 0.005,
        f'NDVI {_ndvi_disp:.2f}',
        transform=ax_sat.transAxes,
        color=_ndvi_c, fontsize=5.5, fontweight='bold',
        ha='center', va='top', fontfamily='monospace')
    ax_sat.text(
        _hx0 + _hm_w / 2, _hm_y0 - 0.035,
        _z.get('ccci', 'sin hist'),
        transform=ax_sat.transAxes,
        color=WDIM, fontsize=4.8,
        ha='center', va='top', fontfamily='monospace')

# ── Separador + encabezado fila compacta ─────────────────────────────────
_tbl_sep   = _hm_y0 - 0.065
_tbl_hdr_s = _tbl_sep - 0.008
_tbl_hdr_c = _tbl_sep - 0.038
_tbl_row0  = _tbl_sep - 0.060
_tbl_row_h = max(0.025, (_tbl_row0 - 0.012) / max(len(ZONES), 1))
_row_ys    = [_tbl_row0 - i * _tbl_row_h for i in range(len(ZONES))]

ax_sat.plot([0.01, 0.99], [_tbl_sep, _tbl_sep], color=GOLD + '33', linewidth=0.5,
    transform=ax_sat.transAxes)
ax_sat.text(0.012, _tbl_hdr_s,
    'DATOS REALES COMPLEMENTARIOS — lo que el mapa no muestra:',
    transform=ax_sat.transAxes,
    color=GOLD, fontsize=6.0, fontweight='bold',
    va='top', fontfamily='monospace')

# ── Headers de columnas ───────────────────────────────────────────────────
_ct_labels = ['ZONA', 'RESIEMBRA', 'RIEGO mm', 'HONGOS', 'COMPACT.', 'DRENAJE', 'TIMELINE']
_ct_xs     = [0.012,   0.155,       0.270,      0.380,    0.510,      0.640,      0.800]

for _lbl, _lx in zip(_ct_labels, _ct_xs):
    ax_sat.text(_lx, _tbl_hdr_c, _lbl,
        transform=ax_sat.transAxes,
        color=GOLD, fontsize=5.5, fontweight='bold',
        va='top', fontfamily='monospace')

# ── Datos por zona ────────────────────────────────────────────────────────
_tl_map = {'verde': 'Semana 3', 'amarillo': 'Esta semana', 'rojo': 'HOY — URGENTE'}

for _z, _ry in zip(ZONES, _row_ys):
    _sem = _z['sem']
    _sc  = SEM_COLOR[_sem]
    _bg_row = ('#1a0505' if _sem == 'rojo' else
               '#181200' if _sem == 'amarillo' else BG3)
    ax_sat.add_patch(mpatches.Rectangle(
        (0.010, _ry - _tbl_row_h * 0.35), 0.980, _tbl_row_h * 0.85,
        facecolor=_bg_row, edgecolor=GOLD + '22', linewidth=0.4,
        transform=ax_sat.transAxes, zorder=1))
    ax_sat.plot(0.025, _ry + _tbl_row_h * 0.15, 'o', ms=4.5, color=_sc,
        transform=ax_sat.transAxes, zorder=3, clip_on=False)

    _res = _z.get('resiembra', '—')
    _res_c = (REDL  if 'Total'   in _res else
              YELL  if 'Parcial' in _res else GRNL)
    _hon = str(_z.get('hongos', '—'))
    _hon_c = (REDL  if 'Activos' in _hon else
              YELL  if 'rev' in _hon.lower() or 'Prev' in _hon else GRNL)
    _cmp = str(_z.get('compact', '—'))
    _cmp_c = (REDL  if 'Cr' in _cmp else
              YELL  if 'Alta' in _cmp else GRNL)
    _drn   = str(_z.get('drenaje', '—'))
    _drn_c = (REDL  if _drn.startswith('Crítico') else
              YELL  if _drn.startswith('Regular') else
              GRNL  if _drn.startswith('OK')      else WDIM)
    _tl    = _tl_map.get(_sem, 'Esta semana')
    _tl_c  = (REDL if _sem == 'rojo' else YELL if _sem == 'amarillo' else GRNL)

    _row_vals = [
        (_ct_xs[0], _z['name'].replace('\n', ' '),  WHITE),
        (_ct_xs[1], _res,                           _res_c),
        (_ct_xs[2], str(_z.get('riego', '—')),      WHITE),
        (_ct_xs[3], _hon,                           _hon_c),
        (_ct_xs[4], _cmp,                           _cmp_c),
        (_ct_xs[5], _drn,                           _drn_c),
        (_ct_xs[6], _tl,                            _tl_c),
    ]
    for (_rx, _rv, _rc) in _row_vals:
        ax_sat.text(_rx, _ry + _tbl_row_h * 0.15, _rv,
            transform=ax_sat.transAxes,
            color=_rc, fontsize=4.8, va='center',
            fontfamily='monospace', zorder=3)

ax_sat.text(0.012, 0.004,
    'Drenaje Amalfitani: planos C&G reales (14 líneas DRENAFLEX 118mm + 2 colectores 218mm)'
    '  ·  VO: θ_soil Van Genuchten (SAR=Sentinel-1 / proxy=Open-Meteo)',
    transform=ax_sat.transAxes,
    color=WDIM, fontsize=4.8, va='bottom',
    fontfamily='monospace')


# ══════════════════════════════════════════════════════════════════════════════
# 7 · COMPARATIVA
# ══════════════════════════════════════════════════════════════════════════════
ax_cmp = fig.add_subplot(gs[7])
ax_cmp.set_facecolor(BG2)
ax_cmp.set_xlim(0, 1); ax_cmp.set_ylim(0, 1)
ax_cmp.axis('off')

ax_cmp.text(0.5, 0.97, 'COMPARATIVA · TENDENCIA vs. ESCANEO ANTERIOR',
    transform=ax_cmp.transAxes,
    color=GOLD, fontsize=8.5, fontweight='bold',
    ha='center', va='top', fontfamily='monospace')

# 1.3: use real score_prev from ZONES if available; else hardcoded fallback
if ZONES and all('score_prev' in z for z in ZONES):
    comp_data = [
        (z['name'], z['fusion'], z['score_prev'], z['comparativa'], SEM_COLOR[z['sem']])
        for z in ZONES
    ]
else:
    comp_data = [
        ('Amalfitani',  80.0, 78.5, '→', GRNL),
        ('1FA',         65.0, 68.0, '↓', YELL),
        ('2FA',         58.0, 55.0, '↑', YELL),
        ('3FA',         72.0, 70.0, '→', YELL),
        ('4FA',         45.0, 51.0, '↓', YELL),
        ('5FA',         60.0, 58.0, '↑', YELL),
        ('6FA',         55.0, 60.0, '↓', YELL),
        ('7FA',         68.0, 65.0, '↑', YELL),
        ('8FA',         74.0, 72.0, '→', YELL),
        ('9FA',         82.0, 80.0, '→', GRNL),
        ('10FA',        78.0, 75.0, '↑', GRNL),
        ('1FP',         50.0, 55.0, '↓', YELL),
        ('2FP',         85.0, 83.0, '↑', GRNL),
    ]
_n_cmp = len(comp_data)
_cmp_name_fs  = max(5.0, 7.5 - max(0, _n_cmp - 5) * 0.25)
_cmp_arrow_fs = max(12, 22 - max(0, _n_cmp - 5) * 1.4)
_cmp_score_fs = max(7, 11 - max(0, _n_cmp - 5) * 0.5)
_cmp_prev_fs  = max(5.0, 7.5 - max(0, _n_cmp - 5) * 0.25)
xs5c = np.linspace(0.05, 0.95, len(comp_data))
for i, (nm, cur, prev, arrow, clr) in enumerate(comp_data):
    cx = xs5c[i]
    ax_cmp.text(cx, 0.85, nm,
        transform=ax_cmp.transAxes,
        color=WHITE, fontsize=_cmp_name_fs, fontweight='bold',
        ha='center', va='top', fontfamily='sans-serif')
    ax_cmp.text(cx, 0.60, arrow,
        transform=ax_cmp.transAxes,
        color=clr, fontsize=_cmp_arrow_fs, fontweight='bold',
        ha='center', va='center')
    ax_cmp.text(cx, 0.38, f'{cur:.1f}',
        transform=ax_cmp.transAxes,
        color=clr, fontsize=_cmp_score_fs, fontweight='bold',
        ha='center', va='center', fontfamily='monospace')
    ax_cmp.text(cx, 0.20, f'prev: {prev:.1f}',
        transform=ax_cmp.transAxes,
        color=WDIM, fontsize=_cmp_prev_fs,
        ha='center', va='center', fontfamily='monospace')

ax_cmp.text(0.5, 0.04, '↑ Mejoró   →  Estable   ↓  Empeoró',
    transform=ax_cmp.transAxes,
    color=WDIM, fontsize=8, ha='center', va='bottom',
    fontfamily='monospace')

ax_cmp.axhline(0.0, color=GOLD+"44", linewidth=0.5)


# ══════════════════════════════════════════════════════════════════════════════
# 8 · CALIDAD CAMPO — SEMÁFORO BONILLO / UEFA-STRI
# ══════════════════════════════════════════════════════════════════════════════
ax_bon = fig.add_subplot(gs[8])
ax_bon.set_facecolor(BG2)
ax_bon.set_xlim(0, 1); ax_bon.set_ylim(0, 1)
ax_bon.axis('off')

ax_bon.text(0.5, 0.97,
    'CALIDAD DE SUPERFICIE · ESTÁNDAR UEFA/STRI — MEDICIONES DE CAMPO',
    transform=ax_bon.transAxes,
    color=GOLD, fontsize=8.5, fontweight='bold',
    ha='center', va='top', fontfamily='monospace')

ax_bon.plot([0.01, 0.99], [0.89, 0.89], color=GOLD + '33', linewidth=0.5,
    transform=ax_bon.transAxes)

if not _bonillo_data:
    ax_bon.text(0.5, 0.60,
        'Pendiente — Roger debe cargar mediciones via /velez/medicion  (tipo: clegg)',
        transform=ax_bon.transAxes,
        color=WDIM, fontsize=8, ha='center', va='center',
        fontfamily='monospace')
    ax_bon.text(0.5, 0.38,
        'Verde: Compact. 70-90 CG  ·  Tracc. >=30 Nm  ·  '
        'Altura 24-28 mm  ·  Humedad 20-30%  ·  Raices >85 mm',
        transform=ax_bon.transAxes,
        color=WDIM + '77', fontsize=6.5, ha='center', va='center',
        fontfamily='monospace')
else:
    _bon_col_xs = [0.012, 0.160, 0.315, 0.470, 0.625, 0.780]
    _bon_hdrs   = ['ZONA', 'Compact. CG', 'Traccion Nm', 'Altura mm', 'Humedad %', 'Raices mm']
    _bon_ranges = ['',     '70-90',        '>=30',        '24-28',     '20-30',     '>85']

    _bon_hdr_y = 0.84
    for _hx, _hdr, _rng in zip(_bon_col_xs, _bon_hdrs, _bon_ranges):
        ax_bon.text(_hx, _bon_hdr_y, _hdr,
            transform=ax_bon.transAxes,
            color=GOLD, fontsize=5.5, fontweight='bold',
            va='top', fontfamily='monospace')
        if _rng:
            ax_bon.text(_hx, _bon_hdr_y - 0.10, f'V: {_rng}',
                transform=ax_bon.transAxes,
                color=GRNL + 'AA', fontsize=4.5,
                va='top', fontfamily='monospace')

    ax_bon.plot([0.01, 0.99], [0.70, 0.70], color=GOLD + '22', linewidth=0.4,
        transform=ax_bon.transAxes)

    _bon_row_h = max(0.030, 0.66 / max(len(ZONES), 1))
    _bon_row_y0 = 0.68

    _bon_metrics = _bs.METRICS if _bs else []
    for _bi, _z in enumerate(ZONES):
        _bry = _bon_row_y0 - _bi * _bon_row_h
        _zkey = _z['name'].lower()
        _bd   = _bonillo_data.get(_zkey, {})

        _bbg = BG3 if _bi % 2 == 0 else BG2
        ax_bon.add_patch(mpatches.Rectangle(
            (0.010, _bry - _bon_row_h * 0.45), 0.980, _bon_row_h * 0.90,
            facecolor=_bbg, edgecolor=GOLD + '11', linewidth=0.3,
            transform=ax_bon.transAxes, zorder=1))

        ax_bon.text(_bon_col_xs[0], _bry, _z['name'],
            transform=ax_bon.transAxes,
            color=WHITE, fontsize=5.5, fontweight='bold',
            va='center', fontfamily='monospace', zorder=2)

        for _mci, (_mkey, _, _munit, _, _) in enumerate(_bon_metrics):
            _bmx = _bon_col_xs[_mci + 1]
            _mval = _bd.get(_mkey)
            if _mval is not None:
                _msem = _bs.classify(_mkey, float(_mval))
                _mc   = SEM_COLOR[_msem]
                ax_bon.plot(_bmx - 0.006, _bry, 'o', ms=3.5, color=_mc,
                    transform=ax_bon.transAxes, zorder=3, clip_on=False)
                ax_bon.text(_bmx + 0.004, _bry, f'{_mval:.0f}',
                    transform=ax_bon.transAxes,
                    color=_mc, fontsize=5.5, fontweight='bold',
                    va='center', fontfamily='monospace', zorder=3)
            else:
                ax_bon.text(_bmx, _bry, '—',
                    transform=ax_bon.transAxes,
                    color=WDIM + '55', fontsize=5.5,
                    va='center', fontfamily='monospace', zorder=2)

    ax_bon.text(0.012, 0.01,
        'Fuente: UEFA/STRI standard  ·  '
        'Lecturas ingresadas por Roger via /velez/medicion  ·  '
        'Verde: 70-90 CG  /  >=30 Nm  /  24-28 mm  /  20-30%  /  >85 mm',
        transform=ax_bon.transAxes,
        color=WDIM + '77', fontsize=4.8, va='bottom',
        fontfamily='monospace')

ax_bon.axhline(0.0, color=GOLD + '22', linewidth=0.4)


# ══════════════════════════════════════════════════════════════════════════════
# 9 · FOOTER
# ══════════════════════════════════════════════════════════════════════════════
ax_ftr = fig.add_subplot(gs[9])
ax_ftr.set_facecolor(BG)
ax_ftr.set_xlim(0, 1); ax_ftr.set_ylim(0, 1)
ax_ftr.axis('off')

ax_ftr.axhline(0.99, color=GOLD, linewidth=1.2)

_hoy = datetime.now().date()
_dias = (7 - _hoy.weekday()) % 7 or 7
_lunes = _hoy + timedelta(days=_dias)
_meses = ['enero','febrero','marzo','abril','mayo','junio','julio','agosto','septiembre','octubre','noviembre','diciembre']
_proximo_lunes = f"lunes {_lunes.day} de {_meses[_lunes.month-1]} {_lunes.year}"

ax_ftr.text(0.5, 0.88,
    f'Próximo escaneo automático: {_proximo_lunes}',
    transform=ax_ftr.transAxes,
    color=GOLD, fontsize=9, fontweight='bold',
    ha='center', va='top', fontfamily='monospace')

ax_ftr.text(0.5, 0.66,
    'Sistema Fortín Inteligente · Faro Protocol · protocolfaro@gmail.com',
    transform=ax_ftr.transAxes,
    color=WDIM, fontsize=8,
    ha='center', va='top', fontfamily='monospace')

ax_ftr.text(0.5, 0.45,
    f'SHA-256: {SHA}',
    transform=ax_ftr.transAxes,
    color=GOLD+'99', fontsize=6.5,
    ha='center', va='top', fontfamily='monospace')

ax_ftr.text(0.5, 0.20,
    'Generado automáticamente · Sin intervención manual · Sentinel-2 / SAR / InSAR',
    transform=ax_ftr.transAxes,
    color=WDIM, fontsize=7.5,
    ha='center', va='top', fontfamily='monospace')

for lx, ltxt in [(0.06, 'ESA'), (0.14, 'COP'), (0.22, 'NASA')]:
    ax_ftr.add_patch(FancyBboxPatch(
        (lx - 0.03, 0.02), 0.07, 0.28,
        boxstyle='round,pad=0.01', transform=ax_ftr.transAxes,
        facecolor=BG3, edgecolor=GOLD+'66', linewidth=0.7, zorder=3))
    ax_ftr.text(lx + 0.005, 0.16, ltxt,
        transform=ax_ftr.transAxes, color=GOLD,
        fontsize=7, fontweight='bold', ha='center', va='center',
        fontfamily='monospace', zorder=4)
for lx, ltxt in [(0.78, 'ESA'), (0.86, 'COP'), (0.94, 'NASA')]:
    ax_ftr.add_patch(FancyBboxPatch(
        (lx - 0.03, 0.02), 0.07, 0.28,
        boxstyle='round,pad=0.01', transform=ax_ftr.transAxes,
        facecolor=BG3, edgecolor=GOLD+'66', linewidth=0.7, zorder=3))
    ax_ftr.text(lx + 0.005, 0.16, ltxt,
        transform=ax_ftr.transAxes, color=GOLD,
        fontsize=7, fontweight='bold', ha='center', va='center',
        fontfamily='monospace', zorder=4)


# ─── SAVE ────────────────────────────────────────────────────────────────────
import pathlib as _pl
_REPORT_DIR = _pl.Path(__file__).parents[4] / 'reportes_velez'
_REPORT_DIR.mkdir(exist_ok=True)
out = str(_out_path or (_REPORT_DIR / 'faro_reporte_velez_canchero.png'))
fig.savefig(out, dpi=DPI, facecolor=BG, bbox_inches='tight', pad_inches=0.08)
plt.close(fig)
print(f"Saved: {out}")
