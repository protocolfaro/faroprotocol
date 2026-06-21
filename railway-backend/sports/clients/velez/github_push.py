"""
github_push.py — Push files to GitHub via Contents API
Faro Protocol · Vélez Sarsfield
"""
from __future__ import annotations
import base64, json, logging, os, time
from datetime import date, datetime, timezone
import requests

log = logging.getLogger(__name__)
API      = "https://api.github.com"
OWNER    = "protocolfaro"
REPO     = "faroprotocol"
BRANCH   = "main"
CFG_PATH  = "velez/config_velez.json"
VD_PATH   = "velez/velez_data.json"
HM_DIR    = "velez/heatmaps"
CRON_PATH = "velez/cronograma_semana.jpg"

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


# ── Dormancia estacional (invierno austral) ───────────────────────────────────
# Jun–Ago: el pasto entra en dormancia. NDVI bajo es normal, no urgente.
# Se reduce la referencia de NDVI máximo esperado (0.75 → 0.55) para que
# un NDVI de 0.25-0.35 no dispare rojo cuando es solo dormancia invernal.
_WINTER_MONTHS = frozenset((6, 7, 8))


def _ndvi_norm_seasonal(ndvi: float, month: int) -> int:
    """Normalize NDVI to 0-100 with seasonal reference peak."""
    ref = 0.55 if month in _WINTER_MONTHS else 0.75
    return min(100, round((ndvi / ref) * 100))


# ── Tendencia NDVI ────────────────────────────────────────────────────────────
def _positive_trend(ndvi_hist: list) -> bool:
    """
    True if the last ≥3 NDVI readings show a positive linear slope (> 0.005/week).
    Used to suppress false-positive deterioro alerts after reseeding or intervention.
    """
    vals = [v for v in (ndvi_hist or []) if isinstance(v, (int, float))]
    if len(vals) < 3:
        return False
    v = vals[-4:]
    n = len(v)
    mean_x = (n - 1) / 2
    mean_y = sum(v) / n
    num = sum((i - mean_x) * (vi - mean_y) for i, vi in enumerate(v))
    den = sum((i - mean_x) ** 2 for i in range(n))
    if not den:
        return False
    return (num / den) > 0.005


def _combined_score(ndvi: float, ipos_score: float, ipos_sem: str,
                    month: int = None) -> tuple:
    """Score fusión: NDVI Planetary Computer 60% + IPOS invertido 40%. Returns (score, sem)."""
    _month    = month or date.today().month
    ndvi_norm = _ndvi_norm_seasonal(ndvi, _month)
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

    _month  = date.today().month
    _winter = _month in _WINTER_MONTHS

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

        # Rolling NDVI history (last 5 distinct readings) — persisted in velez_data.json.
        # Only append when value changes by ≥0.01 to avoid filling 5 slots within one week
        # (push_velez_data is called both from IPOS sync and satellite pipeline).
        ndvi_hist = list(cancha.get("ndvi_historial") or [])
        if not ndvi_hist or abs(ndvi - ndvi_hist[-1]) >= 0.01:
            ndvi_hist.append(ndvi)
        ndvi_hist = ndvi_hist[-5:]
        recovering = _positive_trend(ndvi_hist)

        if cid in ipos:
            ip       = ipos[cid]
            ipos_sem = ip.get("semaforo", "verde")
            score, sem = _combined_score(ndvi, float(ip.get("score", 0)), ipos_sem,
                                         month=_month)
            score, sem = _apply_shadow(score, sem, sombra_pct)
            shadow_note = f" · {sombra_pct:.0f}% sombra perm." if sombra_pct >= 15 else ""
            # Trend suppression: recovering field → not urgent
            if sem == "rojo" and recovering:
                sem   = "amarillo"
                score = max(score, 50)
            # Context note for detalle
            ctx_note = (" · Recuperación activa ↗" if recovering
                        else (" · Dormancia estacional" if _winter else ""))
            cancha["score_prev"]    = cancha.get("score", score)
            cancha["ndvi_prev"]     = cancha.get("ndvi", ndvi)
            cancha["score"]         = score
            cancha["sem"]           = sem
            cancha["ndvi"]          = ndvi
            cancha["ndvi_historial"] = ndvi_hist
            cancha["detalle"]       = (f"NDVI {ndvi:.2f} · {ip.get('texto', '')} · "
                                       f"{ip.get('personas', 0)} pers · {ip.get('horas', 0)}h"
                                       + ctx_note + shadow_note)
        else:
            # 0 sessions this week — fully rested, NDVI governs
            ndvi_norm = _ndvi_norm_seasonal(ndvi, _month)
            score     = round(ndvi_norm * 0.60 + 100 * 0.40)
            sem       = "verde" if score >= 70 else "amarillo" if score >= 50 else "rojo"
            score, sem = _apply_shadow(score, sem, sombra_pct)
            shadow_note = f" · {sombra_pct:.0f}% sombra perm." if sombra_pct >= 15 else ""
            # Trend suppression: recovering field → not urgent
            if sem == "rojo" and recovering:
                sem   = "amarillo"
                score = max(score, 50)
            ctx_note = (" · Recuperación activa ↗" if recovering
                        else (" · Dormancia estacional" if _winter else ""))
            cancha["score_prev"]    = cancha.get("score", score)
            cancha["ndvi_prev"]     = cancha.get("ndvi", ndvi)
            cancha["score"]         = score
            cancha["sem"]           = sem
            cancha["ndvi"]          = ndvi
            cancha["ndvi_historial"] = ndvi_hist
            cancha["detalle"]       = (f"NDVI {ndvi:.2f} · Sin uso esta semana — descansada"
                                       + ctx_note + shadow_note)

        # Nivel 2: propagate real spectral metrics to canchero cancha dict
        if isinstance(hm_e, dict):
            if hm_e.get("ndre") is not None:
                cancha["ndre"] = hm_e["ndre"]
            if hm_e.get("ccci") is not None:
                cancha["ccci"] = hm_e["ccci"]
            if hm_e.get("pct_baja_ndvi") is not None:
                cancha["pct_baja_ndvi"] = hm_e["pct_baja_ndvi"]
            if hm_e.get("focos_reales"):
                cancha["focos_reales"] = hm_e["focos_reales"]

    # Aggregate sector-level score from cancha scores (single source of truth)
    cancha_scores = [c.get("score") for c in canchas if isinstance(c.get("score"), (int, float))]
    if cancha_scores:
        sector_score = round(sum(cancha_scores) / len(cancha_scores))
        sector_sem   = "verde" if sector_score >= 70 else "amarillo" if sector_score >= 50 else "rojo"
        canchero = vd.setdefault("sectores", {}).setdefault("canchero", {})
        canchero["score"] = sector_score
        canchero["sem"]   = sector_sem

    roger.setdefault("heatmaps_meta", {})["updated_at"] = ts
    vd["updated_at"] = ts

    # Parallel write: also push cancha scores + sector score to Supabase
    try:
        import velez_supabase as _vs
        if _vs._ok():
            sb_canchas = {
                c["id"]: {k: c.get(k) for k in ("ndvi", "score", "score_prev", "sem", "detalle")}
                for c in canchas if c.get("id")
            }
            _vs.upsert_canchas(sb_canchas)
            if cancha_scores:
                _vs.upsert_sectores({"canchero": {"score": sector_score, "sem": sector_sem}})
    except Exception as _se:
        log.debug("push_velez_data Supabase sync (non-fatal): %s", _se)

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


def get_aspersores() -> dict:
    """Return aspersores_por_cancha dict from config_velez.json, or {} if missing/error."""
    r = requests.get(f"{API}/repos/{OWNER}/{REPO}/contents/{CFG_PATH}",
                     headers=_hdrs(), params={"ref": BRANCH}, timeout=15)
    if r.status_code != 200:
        return {}
    try:
        cfg = json.loads(base64.b64decode(r.json()["content"]).decode())
        return cfg.get("aspersores_por_cancha") or {}
    except Exception as e:
        log.warning("get_aspersores parse error: %s", e)
        return {}


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


def push_clegg_medicion(med: dict) -> str:
    """Append Clegg Impact Hammer reading to usuarios.roger.mediciones.clegg (persistent)."""
    ts  = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    med = {**med, "timestamp": ts}
    r = requests.get(f"{API}/repos/{OWNER}/{REPO}/contents/{VD_PATH}",
                     headers=_hdrs(), params={"ref": BRANCH}, timeout=15)
    if r.status_code != 200:
        raise RuntimeError(f"velez_data.json fetch failed: {r.status_code}")
    d = r.json()
    existing_sha = d["sha"]
    vd = json.loads(base64.b64decode(d["content"]).decode())
    clegg = (vd.setdefault("usuarios", {})
               .setdefault("roger", {})
               .setdefault("mediciones", {})
               .setdefault("clegg", []))
    clegg.insert(0, med)
    vd["usuarios"]["roger"]["mediciones"]["clegg"] = clegg[:50]
    vd["updated_at"] = ts
    data = json.dumps(vd, ensure_ascii=False, indent=2).encode()
    msg  = f"clegg {med.get('cancha','?')} {med.get('valor_cg','?')}CG [{ts}]"
    resp = _put(VD_PATH, data, msg, existing_sha)
    return (resp.get("commit", {}).get("html_url") or
            f"https://github.com/{OWNER}/{REPO}/blob/{BRANCH}/{VD_PATH}")


def _push_roger_medicion_key(key: str, med: dict, valor_key: str, unit: str) -> str:
    """Append to usuarios.roger.mediciones.{key}, newest-first, max 50 entries."""
    ts  = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    med = {**med, "timestamp": ts}
    r = requests.get(f"{API}/repos/{OWNER}/{REPO}/contents/{VD_PATH}",
                     headers=_hdrs(), params={"ref": BRANCH}, timeout=15)
    if r.status_code != 200:
        raise RuntimeError(f"velez_data.json fetch failed: {r.status_code}")
    d = r.json()
    existing_sha = d["sha"]
    vd = json.loads(base64.b64decode(d["content"]).decode())
    lst = (vd.setdefault("usuarios", {})
              .setdefault("roger", {})
              .setdefault("mediciones", {})
              .setdefault(key, []))
    lst.insert(0, med)
    vd["usuarios"]["roger"]["mediciones"][key] = lst[:50]
    vd["updated_at"] = ts
    data = json.dumps(vd, ensure_ascii=False, indent=2).encode()
    msg  = f"{key} {med.get('cancha','?')} {med.get(valor_key,'?')}{unit} [{ts}]"
    resp = _put(VD_PATH, data, msg, existing_sha)
    return (resp.get("commit", {}).get("html_url") or
            f"https://github.com/{OWNER}/{REPO}/blob/{BRANCH}/{VD_PATH}")


def push_traccion_medicion(med: dict) -> str:
    """Append torque wrench reading to usuarios.roger.mediciones.traccion (Nm)."""
    return _push_roger_medicion_key("traccion", med, "valor_nm", "Nm")


def push_altura_medicion(med: dict) -> str:
    """Append grass height reading to usuarios.roger.mediciones.altura (mm)."""
    return _push_roger_medicion_key("altura", med, "valor_mm", "mm")


def push_humedad_medicion(med: dict) -> str:
    """Append soil moisture reading to usuarios.roger.mediciones.humedad (%)."""
    return _push_roger_medicion_key("humedad", med, "valor_pct", "%")


def push_raices_medicion(med: dict) -> str:
    """Append root depth reading to usuarios.roger.mediciones.raices (mm)."""
    return _push_roger_medicion_key("raices", med, "valor_mm", "mm")


def push_shadow_maps(shadow_data: dict) -> str:
    """Write shadow_maps analysis to velez_data.json.shadow_maps.
    shadow_data: {cid: {sombra_permanente_pct, horas_sol_dia, notas}}"""
    ts  = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    r = requests.get(f"{API}/repos/{OWNER}/{REPO}/contents/{VD_PATH}",
                     headers=_hdrs(), params={"ref": BRANCH}, timeout=15)
    if r.status_code != 200:
        raise RuntimeError(f"velez_data.json fetch failed: {r.status_code}")
    d = r.json()
    existing_sha = d["sha"]
    vd = json.loads(base64.b64decode(d["content"]).decode())
    vd["shadow_maps"] = {**vd.get("shadow_maps", {}), **shadow_data,
                         "_updated_at": ts}
    vd["updated_at"] = ts
    data = json.dumps(vd, ensure_ascii=False, indent=2).encode()
    msg  = f"shadow_maps: {len(shadow_data)} canchas [{ts}]"
    resp = _put(VD_PATH, data, msg, existing_sha)
    return (resp.get("commit", {}).get("html_url") or
            f"https://github.com/{OWNER}/{REPO}/blob/{BRANCH}/{VD_PATH}")


def delete_medicion(rec_id: str) -> str:
    """Remove a medicion from velez_data.json.mediciones_campo by its client-generated id."""
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    r = requests.get(f"{API}/repos/{OWNER}/{REPO}/contents/{VD_PATH}",
                     headers=_hdrs(), params={"ref": BRANCH}, timeout=15)
    if r.status_code != 200:
        raise RuntimeError(f"velez_data.json fetch failed: {r.status_code}")
    d = r.json()
    existing_sha = d["sha"]
    vd = json.loads(base64.b64decode(d["content"]).decode())
    mediciones = vd.get("mediciones_campo", [])
    before = len(mediciones)
    vd["mediciones_campo"] = [m for m in mediciones if m.get("id") != rec_id]
    if len(vd["mediciones_campo"]) == before:
        return "not_found"
    data = json.dumps(vd, ensure_ascii=False, indent=2).encode()
    msg  = f"delete medicion id={rec_id[:12]} [{ts}]"
    resp = _put(VD_PATH, data, msg, existing_sha)
    return (resp.get("commit", {}).get("html_url") or
            f"https://github.com/{OWNER}/{REPO}/blob/{BRANCH}/{VD_PATH}")


def delete_aspersores(cid: str) -> str:
    """Clear all sprinkler positions for a cancha in config_velez.json."""
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
            log.warning("delete_aspersores parse error: %s", e)
    elif r.status_code != 404:
        r.raise_for_status()
    cfg.setdefault("aspersores_por_cancha", {})[cid] = []
    data = json.dumps(cfg, ensure_ascii=False, indent=2).encode()
    msg  = f"clear aspersores {cid.upper()} [{ts}]"
    resp = _put(CFG_PATH, data, msg, existing_sha)
    return (resp.get("commit", {}).get("html_url") or
            f"https://github.com/{OWNER}/{REPO}/blob/{BRANCH}/{CFG_PATH}")


def push_cronograma(image_bytes: bytes) -> str:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    existing = _sha(CRON_PATH)
    resp = _put(CRON_PATH, image_bytes, f"cronograma semanal [{ts}]", existing)
    return (resp.get("content", {}).get("html_url") or
            f"https://github.com/{OWNER}/{REPO}/blob/{BRANCH}/{CRON_PATH}")


def push_horarios_from_parse(sessions: list) -> str:
    """Update only horarios_vo_semana.sessions in config_velez.json."""
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
            log.warning("push_horarios_from_parse parse error: %s", e)
    elif r.status_code != 404:
        r.raise_for_status()
    cfg.setdefault("horarios_vo_semana", {})["sessions"] = sessions
    data = json.dumps(cfg, ensure_ascii=False, indent=2).encode()
    resp = _put(CFG_PATH, data, f"horarios via cronograma-parse [{ts}]", existing_sha)
    return (resp.get("commit", {}).get("html_url") or
            f"https://github.com/{OWNER}/{REPO}/blob/{BRANCH}/{CFG_PATH}")
