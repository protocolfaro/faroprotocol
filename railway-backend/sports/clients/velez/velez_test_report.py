"""
velez_test_report.py — genera PNGs _TEST con datos actuales de Supabase y los manda por mail.

- Lee datos live via faro_assembler.assemble_report()
- Escribe PNGs en tmp/ con sufijo _TEST (nunca toca reportes_velez/)
- Envía email a protocolfaro@gmail.com con los 8 adjuntos
- Funciona como script standalone Y como ruta Flask (run_test_report())
"""
from __future__ import annotations

import base64
import json
import logging
import os
import subprocess
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

import requests

log = logging.getLogger(__name__)

_HERE       = Path(__file__).parent
_RESEND_KEY = lambda: os.environ.get("RESEND_API_KEY", "")
_RESEND_FROM = lambda: os.environ.get("RESEND_FROM", "Faro Protocol <onboarding@resend.dev>")
_TEST_TO    = "protocolfaro@gmail.com"

# (script, _TEST output filename)
_GEN_SCRIPTS: list[tuple[str, str]] = [
    ("gen_velez_main.py",      "faro_reporte_velez_TEST.png"),
    ("gen_velez_canchero.py",  "faro_reporte_velez_canchero_TEST.png"),
    ("gen_velez_final.py",     "faro_reporte_velez_agro_FINAL_TEST.png"),
    ("gen_velez_solar_v2.py",  "faro_reporte_velez_solar_v2_TEST.png"),
    ("gen_velez_poli.py",      "faro_reporte_velez_poli_TEST.png"),
    ("gen_velez_sede.py",      "faro_reporte_velez_sede_TEST.png"),
    ("gen_velez_piletas.py",   "faro_reporte_velez_piletas_TEST.png"),
    ("gen_velez_instituto.py", "faro_reporte_velez_instituto_TEST.png"),
]


# ── Data ──────────────────────────────────────────────────────────────────────

def _get_live_data() -> dict:
    """Llama a faro_assembler igual que el scheduler real."""
    here = str(_HERE)
    if here not in sys.path:
        sys.path.insert(0, here)
    try:
        from faro_assembler import assemble_report
        vd = assemble_report("amalfitani")
        log.info("velez_test_report: datos obtenidos de faro_assembler OK")
        return vd
    except Exception as e:
        log.warning("velez_test_report: faro_assembler falló (%s) — usando dict vacío", e)
        return {}


# ── Render ────────────────────────────────────────────────────────────────────

def _render_test_pngs(vd: dict, out_dir: Path) -> list[tuple[str, Path]]:
    """
    Corre cada gen script con FARO_VD_PATH + FARO_OUT_PATH apuntando a out_dir/_TEST.png.
    Retorna lista de (nombre_archivo, path) para los PNGs que se generaron correctamente.
    """
    vd_tmp_path: str | None = None
    results: list[tuple[str, Path]] = []

    try:
        # Volcar vd a temp JSON
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False, encoding="utf-8"
        ) as tf:
            json.dump(vd, tf, ensure_ascii=False, default=str)
            vd_tmp_path = tf.name

        for script, png_name in _GEN_SCRIPTS:
            script_path = _HERE / script
            if not script_path.exists():
                log.warning("velez_test_report: script no encontrado: %s", script)
                continue

            out_png = out_dir / png_name
            env = {
                **os.environ,
                "FARO_VD_PATH":  vd_tmp_path,
                "FARO_OUT_PATH": str(out_png),
            }

            t0 = time.time()
            try:
                proc = subprocess.run(
                    [sys.executable, str(script_path)],
                    env=env,
                    capture_output=True,
                    text=True,
                    timeout=120,
                )
                elapsed = round(time.time() - t0, 1)
                if proc.returncode == 0 and out_png.exists():
                    results.append((png_name, out_png))
                    log.info("velez_test_report: %s OK (%.1fs)", script, elapsed)
                else:
                    log.warning(
                        "velez_test_report: %s FAIL rc=%d (%.1fs)\n%s",
                        script, proc.returncode, elapsed, proc.stderr[-400:],
                    )
            except subprocess.TimeoutExpired:
                log.warning("velez_test_report: %s timeout (>120s)", script)
            except Exception as ex:
                log.warning("velez_test_report: %s excepción: %s", script, ex)

    finally:
        if vd_tmp_path:
            try:
                os.unlink(vd_tmp_path)
            except Exception:
                pass

    return results


# ── Email ─────────────────────────────────────────────────────────────────────

def _send_test_email(png_files: list[tuple[str, Path]], fecha: str) -> bool:
    key = _RESEND_KEY()
    if not key:
        log.error("velez_test_report: RESEND_API_KEY no configurado")
        return False

    n = len(png_files)
    subject = f"[TEST] Faro · Vélez · Reportes visuales {fecha} ({n} adjuntos)"

    # Lista de PNGs generados para el cuerpo
    png_list_html = "".join(
        f"<li style='margin:4px 0'>{name}</li>" for name, _ in png_files
    )
    body_html = f"""
<div style="background:#111;color:#eee;font-family:Georgia,serif;padding:28px 32px;max-width:680px">
  <h2 style="color:#c9a84c;margin:0 0 12px">Faro Protocol · TEST de reportes visuales</h2>
  <p style="color:#aaa;font-size:13px;margin:0 0 18px">
    Fecha de generación: <strong style="color:#eee">{fecha}</strong> ·
    Datos: <strong style="color:#eee">Supabase live (faro_assembler)</strong>
  </p>
  <p style="color:#ccc;font-size:14px">
    Se adjuntan <strong style="color:#c9a84c">{n} PNGs</strong>
    generados con datos actuales. Sufijo <code style="color:#c9a84c">_TEST</code> —
    no afectan los reportes reales en <code>reportes_velez/</code>.
  </p>
  <ul style="color:#ccc;font-size:13px;padding-left:20px;margin:12px 0 20px">
    {png_list_html}
  </ul>
  <p style="color:#888;font-size:11px;margin:0">
    Faro Protocol · Sistema de monitoreo satelital · protocolfaro.com
  </p>
</div>
"""

    attachments = []
    for name, path in png_files:
        try:
            data = path.read_bytes()
            attachments.append({"filename": name, "content": base64.b64encode(data).decode()})
        except Exception as ex:
            log.warning("velez_test_report: no se pudo leer %s: %s", name, ex)

    payload = {
        "from":        _RESEND_FROM(),
        "to":          [_TEST_TO],
        "subject":     subject,
        "html":        body_html,
        "attachments": attachments,
    }

    try:
        r = requests.post(
            "https://api.resend.com/emails",
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            json=payload,
            timeout=60,
        )
        if r.status_code in (200, 201):
            log.info("velez_test_report: email OK → %s (%d adjuntos)", _TEST_TO, len(attachments))
            return True
        log.error("velez_test_report: Resend HTTP %s: %s", r.status_code, r.text[:300])
        return False
    except Exception as ex:
        log.error("velez_test_report: Resend excepción: %s", ex)
        return False


# ── Entry point ───────────────────────────────────────────────────────────────

def run_test_report() -> dict:
    """
    Callable desde Flask route o directamente.
    Retorna dict con resultado para jsonify().
    """
    fecha = datetime.now().strftime("%d/%m/%Y %H:%M")
    log.info("velez_test_report: iniciando — %s", fecha)

    # 1. Datos live
    vd = _get_live_data()

    # 2. Render en tmp dir
    with tempfile.TemporaryDirectory(prefix="faro_test_") as tmp:
        out_dir = Path(tmp)
        png_files = _render_test_pngs(vd, out_dir)

        if not png_files:
            log.error("velez_test_report: 0 PNGs generados — abortando email")
            return {"ok": False, "error": "0 PNGs generados", "pngs": 0}

        log.info("velez_test_report: %d/%d PNGs generados", len(png_files), len(_GEN_SCRIPTS))

        # 3. Email con adjuntos (dentro del with para que los paths existan)
        ok = _send_test_email(png_files, fecha)

    return {
        "ok":      ok,
        "pngs":    len(png_files),
        "total":   len(_GEN_SCRIPTS),
        "to":      _TEST_TO,
        "fecha":   fecha,
        "adjuntos": [name for name, _ in png_files],
    }


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    result = run_test_report()
    print(json.dumps(result, ensure_ascii=False, indent=2))
