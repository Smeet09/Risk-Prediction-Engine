-- ============================================================
-- Aether-Disaster — Migration 003: India Boundaries + Full Pipeline
-- ============================================================

-- 1. States Boundaries
CREATE TABLE IF NOT EXISTS states_boundaries (
  id UUID DEFAULT uuid_generate_v4() PRIMARY KEY,
  name TEXT NOT NULL,
  geom geometry(MultiPolygon, 4326),
  centroid geometry(Point, 4326),
  bbox geometry(Polygon, 4326),
  source_fid INTEGER,
  created_at TIMESTAMPTZ DEFAULT NOW(),
  UNIQUE(name)
);
CREATE INDEX IF NOT EXISTS idx_states_geom ON states_boundaries USING GIST(geom);
CREATE INDEX IF NOT EXISTS idx_states_name ON states_boundaries(name);

-- 2. Districts Boundaries
CREATE TABLE IF NOT EXISTS districts_boundaries (
  id UUID DEFAULT uuid_generate_v4() PRIMARY KEY,
  name TEXT NOT NULL,
  state_name TEXT,
  geom geometry(MultiPolygon, 4326),
  centroid geometry(Point, 4326),
  bbox geometry(Polygon, 4326),
  source_fid INTEGER,
  created_at TIMESTAMPTZ DEFAULT NOW(),
  UNIQUE(name, state_name)
);
CREATE INDEX IF NOT EXISTS idx_districts_geom  ON districts_boundaries USING GIST(geom);
CREATE INDEX IF NOT EXISTS idx_districts_name  ON districts_boundaries(name);
CREATE INDEX IF NOT EXISTS idx_districts_state ON districts_boundaries(state_name);

-- 3. Talukas Boundaries
CREATE TABLE IF NOT EXISTS talukas_boundaries (
  id UUID DEFAULT uuid_generate_v4() PRIMARY KEY,
  name TEXT NOT NULL,
  district_name TEXT,
  state_name TEXT,
  geom geometry(MultiPolygon, 4326),
  centroid geometry(Point, 4326),
  bbox geometry(Polygon, 4326),
  source_fid INTEGER,
  created_at TIMESTAMPTZ DEFAULT NOW(),
  UNIQUE(name, district_name, state_name)
);
CREATE INDEX IF NOT EXISTS idx_talukas_geom  ON talukas_boundaries USING GIST(geom);
CREATE INDEX IF NOT EXISTS idx_talukas_name  ON talukas_boundaries(name);
CREATE INDEX IF NOT EXISTS idx_talukas_parent ON talukas_boundaries(state_name, district_name);

-- 4. Villages Boundaries
CREATE TABLE IF NOT EXISTS villages_boundaries (
  id UUID DEFAULT uuid_generate_v4() PRIMARY KEY,
  name TEXT NOT NULL,
  taluka_name TEXT,
  district_name TEXT,
  state_name TEXT,
  geom geometry(MultiPolygon, 4326),
  centroid geometry(Point, 4326),
  bbox geometry(Polygon, 4326),
  source_fid INTEGER,
  created_at TIMESTAMPTZ DEFAULT NOW()
  -- Intentionally leaving off UNIQUE since multiple villages in a taluka might have identical names in raw data strings
);
CREATE INDEX IF NOT EXISTS idx_villages_geom  ON villages_boundaries USING GIST(geom);
CREATE INDEX IF NOT EXISTS idx_villages_name  ON villages_boundaries(name);
CREATE INDEX IF NOT EXISTS idx_villages_parent ON villages_boundaries(state_name, district_name, taluka_name);

-- ---- DEM Uploads (one per region) ----
CREATE TABLE IF NOT EXISTS dem_uploads (
  id           UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  region_id    UUID NOT NULL REFERENCES regions(id) ON DELETE CASCADE,
  file_path    TEXT NOT NULL,
  file_name    TEXT,
  file_size_mb FLOAT,
  uploaded_at  TIMESTAMPTZ DEFAULT NOW(),
  UNIQUE(region_id)
);

-- ---- Topographic Features (generated from DEM via WhiteboxTools) ----
CREATE TABLE IF NOT EXISTS topographic_features (
  id             UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  region_id      UUID NOT NULL REFERENCES regions(id) ON DELETE CASCADE,
  output_dir     TEXT NOT NULL,
  features_list  TEXT[] DEFAULT ARRAY['slope','aspect','curvature','flow_accumulation','twi','roughness'],
  job_log        TEXT,
  generated_at   TIMESTAMPTZ DEFAULT NOW(),
  UNIQUE(region_id)
);

-- ---- Terrain Classification Results (12 physics-based classes) ----
CREATE TABLE IF NOT EXISTS terrain_classifications (
  id             UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  region_id      UUID NOT NULL REFERENCES regions(id) ON DELETE CASCADE,
  tif_path       TEXT,
  shp_path       TEXT,
  class_stats    JSONB,
  -- e.g. {"1":{"name":"Coastal_Lowland","pixel_count":1234,"pct":12.3}, ...}
  dominant_class INTEGER,
  dominant_name  TEXT,
  classified_at  TIMESTAMPTZ DEFAULT NOW(),
  UNIQUE(region_id)
);

-- ---- India-Wide Manual Data (uploaded ONCE for all India) ----
CREATE TABLE IF NOT EXISTS manual_data_india (
  id           UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  data_type    TEXT NOT NULL CHECK (data_type IN ('lulc','river','soil','fault')),
  file_path    TEXT NOT NULL,
  file_name    TEXT,
  description  TEXT,
  uploaded_at  TIMESTAMPTZ DEFAULT NOW(),
  uploaded_by  UUID REFERENCES users(id) ON DELETE SET NULL,
  UNIQUE(data_type)
);

-- ---- Dynamic Disaster Types (admin-managed) ----
CREATE TABLE IF NOT EXISTS disaster_types (
  id            UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  name          TEXT NOT NULL UNIQUE,       -- e.g. "Flood"
  code          TEXT NOT NULL UNIQUE,       -- e.g. "flood"
  category      TEXT NOT NULL,              -- e.g. "Hydro-meteorological"
  description   TEXT,
  icon          TEXT DEFAULT '⚠️',
  color         TEXT DEFAULT '#0071e3',
  is_active     BOOLEAN DEFAULT TRUE,       -- admin can hide
  sort_order    INTEGER DEFAULT 0,
  -- Default terrain class weights (class_id → weight 0–1)
  default_weights JSONB DEFAULT '{}',
  created_at    TIMESTAMPTZ DEFAULT NOW(),
  updated_at    TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_disaster_category ON disaster_types(category);
CREATE INDEX IF NOT EXISTS idx_disaster_active    ON disaster_types(is_active);

-- ---- Extend data_inventory ----
ALTER TABLE data_inventory
  ADD COLUMN IF NOT EXISTS terrain_ready       BOOLEAN DEFAULT FALSE,
  ADD COLUMN IF NOT EXISTS manual_india_ready  BOOLEAN DEFAULT FALSE;

-- ---- Drop old susceptibility_results and recreate with disaster FK ----
DROP TABLE IF EXISTS susceptibility_results CASCADE;

CREATE TABLE IF NOT EXISTS susceptibility_results (
  id               UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  region_id        UUID NOT NULL REFERENCES regions(id) ON DELETE CASCADE,
  disaster_type_id UUID REFERENCES disaster_types(id) ON DELETE SET NULL,
  disaster_code    TEXT NOT NULL,    -- kept for quick query without join
  terrain_weights  JSONB,            -- weights used for this run
  tif_path         TEXT,
  geojson_path     TEXT,
  final_geojson    JSONB,            -- cached for map display
  class_stats      JSONB,            -- susceptibility class distribution
  status           TEXT DEFAULT 'done' CHECK (status IN ('pending','processing','done','failed')),
  log              TEXT,
  generated_at     TIMESTAMPTZ DEFAULT NOW(),
  UNIQUE(region_id, disaster_code)
);

CREATE INDEX IF NOT EXISTS idx_susc_region   ON susceptibility_results(region_id);
CREATE INDEX IF NOT EXISTS idx_susc_disaster ON susceptibility_results(disaster_code);

-- ---- Seed default disaster types ----
INSERT INTO disaster_types (name, code, category, description, icon, color, sort_order, default_weights) VALUES
  -- Hydro-meteorological
  ('Flood',     'flood',     'Hydro-meteorological', 'River and flash flood susceptibility',          '🌊', '#0071e3', 1,
   '{"1":0.9,"2":1.0,"3":0.7,"4":0.8,"5":0.3,"6":0.2,"7":0.1,"8":0.0,"9":0.2,"10":0.0,"11":0.5,"12":0.6}'),
  ('Cyclone',   'cyclone',   'Hydro-meteorological', 'Tropical cyclone and storm surge susceptibility','🌀','#5856d6', 2,
   '{"1":1.0,"2":0.8,"3":0.6,"4":0.5,"5":0.2,"6":0.1,"7":0.1,"8":0.0,"9":0.1,"10":0.0,"11":0.3,"12":1.0}'),
  ('Drought',   'drought',   'Hydro-meteorological', 'Meteorological and agricultural drought',       '☀️','#ff9f0a', 3,
   '{"1":0.2,"2":0.3,"3":0.5,"4":0.3,"5":0.4,"6":0.3,"7":0.2,"8":0.1,"9":0.6,"10":0.1,"11":1.0,"12":0.2}'),
  -- Geological
  ('Landslide', 'landslide', 'Geological',           'Rainfall-induced slope failure susceptibility', '🏔','#ff3b30',  4,
   '{"1":0.1,"2":0.2,"3":0.1,"4":0.6,"5":0.7,"6":0.8,"7":0.9,"8":1.0,"9":0.2,"10":1.0,"11":0.0,"12":0.3}'),
  ('Earthquake','earthquake','Geological',           'Seismic hazard and liquefaction susceptibility','🌍','#ff6b35',  5,
   '{"1":0.8,"2":0.9,"3":0.7,"4":0.8,"5":0.5,"6":0.4,"7":0.3,"8":0.2,"9":0.3,"10":0.4,"11":0.6,"12":0.7}'),
  -- Climatological
  ('Heat Wave', 'heatwave',  'Climatological',       'Extreme heat event susceptibility',             '🌡','#ff6b35',  6,
   '{"1":0.3,"2":0.3,"3":0.5,"4":0.3,"5":0.4,"6":0.3,"7":0.2,"8":0.1,"9":0.7,"10":0.1,"11":1.0,"12":0.2}'),
  ('Wildfire',  'wildfire',  'Climatological',       'Forest and grassland fire susceptibility',      '🔥','#ff9500',  7,
   '{"1":0.1,"2":0.1,"3":0.2,"4":0.2,"5":0.7,"6":0.8,"7":0.9,"8":0.7,"9":0.6,"10":0.5,"11":0.4,"12":0.2}')
ON CONFLICT (code) DO NOTHING;
