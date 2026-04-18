import math
import calendar
from config import settings
import psycopg2
import psycopg2.extras
import cdsapi
import zipfile
import tempfile
import os
import numpy as np
import netCDF4 as nc
from scipy.interpolate import RegularGridInterpolator
from psycopg2.extensions import register_adapter, AsIs

# ---------------------------------------------------------------------------
# Numpy → PostgreSQL type adapters
# ---------------------------------------------------------------------------
def _np_float(v): return AsIs(v)
def _np_int(v):   return AsIs(v)

register_adapter(np.float64, _np_float)
register_adapter(np.float32, _np_float)
register_adapter(np.int64,   _np_int)
register_adapter(np.int32,   _np_int)

# ---------------------------------------------------------------------------
# CONFIRMED variable → NetCDF short-name mapping
# Each entry: (cds_dataset, cds_long_name, nc_short_name)
# The derived-era5-*-daily-statistics API returns ONE variable per request.
# ---------------------------------------------------------------------------

# ERA5 Single-Level variables  (one request each)
ERA5_SINGLE_VARS = {
    # nc_key          : cds long_name
    "tp"   : "total_precipitation",
    "cp"   : "convective_precipitation",
    "smlt" : "snowmelt",
    "tcwv" : "total_column_water_vapour",
    "msl"  : "mean_sea_level_pressure",
    "u10"  : "10m_u_component_of_wind",
    "v10"  : "10m_v_component_of_wind",
    "u100" : "100m_u_component_of_wind",
    "v100" : "100m_v_component_of_wind",
    "t2m"  : "2m_temperature",
    "d2m"  : "2m_dewpoint_temperature",
    "blh"  : "boundary_layer_height",
    "sst"  : "sea_surface_temperature",
    "mtpr" : "mean_total_precipitation_rate",
    "ssr"  : "surface_net_solar_radiation",
    "pev"  : "potential_evaporation",
    "sd"   : "snow_depth",
    "mer"  : "mean_evaporation_rate",
    "cape" : "convective_available_potential_energy",
    "cin"  : "convective_inhibition",
    "kx"   : "k_index",
    "cbh"  : "cloud_base_height",
}

# ERA5-Land variables  (one request each)
ERA5_LAND_VARS = {
    # nc_key   : cds long_name
    "swvl1"  : "volumetric_soil_water_layer_1",
    "swvl2"  : "volumetric_soil_water_layer_2",
    "sro"    : "surface_runoff",
    "stl1"   : "soil_temperature_level_1",
    "lai_hv" : "leaf_area_index_high_vegetation",   # CONFIRMED by diagnostic
    "lai_lv" : "leaf_area_index_low_vegetation",    # CONFIRMED by diagnostic
}

# ERA5 Pressure-Level variables: nc_key → (cds_long_name, preferred_level_hPa)
# Each combination is ONE separate request.
ERA5_PRESSURE_VARS = {
    "vo" : ("relative_vorticity", "850"),
    "d"  : ("divergence",         "200"),
    "w"  : ("vertical_velocity",  "500"),
    "q"  : ("specific_humidity",  "850"),
    "r"  : ("relative_humidity",  "700"),
}

# ---------------------------------------------------------------------------
# INSERT column list (44 columns, matches DB schema)
# ---------------------------------------------------------------------------
INSERT_COLUMNS = """
    country, state, date, lon, lat, grid_id,
    rain_mm, max_intensity_mm_hr, convective_precipitation_mm, snowmelt_mm,
    total_column_water_vapour_kg_m2, mean_sea_level_pressure_pa,
    wind_speed_ms, wind_dir_deg,
    u10_ms, v10_ms, u100_ms, v100_ms,
    temperature_2m_k, dewpoint_temperature_2m_k, boundary_layer_height_m,
    sea_surface_temperature_k,
    mean_total_precipitation_rate_kg_m2s,
    surface_net_solar_radiation_j_m2,
    potential_evaporation_m, snow_cover_fraction, mean_evaporation_rate_kg_m2s,
    cape_j_kg, cin_j_kg, k_index_k, cloud_base_height_m,
    soil_moisture_layer1_m3m3, soil_moisture_layer2_m3m3, surface_runoff_mm,
    soil_temperature_level1_k,
    lai_high, lai_low,
    relative_vorticity_850_s1, divergence_200_s1,
    vertical_velocity_500_pa_s, specific_humidity_850_kg_kg,
    relative_humidity_700_pct,
    significant_wave_height_m, peak_wave_period_s
"""

ON_CONFLICT_SET = """
    rain_mm                              = EXCLUDED.rain_mm,
    max_intensity_mm_hr                  = EXCLUDED.max_intensity_mm_hr,
    convective_precipitation_mm          = EXCLUDED.convective_precipitation_mm,
    snowmelt_mm                          = EXCLUDED.snowmelt_mm,
    total_column_water_vapour_kg_m2      = EXCLUDED.total_column_water_vapour_kg_m2,
    mean_sea_level_pressure_pa           = EXCLUDED.mean_sea_level_pressure_pa,
    wind_speed_ms                        = EXCLUDED.wind_speed_ms,
    wind_dir_deg                         = EXCLUDED.wind_dir_deg,
    u10_ms                               = EXCLUDED.u10_ms,
    v10_ms                               = EXCLUDED.v10_ms,
    u100_ms                              = EXCLUDED.u100_ms,
    v100_ms                              = EXCLUDED.v100_ms,
    temperature_2m_k                     = EXCLUDED.temperature_2m_k,
    dewpoint_temperature_2m_k            = EXCLUDED.dewpoint_temperature_2m_k,
    boundary_layer_height_m              = EXCLUDED.boundary_layer_height_m,
    sea_surface_temperature_k            = EXCLUDED.sea_surface_temperature_k,
    mean_total_precipitation_rate_kg_m2s = EXCLUDED.mean_total_precipitation_rate_kg_m2s,
    surface_net_solar_radiation_j_m2     = EXCLUDED.surface_net_solar_radiation_j_m2,
    potential_evaporation_m              = EXCLUDED.potential_evaporation_m,
    snow_cover_fraction                  = EXCLUDED.snow_cover_fraction,
    mean_evaporation_rate_kg_m2s         = EXCLUDED.mean_evaporation_rate_kg_m2s,
    cape_j_kg                            = EXCLUDED.cape_j_kg,
    cin_j_kg                             = EXCLUDED.cin_j_kg,
    k_index_k                            = EXCLUDED.k_index_k,
    cloud_base_height_m                  = EXCLUDED.cloud_base_height_m,
    soil_moisture_layer1_m3m3            = EXCLUDED.soil_moisture_layer1_m3m3,
    soil_moisture_layer2_m3m3            = EXCLUDED.soil_moisture_layer2_m3m3,
    surface_runoff_mm                    = EXCLUDED.surface_runoff_mm,
    soil_temperature_level1_k            = EXCLUDED.soil_temperature_level1_k,
    lai_high                             = EXCLUDED.lai_high,
    lai_low                              = EXCLUDED.lai_low,
    relative_vorticity_850_s1            = EXCLUDED.relative_vorticity_850_s1,
    divergence_200_s1                    = EXCLUDED.divergence_200_s1,
    vertical_velocity_500_pa_s           = EXCLUDED.vertical_velocity_500_pa_s,
    specific_humidity_850_kg_kg          = EXCLUDED.specific_humidity_850_kg_kg,
    relative_humidity_700_pct            = EXCLUDED.relative_humidity_700_pct,
    significant_wave_height_m            = EXCLUDED.significant_wave_height_m,
    peak_wave_period_s                   = EXCLUDED.peak_wave_period_s
"""


class WeatherDownloader:
    def __init__(self):
        self.grid_step    = 0.018   # ~2 km grid
        self.rows_per_tile = 50

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _cds_client(self):
        return cdsapi.Client(url=settings.CDS_URL, key=settings.CDS_KEY)

    def _build_cds_area(self, lon_range, lat_range):
        """[N, W, S, E] bounding box with 0.1° padding."""
        return [
            round(lat_range[1] + 0.1, 2),
            round(lon_range[0] - 0.1, 2),
            round(lat_range[0] - 0.1, 2),
            round(lon_range[1] + 0.1, 2),
        ]

    def _extract_nc(self, path):
        """If path is a zip file, extract and return the .nc inside it."""
        try:
            if zipfile.is_zipfile(path):
                d = tempfile.mkdtemp()
                with zipfile.ZipFile(path, "r") as zf:
                    zf.extractall(d)
                ncs = [os.path.join(d, f) for f in os.listdir(d) if f.endswith(".nc")]
                if ncs:
                    return ncs[0]
        except Exception:
            pass
        return path

    # ------------------------------------------------------------------
    # ONE-VARIABLE download  (the ONLY pattern that works with this API)
    # ------------------------------------------------------------------

    def _download_one_var(self, dataset, cds_long_name, extra_params, area, year, month, days):
        """
        Download a single ERA5 variable for the full month.
        Returns (nc_path, nc_short_key) or (None, None) on failure.
        """
        client = self._cds_client()
        with tempfile.NamedTemporaryFile(suffix=".nc", delete=False) as f:
            tmp = f.name
        try:
            params = {
                "variable": cds_long_name,
                "year": str(year),
                "month": f"{month:02d}",
                "day": days,
                "daily_statistic": "daily_mean",
                "time_zone": "utc+00:00",
                "frequency": "1_hourly",
                "area": area,
                "data_format": "netcdf",
            }
            params.update(extra_params)
            client.retrieve(dataset, params).download(tmp)
            nc_path = self._extract_nc(tmp)
            return nc_path
        except Exception as e:
            print(f"  WARNING: download failed for '{cds_long_name}': {e}")
            try: os.remove(tmp)
            except Exception: pass
            return None

    # ------------------------------------------------------------------
    # NetCDF → interpolated array for all days
    # Returns {day_idx: np.ndarray(shape=n_pts)} or {}
    # ------------------------------------------------------------------

    def _load_var_from_nc(self, nc_path, nc_short_key, lons, lats, n_days):
        """
        Interpolate one variable from an already-downloaded NetCDF onto
        the (lons × lats) grid.  Points ordered: outer=lons, inner=lats
        (matching _build_grid_tiles point order).
        """
        try:
            ds = nc.Dataset(nc_path)

            # Find the variable in the file (case-insensitive, exact key)
            vkey = None
            for k in ds.variables:
                if k.lower() == nc_short_key.lower():
                    vkey = k
                    break

            if vkey is None:
                avail = list(ds.variables.keys())
                print(f"  DEBUG: key '{nc_short_key}' not found. Available: {avail}")
                # Try to pick the first non-coordinate variable as fallback
                coord_names = {"latitude", "longitude", "lat", "lon",
                               "valid_time", "time", "number", "pressure_level"}
                for k in ds.variables:
                    if k.lower() not in coord_names:
                        vkey = k
                        print(f"  DEBUG: falling back to first data var '{k}'")
                        break
                if vkey is None:
                    ds.close()
                    return {}

            # Detect coordinate axes
            lat_key = next(
                (k for k in ds.variables if k.lower() in ("lat", "latitude", "lats")), None
            )
            lon_key = next(
                (k for k in ds.variables if k.lower() in ("lon", "longitude", "lons")), None
            )
            if lat_key is None or lon_key is None:
                print(f"  DEBUG: coordinates not found in {os.path.basename(nc_path)}")
                ds.close()
                return {}

            grid_lats = np.array(ds.variables[lat_key][:], dtype=np.float64)
            grid_lons = np.array(ds.variables[lon_key][:], dtype=np.float64)
            data      = np.array(ds.variables[vkey][:])   # (time [,level], lat, lon)
            ds.close()

            # Flatten extra leading dims  → (time, lat, lon)
            while data.ndim > 3:
                data = data[:, 0, :, :]

            # Sort so axes are strictly increasing (required by RegularGridInterpolator)
            lat_ord = np.argsort(grid_lats);  grid_lats = grid_lats[lat_ord];  data = data[:, lat_ord, :]
            lon_ord = np.argsort(grid_lons);  grid_lons = grid_lons[lon_ord];  data = data[:, :, lon_ord]

            # Evaluation points: outer=lons, inner=lats  (matches point ordering in tiles)
            pts = np.array([[lat, lon] for lon in lons for lat in lats], dtype=np.float64)

            result = {}
            for t in range(min(data.shape[0], n_days)):
                try:
                    interp = RegularGridInterpolator(
                        (grid_lats, grid_lons),
                        data[t].astype(np.float64),
                        method="linear",
                        bounds_error=False,
                        fill_value=None,
                    )
                    vals = interp(pts)
                    result[t] = vals
                except Exception as ie:
                    print(f"  DEBUG: interp error t={t}: {ie}")
                    result[t] = np.full(len(pts), np.nan)

            return result

        except Exception as e:
            print(f"  DEBUG: error reading '{nc_path}': {e}")
            return {}

    # ------------------------------------------------------------------
    # Grid construction
    # ------------------------------------------------------------------

    def _get_region_bounds(self, state):
        conn = psycopg2.connect(settings.DATABASE_URL)
        cur  = conn.cursor()
        cur.execute("""
            SELECT ST_XMin(geom), ST_XMax(geom), ST_YMin(geom), ST_YMax(geom)
            FROM states_boundaries WHERE name=%s
        """, (state,))
        row = cur.fetchone(); cur.close(); conn.close()
        if row:
            return (row[0], row[1]), (row[2], row[3])
        raise Exception(f"State '{state}' not found in states_boundaries.")

    def _build_grid_tiles(self, lon_range, lat_range):
        min_lon, max_lon = min(lon_range), max(lon_range)
        min_lat, max_lat = min(lat_range), max(lat_range)

        nx = max(4, math.ceil((max_lon - min_lon) / self.grid_step) + 1)
        ny = max(4, math.ceil((max_lat - min_lat) / self.grid_step) + 1)

        lons = np.linspace(min_lon, max_lon, nx)
        lats = np.linspace(min_lat, max_lat, ny)

        tiles = []
        n_tiles = math.ceil(len(lats) / self.rows_per_tile)
        for tile_idx in range(n_tiles):
            lat_slice = lats[tile_idx * self.rows_per_tile: (tile_idx + 1) * self.rows_per_tile]
            points = []
            # Outer=lons, inner=lats — MUST match interpolation point ordering
            for lon in lons:
                for lat in lat_slice:
                    grid_id = f"g_{lon:.4f}_{lat:.4f}"
                    points.append({"grid_id": grid_id,
                                   "lon": round(float(lon), 6),
                                   "lat": round(float(lat), 6)})
            tiles.append({
                "tile_idx": tile_idx,
                "points": points,
                "lons": [round(float(v), 6) for v in lons],
                "lats": [round(float(v), 6) for v in lat_slice],
            })
        return tiles

    # ------------------------------------------------------------------
    # Build one DB record tuple (one point, one day)
    # ------------------------------------------------------------------

    def _build_records(self, country, state, year, month, day_idx, tile, sv, lv, pv):
        """
        sv = {nc_key: {day: array}}  — single-level vars
        lv = {nc_key: {day: array}}  — land vars
        pv = {nc_key: {day: array}}  — pressure vars
        """
        n = len(tile["points"])

        def _get(store, key):
            arr = store.get(key, {}).get(day_idx, None)
            if arr is None:
                return np.full(n, np.nan)
            if len(arr) != n:
                print(f"  DEBUG: shape mismatch '{key}': got {len(arr)}, expected {n}")
                return np.full(n, np.nan)
            return arr

        tp   = _get(sv, "tp");   cp   = _get(sv, "cp");   smlt = _get(sv, "smlt")
        tcwv = _get(sv, "tcwv"); msl  = _get(sv, "msl")
        u10  = _get(sv, "u10");  v10  = _get(sv, "v10")
        u100 = _get(sv, "u100"); v100 = _get(sv, "v100")
        t2m  = _get(sv, "t2m");  d2m  = _get(sv, "d2m");  blh  = _get(sv, "blh")
        sst  = _get(sv, "sst");  mtpr = _get(sv, "mtpr"); ssr  = _get(sv, "ssr")
        pev  = _get(sv, "pev");  sd   = _get(sv, "sd");   mer  = _get(sv, "mer")
        cape = _get(sv, "cape"); cin  = _get(sv, "cin");   kx   = _get(sv, "kx")
        cbh  = _get(sv, "cbh")

        wind_speed = np.sqrt(u10**2 + v10**2)
        wind_dir   = np.degrees(np.arctan2(v10, u10))

        swvl1  = _get(lv, "swvl1"); swvl2 = _get(lv, "swvl2")
        sro    = _get(lv, "sro");   stl1  = _get(lv, "stl1")
        lai_hv = _get(lv, "lai_hv"); lai_lv = _get(lv, "lai_lv")

        vo  = _get(pv, "vo"); d_var = _get(pv, "d")
        w   = _get(pv, "w");  q    = _get(pv, "q"); r = _get(pv, "r")

        swh  = np.full(n, np.nan)
        pp1d = np.full(n, np.nan)

        def _v(arr, i):
            try:
                val = float(arr[i].item() if hasattr(arr[i], 'item') else arr[i])
                return None if (math.isnan(val) or math.isinf(val)) else val
            except Exception:
                return None

        date_str = f"{year}-{month:02d}-{day_idx+1:02d}"
        records  = []
        for i, pt in enumerate(tile["points"]):
            records.append((
                # ── identity ──────────────────────────────────────── (6)
                country, state, date_str,
                pt["lon"], pt["lat"], pt["grid_id"],
                # ── precipitation ─────────────────────────────────── (4)
                _v(tp,   i),    # rain_mm
                _v(mtpr, i),    # max_intensity_mm_hr
                _v(cp,   i),    # convective_precipitation_mm
                _v(smlt, i),    # snowmelt_mm
                # ── atmosphere ────────────────────────────────────── (2)
                _v(tcwv, i),    # total_column_water_vapour_kg_m2
                _v(msl,  i),    # mean_sea_level_pressure_pa
                # ── wind ──────────────────────────────────────────── (4)
                _v(wind_speed, i),  # wind_speed_ms
                _v(wind_dir,   i),  # wind_dir_deg
                _v(u10,  i),    # u10_ms
                _v(v10,  i),    # v10_ms
                _v(u100, i),    # u100_ms
                _v(v100, i),    # v100_ms
                # ── temperature / humidity ────────────────────────── (4)
                _v(t2m,  i),    # temperature_2m_k
                _v(d2m,  i),    # dewpoint_temperature_2m_k
                _v(blh,  i),    # boundary_layer_height_m
                _v(sst,  i),    # sea_surface_temperature_k
                # ── radiation / evaporation ───────────────────────── (5)
                _v(mtpr, i),    # mean_total_precipitation_rate_kg_m2s
                _v(ssr,  i),    # surface_net_solar_radiation_j_m2
                _v(pev,  i),    # potential_evaporation_m
                _v(sd,   i),    # snow_cover_fraction
                _v(mer,  i),    # mean_evaporation_rate_kg_m2s
                # ── instability ───────────────────────────────────── (4)
                _v(cape, i),    # cape_j_kg
                _v(cin,  i),    # cin_j_kg
                _v(kx,   i),    # k_index_k
                _v(cbh,  i),    # cloud_base_height_m
                # ── soil / land ───────────────────────────────────── (6)
                _v(swvl1,  i),  # soil_moisture_layer1_m3m3
                _v(swvl2,  i),  # soil_moisture_layer2_m3m3
                _v(sro,    i),  # surface_runoff_mm
                _v(stl1,   i),  # soil_temperature_level1_k
                _v(lai_hv, i),  # lai_high
                _v(lai_lv, i),  # lai_low
                # ── pressure-level dynamics ───────────────────────── (5)
                _v(vo,    i),   # relative_vorticity_850_s1
                _v(d_var, i),   # divergence_200_s1
                _v(w,     i),   # vertical_velocity_500_pa_s
                _v(q,     i),   # specific_humidity_850_kg_kg
                _v(r,     i),   # relative_humidity_700_pct
                # ── waves (not fetched — NULL) ────────────────────── (2)
                _v(swh,  i),    # significant_wave_height_m
                _v(pp1d, i),    # peak_wave_period_s
            ))
        return records

    # ------------------------------------------------------------------
    # Job progress logger
    # ------------------------------------------------------------------

    def _log(self, job_id, status, progress, msg):
        try:
            conn = psycopg2.connect(settings.DATABASE_URL)
            cur  = conn.cursor()
            cur.execute("""
                UPDATE jobs
                SET status=%s, progress=%s, log = log || E'\\n' || %s, updated_at=NOW()
                WHERE id=%s
            """, (status, progress, msg, job_id))
            conn.commit(); cur.close(); conn.close()
        except Exception as e:
            print(f"  log_progress error: {e}")

    # ------------------------------------------------------------------
    # Main entry point
    # ------------------------------------------------------------------

    def run(self, country: str, state: str, year: int, month: int, job_id: str):
        self._log(job_id, "processing", 5, "Initializing…")

        try:
            # ── Spatial setup ─────────────────────────────────────────────
            self._log(job_id, "processing", 8, f"Fetching bounds for {state}…")
            lon_range, lat_range = self._get_region_bounds(state)

            self._log(job_id, "processing", 10, "Building grid tiles…")
            tiles  = self._build_grid_tiles(lon_range, lat_range)
            area   = self._build_cds_area(lon_range, lat_range)
            n_days = calendar.monthrange(year, month)[1]
            days   = [f"{d:02d}" for d in range(1, n_days + 1)]

            total_vars = len(ERA5_SINGLE_VARS) + len(ERA5_LAND_VARS) + len(ERA5_PRESSURE_VARS)
            self._log(job_id, "processing", 12,
                f"Will download {total_vars} ERA5 variables × {n_days} days for {state}.")

            # ── Download every variable individually ──────────────────────
            # nc_store: {nc_short_key: nc_path}
            nc_store       = {}   # all successfully downloaded files
            failed_vars    = []

            downloaded = 0

            # Single-level
            for nc_key, cds_name in ERA5_SINGLE_VARS.items():
                path = self._download_one_var(
                    "derived-era5-single-levels-daily-statistics",
                    cds_name,
                    {"product_type": "reanalysis"},
                    area, year, month, days,
                )
                pct = 12 + int(downloaded / total_vars * 38)
                if path:
                    nc_store[nc_key] = path
                    downloaded += 1
                    self._log(job_id, "processing", pct,
                        f"Downloaded {nc_key} ({downloaded}/{total_vars})")
                else:
                    failed_vars.append(nc_key)
                    self._log(job_id, "processing", pct,
                        f"WARNING: failed to download '{nc_key}'")

            # Land
            for nc_key, cds_name in ERA5_LAND_VARS.items():
                path = self._download_one_var(
                    "derived-era5-land-daily-statistics",
                    cds_name,
                    {},
                    area, year, month, days,
                )
                pct = 12 + int(downloaded / total_vars * 38)
                if path:
                    nc_store[nc_key] = path
                    downloaded += 1
                    self._log(job_id, "processing", pct,
                        f"Downloaded {nc_key} ({downloaded}/{total_vars})")
                else:
                    failed_vars.append(nc_key)

            # Pressure-level (one request per variable per level)
            for nc_key, (cds_name, level) in ERA5_PRESSURE_VARS.items():
                path = self._download_one_var(
                    "derived-era5-pressure-levels-daily-statistics",
                    cds_name,
                    {"product_type": "reanalysis", "pressure_level": [level]},
                    area, year, month, days,
                )
                pct = 12 + int(downloaded / total_vars * 38)
                if path:
                    nc_store[nc_key] = path
                    downloaded += 1
                    self._log(job_id, "processing", pct,
                        f"Downloaded {nc_key} @ {level} hPa ({downloaded}/{total_vars})")
                else:
                    failed_vars.append(nc_key)

            self._log(job_id, "processing", 50,
                f"Downloads done: {len(nc_store)}/{total_vars} vars OK. "
                f"Inserting {n_days}d × {len(tiles)} tiles…")

            if failed_vars:
                self._log(job_id, "processing", 50,
                    f"Missing vars (will be NULL in DB): {', '.join(failed_vars)}")

            # ── Database insertion ─────────────────────────────────────────
            conn = psycopg2.connect(settings.DATABASE_URL)
            days_inserted = 0
            failed_days   = []

            for tile in tiles:
                tile_idx  = tile["tile_idx"]
                tile_lons = tile["lons"]
                tile_lats = tile["lats"]

                # Load & interpolate all vars for this tile
                sv = {}
                lv = {}
                pv = {}

                for nc_key, nc_path in nc_store.items():
                    data = self._load_var_from_nc(nc_path, nc_key, tile_lons, tile_lats, n_days)
                    if nc_key in ERA5_SINGLE_VARS:
                        sv[nc_key] = data
                    elif nc_key in ERA5_LAND_VARS:
                        lv[nc_key] = data
                    else:
                        pv[nc_key] = data

                for day_idx in range(n_days):
                    date_str = f"{year}-{month:02d}-{day_idx+1:02d}"
                    try:
                        records = self._build_records(
                            country, state, year, month, day_idx, tile, sv, lv, pv
                        )
                        if records:
                            cur = conn.cursor()
                            psycopg2.extras.execute_values(
                                cur,
                                f"INSERT INTO weather_data ({INSERT_COLUMNS}) VALUES %s "
                                f"ON CONFLICT (state, grid_id, date) DO UPDATE SET {ON_CONFLICT_SET}",
                                records,
                            )
                            conn.commit()
                            cur.close()
                            days_inserted += 1
                    except Exception as e:
                        failed_days.append(f"{date_str}|tile{tile_idx}: {str(e)[:80]}")
                        try: conn.rollback()
                        except Exception: pass

                pct = 50 + int((tile_idx + 1) / len(tiles) * 46)
                self._log(job_id, "processing", pct,
                    f"Tile {tile_idx+1}/{len(tiles)} done — {days_inserted} records inserted so far.")

            conn.close()

            # ── Cleanup temp files ─────────────────────────────────────────
            for path in nc_store.values():
                try: os.remove(path)
                except Exception: pass

            # ── Final report ───────────────────────────────────────────────
            total_expected = n_days * len(tiles)
            rate = (days_inserted / total_expected * 100) if total_expected else 0

            report = [
                "JOB COMPLETE",
                f"Region : {state}, {country}",
                f"Period : {year}-{month:02d}",
                f"Success: {days_inserted}/{total_expected} records ({rate:.1f}%)",
            ]
            if failed_vars:
                report.append(f"Missing vars: {', '.join(failed_vars)}")
            if failed_days:
                report.append("Failed days (first 10):")
                report += [f"  {d}" for d in failed_days[:10]]
                if len(failed_days) > 10:
                    report.append(f"  ...and {len(failed_days)-10} more")

            final_msg = "\n".join(report)
            print(final_msg)

            if days_inserted == 0:
                raise Exception("Zero records inserted. Check variable download logs above.")

            self._log(job_id, "done", 100, final_msg)

        except Exception as e:
            msg = f"FAILED: {e}"
            print(msg)
            self._log(job_id, "failed", 0, msg)
