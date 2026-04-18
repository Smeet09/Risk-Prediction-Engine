"""
susceptibility.py — Disaster-wise Susceptibility Router (Production)
=====================================================================
Dynamically routes susceptibility generation to the correct
disaster-specific Python script.

Script resolution order:
  1. disaster_types.script_path  (admin-uploaded custom script via DB)
  2. gis-service/susceptibility_scripts/{code}_susceptibility.py (built-in)
  3. HTTP 404 — no script registered

Manual data lookup:
  Pulls river, fault, lulc, soil paths from manual_data_india DB table.
  Pulls coastline geometry from india_coastlines PostGIS table.
"""
import re, json, importlib.util, sys, tempfile, os
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException, UploadFile, File, Form
from pydantic import BaseModel
from sqlalchemy import text

from config import settings

router = APIRouter()

# ─────────────────────────────────────────────────────────────────────────────
# Built-in script directory (inside gis-service)
SCRIPTS_DIR = Path(__file__).parent.parent / "susceptibility_scripts"

# ─────────────────────────────────────────────────────────────────────────────
# Models
# ─────────────────────────────────────────────────────────────────────────────

class SusceptibilityRequest(BaseModel):
    job_id:          str
    region_id:       str
    disaster_code:   str           # e.g. "flood", "landslide"
    country:         str
    state:           Optional[str] = None
    district:        Optional[str] = None
    terrain_weights: dict = {}     # { "1": 0.9, "2": 1.0, ... }
    data_root:       Optional[str] = None
    script_path:     Optional[str] = None   # override from backend


def _safe(s) -> str:
    """Match the exact same logic as dem.py to reconstruct file paths correctly."""
    if not s or str(s).strip() == "" or str(s).lower() == "none":
        return "_state_level"
    return re.sub(r"[^a-zA-Z0-9_-]", "_", str(s))


# ─────────────────────────────────────────────────────────────────────────────
# DB helper
# ─────────────────────────────────────────────────────────────────────────────

def _get_engine():
    from sqlalchemy import create_engine
    return create_engine(settings.DATABASE_URL)


def _get_features_dir_from_db(engine, region_id: str) -> Optional[str]:
    """
    Lookup the actual features_dir (output_dir) for a region from topographic_features table.
    This avoids reconstructing the path and ensures we use the exact folder that was created.
    """
    try:
        with engine.connect() as conn:
            row = conn.execute(
                text("SELECT output_dir FROM topographic_features WHERE region_id=:rid LIMIT 1"),
                {"rid": region_id}
            ).fetchone()
        if row and row[0]:
            return row[0]
    except Exception as e:
        print(f"[SuscRouter] Could not lookup features_dir from DB: {e}")
    return None


def _get_manual_data_paths(engine) -> dict:
    """Returns { data_type: file_path } from manual_data_india table."""
    with engine.connect() as conn:
        rows = conn.execute(
            text("SELECT data_type, file_path FROM manual_data_india WHERE file_path IS NOT NULL")
        ).fetchall()
    return {r.data_type: r.file_path for r in rows}

def _crop_vector_from_postgis(features_dir: Path, target_table: str, engine, out_dir: Path, out_name: str) -> Optional[str]:
    """
    Reads the DEM boundary, queries PostGIS to extract intersecting features,
    and saves them out to a temporary Shapefile. If table doesn't exist or is empty, returns None.
    """
    import rasterio
    import geopandas as gpd
    
    dem_path = Path(features_dir) / "breached_dem.tif"
    if not dem_path.exists():
        dem_path = Path(features_dir) / "elevation.tif"
    if not dem_path.exists():
        print(f"[SuscRouter] Cannot crop {target_table} - no DEM found to establish bounding box")
        return None

    try:
        with rasterio.open(str(dem_path)) as src:
            bounds = src.bounds
            
        # Verify table exists to prevent strict crash
        with engine.connect() as conn:
            res = conn.execute(text(f"SELECT EXISTS (SELECT FROM information_schema.tables WHERE table_name = '{target_table}');"))
            if not res.scalar():
                print(f"[SuscRouter] Table {target_table} does not exist in DB. Skipping spatial extraction.")
                return None
                
        # Query utilizing geodatabase spatial index
        sql = f"""
            SELECT geom FROM {target_table}
            WHERE geom && ST_MakeEnvelope(
                {bounds.left}, {bounds.bottom}, 
                {bounds.right}, {bounds.top}, 
                4326
            )
        """
        gdf = gpd.read_postgis(sql, engine, geom_col='geom')
        
        if gdf.empty:
            print(f"[SuscRouter] No geometries from {target_table} intersect the DEM bounds.")
            return None
            
        # Save temp cropped shapefile mapped specifically to standard CRS
        gdf = gdf.set_crs("EPSG:4326", allow_override=True)
        shp_path = Path(out_dir) / f"{out_name}.shp"
        gdf.to_file(str(shp_path))
        print(f"[SuscRouter] ✅ Geo-extracted and saved shapefile from {target_table} → {shp_path.name}")
        return str(shp_path)

    except Exception as e:
        print(f"[SuscRouter] Failed spatial extraction of {target_table} from DB: {e}")
        return None


# ─────────────────────────────────────────────────────────────────────────────
# Script Loader
# ─────────────────────────────────────────────────────────────────────────────

def _load_script_module(script_path: str, disaster_code: str):
    """
    Dynamically import a Python susceptibility script as a module.
    Returns the module object or raises HTTPException.
    """
    p = Path(script_path)
    if not p.exists():
        raise HTTPException(
            404,
            detail=f"Script file not found: {script_path}"
        )

    module_name = f"susc_{disaster_code}_{p.stem}"
    spec = importlib.util.spec_from_file_location(module_name, str(p))
    mod  = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = mod
    try:
        spec.loader.exec_module(mod)
    except Exception as e:
        raise HTTPException(500, detail=f"Error loading script {p.name}: {e}")
    return mod


def _resolve_script(disaster_code: str, script_path_override: Optional[str]) -> str:
    """
    Resolve the final script path.
    Priority:
      1. Override from backend (DB-stored custom script)
      2. Built-in script: susceptibility_scripts/{code}_susceptibility.py
    """
    # 1. Backend-supplied custom path (uploaded by admin)
    if script_path_override and Path(script_path_override).exists():
        print(f"[SuscRouter] Using admin-uploaded script: {script_path_override}")
        return script_path_override

    # 2. Built-in
    builtin = SCRIPTS_DIR / f"{disaster_code}_susceptibility.py"
    if builtin.exists():
        print(f"[SuscRouter] Using built-in script: {builtin}")
        return str(builtin)

    raise HTTPException(
        404,
        detail=(
            f"No susceptibility script registered for disaster '{disaster_code}'. "
            f"Please upload a script via Disaster Manager or add "
            f"'{disaster_code}_susceptibility.py' to the susceptibility_scripts/ directory."
        )
    )


# ─────────────────────────────────────────────────────────────────────────────
# Helper: vectorise raster to GeoJSON
# ─────────────────────────────────────────────────────────────────────────────

def _raster_to_geojson(tif_path: str) -> dict:
    """
    Convert the 5-class susceptibility raster to a GeoJSON FeatureCollection.
    Uses rasterio.features.shapes for fast vectorisation.
    """
    import rasterio
    import rasterio.features
    import numpy as np
    from shapely.geometry import shape, mapping
    import geopandas as gpd

    CLASS_LABELS = {1: "Very Low", 2: "Low", 3: "Moderate", 4: "High", 5: "Very High"}
    COLORS = {
        1: "#2dc653", 2: "#80b918",
        3: "#f9c74f", 4: "#f3722c", 5: "#d62828"
    }

    features = []
    try:
        with rasterio.open(tif_path) as src:
            data  = src.read(1)
            nodata = src.nodata
            transform = src.transform
            crs = src.crs

        valid_classes = [c for c in CLASS_LABELS if np.any(data == c)]
        for cls_id in valid_classes:
            mask = (data == cls_id).astype(np.uint8)
            for geom, val in rasterio.features.shapes(mask, mask=mask, transform=transform):
                features.append({
                    "type": "Feature",
                    "properties": {
                        "class_id":     cls_id,
                        "susceptibility": CLASS_LABELS[cls_id],
                        "color":        COLORS.get(cls_id, "#aaa"),
                    },
                    "geometry": geom,
                })
    except Exception as e:
        print(f"[SuscRouter] Vectorisation warning: {e}")

    return {"type": "FeatureCollection", "features": features}


# ─────────────────────────────────────────────────────────────────────────────
# Helper: save SHP to PostGIS
# ─────────────────────────────────────────────────────────────────────────────

def _save_to_postgis(geojson: dict, disaster_code: str, region_id: str,
                     country: str, state: Optional[str], district: Optional[str],
                     engine, out_dir: Optional[str] = None) -> bool:
    """
    Save susceptibility polygons to a disaster-specific PostGIS table:
      susceptibility_flood, susceptibility_landslide, susceptibility_<code>
    This ensures different disasters for the same region never conflict.
    Old rows for the same region_id are deleted before inserting fresh ones.
    """
    import geopandas as gpd
    from shapely.geometry import shape

    if not geojson.get("features"):
        print("[SuscRouter] No features to store in PostGIS")
        return False

    # Sanitize table name: only alphanumeric + underscore
    safe_code  = re.sub(r"[^a-z0-9_]", "_", disaster_code.lower())
    table_name = f"susceptibility_{safe_code}"
    print(f"[SuscRouter] Storing into PostGIS table: {table_name}")

    try:
        import geopandas as gpd
        from shapely.geometry import shape
        import shapely

        rows = []
        for f in geojson["features"]:
            try:
                geom = shape(f["geometry"])
                # Ensure MultiPolygon
                if geom.geom_type == "Polygon":
                    geom = shapely.geometry.MultiPolygon([geom])
                rows.append({
                    "region_id":      region_id,
                    "country":        country or "",
                    "state":          state or "",
                    "district":       district or "",
                    "class_id":       f["properties"].get("class_id"),
                    "susceptibility": f["properties"].get("susceptibility"),
                    "geometry":       geom,
                })
            except Exception:
                continue

        if not rows:
            print("[SuscRouter] All features failed geometry conversion")
            return False

        gdf = gpd.GeoDataFrame(rows, crs="EPSG:4326")

        if out_dir:
            try:
                # rename column to fit SHP 10-character limit
                gdf_shp = gdf.rename(columns={"susceptibility": "susc_class"})
                shp_path = Path(out_dir) / f"{disaster_code}_class.shp"
                gdf_shp.to_file(str(shp_path))
                print(f"[SuscRouter] ✅ Saved local shapefile → {shp_path}")
            except Exception as e:
                print(f"[SuscRouter] Local shapefile save warning: {e}")

        # Create disaster-specific table if it doesn't exist
        with engine.begin() as conn:
            conn.execute(text(f"""
                CREATE TABLE IF NOT EXISTS {table_name} (
                    id              BIGSERIAL PRIMARY KEY,
                    region_id       TEXT NOT NULL,
                    country         TEXT DEFAULT '',
                    state           TEXT DEFAULT '',
                    district        TEXT DEFAULT '',
                    class_id        INTEGER,
                    susceptibility  TEXT,
                    geom            GEOMETRY(MULTIPOLYGON, 4326),
                    created_at      TIMESTAMPTZ DEFAULT NOW()
                );
                CREATE INDEX IF NOT EXISTS idx_{table_name}_region
                  ON {table_name} (region_id);
            """))
            # Remove old data for this region before inserting fresh
            conn.execute(text(
                f"DELETE FROM {table_name} WHERE region_id=:rid"
            ), {"rid": region_id})

        gdf = gdf.rename_geometry("geom")
        gdf.to_postgis(table_name, engine,
                       if_exists="append", index=False,
                       chunksize=500)
        print(f"[SuscRouter] ✅ Stored {len(rows)} polygons in PostGIS → {table_name}")
        return True
    except Exception as e:
        print(f"[SuscRouter] PostGIS store warning (non-fatal): {e}")
        return False


# ─────────────────────────────────────────────────────────────────────────────
# Main endpoint
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/generate-susceptibility")
async def generate_susceptibility(req: SusceptibilityRequest):
    """
    Production susceptibility map generation.

    1. Resolves features_dir from DB (topographic_features.output_dir)
       to avoid path reconstruction mismatches.
    2. Falls back to reconstructing path from country/state/district if not in DB.
    3. Loads the correct disaster script (built-in or custom uploaded).
    4. Fetches manual data paths from DB (river, fault, lulc, soil).
    5. Fetches coastline from PostGIS india_coastlines table.
    6. Calls compute_{disaster}_susceptibility().
    7. Vectorises & stores polygons in disaster-specific PostGIS table.
    """
    engine = _get_engine()

    root = Path(req.data_root).resolve() if req.data_root else settings.data_root_path

    # ── Resolve features_dir ─────────────────────────────────────────────────
    # First: try the DB-stored output_dir from topographic_features
    features_dir_str = _get_features_dir_from_db(engine, req.region_id)
    if features_dir_str:
        features_dir = Path(features_dir_str)
        print(f"[SuscRouter] features_dir from DB: {features_dir}")
    else:
        # Fallback: reconstruct using dem.py-compatible _safe() logic
        features_dir = (
            root
            / _safe(req.country)
            / _safe(req.state)
            / _safe(req.district)
            / "dem_features"
        )
        print(f"[SuscRouter] features_dir reconstructed: {features_dir}")

    if not features_dir.exists():
        raise HTTPException(
            422,
            detail=(
                f"DEM features directory not found: {features_dir}\n"
                f"Please upload and process a DEM for this region first.\n"
                f"Region: {req.country} / {req.state} / {req.district}"
            )
        )

    out_dir = (
        root / _safe(req.disaster_code)
              / _safe(req.country)
              / _safe(req.state)
              / _safe(req.district)
              / "susceptibility_output"
    )
    out_dir.mkdir(parents=True, exist_ok=True)

    # ── 1. Resolve script ─────────────────────────────────────────────────────
    script_path = _resolve_script(req.disaster_code, req.script_path)

    # ── 2. Load manual data paths from DB ─────────────────────────────────────
    print(f"[SuscRouter] Fetching manual data paths from DB ...")
    manual_paths = _get_manual_data_paths(engine)
    print(f"[SuscRouter] Manual data types available: {list(manual_paths.keys())}")

    # We intentionally ignore the raw river/fault ZIP files here to prevent GeoPandas crashes 
    # Instead, if the user requested those layers, we extract a tight bounding box dynamically from PostGIS
    if manual_paths.get("river"):
        river_path = _crop_vector_from_postgis(features_dir, "india_rivers", engine, out_dir, "river_cropped")
    else:
        river_path = None
        
    if manual_paths.get("fault"):
        fault_path = _crop_vector_from_postgis(features_dir, "india_faults", engine, out_dir, "fault_cropped")
    else:
        fault_path = None
        
    # LULC and soil remain as raster paths
    lulc_path   = manual_paths.get("lulc")
    soil_path   = manual_paths.get("soil")

    # ── 4. Load and call the script ───────────────────────────────────────────
    print(f"[SuscRouter] Loading script: {script_path}")
    mod = _load_script_module(script_path, req.disaster_code)

    # Build kwargs based on disaster type (each script has a specific signature)
    result: dict = {}

    try:
        if req.disaster_code == "flood":
            fn = getattr(mod, "compute_flood_susceptibility", None)
            if fn is None:
                raise HTTPException(500, detail="flood script missing compute_flood_susceptibility()")
            print(f"[SuscRouter] Calling compute_flood_susceptibility ...")
            result = fn(
                features_dir     = str(features_dir),
                river_shp_path   = river_path,
                lulc_shp_path    = lulc_path,
                soil_raster_path = soil_path,
                output_dir       = str(out_dir),
            )

        elif req.disaster_code == "landslide":
            fn = getattr(mod, "compute_landslide_susceptibility", None)
            if fn is None:
                raise HTTPException(500, detail="landslide script missing compute_landslide_susceptibility()")
            print(f"[SuscRouter] Calling compute_landslide_susceptibility ...")
            result = fn(
                features_dir     = str(features_dir),
                fault_shp_path   = fault_path,
                lulc_shp_path    = lulc_path,
                soil_raster_path = soil_path,
                output_dir       = str(out_dir),
            )

        else:
            # Generic / custom uploaded scripts.
            # Convention: the script must expose a function named
            # compute_{disaster_code}_susceptibility(**kwargs)
            fn_name = f"compute_{req.disaster_code}_susceptibility"
            fn = getattr(mod, fn_name, None)

            # Fallback: check for a generic "compute_susceptibility" hook
            if fn is None:
                fn = getattr(mod, "compute_susceptibility", None)

            if fn is None:
                raise HTTPException(
                    500,
                    detail=(
                        f"Script for '{req.disaster_code}' must expose a function named "
                        f"'{fn_name}' or 'compute_susceptibility'."
                    )
                )

            print(f"[SuscRouter] Calling {fn_name} (custom script) ...")
            result = fn(
                features_dir     = str(features_dir),
                river_shp_path   = river_path,
                lulc_shp_path    = lulc_path,
                fault_shp_path   = fault_path,
                soil_raster_path = soil_path,
                output_dir       = str(out_dir),
            )
    except HTTPException:
        raise
    except Exception as e:
        import traceback
        tb = traceback.format_exc()
        print(f"[SuscRouter] Script execution failed:\n{tb}")
        raise HTTPException(500, detail=f"Susceptibility script error: {str(e)}")

    # ── 5. Vectorise the 5-class raster → GeoJSON ────────────────────────────
    class_tif = result.get("class_tif_path") or result.get("tif_path", "")
    geojson   = {}
    if class_tif and Path(class_tif).exists():
        print(f"[SuscRouter] Vectorising {class_tif} ...")
        try:
            geojson = _raster_to_geojson(class_tif)
        except Exception as e:
            print(f"[SuscRouter] Vectorisation failed (non-fatal): {e}")

    # ── 6. Save to PostGIS susceptibility_maps ────────────────────────────────
    if geojson.get("features"):
        _save_to_postgis(
            geojson, req.disaster_code, req.region_id,
            req.country, req.state, req.district, engine,
            out_dir=str(out_dir)
        )

    # ── 7. Build response ─────────────────────────────────────────────────────
    log_lines = [
        f"✓ {req.disaster_code.upper()} susceptibility map generated",
        f"  Region  : {req.country} / {req.state} / {req.district}",
        f"  Script  : {Path(script_path).name}",
        f"  River   : {river_path or 'not available'}",
        f"  Fault   : {fault_path or 'not available'}",
        f"  LULC    : {lulc_path  or 'not available'}",
        f"  Soil    : {soil_path  or 'not available'}",
        f"  Output  : {result.get('tif_path', 'N/A')}",
    ]
    if geojson.get("features"):
        log_lines.append(f"  PostGIS : {len(geojson['features'])} polygons stored → susceptibility_maps")

    return {
        "status":      "done",
        "log":         "\n".join(log_lines),
        "output_dir":  str(out_dir),
        "tif_path":    result.get("tif_path", ""),
        "geojson_path": str(out_dir / f"{req.disaster_code}_susceptibility.json"),
        "class_stats": result.get("class_stats", {}),
        "dominant_class": result.get("dominant_class"),
        "dominant_name":  result.get("dominant_name"),
        "index_stats":    result.get("index_stats", {}),
        "geojson": {
            "final": geojson,
        },
    }


# ─────────────────────────────────────────────────────────────────────────────
# Script Upload Endpoint (called by DisasterManager admin UI)
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/upload-script")
async def upload_script(
    file: UploadFile = File(...),
    disaster_code: str = Form(...)
):
    """
    Upload a custom susceptibility .py script for a disaster type.
    Saves to gis-service/susceptibility_scripts/{disaster_code}_susceptibility.py
    (or a _custom suffix if it's an override, not replacing the built-in).
    Returns the file path for the backend to store in disaster_types.script_path.
    """
    if not file.filename.endswith(".py"):
        raise HTTPException(400, detail="Only .py files are allowed")

    SCRIPTS_DIR.mkdir(parents=True, exist_ok=True)

    # If there's already a built-in, save as _custom to avoid overwriting
    builtin = SCRIPTS_DIR / f"{disaster_code}_susceptibility.py"
    if builtin.exists():
        dest = SCRIPTS_DIR / f"{disaster_code}_custom_susceptibility.py"
    else:
        dest = SCRIPTS_DIR / f"{disaster_code}_susceptibility.py"

    content = await file.read()
    dest.write_bytes(content)
    print(f"[SuscRouter] Script uploaded: {dest}")

    return {
        "script_path": str(dest),
        "filename":    dest.name,
        "message":     f"Script saved for disaster '{disaster_code}'",
    }
