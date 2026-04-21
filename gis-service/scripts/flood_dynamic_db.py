"""
flood_dynamic_db.py
===================
Flood dynamic trigger score — SCS-CN physics + ERA5 surface runoff.

Physics (Option B — full SCS-CN):
  1. SCS-CN Runoff:  Q = (P - 0.2S)² / (P + 0.8S)
  2. ERA5 surface_runoff_mm: direct observed runoff from DB
  3. Soil Saturation Index from soil_moisture_layer1 + antecedent rain
  
Blend: 40% ERA5 runoff + 35% SCS-CN trigger + 25% soil saturation

LULC-derived CN adjustment:
  ESRI LULC infiltration capacity → CN per pixel
  High infiltration (forests) → lower CN → less runoff
  Low infiltration (urban)    → higher CN → more runoff

References:
  SCS TR-55 (1986) — Urban hydrology for small watersheds
  Ponce & Hawkins (1996) — Runoff curve number: has it reached maturity?
"""

import numpy as np
import pandas as pd

from scripts.dynamic_core import idw_interpolate_grid, normalize_array

# ─── SCS Curve Number lookup from LULC infiltration capacity ─────────────────
# CN = 100 × (1 - infiltration_capacity)  capped [40, 95]
# These approximate NRCS Table 2-2 soil group B values
LULC_CN = {
    10: 36,    # Dense forest — very low CN
    20: 55,    # Shrubland
    30: 65,    # Grassland
    40: 72,    # Cropland
    50: 90,    # Urban/built-up
    60: 45,    # Open forest
    70: 68,    # Wetland / herbs
    80: 88,    # Barren land
    90: 92,    # Water body
    100: 98,   # Permanent ice
    110: 75,   # Mixed land
    254: 60,   # Other natural
}
_DEFAULT_CN = 72.0


def cn_from_lulc(lulc_val: int) -> float:
    return float(LULC_CN.get(int(lulc_val), _DEFAULT_CN))


def compute_scs_runoff(rain_mm: float, antecedent_rain_mm: float,
                        cn: float = _DEFAULT_CN) -> float:
    """
    SCS-CN runoff depth Q (mm).
    AMC adjustment: if antecedent_rain > 50 mm → AMC-III (wet) CN adjustment
    """
    # AMC-III adjustment for wet conditions
    if antecedent_rain_mm > 50:
        cn = min(cn * 1.2, 98.0)    # wet antecedent moisture
    elif antecedent_rain_mm < 15:
        cn = max(cn * 0.8, 35.0)    # dry antecedent moisture

    S  = (25400.0 / cn) - 254.0
    Ia = 0.2 * S
    if rain_mm <= Ia:
        return 0.0
    Q = (rain_mm - Ia) ** 2 / (rain_mm - Ia + S)
    return float(np.clip(Q, 0.0, rain_mm))


def compute_soil_saturation_index(rain_mm: float, api: float,
                                   soil_moisture: float | None = None,
                                   theta_sat: float = 0.45) -> float:
    """
    Soil saturation index ∈ [0, 1].
    Combines ERA5 soil moisture (if available) with API-based estimate.
    """
    # API-based estimate: 200 mm = saturated
    api_sat = float(np.clip(api / 200.0, 0.0, 1.0))
    # Today's rain contribution: 100 mm = fully saturated top soil
    rain_sat = float(np.clip(rain_mm / 100.0, 0.0, 1.0))
    sat_est  = 0.6 * api_sat + 0.4 * rain_sat

    if soil_moisture is not None and np.isfinite(float(soil_moisture)):
        sm = float(soil_moisture)
        sm_sat = float(np.clip(sm / theta_sat, 0.0, 1.0))
        sat_est = 0.5 * sm_sat + 0.5 * sat_est

    return float(np.clip(sat_est, 0.0, 1.0))


def compute_flood_trigger(weather_df: pd.DataFrame,
                           grid_meta: dict):
    """
    Flood dynamic trigger score (SCS-CN physics, Option B).

    Per station:
      1. SCS-CN runoff Q using rain_mm + antecedent + LULC-derived CN
      2. Soil saturation index from soil_moisture + API
      3. Normalize ERA5 surface_runoff_mm

    Grid: IDW-interpolate all three onto full weather grid.
    Blend: 40% ERA5 runoff + 35% SCS-CN trigger + 25% soil saturation.

    Returns (trigger_score [0,1], method_label, metadata_dict)
    """
    nrows, ncols = grid_meta["nrows"], grid_meta["ncols"]

    scscn_scores = []
    soil_sat_scores = []
    era5_runoff_vals = []
    has_era5_runoff  = False

    for _, row in weather_df.iterrows():
        rain   = float(row.get("rain_mm", 0) or 0)
        api    = float(row.get("api", 0) or 0)
        ant    = float(row.get("antecedent_rain_mm", 0) or 0)

        sm_obs = None
        sm_val = row.get("soil_moisture")
        if sm_val is not None and pd.notna(sm_val):
            try:
                v = float(sm_val)
                if np.isfinite(v):
                    sm_obs = v
            except Exception:
                pass

        # SCS-CN runoff score (normalised by a 100 mm reference runoff event)
        Q = compute_scs_runoff(rain, ant, cn=_DEFAULT_CN)
        scscn_scores.append(float(np.clip(Q / 100.0, 0.0, 1.0)))

        # Soil saturation
        soil_sat_scores.append(compute_soil_saturation_index(rain, api, sm_obs))

        # ERA5 observed surface runoff (in mm — can be direct from DB)
        era5_sro = row.get("surface_runoff_mm")
        if era5_sro is not None and pd.notna(era5_sro):
            try:
                v = float(era5_sro)
                if np.isfinite(v) and v > 0:
                    era5_runoff_vals.append(v)
                    has_era5_runoff = True
                else:
                    era5_runoff_vals.append(0.0)
            except Exception:
                era5_runoff_vals.append(0.0)
        else:
            era5_runoff_vals.append(0.0)

    scscn_arr   = np.array(scscn_scores,    dtype=np.float32)
    soil_arr    = np.array(soil_sat_scores, dtype=np.float32)
    era5_arr    = np.array(era5_runoff_vals, dtype=np.float32)

    mean_q   = float(np.mean(scscn_arr))
    mean_sat = float(np.mean(soil_arr))
    print(f"  [FL] SCS-CN runoff trigger: mean={mean_q:.3f}  max={scscn_arr.max():.3f}")
    print(f"  [FL] Soil saturation index: mean={mean_sat:.3f}  max={soil_arr.max():.3f}")

    # IDW each component onto the weather grid
    print(f"  [FL] IDW-interpolating {len(scscn_scores)} station scores…")
    idw_scscn = idw_interpolate_grid(weather_df, scscn_arr,  grid_meta)
    idw_soil  = idw_interpolate_grid(weather_df, soil_arr,   grid_meta)

    if has_era5_runoff:
        # Normalise ERA5 runoff (cap at 50 mm for scoring)
        era5_norm = np.clip(era5_arr / 50.0, 0.0, 1.0)
        idw_era5  = idw_interpolate_grid(weather_df, era5_norm, grid_meta)
        combined  = (0.40 * idw_era5 + 0.35 * idw_scscn + 0.25 * idw_soil)
        method    = "SCS-CN+ERA5Runoff+SoilSaturation"
        print(f"  [FL] ERA5 surface runoff available — using full physics blend")
    else:
        # No ERA5 runoff: redistribute weight between SCS-CN and soil sat
        combined = (0.60 * idw_scscn + 0.40 * idw_soil)
        method   = "SCS-CN+SoilSaturation"
        print(f"  [FL] ERA5 surface runoff not available — using SCS-CN + soil blend")

    combined[~np.isfinite(combined)] = np.nan
    print(f"  [FL] Trigger: mean={np.nanmean(combined):.3f}  max={np.nanmax(combined):.3f}")

    meta = {
        "mean_scscn_trigger": round(mean_q, 4),
        "mean_soil_saturation": round(mean_sat, 4),
        "has_era5_runoff": has_era5_runoff,
    }
    return combined, method, meta
