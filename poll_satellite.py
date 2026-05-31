import requests, time, json
BASE = "https://faroprotocol-production-45fd.up.railway.app"

# Fire
r = requests.post(f"{BASE}/velez/satellite_force", json={"pin": "1234"}, timeout=10)
print("POST:", r.status_code, r.json())

# Poll for up to 2 minutes
for i in range(24):
    time.sleep(5)
    s = requests.get(f"{BASE}/velez/refresh_status", timeout=10).json().get("satellite", {})
    running = s.get("running")
    last = s.get("last", {})
    print(f"  poll {i+1}: running={running} last_keys={list(last.keys())}")
    if last:
        print("  RESULT:", json.dumps(last, indent=2))
        break
else:
    print("TIMEOUT — no result after 2 min")
