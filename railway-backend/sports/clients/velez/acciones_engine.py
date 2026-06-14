"""
acciones_engine.py — Motor de acciones agronómicas dinámicas · Faro Protocol · Vélez

Genera roger.acciones y roger.kpis en tiempo real desde datos del sistema.
NO hay strings hardcodeados — todo derivado de sensores reales.

Fuentes:
  - weather_live (T, RH, ET0, riesgo fungosis, Smith-Kerns)
  - heatmaps[*] (NDVI + IPOS reales, último ciclo satelital o Kalman)
  - canchas[] (scores fusión)
  - mes del año (contexto estacional — dormancia Jun-Ago)

Llamado diariamente por data_refresh.run_refresh() antes del push a GitHub.
"""
from __future__ import annotations
import logging
from datetime import date
from typing import Optional

log = logging.getLogger(__name__)

# ── Estaciones (hemisferio sur) ────────────────────────────────────────────────
_WINTER = frozenset({6, 7, 8})   # Jun–Ago: dormancia bermuda/kikuyo
_SPRING = frozenset({9, 10, 11}) # Sep–Nov: reactivación
_SUMMER = frozenset({12, 1, 2})  # Dic–Feb: plena actividad

# NDVI mínimo de crisis por mes (por debajo → daño real, no dormancia)
_NDVI_CRISIS: dict[int, float] = {
    1: 0.14, 2: 0.14, 3: 0.11, 4: 0.09, 5: 0.06,
    6: 0.04, 7: 0.04, 8: 0.05,
    9: 0.08, 10: 0.11, 11: 0.13, 12: 0.14,
}

# Orden de prioridad de canchas Villa Olímpica para Roger
_VO_ORDER = ["1fa","2fa","3fa","4fa","5fa","6fa","7fa","8fa","9fa","10fa","1fp","2fp"]


def _crisis(ndvi: float, month: int) -> bool:
    return ndvi < _NDVI_CRISIS.get(month, 0.10)


def _lbl(cid: str) -> str:
    return cid.upper()


def generate_acciones(
    weather_live: dict,
    heatmaps: dict,
    physics_acciones: list[str],
    month: Optional[int] = None,
) -> list[str]:
    """
    Genera lista de acciones para Roger ordenadas por severidad.

    physics_acciones:  [ventana de corte, riego SAR] — van primero.

    Reglas (P1 > P2 > P3 > P4 > P5 > P6):
      P1  IPOS ≥ 350 → descanso urgente
      P2  IPOS ≥ 100 + NDVI < crisis → deterioro activo bajo carga
      P3  Riesgo fungosis ALTO → fungicida HOY (todas canchas)
      P4  NDVI < crisis + descansada → daño no relacionado a uso
      P5  Riesgo fungosis MEDIO → monitoreo + canchas específicas
      P6  GNDVI < 0.28 + no invierno → fertilizar N
      P7  NDVI < 0.06 + descanso + primavera → resembrar
      P8  Invierno sin alertas → mantenimiento estructural
    """
    m = month or date.today().month
    is_winter = m in _WINTER
    is_spring = m in _SPRING

    fung       = weather_live.get("riesgo_fungosis", {})
    fung_nivel = fung.get("nivel", "bajo")
    fung_set   = set(fung.get("canchas_en_riesgo", []))
    sk_pct     = weather_live.get("riesgo_dollar_spot_pct", 0)
    h_fav      = fung.get("horas_favorables_48h", 0)

    p1_limite, p2_activo, p4_danio = [], [], []
    p5_fung_medio, p6_nitro, p7_resembrar = [], [], []
    fung_all_urgent = False

    for cid in _VO_ORDER:
        hm = heatmaps.get(cid)
        if not isinstance(hm, dict):
            continue

        ndvi  = hm.get("ndvi")  or 0.0
        gndvi = hm.get("gndvi") or 0.0
        ipos  = hm.get("ipos")  or 0.0
        metodo = hm.get("metodo_generacion", "real")
        src_tag = " (Kalman)" if "KALMAN" in str(metodo) else ""

        lbl = _lbl(cid)

        # P1: al límite por uso
        if ipos >= 350:
            p1_limite.append((lbl, ipos, ndvi, src_tag))

        # P2: carga activa + NDVI en crisis
        elif ipos >= 100 and _crisis(ndvi, m):
            p2_activo.append((lbl, ndvi, ipos, src_tag))

        # P4: descansada pero NDVI en crisis (daño ambiental / enfermedad)
        elif ipos < 50 and _crisis(ndvi, m) and not is_winter:
            p4_danio.append((lbl, ndvi, src_tag))

        # P5: riesgo fungal medio + en uso activo
        if fung_nivel == "medio" and cid in fung_set and ipos >= 50:
            p5_fung_medio.append(lbl)

        # P6: deficiencia N (fuera de invierno — en invierno bajo GNDVI es normal)
        if not is_winter and gndvi > 0 and gndvi < 0.28 and ipos < 350:
            p6_nitro.append((lbl, gndvi))

        # P7: resembrar en primavera
        if is_spring and ipos < 50 and ndvi < 0.06:
            p7_resembrar.append((lbl, ndvi, src_tag))

    if fung_nivel == "alto":
        fung_all_urgent = True

    # ── Ensamblar acciones ─────────────────────────────────────────────────────
    acciones = list(physics_acciones)

    # P1
    for lbl, ipos, ndvi, src in sorted(p1_limite, key=lambda x: -x[1])[:3]:
        acciones.append(
            f"{lbl}: DESCANSO URGENTE — {ipos:.0f} pers·h · "
            f"NDVI {ndvi:.3f}{src} — no programar sesiones hasta recuperación"
        )

    # P2
    for lbl, ndvi, ipos, src in p2_activo[:2]:
        rec = "resembrar zona central" if ndvi < 0.04 else "fertilizar + reducir carga"
        acciones.append(
            f"{lbl}: NDVI {ndvi:.3f}{src} crítico bajo {ipos:.0f} pers·h esta semana — {rec}"
        )

    # P3
    if fung_all_urgent:
        acciones.append(
            f"Fungicida URGENTE — todas las canchas — "
            f"{sk_pct:.0f}% Dollar Spot · {h_fav}h favorables 48h — aplicar HOY"
        )

    # P4
    for lbl, ndvi, src in p4_danio[:2]:
        acciones.append(
            f"{lbl}: NDVI {ndvi:.3f}{src} en crisis sin carga — "
            f"posible enfermedad/encharcamiento — inspección visual urgente"
        )

    # P5
    if p5_fung_medio and not fung_all_urgent:
        cs = " · ".join(p5_fung_medio[:4])
        acciones.append(
            f"Monitorear {cs} — Dollar Spot MEDIO {sk_pct:.0f}% · "
            f"{h_fav}h favorables 48h — aplicar fungicida si aparecen manchas"
        )

    # P6
    if p6_nitro:
        cs = " · ".join(c for c, _ in p6_nitro[:3])
        avg_g = round(sum(g for _, g in p6_nitro) / len(p6_nitro), 3)
        acciones.append(
            f"Fertilizar N {cs} — GNDVI medio {avg_g:.3f} < 0.28 — "
            f"20 kg N/ha esta semana"
        )

    # P7
    if p7_resembrar:
        cs = " · ".join(c for c, _, _ in p7_resembrar[:2])
        acciones.append(
            f"Resembrar {cs} — NDVI < 0.06 en primavera — "
            f"sembrar bermuda/kikuyo antes de temporada alta"
        )

    # P8: contexto invernal sin alertas críticas
    if is_winter and not p1_limite and not fung_all_urgent:
        acciones.append(
            f"Mantenimiento invernal — NDVI en dormancia Jun–Ago (estacional) · "
            f"aerificar + compactar + preparar suelo para reactivación primaveral"
        )

    return acciones[:9]  # cap: panel legible


def generate_kpis(
    heatmaps: dict,
    canchas: list[dict],
    weather_live: dict,
    month: Optional[int] = None,
) -> list[dict]:
    """
    Genera roger.kpis desde datos reales actuales.
    Reemplaza valores hardcodeados de verano con datos del sistema.
    """
    m = month or date.today().month
    is_winter = m in _WINTER

    fung    = weather_live.get("riesgo_fungosis", {})
    sk_pct  = weather_live.get("riesgo_dollar_spot_pct", 0)
    h_fav   = fung.get("horas_favorables_48h", 0)
    fung_sem = ("rojo" if fung.get("nivel") == "alto"
                else "amarillo" if fung.get("nivel") == "medio"
                else "verde")

    # Cancha más crítica: mayor IPOS con NDVI bajo
    top_crit = None
    for cid in _VO_ORDER:
        hm = heatmaps.get(cid)
        if not isinstance(hm, dict):
            continue
        ipos = hm.get("ipos") or 0
        ndvi = hm.get("ndvi") or 1.0
        if top_crit is None or (ipos > top_crit[1] and ndvi < top_crit[2]):
            top_crit = (cid, ipos, ndvi)

    # Cancha con NDVI más bajo
    lowest_ndvi_entry = None
    for cid in _VO_ORDER:
        hm = heatmaps.get(cid)
        if not isinstance(hm, dict) or not hm.get("ndvi"):
            continue
        ndvi = hm["ndvi"]
        if lowest_ndvi_entry is None or ndvi < lowest_ndvi_entry[1]:
            lowest_ndvi_entry = (cid, ndvi)

    kpis: list[dict] = []

    # KPI 1: Cancha más crítica (IPOS)
    if top_crit:
        cid_c, ipos_c, ndvi_c = top_crit
        hm_c = heatmaps.get(cid_c, {})
        ndvi_hist = next(
            (c.get("ndvi_prev") for c in canchas if c.get("id") == cid_c), ndvi_c
        )
        crisis_now = _crisis(ndvi_c, m)
        sub = ("CRÍTICO — carga extrema" if ipos_c >= 350
               else "Uso alto" if ipos_c >= 200
               else "Uso moderado")
        if crisis_now:
            sub += " + NDVI en crisis"
        elif is_winter:
            sub += " · dormancia invernal"
        kpis.append({
            "label":      f"NDVI {_lbl(cid_c)}",
            "value":      f"{ndvi_c:.3f}",
            "value_prev": f"{ndvi_hist:.3f}" if isinstance(ndvi_hist, float) else "—",
            "unit":       "",
            "sub":        sub,
            "sem":        hm_c.get("semaforo", "verde"),
        })

    # KPI 2: NDVI más bajo (si es diferente cancha)
    if lowest_ndvi_entry and (not top_crit or lowest_ndvi_entry[0] != top_crit[0]):
        cid_l, ndvi_l = lowest_ndvi_entry
        hm_l   = heatmaps.get(cid_l, {})
        ndvi_p = next(
            (c.get("ndvi_prev") for c in canchas if c.get("id") == cid_l), ndvi_l
        )
        sub_l  = "Crítico" if _crisis(ndvi_l, m) else ("Dormancia estacional" if is_winter else "Bajo")
        kpis.append({
            "label":      f"NDVI {_lbl(cid_l)}",
            "value":      f"{ndvi_l:.3f}",
            "value_prev": f"{ndvi_p:.3f}" if isinstance(ndvi_p, float) else "—",
            "unit":       "",
            "sub":        sub_l,
            "sem":        hm_l.get("semaforo", "verde"),
        })

    # KPI 3: Riesgo fungosis
    kpis.append({
        "label":      "Dollar Spot",
        "value":      f"{sk_pct:.0f}",
        "value_prev": "—",
        "unit":       "%",
        "sub":        f"Smith-Kerns · {h_fav}h T/HR favorables 48h",
        "sem":        fung_sem,
    })

    # KPI 4: Score fusión promedio con fecha
    scores = [c.get("score") for c in canchas if isinstance(c.get("score"), (int, float))]
    if scores:
        avg   = round(sum(scores) / len(scores))
        avg_p = round(sum(c.get("score_prev", c.get("score", 0)) for c in canchas if c.get("score")) / len(scores))
        sem_a = "verde" if avg >= 70 else "amarillo" if avg >= 50 else "rojo"
        kpis.append({
            "label":      "Score VO",
            "value":      str(avg),
            "value_prev": str(avg_p),
            "unit":       "",
            "sub":        f"Promedio {len(scores)} canchas · {date.today().isoformat()}",
            "sem":        sem_a,
        })

    return kpis
