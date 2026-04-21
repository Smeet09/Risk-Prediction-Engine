-- ============================================================
-- Aether-Disaster — Initial Database Migration
-- Requires PostgreSQL with PostGIS extension
-- ============================================================

-- Enable PostGIS
CREATE EXTENSION IF NOT EXISTS postgis;
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- ---- Users ----
CREATE TABLE IF NOT EXISTS users (
  id          UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  email       TEXT UNIQUE NOT NULL,
  password    TEXT NOT NULL,
  role        TEXT NOT NULL CHECK (role IN ('admin', 'user')),
  full_name   TEXT,
  created_at  TIMESTAMPTZ DEFAULT NOW()
);

-- ---- Regions master table ----
CREATE TABLE IF NOT EXISTS regions (
  id          UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  country     TEXT NOT NULL,
  state       TEXT,
  district    TEXT,
  centroid    GEOMETRY(Point, 4326),
  bbox        GEOMETRY(Polygon, 4326),
  created_at  TIMESTAMPTZ DEFAULT NOW(),
  UNIQUE (country, state, district)
);

-- ---- Data inventory per region ----
CREATE TABLE IF NOT EXISTS data_inventory (
  id                  UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  region_id           UUID NOT NULL REFERENCES regions(id) ON DELETE CASCADE,
  dem_ready           BOOLEAN DEFAULT FALSE,
  exposure_ready      BOOLEAN DEFAULT FALSE,
  manual_ready        BOOLEAN DEFAULT FALSE,
  susceptibility_ready BOOLEAN DEFAULT FALSE,
  updated_at          TIMESTAMPTZ DEFAULT NOW(),
  UNIQUE (region_id)
);

-- ---- Upload / Processing Jobs ----
CREATE TABLE IF NOT EXISTS jobs (
  id           UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  region_id    UUID REFERENCES regions(id) ON DELETE SET NULL,
  module       TEXT NOT NULL CHECK (module IN ('dem', 'exposure', 'manual', 'susceptibility')),
  disaster_type TEXT,
  status       TEXT NOT NULL DEFAULT 'pending'
               CHECK (status IN ('pending', 'processing', 'done', 'failed')),
  log          TEXT DEFAULT '',
  progress     INTEGER DEFAULT 0,
  file_path    TEXT,
  created_at   TIMESTAMPTZ DEFAULT NOW(),
  updated_at   TIMESTAMPTZ DEFAULT NOW()
);

-- ---- Rainfall time-series ----
CREATE TABLE IF NOT EXISTS rainfall_timeseries (
  id          UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  region_id   UUID NOT NULL REFERENCES regions(id) ON DELETE CASCADE,
  date        DATE NOT NULL,
  hour        SMALLINT NOT NULL CHECK (hour BETWEEN 0 AND 23),
  rainfall_mm FLOAT,
  geom        GEOMETRY(MultiPolygon, 4326),
  created_at  TIMESTAMPTZ DEFAULT NOW(),
  UNIQUE (region_id, date, hour)
);

-- ---- AHP / Susceptibility results ----
CREATE TABLE IF NOT EXISTS susceptibility_results (
  id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  region_id       UUID NOT NULL REFERENCES regions(id) ON DELETE CASCADE,
  disaster_type   TEXT NOT NULL,
  hazard_geojson  JSONB,
  exposure_geojson JSONB,
  vuln_geojson    JSONB,
  final_geojson   JSONB,
  generated_at    TIMESTAMPTZ DEFAULT NOW()
);

-- ---- Indexes ----
CREATE INDEX IF NOT EXISTS idx_regions_country ON regions(country);
CREATE INDEX IF NOT EXISTS idx_regions_state ON regions(state);
CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);
CREATE INDEX IF NOT EXISTS idx_rainfall_region_date ON rainfall_timeseries(region_id, date);

-- ---- Seed default admin ----
-- Password: Admin@1234 (bcrypt hash — change in production!)
INSERT INTO users (email, password, role, full_name)
VALUES (
  'admin@aether.local',
  '$2a$10$92IXUNpkjO0rOQ5byMi.Ye4oKoEa3Ro9llC/.og/at2.uheWG/igi',
  'admin',
  'System Administrator'
) ON CONFLICT (email) DO NOTHING;

-- Seed demo user
INSERT INTO users (email, password, role, full_name)
VALUES (
  'user@aether.local',
  '$2a$10$92IXUNpkjO0rOQ5byMi.Ye4oKoEa3Ro9llC/.og/at2.uheWG/igi',
  'user',
  'Demo User'
) ON CONFLICT (email) DO NOTHING;

-- Seed sample regions
INSERT INTO regions (country, state, district, centroid, bbox)
VALUES
  ('India', 'Gujarat', 'Ahmedabad',
   ST_SetSRID(ST_MakePoint(72.5714, 23.0225), 4326),
   ST_MakeEnvelope(72.4, 22.9, 72.8, 23.1, 4326)),
  ('India', 'Gujarat', 'Surat',
   ST_SetSRID(ST_MakePoint(72.8311, 21.1702), 4326),
   ST_MakeEnvelope(72.6, 21.0, 73.0, 21.3, 4326)),
  ('India', 'Maharashtra', 'Mumbai',
   ST_SetSRID(ST_MakePoint(72.8777, 19.0760), 4326),
   ST_MakeEnvelope(72.7, 18.9, 73.0, 19.3, 4326)),
  ('India', 'Rajasthan', 'Jaipur',
   ST_SetSRID(ST_MakePoint(75.7873, 26.9124), 4326),
   ST_MakeEnvelope(75.6, 26.7, 75.9, 27.1, 4326))
ON CONFLICT (country, state, district) DO NOTHING;

-- Seed data_inventory for seeded regions
INSERT INTO data_inventory (region_id)
SELECT id FROM regions ON CONFLICT (region_id) DO NOTHING;
