# =============================================================================
# pipeline/nasa_power.py  —  NASA POWER API daily weather fetch
# =============================================================================

import json
import time
import urllib.request
from datetime import date
from typing import Optional
import pandas as pd

NASA_PARAMS = [
    "T2M_MAX", "T2M_MIN", "WS2M",
    "RH2M", "PRECTOTCORR",
    "ALLSKY_SFC_SW_DWN", "ALLSKY_SFC_SW_DIFF",
]

_BASE_URL = (
    "https://power.larc.nasa.gov/api/temporal/daily/point"
    "?parameters={params}"
    "&community=AG"
    "&longitude={lon:.4f}"
    "&latitude={lat:.4f}"
    "&start={start}"
    "&end={end}"
    "&format=JSON"
)

PARAM_LABELS = {
    "T2M_MAX":            "Max Temperature (°C)",
    "T2M_MIN":            "Min Temperature (°C)",
    "WS2M":               "Wind Speed (m/s)",
    "RH2M":               "Relative Humidity (%)",
    "PRECTOTCORR":        "Rainfall (mm/day)",
    "ALLSKY_SFC_SW_DWN":  "Solar Radiation ↓ (MJ/m²/day)",
    "ALLSKY_SFC_SW_DIFF": "Diffuse Radiation (MJ/m²/day)",
}


def fetch_nasa_power(
    lat: float,
    lon: float,
    start: date,
    end: date,
    retries: int = 3,
    timeout: int = 30,
) -> Optional[pd.DataFrame]:
    params_str = ",".join(NASA_PARAMS)
    url = _BASE_URL.format(
        params=params_str, lon=lon, lat=lat,
        start=start.strftime("%Y%m%d"),
        end=end.strftime("%Y%m%d"),
    )

    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "AgriIntelligenceApp/1.0"})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = json.loads(resp.read().decode())

            props = raw.get("properties", {}).get("parameter", {})
            if not props:
                return None

            records = {}
            for param in NASA_PARAMS:
                daily = props.get(param, {})
                for date_str, val in daily.items():
                    if date_str not in records:
                        records[date_str] = {}
                    records[date_str][param] = val if val != -999.0 else None

            if not records:
                return None

            df = pd.DataFrame.from_dict(records, orient="index")
            df.index = pd.to_datetime(df.index, format="%Y%m%d")
            df.sort_index(inplace=True)
            for p in NASA_PARAMS:
                if p not in df.columns:
                    df[p] = None
            return df[NASA_PARAMS]

        except Exception as e:
            if attempt < retries - 1:
                time.sleep(2 ** attempt)
            else:
                print(f"[NASA POWER] Fetch failed after {retries} attempts: {e}")
                return None
    return None


def aggregate_weather(daily_df: pd.DataFrame) -> dict:
    if daily_df is None or daily_df.empty:
        return {p: None for p in NASA_PARAMS}
    result = {}
    for p in NASA_PARAMS:
        col = daily_df[p].dropna()
        result[p] = float(col.mean()) if len(col) > 0 else None
    return result


def get_stage_weather(daily_df: pd.DataFrame, stage_start: date, stage_end: date) -> dict:
    """Aggregate weather for a specific stage date range."""
    if daily_df is None or daily_df.empty:
        return {p: None for p in NASA_PARAMS}
    mask = (daily_df.index.date >= stage_start) & (daily_df.index.date <= stage_end)
    sub = daily_df[mask]
    return aggregate_weather(sub) if not sub.empty else {p: None for p in NASA_PARAMS}
