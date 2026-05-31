"""
dale_play_acoustic.py — Análisis acústico + sightlines para eventos masivos.
Modelo base: free-field geométrico L(d) = Lw - 20·log10(d) - 11 dB.
Mejorado: pyroomacoustics (simulación RT60 + corrección reverberación Sabine-Eyring).
Sectores: Campo C., Trib. Norte, Trib. Sur, Trib. Este, Platea Alta N (150m/+15m),
          Platea Alta S (170m/+15m). Tribuna Oeste excluida (es el escenario).
Guarda: models/acoustic_real_{show_id}.json
"""
from __future__ import annotations
import json, logging, math, os, pathlib, re
from datetime import datetime
from typing import Optional

from dale_play_config import VENUE_CAP

log = logging.getLogger(__name__)

MODELS_DIR = pathlib.Path(__file__).parent / "models"

# Sectores vigentes — Tribuna Oeste eliminada (zona de escenario)
# height_m: altura relativa al campo (0 = nivel del campo)
_SECTORES = [
    {"id": "campo_central",     "name": "Campo Central",     "dist_m": 80,  "cap": 25_000, "angle": 0,   "height_m": 0},
    {"id": "tribuna_norte",     "name": "Tribuna Norte",     "dist_m": 120, "cap": 8_000,  "angle": 30,  "height_m": 8},
    {"id": "tribuna_sur",       "name": "Tribuna Sur",       "dist_m": 140, "cap": 8_000,  "angle": 330, "height_m": 8},
    {"id": "tribuna_este",      "name": "Tribuna Este",      "dist_m": 130, "cap": 4_000,  "angle": 0,   "height_m": 8},
    {"id": "platea_alta_norte", "name": "Platea Alta Norte", "dist_m": 150, "cap": 5_000,  "angle": 25,  "height_m": 15},
    {"id": "platea_alta_sur",   "name": "Platea Alta Sur",   "dist_m": 170, "cap": 5_000,  "angle": 335, "height_m": 15},
]

# Constantes acústicas del recinto (Amalfitani 105×68×22m aprox.)
_ROOM_W  = 68.0   # m (E-O)
_ROOM_L  = 105.0  # m (N-S)
_ROOM_H  = 22.0   # m (altura tribuna + clearance)

# Coeficientes de absorción Sabine (1kHz) por superficie
_A_GRASS    = 0.50   # pasto — absorbente moderado
_A_CONCRETE = 0.05   # tribunas de hormigón — muy reflectivo
_A_SKY      = 0.99   # apertura cenital — prácticamente transparente


def _room_rt60_sabine() -> float:
    """RT60 via fórmula de Sabine para el recinto Amalfitani (semi-abierto)."""
    V  = _ROOM_W * _ROOM_L * _ROOM_H
    S_floor   = _ROOM_W * _ROOM_L
    S_ceiling = S_floor
    S_walls   = 2 * (_ROOM_W + _ROOM_L) * _ROOM_H

    A = (S_floor * _A_GRASS + S_ceiling * _A_SKY + S_walls * _A_CONCRETE)
    return 0.161 * V / A if A > 0 else 1.0


def _room_constant_r() -> float:
    """Constante de sala R (m²) = Sα/(1−ᾱ)."""
    S_floor   = _ROOM_W * _ROOM_L
    S_ceiling = S_floor
    S_walls   = 2 * (_ROOM_W + _ROOM_L) * _ROOM_H
    S_total   = S_floor + S_ceiling + S_walls

    A = S_floor * _A_GRASS + S_ceiling * _A_SKY + S_walls * _A_CONCRETE
    alpha_bar = A / S_total
    return S_total * alpha_bar / max(1 - alpha_bar, 0.01)


def _spl_geometric(lw_db: float, dist_m: float, height_m: float = 0) -> float:
    """SPL geométrico free-field corregido por altura del receptor."""
    d_eff = math.sqrt(dist_m ** 2 + max(0, height_m - 2) ** 2)  # fuente a 2m
    return lw_db - 20 * math.log10(max(d_eff, 1)) - 11


def _spl_room(lw_db: float, dist_m: float, height_m: float = 0, Q: float = 2.0) -> float:
    """
    SPL con contribución del campo reverberante (modelo Sabine-Eyring).
    SPL = Lw + 10·log10(Q/(4π·d²) + 4/R)
    Q=2 para fuente semidireccional (escenario apunta hacia el campo).
    """
    R   = _room_constant_r()
    d_e = math.sqrt(dist_m ** 2 + max(0, height_m - 2) ** 2)
    d_e = max(d_e, 1.0)
    p   = Q / (4 * math.pi * d_e ** 2) + 4.0 / max(R, 1.0)
    if p > 0:
        return lw_db + 10 * math.log10(p)
    return _spl_geometric(lw_db, dist_m, height_m)


def _sightline(angle_deg: float) -> str:
    # Normalize to 0-180° so 330° → 30° (tribuna_sur = optima, not obstruida)
    a = angle_deg % 360
    if a > 180:
        a = 360 - a
    if a <= 60:   return "optima"
    if a <= 120:  return "buena"
    if a <= 150:  return "parcial"
    return "obstruida"


# ── Pyroomacoustics (opcional) ────────────────────────────────────────────────

def _pra_simulate(lw_db: float) -> Optional[dict]:
    """
    Simulación con pyroomacoustics: recinto Shoebox Amalfitani (semi-abierto).
    Retorna {rt60_s, spl_per_sector} o None si no está instalado.
    """
    try:
        import pyroomacoustics as pra
        import numpy as np
    except ImportError:
        log.info("pyroomacoustics no instalado — usando modelo geométrico")
        return None

    try:
        m_grass    = pra.Material(energy_absorption=_A_GRASS)
        m_concrete = pra.Material(energy_absorption=_A_CONCRETE)
        m_sky      = pra.Material(energy_absorption=_A_SKY)

        room = pra.ShoeBox(
            [_ROOM_W, _ROOM_L, _ROOM_H],
            fs=8000,
            materials={
                "floor":   m_grass,
                "ceiling": m_sky,
                "east":    m_concrete,
                "west":    m_concrete,
                "north":   m_concrete,
                "south":   m_concrete,
            },
            max_order=3,
            ray_tracing=False,
            air_absorption=True,
        )

        # Fuente: escenario en extremo este, centro N-S, a 2m de altura
        room.add_source([5.0, _ROOM_L / 2, 2.0])

        # Receptores por sector (eje x=E-W desde escenario en x=5)
        mics = []
        for sec in _SECTORES:
            d = sec["dist_m"]
            a = math.radians(sec["angle"])
            x = 5.0 + d * math.cos(a)
            y = _ROOM_L / 2 + d * math.sin(a)
            z = float(sec["height_m"]) + 1.2  # altura oído promedio
            x = max(1.0, min(_ROOM_W - 1, x))
            y = max(1.0, min(_ROOM_L - 1, y))
            z = max(0.5, min(_ROOM_H - 1, z))
            mics.append([x, y, z])

        mic_arr = np.array(mics).T   # shape (3, N_sectores)
        room.add_microphone(mic_arr)
        room.simulate()

        # RT60 estimado (Sabine como referencia)
        try:
            rt60_arr = room.measure_rt60()
            rt60_s   = float(np.nanmean(rt60_arr))
        except Exception:
            rt60_s = _room_rt60_sabine()

        # SPL por sector: energía del RIR → corrección respecto a modelo geométrico
        R = _room_constant_r()
        spl_per_sector: dict = {}
        for i, sec in enumerate(_SECTORES):
            try:
                rir = room.rir[i][0]
                # Potencia del RIR normalizada por tiempo y energía
                energy = float(np.sum(rir ** 2) / len(rir))
                if energy > 0:
                    geom   = _spl_geometric(lw_db, sec["dist_m"], sec["height_m"])
                    reverb = 10 * math.log10(max(4.0 / R, 1e-12))
                    # SPL = geom + reverb_correction (capped para evitar valores irreales)
                    spl = min(lw_db - 5, max(60, geom + max(-3, min(6, reverb))))
                else:
                    spl = _spl_geometric(lw_db, sec["dist_m"], sec["height_m"])
            except Exception:
                spl = _spl_geometric(lw_db, sec["dist_m"], sec["height_m"])
            spl_per_sector[sec["id"]] = round(spl, 1)

        log.info("pyroomacoustics OK — RT60=%.2fs", rt60_s)
        return {"rt60_s": round(rt60_s, 2), "spl_per_sector": spl_per_sector}

    except Exception as exc:
        log.warning("pyroomacoustics simulation error: %s — fallback a modelo geométrico", exc)
        return None


# Mapeo sightline Claude → interno
_CLAUDE_SL_MAP: dict[str, str] = {
    "directa":   "optima",
    "lateral":   "buena",
    "obstruida": "obstruida",
}


# ── Claude API acoustic analysis ─────────────────────────────────────────────

def _claude_acoustic_analysis(rider: dict, lw_db: float) -> Optional[dict]:
    """
    Llama a Claude API para calcular SPL real, C-value ISO/FIFA y sightlines.
    Retorna dict {sectores, rt60_estimado_s, alertas_globales} o None si falla.
    """
    try:
        import anthropic
    except ImportError:
        log.warning("anthropic SDK no instalado — fallback a modelo geométrico")
        return None

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        log.warning("ANTHROPIC_API_KEY no configurada — fallback a modelo geométrico")
        return None

    throws  = rider.get("stage", {}).get("throws", ["main"])
    cap_est = int(rider.get("capacidad_estimada", 0) or 0)

    stadium_ctx = {
        "venue":                   "Estadio José Amalfitani",
        "dimensiones_m":           f"{_ROOM_W:.0f}×{_ROOM_L:.0f}×{_ROOM_H:.0f}",
        "capacidad_total":         VENUE_CAP,
        "capacidad_estimada_show": cap_est,
        "escenario": {
            "posicion":     "extremo oeste del campo (Tribuna Oeste = zona de escenario)",
            "lw_db":        lw_db,
            "throws":       throws,
            "delay_towers": "delay" in throws,
        },
        "sectores": [
            {
                "id":                              s["id"],
                "nombre":                          s["name"],
                "distancia_m":                     s["dist_m"],
                "angulo_respecto_escenario_deg":   s["angle"],
                "altura_receptor_m":               s["height_m"],
                "capacidad":                       s["cap"],
            }
            for s in _SECTORES
        ],
        "acustica_sala": {
            "absorcion_pasto":         _A_GRASS,
            "absorcion_hormigon":      _A_CONCRETE,
            "apertura_cenital_alpha":  _A_SKY,
            "rt60_sabine_s":           round(_room_rt60_sabine(), 2),
        },
    }

    prompt = (
        f"Configuración del estadio:\n```json\n"
        f"{json.dumps(stadium_ctx, ensure_ascii=False, indent=2)}\n```\n\n"
        "Calculá para cada sector:\n"
        "- spl_db: partí de lw_db del rider, restá 20·log10(distancia_efectiva) + 11 dB "
        "(distancia efectiva = sqrt(distancia_m² + max(0,altura_receptor_m-2)²)), "
        "sumá +3 dB por campo reverberante y +2 dB si hay delay_towers en ese sector.\n"
        "- c_value: claridad ISO/FIFA estimada (rango típico -4 a +3 dB; "
        "mayor en campo, menor en tribunas altas).\n"
        "- sightline: 'DIRECTA' si ángulo ≤60°, 'LATERAL' si ≤120°, 'OBSTRUIDA' si >120°.\n"
        "- recomendaciones: lista de strings, máx 2 por sector, solo si SPL < 98 dB.\n"
        "Devolvé EXACTAMENTE este JSON, sin texto adicional:\n"
        '{"sectores":[{"id":"campo_central","spl_db":0.0,"c_value":0.0,'
        '"sightline":"DIRECTA","recomendaciones":[]}],'
        '"rt60_estimado_s":0.0,"alertas_globales":[],'
        '"modelo":"Claude API acústico geométrico"}'
    )

    try:
        client = anthropic.Anthropic(api_key=api_key)
        msg = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=1500,
            system=(
                "Sos un ingeniero acústico especializado en estadios de fútbol. "
                "Dado el layout de un estadio y la posición del escenario, calculá con precisión "
                "geométrica real: SPL en dB por sector (usando LW=134dB y atenuación 6dB por "
                "duplicación de distancia), C-value ISO/FIFA por sector, clasificación de sightline "
                "(DIRECTA/LATERAL/OBSTRUIDA), y recomendaciones específicas de delay towers o "
                "pantallas. Devolvé solo JSON sin texto adicional."
            ),
            messages=[{"role": "user", "content": prompt}],
        )
        raw = msg.content[0].text.strip()
        log.info("Claude API acoustic raw:\n%s", raw)

        # Strip ```json fences if present
        raw_clean = re.sub(r'^```(?:json)?\s*', '', raw, flags=re.MULTILINE)
        raw_clean = re.sub(r'\s*```\s*$', '', raw_clean, flags=re.MULTILINE).strip()

        result = json.loads(raw_clean)
        log.info(
            "Claude API acoustic OK — %d sectores, RT60=%.2fs",
            len(result.get("sectores", [])),
            result.get("rt60_estimado_s", 0),
        )
        return result

    except Exception as exc:
        log.warning("Claude API acoustic failed: %s — fallback a modelo geométrico", exc)
        return None


# ── Análisis principal ────────────────────────────────────────────────────────

def analyze_acoustic_sightlines(rider: dict, show_id: str = "unknown") -> dict:
    """
    Analiza cobertura acústica y sightlines para el show.

    rider: {
        "stage": {"lw_db": float, "throws": ["main","delay","front_fill"]},
        "artist": str, "show_date": str, "capacidad_estimada": int
    }
    show_id: identificador del show (para acoustic_real_{show_id}.json).
    """
    stage     = rider.get("stage", {})
    lw_db     = float(stage.get("lw_db", 130))
    throws    = stage.get("throws", ["main"])
    cap_est   = int(rider.get("capacidad_estimada", 0) or 0)
    has_delay = "delay"      in throws
    has_fill  = "front_fill" in throws

    # ── 1. Claude API (modelo primario) ──────────────────────────────────────
    claude_result = _claude_acoustic_analysis(rider, lw_db)

    # ── 2. pyroomacoustics (RT60 secundario) ─────────────────────────────────
    pra_result = _pra_simulate(lw_db)

    # RT60: pyroomacoustics > Claude > Sabine
    if pra_result:
        rt60_s = pra_result["rt60_s"]
    elif claude_result and claude_result.get("rt60_estimado_s"):
        rt60_s = round(float(claude_result["rt60_estimado_s"]), 2)
    else:
        rt60_s = round(_room_rt60_sabine(), 2)

    # Índice de datos por sector de Claude
    claude_by_id: dict[str, dict] = {}
    if claude_result:
        for _cs in claude_result.get("sectores", []):
            claude_by_id[_cs.get("id", "")] = _cs

    # Modelo activo (para metadatos)
    if claude_result:
        _modelo = "Claude API acústico geométrico"
        if pra_result:
            _modelo += " + pyroomacoustics RT60"
        _fuente = (
            "Claude API (claude-sonnet-4-6) · análisis acústico geométrico real"
            + (" · pyroomacoustics RT60" if pra_result else "")
        )
    elif pra_result:
        _modelo = "pyroomacoustics + Sabine-Eyring"
        _fuente = "Simulación pyroomacoustics (ISM max_order=3) · Sabine-Eyring · Faro Protocol · Dale Play"
    else:
        _modelo = "Sabine-Eyring geométrico"
        _fuente = "Modelo acústico Sabine-Eyring · Faro Protocol · Dale Play"

    alertas: list[str] = []
    sectores_out: list[dict] = []
    spl_vals: list[float] = []
    n_optima = 0

    for sec in _SECTORES:
        dist  = sec["dist_m"]
        h_m   = sec["height_m"]
        angle = sec["angle"]

        cs = claude_by_id.get(sec["id"], {})

        # SPL: Claude > pyroomacoustics > modelo Sabine-Eyring
        if cs and "spl_db" in cs:
            spl = float(cs["spl_db"])
        elif pra_result and sec["id"] in pra_result.get("spl_per_sector", {}):
            spl = pra_result["spl_per_sector"][sec["id"]]
        else:
            if has_delay and angle not in (0, 25, 335):
                dist = dist * 0.65
            spl = _spl_room(lw_db, dist, h_m)

        # Sightline: campo_central siempre óptima (frontal directo).
        # Platea alta → atención por distancia/elevación.
        # Resto: clasificación por ángulo respecto al escenario (no por SPL).
        if sec["id"] == "campo_central":
            sl = "optima"
        elif sec["height_m"] > 10:
            sl = "atencion"
        else:
            sl = _sightline(sec["angle"])

        # cobertura_optima cuenta sectores que alcanzan el mínimo del rider (98 dB)
        if spl >= 98:    cobertura = "optima";   n_optima += 1
        elif spl >= 93:  cobertura = "buena"
        elif spl >= 88:  cobertura = "aceptable"
        else:            cobertura = "baja"

        sec_alertas: list[str] = []
        # Recomendaciones de Claude primero
        if cs and cs.get("recomendaciones"):
            sec_alertas.extend(str(r)[:80] for r in cs["recomendaciones"][:2])
        # Alertas computadas como fallback/complemento
        if sl == "obstruida" and not sec_alertas:
            sec_alertas.append(
                f"Sightline obstruido en {sec['name']} — pantalla lateral recomendada"
            )
        if cobertura == "baja" and not any("delay" in a for a in sec_alertas):
            sec_alertas.append(
                f"SPL insuficiente ({spl:.0f} dB) en {sec['name']} — agregar delay tower"
            )
        if cobertura in ("baja", "aceptable") and not has_delay and not sec_alertas:
            sec_alertas.append(
                f"{sec['name']}: cobertura parcial sin delay towers declaradas"
            )

        sec_out: dict = {
            "id":        sec["id"],
            "name":      sec["name"],
            "spl_db":    round(spl, 1),
            "sightline": sl,
            "cobertura": cobertura,
            "dist_m":    sec["dist_m"],
            "height_m":  h_m,
            "alertas":   sec_alertas,
        }
        if cs and "c_value" in cs:
            sec_out["c_value_db"] = round(float(cs["c_value"]), 1)

        sectores_out.append(sec_out)
        spl_vals.append(spl)

    # Alertas globales: Claude + computadas
    if claude_result and claude_result.get("alertas_globales"):
        alertas.extend(str(a)[:100] for a in claude_result["alertas_globales"][:3])
    if cap_est > 0 and cap_est > VENUE_CAP * 0.85:
        alertas.append(
            f"Capacidad estimada {cap_est:,} supera el 85% del aforo ({VENUE_CAP:,}) — "
            "revisar plan de evacuación"
        )
    if not has_delay:
        alertas.append(
            "Sin delay towers declaradas — cobertura acústica parcial en tribunas laterales y Platea Alta"
        )
    low_cov = [s for s in sectores_out if s["cobertura"] == "baja"]
    if low_cov:
        alertas.append(
            f"Cobertura insuficiente en {len(low_cov)} sector(es): " +
            ", ".join(s["name"] for s in low_cov)
        )

    n = len(_SECTORES)
    result = {
        "artista":              rider.get("artist", "Artista"),
        "show_date":            rider.get("show_date", ""),
        "sectores":             sectores_out,
        "alertas_globales":     alertas,
        "spl_promedio_db":      round(sum(spl_vals) / n, 1) if n else 0,
        "cobertura_optima_pct": round(n_optima / n * 100, 1) if n else 0,
        "rt60_s":               rt60_s,
        "modelo_acustico":      _modelo,
        "fuente":               _fuente,
        "sala": {
            "dimensiones_m":       f"{_ROOM_W:.0f}×{_ROOM_L:.0f}×{_ROOM_H:.0f}",
            "rt60_sabine_s":       round(_room_rt60_sabine(), 2),
            "room_constant_R_m2":  round(_room_constant_r(), 0),
        },
        "claude_raw": claude_result,
    }

    # Guardar JSON del modelo acústico real
    MODELS_DIR.mkdir(exist_ok=True)
    out = MODELS_DIR / f"acoustic_real_{show_id}.json"
    out.write_text(
        json.dumps({**result, "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M")},
                   ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    log.info("Acoustic real → %s  (RT60=%.2fs, modelo=%s)", out, rt60_s, result["modelo_acustico"])
    return result
