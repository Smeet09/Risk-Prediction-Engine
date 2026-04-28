# =============================================================================
# pipeline/yield_pipeline.py
# Build a synthetic current-year row from TIFF analysis + NASA POWER weather,
# append it to my_data.csv historical rows, and run xgb_engine.compute_risk_core
# to get 3-year yield forecast + Monte Carlo risk.
# =============================================================================

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
import pandas as pd
from datetime import date
from typing import Optional

try:
    from xgb_engine import (
        engineer_weather, build_full_X, compute_risk_core,
        YIELD_COL, FEAT_COLS,
    )
    _ENGINE_OK = True
except ImportError:
    _ENGINE_OK = False

# Crop name mapping: RF classifier name → my_data.csv Crops column value
CROP_MAP_TO_DATA = {
    "rice":      "rice",
    "wheat":     "wheat",
    "groundnut": "groundnut",
    "cotton":    "cotton",
    "sugarcane": "sugarcane",
    "maize":     "maize",
    "soybean":   "soyabean",
    "mustard":   "rapeseed and mustard",
    "others":    None,
}


def load_crop_history(
    my_data_path: str,
    crop: str,
    season: Optional[str] = None,
    min_rows: int = 30,
) -> Optional[pd.DataFrame]:
    """
    Load historical records for a given crop from my_data.csv.
    Returns filtered DataFrame or None if insufficient data.
    Filters out zero/negative yields to improve model quality.
    """
    try:
        df = pd.read_csv(my_data_path)
    except Exception as e:
        print(f"[yield_pipeline] Cannot load {my_data_path}: {e}")
        return None

    data_crop = CROP_MAP_TO_DATA.get(crop.lower())
    if data_crop is None:
        print(f"[yield_pipeline] No data mapping for crop: {crop}")
        return None

    mask = df["Crops"].str.lower() == data_crop.lower()
    if season:
        mask = mask & (df["Season"].str.lower() == season.lower())

    sub = df[mask].copy()
    if len(sub) < min_rows:
        # Try without season filter
        sub = df[df["Crops"].str.lower() == data_crop.lower()].copy()

    # Remove zero/negative yields — they skew the model (especially cotton ~52% zeros)
    yield_col = "YIELD (Kg per ha)"
    if yield_col in sub.columns:
        sub = sub[sub[yield_col] > 50].copy()  # >50 kg/ha minimum threshold

    if sub.empty:
        print(f"[yield_pipeline] No valid yield rows for {crop} after filtering")
        return None

    if len(sub) < min_rows:
        print(f"[yield_pipeline] Only {len(sub)} rows for {crop} — may be less reliable")

    return sub


def inject_current_row(
    hist_df: pd.DataFrame,
    crop: str,
    season: str,
    image_year: int,
    agri_area_ha: float,
    actual_weather: dict,
    ideal_weather: dict,
) -> pd.DataFrame:
    """
    Build a synthetic 'current year' row from TIFF + NASA POWER data
    and append it to the historical DataFrame.

    agri_area_ha   : agriculture area in hectares from TIFF
    actual_weather : {nasa_param: float}  (season averages)
    ideal_weather  : {ideal_col: float}  (from my_data.csv ideal cols)
    """
    # Pick a representative state / district from the historical data
    state = hist_df["State Name"].mode()[0] if "State Name" in hist_df.columns else "india"
    dist  = hist_df["Dist Name"].mode()[0]  if "Dist Name"  in hist_df.columns else "unknown"

    # Convert area: my_data uses '1000 ha' units
    area_1000ha = agri_area_ha / 1000.0

    # Estimate production from area × historical average yield
    avg_yield   = float(hist_df[YIELD_COL].dropna().mean()) if YIELD_COL in hist_df.columns else 2000.0
    prod_1000t  = area_1000ha * avg_yield / 1000.0   # 1000 ha × kg/ha / 1000 = kt

    data_crop = CROP_MAP_TO_DATA.get(crop.lower(), crop)

    new_row = {
        "Year":             image_year,
        "State Name":       state,
        "Dist Name":        dist,
        "Crops":            data_crop,
        "AREA (1000 ha)":   round(area_1000ha, 4),
        "PRODUCTION (1000 tons)": round(prod_1000t, 4),
        YIELD_COL:          avg_yield,   # placeholder; will be predicted
        "Season":           season,
        "Date Range":       f"{image_year-1}-11-01 to {image_year}-03-31",
        # Actual weather (NASA POWER season averages)
        "T2M_MAX":          actual_weather.get("T2M_MAX"),
        "T2M_MIN":          actual_weather.get("T2M_MIN"),
        "WS2M":             actual_weather.get("WS2M"),
        "RH2M":             actual_weather.get("RH2M"),
        "PRECTOTCORR":      actual_weather.get("PRECTOTCORR"),
        "ALLSKY_SFC_SW_DWN":  actual_weather.get("ALLSKY_SFC_SW_DWN"),
        "ALLSKY_SFC_SW_DIFF": actual_weather.get("ALLSKY_SFC_SW_DIFF"),
        # Ideal weather (from my_data.csv)
        "Tmin_Ideal":       ideal_weather.get("Tmin_Ideal"),
        "Tmax_Ideal":       ideal_weather.get("Tmax_Ideal"),
        "Rain_Ideal (mm/day)_Ideal":  ideal_weather.get("Rain_Ideal (mm/day)_Ideal"),
        "RH_Ideal (%)_Ideal":         ideal_weather.get("RH_Ideal (%)_Ideal"),
        "ALLSKY_SFC_SW_DIFF (MJ/m2/day)_Ideal": ideal_weather.get("ALLSKY_SFC_SW_DIFF (MJ/m2/day)_Ideal"),
        "ALLSKY_SFC_SW_DWN (MJ/m2/day)_Ideal":  ideal_weather.get("ALLSKY_SFC_SW_DWN (MJ/m2/day)_Ideal"),
        "WS2M_Ideal":       ideal_weather.get("WS2M_Ideal"),
    }

    new_df  = pd.DataFrame([new_row])
    combined = pd.concat([hist_df, new_df], ignore_index=True)

    # Fill missing weather cols from historical medians
    weather_cols = [
        "T2M_MAX","T2M_MIN","WS2M","RH2M","PRECTOTCORR",
        "ALLSKY_SFC_SW_DWN","ALLSKY_SFC_SW_DIFF",
    ]
    for col in weather_cols:
        if col in combined.columns:
            med = combined[col].dropna().median()
            combined[col] = combined[col].fillna(med if pd.notna(med) else 0.0)

    return combined


def run_yield_forecast(
    my_data_path: str,
    crop: str,
    season: str,
    image_year: int,
    agri_area_ha: float,
    actual_weather: dict,
    ideal_weather: dict,
    n_forecast: int = 3,
) -> Optional[dict]:
    """
    Full yield forecast pipeline:
    1. Load historical crop data
    2. Inject current year row
    3. Engineer features
    4. Run XGBoost + Monte Carlo
    5. Return results dict

    Returns the full result dict from compute_risk_core, plus extra keys:
      'crop', 'season', 'area_ha', 'engine_ok'
    Returns None if engine unavailable or insufficient data.
    """
    if not _ENGINE_OK:
        return {"engine_ok": False, "error": "xgb_engine not available"}

    hist_df = load_crop_history(my_data_path, crop, season)
    if hist_df is None:
        return {"engine_ok": False, "error": f"No historical data for crop: {crop}"}

    combined = inject_current_row(
        hist_df,
        crop=crop,
        season=season,
        image_year=image_year,
        agri_area_ha=agri_area_ha,
        actual_weather=actual_weather,
        ideal_weather=ideal_weather,
    )

    try:
        df_eng = engineer_weather(combined.copy())
    except Exception as e:
        return {"engine_ok": False, "error": f"Feature engineering failed: {e}"}

    for c in FEAT_COLS:
        if c not in df_eng.columns:
            df_eng[c] = 0.0

    X_all = df_eng[FEAT_COLS].fillna(0).values.astype(np.float64)
    y_all = df_eng[YIELD_COL].fillna(0).values.astype(np.float64)
    years = df_eng["Year"].fillna(image_year).values.astype(int)

    # mean_cat is expected by signature but not used inside — pass zeros
    mean_cat = np.zeros(len(FEAT_COLS))

    try:
        result = compute_risk_core(
            X_all=X_all,
            y_all=y_all,
            years=years,
            mean_cat=mean_cat,
            n_forecast=n_forecast,
        )
    except Exception as e:
        return {"engine_ok": False, "error": f"compute_risk_core failed: {e}"}

    result["engine_ok"]  = True
    result["crop"]       = crop
    result["season"]     = season
    result["area_ha"]    = agri_area_ha
    result["image_year"] = image_year
    return result
