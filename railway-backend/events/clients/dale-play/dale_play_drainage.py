"""
dale_play_drainage.py — Red de drenaje Amalfitani basada en planos C&G reales.

Infraestructura documentada (planos constructivos Estadio José Amalfitani):
  - 14 líneas de drenaje interno longitudinales, cada 5m, orientación E-O
  - Cañería interna: DRENAFLEX AWACOR 118mm (diámetro exterior nominal 110mm)
  - 2 colectores principales: AWACOR CL Y PL 218mm, uno detrás de cada arco
    (cabecera Norte y cabecera Sur) — cada colector recibe 7 líneas
  - 4 cámaras colectoras en vértices del campo — reciben agua de líneas + perimetral
  - Toda la red descarga en fosa de descarga (Norte / Platea Sur Baja, plano 02)
  - Drenaje perimetral tipo guardaganado: 380 ml, canaleta 15cm de ancho
    Montado sobre camino de césped sintético perimetral, agua hacia vértices
  - Profundidad de instalación: 0.40-0.60m bajo nivel de juego
  - Agua de fosa reutilizada para riego

Reemplaza el modelo SoilGrids Ksat por cálculo basado en infraestructura real.
La capacidad hidráulica efectiva se calcula con Manning desde cada subsistema.
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
    "n_lineas_internas":            14,
    "paso_lineas_m":                5.0,     # distancia entre líneas paralelas
    "diametro_interno_mm":          118,     # DRENAFLEX AWACOR 118 (ext nominal 110mm)
    "diametro_principal_mm":        218,     # AWACOR CL Y PL 218 (ext nominal 200mm)
    "n_colectores_principales":     2,       # uno por cabecera: Norte y Sur
    "perimetral_ml":                380,     # metros lineales guardaganado
    "ancho_canaleta_perimetral_cm": 15,      # canaleta guardaganado 15cm ancho
    "camaras_vertices":             4,       # cámaras en 4 vértices del campo
    "profundidad_min_m":            0.40,
    "profundidad_max_m":            0.60,
    "fosa_descarga_pos":            "Norte", # fosa descarga (plano 02 C&G, Platea Sur Baja)
    "orientacion_lineas":           "E-O",
    "pendiente_hidraulica_pct":     0.5,     # pendiente mínima hacia colectores (%)
    "reutilizacion_riego":          True,    # agua fosa reutilizada para riego
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

    Topología real (planos C&G):
      14 líneas 118mm → 2 colectores 218mm en paralelo (7 líneas c/u, una por cabecera)
      → 4 cámaras en vértices → fosa de descarga Norte
    """
    S = _INFRA["pendiente_hidraulica_pct"]

    # Caudal por línea individual (118mm DRENAFLEX AWACOR)
    q_linea = _caudal_manning(_INFRA["diametro_interno_mm"], S)
    # Total 14 líneas
    q_total_lineas = round(q_linea * _INFRA["n_lineas_internas"], 2)
    # Cada colector 218mm AWACOR — recibe 7 líneas (mitad campo)
    q_colector_uno = _caudal_manning(_INFRA["diametro_principal_mm"], S)
    # 2 colectores en paralelo (Norte + Sur)
    q_colectores   = round(q_colector_uno * _INFRA["n_colectores_principales"], 2)
    # Capacidad efectiva = mínimo entre total líneas y total colectores
    q_efectivo     = min(q_total_lineas, q_colectores)

    # Lluvia de diseño → caudal a drenar [L/s]
    area_m2   = _CAMPO["area_m2"]
    q_lluvia  = round((_LLUVIA_DISENO_MM_H / 3600) * area_m2, 2)  # L/s

    # Tiempo de vaciado estimado para 10mm de lluvia acumulada
    vol_10mm  = area_m2 * 0.010 * 1000      # litros
    t_vac_min = round(vol_10mm / (q_efectivo * 60), 1) if q_efectivo > 0 else 999

    return {
        "q_por_linea_ls":          q_linea,
        "q_total_lineas_ls":       q_total_lineas,
        "q_colector_uno_ls":       q_colector_uno,
        "q_colectores_total_ls":   q_colectores,
        "n_colectores":            _INFRA["n_colectores_principales"],
        "q_efectivo_ls":           q_efectivo,
        "q_lluvia_diseno_ls":      q_lluvia,
        "superavit_ls":            round(q_efectivo - q_lluvia, 2),
        "sistema_suficiente":      q_efectivo >= q_lluvia,
        "tiempo_vac_10mm_min":     t_vac_min,
        "lluvia_diseno_mm_h":      _LLUVIA_DISENO_MM_H,
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
        # Fosa de descarga Norte — zona de máxima carga hidráulica
        {
            "id":           "fosa_descarga",
            "nombre":       "Fosa de Descarga (Norte / Platea Sur Baja)",
            "tipo":         "exclusion",
            "descripcion":  (
                "Punto de descarga final de toda la red de drenaje. Agua reutilizada para riego. "
                "Suelo excavado con diferente compactación. "
                "Prohibido apoyar estructuras. Radio de exclusión: 3m desde borde fosa."
            ),
            "x_pct":        [5, 22],
            "y_pct":        [78, 98],
            "capacidad_kpa": kpa(25),
            "riesgo":       "alto",
            "color":        "rojo",
            "infraestructura": "Fosa descarga · recibe 2 colectores 218mm · plano 02 C&G",
        },
        # Colectores 218mm detrás de arcos — zonas de exclusión por cabecera
        {
            "id":           "colector_cabecera_norte",
            "nombre":       "Colector 218mm — Cabecera Norte",
            "tipo":         "exclusion",
            "descripcion":  (
                "Cañería colectora 218mm AWACOR detrás del arco Norte. "
                "Recibe las 7 líneas 118mm del sector Norte. "
                "Franja de 1m a cada lado: sin cargas puntuales. Tarimas distribuidoras obligatorias."
            ),
            "x_pct":        [5, 95],
            "y_pct":        [82, 98],
            "capacidad_kpa": kpa(38),
            "riesgo":       "alto",
            "color":        "rojo",
            "infraestructura": "Colector 218mm AWACOR · pendiente 0.5% → vértices → fosa Norte",
        },
        {
            "id":           "colector_cabecera_sur",
            "nombre":       "Colector 218mm — Cabecera Sur",
            "tipo":         "exclusion",
            "descripcion":  (
                "Cañería colectora 218mm AWACOR detrás del arco Sur. "
                "Recibe las 7 líneas 118mm del sector Sur. "
                "Franja de 1m a cada lado: sin cargas puntuales. Tarimas distribuidoras obligatorias."
            ),
            "x_pct":        [5, 95],
            "y_pct":        [2, 18],
            "capacidad_kpa": kpa(38),
            "riesgo":       "alto",
            "color":        "rojo",
            "infraestructura": "Colector 218mm AWACOR · pendiente 0.5% → vértices → fosa Norte",
        },
        # Drenaje perimetral guardaganado — canaleta 15cm sobre camino sintético
        {
            "id":           "perimetral_guardaganado",
            "nombre":       "Perímetro — Drenaje Guardaganado (380ml, 15cm)",
            "tipo":         "precaucion",
            "descripcion":  (
                "Canaleta tipo guardaganado, 15cm de ancho, 380 metros lineales. "
                "Montado sobre camino de césped sintético perimetral (no sobre el campo). "
                "Agua fluye del centro hacia los vértices → cámaras → fosa. "
                "Estructuras en borde de campo: calzar con plataformas distribuidoras."
            ),
            "x_pct":        [0, 100],
            "y_pct":        [0, 5],
            "capacidad_kpa": kpa(52),
            "riesgo":       "medio",
            "color":        "amarillo",
            "infraestructura": "Guardaganado 380ml · 15cm ancho · camino sintético perimetral",
        },
        # Cámaras en vértices — zona de cruce hidráulico
        {
            "id":           "camaras_vertices",
            "nombre":       "Cámaras Vértices (×4)",
            "tipo":         "exclusion",
            "descripcion":  (
                "4 cámaras colectoras en los vértices del campo. Reciben agua de las "
                "14 líneas internas + perimetral guardaganado. Punto de máxima concentración "
                "de flujo. Radio de exclusión 2m desde cada cámara."
            ),
            "x_pct":        [0, 12],
            "y_pct":        [0, 12],
            "capacidad_kpa": kpa(30),
            "riesgo":       "alto",
            "color":        "rojo",
            "infraestructura": "4 cámaras en esquinas · confluencia líneas internas + perimetral",
        },
        # Franjas de drenaje interno — sector Norte (líneas 1-7)
        {
            "id":           "franjas_drenaje_norte",
            "nombre":       "Franjas Drenaje — Sector Norte (líneas 1-7)",
            "tipo":         "precaucion",
            "descripcion":  (
                "Franjas de excavación líneas 1-7, sector Norte. "
                "DRENAFLEX AWACOR 118mm a 0.40-0.60m de profundidad. "
                "Líneas parten desde mediocampo hacia cabecera Norte. "
                "Evitar cargas puntuales alineadas E-O sobre la franja. OK transversal."
            ),
            "x_pct":        [5, 95],
            "y_pct":        [52, 80],
            "capacidad_kpa": kpa(65),
            "riesgo":       "medio",
            "color":        "amarillo",
            "infraestructura": f"7 líneas 118mm · paso 5m · orientación {_INFRA['orientacion_lineas']} → colector Norte",
        },
        # Franjas de drenaje interno — sector Sur (líneas 8-14)
        {
            "id":           "franjas_drenaje_sur",
            "nombre":       "Franjas Drenaje — Sector Sur (líneas 8-14)",
            "tipo":         "precaucion",
            "descripcion":  (
                "Franjas de excavación líneas 8-14, sector Sur. "
                "DRENAFLEX AWACOR 118mm a 0.40-0.60m de profundidad. "
                "Líneas parten desde mediocampo hacia cabecera Sur. "
                "Evitar cargas puntuales alineadas E-O sobre la franja."
            ),
            "x_pct":        [5, 95],
            "y_pct":        [20, 48],
            "capacidad_kpa": kpa(62),
            "riesgo":       "medio",
            "color":        "amarillo",
            "infraestructura": f"7 líneas 118mm · paso 5m · orientación {_INFRA['orientacion_lineas']} → colector Sur",
        },
        # Mediocampo — punto de partida de las 14 líneas, menor flujo acumulado
        {
            "id":           "mediocampo",
            "nombre":       "Mediocampo",
            "tipo":         "seguro",
            "descripcion":  (
                "Punto de partida de las 14 líneas de drenaje. Menor flujo acumulado "
                "que las cabeceras. Zona óptima para FOH, consola de sonido y estructuras livianas."
            ),
            "x_pct":        [8, 92],
            "y_pct":        [44, 56],
            "capacidad_kpa": kpa(115),
            "riesgo":       riesgo_base,
            "color":        "verde" if sat_factor == 1.0 else "amarillo",
            "infraestructura": "Punto de partida drenes — mínimo flujo acumulado",
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
      - 14 líneas internas DRENAFLEX AWACOR 118mm cada 5m (E-O)
      - 2 colectores AWACOR 218mm, uno por cabecera (Norte y Sur)
      - 4 cámaras en vértices + fosa de descarga Norte
      - Perimetral guardaganado 380ml, canaleta 15cm ancho
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
            "Infraestructura real C&G — 14 líneas DRENAFLEX AWACOR 118mm + "
            "2 colectores AWACOR 218mm (Norte+Sur) + 4 cámaras vértices + "
            "guardaganado 380ml/15cm · Manning Q=AR^(2/3)S^(1/2)/n"
        ),
        "campo":           {**_CAMPO, "orientacion": "N-S"},
        "infraestructura": _INFRA,
        "hidraulica":      {
            **hidraulica,
            "manning_n":    _MANNING_N,
            "nota":         (
                f"Sistema {'SUFICIENTE' if hidraulica['sistema_suficiente'] else 'INSUFICIENTE'} "
                f"para lluvia de diseño {_LLUVIA_DISENO_MM_H}mm/h. "
                f"2 colectores 218mm en paralelo: {hidraulica['q_colectores_total_ls']}L/s total. "
                f"Tiempo vaciado 10mm lluvia: ~{hidraulica['tiempo_vac_10mm_min']} min."
            ),
        },
        "lluvia_48h_mm":   lluvia_48h_mm,
        "zonas":           zonas,
        "exclusiones": [
            "Fosa descarga Norte: radio mínimo de exclusión 3m desde borde",
            "Colectores 218mm (Norte+Sur): franja 1m a cada lado — tarimas distribuidoras obligatorias",
            "4 cámaras en vértices: radio de exclusión 2m desde cada cámara",
            "Líneas 118mm: evitar cargas puntuales alineadas E-O sobre la franja de la cañería",
            "Perímetro guardaganado (canaleta 15cm): estructuras en borde deben calzarse (plataformas mín. 2m²)",
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
            "n_colectores":         _INFRA["n_colectores_principales"],
            "perimetral_ml":        _INFRA["perimetral_ml"],
            "ancho_canaleta_cm":    _INFRA["ancho_canaleta_perimetral_cm"],
            "camaras_vertices":     _INFRA["camaras_vertices"],
        },
        "fuente_datos":    (
            "Planos constructivos C&G · Estadio José Amalfitani · "
            "DRENAFLEX AWACOR 118mm + AWACOR CL Y PL 218mm"
        ),
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
        f"  {s['n_lineas_drenaje']} líneas DRENAFLEX AWACOR 118mm + "
        f"{s['n_colectores']} colectores AWACOR 218mm + "
        f"{s['camaras_vertices']} cámaras + {s['perimetral_ml']}ml guardaganado {s['ancho_canaleta_cm']}cm\n"
        f"  Q efectivo: {s['q_efectivo_ls']} L/s | Sistema: {'OK' if s['sistema_suficiente'] else 'INSUFICIENTE'}\n"
        f"  {s['zonas_exclusion']} exclusiones · {s['zonas_precaucion']} precaución · {s['zonas_seguras']} seguras\n"
        f"  Riesgo global: {s['riesgo_global']}"
    )
