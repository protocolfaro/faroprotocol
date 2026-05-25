"""
send_emails_weekly.py — Envío semanal de emails Vélez Sarsfield
Ejecutar manualmente o vía Windows Task Scheduler (cada lunes 07:00 ART).

Lee config y datos desde GitHub (faro-paneles) — no depende de archivos locales.
Usa SMTP de Gmail directamente (funciona desde PC local, no desde Railway).

Requiere: GMAIL_APP_PASS en variables de entorno o en .env junto a este archivo.
"""
import json, logging, os, smtplib, sys
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from urllib.request import urlopen, Request as UReq

# ── Cargar .env si existe ────────────────────────────────────────────────────
_env_file = Path(__file__).parent / ".env"
if _env_file.exists():
    for line in _env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip())

# ── Config ───────────────────────────────────────────────────────────────────
GMAIL_USER    = os.environ.get("GMAIL_USER", "protocolfaro@gmail.com")
GMAIL_PASS    = os.environ.get("GMAIL_APP_PASS", "")
PANEL_BASE    = "https://protocolfaro.github.io/faro-paneles/velez/"
_CFG_URL      = "https://raw.githubusercontent.com/protocolfaro/faro-paneles/main/velez/config_velez.json"
_VD_URL       = "https://raw.githubusercontent.com/protocolfaro/faro-paneles/main/velez/velez_data.json"

# ── Logging ──────────────────────────────────────────────────────────────────
log_path = Path(__file__).parent / "email_weekly.log"
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.FileHandler(log_path, encoding="utf-8"), logging.StreamHandler()],
)
log = logging.getLogger("weekly")

# ── Helpers ──────────────────────────────────────────────────────────────────
_SEM_COLOR = {"verde": "#27ae60", "amarillo": "#f0b429", "rojo": "#e74c3c"}
_SEM_LABEL = {"verde": "ÓPTIMO",  "amarillo": "ATENCIÓN", "rojo": "CRÍTICO"}


def _fetch(url: str) -> dict:
    req = UReq(url, headers={"User-Agent": "FaroProtocol/4.0"})
    with urlopen(req, timeout=15) as r:
        return json.loads(r.read())


def _html_wrap(title: str, body: str, panel_url: str = "") -> str:
    link = (f'<p style="color:#c9a84c;font-size:12px">Panel: '
            f'<a href="{panel_url}" style="color:#c9a84c">{panel_url}</a></p>') if panel_url else ""
    return (f'<html><body style="font-family:Arial,sans-serif;background:#06080b;'
            f'color:#f2ede4;padding:20px">'
            f'<h2 style="color:#c9a84c">{title}</h2>{body}{link}'
            f'<hr style="border-color:#c9a84c44">'
            f'<p style="color:#9aa0a8;font-size:12px">Faro Protocol · '
            f'{datetime.now().strftime("%d/%m/%Y %H:%M")} · protocolfaro@gmail.com</p>'
            f'</body></html>')


def _sector_row(s: dict, k: str) -> str:
    sem = s.get("sem", "verde")
    return (f'<tr><td style="padding:6px;border:1px solid #c9a84c22">{s.get("nombre", k)}</td>'
            f'<td style="padding:6px;color:{_SEM_COLOR.get(sem,"#27ae60")};border:1px solid #c9a84c22">'
            f'<b>{_SEM_LABEL.get(sem,"—")}</b> ({s.get("score","—")}/100)</td>'
            f'<td style="padding:6px;border:1px solid #c9a84c22;font-size:13px">'
            f'{s.get("detalle","—")}</td></tr>')


def _table(rows_html: str) -> str:
    return (f'<table style="color:#f2ede4;border-collapse:collapse;width:100%;font-size:14px">'
            f'<tr style="background:#141c24">'
            f'<th style="padding:6px;border:1px solid #c9a84c44">Área</th>'
            f'<th style="padding:6px;border:1px solid #c9a84c44">Estado</th>'
            f'<th style="padding:6px;border:1px solid #c9a84c44">Detalle</th>'
            f'</tr>{rows_html}</table>')


# ── Cuerpos de email (reutilizados de velez_scheduler Railway) ────────────────
def _body(vd: dict, nombre: str, sectores_keys: list, panel_url: str) -> str:
    sec  = vd.get("sectores", {})
    rows = "".join(_sector_row(sec.get(k, {}), k) for k in sectores_keys if k in sec)
    wl   = vd.get("weather_live", {})
    fung = wl.get("riesgo_fungosis", {})
    fung_niv = fung.get("nivel", "bajo")
    fung_acc = fung.get("accion_recomendada", "")
    fung_col = _SEM_COLOR.get(fung_niv, "#27ae60")
    extra = (f'<p><b>Riesgo fungoso:</b> '
             f'<span style="color:{fung_col}">{fung_niv.upper()}</span>'
             f'{(" — " + fung_acc) if fung_acc else ""}</p>')
    body = f"<p>{nombre}, estado semanal del predio.</p>" + _table(rows) + extra
    return _html_wrap(f"Informe Semanal Vélez · {datetime.now().strftime('%d/%m/%Y')}", body, panel_url)


_SLUG = {
    "Roger Bernal":      ("roger",    ["canchero"]),
    "Juan González":     ("juan",     ["canchero", "agro", "poli"]),
    "Fernando Banchero": ("banchero", ["estadio","agro","solar","canchero","sede","poli","piletas"]),
    "Sebastián Pait":    ("pait",     ["canchero", "poli"]),
    "Fabián Berlanga":   ("berlanga", ["estadio","agro","solar","canchero","sede","poli","piletas"]),
    "Nelson Pugliese":   ("nelson",   ["estadio","agro","solar","canchero","sede","poli","piletas"]),
    "Alberto Aveleyra":  ("aveleyra", ["estadio","agro","solar","canchero","sede","poli","piletas"]),
}


# ── SMTP ──────────────────────────────────────────────────────────────────────
def send_email(to: str, subject: str, body_html: str) -> bool:
    if not GMAIL_PASS:
        log.error("GMAIL_APP_PASS no configurada — abortando")
        return False
    try:
        msg = MIMEMultipart("mixed")
        msg["From"]    = GMAIL_USER
        msg["To"]      = to
        msg["Subject"] = subject
        msg.attach(MIMEText(body_html, "html"))
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=30) as server:
            server.login(GMAIL_USER, GMAIL_PASS)
            server.sendmail(GMAIL_USER, [to], msg.as_string())
        log.info("OK → %s", to)
        return True
    except Exception as e:
        log.error("FAIL → %s : %s", to, e)
        return False


# ── Job principal ─────────────────────────────────────────────────────────────
def run():
    log.info("=== Envío semanal iniciado ===")
    if not GMAIL_PASS:
        log.error("GMAIL_APP_PASS no encontrada. Agregala al .env o como variable de entorno.")
        sys.exit(1)

    try:
        config = _fetch(_CFG_URL)
        vd     = _fetch(_VD_URL)
    except Exception as e:
        log.error("Error al leer datos de GitHub: %s", e)
        sys.exit(1)

    dest     = config.get("destinatarios", [])
    date_str = datetime.now().strftime("%d/%m/%Y")
    sent, failed = 0, 0

    for d in dest:
        nombre = d.get("nombre", "")
        email  = d.get("email", "")
        if not nombre or not email:
            continue
        slug, sectores = _SLUG.get(nombre, (nombre.lower().split()[0], ["canchero"]))
        panel_url      = f"{PANEL_BASE}#{slug}"
        body_html      = _body(vd, nombre.split()[0], sectores, panel_url)
        subject        = f"Faro · Reporte semanal · Vélez · {date_str}"
        ok = send_email(email, subject, body_html)
        if ok: sent += 1
        else:  failed += 1

    log.info("=== Listo: %d enviados · %d fallidos · %d total ===", sent, failed, sent + failed)
    return sent, failed


if __name__ == "__main__":
    run()
