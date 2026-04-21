-- ============================================================
-- Migration: Dynamic Risk Prediction
-- Run this against: aether_disaster DB
-- ============================================================

-- 1. New table for dynamic risk results
CREATE TABLE IF NOT EXISTS public.dynamic_risk_results (
    id              BIGSERIAL PRIMARY KEY,
    region_id       TEXT NOT NULL,
    disaster_code   TEXT NOT NULL,
    target_date     DATE NOT NULL,
    class_stats     JSONB,
    risk_geojson    JSONB,
    composite_mean  FLOAT,
    trigger_method  TEXT,
    warning_summary JSONB,
    tif_path        TEXT,
    physics_meta    JSONB,
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (region_id, disaster_code, target_date)
);

CREATE INDEX IF NOT EXISTS idx_dynrisk_region_code_date
    ON public.dynamic_risk_results (region_id, disaster_code, target_date DESC);

ALTER TABLE public.dynamic_risk_results OWNER TO aether;

-- 2. Update jobs.module CHECK constraint to allow 'dynamic'
--    (drop and recreate since Postgres does not support ALTER CONSTRAINT)
ALTER TABLE public.jobs DROP CONSTRAINT IF EXISTS jobs_module_check;

ALTER TABLE public.jobs
    ADD CONSTRAINT jobs_module_check CHECK (
        module = ANY (ARRAY[
            'dem'::text,
            'exposure'::text,
            'manual'::text,
            'susceptibility'::text,
            'weather'::text,
            'dynamic'::text
        ])
    );
