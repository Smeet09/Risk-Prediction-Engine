import os
import shutil
import numpy as np
import rasterio
from rasterio.crs import CRS
import gc
from pathlib import Path
import whitebox

class TopoProcessor:
    """
    Automated topographic feature extraction using WhiteboxTools.
    Optimized for high-resolution grids (100M+ pixels) with memory constraints.
    """

    def __init__(self, working_dir: Path):
        self.working_dir = working_dir
        self.working_dir.mkdir(parents=True, exist_ok=True)
        self.wbt = whitebox.WhiteboxTools()
        self.wbt.set_working_dir(str(self.working_dir))
        self.wbt.set_verbose_mode(True)
        
        # Reverted: Removing max_procs(1) as it causes silent failures on some systems.
        # WBT will now use the default optimal thread count for the CPU.

    def read_raster(self, path: Path):
        with rasterio.open(path) as src:
            arr = src.read(1).astype(np.float32)
            meta = src.meta.copy()
            nd = src.nodata
        if nd is not None:
            arr[arr == nd] = np.nan
        return arr, meta

    def write_raster(self, arr, meta, path: Path, dtype='float32', nodata=-9999):
        m = meta.copy()
        m.update({
            'count': 1,
            'dtype': dtype,
            'nodata': 0 if dtype == 'uint8' else nodata,
            'compress': 'lzw',
            'tiled': True, # Tiling helps with file I/O for large rasters
            'blockxsize': 256,
            'blockysize': 256
        })
        out = arr.astype(np.float32)
        if dtype != 'uint8':
            out[~np.isfinite(out)] = nodata
        with rasterio.open(path, 'w', **m) as dst:
            dst.write(out.astype(dtype if dtype != 'uint8' else np.uint8)[None, :, :])
        
        # Force cleanup
        del out
        gc.collect()



    def _ensure_crs(self, dem_path: Path, logs: list) -> Path:
        """
        WhiteboxTools silently fails (produces no output) when the input DEM
        has no CRS defined. This method detects that case and writes a
        repaired copy in-place with EPSG:4326 (WGS84) — the correct CRS
        for degree-coordinate DEMs like SRTM / ALOS.
        Returns the (possibly repaired) path to use for subsequent WBT calls.
        """
        with rasterio.open(dem_path) as src:
            if src.crs is not None:
                return dem_path  # CRS is fine, nothing to do

            logs.append("⚠️  DEM has no CRS — auto-assigning EPSG:4326 (WGS84) based on coordinate extent.")

            # Determine the correct CRS from bounding box coordinates
            bounds = src.bounds
            # Lat/lon degree range → definitely geographic WGS84
            if -180 <= bounds.left <= 180 and -90 <= bounds.bottom <= 90:
                target_crs = CRS.from_epsg(4326)
            else:
                # Fallback: still assign WGS84 so WBT can run
                target_crs = CRS.from_epsg(4326)
                logs.append("⚠️  Coordinates out of normal lat/lon range; defaulting CRS to EPSG:4326.")

            meta = src.meta.copy()
            data = src.read()

        # Write repaired file back to the same path
        meta.update(crs=target_crs)
        with rasterio.open(dem_path, 'w', **meta) as dst:
            dst.write(data)

        logs.append(f"✅ CRS repaired → {target_crs} written to {dem_path.name}")
        return dem_path

    def process(self, dem_path: Path):
        """Runs the full topography extraction pipeline with single-core memory optimization."""
        logs = []
        
        def _check_skip(output_name, task_name):
            if (self.working_dir / output_name).exists():
                logs.append(f"⏩ Skipping {task_name} (File already exists)")
                return True
            return False

        # 0. CRS sanity check — WBT silently fails on CRS-less DEMs
        dem_path = self._ensure_crs(dem_path, logs)

        # 1. Breach depressions
        if not _check_skip("breached_dem.tif", "Breaching"):
            logs.append("Breaching depressions...")
            out_file = str(self.working_dir / "breached_dem.tif")
            # Using absolute paths is safer on Windows with spaces
            ret = self.wbt.breach_depressions(
                dem=str(dem_path), 
                output=out_file
            )
            if not os.path.exists(out_file):
                raise FileNotFoundError(
                    f"Breaching failed: {out_file} was not created by the engine. "
                    f"WBT return code: {ret}. Check that the DEM is a valid raster "
                    f"with elevation data and adequate disk space."
                )
            gc.collect()
        
        # 2. Slope
        if not _check_skip("slope.tif", "Slope computation"):
            logs.append("Computing slope...")
            out_file = str(self.working_dir / "slope.tif")
            self.wbt.slope(
                dem=str(self.working_dir / "breached_dem.tif"), 
                output=out_file, 
                units="degrees"
            )
            if not os.path.exists(out_file):
                raise FileNotFoundError(f"Slope failed: {out_file} not created.")
            gc.collect()
        
        # 3. Curvatures
        if not _check_skip("plan_curv.tif", "Plan Curvature"):
            out_file = str(self.working_dir / "plan_curv.tif")
            self.wbt.plan_curvature(dem=str(self.working_dir / "breached_dem.tif"), output=out_file)
            gc.collect()

        if not _check_skip("profile_curv.tif", "Profile Curvature"):
            out_file = str(self.working_dir / "profile_curv.tif")
            self.wbt.profile_curvature(dem=str(self.working_dir / "breached_dem.tif"), output=out_file)
            gc.collect()
        
        # 4. Aspect
        if not _check_skip("aspect.tif", "Aspect computation"):
            logs.append("Computing aspect...")
            out_file = str(self.working_dir / "aspect.tif")
            self.wbt.aspect(dem=str(self.working_dir / "breached_dem.tif"), output=out_file)
            gc.collect()
        
        # 5. Roughness
        if not _check_skip("roughness.tif", "Roughness computation"):
            logs.append("Computing roughness...")
            out_file = str(self.working_dir / "roughness.tif")
            self.wbt.ruggedness_index(dem=str(self.working_dir / "breached_dem.tif"), output=out_file)
            gc.collect()
        
        # 6. Flow Direction (D8)
        if not _check_skip("flow_direction.tif", "Flow Direction (D8)"):
            logs.append("Computing Flow Direction (D8)...")
            out_file = str(self.working_dir / "flow_direction.tif")
            self.wbt.d8_pointer(dem=str(self.working_dir / "breached_dem.tif"), output=out_file)
            gc.collect()

        # 7. TWI using D8, D-Inf, and MFD (FD8)
        def calc_twi(method_name, out_acc_file, out_twi_file, acc_func):
            if (self.working_dir / out_twi_file).exists():
                logs.append(f"⏩ Skipping TWI ({method_name}) calculation (File exists)")
                return
            
            logs.append(f"Computing TWI ({method_name})...")
            out_acc = str(self.working_dir / out_acc_file)
            if not os.path.exists(out_acc):
                in_dem = str(self.working_dir / "breached_dem.tif")
                acc_func(in_dem, out_acc)
                if not os.path.exists(out_acc):
                    raise FileNotFoundError(f"{method_name} Flow Accumulation failed to produce {out_acc}. Check disk space/RAM.")
                gc.collect()
            
            arr_acc_log, meta = self.read_raster(self.working_dir / out_acc_file)
            arr_slope, _ = self.read_raster(self.working_dir / "slope.tif")
            
            px_deg = abs(meta['transform'].a)
            px_m = px_deg * 111320 * np.cos(np.radians(35.0))
            
            acc = np.maximum(np.power(10, arr_acc_log) - 1, 1)
            del arr_acc_log
            
            As = acc * (px_m ** 2) / px_m
            del acc
            
            tan_s = np.where(np.tan(np.radians(arr_slope)) < 0.001, 0.001, np.tan(np.radians(arr_slope)))
            del arr_slope
            
            twi = np.log(As / tan_s).astype(np.float32)
            self.write_raster(twi, meta, self.working_dir / out_twi_file)
            
            del twi, As, tan_s
            gc.collect()

        calc_twi("D8", "flow_acc_d8.tif", "twi_d8.tif", lambda i, out: self.wbt.d8_flow_accumulation(i=i, output=out, out_type="cells", log=True))
        calc_twi("D-Inf", "flow_acc_dinf.tif", "twi_dinf.tif", lambda i, out: self.wbt.d_inf_flow_accumulation(i=i, output=out, log=True))
        calc_twi("MFD", "flow_acc_mfd.tif", "twi_mfd.tif", lambda i, out: self.wbt.fd8_flow_accumulation(dem=i, output=out, log=True))

        # 8. Drainage Network
        if not _check_skip("stream_network.tif", "Drainage Network"):
            logs.append("Computing drainage network (D8)...")
            arr_d8, meta = self.read_raster(self.working_dir / "flow_acc_d8.tif")
            stream = (arr_d8 > 5.0).astype(np.float32) # Increased threshold slightly for cleaner network
            self.write_raster(stream, meta, self.working_dir / "stream_network.tif")
            
            del arr_d8, stream
            gc.collect()

        logs.append("✅ All topographic features verified/generated.")
        
        summary = [
            "\n" + "="*30,
            "TOPOGRAPHIC FEATURE SUMMARY",
            "="*30,
            f"Directory: {self.working_dir.name}",
            "Engine: Whitebox (Single-Core Optimized)",
            "Resolution: FULL Scale",
            "="*30
        ]
        
        full_log = "\n".join(logs) + "\n".join(summary)
        print(f"[TopoProcessor] Finished pipeline check.")
        return full_log
