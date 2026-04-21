"""
susceptibility.py — Disaster-wise Susceptibility Router
=========================================================
Routes susceptibility generation to the correct disaster-specific script.

Script resolution order:
  1. disaster_types.script_path  (admin-uploaded custom script via DB)
  2. gis-service/susceptibility_scripts/{code}_susceptibility.py (built-in)
  3. HTTP 404

All built-in scripts must expose:
  compute_{disaster}_susceptibility(features_dir, ..., output_dir) -> dict
  with keys: tif_path, class_tif_path, class_stats

Manual data is pulled from manual_data_india DB table.
River / fault geometries are extracted live from PostGIS by DEM bounding box.
"""
import re
import importlib.util
import sys
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException, UploadFile, File, Form
from pydantic import BaseModel
from sqlalchemy import text

from config import settings

router = APIRouter()

SCRIPTS_DIR = Path(__file__).parent.parent / "susceptibility_scripts"


class SusceptibilityRequest(BaseModel):
    job_id:          str
    region_id:       str
    disaster_code:   str
    country:         str
    state:           Optional[str] = None
    district:        Optional[str] = None
    terrain_weights: dict = {}
    data_root:       Optional[str] = None
    script_path:     Optional[str] = None


def _safe(s) -> str:
    if not s or str(s).strip() == "" or str(s).lower() == "none":
        return "_state_level"
    return re.sub(r"[^a-zA-Z0-9_-]", "_", str(s))


def _get_engine():
    from sqlalchemy import create_engine
    return create_engine(settings.DATABASE_URL)


def _get_features_dir_from_db(engine, region_id: str) -> Optional[str]:
    try:
        with engine.connect() as conn:
            row = conn.execute(
                text("SELECT output_dir FROM topographic_features WHERE region_id=:rid LIMIT 1"),
                {"rid": region_id}
            ).fetchone()
        return row[0] if row and row[0] else None
    except Exception:
        return None


def _get_manual_data_paths(engine) -> dict:
    with engine.connect() as conn:
        rows = conn.execute(
            text("SELECT data_type, file_path FROM manual_data_india WHERE file_path IS NOT NULL")
        ).fetchall()
    return {r.data_type: r.file_path for r in rows}


def _crop_vector_from_postgis(features_dir, target_table, engine, out_dir, out_name) -> Optional[str]:
    import rasterio
    import geopandas as gpd

    dem_path = Path(features_dir) / "breached_dem.tif"
    if not dem_path.exists():
        dem_path = Path(features_dir) / "elevation.tif"
    if not dem_path.exists():
        return None

    try:
        with rasterio.open(str(dem_path)) as src:
            bounds = src.bounds

        with engine.connect() as conn:
            exists = conn.execute(text(
                f"SELECT EXISTS (SELECT FROM information_schema.tables WHERE table_name = '{target_table}');"
            )).scalar()
            if not exists:
                return None

        sql = f"""
            SELECT geom FROM {target_table}
            WHERE geom && ST_MakeEnvelope(
                {bounds.left}, {bounds.bottom}, {bounds.right}, {bounds.top}, 4326
            )
        """
        gdf = gpd.read_postgis(sql, engine, geom_col="geom")
        if gdf.empty:
            return None

        gdf = gdf.set_crs("EPSG:4326", allow_override=True)
        shp_path = Path(out_dir) / f"{out_name}.shp"
        gdf.to_file(str(shp_path))
        return str(shp_path)
    except Exception:
        return None


def _load_script(script_path: str, disaster_code: str):
    p = Path(script_path)
    if not p.exists():
        raise HTTPException(404, detail=f"Script not found: {script_path}")
    module_name = f"susc_{disaster_code}_{p.stem}"
    spec = importlib.util.spec_from_file_location(module_name, str(p))
    mod  = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = mod
    try:
        spec.loader.exec_module(mod)
    except Exception as e:
        raise HTTPException(500, detail=f"Error loading script {p.name}: {e}")
    return mod


def _resolve_script(disaster_code: str, override: Optional[str]) -> str:
    if override and Path(override).exists():
        return override
    builtin = SCRIPTS_DIR / f"{disaster_code}_susceptibility.py"
    if builtin.exists():
        return str(builtin)
    raise HTTPException(
        404,
        detail=f"No script for disaster '{disaster_code}'. Upload one via Disaster Manager "
               f"or add '{disaster_code}_susceptibility.py' to susceptibility_scripts/."
    )


def _raster_to_geojson(tif_path: str) -> dict:
    import rasterio
    import rasterio.features
    import numpy as np
    from shapely.geometry import shape

    CLASS_LABELS = {1: "Very Low", 2: "Low", 3: "Moderate", 4: "High", 5: "Very High"}
    COLORS       = {1: "#2dc653", 2: "#80b918", 3: "#f9c74f", 4: "#f3722c", 5: "#d62828"}

    features = []
    try:
        with rasterio.open(tif_path) as src:
            data      = src.read(1)
            transform = src.transform
        for cls_id in CLASS_LABELS:
            mask = (data == cls_id).astype(np.uint8)
            if not mask.any():
                continue
            for geom, _ in rasterio.features.shapes(mask, mask=mask, transform=transform):
                features.append({
                    "type": "Feature",
                    "properties": {
                        "class_id":       cls_id,
                        "susceptibility": CLASS_LABELS[cls_id],
                        "color":          COLORS[cls_id],
                    },
                    "geometry": geom,
                })
    except Exception as e:
        print(f"[SuscRouter] Vectorisation warning: {e}")

    return {"type": "FeatureCollection", "features": features}


def _save_to_postgis(geojson, disaster_code, region_id, country, state, district,
                     engine, out_dir=None) -> bool:
    import geopandas as gpd
    import shapely
    from shapely.geometry import shape

    if not geojson.get("features"):
        return False

    safe_code  = re.sub(r"[^a-z0-9_]", "_", disaster_code.lower())
    table_name = f"susceptibility_{safe_code}"

    try:
        rows = []
        for f in geojson["features"]:
            try:
                geom = shape(f["geometry"])
                if geom.geom_type == "Polygon":
                    geom = shapely.geometry.MultiPolygon([geom])
                rows.append({
                    "region_id":      region_id,
                    "country":        country or "",
                    "state":          state   or "",
                    "district":       district or "",
                    "class_id":       f["properties"].get("class_id"),
                    "susceptibility": f["properties"].get("susceptibility"),
                    "geometry":       geom,
                })
            except Exception:
                continue

        if not rows:
            return False

        import geopandas as gpd
        gdf = gpd.GeoDataFrame(rows, crs="EPSG:4326")

        if out_dir:
            try:
                gdf.rename(columns={"susceptibility": "susc_class"}).to_file(
                    str(Path(out_dir) / f"{disaster_code}_class.shp"))
            except Exception:
                pass

        with engine.begin() as conn:
            conn.execute(text(f"""
                CREATE TABLE IF NOT EXISTS {table_name} (
                    id             BIGSERIAL PRIMARY KEY,
                    region_id      TEXT NOT NULL,
                    country        TEXT DEFAULT '',
                    state          TEXT DEFAULT '',
                    district       TEXT DEFAULT '',
                    class_id       INTEGER,
                    susceptibility TEXT,
                    geom           GEOMETRY(MULTIPOLYGON, 4326),
                    created_at     TIMESTAMPTZ DEFAULT NOW()
                );
                CREATE INDEX IF NOT EXISTS idx_{table_name}_region ON {table_name} (region_id);
            """))
            conn.execute(text(f"DELETE FROM {table_name} WHERE region_id=:rid"), {"rid": region_id})

        gdf.rename_geometry("geom").to_postgis(table_name, engine,
                                               if_exists="append", index=False, chunksize=500)
        return True
    except Exception as e:
        print(f"[SuscRouter] PostGIS store warning (non-fatal): {e}")
        return False


@router.post("/generate-susceptibility")
async def generate_susceptibility(req: SusceptibilityRequest):
    engine = _get_engine()
    root   = Path(req.data_root).resolve() if req.data_root else settings.data_root_path

    features_dir_str = _get_features_dir_from_db(engine, req.region_id)
    if features_dir_str:
        features_dir = Path(features_dir_str)
    else:
        features_dir = root / _safe(req.country) / _safe(req.state) / _safe(req.district) / "dem_features"

    if not features_dir.exists():
        raise HTTPException(422, detail=(
            f"DEM features directory not found: {features_dir}\n"
            f"Process a DEM for this region first."
        ))

    out_dir = (root / _safe(req.disaster_code) / _safe(req.country)
               / _safe(req.state) / _safe(req.district) / "susceptibility_output")
    out_dir.mkdir(parents=True, exist_ok=True)

    script_path = _resolve_script(req.disaster_code, req.script_path)
    manual_paths = _get_manual_data_paths(engine)

    river_path = _crop_vector_from_postgis(features_dir, "india_rivers", engine, out_dir, "river_cropped") \
                 if manual_paths.get("river") else None
    fault_path = _crop_vector_from_postgis(features_dir, "india_faults", engine, out_dir, "fault_cropped") \
                 if manual_paths.get("fault") else None

    mod = _load_script(script_path, req.disaster_code)

    try:
        if req.disaster_code == "flood":
            fn = getattr(mod, "compute_flood_susceptibility", None) or getattr(mod, "run", None)
            if fn is None:
                raise HTTPException(500, detail="flood script missing compute_flood_susceptibility() or run()")
            result = fn(
                features_dir=str(features_dir),
                river_shp=river_path,
                output_dir=str(out_dir),
            )

        elif req.disaster_code == "landslide":
            fn = getattr(mod, "compute_landslide_susceptibility", None) or getattr(mod, "run", None)
            if fn is None:
                raise HTTPException(500, detail="landslide script missing compute_landslide_susceptibility() or run()")
            result = fn(
                features_dir=str(features_dir),
                fault_shp=fault_path,
                output_dir=str(out_dir),
            )

        else:
            fn_name = f"compute_{req.disaster_code}_susceptibility"
            fn = getattr(mod, fn_name, None) or getattr(mod, "compute_susceptibility", None)
            if fn is None:
                raise HTTPException(500, detail=(
                    f"Script for '{req.disaster_code}' must expose '{fn_name}' or 'compute_susceptibility'."
                ))
            result = fn(
                features_dir=str(features_dir),
                river_shp=river_path,
                fault_shp=fault_path,
                output_dir=str(out_dir),
            )

    except HTTPException:
        raise
    except Exception as e:
        import traceback
        print(f"[SuscRouter] Script execution failed:\n{traceback.format_exc()}")
        raise HTTPException(500, detail=f"Susceptibility script error: {e}")

    class_tif = result.get("class_tif_path") or result.get("tif_path", "")
    geojson   = {}
    if class_tif and Path(class_tif).exists():
        try:
            geojson = _raster_to_geojson(class_tif)
        except Exception as e:
            print(f"[SuscRouter] Vectorisation failed (non-fatal): {e}")

    if geojson.get("features"):
        _save_to_postgis(geojson, req.disaster_code, req.region_id,
                         req.country, req.state, req.district, engine, out_dir=str(out_dir))

    return {
        "status":      "done",
        "output_dir":  str(out_dir),
        "tif_path":    result.get("tif_path", ""),
        "class_stats": result.get("class_stats", {}),
        "geojson":     {"final": geojson},
        "log": "\n".join([
            f"✓ {req.disaster_code.upper()} susceptibility map generated",
            f"  Region : {req.country} / {req.state} / {req.district}",
            f"  Script : {Path(script_path).name}",
            f"  River  : {river_path or 'n/a'}",
            f"  Fault  : {fault_path or 'n/a'}",
            f"  Output : {result.get('tif_path', 'N/A')}",
        ]),
    }


@router.post("/upload-script")
async def upload_script(file: UploadFile = File(...), disaster_code: str = Form(...)):
    if not file.filename.endswith(".py"):
        raise HTTPException(400, detail="Only .py files are allowed")
    SCRIPTS_DIR.mkdir(parents=True, exist_ok=True)
    builtin = SCRIPTS_DIR / f"{disaster_code}_susceptibility.py"
    dest    = SCRIPTS_DIR / f"{disaster_code}_custom_susceptibility.py" if builtin.exists() \
              else builtin
    dest.write_bytes(await file.read())
    return {"script_path": str(dest), "filename": dest.name,
            "message": f"Script saved for disaster '{disaster_code}'"}
