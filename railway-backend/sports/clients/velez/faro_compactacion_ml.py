"""
faro_compactacion_ml.py — Índice de compactación del suelo (0–100) via XGBoost/RandomForest.

Ruta 1 — ML (XGBoost o RandomForest):
  Entrena con historial de soil_metrics (≥ 20 filas):
    Features: sar_vv_db, theta_soil, horas_uso_14d, entropia_h, angulo_alpha
    Target:   puntuación física calculada por score_compactacion_fisica()
    Modelo:   XGBRegressor (si disponible), else RandomForestRegressor

Ruta 2 — Física pura (siempre disponible):
  score_compactacion_fisica() combina:
    - θ_soil bajo → suelo denso
    - SAR VV alto → superficie dura
    - H < 0.3, Alpha < 20° → scattering especular confirmado
    - Aireación reciente → descuenta compactación
    - Riego intensivo → ablanda suelo

Guardado: soil_metrics.compactacion_index_ml (0–100)
Alerta QA: compactacion_index_ml > 70 → alerta de nivel WARNING
"""
from __future__ import annotations
import logging, os, sys
from datetime import datetime, timedelta, timezone

log = logging.getLogger(__name__)


def _get_vs():
    _here = os.path.dirname(os.path.abspath(__file__))
    if _here not in sys.path:
        sys.path.insert(0, _here)
    import velez_supabase as _vs
    return _vs


# ── Scoring físico ────────────────────────────────────────────────────────────

def score_compactacion_fisica(
    soil_row: dict,
    intervenciones: list[dict] | None = None,
) -> float:
    """
    Índice de compactación 0–100 basado en física del suelo.

    Componentes positivos (aumentan compactación):
      θ_soil < 0.15   → +35  (suelo muy seco = compacto)
      θ_soil < 0.20   → +20  (sub-óptimo)
      SAR VV > -10dB  → +20  (superficie dura/reflectiva)
      H < 0.3         → +15  (scattering especular)
      Alpha < 20°     → +10  (confirma sin volumen vegetal)

    Componentes negativos (reducen compactación):
      Aireación < 14d → -30  (intervención mecánica reciente)
      Aireación < 7d  → -40  (aireación muy reciente)
      Riego ≥ 30mm    → -15  (ablandamiento hídrico)
      θ_soil > 0.30   → -10  (suelo húmedo)

    Rango final: clamp [0, 100].
    """
    score = 0.0

    # ── Humedad SAR ──────────────────────────────────────────────────────────
    theta = float(soil_row.get("theta_soil") or 0.20)  # sat-fallback: ok — módulo inactivo, ImportError guard en gen_velez_canchero.py:315
    if theta < 0.12:
        score += 35
    elif theta < 0.15:
        score += 25
    elif theta < 0.20:
        score += 12
    elif theta > 0.30:
        score -= 10

    # ── Backscatter SAR VV ────────────────────────────────────────────────────
    vv = float(soil_row.get("sar_vv_db") or -14.0)
    if vv > -8:
        score += 22
    elif vv > -10:
        score += 14
    elif vv > -12:
        score += 6

    # ── H-Alpha polarimétrico ─────────────────────────────────────────────────
    h_val = soil_row.get("entropia_h")
    alpha = soil_row.get("angulo_alpha")
    if h_val is not None:
        h_val = float(h_val)
        if h_val < 0.3:
            score += 15
        elif h_val < 0.45:
            score += 5
    if alpha is not None:
        alpha = float(alpha)
        if alpha < 15.0:
            score += 10
        elif alpha < 25.0:
            score += 4

    # ── Intervenciones recientes ──────────────────────────────────────────────
    if intervenciones:
        now = datetime.now(timezone.utc)
        for iv in intervenciones:
            tipo = iv.get("tipo", "")
            try:
                ts_str = str(iv.get("created_at", ""))[:19]
                ts_dt  = datetime.fromisoformat(ts_str).replace(tzinfo=timezone.utc)
                age_days = (now - ts_dt).days
            except Exception:
                age_days = 99

            if tipo == "aireacion":
                if age_days <= 3:
                    score -= 45
                elif age_days <= 7:
                    score -= 35
                elif age_days <= 14:
                    score -= 20
            elif tipo == "riego":
                detalle = iv.get("detalle", "")
                # Detectar mm en detalle (ej. "30mm" o "30 mm")
                try:
                    import re
                    mm_match = re.search(r"(\d+)\s*mm", detalle)
                    mm_val = float(mm_match.group(1)) if mm_match else 10.0
                except Exception:
                    mm_val = 10.0
                if mm_val >= 30:
                    score -= 15
                elif mm_val >= 15:
                    score -= 7

    return round(max(0.0, min(100.0, score)), 1)


# ── ML con historial ──────────────────────────────────────────────────────────

def _extract_features(row: dict, horas_uso: float = 0.0) -> list[float]:
    """Extrae vector de features desde una fila soil_metrics."""
    return [
        float(row.get("sar_vv_db")    or -14.0),
        float(row.get("theta_soil")   or 0.20),  # sat-fallback: ok — módulo inactivo, ImportError guard en gen_velez_canchero.py:315
        float(horas_uso),
        float(row.get("entropia_h")   or 0.50),
        float(row.get("angulo_alpha") or 45.0),
    ]


def _get_horas_uso(venue_id: str, cancha_id: str | None, dias: int = 14) -> float:
    """Suma horas_uso de velez_intervenciones en últimos `dias` días."""
    try:
        _vs = _get_vs()
        interv = _vs.get_intervenciones_recientes(cancha_id or venue_id, dias=dias)
        return sum(float(iv.get("horas_uso") or 0) for iv in interv if iv.get("horas_uso"))
    except Exception:
        return 0.0


def train_and_predict_ml(
    history_rows: list[dict],
    target_row:   dict,
    horas_uso:    float = 0.0,
    intervenciones: list[dict] | None = None,
) -> float | None:
    """
    Entrena XGBoost (o RandomForest) con historial, predice compactación para target_row.
    Target es score_compactacion_fisica() calculado en todas las filas históricas.
    Retorna score predicho o None si faltan datos.
    """
    if len(history_rows) < 10:
        return None

    try:
        import numpy as np

        X: list[list[float]] = []
        y: list[float]       = []
        for row in history_rows:
            feat   = _extract_features(row, horas_uso)
            target = score_compactacion_fisica(row, intervenciones)
            X.append(feat)
            y.append(target)

        X_arr = np.array(X, dtype="float32")
        y_arr = np.array(y, dtype="float32")
        x_pred = np.array([_extract_features(target_row, horas_uso)], dtype="float32")

        # Intentar XGBoost
        try:
            from xgboost import XGBRegressor
            model = XGBRegressor(
                n_estimators=50, max_depth=4, learning_rate=0.15,
                subsample=0.8, verbosity=0, random_state=42,
            )
            model.fit(X_arr, y_arr)
            pred = float(model.predict(x_pred)[0])
            log.info("compactacion_ml: XGBoost predict=%.1f (n_train=%d)", pred, len(X_arr))
            return round(max(0.0, min(100.0, pred)), 1)
        except ImportError:
            pass

        # Fallback: RandomForestRegressor
        from sklearn.ensemble import RandomForestRegressor
        model = RandomForestRegressor(n_estimators=50, random_state=42, n_jobs=1)
        model.fit(X_arr, y_arr)
        pred = float(model.predict(x_pred)[0])
        log.info("compactacion_ml: RandomForest predict=%.1f (n_train=%d)", pred, len(X_arr))
        return round(max(0.0, min(100.0, pred)), 1)

    except Exception as exc:
        log.debug("compactacion_ml: train_and_predict_ml: %s", exc)
        return None


# ── Ciclo principal ───────────────────────────────────────────────────────────

def run_compactacion_cycle(venue_id: str = "amalfitani",
                            cancha_id: str | None = None) -> dict:
    """
    Calcula compactacion_index_ml, parchea soil_metrics, emite alerta si > 70.
    Nunca lanza excepción.
    """
    out: dict = {
        "venue_id":               venue_id,
        "compactacion_index_ml":  None,
        "metodo":                 None,
        "alerta":                 False,
        "error":                  None,
    }
    try:
        _vs = _get_vs()
        soil_row = _vs.get_latest_soil_row(venue_id, cancha_id)
        if soil_row is None:
            log.info("compactacion_ml: sin filas soil_metrics para %s", venue_id)
            return out

        horas_uso    = _get_horas_uso(venue_id, cancha_id, dias=14)
        intervenciones = _vs.get_intervenciones_recientes(cancha_id or venue_id, dias=14)

        # Intentar ML primero con historial de 90 días
        history_rows = _vs.get_soil_metrics_latest(venue_id, cancha_id, dias=90)
        ml_score = train_and_predict_ml(history_rows, soil_row,
                                        horas_uso=horas_uso,
                                        intervenciones=intervenciones)

        if ml_score is not None:
            score  = ml_score
            metodo = "XGBoost" if _xgb_available() else "RandomForest"
        else:
            score  = score_compactacion_fisica(soil_row, intervenciones)
            metodo = "fisica_pura"

        out.update({"compactacion_index_ml": score, "metodo": metodo})
        log.info("compactacion [%s] %s → score=%.1f", venue_id, metodo, score)

        # Parchear soil_metrics
        row_id = soil_row.get("id")
        if row_id:
            _vs.patch_row("soil_metrics", row_id, {"compactacion_index_ml": score})

        # Alerta QA
        if score > 70:
            out["alerta"] = True
            try:
                _here = os.path.dirname(os.path.abspath(__file__))
                if _here not in sys.path:
                    sys.path.insert(0, _here)
                from faro_qa_watchdog import _insert_alert
                _insert_alert(
                    nivel="WARNING",
                    modulo="compactacion_ml",
                    mensaje=(
                        f"Compactación del suelo alta: índice={score:.0f}/100 > 70 "
                        f"(método: {metodo}) — considerar aireación mecánica"
                    ),
                    venue_id=venue_id,
                )
            except Exception:
                pass

    except Exception as exc:
        log.warning("compactacion_cycle (non-fatal): %s", exc)
        out["error"] = str(exc)
    return out


def _xgb_available() -> bool:
    try:
        import xgboost  # noqa
        return True
    except ImportError:
        return False
