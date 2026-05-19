"""
github_push.py — Push files to GitHub via Contents API
Faro Protocol · Vélez Sarsfield
"""
from __future__ import annotations
import base64, json, logging, os, time
from datetime import datetime, timezone
import requests

log = logging.getLogger(__name__)
API   = "https://api.github.com"
OWNER = "protocolfaro"
REPO  = "faroprotocol"
BRANCH = "main"
CFG_PATH = "velez/config_velez.json"
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
