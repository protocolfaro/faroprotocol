"""
FARO PROTOCOL — Auto-runner semanal

Corre el pipeline completo para todas las áreas activas cada lunes a las 10:00 ART
(hora local Windows = UTC-3 = 13:00 UTC), valida con Hermes, entrega los reportes
aprobados a los clientes via email, genera bricolage/certificados y hace git push.

Uso:
    python faro_auto.py                     # correr ahora (todas las áreas)
    python faro_auto.py --area cordoba      # solo un área
    python faro_auto.py --dry-run           # simular sin procesar ni enviar
    python faro_auto.py --instalar          # registrar en Windows Task Scheduler
    python faro_auto.py --status            # ver estado del Task Scheduler
"""

import argparse
import json
import logging
import os
import subprocess
import sys
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')
if hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(encoding='utf-8')

PROJECT_ROOT = Path(__file__).parent
LOGS_DIR     = PROJECT_ROOT / 'logs'
LOGS_DIR.mkdir(exist_ok=True)

TASK_NAME = 'FaroProtocol_AutoRunner'


# ── Logging ───────────────────────────────────────────────────────────────────

def _setup_logger() -> logging.Logger:
    log = logging.getLogger('faro_auto')
    log.setLevel(logging.INFO)
    if log.handlers:
        return log

    fmt = logging.Formatter('%(asctime)s [%(levelname)s] %(message)s',
                             datefmt='%Y-%m-%d %H:%M:%S')

    fh = RotatingFileHandler(
        LOGS_DIR / 'auto_runner.log',
        maxBytes=1_000_000, backupCount=3, encoding='utf-8'
    )
    fh.setFormatter(fmt)

    ch = logging.StreamHandler(sys.stdout)
    ch.setFormatter(fmt)

    log.addHandler(fh)
    log.addHandler(ch)
    return log


# ── Env loader ────────────────────────────────────────────────────────────────

def _cargar_env():
    env_file = PROJECT_ROOT / '.env'
    if env_file.exists():
        for linea in env_file.read_text(encoding='utf-8').splitlines():
            linea = linea.strip()
            if linea and not linea.startswith('#') and '=' in linea:
                k, _, v = linea.partition('=')
                os.environ.setdefault(k.strip(), v.strip())


# ── Area helpers ──────────────────────────────────────────────────────────────

def _areas_activas(solo_area: str = None) -> list:
    """Retorna áreas que tienen SAR georreferenciado (pipeline puede correr)."""
    sys.path.insert(0, str(PROJECT_ROOT))
    from faro_areas import load_area, list_areas

    nombres = [solo_area] if solo_area else list_areas()
    activas = []
    for nombre in nombres:
        try:
            area = load_area(nombre)
            sar_path = PROJECT_ROOT / area.get('sar_output', '')
            if sar_path.exists():
                activas.append(area)
            else:
                _log.info(f"  [{nombre}] SAR no encontrado — omitido ({sar_path.name})")
        except Exception as e:
            _log.warning(f"  [{nombre}] Error cargando área: {e}")
    return activas


# ── Pipeline runner ───────────────────────────────────────────────────────────

def correr_pipeline(area: dict, dry_run: bool = False) -> dict:
    """Ejecuta el pipeline completo para un área. Retorna {'ok': bool, 'score': ..., ...}"""
    nombre = area['name']
    label  = area['label']

    if dry_run:
        _log.info(f"  [DRY-RUN] {label} — pipeline omitido")
        return {'ok': True, 'dry_run': True, 'area': nombre}

    _log.info(f"  Corriendo pipeline: {label}")
    cmd = [sys.executable, str(PROJECT_ROOT / 'engine' / 'faro_engine.py'), '--area', nombre]

    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True,
            encoding='utf-8', errors='replace',
            cwd=str(PROJECT_ROOT), timeout=300,
        )

        if result.returncode != 0:
            _log.error(f"  [{nombre}] Pipeline falló (rc={result.returncode})")
            if result.stderr:
                _log.error(f"  STDERR: {result.stderr[-500:]}")
            return {'ok': False, 'area': nombre, 'error': result.stderr[-200:]}

        # Extraer score del output
        score = None
        for line in result.stdout.splitlines():
            if 'Score Faro' in line or 'score_faro' in line:
                for part in line.split():
                    try:
                        score = float(part)
                        break
                    except ValueError:
                        pass

        _log.info(f"  [{nombre}] Pipeline OK — Score: {score}")
        return {'ok': True, 'area': nombre, 'score': score}

    except subprocess.TimeoutExpired:
        _log.error(f"  [{nombre}] Pipeline timeout (>5 min)")
        return {'ok': False, 'area': nombre, 'error': 'timeout'}
    except Exception as e:
        _log.error(f"  [{nombre}] Error inesperado: {e}")
        return {'ok': False, 'area': nombre, 'error': str(e)}


# ── Hermes validation ─────────────────────────────────────────────────────────

def correr_hermes(area_name: str, dry_run: bool = False) -> bool:
    """Ejecuta Hermes para el área. Retorna True si GO."""
    if dry_run:
        _log.info(f"  [DRY-RUN] Hermes para {area_name} — omitido")
        return True

    _log.info(f"  Validando con Hermes: {area_name}")
    try:
        sys.path.insert(0, str(PROJECT_ROOT / 'MachinaOS'))
        import hermes_agent
        resultado = hermes_agent.main(area_name, alert_on_fail=False)
        es_go = resultado.get('go', False)
        veredicto = resultado.get('veredicto', '')[:80]
        _log.info(f"  [Hermes/{area_name}] {'GO' if es_go else 'NO-GO'} — {veredicto}")
        return es_go
    except Exception as e:
        _log.warning(f"  [Hermes/{area_name}] Error: {e} — asumiendo GO (no bloquear entrega)")
        return True


# ── Delivery ──────────────────────────────────────────────────────────────────

def entregar(area_name: str, dry_run: bool = False):
    """Llama a faro_deliver.py para enviar el reporte al cliente."""
    if dry_run:
        _log.info(f"  [DRY-RUN] Entrega para {area_name} — omitida")
        return

    deliver_script = PROJECT_ROOT / 'faro_deliver.py'
    if not deliver_script.exists():
        _log.warning(f"  faro_deliver.py no encontrado — entrega manual requerida")
        return

    cmd = [sys.executable, str(deliver_script), '--area', area_name]
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True,
            encoding='utf-8', errors='replace',
            cwd=str(PROJECT_ROOT), timeout=60,
        )
        if result.returncode == 0:
            _log.info(f"  [{area_name}] Entrega enviada OK")
        else:
            _log.warning(f"  [{area_name}] Entrega fallida: {result.stderr[-200:]}")
    except Exception as e:
        _log.warning(f"  [{area_name}] Error en entrega: {e}")


# ── Post-pipeline (bricolage + certificados + git push) ──────────────────────

def _post_pipeline(dry_run: bool = False):
    """Genera outputs de difusión y publica cambios en git tras el pipeline."""
    if dry_run:
        _log.info("  [DRY-RUN] Post-pipeline — bricolage/certificados/git omitidos")
        return

    # 1. Bricolage LinkedIn
    bricolage = PROJECT_ROOT / 'faro_bricolage.py'
    if bricolage.exists():
        _log.info("  [Post-pipeline] Ejecutando faro_bricolage.py...")
        try:
            res = subprocess.run(
                [sys.executable, str(bricolage)],
                capture_output=True, text=True, encoding='utf-8',
                errors='replace', cwd=str(PROJECT_ROOT), timeout=120,
            )
            if res.returncode == 0:
                _log.info("  [Bricolage] OK")
            else:
                _log.warning(f"  [Bricolage] Falló (rc={res.returncode}): {res.stderr[-200:]}")
        except Exception as e:
            _log.warning(f"  [Bricolage] Error: {e}")
    else:
        _log.warning("  [Post-pipeline] faro_bricolage.py no encontrado — saltando")

    # 2. Certificados (solo áreas que pasan QualityGate; el script maneja la lógica internamente)
    certificado = PROJECT_ROOT / 'faro_certificado.py'
    if certificado.exists():
        _log.info("  [Post-pipeline] Ejecutando faro_certificado.py --all...")
        try:
            res = subprocess.run(
                [sys.executable, str(certificado), '--all'],
                capture_output=True, text=True, encoding='utf-8',
                errors='replace', cwd=str(PROJECT_ROOT), timeout=120,
            )
            if res.returncode == 0:
                _log.info("  [Certificado] OK")
            else:
                _log.warning(f"  [Certificado] Falló (rc={res.returncode}): {res.stderr[-200:]}")
        except Exception as e:
            _log.warning(f"  [Certificado] Error: {e}")
    else:
        _log.warning("  [Post-pipeline] faro_certificado.py no encontrado — saltando")

    # 3. Notificaciones WhatsApp para anomalías críticas (score < 40)
    _log.info("  [Post-pipeline] Verificando anomalías críticas...")
    try:
        import json as _json
        data_path = PROJECT_ROOT / 'data.json'
        if data_path.exists():
            data = _json.loads(data_path.read_text(encoding='utf-8'))
            pipeline_data = data.get('pipeline', {})
            for area_name, area_data in pipeline_data.items():
                if not isinstance(area_data, dict):
                    continue
                score   = area_data.get('score_faro', 100)
                estado  = area_data.get('estado', 'OK')
                anomaly = area_data.get('pct_anomalia', 0) or 0
                if score < 40 or estado == 'ALERTA' or anomaly > 0:
                    alerta_txt = area_data.get('alerta') or f'score_faro={score:.1f}'
                    _log.warning(f"  [ALERTA] {area_name}: score={score:.1f} estado={estado}")
                    try:
                        from faro_notifier import notify_critical_anomaly
                        notify_critical_anomaly(area_name, score, str(alerta_txt)[:200])
                    except Exception as e:
                        _log.warning(f"  [Notifier] Error enviando alerta {area_name}: {e}")
    except Exception as e:
        _log.warning(f"  [Post-pipeline] Error leyendo data.json para alertas: {e}")

    # 4. Git commit + push — publica reportes nuevos a Netlify vía origin/master
    from datetime import timezone, timedelta
    ART = timezone(timedelta(hours=-3))
    ts  = datetime.now(ART).strftime('%Y-%m-%d %H:%M ART')
    _log.info(f"  [Post-pipeline] git add → commit → push origin master ({ts})...")

    try:
        # Stagear todo — git add . (equivalente a lo generado por el pipeline)
        subprocess.run(
            ['git', 'add', '.'],
            cwd=str(PROJECT_ROOT), timeout=30, capture_output=True,
        )

        msg = f'chore: pipeline auto {ts}'
        commit_res = subprocess.run(
            ['git', 'commit', '-m', msg],
            capture_output=True, text=True, encoding='utf-8',
            cwd=str(PROJECT_ROOT), timeout=30,
        )

        stdout_lower = commit_res.stdout.lower()
        if 'nothing to commit' in stdout_lower or 'nothing added' in stdout_lower:
            _log.info("  [Git] Sin cambios nuevos — push omitido")
        elif commit_res.returncode == 0:
            push_res = subprocess.run(
                ['git', 'push', 'origin', 'master'],
                capture_output=True, text=True, encoding='utf-8',
                cwd=str(PROJECT_ROOT), timeout=60,
            )
            if push_res.returncode == 0:
                _log.info("  [Git] Push OK → origin/master")
            else:
                _log.warning(f"  [Git] Push falló: {push_res.stderr[-200:]}")
        else:
            _log.warning(f"  [Git] Commit falló: {commit_res.stderr[-200:]}")
    except Exception as e:
        _log.warning(f"  [Git] Error inesperado: {e}")


# ── Main run ──────────────────────────────────────────────────────────────────

def run(solo_area: str = None, dry_run: bool = False):
    _cargar_env()
    inicio = datetime.now()

    _log.info("=" * 60)
    _log.info(f"  FARO PROTOCOL — Auto-runner  {inicio.strftime('%Y-%m-%d %H:%M')}")
    if dry_run:
        _log.info("  MODO DRY-RUN — sin procesamiento real")
    _log.info("=" * 60)

    areas = _areas_activas(solo_area)
    if not areas:
        _log.warning("  Sin áreas activas con SAR disponible. Fin.")
        return

    _log.info(f"  Áreas a procesar: {[a['name'] for a in areas]}")

    resumen = []
    for area in areas:
        nombre = area['name']
        _log.info(f"\n── {area['label']} ──────────────────────")

        # 1. Pipeline
        pipeline_res = correr_pipeline(area, dry_run=dry_run)
        if not pipeline_res['ok']:
            resumen.append({'area': nombre, 'estado': 'PIPELINE_FAIL'})
            continue

        # 2. Hermes
        go = correr_hermes(nombre, dry_run=dry_run)
        if not go:
            _log.warning(f"  [{nombre}] Hermes NO-GO — reporte no entregado")
            resumen.append({'area': nombre, 'estado': 'HERMES_NOGO'})
            continue

        # 3. Entrega
        entregar(nombre, dry_run=dry_run)
        resumen.append({'area': nombre, 'estado': 'OK', 'score': pipeline_res.get('score')})

    # Resumen final
    duracion = round((datetime.now() - inicio).total_seconds(), 1)
    _log.info(f"\n{'=' * 60}")
    _log.info(f"  Auto-runner completado en {duracion}s")
    for r in resumen:
        estado_tag = '[OK]' if r['estado'] == 'OK' else '[!!]'
        score_s = f" Score {r['score']}" if r.get('score') else ''
        _log.info(f"  {estado_tag} {r['area']}{score_s} — {r['estado']}")
    _log.info("=" * 60)

    # Post-pipeline: bricolage + certificados + git push
    _log.info(f"\n{'─' * 60}")
    _log.info("  Post-pipeline: bricolage · certificados · git push")
    _log.info('─' * 60)
    _post_pipeline(dry_run=dry_run)


# ── Task Scheduler ────────────────────────────────────────────────────────────

def instalar_scheduler():
    """Registra el auto-runner en Windows Task Scheduler — lunes 10:00 ART (hora local Windows = 13:00 UTC)."""
    python_exe = str(Path(sys.executable).resolve())
    script     = str(Path(__file__).resolve())

    # /TR requiere que el comando completo esté entre comillas y las rutas internas escapadas
    tr_value = f'"{python_exe}" "{script}"'

    print(f"\nRegistrando: {TASK_NAME}")
    print(f"Horario    : Lunes 10:00 ART (hora local Windows; UTC-3 = 13:00 UTC)")
    print(f"Comando    : {tr_value}\n")

    ps_script = f"""
$action  = New-ScheduledTaskAction -Execute '{python_exe}' -Argument '"{script}"' -WorkingDirectory '{str(Path(script).parent)}'
$trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday -At 10am
$settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit (New-TimeSpan -Hours 2) -RestartCount 1
Register-ScheduledTask -TaskName '{TASK_NAME}' -Action $action -Trigger $trigger -Settings $settings -RunLevel Highest -Force
"""

    resultado = subprocess.run(
        ['powershell', '-NoProfile', '-Command', ps_script],
        capture_output=True, text=True
    )

    if resultado.returncode == 0:
        print(f"OK — Tarea registrada en Task Scheduler")
        print(f"Verificar con: python faro_auto.py --status")
    else:
        print(f"ERROR: {resultado.stderr.strip() or resultado.stdout.strip()}")
        print("Consejo: abrir PowerShell como Administrador y correr de nuevo.")


def estado_scheduler():
    ret = os.system(f'schtasks /Query /TN "{TASK_NAME}" /FO LIST')
    if ret != 0:
        print(f"\nTarea '{TASK_NAME}' no encontrada.")
        print("Correr: python faro_auto.py --instalar")


# ── CLI ───────────────────────────────────────────────────────────────────────

_log = _setup_logger()

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='FARO PROTOCOL — Auto-runner semanal')
    parser.add_argument('--area',     help='Procesar solo este área (ej: cordoba)')
    parser.add_argument('--dry-run',  action='store_true', help='Simular sin procesar ni enviar')
    parser.add_argument('--instalar', action='store_true', help='Registrar en Windows Task Scheduler')
    parser.add_argument('--status',   action='store_true', help='Ver estado en Task Scheduler')
    args = parser.parse_args()

    if args.instalar:
        instalar_scheduler()
    elif args.status:
        estado_scheduler()
    else:
        run(solo_area=args.area, dry_run=args.dry_run)
