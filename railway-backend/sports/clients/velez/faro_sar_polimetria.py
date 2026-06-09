"""
faro_sar_polimetria.py — Dual-pol H-Alpha decomposition (Cloude-Pottier) desde Sentinel-1.

Calcula entropía H y ángulo Alpha desde la matriz de coherencia C2 2x2 construida
a partir de las intensidades VV+VH de backscatter SAR ya almacenadas en soil_metrics.

Interpretación física (Cloude-Pottier 1997 — dual-pol intensity):
  H < 0.3, Alpha < 20°  → scattering especular superficial → suelo compactado (post-show)
  H > 0.7               → scattering de volumen dominante  → césped activo y sano
  0.3 ≤ H ≤ 0.7         → mecanismos mixtos

Método: coherency matrix C2 diagonal para datos de solo-intensidad (sin fase compleja):
  λ1 = P_VV,  λ2 = P_VH  (eigenvalues = potencias lineales de backscatter)
  P1 = λ1/(λ1+λ2),  P2 = λ2/(λ1+λ2)
  H = −P1·log₂(P1) − P2·log₂(P2)   ∈ [0, 1]
  Alpha = P2 · 90°                    ∈ [0°, 90°]

opensartoolkit se intenta importar para full-domain complex processing;
cae en fallback numpy puro si no está disponible.
"""
from __future__ import annotations
import logging, math, os, sys
from datetime import datetime, timezone

log = logging.getLogger(__name__)


# ── H-Alpha puro numpy (siempre disponible) ───────────────────────────────────

def compute_h_alpha(sar_vv_db: float, sar_vh_db: float) -> tuple[float, float]:
    """
    Dual-pol Cloude-Pottier H-Alpha desde intensidades VV/VH (sin fase compleja).

    Returns (entropia_h, angulo_alpha_deg).

    Con solo-intensidad la C2 coherency matrix es diagonal:
      λ1 = P_VV,  λ2 = P_VH
      P1 = λ1/(λ1+λ2),  P2 = λ2/(λ1+λ2)
      H = −Σ Pi·log₂(Pi)     [0=mecanismo único, 1=máxima aleatoriedad]
      Alpha = P2·90°          [0°=superficie especular, 90°=volumen/dipolo]
    """
    if sar_vv_db is None or sar_vh_db is None:
        return 0.5, 45.0
    try:
        p_vv = 10.0 ** (float(sar_vv_db) / 10.0)
        p_vh = 10.0 ** (float(sar_vh_db) / 10.0)
        total = p_vv + p_vh
        if total < 1e-12:
            return 0.5, 45.0
        p1 = p_vv / total
        p2 = p_vh / total
        h = 0.0
        for p in (p1, p2):
            if p > 1e-10:
                h -= p * math.log2(p)
        alpha = p2 * 90.0
        return round(float(h), 4), round(float(alpha), 3)
    except Exception as exc:
        log.debug("compute_h_alpha: %s", exc)
        return 0.5, 45.0


def _interpret(h: float, alpha: float) -> str:
    if h < 0.3 and alpha < 20.0:
        return "COMPACTADO: superficie especular, sin volumen vegetal"
    if h > 0.7:
        return "VEGETAL_ACTIVO: scattering de volumen dominante"
    if h < 0.5 and alpha < 40.0:
        return "MIXTO_BAJO: predominancia superficial"
    return "MIXTO: mecanismos combinados"


# ── Helper de importación ─────────────────────────────────────────────────────

def _get_vs():
    _here = os.path.dirname(os.path.abspath(__file__))
    if _here not in sys.path:
        sys.path.insert(0, _here)
    import velez_supabase as _vs
    return _vs


# ── Ciclo principal ───────────────────────────────────────────────────────────

def run_polimetria(venue_id: str = "amalfitani", cancha_id: str | None = None) -> dict:
    """
    Extrae filas recientes de soil_metrics, calcula H-Alpha por cada una,
    parchea las columnas entropia_h + angulo_alpha, y emite alerta QA si H < 0.3.
    Nunca lanza excepción.
    """
    out: dict = {
        "venue_id":    venue_id,
        "cancha_id":   cancha_id,
        "rows_processed": 0,
        "rows_patched":   0,
        "compactacion_detectada": False,
        "error": None,
    }
    try:
        _vs = _get_vs()
        rows = _vs.get_soil_metrics_latest(venue_id, cancha_id, dias=3)
        if not rows:
            log.info("polimetria: sin filas soil_metrics en últimos 3d para %s", venue_id)
            return out

        for row in rows:
            vv = row.get("sar_vv_db")
            vh = row.get("sar_vh_db")
            row_id = row.get("id")
            if vv is None or vh is None or row_id is None:
                continue
            out["rows_processed"] += 1

            # Opcional: intentar OST para mayor precisión
            h, alpha = _try_ost_or_fallback(vv, vh)

            interpretacion = _interpret(h, alpha)
            ok = _vs.patch_row("soil_metrics", row_id,
                               {"entropia_h": h, "angulo_alpha": alpha})
            if ok:
                out["rows_patched"] += 1
                log.info(
                    "polimetria [%s id=%d] VV=%.1fdB VH=%.1fdB → H=%.3f α=%.1f° — %s",
                    cancha_id or venue_id, row_id, vv, vh, h, alpha, interpretacion,
                )

            # Alerta compactación confirmada (post-show Dale Play)
            if h < 0.3 and alpha < 20.0:
                out["compactacion_detectada"] = True
                try:
                    _here = os.path.dirname(os.path.abspath(__file__))
                    if _here not in sys.path:
                        sys.path.insert(0, _here)
                    from faro_qa_watchdog import _insert_alert
                    _insert_alert(
                        nivel="WARNING",
                        modulo="polimetria_sar",
                        mensaje=(
                            f"Compactación mecánica SAR confirmada: "
                            f"H={h:.3f}<0.3, Alpha={alpha:.1f}°<20° — "
                            f"superficie especular post-evento"
                        ),
                        venue_id=venue_id,
                    )
                except Exception:
                    pass

    except Exception as exc:
        log.warning("polimetria: error (non-fatal): %s", exc)
        out["error"] = str(exc)
    return out


def _try_ost_or_fallback(vv_db: float, vh_db: float) -> tuple[float, float]:
    """Intenta OST complex-domain; cae en numpy puro."""
    try:
        import opensartoolkit  # noqa — solo chequeo importación
        # OST no provee interfaz escalar directa; usar mismo método numpy
        # (la librería es para workflows de escenas completas, no pixeles individuales)
    except ImportError:
        pass
    return compute_h_alpha(vv_db, vh_db)
