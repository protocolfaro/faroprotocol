"""
ipos.py — IPOS Algorithm (Índice de Presión sobre el Césped)
Faro Protocol · Vélez Sarsfield — Pure functions, no I/O.
"""
from __future__ import annotations

PLAYERS = 30

FACTORS: dict[str, float] = {
    "reserva": 1.0, "primera": 1.0, "cuarta": 1.0, "quinta": 1.0,
    "sexta": 1.0, "septima": 1.0, "séptima": 1.0, "octava": 1.0,
    "femenino": 1.0, "liga": 1.0,
    "novena": 0.7, "infantiles": 0.7, "campus": 0.7,
}

TURNO_H: dict[str, float] = {
    "manana": 3.0, "mañana": 3.0,
    "infantiles": 3.5,
    "femenino": 3.5,
}

CANCHAS = {
    "1fa","2fa","3fa","4fa","5fa","6fa","7fa","8fa","9fa","10fa","1fp","2fp","campus",
    "amalfitani","poli_f11","poli_f8a","poli_f8b","poli_hockey",
}

def _factor(cat: str) -> float:
    return FACTORS.get(cat.strip().lower(), 1.0)

def _horas(turno: str) -> float:
    return TURNO_H.get(turno.strip().lower(), 3.0)

def classify(score: float) -> dict:
    if score >= 350:
        return {"semaforo":"rojo","icono":"🔴","texto":"Cancha al límite — necesita descanso"}
    if score >= 200:
        return {"semaforo":"rojo","icono":"🔴","texto":"Uso muy alto — monitorear de cerca"}
    if score >= 140:
        return {"semaforo":"amarillo","icono":"🟡","texto":"Uso moderado — seguir de cerca"}
    if score >= 100:
        return {"semaforo":"amarillo","icono":"🟡","texto":"Uso normal"}
    return {"semaforo":"verde","icono":"🟢","texto":"Cancha descansada"}

def compute_ipos(sessions: list[dict]) -> dict:
    """
    sessions: [{categoria, dia, canchas:[str], turno}]
    Returns {cancha_id: {score, semaforo, icono, texto, personas, horas, detalle}}
    """
    acc: dict[str, dict] = {}

    for s in sessions:
        cat   = s.get("categoria", "")
        turno = s.get("turno", "manana")
        clist = [c.strip().lower() for c in s.get("canchas", [])]
        if not clist:
            continue

        f     = _factor(cat)
        h     = _horas(turno)
        n     = len(clist)
        is_liga = cat.strip().lower() == "liga"

        for cid in clist:
            if cid not in CANCHAS or cid == "campus":
                continue
            p = float(PLAYERS) if is_liga else float(PLAYERS) / n
            acc.setdefault(cid, {"score":0.0,"personas":0.0,"ph":0.0,"field_h":0.0})
            acc[cid]["score"]   += p * f * h
            acc[cid]["personas"] += p
            acc[cid]["ph"]       += p * h
            acc[cid]["field_h"]  += h

    result = {}
    for cid, a in acc.items():
        score = round(a["score"], 1)
        sem   = classify(score)
        personas = round(a["personas"])
        horas    = round(a["field_h"], 1)
        detalle  = (
            f"Esta semana pisaron esta cancha {personas} personas "
            f"durante {horas:.0f} horas en total"
        )
        result[cid] = {
            "score":    score,
            "semaforo": sem["semaforo"],
            "icono":    sem["icono"],
            "texto":    sem["texto"],
            "personas": personas,
            "horas":    horas,
            "detalle":  detalle,
        }
    return result
