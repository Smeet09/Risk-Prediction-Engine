"""
landslide_susceptibility_mapper.py — Physics-Informed Landslide Susceptibility Mapper
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

Output: landslide_susceptibility.tif/.shp, landslide_class.tif/.shp

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

def get_landslide_terrain_params(terrain_cls: int) -> dict:
    """
    Return landslide susceptibility algorithm parameters per terrain class.
    Uses physics-based Factor of Safety (FoS) framework:
      FoS = [c' + (γ·z - m·γw·z) · cos²α · tan(φ)] / [γ·z · sin(α) · cos(α)]
    Where m = degree of saturation (derived from TWI proxy)

    Additional mass wasting processes per terrain:
      - Mountain/High Hill: debris flows (high slope + soil saturation)
      - Escarpment: rotational/planar slides (very steep, tension cracks)
      - Piedmont: translational slides + debris avalanche
      - Coastal: submarine slides + wave-cut destabilisation
    """
    params = {
        # Mountain (8): debris flow, snow avalanche initiation, rockfall
        8: {"fos_weight": 0.55, "w_curv": 0.12, "w_fault": 0.10, "w_aspect": 0.05,
            "w_roughness": 0.08, "w_lulc": 0.05, "w_twi_ls": 0.05,
            "algo": "debris_flow",
            "gamma_soil": 20.0, "z_depth": 3.0, "min_slope_deg": 15,
            "curv_sensitivity": 2.0, "fault_decay_km": 3.0,
            "smooth_sigma": 1.0},
        # Escarpment / Cliff (10): rotational + planar slides, rockfall
        10: {"fos_weight": 0.60, "w_curv": 0.10, "w_fault": 0.12, "w_aspect": 0.05,
             "w_roughness": 0.06, "w_lulc": 0.03, "w_twi_ls": 0.04,
             "algo": "rotational_planar",
             "gamma_soil": 22.0, "z_depth": 2.5, "min_slope_deg": 25,
             "curv_sensitivity": 1.8, "fault_decay_km": 2.0,
             "smooth_sigma": 0.8},
        # High Hill (7): translational slides + debris avalanche
        7: {"fos_weight": 0.50, "w_curv": 0.13, "w_fault": 0.10, "w_aspect": 0.07,
            "w_roughness": 0.07, "w_lulc": 0.08, "w_twi_ls": 0.05,
            "algo": "translational",
            "gamma_soil": 19.0, "z_depth": 2.5, "min_slope_deg": 12,
            "curv_sensitivity": 1.5, "fault_decay_km": 4.0,
            "smooth_sigma": 1.2},
        # Piedmont / Foothill (5): shallow translational + earthflow
        5: {"fos_weight": 0.45, "w_curv": 0.15, "w_fault": 0.08, "w_aspect": 0.07,
            "w_roughness": 0.08, "w_lulc": 0.10, "w_twi_ls": 0.07,
            "algo": "translational",
            "gamma_soil": 18.5, "z_depth": 2.0, "min_slope_deg": 8,
            "curv_sensitivity": 1.3, "fault_decay_km": 5.0,
            "smooth_sigma": 1.5},
        # Low Hill (6): shallow slides, earthflows
        6: {"fos_weight": 0.42, "w_curv": 0.15, "w_fault": 0.08, "w_aspect": 0.08,
            "w_roughness": 0.08, "w_lulc": 0.12, "w_twi_ls": 0.07,
            "algo": "translational",
            "gamma_soil": 18.0, "z_depth": 1.8, "min_slope_deg": 6,
            "curv_sensitivity": 1.2, "fault_decay_km": 6.0,
            "smooth_sigma": 1.8},
        # Valley / River Basin (4): bank erosion slides, retrogressive failure
        4: {"fos_weight": 0.38, "w_curv": 0.18, "w_fault": 0.07, "w_aspect": 0.05,
            "w_roughness": 0.07, "w_lulc": 0.10, "w_twi_ls": 0.15,
            "algo": "bank_failure",
            "gamma_soil": 18.0, "z_depth": 2.0, "min_slope_deg": 5,
            "curv_sensitivity": 1.5, "fault_decay_km": 6.0,
            "smooth_sigma": 2.0},
        # Alluvial Plain (3): very low LS — creep only
        3: {"fos_weight": 0.15, "w_curv": 0.20, "w_fault": 0.05, "w_aspect": 0.05,
            "w_roughness": 0.05, "w_lulc": 0.10, "w_twi_ls": 0.40,
            "algo": "creep",
            "gamma_soil": 17.0, "z_depth": 1.5, "min_slope_deg": 2,
            "curv_sensitivity": 0.8, "fault_decay_km": 8.0,
            "smooth_sigma": 3.0},
        # Floodplain (2): minimal LS — lateral spreading only
        2: {"fos_weight": 0.10, "w_curv": 0.15, "w_fault": 0.05, "w_aspect": 0.05,
            "w_roughness": 0.05, "w_lulc": 0.10, "w_twi_ls": 0.50,
            "algo": "lateral_spreading",
            "gamma_soil": 17.5, "z_depth": 1.5, "min_slope_deg": 1,
            "curv_sensitivity": 0.6, "fault_decay_km": 8.0,
            "smooth_sigma": 3.5},
        # Coastal Lowland (1): coastal instability, wave-cut notch
        1: {"fos_weight": 0.20, "w_curv": 0.12, "w_fault": 0.05, "w_aspect": 0.10,
            "w_roughness": 0.08, "w_lulc": 0.15, "w_twi_ls": 0.30,
            "algo": "coastal_failure",
            "gamma_soil": 18.0, "z_depth": 1.5, "min_slope_deg": 2,
            "curv_sensitivity": 0.8, "fault_decay_km": 7.0,
            "smooth_sigma": 3.0},
        # Coastal Dune (12): dune collapse, aeolian destabilisation
        12: {"fos_weight": 0.25, "w_curv": 0.15, "w_fault": 0.03, "w_aspect": 0.12,
             "w_roughness": 0.10, "w_lulc": 0.15, "w_twi_ls": 0.20,
             "algo": "dune_collapse",
             "gamma_soil": 16.5, "z_depth": 1.2, "min_slope_deg": 2,
             "curv_sensitivity": 1.0, "fault_decay_km": 10.0,
             "smooth_sigma": 3.0},
        # Plateau / Mesa (9): scarp retreat, plateau-edge failure
        9: {"fos_weight": 0.30, "w_curv": 0.20, "w_fault": 0.10, "w_aspect": 0.08,
            "w_roughness": 0.07, "w_lulc": 0.08, "w_twi_ls": 0.17,
            "algo": "scarp_retreat",
            "gamma_soil": 19.0, "z_depth": 2.0, "min_slope_deg": 5,
            "curv_sensitivity": 1.3, "fault_decay_km": 4.0,
            "smooth_sigma": 2.0},
        # Arid Plain (11): minimal
        11: {"fos_weight": 0.08, "w_curv": 0.10, "w_fault": 0.05, "w_aspect": 0.05,
             "w_roughness": 0.05, "w_lulc": 0.12, "w_twi_ls": 0.55,
             "algo": "creep",
             "gamma_soil": 16.0, "z_depth": 1.0, "min_slope_deg": 1,
             "curv_sensitivity": 0.5, "fault_decay_km": 10.0,
             "smooth_sigma": 4.0},
    }
    return params.get(terrain_cls, params[3])


# ─────────────────────────────────────────────────────────────────────────────
# HELPER FUNCTIONS
# ─────────────────────────────────────────────────────────────────────────────
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
def compute_fos(slope_deg, cohesion_kpa, friction_deg, gamma_kN_m3, z_m, m_sat):
    """
    Infinite slope Factor of Safety (FoS).
    
    FoS = [c' + (γz - m·γw·z) · cos²α · tan(φ)] / [γz · sinα · cosα]
    
    Parameters:
      slope_deg   : slope angle in degrees
      cohesion_kpa: effective cohesion (kPa)
      friction_deg: effective friction angle (degrees)
      gamma_kN_m3 : unit weight of soil (kN/m³)
      z_m         : assumed failure depth (m)
      m_sat       : degree of saturation (0–1), pore pressure proxy
    
    Returns:
      FoS array (clipped to 0.1–5.0 for numerical stability)
    Notes:
      γw = 9.81 kN/m³ (water unit weight)
      Safe: FoS > 1.5;  Marginal: 1.0–1.5;  Unstable: < 1.0
    """
    GAMMA_W = 9.81  # kN/m³
    alpha_rad = np.radians(np.clip(slope_deg, 0.01, 89.99))

    cos2 = np.cos(alpha_rad) ** 2
    sincos = np.sin(alpha_rad) * np.cos(alpha_rad)
    tan_phi = np.tan(np.radians(np.clip(friction_deg, 1.0, 60.0)))

    sigma_normal = gamma_kN_m3 * z_m * cos2
    pore_pressure = m_sat * GAMMA_W * z_m * cos2
    effective_normal = sigma_normal - pore_pressure
    shear_strength = cohesion_kpa + np.maximum(effective_normal, 0.0) * tan_phi
    shear_driving = gamma_kN_m3 * z_m * sincos

    fos = np.where(
        shear_driving > 0.01,
        shear_strength / shear_driving,
        5.0  # flat ground is stable
    )
    return np.clip(fos, 0.1, 5.0).astype(np.float32)


def fos_to_susceptibility(fos):
    """
    Convert FoS to susceptibility index (0–1).
    FoS ≤ 1.0 → highly susceptible (1.0)
    FoS = 1.5 → marginal (0.5)
    FoS ≥ 2.5 → stable (0.0)
    Uses a logistic-like decay function.
    """
    # Susceptibility = 1 / (1 + exp(k*(FoS - threshold)))
    # Calibrated so FoS=1.0 → ~0.85, FoS=1.5 → ~0.5, FoS=2.5 → ~0.1
    k = 3.5
    threshold = 1.5
    s = 1.0 / (1.0 + np.exp(k * (fos - threshold)))
    return np.clip(s, 0.0, 1.0).astype(np.float32)


def compute_landslide_susceptibility_for_terrain(
        terrain_id, mask, params,
        elev, slope, twi, fa, curv_plan, curv_prof,
        aspect, roughness, lulc, soil_arr, rzsm,
        fault_prox, h, w):
    """
    Physics-based landslide susceptibility per terrain class.
    Core: Factor of Safety (infinite slope) + terrain modifiers.
    """
    ls = np.full((h, w), np.nan, dtype=np.float32)
    vm = mask & np.isfinite(slope)
    if not vm.any():
        return ls

    # ── Soil properties ────────────────────────────────────────────────────
    cohesion, friction, ks = _derive_soil_properties(soil_arr, rzsm)

    # ── Saturation proxy ───────────────────────────────────────────────────
    m_sat = _compute_saturation_proxy(twi, rzsm, ks)

    # ── FoS calculation ────────────────────────────────────────────────────
    gamma = params["gamma_soil"]  # kN/m³
    z = params["z_depth"]  # failure depth (m)

    if slope is None:
        slope_use = np.full((h, w), 5.0, dtype=np.float32)
    else:
        slope_use = np.where(np.isfinite(slope), slope, 5.0)

    # Ensure arrays have correct shape
    if cohesion.shape != (h, w):
        cohesion = np.full((h, w), 10.0, dtype=np.float32)
        friction = np.full((h, w), 28.0, dtype=np.float32)
    if m_sat.shape != (h, w):
        m_sat = np.full((h, w), 0.3, dtype=np.float32)

    fos = compute_fos(slope_use, cohesion, friction, gamma, z, m_sat)
    ls_fos = fos_to_susceptibility(fos)

    # ── Zero-out below terrain's minimum slope threshold ──────────────────
    min_slope = params["min_slope_deg"]
    ls_fos[slope_use < min_slope] = 0.0

    # ── Plan curvature component ──────────────────────────────────────────
    # Convex slope (positive plan curv) → material converges → more failure
    if curv_plan is not None:
        curv_sens = params["curv_sensitivity"]
        curv_ls = np.clip(curv_plan * curv_sens * 100, -0.3, 0.5)
        # Convex: positive curv_ls addition; concave: slight reduction
        ls_fos = np.clip(ls_fos + curv_ls, 0.0, 1.0)

    # ── Profile curvature ─────────────────────────────────────────────────
    if curv_prof is not None:
        # Convex profile (positive prof curv) = oversteepened → more failure
        prof_boost = np.clip(curv_prof * 50, -0.2, 0.3)
        ls_fos = np.clip(ls_fos + prof_boost, 0.0, 1.0)

    # ── LULC root cohesion modifier ───────────────────────────────────────
    if lulc is not None:
        root_c = _lulc_to_map(lulc, LULC_ROOT_COHESION, default=2.0)
        # Root cohesion provides additional stability
        # Normalise to 0–1 effect (0 at max root_c=8.5, 1 at root_c=0)
        root_stability = np.clip(root_c / 8.5, 0.0, 1.0)
        root_reduction = root_stability * 0.35  # up to 35% reduction
        ls_fos = np.clip(ls_fos - root_reduction, 0.0, 1.0)

    # ── Fault proximity ───────────────────────────────────────────────────
    if fault_prox is not None:
        fault_decay_km = params["fault_decay_km"]
        fault_amp = fault_prox * 0.20  # up to 20% amplification near faults
        ls_fos = np.clip(ls_fos + fault_amp, 0.0, 1.0)

    # ── Aspect modifier (moisture accumulation on specific aspects) ────────
    if aspect is not None:
        # North/NE-facing slopes often wetter in India → higher LS
        # South-facing slopes drier in northern hemisphere → lower
        aspect_rad = np.radians(aspect)
        # North-facing (180–360) in N hemisphere more shaded → wetter
        north_factor = 0.5 * (1.0 + np.cos(aspect_rad))  # max at north (180°)
        aspect_mod = north_factor * 0.10  # up to 10% from aspect
        ls_fos = np.clip(ls_fos + aspect_mod - 0.05, 0.0, 1.0)

    # ── Roughness (terrain texture) ───────────────────────────────────────
    if roughness is not None:
        rough_norm = _normalise(roughness)
        # High roughness = disturbed terrain = higher susceptibility
        rough_boost = rough_norm * 0.10
        ls_fos = np.clip(ls_fos + rough_boost, 0.0, 1.0)

    # ── TWI secondary modifier ────────────────────────────────────────────
    if twi is not None:
        twi_norm = _normalise(twi)
        twi_boost = twi_norm * params["w_twi_ls"] * 0.5
        ls_fos = np.clip(ls_fos + twi_boost, 0.0, 1.0)

    # ── Algorithm-specific adjustments ────────────────────────────────────
    algo = params["algo"]
    if algo == "debris_flow" and fa is not None:
        # Debris flows initiate at high-FA hollows on steep slopes
        fa_norm = _normalise(np.log1p(np.where(fa > 0, fa, 1)))
        steep_hollow = fa_norm * np.clip(slope_use / 35.0, 0.0, 1.0)
        ls_fos = np.clip(ls_fos + 0.15 * steep_hollow, 0.0, 1.0)

    elif algo == "lateral_spreading":
        # Floodplain liquefaction / lateral spreading — amplify in low slope
        flat_boost = np.clip(1.0 - slope_use / 5.0, 0.0, 1.0) * 0.15
        ls_fos = np.clip(ls_fos + flat_boost, 0.0, 1.0)

    elif algo == "coastal_failure":
        # Coastal cliffs — amplify based on aspect facing the sea
        if aspect is not None:
            # Seaward-facing slopes = S,SW,W in India (approx 90–270°)
            sea_factor = np.clip(np.sin(np.radians(aspect)), 0.0, 1.0)
            ls_fos = np.clip(ls_fos + 0.10 * sea_factor, 0.0, 1.0)

    # Combine FoS-based index with terrain modifier weights
    w_fos = params["fos_weight"]
    w_other = 1.0 - w_fos
    # ls_fos already integrates all modifiers; weight by FoS confidence
    ls_final = ls_fos  # already composite

    ls[vm] = ls_final[vm]
    return ls


def compute_landslide_susceptibility(features_dir: str, fault_shp=None, layers=None) -> np.ndarray:
    """Master landslide susceptibility calculator."""
    print("[LandslideMapper] Computing landslide susceptibility...")
    L = layers

    terrain = L.get("terrain_class")
    elev    = L.get("elev")
    slope   = L.get("slope")
    twi     = L.get("twi")
    fa      = L.get("fa")
    curv_p  = L.get("curv_plan")
    curv_r  = L.get("curv_prof")
    aspect  = L.get("aspect")
    rough   = L.get("roughness")
    lulc    = L.get("lulc")
    soil    = L.get("soil")
    rzsm    = L.get("rzsm")
    meta    = L.get("meta")

    h, w = elev.shape
    ls_final = np.full((h, w), np.nan, dtype=np.float32)

    # Build fault proximity
    fault_prox = None
    if fault_shp and os.path.exists(fault_shp):
        print("  Building fault proximity raster...")
        fault_prox = _build_proximity_raster(fault_shp, meta, max_dist_m=20000)

    # Process each terrain class
    present_classes = np.unique(terrain[np.isfinite(terrain) & (terrain != NODATA_INT)])
    for tc in present_classes:
        tc_int = int(tc)
        if tc_int not in TERRAIN_NAMES:
            continue
        mask = (terrain == tc_int) & np.isfinite(slope)
        if not mask.any():
            continue

        px_count = mask.sum()
        print(f"  Terrain {tc_int} ({TERRAIN_NAMES[tc_int]}): {px_count:,} px")
        params = get_landslide_terrain_params(tc_int)

        partial = compute_landslide_susceptibility_for_terrain(
            tc_int, mask, params,
            elev, slope, twi, fa, curv_p, curv_r,
            aspect, rough, lulc, soil, rzsm,
            fault_prox, h, w)

        # Terrain-specific smoothing
        sigma = params["smooth_sigma"]
        partial_smooth = _smooth(partial, sigma=sigma, valid_mask=mask)
        ls_final[mask] = partial_smooth[mask]

    # Final normalisation
    valid = np.isfinite(ls_final)
    if valid.any():
        p2 = np.nanpercentile(ls_final[valid], 2)
        p98 = np.nanpercentile(ls_final[valid], 98)
        ls_final[valid] = np.clip(
            (ls_final[valid] - p2) / (p98 - p2 + 1e-9), 0.0, 1.0)

    print(f"  Landslide susceptibility range: {np.nanmin(ls_final):.3f} – {np.nanmax(ls_final):.3f}")
    return ls_final


# ─────────────────────────────────────────────────────────────────────────────
# CLASSIFICATION
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
        fault_shp: str = None,
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
    print(" AETHER-DISASTER | Landslide Susceptibility Mapper")
    print(f" Features dir: {features_dir}")
    print("=" * 65)

    # ── Load all layers ───────────────────────────────────────────────────
    layers = _load_layers(features_dir)
    meta = layers["meta"]
    terrain = layers["terrain_class"]

    # Detect auxiliary vector files in features_dir if not provided
    for shp_name in ["fault_lines.shp", "faults.shp", "geological_faults.shp"]:
        p = os.path.join(features_dir, shp_name)
        if os.path.exists(p) and fault_shp is None:
            fault_shp = p
            print(f"  Auto-detected fault SHP: {shp_name}")

    # ── Landslide Susceptibility ──────────────────────────────────────────
    print("\n── LANDSLIDE SUSCEPTIBILITY ──────────────────────────────────────")
    ls_idx = compute_landslide_susceptibility(features_dir, fault_shp, layers)

    ls_tif = os.path.join(output_dir, "landslide_susceptibility.tif")
    _save_continuous(ls_idx, meta, ls_tif, "Landslide susceptibility index 0-1")
    print(f"  Saved: {os.path.basename(ls_tif)}")

    ls_class = susceptibility_to_class(ls_idx, terrain)
    ls_cls_tif = os.path.join(output_dir, "landslide_class.tif")
    _save_class(ls_class.astype(np.float32), meta, ls_cls_tif, CLASS_LABELS)
    print(f"  Saved: {os.path.basename(ls_cls_tif)}")

    ls_idx_shp = os.path.join(output_dir, "landslide_susceptibility.shp")
    _vectorise(ls_idx, meta, ls_idx_shp, "ls_idx", is_float=True)

    ls_cls_shp = os.path.join(output_dir, "landslide_class.shp")
    _class_to_shp(ls_class, meta, ls_cls_shp)

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

    ls_stats = _class_stats(ls_class, "landslide")

    elapsed = time.time() - t_start
    print(f"\n{'='*65}")
    print(f" COMPLETE in {elapsed:.1f}s")
    print(f"\n Landslide class distribution:")
    for cls_name, s in ls_stats.items():
        print(f"   {cls_name:<12}: {s['pct']:5.1f}%  ({s['pixel_count']:,} px)")
    print("=" * 65)

    return {
        "landslide_susceptibility_tif": ls_tif,
        "landslide_class_tif":          ls_cls_tif,
        "landslide_susceptibility_shp": ls_idx_shp,
        "landslide_class_shp":          ls_cls_shp,
        "landslide_class_stats": ls_stats,
        "elapsed_seconds": elapsed,
    }


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    features_dir = r"D:\PDEU\SEM-4\Prediction Engine\database\India\DELHI\_state_level\dem_features"
    fault_shp = r"D:\PDEU\SEM-4\Prediction Engine\Dataset\4 data\India_Fault\India_Fault.shp"
    output_dir = r"D:\PDEU\SEM-4\Prediction Engine\database\landslide\India\DELHI\_state_level\susceptibility_output"

    results = run(
        features_dir=features_dir,
        fault_shp=fault_shp,
        output_dir=output_dir,
    )
    print("\nOutput files:")
    for k, v in results.items():
        if isinstance(v, str) and os.path.exists(v):
            print(f"  {k}: {v}")
