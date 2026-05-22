"""
github_push.py — Push files to GitHub via Contents API
Faro Protocol · Vélez Sarsfield
"""
from __future__ import annotations
import base64, json, logging, os, time
from datetime import datetime, timezone
import requests

log = logging.getLogger(__name__)
API      = "https://api.github.com"
OWNER    = "protocolfaro"
REPO     = "faroprotocol"
BRANCH   = "main"
CFG_PATH = "velez/config_velez.json"
VD_PATH  = "velez/velez_data.json"
HM_DIR   = "velez/heatmaps"

def _token():
    t = os.environ.get("GITHUB_TOKEN","")
    if not t: raise EnvironmentError("GITHUB_TOKEN not set")
    return t

def _hdrs():
    return {"Authorization":f"Bearer {_token()}",
            "Accept":"application/vnd.github+json",
            "X-GitHub-Api-Version":"2022-11-28"}

def _sha(path):
    r = requests.get(f"{API}/repos/{OWNER}/{REPO}/contents/{path}",
                     headers=_hdrs(), params={"ref":BRANCH}, timeout=15)
    return r.json().get("sha") if r.status_code == 200 else None

def _put(path, content_bytes, msg, sha=None, retries=3):
    url = f"{API}/repos/{OWNER}/{REPO}/contents/{path}"
    payload = {"message":msg,
               "content":base64.b64encode(content_bytes).decode(),
               "branch":BRANCH}
    if sha: payload["sha"] = sha
    for i in range(1, retries+1):
        try:
            r = requests.put(url, headers=_hdrs(), json=payload, timeout=35)
            if r.status_code in (200,201): return r.json()
            if r.status_code == 409 and i < retries:
                time.sleep(1)
                new_sha = _sha(path)
                if new_sha: payload["sha"] = new_sha
                continue
            r.raise_for_status()
        except requests.RequestException as e:
            if i == retries: raise
            log.warning(f"retry {i}: {e}")
            time.sleep(2)
    raise RuntimeError(f"Failed pushing {path}")

def push_heatmaps(png_bytes: dict, semana_label: str, ipos: dict) -> dict:
    urls = {}
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    for cid, data in png_bytes.items():
        path = f"{HM_DIR}/heatmap_{cid}.png"
        existing = _sha(path)
        score = ipos.get(cid, {}).get("score", 0)
        msg = f"heatmap {cid.upper()} sem{semana_label} IPOS={score:.0f} [{ts}]"
        log.info(f"Pushing {path} ({len(data)//1024}KB)")
        resp = _put(path, data, msg, existing)
        urls[cid] = (resp.get("content",{}).get("html_url") or
                     f"https://github.com/{OWNER}/{REPO}/blob/{BRANCH}/{path}")
    return urls

def _ipos_to_health(ipos_score: float, semaforo: str) -> int:
    """Convert IPOS usage load → 0-100 health score (inverse: higher load = lower health)."""
    if semaforo == "verde":
        return max(80, round(100 - ipos_score * 0.22))
    elif semaforo == "amarillo":
        return max(55, round(80 - (ipos_score - 90) * 0.25))
    return max(10, round(55 - (ipos_score - 200) * 0.14))


def _shadow_penalty(sombra_permanente_pct: float) -> float:
    """Multiplicador por sombra permanente: menos sol → pasto más débil."""
    if sombra_permanente_pct >= 30: return 0.85
    if sombra_permanente_pct >= 15: return 0.92
    return 1.0


def _combined_score(ndvi: float, ipos_score: float, ipos_sem: str) -> tuple:
    """Score fusión: NDVI Planetary Computer 60% + IPOS invertido 40%. Returns (score, sem)."""
    ndvi_norm = min(100, round((ndvi / 0.75) * 100))
    ipos_inv  = _ipos_to_health(ipos_score, ipos_sem)
    combined  = round(ndvi_norm * 0.60 + ipos_inv * 0.40)
    sem = "verde" if combined >= 70 else "amarillo" if combined >= 50 else "rojo"
    return combined, sem


def _apply_shadow(score: int, sem: str, sombra_pct: float) -> tuple:
    """Apply shadow penalty and recompute semaforo."""
    penalty = _shadow_penalty(sombra_pct)
    if penalty < 1.0:
        score = round(score * penalty)
        sem   = "verde" if score >= 70 else "amarillo" if score >= 50 else "rojo"
    return score, sem


def push_velez_data(ipos: dict, ts: str) -> str:
    """Update velez_data.json: score fusión en canchas, heatmaps_meta, updated_at."""
    r = requests.get(f"{API}/repos/{OWNER}/{REPO}/contents/{VD_PATH}",
                     headers=_hdrs(), params={"ref": BRANCH}, timeout=15)
    if r.status_code != 200:
        raise RuntimeError(f"velez_data.json fetch failed: {r.status_code}")
    d = r.json()
    existing_sha = d["sha"]
    vd = json.loads(base64.b64decode(d["content"]).decode())

    # Real NDVI per cancha from Planetary Computer heatmap pipeline
    roger = vd.get("usuarios", {}).get("roger", {})
    hm    = roger.get("heatmaps", {})
    shadow_maps = vd.get("shadow_maps", {})

    # Migrate fuente/semana out of heatmaps → heatmaps_meta (one-shot if not done yet)
    if "fuente" in hm or "semana" in hm:
        roger.setdefault("heatmaps_meta", {})
        if "fuente" in hm:
            roger["heatmaps_meta"]["fuente"] = hm.pop("fuente")
        if "semana" in hm:
            roger["heatmaps_meta"]["semana"] = hm.pop("semana")

    canchas = vd.get("sectores", {}).get("canchero", {}).get("canchas", [])
    for cancha in canchas:
        cid  = cancha.get("id", "")
        # Prefer real NDVI from Planetary Computer pipeline; fall back to existing value
        hm_e = hm.get(cid)
        ndvi = (hm_e.get("ndvi") if isinstance(hm_e, dict) and hm_e.get("ndvi") is not None
                else cancha.get("ndvi", 0.4))

        # Shadow permanent % from shadow_maps analysis
        sm_data     = shadow_maps.get(cid, {})
        sombra_pct  = sm_data.get("sombra_permanente_pct", 0) if isinstance(sm_data, dict) else 0

        if cid in ipos:
            ip       = ipos[cid]
            ipos_sem = ip.get("semaforo", "verde")
            score, sem = _combined_score(ndvi, float(ip.get("score", 0)), ipos_sem)
            score, sem = _apply_shadow(score, sem, sombra_pct)
            shadow_note = f" · {sombra_pct:.0f}% sombra perm." if sombra_pct >= 15 else ""
            cancha["score_prev"] = cancha.get("score", score)
            cancha["score"]      = score
            cancha["sem"]        = sem
            cancha["ndvi"]       = ndvi
            cancha["detalle"]    = (f"NDVI {ndvi:.2f} · {ip.get('texto', '')} · "
                                    f"{ip.get('personas', 0)} pers · {ip.get('horas', 0)}h"
                                    + shadow_note)
        else:
            # 0 sessions this week — fully rested, NDVI governs
            ndvi_norm = min(100, round((ndvi / 0.75) * 100))
            score     = round(ndvi_norm * 0.60 + 100 * 0.40)
            sem       = "verde" if score >= 70 else "amarillo" if score >= 50 else "rojo"
            score, sem = _apply_shadow(score, sem, sombra_pct)
            shadow_note = f" · {sombra_pct:.0f}% sombra perm." if sombra_pct >= 15 else ""
            cancha["score_prev"] = cancha.get("score", score)
            cancha["score"]      = score
            cancha["sem"]        = sem
            cancha["ndvi"]       = ndvi
            cancha["detalle"]    = f"NDVI {ndvi:.2f} · Sin uso esta semana — descansada" + shadow_note

    vd["updated_at"] = ts

    data = json.dumps(vd, ensure_ascii=False, indent=2).encode()
    msg  = f"ipos sync velez_data — score fusión + heatmaps_meta [{ts}]"
    resp = _put(VD_PATH, data, msg, existing_sha)
    return (resp.get("commit", {}).get("html_url") or
            f"https://github.com/{OWNER}/{REPO}/blob/{BRANCH}/{VD_PATH}")


def push_medicion(med: dict) -> str:
    """Append a field measurement to velez_data.json.mediciones_campo."""
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    med = {**med, "timestamp": ts}
    r = requests.get(f"{API}/repos/{OWNER}/{REPO}/contents/{VD_PATH}",
                     headers=_hdrs(), params={"ref": BRANCH}, timeout=15)
    if r.status_code != 200:
        raise RuntimeError(f"velez_data.json fetch failed: {r.status_code}")
    d = r.json()
    existing_sha = d["sha"]
    vd = json.loads(base64.b64decode(d["content"]).decode())
    vd.setdefault("mediciones_campo", [])
    vd["mediciones_campo"].insert(0, med)
    vd["mediciones_campo"] = vd["mediciones_campo"][:100]
    data = json.dumps(vd, ensure_ascii=False, indent=2).encode()
    msg  = f"medicion {med.get('cancha','?')} {med.get('tipo','?')} [{ts}]"
    resp = _put(VD_PATH, data, msg, existing_sha)
    return (resp.get("commit", {}).get("html_url") or
            f"https://github.com/{OWNER}/{REPO}/blob/{BRANCH}/{VD_PATH}")


def push_config(ipos: dict, semana_label: str, semana_info: dict,
                verify_hashes: dict, sessions: list) -> str:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    # Fetch current config
    r = requests.get(f"{API}/repos/{OWNER}/{REPO}/contents/{CFG_PATH}",
                     headers=_hdrs(), params={"ref":BRANCH}, timeout=15)
    existing_sha = None
    cfg = {}
    if r.status_code == 200:
        d = r.json()
        existing_sha = d.get("sha")
        try:
            cfg = json.loads(base64.b64decode(d["content"]).decode())
        except Exception as e:
            log.warning(f"parse error: {e}")
    elif r.status_code != 404:
        r.raise_for_status()

    # Merge
    cfg.setdefault("ipos_historial", [])
    cfg["ipos_semana"] = {
        "label": semana_label,
        "fecha_inicio": semana_info.get("fecha_inicio",""),
        "fecha_fin": semana_info.get("fecha_fin",""),
        "generado_en": ts,
        "canchas": ipos,
    }
    cfg.setdefault("horarios_vo_semana",{})
    cfg["horarios_vo_semana"]["sessions"] = sessions
    cfg["verify_hashes"] = verify_hashes
    cfg["ipos_historial"].append({
        "label": semana_label,
        "fecha": semana_info.get("fecha_inicio",""),
        "ts": ts,
        "canchas": {k:v["score"] for k,v in ipos.items()},
    })
    cfg["ipos_historial"] = cfg["ipos_historial"][-12:]

    data = json.dumps(cfg, ensure_ascii=False, indent=2).encode()
    msg = f"ipos update semana {semana_label} [{ts}]"
    resp = _put(CFG_PATH, data, msg, existing_sha)
    return (resp.get("commit",{}).get("html_url") or
            f"https://github.com/{OWNER}/{REPO}/blob/{BRANCH}/{CFG_PATH}")


HISTORIAL_DIR = "historial"


def push_historial_snapshot(date_str: str | None = None) -> dict:
    """Crea historial/YYYY-MM-DD.json en GitHub con el snapshot actual de velez_data.json.
    Lee el JSON directo desde GitHub (fuente de verdad), no desde Railway filesystem.
    Idempotente: si el archivo del día ya existe lo actualiza con SHA correcto."""
    ts       = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    date_str = date_str or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    hist_path = f"{HISTORIAL_DIR}/{date_str}.json"

    # Leer velez_data.json desde GitHub
    r = requests.get(f"{API}/repos/{OWNER}/{REPO}/contents/{VD_PATH}",
                     headers=_hdrs(), params={"ref": BRANCH}, timeout=15)
    if r.status_code != 200:
        raise RuntimeError(f"velez_data.json fetch failed: HTTP {r.status_code}")
    vd = json.loads(base64.b64decode(r.json()["content"]).decode())

    snapshot = {
        **vd,
        "historial_meta": {
            "semana":       date_str,
            "committed_at": ts,
            "fuente":       "Faro Protocol · Railway auto-commit",
        },
    }
    content_bytes = json.dumps(snapshot, indent=2, ensure_ascii=False).encode()
    existing_sha  = _sha(hist_path)
    msg = f"historial: snapshot {date_str} · Faro Protocol [{ts}]"
    resp = _put(hist_path, content_bytes, msg, existing_sha)
    action     = "actualizado" if existing_sha else "creado"
    commit_sha = resp.get("commit", {}).get("sha", "")[:7]
    html_url   = (resp.get("content", {}).get("html_url") or
                  f"https://github.com/{OWNER}/{REPO}/blob/{BRANCH}/{hist_path}")
    log.info("historial/%s.json %s — commit %s", date_str, action, commit_sha)
    return {"ok": True, "file": hist_path, "action": action,
            "date": date_str, "commit": commit_sha, "url": html_url}


def push_aspersores(cid: str, aspersores: list) -> str:
    """Store sprinkler positions for a cancha in config_velez.json.aspersores_por_cancha."""
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    r = requests.get(f"{API}/repos/{OWNER}/{REPO}/contents/{CFG_PATH}",
                     headers=_hdrs(), params={"ref": BRANCH}, timeout=15)
    existing_sha = None
    cfg = {}
    if r.status_code == 200:
        d = r.json()
        existing_sha = d.get("sha")
        try:
            cfg = json.loads(base64.b64decode(d["content"]).decode())
        except Exception as e:
            log.warning(f"push_aspersores parse error: {e}")
    elif r.status_code != 404:
        r.raise_for_status()
    cfg.setdefault("aspersores_por_cancha", {})[cid] = aspersores
    data = json.dumps(cfg, ensure_ascii=False, indent=2).encode()
    msg  = f"aspersores {cid.upper()} n={len(aspersores)} [{ts}]"
    resp = _put(CFG_PATH, data, msg, existing_sha)
    return (resp.get("commit", {}).get("html_url") or
            f"https://github.com/{OWNER}/{REPO}/blob/{BRANCH}/{CFG_PATH}")
