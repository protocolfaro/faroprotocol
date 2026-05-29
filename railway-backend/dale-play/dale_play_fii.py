"""
dale_play_fii.py — Índice Faro de Integridad (FII).

FII = 40% NDVI + 35% EGMS + 25% Layout compliance
Escala 0-100. 100 = configuración perfecta.
"""
from __future__ import annotations


def compute_fii(
    ndvi: float | None,
    egms_data: dict | None,
    layout: dict | None,
    drainage_data: dict | None,
) -> dict:
    """
    Calcula el FII y sus componentes.
    Retorna dict con fii, componentes y semáforo.
    """

    # ── Componente NDVI (40%) ─────────────────────────────────────────────────
    # 0.45+ → 100, 0.30-0.45 → 70-100, 0.20-0.30 → 40-70, <0.20 → 0-40
    if ndvi is None:
        ndvi_score = 50.0
        ndvi_label = "N/D"
    elif ndvi >= 0.45:
        ndvi_score = 100.0
        ndvi_label = "Excelente"
    elif ndvi >= 0.30:
        ndvi_score = 70.0 + (ndvi - 0.30) / 0.15 * 30.0
        ndvi_label = "Bueno"
    elif ndvi >= 0.20:
        ndvi_score = 40.0 + (ndvi - 0.20) / 0.10 * 30.0
        ndvi_label = "Regular"
    elif ndvi >= 0.10:
        ndvi_score = 10.0 + (ndvi - 0.10) / 0.10 * 30.0
        ndvi_label = "Malo"
    else:
        ndvi_score = 0.0
        ndvi_label = "Crítico"

    # ── Componente EGMS (35%) ─────────────────────────────────────────────────
    # Sin datos → neutral 50. Sectores críticos (>3.5 mm/yr) = -25 c/u.
    # Sectores atención (2.5-3.5) = -10 c/u.
    if not egms_data or "error" in egms_data:
        egms_score = 50.0
        egms_label = "N/D"
    else:
        resumen = egms_data.get("resumen") or {}
        n_crit  = resumen.get("sectores_criticos", 0)
        n_aten  = resumen.get("sectores_atencion", 0)
        egms_score = max(0.0, 100.0 - n_crit * 25.0 - n_aten * 10.0)
        if n_crit >= 2:
            egms_label = "Crítico"
        elif n_crit == 1 or n_aten >= 2:
            egms_label = "Atención"
        else:
            egms_label = "Estable"

    # ── Componente Layout compliance (25%) ────────────────────────────────────
    # Sin layout → neutral 50. Colisión zona roja = -40. Zona amarilla = -15.
    if not layout or not drainage_data:
        layout_score = 50.0
        layout_label = "Sin layout"
    else:
        struct  = layout.get("estructuras") or {}
        zonas   = drainage_data.get("zonas") or []

        def _overlaps(xp0, xp1, yp0, yp1, z):
            return (xp0 < z["x_pct"][1] and xp1 > z["x_pct"][0] and
                    yp0 < z["y_pct"][1] and yp1 > z["y_pct"][0])

        colisiones_roja = 0
        colisiones_amarilla = 0

        esc = struct.get("escenario") or {}
        ex, ey = esc.get("x_m"), esc.get("y_m")
        ew = esc.get("ancho_m") or 28
        ed = esc.get("profundidad_m") or 20
        if ex is not None and ey is not None:
            ex0p = (ex - ew/2 + 34.0) / 68.0 * 100
            ex1p = (ex + ew/2 + 34.0) / 68.0 * 100
            ey0p = (ey - ed/2 + 52.5) / 105.0 * 100
            ey1p = (ey + ed/2 + 52.5) / 105.0 * 100
            for z in zonas:
                if _overlaps(ex0p, ex1p, ey0p, ey1p, z):
                    if z.get("riesgo") == "alto":
                        colisiones_roja += 1
                    elif z.get("riesgo") == "medio":
                        colisiones_amarilla += 1

        for t in (struct.get("torres_lr") or []):
            tx, ty = t.get("x_m"), t.get("y_m")
            if tx is not None and ty is not None:
                txp = (tx + 34.0) / 68.0 * 100
                typ = (ty + 52.5) / 105.0 * 100
                for z in zonas:
                    if _overlaps(txp - 2, txp + 2, typ - 2, typ + 2, z):
                        if z.get("riesgo") == "alto":
                            colisiones_roja += 1
                        elif z.get("riesgo") == "medio":
                            colisiones_amarilla += 1

        layout_score = max(0.0, 100.0
                           - colisiones_roja * 40.0
                           - colisiones_amarilla * 15.0)
        if colisiones_roja > 0:
            layout_label = f"{colisiones_roja} colisión(es) zona ROJA"
        elif colisiones_amarilla > 0:
            layout_label = f"{colisiones_amarilla} precaución zona AMARILLA"
        else:
            layout_label = "Sin colisiones"

    # ── FII global ────────────────────────────────────────────────────────────
    fii = round(0.40 * ndvi_score + 0.35 * egms_score + 0.25 * layout_score, 1)

    semaforo = ("critico"  if fii < 40
                else "atencion" if fii < 65
                else "ok")
    sello = ("CERTIFICADO FARO PROTOCOL" if fii >= 65
             else "REQUIERE INTERVENCIÓN" if fii >= 40
             else "RIESGO OPERATIVO")

    return {
        "fii":         fii,
        "semaforo":    semaforo,
        "sello":       sello,
        "componentes": {
            "ndvi":   {"score": round(ndvi_score, 1),   "peso": 40, "label": ndvi_label},
            "egms":   {"score": round(egms_score, 1),   "peso": 35, "label": egms_label},
            "layout": {"score": round(layout_score, 1), "peso": 25, "label": layout_label},
        },
    }
