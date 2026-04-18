"""
terrain_classifier.py — Single-Region Terrain Classifier
=========================================================
Adapted from terrain_classify_india.py for integration into the
Aether-Disaster GIS Microservice.

Input  : path to a dem_features/ directory already processed by TopoProcessor
         (expects: breached_dem.tif OR elevation.tif, slope.tif, aspect.tif,
                   plan_curv.tif, twi.tif)
Output : terrain_class.tif + terrain_class.shp written to the same directory
Returns: dict with class_stats, dominant_class, dominant_name

Terrain classes (12 physics-based):
  1  Coastal Lowland       7  High Hill
  2  Floodplain            8  Mountain
  3  Alluvial Plain        9  Plateau / Mesa
  4  Valley / River Basin  10 Escarpment / Cliff
  5  Piedmont / Foothill   11 Arid Plain
  6  Low Hill              12 Coastal Dune
"""

import os
import warnings
import numpy as np
import rasterio
import rasterio.features as rio_features
from rasterio.transform import array_bounds
from scipy.ndimage import uniform_filter
import geopandas as gpd
from shapely.geometry import shape

warnings.filterwarnings("ignore")

NODATA        = -9999.0
NODATA_INT    = -9999
COAST_DIST_KM = 50.0

CLASSES = {
    1:  "Coastal_Lowland",
    2:  "Floodplain",
    3:  "Alluvial_Plain",
    4:  "Valley_River_Basin",
    5:  "Piedmont_Foothill",
    6:  "Low_Hill",
    7:  "High_Hill",
    8:  "Mountain",
    9:  "Plateau_Mesa",
    10: "Escarpment_Cliff",
    11: "Arid_Plain",
    12: "Coastal_Dune",
}

CLASS_DESCRIPTION = {
    1:  "Near-coast, low elevation (<50m), flat slope (<2deg)",
    2:  "Low elevation (<100m), very flat (<1.5deg), high TWI (>8), high flow accumulation",
    3:  "Flat lowland (slope<3deg), moderate elevation (50-300m), interior alluvial",
    4:  "Concave curvature, high TWI (>7), channelised drainage network",
    5:  "Moderate slope (5-15deg), elevation 100-600m, transitional terrain",
    6:  "Moderate slope (8-20deg), elevation 200-900m, rounded hills",
    7:  "Steep slope (15-30deg), elevation 500-1800m",
    8:  "Very steep (>25deg) and/or elevation >1500m, major ranges",
    9:  "High elevation (>600m) but very low slope (<3deg), plateau/mesa",
    10: "Extremely steep slope (>35deg), cliffs and escarpments",
    11: "Interior flat plain, very low TWI (<3), low elevation, arid signature",
    12: "Coastal proximity, moderate slope (2-8deg), sandy dune elevation signature",
}


def _read_layer(directory: str, name: str):
    """Read a .tif layer from directory. Returns (array, meta) or (None, None)."""
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


def _make_coastal_mask(transform, shape_hw, crs_str, coast_shp_path: str = None):
    """Rasterise coastal proximity mask. Returns bool array."""
    coast = None
    
    # 1. Try fetching from PostGIS first (Preferred "Organised" Way)
    try:
        from sqlalchemy import create_engine
        from config import settings
        import geopandas as gpd
        
        # We check the database first
        engine = create_engine(settings.DATABASE_URL)
        print("[TerrainClassifier] Attempting to fetch coastline from database...")
        # Simple query to see if table exists and has data
        coast = gpd.read_postgis("SELECT geom FROM india_coastlines", engine, geom_col='geom')
        if not coast.empty:
            print(f"[TerrainClassifier] Successfully fetched {len(coast)} coastline segments from PostGIS.")
    except Exception as e:
        print(f"[TerrainClassifier] Database coastline fetch skipped/failed: {e}")

    # 2. Fallback to local SHP if DB is empty/failed
    if (coast is None or coast.empty) and coast_shp_path and os.path.exists(coast_shp_path):
        try:
            print(f"[TerrainClassifier] Falling back to local coastline SHP: {coast_shp_path}")
            coast = gpd.read_file(coast_shp_path)
        except Exception as e:
            print(f"[TerrainClassifier] Local SHP read failed: {e}")

    if coast is None or coast.empty:
        print("[TerrainClassifier] No coastline data found in DB or local path — coastal logic disabled.")
        return np.zeros(shape_hw, dtype=bool)

    try:
        # 3. Quick Bounding Box Check — Skip if region is inland
        # Get raster bounds in CRS
        from shapely.geometry import box
        bounds = array_bounds(shape_hw[0], shape_hw[1], transform)
        region_box = box(*bounds)
        
        print(f"[TerrainClassifier] Region Bounds: {bounds}")
        
        # Ensure coast matches raster CRS
        if coast.crs is None: # PostGIS might not have CRS attached in some gpd versions
             coast.set_crs("EPSG:4326", inplace=True)
        coast = coast.to_crs(crs_str)
        
        # 4. Check if region box is anywhere near ANY coastal geometry
        # Buffered region box to see if it even touches the coastline
        search_area = region_box.buffer(COAST_DIST_KM / 111.0) # approx 100km search
        
        if not coast.intersects(search_area).any():
            print("[TerrainClassifier] Interior region detected — skipping coastal mask.")
            return np.zeros(shape_hw, dtype=bool)

        print("[TerrainClassifier] Coastal proximity detected — buffering coastline ...")
        buf_deg = COAST_DIST_KM / 111.0
        # Only buffer geometries that might matter (near search area)
        potential_coast = coast[coast.intersects(search_area.buffer(0.5))] 
        if potential_coast.empty:
             return np.zeros(shape_hw, dtype=bool)
             
        coast_buf = potential_coast.copy()
        coast_buf["geometry"] = potential_coast.geometry.buffer(buf_deg)
        mask = rio_features.geometry_mask(
            [g.__geo_interface__ for g in coast_buf.geometry if g is not None],
            transform=transform,
            invert=True,
            out_shape=shape_hw,
        )
        return mask
    except Exception as e:
        print(f"[WARN] Coastal mask logic failed: {e}")
        import traceback
        traceback.print_exc()
        return np.zeros(shape_hw, dtype=bool)


def _classify_pixels(elev, slope, curv, fa, twi, coastal_mask):
    """Memory-efficient classification using in-place boolean masking."""
    h, w = elev.shape
    cls = np.full((h, w), NODATA_INT, dtype=np.int16)
    
    # Identify valid pixels once to save repetitive checks
    valid = np.isfinite(elev)
    if not valid.any(): return cls

    # Pre-calculate common threshold masks to avoid full-grid copies
    fa_p90 = np.nanpercentile(fa[valid], 90) if valid.any() else 0
    
    # Use boolean masks directly instead of np.where copies
    # 3: Alluvial Plain (Initial state)
    cls[valid] = 3
    
    # Higher priorities overwrite lower ones
    # 11: Arid Plain
    m = valid & (elev < 400) & (slope < 2) & (twi < 4) & ~coastal_mask
    cls[m] = 11

    # 2: Floodplain
    m = valid & (elev < 100) & (slope < 1.5) & (twi > 8) & (fa > fa_p90 * 0.5)
    cls[m] = 2

    # 4: Valley / River Basin
    m = valid & (curv < -0.005) & (twi > 7)
    cls[m] = 4

    # 5: Piedmont / Foothill
    m = valid & (elev >= 100) & (elev < 600) & (slope >= 5) & (slope < 15)
    cls[m] = 5

    # 6: Low Hill
    m = valid & (elev >= 200) & (elev < 900) & (slope >= 8) & (slope < 20)
    cls[m] = 6

    # 9: Plateau / Mesa
    m = valid & (elev >= 600) & (slope < 3)
    cls[m] = 9

    # 7: High Hill
    m = valid & (elev >= 500) & (elev < 1800) & (slope >= 15) & (slope < 30)
    cls[m] = 7

    # 8: Mountain
    m = valid & ((elev >= 1500) | (slope >= 25))
    cls[m] = 8

    # 10: Escarpment / Cliff
    m = valid & (slope >= 35)
    cls[m] = 10

    # 12: Coastal Dune
    m = valid & coastal_mask & (slope >= 2) & (slope < 8) & (elev >= 5) & (elev < 80)
    cls[m] = 12

    # 1: Coastal Lowland
    m = valid & coastal_mask & (elev < 50) & (slope < 2)
    cls[m] = 1

    return cls


def _vectorise(cls_arr, meta, output_dir):
    """Optimized vectorisation: Sieve -> Downsample -> Fast Vectorise."""
    import time
    from rasterio.features import sieve
    from rasterio.transform import Affine

    shp_path = os.path.join(output_dir, "terrain_class.shp")
    t0 = time.time()

    # Step 1: Sieve — remove salt-and-pepper noise (polygons < 10px)
    print(f"[TerrainClassifier] [SHP 1/4] Sieve filter (removes micro-polygons < 10px)...")
    sieved = sieve(cls_arr.astype(np.int16), size=10)
    print(f"[TerrainClassifier]          Done ({time.time()-t0:.1f}s)")

    # Step 2: Downsample to 1/4 resolution — SHP is a display proxy only
    ds_factor = 4
    h, w = cls_arr.shape
    new_h, new_w = h // ds_factor, w // ds_factor
    if new_h > 0 and new_w > 0:
        print(f"[TerrainClassifier] [SHP 2/4] Downsampling {h}x{w} → {new_h}x{new_w} (1/4 proxy)...")
        ds_arr = sieved[::ds_factor, ::ds_factor]
        new_transform = meta["transform"] * Affine.scale(ds_factor, ds_factor)
    else:
        ds_arr = sieved
        new_transform = meta["transform"]
    print(f"[TerrainClassifier]          Done ({time.time()-t0:.1f}s)")

    # Step 3: Vectorize — shapes() on downsampled grid
    crs_str = str(meta["crs"])
    valid_mask = ds_arr != NODATA_INT
    print(f"[TerrainClassifier] [SHP 3/4] Vectorizing {new_h}x{new_w} grid...")
    shapes_gen = rio_features.shapes(
        ds_arr.astype(np.int32),
        mask=valid_mask.astype(np.uint8),
        transform=new_transform,
    )
    records = []
    for geom_dict, class_id in shapes_gen:
        cid = int(class_id)
        if cid == NODATA_INT:
            continue
        records.append({
            "geometry":    shape(geom_dict),
            "class_id":    cid,
            "class_name":  CLASSES.get(cid, "Unknown"),
            "description": CLASS_DESCRIPTION.get(cid, ""),
        })
    print(f"[TerrainClassifier]          {len(records)} polygons collected ({time.time()-t0:.1f}s)")

    if not records:
        print("[TerrainClassifier] WARNING: No valid polygons — SHP skipped.")
        return shp_path

    # Step 4: Save SHP (NO dissolve — dissolve hangs on 100M+ pixel grids)
    print(f"[TerrainClassifier] [SHP 4/4] Writing shapefile → {os.path.basename(shp_path)}...")
    gdf = gpd.GeoDataFrame(records, crs=crs_str)
    gdf.to_file(shp_path)
    print(f"[TerrainClassifier] ✅ SHP saved ({time.time()-t0:.1f}s total)")
    return shp_path


def _majority_filter(cls_arr, size=3):
    result = cls_arr.copy()
    best_count = np.zeros(cls_arr.shape, dtype=np.float32)
    for cid in CLASSES.keys():
        binary   = (cls_arr == cid).astype(np.float32)
        smoothed = uniform_filter(binary, size=size)
        update   = smoothed > best_count
        result[update]    = cid
        best_count[update] = smoothed[update]
    result[cls_arr == NODATA_INT] = NODATA_INT
    return result


def _save_tif(cls_arr, meta, path):
    m = meta.copy()
    m.update(dtype="int16", nodata=NODATA_INT, count=1,
             compress="lzw", tiled=True, blockxsize=256, blockysize=256)
    with rasterio.open(path, "w", **m) as dst:
        dst.write(cls_arr.astype(np.int16), 1)
        dst.update_tags(1, **{f"CLASS_{k}": v for k, v in CLASSES.items()})
        dst.update_tags(1, **{f"DESC_{k}": v for k, v in CLASS_DESCRIPTION.items()})
        dst.update_tags(NODATA_VALUE=str(NODATA_INT),
                        CLASSIFICATION="Rule-based terrain classification",
                        SOURCE_LAYERS="elevation,slope,curvature,flow_accumulation,twi")


# NOTE: The slow duplicate _vectorise (with dissolve) was removed.
# The optimized sieve+downsample version above is the only implementation.


def classify(features_dir: str, coast_shp_path: str = None) -> dict:
    """
    Main entry point.

    Parameters
    ----------
    features_dir  : str — path to dem_features/ directory
    coast_shp_path: str — optional path to Coastline_All.shp

    Returns
    -------
    dict with keys: tif_path, shp_path, class_stats, dominant_class, dominant_name
    """
    print(f"[TerrainClassifier] Input dir: {features_dir}")

    # Layer name aliases: TopoProcessor writes breached_dem.tif for elevation
    elev, meta = _read_layer(features_dir, "breached_dem")
    if elev is None:
        elev, meta = _read_layer(features_dir, "elevation")
    if elev is None:
        raise FileNotFoundError(f"No elevation raster found in {features_dir}")

    slope, _   = _read_layer(features_dir, "slope")
    curv,  _   = _read_layer(features_dir, "plan_curv")
    twi,   _   = _read_layer(features_dir, "twi")
    fa,    _   = _read_layer(features_dir, "d8_flow_acc")

    # Provide fallbacks for missing layers
    h, w = elev.shape
    if slope is None:
        print("[WARN] slope.tif missing — using zeros")
        slope = np.zeros((h, w), dtype=np.float32)
    if curv is None:
        print("[WARN] plan_curv.tif missing — using zeros")
        curv = np.zeros((h, w), dtype=np.float32)
    if twi is None:
        print("[WARN] twi.tif missing — using zeros")
        twi = np.zeros((h, w), dtype=np.float32)
    if fa is None:
        print("[WARN] d8_flow_acc.tif missing — using zeros")
        fa = np.zeros((h, w), dtype=np.float32)

    crs_str = str(meta["crs"])
    transform = meta["transform"]

    print(f"[TerrainClassifier] Grid: {h}×{w}  CRS: {crs_str}")
    print("[TerrainClassifier] Building coastal mask ...")
    coastal_mask = _make_coastal_mask(transform, (h, w), crs_str, coast_shp_path)

    print("[TerrainClassifier] Classifying pixels ...")
    cls = _classify_pixels(elev, slope, curv, fa, twi, coastal_mask)

    print("[TerrainClassifier] Applying majority filter ...")
    cls = _majority_filter(cls, size=3)

    # Save outputs
    tif_path = os.path.join(features_dir, "terrain_class.tif")
    _save_tif(cls, meta, tif_path)
    print(f"[TerrainClassifier] TIF saved → {tif_path}")

    shp_path = _vectorise(cls, meta, features_dir)
    print(f"[TerrainClassifier] SHP saved → {shp_path}")

    # Compute class stats
    class_stats = {}
    valid_pixels = cls[cls != NODATA_INT]
    total = len(valid_pixels)
    unique, counts = np.unique(valid_pixels, return_counts=True)
    dominant_class = int(unique[np.argmax(counts)]) if len(unique) > 0 else 3

    for cid, cnt in zip(unique.tolist(), counts.tolist()):
        class_stats[str(cid)] = {
            "name":        CLASSES.get(int(cid), "Unknown"),
            "description": CLASS_DESCRIPTION.get(int(cid), ""),
            "pixel_count": int(cnt),
            "pct":         round(cnt / total * 100, 2) if total > 0 else 0,
        }

    print(f"[TerrainClassifier] Done. Dominant class: {dominant_class} ({CLASSES.get(dominant_class,'?')})")

    return {
        "tif_path":      tif_path,
        "shp_path":      shp_path,
        "class_stats":   class_stats,
        "dominant_class": dominant_class,
        "dominant_name":  CLASSES.get(dominant_class, "Unknown"),
    }
