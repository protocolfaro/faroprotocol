"""
faro_cerebro.py — Monitor autónomo Faro Protocol (arquitectura completa).

Corre cada 60 min via APScheduler:
  1. Memoria episódica SQLite  — evita loops infinitos de reintentos
  2. Skills automáticas        — guarda lo que aprende en hermes_skills/
  3. DGM Pattern               — propone fixes via GitHub PR (nunca toca main)
  4. Claude SDK                — toma decisiones con contexto de skills aprendidas

Chequeos base:
  - JSON freshness < 25h      → auto-llama run_now
  - /health responde 200      → alerta si cae
  - PNGs en reportes_velez/   → auto-llama run_now

Reglas absolutas:
  - NUNCA emails a destinatarios del club sin aprobación de Emilio
  - NUNCA modifica código en main — siempre PR
  - SÍ puede: correr refreshes, regenerar PNGs, abrir PRs, alertar a protocolfaro@gmail.com
"""
from __future__ import annotations
import base64, json, logging, os, py_compile, sqlite3, sys, tempfile, time
from datetime import datetime, timezone, timedelta
from pathlib import Path

import requests
import anthropic

log = logging.getLogger(__name__)

# ── Constantes ────────────────────────────────────────────────────────────────

_ROOT       = Path(__file__).parents[1]          # railway-backend/
_AGENT_DIR  = Path(__file__).parent              # railway-backend/agents/
_PNG_DIR    = _ROOT / "reportes_velez"
CEREBRO_ALERT_EMAIL = "protocolfaro@gmail.com"  # NUNCA cambiar esto
_PORT       = os.environ.get("PORT", "8080")
_BASE_URL   = f"http://localhost:{_PORT}"
_GITHUB_REPO = "protocolfaro/faroprotocol"

_EXPECTED_PNGS  = ["faro_reporte_velez.png", "faro_reporte_velez_solar_v2.png"]
_MAX_JSON_AGE_H = 25

# ── Memoria episódica SQLite ──────────────────────────────────────────────────

DB_PATH = _AGENT_DIR / "hermes_memory.db"


def _init_db():
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS short_term_memory (
                id        INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT,
                task      TEXT,
                status    TEXT,
                attempts  INTEGER DEFAULT 1
            )
        """)
        conn.commit()


_ALERT_COOLDOWN_H = 6  # máximo 1 email de alerta cada 6 horas


def _alert_on_cooldown() -> bool:
    """True si ya se mandó un email de alerta en las últimas _ALERT_COOLDOWN_H horas."""
    try:
        with sqlite3.connect(DB_PATH) as conn:
            row = conn.execute(
                "SELECT timestamp FROM short_term_memory WHERE task='alert_email_sent' "
                "ORDER BY id DESC LIMIT 1"
            ).fetchone()
            if not row:
                return False
            last = datetime.fromisoformat(row[0])
            return (datetime.utcnow() - last).total_seconds() / 3600 < _ALERT_COOLDOWN_H
    except Exception:
        return False


def _record_alert_sent():
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "INSERT INTO short_term_memory (timestamp, task, status) VALUES (?,?,?)",
            (datetime.utcnow().isoformat(), "alert_email_sent", "sent")
        )
        conn.commit()


def _get_attempts(task: str) -> int:
    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute(
            "SELECT attempts FROM short_term_memory WHERE task=? ORDER BY id DESC LIMIT 1",
            (task,)
        ).fetchone()
        return row[0] if row else 0


def _record_attempt(task: str, status: str):
    with sqlite3.connect(DB_PATH) as conn:
        existing = conn.execute(
            "SELECT id, attempts FROM short_term_memory WHERE task=? ORDER BY id DESC LIMIT 1",
            (task,)
        ).fetchone()
        if existing and status == "failed":
            conn.execute(
                "UPDATE short_term_memory SET attempts=?, timestamp=? WHERE id=?",
                (existing[1] + 1, datetime.utcnow().isoformat(), existing[0])
            )
        else:
            conn.execute(
                "INSERT INTO short_term_memory (timestamp, task, status) VALUES (?,?,?)",
                (datetime.utcnow().isoformat(), task, status)
            )
        conn.commit()


# ── Skills automáticas ────────────────────────────────────────────────────────

SKILLS_DIR = _AGENT_DIR / "hermes_skills"
SKILLS_DIR.mkdir(exist_ok=True)


def _save_skill(name: str, code: str, description: str):
    skill_file = SKILLS_DIR / f"{name}.py"
    skill_file.write_text(f'"""{description}"""\n\n{code}')
    log.info("Cerebro: nueva skill guardada — %s", name)


def _load_skills() -> str:
    skills = []
    for f in sorted(SKILLS_DIR.glob("*.py")):
        skills.append(f"- {f.stem}: {f.read_text()[:200]}")
    return "\n".join(skills) if skills else "Ninguna aún."


# ── DGM Pattern — fix via GitHub PR ──────────────────────────────────────────

def _propose_fix_via_pr(file_path: str, new_content: str, bug_description: str) -> str:
    """
    Propone un fix via Pull Request. NUNCA toca main directamente.
    Retorna la URL del PR creado, o "" si falló.
    """
    token = os.environ.get("GITHUB_TOKEN", "")
    if not token:
        log.warning("DGM: GITHUB_TOKEN no configurado — no se puede abrir PR")
        return ""
    headers = {"Authorization": f"token {token}", "Accept": "application/vnd.github+json"}
    branch  = f"faro-cerebro-fix-{datetime.utcnow().strftime('%Y%m%d-%H%M')}"

    try:
        # SHA del archivo en main
        r = requests.get(
            f"https://api.github.com/repos/{_GITHUB_REPO}/contents/{file_path}",
            headers=headers, timeout=10
        )
        if r.status_code != 200:
            log.warning("DGM: no se pudo obtener SHA de %s — HTTP %s", file_path, r.status_code)
            return ""
        file_sha = r.json()["sha"]

        # SHA de main
        main_sha = requests.get(
            f"https://api.github.com/repos/{_GITHUB_REPO}/git/refs/heads/main",
            headers=headers, timeout=10
        ).json()["object"]["sha"]

        # Crear rama
        requests.post(
            f"https://api.github.com/repos/{_GITHUB_REPO}/git/refs",
            headers=headers,
            json={"ref": f"refs/heads/{branch}", "sha": main_sha},
            timeout=10
        )

        # Subir fix a la rama
        encoded = base64.b64encode(new_content.encode()).decode()
        requests.put(
            f"https://api.github.com/repos/{_GITHUB_REPO}/contents/{file_path}",
            headers=headers,
            json={"message": f"fix(cerebro): {bug_description[:72]}",
                  "content": encoded, "sha": file_sha, "branch": branch},
            timeout=10
        )

        # Abrir PR
        pr = requests.post(
            f"https://api.github.com/repos/{_GITHUB_REPO}/pulls",
            headers=headers,
            json={"title": f"Cerebro Fix: {bug_description[:72]}",
                  "head": branch, "base": "main",
                  "body": f"Fix autogenerado por Faro Cerebro.\n\n**Bug:** {bug_description}\n\n"
                          f"Validado sintácticamente con `py_compile`. Requiere revisión de Emilio."},
            timeout=10
        )
        if pr.status_code == 201:
            pr_url = pr.json()["html_url"]
            log.info("Cerebro DGM: PR creado — %s", pr_url)
            return pr_url
        log.warning("DGM: PR falló HTTP %s — %s", pr.status_code, pr.text[:200])
    except Exception as exc:
        log.error("DGM _propose_fix_via_pr: %s", exc)
    return ""


# ── Supabase log ──────────────────────────────────────────────────────────────

def _supa_base() -> str:
    return os.environ.get("SUPABASE_URL", "").rstrip("/")


def _supa_key() -> str:
    return os.environ.get("SUPABASE_KEY", "")


def _supa_hdrs() -> dict:
    k = _supa_key()
    return {"apikey": k, "Authorization": f"Bearer {k}",
            "Content-Type": "application/json", "Prefer": "return=minimal"}


def _log_supabase(accion: str, resultado: str = None, error: str = None, escalado: bool = False):
    base = _supa_base()
    if not base or not _supa_key():
        log.debug("cerebro_log: Supabase no configurado")
        return
    row = {"accion": accion, "resultado": resultado, "error": error, "escalado": escalado}
    try:
        r = requests.post(f"{base}/rest/v1/faro_cerebro_log",
                          headers=_supa_hdrs(),
                          data=json.dumps(row, default=str), timeout=8)
        if r.status_code not in (200, 201, 204):
            log.warning("cerebro_log HTTP %s: %s", r.status_code, r.text[:150])
    except Exception as exc:
        log.warning("cerebro_log: %s", exc)


# ── Email alert ───────────────────────────────────────────────────────────────

def send_alert_email(subject: str, body: str):
    try:
        if str(_ROOT) not in sys.path:
            sys.path.insert(0, str(_ROOT))
        from sports.clients.velez.velez_scheduler import send_email
        _ART = timezone(timedelta(hours=-3))
        body_html = f"""
<html><body style="margin:0;padding:0;background:#06080b">
<div style="font-family:Arial,sans-serif;background:#06080b;color:#f2ede4;
            padding:20px;max-width:680px;margin:0 auto">
<h2 style="color:#c9a84c;margin-top:0">{subject}</h2>
<p style="color:#f2ede4;white-space:pre-wrap">{body}</p>
<hr style="border-color:#c9a84c44;margin-top:20px">
<p style="color:#9aa0a8;font-size:12px">
Faro Cerebro · Monitor autónomo · protocolfaro@gmail.com<br>
{datetime.now(_ART).strftime('%d/%m/%Y %H:%M')} UTC-3
</p>
</div></body></html>"""
        ok = send_email(CEREBRO_ALERT_EMAIL, f"[Faro Cerebro] {subject}", body_html)
        log.info("cerebro alerta → %s: %s", "OK" if ok else "FAIL", subject)
    except Exception as exc:
        log.error("send_alert_email: %s", exc)


# ── Chequeos base ─────────────────────────────────────────────────────────────

def _check_json() -> tuple[bool, str]:
    vd_path = os.environ.get("FARO_VD_PATH", "")
    if not vd_path:
        # Railway stateless: FARO_VD_PATH no se setea como env var, skip check
        return True, "FARO_VD_PATH no configurado — check omitido (Railway stateless)"
    if not os.path.exists(vd_path):
        return False, f"FARO_VD_PATH apunta a archivo inexistente: {vd_path!r}"
    age_h = (time.time() - os.path.getmtime(vd_path)) / 3600
    if age_h > _MAX_JSON_AGE_H:
        return False, f"JSON tiene {age_h:.1f}h de antigüedad (límite {_MAX_JSON_AGE_H}h)"
    return True, f"JSON OK — {age_h:.1f}h"


def _check_health() -> tuple[bool, str]:
    try:
        r = requests.get(f"{_BASE_URL}/health", timeout=10)
        if r.status_code == 200:
            return True, "health OK"
        return False, f"/health HTTP {r.status_code}"
    except Exception as exc:
        return False, f"/health error: {exc}"


def _check_pngs() -> tuple[bool, str]:
    missing = [p for p in _EXPECTED_PNGS if not (_PNG_DIR / p).exists()]
    if missing:
        return False, f"PNGs faltantes: {missing}"
    return True, f"PNGs OK ({len(_EXPECTED_PNGS)} archivos)"


_RUN_NOW_COOLDOWN_H = 2  # máximo 1 run_now cada 2 horas


def _run_now_on_cooldown() -> bool:
    try:
        with sqlite3.connect(DB_PATH) as conn:
            row = conn.execute(
                "SELECT timestamp FROM short_term_memory WHERE task='run_now_fired' "
                "ORDER BY id DESC LIMIT 1"
            ).fetchone()
            if not row:
                return False
            last = datetime.fromisoformat(row[0])
            return (datetime.utcnow() - last).total_seconds() / 3600 < _RUN_NOW_COOLDOWN_H
    except Exception:
        return False


def _record_run_now_fired():
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "INSERT INTO short_term_memory (timestamp, task, status) VALUES (?,?,?)",
            (datetime.utcnow().isoformat(), "run_now_fired", "ok")
        )
        conn.commit()


def _trigger_run_now() -> bool:
    if _run_now_on_cooldown():
        log.info("_trigger_run_now: cooldown activo (%dh) — skip", _RUN_NOW_COOLDOWN_H)
        return False
    try:
        r = requests.post(f"{_BASE_URL}/velez/run_now", timeout=120)
        ok = r.status_code in (200, 201, 202)
        if ok:
            _record_run_now_fired()
        return ok
    except Exception as exc:
        log.warning("_trigger_run_now: %s", exc)
        return False


_ALL_PNGS = [
    "faro_reporte_velez.png",
    "faro_reporte_velez_canchero.png",
    "faro_reporte_velez_agro_FINAL.png",
    "faro_reporte_velez_solar_v2.png",
    "faro_reporte_velez_poli.png",
    "faro_reporte_velez_sede.png",
    "faro_reporte_velez_piletas.png",
    "faro_reporte_velez_instituto.png",
]


def _run_checks() -> list[str]:
    """Corre los 8 chequeos. Retorna lista de alertas no resueltas."""
    alertas: list[str] = []

    # 1 — JSON freshness (con auto-fix)
    ok_json, msg_json = _check_json()
    _log_supabase("check_json", resultado=msg_json, escalado=not ok_json)
    if not ok_json:
        log.warning("Cerebro check_json: %s — intentando run_now", msg_json)
        fired = _trigger_run_now()
        time.sleep(10)
        ok_json2, msg_json2 = _check_json()
        if ok_json2:
            _log_supabase("auto_fix_json", resultado="run_now exitoso")
        else:
            err = msg_json2 if fired else f"run_now falló + {msg_json}"
            alertas.append(err)
            _log_supabase("auto_fix_json_fail", error=err, escalado=True)

    # 2 — /health
    ok_health, msg_health = _check_health()
    _log_supabase("check_health", resultado=msg_health, escalado=not ok_health)
    if not ok_health:
        alertas.append(msg_health)
        log.error("Cerebro check_health: %s", msg_health)

    # 3 — PNGs principales (con auto-fix)
    ok_pngs, msg_pngs = _check_pngs()
    _log_supabase("check_pngs", resultado=msg_pngs, escalado=not ok_pngs)
    if not ok_pngs:
        log.warning("Cerebro check_pngs: %s — intentando run_now", msg_pngs)
        _trigger_run_now()
        time.sleep(10)
        ok_pngs2, msg_pngs2 = _check_pngs()
        if ok_pngs2:
            _log_supabase("auto_fix_pngs", resultado="run_now exitoso")
        else:
            alertas.append(msg_pngs2)
            _log_supabase("auto_fix_pngs_fail", error=msg_pngs2, escalado=True)

    # 4 — Pipeline semanal corrió en los últimos 7 días
    try:
        r = requests.get(f"{_BASE_URL}/velez/weekly_status", timeout=10)
        if r.ok:
            last = r.json().get("last_weekly", {}).get("ran_at", "")
            if last:
                ran   = datetime.fromisoformat(last.replace("Z", "+00:00"))
                horas = (datetime.now(timezone.utc) - ran).total_seconds() / 3600
                if horas > 170:
                    alertas.append(f"Pipeline semanal no corrió hace {horas:.0f}h")
    except Exception as e:
        alertas.append(f"No se pudo verificar pipeline semanal: {e}")

    # 5 — Supabase responde
    try:
        r = requests.get(
            f"{_supa_base()}/rest/v1/faro_cerebro_log?limit=1",
            headers=_supa_hdrs(), timeout=8
        )
        if r.status_code not in (200, 201):
            alertas.append(f"Supabase no responde: HTTP {r.status_code}")
    except Exception as e:
        alertas.append(f"Supabase no responde: {e}")

    # 6 — Panel Roger accesible en GitHub Pages
    try:
        r = requests.get(
            "https://protocolfaro.github.io/faro-paneles/velez/",
            timeout=10
        )
        if r.status_code != 200:
            alertas.append(f"Panel Vélez HTTP {r.status_code}")
    except Exception as e:
        alertas.append(f"Panel Vélez inaccesible: {e}")

    # 7 — velez_data.json en GitHub Pages tiene datos recientes
    # Umbral: 170h (pipeline semanal — datos válidos hasta el próximo lunes)
    try:
        r = requests.get(
            "https://raw.githubusercontent.com/protocolfaro/faro-paneles/main/velez/velez_data.json",
            timeout=10
        )
        if r.ok:
            ts = r.json().get("weather_live", {}).get("timestamp", "")
            if ts:
                t     = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                horas = (datetime.now(timezone.utc) - t).total_seconds() / 3600
                if horas > 170:
                    alertas.append(f"velez_data.json tiene {horas:.0f}h de antigüedad (>170h — pipeline no corrió)")
    except Exception as e:
        alertas.append(f"velez_data.json inaccesible: {e}")

    # 8 — Todos los PNGs del ciclo existen
    # En Railway (stateless) los PNGs se pierden en restart: intentar run_now primero
    missing_pngs = [p for p in _ALL_PNGS if not (_PNG_DIR / p).exists()]
    if missing_pngs:
        log.warning("Cerebro check_pngs (todos): %d faltantes — intentando run_now", len(missing_pngs))
        _trigger_run_now()
        time.sleep(15)
        still_missing = [p for p in missing_pngs if not (_PNG_DIR / p).exists()]
        if still_missing:
            alertas.append(f"{len(still_missing)} PNGs faltantes tras run_now: {still_missing}")

    return alertas


# ── Loop principal con Claude SDK ─────────────────────────────────────────────

def run_cerebro_cycle():
    _init_db()
    skills_context = _load_skills()

    log.info("Faro Cerebro — ciclo inicio")
    alertas = _run_checks()

    if not alertas:
        _record_attempt("ciclo_rutina", "ok")
        _log_supabase("ciclo_rutina", "Sistema nominal")
        log.info("Faro Cerebro — todo OK")
        return

    # Consultar Claude con contexto de skills aprendidas
    try:
        client = anthropic.Anthropic()
        response = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=1000,
            system=f"""Sos el cerebro autónomo de Faro Protocol.
Skills aprendidas disponibles:
{skills_context}

Reglas absolutas:
1. NUNCA mandés emails a destinatarios del club sin aprobación de Emilio
2. NUNCA modifiques código en main directamente — siempre PR
3. SÍ podés: correr refreshes, regenerar PNGs, abrir PRs, mandar alertas a protocolfaro@gmail.com""",
            messages=[{"role": "user",
                       "content": f"Alertas detectadas: {alertas}. ¿Qué acción tomamos?"}]
        )
        decision = response.content[0].text
        log.info("Cerebro decisión: %s", decision[:200])
    except Exception as exc:
        decision = f"(Claude SDK no disponible: {exc})"
        log.warning("Cerebro Claude SDK: %s", exc)

    # DGM: si alguna tarea falló 3+ veces → generar fix y proponer PR
    for alerta in alertas:
        task_key = alerta[:50]
        attempts = _get_attempts(task_key)
        if attempts >= 3:
            log.info("Cerebro DGM: %d intentos para '%s' — generando fix", attempts, task_key)
            try:
                fix_client = anthropic.Anthropic()
                fix_response = fix_client.messages.create(
                    model="claude-sonnet-4-6",
                    max_tokens=2000,
                    system="Generá un fix de Python para el siguiente bug. Solo código, sin explicaciones.",
                    messages=[{"role": "user", "content": f"Bug que falla {attempts} veces: {alerta}"}]
                )
                fix_code = fix_response.content[0].text

                # Validar sintaxis antes de proponer
                with tempfile.NamedTemporaryFile(suffix=".py", mode="w", delete=False) as tmp:
                    tmp.write(fix_code)
                    tmp_path = tmp.name
                try:
                    py_compile.compile(tmp_path, doraise=True)
                    # Fix válido → proponer PR
                    pr_url = _propose_fix_via_pr(
                        "railway-backend/sports/clients/velez/gen_velez_solar_v2.py",
                        fix_code,
                        alerta
                    )
                    if pr_url:
                        send_alert_email(
                            f"Fix generado para: {task_key}",
                            f"El cerebro propuso un fix para un bug que falló {attempts} veces.\n\n"
                            f"Bug: {alerta}\n\nRevisá y aprobá el PR:\n{pr_url}"
                        )
                        _save_skill(f"fix_{task_key[:20].replace(' ','_')}", fix_code, alerta)
                        _log_supabase("dgm_pr_creado", resultado=pr_url, escalado=True)
                    _record_attempt(task_key, "fix_propuesto")
                except py_compile.PyCompileError as pce:
                    log.warning("Cerebro DGM: fix sintácticamente inválido — %s", pce)
                    _record_attempt(task_key, "failed")
                finally:
                    try:
                        os.unlink(tmp_path)
                    except Exception:
                        pass
            except Exception as exc:
                log.error("Cerebro DGM cycle: %s", exc)
                _record_attempt(task_key, "failed")
        else:
            _record_attempt(task_key, "failed")

    # Escalar al humano con decisión de Claude (cooldown: 1 email cada 6h)
    body = "\n".join(f"• {a}" for a in alertas)
    body += f"\n\nDecisión del cerebro:\n{decision}"
    if _alert_on_cooldown():
        log.info("Faro Cerebro: alerta suprimida por cooldown (%dh) — %d issue(s)", _ALERT_COOLDOWN_H, len(alertas))
        _log_supabase("ciclo_con_alertas", resultado=f"[cooldown] {decision[:400]}", escalado=False)
    else:
        send_alert_email("Alerta del sistema", body)
        _record_alert_sent()
        _log_supabase("ciclo_con_alertas", resultado=decision[:500], escalado=True)
        log.error("Faro Cerebro: %d issue(s) escalado(s)", len(alertas))


# ── APScheduler registration ──────────────────────────────────────────────────

def register_cerebro_job(scheduler) -> None:
    scheduler.add_job(
        run_cerebro_cycle,
        "interval",
        minutes=60,
        id="faro_cerebro",
        replace_existing=True,
        misfire_grace_time=1800,
    )
    log.info("Faro Cerebro registrado (cada 60 min)")
