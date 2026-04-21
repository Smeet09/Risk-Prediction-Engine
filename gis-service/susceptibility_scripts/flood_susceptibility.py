import os
import warnings
import numpy as np
import rasterio
import rasterio.features as rio_features
from rasterio.features import sieve
from rasterio.transform import Affine
from rasterio.warp import reproject, Resampling
from scipy.ndimage import uniform_filter, gaussian_filter, distance_transform_edt
import geopandas as gpd
from shapely.geometry import shape
import time

warnings.filterwarnings("ignore")

NODATA     = -9999.0
NODATA_INT = -9999

TERRAIN_NAMES = {
    1: "Coastal_Lowland",    2: "Floodplain",        3: "Alluvial_Plain",
    4: "Valley_River_Basin", 5: "Piedmont_Foothill",  6: "Low_Hill",
    7: "High_Hill",          8: "Mountain",           9: "Plateau_Mesa",
    10: "Escarpment_Cliff",  11: "Arid_Plain",        12: "Coastal_Dune",
}

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

SOIL_COHESION_KPA = {1: 25.0, 2: 18.0, 3: 12.0, 4: 8.0, 5: 5.0, 6: 15.0, 0: 10.0}
SOIL_FRICTION_DEG = {1: 15.0, 2: 22.0, 3: 30.0, 4: 35.0, 5: 38.0, 6: 25.0, 0: 28.0}
SOIL_HYDRAULIC_CONDUCTIVITY = {1: 0.1, 2: 0.5, 3: 2.0, 4: 10.0, 5: 20.0, 6: 0.8, 0: 1.5}

FLOOD_TERRAIN_PARAMS = {
    1:  {"w_fa": 0.20, "w_twi": 0.25, "w_elev": 0.30, "w_slope": 0.05, "w_river": 0.12,
         "w_drain": 0.03, "w_lulc": 0.05, "w_soil": 0.05, "algo": "coastal_inundation",
         "elev_thresh": 50,   "slope_cap": 3,  "twi_boost": 1.2, "fa_transform": "sqrt", "smooth_sigma": 4.0},
    2:  {"w_fa": 0.35, "w_twi": 0.30, "w_elev": 0.20, "w_slope": 0.05, "w_river": 0.08,
         "w_drain": 0.02, "w_lulc": 0.05, "w_soil": 0.08, "algo": "inundation",
         "elev_thresh": 100,  "slope_cap": 5,  "twi_boost": 1.5, "fa_transform": "log",  "smooth_sigma": 3.0},
    3:  {"w_fa": 0.30, "w_twi": 0.25, "w_elev": 0.15, "w_slope": 0.08, "w_river": 0.10,
         "w_drain": 0.05, "w_lulc": 0.07, "w_soil": 0.10, "algo": "inundation",
         "elev_thresh": 300,  "slope_cap": 8,  "twi_boost": 1.2, "fa_transform": "log",  "smooth_sigma": 2.5},
    4:  {"w_fa": 0.32, "w_twi": 0.28, "w_elev": 0.12, "w_slope": 0.08, "w_river": 0.12,
         "w_drain": 0.05, "w_lulc": 0.03, "w_soil": 0.05, "algo": "channel_flood",
         "elev_thresh": 800,  "slope_cap": 15, "twi_boost": 1.3, "fa_transform": "log",  "smooth_sigma": 2.0},
    5:  {"w_fa": 0.28, "w_twi": 0.20, "w_elev": 0.10, "w_slope": 0.18, "w_river": 0.10,
         "w_drain": 0.07, "w_lulc": 0.07, "w_soil": 0.05, "algo": "flash_flood",
         "elev_thresh": 600,  "slope_cap": 20, "twi_boost": 1.0, "fa_transform": "log",  "smooth_sigma": 1.5},
    6:  {"w_fa": 0.22, "w_twi": 0.18, "w_elev": 0.08, "w_slope": 0.22, "w_river": 0.08,
         "w_drain": 0.10, "w_lulc": 0.07, "w_soil": 0.05, "algo": "flash_flood",
         "elev_thresh": 900,  "slope_cap": 25, "twi_boost": 0.9, "fa_transform": "log",  "smooth_sigma": 1.5},
    7:  {"w_fa": 0.18, "w_twi": 0.15, "w_elev": 0.05, "w_slope": 0.25, "w_river": 0.10,
         "w_drain": 0.15, "w_lulc": 0.07, "w_soil": 0.05, "algo": "flash_flood",
         "elev_thresh": 1800, "slope_cap": 35, "twi_boost": 0.8, "fa_transform": "sqrt", "smooth_sigma": 1.0},
    8:  {"w_fa": 0.20, "w_twi": 0.12, "w_elev": 0.05, "w_slope": 0.28, "w_river": 0.12,
         "w_drain": 0.15, "w_lulc": 0.05, "w_soil": 0.03, "algo": "mountain_flood",
         "elev_thresh": 3000, "slope_cap": 60, "twi_boost": 0.7, "fa_transform": "sqrt", "smooth_sigma": 1.0},
    9:  {"w_fa": 0.25, "w_twi": 0.28, "w_elev": 0.10, "w_slope": 0.08, "w_river": 0.08,
         "w_drain": 0.12, "w_lulc": 0.05, "w_soil": 0.04, "algo": "plateau_pond",
         "elev_thresh": 1500, "slope_cap": 5,  "twi_boost": 1.1, "fa_transform": "log",  "smooth_sigma": 2.0},
    10: {"w_fa": 0.22, "w_twi": 0.15, "w_elev": 0.10, "w_slope": 0.20, "w_river": 0.15,
         "w_drain": 0.12, "w_lulc": 0.03, "w_soil": 0.03, "algo": "flash_flood",
         "elev_thresh": 2000, "slope_cap": 70, "twi_boost": 0.9, "fa_transform": "sqrt", "smooth_sigma": 1.5},
    11: {"w_fa": 0.30, "w_twi": 0.20, "w_elev": 0.12, "w_slope": 0.10, "w_river": 0.12,
         "w_drain": 0.08, "w_lulc": 0.05, "w_soil": 0.03, "algo": "sheet_flood",
         "elev_thresh": 400,  "slope_cap": 10, "twi_boost": 0.8, "fa_transform": "log",  "smooth_sigma": 2.5},
    12: {"w_fa": 0.15, "w_twi": 0.20, "w_elev": 0.35, "w_slope": 0.10, "w_river": 0.08,
         "w_drain": 0.02, "w_lulc": 0.05, "w_soil": 0.05, "algo": "coastal_inundation",
         "elev_thresh": 30,   "slope_cap": 10, "twi_boost": 1.0, "fa_transform": "linear","smooth_sigma": 3.5},
}


def _read(directory, name):
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


def _save_continuous(arr, meta, path, description="susceptibility"):
    m = meta.copy()
    m.update(dtype="float32", nodata=NODATA, count=1,
             compress="lzw", tiled=True, blockxsize=256, blockysize=256)
    with rasterio.open(path, "w", **m) as dst:
        out = arr.copy()
        out[~np.isfinite(out)] = NODATA
        dst.write(out.astype(np.float32), 1)
        dst.update_tags(1, DESCRIPTION=description)


def _save_class(cls_arr, meta, path, class_labels):
    m = meta.copy()
    m.update(dtype="int16", nodata=NODATA_INT, count=1,
             compress="lzw", tiled=True, blockxsize=256, blockysize=256)
    with rasterio.open(path, "w", **m) as dst:
        out = cls_arr.copy().astype(np.float32)
        out[~np.isfinite(out)] = NODATA_INT
        dst.write(out.astype(np.int16), 1)
        for k, v in class_labels.items():
            dst.update_tags(1, **{f"CLASS_{k}": v})


def _vectorise(arr, meta, output_path, value_field="value", is_float=False):
    h, w = arr.shape
    if is_float:
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
        ds_factor, ds_arr, new_transform = 1, sieved, meta["transform"]
    else:
        ds_arr = sieved[::ds_factor, ::ds_factor]
        new_transform = meta["transform"] * Affine.scale(ds_factor, ds_factor)
    valid_mask = (ds_arr != NODATA_INT).astype(np.uint8)
    records = []
    for geom_dict, val in rio_features.shapes(ds_arr.astype(np.int32), mask=valid_mask, transform=new_transform):
        v = int(val)
        if v != NODATA_INT:
            records.append({"geometry": shape(geom_dict), value_field: v / 100.0 if is_float else v})
    if records:
        gpd.GeoDataFrame(records, crs=str(meta["crs"])).to_file(output_path)
    return output_path


def _class_to_shp(cls_arr, meta, output_path):
    CLASS_LABELS = {1: "Very_Low", 2: "Low", 3: "Moderate", 4: "High", 5: "Very_High"}
    sieved = sieve(cls_arr.astype(np.int16), size=8)
    h, w = cls_arr.shape
    ds = max(1, min(h, w) // 800)
    ds_arr = sieved[::ds, ::ds]
    new_transform = meta["transform"] * Affine.scale(ds, ds)
    valid_mask = ((ds_arr >= 1) & (ds_arr <= 5)).astype(np.uint8)
    records = []
    for geom_dict, cid in rio_features.shapes(ds_arr.astype(np.int32), mask=valid_mask, transform=new_transform):
        c = int(cid)
        if 1 <= c <= 5:
            records.append({"geometry": shape(geom_dict), "class_id": c, "class_name": CLASS_LABELS[c]})
    if records:
        gpd.GeoDataFrame(records, crs=str(meta["crs"])).to_file(output_path)


def _normalise(arr, percentile_clip=(1, 99)):
    out = arr.copy().astype(np.float32)
    valid = np.isfinite(out)
    if not valid.any():
        return out
    vmin = np.nanpercentile(out[valid], percentile_clip[0])
    vmax = np.nanpercentile(out[valid], percentile_clip[1])
    if vmax <= vmin:
        out[valid] = 0.0
        return out
    out[valid] = np.clip((out[valid] - vmin) / (vmax - vmin), 0.0, 1.0)
    return out


def _smooth(arr, sigma, valid_mask=None):
    if sigma <= 0:
        return arr
    filled = arr.copy()
    if valid_mask is None:
        valid_mask = np.isfinite(arr)
    filled[~valid_mask] = np.nanmedian(arr[valid_mask]) if valid_mask.any() else 0.0
    smoothed = gaussian_filter(filled, sigma=sigma)
    result = arr.copy()
    result[valid_mask] = smoothed[valid_mask]
    return result


def _build_proximity_raster(shp_path, meta, max_dist_m=50000):
    if not shp_path or not os.path.exists(shp_path):
        return None
    h, w = meta["height"], meta["width"]
    try:
        gdf = gpd.read_file(shp_path)
        if gdf.empty:
            return None
        burn_mask = np.zeros((h, w), dtype=np.uint8)
        for geom in gdf.geometry:
            if geom is None:
                continue
            try:
                rasterised = rio_features.rasterize(
                    [(geom.__geo_interface__, 1)], out_shape=(h, w),
                    transform=meta["transform"], fill=0, dtype=np.uint8)
                burn_mask = np.maximum(burn_mask, rasterised)
            except Exception:
                pass
        if burn_mask.sum() == 0:
            return None
        pixel_size = abs(meta["transform"].a)
        dist_m = distance_transform_edt(1 - burn_mask) * pixel_size * 111000
        return np.clip(1.0 - dist_m / max_dist_m, 0.0, 1.0).astype(np.float32)
    except Exception:
        return None


def _flow_acc_transform(fa, method="log"):
    fa_safe = np.where(fa > 0, fa, 1.0)
    if method == "log":
        return np.log1p(fa_safe)
    elif method == "sqrt":
        return np.sqrt(fa_safe)
    return fa_safe.copy()


def _lulc_to_map(lulc, lookup, default=0.5):
    out = np.full_like(lulc, default, dtype=np.float32)
    for cls_val, factor in lookup.items():
        out[lulc == cls_val] = factor
    return out


def _derive_soil_ks(soil_arr):
    h, w = soil_arr.shape
    ks = np.full((h, w), 1.5, dtype=np.float32)
    for cls, val in SOIL_HYDRAULIC_CONDUCTIVITY.items():
        ks[soil_arr == cls] = val
    return ks


def _resample_to_dem(arr, arr_meta, dem_meta):
    if arr is None:
        return None
    if arr.shape == (dem_meta["height"], dem_meta["width"]):
        return arr
    dest = np.full((dem_meta["height"], dem_meta["width"]), np.nan, dtype=np.float32)
    try:
        reproject(source=arr, destination=dest,
                  src_transform=arr_meta["transform"],
                  src_crs=arr_meta.get("crs", dem_meta["crs"]),
                  dst_transform=dem_meta["transform"],
                  dst_crs=dem_meta["crs"],
                  src_nodata=np.nan, dst_nodata=np.nan,
                  resampling=Resampling.bilinear)
    except Exception:
        return arr
    return dest


def _load_layers(features_dir):
    L = {}

    def _try(name, alt=None):
        arr, meta = _read(features_dir, name)
        if arr is None and alt:
            arr, meta = _read(features_dir, alt)
        return arr, meta

    elev, meta = _try("breached_dem", "elevation")
    if elev is None:
        raise FileNotFoundError(f"No elevation raster found in {features_dir}")
    L["elev"]  = elev
    L["meta"]  = meta
    L["meta"]["height"] = elev.shape[0]
    L["meta"]["width"]  = elev.shape[1]

    LAYER_MAP = {
        "slope": "slope", "aspect": "aspect", "roughness": "roughness",
        "curv_plan": "plan_curv", "curv_prof": "profile_curv",
        "twi": "twi", "fa": "d8_flow_acc", "fa_dinf": "dinf_flow_acc",
        "fa_mfd": "mfd_flow_acc", "lulc": "lulc", "soil": "soil_class",
        "terrain_class": "terrain_class", "river_raster": "river_network",
    }
    for key, fname in LAYER_MAP.items():
        arr, ameta = _read(features_dir, fname)
        L[key] = _resample_to_dem(arr, ameta, L["meta"]) if (arr is not None and ameta is not None) else arr

    if L.get("twi") is None:
        L["twi"], ameta = _try("twi_d8")
        if L["twi"] is not None and ameta is not None:
            L["twi"] = _resample_to_dem(L["twi"], ameta, L["meta"])

    if L.get("fa") is None:
        L["fa"], ameta = _try("flow_acc")
        if L["fa"] is not None and ameta is not None:
            L["fa"] = _resample_to_dem(L["fa"], ameta, L["meta"])

    rzsm_path = os.path.join(features_dir, "RZSM_2024_Soil.tif")
    L["rzsm"] = None
    if os.path.exists(rzsm_path):
        try:
            with rasterio.open(rzsm_path) as src:
                n = min(src.count, 50)
                bands = []
                for b in range(1, n + 1):
                    a = src.read(b).astype(np.float32)
                    nd = src.nodata
                    if nd is not None:
                        a[a == nd] = np.nan
                    a[~np.isfinite(a)] = np.nan
                    bands.append(a)
                rzsm_mean = np.nanmean(np.stack(bands), axis=0)
                src_meta = src.meta.copy()
            L["rzsm"] = _resample_to_dem(rzsm_mean, src_meta, L["meta"])
        except Exception:
            pass

    h, w = elev.shape
    if L.get("slope") is None:
        L["slope"] = np.zeros((h, w), dtype=np.float32)
    if L.get("twi") is None:
        L["twi"] = np.zeros((h, w), dtype=np.float32)
    if L.get("fa") is None:
        L["fa"] = np.zeros((h, w), dtype=np.float32)
    if L.get("terrain_class") is None:
        L["terrain_class"] = np.full((h, w), 3.0, dtype=np.float32)

    if L.get("fa_mfd") is not None:
        L["fa"] = L["fa_mfd"]
    elif L.get("fa_dinf") is not None:
        L["fa"] = L["fa_dinf"]

    return L


def _compute_flood_for_terrain(terrain_id, mask, params, elev, slope, twi, fa,
                                curv_plan, curv_prof, river_prox, lulc, soil_arr,
                                rzsm, drain_density, h, w):
    flood = np.full((h, w), np.nan, dtype=np.float32)
    vm = mask & np.isfinite(elev)
    if not vm.any():
        return flood

    fa_norm   = _normalise(_flow_acc_transform(fa, params["fa_transform"])) if fa is not None \
                else np.full((h, w), 0.3, dtype=np.float32)
    twi_norm  = _normalise(twi * params["twi_boost"]) if twi is not None \
                else np.full((h, w), 0.3, dtype=np.float32)
    elev_norm = np.where(np.isfinite(elev),
                         np.clip(1.0 - elev / params["elev_thresh"], 0.0, 1.0), np.nan).astype(np.float32)
    slope_norm = np.where(np.isfinite(slope),
                          np.clip(1.0 - slope / params["slope_cap"], 0.0, 1.0), np.nan).astype(np.float32)
    river_c   = river_prox   if river_prox   is not None else np.full((h, w), 0.2, dtype=np.float32)
    drain_c   = drain_density if drain_density is not None else np.full((h, w), 0.3, dtype=np.float32)

    if lulc is not None:
        infilt = _lulc_to_map(lulc, LULC_INFILTRATION, default=0.4)
        rough  = _lulc_to_map(lulc, LULC_ROUGHNESS, default=0.2)
        lulc_flood = (1.0 - infilt) * 0.6 + (1.0 - rough) * 0.4
    else:
        lulc_flood = np.full((h, w), 0.3, dtype=np.float32)

    if rzsm is not None:
        soil_c = np.clip(rzsm, 0.0, 1.0)
    elif soil_arr is not None:
        soil_c = 1.0 - np.clip(_derive_soil_ks(soil_arr) / 20.0, 0.0, 1.0)
    else:
        soil_c = np.full((h, w), 0.3, dtype=np.float32)

    algo = params["algo"]
    if algo == "coastal_inundation":
        fa_norm = np.clip(fa_norm * np.where(elev < 10, 1.3, 1.0), 0.0, 1.0)
    elif algo == "mountain_flood" and fa is not None and slope is not None:
        vel_norm = _normalise(np.log1p(np.maximum(fa, 1)) * (slope / 30.0))
        fa_norm = 0.5 * fa_norm + 0.5 * vel_norm
    elif algo == "plateau_pond" and curv_plan is not None:
        pond_factor = np.where(curv_plan < 0, np.clip(np.abs(curv_plan) * 100, 0, 1), 0.0)
        twi_norm = np.clip(twi_norm + 0.3 * pond_factor, 0.0, 1.0)
    elif algo == "sheet_flood" and slope is not None:
        fa_norm = np.clip(fa_norm * (0.7 + 0.3 * np.clip(1.0 - slope / 5.0, 0.0, 1.0)), 0.0, 1.0)

    w_sum = (params["w_fa"] + params["w_twi"] + params["w_elev"] + params["w_slope"]
             + params["w_river"] + params["w_drain"] + params["w_lulc"] + params["w_soil"])

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

    if curv_plan is not None:
        curv_boost = np.where(curv_plan < -0.002, np.clip(np.abs(curv_plan) * 50, 0.0, 0.2), 0.0)
        flood_val = np.clip(flood_val + curv_boost, 0.0, 1.0)

    flood[vm] = flood_val[vm]
    return flood


def _susceptibility_to_class(arr, terrain_arr=None):
    cls_arr = np.full_like(arr, NODATA_INT, dtype=np.int16)
    valid = np.isfinite(arr)
    if terrain_arr is not None:
        result = np.full_like(arr, NODATA_INT, dtype=np.int16)
        for tc in np.unique(terrain_arr[valid & np.isfinite(terrain_arr)]):
            tc_int = int(tc)
            if tc_int not in TERRAIN_NAMES:
                continue
            tmask = valid & (terrain_arr == tc_int)
            if not tmask.any():
                continue
            vals = arr[tmask]
            c = np.ones(vals.shape, dtype=np.int16)
            for i, p in enumerate([20, 40, 60, 80], start=2):
                c[vals >= np.nanpercentile(vals, p - 20)] = i
            result[tmask] = c
        return result
    cls_arr[valid] = 1
    cls_arr[valid & (arr >= 0.15)] = 2
    cls_arr[valid & (arr >= 0.35)] = 3
    cls_arr[valid & (arr >= 0.55)] = 4
    cls_arr[valid & (arr >= 0.75)] = 5
    return cls_arr


def compute_flood_susceptibility(features_dir, river_shp=None, output_dir=None):
    t_start = time.time()
    if output_dir is None:
        output_dir = features_dir
    os.makedirs(output_dir, exist_ok=True)

    CLASS_LABELS = {1: "Very_Low", 2: "Low", 3: "Moderate", 4: "High", 5: "Very_High"}

    layers  = _load_layers(features_dir)
    meta    = layers["meta"]
    terrain = layers["terrain_class"]
    elev    = layers["elev"]
    slope   = layers["slope"]
    twi     = layers["twi"]
    fa      = layers["fa"]
    curv_p  = layers.get("curv_plan")
    curv_r  = layers.get("curv_prof")
    lulc    = layers.get("lulc")
    soil    = layers.get("soil")
    rzsm    = layers.get("rzsm")

    h, w = elev.shape
    flood_final = np.full((h, w), np.nan, dtype=np.float32)

    for shp_name in ["river_network.shp", "stream_network.shp", "rivers.shp"]:
        p = os.path.join(features_dir, shp_name)
        if os.path.exists(p) and river_shp is None:
            river_shp = p

    river_prox = None
    if river_shp and os.path.exists(river_shp):
        river_prox = _build_proximity_raster(river_shp, meta, max_dist_m=30000)
    elif layers.get("river_raster") is not None:
        river_prox = _normalise(layers["river_raster"])

    drain_density = None
    if fa is not None:
        fa_log = np.log1p(np.where(np.isfinite(fa) & (fa > 0), fa, 1))
        fa_mean    = uniform_filter(np.where(np.isfinite(fa_log), fa_log, 0.0), size=15)
        fa_sq_mean = uniform_filter(np.where(np.isfinite(fa_log), fa_log**2, 0.0), size=15)
        drain_density = _normalise(np.sqrt(np.maximum(fa_sq_mean - fa_mean**2, 0)))

    for tc in np.unique(terrain[np.isfinite(terrain) & (terrain != NODATA_INT)]):
        tc_int = int(tc)
        if tc_int not in TERRAIN_NAMES:
            continue
        mask = (terrain == tc_int) & np.isfinite(elev)
        if not mask.any():
            continue
        params  = FLOOD_TERRAIN_PARAMS.get(tc_int, FLOOD_TERRAIN_PARAMS[3])
        partial = _compute_flood_for_terrain(
            tc_int, mask, params, elev, slope, twi, fa, curv_p, curv_r,
            river_prox, lulc, soil, rzsm, drain_density, h, w)
        flood_final[mask] = _smooth(partial, sigma=params["smooth_sigma"], valid_mask=mask)[mask]

    valid = np.isfinite(flood_final)
    if valid.any():
        p2, p98 = np.nanpercentile(flood_final[valid], 2), np.nanpercentile(flood_final[valid], 98)
        flood_final[valid] = np.clip((flood_final[valid] - p2) / (p98 - p2 + 1e-9), 0.0, 1.0)

    flood_tif     = os.path.join(output_dir, "flood_susceptibility.tif")
    flood_cls_tif = os.path.join(output_dir, "flood_class.tif")
    flood_shp     = os.path.join(output_dir, "flood_susceptibility.shp")
    flood_cls_shp = os.path.join(output_dir, "flood_class.shp")

    _save_continuous(flood_final, meta, flood_tif, "Flood susceptibility index 0-1")
    flood_class = _susceptibility_to_class(flood_final, terrain)
    _save_class(flood_class.astype(np.float32), meta, flood_cls_tif, CLASS_LABELS)
    _vectorise(flood_final, meta, flood_shp, "flood_idx", is_float=True)
    _class_to_shp(flood_class, meta, flood_cls_shp)

    valid_cls = flood_class[(flood_class >= 1) & (flood_class <= 5)]
    total     = len(valid_cls)
    class_stats = {
        CLASS_LABELS[c]: {
            "pixel_count": int((valid_cls == c).sum()),
            "pct": round(int((valid_cls == c).sum()) / total * 100, 2) if total > 0 else 0,
        }
        for c in range(1, 6)
    }

    return {
        "tif_path":        flood_tif,
        "class_tif_path":  flood_cls_tif,
        "shp_path":        flood_shp,
        "class_shp_path":  flood_cls_shp,
        "class_stats":     class_stats,
        "elapsed_seconds": round(time.time() - t_start, 1),
    }


def run(features_dir, river_shp=None, output_dir=None):
    return compute_flood_susceptibility(features_dir, river_shp=river_shp, output_dir=output_dir)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Flood susceptibility mapper")
    parser.add_argument("features_dir")
    parser.add_argument("--river-shp", default=None)
    parser.add_argument("--output-dir", default=None)
    args = parser.parse_args()
    result = run(args.features_dir, river_shp=args.river_shp, output_dir=args.output_dir)
    for k, v in result.items():
        if isinstance(v, str):
            print(f"  {k}: {v}")
