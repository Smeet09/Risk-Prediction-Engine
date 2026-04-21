"""
dynamic_core.py
===============
Shared physics & grid engine for dynamic risk prediction.
Extracted from landslide_dynamic_FINAL.py so that both
landslide and flood pipelines can reuse the same functions.

Physics references:
  Beven & Kirkby (1979) — TOPMODEL
  Iverson (2000)         — Infinite-slope FS
  SCS-TR-55 (1986)       — Curve Number runoff
  Skempton & DeLory (1957)
"""

import numpy as np
import pandas as pd
import rasterio
from rasterio.transform import from_bounds
from rasterio.warp import reproject, Resampling
from pathlib import Path
import psycopg2
from datetime import timedelta, datetime
import rasterio.features
from shapely.geometry import shape

# ─── Risk class mapping ───────────────────────────────────────────────────────
RISK_LABELS = {0: "No Data", 1: "Very Low", 2: "Low",
               3: "Moderate", 4: "High", 5: "Very High"}

RISK_COLORS = {
    1: "#2dc653", 2: "#80b918", 3: "#f9c74f",
    4: "#f3722c", 5: "#d62828"
}

# ─── Antecedent decay constant (Kohler & Linsley 1951) ───────────────────────
_API_K = 0.85

# ─── Weights per disaster type ────────────────────────────────────────────────
COMBO_WEIGHTS = {
    "landslide": {"susceptibility": 0.45, "trigger": 0.40, "lulc": 0.15},
    "flood":     {"susceptibility": 0.40, "trigger": 0.45, "lulc": 0.15},
}


# ═══════════════════════════════════════════════════════════════════════════════
#  STEP 1 — LOAD WEATHER FROM DB
# ═══════════════════════════════════════════════════════════════════════════════

def load_weather_from_db(database_url: str, state: str,
                          target_date_str: str, antecedent_days: int = 10) -> pd.DataFrame:
    """
    Query weather_data table for target date + antecedent window.

    Returns a DataFrame with columns that mirror what the standalone
    landslide_dynamic.py expects:
        grid_id, lat, lon, date, rain_mm, soil_moisture,
        surface_runoff_mm, potential_evaporation_m, temperature_2m_k,
        antecedent_rain_mm (computed), api (computed)

    Missing columns are filled with NaN — the trigger functions handle
    graceful fallback.
    """
    target_dt = pd.to_datetime(target_date_str)
    start_dt  = target_dt - timedelta(days=antecedent_days)

    conn = psycopg2.connect(database_url)
    try:
        query = """
            SELECT
                grid_id, lat, lon, date,
                COALESCE(rain_mm, 0)                     AS rain_mm,
                COALESCE(soil_moisture_layer1_m3m3,
                         soil_moisture_m3m3)::float      AS soil_moisture,
                COALESCE(surface_runoff_mm,
                         surface_runoff_m * 1000)::float AS surface_runoff_mm,
                COALESCE(potential_evaporation_m,
                         0)::float                        AS potential_evaporation_m,
                COALESCE(temperature_2m_k,
                         temperature_2m + 273.15)::float  AS temperature_2m_k,
                COALESCE(relative_humidity_700_pct,
                         0)::float                        AS relative_humidity_pct
            FROM weather_data
            WHERE state = %s
              AND date BETWEEN %s AND %s
            ORDER BY date, grid_id
        """
        df = pd.read_sql_query(
            query, conn,
            params=(state, start_dt.date(), target_dt.date())
        )
    finally:
        conn.close()

    if df.empty:
        raise ValueError(
            f"No weather data found for state='{state}' "
            f"between {start_dt.date()} and {target_dt.date()}. "
            f"Please download weather data for this period first."
        )

    df["date"] = pd.to_datetime(df["date"])
    today = df[df["date"] == target_dt].copy()
    if today.empty:
        sample = sorted(df["date"].dt.date.unique())[:5]
        raise ValueError(
            f"No weather data for target date {target_date_str}. "
            f"Available dates: {sample}"
        )

    # ── Compute antecedent_rain_mm (cumulative, excluding target day) ──────────
    prior = df[(df["date"] > start_dt) & (df["date"] < target_dt)]
    ant_rain = (
        prior.groupby("grid_id")["rain_mm"].sum()
             .reset_index()
             .rename(columns={"rain_mm": "antecedent_rain_mm"})
    )
    today = today.merge(ant_rain, on="grid_id", how="left")
    today["antecedent_rain_mm"] = today["antecedent_rain_mm"].fillna(0.0)

    # ── Compute API with exponential decay k=0.85 ─────────────────────────────
    api_vals = {gid: 0.0 for gid in today["grid_id"]}
    for d in range(1, antecedent_days + 1):
        day_d    = target_dt - timedelta(days=d)
        day_data = df[df["date"] == day_d][["grid_id", "rain_mm"]]
        if not day_data.empty:
            day_data = day_data.set_index("grid_id")["rain_mm"]
            for gid in api_vals:
                api_vals[gid] += float(day_data.get(gid, 0)) * (_API_K ** d)
    today["api"] = today["grid_id"].map(api_vals).fillna(0.0)

    print(f"  [WeatherDB] Grid points : {len(today)}")
    print(f"  [WeatherDB] rain_mm     : mean={today['rain_mm'].mean():.2f}  max={today['rain_mm'].max():.2f}")
    print(f"  [WeatherDB] API (10-day): mean={today['api'].mean():.2f}  max={today['api'].max():.2f}")
    if today["antecedent_rain_mm"].any():
        print(f"  [WeatherDB] Antecedent  : mean={today['antecedent_rain_mm'].mean():.2f} mm")

    return today


# ═══════════════════════════════════════════════════════════════════════════════
#  STEP 2 — BUILD 2KM WEATHER GRID
# ═══════════════════════════════════════════════════════════════════════════════

def build_grid(weather_df: pd.DataFrame):
    """
    Build a regular grid from unique lat/lon values in the weather DataFrame.
    Assigns 'row' and 'col' indices to every station for direct array lookup.
    Returns (weather_df_with_rowcol, grid_meta dict).
    """
    lats = np.sort(weather_df["lat"].unique())
    lons = np.sort(weather_df["lon"].unique())
    dlat = float(np.median(np.diff(lats))) if len(lats) > 1 else 0.018
    dlon = float(np.median(np.diff(lons))) if len(lons) > 1 else 0.018

    transform = from_bounds(
        lons.min() - dlon / 2, lats.min() - dlat / 2,
        lons.max() + dlon / 2, lats.max() + dlat / 2,
        len(lons), len(lats),
    )
    df = weather_df.copy()
    df["row"] = np.searchsorted(lats, df["lat"]).clip(0, len(lats) - 1)
    df["col"] = np.searchsorted(lons, df["lon"]).clip(0, len(lons) - 1)

    grid_meta = {
        "nrows": len(lats), "ncols": len(lons),
        "transform": transform,
        "lats": lats, "lons": lons,
        "crs": "EPSG:4326",
    }
    print(f"  [Grid] {grid_meta['ncols']}×{grid_meta['nrows']} cells  "
          f"lon=[{lons.min():.3f}–{lons.max():.3f}]  "
          f"lat=[{lats.min():.3f}–{lats.max():.3f}]")
    return df, grid_meta


# ═══════════════════════════════════════════════════════════════════════════════
#  STEP 3 — AGGREGATE SUSCEPTIBILITY RASTER
# ═══════════════════════════════════════════════════════════════════════════════

def aggregate_susceptibility(grid_meta: dict, susc_path: str) -> np.ndarray:
    """
    Reproject susceptibility raster onto the weather grid.
    Uses average + max resampling and blends 50/50.
    Returns normalised float32 array [0,1] on the weather grid.
    """
    with rasterio.open(susc_path) as src:
        susc     = src.read(1).astype(np.float32)
        susc_crs = src.crs
        susc_tr  = src.transform
        nd       = src.nodata
    if nd is not None:
        susc[susc == nd] = np.nan
    susc[~np.isfinite(susc)] = np.nan

    nrows, ncols = grid_meta["nrows"], grid_meta["ncols"]
    transform    = grid_meta["transform"]

    dest_mean = np.full((nrows, ncols), np.nan, dtype=np.float32)
    dest_max  = np.full((nrows, ncols), np.nan, dtype=np.float32)

    reproject(source=susc, destination=dest_mean,
              src_transform=susc_tr, src_crs=susc_crs,
              dst_transform=transform, dst_crs="EPSG:4326",
              resampling=Resampling.average,
              src_nodata=np.nan, dst_nodata=np.nan)

    reproject(source=susc, destination=dest_max,
              src_transform=susc_tr, src_crs=susc_crs,
              dst_transform=transform, dst_crs="EPSG:4326",
              resampling=Resampling.max,
              src_nodata=np.nan, dst_nodata=np.nan)

    dest_mean[dest_mean == 0] = np.nan
    dest_max[dest_max == 0]   = np.nan
    combined = dest_mean * 0.5 + dest_max * 0.5

    norm = normalize_array(combined)
    cov  = np.isfinite(norm).mean() * 100
    print(f"  [Susc] coverage={cov:.1f}%  mean={np.nanmean(norm):.3f}  max={np.nanmax(norm):.3f}")
    return norm


# ═══════════════════════════════════════════════════════════════════════════════
#  STEP 4 — AGGREGATE LULC
# ═══════════════════════════════════════════════════════════════════════════════

# LULC values from flood_susceptibility.py (ESRI LULC codes)
LULC_ROUGHNESS = {
    10: 0.40, 20: 0.25, 30: 0.20, 40: 0.15, 50: 0.05,
    60: 0.35, 70: 0.12, 80: 0.03, 90: 0.02, 100: 0.01,
    110: 0.08, 254: 0.10,
}
LULC_INFILTRATION = {
    10: 0.80, 20: 0.65, 30: 0.55, 40: 0.45, 50: 0.20,
    60: 0.70, 70: 0.50, 80: 0.15, 90: 0.05, 100: 0.00,
    110: 0.30, 254: 0.40,
}
# Root reinforcement (landslide — higher means more root cohesion → lower risk)
LULC_ROOT_RISK = {
    10: 0.20, 20: 0.35, 30: 0.45, 40: 0.55, 50: 0.90,
    60: 0.25, 70: 0.50, 80: 0.75, 90: 0.85, 100: 0.95,
    110: 0.60, 254: 0.50,
}


def aggregate_lulc(grid_meta: dict, lulc_path: str,
                   disaster_code: str = "landslide") -> np.ndarray:
    """
    Reproject LULC raster onto weather grid and map to risk weight.
    disaster_code='landslide'  → uses LULC_ROOT_RISK (root reinforcement)
    disaster_code='flood'      → uses (1-infiltration)*0.6 + (1-roughness)*0.4
    Returns normalised float32 array [0,1].
    """
    TILE = 2000
    nrows_out, ncols_out = grid_meta["nrows"], grid_meta["ncols"]
    transform            = grid_meta["transform"]
    risk_sum   = np.zeros((nrows_out, ncols_out), dtype=np.float64)
    risk_count = np.zeros((nrows_out, ncols_out), dtype=np.float64)

    with rasterio.open(lulc_path) as src:
        lulc_crs    = src.crs
        lulc_nodata = src.nodata
        lulc_h      = src.height
        lulc_w      = src.width

    import rasterio.windows
    ntil = (lulc_h + TILE - 1) // TILE
    for ti, r0 in enumerate(range(0, lulc_h, TILE)):
        r1 = min(r0 + TILE, lulc_h)
        with rasterio.open(lulc_path) as src:
            win     = rasterio.windows.Window(0, r0, lulc_w, r1 - r0)
            tile    = src.read(1, window=win).astype(np.float32)
            tile_tr = src.window_transform(win)
        if lulc_nodata is not None:
            tile[tile == lulc_nodata] = np.nan

        # Map LULC class → risk weight
        risk_tile = np.full_like(tile, np.nan)
        if disaster_code == "flood":
            infilt = np.full_like(tile, 0.40)
            rough  = np.full_like(tile, 0.20)
            for cls, v in LULC_INFILTRATION.items():
                infilt[tile == cls] = v
            for cls, v in LULC_ROUGHNESS.items():
                rough[tile == cls] = v
            risk_tile = (1.0 - infilt) * 0.6 + (1.0 - rough) * 0.4
        else:
            for cls, coeff in LULC_ROOT_RISK.items():
                risk_tile[tile == cls] = coeff

        tmp = np.full((nrows_out, ncols_out), np.nan, dtype=np.float32)
        reproject(source=risk_tile, destination=tmp,
                  src_transform=tile_tr, src_crs=lulc_crs,
                  dst_transform=transform, dst_crs="EPSG:4326",
                  resampling=Resampling.average,
                  src_nodata=np.nan, dst_nodata=np.nan)
        ok = np.isfinite(tmp)
        risk_sum[ok]   += tmp[ok]
        risk_count[ok] += 1

        if ntil <= 5 or (ti + 1) % max(1, ntil // 5) == 0:
            print(f"    [LULC] tile {ti+1}/{ntil}")

    dest = np.full((nrows_out, ncols_out), np.nan, dtype=np.float32)
    ok   = risk_count > 0
    dest[ok] = (risk_sum[ok] / risk_count[ok]).astype(np.float32)
    dest[dest <= 0] = np.nan
    norm = normalize_array(dest)
    print(f"  [LULC] mean={np.nanmean(norm):.3f}  max={np.nanmax(norm):.3f}")
    return norm


# ═══════════════════════════════════════════════════════════════════════════════
#  STEP 5 — IDW INTERPOLATION
# ═══════════════════════════════════════════════════════════════════════════════

def idw_interpolate_grid(weather_df: pd.DataFrame, point_scores: np.ndarray,
                          grid_meta: dict, power: int = 2) -> np.ndarray:
    """
    IDW interpolation from station point_scores onto the full weather grid.
    Memory-safe: chunked by rows.
    """
    nrows, ncols = grid_meta["nrows"], grid_meta["ncols"]
    lats, lons   = grid_meta["lats"], grid_meta["lons"]

    sx = weather_df["lon"].values.astype(np.float32)
    sy = weather_df["lat"].values.astype(np.float32)
    sv = np.array(point_scores, dtype=np.float32)

    grid_lon, grid_lat = np.meshgrid(lons, lats)
    result = np.full((nrows, ncols), np.nan, dtype=np.float32)

    MAX_BYTES  = 300 * 1024 * 1024
    CHUNK_ROWS = max(1, min(nrows, MAX_BYTES // (ncols * len(sv) * 4)))

    for r0 in range(0, nrows, CHUNK_ROWS):
        r1 = min(r0 + CHUNK_ROWS, nrows)
        cx = grid_lon[r0:r1, :].ravel()
        cy = grid_lat[r0:r1, :].ravel()

        dx   = cx[:, None] - sx[None, :]
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


# ═══════════════════════════════════════════════════════════════════════════════
#  STEP 6 — COMBINE & CLASSIFY
# ═══════════════════════════════════════════════════════════════════════════════

def combine_and_classify(susc_norm: np.ndarray, trigger_score: np.ndarray,
                          lulc_norm: np.ndarray,
                          disaster_code: str = "landslide"):
    """
    Composite = w_s×S + w_t×T + w_l×L
    Gracefully degrades if any layer is NaN at a pixel.
    Returns (composite [0,1], risk_class [0-5 uint8]).
    """
    w = COMBO_WEIGHTS.get(disaster_code, COMBO_WEIGHTS["landslide"])
    w_s = w["susceptibility"]
    w_t = w["trigger"]
    w_l = w["lulc"]

    composite = np.full_like(susc_norm, np.nan, dtype=np.float32)
    fs = np.isfinite(susc_norm)
    ft = np.isfinite(trigger_score)
    fl = np.isfinite(lulc_norm)

    # All three
    m1 = fs & ft & fl
    composite[m1] = (susc_norm[m1] * w_s + trigger_score[m1] * w_t + lulc_norm[m1] * w_l)

    # No LULC — redistribute
    m2 = fs & ft & ~fl
    composite[m2] = (susc_norm[m2] * (w_s + w_l * 0.5) + trigger_score[m2] * (w_t + w_l * 0.5))

    # No trigger — susc + LULC only
    m3 = fs & ~ft
    if m3.any():
        tot = w_s + w_l
        composite[m3] = (susc_norm[m3] * (w_s / tot) + lulc_norm[m3] * (w_l / tot))

    cov = np.isfinite(composite).mean() * 100
    print(f"  [Composite] mean={np.nanmean(composite):.3f}  "
          f"max={np.nanmax(composite):.3f}  coverage={cov:.1f}%")

    return composite, classify_fixed(composite)


def classify_fixed(arr: np.ndarray) -> np.ndarray:
    """Map composite [0,1] → 5-class risk."""
    risk = np.zeros_like(arr, dtype=np.uint8)
    risk[arr >  0.00] = 1
    risk[arr >= 0.25] = 2
    risk[arr >= 0.45] = 3
    risk[arr >= 0.60] = 4
    risk[arr >= 0.75] = 5
    risk[~np.isfinite(arr)] = 0
    return risk


# ═══════════════════════════════════════════════════════════════════════════════
#  STEP 7 — VECTORISE RISK RASTER → GEOJSON
# ═══════════════════════════════════════════════════════════════════════════════

def raster_to_geojson(risk: np.ndarray, grid_meta: dict) -> dict:
    """
    Convert uint8 risk raster → GeoJSON FeatureCollection.
    Each feature = one polygon per risk class.
    """
    transform = grid_meta["transform"]
    features  = []

    for cls_id in range(1, 6):
        mask = (risk == cls_id).astype(np.uint8)
        if not mask.any():
            continue
        for geom_dict, val in rasterio.features.shapes(
            mask, mask=mask, transform=transform
        ):
            features.append({
                "type": "Feature",
                "properties": {
                    "class_id":    cls_id,
                    "risk_class":  RISK_LABELS[cls_id],
                    "color":       RISK_COLORS[cls_id],
                },
                "geometry": geom_dict,
            })

    return {"type": "FeatureCollection", "features": features}


# ═══════════════════════════════════════════════════════════════════════════════
#  UTILITIES
# ═══════════════════════════════════════════════════════════════════════════════

def normalize_array(arr: np.ndarray, low_is_high: bool = False) -> np.ndarray:
    valid = arr[np.isfinite(arr)]
    if len(valid) == 0:
        return arr.copy()
    p2, p98 = np.percentile(valid, 2), np.percentile(valid, 98)
    clipped  = np.clip(arr, p2, p98)
    vmin, vmax = np.nanmin(clipped), np.nanmax(clipped)
    n = (clipped - vmin) / (vmax - vmin + 1e-10)
    if low_is_high:
        n = 1.0 - n
    n[~np.isfinite(arr)] = np.nan
    return n.astype(np.float32)


def get_class_stats(risk: np.ndarray) -> dict:
    total = int((risk > 0).sum())
    stats = {}
    for cls in range(1, 6):
        n   = int((risk == cls).sum())
        pct = round(n / max(1, total) * 100, 2)
        stats[RISK_LABELS[cls]] = {"pixel_count": n, "pct": pct}
    return stats


def get_lulc_path_from_db(database_url: str) -> str | None:
    """Fetch LULC TIF path from manual_data_india table."""
    try:
        conn = psycopg2.connect(database_url)
        cur  = conn.cursor()
        cur.execute(
            "SELECT file_path FROM manual_data_india WHERE data_type='lulc' LIMIT 1"
        )
        row = cur.fetchone()
        cur.close(); conn.close()
        return row[0] if row and row[0] else None
    except Exception as e:
        print(f"  [LULC] DB lookup failed: {e}")
        return None


def get_susceptibility_path_from_db(database_url: str,
                                     region_id: str,
                                     disaster_code: str) -> str | None:
    """Fetch susceptibility TIF path from susceptibility_results table."""
    try:
        conn = psycopg2.connect(database_url)
        cur  = conn.cursor()
        cur.execute(
            """
            SELECT tif_path FROM susceptibility_results
            WHERE region_id = %s AND disaster_code = %s AND status = 'done'
            ORDER BY generated_at DESC LIMIT 1
            """,
            (region_id, disaster_code)
        )
        row = cur.fetchone()
        cur.close(); conn.close()
        return row[0] if row and row[0] else None
    except Exception as e:
        print(f"  [Susc] DB path lookup failed: {e}")
        return None
