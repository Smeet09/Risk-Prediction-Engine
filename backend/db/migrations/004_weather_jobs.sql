-- Migration to add country/state support to jobs table for Weather module
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS country TEXT;
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS state TEXT;

-- Update the module check constraint to include 'weather'
-- First, drop the existing constraint if it exists (standard name is usually jobs_module_check or similar)
DO $$ 
BEGIN
    ALTER TABLE jobs DROP CONSTRAINT IF EXISTS jobs_module_check;
    ALTER TABLE jobs ADD CONSTRAINT jobs_module_check CHECK (module IN ('dem', 'exposure', 'manual', 'susceptibility', 'weather'));
END $$;
