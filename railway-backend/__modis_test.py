import sys, json, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, "events/clients/dale-play")

from dale_play_modis import fetch_modis_ndvi

print("=== fetch_modis_ndvi — show_date=2026-06-01, days_back=7 ===")
r = fetch_modis_ndvi(show_date="2026-06-01", days_back=7)
print(json.dumps(r, indent=2, ensure_ascii=False))
