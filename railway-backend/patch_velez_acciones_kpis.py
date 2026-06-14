# patch_velez_acciones_kpis.py
# Parche inmediato: roger.acciones + roger.kpis + fusion scores desde datos reales.
# Uso: python patch_velez_acciones_kpis.py
from __future__ import annotations
import base64, json, math, os, sys
from datetime import date, datetime
from pathlib import Path

import requests

TOKEN  = os.environ.get("GITHUB_TOKEN", "")
OWNER  = "protocolfaro"
REPO   = "faroprotocol"
BRANCH = "main"
PATH   = "velez/velez_data.json"
API    = "https://api.github.com"

sys.path.insert(0, str(Path(__file__).resolve().parent / "sports" / "clients" / "velez"))
import acciones_engine as ae

# ── Exact same score formula as github_push.py ────────────────────────────────
_WINTER = frozenset((6, 7, 8))

def _ndvi_norm(ndvi, month):
    ref = 0.55 if month in _WINTER else 0.75
    return min(100, round((ndvi / ref) * 100))

def _ipos_health(ipos_score, sem):
    if sem == "verde":
        return max(80, round(100 - ipos_score * 0.22))
    if sem == "amarillo":
        return max(55, round(80 - (ipos_score - 90) * 0.25))
    return max(10, round(55 - (ipos_score - 200) * 0.14))

def _combined(ndvi, ipos_score, ipos_sem, month):
    nn   = _ndvi_norm(ndvi, month)
    inv  = _ipos_health(ipos_score, ipos_sem)
    c    = round(nn * 0.60 + inv * 0.40)
    sem  = "verde" if c >= 70 else "amarillo" if c >= 50 else "rojo"
    return c, sem

# ── GitHub helpers ─────────────────────────────────────────────────────────────

def _hdrs():
    return {"Authorization": f"Bearer {TOKEN}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28"}

def _get():
    r = requests.get(f"{API}/repos/{OWNER}/{REPO}/contents/{PATH}",
                     headers=_hdrs(), params={"ref": BRANCH}, timeout=15)
    r.raise_for_status()
    d = r.json()
    return d["sha"], json.loads(base64.b64decode(d["content"]).decode())

def _put(sha, vd, msg):
    payload = {
        "message": msg,
        "content": base64.b64encode(
            json.dumps(vd, ensure_ascii=False, separators=(",", ":")).encode()
        ).decode(),
        "branch": BRANCH,
        "sha": sha,
    }
    r = requests.put(f"{API}/repos/{OWNER}/{REPO}/contents/{PATH}",
                     headers=_hdrs(), json=payload, timeout=35)
    r.raise_for_status()
    return r.json().get("commit", {}).get("html_url", "?")

# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    print("Fetching velez_data.json...")
    if not TOKEN:
        raise EnvironmentError("GITHUB_TOKEN env var no configurada — export GITHUB_TOKEN=tu_token")
    sha, vd = _get()

    heatmaps  = vd.get("usuarios", {}).get("roger", {}).get("heatmaps", {})
    canchas   = vd.get("sectores", {}).get("canchero", {}).get("canchas", [])
    weather   = vd.get("weather_live", {})
    month     = date.today().month

    # Physics prescription from existing acciones
    existing_acc = vd.get("usuarios", {}).get("roger", {}).get("acciones", [])
    physics_acc  = [a for a in existing_acc if a.startswith("Ventana de corte:")]

    # ── 1. Recalculate fusion scores first (KPIs read scores from canchas list) ─
    print(f"\nRecalculando scores (mes={month}, winter={month in _WINTER}):")
    for cancha in canchas:
        cid  = cancha.get("id", "")
        hm   = heatmaps.get(cid, {})
        ndvi = hm.get("ndvi") or cancha.get("ndvi")
        if not ndvi:
            continue
        ipos_score = hm.get("ipos", 0) or 0
        ipos_sem   = hm.get("semaforo", "verde")
        old_score  = cancha.get("score", 0)
        old_sem    = cancha.get("sem", "?")
        new_score, new_sem = _combined(ndvi, ipos_score, ipos_sem, month)
        cancha["score_prev"] = old_score
        cancha["score"]      = new_score
        cancha["sem"]        = new_sem
        change = "  " if (old_sem == new_sem) else f"({old_sem}->{new_sem})"
        print(f"  {cid:6s} ndvi={ndvi:.3f} ipos={ipos_score:6.0f} {ipos_sem:8s} "
              f"score {old_score:3d}->{new_score:3d} {new_sem:8s} {change}")

    # ── 2. Dynamic acciones ────────────────────────────────────────────────────
    new_acciones = ae.generate_acciones(
        weather_live=weather,
        heatmaps=heatmaps,
        physics_acciones=physics_acc,
        month=month,
    )
    print(f"\nAcciones ({len(new_acciones)}):")
    for a in new_acciones:
        print(f"  {a[:90]}")

    # ── 3. Dynamic KPIs (after score update — Score VO uses new scores) ───────
    new_kpis = ae.generate_kpis(
        heatmaps=heatmaps,
        canchas=canchas,
        weather_live=weather,
        month=month,
    )
    print(f"\nKPIs ({len(new_kpis)}):")
    for k in new_kpis:
        print(f"  {k['label']}: {k['value']}{k['unit']} ({k['sem']}) — {k['sub'][:60]}")

    # ── Patch vd ──────────────────────────────────────────────────────────────
    roger = vd.setdefault("usuarios", {}).setdefault("roger", {})
    roger["acciones"] = new_acciones
    roger["kpis"]     = new_kpis
    vd["updated_at"]  = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")

    ts  = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    msg = f"patch: acciones+kpis+scores invierno [{ts}]"
    url = _put(sha, vd, msg)
    print(f"\nOK Commit: {url}")

if __name__ == "__main__":
    main()
