"""
landslide_dynamic_FINAL.py  (MERGED & WORKING)
=====================================================
Base : landslide_dynamic_B.py  (your working grid + CSV lookup)
Added: TOPMODEL soil moisture + Infinite-Slope Factor of Safety
       from landslide_dynamic.py (v6)

═══════════════════════════════════════════════════════════════════════
  WHAT EACH FILE CONTRIBUTED
═══════════════════════════════════════════════════════════════════════
landslide_dynamic_B.py  (YOUR ORIGINAL — KEPT AS-IS):
  ✓ load_weather()         — tile CSV glob, date parsing, API, antecedent
  ✓ build_grid()           — weather-native 2km grid from lat/lon
  ✓ aggregate_susceptibility() — reproject susc raster onto weather grid
  ✓ aggregate_lulc()       — tiled LULC reproject
  ✓ save_output()          — row/col lookup → correct CSV scores
  ✓ main()                 — working pipeline

landslide_dynamic.py  (v6 — PHYSICS ADDED HERE):
  + GEOTECH constants      — Himalayan soil/rock parameters
  + compute_topmodel_h_norm() — TOPMODEL daily water table depth
  + compute_fs_grid()      — Infinite-slope FS at every pixel
  + compute_weather_score_physics() — replaces statistical score
    • TOPMODEL h/z per station
    • IDW-interpolated station FS scores
    • 70% pixel FS + 30% IDW blend (if slope.tif available)
    • falls back to B's statistical score if slope.tif missing
  + apply_empirical_warning() — Watch/Warning/Alert labels
  + enriched CSV output    — h_norm, fs_trigger, warning_label

═══════════════════════════════════════════════════════════════════════
  HYDROLOGY PHYSICS
═══════════════════════════════════════════════════════════════════════
TOPMODEL (Beven & Kirkby 1979):
  Soil moisture deficit D(t) updated from daily water balance.
  D(t) = D(t-1) - P_eff(SCS-CN) + ET_est + Q_base
  h/z  = 1 - D(t)/D_max   (0=dry, 1=saturated)

INFINITE SLOPE FS (Skempton & DeLory 1957; Iverson 2000):
  FS(t) = tan(φ')/tan(β) × [1 - (γw/γ) × h/z]
          + c' / [γ × z × sin(β) × cos(β)]
  FS_score = sigmoid(1 - FS)   maps FS → [0,1] trigger score

GRID STRATEGY (from landslide_dynamic_B.py — unchanged):
  Weather grid = 2km ERA5 native resolution (from lat/lon in CSV)
  Susceptibility + slope TIFs reprojected onto weather grid
  Output raster = weather-grid resolution (~0.018°)

═══════════════════════════════════════════════════════════════════════
  USAGE
═══════════════════════════════════════════════════════════════════════
  python landslide_dynamic_FINAL.py
  python landslide_dynamic_FINAL.py 23-06-2024
  python landslide_dynamic_FINAL.py 23-06-2024 --antecedent-days 14
  python landslide_dynamic_FINAL.py 23-06-2024 --output-dir ./out

REFERENCES:
  Beven & Kirkby (1979) — TOPMODEL. Hydrol. Sci. Bull. 24(1).
  Guzzetti et al. (2008) — Rainfall thresholds. Met. Atmos. Phys. 98.
  Iverson (2000) — Rain infiltration & landslide triggering. WRR 36(7).
  Skempton & DeLory (1957) — Infinite slope. 4th ICSMFE London.
  Tarboton (1997) — D-infinity. Water Resour. Res. 33(2):309-319.
"""

import argparse
import numpy as np
import pandas as pd
import rasterio
import rasterio.windows
from rasterio.transform import from_bounds
from rasterio.warp import reproject, Resampling
from pathlib import Path
from datetime import timedelta

from landslide_config import (
    WEATHER_DIR,
    LULC_PATH,
    SUSCEPTIBILITY_PATH,
    OUTPUT_DYNAMIC_BASE,
    OUTPUT_STATIC,
    ANTECEDENT_DAYS,
    COMBO_WEIGHTS,
    LULC_ROOT_RISK,
    RAINFALL_THRESHOLDS_EMPIRICAL,
)

# ─────────────────────────────────────────────────────────────────────────────
#  CONSTANTS
# ─────────────────────────────────────────────────────────────────────────────

RISK_LABELS = {0: 'No Data', 1: 'Very Low', 2: 'Low',
               3: 'Moderate', 4: 'High', 5: 'Very High'}

_DEFAULT_THRESHOLDS = {
    "watch":   {"rain_mm": 50,  "api": 100, "soil_moisture": 0.60},
    "warning": {"rain_mm": 100, "api": 150, "soil_moisture": 0.75},
    "alert":   {"rain_mm": 150, "api": 200, "soil_moisture": 0.90},
}
try:
    _THRESHOLDS = RAINFALL_THRESHOLDS_EMPIRICAL or _DEFAULT_THRESHOLDS
except Exception:
    _THRESHOLDS = _DEFAULT_THRESHOLDS

_API_K = 0.85

# ─────────────────────────────────────────────────────────────────────────────
#  GEOTECHNICAL PARAMETERS
#  Himalayan defaults — Sah & Mazari (1998); Sharma & Mehta (2012)
# ─────────────────────────────────────────────────────────────────────────────
GEOTECH = {
    "phi_deg":    30.0,   # effective friction angle (degrees)
    "cohesion":    5.0,   # effective cohesion c' (kPa)
    "gamma":      18.0,   # soil bulk unit weight (kN/m³)
    "gamma_w":     9.81,  # water unit weight (kN/m³)
    "z_soil":      1.5,   # soil depth above slip surface (m)
    "theta_sat":   0.45,  # saturated volumetric water content (m³/m³)
    "K_sat":       1e-5,  # saturated hydraulic conductivity (m/s)
    "deficit_max": 80.0,  # maximum soil moisture deficit (mm)
    "CN":          75.0,  # SCS Curve Number (mixed forest/agri, Himalaya)
}


# ─────────────────────────────────────────────────────────────────────────────
#  UTILITY  (unchanged from landslide_dynamic_B.py)
# ─────────────────────────────────────────────────────────────────────────────

def read_raster(path):
    with rasterio.open(path) as src:
        arr  = src.read(1).astype(np.float32)
        meta = src.meta.copy()
        nd   = src.nodata
    if nd is not None:
        arr[arr == nd] = np.nan
    return arr, meta


def normalize_array(arr, low_is_high=False):
    valid = arr[np.isfinite(arr)]
    if len(valid) == 0:
        return arr.copy()
    p2, p98 = np.percentile(valid, 2), np.percentile(valid, 98)
    clipped = np.clip(arr, p2, p98)
    vmin, vmax = np.nanmin(clipped), np.nanmax(clipped)
    n = (clipped - vmin) / (vmax - vmin + 1e-10)
    if low_is_high:
        n = 1.0 - n
    n[~np.isfinite(arr)] = np.nan
    return n


def classify_fixed(arr):
    """Map composite [0,1] → 5-class risk (calibrated Himalayan thresholds)."""
    risk = np.zeros_like(arr, dtype=np.uint8)
    risk[arr >  0.00] = 1   # Very Low
    risk[arr >= 0.25] = 2   # Low
    risk[arr >= 0.45] = 3   # Moderate
    risk[arr >= 0.60] = 4   # High
    risk[arr >= 0.75] = 5   # Very High
    risk[~np.isfinite(arr)] = 0
    return risk


# ─────────────────────────────────────────────────────────────────────────────
#  STEP 1: LOAD WEATHER  (unchanged from landslide_dynamic_B.py — working)
# ─────────────────────────────────────────────────────────────────────────────

def load_weather(target_date_str, antecedent_days):
    """
    Load 2km ERA5 tile CSVs.
    File pattern: UK_2km_YYYY_MM_tile*.csv
    Computes antecedent_rain_mm and API (Kohler & Linsley 1951).
    """
    target_dt   = pd.to_datetime(target_date_str, dayfirst=True)
    start_dt    = target_dt - timedelta(days=antecedent_days)
    weather_dir = Path(WEATHER_DIR)

    all_dates     = pd.date_range(start=start_dt, end=target_dt, freq='D')
    months_needed = sorted(set((d.year, d.month) for d in all_dates))

    frames = []
    for yr, mo in months_needed:
        pattern    = f"UK_2km_{yr}_{mo:02d}_tile*.csv"
        tile_files = sorted(weather_dir.glob(pattern))

        if not tile_files:
            if yr == target_dt.year and mo == target_dt.month:
                raise FileNotFoundError(
                    f"Required tiles not found: {weather_dir / pattern}"
                )
            continue  # antecedent month missing — skip silently

        month_frames = []
        for tf in tile_files:
            df_tile        = pd.read_csv(tf)
            df_tile['date'] = pd.to_datetime(
                df_tile['date'], errors='coerce', format='mixed', dayfirst=True
            )
            month_frames.append(df_tile)

        frames.append(pd.concat(month_frames, ignore_index=True))

    df    = pd.concat(frames, ignore_index=True)
    today = df[df['date'] == target_dt].copy()
    if today.empty:
        sample = sorted(df['date'].dropna().unique())[:5]
        raise ValueError(
            f"No data for {target_date_str}. "
            f"First dates in file: {[str(d)[:10] for d in sample]}"
        )

    # Antecedent cumulative rainfall
    prior    = df[(df['date'] > start_dt) & (df['date'] < target_dt)]
    ant_rain = (prior.groupby('grid_id')['rain_mm'].sum()
                      .reset_index()
                      .rename(columns={'rain_mm': 'antecedent_rain_mm'}))
    today = today.merge(ant_rain, on='grid_id', how='left')
    today['antecedent_rain_mm'] = today['antecedent_rain_mm'].fillna(0)

    # API with exponential decay (k=0.85)
    api_vals = {gid: 0.0 for gid in today['grid_id']}
    for d in range(1, antecedent_days + 1):
        day_d    = target_dt - timedelta(days=d)
        day_data = df[df['date'] == day_d][['grid_id', 'rain_mm']]
        if not day_data.empty:
            day_data = day_data.set_index('grid_id')['rain_mm']
            for gid in api_vals:
                api_vals[gid] += day_data.get(gid, 0) * (_API_K ** d)
    today['api'] = today['grid_id'].map(api_vals).fillna(0)

    sm_col = next((c for c in today.columns
                   if 'soil' in c.lower() and 'moist' in c.lower()), None)
    today['_sm_col'] = sm_col or ''

    print(f"  Grid points  : {len(today)}")
    print(f"  Rain_mm      : mean={today['rain_mm'].mean():.1f}  "
          f"max={today['rain_mm'].max():.1f}")
    print(f"  API          : mean={today['api'].mean():.1f}  "
          f"max={today['api'].max():.1f}")
    if sm_col:
        print(f"  Soil ({sm_col}): mean={today[sm_col].mean():.3f}")

    return today


# ─────────────────────────────────────────────────────────────────────────────
#  STEP 2: BUILD WEATHER GRID  (unchanged from landslide_dynamic_B.py)
# ─────────────────────────────────────────────────────────────────────────────

def build_grid(weather_df):
    """
    Build a regular 2km grid from the unique lat/lon values in the weather CSV.
    Assigns row/col index to every station for direct array lookup later.
    This is what makes the CSV scores correct — no rasterio_rowcol needed.
    """
    lats = np.sort(weather_df['lat'].unique())
    lons = np.sort(weather_df['lon'].unique())
    dlat = np.median(np.diff(lats))
    dlon = np.median(np.diff(lons))
    transform = from_bounds(
        lons.min() - dlon / 2, lats.min() - dlat / 2,
        lons.max() + dlon / 2, lats.max() + dlat / 2,
        len(lons), len(lats)
    )
    weather_df = weather_df.copy()
    weather_df['row'] = np.searchsorted(lats, weather_df['lat']).clip(0, len(lats) - 1)
    weather_df['col'] = np.searchsorted(lons, weather_df['lon']).clip(0, len(lons) - 1)
    return weather_df, {
        'nrows': len(lats), 'ncols': len(lons),
        'transform': transform,
        'lats': lats, 'lons': lons,
        'crs': 'EPSG:4326',
    }


# ─────────────────────────────────────────────────────────────────────────────
#  STEP 3a: TOPMODEL WATER TABLE DEPTH  (from v6 — physics)
# ─────────────────────────────────────────────────────────────────────────────

def compute_topmodel_h_norm(rain_mm, api, antecedent_rain_mm,
                             soil_moisture_obs=None):
    """
    Estimate normalised water table depth h(t)/z per weather station.

    TOPMODEL (Beven & Kirkby 1979):
      D(t) = max(0, min(D_max, D(t-1) - P_eff + ET + Q_base))
      h/z  = 1 - D(t)/D_max    [0=dry, 1=saturated]

    SCS Curve Number separates effective rainfall from runoff.
    Baseflow Q_base drains with exponential decay from deficit.

    Parameters
    ----------
    rain_mm           : today's rainfall (mm)
    api               : 14-day weighted Antecedent Precipitation Index (mm)
    antecedent_rain_mm: 14-day cumulative (mm)
    soil_moisture_obs : observed m³/m³ if available, else None

    Returns float h_norm ∈ [0, 1]
    """
    D_max   = GEOTECH["deficit_max"]
    K_sat   = GEOTECH["K_sat"] * 86400 * 1000   # m/s → mm/day
    CN      = GEOTECH["CN"]
    f_decay = 0.03   # TOPMODEL recession parameter

    # Initial deficit from API (high API = wet soil = low deficit)
    api_norm = min(api / 200.0, 1.0)
    deficit  = D_max * (1.0 - api_norm)

    # SCS-CN effective rainfall
    S  = (25400.0 / CN) - 254.0
    Ia = 0.2 * S
    P_eff = ((rain_mm - Ia) ** 2 / (rain_mm - Ia + S)
             if rain_mm > Ia else 0.0)

    # Baseflow drainage
    Q_base = K_sat * np.exp(-f_decay * deficit)

    # ET estimate for Himalayan monsoon (no temp data available)
    ET_est = 3.0   # mm/day

    # Updated deficit
    deficit_new = float(np.clip(deficit - P_eff + ET_est + Q_base, 0.0, D_max))
    h_norm      = 1.0 - deficit_new / D_max

    # Blend with observed soil moisture if available
    if soil_moisture_obs is not None:
        sm = float(soil_moisture_obs)
        if np.isfinite(sm) and sm > 0:
            h_obs  = sm / GEOTECH["theta_sat"]
            h_norm = 0.6 * min(h_obs, 1.0) + 0.4 * h_norm

    return float(np.clip(h_norm, 0.0, 1.0))


# ─────────────────────────────────────────────────────────────────────────────
#  STEP 3b: FACTOR OF SAFETY RASTER  (from v6 — physics)
# ─────────────────────────────────────────────────────────────────────────────

def compute_fs_grid(grid_meta, h_norm):
    """
    Compute dynamic Infinite-Slope FS at every pixel on the weather grid.

    Iverson (2000) / Skempton & DeLory (1957):
      FS(t) = tan(φ')/tan(β) × [1 - (γw/γ) × h/z]
              + c' / [γ × z × sin(β) × cos(β)]

    Slope raster (from OUTPUT_STATIC/slope.tif) is reprojected onto
    the weather grid (same build_grid() transform as everything else).

    Returns
    -------
    fs_grid  : 2-D float32, FS value per pixel
    fs_score : 2-D float32, sigmoid trigger score ∈ [0,1]
               FS=2.0 → ~0.0 (stable), FS=1.0 → 0.5, FS<0.5 → ~1.0
    """
    slope_path = Path(OUTPUT_STATIC) / "slope.tif"
    if not slope_path.exists():
        print(f"  WARNING: {slope_path} not found → skipping FS physics.")
        print("  Run landslide_static.py first to enable physics-based FS.")
        return None, None

    nrows, ncols = grid_meta['nrows'], grid_meta['ncols']
    transform    = grid_meta['transform']

    # Reproject slope onto weather grid
    slope_grid = np.full((nrows, ncols), np.nan, dtype=np.float32)
    with rasterio.open(slope_path) as src:
        reproject(
            source      = rasterio.band(src, 1),
            destination = slope_grid,
            dst_transform = transform,
            dst_crs     = 'EPSG:4326',
            resampling  = Resampling.bilinear,
            dst_nodata  = np.nan,
        )

    beta = np.radians(np.clip(slope_grid, 0.1, 89.9))

    phi_rad     = np.radians(GEOTECH["phi_deg"])
    c_pa        = GEOTECH["cohesion"] * 1000.0    # kPa → Pa
    g_nm3       = GEOTECH["gamma"]   * 1000.0     # kN/m³ → N/m³
    z           = GEOTECH["z_soil"]
    gamma_ratio = GEOTECH["gamma_w"] / GEOTECH["gamma"]

    # FS formula (Iverson 2000)
    tan_phi  = np.tan(phi_rad)
    tan_beta = np.maximum(np.tan(beta), 1e-6)

    friction_term  = tan_phi / tan_beta
    pore_term      = friction_term * gamma_ratio * h_norm
    denom          = g_nm3 * z * np.sin(beta) * np.cos(beta)
    denom          = np.where(denom < 1.0, 1.0, denom)
    cohesion_term  = c_pa / denom

    fs_grid = (friction_term - pore_term + cohesion_term).astype(np.float32)
    fs_grid = np.where(np.isfinite(slope_grid),
                       np.maximum(fs_grid, 0.01), np.nan)

    # Sigmoid: maps FS → trigger score
    # FS=2.0→0.05  FS=1.5→0.18  FS=1.0→0.50  FS=0.5→0.82  FS=0.1→~1.0
    fs_score = (1.0 / (1.0 + np.exp(3.0 * (fs_grid - 1.0)))).astype(np.float32)
    fs_score = np.where(np.isfinite(fs_grid), fs_score, np.nan)

    valid = fs_grid[np.isfinite(fs_grid)]
    if len(valid):
        print(f"  FS(t) h/z={h_norm:.3f}: mean={np.nanmean(valid):.2f}  "
              f"FS<1.0={( valid < 1.0).mean()*100:.1f}%  "
              f"FS<1.5={(valid < 1.5).mean()*100:.1f}%")

    return fs_grid, fs_score


# ─────────────────────────────────────────────────────────────────────────────
#  STEP 3c: IDW INTERPOLATION  (simple numpy — no scipy dependency)
# ─────────────────────────────────────────────────────────────────────────────

def idw_interpolate_grid(weather_df, point_scores, grid_meta, power=2):
    """
    IDW from station point_scores onto the weather grid.
    Uses numpy chunking to stay memory-safe.
    Stations are already on a regular 2km grid, so IDW gives smooth
    spatial rainfall variation between neighbouring ERA5 cells.
    """
    nrows = grid_meta['nrows']
    ncols = grid_meta['ncols']
    lats  = grid_meta['lats']
    lons  = grid_meta['lons']

    # Station coordinates and values
    sx = weather_df['lon'].values.astype(np.float32)
    sy = weather_df['lat'].values.astype(np.float32)
    sv = np.array(point_scores, dtype=np.float32)

    # Grid coordinate arrays
    grid_lon, grid_lat = np.meshgrid(lons, lats)   # (nrows, ncols)
    result = np.full((nrows, ncols), np.nan, dtype=np.float32)

    # Chunk by rows to stay under ~300 MB
    MAX_BYTES  = 300 * 1024 * 1024
    CHUNK_ROWS = max(1, min(nrows, MAX_BYTES // (ncols * len(sv) * 4)))

    for r0 in range(0, nrows, CHUNK_ROWS):
        r1 = min(r0 + CHUNK_ROWS, nrows)
        cx = grid_lon[r0:r1, :].ravel()   # (chunk_pix,)
        cy = grid_lat[r0:r1, :].ravel()

        dx   = cx[:, None] - sx[None, :]  # (chunk_pix, n_stations)
        dy   = cy[:, None] - sy[None, :]
        dist = np.sqrt(dx * dx + dy * dy)

        exact  = dist == 0.0
        d_safe = np.where(exact, np.float32(1e-10), dist)
        w      = 1.0 / (d_safe ** power)
        has_ex = exact.any(axis=1)
        w_ex   = np.where(exact, w, 0.0)

        interp = np.where(
            has_ex,
            (w_ex * sv[None, :]).sum(1) / (w_ex.sum(1) + 1e-20),
            (w    * sv[None, :]).sum(1) / (w.sum(1)    + 1e-20),
        )
        result[r0:r1, :] = interp.reshape(r1 - r0, ncols)

    return result


# ─────────────────────────────────────────────────────────────────────────────
#  STEP 3d: PHYSICS WEATHER SCORE  (replaces compute_weather_score from B)
#           Falls back to B's statistical score if slope.tif missing
# ─────────────────────────────────────────────────────────────────────────────

def compute_weather_score(weather_df, grid_meta):
    """
    Primary path: TOPMODEL + FS physics
    Fallback:     Statistical score from B (rain/api/soil/antecedent).

    Physics path:
      1. TOPMODEL h/z per station (daily water balance, SCS-CN)
      2. Station-level FS score at mean slope 25°
      3. IDW interpolation of station scores onto weather grid
      4. FS raster at every pixel (if slope.tif exists)
      5. Blend: 70% pixel-FS + 30% IDW-station

    Fallback path (if slope.tif missing):
      rain×0.40 + api×0.30 + soil×0.20 + antecedent×0.10
      (unchanged from landslide_dynamic_B.py)

    Returns
    -------
    weather_score : (nrows, ncols) float32 array ∈ [0,1]
    used_physics  : bool
    h_norms       : array of TOPMODEL h/z per station (for CSV output)
    point_scores  : array of station-level FS trigger scores (for CSV)
    mean_h_norm   : float, mean water table depth (for reporting)
    """
    nrows, ncols = grid_meta['nrows'], grid_meta['ncols']
    sm_col       = weather_df['_sm_col'].iloc[0]
    slope_path   = Path(OUTPUT_STATIC) / "slope.tif"

    # ── Compute TOPMODEL h_norm + station FS score per station ────────────────
    h_norms      = []
    point_scores = []

    for _, row in weather_df.iterrows():
        sm_obs = None
        if sm_col and sm_col in row.index and pd.notna(row.get(sm_col)):
            try:
                v = float(row[sm_col])
                if np.isfinite(v):
                    sm_obs = v
            except Exception:
                pass

        h = compute_topmodel_h_norm(
            rain_mm            = float(row['rain_mm']),
            api                = float(row['api']),
            antecedent_rain_mm = float(row['antecedent_rain_mm']),
            soil_moisture_obs  = sm_obs,
        )
        h_norms.append(h)

        # Station-level FS at representative Himalayan slope = 25°
        phi_rad  = np.radians(GEOTECH["phi_deg"])
        gw_g     = GEOTECH["gamma_w"] / GEOTECH["gamma"]
        fs_pt    = (np.tan(phi_rad) / np.tan(np.radians(25.0))
                    * (1.0 - gw_g * h))
        fs_pt    = max(0.01, fs_pt)
        score_pt = float(1.0 / (1.0 + np.exp(3.0 * (fs_pt - 1.0))))
        point_scores.append(score_pt)

    h_norms      = np.array(h_norms,      dtype=np.float32)
    point_scores = np.array(point_scores, dtype=np.float32)
    mean_h_norm  = float(np.mean(h_norms))

    print(f"  TOPMODEL: mean h/z = {mean_h_norm:.3f}  "
          f"(0=dry, 1=sat)  "
          f"range=[{h_norms.min():.3f}, {h_norms.max():.3f}]")

    # ── IDW of station scores onto full grid ──────────────────────────────────
    print(f"  IDW-interpolating {len(point_scores)} station scores...")
    idw_surface = idw_interpolate_grid(weather_df, point_scores, grid_meta)

    # ── Physics FS raster (if slope.tif available) ────────────────────────────
    if not slope_path.exists():
        print(f"  WARNING: slope.tif not found → statistical fallback trigger.")
        used_physics = False
        weather_score = _statistical_weather_score(weather_df, grid_meta)
        return weather_score, used_physics, h_norms, point_scores, mean_h_norm

    print(f"  Computing FS(t) raster at h/z = {mean_h_norm:.3f}...")
    fs_grid, fs_score = compute_fs_grid(grid_meta, mean_h_norm)

    if fs_score is None:
        print("  FS computation failed → statistical fallback trigger.")
        used_physics  = False
        weather_score = _statistical_weather_score(weather_df, grid_meta)
        return weather_score, used_physics, h_norms, point_scores, mean_h_norm

    # ── Blend: 70% pixel-FS + 30% IDW ────────────────────────────────────────
    combined  = np.full((nrows, ncols), np.nan, dtype=np.float32)
    has_fs    = np.isfinite(fs_score)
    has_idw   = np.isfinite(idw_surface)
    both      = has_fs & has_idw

    combined[both]              = 0.70 * fs_score[both]    + 0.30 * idw_surface[both]
    combined[has_fs & ~has_idw] = fs_score[has_fs & ~has_idw]
    combined[~has_fs & has_idw] = idw_surface[~has_fs & has_idw]

    cov = np.isfinite(combined).mean() * 100
    print(f"  Physics trigger (70% FS + 30% IDW): "
          f"mean={np.nanmean(combined):.3f}  "
          f"max={np.nanmax(combined):.3f}  coverage={cov:.1f}%")

    return combined, True, h_norms, point_scores, mean_h_norm


def _statistical_weather_score(weather_df, grid_meta):
    """
    Original B-file statistical weather score.
    Used as fallback when slope.tif is missing.
    rain×0.40 + api×0.30 + soil×0.20 + antecedent×0.10
    """
    nrows, ncols = grid_meta['nrows'], grid_meta['ncols']
    rain_grid  = np.full((nrows, ncols), np.nan)
    ant_grid   = np.full((nrows, ncols), np.nan)
    api_grid   = np.full((nrows, ncols), np.nan)
    soil_grid  = np.full((nrows, ncols), np.nan)
    sm_col     = weather_df['_sm_col'].iloc[0]

    for _, row in weather_df.iterrows():
        r, c = int(row['row']), int(row['col'])
        rain_grid[r, c] = row['rain_mm']
        ant_grid[r, c]  = row['antecedent_rain_mm']
        api_grid[r, c]  = row['api']
        if sm_col and sm_col in row.index and pd.notna(row.get(sm_col)):
            soil_grid[r, c] = row[sm_col]

    r_rain  = normalize_array(np.sqrt(np.maximum(rain_grid, 0)))
    r_api   = normalize_array(api_grid)
    r_soil  = normalize_array(soil_grid)
    r_ant   = normalize_array(ant_grid)

    score = (r_rain * 0.40 + r_api * 0.30 + r_soil * 0.20 + r_ant * 0.10)
    score[~np.isfinite(score)] = np.nan
    return score


# ─────────────────────────────────────────────────────────────────────────────
#  STEP 4: AGGREGATE SUSCEPTIBILITY  (unchanged from landslide_dynamic_B.py)
# ─────────────────────────────────────────────────────────────────────────────

def aggregate_susceptibility(grid_meta):
    """
    Reproject susceptibility raster onto the weather grid using
    both mean and max resampling, then average them.
    This captures both the average terrain susceptibility and the
    highest-risk pixel within each 2km cell.
    """
    susc, susc_meta = read_raster(SUSCEPTIBILITY_PATH)
    nrows, ncols    = grid_meta['nrows'], grid_meta['ncols']
    transform       = grid_meta['transform']

    dest_mean = np.full((nrows, ncols), np.nan, dtype=np.float32)
    dest_max  = np.full((nrows, ncols), np.nan, dtype=np.float32)

    reproject(source=susc, destination=dest_mean,
              src_transform=susc_meta['transform'], src_crs=susc_meta['crs'],
              dst_transform=transform, dst_crs='EPSG:4326',
              resampling=Resampling.average,
              src_nodata=np.nan, dst_nodata=np.nan)

    reproject(source=susc, destination=dest_max,
              src_transform=susc_meta['transform'], src_crs=susc_meta['crs'],
              dst_transform=transform, dst_crs='EPSG:4326',
              resampling=Resampling.max,
              src_nodata=np.nan, dst_nodata=np.nan)

    dest_mean[dest_mean == 0] = np.nan
    dest_max[dest_max  == 0]  = np.nan
    susc_combined = dest_mean * 0.5 + dest_max * 0.5

    # Coverage check
    cov = np.isfinite(susc_combined).mean() * 100
    print(f"  Susceptibility coverage on weather grid: {cov:.1f}%")
    if cov < 50:
        print("  WARNING: <50% coverage — susceptibility raster may be")
        print("  clipped to inventory extent. Re-run landslide_logistic.py")
        print("  with UK_state.shp as mask for full Uttarakhand coverage.")

    norm = normalize_array(susc_combined)
    print(f"  Susceptibility: mean={np.nanmean(norm):.3f}  max={np.nanmax(norm):.3f}")
    return norm


# ─────────────────────────────────────────────────────────────────────────────
#  STEP 5: AGGREGATE LULC  (unchanged from landslide_dynamic_B.py)
# ─────────────────────────────────────────────────────────────────────────────

def aggregate_lulc(grid_meta):
    """Tiled LULC root-reinforcement risk reprojected onto weather grid."""
    nrows_out, ncols_out = grid_meta['nrows'], grid_meta['ncols']
    transform            = grid_meta['transform']
    risk_sum   = np.zeros((nrows_out, ncols_out), dtype=np.float64)
    risk_count = np.zeros((nrows_out, ncols_out), dtype=np.float64)

    with rasterio.open(LULC_PATH) as src:
        lulc_crs    = src.crs
        lulc_nodata = src.nodata
        lulc_h      = src.height
        lulc_w      = src.width

    TILE = 2000
    ntil = (lulc_h + TILE - 1) // TILE

    for ti, r0 in enumerate(range(0, lulc_h, TILE)):
        r1 = min(r0 + TILE, lulc_h)
        with rasterio.open(LULC_PATH) as src:
            win        = rasterio.windows.Window(0, r0, lulc_w, r1 - r0)
            tile       = src.read(1, window=win).astype(np.float32)
            tile_tr    = src.window_transform(win)
        if lulc_nodata is not None:
            tile[tile == lulc_nodata] = np.nan

        risk_tile = np.full_like(tile, np.nan)
        for cls, coeff in LULC_ROOT_RISK.items():
            risk_tile[tile == cls] = coeff

        tmp = np.full((nrows_out, ncols_out), np.nan, dtype=np.float32)
        reproject(source=risk_tile, destination=tmp,
                  src_transform=tile_tr, src_crs=lulc_crs,
                  dst_transform=transform, dst_crs='EPSG:4326',
                  resampling=Resampling.average,
                  src_nodata=np.nan, dst_nodata=np.nan)
        ok = np.isfinite(tmp)
        risk_sum[ok]   += tmp[ok]
        risk_count[ok] += 1

        if ntil <= 5 or (ti + 1) % max(1, ntil // 5) == 0:
            print(f"    LULC tile {ti+1}/{ntil}")

    dest = np.full((nrows_out, ncols_out), np.nan, dtype=np.float32)
    ok   = risk_count > 0
    dest[ok] = (risk_sum[ok] / risk_count[ok]).astype(np.float32)
    dest[dest <= 0] = np.nan
    norm = normalize_array(dest)
    print(f"  LULC risk: mean={np.nanmean(norm):.3f}  max={np.nanmax(norm):.3f}")
    return norm


# ─────────────────────────────────────────────────────────────────────────────
#  STEP 6: COMBINE & CLASSIFY
# ─────────────────────────────────────────────────────────────────────────────

def combine_and_classify(susc_norm, weather_score, lulc_norm, used_physics):
    """
    Composite = w_s×S + w_w×T + w_l×L
    Gracefully degrades if any layer is NaN at a pixel.
    """
    w_s = COMBO_WEIGHTS['susceptibility']
    w_w = COMBO_WEIGHTS['weather']
    w_l = COMBO_WEIGHTS['lulc']
    assert abs(w_s + w_w + w_l - 1.0) < 1e-6, "COMBO_WEIGHTS must sum to 1.0"

    method = "TOPMODEL + FS (Iverson 2000)" if used_physics else "Statistical IDW"
    print(f"  Trigger method: {method}")

    composite = np.full_like(susc_norm, np.nan)
    fs = np.isfinite(susc_norm)
    fw = np.isfinite(weather_score)
    fl = np.isfinite(lulc_norm)

    # All three layers
    m1 = fs & fw & fl
    composite[m1] = (susc_norm[m1] * w_s
                     + weather_score[m1] * w_w
                     + lulc_norm[m1] * w_l)

    # No LULC — redistribute its weight
    m2 = fs & fw & ~fl
    composite[m2] = (susc_norm[m2] * (w_s + w_l * 0.5)
                     + weather_score[m2] * (w_w + w_l * 0.5))

    # No weather — susceptibility + LULC only
    m3 = fs & ~fw
    if m3.any():
        tot = w_s + w_l
        composite[m3] = (susc_norm[m3] * (w_s / tot)
                         + lulc_norm[m3] * (w_l / tot))

    cov = np.isfinite(composite).mean() * 100
    print(f"  Composite: mean={np.nanmean(composite):.3f}  "
          f"max={np.nanmax(composite):.3f}  coverage={cov:.1f}%")

    return composite, classify_fixed(composite)


# ─────────────────────────────────────────────────────────────────────────────
#  STEP 7: EMPIRICAL WARNING LEVELS
# ─────────────────────────────────────────────────────────────────────────────

def apply_empirical_warning(df, thresholds):
    """Assign Watch / Warning / Alert based on empirical rain thresholds."""
    df = df.copy()
    df['warning_level'] = 0
    df['warning_label'] = 'None'
    sm_col = df.get('_sm_col', pd.Series(['']*len(df))).iloc[0] \
             if '_sm_col' in df.columns else ''
    has_sm = bool(sm_col) and sm_col in df.columns

    for lvl, (label, key) in enumerate(
        [('Watch','watch'), ('Warning','warning'), ('Alert','alert')], start=1
    ):
        thr  = thresholds.get(key, {})
        cond = ((df['rain_mm'] >= thr.get('rain_mm', 9999)) |
                (df['api']     >= thr.get('api',     9999)))
        if has_sm:
            cond = cond | (df[sm_col] >= thr.get('soil_moisture', 9999))
        df.loc[cond, 'warning_level'] = lvl
        df.loc[cond, 'warning_label'] = label

    return df


# ─────────────────────────────────────────────────────────────────────────────
#  STEP 8: SAVE OUTPUT  (B's row/col lookup — always correct)
# ─────────────────────────────────────────────────────────────────────────────

def save_output(composite, risk, grid_meta, weather_df,
                target_date_str, out_dir,
                used_physics=False, h_norms=None,
                point_scores=None, mean_h_norm=None):
    """
    Save risk raster, composite raster, and enriched CSV.
    CSV uses B's row/col direct lookup — guaranteed correct scores.
    """
    out_dir  = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    date_tag = target_date_str.replace("-", "")
    nrows, ncols = grid_meta['nrows'], grid_meta['ncols']

    base_meta = {
        'driver': 'GTiff', 'crs': 'EPSG:4326',
        'transform': grid_meta['transform'],
        'width': ncols, 'height': nrows,
        'count': 1, 'compress': 'lzw',
    }

    # ── Risk raster (uint8 1-5) ────────────────────────────────────────────────
    risk_path = out_dir / f"landslide_risk_{date_tag}.tif"
    m = base_meta.copy(); m.update({'dtype': 'uint8', 'nodata': 0})
    with rasterio.open(risk_path, 'w', **m) as dst:
        dst.write(risk[None, :, :])

    # ── Composite raster (float32 0-1) ────────────────────────────────────────
    comp_path = out_dir / f"landslide_composite_{date_tag}.tif"
    m2 = base_meta.copy(); m2.update({'dtype': 'float32', 'nodata': -9999.0})
    c  = composite.copy(); c[~np.isfinite(c)] = -9999.0
    with rasterio.open(comp_path, 'w', **m2) as dst:
        dst.write(c.astype(np.float32)[None, :, :])

    # ── CSV — direct row/col lookup (B's approach) ────────────────────────────
    df_out = weather_df.drop(columns=['_sm_col'], errors='ignore').copy()

    def lookup(arr, rows, cols, fill=np.nan):
        nr, nc = arr.shape
        return [float(arr[int(r), int(c)])
                if 0 <= int(r) < nr and 0 <= int(c) < nc else fill
                for r, c in zip(rows, cols)]

    df_out['composite_score']      = lookup(composite, df_out['row'], df_out['col'])
    df_out['landslide_risk_class'] = lookup(risk,      df_out['row'], df_out['col'], fill=0)
    df_out['landslide_risk_class'] = df_out['landslide_risk_class'].astype(int)
    df_out['landslide_risk_label'] = df_out['landslide_risk_class'].map(RISK_LABELS)
    df_out['composite_score']      = df_out['composite_score'].round(4)

    # Physics enrichment columns
    df_out['trigger_method'] = 'FS+TOPMODEL' if used_physics else 'Statistical'
    if h_norms is not None:
        df_out['h_norm']     = np.round(h_norms, 4)
    if point_scores is not None:
        df_out['fs_trigger'] = np.round(point_scores, 4)
    if mean_h_norm is not None:
        df_out['mean_water_table_hz'] = round(mean_h_norm, 4)
        state = ("SATURATED" if mean_h_norm > 0.8 else
                 "WET"       if mean_h_norm > 0.5 else
                 "MOIST"     if mean_h_norm > 0.3 else "DRY")
        df_out['soil_moisture_state'] = state

    # Warning levels
    df_out = apply_empirical_warning(df_out, _THRESHOLDS)

    # Column ordering: base | physics | risk | warning
    physics_cols = [c for c in ['h_norm','fs_trigger','mean_water_table_hz',
                                 'soil_moisture_state','trigger_method']
                    if c in df_out.columns]
    risk_cols    = ['composite_score','landslide_risk_class','landslide_risk_label']
    warn_cols    = ['warning_level','warning_label']
    drop_cols    = set(['row','col'] + physics_cols + risk_cols + warn_cols)
    base_cols    = [c for c in df_out.columns if c not in drop_cols]
    df_out       = df_out[base_cols + physics_cols + risk_cols + warn_cols]
    df_out       = df_out.drop(columns=['row','col'], errors='ignore')

    csv_path = out_dir / f"landslide_risk_assessment_{date_tag}.csv"
    df_out.to_csv(csv_path, index=False)

    # ── Console summary ────────────────────────────────────────────────────────
    print(f"\n  Risk distribution ({target_date_str}):")
    for cls in range(1, 6):
        n   = int((risk == cls).sum())
        pct = n / max(1, int(np.sum(risk > 0))) * 100
        bar = "█" * int(pct / 2)
        print(f"    {RISK_LABELS[cls]:12s}: {n:>6,} px  {pct:5.1f}%  {bar}")

    if mean_h_norm is not None:
        print(f"\n  Soil moisture: {state}  "
              f"(h/z = {mean_h_norm:.3f} = {mean_h_norm*100:.0f}% saturated)")

    for lbl in ('Alert', 'Warning', 'Watch'):
        n = int((df_out['warning_label'] == lbl).sum())
        if n: print(f"  {lbl:8s}: {n:4d} stations")

    return risk_path, csv_path


# ─────────────────────────────────────────────────────────────────────────────
#  MAIN
# ─────────────────────────────────────────────────────────────────────────────

def main(target_date=None, antecedent_days=None, output_dir=None):
    target_date     = target_date     or "23-06-2024"
    antecedent_days = antecedent_days or ANTECEDENT_DAYS
    out_dir         = output_dir      or OUTPUT_DYNAMIC_BASE

    assert abs(sum(COMBO_WEIGHTS.values()) - 1.0) < 1e-6, \
        "COMBO_WEIGHTS must sum to 1.0"

    print("=" * 65)
    print("  LANDSLIDE DYNAMIC RISK")
    print("=" * 65)
    print(f"  Date       : {target_date}")
    print(f"  Antecedent : {antecedent_days} days  |  API-k : {_API_K}")
    w = COMBO_WEIGHTS
    print(f"  Weights    : susc={w['susceptibility']}  "
          f"trigger={w['weather']}  lulc={w['lulc']}")
    g = GEOTECH
    print(f"  Geotech    : phi={g['phi_deg']}°  c={g['cohesion']}kPa  "
          f"z={g['z_soil']}m  γ={g['gamma']}kN/m³")
    print(f"  TOPMODEL   : CN={g['CN']}  D_max={g['deficit_max']}mm  "
          f"Ksat={g['K_sat']:.1e}m/s")
    print()

    print("[1/6] Loading weather data...")
    weather_df = load_weather(target_date, antecedent_days)

    print("\n[2/6] Building 2km weather grid...")
    weather_df, grid_meta = build_grid(weather_df)
    print(f"  Grid: {grid_meta['ncols']}×{grid_meta['nrows']} cells  "
          f"(lon={grid_meta['lons'].min():.2f}–{grid_meta['lons'].max():.2f}  "
          f"lat={grid_meta['lats'].min():.2f}–{grid_meta['lats'].max():.2f})")

    print("\n[3/6] Computing weather/physics trigger score...")
    weather_score, used_physics, h_norms, point_scores, mean_h_norm = \
        compute_weather_score(weather_df, grid_meta)

    print("\n[4/6] Aggregating susceptibility onto weather grid...")
    susc_norm = aggregate_susceptibility(grid_meta)

    print("\n[5/6] Aggregating LULC root-reinforcement risk...")
    lulc_norm = aggregate_lulc(grid_meta)

    print("\n[6/6] Combining layers and classifying...")
    composite, risk = combine_and_classify(susc_norm, weather_score,
                                            lulc_norm, used_physics)

    risk_path, csv_path = save_output(
        composite, risk, grid_meta, weather_df,
        target_date, out_dir,
        used_physics=used_physics,
        h_norms=h_norms,
        point_scores=point_scores,
        mean_h_norm=mean_h_norm,
    )

    print(f"\n  OUTPUT RASTER : {risk_path}")
    print(f"  OUTPUT CSV    : {csv_path}")
    print(f"\n  QGIS → load {risk_path.name}")
    print(f"  → Symbology → Paletted/Unique Values → Classify")
    print(f"  → 1=green  2=lime  3=yellow  4=orange  5=dark-red  0=transparent")
    if used_physics:
        print(f"\n  Physics active: TOPMODEL h/z + Infinite-Slope FS")
        print(f"  Add factor_of_safety_{target_date.replace('-','')}.tif if generated separately")

    return risk_path, csv_path


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Landslide dynamic risk v7 — TOPMODEL + FS merged")
    parser.add_argument("date", nargs="?", default="23-06-2024",
                        help="Target date DD-MM-YYYY (default: 23-06-2024)")
    parser.add_argument("--output-dir",      default=None,
                        help="Output directory (default: from config)")
    parser.add_argument("--antecedent-days", type=int, default=None,
                        help="Antecedent days (default: from config)")
    args = parser.parse_args()
    main(target_date=args.date,
         antecedent_days=args.antecedent_days,
         output_dir=args.output_dir)