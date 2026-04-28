# =============================================================================
# pipeline/sowing_harvest.py
# Estimate sowing and harvesting dates. Stage-wise duration from data_bank_ref.xlsx
# =============================================================================

import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
from datetime import date, timedelta
from typing import Optional, Tuple, List

# Fallback total crop durations (days) — used if databank not available
CROP_TOTAL_DAYS = {
    "rice": 132, "wheat": 130, "groundnut": 135, "cotton": 135,
    "sugarcane": 355, "maize": 100, "soybean": 132,
    "mustard": 130, "others": 120,
}

# Stage order in crop cycle (lowercase)
STAGE_ORDER_NORM = [
    "sowing", "germination", "vegetative", "flowering",
    "reproductive / pod / fruit formation", "maturity", "harvesting"
]

# Map classifier stage names → databank stage names
STAGE_ALIAS = {
    "nursery":       "sowing",
    "germination":   "germination",
    "transplanting": "germination",
    "vegetative":    "vegetative",
    "peak canopy":   "flowering",
    "maturity":      "maturity",
    "senescence":    "maturity",
    "harvest":       "harvesting",
    "harvesting":    "harvesting",
    "flowering":     "flowering",
    "reproductive":  "reproductive / pod / fruit formation",
}

# Crop name mapping: classifier name → databank name (matches new data_bank_ref_new.xlsx)
CROP_ALIAS = {
    "rice":      "RICE",
    "wheat":     "WHEAT",
    "groundnut": "GROUNDNUT",
    "cotton":    "COTTON",
    "sugarcane": "SUGARCANE",
    "maize":     "MAIZE",
    "soybean":   "SOYABEAN",
    "mustard":   "MUSTARD",
}

_DATABANK_CACHE = None
_DATABANK_CACHE_PATH = None


def _load_databank(xlsx_path: str = None) -> Optional[pd.DataFrame]:
    global _DATABANK_CACHE, _DATABANK_CACHE_PATH

    if xlsx_path is None:
        xlsx_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data_bank_ref_new.xlsx")

    # Invalidate cache if a different path is requested
    if _DATABANK_CACHE is not None and _DATABANK_CACHE_PATH == xlsx_path:
        return _DATABANK_CACHE

    try:
        df = pd.read_excel(xlsx_path)
        df["Crops_norm"] = df["Crops"].str.strip().str.upper()
        df["Stage_norm"] = df["Stage"].str.strip().str.lower()
        _DATABANK_CACHE = df
        _DATABANK_CACHE_PATH = xlsx_path
        return df
    except Exception as e:
        print(f"[sowing_harvest] Cannot load databank: {e}")
        return None


def get_crop_stages(crop: str, xlsx_path: str = None) -> List[dict]:
    """
    Return ordered list of stages for a crop with Duration_Days and ideal weather.
    Each item: {stage, duration_days, tmin, tmax, rain, rh, solar_dwn, solar_diff, key_param}
    """
    db = _load_databank(xlsx_path)
    if db is None:
        return []

    db_crop = CROP_ALIAS.get(crop.lower(), crop.upper())
    sub = db[db["Crops_norm"] == db_crop].copy()
    if sub.empty:
        return []

    result = []
    for _, row in sub.iterrows():
        result.append({
            "stage":       row["Stage_norm"],
            "stage_label": row["Stage"].strip().title(),
            "duration_days": int(row.get("Duration_Days", 14)),
            "tmin":        row.get("Tmin_Ideal (°C)", None),
            "tmax":        row.get("Tmax_Ideal (°C)", None),
            "rain":        row.get("Rain_Ideal (mm/day)", None),
            "rh":          row.get("RH_Ideal (%)", None),
            "solar_dwn":   row.get("ALLSKY_SFC_SW_DWN (MJ/m2/day)", None),
            "solar_diff":  row.get("ALLSKY_SFC_SW_DIFF (MJ/m2/day)", None),
            "ws2m":        row.get("WS2M_Ideal", None),   # now present in new xlsx
            "key_param":   str(row.get("Key_Parameter", "")).strip(),
        })

    # Sort by STAGE_ORDER_NORM
    def _sort_key(item):
        sn = item["stage"].lower()
        for i, s in enumerate(STAGE_ORDER_NORM):
            if s in sn or sn in s:
                return i
        return 99

    result.sort(key=_sort_key)
    return result


def get_crop_total_days(crop: str, xlsx_path: str = None) -> int:
    stages = get_crop_stages(crop, xlsx_path)
    if stages:
        return sum(s["duration_days"] for s in stages)
    return CROP_TOTAL_DAYS.get(crop.lower(), 120)


def estimate_sowing_harvest(
    crop: str,
    growth_stage: str,
    image_date: date,
    xlsx_path: str = None,
) -> Tuple[Optional[date], Optional[date], int]:
    """
    Estimate sowing and harvest dates from detected crop, stage, and image date.
    Returns (sowing_date, harvest_date, total_days).
    """
    crop_norm = crop.lower().strip()
    stage_norm = STAGE_ALIAS.get(growth_stage.lower().strip(), "vegetative")

    stages = get_crop_stages(crop_norm, xlsx_path)
    total_days = sum(s["duration_days"] for s in stages) if stages else CROP_TOTAL_DAYS.get(crop_norm, 120)

    # Find cumulative days up to current stage
    days_elapsed = 0
    found = False
    for s in stages:
        s_norm = s["stage"].lower()
        if s_norm == stage_norm or stage_norm in s_norm or s_norm in stage_norm:
            days_elapsed += s["duration_days"] // 2  # midpoint of current stage
            found = True
            break
        days_elapsed += s["duration_days"]

    if not found:
        # Fallback: use percentage
        pct_map = {
            "sowing": 0.05, "germination": 0.10, "transplanting": 0.12,
            "vegetative": 0.30, "flowering": 0.50,
            "reproductive / pod / fruit formation": 0.65,
            "maturity": 0.80, "harvesting": 0.95,
        }
        pct = pct_map.get(stage_norm, 0.30)
        days_elapsed = int(total_days * pct)

    sowing_date  = image_date - timedelta(days=days_elapsed)
    harvest_date = sowing_date + timedelta(days=total_days)
    return sowing_date, harvest_date, total_days


def get_ideal_weather_for_stage(stage_info: dict) -> dict:
    """Convert a stage dict to NASA POWER param names."""
    return {
        "T2M_MAX":            _safe_float(stage_info.get("tmax")),
        "T2M_MIN":            _safe_float(stage_info.get("tmin")),
        "WS2M":               _safe_float(stage_info.get("ws2m")),   # now sourced from new xlsx
        "RH2M":               _safe_float(stage_info.get("rh")),
        "PRECTOTCORR":        _safe_float(stage_info.get("rain")),
        "ALLSKY_SFC_SW_DWN":  _safe_float(stage_info.get("solar_dwn")),
        "ALLSKY_SFC_SW_DIFF": _safe_float(stage_info.get("solar_diff")),
    }


def _safe_float(v):
    try:
        return float(v) if v is not None and str(v).strip() not in ("", "nan") else None
    except:
        return None


def get_ideal_weather_from_xlsx(crop: str, xlsx_path: str = None) -> dict:
    """
    Read season-average ideal weather directly from the new data_bank_ref_new.xlsx.
    Averages all stages for a crop to get a full-season ideal profile.
    Used by yield_pipeline as the primary ideal weather source.
    Returns dict with keys matching my_data.csv ideal column names.
    """
    db = _load_databank(xlsx_path)
    if db is None:
        return {}

    db_crop = CROP_ALIAS.get(crop.lower(), crop.upper())
    sub = db[db["Crops_norm"] == db_crop]
    if sub.empty:
        return {}

    def _col_mean(col):
        if col in sub.columns:
            v = pd.to_numeric(sub[col], errors="coerce").dropna()
            return float(v.mean()) if len(v) > 0 else None
        return None

    return {
        "Tmin_Ideal":                           _col_mean("Tmin_Ideal (°C)"),
        "Tmax_Ideal":                           _col_mean("Tmax_Ideal (°C)"),
        "Rain_Ideal (mm/day)_Ideal":            _col_mean("Rain_Ideal (mm/day)"),
        "RH_Ideal (%)_Ideal":                   _col_mean("RH_Ideal (%)"),
        "ALLSKY_SFC_SW_DWN (MJ/m2/day)_Ideal": _col_mean("ALLSKY_SFC_SW_DWN (MJ/m2/day)"),
        "ALLSKY_SFC_SW_DIFF (MJ/m2/day)_Ideal":_col_mean("ALLSKY_SFC_SW_DIFF (MJ/m2/day)"),
        "WS2M_Ideal":                           _col_mean("WS2M_Ideal"),
    }


# ── Legacy support for yield_pipeline.py ──────────────────────────────────────

def get_ideal_weather_for_crop(crop: str, my_data_df) -> dict:
    """Season-average ideal weather from my_data.csv (legacy use by yield_pipeline)."""
    IDEAL_COLS = [
        "Tmin_Ideal", "Tmax_Ideal",
        "Rain_Ideal (mm/day)_Ideal", "RH_Ideal (%)_Ideal",
        "ALLSKY_SFC_SW_DIFF (MJ/m2/day)_Ideal",
        "ALLSKY_SFC_SW_DWN (MJ/m2/day)_Ideal", "WS2M_Ideal",
    ]
    CROP_MAP = {
        "rice":      ["rice"],
        "wheat":     ["wheat"],
        "groundnut": ["groundnut"],
        "cotton":    ["cotton"],
        "sugarcane": ["sugarcane"],
        "maize":     ["maize"],
        "soybean":   ["soyabean", "soybean"],
        "mustard":   ["rapeseed and mustard", "mustard"],
        "others":    [],
    }
    crop_lower = crop.lower().strip()
    aliases = CROP_MAP.get(crop_lower, [crop_lower])

    if my_data_df is None or my_data_df.empty:
        return {}

    crop_col = "Crops"
    if crop_col not in my_data_df.columns:
        return {}

    mask = my_data_df[crop_col].str.lower().isin([a.lower() for a in aliases])
    sub  = my_data_df[mask]
    if sub.empty:
        return {}

    result = {}
    for col in IDEAL_COLS:
        if col in sub.columns:
            val = sub[col].dropna()
            result[col] = float(val.median()) if len(val) > 0 else None
        else:
            result[col] = None
    return result


def ideal_to_nasa_param_map(ideal_dict: dict) -> dict:
    return {
        "T2M_MAX":            ideal_dict.get("Tmax_Ideal"),
        "T2M_MIN":            ideal_dict.get("Tmin_Ideal"),
        "WS2M":               ideal_dict.get("WS2M_Ideal"),
        "RH2M":               ideal_dict.get("RH_Ideal (%)_Ideal"),
        "PRECTOTCORR":        ideal_dict.get("Rain_Ideal (mm/day)_Ideal"),
        "ALLSKY_SFC_SW_DWN":  ideal_dict.get("ALLSKY_SFC_SW_DWN (MJ/m2/day)_Ideal"),
        "ALLSKY_SFC_SW_DIFF": ideal_dict.get("ALLSKY_SFC_SW_DIFF (MJ/m2/day)_Ideal"),
    }
