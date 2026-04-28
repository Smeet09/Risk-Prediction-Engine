# ═══════════════════════════════════════════════════════════════════════════
#  xgb_engine.py  —  Drop-in replacement for risk_engine.py
#  Uses XGBoost instead of Random Forest.
#  Features: 37 total (7 raw + 6 derived + 3 cat + 7 temporal + 7 |diff| + 7 signed dev)
# ═══════════════════════════════════════════════════════════════════════════

import numpy as np
import pandas as pd
import warnings
warnings.filterwarnings("ignore")

from sklearn.model_selection import train_test_split
from sklearn.metrics import mean_squared_error, mean_absolute_error, r2_score
from sklearn.preprocessing import LabelEncoder
from xgboost import XGBRegressor

# ─────────────────────────────────────────────────────────────────────────
# CONSTANTS
# ─────────────────────────────────────────────────────────────────────────

RISK_THRESH = {
    "Low":      (0.70, 1.00),
    "Moderate": (0.50, 0.70),
    "High":     (0.30, 0.50),
    "Extreme":  (0.00, 0.30),
}

N_WEATHER_FEATS = 7    # raw NASA weather cols
N_CAT_FEATS     = 3    # Season_enc, Crop_enc, State_enc
N_TOTAL_FEATS   = 37   # 7 raw + 6 derived + 3 cat + 7 temporal + 7 |diff| + 7 signed dev

FEAT_COLS = [
    # Raw weather (7)
    "T2M_MAX","T2M_MIN","RH2M","WS2M","PRECTOTCORR",
    "ALLSKY_SFC_SW_DWN","ALLSKY_SFC_SW_DIFF",
    # Derived weather (6)
    "temp_range","heat_stress","cold_stress",
    "rain_x_hum","rad_total","rad_ratio",
    # Categorical (3)
    "Season_enc","Crop_enc","State_enc",
    # Temporal / agronomic (7)
    "year_norm","crop_season_ix","state_season_ix",
    "yield_trend","lag1_yield","lag2_yield","roll3_yield",
    # |Actual − Ideal| deviation features (7)
    "temp_diff_max","temp_diff_min","rain_diff","rh_diff",
    "rad_diff_diff","rad_dwn_diff","wind_diff",
    # Signed deviation from ideal (7)
    "temp_dev_max","temp_dev_min","rain_dev","rh_dev",
    "rad_dwn_dev","rad_diff_dev","wind_dev",
]

FEAT_LABELS = [
    "Max Temp (°C)", "Min Temp (°C)", "Humidity (%)", "Wind Speed (m/s)",
    "Rainfall (mm/d)", "SW Downwelling", "SW Diffuse",
    "Temp Range (°C)", "Heat Stress Index", "Cold Stress Index",
    "Rain × Humidity", "Radiation Total", "Radiation Ratio",
    "Season (enc)", "Crop (enc)", "State (enc)",
    "Year (norm)", "Crop × Season", "State × Season",
    "Yield Trend", "Lag-1 Yield", "Lag-2 Yield", "Rolling-3yr Mean",
    "|Δ Max Temp|", "|Δ Min Temp|", "|Δ Rainfall|", "|Δ Humidity|",
    "|Δ SW Diffuse|", "|Δ SW Downwelling|", "|Δ Wind Speed|",
    "Dev Max Temp", "Dev Min Temp", "Dev Rainfall", "Dev Humidity",
    "Dev SW Downwelling", "Dev SW Diffuse", "Dev Wind Speed",
]

ALL_FEAT_LABELS = FEAT_LABELS
YIELD_COL = "YIELD (Kg per ha)"


# ─────────────────────────────────────────────────────────────────────────
# FEATURE ENGINEERING
# ─────────────────────────────────────────────────────────────────────────

def engineer_weather(df: pd.DataFrame) -> pd.DataFrame:
    d = df.copy()

    # a) Encode categoricals
    for raw_col, enc_col in [("Season","Season_enc"),
                              ("Crops","Crop_enc"),
                              ("State Name","State_enc")]:
        if raw_col in d.columns:
            le = LabelEncoder()
            d[enc_col] = le.fit_transform(d[raw_col].astype(str))
        else:
            d[enc_col] = 0

    # b) Derived weather features
    d["temp_range"]  = d["T2M_MAX"] - d["T2M_MIN"]
    d["heat_stress"] = np.maximum(0.0, d["T2M_MAX"] - 35.0)
    d["cold_stress"] = np.maximum(0.0, 10.0 - d["T2M_MIN"])
    d["rain_x_hum"]  = d["PRECTOTCORR"] * d["RH2M"]
    d["rad_total"]   = d["ALLSKY_SFC_SW_DWN"] + d["ALLSKY_SFC_SW_DIFF"]
    d["rad_ratio"]   = d["ALLSKY_SFC_SW_DIFF"] / (d["ALLSKY_SFC_SW_DWN"] + 1e-3)

    # c) Temporal / agronomic
    yr_min = d["Year"].min(); yr_max = d["Year"].max()
    d["year_norm"]       = (d["Year"] - yr_min) / max(yr_max - yr_min, 1.0)
    d["crop_season_ix"]  = d["Crop_enc"] * d["Season_enc"]
    d["state_season_ix"] = d["State_enc"] * d["Season_enc"]

    # d) Lag & rolling yield
    grp_cols = [c for c in ["Crops","State Name","Dist Name"] if c in d.columns]
    d = d.sort_values(grp_cols + ["Year"]).reset_index(drop=True)
    d["lag1_yield"]  = d.groupby(grp_cols)[YIELD_COL].shift(1)
    d["lag2_yield"]  = d.groupby(grp_cols)[YIELD_COL].shift(2)
    d["roll3_yield"] = (d.groupby(grp_cols)[YIELD_COL]
                         .transform(lambda x: x.shift(1).rolling(3, min_periods=1).mean()))

    def _trend_slope(series):
        result = np.full(len(series), np.nan)
        for i in range(len(series)):
            win = series[max(0, i-4):i+1].dropna()
            if len(win) >= 3:
                result[i] = float(np.polyfit(np.arange(len(win), dtype=float), win.values, 1)[0])
        return pd.Series(result, index=series.index)

    d["yield_trend"] = d.groupby(grp_cols)[YIELD_COL].transform(_trend_slope)
    for c in ["lag1_yield","lag2_yield","roll3_yield","yield_trend"]:
        med = d[c].median()
        d[c] = d[c].fillna(med if pd.notna(med) else 0.0)

    # e) |Actual − Ideal| deviation features
    def _absdiff(a, b_col):
        return np.abs(d[a] - d[b_col]) if b_col in d.columns else pd.Series(0.0, index=d.index)

    d["temp_diff_max"]  = _absdiff("T2M_MAX",            "Tmax_Ideal")
    d["temp_diff_min"]  = _absdiff("T2M_MIN",            "Tmin_Ideal")
    d["rain_diff"]      = _absdiff("PRECTOTCORR",        "Rain_Ideal (mm/day)_Ideal")
    d["rh_diff"]        = _absdiff("RH2M",               "RH_Ideal (%)_Ideal")
    d["rad_dwn_diff"]   = _absdiff("ALLSKY_SFC_SW_DWN",  "ALLSKY_SFC_SW_DWN (MJ/m2/day)_Ideal")
    d["rad_diff_diff"]  = _absdiff("ALLSKY_SFC_SW_DIFF", "ALLSKY_SFC_SW_DIFF (MJ/m2/day)_Ideal")
    d["wind_diff"]      = _absdiff("WS2M",               "WS2M_Ideal")

    # f) Signed deviation from ideal
    def _signdev(a, b_col):
        return (d[a] - d[b_col]) if b_col in d.columns else pd.Series(0.0, index=d.index)

    d["temp_dev_max"]  = _signdev("T2M_MAX",            "Tmax_Ideal")
    d["temp_dev_min"]  = _signdev("T2M_MIN",            "Tmin_Ideal")
    d["rain_dev"]      = _signdev("PRECTOTCORR",        "Rain_Ideal (mm/day)_Ideal")
    d["rh_dev"]        = _signdev("RH2M",               "RH_Ideal (%)_Ideal")
    d["rad_dwn_dev"]   = _signdev("ALLSKY_SFC_SW_DWN",  "ALLSKY_SFC_SW_DWN (MJ/m2/day)_Ideal")
    d["rad_diff_dev"]  = _signdev("ALLSKY_SFC_SW_DIFF", "ALLSKY_SFC_SW_DIFF (MJ/m2/day)_Ideal")
    d["wind_dev"]      = _signdev("WS2M",               "WS2M_Ideal")

    return d


def build_full_X(risk_df: pd.DataFrame, cat_maps: dict) -> np.ndarray:
    df_eng = engineer_weather(risk_df.copy())
    for c in FEAT_COLS:
        if c not in df_eng.columns:
            df_eng[c] = 0.0
    return df_eng[FEAT_COLS].fillna(0).values.astype(np.float64)


# ─────────────────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────────────────

def safe_mape(y_true, y_pred):
    mask = np.asarray(y_true) > 1e-6
    if mask.sum() == 0:
        return np.nan
    return float(np.mean(np.abs((np.asarray(y_true)[mask] - np.asarray(y_pred)[mask])
                                 / np.asarray(y_true)[mask])) * 100)


def _yield_to_risk(yield_val: float, long_mean: float) -> str:
    if long_mean <= 0:
        return "Low"
    r = yield_val / long_mean
    if   r >= 0.70: return "Low"
    elif r >= 0.50: return "Moderate"
    elif r >= 0.30: return "High"
    else:           return "Extreme"


# ─────────────────────────────────────────────────────────────────────────
# CORE RISK COMPUTATION
# ─────────────────────────────────────────────────────────────────────────

def compute_risk_core(X_all: np.ndarray, y_all: np.ndarray,
                      years: np.ndarray, mean_cat: np.ndarray,
                      n_forecast: int = 3,
                      risk_threshold=None) -> dict:
    # 1. Train / test split
    y_log = np.log1p(np.clip(y_all, 0, None))
    X_tr, X_te, y_tr, y_te = train_test_split(X_all, y_log,
                                               test_size=0.20, random_state=42)

    # 2. XGBoost model
    model = XGBRegressor(
        n_estimators    = 500,
        learning_rate   = 0.05,
        max_depth       = 8,
        subsample       = 0.8,
        colsample_bytree= 0.8,
        reg_alpha       = 0.5,
        reg_lambda      = 1.0,
        random_state    = 42,
        n_jobs          = -1,
        verbosity       = 0,
    )
    model.fit(X_tr, y_tr)

    # 3. Evaluation
    y_pred_log  = model.predict(X_te)
    y_true_orig = np.expm1(y_te)
    y_pred_orig = np.expm1(y_pred_log)

    r2   = float(r2_score(y_true_orig, y_pred_orig))
    rmse = float(np.sqrt(mean_squared_error(y_true_orig, y_pred_orig)))
    mae  = float(mean_absolute_error(y_true_orig, y_pred_orig))
    mape = safe_mape(y_true_orig, y_pred_orig)
    pa   = float(np.mean(np.abs(y_true_orig - y_pred_orig) /
                          np.maximum(y_true_orig, 1e-6) <= 0.15) * 100)

    eval_res = {
        "XGBoost": {
            "r2":   r2,
            "acc%": round(r2 * 100, 2),
            "rmse": rmse,
            "mae":  mae,
            "mape": mape,
            "pa%":  pa,
        }
    }

    # 4. Full-dataset predictions
    y_pred_all = np.expm1(model.predict(X_all))
    long_mean  = float(np.mean(y_all[y_all > 0])) if np.any(y_all > 0) else 1.0
    hist_std   = float(np.std(y_all))

    # 5. Year-wise actual vs predicted
    unique_years = sorted(np.unique(years))
    act_by_yr, pred_by_yr = [], []
    for yr in unique_years:
        mask = years == yr
        act_by_yr.append(float(np.mean(y_all[mask])))
        pred_by_yr.append(float(np.mean(y_pred_all[mask])))

    # 6. Expected yield
    exp_y = float(np.mean(y_pred_all))

    # 7. Monte Carlo — per-sample scenarios (realistic spread)
    N_MC  = 1_000
    rng   = np.random.default_rng(42)
    sigma = X_all[:, :N_WEATHER_FEATS].std(axis=0).clip(min=1e-8)
    n_samples = len(X_all)
    sim   = []
    for _ in range(N_MC):
        idx  = rng.integers(0, n_samples)
        x_mc = X_all[idx:idx+1].copy()
        x_mc[:, :N_WEATHER_FEATS] += rng.normal(0, sigma * 0.60, (1, N_WEATHER_FEATS))
        sim.append(float(np.expm1(model.predict(x_mc))[0]))
    sim = np.array(sim)

    # 8. Risk threshold & probability
    if risk_threshold is None:
        risk_threshold = float(np.percentile(y_all[y_all > 0], 25)) if np.any(y_all > 0) else long_mean * 0.5

    prob_loss_mc   = float(np.mean(sim < risk_threshold))
    prob_loss_pred = float(np.mean(y_pred_all < risk_threshold))
    prob_loss = round(0.60 * prob_loss_mc + 0.40 * prob_loss_pred, 4)

    var_vals  = {p: float(np.percentile(sim, p)) for p in [5, 10, 20, 50]}
    cvar_vals = {p: float(np.mean(sim[sim <= np.percentile(sim, p)]))
                 for p in [5, 10, 20, 50]}

    # 9. Risk category
    var_5     = float(np.percentile(sim, 5))
    cv_pct    = float(np.std(sim) / max(float(np.mean(sim)), 1.0)) * 100.0
    var_sev   = max(0.0, (long_mean - var_5) / max(long_mean, 1.0)) * 100.0
    composite = (min(prob_loss * 100, 100) * 0.40
               + min(cv_pct, 100) * 0.30
               + min(var_sev, 100) * 0.30)

    if   prob_loss < 0.15: risk_cat = "Low"
    elif prob_loss < 0.30: risk_cat = "Moderate"
    elif prob_loss < 0.50: risk_cat = "High"
    else:                  risk_cat = "Extreme"

    # 10. Current-year prediction: predict directly on the LAST row (injected current-year row)
    #     This is weather-sensitive and avoids the 7-year extrapolation gap.
    #     The blended approach (45% XGB mean + 55% linear trend to future year) would
    #     extrapolate from 2017 data to 2025, which loses weather sensitivity.
    max_yr  = int(max(unique_years))
    yr_arr  = np.array(unique_years, dtype=float)
    act_arr = np.array(act_by_yr,   dtype=float)

    # Direct prediction on injected row (last row = current year with actual weather)
    current_row_pred = float(np.expm1(model.predict(X_all[-1:]))[0])

    # Linear trend for reference (used as secondary blend — downweighted)
    if len(yr_arr) > 1:
        z     = np.polyfit(yr_arr, act_arr, 1)
        trend = np.poly1d(z)
    else:
        trend = lambda x: float(act_arr.mean())  # noqa

    # Blended: 75% direct weather-based XGB prediction + 25% historical trend
    # Higher XGB weight makes prediction more sensitive to actual weather conditions
    yr_f    = max_yr  # current year (not +1), since injected row IS this year
    lin     = float(trend(yr_f))
    blended = max(0.75 * current_row_pred + 0.25 * lin, 0.0)

    fci = []
    for k in range(1, n_forecast + 1):
        fci.append({"year": yr_f, "pred": blended, "low": None, "high": None})

    # 11. Feature importance
    fi     = model.feature_importances_
    imp_df = [{"Feature": FEAT_COLS[i], "MDI": float(fi[i])}
              for i in range(len(FEAT_COLS))]
    feat_imp = list(fi)

    # 12. Confusion data
    long_med   = float(np.median(y_true_orig))
    y_true_cls = np.array([_yield_to_risk(v, long_med) for v in y_true_orig])
    y_pred_cls = np.array([_yield_to_risk(v, long_med) for v in y_pred_orig])

    return {
        "eval_res":     eval_res,
        "imp_df":       imp_df,
        "feat_imp":     feat_imp,
        "risk_cat":     risk_cat,
        "composite":    round(composite, 2),
        "prob_loss":    round(prob_loss, 4),
        "threshold":    round(float(risk_threshold), 2),
        "auto_thresh_pct":   round(float(risk_threshold) / max(long_mean, 1.0) * 100, 1),
        "auto_thresh_label": "XGB Auto-Threshold",
        "hist_std":     hist_std,
        "long_mean":    long_mean,
        "exp_y":        exp_y,
        "var_vals":     var_vals,
        "cvar_vals":    cvar_vals,
        "sim":          sim.tolist(),
        "unique_years": unique_years,
        "act_by_yr":    act_by_yr,
        "pred_by_yr":   pred_by_yr,
        "fci":          fci,
        "cm_y_true":    y_true_cls.tolist(),
        "cm_y_pred":    y_pred_cls.tolist(),
        "cm_long_med":  long_med,
    }