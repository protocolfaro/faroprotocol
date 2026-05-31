import os, json, sys
print("--- TEST FIREBASE ---", flush=True)
print(f"SA presente: {bool(os.getenv('FIREBASE_SERVICE_ACCOUNT'))}", flush=True)
try:
    import firebase_admin
    from firebase_admin import credentials
    print(f"firebase_admin version: {firebase_admin.__version__}", flush=True)
    print(f"_apps antes: {firebase_admin._apps}", flush=True)
    raw = os.getenv("FIREBASE_SERVICE_ACCOUNT", "")
    d = json.loads(raw)
    firebase_admin.initialize_app(credentials.Certificate(d))
    print(f"_apps despues: {firebase_admin._apps}", flush=True)
    print(f"_FB_OK seria: {bool(firebase_admin._apps)}", flush=True)
except Exception as e:
    print(f"ERROR: {type(e).__name__}: {e}", flush=True)
