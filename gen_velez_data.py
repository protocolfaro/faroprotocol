"""
gen_velez_data.py — Generador de velez_data.json para el panel móvil.
Actualizar SECTOR_DATA y las constantes de partido/calendario cada lunes.
"""

import json
import os
import sys
from datetime import date, timedelta

FECHA_EMISION = date.today().isoformat()

# ── SECTOR DATA ───────────────────────────────────────────────────────────────
SECTOR_DATA = {
    "estadio": {
        "nombre": "Estadio J. Amalfitani",
        "score": 78, "score_prev": 74,
        "sem": "amarillo",
        "detalle": "NDVI cubierta: 0.61 · InSAR: 0.22 mm",
    },
    "agro": {
        "nombre": "Área Agronómica",
        "score": 82, "score_prev": 80,
        "sem": "amarillo",
        "detalle": "NDVI campo norte: 0.58 · riego activo",
    },
    "solar": {
        "nombre": "Sistema Solar",
        "score": 71, "score_prev": 75,
        "sem": "amarillo",
        "detalle": "Eficiencia: 71% · 3 paneles en falla",
    },
    "canchero": {
        "nombre": "Villa Olímpica",
        "score": 59, "score_prev": 63,
        "sem": "rojo",
        "detalle": "Cancha 4: fungicida urgente · NDVI: 0.31",
        # Detalle por cancha — actualizar cada lunes
        "canchas": [
            {
                "id": "c1", "nombre": "Cancha 1",
                "score": 68, "score_prev": 72,
                "sem": "amarillo",
                "ndvi": 0.48, "ndvi_prev": 0.52,
                "detalle": "Focos fungosos leve · tratamiento preventivo",
            },
            {
                "id": "c2", "nombre": "Cancha 2",
                "score": 75, "score_prev": 70,
                "sem": "amarillo",
                "ndvi": 0.52, "ndvi_prev": 0.48,
                "detalle": "Fertilización requerida · NDVI estable",
            },
            {
                "id": "c3", "nombre": "Cancha 3",
                "score": 55, "score_prev": 61,
                "sem": "rojo",
                "ndvi": 0.38, "ndvi_prev": 0.44,
                "detalle": "Focos fungosos activos · fungicida urgente",
            },
            {
                "id": "c4", "nombre": "Cancha 4",
                "score": 31, "score_prev": 42,
                "sem": "rojo",
                "ndvi": 0.31, "ndvi_prev": 0.38,
                "detalle": "CRÍTICO · fungicida + drenaje + resembrado",
            },
        ],
    },
    "sede": {
        "nombre": "Sede Central",
        "score": 75, "score_prev": 75,
        "sem": "amarillo",
        "detalle": "InSAR Anexo Norte: 0.55 mm · Landsat: 41.2°C",
    },
    "poli": {
        "nombre": "Polideportivo Feijóo",
        "score": 39, "score_prev": 44,
        "sem": "rojo",
        "detalle": "Básquet InSAR: 0.85 mm · Playón: 0.72 mm",
    },
    "piletas": {
        "nombre": "Complejo Acuático",
        "score": 91, "score_prev": 89,
        "sem": "verde",
        "detalle": "Calidad agua OK · InSAR: 0.35 mm",
    },
}

# ── HISTORIAL GLOBAL ──────────────────────────────────────────────────────────
HISTORIAL_GLOBAL = [
    {"semana": "S-5", "score": 65, "sem": "amarillo"},
    {"semana": "S-4", "score": 68, "sem": "amarillo"},
    {"semana": "S-3", "score": 72, "sem": "amarillo"},
    {"semana": "S-2", "score": 69, "sem": "amarillo"},
    {"semana": "S-1", "score": 71, "sem": "amarillo"},
    {"semana": "HOY", "score": 70, "sem": "amarillo"},
]

# ── PRÓXIMO PARTIDO (actualizar cada semana) ──────────────────────────────────
PROXIMO_PARTIDO_ROGER = {
    "fecha": "2026-05-24",
    "rival": "Independiente",
    "tipo": "local",
    "cancha": "Estadio José Amalfitani",
    "checklist": [
        "Corte de cancha día previo",
        "Marcación de líneas",
        "Riego final (2 h antes del partido)",
        "Revisión arcos y redes",
        "Banderines de córner",
        "Vestuarios y accesos habilitados",
    ],
}

# ── TAREAS BASE POR DÍA (actualizar cada lunes) ───────────────────────────────
_TAREAS_BASE = [
    ["Aplicar fungicida C4 zona central y lateral sur", "Verificar drenaje lateral C4"],
    ["Regar C3 y C4: 25 min por sector", "Fungicida preventivo C1/C3"],
    ["Cortar C2 y C4 temprano (7-9 hs)", "Fertilizar C2: 20 kg N/ha"],
    ["Sembrar C4 zona central (85 m²)", "Regar C1 y C3"],
    ["Aerificar porterías Amalfitani", "Preparación previa partido: corte y riego"],
    ["Marcar líneas estadio", "Riego final: 2 h antes del partido"],
    ["PARTIDO vs Independiente — estadio abierto 2 h antes",
     "Post-partido: revisión cancha y sistema de riego"],
]
_DIA_NOMBRES = ["Lun", "Mar", "Mié", "Jue", "Vie", "Sáb", "Dom"]


def _build_roger_calendar() -> list:
    """Genera el calendario lun-dom de la semana actual."""
    today = date.today()
    monday = today - timedelta(days=today.weekday())
    return [
        {
            "dia_offset": i,
            "dia_nombre": _DIA_NOMBRES[i],
            "fecha_iso": (monday + timedelta(days=i)).isoformat(),
            "tareas": _TAREAS_BASE[i],
            "es_partido": (monday + timedelta(days=i)).isoformat() == PROXIMO_PARTIDO_ROGER["fecha"],
        }
        for i in range(7)
    ]


_ALL = ["estadio", "agro", "solar", "canchero", "sede", "poli", "piletas"]

# ── CONFIGURACIÓN DE USUARIOS ─────────────────────────────────────────────────
USUARIO_CONFIG = {
    "roger": {
        "nombre": "Roger Bernal",
        "tipo": "canchero",
        "sectores": ["canchero"],
        "sort_by_score": False,
        "proximo_partido": PROXIMO_PARTIDO_ROGER,
        # kpis con value_prev para comparativa semanal
        "kpis": [
            {"label": "NDVI C4",      "value": "0.31", "value_prev": "0.38", "unit": "", "sub": "CRÍTICO — resembrar",     "sem": "rojo"},
            {"label": "NDVI C1/C3",   "value": "0.38", "value_prev": "0.44", "unit": "", "sub": "Focos fungosos activos",  "sem": "rojo"},
            {"label": "NDVI C2",      "value": "0.52", "value_prev": "0.48", "unit": "", "sub": "Fertilización requerida", "sem": "amarillo"},
            {"label": "Score Fusión", "value": "59",   "value_prev": "63",   "unit": "", "sub": "Promedio 4 canchas",      "sem": "rojo"},
        ],
        "acciones": [
            "Cancha 4: intervención URGENTE — fungicida + drenaje + resembrar HOY",
            "Canchas 1 y 3: aplicar fungicida activo — focos exactos en reporte PDF",
            "Cancha 2: fertilizar 20 kg N/ha uniformemente esta semana",
        ],
    },
    "juan": {
        "nombre": "Juan González",
        "tipo": "intendente",
        "sectores": ["canchero", "agro", "poli"],
        "sort_by_score": True,
        "resumen_ejecutivo": [
            "2 sectores en rojo: Poli Básquet (InSAR 0.85 mm) y Villa Olímpica C4 — acción urgente esta semana.",
            "Sistema solar al 71% de eficiencia — 3 paneles en falla, pérdida de 4.2 kWp activa.",
            "Área agronómica estable (NDVI 0.58). Complejo acuático y estadio sin intervención inmediata.",
        ],
        "presupuesto_urgente": {
            "moneda": "ARS",
            "total": 180000,
            "items": [
                {"concepto": "Fungicida + insumos resembrado C4",       "monto": 85000, "sem": "rojo"},
                {"concepto": "Inspección estructural Básquet Feijóo",   "monto": 45000, "sem": "rojo"},
                {"concepto": "Limpieza y revisión 7 paneles solares",   "monto": 32000, "sem": "amarillo"},
                {"concepto": "Reparación drenaje lateral C4",           "monto": 18000, "sem": "amarillo"},
            ],
        },
        "sectores_autorizacion": [
            {"sector": "Villa Olímpica",    "accion": "Compra fungicida + insumos resembrado", "monto": 85000, "urgencia": "HOY"},
            {"sector": "Poli Feijóo",       "accion": "Contratar empresa inspección estructural", "monto": 45000, "urgencia": "esta-sem"},
        ],
        "kpis": [
            {"label": "InSAR Básquet", "value": "0.85", "value_prev": "0.70", "unit": "mm", "sub": "SUPERA umbral 0.8 mm", "sem": "rojo"},
            {"label": "NDVI C4",       "value": "0.31", "value_prev": "0.38", "unit": "",   "sub": "Cancha crítica",       "sem": "rojo"},
            {"label": "NDVI Agro",     "value": "0.58", "value_prev": "0.56", "unit": "",   "sub": "Riego activo OK",      "sem": "amarillo"},
            {"label": "Score Poli",    "value": "39",   "value_prev": "44",   "unit": "",   "sub": "Atención urgente",     "sem": "rojo"},
        ],
        "acciones": [
            "Poli Básquet: inspección fisuras + drenaje URGENTE (InSAR 0.85 mm)",
            "Cancha 4: intervención urgente — fungicida + drenaje esta semana",
            "Agro: riego preventivo — monitorear NDVI campo norte",
        ],
    },
    "banchero": {
        "nombre": "Fernando Banchero",
        "tipo": "ejecutivo",
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
        "tipo": "intendente",
        "sectores": ["canchero", "agro", "poli"],
        "sort_by_score": True,
        "resumen_ejecutivo": [
            "Cancha 4 y Poli Básquet en estado crítico — intervención urgente requerida.",
            "Cancha 2 y Área Agronómica en condiciones aptas para uso normal.",
            "Poli Playón Norte: evaluar antes de actividades de alto impacto.",
        ],
        "presupuesto_urgente": {
            "moneda": "ARS",
            "total": 130000,
            "items": [
                {"concepto": "Fungicida + resembrado C4",             "monto": 85000, "sem": "rojo"},
                {"concepto": "Inspección estructural Básquet",        "monto": 45000, "sem": "rojo"},
            ],
        },
        "sectores_autorizacion": [
            {"sector": "Villa Olímpica", "accion": "Compra fungicida + resembrado", "monto": 85000, "urgencia": "HOY"},
            {"sector": "Poli Feijóo",    "accion": "Inspección estructural Básquet", "monto": 45000, "urgencia": "esta-sem"},
        ],
        "acciones": [
            "Cancha 4: NO APTA — hongo activo + drenaje roto + pasto ralo",
            "Poli Básquet: evaluar antes de uso intensivo (InSAR 0.85 mm)",
            "Cancha 2 y Campo Agro: estado óptimo — sin restricciones",
        ],
        "kpis": [
            {"label": "Canchas Aptas",  "value": "2",   "unit": "/4", "sub": "C2 y campo OK",         "sem": "amarillo"},
            {"label": "NDVI C4",        "value": "0.31", "unit": "",   "sub": "NO APTA — crítica",     "sem": "rojo"},
            {"label": "NDVI Agro",      "value": "0.58", "unit": "",   "sub": "Uso normal",            "sem": "amarillo"},
            {"label": "InSAR Básquet",  "value": "0.85", "unit": "mm", "sub": "Evaluar antes de uso", "sem": "rojo"},
        ],
    },
    "berlanga": {
        "nombre": "Fabián Berlanga",
        "tipo": "ejecutivo",
        "sectores": _ALL,
        "sort_by_score": True,
        "acciones": [
            "Poli + Canchero: sectores críticos — intervención esta semana",
            "Solar: eficiencia por debajo de objetivo — revisión técnica",
            "Piletas: estado óptimo — mantener protocolo de calidad",
        ],
        "kpis": [
            {"label": "Score Global",    "value": "70", "unit": "",  "sub": "Predio completo",        "sem": "amarillo"},
            {"label": "Alertas Rojas",   "value": "2",  "unit": "",  "sub": "Poli + Villa Olímpica",  "sem": "rojo"},
            {"label": "Efic. Solar",     "value": "71", "unit": "%", "sub": "Objetivo: ≥85%",         "sem": "amarillo"},
            {"label": "Piletas",         "value": "91", "unit": "",  "sub": "Calidad agua excelente", "sem": "verde"},
        ],
    },
    "nelson": {
        "nombre": "Nelson Pugliese",
        "tipo": "ejecutivo",
        "sectores": _ALL,
        "sort_by_score": True,
        "acciones": [
            "Poli + Villa Olímpica: acciones estructurales urgentes esta semana",
            "Sistema Solar: 3 paneles en falla — pérdida 4.2 kWp activa",
            "Sede + Piletas: monitoreo térmico continuo — sin acción urgente",
        ],
        "kpis": [
            {"label": "Score Global",    "value": "70",  "unit": "",    "sub": "Promedio 7 sectores",      "sem": "amarillo"},
            {"label": "Alertas Activas", "value": "4",   "unit": "",    "sub": "2 rojas · 2 amarillas",    "sem": "rojo"},
            {"label": "Sectores",        "value": "7",   "unit": "",    "sub": "100% con cobertura",       "sem": "verde"},
            {"label": "Solar kWp",       "value": "18.4","unit": "kWp", "sub": "Pérdida activa: 4.2 kWp", "sem": "amarillo"},
        ],
    },
    "aveleyra": {
        "nombre": "Alberto Aveleyra",
        "tipo": "ejecutivo",
        "sectores": _ALL,
        "sort_by_score": True,
        "acciones": [
            "Poli + Canchero: intervenciones urgentes — impacto directo en operación",
            "Solar: revisar paneles en falla esta semana — pérdida económica activa",
            "80% de problemas identificados en etapa temprana — detección anticipada",
        ],
        "kpis": [
            {"label": "Score Predio",    "value": "70", "unit": "",  "sub": "Global ponderado",      "sem": "amarillo"},
            {"label": "Score Piletas",   "value": "91", "unit": "",  "sub": "Sin acción inmediata",  "sem": "verde"},
            {"label": "Efic. Solar",     "value": "71", "unit": "%", "sub": "Pérdida: 4.2 kWp/sem", "sem": "amarillo"},
            {"label": "Alertas Estruc.", "value": "1",  "unit": "",  "sub": "InSAR crítico — poli",  "sem": "rojo"},
        ],
    },
    "admin": {
        "nombre": "Administración General",
        "tipo": "ejecutivo",
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
    f = fecha or FECHA_EMISION
    import copy
    usuarios = copy.deepcopy(USUARIO_CONFIG)
    # Inject dynamic weekly calendar for roger
    usuarios["roger"]["tareas_semana"] = _build_roger_calendar()
    return {
        "meta": {
            "version": "2.0",
            "fecha": f,
            "cliente": "Club Atletico Velez Sarsfield",
            "coords": {"lat": -34.6375, "lon": -58.5215},
            "historial_global": HISTORIAL_GLOBAL,
        },
        "sectores": SECTOR_DATA,
        "usuarios": usuarios,
    }


def write_velez_data(output_path: str = None, fecha: str = None) -> str:
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
        print(json.dumps(build_velez_data(), ensure_ascii=False, indent=2))
    else:
        path = write_velez_data()
        print(f"OK  {path}  ({os.path.getsize(path)} bytes)")
