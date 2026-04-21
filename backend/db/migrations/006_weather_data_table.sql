-- Migration to add tabular storage for ERA5 aggregated 2km weather grids
CREATE TABLE IF NOT EXISTS weather_data (
  id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  country TEXT NOT NULL,
  state TEXT NOT NULL,
  date DATE NOT NULL,
  lon FLOAT NOT NULL,
  lat FLOAT NOT NULL,
  grid_id TEXT NOT NULL,
  rain_mm FLOAT,
  max_intensity_mm_hr FLOAT,
  temperature_2m FLOAT,
  dewpoint_temperature_2m FLOAT,
  surface_pressure FLOAT,
  mean_sea_level_pressure FLOAT,
  wind_speed_ms FLOAT,
  wind_dir_deg FLOAT,
  soil_moisture_m3m3 FLOAT,
  surface_runoff_mm FLOAT,
  created_at TIMESTAMPTZ DEFAULT NOW(),
  UNIQUE(state, grid_id, date)
);

CREATE INDEX IF NOT EXISTS idx_weather_data_state_date ON weather_data (state, date);
CREATE INDEX IF NOT EXISTS idx_weather_data_grid ON weather_data (grid_id);
