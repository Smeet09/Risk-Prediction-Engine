# AgriIntel v2 — Satellite Crop Intelligence System

## Quick Start
```bash
pip install -r requirements.txt
streamlit run main_app.py
```

## Project Structure
```
AgriIntel_Final/
├── main_app.py                       # Streamlit orchestrator (4-tab UI)
├── nn_classifier.py                  # Nearest-Neighbour crop classifier
├── xgb_crop_classifier.py            # XGBoost crop classifier
├── xgb_engine.py                     # XGBoost yield + Monte Carlo engine
├── xgb_crop_model.pkl                # Pretrained XGBoost crop model
├── my_data.csv                       # Historical yield/weather data
├── data_bank_reference_updated.csv   # NN spectral reference (new)
├── data_bank_ref.xlsx                # Stage-wise ideal weather + Duration_Days + Key_Parameter
├── training_features_pan_india.csv   # Pan-India training features
├── requirements.txt
└── pipeline/
    ├── __init__.py
    ├── nasa_power.py        # NASA POWER API fetch + stage-wise aggregation
    ├── sowing_harvest.py    # Stage duration from XLSX, sowing/harvest estimation
    ├── weather_risk.py      # Stage-wise risk, Key_Parameter weighting, consistent scoring
    └── yield_pipeline.py    # XGBoost + Monte Carlo yield forecast
```

## Data Flow (TIFF → Final Output)

```
1. TIFF UPLOAD
   └── Band count check
       ├── <10 bands → Agriculture map only (Tab 1)
       └── 10 bands  → Full pipeline ↓

2. DATE EXTRACTION (priority order)
   ├── TIFFTAG_DATETIME / GeoTIFF acquisition tags
   ├── Sentinel-2 specific tags (DATATAKE_SENSING_START, etc.)
   ├── Description XML scan
   └── Filename pattern (YYYYMMDD fallback)

3. SPECTRAL FEATURES (18 features)
   └── B4, B8, B8A, B11, B12 + NDVI, NDRE, GNDVI, EVI, SAVI, CIre, RVI,
       NDWI, LSWI, NDMI, BSI, NDBI, SWIR_ratio

4. CROP CLASSIFICATION (NN or XGBoost)
   └── Per-pixel crop type + growth stage
   └── All detected crops ranked by pixel count

5. PER-CROP PIPELINE (for each detected crop):
   │
   ├── a. Sowing/Harvest Estimation
   │      ├── Load stage durations from data_bank_ref.xlsx
   │      ├── Find current stage position in crop cycle
   │      └── Back-calculate sowing date → forward-calculate harvest date
   │
   ├── b. NASA POWER Weather Fetch
   │      └── 7 daily params from sowing → harvest date
   │          T2M_MAX, T2M_MIN, WS2M, RH2M, PRECTOTCORR,
   │          ALLSKY_SFC_SW_DWN, ALLSKY_SFC_SW_DIFF
   │
   ├── c. Stage-wise Weather Comparison
   │      ├── For each stage: slice daily data to stage date range
   │      ├── Compare actual vs ideal from data_bank_ref.xlsx
   │      ├── Apply Key_Parameter extra weight (2.5× boost)
   │      ├── Compute stage score 0–100
   │      └── Consistency guard: extreme param → floor overall score
   │
   ├── d. Crop-level Risk Aggregation
   │      ├── Duration-weighted average across stages
   │      ├── Extreme stage → overall ≥ High
   │      └── Map score → Low / Moderate / High / Extreme
   │
   └── e. Yield Prediction
          ├── Historical data from my_data.csv
          ├── Inject current-year row (area + weather)
          ├── XGBoost regression (37 engineered features)
          ├── Monte Carlo (1000 simulations) — INTERNAL ONLY, no plot shown
          └── Output: predicted yield (kg/ha) + CI + est. production (tonnes)
```

## Key Design Decisions

### Stage-wise Risk (not season-level)
The databank (`data_bank_ref.xlsx`) provides per-stage ideal weather and Duration_Days.
Risk is computed separately for each stage, then aggregated by duration weight.
This prevents a benign early season from masking extreme late-season stress.

### Key_Parameter Weighting
Each crop stage has a `Key_Parameter` column (e.g., `Rain_Ideal`, `Tmax_Ideal`, `SolarRad`).
That parameter's weight is multiplied by 2.5 during risk scoring, ensuring critical stress
factors drive the final risk category. The remaining weights are reduced proportionally.

### Consistent Risk Output
If any stage has an extreme-stress parameter (score ≥ 65/100), the final composite is
floored to at least Moderate (≥ 38/100). If any stage category is Extreme, the overall
crop risk is floored to High (≥ 60/100). This prevents the paradox of a low final risk
when individual parameters show extreme stress.

### Date Extraction Priority
1. GeoTIFF acquisition tags (TIFFTAG_DATETIME, ACQUISITION_DATE, etc.)
2. Sentinel-2 specific metadata (DATATAKE_SENSING_START)
3. Description field XML scan
4. Filename pattern (YYYYMMDD)
Never guesses or assumes a date.

### All Crops Processed
Tab 4 loops over every detected crop (not just the dominant one) and shows
independent weather risk + yield forecast for each one.

### Monte Carlo — Internal Only
Monte Carlo runs internally for uncertainty quantification (P(below threshold),
confidence intervals). The histogram plot is NOT shown in the UI per requirements.

## Tabs
1. **Agriculture Map** — NDVI-based agri/non-agri overlay + area stats
2. **Crop Classification** — Multi-crop pixel map + confidence stats
3. **Growth Stage** — Per-crop stage + sowing/harvest dates
4. **Weather & Yield Forecast** — Stage-wise risk + yield for ALL detected crops

Historic Analysis tab has been completely removed.
