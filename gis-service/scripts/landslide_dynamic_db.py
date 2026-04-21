"""
landslide_dynamic_db.py
=======================
Landslide dynamic trigger score — reads from DB instead of CSV files.
Uses the same TOPMODEL + Infinite-Slope FS physics as landslide_dynamic_FINAL.py.

Physics:
  TOPMODEL (Beven & Kirkby 1979): D(t) = D(t-1) - P_eff + ET + Q_base
  Infinite-Slope FS (Iverson 2000 / Skempton & DeLory 1957):
      FS(t) = tan(φ')/tan(β) × [1 - (γw/γ) × h/z] + c'/[γ × z × sin(β)cos(β)]
"""

import numpy as np
import pandas as pd
from pathlib import Path

import rasterio
from rasterio.warp import reproject, Resampling

from scripts.dynamic_core import (
    idw_interpolate_grid, normalize_array, build_grid
)

# ─── Geotechnical parameters (Himalayan defaults) ────────────────────────────
GEOTECH = {
    "phi_deg":    30.0,
    "cohesion":    5.0,
    "gamma":      18.0,
    "gamma_w":     9.81,
    "z_soil":      1.5,
    "theta_sat":   0.45,
    "K_sat":       1e-5,
    "deficit_max": 80.0,
    "CN":          75.0,
}

_API_K = 0.85


def compute_topmodel_h_norm(rain_mm: float, api: float,
                             antecedent_rain_mm: float,
                             soil_moisture_obs: float | None = None) -> float:
    """
    Estimate normalised water table depth h(t)/z per weather station.
    Identical to landslide_dynamic_FINAL.py — no changes.
    """
    D_max   = GEOTECH["deficit_max"]
    K_sat   = GEOTECH["K_sat"] * 86400 * 1000   # m/s → mm/day
    CN      = GEOTECH["CN"]
    f_decay = 0.03

    api_norm = min(api / 200.0, 1.0)
    deficit  = D_max * (1.0 - api_norm)

    S  = (25400.0 / CN) - 254.0
    Ia = 0.2 * S
    P_eff = ((rain_mm - Ia) ** 2 / (rain_mm - Ia + S)
             if rain_mm > Ia else 0.0)

    Q_base = K_sat * np.exp(-f_decay * deficit)
    ET_est = 3.0

    deficit_new = float(np.clip(deficit - P_eff + ET_est + Q_base, 0.0, D_max))
    h_norm      = 1.0 - deficit_new / D_max

    if soil_moisture_obs is not None:
        sm = float(soil_moisture_obs)
        if np.isfinite(sm) and sm > 0:
            h_obs  = sm / GEOTECH["theta_sat"]
            h_norm = 0.6 * min(h_obs, 1.0) + 0.4 * h_norm

    return float(np.clip(h_norm, 0.0, 1.0))


def compute_fs_grid(grid_meta: dict, h_norm: float,
                     slope_path: str | None = None):
    """
    Compute Infinite-Slope FS raster at every pixel on the weather grid.
    Returns (fs_grid, fs_score) or (None, None) if slope raster missing.
    """
    if slope_path is None or not Path(slope_path).exists():
        print("  [LS] slope.tif not found → skipping FS physics")
        return None, None

    nrows, ncols = grid_meta["nrows"], grid_meta["ncols"]
    transform    = grid_meta["transform"]

    slope_grid = np.full((nrows, ncols), np.nan, dtype=np.float32)
    with rasterio.open(slope_path) as src:
        reproject(
            source=rasterio.band(src, 1),
            destination=slope_grid,
            dst_transform=transform, dst_crs="EPSG:4326",
            resampling=Resampling.bilinear,
            dst_nodata=np.nan,
        )

    beta = np.radians(np.clip(slope_grid, 0.1, 89.9))

    phi_rad     = np.radians(GEOTECH["phi_deg"])
    c_pa        = GEOTECH["cohesion"] * 1000.0
    g_nm3       = GEOTECH["gamma"]   * 1000.0
    z           = GEOTECH["z_soil"]
    gamma_ratio = GEOTECH["gamma_w"] / GEOTECH["gamma"]

    tan_phi  = np.tan(phi_rad)
    tan_beta = np.maximum(np.tan(beta), 1e-6)

    friction_term = tan_phi / tan_beta
    pore_term     = friction_term * gamma_ratio * h_norm
    denom         = g_nm3 * z * np.sin(beta) * np.cos(beta)
    denom         = np.where(denom < 1.0, 1.0, denom)
    cohesion_term = c_pa / denom

    fs_grid = (friction_term - pore_term + cohesion_term).astype(np.float32)
    fs_grid = np.where(np.isfinite(slope_grid), np.maximum(fs_grid, 0.01), np.nan)

    fs_score = (1.0 / (1.0 + np.exp(3.0 * (fs_grid - 1.0)))).astype(np.float32)
    fs_score = np.where(np.isfinite(fs_grid), fs_score, np.nan)

    valid = fs_grid[np.isfinite(fs_grid)]
    if len(valid):
        print(f"  [LS] FS h/z={h_norm:.3f}: mean={np.nanmean(valid):.2f}  "
              f"FS<1.0={(valid < 1.0).mean() * 100:.1f}%")

    return fs_grid, fs_score


def compute_landslide_trigger(weather_df: pd.DataFrame,
                               grid_meta: dict,
                               slope_path: str | None = None):
    """
    Primary landslide dynamic trigger score.
    Path A (with slope.tif): TOPMODEL h/z + Infinite-Slope FS
    Path B (no slope.tif):   Statistical — rain×0.40 + api×0.30 + soil×0.20 + ant×0.10

    Returns (trigger_score [0,1], method_label, metadata_dict)
    """
    nrows, ncols = grid_meta["nrows"], grid_meta["ncols"]
    h_norms      = []
    point_scores = []

    for _, row in weather_df.iterrows():
        sm_obs = None
        sm_val = row.get("soil_moisture")
        if sm_val is not None and pd.notna(sm_val):
            try:
                v = float(sm_val)
                if np.isfinite(v):
                    sm_obs = v
            except Exception:
                pass

        h = compute_topmodel_h_norm(
            rain_mm            = float(row["rain_mm"]),
            api                = float(row["api"]),
            antecedent_rain_mm = float(row["antecedent_rain_mm"]),
            soil_moisture_obs  = sm_obs,
        )
        h_norms.append(h)

        # Station-level FS at representative Himalayan slope = 25°
        phi_rad = np.radians(GEOTECH["phi_deg"])
        gw_g    = GEOTECH["gamma_w"] / GEOTECH["gamma"]
        fs_pt   = (np.tan(phi_rad) / np.tan(np.radians(25.0)) * (1.0 - gw_g * h))
        fs_pt   = max(0.01, fs_pt)
        score_pt = float(1.0 / (1.0 + np.exp(3.0 * (fs_pt - 1.0))))
        point_scores.append(score_pt)

    h_norms      = np.array(h_norms,      dtype=np.float32)
    point_scores = np.array(point_scores, dtype=np.float32)
    mean_h_norm  = float(np.mean(h_norms))

    state = ("SATURATED" if mean_h_norm > 0.8 else
             "WET"       if mean_h_norm > 0.5 else
             "MOIST"     if mean_h_norm > 0.3 else "DRY")
    print(f"  [LS] TOPMODEL h/z={mean_h_norm:.3f} ({state})  "
          f"range=[{h_norms.min():.3f}, {h_norms.max():.3f}]")

    print(f"  [LS] IDW-interpolating {len(point_scores)} station scores…")
    idw_surface = idw_interpolate_grid(weather_df, point_scores, grid_meta)

    # Try physics path
    fs_grid, fs_score = compute_fs_grid(grid_meta, mean_h_norm, slope_path)

    if fs_score is not None:
        combined  = np.full((nrows, ncols), np.nan, dtype=np.float32)
        has_fs    = np.isfinite(fs_score)
        has_idw   = np.isfinite(idw_surface)
        both      = has_fs & has_idw
        combined[both]              = 0.70 * fs_score[both]  + 0.30 * idw_surface[both]
        combined[has_fs & ~has_idw] = fs_score[has_fs & ~has_idw]
        combined[~has_fs & has_idw] = idw_surface[~has_fs & has_idw]
        method = "TOPMODEL+InfiniteSlope-FS"
    else:
        # Statistical fallback
        combined = _statistical_fallback(weather_df, grid_meta)
        method   = "Statistical-IDW"

    print(f"  [LS] Trigger method: {method}")
    meta = {
        "mean_h_norm": round(mean_h_norm, 4),
        "soil_moisture_state": state,
        "h_norms": h_norms.tolist(),
        "point_scores": point_scores.tolist(),
    }
    return combined, method, meta


def _statistical_fallback(weather_df: pd.DataFrame, grid_meta: dict) -> np.ndarray:
    nrows, ncols = grid_meta["nrows"], grid_meta["ncols"]
    rain_grid = np.full((nrows, ncols), np.nan)
    ant_grid  = np.full((nrows, ncols), np.nan)
    api_grid  = np.full((nrows, ncols), np.nan)
    soil_grid = np.full((nrows, ncols), np.nan)

    for _, row in weather_df.iterrows():
        r, c = int(row["row"]), int(row["col"])
        rain_grid[r, c] = row["rain_mm"]
        ant_grid[r, c]  = row["antecedent_rain_mm"]
        api_grid[r, c]  = row["api"]
        sm = row.get("soil_moisture")
        if sm is not None and pd.notna(sm):
            soil_grid[r, c] = float(sm)

    r_rain = normalize_array(np.sqrt(np.maximum(rain_grid, 0)))
    r_api  = normalize_array(api_grid)
    r_soil = normalize_array(soil_grid)
    r_ant  = normalize_array(ant_grid)

    score = r_rain * 0.40 + r_api * 0.30 + r_soil * 0.20 + r_ant * 0.10
    score[~np.isfinite(score)] = np.nan
    return score
