-- Faro Protocol — Tablas Supabase para secciones mutables de velez_data.json
-- Ejecutar una vez en Supabase SQL Editor.
-- Reemplaza hot-path writes a GitHub (weather, NDVI, sectores) → elimina SHA conflicts.

-- velez_canchas: NDVI/score/sem/detalle por cancha (actualizado por satellite_pipeline + push_velez_data)
CREATE TABLE IF NOT EXISTS velez_canchas (
    cancha_id   TEXT PRIMARY KEY,
    ndvi        DOUBLE PRECISION,
    gndvi       DOUBLE PRECISION,
    bsi         DOUBLE PRECISION,
    ndwi        DOUBLE PRECISION,
    n_status    TEXT,
    n_rec       TEXT,
    score       INTEGER,
    score_prev  INTEGER,
    sem         TEXT,
    detalle     TEXT,
    fuente      TEXT,
    updated_at  TIMESTAMPTZ DEFAULT now()
);

-- velez_sectores: score/sem/detalle/insar_mm por sector (actualizado por pipeline + insar_hyp3)
CREATE TABLE IF NOT EXISTS velez_sectores (
    sector_id   TEXT PRIMARY KEY,
    nombre      TEXT,
    score       INTEGER,
    score_prev  INTEGER,
    sem         TEXT,
    detalle     TEXT,
    insar_mm    DOUBLE PRECISION,
    updated_at  TIMESTAMPTZ DEFAULT now()
);

-- velez_weather_live: fila única (id='current') con weather_live JSONB (actualizado diario)
CREATE TABLE IF NOT EXISTS velez_weather_live (
    id          TEXT PRIMARY KEY DEFAULT 'current',
    data        JSONB NOT NULL,
    updated_at  TIMESTAMPTZ DEFAULT now()
);
