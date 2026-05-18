"""
gen_velez_data.py — Generador de velez_data.json para el panel móvil.

Actualizar SECTOR_DATA y HISTORIAL_GLOBAL cada lunes antes de ejecutar.
Integrar en faro_velez_scheduler.py llamando write_velez_data() al final de weekly_report().

Uso:
    python gen_velez_data.py                   # escribe velez/velez_data.json
    python gen_velez_data.py --stdout          # imprime JSON a stdout
"""

import json
import os
import sys
from datetime import date

# ── FECHA DE EMISIÓN ──────────────────────────────────────────────────────────
FECHA_EMISION = date.today().isoformat()

# ── DATOS POR SECTOR (actualizar cada semana) ─────────────────────────────────
# score_prev: score de la semana anterior (para flecha de tendencia en panel)
SECTOR_DATA = {
    "estadio": {
        "nombre": "Estadio J. Amalfitani",
        "score": 78,
        "score_prev": 74,
        "sem": "amarillo",
        "detalle": "NDVI cubierta: 0.61 · InSAR: 0.22 mm",
    },
    "agro": {
        "nombre": "Área Agronómica",
        "score": 82,
        "score_prev": 80,
        "sem": "amarillo",
        "detalle": "NDVI campo norte: 0.58 · riego activo",
    },
    "solar": {
        "nombre": "Sistema Solar",
        "score": 71,
        "score_prev": 75,
        "sem": "amarillo",
        "detalle": "Eficiencia: 71% · 3 paneles en falla",
    },
    "canchero": {
        "nombre": "Villa Olímpica",
        "score": 59,
        "score_prev": 63,
        "sem": "rojo",
        "detalle": "Cancha 4: fungicida urgente · NDVI: 0.31",
    },
    "sede": {
        "nombre": "Sede Central",
        "score": 75,
        "score_prev": 75,
        "sem": "amarillo",
        "detalle": "InSAR Anexo Norte: 0.55 mm · Landsat: 41.2°C",
    },
    "poli": {
        "nombre": "Polideportivo Feijóo",
        "score": 39,
        "score_prev": 44,
        "sem": "rojo",
        "detalle": "Básquet InSAR: 0.85 mm · Playón: 0.72 mm",
    },
    "piletas": {
        "nombre": "Complejo Acuático",
        "score": 91,
        "score_prev": 89,
        "sem": "verde",
        "detalle": "Calidad agua OK · InSAR: 0.35 mm",
    },
}

# ── HISTORIAL GLOBAL (últimas 6 semanas, más reciente al final) ───────────────
HISTORIAL_GLOBAL = [
    {"semana": "S-5", "score": 65, "sem": "amarillo"},
    {"semana": "S-4", "score": 68, "sem": "amarillo"},
    {"semana": "S-3", "score": 72, "sem": "amarillo"},
    {"semana": "S-2", "score": 69, "sem": "amarillo"},
    {"semana": "S-1", "score": 71, "sem": "amarillo"},
    {"semana": "HOY", "score": 70, "sem": "amarillo"},
]

_ALL = ["estadio", "agro", "solar", "canchero", "sede", "poli", "piletas"]

# ── CONFIGURACIÓN DE USUARIOS ─────────────────────────────────────────────────
# slug → config. Cada usuario ve SOLO sus sectores asignados.
# sort_by_score: true → sectores ordenados por score ascendente (críticos primero)
USUARIO_CONFIG = {
    "roger": {
        "nombre": "Roger Bernal",
        "sectores": ["canchero"],
        "sort_by_score": False,
        "acciones": [
            "Cancha 4: intervención URGENTE — fungicida + drenaje + resembrar HOY",
            "Canchas 1 y 3: aplicar fungicida activo — focos exactos en reporte PDF",
            "Cancha 2: fertilizar 20 kg N/ha uniformemente esta semana",
        ],
        "kpis": [
            {"label": "NDVI C4",      "value": "0.31", "unit": "",   "sub": "CRÍTICO — resembrar",     "sem": "rojo"},
            {"label": "NDVI C1/C3",   "value": "0.38", "unit": "",   "sub": "Focos fungosos activos",  "sem": "rojo"},
            {"label": "NDVI C2",      "value": "0.52", "unit": "",   "sub": "Fertilización requerida", "sem": "amarillo"},
            {"label": "Score Fusión", "value": "59",   "unit": "",   "sub": "Promedio 4 canchas",      "sem": "rojo"},
        ],
    },
    "juan": {
        "nombre": "Juan González",
        "sectores": ["canchero", "agro", "poli"],
        "sort_by_score": False,
        "acciones": [
            "Poli Básquet: inspección fisuras + drenaje URGENTE (InSAR 0.85 mm)",
            "Cancha 4: intervención urgente — fungicida + drenaje esta semana",
            "Agro: riego preventivo — estrés hídrico detectado en campo norte",
        ],
        "kpis": [
            {"label": "InSAR Básquet", "value": "0.85", "unit": "mm", "sub": "SUPERA umbral 0.8 mm", "sem": "rojo"},
            {"label": "NDVI C4",       "value": "0.31", "unit": "",   "sub": "Cancha crítica",       "sem": "rojo"},
            {"label": "NDVI Agro",     "value": "0.58", "unit": "",   "sub": "Riego activo",         "sem": "amarillo"},
            {"label": "Score Poli",    "value": "39",   "unit": "",   "sub": "Atención urgente",     "sem": "rojo"},
        ],
    },
    "banchero": {
        "nombre": "Fernando Banchero",
        "sectores": _ALL,
        "sort_by_score": True,
        "acciones": [
            "Poli + Villa Olímpica C4: intervenciones urgentes esta semana",
            "Sistema Solar: mantenimiento técnico + limpieza paneles degradados",
            "Sede Anexo Norte + Piletas Techo: inspección térmica preventiva",
        ],
        "kpis": [
            {"label": "Sectores OK",    "value": "2",  "unit": "/7", "sub": "Verde: piletas, estadio",       "sem": "verde"},
            {"label": "Sectores Alerta","value": "3",  "unit": "/7", "sub": "Amarillo: agro, solar, sede",   "sem": "amarillo"},
            {"label": "Sectores Crit.", "value": "2",  "unit": "/7", "sub": "Rojo: canchero, poli",          "sem": "rojo"},
            {"label": "Score Global",   "value": "70", "unit": "",   "sub": "Promedio 7 sectores",           "sem": "amarillo"},
        ],
    },
    "pait": {
        "nombre": "Sebastián Pait",
        "sectores": ["canchero", "agro", "poli"],
        "sort_by_score": False,
        "acciones": [
            "Cancha 4: NO APTA — hongo activo + drenaje roto + pasto ralo",
            "Poli Básquet: evaluar antes de uso intensivo (InSAR 0.85 mm)",
            "Cancha 2 y Campo Agro: estado óptimo — sin restricciones",
        ],
        "kpis": [
            {"label": "Canchas Aptas",  "value": "2",  "unit": "/4", "sub": "C2 y campo OK",         "sem": "amarillo"},
            {"label": "NDVI C4",        "value": "0.31","unit": "",   "sub": "NO APTA — crítica",     "sem": "rojo"},
            {"label": "NDVI Agro",      "value": "0.58","unit": "",   "sub": "Uso normal",            "sem": "amarillo"},
            {"label": "InSAR Básquet",  "value": "0.85","unit": "mm", "sub": "Evaluar antes de uso", "sem": "rojo"},
        ],
    },
    "berlanga": {
        "nombre": "Fabián Berlanga",
        "sectores": _ALL,
        "sort_by_score": True,
        "acciones": [
            "Poli + Canchero: sectores críticos — intervención esta semana",
            "Solar: eficiencia por debajo de objetivo — revisión técnica",
            "Piletas: estado óptimo — mantener protocolo de calidad",
        ],
        "kpis": [
            {"label": "Score Global",   "value": "70", "unit": "",   "sub": "Predio completo",          "sem": "amarillo"},
            {"label": "Alertas Rojas",  "value": "2",  "unit": "",   "sub": "Poli + Villa Olímpica",    "sem": "rojo"},
            {"label": "Eficiencia Solar","value": "71","unit": "%",  "sub": "Objetivo: ≥85%",           "sem": "amarillo"},
            {"label": "Piletas",        "value": "91", "unit": "",   "sub": "Calidad agua excelente",   "sem": "verde"},
        ],
    },
    "nelson": {
        "nombre": "Nelson Pugliese",
        "sectores": _ALL,
        "sort_by_score": True,
        "acciones": [
            "Poli + Villa Olímpica: acciones estructurales urgentes esta semana",
            "Sistema Solar: 3 paneles en falla — pérdida 4.2 kWp activa",
            "Sede + Piletas: monitoreo térmico continuo — sin acción urgente",
        ],
        "kpis": [
            {"label": "Score Global",   "value": "70", "unit": "",   "sub": "Promedio 7 sectores",     "sem": "amarillo"},
            {"label": "Alertas Activas","value": "4",  "unit": "",   "sub": "2 rojas · 2 amarillas",   "sem": "rojo"},
            {"label": "Sectores",       "value": "7",  "unit": "",   "sub": "100% con cobertura",      "sem": "verde"},
            {"label": "Solar kWp",      "value": "18.4","unit": "kWp","sub": "Pérdida activa: 4.2 kWp","sem": "amarillo"},
        ],
    },
    "aveleyra": {
        "nombre": "Alberto Aveleyra",
        "sectores": _ALL,
        "sort_by_score": True,
        "acciones": [
            "Poli + Canchero: intervenciones urgentes — impacto directo en operación",
            "Solar: revisar paneles en falla esta semana — pérdida económica activa",
            "80% de problemas identificados en etapa temprana — detección anticipada",
        ],
        "kpis": [
            {"label": "Score Predio",   "value": "70", "unit": "",   "sub": "Global ponderado",       "sem": "amarillo"},
            {"label": "Score Piletas",  "value": "91", "unit": "",   "sub": "Sin acción inmediata",   "sem": "verde"},
            {"label": "Eficiencia Solar","value":"71", "unit": "%",  "sub": "Pérdida: 4.2 kWp/sem",  "sem": "amarillo"},
            {"label": "Alertas Estruc.","value": "1",  "unit": "",   "sub": "InSAR crítico — poli",   "sem": "rojo"},
        ],
    },
    "admin": {
        "nombre": "Administración General",
        "sectores": _ALL,
        "sort_by_score": True,
        "acciones": [
            "Poli Básquet + Villa Olímpica C4: intervenciones urgentes esta semana",
            "Sistema Solar: mantenimiento técnico + limpieza paneles degradados",
            "Sede Anexo Norte + Piletas Techo: inspección térmica preventiva",
        ],
        "kpis": [
            {"label": "Sectores OK",    "value": "2",  "unit": "/7", "sub": "Verde: piletas, estadio",     "sem": "verde"},
            {"label": "Sectores Alerta","value": "3",  "unit": "/7", "sub": "Amarillo: agro, solar, sede", "sem": "amarillo"},
            {"label": "Sectores Crit.", "value": "2",  "unit": "/7", "sub": "Rojo: canchero, poli",        "sem": "rojo"},
            {"label": "Score Global",   "value": "70", "unit": "",   "sub": "Promedio 7 sectores",         "sem": "amarillo"},
        ],
    },
}


def build_velez_data(fecha: str = None) -> dict:
    """Construye el diccionario completo para velez_data.json."""
    f = fecha or FECHA_EMISION
    return {
        "meta": {
            "version": "1.1",
            "fecha": f,
            "cliente": "Club Atletico Velez Sarsfield",
            "historial_global": HISTORIAL_GLOBAL,
        },
        "sectores": SECTOR_DATA,
        "usuarios": USUARIO_CONFIG,
    }


def write_velez_data(output_path: str = None, fecha: str = None) -> str:
    """Escribe velez_data.json. Retorna la ruta del archivo generado."""
    if output_path is None:
        here = os.path.dirname(os.path.abspath(__file__))
        output_path = os.path.join(here, "velez", "velez_data.json")

    data = build_velez_data(fecha)
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, separators=(",", ":"))
    return output_path


if __name__ == "__main__":
    if "--stdout" in sys.argv:
        data = build_velez_data()
        print(json.dumps(data, ensure_ascii=False, indent=2))
    else:
        path = write_velez_data()
        size = os.path.getsize(path)
        print(f"OK  {path}  ({size} bytes)")
