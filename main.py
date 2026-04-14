# main.py — entrypoint Railway
# Levanta el webhook Flask + scheduler semanal (lunes 13:00 UTC = 10:00 ART)
from faro_webhook import app

import os
import sys
import logging

# ── Scheduler semanal: lunes 13:00 UTC (10:00 ART) ───────────────────────────
try:
    from apscheduler.schedulers.background import BackgroundScheduler
    from apscheduler.triggers.cron import CronTrigger

    def _run_weekly_pipeline():
        logging.info('[Scheduler] Iniciando pipeline semanal...')
        try:
            from faro_auto import run as auto_run
            auto_run()
            logging.info('[Scheduler] Pipeline semanal completado.')
        except Exception as e:
            logging.error(f'[Scheduler] Error en pipeline semanal: {e}')

    scheduler = BackgroundScheduler(timezone='UTC')
    scheduler.add_job(
        _run_weekly_pipeline,
        CronTrigger(day_of_week='mon', hour=13, minute=0, timezone='UTC'),
        id='weekly_pipeline',
        replace_existing=True,
    )
    scheduler.start()
    logging.info('[Scheduler] Cron activo: lunes 13:00 UTC')
except ImportError:
    logging.warning('[Scheduler] APScheduler no disponible — cron deshabilitado')

# ── Flask webhook server ──────────────────────────────────────────────────────
if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
