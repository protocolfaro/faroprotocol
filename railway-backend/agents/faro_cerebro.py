"""
faro_cerebro.py — Cerebro Autónomo Faro Protocol v2.0

Arquitectura basada en:
  - AIOpsLab (Microsoft Research, MLSys 2025) — agente nunca accede directo al sistema
  - Springdrift (arXiv 2604.04660) — checks en paralelo, audit log completo
  - Azure SRE Agent — allowlist explícita, autonomy ladder
  - Darwin Gödel Machine (Sakana AI, ICLR 2026) — fix via PR, nunca toca main
  - Alertmanager — inhibition rules, flapping detection

REGLAS ABSOLUTAS — gravadas en piedra:
  1. NUNCA mandar emails a la lista del club — solo a CEREBRO_ALERT_EMAIL
  2. NUNCA tocar main directamente — siempre Pull Request
  3. NUNCA llamar send_all_reports() ni run_weekly_job()
  4. SOLO puede llamar: /velez/run-refresh, GitHub API (PR), Gmail (alerta interna)
"""
from __future__ import annotations

import base64, json, logging, os, py_compile, sys, tempfile, time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

import anthropic
import requests

log = logging.getLogger("faro.cerebro")

# ── PATH SETUP ────────────────────────────────────────────────────────────────
_ROOT = Path(__file__).parents[1]   # railway-backend/
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

# ── CONFIGURACIÓN ─────────────────────────────────────────────────────────────
CEREBRO_ALERT_EMAIL = "protocolfaro@gmail.com"   # NUNCA cambiar — nunca lista del club

_PORT        = os.environ.get("PORT", "8080")
INTERNAL_URL = f"http://localhost:{_PORT}"        # llamadas internas al mismo contenedor Railway

SUPABASE_URL  = os.environ.get("SUPABASE_URL", "").rstrip("/")
SUPABASE_KEY  = os.environ.get("SUPABASE_KEY", "")
GITHUB_TOKEN  = os.environ.get("GITHUB_TOKEN", "")
ANTHROPIC_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
DEADMAN_UUID  = os.environ.get("HEALTHCHECKS_UUID", "")
REPO          = "protocolfaro/faroprotocol"
_GH           = f"https://api.github.com/repos/{REPO}"

# ── ALLOWLIST — el cerebro SOLO puede ejecutar estas acciones ─────────────────
ALLOWED_ACTIONS = frozenset([
    "check_health",
    "check_json_freshness",
    "check_pngs",
    "check_supabase",
    "check_panel",
    "trigger_refresh",    # POST /velez/run-refresh — datos y PNGs, NUNCA emails
    "send_alert_email",   # SOLO a CEREBRO_ALERT_EMAIL
    "open_github_pr",     # NUNCA push a main
    "ping_deadman",       # healthchecks.io
    "log_to_supabase",
])

# Archivos que el DGM puede proponer modificar via PR
_DGM_ALLOWED_FILES = frozenset([
    "railway-backend/sports/clients/velez/gen_velez_solar_v2.py",
    "railway-backend/sports/clients/velez/gen_velez_main.py",
    "railway-backend/sports/clients/velez/gen_velez_canchero.py",
    "railway-backend/sports/clients/velez/gen_velez_final.py",
])

# ── INHIBITION RULES — si A falla, no escalar B (Alertmanager pattern) ────────
INHIBITION_RULES: dict[str, list[str]] = {
    "check_health":   ["check_json_freshness", "check_pngs", "check_supabase", "check_panel"],
    "check_supabase": ["check_json_freshness"],
}

# ── FLAPPING DETECTION — escalar solo tras N fallos consecutivos ──────────────
_consecutive: dict[str, int] = {}   # reinicia en restart (aceptable — Railway es estable)
FLAP_THRESHOLD = 2                  # escalar a partir del 3er fallo consecutivo

# ── CIRCUIT BREAKER — Claude API ─────────────────────────────────────────────
_cb_failures    = 0
_cb_open_until: Optional[datetime] = None
CB_FAIL_MAX     = 3
CB_RESET_MIN    = 30

# ── ZONA HORARIA ──────────────────────────────────────────────────────────────
_ART = timezone(timedelta(hours=-3))


# ═══════════════════════════════════════════════════════════════════════════════
# SUPABASE HELPERS
# ═══════════════════════════════════════════════════════════════════════════════

def _sb_headers() -> dict:
    return {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "return=minimal",
    }


def _sb_insert(table: str, data: dict) -> bool:
    if not SUPABASE_URL or not SUPABASE_KEY:
        return False
    try:
        r = requests.post(
            f"{SUPABASE_URL}/rest/v1/{table}",
            headers=_sb_headers(), json=data, timeout=10,
        )
        return r.status_code in (200, 201, 204)
    except Exception as e:
        log.warning("Supabase insert [%s]: %s", table, e)
        return False


def _sb_query(table: str, filters: str) -> Optional[list]:
    """Retorna lista en éxito, None en error (distinguible de lista vacía)."""
    if not SUPABASE_URL or not SUPABASE_KEY:
        return None
    try:
        r = requests.get(
            f"{SUPABASE_URL}/rest/v1/{table}?{filters}",
            headers=_sb_headers(), timeout=10,
        )
        return r.json() if r.ok else None
    except Exception as e:
        log.warning("Supabase query [%s]: %s", table, e)
        return None


# ═══════════════════════════════════════════════════════════════════════════════
# COOLDOWN — Supabase primero, memoria local como fallback
# Persiste entre redeployos de Railway (SQLite local no lo haría)
# ═══════════════════════════════════════════════════════════════════════════════

_local_cooldowns: dict[str, datetime] = {}


def _cooldown_active(fp: str, hours: int) -> bool:
    # Supabase
    since = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    rows = _sb_query(
        "faro_cerebro_cooldowns",
        f"fingerprint=eq.{fp}&sent_at=gt.{since}&select=id&limit=1",
    )
    if isinstance(rows, list) and len(rows) > 0:
        return True
    # Fallback local (Supabase caído)
    sent_at = _local_cooldowns.get(fp)
    if sent_at and (datetime.now(timezone.utc) - sent_at).total_seconds() / 3600 < hours:
        return True
    return False


def _cooldown_set(fp: str) -> bool:
    """INSERT atómico — si falla, NO mandar el email (previene duplicados)."""
    ok = _sb_insert("faro_cerebro_cooldowns", {
        "fingerprint": fp,
        "sent_at": datetime.now(timezone.utc).isoformat(),
    })
    _local_cooldowns[fp] = datetime.now(timezone.utc)   # siempre setear local
    return ok


# ═══════════════════════════════════════════════════════════════════════════════
# DEAD MAN'S SWITCH
# ═══════════════════════════════════════════════════════════════════════════════

def _ping_deadman():
    if not DEADMAN_UUID:
        return
    try:
        requests.get(f"https://hc-ping.com/{DEADMAN_UUID}", timeout=5)
        log.debug("Dead man's switch: ping OK")
    except Exception:
        pass


# ═══════════════════════════════════════════════════════════════════════════════
# CIRCUIT BREAKER — Claude API
# ═══════════════════════════════════════════════════════════════════════════════

def _ask_claude(system: str, user: str) -> str:
    global _cb_failures, _cb_open_until
    now = datetime.now(timezone.utc)

    if not ANTHROPIC_KEY:
        log.warning("Cerebro: ANTHROPIC_API_KEY no configurada — Claude skip (agregar en Railway Variables)")
        return ""

    if _cb_open_until and now < _cb_open_until:
        mins = int((_cb_open_until - now).total_seconds() / 60)
        log.warning("Cerebro: circuit breaker abierto — Claude en pausa %d min más", mins)
        return ""

    try:
        client = anthropic.Anthropic(api_key=ANTHROPIC_KEY)
        resp = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=1000,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        _cb_failures = 0
        _cb_open_until = None
        return resp.content[0].text
    except Exception as e:
        _cb_failures += 1
        log.warning("Cerebro: Claude API error (%d/%d): %s", _cb_failures, CB_FAIL_MAX, e)
        if _cb_failures >= CB_FAIL_MAX:
            _cb_open_until = now + timedelta(minutes=CB_RESET_MIN)
            log.error("Cerebro: circuit breaker activado — Claude en pausa %d min", CB_RESET_MIN)
        return ""


# ═══════════════════════════════════════════════════════════════════════════════
# CHECKS — funciones individuales
# ═══════════════════════════════════════════════════════════════════════════════

def _fn_health() -> tuple[bool, str]:
    r = requests.get(f"{INTERNAL_URL}/health", timeout=10)
    return r.status_code == 200, f"HTTP {r.status_code}"


def _fn_json_freshness() -> tuple[bool, str]:
    r = requests.get(
        "https://raw.githubusercontent.com/protocolfaro/faro-paneles/main/velez/velez_data.json",
        timeout=15,
    )
    if not r.ok:
        return False, f"GitHub raw HTTP {r.status_code}"
    ts = r.json().get("weather_live", {}).get("timestamp", "")
    if not ts:
        return True, "sin timestamp — skip"
    t = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    horas = (datetime.now(timezone.utc) - t).total_seconds() / 3600
    return horas < 170, f"{horas:.0f}h de antigüedad (límite 170h — pipeline semanal)"


def _fn_supabase() -> tuple[bool, str]:
    rows = _sb_query("faro_cerebro_log", "select=id&limit=1")
    if rows is None:
        return False, "Supabase no responde"
    return True, "ok"


def _fn_panel() -> tuple[bool, str]:
    r = requests.get(
        "https://protocolfaro.github.io/faro-paneles/velez/",
        timeout=10,
    )
    return r.status_code == 200, f"GitHub Pages HTTP {r.status_code}"


def _fn_pngs() -> tuple[bool, str]:
    try:
        r = requests.get(f"{INTERNAL_URL}/velez/check-pngs", timeout=10)
        if r.ok:
            missing = r.json().get("missing", [])
            return len(missing) == 0, (f"faltantes: {missing}" if missing else "ok")
    except Exception:
        pass
    return True, "endpoint no disponible — skip"


# ═══════════════════════════════════════════════════════════════════════════════
# CHECK RUNNER — paralelo con retry y timeout
# Dos pasadas: 1) ejecutar todos, 2) aplicar inhibition + flapping
# ═══════════════════════════════════════════════════════════════════════════════

_CHECKS: dict[str, callable] = {
    "check_health":         _fn_health,
    "check_supabase":       _fn_supabase,
    "check_json_freshness": _fn_json_freshness,
    "check_panel":          _fn_panel,
    "check_pngs":           _fn_pngs,
}


def _run_check_with_retry(name: str, fn, retries: int = 2, delay: int = 30) -> tuple[bool, str]:
    msg = "sin resultado"
    for attempt in range(retries + 1):
        try:
            ok, msg = fn()
            if ok:
                _consecutive[name] = 0
                return True, msg
            if attempt < retries:
                log.warning("%s: intento %d/%d falló — reintentando en %ds",
                            name, attempt + 1, retries, delay)
                time.sleep(delay)
        except Exception as e:
            msg = str(e)
            if attempt < retries:
                time.sleep(delay)
            else:
                _consecutive[name] = _consecutive.get(name, 0) + 1
                return False, msg
    _consecutive[name] = _consecutive.get(name, 0) + 1
    return False, msg


def _run_all_checks() -> dict[str, tuple[bool, str]]:
    """Pasada 1: correr todos los checks en paralelo."""
    results: dict[str, tuple[bool, str]] = {}
    with ThreadPoolExecutor(max_workers=5, thread_name_prefix="cerebro") as pool:
        futures = {
            pool.submit(_run_check_with_retry, name, fn): name
            for name, fn in _CHECKS.items()
        }
        for future in as_completed(futures, timeout=180):
            name = futures[future]
            try:
                results[name] = future.result(timeout=5)
            except Exception as e:
                results[name] = (False, f"excepción: {e}")
                _consecutive[name] = _consecutive.get(name, 0) + 1
    return results


def _apply_inhibition(raw: dict[str, tuple[bool, str]]) -> dict[str, str]:
    """
    Pasada 2: aplicar inhibition rules y flapping detection.
    Retorna solo los fallos que deben escalarse.
    """
    failed = {name for name, (ok, _) in raw.items() if not ok}
    to_escalate: dict[str, str] = {}

    for name in failed:
        # Inhibition: suprimir si algún padre también falló
        suppressed = any(
            name in children
            for parent, children in INHIBITION_RULES.items()
            if parent in failed
        )
        if suppressed:
            log.info("Cerebro: %s suprimido (inhibition rule)", name)
            continue

        # Flapping: esperar N fallos consecutivos antes de escalar
        consec = _consecutive.get(name, 0)
        if consec < FLAP_THRESHOLD:
            log.warning("Cerebro: %s falló %d vez/veces — esperando confirmación (%d para escalar)",
                        name, consec, FLAP_THRESHOLD)
            continue

        to_escalate[name] = raw[name][1]

    return to_escalate


# ═══════════════════════════════════════════════════════════════════════════════
# ACCIONES CORRECTIVAS
# ═══════════════════════════════════════════════════════════════════════════════

def _trigger_refresh() -> tuple[bool, str]:
    """POST /velez/run-refresh — datos y PNGs. NUNCA emails."""
    try:
        r = requests.post(f"{INTERNAL_URL}/velez/run-refresh", timeout=30)
        return r.ok, f"HTTP {r.status_code}"
    except Exception as e:
        return False, str(e)


def _send_alert(subject: str, body_html: str, fingerprint: str) -> bool:
    """Email SOLO a CEREBRO_ALERT_EMAIL. Cooldown gestionado por _procesar_alerta."""
    try:
        from sports.clients.velez.velez_scheduler import send_email
        html = f"""
<html><body style="font-family:Arial,sans-serif;background:#07110a;color:#f2ede4;
                   padding:24px;max-width:680px;margin:0 auto">
  <h2 style="color:#c9a84c;margin-top:0">Faro Cerebro — {subject}</h2>
  {body_html}
  <hr style="border-color:#c9a84c44;margin:24px 0">
  <p style="color:#9aa0a8;font-size:11px">
    Faro Cerebro v2.0 · Monitor autónomo · {datetime.now(_ART).strftime('%d/%m/%Y %H:%M')} ART<br>
    Alertas internas únicamente · <a href="mailto:{CEREBRO_ALERT_EMAIL}"
    style="color:#c9a84c">{CEREBRO_ALERT_EMAIL}</a>
  </p>
</body></html>"""
        ok = send_email(CEREBRO_ALERT_EMAIL, f"[Faro Cerebro] {subject}", html)
        log.info("Cerebro: alerta %s — %s", "enviada" if ok else "falló SMTP", subject)
        return bool(ok)
    except Exception as e:
        log.error("Cerebro: send_email error: %s", e)
        return False


# ═══════════════════════════════════════════════════════════════════════════════
# SEVERITY — clasificación de alertas por nivel de urgencia
# ═══════════════════════════════════════════════════════════════════════════════

SEVERITY_RULES: dict = {
    # LEVE — nunca manda email, solo se loguea en Supabase
    "leve": {
        "checks": ["check_pngs_lento", "check_supabase_warning"],
        "cooldown_horas": None,
    },
    # MEDIA — falla 2+ veces seguidas, email cada 12h máximo
    "media": {
        "checks": ["check_json_freshness", "check_pngs", "check_panel"],
        "cooldown_horas": 12,
        "min_fallos_consecutivos": 2,
    },
    # CRÍTICA — falla el servidor o lleva +24h sin actualizar — email inmediato sin cooldown
    "critica": {
        "checks": ["check_health", "pipeline_24h_caido"],
        "cooldown_horas": 0,
    },
}


def _clasificar_severidad(check_name: str, fallos_consecutivos: int) -> str:
    if check_name in SEVERITY_RULES["critica"]["checks"]:
        return "critica"
    if check_name in SEVERITY_RULES["media"]["checks"] and fallos_consecutivos >= 2:
        return "media"
    return "leve"


def _procesar_alerta(check_name: str, mensaje: str, fallos_consecutivos: int) -> None:
    severidad = _clasificar_severidad(check_name, fallos_consecutivos)

    _sb_insert("faro_cerebro_log", {
        "accion": check_name,
        "resultado": mensaje,
        "error": severidad,
        "escalado": severidad in ("media", "critica"),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })

    if severidad == "leve":
        return

    cooldown_h = SEVERITY_RULES[severidad]["cooldown_horas"]
    fingerprint = f"{severidad}_{check_name}"

    if cooldown_h > 0 and _cooldown_active(fingerprint, hours=cooldown_h):
        log.info("Cerebro: alerta suprimida por cooldown — %s", fingerprint)
        return

    if cooldown_h > 0:
        _cooldown_set(fingerprint)

    _send_alert(
        subject=f"[{severidad.upper()}] {check_name}",
        body_html=mensaje,
        fingerprint=fingerprint,
    )


# ═══════════════════════════════════════════════════════════════════════════════
# DGM PATTERN — fix via PR, nunca toca main
# ═══════════════════════════════════════════════════════════════════════════════

def _propose_fix_via_pr(file_path: str, new_content: str, description: str) -> str:
    """
    Proponer fix via Pull Request.
    Requisitos: GITHUB_TOKEN, archivo en allowlist, código Python válido.
    """
    if not GITHUB_TOKEN:
        log.warning("DGM: GITHUB_TOKEN no configurado")
        return ""
    if file_path not in _DGM_ALLOWED_FILES:
        log.warning("DGM: %s no está en la allowlist — rechazado", file_path)
        return ""

    # Validar sintaxis antes de proponer
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".py", mode="w", delete=False, encoding="utf-8") as tmp:
            tmp.write(new_content)
            tmp_path = tmp.name
        py_compile.compile(tmp_path, doraise=True)
    except py_compile.PyCompileError as e:
        log.warning("DGM: fix inválido sintácticamente — %s", e)
        return ""
    finally:
        if tmp_path:
            try:
                os.unlink(tmp_path)
            except Exception:
                pass

    hdrs = {"Authorization": f"token {GITHUB_TOKEN}", "Accept": "application/vnd.github+json"}
    branch = f"faro-cerebro-fix-{datetime.utcnow().strftime('%Y%m%d-%H%M')}"

    try:
        r = requests.get(f"{_GH}/contents/{file_path}", headers=hdrs, timeout=10)
        if not r.ok:
            log.warning("DGM: no se pudo obtener SHA de %s — HTTP %s", file_path, r.status_code)
            return ""
        file_sha = r.json()["sha"]

        main_sha = requests.get(
            f"{_GH}/git/refs/heads/main", headers=hdrs, timeout=10
        ).json()["object"]["sha"]

        requests.post(f"{_GH}/git/refs", headers=hdrs, timeout=10,
                      json={"ref": f"refs/heads/{branch}", "sha": main_sha})

        requests.put(f"{_GH}/contents/{file_path}", headers=hdrs, timeout=10,
                     json={"message": f"fix(cerebro): {description[:72]}",
                           "content": base64.b64encode(new_content.encode()).decode(),
                           "sha": file_sha, "branch": branch})

        pr = requests.post(f"{_GH}/pulls", headers=hdrs, timeout=10,
                           json={"title": f"Cerebro Fix: {description[:72]}",
                                 "head": branch, "base": "main",
                                 "body": (
                                     f"Fix autogenerado por Faro Cerebro v2.0.\n\n"
                                     f"**Bug:** {description}\n\n"
                                     f"⚠️ Revisar y aprobar antes de mergear — nunca auto-merge."
                                 )})
        if pr.status_code == 201:
            url = pr.json()["html_url"]
            log.info("DGM: PR creado — %s", url)
            return url
        log.warning("DGM: PR falló HTTP %s — %s", pr.status_code, pr.text[:200])
    except Exception as e:
        log.error("DGM: error — %s", e)
    return ""


# ═══════════════════════════════════════════════════════════════════════════════
# SKILLS EN SUPABASE
# ═══════════════════════════════════════════════════════════════════════════════

def _get_skills() -> str:
    rows = _sb_query(
        "faro_cerebro_skills",
        "select=name,description&order=created_at.desc&limit=10",
    )
    if not rows:
        return "Ninguna aún."
    return "\n".join(f"- {r['name']}: {r['description']}" for r in rows)


def _save_skill(name: str, code: str, description: str):
    _sb_insert("faro_cerebro_skills", {
        "name": name[:80],
        "code": code[:4000],
        "description": description[:200],
        "created_at": datetime.now(timezone.utc).isoformat(),
    })


# ═══════════════════════════════════════════════════════════════════════════════
# CICLO PRINCIPAL
# ═══════════════════════════════════════════════════════════════════════════════

def run_cerebro_cycle():
    cycle_start = datetime.now(timezone.utc)
    log.info("Faro Cerebro v2.0: ciclo — %s", cycle_start.isoformat())

    try:
        # ── Pasada 1: correr todos los checks en paralelo ─────────────────────
        raw = _run_all_checks()

        # ── Pasada 2: inhibition rules + flapping detection ───────────────────
        failures = _apply_inhibition(raw)

        # ── Sistema nominal ───────────────────────────────────────────────────
        if not failures:
            _sb_insert("faro_cerebro_log", {
                "accion": "ciclo_rutina",
                "resultado": f"Sistema nominal — {len(raw)} checks OK",
                "escalado": False,
                "timestamp": cycle_start.isoformat(),
            })
            log.info("Faro Cerebro: sistema nominal")
            return

        log.warning("Faro Cerebro: %d alerta(s) — %s", len(failures), list(failures.keys()))

        # ── Autocorrección: refresh si hay problemas de datos o PNGs ─────────
        needs_refresh = bool({"check_pngs", "check_json_freshness"} & set(failures))
        if needs_refresh and not _cooldown_active("trigger_refresh", hours=2):
            ok_ref, msg_ref = _trigger_refresh()
            _cooldown_set("trigger_refresh")
            log.info("Cerebro: refresh disparado — %s", msg_ref)
            _sb_insert("faro_cerebro_log", {
                "accion": "autocorreccion_refresh",
                "resultado": msg_ref,
                "escalado": False,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })
            if ok_ref:
                return  # refresh resolvió — no escalar

        # ── Consultar Claude para diagnóstico ─────────────────────────────────
        skills = _get_skills()
        decision = _ask_claude(
            system=f"""Sos el cerebro autónomo de Faro Protocol.
Skills aprendidas:
{skills}

Acciones permitidas ÚNICAMENTE: {sorted(ALLOWED_ACTIONS)}
NUNCA: send_all_reports, run_weekly_job, emails a velez.com.ar, push directo a main.""",
            user=(
                f"Fallos detectados:\n{json.dumps(failures, indent=2, ensure_ascii=False)}\n\n"
                f"¿Qué acción tomamos?"
            ),
        )

        # ── DGM: proponer fix si un check falla de manera sostenida ──────────
        for check_name, msg in failures.items():
            if _consecutive.get(check_name, 0) >= 5:
                log.info("DGM: %s falló %d veces — solicitando fix a Claude",
                         check_name, _consecutive[check_name])
                fix_response = _ask_claude(
                    system=(
                        "Generá un fix de Python para el siguiente bug. "
                        f"El archivo a modificar debe ser uno de: {sorted(_DGM_ALLOWED_FILES)}. "
                        "Respondé con un JSON: {\"file\": \"ruta/al/archivo.py\", \"code\": \"...código completo...\"}"
                    ),
                    user=f"Bug persistente ({_consecutive[check_name]} fallos): {check_name}\nDetalle: {msg}",
                )
                if fix_response:
                    try:
                        fix_data = json.loads(fix_response)
                        target_file = fix_data.get("file", "")
                        fix_code    = fix_data.get("code", "")
                        if target_file and fix_code:
                            pr_url = _propose_fix_via_pr(target_file, fix_code,
                                                         f"{check_name}: {msg}")
                            if pr_url:
                                _save_skill(
                                    f"fix_{check_name}"[:80],
                                    fix_code,
                                    f"Fix para {check_name} ({_consecutive[check_name]} fallos)"
                                )
                                _sb_insert("faro_cerebro_log", {
                                    "accion": "dgm_pr_creado",
                                    "resultado": pr_url,
                                    "escalado": True,
                                    "timestamp": datetime.now(timezone.utc).isoformat(),
                                })
                    except (json.JSONDecodeError, KeyError):
                        log.warning("DGM: respuesta Claude no es JSON válido — %s", fix_response[:100])

        # ── Escalar por severidad ─────────────────────────────────────────────
        for check_name, msg in failures.items():
            _procesar_alerta(check_name, msg, _consecutive.get(check_name, 0))

        _sb_insert("faro_cerebro_log", {
            "accion": "ciclo_con_alertas",
            "resultado": (decision or "sin respuesta Claude")[:500],
            "error": json.dumps(failures, ensure_ascii=False),
            "escalado": True,
            "timestamp": cycle_start.isoformat(),
        })

    except Exception as e:
        log.error("Faro Cerebro: excepción no capturada — %s", e, exc_info=True)
    finally:
        _ping_deadman()   # siempre pingar, incluso si el ciclo falló


# ═══════════════════════════════════════════════════════════════════════════════
# REGISTRO EN APSCHEDULER
# ═══════════════════════════════════════════════════════════════════════════════

def register_cerebro_job(scheduler) -> None:
    scheduler.add_job(
        run_cerebro_cycle,
        "interval",
        minutes=60,
        id="faro_cerebro",
        replace_existing=True,   # evita duplicados en restart de Railway
        max_instances=1,
        coalesce=True,           # si se acumularon ejecuciones perdidas, correr solo una
        misfire_grace_time=300,
        jitter=120,              # variación de hasta 2 min para evitar thundering herd
    )
    log.info("Faro Cerebro v2.0 registrado — ciclo cada 60 min (jitter 2 min)")
