CREATE TABLE IF NOT EXISTS venues (
    venue_id    TEXT        PRIMARY KEY,
    nombre      TEXT        NOT NULL,
    activo      BOOLEAN     NOT NULL DEFAULT TRUE,
    plan        TEXT        NOT NULL DEFAULT 'starter',
    config      JSONB       NOT NULL DEFAULT '{}',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

INSERT INTO venues (venue_id, nombre, plan, config)
VALUES ('velez', 'Club Atlético Vélez Sarsfield', 'enterprise',
        '{"sectors":["amalfitani_central","vo_bloque_a","vo_bloque_b","vo_bloque_c","vo_bloque_d"]}')
ON CONFLICT (venue_id) DO NOTHING;

ALTER TABLE pipeline_runs       ADD COLUMN IF NOT EXISTS venue_id TEXT NOT NULL DEFAULT 'velez';
ALTER TABLE velez_canchas       ADD COLUMN IF NOT EXISTS venue_id TEXT NOT NULL DEFAULT 'velez';
ALTER TABLE velez_sectores      ADD COLUMN IF NOT EXISTS venue_id TEXT NOT NULL DEFAULT 'velez';
ALTER TABLE velez_weather_live  ADD COLUMN IF NOT EXISTS venue_id TEXT NOT NULL DEFAULT 'velez';
ALTER TABLE velez_intervenciones ADD COLUMN IF NOT EXISTS venue_id TEXT NOT NULL DEFAULT 'velez';

UPDATE pipeline_runs        SET venue_id = 'velez' WHERE venue_id IS NULL OR venue_id = '';
UPDATE velez_intervenciones SET venue_id = 'velez' WHERE venue_id IS NULL OR venue_id = '';

CREATE INDEX IF NOT EXISTS pr_venue       ON pipeline_runs(venue_id);
CREATE INDEX IF NOT EXISTS vc_venue       ON velez_canchas(venue_id);
CREATE INDEX IF NOT EXISTS vs_venue       ON velez_sectores(venue_id);
CREATE INDEX IF NOT EXISTS vi_venue_time  ON velez_intervenciones(venue_id, created_at DESC);

CREATE OR REPLACE FUNCTION faro_venue_id() RETURNS TEXT LANGUAGE sql STABLE AS $$
    SELECT COALESCE(
        (current_setting('request.jwt.claims', TRUE)::json ->> 'venue_id'),
        'velez'
    )
$$;

ALTER TABLE audit_log           ENABLE ROW LEVEL SECURITY;
ALTER TABLE intervention_log    ENABLE ROW LEVEL SECURITY;
ALTER TABLE soil_metrics        ENABLE ROW LEVEL SECURITY;
ALTER TABLE vegetation_metrics  ENABLE ROW LEVEL SECURITY;
ALTER TABLE climate_metrics     ENABLE ROW LEVEL SECURITY;
ALTER TABLE pipeline_runs       ENABLE ROW LEVEL SECURITY;
ALTER TABLE velez_canchas       ENABLE ROW LEVEL SECURITY;
ALTER TABLE velez_sectores      ENABLE ROW LEVEL SECURITY;
ALTER TABLE velez_intervenciones ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS p_audit_log          ON audit_log;
DROP POLICY IF EXISTS p_intervention_log   ON intervention_log;
DROP POLICY IF EXISTS p_soil_metrics       ON soil_metrics;
DROP POLICY IF EXISTS p_vegetation_metrics ON vegetation_metrics;
DROP POLICY IF EXISTS p_climate_metrics    ON climate_metrics;
DROP POLICY IF EXISTS p_pipeline_runs      ON pipeline_runs;
DROP POLICY IF EXISTS p_velez_canchas      ON velez_canchas;
DROP POLICY IF EXISTS p_velez_sectores     ON velez_sectores;
DROP POLICY IF EXISTS p_velez_intervenciones ON velez_intervenciones;

CREATE POLICY p_audit_log          ON audit_log           FOR ALL TO authenticated, anon USING (venue_id = faro_venue_id());
CREATE POLICY p_intervention_log   ON intervention_log    FOR ALL TO authenticated, anon USING (venue_id = faro_venue_id());
CREATE POLICY p_soil_metrics       ON soil_metrics        FOR ALL TO authenticated, anon USING (venue_id = faro_venue_id());
CREATE POLICY p_vegetation_metrics ON vegetation_metrics  FOR ALL TO authenticated, anon USING (venue_id = faro_venue_id());
CREATE POLICY p_climate_metrics    ON climate_metrics     FOR ALL TO authenticated, anon USING (venue_id = faro_venue_id());
CREATE POLICY p_pipeline_runs      ON pipeline_runs       FOR ALL TO authenticated, anon USING (venue_id = faro_venue_id());
CREATE POLICY p_velez_canchas      ON velez_canchas       FOR ALL TO authenticated, anon USING (venue_id = faro_venue_id());
CREATE POLICY p_velez_sectores     ON velez_sectores      FOR ALL TO authenticated, anon USING (venue_id = faro_venue_id());
CREATE POLICY p_velez_intervenciones ON velez_intervenciones FOR ALL TO authenticated, anon USING (venue_id = faro_venue_id());
