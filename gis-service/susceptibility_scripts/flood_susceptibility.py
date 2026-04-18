"""
susceptibility_mapper.py — Physics-Informed Flood & Landslide Susceptibility Mapper
=====================================================================================
Fully static, terrain-adaptive susceptibility mapping system for use with
Aether-Disaster GIS Microservice.

Design philosophy:
  - NO rainfall or time-based inputs — long-term intrinsic/static risk
  - Parameters, weights, and algorithms ADAPT per terrain class, soil, and LULC
  - Physics-informed: Factor of Safety, TWI-based saturation, curvature-driven
    runoff concentration, fault proximity amplification
  - Multi-scale smoothing for spatial stability
  - Outputs: continuous 0–1 indices + 5-class maps (TIF + SHP)

Terrain classes supported (from terrain_classifier.py):
  1  Coastal Lowland       7  High Hill
  2  Floodplain            8  Mountain
  3  Alluvial Plain        9  Plateau / Mesa
  4  Valley / River Basin  10 Escarpment / Cliff
  5  Piedmont / Foothill   11 Arid Plain
  6  Low Hill              12 Coastal Dune

Input directory (dem_features/) must contain:
  Required:  terrain_class.tif, slope.tif, elevation.tif (or breached_dem.tif)
  Preferred: twi.tif, plan_curv.tif, d8_flow_acc.tif, d8_flow_dir.tif,
             lulc.tif, soil_class.tif (or RZSM_2024_Soil.tif),
             river_network.tif (or .shp), fault_lines.shp,
             aspect.tif, roughness.tif, profile_curv.tif,
             dinf_flow_acc.tif, mfd_flow_acc.tif

Output: flood_susceptibility.tif/.shp, flood_class.tif/.shp

Author: Aether-Disaster GIS Microservice
"""

import os
import warnings
import numpy as np
import rasterio
import rasterio.features as rio_features
from rasterio.transform import array_bounds
from rasterio.features import sieve
from rasterio.transform import Affine
from scipy.ndimage import (uniform_filter, gaussian_filter,
                           distance_transform_edt, generic_filter)
from scipy.stats import rankdata
import geopandas as gpd
from shapely.geometry import shape, box
import time

warnings.filterwarnings("ignore")

# ─────────────────────────────────────────────────────────────────────────────
# CONSTANTS
# ─────────────────────────────────────────────────────────────────────────────
NODATA        = -9999.0
NODATA_INT    = -9999

TERRAIN_NAMES = {
    1: "Coastal_Lowland",   2: "Floodplain",         3: "Alluvial_Plain",
    4: "Valley_River_Basin",5: "Piedmont_Foothill",   6: "Low_Hill",
    7: "High_Hill",         8: "Mountain",            9: "Plateau_Mesa",
    10:"Escarpment_Cliff",  11:"Arid_Plain",          12:"Coastal_Dune",
}

# LULC class→ surface roughness (Manning's n proxy) and infiltration capacity
# Adapted from published Manning-n tables and USDA curve number logic
LULC_ROUGHNESS = {
    10: 0.40,   # Tree cover — high roughness, high interception
    20: 0.25,   # Shrubland
    30: 0.20,   # Grassland
    40: 0.15,   # Cropland — moderate, seasonal variation
    50: 0.05,   # Herbaceous wetland — low roughness, saturated
    60: 0.35,   # Mangroves — very high roughness
    70: 0.12,   # Moss and lichen
    80: 0.03,   # Bare/sparse vegetation — lowest resistance
    90: 0.02,   # Built-up — impervious, lowest infiltration
    100: 0.01,  # Permanent water bodies
    110: 0.08,  # Snow and ice
    254: 0.10,  # Unclassified
}

# LULC → infiltration capacity (0=impervious → 1=highly permeable)
LULC_INFILTRATION = {
    10: 0.80, 20: 0.65, 30: 0.55, 40: 0.45, 50: 0.20,
    60: 0.70, 70: 0.50, 80: 0.15, 90: 0.05, 100: 0.00,
    110: 0.30, 254: 0.40,
}

# LULC → root cohesion factor for landslide (higher = more stable)
# Based on published root-cohesion studies (e.g., Sidle & Ochiai 2006)
LULC_ROOT_COHESION = {
    10: 8.5,   # Dense forest: ~8.5 kPa
    20: 4.0,   # Shrubland: ~4 kPa
    30: 2.5,   # Grassland: ~2.5 kPa
    40: 1.5,   # Cropland: ~1.5 kPa
    50: 1.0,   # Wetland
    60: 6.0,   # Mangroves
    70: 1.2,   # Moss/lichen
    80: 0.5,   # Bare: minimal root cohesion
    90: 0.0,   # Built-up: no cohesion
    100: 0.0,  # Water
    110: 0.0,  # Snow/ice
    254: 1.5,  # Unclassified
}

# Soil RZSM-derived hydraulic properties:
# RZSM mean ~0.20–0.32 across India → map to soil types
# These are physics-based defaults; per-pixel RZSM used when available
SOIL_COHESION_KPA = {
    # Soil class index → cohesion (kPa) for Mohr-Coulomb
    # Approximated from FAO soil texture classes
    1: 25.0,  # Clay-heavy: high cohesion
    2: 18.0,  # Clay-loam
    3: 12.0,  # Sandy-loam
    4: 8.0,   # Sandy
    5: 5.0,   # Gravelly / coarse
    6: 15.0,  # Silt-loam
    0: 10.0,  # Default/unknown
}

SOIL_FRICTION_DEG = {
    1: 15.0,  # Clay — lower friction angle
    2: 22.0,  # Clay-loam
    3: 30.0,  # Sandy-loam
    4: 35.0,  # Sandy — higher friction angle
    5: 38.0,  # Gravelly
    6: 25.0,  # Silt-loam
    0: 28.0,  # Default
}

SOIL_HYDRAULIC_CONDUCTIVITY = {
    # m/day — controls drainage speed
    1: 0.1,   # Clay
    2: 0.5,   # Clay-loam
    3: 2.0,   # Sandy-loam
    4: 10.0,  # Sandy
    5: 20.0,  # Gravelly
    6: 0.8,   # Silt-loam
    0: 1.5,   # Default
}


# ─────────────────────────────────────────────────────────────────────────────
# I/O UTILITIES
# ─────────────────────────────────────────────────────────────────────────────
def _read(directory: str, name: str):
    """Read a .tif layer. Returns (float32 array with NaN masking, meta) or (None,None)."""
    path = os.path.join(directory, f"{name}.tif")
    if not os.path.exists(path):
        return None, None
    with rasterio.open(path) as src:
        arr = src.read(1).astype(np.float32)
        meta = src.meta.copy()
        nd = src.nodata
    if nd is not None:
        arr[arr == nd] = np.nan
    arr[~np.isfinite(arr)] = np.nan
    return arr, meta


def _read_multiband(directory: str, name: str, band: int = 1):
    """Read specific band from multiband tif (e.g., RZSM soil moisture)."""
    path = os.path.join(directory, f"{name}.tif")
    if not os.path.exists(path):
        return None, None
    with rasterio.open(path) as src:
        if band > src.count:
            band = 1
        arr = src.read(band).astype(np.float32)
        meta = src.meta.copy()
        nd = src.nodata
    if nd is not None:
        arr[arr == nd] = np.nan
    arr[~np.isfinite(arr)] = np.nan
    return arr, meta


def _save_continuous(arr, meta, path, description="susceptibility"):
    """Save a 0–1 float array as a compressed GeoTIFF."""
    m = meta.copy()
    m.update(dtype="float32", nodata=NODATA, count=1,
             compress="lzw", tiled=True, blockxsize=256, blockysize=256)
    with rasterio.open(path, "w", **m) as dst:
        out = arr.copy()
        out[~np.isfinite(out)] = NODATA
        dst.write(out.astype(np.float32), 1)
        dst.update_tags(1, DESCRIPTION=description,
                        RANGE="0=Very_Low 1=Very_High",
                        CLASSIFICATION="Physics-informed static susceptibility")


def _save_class(cls_arr, meta, path, class_labels: dict):
    """Save integer class array (1–5) as a compressed GeoTIFF."""
    m = meta.copy()
    m.update(dtype="int16", nodata=NODATA_INT, count=1,
             compress="lzw", tiled=True, blockxsize=256, blockysize=256)
    with rasterio.open(path, "w", **m) as dst:
        out = cls_arr.copy()
        out[~np.isfinite(cls_arr)] = NODATA_INT
        dst.write(out.astype(np.int16), 1)
        for k, v in class_labels.items():
            dst.update_tags(1, **{f"CLASS_{k}": v})


def _vectorise(arr, meta, output_path, value_field="value", is_float=False):
    """Vectorise a raster to shapefile with sieve + downsample."""
    t0 = time.time()
    h, w = arr.shape

    if is_float:
        # Quantise to 0–100 integer for efficient vectorisation
        quant = np.full_like(arr, NODATA_INT, dtype=np.int16)
        valid = np.isfinite(arr)
        quant[valid] = (arr[valid] * 100).astype(np.int16)
        work = quant
    else:
        work = arr.astype(np.int16)

    sieved = sieve(work.astype(np.int16), size=8)
    ds_factor = max(1, min(h, w) // 1000) if min(h, w) > 2000 else 2
    new_h, new_w = h // ds_factor, w // ds_factor
    if new_h < 1 or new_w < 1:
        ds_factor = 1
        ds_arr = sieved
        new_transform = meta["transform"]
    else:
        ds_arr = sieved[::ds_factor, ::ds_factor]
        new_transform = meta["transform"] * Affine.scale(ds_factor, ds_factor)

    valid_mask = (ds_arr != NODATA_INT).astype(np.uint8)
    shapes_gen = rio_features.shapes(
        ds_arr.astype(np.int32),
        mask=valid_mask,
        transform=new_transform,
    )
    records = []
    for geom_dict, val in shapes_gen:
        v = int(val)
        if v == NODATA_INT:
            continue
        display_val = v / 100.0 if is_float else v
        records.append({
            "geometry": shape(geom_dict),
            value_field: display_val,
        })

    if records:
        gdf = gpd.GeoDataFrame(records, crs=str(meta["crs"]))
        gdf.to_file(output_path)
        print(f"  SHP saved → {os.path.basename(output_path)} ({len(records)} polys, {time.time()-t0:.1f}s)")
    else:
        print(f"  [WARN] No polygons for {os.path.basename(output_path)}")
    return output_path


def _class_to_shp(cls_arr, meta, output_path):
    CLASS_LABELS = {1: "Very_Low", 2: "Low", 3: "Moderate", 4: "High", 5: "Very_High"}
    t0 = time.time()
    sieved = sieve(cls_arr.astype(np.int16), size=8)
    h, w = cls_arr.shape
    ds = max(1, min(h, w) // 800)
    ds_arr = sieved[::ds, ::ds]
    new_transform = meta["transform"] * Affine.scale(ds, ds)
    valid_mask = ((ds_arr >= 1) & (ds_arr <= 5)).astype(np.uint8)
    shapes_gen = rio_features.shapes(ds_arr.astype(np.int32),
                                     mask=valid_mask, transform=new_transform)
    records = []
    for geom_dict, cid in shapes_gen:
        c = int(cid)
        if 1 <= c <= 5:
            records.append({"geometry": shape(geom_dict),
                             "class_id": c,
                             "class_name": CLASS_LABELS.get(c, "Unknown")})
    if records:
        gdf = gpd.GeoDataFrame(records, crs=str(meta["crs"]))
        gdf.to_file(output_path)
        print(f"  Class SHP → {os.path.basename(output_path)} ({len(records)} polys, {time.time()-t0:.1f}s)")


# ─────────────────────────────────────────────────────────────────────────────
# TERRAIN-CLASS PARAMETER TABLES
# These encode the physical behaviour of each terrain in real hydrology/geo
# ─────────────────────────────────────────────────────────────────────────────

def get_flood_terrain_params(terrain_cls: int) -> dict:
    """
    Return flood susceptibility algorithm parameters per terrain class.
    All weights are physically motivated:
      - Floodplains dominated by flow accumulation + TWI
      - Mountains dominated by flow velocity (slope × FA)
      - Coastal: tidal + elevation + proximity to coast/river
      - Plateaus: moderate with drainage concentration focus
    """
    params = {
        # Floodplain (2): Dominated by inundation from overbank flow
        2: {"w_fa": 0.35, "w_twi": 0.30, "w_elev": 0.20, "w_slope": 0.05,
            "w_river": 0.08, "w_drain": 0.02, "w_lulc": 0.05, "w_soil": 0.08,
            "algo": "inundation",
            "elev_thresh": 100, "slope_cap": 5, "twi_boost": 1.5,
            "fa_transform": "log", "smooth_sigma": 3.0},
        # Coastal Lowland (1): Flooding from sea + river + tidal
        1: {"w_fa": 0.20, "w_twi": 0.25, "w_elev": 0.30, "w_slope": 0.05,
            "w_river": 0.12, "w_drain": 0.03, "w_lulc": 0.05, "w_soil": 0.05,
            "algo": "coastal_inundation",
            "elev_thresh": 50, "slope_cap": 3, "twi_boost": 1.2,
            "fa_transform": "sqrt", "smooth_sigma": 4.0},
        # Coastal Dune (12): Storm surge + overwash
        12: {"w_fa": 0.15, "w_twi": 0.20, "w_elev": 0.35, "w_slope": 0.10,
             "w_river": 0.08, "w_drain": 0.02, "w_lulc": 0.05, "w_soil": 0.05,
             "algo": "coastal_inundation",
             "elev_thresh": 30, "slope_cap": 10, "twi_boost": 1.0,
             "fa_transform": "linear", "smooth_sigma": 3.5},
        # Alluvial Plain (3): Moderate inundation risk, irrigation effects
        3: {"w_fa": 0.30, "w_twi": 0.25, "w_elev": 0.15, "w_slope": 0.08,
            "w_river": 0.10, "w_drain": 0.05, "w_lulc": 0.07, "w_soil": 0.10,
            "algo": "inundation",
            "elev_thresh": 300, "slope_cap": 8, "twi_boost": 1.2,
            "fa_transform": "log", "smooth_sigma": 2.5},
        # Valley / River Basin (4): Channel flooding + lateral inundation
        4: {"w_fa": 0.32, "w_twi": 0.28, "w_elev": 0.12, "w_slope": 0.08,
            "w_river": 0.12, "w_drain": 0.05, "w_lulc": 0.03, "w_soil": 0.05,
            "algo": "channel_flood",
            "elev_thresh": 800, "slope_cap": 15, "twi_boost": 1.3,
            "fa_transform": "log", "smooth_sigma": 2.0},
        # Piedmont / Foothill (5): Flash flood risk from upstream
        5: {"w_fa": 0.28, "w_twi": 0.20, "w_elev": 0.10, "w_slope": 0.18,
            "w_river": 0.10, "w_drain": 0.07, "w_lulc": 0.07, "w_soil": 0.05,
            "algo": "flash_flood",
            "elev_thresh": 600, "slope_cap": 20, "twi_boost": 1.0,
            "fa_transform": "log", "smooth_sigma": 1.5},
        # Low Hill (6): Localized runoff concentration
        6: {"w_fa": 0.22, "w_twi": 0.18, "w_elev": 0.08, "w_slope": 0.22,
            "w_river": 0.08, "w_drain": 0.10, "w_lulc": 0.07, "w_soil": 0.05,
            "algo": "flash_flood",
            "elev_thresh": 900, "slope_cap": 25, "twi_boost": 0.9,
            "fa_transform": "log", "smooth_sigma": 1.5},
        # High Hill (7): Low flood, mainly in valleys/hollows
        7: {"w_fa": 0.18, "w_twi": 0.15, "w_elev": 0.05, "w_slope": 0.25,
            "w_river": 0.10, "w_drain": 0.15, "w_lulc": 0.07, "w_soil": 0.05,
            "algo": "flash_flood",
            "elev_thresh": 1800, "slope_cap": 35, "twi_boost": 0.8,
            "fa_transform": "sqrt", "smooth_sigma": 1.0},
        # Mountain (8): Flash flood in gorges, debris flow corridors
        8: {"w_fa": 0.20, "w_twi": 0.12, "w_elev": 0.05, "w_slope": 0.28,
            "w_river": 0.12, "w_drain": 0.15, "w_lulc": 0.05, "w_soil": 0.03,
            "algo": "mountain_flood",
            "elev_thresh": 3000, "slope_cap": 60, "twi_boost": 0.7,
            "fa_transform": "sqrt", "smooth_sigma": 1.0},
        # Plateau / Mesa (9): Internal drainage, playa flooding
        9: {"w_fa": 0.25, "w_twi": 0.28, "w_elev": 0.10, "w_slope": 0.08,
            "w_river": 0.08, "w_drain": 0.12, "w_lulc": 0.05, "w_soil": 0.04,
            "algo": "plateau_pond",
            "elev_thresh": 1500, "slope_cap": 5, "twi_boost": 1.1,
            "fa_transform": "log", "smooth_sigma": 2.0},
        # Escarpment (10): Toe-of-slope flooding from runoff
        10: {"w_fa": 0.22, "w_twi": 0.15, "w_elev": 0.10, "w_slope": 0.20,
             "w_river": 0.15, "w_drain": 0.12, "w_lulc": 0.03, "w_soil": 0.03,
             "algo": "flash_flood",
             "elev_thresh": 2000, "slope_cap": 70, "twi_boost": 0.9,
             "fa_transform": "sqrt", "smooth_sigma": 1.5},
        # Arid Plain (11): Wadi flash flood, sheet flood after cloudbursts
        11: {"w_fa": 0.30, "w_twi": 0.20, "w_elev": 0.12, "w_slope": 0.10,
             "w_river": 0.12, "w_drain": 0.08, "w_lulc": 0.05, "w_soil": 0.03,
             "algo": "sheet_flood",
             "elev_thresh": 400, "slope_cap": 10, "twi_boost": 0.8,
             "fa_transform": "log", "smooth_sigma": 2.5},
    }
    return params.get(terrain_cls, params[3])  # default to alluvial plain


def _normalise(arr, vmin=None, vmax=None, percentile_clip=(1, 99)):
    """Robust 0–1 normalisation using percentile clipping."""
    out = arr.copy().astype(np.float32)
    valid = np.isfinite(out)
    if not valid.any():
        return out
    if vmin is None:
        vmin = np.nanpercentile(out[valid], percentile_clip[0])
    if vmax is None:
        vmax = np.nanpercentile(out[valid], percentile_clip[1])
    if vmax <= vmin:
        out[valid] = 0.0
        return out
    out[valid] = np.clip((out[valid] - vmin) / (vmax - vmin), 0.0, 1.0)
    return out


def _invert(arr):
    """Invert a 0–1 array (1 − arr), preserving NaN."""
    out = np.full_like(arr, np.nan)
    valid = np.isfinite(arr)
    out[valid] = 1.0 - arr[valid]
    return out


def _smooth(arr, sigma, valid_mask=None):
    """Gaussian smooth with NaN handling."""
    if sigma <= 0:
        return arr
    filled = arr.copy()
    if valid_mask is None:
        valid_mask = np.isfinite(arr)
    fill_val = np.nanmedian(arr[valid_mask]) if valid_mask.any() else 0.0
    filled[~valid_mask] = fill_val
    smoothed = gaussian_filter(filled, sigma=sigma)
    result = arr.copy()
    result[valid_mask] = smoothed[valid_mask]
    return result


def _build_proximity_raster(shp_path, meta, max_dist_m=50000):
    """
    Build a distance raster (in map units) from a vector layer.
    Returns normalised inverse proximity (0=far, 1=right on feature).
    """
    if shp_path is None or not os.path.exists(shp_path):
        return None

    h = meta["height"]
    w = meta["width"]

    try:
        bounds = array_bounds(h, w, meta["transform"])
        gdf = gpd.read_file(shp_path, bbox=bounds)
        if gdf.empty:
            return None

        burn_mask = np.zeros((h, w), dtype=np.uint8)
        pixel_size = abs(meta["transform"].a)  # degrees or metres
        for geom in gdf.geometry:
            if geom is None:
                continue
            try:
                shapes_list = [(geom.__geo_interface__, 1)]
                rasterised = rio_features.rasterize(
                    shapes_list, out_shape=(h, w),
                    transform=meta["transform"], fill=0, dtype=np.uint8)
                burn_mask = np.maximum(burn_mask, rasterised)
            except Exception:
                pass

        if burn_mask.sum() == 0:
            return None

        # EDT gives distance in pixels; convert to spatial units
        dist_px = distance_transform_edt(1 - burn_mask)
        dist_m = dist_px * pixel_size * 111000  # approx m for geographic CRS

        # Clip and normalise: proximity = 1 near feature, 0 far
        prox = np.clip(1.0 - dist_m / max_dist_m, 0.0, 1.0)
        return prox.astype(np.float32)
    except Exception as e:
        print(f"  [WARN] Proximity raster failed for {shp_path}: {e}")
        return None


def _flow_acc_transform(fa, method="log"):
    """Transform flow accumulation by terrain-specific method."""
    fa_safe = np.where(fa > 0, fa, 1.0)
    if method == "log":
        return np.log1p(fa_safe)
    elif method == "sqrt":
        return np.sqrt(fa_safe)
    else:  # linear
        return fa_safe.copy()


def _lulc_to_map(lulc, lookup: dict, default=0.5):
    """Map LULC class values to continuous factor array."""
    out = np.full_like(lulc, default, dtype=np.float32)
    for cls_val, factor in lookup.items():
        out[lulc == cls_val] = factor
    return out


def _derive_soil_properties(soil_arr, rzsm_arr):
    """
    Derive per-pixel cohesion, friction angle, Ks from soil classification.
    If RZSM (root zone soil moisture) is available, modulate cohesion:
    wet soil → lower effective cohesion (Terzaghi effective stress).
    """
    h, w = soil_arr.shape if soil_arr is not None else (1, 1)

    if soil_arr is not None:
        cohesion = np.full((h, w), 10.0, dtype=np.float32)
        friction = np.full((h, w), 28.0, dtype=np.float32)
        ks = np.full((h, w), 1.5, dtype=np.float32)
        for cls, c_val in SOIL_COHESION_KPA.items():
            mask = soil_arr == cls
            cohesion[mask] = c_val
            friction[mask] = SOIL_FRICTION_DEG.get(cls, 28.0)
            ks[mask] = SOIL_HYDRAULIC_CONDUCTIVITY.get(cls, 1.5)
    else:
        cohesion = np.full((h, w), 10.0, dtype=np.float32)
        friction = np.full((h, w), 28.0, dtype=np.float32)
        ks = np.full((h, w), 1.5, dtype=np.float32)

    # RZSM modulation: high soil moisture → reduce effective cohesion
    # Based on Fredlund & Rahardjo (1993) unsaturated shear strength
    if rzsm_arr is not None:
        rzsm_safe = np.clip(rzsm_arr, 0.0, 1.0)
        # At RZSM=0 cohesion = base; at RZSM=1 reduce by 40%
        cohesion_factor = 1.0 - 0.40 * rzsm_safe
        cohesion = cohesion * cohesion_factor
        # High RZSM also reduces friction slightly (pore pressure)
        friction_factor = 1.0 - 0.15 * rzsm_safe
        friction = friction * friction_factor

    return cohesion, friction, ks


def _compute_saturation_proxy(twi, rzsm=None, ks=None):
    """
    Compute degree of saturation (m) for infinite slope model.
    m ∈ [0,1]: fraction of soil column that is saturated.

    TWI approach (Beven & Kirkby TOPMODEL):
      m ≈ f(TWI) — high TWI → higher saturation

    Modulated by RZSM when available and hydraulic conductivity.
    """
    if twi is None:
        # Fallback if no TWI
        if rzsm is not None:
            return np.clip(rzsm * 0.8, 0.0, 1.0).astype(np.float32)
        return np.full((1, 1), 0.3, dtype=np.float32)

    # TWI-based: calibrated against observed landslide initiation
    # twi_thresh = 8 is typical threshold for saturation in India
    m_twi = np.clip((twi - 2.0) / 12.0, 0.0, 1.0)

    if rzsm is not None:
        # Weighted combination: TWI dominates, RZSM adjusts
        rzsm_safe = np.clip(rzsm, 0.0, 1.0)
        m = 0.6 * m_twi + 0.4 * rzsm_safe
    else:
        m = m_twi

    # Hydraulic conductivity correction: high Ks → faster drainage → lower m
    if ks is not None:
        ks_norm = np.clip(ks / 20.0, 0.01, 1.0)  # normalise to 0-1
        m = m * (1.0 - 0.3 * ks_norm)

    return np.clip(m, 0.0, 1.0).astype(np.float32)


# ─────────────────────────────────────────────────────────────────────────────
# FLOOD SUSCEPTIBILITY CORE
# ─────────────────────────────────────────────────────────────────────────────
def compute_flood_susceptibility_for_terrain(
        terrain_id, mask, params,
        elev, slope, twi, fa, curv_plan, curv_prof,
        river_prox, lulc, soil_arr, rzsm, drain_density, h, w):
    """
    Compute flood susceptibility for pixels of a given terrain class.
    Returns a partial array (only valid where mask==True) with values 0–1.
    """
    algo = params["algo"]
    flood = np.full((h, w), np.nan, dtype=np.float32)
    vm = mask & np.isfinite(elev)
    if not vm.any():
        return flood

    # ── 1. Flow Accumulation Component ──────────────────────────────────────
    if fa is not None:
        fa_t = _flow_acc_transform(fa, params["fa_transform"])
        fa_norm = _normalise(fa_t)
    else:
        fa_norm = np.full((h, w), 0.3, dtype=np.float32)

    # ── 2. TWI Component ─────────────────────────────────────────────────────
    if twi is not None:
        twi_norm = _normalise(twi * params["twi_boost"])
    else:
        twi_norm = np.full((h, w), 0.3, dtype=np.float32)

    # ── 3. Elevation Component ───────────────────────────────────────────────
    # Low elevation = higher flood susceptibility (below terrain threshold)
    elev_thresh = params["elev_thresh"]
    elev_flood = np.clip(1.0 - (elev / elev_thresh), 0.0, 1.0)
    elev_norm = np.where(np.isfinite(elev), elev_flood, np.nan).astype(np.float32)

    # ── 4. Slope Component (inverse — steeper = less inundation) ─────────────
    slope_cap = params["slope_cap"]
    slope_inv = np.clip(1.0 - (slope / slope_cap), 0.0, 1.0)
    slope_norm = np.where(np.isfinite(slope), slope_inv, np.nan).astype(np.float32)

    # ── 5. River Proximity ───────────────────────────────────────────────────
    river_c = river_prox if river_prox is not None else np.full((h, w), 0.2, dtype=np.float32)

    # ── 6. Drainage Density ──────────────────────────────────────────────────
    drain_c = drain_density if drain_density is not None else np.full((h, w), 0.3, dtype=np.float32)

    # ── 7. LULC Component ────────────────────────────────────────────────────
    if lulc is not None:
        # Low infiltration + low roughness → higher flood susceptibility
        infilt = _lulc_to_map(lulc, LULC_INFILTRATION, default=0.4)
        rough = _lulc_to_map(lulc, LULC_ROUGHNESS, default=0.2)
        # Low infiltration + low roughness = high flood susceptibility
        lulc_flood = (1.0 - infilt) * 0.6 + (1.0 - rough) * 0.4
    else:
        lulc_flood = np.full((h, w), 0.3, dtype=np.float32)

    # ── 8. Soil Component ────────────────────────────────────────────────────
    # High RZSM = saturated soil = higher flood susceptibility
    if rzsm is not None:
        soil_c = np.clip(rzsm, 0.0, 1.0)
    elif soil_arr is not None:
        # Clay soils → poor drainage → higher flood susceptibility
        _, _, ks = _derive_soil_properties(soil_arr, None)
        soil_c = 1.0 - np.clip(ks / 20.0, 0.0, 1.0)
    else:
        soil_c = np.full((h, w), 0.3, dtype=np.float32)

    # ── Algorithm-specific adjustments ───────────────────────────────────────
    if algo == "coastal_inundation":
        # Boost low-elevation pixels near coast heavily
        coastal_boost = np.where(elev < 10, 1.3, 1.0)
        fa_norm = np.clip(fa_norm * coastal_boost, 0.0, 1.0)

    elif algo == "mountain_flood":
        # Gorge flooding — high FA in narrow valleys, boosted by slope
        if fa is not None and slope is not None:
            velocity_proxy = np.log1p(np.maximum(fa, 1)) * (slope / 30.0)
            vel_norm = _normalise(velocity_proxy)
            fa_norm = 0.5 * fa_norm + 0.5 * vel_norm

    elif algo == "plateau_pond":
        # Depressions on plateau concentrate water — use plan curvature
        if curv_plan is not None:
            # Concave curvature (negative plan curv) → water concentration
            pond_factor = np.where(curv_plan < 0,
                                   np.clip(np.abs(curv_plan) * 100, 0, 1),
                                   0.0)
            twi_norm = np.clip(twi_norm + 0.3 * pond_factor, 0.0, 1.0)

    elif algo == "sheet_flood":
        # Arid plains — water spreads laterally, low TWI typical
        # Boost based on flow accumulation + low slope
        flatness = np.clip(1.0 - slope / 5.0, 0.0, 1.0)
        fa_norm = np.clip(fa_norm * (0.7 + 0.3 * flatness), 0.0, 1.0)

    # ── Weighted combination ──────────────────────────────────────────────────
    w_sum = (params["w_fa"] + params["w_twi"] + params["w_elev"] +
             params["w_slope"] + params["w_river"] + params["w_drain"] +
             params["w_lulc"] + params["w_soil"])

    flood_val = (
        params["w_fa"]    * fa_norm +
        params["w_twi"]   * twi_norm +
        params["w_elev"]  * elev_norm +
        params["w_slope"] * slope_norm +
        params["w_river"] * river_c +
        params["w_drain"] * drain_c +
        params["w_lulc"]  * lulc_flood +
        params["w_soil"]  * soil_c
    ) / w_sum

    # ── Concave curvature enhancement ────────────────────────────────────────
    if curv_plan is not None:
        curv_boost = np.where(curv_plan < -0.002,
                              np.clip(np.abs(curv_plan) * 50, 0.0, 0.2), 0.0)
        flood_val = np.clip(flood_val + curv_boost, 0.0, 1.0)

    # Apply to terrain mask only
    flood[vm] = flood_val[vm]
    return flood


def compute_flood_susceptibility(features_dir: str, river_shp=None, layers=None) -> np.ndarray:
    """
    Master flood susceptibility calculator.
    Iterates over terrain classes and applies terrain-specific algorithms.
    """
    print("[FloodMapper] Computing flood susceptibility...")
    L = layers  # pre-loaded layer dict

    terrain = L.get("terrain_class")
    elev    = L.get("elev")
    slope   = L.get("slope")
    twi     = L.get("twi")
    fa      = L.get("fa")
    curv_p  = L.get("curv_plan")
    curv_r  = L.get("curv_prof")
    lulc    = L.get("lulc")
    soil    = L.get("soil")
    rzsm    = L.get("rzsm")
    meta    = L.get("meta")

    h, w = elev.shape
    flood_final = np.full((h, w), np.nan, dtype=np.float32)

    # Build river proximity once
    river_prox = None
    if river_shp and os.path.exists(river_shp):
        print("  Building river proximity raster...")
        river_prox = _build_proximity_raster(river_shp, meta, max_dist_m=30000)
    elif L.get("river_raster") is not None:
        river_prox = _normalise(L.get("river_raster"))

    # Build drainage density proxy from FA
    drain_density = None
    if fa is not None:
        # High FA variance in a window → dense drainage
        fa_log = np.log1p(np.where(np.isfinite(fa) & (fa > 0), fa, 1))
        # Local std dev of log-FA as drainage density proxy
        fa_mean = uniform_filter(np.where(np.isfinite(fa_log), fa_log, 0.0), size=15)
        fa_sq_mean = uniform_filter(np.where(np.isfinite(fa_log), fa_log**2, 0.0), size=15)
        fa_var = np.maximum(fa_sq_mean - fa_mean**2, 0)
        drain_density = _normalise(np.sqrt(fa_var))

    # Process each terrain class
    present_classes = np.unique(terrain[np.isfinite(terrain) & (terrain != NODATA_INT)])
    for tc in present_classes:
        tc_int = int(tc)
        if tc_int not in TERRAIN_NAMES:
            continue
        mask = (terrain == tc_int) & np.isfinite(elev)
        if not mask.any():
            continue

        px_count = mask.sum()
        print(f"  Terrain {tc_int} ({TERRAIN_NAMES[tc_int]}): {px_count:,} px")
        params = get_flood_terrain_params(tc_int)

        partial = compute_flood_susceptibility_for_terrain(
            tc_int, mask, params,
            elev, slope, twi, fa, curv_p, curv_r,
            river_prox, lulc, soil, rzsm, drain_density, h, w)

        # Terrain-specific smoothing
        sigma = params["smooth_sigma"]
        partial_smooth = _smooth(partial, sigma=sigma, valid_mask=mask)
        flood_final[mask] = partial_smooth[mask]

    # Final global normalisation (preserve relative per-terrain ranges)
    # Use a soft percentile clamp to prevent a single class from dominating
    valid = np.isfinite(flood_final)
    if valid.any():
        p2 = np.nanpercentile(flood_final[valid], 2)
        p98 = np.nanpercentile(flood_final[valid], 98)
        flood_final[valid] = np.clip(
            (flood_final[valid] - p2) / (p98 - p2 + 1e-9), 0.0, 1.0)

    print(f"  Flood susceptibility range: {np.nanmin(flood_final):.3f} – {np.nanmax(flood_final):.3f}")
    return flood_final


# ─────────────────────────────────────────────────────────────────────────────
# LANDSLIDE SUSCEPTIBILITY CORE
# ─────────────────────────────────────────────────────────────────────────────
def susceptibility_to_class(arr: np.ndarray, terrain_cls_arr: np.ndarray = None) -> np.ndarray:
    """
    Convert continuous 0–1 susceptibility to 5-class map.
    Class breaks are terrain-adaptive using quantile-based local thresholds.
    
    Global breaks: Very Low<0.15, Low 0.15–0.35, Moderate 0.35–0.55,
                   High 0.55–0.75, Very High>0.75
    
    Per-terrain refinement: breaks shift based on terrain's baseline range.
    """
    cls_arr = np.full_like(arr, NODATA_INT, dtype=np.int16)
    valid = np.isfinite(arr)

    if terrain_cls_arr is not None:
        # Per-terrain quantile-aware classification
        result = np.full_like(arr, NODATA_INT, dtype=np.int16)
        terrain_ids = np.unique(terrain_cls_arr[valid & np.isfinite(terrain_cls_arr)])

        for tc in terrain_ids:
            tc_int = int(tc)
            if tc_int not in TERRAIN_NAMES:
                continue
            tmask = valid & (terrain_cls_arr == tc_int)
            if not tmask.any():
                continue
            vals = arr[tmask]

            # Use terrain's own distribution for class breaks (equal-frequency)
            # to avoid low-risk terrain all falling in class 1
            p20 = np.nanpercentile(vals, 20)
            p40 = np.nanpercentile(vals, 40)
            p60 = np.nanpercentile(vals, 60)
            p80 = np.nanpercentile(vals, 80)

            c = np.full(vals.shape, 1, dtype=np.int16)
            c[vals >= p20] = 2
            c[vals >= p40] = 3
            c[vals >= p60] = 4
            c[vals >= p80] = 5
            result[tmask] = c

        return result
    else:
        # Global fixed breaks
        cls_arr[valid] = 1  # Very Low
        cls_arr[valid & (arr >= 0.15)] = 2  # Low
        cls_arr[valid & (arr >= 0.35)] = 3  # Moderate
        cls_arr[valid & (arr >= 0.55)] = 4  # High
        cls_arr[valid & (arr >= 0.75)] = 5  # Very High
        return cls_arr



# ─────────────────────────────────────────────────────────────────────────────
# RESAMPLING UTILITY
# ─────────────────────────────────────────────────────────────────────────────
def _resample_to_dem(arr, arr_meta, dem_meta):
    """
    Resample any raster array to match the DEM grid (extent, resolution, CRS).
    Uses bilinear resampling for continuous data.
    Returns a float32 array aligned to the DEM, or None if input is None.
    """
    from rasterio.warp import reproject, Resampling
    if arr is None:
        return None
    if arr.shape == (dem_meta["height"], dem_meta["width"]):
        return arr  # already matches DEM grid — no work needed
    dest = np.full((dem_meta["height"], dem_meta["width"]), np.nan, dtype=np.float32)
    try:
        reproject(
            source=arr,
            destination=dest,
            src_transform=arr_meta["transform"],
            src_crs=arr_meta.get("crs", dem_meta["crs"]),
            dst_transform=dem_meta["transform"],
            dst_crs=dem_meta["crs"],
            src_nodata=np.nan,
            dst_nodata=np.nan,
            resampling=Resampling.bilinear,
        )
    except Exception as e:
        print(f"  [WARN] Resample failed: {e}")
        return arr  # return original on failure
    return dest

# ─────────────────────────────────────────────────────────────────────────────
# LAYER LOADING
# ─────────────────────────────────────────────────────────────────────────────
def _load_layers(features_dir: str) -> dict:
    """Load all available layers from dem_features directory."""
    print("[LayerLoader] Loading input layers...")
    L = {}

    def _try(name, alt=None):
        arr, meta = _read(features_dir, name)
        if arr is None and alt:
            arr, meta = _read(features_dir, alt)
        return arr, meta

    elev, meta = _try("breached_dem", "elevation")
    if elev is None:
        raise FileNotFoundError(f"No elevation raster in {features_dir}")
    L["elev"] = elev
    L["meta"] = meta
    L["meta"]["height"] = elev.shape[0]
    L["meta"]["width"] = elev.shape[1]

    L["slope"],      _ = _try("slope")
    L["aspect"],     _ = _try("aspect")
    L["roughness"],  _ = _try("roughness")
    L["curv_plan"],  _ = _try("plan_curv")
    L["curv_prof"],  _ = _try("profile_curv")
    L["twi"],        _ = _try("twi", "twi_d8")
    L["fa"],         _ = _try("d8_flow_acc", "flow_acc")
    L["fa_dinf"],    _ = _try("dinf_flow_acc")
    L["fa_mfd"],     _ = _try("mfd_flow_acc")
    L["lulc"],       _ = _try("lulc")
    L["soil"],       _ = _try("soil_class")
    L["terrain_class"], _ = _try("terrain_class")
    L["river_raster"],  _ = _try("river_network")

    # ── Resample all layers to DEM grid ──────────────────────────────────────
    _dem_meta = L["meta"]
    _layers_to_resample = [
        "slope", "aspect", "roughness", "curv_plan", "curv_prof",
        "twi", "fa", "fa_dinf", "fa_mfd", "lulc", "soil",
        "terrain_class", "river_raster",
    ]
    for _lname in _layers_to_resample:
        if L.get(_lname) is not None:
            _arr, _ameta = _read(features_dir, {
                "slope": "slope", "aspect": "aspect", "roughness": "roughness",
                "curv_plan": "plan_curv", "curv_prof": "profile_curv",
                "twi": "twi", "fa": "d8_flow_acc", "fa_dinf": "dinf_flow_acc",
                "fa_mfd": "mfd_flow_acc", "lulc": "lulc", "soil": "soil_class",
                "terrain_class": "terrain_class", "river_raster": "river_network",
            }[_lname])
            if _ameta is not None:
                L[_lname] = _resample_to_dem(L[_lname], _ameta, _dem_meta)
    print("  All layers resampled to DEM grid.")

    # RZSM soil moisture (multiband — take temporal mean across bands)
    rzsm_path = os.path.join(features_dir, "RZSM_2024_Soil.tif")
    if os.path.exists(rzsm_path):
        print("  Loading RZSM soil moisture (taking band mean)...")
        try:
            with rasterio.open(rzsm_path) as src:
                # Take mean of all bands (temporal average) — robust proxy for
                # long-term moisture conditions
                n = min(src.count, 50)  # first 50 bands for efficiency
                bands = []
                for b in range(1, n + 1):
                    arr = src.read(b).astype(np.float32)
                    nd = src.nodata
                    if nd is not None:
                        arr[arr == nd] = np.nan
                    arr[~np.isfinite(arr)] = np.nan
                    bands.append(arr)
                rzsm_mean = np.nanmean(np.stack(bands, axis=0), axis=0)
                # Resample to match elevation grid if needed
                if rzsm_mean.shape != elev.shape:
                    from rasterio.warp import reproject, Resampling
                    rzsm_repr = np.full(elev.shape, np.nan, dtype=np.float32)
                    rzsm_src_meta = src.meta.copy()
                    reproject(
                        source=rzsm_mean,
                        destination=rzsm_repr,
                        src_transform=src.transform,
                        src_crs=src.crs,
                        dst_transform=meta["transform"],
                        dst_crs=meta["crs"],
                        src_nodata=np.nan,
                        dst_nodata=np.nan,
                        resampling=Resampling.bilinear
                    )
                    L["rzsm"] = rzsm_repr
                else:
                    L["rzsm"] = rzsm_mean
                print(f"  RZSM loaded: mean={np.nanmean(L['rzsm']):.3f}")
        except Exception as e:
            print(f"  [WARN] RZSM load failed: {e}")
            L["rzsm"] = None
    else:
        L["rzsm"] = None

    # Fallbacks
    h, w = elev.shape
    if L["slope"] is None:
        print("  [WARN] slope.tif missing — using zeros")
        L["slope"] = np.zeros((h, w), dtype=np.float32)
    if L["twi"] is None:
        print("  [WARN] twi.tif missing — using zeros")
        L["twi"] = np.zeros((h, w), dtype=np.float32)
    if L["fa"] is None:
        print("  [WARN] flow_acc missing — using zeros")
        L["fa"] = np.zeros((h, w), dtype=np.float32)
    if L["terrain_class"] is None:
        print("  [WARN] terrain_class.tif missing — assuming Alluvial Plain (3)")
        L["terrain_class"] = np.full((h, w), 3.0, dtype=np.float32)
    if L["curv_plan"] is None:
        L["curv_plan"] = None  # handled downstream

    # Use best available FA: prefer MFD (more stable) → D∞ → D8
    if L["fa_mfd"] is not None:
        L["fa"] = L["fa_mfd"]
        print("  Using MFD flow accumulation")
    elif L["fa_dinf"] is not None:
        L["fa"] = L["fa_dinf"]
        print("  Using D-infinity flow accumulation")
    else:
        print("  Using D8 flow accumulation")

    # Log summary
    loaded = [k for k, v in L.items() if v is not None and k != "meta"]
    print(f"  Loaded layers: {', '.join(loaded)}")
    return L


# ─────────────────────────────────────────────────────────────────────────────
# MAIN ENTRY POINT
# ─────────────────────────────────────────────────────────────────────────────
def run(features_dir: str,
        river_shp: str = None,
        output_dir: str = None) -> dict:
    """
    Run complete flood and landslide susceptibility mapping.

    Parameters
    ----------
    features_dir : str  — directory with DEM-derived layers
    river_shp    : str  — optional path to river/stream network .shp
    fault_shp    : str  — optional path to geological fault lines .shp
    output_dir   : str  — output directory (defaults to features_dir)

    Returns
    -------
    dict with paths to all output files and summary statistics
    """
    t_start = time.time()
    if output_dir is None:
        output_dir = features_dir
    os.makedirs(output_dir, exist_ok=True)

    CLASS_LABELS = {1: "Very_Low", 2: "Low", 3: "Moderate",
                    4: "High", 5: "Very_High"}

    print("=" * 65)
    print(" AETHER-DISASTER | Flood Susceptibility Mapper")
    print(f" Features dir: {features_dir}")
    print("=" * 65)

    # ── Load all layers ───────────────────────────────────────────────────
    layers = _load_layers(features_dir)
    meta = layers["meta"]
    terrain = layers["terrain_class"]

    # Detect auxiliary vector files in features_dir if not provided
    for shp_name in ["river_network.shp", "stream_network.shp", "rivers.shp"]:
        p = os.path.join(features_dir, shp_name)
        if os.path.exists(p) and river_shp is None:
            river_shp = p
            print(f"  Auto-detected river SHP: {shp_name}")

    # ── Flood Susceptibility ──────────────────────────────────────────────
    print("\n── FLOOD SUSCEPTIBILITY ─────────────────────────────────────────")
    flood_idx = compute_flood_susceptibility(features_dir, river_shp, layers)

    flood_tif = os.path.join(output_dir, "flood_susceptibility.tif")
    _save_continuous(flood_idx, meta, flood_tif, "Flood susceptibility index 0-1")
    print(f"  Saved: {os.path.basename(flood_tif)}")

    flood_class = susceptibility_to_class(flood_idx, terrain)
    flood_cls_tif = os.path.join(output_dir, "flood_class.tif")
    _save_class(flood_class.astype(np.float32), meta, flood_cls_tif, CLASS_LABELS)
    print(f"  Saved: {os.path.basename(flood_cls_tif)}")

    flood_idx_shp = os.path.join(output_dir, "flood_susceptibility.shp")
    _vectorise(flood_idx, meta, flood_idx_shp, "flood_idx", is_float=True)

    flood_cls_shp = os.path.join(output_dir, "flood_class.shp")
    _class_to_shp(flood_class, meta, flood_cls_shp)

    # ── Statistics ────────────────────────────────────────────────────────
    def _class_stats(cls_arr, name):
        stats = {}
        valid = cls_arr[(cls_arr >= 1) & (cls_arr <= 5)]
        total = len(valid)
        for c in range(1, 6):
            cnt = int((valid == c).sum())
            stats[CLASS_LABELS[c]] = {
                "pixel_count": cnt,
                "pct": round(cnt / total * 100, 2) if total > 0 else 0
            }
        return stats

    flood_stats = _class_stats(flood_class, "flood")

    elapsed = time.time() - t_start
    print(f"\n{'='*65}")
    print(f" COMPLETE in {elapsed:.1f}s")
    print(f"\n Flood class distribution:")
    for cls_name, s in flood_stats.items():
        print(f"   {cls_name:<12}: {s['pct']:5.1f}%  ({s['pixel_count']:,} px)")

    print("=" * 65)

    return {
        "flood_susceptibility_tif":  flood_tif,
        "flood_class_tif":           flood_cls_tif,
        "flood_susceptibility_shp":  flood_idx_shp,
        "flood_class_shp":           flood_cls_shp,
        "flood_class_stats":    flood_stats,
        "elapsed_seconds": elapsed,
    }


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    features_dir = r"D:\PDEU\SEM-4\Prediction Engine\database\India\DELHI\_state_level\dem_features"
    river_shp = r"D:\PDEU\SEM-4\Prediction Engine\Dataset\4 data\India_River\India_River_Final.shp"
    output_dir = r"D:\PDEU\SEM-4\Prediction Engine\database\flood\India\DELHI\_state_level\susceptibility_output"

    results = run(
        features_dir=features_dir,
        river_shp=river_shp,
        output_dir=output_dir,
    )
    print("\nOutput files:")
    for k, v in results.items():
        if isinstance(v, str) and os.path.exists(v):
            print(f"  {k}: {v}")
