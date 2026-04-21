-- Add MultiPolygon geometry column to regions for boundary storage
ALTER TABLE regions ADD COLUMN IF NOT EXISTS geom GEOMETRY(MultiPolygon, 4326);

-- Optional: Index the geometry column for performance
CREATE INDEX IF NOT EXISTS idx_regions_geom ON regions USING GIST (geom);
