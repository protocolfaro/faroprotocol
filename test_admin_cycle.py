"""
test_admin_cycle.py — End-to-end test: admin panel → Railway → GitHub
Simulates what the admin panel does when Roger submits Semana D.
"""
import json, hashlib, sys
import urllib.request, urllib.error

RAILWAY_URL = "https://velez-ipos-production.up.railway.app"
PIN = "1234"

# Semana D — minimal real sessions
PAYLOAD = {
    "pin": PIN,
    "semana": {
        "label": "D",
        "fecha_inicio": "2026-05-25",
        "fecha_fin": "2026-05-30",
    },
    "sessions": [
        {"categoria": "Reserva",   "dia": "lun", "canchas": ["1fa"],        "turno": "manana"},
        {"categoria": "Cuarta",    "dia": "lun", "canchas": ["8fa", "9fa"], "turno": "manana"},
        {"categoria": "Quinta",    "dia": "lun", "canchas": ["5fa"],        "turno": "infantiles"},
        {"categoria": "Sexta",     "dia": "mar", "canchas": ["1fa"],        "turno": "manana"},
        {"categoria": "Séptima",   "dia": "mar", "canchas": ["3fa"],        "turno": "manana"},
        {"categoria": "Femenino",  "dia": "mie", "canchas": ["1fa"],        "turno": "femenino"},
        {"categoria": "Infantiles","dia": "mie", "canchas": ["8fa"],        "turno": "infantiles"},
        {"categoria": "Reserva",   "dia": "jue", "canchas": ["1fa"],        "turno": "manana"},
        {"categoria": "Primera",   "dia": "vie", "canchas": ["1fp"],        "turno": "manana"},
    ],
}

print(f"Testing POST {RAILWAY_URL}/velez/horarios")
print(f"  semana: {PAYLOAD['semana']['label']} | sessions: {len(PAYLOAD['sessions'])}")
print()

body = json.dumps(PAYLOAD).encode()
req = urllib.request.Request(
    f"{RAILWAY_URL}/velez/horarios",
    data=body,
    headers={"Content-Type": "application/json"},
    method="POST",
)

try:
    with urllib.request.urlopen(req, timeout=120) as r:
        resp = json.loads(r.read())
except urllib.error.HTTPError as e:
    raw = e.read()
    print(f"HTTP {e.code}: {raw[:500]}")
    sys.exit(1)

status = resp.get("status")
print(f"Status: {status}")
print()

if status != "ok":
    print("ERROR:", resp.get("error"))
    sys.exit(1)

# IPOS results
ipos = resp.get("ipos", {})
print("IPOS results:")
for cid, data in sorted(ipos.items()):
    print(f"  {cid.upper():5s}: {data['score']:6.1f}  {data['icono']} {data['semaforo']}")

# Heatmap URLs
hm = resp.get("heatmaps", {})
ok_hm  = sum(1 for v in hm.values() if v and not v.startswith("ERROR"))
err_hm = sum(1 for v in hm.values() if v and v.startswith("ERROR"))
print(f"\nHeatmaps pushed: {ok_hm}/{len(hm)} OK, {err_hm} errors")
if err_hm:
    for k,v in hm.items():
        if v.startswith("ERROR"):
            print(f"  {k}: {v[:120]}")

# Config commit
cfg = resp.get("config_commit", "")
print(f"\nconfig_velez.json commit: {cfg[:80] if cfg else '(not pushed)'}")
print(f"\nGenerado en: {resp.get('generado_en','?')}")

if ok_hm == len(hm) and cfg and not cfg.startswith("ERROR"):
    print("\n✅ CYCLE COMPLETE — admin panel → Railway → GitHub working end-to-end")
    sys.exit(0)
else:
    print("\n⚠️  PARTIAL — check errors above")
    sys.exit(1)
