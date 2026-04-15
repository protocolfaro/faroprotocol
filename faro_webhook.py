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
import smtplib
import subprocess
import sys
import threading
from datetime import datetime, timezone
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
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
    _SA_RAW = os.getenv("FIREBASE_SERVICE_ACCOUNT", "")
    if not firebase_admin._apps and _SA_RAW:
        # Acepta JSON string (Railway/Netlify) o path de archivo (local dev)
        if _SA_RAW.strip().startswith("{"):
            _sa_dict = json.loads(_SA_RAW)
            firebase_admin.initialize_app(credentials.Certificate(_sa_dict))
        elif Path(_SA_RAW).exists():
            firebase_admin.initialize_app(credentials.Certificate(_SA_RAW))
        else:
            print("[WARN] FIREBASE_SERVICE_ACCOUNT: no es JSON ni path válido")
    elif not firebase_admin._apps:
        print("[WARN] FIREBASE_SERVICE_ACCOUNT no configurado")
    _FB_OK = bool(firebase_admin._apps)
except Exception as _e:
    print(f"[WARN] Firebase Admin no disponible: {_e}")
    _FB_OK = False

PLAN_MAX_ASSETS = {"observer": 1, "analyst": 3, "sovereign": 999, "enterprise": 999}
PROJECT_ROOT = Path(__file__).parent
app = Flask(__name__)


_ALLOWED_ORIGINS = {
    "https://faro-protocol.netlify.app",
    "https://faroprotocol.com",
    "https://www.faroprotocol.com",
}


def _cors():
    origin = request.headers.get("Origin", "")
    allowed = origin if origin in _ALLOWED_ORIGINS else "https://faro-protocol.netlify.app"
    return {
        "Access-Control-Allow-Origin":  allowed,
        "Access-Control-Allow-Methods": "POST, OPTIONS",
        "Access-Control-Allow-Headers": "Content-Type, Authorization",
        "Vary": "Origin",
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


def _gen_manual_pdf(email: str, name: str, plan: str) -> Path | None:
    """Genera PDF de bienvenida personalizado con faro_manual_cliente.py."""
    script = PROJECT_ROOT / "faro_manual_cliente.py"
    if not script.exists():
        print("  [Manual] faro_manual_cliente.py no encontrado")
        return None
    slug = re.sub(r"[^a-z0-9]+", "_", (name or email).lower())[:20].strip("_") or "cliente"
    cmd  = [sys.executable, str(script),
            "--email", email, "--name", name or email, "--areas", "demo"]
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, encoding="utf-8",
            errors="replace", cwd=str(PROJECT_ROOT), timeout=60,
        )
        if result.returncode == 0:
            # Buscar PDF generado más reciente
            candidates = sorted(
                PROJECT_ROOT.glob("faro_manual_*.pdf"),
                key=lambda p: p.stat().st_mtime, reverse=True,
            )
            if candidates:
                print(f"  [Manual] PDF generado: {candidates[0].name}")
                return candidates[0]
        else:
            print(f"  [Manual] PDF error: {result.stderr[-200:]}")
    except subprocess.TimeoutExpired:
        print("  [Manual] PDF timeout (>60s)")
    except Exception as e:
        print(f"  [Manual] PDF excepción: {e}")
    return None


def _send_welcome_email(email: str, name: str, plan: str):
    """Envía email de bienvenida con PDF adjunto (o demo PDF si falla generación)."""
    gmail_user = os.getenv("GMAIL_USER", "")
    gmail_pass = os.getenv("GMAIL_APP_PASS", "")
    if not gmail_user or not gmail_pass:
        print("  [Email] GMAIL no configurado — bienvenida omitida")
        return

    portal_url = os.getenv("PORTAL_URL", "https://faro-protocol.netlify.app")
    plan_labels = {
        "observer":   "Observer · 1 zona",
        "analyst":    "Analyst · 3 zonas",
        "sovereign":  "Sovereign · Ilimitadas",
        "enterprise": "Enterprise",
    }
    plan_label = plan_labels.get(plan.lower(), plan.capitalize())

    # Intentar generar PDF personalizado; fallback a demo
    pdf_path = _gen_manual_pdf(email, name, plan)
    if not pdf_path:
        fallback = PROJECT_ROOT / "faro_manual_demo.pdf"
        pdf_path = fallback if fallback.exists() else None

    asunto = f"Bienvenido a FARO PROTOCOL — {plan_label}"
    cuerpo = f"""FARO PROTOCOL — Satellite Intelligence
{"=" * 60}

Hola {name or email},

Tu suscripcion {plan_label} fue activada correctamente.

Portal de acceso: {portal_url}/portal

{"=" * 60}
PRIMEROS PASOS
{"=" * 60}

1. Ingresa al portal con tu email: {email}
2. Dibuja tu primera zona en el mapa 3D global
3. El pipeline Sentinel-1/2 procesara tu zona en ~24 horas
4. Recibiras una notificacion cuando los datos esten listos

{"=" * 60}
TU PLAN: {plan_label}
{"=" * 60}

{("1 zona disponible" if plan.lower() == "observer" else
  "3 zonas disponibles" if plan.lower() == "analyst" else
  "Zonas ilimitadas")}

El manual de uso esta adjunto en este email.
Para soporte: protocolfaro@gmail.com

FARO PROTOCOL
{portal_url}
"""
    msg = MIMEMultipart()
    msg["From"]    = gmail_user
    msg["To"]      = email
    msg["Subject"] = asunto
    msg.attach(MIMEText(cuerpo, "plain", "utf-8"))

    if pdf_path and pdf_path.exists():
        try:
            with open(pdf_path, "rb") as f:
                adj = MIMEApplication(f.read(), _subtype="pdf")
                adj.add_header("Content-Disposition", "attachment",
                               filename="faro_manual.pdf")
                msg.attach(adj)
            print(f"  [Email] PDF adjunto: {pdf_path.name}")
        except Exception as e:
            print(f"  [Email] Error adjuntando PDF: {e}")

    try:
        with smtplib.SMTP("smtp.gmail.com", 587) as server:
            server.ehlo()
            server.starttls()
            server.login(gmail_user, gmail_pass)
            server.sendmail(gmail_user, email, msg.as_string())
        print(f"  [Email] Bienvenida enviada a {email}")
    except smtplib.SMTPAuthenticationError:
        print("  [Email] Auth Gmail fallida — verificar GMAIL_APP_PASS")
    except Exception as e:
        print(f"  [Email] Error SMTP: {e}")


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
        # Email de bienvenida + PDF en background (no bloquea el webhook)
        threading.Thread(
            target=_send_welcome_email,
            args=(customer, first_name, plan),
            daemon=True,
        ).start()
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


# ---- /api/zone/trigger -------------------------------------------------------
# Dispara el pipeline inmediatamente para una zona ya existente en Firestore.
# Útil cuando el admin agrega una zona manualmente sin pasar por /api/zone/new.

@app.route("/api/zone/trigger", methods=["POST", "OPTIONS"])
def zone_trigger():
    hdr = request.headers.get("Authorization", "")
    if not hdr.startswith("Bearer "):
        return jsonify({"error": "missing token"}), 401
    decoded = _verify_token(hdr[7:])
    if not decoded:
        return jsonify({"error": "invalid token"}), 401

    uid  = decoded["uid"]
    body = request.get_json(silent=True) or {}
    slug = (body.get("zone_id") or "").strip()

    if not slug:
        return jsonify({"error": "missing zone_id"}), 400

    profile = _get_profile(uid)
    if profile:
        if not (profile.get("active_subscription") or profile.get("status") == "active"):
            return jsonify({"error": "subscription_inactive"}), 403
        if slug not in profile.get("zones", {}):
            return jsonify({"error": "zone_not_found"}), 404

    # Marcar como processing y lanzar pipeline
    _update_zone(uid, slug, {
        "status": "processing",
        "triggered_at": datetime.now(timezone.utc).isoformat(),
    })
    _launch(slug, uid)
    print(f"  [Trigger] Pipeline disparado para {slug} (uid={uid})")
    return jsonify({"ok": True, "zone_id": slug, "eta_hours": 24}), 200


# ---- /api/chat ---------------------------------------------------------------
# Chat con Hermes — responde con datos reales del área activa.

@app.route("/api/chat", methods=["POST", "OPTIONS"])
def chat():
    body    = request.get_json(silent=True) or {}
    message = (body.get("message") or "").strip()
    area    = (body.get("area") or "").strip()

    if not message:
        return jsonify({"error": "missing message"}), 400

    # Auth: requerida en prod, omitida en dev (Firebase no disponible)
    if _FB_OK:
        hdr = request.headers.get("Authorization", "")
        if not hdr.startswith("Bearer "):
            return jsonify({"error": "missing token"}), 401
        if not _verify_token(hdr[7:]):
            return jsonify({"error": "invalid token"}), 401

    # Contexto del área actual desde data.json
    area_context = ""
    if area:
        try:
            dj = json.loads((PROJECT_ROOT / "data.json").read_text(encoding="utf-8"))
            pd = dj.get("pipeline", {}).get(area, {})
            if pd:
                area_context = (
                    f"\nContexto zona '{area}':\n"
                    f"  Score Faro    : {pd.get('score_faro', 'N/A')}\n"
                    f"  NDVI medio    : {pd.get('ndvi_medio', 'N/A')}\n"
                    f"  SAR (dB)      : {pd.get('sar_medio_db', 'N/A')}\n"
                    f"  Índice Fusión : {pd.get('indice_fusion_medio', 'N/A')}\n"
                    f"  Rinde est.    : {pd.get('rinde_estimado_tha', 'N/A')} t/ha\n"
                    f"  Estado        : {pd.get('estado', 'N/A')}\n"
                    f"  Último run    : {pd.get('ultimo_run', 'N/A')}\n"
                )
        except Exception:
            pass

    groq_key = os.getenv("GROQ_API_KEY", "")
    if not groq_key:
        # Respuesta local sin LLM
        reply = (
            f"Hermes sin LLM (GROQ_API_KEY no configurado).\n"
            + (f"Zona activa: {area}.{area_context}" if area_context else "")
        )
        return jsonify({"reply": reply}), 200

    try:
        from groq import Groq
        client = Groq(api_key=groq_key)

        system_prompt = (
            "Sos Hermes, el agente de inteligencia satelital de FARO PROTOCOL. "
            "Tenés acceso a datos reales de Sentinel-1 SAR y Sentinel-2 NDVI. "
            "Respondés en español, de forma concisa y técnica (máximo 3 párrafos). "
            "Si el score es bajo (<45) alertás claramente. "
            "No usés markdown excepto negritas."
            + area_context
        )

        resp = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            max_tokens=512,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user",   "content": message},
            ],
        )
        return jsonify({"reply": resp.choices[0].message.content}), 200

    except Exception as e:
        print(f"[Chat] Error Groq: {e}")
        return jsonify({"error": str(e)}), 500


# -----------------------------------------------------------------------------
if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    print(f"[Faro Webhook] Puerto {port}")
    app.run(host="0.0.0.0", port=port, debug=False)
