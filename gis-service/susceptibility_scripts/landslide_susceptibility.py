import os
import warnings
import numpy as np
import rasterio
import rasterio.features as rio_features
from rasterio.features import sieve
from rasterio.transform import Affine
from rasterio.warp import reproject, Resampling
from scipy.ndimage import gaussian_filter, distance_transform_edt
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

LULC_ROOT_COHESION = {
    10: 8.5, 20: 4.0, 30: 2.5, 40: 1.5, 50: 1.0,
    60: 6.0, 70: 1.2, 80: 0.5, 90: 0.0, 100: 0.0,
    110: 0.0, 254: 1.5,
}

SOIL_COHESION_KPA = {1: 25.0, 2: 18.0, 3: 12.0, 4: 8.0, 5: 5.0, 6: 15.0, 0: 10.0}
SOIL_FRICTION_DEG = {1: 15.0, 2: 22.0, 3: 30.0, 4: 35.0, 5: 38.0, 6: 25.0, 0: 28.0}
SOIL_HYDRAULIC_CONDUCTIVITY = {1: 0.1, 2: 0.5, 3: 2.0, 4: 10.0, 5: 20.0, 6: 0.8, 0: 1.5}

LANDSLIDE_TERRAIN_PARAMS = {
    1:  {"fos_weight": 0.20, "w_curv": 0.12, "w_fault": 0.05, "w_aspect": 0.10,
         "w_roughness": 0.08, "w_lulc": 0.15, "w_twi_ls": 0.30,
         "algo": "coastal_failure",  "gamma_soil": 18.0, "z_depth": 1.5,
         "min_slope_deg": 2,  "curv_sensitivity": 0.8, "fault_decay_km": 7.0, "smooth_sigma": 3.0},
    2:  {"fos_weight": 0.10, "w_curv": 0.15, "w_fault": 0.05, "w_aspect": 0.05,
         "w_roughness": 0.05, "w_lulc": 0.10, "w_twi_ls": 0.50,
         "algo": "lateral_spreading","gamma_soil": 17.5, "z_depth": 1.5,
         "min_slope_deg": 1,  "curv_sensitivity": 0.6, "fault_decay_km": 8.0, "smooth_sigma": 3.5},
    3:  {"fos_weight": 0.15, "w_curv": 0.20, "w_fault": 0.05, "w_aspect": 0.05,
         "w_roughness": 0.05, "w_lulc": 0.10, "w_twi_ls": 0.40,
         "algo": "creep",            "gamma_soil": 17.0, "z_depth": 1.5,
         "min_slope_deg": 2,  "curv_sensitivity": 0.8, "fault_decay_km": 8.0, "smooth_sigma": 3.0},
    4:  {"fos_weight": 0.38, "w_curv": 0.18, "w_fault": 0.07, "w_aspect": 0.05,
         "w_roughness": 0.07, "w_lulc": 0.10, "w_twi_ls": 0.15,
         "algo": "bank_failure",     "gamma_soil": 18.0, "z_depth": 2.0,
         "min_slope_deg": 5,  "curv_sensitivity": 1.5, "fault_decay_km": 6.0, "smooth_sigma": 2.0},
    5:  {"fos_weight": 0.45, "w_curv": 0.15, "w_fault": 0.08, "w_aspect": 0.07,
         "w_roughness": 0.08, "w_lulc": 0.10, "w_twi_ls": 0.07,
         "algo": "translational",    "gamma_soil": 18.5, "z_depth": 2.0,
         "min_slope_deg": 8,  "curv_sensitivity": 1.3, "fault_decay_km": 5.0, "smooth_sigma": 1.5},
    6:  {"fos_weight": 0.42, "w_curv": 0.15, "w_fault": 0.08, "w_aspect": 0.08,
         "w_roughness": 0.08, "w_lulc": 0.12, "w_twi_ls": 0.07,
         "algo": "translational",    "gamma_soil": 18.0, "z_depth": 1.8,
         "min_slope_deg": 6,  "curv_sensitivity": 1.2, "fault_decay_km": 6.0, "smooth_sigma": 1.8},
    7:  {"fos_weight": 0.50, "w_curv": 0.13, "w_fault": 0.10, "w_aspect": 0.07,
         "w_roughness": 0.07, "w_lulc": 0.08, "w_twi_ls": 0.05,
         "algo": "translational",    "gamma_soil": 19.0, "z_depth": 2.5,
         "min_slope_deg": 12, "curv_sensitivity": 1.5, "fault_decay_km": 4.0, "smooth_sigma": 1.2},
    8:  {"fos_weight": 0.55, "w_curv": 0.12, "w_fault": 0.10, "w_aspect": 0.05,
         "w_roughness": 0.08, "w_lulc": 0.05, "w_twi_ls": 0.05,
         "algo": "debris_flow",      "gamma_soil": 20.0, "z_depth": 3.0,
         "min_slope_deg": 15, "curv_sensitivity": 2.0, "fault_decay_km": 3.0, "smooth_sigma": 1.0},
    9:  {"fos_weight": 0.30, "w_curv": 0.20, "w_fault": 0.10, "w_aspect": 0.08,
         "w_roughness": 0.07, "w_lulc": 0.08, "w_twi_ls": 0.17,
         "algo": "scarp_retreat",    "gamma_soil": 19.0, "z_depth": 2.0,
         "min_slope_deg": 5,  "curv_sensitivity": 1.3, "fault_decay_km": 4.0, "smooth_sigma": 2.0},
    10: {"fos_weight": 0.60, "w_curv": 0.10, "w_fault": 0.12, "w_aspect": 0.05,
         "w_roughness": 0.06, "w_lulc": 0.03, "w_twi_ls": 0.04,
         "algo": "rotational_planar","gamma_soil": 22.0, "z_depth": 2.5,
         "min_slope_deg": 25, "curv_sensitivity": 1.8, "fault_decay_km": 2.0, "smooth_sigma": 0.8},
    11: {"fos_weight": 0.08, "w_curv": 0.10, "w_fault": 0.05, "w_aspect": 0.05,
         "w_roughness": 0.05, "w_lulc": 0.12, "w_twi_ls": 0.55,
         "algo": "creep",            "gamma_soil": 16.0, "z_depth": 1.0,
         "min_slope_deg": 1,  "curv_sensitivity": 0.5, "fault_decay_km": 10.0,"smooth_sigma": 4.0},
    12: {"fos_weight": 0.25, "w_curv": 0.15, "w_fault": 0.03, "w_aspect": 0.12,
         "w_roughness": 0.10, "w_lulc": 0.15, "w_twi_ls": 0.20,
         "algo": "dune_collapse",    "gamma_soil": 16.5, "z_depth": 1.2,
         "min_slope_deg": 2,  "curv_sensitivity": 1.0, "fault_decay_km": 10.0,"smooth_sigma": 3.0},
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
    ds = max(1, min(h, w) // 1000) if min(h, w) > 2000 else 2
    new_h, new_w = h // ds, w // ds
    if new_h < 1 or new_w < 1:
        ds, ds_arr, new_transform = 1, sieved, meta["transform"]
    else:
        ds_arr = sieved[::ds, ::ds]
        new_transform = meta["transform"] * Affine.scale(ds, ds)
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


def _lulc_to_map(lulc, lookup, default=0.5):
    out = np.full_like(lulc, default, dtype=np.float32)
    for cls_val, factor in lookup.items():
        out[lulc == cls_val] = factor
    return out


def _derive_soil_properties(soil_arr, rzsm_arr, h, w):
    if soil_arr is not None:
        cohesion = np.full((h, w), 10.0, dtype=np.float32)
        friction = np.full((h, w), 28.0, dtype=np.float32)
        ks       = np.full((h, w),  1.5, dtype=np.float32)
        for cls in SOIL_COHESION_KPA:
            mask = soil_arr == cls
            cohesion[mask] = SOIL_COHESION_KPA[cls]
            friction[mask] = SOIL_FRICTION_DEG.get(cls, 28.0)
            ks[mask]       = SOIL_HYDRAULIC_CONDUCTIVITY.get(cls, 1.5)
    else:
        cohesion = np.full((h, w), 10.0, dtype=np.float32)
        friction = np.full((h, w), 28.0, dtype=np.float32)
        ks       = np.full((h, w),  1.5, dtype=np.float32)

    if rzsm_arr is not None:
        rzsm_safe = np.clip(rzsm_arr, 0.0, 1.0)
        cohesion *= (1.0 - 0.40 * rzsm_safe)
        friction *= (1.0 - 0.15 * rzsm_safe)
    return cohesion, friction, ks


def _compute_saturation_proxy(twi, rzsm, ks, h, w):
    if twi is not None:
        m = np.clip((twi - 2.0) / 12.0, 0.0, 1.0)
        if rzsm is not None:
            m = 0.6 * m + 0.4 * np.clip(rzsm, 0.0, 1.0)
        if ks is not None:
            m = m * (1.0 - 0.3 * np.clip(ks / 20.0, 0.01, 1.0))
        return np.clip(m, 0.0, 1.0).astype(np.float32)
    if rzsm is not None:
        return np.clip(rzsm * 0.8, 0.0, 1.0).astype(np.float32)
    return np.full((h, w), 0.3, dtype=np.float32)


def _compute_fos(slope_deg, cohesion, friction_deg, gamma, z, m_sat):
    GAMMA_W   = 9.81
    alpha_rad = np.radians(np.clip(slope_deg, 0.01, 89.99))
    cos2      = np.cos(alpha_rad) ** 2
    sincos    = np.sin(alpha_rad) * np.cos(alpha_rad)
    tan_phi   = np.tan(np.radians(np.clip(friction_deg, 1.0, 60.0)))
    effective_normal = gamma * z * cos2 - m_sat * GAMMA_W * z * cos2
    shear_strength   = cohesion + np.maximum(effective_normal, 0.0) * tan_phi
    shear_driving    = gamma * z * sincos
    fos = np.where(shear_driving > 0.01, shear_strength / shear_driving, 5.0)
    return np.clip(fos, 0.1, 5.0).astype(np.float32)


def _fos_to_susceptibility(fos):
    return np.clip(1.0 / (1.0 + np.exp(3.5 * (fos - 1.5))), 0.0, 1.0).astype(np.float32)


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
    L["elev"] = elev
    L["meta"] = meta
    L["meta"]["height"] = elev.shape[0]
    L["meta"]["width"]  = elev.shape[1]

    LAYER_MAP = {
        "slope": "slope", "aspect": "aspect", "roughness": "roughness",
        "curv_plan": "plan_curv", "curv_prof": "profile_curv",
        "twi": "twi", "fa": "d8_flow_acc", "fa_dinf": "dinf_flow_acc",
        "fa_mfd": "mfd_flow_acc", "lulc": "lulc", "soil": "soil_class",
        "terrain_class": "terrain_class",
    }
    for key, fname in LAYER_MAP.items():
        arr, ameta = _read(features_dir, fname)
        L[key] = _resample_to_dem(arr, ameta, L["meta"]) if (arr is not None and ameta is not None) else arr

    if L.get("twi") is None:
        arr, ameta = _try("twi_d8")
        if arr is not None and ameta is not None:
            L["twi"] = _resample_to_dem(arr, ameta, L["meta"])

    if L.get("fa") is None:
        arr, ameta = _try("flow_acc")
        if arr is not None and ameta is not None:
            L["fa"] = _resample_to_dem(arr, ameta, L["meta"])

    L["rzsm"] = None
    rzsm_path = os.path.join(features_dir, "RZSM_2024_Soil.tif")
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
                src_meta  = src.meta.copy()
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


def _compute_ls_for_terrain(terrain_id, mask, params, elev, slope, twi, fa,
                             curv_plan, curv_prof, aspect, roughness, lulc,
                             soil_arr, rzsm, fault_prox, h, w):
    ls = np.full((h, w), np.nan, dtype=np.float32)
    vm = mask & np.isfinite(slope)
    if not vm.any():
        return ls

    cohesion, friction, ks = _derive_soil_properties(soil_arr, rzsm, h, w)
    m_sat     = _compute_saturation_proxy(twi, rzsm, ks, h, w)
    slope_use = np.where(np.isfinite(slope), slope, 5.0)

    if cohesion.shape != (h, w):
        cohesion = np.full((h, w), 10.0, dtype=np.float32)
        friction = np.full((h, w), 28.0, dtype=np.float32)
    if m_sat.shape != (h, w):
        m_sat = np.full((h, w), 0.3, dtype=np.float32)

    fos    = _compute_fos(slope_use, cohesion, friction, params["gamma_soil"], params["z_depth"], m_sat)
    ls_fos = _fos_to_susceptibility(fos)
    ls_fos[slope_use < params["min_slope_deg"]] = 0.0

    if curv_plan is not None:
        ls_fos = np.clip(ls_fos + np.clip(curv_plan * params["curv_sensitivity"] * 100, -0.3, 0.5), 0.0, 1.0)
    if curv_prof is not None:
        ls_fos = np.clip(ls_fos + np.clip(curv_prof * 50, -0.2, 0.3), 0.0, 1.0)
    if lulc is not None:
        root_c = _lulc_to_map(lulc, LULC_ROOT_COHESION, default=2.0)
        ls_fos = np.clip(ls_fos - np.clip(root_c / 8.5, 0.0, 1.0) * 0.35, 0.0, 1.0)
    if fault_prox is not None:
        ls_fos = np.clip(ls_fos + fault_prox * 0.20, 0.0, 1.0)
    if aspect is not None:
        north_factor = 0.5 * (1.0 + np.cos(np.radians(aspect)))
        ls_fos = np.clip(ls_fos + north_factor * 0.10 - 0.05, 0.0, 1.0)
    if roughness is not None:
        ls_fos = np.clip(ls_fos + _normalise(roughness) * 0.10, 0.0, 1.0)
    if twi is not None:
        ls_fos = np.clip(ls_fos + _normalise(twi) * params["w_twi_ls"] * 0.5, 0.0, 1.0)

    algo = params["algo"]
    if algo == "debris_flow" and fa is not None:
        fa_norm     = _normalise(np.log1p(np.where(fa > 0, fa, 1)))
        steep_hollow = fa_norm * np.clip(slope_use / 35.0, 0.0, 1.0)
        ls_fos = np.clip(ls_fos + 0.15 * steep_hollow, 0.0, 1.0)
    elif algo == "lateral_spreading":
        ls_fos = np.clip(ls_fos + np.clip(1.0 - slope_use / 5.0, 0.0, 1.0) * 0.15, 0.0, 1.0)
    elif algo == "coastal_failure" and aspect is not None:
        ls_fos = np.clip(ls_fos + 0.10 * np.clip(np.sin(np.radians(aspect)), 0.0, 1.0), 0.0, 1.0)

    ls[vm] = ls_fos[vm]
    return ls


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
            for threshold, cls_id in zip(
                [np.nanpercentile(vals, p) for p in [20, 40, 60, 80]], [2, 3, 4, 5]
            ):
                c[vals >= threshold] = cls_id
            result[tmask] = c
        return result
    cls_arr[valid] = 1
    cls_arr[valid & (arr >= 0.15)] = 2
    cls_arr[valid & (arr >= 0.35)] = 3
    cls_arr[valid & (arr >= 0.55)] = 4
    cls_arr[valid & (arr >= 0.75)] = 5
    return cls_arr


def compute_landslide_susceptibility(features_dir, fault_shp=None, output_dir=None):
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
    fa      = layers.get("fa")
    curv_p  = layers.get("curv_plan")
    curv_r  = layers.get("curv_prof")
    aspect  = layers.get("aspect")
    rough   = layers.get("roughness")
    lulc    = layers.get("lulc")
    soil    = layers.get("soil")
    rzsm    = layers.get("rzsm")

    h, w = elev.shape
    ls_final = np.full((h, w), np.nan, dtype=np.float32)

    for shp_name in ["fault_lines.shp", "faults.shp", "geological_faults.shp"]:
        p = os.path.join(features_dir, shp_name)
        if os.path.exists(p) and fault_shp is None:
            fault_shp = p

    fault_prox = _build_proximity_raster(fault_shp, meta, max_dist_m=20000) if fault_shp else None

    for tc in np.unique(terrain[np.isfinite(terrain) & (terrain != NODATA_INT)]):
        tc_int = int(tc)
        if tc_int not in TERRAIN_NAMES:
            continue
        mask = (terrain == tc_int) & np.isfinite(slope)
        if not mask.any():
            continue
        params  = LANDSLIDE_TERRAIN_PARAMS.get(tc_int, LANDSLIDE_TERRAIN_PARAMS[3])
        partial = _compute_ls_for_terrain(
            tc_int, mask, params, elev, slope, twi, fa, curv_p, curv_r,
            aspect, rough, lulc, soil, rzsm, fault_prox, h, w)
        ls_final[mask] = _smooth(partial, sigma=params["smooth_sigma"], valid_mask=mask)[mask]

    valid = np.isfinite(ls_final)
    if valid.any():
        p2, p98 = np.nanpercentile(ls_final[valid], 2), np.nanpercentile(ls_final[valid], 98)
        ls_final[valid] = np.clip((ls_final[valid] - p2) / (p98 - p2 + 1e-9), 0.0, 1.0)

    ls_tif     = os.path.join(output_dir, "landslide_susceptibility.tif")
    ls_cls_tif = os.path.join(output_dir, "landslide_class.tif")
    ls_shp     = os.path.join(output_dir, "landslide_susceptibility.shp")
    ls_cls_shp = os.path.join(output_dir, "landslide_class.shp")

    _save_continuous(ls_final, meta, ls_tif, "Landslide susceptibility index 0-1")
    ls_class = _susceptibility_to_class(ls_final, terrain)
    _save_class(ls_class.astype(np.float32), meta, ls_cls_tif, CLASS_LABELS)
    _vectorise(ls_final, meta, ls_shp, "ls_idx", is_float=True)
    _class_to_shp(ls_class, meta, ls_cls_shp)

    valid_cls = ls_class[(ls_class >= 1) & (ls_class <= 5)]
    total     = len(valid_cls)
    class_stats = {
        CLASS_LABELS[c]: {
            "pixel_count": int((valid_cls == c).sum()),
            "pct": round(int((valid_cls == c).sum()) / total * 100, 2) if total > 0 else 0,
        }
        for c in range(1, 6)
    }

    return {
        "tif_path":        ls_tif,
        "class_tif_path":  ls_cls_tif,
        "shp_path":        ls_shp,
        "class_shp_path":  ls_cls_shp,
        "class_stats":     class_stats,
        "elapsed_seconds": round(time.time() - t_start, 1),
    }


def run(features_dir, fault_shp=None, output_dir=None):
    return compute_landslide_susceptibility(features_dir, fault_shp=fault_shp, output_dir=output_dir)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Landslide susceptibility mapper")
    parser.add_argument("features_dir")
    parser.add_argument("--fault-shp", default=None)
    parser.add_argument("--output-dir", default=None)
    args = parser.parse_args()
    result = run(args.features_dir, fault_shp=args.fault_shp, output_dir=args.output_dir)
    for k, v in result.items():
        if isinstance(v, str):
            print(f"  {k}: {v}")
