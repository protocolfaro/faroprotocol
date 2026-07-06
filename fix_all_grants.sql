-- ══════════════════════════════════════════════════════════════════════════════
-- fix_all_grants.sql — EJECUTAR UNA SOLA VEZ en Supabase SQL Editor
-- Otorga permisos completos a service_role (pipeline server-side)
-- y lectura a anon (health-dashboard + panel público)
-- ══════════════════════════════════════════════════════════════════════════════

-- service_role: acceso completo a todas las tablas del schema public
-- (bypasea RLS pero aun necesita GRANT a nivel objeto PostgreSQL)
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO service_role;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO service_role;

-- anon: lectura a todas las tablas (health-dashboard + panel)
GRANT SELECT ON ALL TABLES IN SCHEMA public TO anon;

-- Verificación: debe retornar filas con grantee=service_role y grantee=anon
SELECT grantee, table_name, privilege_type
FROM information_schema.role_table_grants
WHERE table_schema = 'public'
  AND grantee IN ('service_role', 'anon')
  AND table_name IN (
    'velez_weather_live', 'velez_sectores', 'velez_canchas',
    'soil_metrics', 'vegetation_metrics', 'climate_metrics',
    'climate_metrics_sectorial', 'velez_solar', 'velez_intervenciones',
    'fenologia_baselines', 'pipeline_runs'
  )
ORDER BY table_name, grantee, privilege_type;
