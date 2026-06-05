-- ── Faro Protocol — APScheduler SQLAlchemy Jobstore ──────────────────────────
-- APScheduler crea esta tabla automáticamente al arrancar con SQLAlchemyJobStore.
-- Este archivo es solo documentación del schema esperado.
-- NO ejecutar manualmente — APScheduler lo gestiona solo.
--
-- Prerequisito Railway: agregar variable SUPABASE_DB_URL con la connection string
-- de Supabase Postgres (Settings → Database → Connection string → URI):
--   postgresql://postgres.[ref]:[password]@[host]:5432/postgres
--
-- Schema generado por APScheduler 3.x / SQLAlchemy 2.x:
CREATE TABLE IF NOT EXISTS apscheduler_jobs (
    id          VARCHAR(191) NOT NULL,
    next_run_time DOUBLE PRECISION,
    job_state   BYTEA        NOT NULL,
    CONSTRAINT apscheduler_jobs_pkey PRIMARY KEY (id)
);
CREATE INDEX IF NOT EXISTS ix_apscheduler_jobs_next_run_time
    ON apscheduler_jobs (next_run_time);
--
-- Jobs que persisten (registrados con replace_existing=True):
--   daily_weather_refresh      — diario 09:00 UTC
--   weekly_insar_refresh       — lunes 10:00 UTC
--   weekly_report              — lunes 10:00 UTC (velez_scheduler)
--   dp_source_scout_semanal    — lunes 11:00 UTC (dale_play_scheduler)
--   dp_monitor_amalfitani      — c/6h (dale_play_monitor)
--   dp_postshow_{show_id}      — c/6h por show, dinámico (persiste entre reinicios)
