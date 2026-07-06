-- ══════════════════════════════════════════════════════════════════════════════
-- ERA5-Land Sectorial — Migración Supabase
-- Ejecutar en: Supabase Dashboard → SQL Editor
-- ══════════════════════════════════════════════════════════════════════════════

-- Tabla principal de datos climáticos sectoriales
CREATE TABLE IF NOT EXISTS climate_metrics_sectorial (
    id           BIGSERIAL PRIMARY KEY,
    sector_id    VARCHAR(64)  NOT NULL,
    fecha        DATE         NOT NULL,
    et0_mm_dia   NUMERIC,              -- ET₀ Penman-Monteith mm/día (ERA5-Land pev)
    temp_2m_c    NUMERIC,              -- Temperatura 2m media diaria °C
    rh_pct       NUMERIC,              -- Humedad relativa % (calculada de T + Td)
    sm_0_7cm_pct  NUMERIC,             -- SM capa 0-7cm × 100
    sm_0_7cm_m3m3 NUMERIC,             -- SM capa 0-7cm en m³/m³ (ERA5 swvl1)
    fuente       TEXT DEFAULT 'ERA5-Land-hourly',
    created_at   TIMESTAMPTZ DEFAULT NOW(),
    updated_at   TIMESTAMPTZ DEFAULT NOW(),

    CONSTRAINT uq_sector_fecha UNIQUE (sector_id, fecha),
    CONSTRAINT chk_sector_valid CHECK (
        sector_id IN (
            'amalfitani_central',
            'vo_bloque_a',
            'vo_bloque_b',
            'vo_bloque_c',
            'vo_bloque_d'
        )
    )
);

-- Índices para queries rápidas
CREATE INDEX IF NOT EXISTS idx_cms_sector_fecha
    ON climate_metrics_sectorial (sector_id, fecha DESC);
CREATE INDEX IF NOT EXISTS idx_cms_fecha
    ON climate_metrics_sectorial (fecha DESC);

-- Trigger para auto-update updated_at (requiere que exista la función update_updated_at_column)
-- Si la función ya existe en el schema (es estándar en Supabase), simplemente ejecutar:
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_proc
        WHERE proname = 'update_updated_at_column'
    ) THEN
        CREATE FUNCTION update_updated_at_column()
        RETURNS TRIGGER AS $func$
        BEGIN
            NEW.updated_at = NOW();
            RETURN NEW;
        END;
        $func$ LANGUAGE plpgsql;
    END IF;
END $$;

CREATE TRIGGER trg_cms_updated_at
    BEFORE UPDATE ON climate_metrics_sectorial
    FOR EACH ROW EXECUTE PROCEDURE update_updated_at_column();

-- ── Permisos ──────────────────────────────────────────────────────────────────
-- service_role: escribe datos desde GitHub Actions ERA5 pipeline
GRANT SELECT, INSERT, UPDATE ON public.climate_metrics_sectorial TO service_role;
GRANT USAGE, SELECT ON SEQUENCE public.climate_metrics_sectorial_id_seq TO service_role;

-- anon: lectura para el health-dashboard (panel público de control)
GRANT SELECT ON public.climate_metrics_sectorial TO anon;

-- RLS: desactivar o crear política permisiva para lectura pública
-- Opción A (simple, tabla no sensible): deshabilitar RLS
ALTER TABLE public.climate_metrics_sectorial DISABLE ROW LEVEL SECURITY;

-- Verificación post-migración
SELECT
    table_name,
    column_name,
    data_type
FROM information_schema.columns
WHERE table_name = 'climate_metrics_sectorial'
ORDER BY ordinal_position;

-- Debe retornar 11 columnas: id, sector_id, fecha, et0_mm_dia, temp_2m_c,
-- rh_pct, sm_0_7cm_pct, sm_0_7cm_m3m3, fuente, created_at, updated_at
