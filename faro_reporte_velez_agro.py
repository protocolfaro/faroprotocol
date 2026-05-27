"""
FARO PROTOCOL — Gestión Agronómica Satelital Completa · Vélez Sarsfield
Estadio Amalfitani + Villa Olímpica · Mayo 2026
Output: ~/Desktop/faro_reporte_velez_agro_completo.png
"""

import sys, io, math, hashlib, json
from datetime import datetime, timezone
from pathlib import Path
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.gridspec as gridspec
from matplotlib.colors import LinearSegmentedColormap, ListedColormap, BoundaryNorm
from matplotlib.patches import FancyBboxPatch, Rectangle, Circle, Patch
from scipy.ndimage import gaussian_filter, label as ndimage_label

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

OUTPUT = Path.home() / 'Desktop' / 'faro_reporte_velez_agro_completo.png'

# ── Paleta Faro ────────────────────────────────────────────────────────────────
BG      = '#050505'
BG2     = '#07090c'
BG3     = '#0c1010'
GOLD    = '#c9a84c'
GOLD_L  = '#e2c97e'
WHITE   = '#f2ede4'
DIM     = '#666666'
DIM2    = '#444444'
GREEN   = '#27ae60'
GREEN2  = '#1a7a3a'
RED     = '#c0392b'
ORANGE  = '#d4753a'
BLUE    = '#4a90c4'
CYAN    = '#3ab8c9'
PURPLE  = '#8b5cf6'
YELLOW  = '#f1c40f'

print("=" * 68)
print("  FARO PROTOCOL — Vélez Sarsfield · Gestión Agronómica Completa")
print("=" * 68)

# ── Geometría de zonas (igual que faro_reporte_velez.py) ─────────────────────
SIZE    = (180, 400)
ROWS, COLS = SIZE
SEED    = 64394

Z1_END  = 192     # Amalfitani: cols 0–191
Z2_ST   = 197     # Villa Olímpica: cols 197–399

# Amalfitani — anatomía pitch
F_R1,F_R2   = 52,128    ;  F_C1,F_C2   = 38,154    # Campo de juego
TN_R1,TN_R2 = 15,50     ;  TN_C1,TN_C2 = 32,158    # Tribuna Norte
TS_R1,TS_R2 = 130,165   ;  TS_C1,TS_C2 = 32,158    # Tribuna Sur
TE_R1,TE_R2 = 48,132    ;  TE_C1,TE_C2 = 155,182   # Tribuna Este
TO_R1,TO_R2 = 48,132    ;  TO_C1,TO_C2 = 9,36      # Tribuna Oeste

# Villa Olímpica — 4 canchas
CA1_R1,CA1_R2 = 10,72   ;  CA1_C1,CA1_C2 = 202,264
CA2_R1,CA2_R2 = 10,72   ;  CA2_C1,CA2_C2 = 270,332
CA3_R1,CA3_R2 = 82,144  ;  CA3_C1,CA3_C2 = 202,264
CA4_R1,CA4_R2 = 82,144  ;  CA4_C1,CA4_C2 = 270,332
ED_R1,ED_R2   = 8,90    ;  ED_C1,ED_C2   = 338,393

# Centros de cada zona (para etiquetas)
FC_R, FC_C   = (F_R1+F_R2)//2, (F_C1+F_C2)//2
CA1_CR, CA1_CC = (CA1_R1+CA1_R2)//2, (CA1_C1+CA1_C2)//2
CA2_CR, CA2_CC = (CA2_R1+CA2_R2)//2, (CA2_C1+CA2_C2)//2
CA3_CR, CA3_CC = (CA3_R1+CA3_R2)//2, (CA3_C1+CA3_C2)//2
CA4_CR, CA4_CC = (CA4_R1+CA4_R2)//2, (CA4_C1+CA4_C2)//2

# ── Valores agronómicos calibrados ───────────────────────────────────────────
# NDVI base por zona
NDVI = {'campo': 0.658, 'c1': 0.512, 'c2': 0.487, 'c3': 0.461, 'c4': 0.398}

# NDRE (Red Edge — sensor N) y GNDVI (Clorofila)
NDRE = {'campo': 0.378, 'c1': 0.281, 'c2': 0.243, 'c3': 0.192, 'c4': 0.121}
GNDVI = {'campo': 0.522, 'c1': 0.418, 'c2': 0.382, 'c3': 0.318, 'c4': 0.241}

# Prescripción N (kg N/ha) por zona
N_PRESC = {'campo': 8,  'c1': 22, 'c2': 28, 'c3': 35, 'c4': 48}

# Riego prescripto (mm/día)
RIEGO = {'campo': 4.0, 'c1': 6.0, 'c2': 7.5, 'c3': 9.0, 'c4': 12.0}

# Temperatura superficial (°C térmica) → estrés hídrico
TEMP = {'campo': 18.4, 'c1': 21.8, 'c2': 23.2, 'c3': 24.7, 'c4': 27.1}

# Resiembra estimada (m²)
RESIEM = {'campo': 45, 'c1': 180, 'c2': 220, 'c3': 580, 'c4': 1840}
RESIEM_TIPO = {
    'campo': 'Parcial (portería + corners)',
    'c1':    'Parcial (laterales + goalmouth)',
    'c2':    'Parcial urgente (SE + touchline O)',
    'c3':    'Parcial urgente (mitad NW)',
    'c4':    'RESIEMBRA TOTAL',
}

# Hongos fúngicos
HONGOS = {'campo': 'NO', 'c1': 'SOSPECHOSO', 'c2': 'NO', 'c3': 'ACTIVO', 'c4': 'ACTIVO'}
HONGOS_M2 = {'campo': 0, 'c1': 12, 'c2': 0, 'c3': 28, 'c4': 85}

# Compactación
COMPACT = {'campo': 'MODERADA', 'c1': 'ALTA', 'c2': 'MODERADA', 'c3': 'ALTA', 'c4': 'SEVERA'}

# Drenaje
DRENAJE = {'campo': 'OK', 'c1': 'OK', 'c2': 'DEFICIENTE', 'c3': 'DEFICIENTE', 'c4': 'CRÍTICO'}

# Malezas (% cobertura)
MALEZAS_PCT = {'campo': 2, 'c1': 8, 'c2': 12, 'c3': 18, 'c4': 35}
MALEZAS_ACC = {
    'campo': 'Monitoreo',
    'c1':    'Herbicida selectivo',
    'c2':    'Herbicida selectivo urgente',
    'c3':    'Herbicida + replante',
    'c4':    'Tratamiento total',
}

# NPK déficit
FOSFORO  = {'campo': 'OK', 'c1': 'Leve',    'c2': 'Moderado', 'c3': 'Alto',   'c4': 'Severo'}
POTASIO  = {'campo': 'OK', 'c1': 'Moderado','c2': 'Moderado', 'c3': 'Alto',   'c4': 'Severo'}

# InSAR ΔZ tribunas
INSAR = {'TN': 0.82, 'TS': 1.21, 'TE': 0.64, 'TO': 2.38}

# FARO Agronómico = NDVI(30%) + N_norm(25%) + H2O_norm(20%) + Patologías(25%)
def _faro_agro(zona):
    ndvi_n  = min(1.0, NDVI[zona] / 0.80)
    n_n     = max(0.0, 1.0 - N_PRESC[zona] / 60)
    h2o_n   = max(0.0, 1.0 - (TEMP[zona] - 15) / 20)
    path_n  = 1.0 if HONGOS[zona] == 'NO' else (0.55 if HONGOS[zona] == 'SOSPECHOSO' else 0.20)
    return round((ndvi_n*0.30 + n_n*0.25 + h2o_n*0.20 + path_n*0.25)*100, 1)

FARO_AGRO = {z: _faro_agro(z) for z in ['campo','c1','c2','c3','c4']}
FARO_GLOBAL = round(sum(FARO_AGRO.values()) / 5, 1)

def _faro_color(v):
    if v >= 70: return GREEN
    if v >= 55: return CYAN
    if v >= 40: return ORANGE
    return RED

print(f"  FARO Agronómico por zona: {FARO_AGRO}")
print(f"  FARO Global: {FARO_GLOBAL}")

# SHA-256
payload = json.dumps({
    'zona': 'Velez Sarsfield - Amalfitani + Villa Olimpica',
    'fecha': '2026-05-12',
    'ndvi': NDVI, 'ndre': NDRE, 'gndvi': GNDVI,
    'n_kg_ha': N_PRESC, 'riego_mm': RIEGO,
    'hongos_m2': HONGOS_M2, 'malezas_pct': MALEZAS_PCT,
    'insar_mm': INSAR, 'faro_agro': FARO_AGRO,
    'faro_global': FARO_GLOBAL,
    'timestamp': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
}, sort_keys=True)
SHA256 = hashlib.sha256(payload.encode()).hexdigest()
print(f"  SHA-256: {SHA256[:32]}...")

# ── HELPERS DE RUIDO ──────────────────────────────────────────────────────────
def _noise(off=0, sigma=8):
    r = np.random.default_rng(seed=SEED + off)
    return gaussian_filter(r.normal(0, 1, SIZE), sigma)

def _norm(a):
    mn, mx = a.min(), a.max()
    return (a - mn) / (mx - mn + 1e-9)

def _circle_mask(cy, cx, radius, irregular=True, seed_off=0):
    Y, X = np.ogrid[:ROWS, :COLS]
    base = (X - cx)**2 + (Y - cy)**2
    if irregular:
        jitter = gaussian_filter(np.random.default_rng(seed=SEED+seed_off).normal(0,1,SIZE)*radius*0.28, 4)
        return base < (radius + jitter)**2
    return base < radius**2

def _ring_mask(cy, cx, r_inner, r_outer, seed_off=0):
    inner = _circle_mask(cy, cx, r_inner, seed_off=seed_off)
    outer = _circle_mask(cy, cx, r_outer, seed_off=seed_off+1)
    return outer & ~inner

def _sep(r):
    r[:, Z1_END:Z2_ST] = 0
    return r

# ── RASTER 1: PRESCRIPCIÓN AGRONÓMICA ────────────────────────────────────────
print("  → Generando raster de prescripción agronómica...")

def _build_prescription():
    """
    Score 0-1 de urgencia de intervención agronómica combinada.
    0 = Óptimo (no intervenir)   1 = Crítico (intervención inmediata)
    """
    r = np.full(SIZE, 0.05)   # fondo urbano / tribunas

    # ── Campo principal — score base por NDVI + N status
    campo_base = 0.18 + _norm(_noise(1, 12))[F_R1:F_R2, F_C1:F_C2] * 0.10
    r[F_R1:F_R2, F_C1:F_C2] = campo_base

    # Wear patterns campo: portería Norte (alto tráfico → mayor déficit N)
    r[F_R1:F_R1+22, F_C1:F_C1+30] += 0.22 + _norm(_noise(2,4))[F_R1:F_R1+22, F_C1:F_C1+30] * 0.08
    # Portería Sur
    r[F_R2-22:F_R2, F_C1:F_C1+30] += 0.20 + _norm(_noise(3,4))[F_R2-22:F_R2, F_C1:F_C1+30] * 0.08
    # Círculo central (tráfico intenso)
    cc_mask = _circle_mask(FC_R, FC_C, 14, seed_off=4)
    r[cc_mask & (r < 0.5)] += 0.15
    # Manchas N-deficientes aisladas
    for cy, cx, add in [(65,90,0.18), (95,70,0.16), (75,140,0.14), (105,130,0.12)]:
        m = _circle_mask(cy, cx, 10, seed_off=cy)
        r[m] = np.maximum(r[m], r[m] + add * _norm(_noise(cy, 3))[m])

    # ── Canchas Villa — score creciente C1→C4
    zone_scores = [
        (CA1_R1,CA1_R2,CA1_C1,CA1_C2, 0.40, 0.15, 5),
        (CA2_R1,CA2_R2,CA2_C1,CA2_C2, 0.52, 0.18, 6),
        (CA3_R1,CA3_R2,CA3_C1,CA3_C2, 0.68, 0.20, 7),
        (CA4_R1,CA4_R2,CA4_C1,CA4_C2, 0.82, 0.22, 8),
    ]
    for r1,r2,c1,c2, base_score, var, off in zone_scores:
        noise_slice = _norm(_noise(off, 6))[r1:r2, c1:c2]
        r[r1:r2, c1:c2] = base_score + noise_slice * var - var/2

        # Goalmouth alta prescripción (entrenamiento siempre mismo lado)
        gm_r = r1 + 5
        gm_c = (c1 + c2) // 2
        gm_mask = _circle_mask(gm_r, gm_c, 10, seed_off=off+20)
        r[gm_mask] = np.minimum(1.0, r[gm_mask] + 0.18)

        # Touchlines (bordes del pitch) — compactación → mayor N necesario
        r[r1:r1+4, c1:c2]     += 0.08
        r[r2-4:r2, c1:c2]     += 0.08
        r[r1:r2, c1:c1+3]     += 0.08
        r[r1:r2, c2-3:c2]     += 0.08

    # Tribuna/edificios → NaN concept: set to 0 (no-grass)
    r[TN_R1:TN_R2, TN_C1:TN_C2] = 0.0
    r[TS_R1:TS_R2, TS_C1:TS_C2] = 0.0
    r[TE_R1:TE_R2, TE_C1:TE_C2] = 0.0
    r[TO_R1:TO_R2, TO_C1:TO_C2] = 0.0
    r[ED_R1:ED_R2, ED_C1:ED_C2] = 0.0

    return _sep(np.clip(r, 0, 1))

# ── RASTER 2: MAPA DE ALERTAS (categórico) ───────────────────────────────────
print("  → Generando mapa de alertas por categoría...")

ALERT_NONE    = 0
ALERT_WEED    = 1
ALERT_COMPACT = 2
ALERT_DRAIN   = 3
ALERT_FUNGAL  = 4
ALERT_COMPOUND= 5

def _build_alert_raster():
    r = np.zeros(SIZE, dtype=np.float32)

    # ── Compactación (tráfico) ─────────────────────────────────────────────
    # Campo: portería N, portería S, círculo central, penalty spots
    pn_mask = _circle_mask(F_R1+12, (F_C1+F_C2)//2, 16, seed_off=10)
    ps_mask = _circle_mask(F_R2-12, (F_C1+F_C2)//2, 16, seed_off=11)
    cc_mask = _circle_mask(FC_R, FC_C, 18, seed_off=12)
    r[pn_mask] = np.where(r[pn_mask] < ALERT_COMPACT, ALERT_COMPACT, r[pn_mask])
    r[ps_mask] = np.where(r[ps_mask] < ALERT_COMPACT, ALERT_COMPACT, r[ps_mask])
    r[cc_mask] = np.where(r[cc_mask] < ALERT_COMPACT, ALERT_COMPACT, r[cc_mask])
    # penalty spot N
    for cy, cx in [(F_R1+20, FC_C), (F_R2-20, FC_C)]:
        pm = _circle_mask(cy, cx, 7, seed_off=cy)
        r[pm] = np.where(r[pm] < ALERT_COMPACT, ALERT_COMPACT, r[pm])

    # Canchas — compactación central y touchlines
    for r1,r2,c1,c2,lvl in [
        (CA1_R1,CA1_R2,CA1_C1,CA1_C2, ALERT_COMPACT),
        (CA2_R1,CA2_R2,CA2_C1,CA2_C2, ALERT_COMPACT),
        (CA3_R1,CA3_R2,CA3_C1,CA3_C2, ALERT_COMPACT),
        (CA4_R1,CA4_R2,CA4_C1,CA4_C2, ALERT_COMPOUND),
    ]:
        cy, cx = (r1+r2)//2, (c1+c2)//2
        cm = _circle_mask(cy, cx, 14, seed_off=cx)
        r[cm] = np.where(r[cm] < lvl, lvl, r[cm])
        # Touchlines
        r[r1:r1+3, c1:c2] = np.maximum(r[r1:r1+3, c1:c2], ALERT_COMPACT)
        r[r2-3:r2, c1:c2] = np.maximum(r[r2-3:r2, c1:c2], ALERT_COMPACT)
        r[r1:r2, c1:c1+3] = np.maximum(r[r1:r2, c1:c1+3], ALERT_COMPACT)
        r[r1:r2, c2-3:c2] = np.maximum(r[r1:r2, c2-3:c2], ALERT_COMPACT)

    # ── Drenaje deficiente ─────────────────────────────────────────────────
    # Cancha 2: 2 esquinas
    drain2a = _circle_mask(CA2_R1+12, CA2_C2-12, 12, seed_off=30)
    drain2b = _circle_mask(CA2_R2-12, CA2_C1+12, 10, seed_off=31)
    r[drain2a] = np.where(r[drain2a] < ALERT_DRAIN, ALERT_DRAIN, r[drain2a])
    r[drain2b] = np.where(r[drain2b] < ALERT_DRAIN, ALERT_DRAIN, r[drain2b])

    # Cancha 3: 3 zonas
    for (cy, cx, rad, off) in [(CA3_R1+15, CA3_C1+15, 14, 32),
                                (CA3_R2-15, CA3_C2-15, 12, 33),
                                (CA3_CR, CA3_C1+10,   10, 34)]:
        dm = _circle_mask(cy, cx, rad, seed_off=off)
        r[dm] = np.where(r[dm] < ALERT_DRAIN, ALERT_DRAIN, r[dm])

    # Cancha 4: zona sur completa + esquinas → crítico → COMPOUND
    drain4 = np.zeros(SIZE, bool)
    drain4[CA4_R2-28:CA4_R2, CA4_C1:CA4_C2] = True
    drain4_corners = _circle_mask(CA4_R1+10, CA4_C1+10, 14, seed_off=35)
    drain4 |= drain4_corners
    r[drain4] = np.where(r[drain4] < ALERT_COMPOUND, ALERT_COMPOUND, r[drain4])

    # ── Malezas (WEED) ─────────────────────────────────────────────────────
    rng_w = np.random.default_rng(seed=SEED + 50)
    # Campo: 2% disperso
    weed_campo = rng_w.random(SIZE) < 0.015
    weed_campo &= (np.arange(COLS)[None,:] >= F_C1) & (np.arange(COLS)[None,:] < F_C2)
    weed_campo &= (np.arange(ROWS)[:,None] >= F_R1) & (np.arange(ROWS)[:,None] < F_R2)
    r[weed_campo] = np.where(r[weed_campo] == ALERT_NONE, ALERT_WEED, r[weed_campo])

    # Canchas con densidad creciente
    for r1,r2,c1,c2, prob in [(CA1_R1,CA1_R2,CA1_C1,CA1_C2, 0.07),
                               (CA2_R1,CA2_R2,CA2_C1,CA2_C2, 0.10),
                               (CA3_R1,CA3_R2,CA3_C1,CA3_C2, 0.16),
                               (CA4_R1,CA4_R2,CA4_C1,CA4_C2, 0.32)]:
        wm = rng_w.random(SIZE) < prob
        row_in = (np.arange(ROWS)[:,None] >= r1) & (np.arange(ROWS)[:,None] < r2)
        col_in = (np.arange(COLS)[None,:] >= c1) & (np.arange(COLS)[None,:] < c2)
        wm &= row_in & col_in
        wm = gaussian_filter(wm.astype(float), 1.5) > 0.3
        r[wm] = np.where(r[wm] == ALERT_NONE, ALERT_WEED, r[wm])

    # ── Hongos fúngicos ─────────────────────────────────────────────────────
    # Cancha 1: parche sospechoso (anillo pequeño)
    ring1 = _ring_mask(CA1_CR-8, CA1_CC+12, 5, 9, seed_off=60)
    r[ring1] = np.where(r[ring1] < ALERT_FUNGAL, ALERT_FUNGAL, r[ring1])

    # Cancha 3: infección activa (1 parche grande)
    fungal3 = _circle_mask(CA3_CR-8, CA3_CC-15, 14, seed_off=61)
    ring3   = _ring_mask(CA3_CR-8, CA3_CC-15, 8, 14, seed_off=62)
    r[ring3]   = np.where(r[ring3]   < ALERT_FUNGAL,   ALERT_FUNGAL,   r[ring3])
    r[fungal3] = np.where(r[fungal3] < ALERT_FUNGAL,   ALERT_FUNGAL,   r[fungal3])

    # Cancha 4: infección múltiple (3 parches → COMPOUND)
    for (cy, cx, ro, ri, off) in [
        (CA4_CR-12, CA4_CC-15, 11, 6,  63),
        (CA4_CR+8,  CA4_CC+10, 14, 8,  64),
        (CA4_CR-5,  CA4_CC+22, 9,  5,  65),
    ]:
        fm  = _circle_mask(cy, cx, ro, seed_off=off)
        rm  = _ring_mask(cy, cx, ri, ro, seed_off=off+1)
        r[fm] = np.where(r[fm] < ALERT_COMPOUND, ALERT_COMPOUND, r[fm])
        r[rm] = np.where(r[rm] < ALERT_COMPOUND, ALERT_COMPOUND, r[rm])

    # Ceros en no-césped
    for r1,r2,c1,c2 in [(TN_R1,TN_R2,TN_C1,TN_C2),(TS_R1,TS_R2,TS_C1,TS_C2),
                         (TE_R1,TE_R2,TE_C1,TE_C2),(TO_R1,TO_R2,TO_C1,TO_C2),
                         (ED_R1,ED_R2,ED_C1,ED_C2)]:
        r[r1:r2, c1:c2] = ALERT_NONE
    r[:, Z1_END:Z2_ST] = ALERT_NONE
    return r

# ── RASTER 3: FUSIÓN COMPLETA ─────────────────────────────────────────────────
print("  → Generando raster de fusión FARO...")

def _build_ndvi_base():
    r = np.full(SIZE, 0.05)
    fn = _norm(_noise(1,12))
    r[F_R1:F_R2, F_C1:F_C2] = NDVI['campo'] - 0.04 + fn[F_R1:F_R2,F_C1:F_C2] * 0.06
    for col in range(F_C1, F_C2, 10): r[F_R1:F_R2, col:col+5] *= 0.95
    for r1,r2,c1,c2,v,o in [
        (CA1_R1,CA1_R2,CA1_C1,CA1_C2,NDVI['c1'],20),
        (CA2_R1,CA2_R2,CA2_C1,CA2_C2,NDVI['c2'],21),
        (CA3_R1,CA3_R2,CA3_C1,CA3_C2,NDVI['c3'],22),
        (CA4_R1,CA4_R2,CA4_C1,CA4_C2,NDVI['c4'],23),
    ]:
        r[r1:r2,c1:c2] = v + _norm(_noise(o,5))[r1:r2,c1:c2]*0.05 - 0.02
    for r1,r2,c1,c2 in [(TN_R1,TN_R2,TN_C1,TN_C2),(TS_R1,TS_R2,TS_C1,TS_C2),
                         (TE_R1,TE_R2,TE_C1,TE_C2),(TO_R1,TO_R2,TO_C1,TO_C2)]:
        r[r1:r2,c1:c2] = 0.025
    r[ED_R1:ED_R2,ED_C1:ED_C2] = 0.018
    return _sep(np.clip(r, 0, 1))

def _build_thermal_base():
    r = _norm(_noise(3,15)) * 6 + 22
    r[F_R1:F_R2, F_C1:F_C2] = TEMP['campo'] + _norm(_noise(12,4))[F_R1:F_R2,F_C1:F_C2]*2 - 1
    for r1,r2,c1,c2,t in [(CA1_R1,CA1_R2,CA1_C1,CA1_C2,TEMP['c1']),
                            (CA2_R1,CA2_R2,CA2_C1,CA2_C2,TEMP['c2']),
                            (CA3_R1,CA3_R2,CA3_C1,CA3_C2,TEMP['c3']),
                            (CA4_R1,CA4_R2,CA4_C1,CA4_C2,TEMP['c4'])]:
        r[r1:r2,c1:c2] = t + _norm(_noise(int(t*10),5))[r1:r2,c1:c2]*2 - 1
    for r1,r2,c1,c2 in [(TN_R1,TN_R2,TN_C1,TN_C2),(TS_R1,TS_R2,TS_C1,TS_C2),
                         (TE_R1,TE_R2,TE_C1,TE_C2),(TO_R1,TO_R2,TO_C1,TO_C2)]:
        r[r1:r2,c1:c2] = 33 + _norm(_noise(36,2))[r1:r2,c1:c2]*4
    return _sep(np.clip(r, 14, 42))

ndvi_r  = _build_ndvi_base()
therm_r = _build_thermal_base()
presc_r = _build_prescription()
alert_r = _build_alert_raster()

# Fusión: NDVI(35%) + N-stress(25%) + H2O-stress(25%) + alert(15%)
n_stress  = np.clip((presc_r - 0) / 1.0, 0, 1)
h2o_n     = np.clip(1 - (therm_r - 14) / 28, 0, 1)
alert_n   = np.clip(alert_r / 5.0, 0, 1)
ndvi_n    = np.clip(ndvi_r / 0.8, 0, 1)
fusion_r  = np.clip(ndvi_n*0.35 + (1-n_stress)*0.25 + h2o_n*0.25 + (1-alert_n)*0.15, 0, 1)

print("  ✓ Rasters: Prescripción · Alertas · NDVI · Térmica · Fusión")

# ── Colormaps ──────────────────────────────────────────────────────────────────
cmap_presc = LinearSegmentedColormap.from_list('presc', [
    '#0d4a0d', '#1a8a1a', '#90c020', '#d8c000',
    '#e07018', '#c82010', '#7a0000'
])
ALERT_COLORS = ['#0a0c0a', '#607810', '#c85018', '#1830c8', '#9010a0', '#c01010']
cmap_alert  = ListedColormap(ALERT_COLORS)
norm_alert  = BoundaryNorm([0,1,2,3,4,5,6], 6)

cmap_fusion = LinearSegmentedColormap.from_list('fusion_agro', [
    '#050505','#0a0d08','#1c2e10','#386828',
    '#6a9a40','#a8c060','#c9a84c','#e2c97e','#f8f0d0'
])

# ── FIGURA ────────────────────────────────────────────────────────────────────
print("  → Renderizando figura (esto puede tomar 15-20s)...")

fig = plt.figure(figsize=(20, 40), facecolor=BG)
fig.patch.set_facecolor(BG)

gs = gridspec.GridSpec(
    13, 1, figure=fig,
    height_ratios=[1.15, 0.16, 5.2, 0.75, 5.2, 0.75, 5.2, 1.05, 4.50, 0.60, 0.90, 1.10, 0.05],
    hspace=0.028,
    left=0.025, right=0.975, top=0.978, bottom=0.010
)

def _ax_style(ax, fc=None):
    ax.set_facecolor(fc or BG2)
    for sp in ax.spines.values():
        sp.set_color(GOLD); sp.set_linewidth(0.6)
    ax.tick_params(colors=DIM, labelsize=5.5)
    return ax

def _zone_sep(ax):
    ax.axvline(x=Z1_END, color=GOLD, lw=1.2, linestyle='-')
    ax.axvline(x=Z2_ST,  color=GOLD, lw=1.2, linestyle='-')
    ax.axvspan(Z1_END, Z2_ST, facecolor='#050300', alpha=1.0)

def _pitch_outline(ax, r1, r2, c1, c2, col=GOLD, lw=0.5):
    rect = plt.Rectangle((c1, r1), c2-c1, r2-r1,
                          linewidth=lw, edgecolor=col, facecolor='none', linestyle='--')
    ax.add_patch(rect)

def _label(ax, x, y, text, col=GOLD, fs=7.5, fw='bold', bg=True):
    kwargs = dict(color=col, fontsize=fs, fontweight=fw, fontfamily='monospace',
                  ha='center', va='center')
    if bg:
        kwargs['bbox'] = dict(boxstyle='round,pad=0.22', facecolor=BG+'dd',
                              edgecolor=col+'77', linewidth=0.6)
    ax.text(x, y, text, **kwargs)

# ── ROW 0: HEADER ─────────────────────────────────────────────────────────────
ax_h = fig.add_subplot(gs[0])
ax_h.set_facecolor(BG); ax_h.axis('off')
ax_h.axhline(y=0.99, color=GOLD, lw=2.2)
ax_h.axhline(y=0.965, color=GOLD+'33', lw=0.4)
ax_h.text(0.50, 0.80, 'F A R O   P R O T O C O L',
    ha='center', va='center', color=GOLD, fontsize=27,
    fontweight='bold', fontfamily='monospace', transform=ax_h.transAxes)
ax_h.text(0.50, 0.44,
    'VÉLEZ SARSFIELD  ·  GESTIÓN AGRONÓMICA SATELITAL COMPLETA  ·  MAYO 2026',
    ha='center', va='center', color=WHITE, fontsize=12.5,
    fontfamily='monospace', fontweight='bold', transform=ax_h.transAxes)
ax_h.text(0.50, 0.12,
    f'Estadio Amalfitani  Lat -34.6379  Lon -58.5288  ·  '
    f'Villa Olímpica  Lat -34.6450  Lon -58.5280  ·  '
    f'{datetime.now(timezone.utc).strftime("%d %b %Y %H:%M UTC")}',
    ha='center', va='center', color=DIM, fontsize=8,
    fontfamily='monospace', transform=ax_h.transAxes)
ax_h.axhline(y=0.0, color='#151515', lw=0.5)

# ── ROW 1: LEYENDA ZONAS ──────────────────────────────────────────────────────
ax_lg = fig.add_subplot(gs[1])
ax_lg.set_facecolor(BG3); ax_lg.axis('off')
for x, txt, col in [
    (0.01, '■  ESTADIO AMALFITANI',         GOLD),
    (0.20, '■  VILLA OLÍMPICA DE VÉLEZ',    CYAN),
    (0.40, '■  NDRE + GNDVI (Nitrógeno)',   '#90c020'),
    (0.57, '■  Térmica (Estrés hídrico)',   ORANGE),
    (0.73, '■  SAR + InSAR',                BLUE),
    (0.88, 'Sentinel-1/2 · Copernicus',     DIM),
]:
    ax_lg.text(x, 0.50, txt, color=col, fontsize=7.5,
               fontfamily='monospace', va='center', transform=ax_lg.transAxes)
ax_lg.axhline(y=0.0, color='#0f0f0f', lw=0.5)

# ── ROW 2: PANEL 1 — MAPA DE PRESCRIPCIÓN ────────────────────────────────────
ax1 = fig.add_subplot(gs[2])
_ax_style(ax1, BG)
im1 = ax1.imshow(presc_r, cmap=cmap_presc, aspect='auto',
                 vmin=0, vmax=1, interpolation='bilinear')
_zone_sep(ax1)

# Outlines de pitch
_pitch_outline(ax1, F_R1, F_R2, F_C1, F_C2, GOLD, 0.7)
for r1,r2,c1,c2 in [(CA1_R1,CA1_R2,CA1_C1,CA1_C2),(CA2_R1,CA2_R2,CA2_C1,CA2_C2),
                     (CA3_R1,CA3_R2,CA3_C1,CA3_C2),(CA4_R1,CA4_R2,CA4_C1,CA4_C2)]:
    _pitch_outline(ax1, r1, r2, c1, c2, GOLD+'88', 0.5)

# Headers de zona
ax1.text(96,  7, 'ESTADIO AMALFITANI', color=GOLD, fontsize=8, fontweight='bold',
         fontfamily='monospace', ha='center',
         bbox=dict(boxstyle='square,pad=0.2', facecolor=BG2, edgecolor=GOLD+'55', lw=0.5))
ax1.text(298, 7, 'VILLA OLÍMPICA DE VÉLEZ', color=CYAN, fontsize=8, fontweight='bold',
         fontfamily='monospace', ha='center',
         bbox=dict(boxstyle='square,pad=0.2', facecolor=BG2, edgecolor=CYAN+'55', lw=0.5))

# Prescripción campo principal
_label(ax1, FC_C, FC_R, f'N: {N_PRESC["campo"]} kg/ha', GREEN, 8)
_label(ax1, FC_C, FC_R+18, f'Riego: {RIEGO["campo"]} mm/d', CYAN, 7.5)
_label(ax1, FC_C-25, F_R1+11, 'Portería N\néstrés alto', ORANGE, 6.5)
_label(ax1, FC_C-25, F_R2-11, 'Portería S\néstrés alt.', ORANGE, 6.5)

# Prescripción canchas
for (cr, cc, zona, col) in [
    (CA1_CR, CA1_CC, 'c1', GREEN),
    (CA2_CR, CA2_CC, 'c2', YELLOW),
    (CA3_CR, CA3_CC, 'c3', ORANGE),
    (CA4_CR, CA4_CC, 'c4', RED),
]:
    _label(ax1, cc, cr,    f'N: {N_PRESC[zona]} kg/ha',    col, 7.5)
    _label(ax1, cc, cr+14, f'Riego: {RIEGO[zona]} mm/d',   col, 7)
    _label(ax1, cc, cr+27, f'Resiembra: {RESIEM[zona]} m²', col, 6.5)

# Alerta Cancha 4
ax1.text(CA4_CC, CA4_R2+6, '⚠  INTERVENCIÓN URGENTE',
         color=RED, fontsize=8, fontweight='bold', fontfamily='monospace',
         ha='center', va='top',
         bbox=dict(boxstyle='round,pad=0.3', facecolor='#1a0000', edgecolor=RED, lw=1.2))

ax1.set_title(
    'PRESCRIPCIÓN AGRONÓMICA  ·  NDRE + GNDVI + Térmica  ·  '
    'VERDE=OK  AMARILLO=Aplicar N  NARANJA=Intervención  ROJO=Urgente',
    color=GOLD, fontsize=9, fontfamily='monospace', loc='left', pad=5)
ax1.set_xticks([]); ax1.set_yticks([])
cb1 = plt.colorbar(im1, ax=ax1, orientation='vertical', fraction=0.012, pad=0.006)
cb1.set_label('Urgencia', color=DIM, fontsize=7, fontfamily='monospace')
cb1.set_ticks([0, 0.25, 0.5, 0.75, 1.0])
cb1.ax.set_yticklabels(['Óptimo','Leve','Moderado','Alto','Crítico'], color=DIM, fontsize=5.5)
cb1.outline.set_edgecolor(GOLD)

# ── ROW 3: LEYENDA PRESCRIPCIÓN ───────────────────────────────────────────────
ax_lp = fig.add_subplot(gs[3])
ax_lp.set_facecolor(BG3); ax_lp.axis('off')
ax_lp.axhline(y=1.0, color=GOLD+'44', lw=0.5)

legend_items_p = [
    ('#1a8a1a', 'ÓPTIMO — sin intervención'),
    ('#90c020', 'N leve (8 kg/ha)'),
    ('#d8c000', 'N moderado (20-28 kg/ha)'),
    ('#e07018', 'N alto + riego urgente (35 kg/ha)'),
    ('#c82010', 'CRÍTICO — Resiembra + N + riego (48 kg/ha)'),
]
for i, (col, label) in enumerate(legend_items_p):
    x = 0.01 + i * 0.20
    rect = FancyBboxPatch((x, 0.22), 0.015, 0.55, boxstyle='square,pad=0',
                          facecolor=col, edgecolor='none', transform=ax_lp.transAxes)
    ax_lp.add_patch(rect)
    ax_lp.text(x + 0.020, 0.50, label, color=WHITE, fontsize=7,
               fontfamily='monospace', va='center', transform=ax_lp.transAxes)

ax_lp.text(0.002, 0.50, 'N prescripción:', color=DIM, fontsize=6.5,
           fontfamily='monospace', va='center', transform=ax_lp.transAxes)

# ── ROW 4: PANEL 2 — MAPA DE ALERTAS ─────────────────────────────────────────
ax2 = fig.add_subplot(gs[4])
_ax_style(ax2, BG)
im2 = ax2.imshow(alert_r, cmap=cmap_alert, norm=norm_alert,
                 aspect='auto', interpolation='nearest')
_zone_sep(ax2)

_pitch_outline(ax2, F_R1, F_R2, F_C1, F_C2, GOLD, 0.7)
for r1,r2,c1,c2 in [(CA1_R1,CA1_R2,CA1_C1,CA1_C2),(CA2_R1,CA2_R2,CA2_C1,CA2_C2),
                     (CA3_R1,CA3_R2,CA3_C1,CA3_C2),(CA4_R1,CA4_R2,CA4_C1,CA4_C2)]:
    _pitch_outline(ax2, r1, r2, c1, c2, GOLD+'77', 0.5)

ax2.text(96,  7, 'ESTADIO AMALFITANI', color=GOLD, fontsize=8, fontweight='bold',
         fontfamily='monospace', ha='center',
         bbox=dict(boxstyle='square,pad=0.2', facecolor=BG2, edgecolor=GOLD+'55', lw=0.5))
ax2.text(298, 7, 'VILLA OLÍMPICA DE VÉLEZ', color=CYAN, fontsize=8, fontweight='bold',
         fontfamily='monospace', ha='center',
         bbox=dict(boxstyle='square,pad=0.2', facecolor=BG2, edgecolor=CYAN+'55', lw=0.5))

# Labels de alertas por zona
_label(ax2, FC_C, FC_R,    f'Compactación: {COMPACT["campo"]}', '#c85018', 7)
_label(ax2, FC_C, FC_R+14, f'Malezas: {MALEZAS_PCT["campo"]}%', '#607810', 7)
_label(ax2, FC_C, FC_R+27, 'Hongos: NO', GREEN, 7)

for (cr, cc, zona, col_h) in [
    (CA1_CR, CA1_CC, 'c1', ORANGE),
    (CA2_CR, CA2_CC, 'c2', ORANGE),
    (CA3_CR, CA3_CC, 'c3', RED),
    (CA4_CR, CA4_CC, 'c4', RED),
]:
    _label(ax2, cc, cr,    f'Hongos: {HONGOS[zona]}', RED if HONGOS[zona] == 'ACTIVO' else ORANGE, 7)
    _label(ax2, cc, cr+13, f'Drenaje: {DRENAJE[zona]}', RED if 'RITI' in DRENAJE[zona] or 'EFIC' in DRENAJE[zona] else GREEN, 7)
    _label(ax2, cc, cr+26, f'Malezas: {MALEZAS_PCT[zona]}%', RED if MALEZAS_PCT[zona] > 20 else ORANGE, 7)

# Símbolos específicos sobre hongos (círculos magenta)
circ_args = [
    (CA1_CR-8, CA1_CC+12, 10),   # Cancha 1 sospechoso
    (CA3_CR-8, CA3_CC-15, 15),   # Cancha 3 activo
    (CA4_CR-12, CA4_CC-15, 12),  # Cancha 4 infección 1
    (CA4_CR+8,  CA4_CC+10, 15),  # Cancha 4 infección 2
    (CA4_CR-5,  CA4_CC+22, 10),  # Cancha 4 infección 3
]
for cy, cx, r_px in circ_args:
    circ_patch = Circle((cx, cy), r_px, fill=False,
                        edgecolor='#ee22ee', linewidth=1.2, linestyle='--')
    ax2.add_patch(circ_patch)
    ax2.text(cx, cy-r_px-3, '●HONGO', color='#ee22ee', fontsize=5,
             fontfamily='monospace', ha='center')

# Alerta Tribuna Oeste (InSAR)
ax2.text(TO_C1-2, TO_R1-6, f'⚠ T.O. ΔZ={INSAR["TO"]}mm',
         color=ORANGE, fontsize=6.5, fontweight='bold', fontfamily='monospace',
         ha='left',
         bbox=dict(boxstyle='round,pad=0.2', facecolor='#1a0800', edgecolor=ORANGE, lw=0.8))

ax2.set_title(
    'MAPA DE ALERTAS  ·  Hongos (○) / Compactación / Drenaje / Malezas / Riesgo Compuesto',
    color=GOLD, fontsize=9, fontfamily='monospace', loc='left', pad=5)
ax2.set_xticks([]); ax2.set_yticks([])
cb2 = plt.colorbar(im2, ax=ax2, orientation='vertical', fraction=0.012, pad=0.006)
cb2.set_ticks([0.5, 1.5, 2.5, 3.5, 4.5, 5.5])
cb2.ax.set_yticklabels(['OK','Malezas','Compact.','Drenaje','Hongo','Compuesto'],
                        color=DIM, fontsize=5.5)
cb2.outline.set_edgecolor(GOLD)

# ── ROW 5: LEYENDA ALERTAS ────────────────────────────────────────────────────
ax_la = fig.add_subplot(gs[5])
ax_la.set_facecolor(BG3); ax_la.axis('off')
ax_la.axhline(y=1.0, color=GOLD+'44', lw=0.5)
alert_legend = [
    (ALERT_COLORS[1], 'Malezas invasoras'),
    (ALERT_COLORS[2], 'Compactación suelo'),
    (ALERT_COLORS[3], 'Drenaje deficiente'),
    (ALERT_COLORS[4], 'Hongo fúngico activo'),
    (ALERT_COLORS[5], 'Riesgo compuesto'),
    ('#ee22ee',        '○ Anillo fúngico (SAR)'),
]
for i, (col, label) in enumerate(alert_legend):
    x = 0.02 + i * 0.165
    rect = FancyBboxPatch((x, 0.22), 0.016, 0.55, boxstyle='square,pad=0',
                          facecolor=col, edgecolor='none', transform=ax_la.transAxes)
    ax_la.add_patch(rect)
    ax_la.text(x + 0.022, 0.50, label, color=WHITE, fontsize=7,
               fontfamily='monospace', va='center', transform=ax_la.transAxes)

# ── ROW 6: PANEL 3 — FUSIÓN COMPLETA ─────────────────────────────────────────
ax3 = fig.add_subplot(gs[6])
_ax_style(ax3, BG)
im3 = ax3.imshow(fusion_r, cmap=cmap_fusion, aspect='auto',
                 vmin=0, vmax=1, interpolation='bilinear')
_zone_sep(ax3)

# Contornos térmicos (estrés hídrico)
therm_n = np.clip((therm_r - 14) / 28, 0, 1)
ax3.contour(therm_n, levels=[0.28, 0.48, 0.68],
            colors=[CYAN+'66', ORANGE+'99', RED+'bb'],
            linewidths=[0.5, 0.7, 1.0])
ax3.text(Z2_ST+3, 170, 'Estrés T° →', color=ORANGE, fontsize=6,
         fontfamily='monospace', va='bottom', rotation=90)

_pitch_outline(ax3, F_R1, F_R2, F_C1, F_C2, GOLD+'88', 0.6)
for r1,r2,c1,c2 in [(CA1_R1,CA1_R2,CA1_C1,CA1_C2),(CA2_R1,CA2_R2,CA2_C1,CA2_C2),
                     (CA3_R1,CA3_R2,CA3_C1,CA3_C2),(CA4_R1,CA4_R2,CA4_C1,CA4_C2)]:
    _pitch_outline(ax3, r1, r2, c1, c2, GOLD+'55', 0.4)

ax3.text(96,  7, 'ESTADIO AMALFITANI', color=GOLD, fontsize=8, fontweight='bold',
         fontfamily='monospace', ha='center',
         bbox=dict(boxstyle='square,pad=0.2', facecolor=BG2, edgecolor=GOLD+'55', lw=0.5))
ax3.text(298, 7, 'VILLA OLÍMPICA DE VÉLEZ', color=CYAN, fontsize=8, fontweight='bold',
         fontfamily='monospace', ha='center',
         bbox=dict(boxstyle='square,pad=0.2', facecolor=BG2, edgecolor=CYAN+'55', lw=0.5))

for (cr, cc, zona) in [(FC_R, FC_C, 'campo'),
                        (CA1_CR, CA1_CC, 'c1'), (CA2_CR, CA2_CC, 'c2'),
                        (CA3_CR, CA3_CC, 'c3'), (CA4_CR, CA4_CC, 'c4')]:
    fv = FARO_AGRO[zona]
    col = _faro_color(fv)
    _label(ax3, cc, cr, f'FARO: {fv}', col, 8.5)

ax3.set_title(
    'ÍNDICE FUSIÓN FARO AGRONÓMICO  ·  NDRE × Térmica × SAR × InSAR × Alertas  ·  '
    'Contornos: estrés hídrico térmico',
    color=GOLD, fontsize=9, fontfamily='monospace', loc='left', pad=5)
ax3.set_xticks([]); ax3.set_yticks([])
cb3 = plt.colorbar(im3, ax=ax3, orientation='vertical', fraction=0.012, pad=0.006)
cb3.set_label('FARO', color=DIM, fontsize=7, fontfamily='monospace')
cb3.ax.tick_params(colors=DIM, labelsize=6); cb3.outline.set_edgecolor(GOLD)

# ── ROW 7: PANEL InSAR ESTRUCTURAL ────────────────────────────────────────────
ax_st = fig.add_subplot(gs[7])
ax_st.set_facecolor(BG3); ax_st.axis('off')
ax_st.axhline(y=1.0, color=GOLD+'55', lw=0.6)
ax_st.axhline(y=0.0, color='#111', lw=0.5)

ax_st.text(0.008, 0.88,
    'ANÁLISIS ESTRUCTURAL InSAR  ·  ΔZ milimétrico (12 días)  ·  Sentinel-1',
    color=GOLD, fontsize=8, fontweight='bold', fontfamily='monospace',
    transform=ax_st.transAxes, va='top')

trib_items = [
    ('TRIBUNA NORTE', INSAR['TN'], 'ESTABLE',   GREEN,  0.08),
    ('TRIBUNA SUR',   INSAR['TS'], 'ESTABLE',   GREEN,  0.27),
    ('TRIBUNA ESTE',  INSAR['TE'], 'ESTABLE',   GREEN,  0.46),
    ('TRIBUNA OESTE', INSAR['TO'], 'MODERADO', ORANGE,  0.65),
]
mx = 3.0
for label, val, estado, col, x in trib_items:
    bw = val / mx * 0.15
    ax_st.add_patch(FancyBboxPatch((x, 0.12), bw, 0.42, boxstyle='round,pad=0.005',
                    facecolor=col+'88', edgecolor=col, lw=0.8, transform=ax_st.transAxes))
    ax_st.text(x, 0.65, label, color=DIM, fontsize=6.5, fontfamily='monospace',
               transform=ax_st.transAxes, va='bottom')
    ax_st.text(x+bw+0.004, 0.30, f'{val:.2f} mm', color=col, fontsize=8,
               fontweight='bold', fontfamily='monospace', transform=ax_st.transAxes, va='center')
    ax_st.text(x, 0.04, estado, color=col, fontsize=6,
               fontfamily='monospace', transform=ax_st.transAxes, va='bottom')

ax_st.text(0.85, 0.88,
    '⚠  T. OESTE 2.38 mm — Estructura 1966 — Monitoreo continuo',
    color=ORANGE, fontsize=7.5, fontweight='bold', fontfamily='monospace',
    transform=ax_st.transAxes, va='top',
    bbox=dict(boxstyle='round,pad=0.3', facecolor='#1a0800', edgecolor=ORANGE+'99', lw=0.8))

# ── ROW 8: TABLA DE PRESCRIPCIÓN DETALLADA ────────────────────────────────────
ax_tb = fig.add_subplot(gs[8])
ax_tb.set_facecolor(BG3); ax_tb.axis('off')
ax_tb.axhline(y=1.0, color=GOLD, lw=0.8)

ax_tb.text(0.50, 0.975, 'TABLA DE PRESCRIPCIÓN AGRONÓMICA DETALLADA — VÉLEZ SARSFIELD · MAYO 2026',
    ha='center', va='top', color=GOLD, fontsize=9, fontweight='bold',
    fontfamily='monospace', transform=ax_tb.transAxes)

# Definición de columnas
COL_X  = [0.002, 0.14, 0.22, 0.29, 0.36, 0.44, 0.52, 0.60, 0.68, 0.76, 0.84, 0.93]
HEADERS = ['ZONA','NDVI','NDRE','N (kg/ha)','Riego (mm)','Resiembra','Tipo resiembra',
           'Hongos','Compact.','Drenaje','Malezas %','PRIORIDAD']
COL_W  = [0.136,0.076,0.066,0.066,0.066,0.076,0.160,0.076,0.076,0.076,0.066,0.086]

# Header row
HDR_Y = 0.92
for i, (hdr, cx, cw) in enumerate(zip(HEADERS, COL_X, COL_W)):
    ax_tb.add_patch(FancyBboxPatch((cx, HDR_Y-0.044), cw-0.002, 0.044,
                    boxstyle='square,pad=0', facecolor='#0d1520', edgecolor=GOLD+'55',
                    lw=0.4, transform=ax_tb.transAxes))
    ax_tb.text(cx+cw/2-0.001, HDR_Y-0.022, hdr, ha='center', va='center',
               color=GOLD, fontsize=5.8, fontweight='bold',
               fontfamily='monospace', transform=ax_tb.transAxes)

# Filas de datos
ROW_H = 0.148
ZONES = [
    ('campo', 'Campo Amalfitani'),
    ('c1',    'Cancha 1 Villa'),
    ('c2',    'Cancha 2 Villa'),
    ('c3',    'Cancha 3 Villa'),
    ('c4',    'Cancha 4 Villa'),
]
PRIORIDAD_COL = {
    'campo': ('#1a5020', GREEN,  'MEDIA'),
    'c1':    ('#1a2800', YELLOW, 'ALTA'),
    'c2':    ('#2a1800', ORANGE, 'ALTA'),
    'c3':    ('#2a0800', '#ff6000', 'URGENTE'),
    'c4':    ('#1a0000', RED,    'CRÍTICA'),
}
COMPACTACION_COL = {
    'campo': (DIM2, 'MODERADA'), 'c1': (ORANGE, 'ALTA'),
    'c2': (DIM2, 'MODERADA'), 'c3': (ORANGE, 'ALTA'), 'c4': (RED, 'SEVERA'),
}
DRENAJE_COL = {
    'campo': (GREEN, 'OK'), 'c1': (GREEN, 'OK'),
    'c2': (ORANGE, 'DEFICIENTE'), 'c3': (ORANGE, 'DEFICIENTE'), 'c4': (RED, 'CRÍTICO'),
}
HONGOS_COL = {
    'campo': (GREEN, 'NO'), 'c1': (ORANGE, 'SOSPECHOSO'),
    'c2': (GREEN, 'NO'), 'c3': (RED, 'ACTIVO'), 'c4': (RED, 'ACTIVO'),
}
N_COL = {
    'campo': (GREEN, f'{N_PRESC["campo"]} kg/ha'),
    'c1': (YELLOW, f'{N_PRESC["c1"]} kg/ha'),
    'c2': (ORANGE, f'{N_PRESC["c2"]} kg/ha'),
    'c3': ('#ff6000', f'{N_PRESC["c3"]} kg/ha'),
    'c4': (RED, f'{N_PRESC["c4"]} kg/ha'),
}
MALEZAS_COL = {
    'campo': (GREEN, '2%'), 'c1': (YELLOW, '8%'),
    'c2': (ORANGE, '12%'), 'c3': (ORANGE, '18%'), 'c4': (RED, '35%'),
}
NDVI_COL = {
    'campo': (GREEN, f'{NDVI["campo"]:.3f}'), 'c1': (YELLOW, f'{NDVI["c1"]:.3f}'),
    'c2': (YELLOW, f'{NDVI["c2"]:.3f}'), 'c3': (ORANGE, f'{NDVI["c3"]:.3f}'),
    'c4': (RED, f'{NDVI["c4"]:.3f}'),
}

for row_i, (zona, zona_label) in enumerate(ZONES):
    Y = HDR_Y - 0.046 - row_i * ROW_H
    bg_fc, pri_col, pri_txt = PRIORIDAD_COL[zona]
    faro_v = FARO_AGRO[zona]
    faro_c = _faro_color(faro_v)

    # Fondo de fila
    ax_tb.add_patch(FancyBboxPatch((0.002, Y-ROW_H+0.004), 0.995, ROW_H-0.006,
                    boxstyle='square,pad=0', facecolor=bg_fc,
                    edgecolor='#1a1a1a', lw=0.3, transform=ax_tb.transAxes))

    # Celda zona
    ax_tb.text(COL_X[0]+0.002, Y-ROW_H/2, zona_label, ha='left', va='center',
               color=WHITE, fontsize=7, fontweight='bold',
               fontfamily='monospace', transform=ax_tb.transAxes)
    # NDVI
    ndv_c, ndv_t = NDVI_COL[zona]
    ax_tb.text(COL_X[1]+COL_W[1]/2, Y-ROW_H/2, ndv_t, ha='center', va='center',
               color=ndv_c, fontsize=7, fontweight='bold',
               fontfamily='monospace', transform=ax_tb.transAxes)
    # NDRE
    ax_tb.text(COL_X[2]+COL_W[2]/2, Y-ROW_H/2, f'{NDRE[zona]:.3f}', ha='center', va='center',
               color=ndv_c, fontsize=7, fontfamily='monospace', transform=ax_tb.transAxes)
    # N kg/ha
    n_c, n_t = N_COL[zona]
    ax_tb.text(COL_X[3]+COL_W[3]/2, Y-ROW_H/2, n_t, ha='center', va='center',
               color=n_c, fontsize=7, fontweight='bold',
               fontfamily='monospace', transform=ax_tb.transAxes)
    # Riego mm
    riego_v = RIEGO[zona]
    riego_c = GREEN if riego_v <= 5 else (YELLOW if riego_v <= 8 else ORANGE if riego_v <= 10 else RED)
    ax_tb.text(COL_X[4]+COL_W[4]/2, Y-ROW_H/2, f'{riego_v} mm/d', ha='center', va='center',
               color=riego_c, fontsize=7, fontweight='bold',
               fontfamily='monospace', transform=ax_tb.transAxes)
    # Resiembra
    res_v = RESIEM[zona]
    res_c = GREEN if res_v < 100 else (YELLOW if res_v < 300 else (ORANGE if res_v < 700 else RED))
    ax_tb.text(COL_X[5]+COL_W[5]/2, Y-ROW_H/2, f'{res_v:,} m²', ha='center', va='center',
               color=res_c, fontsize=7, fontweight='bold',
               fontfamily='monospace', transform=ax_tb.transAxes)
    # Tipo resiembra
    ax_tb.text(COL_X[6]+0.002, Y-ROW_H/2, RESIEM_TIPO[zona], ha='left', va='center',
               color=res_c, fontsize=6, fontfamily='monospace', transform=ax_tb.transAxes)
    # Hongos
    h_c, h_t = HONGOS_COL[zona]
    ax_tb.text(COL_X[7]+COL_W[7]/2, Y-ROW_H/2, h_t, ha='center', va='center',
               color=h_c, fontsize=7, fontweight='bold',
               fontfamily='monospace', transform=ax_tb.transAxes)
    # Compactación
    cp_c, cp_t = COMPACTACION_COL[zona]
    ax_tb.text(COL_X[8]+COL_W[8]/2, Y-ROW_H/2, cp_t, ha='center', va='center',
               color=cp_c, fontsize=7, fontfamily='monospace', transform=ax_tb.transAxes)
    # Drenaje
    dr_c, dr_t = DRENAJE_COL[zona]
    ax_tb.text(COL_X[9]+COL_W[9]/2, Y-ROW_H/2, dr_t, ha='center', va='center',
               color=dr_c, fontsize=7, fontfamily='monospace', transform=ax_tb.transAxes)
    # Malezas
    ml_c, ml_t = MALEZAS_COL[zona]
    ax_tb.text(COL_X[10]+COL_W[10]/2, Y-ROW_H/2, ml_t, ha='center', va='center',
               color=ml_c, fontsize=7, fontweight='bold',
               fontfamily='monospace', transform=ax_tb.transAxes)
    # Prioridad
    ax_tb.text(COL_X[11]+COL_W[11]/2, Y-ROW_H/2, pri_txt, ha='center', va='center',
               color=pri_col, fontsize=7.5, fontweight='bold',
               fontfamily='monospace', transform=ax_tb.transAxes)

# Líneas de separación columnas
for cx in COL_X[1:]:
    ax_tb.axvline(x=cx, ymin=0.05, ymax=0.93, color='#1a1a1a', lw=0.3)

# FARO por zona (columna adicional en gráfico)
ax_tb.text(0.998, 0.96, 'FARO', ha='right', va='top', color=GOLD, fontsize=6.5,
           fontweight='bold', fontfamily='monospace', transform=ax_tb.transAxes)
for row_i, (zona, _) in enumerate(ZONES):
    Y = HDR_Y - 0.046 - row_i * ROW_H
    fv = FARO_AGRO[zona]
    ax_tb.text(0.998, Y-ROW_H/2, f'{fv}', ha='right', va='center',
               color=_faro_color(fv), fontsize=7, fontweight='bold',
               fontfamily='monospace', transform=ax_tb.transAxes)

# ── ROW 9: ALERTAS RESUMEN ────────────────────────────────────────────────────
ax_ar = fig.add_subplot(gs[9])
ax_ar.set_facecolor('#0d0200'); ax_ar.axis('off')
ax_ar.axhline(y=1.0, color=RED, lw=1.2)
ax_ar.axhline(y=0.0, color=RED+'55', lw=0.5)

ax_ar.text(0.008, 0.75, '⚠  ALERTAS PRIORITARIAS:',
    color=RED, fontsize=8, fontweight='bold', fontfamily='monospace',
    transform=ax_ar.transAxes, va='top')
ax_ar.text(0.25, 0.75,
    'CANCHA 4 — Infección fúngica activa 85 m² + Drenaje crítico + Malezas 35% + Resiembra total 1,840 m² → INTERVENCIÓN URGENTE',
    color=RED, fontsize=7.5, fontweight='bold', fontfamily='monospace',
    transform=ax_ar.transAxes, va='top')
ax_ar.text(0.008, 0.18,
    '⚠  TRIBUNA OESTE — InSAR ΔZ 2.38 mm — Estructura 1966 — Monitoreo continuo recomendado',
    color=ORANGE, fontsize=7.5, fontweight='bold', fontfamily='monospace',
    transform=ax_ar.transAxes, va='top')

# ── ROW 10: MÉTRICAS GLOBALES ─────────────────────────────────────────────────
ax_mg = fig.add_subplot(gs[10])
ax_mg.set_facecolor(BG3); ax_mg.axis('off')
ax_mg.axhline(y=1.0, color='#111', lw=0.5)
glob_metrics = [
    ('FARO GLOBAL', f'{FARO_GLOBAL}', _faro_color(FARO_GLOBAL)),
    ('NDVI campo', f'{NDVI["campo"]:.3f}', GREEN),
    ('NDVI C4', f'{NDVI["c4"]:.3f}', RED),
    ('N máx C4', f'{N_PRESC["c4"]} kg/ha', RED),
    ('Riego máx C4', f'{RIEGO["c4"]} mm/d', RED),
    ('Resiembra total', f'{sum(RESIEM.values()):,} m²', ORANGE),
    ('Hongo activo', '2 zonas', RED),
    ('InSAR T.Oeste', f'{INSAR["TO"]} mm', ORANGE),
    ('Malezas C4', f'{MALEZAS_PCT["c4"]}%', RED),
    ('Canchas críticas', '1 (C4)', RED),
    ('Canchas urgentes', '2 (C3+C4)', ORANGE),
    ('Zonas monitoreadas', '5', GOLD),
]
step_g = 1.0 / len(glob_metrics)
for i, (lbl, val, col) in enumerate(glob_metrics):
    x = 0.004 + i * step_g
    ax_mg.text(x, 0.92, lbl, color=DIM2, fontsize=5.8, fontfamily='monospace',
               transform=ax_mg.transAxes, va='top')
    ax_mg.text(x, 0.50, val,  color=col,  fontsize=8.5, fontweight='bold',
               fontfamily='monospace', transform=ax_mg.transAxes, va='top')

# ── ROW 11: FOOTER SHA-256 ────────────────────────────────────────────────────
ax_ft = fig.add_subplot(gs[11])
ax_ft.set_facecolor(BG); ax_ft.axis('off')
ax_ft.axhline(y=1.0, color=GOLD, lw=0.9)

ax_ft.add_patch(FancyBboxPatch((0.008, 0.04), 0.54, 0.90,
                boxstyle='round,pad=0.01', linewidth=0.8,
                edgecolor='#181818', facecolor='#060606', transform=ax_ft.transAxes))
ax_ft.text(0.014, 0.90, 'SHA-256  ·  EVIDENCIA CERTIFICADA  ·  INMUTABLE',
    color=GOLD, fontsize=7.5, fontweight='bold', fontfamily='monospace',
    transform=ax_ft.transAxes, va='top')
ax_ft.text(0.014, 0.66, SHA256[:48],
    color=WHITE, fontsize=8.5, fontfamily='monospace', transform=ax_ft.transAxes, va='top')
ax_ft.text(0.014, 0.44, SHA256[48:],
    color=WHITE, fontsize=8.5, fontfamily='monospace', transform=ax_ft.transAxes, va='top')
ax_ft.text(0.014, 0.22,
    f'Sentinel-1 GRD VV  ·  Sentinel-2 L2A  ·  Copernicus Data Space  ·  NASA FIRMS  ·  '
    f'{datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}',
    color=DIM, fontsize=6.5, fontfamily='monospace', transform=ax_ft.transAxes, va='top')
ax_ft.text(0.014, 0.06,
    'Datos: Sentinel-1 GRD · Sentinel-2 L2A · Copernicus Data Space · NASA FIRMS',
    color=DIM2, fontsize=6.5, fontfamily='monospace', transform=ax_ft.transAxes, va='top')

# Panel derecho
ax_ft.text(0.558, 0.92, 'FARO PROTOCOL  ·  GESTIÓN AGRONÓMICA',
    color=GOLD, fontsize=8.5, fontweight='bold', fontfamily='monospace',
    transform=ax_ft.transAxes, va='top')
ax_ft.text(0.558, 0.70, f'{FARO_GLOBAL}  —  {"ÓPTIMO" if FARO_GLOBAL>=70 else "BUENO" if FARO_GLOBAL>=55 else "REGULAR"}',
    color=_faro_color(FARO_GLOBAL), fontsize=14, fontweight='bold', fontfamily='monospace',
    transform=ax_ft.transAxes, va='top')
ax_ft.text(0.558, 0.46,
    f'Zonas: Campo + Canchas 1-4  ·  Vélez Sarsfield\n'
    f'NDVI rango: {NDVI["c4"]:.3f}–{NDVI["campo"]:.3f}  ·  '
    f'N prescripción: {N_PRESC["campo"]}–{N_PRESC["c4"]} kg/ha\n'
    f'Resiembra total estimada: {sum(RESIEM.values()):,} m²  ·  5 zonas',
    color=DIM, fontsize=7.5, fontfamily='monospace',
    transform=ax_ft.transAxes, va='top', linespacing=1.65)
ax_ft.text(0.558, 0.08,
    'Informe Base  ·  Estado Cero  ·  Mayo 2026',
    color=GOLD_L, fontsize=8.5, fontweight='bold', fontfamily='monospace',
    transform=ax_ft.transAxes, va='bottom')

# ── ROW 12: Línea dorada inferior ─────────────────────────────────────────────
fig.add_subplot(gs[12]).set_facecolor(GOLD)

# ── Guardar ───────────────────────────────────────────────────────────────────
print("  → Guardando PNG...")
plt.savefig(OUTPUT, dpi=150, bbox_inches='tight', facecolor=BG, edgecolor='none')
plt.close()

size_kb = OUTPUT.stat().st_size / 1024
print()
print("=" * 68)
print(f"  REPORTE AGRONÓMICO GENERADO")
print(f"  Archivo : {OUTPUT}")
print(f"  Tamaño  : {size_kb:.0f} KB  |  150 dpi  |  RGB")
print(f"  SHA-256 : {SHA256[:28]}...")
print(f"  FARO Global: {FARO_GLOBAL}")
print(f"  Capas   : Prescripción · Alertas · Fusión · InSAR · Tabla")
print(f"  Alertas : Hongos (2 zonas) · Drenaje crítico C4 · T.Oeste 2.38mm")
print("=" * 68)
