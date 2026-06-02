"""
dale_play_scheduler.py — Auto-actualización post-show Dale Play.

Cada 6 horas después de un post_show, verifica si hay datos mejores disponibles.
Cuando el nivel del Chain of Custody mejora → regenera PNG → nuevo SHA-256
→ push GitHub → email al cliente.

Jerarquía de niveles (ascendente):
  SIN_DATOS < RADAR_PURO < BRONZE < SILVER < GOLD

Se cancela automáticamente tras 7 días o al alcanzar GOLD.

Variables de entorno:
  GMAIL_USER      — cuenta remitente (ej: protocolfaro@gmail.com)
  EMAIL_APP_PASS  — App Password Gmail (16 chars)
  DALE_PLAY_CLIENT_EMAIL — email del cliente por defecto
"""
from __future__ import annotations
import hashlib, json, logging, os, pathlib, time
from datetime import datetime, timezone, timedelta
from typing import Optional

log = logging.getLogger(__name__)

_MODELS_DIR = pathlib.Path(__file__).parent / "models"
_SHOWS_DIR  = pathlib.Path(__file__).parent / "shows"

# Orden de niveles (mayor = mejor)
_NIVEL_RANK = {"SIN_DATOS": 0, "RADAR_PURO": 1, "BRONZE": 2, "OPTICAL_ONLY": 3, "SILVER": 4, "GOLD": 5}

# Intervalo de verificación
_INTERVAL_H   = 6
_MAX_DAYS     = 7

# Estado de monitores activos: {show_id: {start_ts, coc_nivel, n_checks, job_id}}
_active: dict[str, dict] = {}


# ─────────────────────────────────────────────────────────────────────────────
# Scheduler singleton
# ─────────────────────────────────────────────────────────────────────────────

_sched = None   # instancia APScheduler (inyectada desde app.py o creada aquí)


def init_scheduler(external_scheduler=None):
    """
    Inicializa el scheduler. Si app.py pasa su instancia, la reutiliza.
    Si no, crea una propia. Llamar desde app.py al inicio.
    """
    global _sched
    if external_scheduler is not None:
        _sched = external_scheduler
        log.info("dale_play_scheduler: usando scheduler externo")
        return _sched

    if _sched is not None:
        return _sched

    try:
        from apscheduler.schedulers.background import BackgroundScheduler
        _sched = BackgroundScheduler(timezone="UTC")
        _sched.start()
        log.info("dale_play_scheduler: BackgroundScheduler iniciado (instancia propia)")
    except Exception as exc:
        log.warning("dale_play_scheduler: no se pudo iniciar scheduler: %s", exc)
    return _sched


def _get_scheduler():
    global _sched
    if _sched is None:
        init_scheduler()
    return _sched


# ─────────────────────────────────────────────────────────────────────────────
# Registro de show en monitoreo
# ─────────────────────────────────────────────────────────────────────────────

def schedule_source_scout(external_scheduler=None) -> None:
    """
    Registra el job semanal de auto-mejora de fuentes satelitales.
    Corre los lunes a las 11:00 UTC (después del weekly_insar_refresh de las 10:00).
    Llamar desde app.py al iniciar, junto con init_scheduler().
    """
    sched = external_scheduler or _get_scheduler()
    if sched is None:
        log.warning("dale_play_scheduler: schedule_source_scout — scheduler no disponible")
        return

    try:
        sched.add_job(
            _run_source_scout_job,
            "cron",
            day_of_week="mon",
            hour=11,
            minute=0,
            id="dp_source_scout_semanal",
            replace_existing=True,
            max_instances=1,
            misfire_grace_time=7200,   # 2h de gracia
        )
        log.info("dale_play_scheduler: source_scout semanal registrado (lunes 11:00 UTC)")
    except Exception as exc:
        log.warning("dale_play_scheduler: schedule_source_scout add_job: %s", exc)


def _run_source_scout_job() -> None:
    """Wrapper para APScheduler — captura cualquier excepción y loguea."""
    log.info("dale_play_scheduler: _run_source_scout_job — inicio")
    try:
        from dale_play_source_scout import weekly_source_scout_job
        weekly_source_scout_job()
    except Exception as exc:
        log.error("dale_play_scheduler: _run_source_scout_job error: %s", exc, exc_info=True)


def schedule_post_show_updates(
    show_id:    str,
    show_date:  str,
    show_config: dict,
    coc_nivel:  str = "SIN_DATOS",
    client_email: str = "",
) -> None:
    """
    Registra un job de monitoreo cada 6h para show_id.
    Llamado automáticamente al final de run_show_audit(mode='post_show').
    """
    if show_id in _active:
        log.info("dale_play_scheduler: %s ya tiene monitor activo", show_id)
        return

    if coc_nivel == "GOLD":
        log.info("dale_play_scheduler: %s ya es GOLD — no se necesita monitoreo", show_id)
        return

    sched = _get_scheduler()
    if sched is None:
        log.warning("dale_play_scheduler: scheduler no disponible")
        return

    if not client_email:
        client_email = os.environ.get("DALE_PLAY_CLIENT_EMAIL", "")

    _active[show_id] = {
        "start_ts":     time.time(),
        "show_config":  show_config,
        "show_date":    show_date,
        "coc_nivel":    coc_nivel,
        "n_checks":     0,
        "client_email": client_email,
    }

    job_id = f"dp_postshow_{show_id}"
    try:
        sched.add_job(
            _check_and_upgrade,
            "interval",
            hours=_INTERVAL_H,
            args=[show_id],
            id=job_id,
            replace_existing=True,
            max_instances=1,
            misfire_grace_time=3600,
        )
        log.info(
            "dale_play_scheduler: monitor %s registrado (c/6h, máx %d días, nivel=%s)",
            show_id, _MAX_DAYS, coc_nivel,
        )
    except Exception as exc:
        log.warning("dale_play_scheduler: add_job failed: %s", exc)
        _active.pop(show_id, None)


# ─────────────────────────────────────────────────────────────────────────────
# Job de verificación periódica
# ─────────────────────────────────────────────────────────────────────────────

def _check_and_upgrade(show_id: str) -> None:
    """
    Corre cada 6h: verifica si hay datos mejores disponibles.
    Si el nivel CoC mejora → regenera PNG → sha256 → push → email.
    Se cancela si alcanza GOLD o supera 7 días.
    """
    state = _active.get(show_id)
    if state is None:
        return

    state["n_checks"] += 1
    elapsed_days = (time.time() - state["start_ts"]) / 86400

    log.info("dale_play_scheduler: check #%d para %s (día %.1f/7)",
             state["n_checks"], show_id, elapsed_days)

    # ── Auto-cancelación ─────────────────────────────────────────────────────
    if elapsed_days > _MAX_DAYS or state.get("coc_nivel") == "GOLD":
        _cancel_monitor(show_id)
        return

    # ── Re-correr pipeline en modo post_show ─────────────────────────────────
    try:
        from dale_play_pipeline import run_show_audit
        show_config = state.get("show_config") or {}
        if not show_config:
            show_id_file = _SHOWS_DIR / f"{show_id}.json"
            if show_id_file.exists():
                show_config = json.loads(show_id_file.read_text(encoding="utf-8"))
            else:
                log.warning("dale_play_scheduler: show_config no disponible para %s", show_id)
                return

        result = run_show_audit(show_config, mode="post_show")
        new_coc = (result.get("chain_of_custody") or {}).get("nivel", "SIN_DATOS")

    except Exception as exc:
        log.warning("dale_play_scheduler: re-run failed para %s: %s", show_id, exc)
        return

    prev_nivel = state.get("coc_nivel", "SIN_DATOS")
    if _NIVEL_RANK.get(new_coc, 0) > _NIVEL_RANK.get(prev_nivel, 0):
        log.info("dale_play_scheduler: %s MEJORÓ %s → %s", show_id, prev_nivel, new_coc)
        state["coc_nivel"] = new_coc

        # ── Calcular SHA-256 del nuevo PNG ───────────────────────────────────
        png_path = pathlib.Path(__file__).parent / "reportes" / f"reporte_{show_id}.png"
        sha256   = _sha256_file(str(png_path)) if png_path.exists() else None

        # ── Email al cliente ─────────────────────────────────────────────────
        client_email = state.get("client_email") or ""
        if client_email:
            _send_upgrade_email(
                to_email   = client_email,
                show_id    = show_id,
                nivel_prev = prev_nivel,
                nivel_new  = new_coc,
                sha256     = sha256,
                png_url    = result.get("report_png_url"),
            )

        if new_coc == "GOLD":
            _cancel_monitor(show_id)
    else:
        log.info("dale_play_scheduler: %s sin mejora (sigue en %s)", show_id, prev_nivel)


def _cancel_monitor(show_id: str) -> None:
    sched = _get_scheduler()
    job_id = f"dp_postshow_{show_id}"
    try:
        if sched:
            sched.remove_job(job_id)
    except Exception:
        pass
    _active.pop(show_id, None)
    log.info("dale_play_scheduler: monitor cancelado para %s", show_id)


# ─────────────────────────────────────────────────────────────────────────────
# Utilidades
# ─────────────────────────────────────────────────────────────────────────────

def _sha256_file(path: str) -> Optional[str]:
    try:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
        return h.hexdigest().upper()
    except Exception:
        return None


def _send_upgrade_email(
    to_email:   str,
    show_id:    str,
    nivel_prev: str,
    nivel_new:  str,
    sha256:     Optional[str],
    png_url:    Optional[str],
) -> None:
    """Envía email notificando mejora en el nivel del reporte."""
    try:
        gmail_user = os.environ.get("GMAIL_USER", "protocolfaro@gmail.com")
        app_pass   = os.environ.get("EMAIL_APP_PASS", "")
        if not app_pass:
            log.info("dale_play_scheduler: EMAIL_APP_PASS no configurado — no se envía email")
            return

        subject = f"[Faro Protocol] Reporte actualizado: nivel {nivel_new} — {show_id}"
        ts_str  = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

        nivel_color = {"GOLD": "#c9a84c", "SILVER": "#9aa0a8",
                       "BRONZE": "#cd7f32", "RADAR_PURO": "#4a90d9"}.get(nivel_new, "#f2ede4")

        sha_line = (
            f"<p><b>SHA-256 nuevo reporte:</b><br>"
            f"<code style='font-size:11px;color:#9aa0a8'>{sha256}</code></p>"
        ) if sha256 else ""

        url_line = (
            f"<p><b>Reporte:</b> <a href='{png_url}' style='color:{nivel_color}'>"
            f"Ver PNG actualizado</a></p>"
        ) if png_url else ""

        html = f"""
<html><body style="font-family:Arial,sans-serif;background:#07110a;color:#f2ede4;padding:24px">
<h2 style="color:{nivel_color}">Reporte actualizado: nivel {nivel_new}</h2>
<p>El reporte post-show de <b>{show_id}</b> ha sido actualizado con nuevos datos satelitales.</p>
<table style="border-collapse:collapse;margin:16px 0">
  <tr>
    <td style="padding:6px 16px 6px 0;color:#9aa0a8">Nivel anterior:</td>
    <td style="padding:6px 0"><b>{nivel_prev}</b></td>
  </tr>
  <tr>
    <td style="padding:6px 16px 6px 0;color:#9aa0a8">Nivel actual:</td>
    <td style="padding:6px 0"><b style="color:{nivel_color}">{nivel_new}</b></td>
  </tr>
  <tr>
    <td style="padding:6px 16px 6px 0;color:#9aa0a8">Actualizado:</td>
    <td style="padding:6px 0">{ts_str}</td>
  </tr>
</table>
{sha_line}
{url_line}
<hr style="border-color:#c9a84c44;margin:24px 0">
<p style="color:#9aa0a8;font-size:11px">
  Faro Protocol · Sistema de auditoría satelital para eventos masivos<br>
  <a href="mailto:protocolfaro@gmail.com" style="color:#c9a84c">protocolfaro@gmail.com</a>
</p>
</body></html>"""

        import smtplib
        from email.mime.multipart import MIMEMultipart
        from email.mime.text import MIMEText

        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"]    = gmail_user
        msg["To"]      = to_email
        msg.attach(MIMEText(html, "html"))

        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as srv:
            srv.login(gmail_user, app_pass)
            srv.sendmail(gmail_user, to_email, msg.as_string())

        log.info("dale_play_scheduler: email enviado a %s (%s → %s)",
                 to_email, nivel_prev, nivel_new)
    except Exception as exc:
        log.warning("dale_play_scheduler: email failed: %s", exc)


# ─────────────────────────────────────────────────────────────────────────────
# Estado público (para endpoint /dale-play/scheduler-status)
# ─────────────────────────────────────────────────────────────────────────────

def get_active_monitors() -> dict:
    now = time.time()
    return {
        sid: {
            "show_date":    info.get("show_date"),
            "coc_nivel":    info.get("coc_nivel"),
            "n_checks":     info.get("n_checks"),
            "elapsed_h":    round((now - info.get("start_ts", now)) / 3600, 1),
            "max_days":     _MAX_DAYS,
            "interval_h":   _INTERVAL_H,
        }
        for sid, info in _active.items()
    }
