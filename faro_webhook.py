# FARO PROTOCOL -- Servidor webhook Flask
# Endpoints:
#   POST /webhooks/lemon  -> pagos Lemon Squeezy (HMAC-SHA256)
#   POST /api/zone/new    -> cliente dibuja zona, pipeline arranca
#   GET  /health          -> liveness check

import hashlib
import hmac
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent / ".env")
except ImportError:
    pass

from flask import Flask, request, jsonify

_ENGINE_DIR = Path(__file__).parent / "engine"
if _ENGINE_DIR.exists() and str(_ENGINE_DIR) not in sys.path:
    sys.path.insert(0, str(_ENGINE_DIR))

try:
    import firebase_admin
    from firebase_admin import credentials, auth as fb_auth, firestore as fb_fs
    _SA_PATH = os.getenv("FIREBASE_SERVICE_ACCOUNT", "")
    if not firebase_admin._apps:
        if _SA_PATH and Path(_SA_PATH).exists():
            firebase_admin.initialize_app(credentials.Certificate(_SA_PATH))
        else:
            print("[WARN] FIREBASE_SERVICE_ACCOUNT no configurado")
    _FB_OK = bool(firebase_admin._apps)
except Exception as _e:
    print(f"[WARN] Firebase Admin no disponible: {_e}")
    _FB_OK = False

PLAN_MAX_ASSETS = {"observer": 1, "analyst": 3, "sovereign": 999, "enterprise": 999}
PROJECT_ROOT = Path(__file__).parent
app = Flask(__name__)


def _cors():
    return {
        "Access-Control-Allow-Origin":  "*",
        "Access-Control-Allow-Methods": "POST, OPTIONS",
        "Access-Control-Allow-Headers": "Content-Type, Authorization",
    }


def _verify_lemon_hmac(raw: bytes, sig: str) -> bool:
    secret = os.getenv("LEMON_SQUEEZY_SECRET", "")
    if not secret:
        print("[WARN] LEMON_SQUEEZY_SECRET no configurado -- HMAC omitido")
        return True
    expected = hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, sig or "")


def _verify_token(id_token: str):
    if not _FB_OK:
        return {"uid": "dev-uid", "email": "dev@localhost"}
    try:
        return fb_auth.verify_id_token(id_token)
    except Exception as e:
        print(f"[Auth] Token invalido: {e}")
        return None


def _get_profile(uid: str):
    if not _FB_OK:
        return None
    try:
        snap = fb_fs.client().collection("clients").doc(uid).get()
        return snap.data() if snap.exists else None
    except Exception as e:
        print(f"[Firestore] Error: {e}")
        return None


def _update_zone(uid: str, slug: str, data: dict):
    if not _FB_OK:
        return
    try:
        fb_fs.client().collection("clients").doc(uid).update({f"zones.{slug}": data})
    except Exception as e:
        print(f"[Firestore] Error zona: {e}")


def _create_client(uid: str, email: str, plan: str, name: str = ""):
    if not _FB_OK:
        return
    try:
        max_a = PLAN_MAX_ASSETS.get(plan.lower(), 1)
        fb_fs.client().collection("clients").doc(uid).set({
            "email": email, "name": name, "plan": plan.lower(),
            "max_assets": max_a, "zones": {},
            "active_subscription": True, "status": "active",
            "source": "lemon_squeezy",
            "createdAt": datetime.now(timezone.utc).isoformat(),
        }, merge=True)
        print(f"  [Firestore] {email} creado plan={plan} max_assets={max_a}")
    except Exception as e:
        print(f"[Firestore] Error creando cliente: {e}")


def _write_area(slug: str, name: str, vertical: str,
                bounds: list, center: list, polygon_wkt: str):
    d = PROJECT_ROOT / "faro_areas"
    d.mkdir(exist_ok=True)
    data = {
        "name": slug, "label": name, "vertical": vertical,
        "bounds": bounds, "center": center, "polygon": polygon_wkt,
        "report_png": f"faro_reporte_fusion_{slug}.png",
        "sar_output": f"sar_{slug}_georef.tif",
        "ndvi_tif":   f"FaroProtocol_NDVI_{slug}.tif",
        "createdAt":  datetime.now(timezone.utc).isoformat(),
    }
    (d / f"{slug}.json").write_text(
        json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(f"  [Areas] {slug}.json creado")


def _launch(slug: str, uid: str = None):
    cmd = [sys.executable, str(PROJECT_ROOT / "faro_pipeline.py"), "--area", slug]
    if uid:
        cmd += ["--client-uid", uid]
    try:
        subprocess.Popen(cmd, cwd=str(PROJECT_ROOT),
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        print(f"  [Pipeline] Lanzado {slug} background")
    except Exception as e:
        print(f"  [Pipeline] Error: {e}")


def _wa_zone(name: str, vertical: str, email: str):
    try:
        from faro_notifier import send_whatsapp
        send_whatsapp(
            f"FARO - Nueva zona\nZona: {name}\nVertical: {vertical}\n"
            f"Cliente: {email}\nETA: ~24 horas"
        )
    except Exception as e:
        print(f"  [Notifier] WhatsApp omitido: {e}")


def _wa_payment(email: str, plan: str):
    try:
        from faro_notifier import notify_payment_confirmed
        notify_payment_confirmed(email=email, plan=plan, areas=[])
    except Exception as e:
        print(f"  [Notifier] WhatsApp pago omitido: {e}")


# ---- /health -----------------------------------------------------------------

@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok", "firebase": _FB_OK}), 200


# ---- CORS -------------------------------------------------------------------

@app.before_request
def preflight():
    if request.method == "OPTIONS":
        r = app.make_response("")
        r.headers.update(_cors())
        return r, 204


@app.after_request
def cors_hdr(resp):
    resp.headers.update(_cors())
    return resp


# ---- /webhooks/lemon --------------------------------------------------------

@app.route("/webhooks/lemon", methods=["POST"])
def lemon_webhook():
    raw = request.get_data()
    if not _verify_lemon_hmac(raw, request.headers.get("X-Signature", "")):
        return jsonify({"error": "invalid signature"}), 401
    try:
        payload = json.loads(raw)
    except Exception:
        return jsonify({"error": "invalid json"}), 400

    event      = payload.get("meta", {}).get("event_name", "")
    attrs      = payload.get("data", {}).get("attributes", {})
    customer   = attrs.get("customer_email", "") or attrs.get("user_email", "")
    first_name = attrs.get("user_name", "") or attrs.get("customer_name", "")
    variant    = attrs.get("variant_name", "").lower()
    plan_map   = {"observer": "observer", "analyst": "analyst",
                  "sovereign": "sovereign", "enterprise": "enterprise"}
    plan = next((v for k, v in plan_map.items() if k in variant), "observer")
    print(f"[Lemon] {event} customer={customer} variant={variant}")

    if event in ("subscription_created", "order_created"):
        if not customer:
            return jsonify({"error": "missing email"}), 400
        if _FB_OK:
            try:
                try:
                    user = fb_auth.get_user_by_email(customer)
                except fb_auth.UserNotFoundError:
                    user = fb_auth.create_user(email=customer, display_name=first_name)
                _create_client(user.uid, customer, plan, first_name)
                _wa_payment(customer, plan)
            except Exception as e:
                print(f"[Lemon] Error: {e}")
                return jsonify({"error": str(e)}), 500
        return jsonify({"ok": True, "plan": plan}), 200

    if event in ("subscription_cancelled", "subscription_expired"):
        if customer and _FB_OK:
            try:
                user = fb_auth.get_user_by_email(customer)
                fb_fs.client().collection("clients").doc(user.uid).update(
                    {"active_subscription": False, "status": "cancelled"}
                )
            except Exception as e:
                print(f"[Lemon] Cancelacion: {e}")
        return jsonify({"ok": True}), 200

    return jsonify({"ok": True, "event": event}), 200


# ---- /api/zone/new ----------------------------------------------------------

@app.route("/api/zone/new", methods=["POST", "OPTIONS"])
def zone_new():
    hdr = request.headers.get("Authorization", "")
    if not hdr.startswith("Bearer "):
        return jsonify({"error": "missing token"}), 401
    decoded = _verify_token(hdr[7:])
    if not decoded:
        return jsonify({"error": "invalid token"}), 401

    uid   = decoded["uid"]
    email = decoded.get("email", "")
    body  = request.get_json(silent=True) or {}
    name        = (body.get("name") or "").strip()
    vertical    = (body.get("vertical") or "general").strip().lower()
    polygon_wkt = body.get("polygon_wkt", "")
    bounds      = body.get("bounds", [])
    center      = body.get("center", [])

    if not name:
        return jsonify({"error": "missing name"}), 400
    if not polygon_wkt:
        return jsonify({"error": "missing polygon_wkt"}), 400

    slug = re.sub(r"[^a-z0-9]+", "_", name.lower())[:32].strip("_")
    if not slug:
        slug = f"zona_{int(datetime.now().timestamp())}"

    profile = _get_profile(uid)
    if profile:
        if not (profile.get("active_subscription") or profile.get("status") == "active"):
            return jsonify({"error": "subscription_inactive"}), 403
        max_a  = profile.get("max_assets", 1)
        n_used = len(profile.get("zones", {}))
        if max_a < 999 and n_used >= max_a:
            return jsonify({
                "error": "quota_exceeded", "current": n_used,
                "max": max_a, "plan": profile.get("plan", "observer"),
            }), 403

    _write_area(slug, name, vertical, bounds, center, polygon_wkt)
    _update_zone(uid, slug, {
        "name": name, "vertical": vertical, "bounds": bounds, "center": center,
        "status": "processing", "createdAt": datetime.now(timezone.utc).isoformat(),
    })
    _launch(slug, uid)
    _wa_zone(name, vertical, email)

    return jsonify({"zone_id": slug, "eta_hours": 24}), 200


# -----------------------------------------------------------------------------
if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    print(f"[Faro Webhook] Puerto {port}")
    app.run(host="0.0.0.0", port=port, debug=False)
