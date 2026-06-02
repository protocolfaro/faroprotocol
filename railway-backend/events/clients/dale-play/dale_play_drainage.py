"""
dale_play_drainage.py — Red de drenaje Amalfitani basada en planos C&G reales.

Infraestructura documentada (planos constructivos Estadio José Amalfitani):
  - 14 líneas de drenaje interno paralelas, cada 5m, orientación E-O
  - Colectores internos: cañería 110mm PVC corrugado
  - Colector principal: cañería 200mm PVC hacia fosa central
  - Drenaje perimetral tipo guardaganado: 380 ml (metros lineales)
  - Toda la red descarga en fosa central (sumidero SE del campo)
  - Profundidad de instalación: 0.40-0.60m bajo nivel de juego

Reemplaza el modelo SoilGrids Ksat por cálculo basado en infraestructura real.
La capacidad hidráulica efectiva se calcula desde el paso de los drenes, el
diámetro de las cañerías y la pendiente hidráulica hacia la fosa central.
"""
from __future__ import annotations

import json
import logging
import pathlib
from datetime import datetime
from typing import Optional

log = logging.getLogger(__name__)

MODELS_DIR = pathlib.Path(__file__).parent / "models"

# ── Parámetros de infraestructura real C&G ────────────────────────────────────

_INFRA = {
    "n_lineas_internas":        14,
    "paso_lineas_m":            5.0,        # distancia entre líneas paralelas
    "diametro_interno_mm":      110,        # cañería colectora por línea
    "diametro_principal_mm":    200,        # colector principal a fosa
    "perimetral_ml":            380,        # metros lineales drenaje guardaganado
    "profundidad_min_m":        0.40,
    "profundidad_max_m":        0.60,
    "fosa_central_pos":         "SE",       # posición de la fosa de descarga
    "orientacion_lineas":       "E-O",
    "pendiente_hidraulica_pct": 0.5,        # pendiente mínima hacia fosa (%)
}

# Campo: 105m largo × 68m ancho
_CAMPO = {"largo_m": 105, "ancho_m": 68, "area_m2": 105 * 68}

# Coeficiente de Manning para PVC corrugado
_MANNING_N = 0.011

# Lluvia de diseño: tormenta de 2 años de recurrencia, Buenos Aires (~50mm/h)
_LLUVIA_DISENO_MM_H = 50.0


# ── Cálculo hidráulico basado en infraestructura ──────────────────────────────

def _caudal_manning(diametro_mm: float, pendiente_pct: float) -> float:
    """
    Caudal máximo de una cañería circular a sección llena (Manning).
    Q [L/s] = (1/n) * A * R^(2/3) * S^(1/2)
    """
    d_m = diametro_mm / 1000.0
    S   = pendiente_pct / 100.0
    A   = 3.14159 * (d_m / 2) ** 2         # área sección [m²]
    R   = d_m / 4                           # radio hidráulico [m]
    Q_m3s = (1 / _MANNING_N) * A * R ** (2/3) * S ** (1/2)
    return round(Q_m3s * 1000, 2)           # L/s


def _capacidad_sistema() -> dict:
    """
    Capacidad hidráulica total del sistema de drenaje.
    Calcula desde cañerías individuales + colector principal.
    """
    S = _INFRA["pendiente_hidraulica_pct"]

    # Caudal por línea individual (110mm)
    q_linea = _caudal_manning(_INFRA["diametro_interno_mm"], S)
    # Total 14 líneas
    q_total_lineas = round(q_linea * _INFRA["n_lineas_internas"], 2)
    # Colector principal (200mm) — cuello de botella
    q_principal    = _caudal_manning(_INFRA["diametro_principal_mm"], S)
    # Capacidad efectiva = mínimo entre total líneas y colector principal
    q_efectivo     = min(q_total_lineas, q_principal)

    # Lluvia de diseño → caudal a drenar [L/s]
    area_m2   = _CAMPO["area_m2"]
    q_lluvia  = round((_LLUVIA_DISENO_MM_H / 3600) * area_m2, 2)  # L/s

    # Tiempo de vaciado estimado para 10mm de lluvia acumulada
    vol_10mm  = area_m2 * 0.010 * 1000      # litros
    t_vac_min = round(vol_10mm / (q_efectivo * 60), 1) if q_efectivo > 0 else 999

    return {
        "q_por_linea_ls":       q_linea,
        "q_total_lineas_ls":    q_total_lineas,
        "q_colector_principal_ls": q_principal,
        "q_efectivo_ls":        q_efectivo,
        "q_lluvia_diseno_ls":   q_lluvia,
        "superavit_ls":         round(q_efectivo - q_lluvia, 2),
        "sistema_suficiente":   q_efectivo >= q_lluvia,
        "tiempo_vac_10mm_min":  t_vac_min,
        "lluvia_diseno_mm_h":   _LLUVIA_DISENO_MM_H,
    }


def _zonas_desde_infraestructura(lluvia_48h_mm: float = 0.0) -> list[dict]:
    """
    Genera zonas de riesgo basadas en la posición real de las cañerías.

    Las 14 líneas E-O cada 5m crean franjas de excavación.
    La línea perimetral tipo guardaganado crea un anillo de exclusión en bordes.
    La fosa central (SE) es la zona de mayor concentración de carga hidráulica.

    lluvia_48h_mm: lluvia previa — satura las franjas de drenaje y eleva el riesgo.
    """
    # Factor de saturación por lluvia previa
    if lluvia_48h_mm >= 30:
        sat_factor  = 0.70   # suelo saturado → capacidad −30%
        sat_nota    = f"Suelo saturado (lluvia 48h={lluvia_48h_mm:.0f}mm) — capacidad reducida 30%"
        riesgo_base = "alto"
    elif lluvia_48h_mm >= 15:
        sat_factor  = 0.85
        sat_nota    = f"Suelo húmedo (lluvia 48h={lluvia_48h_mm:.0f}mm) — capacidad reducida 15%"
        riesgo_base = "medio"
    else:
        sat_factor  = 1.00
        sat_nota    = ""
        riesgo_base = "bajo"

    def kpa(base: float) -> int:
        return max(int(base * sat_factor), 10)

    zonas = [
        # Fosa central SE — zona de máxima carga hidráulica y terreno removido
        {
            "id":           "fosa_central",
            "nombre":       "Fosa Central (SE)",
            "tipo":         "exclusion",
            "descripcion":  (
                "Sumidero de toda la red de drenaje. Suelo excavado y compactado diferencialmente. "
                "Prohibido apoyar estructuras. Radio de exclusión: 3m desde borde fosa."
            ),
            "x_pct":        [78, 95],
            "y_pct":        [0, 22],
            "capacidad_kpa": kpa(25),
            "riesgo":       "alto",
            "color":        "rojo",
            "infraestructura": "Fosa descarga colector 200mm — toda la red converge aquí",
        },
        # Colector principal 200mm — eje de descarga
        {
            "id":           "colector_principal",
            "nombre":       "Colector Principal 200mm",
            "tipo":         "exclusion",
            "descripcion":  (
                "Cañería 200mm de colección principal hacia fosa SE. "
                "Franja de 1m a cada lado de la cañería: sin cargas puntuales > 50kPa. "
                "Tarimas distribuidoras obligatorias."
            ),
            "x_pct":        [72, 95],
            "y_pct":        [0, 100],
            "capacidad_kpa": kpa(38),
            "riesgo":       "alto",
            "color":        "rojo",
            "infraestructura": "Colector 200mm PVC · pendiente 0.5% hacia SE",
        },
        # Drenaje perimetral guardaganado — anillo externo
        {
            "id":           "perimetral_guardaganado",
            "nombre":       "Perímetro — Drenaje Guardaganado (380ml)",
            "tipo":         "precaucion",
            "descripcion":  (
                "Drenaje perimetral tipo guardaganado, 380 metros lineales. "
                "Franja de 0.8m en todo el borde del campo. "
                "Estructuras sobre esta franja: calzar con plataformas distribuidoras."
            ),
            "x_pct":        [0, 100],
            "y_pct":        [0, 8],
            "capacidad_kpa": kpa(52),
            "riesgo":       "medio",
            "color":        "amarillo",
            "infraestructura": "Guardaganado perimetral 380ml · conecta a colector principal",
        },
        # Franjas de drenaje interno (14 líneas E-O cada 5m) — zona media
        {
            "id":           "franjas_drenaje_norte",
            "nombre":       "Franjas Drenaje N (líneas 1-5)",
            "tipo":         "precaucion",
            "descripcion":  (
                "Franjas de excavación de líneas de drenaje 1-5 (zona norte). "
                "Cañería 110mm PVC corrugado a 0.40-0.60m de profundidad. "
                "Evitar cargas puntuales alineadas con la dirección de la cañería (E-O). "
                "Cargas transversales distribuidas: OK."
            ),
            "x_pct":        [5, 95],
            "y_pct":        [62, 95],
            "capacidad_kpa": kpa(65),
            "riesgo":       "medio",
            "color":        "amarillo",
            "infraestructura": f"5 líneas 110mm · paso 5m · orientación {_INFRA['orientacion_lineas']}",
        },
        {
            "id":           "franjas_drenaje_sur",
            "nombre":       "Franjas Drenaje S (líneas 10-14)",
            "tipo":         "precaucion",
            "descripcion":  (
                "Franjas de excavación líneas 10-14 (zona sur, más cercana a fosa). "
                "Mayor riesgo por convergencia hidráulica hacia SE. "
                "Priorizar distribución de carga en esta zona."
            ),
            "x_pct":        [5, 72],
            "y_pct":        [5, 38],
            "capacidad_kpa": kpa(58),
            "riesgo":       "medio",
            "color":        "amarillo",
            "infraestructura": "5 líneas 110mm · zona de convergencia hacia fosa SE",
        },
        # Zona central — entre líneas de drenaje, máxima capacidad
        {
            "id":           "centro_campo",
            "nombre":       "Centro Campo",
            "tipo":         "seguro",
            "descripcion":  (
                "Área entre franjas de drenaje (mitad del campo). "
                "Sin cañerías en los primeros 0.6m de profundidad. "
                "Zona óptima para FOH, consola de sonido y estructuras livianas."
            ),
            "x_pct":        [8, 70],
            "y_pct":        [40, 60],
            "capacidad_kpa": kpa(115),
            "riesgo":       riesgo_base,
            "color":        "verde" if sat_factor == 1.0 else "amarillo",
            "infraestructura": "Sin cañerías superficiales · suelo nativo entre líneas",
        },
        # Zona norte — opuesta a fosa, menor convergencia hidráulica
        {
            "id":           "area_norte",
            "nombre":       "Área Norte",
            "tipo":         "seguro",
            "descripcion":  (
                "Zona más alejada de la fosa central. Menor saturación hidráulica. "
                "Ideal para estructuras pesadas y equipos de backline. "
                "Verificar que el perímetro guardaganado no corra bajo el equipo."
            ),
            "x_pct":        [10, 88],
            "y_pct":        [82, 98],
            "capacidad_kpa": kpa(122),
            "riesgo":       riesgo_base,
            "color":        "verde" if sat_factor == 1.0 else "amarillo",
            "infraestructura": "Extremo opuesto a fosa — drenaje primario hacia SE",
        },
    ]

    # Agregar nota de saturación a todas las zonas si aplica
    if sat_nota:
        for z in zonas:
            z["nota_lluvia"] = sat_nota

    return zonas


# ── Entry point ───────────────────────────────────────────────────────────────

def analyze_drainage(
    lat: float = -34.6379,
    lon: float = -58.5288,
    venue: str = "Estadio José Amalfitani",
    lluvia_48h_mm: float = 0.0,
) -> dict:
    """
    Análisis de drenaje basado en infraestructura real C&G del Amalfitani.

    Reemplaza el modelo SoilGrids Ksat por cálculo directo desde:
      - 14 líneas internas 110mm cada 5m (E-O)
      - Colector principal 200mm hacia fosa SE
      - Perimetral guardaganado 380ml
      - Ecuación de Manning para caudal máximo
    """
    hidraulica = _capacidad_sistema()
    zonas      = _zonas_desde_infraestructura(lluvia_48h_mm)

    excl = sum(1 for z in zonas if z["tipo"] == "exclusion")
    prec = sum(1 for z in zonas if z["tipo"] == "precaucion")
    seg  = sum(1 for z in zonas if z["tipo"] == "seguro")

    # Riesgo global
    if not hidraulica["sistema_suficiente"] or lluvia_48h_mm >= 30:
        riesgo_global = "alto"
    elif lluvia_48h_mm >= 15:
        riesgo_global = "medio"
    else:
        riesgo_global = "bajo"

    result = {
        "venue":           venue,
        "lat":             lat,
        "lon":             lon,
        "fecha_analisis":  datetime.now().strftime("%Y-%m-%d"),
        "metodo":          (
            "Infraestructura real C&G — 14 líneas 110mm + colector 200mm + "
            "guardaganado 380ml · Manning Q=AR^(2/3)S^(1/2)/n"
        ),
        "campo":           {**_CAMPO, "orientacion": "N-S"},
        "infraestructura": _INFRA,
        "hidraulica":      {
            **hidraulica,
            "manning_n":    _MANNING_N,
            "nota":         (
                f"Sistema {'SUFICIENTE' if hidraulica['sistema_suficiente'] else 'INSUFICIENTE'} "
                f"para lluvia de diseño {_LLUVIA_DISENO_MM_H}mm/h. "
                f"Cuello de botella: colector 200mm ({hidraulica['q_colector_principal_ls']}L/s). "
                f"Tiempo vaciado 10mm lluvia: ~{hidraulica['tiempo_vac_10mm_min']} min."
            ),
        },
        "lluvia_48h_mm":   lluvia_48h_mm,
        "zonas":           zonas,
        "exclusiones": [
            f"Fosa central SE: radio mínimo de exclusión 3m desde borde",
            f"Colector 200mm: franja 1m a cada lado — tarimas distribuidoras obligatorias",
            f"Líneas 110mm: evitar cargas puntuales alineadas E-O sobre la franja de la cañería",
            f"Perímetro guardaganado: estructuras en borde deben calzarse (plataformas mín. 2m²)",
        ],
        "resumen": {
            "zonas_exclusion":      excl,
            "zonas_precaucion":     prec,
            "zonas_seguras":        seg,
            "capacidad_min_kpa":    min(z["capacidad_kpa"] for z in zonas),
            "capacidad_max_kpa":    max(z["capacidad_kpa"] for z in zonas),
            "riesgo_global":        riesgo_global,
            "q_efectivo_ls":        hidraulica["q_efectivo_ls"],
            "sistema_suficiente":   hidraulica["sistema_suficiente"],
            "n_lineas_drenaje":     _INFRA["n_lineas_internas"],
            "perimetral_ml":        _INFRA["perimetral_ml"],
        },
        "fuente_datos":    "Planos constructivos C&G · Estadio José Amalfitani",
    }

    out_path = MODELS_DIR / "drainage_amalfitani.json"
    MODELS_DIR.mkdir(exist_ok=True)
    out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


if __name__ == "__main__":
    r = analyze_drainage(lluvia_48h_mm=0)
    h = r["hidraulica"]
    s = r["resumen"]
    print(
        f"Drenaje Amalfitani (C&G real):\n"
        f"  {s['n_lineas_drenaje']} líneas 110mm + colector 200mm + {s['perimetral_ml']}ml guardaganado\n"
        f"  Q efectivo: {s['q_efectivo_ls']} L/s | Sistema: {'OK' if s['sistema_suficiente'] else 'INSUFICIENTE'}\n"
        f"  {s['zonas_exclusion']} exclusiones · {s['zonas_precaucion']} precaución · {s['zonas_seguras']} seguras\n"
        f"  Riesgo global: {s['riesgo_global']}"
    )
