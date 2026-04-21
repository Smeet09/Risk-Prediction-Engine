-- ============================================================
-- 010_susceptibility_scripts.sql
-- Adds script_path column to disaster_types for custom script uploads.
-- Also adds SUSC-related columns that may be missing.
-- ============================================================

-- Add script_path to disaster_types (custom uploaded Python script)
ALTER TABLE disaster_types
  ADD COLUMN IF NOT EXISTS script_path TEXT DEFAULT NULL;

-- Ensure susceptibility_results has all needed columns
ALTER TABLE susceptibility_results
  ADD COLUMN IF NOT EXISTS disaster_type_id UUID,
  ADD COLUMN IF NOT EXISTS disaster_code    TEXT,
  ADD COLUMN IF NOT EXISTS terrain_weights  JSONB DEFAULT '{}',
  ADD COLUMN IF NOT EXISTS tif_path         TEXT,
  ADD COLUMN IF NOT EXISTS geojson_path     TEXT,
  ADD COLUMN IF NOT EXISTS class_stats      JSONB DEFAULT '{}',
  ADD COLUMN IF NOT EXISTS dominant_class   INTEGER,
  ADD COLUMN IF NOT EXISTS status           TEXT  DEFAULT 'pending',
  ADD COLUMN IF NOT EXISTS log              TEXT  DEFAULT '';

-- Add unique constraint on (region_id, disaster_code) if not present
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint
    WHERE conname = 'susceptibility_results_region_disaster_unique'
  ) THEN
    ALTER TABLE susceptibility_results
      ADD CONSTRAINT susceptibility_results_region_disaster_unique
      UNIQUE (region_id, disaster_code);
  END IF;
END$$;

-- Ensure data_inventory has terrain_ready and manual_india_ready
ALTER TABLE data_inventory
  ADD COLUMN IF NOT EXISTS terrain_ready      BOOLEAN DEFAULT FALSE,
  ADD COLUMN IF NOT EXISTS topo_ready         BOOLEAN DEFAULT FALSE,
  ADD COLUMN IF NOT EXISTS manual_india_ready BOOLEAN DEFAULT FALSE;

-- Index for fast lookups
CREATE INDEX IF NOT EXISTS idx_susc_region_disaster
  ON susceptibility_results (region_id, disaster_code);

CREATE INDEX IF NOT EXISTS idx_disaster_types_code
  ON disaster_types (code);
