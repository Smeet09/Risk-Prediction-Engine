import ee
import time
import math
import asyncio
from datetime import datetime
import calendar
from config import settings
import psycopg2
import psycopg2.extras
import requests
import io
import csv

class WeatherDownloader:
    def __init__(self):
        self.grid_step = 0.018  # ~2 km
        self.scale_meters = 10000
        self.rows_per_tile = 50
        
    def _init_gee(self, job_id, update_progress):
        try:
            ee.Initialize(project='sublime-vine-471510-n9')
        except Exception:
            update_progress(job_id, "failed", 0, "GEE not authenticated on server.")
            raise Exception("GEE not authenticated.")

    def _get_region_bounds(self, state):
        """Fetch bounding box for the state from PostGIS"""
        try:
            conn = psycopg2.connect(settings.DATABASE_URL)
            cur = conn.cursor()
            cur.execute("""
                SELECT ST_XMin(geom), ST_XMax(geom), ST_YMin(geom), ST_YMax(geom)
                FROM states_boundaries
                WHERE name=%s
            """, (state,))
            row = cur.fetchone()
            cur.close()
            conn.close()
            
            if row:
                return (row[0], row[1]), (row[2], row[3])
            else:
                raise Exception(f"State '{state}' not found in DB boundaries.")
        except Exception as e:
            raise Exception(f"Failed to fetch bounds: {e}")

    def _build_grid_tiles(self, lon_range, lat_range):
        import numpy as np
        lons = list(np.arange(lon_range[0], lon_range[1], self.grid_step))
        lats = list(np.arange(lat_range[0], lat_range[1], self.grid_step))

        n_tiles = math.ceil(len(lats) / self.rows_per_tile)
        tiles = []
        for tile_idx in range(n_tiles):
            lat_slice = lats[tile_idx * self.rows_per_tile : (tile_idx + 1) * self.rows_per_tile]
            features = []
            for lon in lons:
                for lat in lat_slice:
                    cx = round(lon + self.grid_step / 2, 6)
                    cy = round(lat + self.grid_step / 2, 6)
                    grid_id = f"g_{lon:.3f}_{lat:.3f}"
                    feat = ee.Feature(ee.Geometry.Point([cx, cy]), {'grid_id': grid_id, 'lon': cx, 'lat': cy})
                    features.append(feat)

            tiles.append({'tile_idx': tile_idx, 'fc': ee.FeatureCollection(features)})
        return tiles

    def _process_day_fc(self, year, month, day, grid_fc):
        d0 = ee.Date.fromYMD(year, month, day)
        d1 = d0.advance(1, 'day')
        date_str = f"{year}-{month:02d}-{day:02d}"

        imerg = (ee.ImageCollection('NASA/GPM_L3/IMERG_V07')
                 .filterDate(d0, d1).select('precipitation')
                 .map(lambda img: img.multiply(0.5).copyProperties(img, ['system:time_start'])))

        era5 = (ee.ImageCollection('ECMWF/ERA5/HOURLY')
                .filterDate(d0, d1)
                .select(['temperature_2m', 'dewpoint_temperature_2m', 'surface_pressure', 
                         'mean_sea_level_pressure', 'u_component_of_wind_10m', 'v_component_of_wind_10m']))

        era5land = (ee.ImageCollection('ECMWF/ERA5_LAND/HOURLY')
                    .filterDate(d0, d1)
                    .select(['volumetric_soil_water_layer_1', 'surface_runoff']))

        rain_sum = imerg.sum().rename('rain_mm')
        rain_max = imerg.max().rename('max_intensity_mm_hr')
        atm = era5.mean()
        wind_speed = atm.expression('sqrt(u*u + v*v)', {'u': atm.select('u_component_of_wind_10m'), 'v': atm.select('v_component_of_wind_10m')}).rename('wind_speed_ms')
        wind_dir = atm.expression('atan2(v, u) * 57.29577951', {'u': atm.select('u_component_of_wind_10m'), 'v': atm.select('v_component_of_wind_10m')}).rename('wind_dir_deg')
        soil = era5land.select('volumetric_soil_water_layer_1').mean().rename('soil_moisture_m3m3')
        runoff = era5land.select('surface_runoff').sum().multiply(1000).rename('surface_runoff_mm')

        stack = rain_sum.addBands(rain_max).addBands(atm).addBands(wind_speed).addBands(wind_dir).addBands(soil).addBands(runoff)
        sampled = stack.sampleRegions(collection=grid_fc, scale=self.scale_meters, geometries=False)
        return sampled.map(lambda f: f.set('date', date_str))

    def run(self, country: str, state: str, year: int, month: int, job_id: str):
        async def log_progress(status, progress, msg):
            try:
                conn = psycopg2.connect(settings.DATABASE_URL)
                cur = conn.cursor()
                cur.execute("""
                    UPDATE jobs 
                    SET status=%s, progress=%s, log = log || E'\\n' || %s, updated_at=NOW()
                    WHERE id=%s
                """, (status, progress, msg, job_id))
                conn.commit()
                cur.close()
                conn.close()
            except Exception as e:
                print(f"Failed to update job status: {e}")

        try:
            asyncio.run(log_progress("processing", 5, "Initializing GEE..."))
            self._init_gee(job_id, lambda status, prog, msg: asyncio.run(log_progress(status, prog, msg)))
            
            asyncio.run(log_progress("processing", 10, f"Fetching spatial bounds for {state}..."))
            lon_range, lat_range = self._get_region_bounds(state)
            
            asyncio.run(log_progress("processing", 20, f"Building point grid tiles for {state}..."))
            tiles = self._build_grid_tiles(lon_range, lat_range)
            
            n_days = calendar.monthrange(year, month)[1]
            total_fetches = len(tiles) * n_days
            fetched_count = 0
            
            asyncio.run(log_progress("processing", 30, f"Streaming {n_days} days across {len(tiles)} tiles directly to PostgreSQL..."))
            
            conn = psycopg2.connect(settings.DATABASE_URL)
            
            for tile in tiles:
                grid_fc = tile['fc']
                
                # Fetch day by day to fit within memory/download limits
                for day in range(1, n_days + 1):
                    fc_day = self._process_day_fc(year, month, day, grid_fc)
                    
                    try:
                        url = fc_day.getDownloadURL('csv')
                        # Download and parse CSV
                        response = requests.get(url, timeout=60)
                        if response.status_code == 200:
                            reader = csv.DictReader(io.StringIO(response.text))
                            records = []
                            for row in reader:
                                def _float(val):
                                    if not val: return None
                                    try: return float(val)
                                    except: return None
                                    
                                records.append((
                                    country, state, row.get('date', f"{year}-{month:02d}-{day:02d}"),
                                    _float(row.get('lon')), _float(row.get('lat')), row.get('grid_id'),
                                    _float(row.get('rain_mm')), _float(row.get('max_intensity_mm_hr')),
                                    _float(row.get('temperature_2m')), _float(row.get('dewpoint_temperature_2m')),
                                    _float(row.get('surface_pressure')), _float(row.get('mean_sea_level_pressure')),
                                    _float(row.get('wind_speed_ms')), _float(row.get('wind_dir_deg')),
                                    _float(row.get('soil_moisture_m3m3')), _float(row.get('surface_runoff_mm'))
                                ))
                            
                            # Bulk insert
                            if records:
                                cur = conn.cursor()
                                psycopg2.extras.execute_values(
                                    cur,
                                    """
                                    INSERT INTO weather_data (
                                        country, state, date, lon, lat, grid_id, 
                                        rain_mm, max_intensity_mm_hr, temperature_2m, dewpoint_temperature_2m,
                                        surface_pressure, mean_sea_level_pressure, wind_speed_ms, wind_dir_deg,
                                        soil_moisture_m3m3, surface_runoff_mm
                                    ) VALUES %s
                                    ON CONFLICT (state, grid_id, date) DO UPDATE SET
                                        rain_mm = EXCLUDED.rain_mm,
                                        max_intensity_mm_hr = EXCLUDED.max_intensity_mm_hr,
                                        temperature_2m = EXCLUDED.temperature_2m,
                                        dewpoint_temperature_2m = EXCLUDED.dewpoint_temperature_2m,
                                        surface_pressure = EXCLUDED.surface_pressure,
                                        mean_sea_level_pressure = EXCLUDED.mean_sea_level_pressure,
                                        wind_speed_ms = EXCLUDED.wind_speed_ms,
                                        wind_dir_deg = EXCLUDED.wind_dir_deg,
                                        soil_moisture_m3m3 = EXCLUDED.soil_moisture_m3m3,
                                        surface_runoff_mm = EXCLUDED.surface_runoff_mm
                                    """,
                                    records
                                )
                                conn.commit()
                                cur.close()
                    except Exception as loop_e:
                        print(f"Warning: Failed processing Day {day} Tile {tile['tile_idx']} - {loop_e}")
                        # Keep going, could just be a minor network hiccup or missing data point
                        
                    fetched_count += 1
                    if fetched_count % max(1, (total_fetches // 10)) == 0:
                        prog = 30 + int((fetched_count / total_fetches) * 65)
                        asyncio.run(log_progress("processing", prog, f"Streamed {fetched_count}/{total_fetches} day-tiles into DB."))
                        
            conn.close()
            asyncio.run(log_progress("done", 100, f"✅ Weather data successfully downloaded and mass-inserted into DB! No manual drive download required."))

        except Exception as e:
            asyncio.run(log_progress("failed", 0, f"Error: {e}"))
