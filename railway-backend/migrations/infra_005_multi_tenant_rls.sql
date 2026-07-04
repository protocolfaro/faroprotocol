-- Faro Protocol · Infra Migration 005
-- Multi-tenant hardening: tenant registry + RLS on all tables
--
-- SAFETY NOTE: Supabase service_role always bypasses RLS — existing app behaviour
-- is unaffected. Policies apply to anon/authenticated roles only.
-- Run AFTER migrations 001–004. Review each ALTER TABLE before executing.

-- ── 1. Tenants registry ───────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS tenants (
    tenant_id   TEXT        PRIMARY KEY,
    nombre      TEXT        NOT NULL,
    activo      BOOLEAN     NOT NULL DEFAULT TRUE,
    plan        TEXT        NOT NULL DEFAULT 'starter',
    -- Allowed metrics sources, stored as JSONB for flexibility
    config      JSONB       NOT NULL DEFAULT '{}',
    creado_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

INSERT INTO tenants (tenant_id, nombre, plan, config)
VALUES (
    'velez',
    'Club Atlético Vélez Sarsfield',
    'enterprise',
    '{"sectors":["amalfitani_central","vo_bloque_a","vo_bloque_b","vo_bloque_c","vo_bloque_d"]}'
)
ON CONFLICT (tenant_id) DO NOTHING;

-- ── 2. Add tenant_id to tables that only use implicit venue_id ────────────────

-- pipeline_runs has no venue_id — add tenant_id
ALTER TABLE pipeline_runs
    ADD COLUMN IF NOT EXISTS tenant_id TEXT NOT NULL DEFAULT 'velez';

UPDATE pipeline_runs SET tenant_id = 'velez' WHERE tenant_id = 'velez';

-- velez_canchas is implicitly single-tenant — add for forward-compat
ALTER TABLE velez_canchas
    ADD COLUMN IF NOT EXISTS tenant_id TEXT NOT NULL DEFAULT 'velez';

ALTER TABLE velez_sectores
    ADD COLUMN IF NOT EXISTS tenant_id TEXT NOT NULL DEFAULT 'velez';

ALTER TABLE velez_weather_live
    ADD COLUMN IF NOT EXISTS tenant_id TEXT NOT NULL DEFAULT 'velez';

ALTER TABLE velez_intervenciones
    ADD COLUMN IF NOT EXISTS tenant_id TEXT NOT NULL DEFAULT 'velez';

UPDATE velez_intervenciones SET tenant_id = 'velez' WHERE tenant_id IS NULL OR tenant_id = '';

-- ── 3. Indexes for new tenant_id columns ──────────────────────────────────────

CREATE INDEX IF NOT EXISTS pipeline_runs_tenant      ON pipeline_runs(tenant_id);
CREATE INDEX IF NOT EXISTS velez_canchas_tenant      ON velez_canchas(tenant_id);
CREATE INDEX IF NOT EXISTS velez_sectores_tenant     ON velez_sectores(tenant_id);
CREATE INDEX IF NOT EXISTS velez_intervenciones_tenant ON velez_intervenciones(tenant_id);

-- ── 4. Enable Row Level Security ──────────────────────────────────────────────
-- service_role bypasses automatically; these policies target anon/authenticated

ALTER TABLE audit_log           ENABLE ROW LEVEL SECURITY;
ALTER TABLE intervention_log    ENABLE ROW LEVEL SECURITY;
ALTER TABLE alert_config        ENABLE ROW LEVEL SECURITY;
ALTER TABLE health_snapshots    ENABLE ROW LEVEL SECURITY;
ALTER TABLE pipeline_runs       ENABLE ROW LEVEL SECURITY;
ALTER TABLE soil_metrics        ENABLE ROW LEVEL SECURITY;
ALTER TABLE vegetation_metrics  ENABLE ROW LEVEL SECURITY;
ALTER TABLE climate_metrics     ENABLE ROW LEVEL SECURITY;
ALTER TABLE velez_canchas       ENABLE ROW LEVEL SECURITY;
ALTER TABLE velez_sectores      ENABLE ROW LEVEL SECURITY;
ALTER TABLE velez_intervenciones ENABLE ROW LEVEL SECURITY;

-- ── 5. RLS Policies — tenant isolation via JWT claim ─────────────────────────
-- JWT claim path: auth.jwt() ->> 'tenant_id'
-- Set via Supabase Auth custom claims or API Gateway header injection.
-- Fallback: current_setting('request.jwt.claims', true)::json ->> 'tenant_id'

-- Helper: extract tenant from JWT or fall back to 'velez' for service_role
CREATE OR REPLACE FUNCTION faro_tenant_id() RETURNS TEXT
    LANGUAGE sql STABLE
AS $$
    SELECT COALESCE(
        (current_setting('request.jwt.claims', true)::json ->> 'tenant_id'),
        'velez'
    )
$$;

-- audit_log
DROP POLICY IF EXISTS audit_rls ON audit_log;
CREATE POLICY audit_rls ON audit_log
    FOR ALL TO authenticated, anon
    USING (tenant_id = faro_tenant_id());

-- intervention_log
DROP POLICY IF EXISTS intervention_rls ON intervention_log;
CREATE POLICY intervention_rls ON intervention_log
    FOR ALL TO authenticated, anon
    USING (tenant_id = faro_tenant_id());

-- alert_config
DROP POLICY IF EXISTS alert_rls ON alert_config;
CREATE POLICY alert_rls ON alert_config
    FOR ALL TO authenticated, anon
    USING (tenant_id = faro_tenant_id());

-- health_snapshots
DROP POLICY IF EXISTS health_rls ON health_snapshots;
CREATE POLICY health_rls ON health_snapshots
    FOR ALL TO authenticated, anon
    USING (tenant_id = faro_tenant_id());

-- pipeline_runs (tenant_id column added above)
DROP POLICY IF EXISTS pipeline_rls ON pipeline_runs;
CREATE POLICY pipeline_rls ON pipeline_runs
    FOR ALL TO authenticated, anon
    USING (tenant_id = faro_tenant_id());

-- soil_metrics (venue_id serves as tenant discriminator)
DROP POLICY IF EXISTS soil_rls ON soil_metrics;
CREATE POLICY soil_rls ON soil_metrics
    FOR ALL TO authenticated, anon
    USING (venue_id = faro_tenant_id());

-- vegetation_metrics
DROP POLICY IF EXISTS veg_rls ON vegetation_metrics;
CREATE POLICY veg_rls ON vegetation_metrics
    FOR ALL TO authenticated, anon
    USING (venue_id = faro_tenant_id());

-- climate_metrics
DROP POLICY IF EXISTS climate_rls ON climate_metrics;
CREATE POLICY climate_rls ON climate_metrics
    FOR ALL TO authenticated, anon
    USING (venue_id = faro_tenant_id());

-- velez_canchas
DROP POLICY IF EXISTS canchas_rls ON velez_canchas;
CREATE POLICY canchas_rls ON velez_canchas
    FOR ALL TO authenticated, anon
    USING (tenant_id = faro_tenant_id());

-- velez_sectores
DROP POLICY IF EXISTS sectores_rls ON velez_sectores;
CREATE POLICY sectores_rls ON velez_sectores
    FOR ALL TO authenticated, anon
    USING (tenant_id = faro_tenant_id());

-- velez_intervenciones
DROP POLICY IF EXISTS intervenciones_rls ON velez_intervenciones;
CREATE POLICY intervenciones_rls ON velez_intervenciones
    FOR ALL TO authenticated, anon
    USING (tenant_id = faro_tenant_id());

COMMENT ON FUNCTION faro_tenant_id IS
    'Extracts tenant_id from Supabase JWT claims. Returns ''velez'' if not set.';
