"""
diag_nc.py  —  Run this ONCE inside the venv to discover exact variable
               names returned by each CDS dataset for your region/period.

Usage (from gis-service folder):
    .\\venv\\Scripts\\python.exe diag_nc.py

This will download tiny test files and print the real NetCDF variable keys.
"""
import cdsapi, netCDF4 as nc, numpy as np, os, tempfile, zipfile

CDS_URL = "https://cds.climate.copernicus.eu/api"
CDS_KEY = "c8969bb3-79bf-4a62-951e-c0e2d6c1788d"

# Tiny area over Gujarat (adjust to your actual region if needed)
AREA = [24.5, 68.0, 20.0, 75.0]   # [N, W, S, E]
YEAR  = "2024"
MONTH = "01"
DAY   = ["01"]

def get_client():
    return cdsapi.Client(url=CDS_URL, key=CDS_KEY)

def extract_nc(path):
    try:
        if zipfile.is_zipfile(path):
            d = tempfile.mkdtemp()
            with zipfile.ZipFile(path, "r") as zf:
                zf.extractall(d)
            ncs = [os.path.join(d,f) for f in os.listdir(d) if f.endswith(".nc")]
            if ncs: return ncs[0]
    except Exception: pass
    return path

def inspect(label, path):
    path = extract_nc(path)
    ds = nc.Dataset(path)
    print(f"\n{'='*60}")
    print(f"  {label}")
    print(f"{'='*60}")
    print(f"  Dimensions : {list(ds.dimensions.keys())}")
    print(f"  Variables  : {list(ds.variables.keys())}")
    for v in ds.variables:
        var = ds.variables[v]
        print(f"    [{v}]  shape={var.shape}  dims={var.dimensions}", end="")
        try:
            ln = getattr(var, 'long_name', '')
            sn = getattr(var, 'short_name', '')
            if ln: print(f"  long_name='{ln}'", end="")
            if sn: print(f"  short_name='{sn}'", end="")
        except Exception: pass
        print()
    ds.close()
    try: os.remove(path)
    except Exception: pass

c = get_client()

# ── 1. ERA5 Single-Level Batch A (small variable set) ─────────────────────────
print("\n[1/4] Downloading ERA5 Single-Level Batch A sample...")
with tempfile.NamedTemporaryFile(suffix=".nc", delete=False) as f:
    p = f.name
try:
    c.retrieve("derived-era5-single-levels-daily-statistics", {
        "product_type": "reanalysis",
        "variable": ["2m_temperature", "total_precipitation",
                     "100m_u_component_of_wind", "boundary_layer_height"],
        "year": YEAR, "month": MONTH, "day": DAY,
        "daily_statistic": "daily_mean",
        "time_zone": "utc+00:00", "frequency": "1_hourly",
        "area": AREA, "data_format": "netcdf",
    }).download(p)
    inspect("ERA5 Single-Level Batch A — 4 variables", p)
except Exception as e:
    print(f"  FAILED: {e}")

# ── 2. ERA5 Single-Level Batch B ───────────────────────────────────────────────
print("\n[2/4] Downloading ERA5 Single-Level Batch B sample...")
with tempfile.NamedTemporaryFile(suffix=".nc", delete=False) as f:
    p = f.name
try:
    c.retrieve("derived-era5-single-levels-daily-statistics", {
        "product_type": "reanalysis",
        "variable": ["convective_available_potential_energy", "k_index",
                     "snow_depth", "mean_evaporation_rate"],
        "year": YEAR, "month": MONTH, "day": DAY,
        "daily_statistic": "daily_mean",
        "time_zone": "utc+00:00", "frequency": "1_hourly",
        "area": AREA, "data_format": "netcdf",
    }).download(p)
    inspect("ERA5 Single-Level Batch B — 4 variables", p)
except Exception as e:
    print(f"  FAILED: {e}")

# ── 3. ERA5-Land ───────────────────────────────────────────────────────────────
print("\n[3/4] Downloading ERA5-Land sample...")
with tempfile.NamedTemporaryFile(suffix=".nc", delete=False) as f:
    p = f.name
try:
    c.retrieve("derived-era5-land-daily-statistics", {
        "variable": ["leaf_area_index_high_vegetation",
                     "leaf_area_index_low_vegetation",
                     "volumetric_soil_water_layer_1",
                     "soil_temperature_level_1"],
        "year": YEAR, "month": MONTH, "day": DAY,
        "daily_statistic": "daily_mean",
        "time_zone": "utc+00:00", "frequency": "1_hourly",
        "area": AREA, "data_format": "netcdf",
    }).download(p)
    inspect("ERA5-Land — 4 variables", p)
except Exception as e:
    print(f"  FAILED: {e}")

# ── 4. ERA5 Pressure-Level (850 hPa)  ─────────────────────────────────────────
print("\n[4/4] Downloading ERA5 Pressure-Level 850 hPa sample...")
with tempfile.NamedTemporaryFile(suffix=".nc", delete=False) as f:
    p = f.name
try:
    c.retrieve("derived-era5-pressure-levels-daily-statistics", {
        "product_type": "reanalysis",
        "variable": ["relative_vorticity", "divergence",
                     "vertical_velocity", "specific_humidity", "relative_humidity"],
        "pressure_level": ["850"],
        "year": YEAR, "month": MONTH, "day": DAY,
        "daily_statistic": "daily_mean",
        "time_zone": "utc+00:00", "frequency": "1_hourly",
        "area": AREA, "data_format": "netcdf",
    }).download(p)
    inspect("ERA5 Pressure-Level 850 hPa — 5 variables", p)
except Exception as e:
    print(f"  FAILED: {e}")

print("\n\n✅  Diagnostic complete. Use the variable names printed above to update CDS_VAR_MAP.")
