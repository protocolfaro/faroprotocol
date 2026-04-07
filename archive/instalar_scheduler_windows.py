"""
FARO PROTOCOL — Instalador del scheduler en Windows Task Scheduler

Registra una tarea que corre faro_price_updater.py automáticamente
cada lunes a las 08:00 hora de Argentina (ART = UTC-3).

Uso (ejecutar como administrador):
    python instalar_scheduler_windows.py          <- instala
    python instalar_scheduler_windows.py --remove <- desinstala
    python instalar_scheduler_windows.py --status <- verifica estado
"""

import subprocess
import sys
import os
from pathlib import Path

PROJECT_DIR = Path(__file__).parent
BAT_FILE    = PROJECT_DIR / 'faro_scheduler.bat'
TASK_NAME   = 'FaroProtocol_PriceUpdate'
LOG_DIR     = PROJECT_DIR / 'logs'


def ensure_logs_dir():
    LOG_DIR.mkdir(exist_ok=True)
    print(f"  Carpeta logs: {LOG_DIR}")


def install():
    ensure_logs_dir()

    # schtasks: lunes a las 08:00 ART
    # ART = UTC-3. Windows Task Scheduler usa hora local.
    # Si el sistema está en ART, usar 08:00 directamente.
    cmd = [
        'schtasks', '/Create',
        '/TN', TASK_NAME,
        '/TR', f'"{BAT_FILE}"',
        '/SC', 'WEEKLY',
        '/D',  'MON',
        '/ST', '08:00',
        '/F',                         # forzar reemplazo si ya existe
        '/RL', 'HIGHEST',             # ejecutar con privilegios elevados
        '/RU', os.environ.get('USERNAME', 'SYSTEM'),
    ]

    print(f"\n  Registrando tarea: {TASK_NAME}")
    print(f"  Frecuencia: cada lunes a las 08:00 (hora local del sistema)")
    print(f"  Script:     {BAT_FILE}")
    print(f"  Logs:       {LOG_DIR / 'scheduler.log'}")
    print()

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode == 0:
        print("  OK — Tarea registrada en Windows Task Scheduler")
        print()
        print("  IMPORTANTE: El sistema debe estar encendido los lunes a las 08:00.")
        print("  Si el sistema estaba apagado, la tarea NO corre retroactivamente.")
        print("  Para eso, configurar en Task Scheduler:")
        print("    > Propiedades de la tarea > Configuración")
        print("    > Activar: 'Ejecutar la tarea lo antes posible si se perdió'")
    else:
        print(f"  ERROR: {result.stderr.strip()}")
        print()
        print("  Si falló por permisos, ejecutá este script como Administrador:")
        print("  Clic derecho en CMD > 'Ejecutar como administrador'")
        print("  Luego: python instalar_scheduler_windows.py")


def remove():
    cmd = ['schtasks', '/Delete', '/TN', TASK_NAME, '/F']
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode == 0:
        print(f"  OK — Tarea '{TASK_NAME}' eliminada.")
    else:
        print(f"  ERROR o no existía: {result.stderr.strip()}")


def status():
    cmd = ['schtasks', '/Query', '/TN', TASK_NAME, '/FO', 'LIST', '/V']
    result = subprocess.run(cmd, capture_output=True, text=True, encoding='cp1252', errors='replace')
    if result.returncode == 0:
        # Mostrar las líneas más relevantes
        for line in result.stdout.splitlines():
            if any(k in line for k in ['Nombre', 'Estado', 'Próxima', 'Última', 'Next', 'Last', 'Status', 'Task Name']):
                print(f"  {line.strip()}")
    else:
        print(f"  Tarea no encontrada: {TASK_NAME}")
        print("  Corré: python instalar_scheduler_windows.py")


def main():
    if '--remove' in sys.argv:
        print("\n  FARO — Desinstalando scheduler...")
        remove()
    elif '--status' in sys.argv:
        print("\n  FARO — Estado del scheduler:")
        status()
    else:
        print("\n  FARO — Instalando scheduler en Windows Task Scheduler...")
        install()


if __name__ == '__main__':
    main()
