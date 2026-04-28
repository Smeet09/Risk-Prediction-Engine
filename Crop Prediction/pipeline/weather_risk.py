# =============================================================================
# pipeline/weather_risk.py
# Stage-wise weather risk computation using data_bank_ref.xlsx
# Key_Parameter receives extra weight. Final risk is logically consistent.
# =============================================================================

import numpy as np
from typing import Optional
import pandas as pd

# Base NASA param weights (used when Key_Parameter boost not applied)
BASE_WEIGHTS = {
    "T2M_MAX":            0.20,
    "T2M_MIN":            0.15,
    "PRECTOTCORR":        0.25,
    "RH2M":               0.15,
    "ALLSKY_SFC_SW_DWN":  0.12,
    "ALLSKY_SFC_SW_DIFF": 0.05,
    "WS2M":               0.08,
}

# Key_Parameter column values → NASA param name
KEY_PARAM_MAP = {
    "Rain_Ideal":  "PRECTOTCORR",
    "Tmin_Ideal":  "T2M_MIN",
    "Tmax_Ideal":  "T2M_MAX",
    "SolarRad":    "ALLSKY_SFC_SW_DWN",
    "RH_Ideal":    "RH2M",
    "WS2M_Ideal":  "WS2M",
    "Rain_Stress": "PRECTOTCORR",  # sugarcane maturity wants low rain
}

# Parameters where ONLY excess causes stress (not deficit)
ONLY_EXCESS_STRESS = {"T2M_MAX", "WS2M"}
# Parameters where ONLY deficit causes stress (not excess)
ONLY_DEFICIT_STRESS = {"T2M_MIN", "RH2M", "ALLSKY_SFC_SW_DWN"}
# Symmetric (both excess and deficit cause stress): PRECTOTCORR, ALLSKY_SFC_SW_DIFF
# Special: Rain_Stress (sugarcane maturity) — excess rain is the stress, low rain is fine


def _stress_score(param: str, act: float, idl: float, is_stress_param: bool = False) -> float:
    """
    Return a 0–1 stress score for one parameter.
    is_stress_param: True for Rain_Stress (low rain preferred — excess = stress).
    """
    if abs(idl) < 0.5:
        rel_dev = (act - idl) / max(abs(idl) + 1.0, 1.0)
    else:
        rel_dev = (act - idl) / abs(idl)

    if is_stress_param:
        # Rain_Stress: crop wants DRY conditions — only excess rain is stressful
        stress = max(0.0, rel_dev)
    elif param in ONLY_EXCESS_STRESS:
        # Heat / wind — only excess is stressful
        stress = max(0.0, rel_dev)
    elif param in ONLY_DEFICIT_STRESS:
        # Cold / low humidity / low solar — only deficit is stressful
        stress = max(0.0, -rel_dev)
    else:
        # Symmetric: rainfall deficit AND excess are both stressful
        stress = abs(rel_dev)

    return min(stress, 2.0) / 2.0  # normalise 0–1


def compute_stage_risk(
    actual: dict,
    ideal: dict,
    key_param_raw: str = "",
    key_weight_multiplier: float = 2.5,
) -> tuple:
    """
    Compute risk score for one stage.
    Returns (score_0_100, param_scores_dict)
    """
    is_stress = "Stress" in key_param_raw
    nasa_key  = KEY_PARAM_MAP.get(key_param_raw.strip(), None)

    # Build per-param weights with key-param boost
    weights = dict(BASE_WEIGHTS)
    if nasa_key and nasa_key in weights:
        boost = weights[nasa_key] * key_weight_multiplier
        extra = boost - weights[nasa_key]
        # Reduce others proportionally
        other_total = sum(v for k, v in weights.items() if k != nasa_key)
        for k in weights:
            if k != nasa_key:
                weights[k] -= extra * (weights[k] / other_total)
        weights[nasa_key] = boost

    param_scores = {}
    weighted_sum = 0.0
    weight_used  = 0.0

    for param, base_w in weights.items():
        act = actual.get(param)
        idl = ideal.get(param)

        if act is None or idl is None:
            param_scores[param] = None
            continue

        score = _stress_score(param, act, idl, is_stress_param=(is_stress and param == nasa_key))
        param_scores[param]  = round(score * 100, 1)
        weighted_sum        += score * base_w
        weight_used         += base_w

    composite = (weighted_sum / weight_used * 100.0) if weight_used > 0.01 else 50.0
    return round(composite, 2), param_scores


def score_to_category(score: float) -> str:
    """
    Logically consistent thresholds.
    Max single-param stress must not be ignored by the composite.
    """
    if score < 18:   return "Low"
    elif score < 38: return "Moderate"
    elif score < 60: return "High"
    else:            return "Extreme"


def compute_weather_risk_score(
    actual: dict,
    ideal: dict,
    key_param_raw: str = "",
) -> tuple:
    """
    Season-level risk (non-stage-wise fallback).
    Returns (score, category, param_scores)
    """
    score, param_scores = compute_stage_risk(actual, ideal, key_param_raw)

    # Consistency check: if ANY param is extreme stress, floor is at least Moderate
    extreme_params = [k for k, v in param_scores.items() if v is not None and v >= 65]
    high_params    = [k for k, v in param_scores.items() if v is not None and v >= 45]

    if extreme_params and score < 38:
        score = max(score, 38.0)
    if high_params and score < 20:
        score = max(score, 20.0)

    return score, score_to_category(score), param_scores


def compute_stagewise_risk(
    daily_weather: pd.DataFrame,
    crop_stages: list,
    sowing_date,
    key_weight_multiplier: float = 2.5,
) -> list:
    """
    Compute per-stage weather risk using daily NASA POWER data.

    crop_stages: list of dicts from sowing_harvest.get_crop_stages()
    sowing_date: date object

    Returns list of stage result dicts.
    """
    from datetime import timedelta
    from pipeline.nasa_power import get_stage_weather
    from pipeline.sowing_harvest import get_ideal_weather_for_stage

    results  = []
    cur_date = sowing_date

    for stage in crop_stages:
        dur         = stage["duration_days"]
        stage_end   = cur_date + timedelta(days=dur - 1)
        ideal_nasa  = get_ideal_weather_for_stage(stage)
        actual_nasa = get_stage_weather(daily_weather, cur_date, stage_end)

        score, param_scores = compute_stage_risk(
            actual_nasa, ideal_nasa,
            key_param_raw=stage.get("key_param", ""),
            key_weight_multiplier=key_weight_multiplier,
        )

        # Consistency guard
        extreme_p = [k for k, v in param_scores.items() if v is not None and v >= 65]
        if extreme_p and score < 38:
            score = max(score, 38.0)

        results.append({
            "stage":        stage["stage_label"],
            "stage_norm":   stage["stage"],
            "start_date":   cur_date,
            "end_date":     stage_end,
            "duration":     dur,
            "key_param":    stage.get("key_param", ""),
            "ideal":        ideal_nasa,
            "actual":       actual_nasa,
            "param_scores": param_scores,
            "score":        score,
            "category":     score_to_category(score),
        })

        cur_date = stage_end + timedelta(days=1)

    return results


def aggregate_crop_risk(stage_results: list) -> tuple:
    """
    Aggregate stage-wise scores into one crop-level risk.
    Stages weighted by duration. Extreme stages can floor the overall score.
    Returns (final_score, final_category)
    """
    if not stage_results:
        return 50.0, "Moderate"

    total_dur  = sum(s["duration"] for s in stage_results)
    weighted   = sum(s["score"] * s["duration"] for s in stage_results)
    base_score = weighted / max(total_dur, 1)

    # If any stage is Extreme, overall is at least High
    has_extreme = any(s["category"] == "Extreme" for s in stage_results)
    has_high    = any(s["category"] in ("Extreme", "High") for s in stage_results)

    if has_extreme and base_score < 60:
        base_score = max(base_score, 60.0)
    elif has_high and base_score < 38:
        base_score = max(base_score, 38.0)

    return round(base_score, 2), score_to_category(base_score)


RISK_COLORS = {
    "Low":      "#2ecc71",
    "Moderate": "#f59e0b",
    "High":     "#e53935",
    "Extreme":  "#b71c1c",
}

RISK_ADVISORIES = {
    "Low": (
        "✅ Weather conditions are close to ideal for this crop. "
        "Normal agronomic practices expected to yield good results."
    ),
    "Moderate": (
        "⚡ Some weather parameters deviate from ideal. "
        "Monitor irrigation needs and consider adjusting nutrient schedules."
    ),
    "High": (
        "⚠️ Significant weather stress detected. "
        "Crop yield may be impacted. Consider protective measures, "
        "adjust water management, and monitor for pest/disease risk."
    ),
    "Extreme": (
        "🔴 Extreme weather stress. Crop is under severe pressure. "
        "Emergency irrigation, shade nets, or other mitigation measures "
        "are strongly recommended. Yield losses expected."
    ),
}


def get_risk_color(risk_cat: str) -> str:
    return RISK_COLORS.get(risk_cat, "#888888")


def get_risk_advisory(risk_cat: str) -> str:
    return RISK_ADVISORIES.get(risk_cat, "")
