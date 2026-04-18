-- ============================================================
-- Aether-Disaster — Migration 007: India-Wide Spatial Layers
-- ============================================================

-- 1. Table for India-wide River geometries
CREATE TABLE IF NOT EXISTS india_rivers (
  id UUID DEFAULT uuid_generate_v4() PRIMARY KEY,
  name TEXT,
  order_val INTEGER, -- Stream order
  geom geometry(MultiLineString, 4326),
  created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_india_rivers_geom ON india_rivers USING GIST(geom);

-- 2. Table for India-wide Fault geometries
CREATE TABLE IF NOT EXISTS india_faults (
  id UUID DEFAULT uuid_generate_v4() PRIMARY KEY,
  name TEXT,
  geom geometry(MultiLineString, 4326),
  created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_india_faults_geom ON india_faults USING GIST(geom);

-- 3. Add column to track if vector data has been imported to PostGIS
ALTER TABLE manual_data_india 
  ADD COLUMN IF NOT EXISTS postgis_imported BOOLEAN DEFAULT FALSE;

-- 4. Add column for registered local path (for large files like LULC)
ALTER TABLE manual_data_india 
  ADD COLUMN IF NOT EXISTS is_local_path BOOLEAN DEFAULT FALSE;
