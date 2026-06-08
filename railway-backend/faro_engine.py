"""
faro_engine.py — Ground Truth Field Photo Analysis (CLAHE + DGCI).
Flask Blueprint — sin persistencia en disco, todo en memoria.
"""
from __future__ import annotations
import gc, os
from flask import Blueprint, request, jsonify

engine_bp = Blueprint("engine", __name__)

_SUPABASE_URL = os.environ.get("SUPABASE_URL", "https://xljxpzudgwhbzcnrvylo.supabase.co")
_SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "")


def _sb_insert_photo(row: dict) -> bool:
    try:
        import requests as _rq
        r = _rq.post(
            f"{_SUPABASE_URL}/rest/v1/field_photos",
            headers={
                "apikey": _SUPABASE_KEY,
                "Authorization": f"Bearer {_SUPABASE_KEY}",
                "Content-Type": "application/json",
                "Prefer": "return=minimal",
            },
            json=row,
            timeout=8,
        )
        return r.status_code in (200, 201)
    except Exception:
        return False


@engine_bp.post("/api/v1/field-analysis/upload-photo")
def upload_field_photo():
    try:
        import numpy as np
        import cv2
    except ImportError as err:
        return jsonify({"error": f"cv2 not available: {err}"}), 503

    venue_id = request.form.get("venue_id", "").strip()
    field_id = request.form.get("field_id", "").strip()
    if not venue_id or not field_id:
        return jsonify({"error": "venue_id and field_id required"}), 400

    f = request.files.get("file")
    if not f:
        return jsonify({"error": "file required"}), 400

    try:
        buf      = np.frombuffer(f.read(), dtype=np.uint8)
        img_raw  = cv2.imdecode(buf, cv2.IMREAD_COLOR)
        if img_raw is None:
            return jsonify({"error": "Invalid image binary"}), 400

        h0, w0 = img_raw.shape[:2]
        if max(h0, w0) > 1024:
            scale = 1024.0 / max(h0, w0)
            img = cv2.resize(img_raw,
                             (int(w0 * scale), int(h0 * scale)),
                             interpolation=cv2.INTER_AREA)
        else:
            img = img_raw.copy()

        hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
        h_ch, s_ch, v_ch = cv2.split(hsv)
        clahe   = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8, 8))
        v_eq    = clahe.apply(v_ch)
        hsv_cor = cv2.merge([h_ch, s_ch, v_eq])

        mask = cv2.inRange(hsv_cor,
                           np.array([35, 40, 40]),
                           np.array([85, 255, 255]))

        total_px = img.shape[0] * img.shape[1]
        green_px = int(cv2.countNonZero(mask))
        cobertura = (green_px / total_px) * 100.0

        if green_px > 0:
            mv    = cv2.mean(hsv_cor, mask=mask)
            h_n   = mv[0] / 180.0
            s_n   = mv[1] / 255.0
            v_n   = mv[2] / 255.0
            dgci_idx  = (h_n - 60.0 / 180.0) / (60.0 / 180.0) + (1.0 - s_n) + (1.0 - v_n)
            dgci_real = float(np.clip((dgci_idx + 1.0) / 3.0, 0.0, 1.0))
        else:
            dgci_real = 0.0

        override = bool(cobertura > 85.0 and dgci_real > 0.58)

        _sb_insert_photo({
            "venue_id":           venue_id,
            "field_id":           field_id,
            "cobertura_foliar_pct": round(cobertura, 2),
            "dgci_real":          round(dgci_real, 4),
            "override_active":    override,
        })

        del img_raw, img, hsv, mask, h_ch, s_ch, v_ch, v_eq, hsv_cor
        gc.collect()

        return jsonify({
            "status":               "success",
            "cobertura_foliar_pct": round(cobertura, 2),
            "dgci_real":            round(dgci_real, 4),
            "override_active":      override,
            "metrics":              {"pixels_processed": total_px},
        })

    except Exception as exc:
        return jsonify({"error": f"Faro Engine error: {exc}"}), 500
