-- India-wide Coastal Data Migration
-- Stores the India coastline as spatial geometries for terrain classification analysis.

CREATE TABLE IF NOT EXISTS india_coastlines (
    id SERIAL PRIMARY KEY,
    name TEXT,
    geom GEOMETRY(GEOMETRY, 4326),
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS india_coastlines_geom_idx ON india_coastlines USING GIST (geom);

-- We don't seed this yet because the user will upload their own Coastline_All.zip 
-- via the Admin UI, which will be imported using the GIS service.
